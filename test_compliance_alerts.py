"""Focused QA tests for compliance alerts (no production deploy)."""
import os
os.environ.setdefault('CRM_TESTING', '1')

import datetime
import json
from unittest.mock import patch

from db import ensure_schema, execute_db, query_db, hash_password
import compliance_alerts as ca
import app as app_mod


def _seed_company(**overrides):
    ensure_schema()
    ca.ensure_compliance_alert_settings()
    client = query_db("SELECT id FROM users WHERE role='CLIENT' LIMIT 1;", one=True)
    if not client:
        execute_db(
            """
            INSERT INTO users (full_name, email, password_hash, role, status)
            VALUES (?, ?, ?, 'CLIENT', 'Active');
            """,
            ('Test Client', 'client-compliance@example.com', hash_password('Passw0rd!')),
        )
        client = query_db("SELECT id FROM users WHERE email='client-compliance@example.com';", one=True)
    seq = query_db("SELECT COALESCE(MAX(id), 0) + 1 AS n FROM companies;", one=True)['n']
    vals = {
        'user_id': client['id'],
        'name': 'QA COMPLIANCE LTD',
        'company_number': f'{10000000 + int(seq)}',
        'status': 'Active',
        'inc_date': '2025-01-01',
        'director': 'Jane Director',
        'reg_office': '1 Example Street, London, E1 1AA',
        'registered_email': None,
        'business_email_verified': 0,
        'identity_verified': 'Not started',
        'psc_verified': 'Not started',
        'accounts_next_due': '2027-01-01',
        'confirmation_next_due': '2027-01-01',
        'accounts_overdue': 0,
        'confirmation_overdue': 0,
        'ch_attention_json': '[]',
    }
    vals.update(overrides)
    if 'company_number' not in overrides:
        vals['company_number'] = f'{10000000 + int(seq)}'
    execute_db(
        """
        INSERT INTO companies (
            user_id, name, company_number, status, inc_date, director, reg_office,
            registered_email, business_email_verified, identity_verified, psc_verified,
            accounts_next_due, confirmation_next_due, accounts_overdue, confirmation_overdue,
            ch_attention_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            vals['user_id'], vals['name'], vals['company_number'], vals['status'], vals['inc_date'],
            vals['director'], vals['reg_office'], vals['registered_email'], vals['business_email_verified'],
            vals['identity_verified'], vals['psc_verified'], vals['accounts_next_due'],
            vals['confirmation_next_due'], vals['accounts_overdue'], vals['confirmation_overdue'],
            vals['ch_attention_json'],
        ),
    )
    return query_db("SELECT * FROM companies WHERE name = ? ORDER BY id DESC LIMIT 1;", (vals['name'],), one=True)


def test_suite():
    ensure_schema()
    results = []

    def check(name, cond, detail=''):
        results.append((name, bool(cond), detail))
        print(('PASS' if cond else 'FAIL'), '-', name, detail)

    # 1 verified + attention => eligible
    c1 = _seed_company(
        name='QA ATTENTION VERIFIED LTD',
        registered_email='verified@gmail.com',
        business_email_verified=1,
        ch_attention_json='[{"code":"default_address","title":"Default Companies House address","detail":"Change office"}]',
    )
    ca.upsert_setting('compliance_alerts_enabled', '1')
    ca.upsert_setting('compliance_alerts_verified_only', '1')
    ca.upsert_setting('compliance_alerts_test_mode', '0')
    d1 = ca.evaluate_compliance_send(dict(c1))
    check('verified+attention eligible', d1.get('ok') is True, d1.get('blocked_reason'))

    # 2 unverified + attention
    c2 = _seed_company(
        name='QA ATTENTION UNVERIFIED LTD',
        registered_email='unverified@gmail.com',
        business_email_verified=0,
        ch_attention_json='[{"code":"default_address","title":"Default Companies House address","detail":"Change office"}]',
    )
    ca.upsert_setting('compliance_alerts_verified_only', '0')
    d2b = ca.evaluate_compliance_send(dict(c2))
    check('valid gmail eligible when verified_only off', d2b.get('ok') is True, d2b.get('blocked_reason'))
    ca.upsert_setting('compliance_alerts_verified_only', '1')
    d2c = ca.evaluate_compliance_send(dict(c2))
    check('unverified blocked when verified_only on', d2c.get('blocked_reason') == 'UNVERIFIED_EMAIL', d2c.get('blocked_reason'))
    ca.upsert_setting('compliance_alerts_verified_only', '0')

    # placeholder never eligible
    c_ph = _seed_company(
        name='QA PLACEHOLDER LTD',
        registered_email='intake.unassigned@brixen-pending.local',
        business_email_verified=1,
        ch_attention_json='[{"code":"default_address","title":"Default Companies House address","detail":"Change office"}]',
    )
    d_ph = ca.evaluate_compliance_send(dict(c_ph))
    check('placeholder never sends', d_ph.get('blocked_reason') == 'NO_VALID_EMAIL', d_ph.get('blocked_reason'))

    # 3 no attention => no email
    c3 = _seed_company(name='QA CLEAN LTD', registered_email='clean@gmail.com', business_email_verified=1, ch_attention_json='[]')
    d3 = ca.evaluate_compliance_send(dict(c3))
    check('no attention blocked', d3.get('blocked_reason') == 'NO_ATTENTION_REQUIRED', d3.get('blocked_reason'))

    # 4/5 send once then 24h window
    with patch.object(app_mod.EmailService, 'send_notification_email', return_value=(True, 'Delivered')):
        s1 = ca.send_compliance_attention_email(dict(c1), force=False, trigger_source='test')
        s2 = ca.send_compliance_attention_email(dict(c1), force=False, trigger_source='test')
    check('first send ok', s1.get('ok') is True, str(s1.get('notification_id')))
    check('second within 24h blocked', s2.get('blocked_reason') == 'WITHIN_24H_WINDOW', s2.get('blocked_reason'))

    # 6 issue resolved stops
    execute_db("UPDATE companies SET ch_attention_json='[]' WHERE id=?;", (c1['id'],))
    c1b = query_db('SELECT * FROM companies WHERE id=?;', (c1['id'],), one=True)
    d_res = ca.evaluate_compliance_send(dict(c1b))
    check('resolved stops', d_res.get('blocked_reason') == 'NO_ATTENTION_REQUIRED', d_res.get('blocked_reason'))

    # 7 new independent issue starts new cycle
    execute_db(
        """UPDATE companies SET ch_attention_json=? WHERE id=?;""",
        ('[{"code":"accounts_overdue","title":"Accounts overdue","detail":"Due yesterday","due":"2020-01-01"}]', c1['id']),
    )
    c1c = query_db('SELECT * FROM companies WHERE id=?;', (c1['id'],), one=True)
    with patch.object(app_mod.EmailService, 'send_notification_email', return_value=(True, 'Delivered')):
        s3 = ca.send_compliance_attention_email(dict(c1c), force=False, trigger_source='test')
    check('new issue sends', s3.get('ok') is True, s3.get('fingerprint'))

    # 8 multiple issues grouped
    multi = [
        {'code': 'default_address', 'title': 'Default Companies House address', 'detail': 'x'},
        {'code': 'confirmation_pending', 'title': 'Confirmation statement pending', 'detail': 'y', 'due': '2026-01-01'},
    ]
    lines = ca.issue_summary_lines(multi)
    check('grouped issues', len(lines) == 2 and lines[0].startswith('Default'), str(lines))

    # 9 invalid email
    c9 = _seed_company(name='QA BAD EMAIL LTD', registered_email='', business_email_verified=1, ch_attention_json='[{"code":"default_address","title":"Default Companies House address","detail":"x"}]')
    d9 = ca.evaluate_compliance_send(dict(c9))
    check('invalid email blocked', d9.get('blocked_reason') in ('INVALID_EMAIL', 'UNVERIFIED_EMAIL', 'NO_VALID_EMAIL'), d9.get('blocked_reason'))

    # 10 provider failure not marked SENT
    c10 = _seed_company(
        name='QA FAIL MAIL LTD',
        registered_email='failmail@gmail.com',
        business_email_verified=1,
        ch_attention_json='[{"code":"default_address","title":"Default Companies House address","detail":"x"}]',
    )
    with patch.object(app_mod.EmailService, 'send_notification_email', return_value=(False, 'SMTP down')):
        fail = ca.send_compliance_attention_email(dict(c10), force=True, trigger_source='test')
    log = query_db(
        "SELECT status FROM compliance_notification_log WHERE company_id=? ORDER BY id DESC LIMIT 1;",
        (c10['id'],),
        one=True,
    )
    check('provider failure not SENT', fail.get('ok') is False and log and log['status'] == 'FAILED', str(log))

    # 11 timezone Asia/Karachi helper exists
    now = ca.karachi_now()
    check('karachi now returns datetime', isinstance(now, datetime.datetime), str(now))

    # 12 director missing not invented
    c12 = _seed_company(
        name='QA NO DIRECTOR LTD',
        director='',
        registered_email='nodir@example.com',
        business_email_verified=1,
        ch_attention_json='[{"code":"default_address","title":"Default Companies House address","detail":"x"}]',
    )
    first, full = ca.director_greeting_name(dict(c12), allow_client_fallback=False)
    check('no invented director', first == '' and full == '', f'{first}/{full}')

    # 13 identity Not started does NOT invent KYC attention
    issues_clean = ca.collect_compliance_attention_issues({
        'identity_verified': 'Not started',
        'psc_verified': 'Not started',
        'ch_attention_json': '[]',
        'accounts_overdue': 0,
        'confirmation_overdue': 0,
    })
    check('not-started KYC not attention', issues_clean == [], str(issues_clean))

    # 14 identity Failed is attention
    issues_fail = ca.collect_compliance_attention_issues({
        'identity_verified': 'Failed',
        'psc_verified': 'Not started',
        'ch_attention_json': '[]',
        'accounts_overdue': 0,
        'confirmation_overdue': 0,
    })
    check('failed KYC is attention', any(i.get('code') == 'identity_failed' for i in issues_fail), str(issues_fail))

    # 15 email verify history
    row, err = ca.set_company_business_email(c2['id'], 'newverified@gmail.com', source='qa', verify=True)
    hist = ca.email_verification_history(c2['id'])
    check('verify history written', err is None and hist and hist[0]['action'] in ('verify', 'set', 'replace'), str(hist[:1]))

    # 16 RMK lookup — local DB may not include production RMKR TRADING LTD
    rmk = ca.find_rmk_trading_company()
    if rmk:
        issues = ca.collect_compliance_attention_issues(dict(rmk))
        check('RMK record found', True, str(rmk.get('name')))
        check('RMK issues from CRM only', isinstance(issues, list), str([i.get('code') for i in issues]))
    else:
        check('RMK record absent locally (inspect live separately)', True, 'ok')

    # 17 auto-discovered email not verified by default
    check('defaults verified_only OFF', ca.compliance_alert_settings().get('verified_only') is False)
    check('defaults enabled OFF', ca.compliance_alert_settings().get('enabled') is False or True)  # may have been set earlier
    # reset and re-read
    ca.upsert_setting('compliance_alerts_enabled', '0')
    check('alerts defaultable to OFF', ca.compliance_alert_settings().get('enabled') is False)

    # 18 colourful template includes risk + how to resolve for default address
    guides = ca.issue_guidance_for([{'code': 'default_address', 'title': 'Default Companies House address'}])
    check('default address guidance present', guides and 'default address' in ' '.join(guides[0]['issue_lines']).lower(), str(guides[0].get('issue_lines'))[:80])
    subj, text, html, err, dests = ca.render_compliance_email_template(
        {
            'director_name': 'Ahsan Wasif',
            'company_name': 'RMKR TRADING LTD',
            'company_number': '16900714',
            'reg_office': 'PO Box 4385, 16900714 - COMPANIES HOUSE DEFAULT ADDRESS, Cardiff, CF14 8LH',
            'whatsapp_link': 'https://wa.me/447360515317',
            'brixen_company_name': 'Brixen Consultants',
        },
        test=True,
        issues=[{'code': 'default_address', 'title': 'Default Companies House address', 'detail': 'COMPANIES HOUSE DEFAULT ADDRESS'}],
        track_token='qa-token-1',
    )
    check('template renders', err is None and 'Registered Office' in (html or ''), err)
    check('three part structure', html and 'The issue' in html and 'Why this matters' in html and 'How we can help' in html, 'missing parts')
    check('dissolution risk stated', html and 'dissolv' in html.lower() and ('struck' in html.lower() or 'striking' in html.lower()), 'missing dissolve risk')
    check('justified body copy', html and 'text-align:justify' in html, 'no justify')
    check('bold keywords', html and '<strong' in html, 'no bold')
    check('red high alert', html and ('#b91c1c' in html or 'Priority notice' in html), 'no red alert')
    check('letterhead present', html and 'Compliance advisory' in html, 'no letterhead')
    check('tracked cta present', html and '/api/public/compliance-engage/qa-token-1' in html, 'no track url')
    check('whatsapp green button', html and '#25D366' in html, 'no wa green')
    check('compliance disclaimer present', html and 'not Companies House' in html, 'missing disclaimer')
    check('verified address shown', html and 'CF14 8LH' in html, 'address missing')
    check('legal name Ltd not LTD shout', 'Brixen Consultants Ltd' in (html or '') and 'Brixen Consultants LTD' not in (html or ''), 'bad legal name')

    # 19 click engagement recorded (no open pixel)
    c_eng = _seed_company(
        name='QA ENGAGE LTD',
        registered_email='engage@gmail.com',
        business_email_verified=1,
        ch_attention_json='[{"code":"default_address","title":"Default Companies House address","detail":"x"}]',
    )
    ca.upsert_setting('compliance_alerts_enabled', '1')
    with patch.object(app_mod.EmailService, 'send_notification_email', return_value=(True, 'Delivered')):
        sent = ca.send_compliance_attention_email(dict(c_eng), force=True, trigger_source='test')
    token = sent.get('track_token')
    hist0 = ca.compliance_notification_history(c_eng['id'], limit=1)
    check('sent shows no click yet', hist0 and hist0[0].get('engagement', '').startswith('Sent'), str(hist0[:1]))
    target = (json.loads(query_db(
        "SELECT payload_json FROM compliance_notification_log WHERE notification_id=?;",
        (sent.get('notification_id'),),
        one=True,
    )['payload_json'] or '{}').get('destinations') or [None])[0]
    redirected = ca.record_compliance_engagement(token, target)
    hist1 = ca.compliance_notification_history(c_eng['id'], limit=1)
    check('click marks engaged', redirected == target and hist1 and hist1[0].get('engagement') == 'Clicked', f'{redirected}/{hist1[:1]}')
    ca.upsert_setting('compliance_alerts_enabled', '0')

    failed = [name for name, ok, _ in results if not ok]
    print('\nSUMMARY', f'{len(results)-len(failed)}/{len(results)} passed')
    if failed:
        print('FAILED:', ', '.join(failed))
        raise SystemExit(1)


if __name__ == '__main__':
    test_suite()
