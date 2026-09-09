"""
Client compliance notification engine (verified-email gated).

Daily digest: 11:00 Asia/Karachi. At most one send per company/issue-set per 24h.
Does not invent Companies House / KYC issues — only uses stored/computed CRM records.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import secrets
import uuid
from html import escape as html_escape

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from db import execute_db, query_db

KARACHI_TZ_NAME = 'Asia/Karachi'
DEFAULT_SEND_HOUR = 11
DEFAULT_INTERVAL_HOURS = 24
NOTIFICATION_TYPE = 'compliance_attention'

SETTING_KEYS = (
    'compliance_alerts_enabled',
    'compliance_alerts_send_hour',
    'compliance_alerts_timezone',
    'compliance_alerts_interval_hours',
    'compliance_alerts_verified_only',
    'compliance_alerts_whatsapp_enabled',
    'compliance_alerts_test_mode',
    'compliance_alerts_test_recipient',
    'compliance_alerts_website',
    'compliance_alerts_whatsapp_url',
)


def _setting(key, default=''):
    row = query_db('SELECT value FROM settings WHERE key = ?;', (key,), one=True)
    if not row or row.get('value') is None:
        return default
    return str(row.get('value') or '').strip()


def _setting_bool(key, default=False):
    raw = _setting(key, '1' if default else '0').lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off', ''):
        return False
    return default


def _setting_int(key, default):
    try:
        return int(_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def upsert_setting(key, value):
    execute_db(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;
        """,
        (key, str(value if value is not None else '')),
    )


def ensure_compliance_alert_settings():
    defaults = {
        'compliance_alerts_enabled': '0',
        'compliance_alerts_send_hour': str(DEFAULT_SEND_HOUR),
        'compliance_alerts_timezone': KARACHI_TZ_NAME,
        'compliance_alerts_interval_hours': str(DEFAULT_INTERVAL_HOURS),
        'compliance_alerts_verified_only': '0',
        'compliance_alerts_whatsapp_enabled': '1',
        'compliance_alerts_test_mode': '0',
        'compliance_alerts_test_recipient': 'brixenconsultant@gmail.com',
        'compliance_alerts_website': 'https://brixenconsultants.com',
        'compliance_alerts_whatsapp_url': '',
    }
    for key, value in defaults.items():
        existing = query_db('SELECT value FROM settings WHERE key = ?;', (key,), one=True)
        if not existing:
            upsert_setting(key, value)


def compliance_alert_settings():
    ensure_compliance_alert_settings()
    return {
        'enabled': _setting_bool('compliance_alerts_enabled', False),
        'send_hour': max(0, min(23, _setting_int('compliance_alerts_send_hour', DEFAULT_SEND_HOUR))),
        'timezone': _setting('compliance_alerts_timezone', KARACHI_TZ_NAME) or KARACHI_TZ_NAME,
        'interval_hours': max(1, _setting_int('compliance_alerts_interval_hours', DEFAULT_INTERVAL_HOURS)),
        # Default OFF: a valid real mailbox on the company card is enough to send.
        'verified_only': _setting_bool('compliance_alerts_verified_only', False),
        'whatsapp_enabled': _setting_bool('compliance_alerts_whatsapp_enabled', True),
        'test_mode': _setting_bool('compliance_alerts_test_mode', False),
        'test_recipient': _setting('compliance_alerts_test_recipient', 'brixenconsultant@gmail.com'),
        'website': _setting('compliance_alerts_website', 'https://brixenconsultants.com'),
        'whatsapp_url': _setting('compliance_alerts_whatsapp_url', ''),
    }


def karachi_now():
    if ZoneInfo is None:
        return datetime.datetime.utcnow() + datetime.timedelta(hours=5)
    return datetime.datetime.now(ZoneInfo(KARACHI_TZ_NAME))


def parse_utc_naive(value):
    text = str(value or '').strip()
    if not text:
        return None
    text = text.replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def director_greeting_name(company, *, allow_client_fallback=False, client=None):
    """Use verified CRM/CH director only. Never invent."""
    directors = []
    raw = company or {}
    for item in (raw.get('directors') or []):
        name = str(item if isinstance(item, str) else (item or {}).get('name') or '').strip()
        if name:
            directors.append(name)
    if not directors:
        single = str(raw.get('director') or '').strip()
        if single and ' · ' not in single and ',' not in single:
            directors = [single]
        elif single:
            parts = [p.strip() for p in re.split(r'\s*[·,]\s*', single) if p.strip()]
            directors = parts
    if len(directors) == 1:
        full = directors[0]
        first = full.split()[0] if full.split() else full
        return first, full
    if allow_client_fallback and client:
        full = str(client.get('full_name') or '').strip()
        if full:
            first = full.split()[0]
            return first, full
    return '', ''


def business_email_record(company):
    src = company or {}
    email = str(src.get('registered_email') or '').strip().lower()
    if email.endswith('@brixen-pending.local'):
        email = ''
    verified = int(src.get('business_email_verified') or 0) == 1 and bool(email)
    return {
        'email': email,
        'email_verified': verified,
        'verified_by': src.get('business_email_verified_by'),
        'verified_at': src.get('business_email_verified_at'),
        'source': src.get('business_email_source') or '',
        'last_updated_at': src.get('business_email_updated_at'),
    }


def append_email_verification_history(company_id, *, email, action, actor=None, source='', note=''):
    execute_db(
        """
        INSERT INTO company_email_verification_history
            (company_id, email, action, source, note, actor_user_id, actor_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'));
        """,
        (
            company_id,
            str(email or '').strip().lower() or None,
            action,
            source or None,
            note or None,
            (actor or {}).get('id'),
            str((actor or {}).get('full_name') or (actor or {}).get('email') or '') or None,
        ),
    )


def set_company_business_email(company_id, email, *, actor=None, source='staff', verify=False):
    email = str(email or '').strip().lower()
    if not email or '@' not in email or email.endswith('@brixen-pending.local'):
        return None, 'Enter a valid business email'
    current = query_db('SELECT registered_email, business_email_verified FROM companies WHERE id = ?;', (company_id,), one=True)
    if not current:
        return None, 'Company not found'
    prev = str(current.get('registered_email') or '').strip().lower()
    verified_flag = 1 if verify else 0
    verified_by = (actor or {}).get('id') if verify else None
    if verify:
        execute_db(
            """
            UPDATE companies
            SET registered_email = ?,
                registered_email_locked = 1,
                business_email_verified = 1,
                business_email_verified_by = ?,
                business_email_verified_at = datetime('now'),
                business_email_source = ?,
                business_email_updated_at = datetime('now')
            WHERE id = ?;
            """,
            (email, verified_by, source, company_id),
        )
    else:
        execute_db(
            """
            UPDATE companies
            SET registered_email = ?,
                registered_email_locked = 1,
                business_email_verified = 0,
                business_email_verified_by = NULL,
                business_email_verified_at = NULL,
                business_email_source = ?,
                business_email_updated_at = datetime('now')
            WHERE id = ?;
            """,
            (email, source, company_id),
        )
    action = 'verify' if verify else ('replace' if prev and prev != email else 'set')
    append_email_verification_history(company_id, email=email, action=action, actor=actor, source=source)
    row = query_db('SELECT * FROM companies WHERE id = ?;', (company_id,), one=True)
    return row, None


def verify_company_business_email(company_id, *, actor=None, source='staff_verify'):
    row = query_db('SELECT registered_email FROM companies WHERE id = ?;', (company_id,), one=True)
    if not row:
        return None, 'Company not found'
    email = str(row.get('registered_email') or '').strip().lower()
    if not email or email.endswith('@brixen-pending.local'):
        return None, 'Add a business email before verifying'
    execute_db(
        """
        UPDATE companies
        SET business_email_verified = 1,
            business_email_verified_by = ?,
            business_email_verified_at = datetime('now'),
            business_email_source = ?,
            business_email_updated_at = datetime('now'),
            registered_email_locked = 1
        WHERE id = ?;
        """,
        ((actor or {}).get('id'), source, company_id),
    )
    append_email_verification_history(company_id, email=email, action='verify', actor=actor, source=source)
    return query_db('SELECT * FROM companies WHERE id = ?;', (company_id,), one=True), None


def unverify_company_business_email(company_id, *, actor=None, source='staff_unverify'):
    row = query_db('SELECT registered_email FROM companies WHERE id = ?;', (company_id,), one=True)
    if not row:
        return None, 'Company not found'
    email = str(row.get('registered_email') or '').strip().lower()
    execute_db(
        """
        UPDATE companies
        SET business_email_verified = 0,
            business_email_verified_by = NULL,
            business_email_verified_at = NULL,
            business_email_source = ?,
            business_email_updated_at = datetime('now')
        WHERE id = ?;
        """,
        (source, company_id),
    )
    append_email_verification_history(company_id, email=email, action='unverify', actor=actor, source=source)
    return query_db('SELECT * FROM companies WHERE id = ?;', (company_id,), one=True), None


def email_verification_history(company_id, limit=50):
    return query_db(
        """
        SELECT id, company_id, email, action, source, note, actor_user_id, actor_name, created_at
        FROM company_email_verification_history
        WHERE company_id = ?
        ORDER BY id DESC
        LIMIT ?;
        """,
        (company_id, limit),
    ) or []


def compliance_notification_history(company_id, limit=50):
    rows = query_db(
        """
        SELECT id, company_id, notification_id, notification_type, issue_fingerprint, issue_summary,
               recipient, status, blocked_reason, message_id, trigger_source, attempt,
               sent_by_user_id, error_category, track_token, first_clicked_at, click_count, created_at
        FROM compliance_notification_log
        WHERE company_id = ?
        ORDER BY id DESC
        LIMIT ?;
        """,
        (company_id, limit),
    ) or []
    out = []
    for row in rows:
        item = dict(row)
        status = str(item.get('status') or '').upper()
        if status == 'SENT':
            if item.get('first_clicked_at'):
                item['engagement'] = 'Clicked'
                item['engagement_detail'] = f"First click {item.get('first_clicked_at')}"
            else:
                item['engagement'] = 'Sent — no click yet'
                item['engagement_detail'] = 'Open tracking is not used; engagement is recorded when a CTA is clicked.'
        else:
            item['engagement'] = ''
            item['engagement_detail'] = ''
        out.append(item)
    return out


def collect_compliance_attention_issues(company, today=None):
    """
    Genuine outstanding issues only. Reuses Companies House attention plus
    explicit KYC/PSC failure states — never invents filings.
    """
    # Lazy import to avoid circular import at module load.
    import app as app_mod

    issues = list(app_mod.company_attention_issues(company) or [])
    seen = {str(item.get('code') or '') for item in issues}
    identity = str((company or {}).get('identity_verified') or '').strip()
    psc = str((company or {}).get('psc_verified') or '').strip()
    if identity == 'Failed' and 'identity_failed' not in seen:
        issues.append({
            'code': 'identity_failed',
            'title': 'Identity verification requires attention',
            'detail': 'KYC / identity verification failed and needs client action.',
        })
        seen.add('identity_failed')
    if psc in ('Failed', 'Update required') and 'psc_attention' not in seen:
        issues.append({
            'code': 'psc_attention',
            'title': 'PSC verification requires attention',
            'detail': f'PSC status is {psc}.',
        })
    return issues


def issue_fingerprint(issues):
    codes = sorted({str((item or {}).get('code') or '').strip() for item in (issues or []) if (item or {}).get('code')})
    return '|'.join(codes)


def issue_summary_lines(issues):
    lines = []
    for item in issues or []:
        title = str((item or {}).get('title') or '').strip()
        detail = str((item or {}).get('detail') or '').strip()
        due = str((item or {}).get('due') or '').strip()
        if not title:
            continue
        if due:
            lines.append(f"{title} (due {due})")
        elif detail:
            lines.append(f"{title} — {detail}")
        else:
            lines.append(title)
    return lines


ISSUE_GUIDANCE = {
    'default_address': {
        'title': 'Registered Office Address',
        'severity': 'medium',
        'issue': (
            'We noticed that the company\'s registered office is currently showing the Companies House default address.',
            ''
        ),
        'consequence': (
            'For ongoing company correspondence, it may be worth reviewing whether this address is still appropriate for the company.',
            ''
        ),
        'resolve': (
            'If an update is required, Brixen Consultants can guide you through the process and the relevant Companies House filing.',
            ''
        ),
        'bold': (),
        'ask': '',
    },
    'accounts_overdue': {
        'title': 'Annual Accounts',
        'severity': 'medium',
        'issue': (
            'Our records indicate that the annual accounts for this company may require attention.',
            ''
        ),
        'consequence': (
            'It may be worth reviewing the filing period currently shown on the company record.',
            ''
        ),
        'resolve': (
            'If an update is required, Brixen Consultants can confirm the details and guide you through the next steps.',
            ''
        ),
        'bold': (),
        'ask': '',
    },
    'confirmation_pending': {
        'title': 'Confirmation Statement',
        'severity': 'medium',
        'issue': (
            'Our records indicate that a confirmation statement for this company may require attention.',
            ''
        ),
        'consequence': (
            'It may be worth reviewing the statement date currently shown on the company record.',
            ''
        ),
        'resolve': (
            'If an update is required, Brixen Consultants can confirm the details and guide you through the next steps.',
            ''
        ),
        'bold': (),
        'ask': '',
    }
}


def _pair_lines(value, fallback1='', fallback2=''):
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return str(value[0] or '').strip(), str(value[1] or '').strip()
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return str(value[0] or '').strip(), fallback2
    text = str(value or '').strip()
    if not text:
        return fallback1, fallback2
    return text, fallback2


def issue_guidance_for(issues):
    blocks = []
    for item in issues or []:
        code = str((item or {}).get('code') or '').strip()
        guide = ISSUE_GUIDANCE.get(code)
        if not guide:
            detail = str((item or {}).get('detail') or '').strip()
            if not detail:
                continue
            guide = {
                'title': str((item or {}).get('title') or code or 'Company record update'),
                'severity': 'medium',
                'issue': (detail if detail.endswith('.') else f'{detail}.',
                          'Please review this item on the company record.'),
                'consequence': (
                    'Leaving this unresolved may affect the company record.',
                    'Prompt review helps keep the company position clear.',
                ),
                'resolve': (
                    'Contact Brixen Consultants so we can review the record with you.',
                    'We will advise on the practical next step.',
                ),
                'bold': ('Brixen Consultants',),
                'ask': 'Would you like us to review this with you?',
            }
        due = str((item or {}).get('due') or '').strip()
        detail = str((item or {}).get('detail') or '').strip()
        i1, i2 = _pair_lines(guide.get('issue'))
        c1, c2 = _pair_lines(guide.get('consequence'))
        r1, r2 = _pair_lines(guide.get('resolve'))
        # Include verified CH default address text when present on the issue/company detail.
        if code == 'default_address' and detail and 'default address' in detail.lower():
            i1 = (
                'Companies House currently shows the registered office as the '
                'Companies House default address in Cardiff.'
            )
        if due:
            i2 = f"{i2} Due date: {due}." if i2 else f"Due date: {due}."
        blocks.append({
            'code': code,
            'title': str(guide.get('title') or 'Company record update').strip(),
            'severity': str(guide.get('severity') or 'medium').strip(),
            'issue_lines': (i1, i2),
            'consequence_lines': (c1, c2),
            'resolve_lines': (r1, r2),
            'bold': tuple(guide.get('bold') or ()),
            'ask': str(guide.get('ask') or '').strip(),
            'noticed': i1,
            'context': c1,
            'help': r1,
            'explanation': i1,
            'action': r1,
            'why': i1,
            'risk': c1,
            'resolve': r1,
        })
    return blocks


def format_issue_guidance_text(blocks):
    parts = []
    for block in blocks or []:
        title = block.get('title') or 'Company record update'
        i1, i2 = block.get('issue_lines') or ('', '')
        c1, c2 = block.get('consequence_lines') or ('', '')
        r1, r2 = block.get('resolve_lines') or ('', '')
        ask = block.get('ask') or ''
        chunk = [
            f"⚠️ {title}",
            '',
            'The issue',
            i1, i2,
            '',
            'Why this matters',
            c1, c2,
            '',
            'How we can help',
            r1, r2,
        ]
        if ask:
            chunk.extend(['', ask])
        parts.append('\n'.join(chunk))
    return '\n\n'.join(parts)


def format_issue_guidance_extra_message(blocks):
    return format_issue_guidance_text(blocks)



def brand_contact_block():
    import app as app_mod

    brand = app_mod.brand_settings()
    cfg = compliance_alert_settings()
    website = cfg.get('website') or brand.get('website') or 'https://brixenconsultants.com'
    phone = str(brand.get('support_phone') or '').strip()
    email = str(brand.get('support_email') or '').strip()
    wa = str(cfg.get('whatsapp_url') or '').strip()
    if not wa and phone:
        digits = re.sub(r'\D+', '', phone)
        if digits:
            wa = f'https://wa.me/{digits}'
    return {
        'brixen_company_name': brand.get('company_name') or 'Brixen Consultants',
        'brixen_website': website,
        'brixen_email': email,
        'brixen_phone': phone,
        'whatsapp_link': wa if cfg.get('whatsapp_enabled') else '',
    }


def engagement_track_url(token, target_url):
    import app as app_mod
    import urllib.parse
    base = app_mod.portal_base_url().rstrip('/')
    qs = urllib.parse.urlencode({'to': target_url})
    return f"{base}/api/public/compliance-engage/{token}?{qs}"


def render_compliance_email_template(vars_map, *, test=False, issues=None, track_token=None):
    import app as app_mod
    import email_engine as eng
    from html import escape as html_escape

    v = {k: str(vars_map.get(k) or '').strip() for k in (
        'client_name', 'director_name', 'company_name', 'company_number',
        'issue_title', 'issue_summary', 'due_date', 'days_overdue', 'reg_office',
        'brixen_company_name', 'brixen_website', 'brixen_email', 'brixen_phone', 'whatsapp_link',
    )}
    greeting = v['director_name'] or v['client_name'] or 'there'
    if ' ' in greeting and greeting.lower() != 'there':
        greeting = greeting.split()[0]
    company = v['company_name'] or 'your company'
    number = v['company_number']
    if not number or number.lower().startswith('pending'):
        number = ''

    subject = f"Registered office notice — {company}"
    if test:
        subject = f"[TEST] {subject}"

    blocks = issue_guidance_for(issues or [])
    # Enrich default-address issue with verified registered office text when available.
    reg = v.get('reg_office') or ''
    if reg and 'default address' in reg.lower():
        for block in blocks:
            if block.get('code') == 'default_address':
                i1, i2 = block.get('issue_lines') or ('', '')
                block['issue_lines'] = (
                    i1,
                    f'The address currently shown is: {reg}.',
                )
                block['noticed'] = i1
                break
    if not blocks and not (v['issue_summary'] or v['issue_title']):
        return None, None, None, 'INSUFFICIENT_VERIFIED_INFORMATION', []

    issue_extra = format_issue_guidance_extra_message(blocks)
    contact_line = (
        'If you would like us to review this with you, simply reply to this email or contact us on WhatsApp.'
        if v['whatsapp_link']
        else 'If you would like us to review this with you, simply reply to this email.'
    )
    message = f"Hi {greeting},<br><br>We are getting in touch regarding {company}."
    if test:
        message = f"[TEST] {message}"

    ch_url = app_mod.companies_house_company_url(number) if number else ''
    wa_url = v['whatsapp_link']
    destinations = []
    if ch_url:
        destinations.append(ch_url)
    if wa_url:
        destinations.append(wa_url)
    if track_token:
        if ch_url:
            ch_url = engagement_track_url(track_token, ch_url)
        if wa_url:
            wa_url = engagement_track_url(track_token, wa_url)

    structured = eng.email_compliance_parts_html(blocks)

    contact_line = (
        'If you would like us to review this with you, simply reply to this email or contact us on WhatsApp.'
        if v['whatsapp_link']
        else 'If you would like us to review this with you, simply reply to this email.'
    )
    structured += (
        f'<p style="margin:12px 0 0;font-family:{eng.EMAIL_FONT_STACK};font-size:15px;line-height:1.6;'
        f'color:#374151;text-align:left;">{html_escape(contact_line)}</p>'
    )


    client = {'full_name': greeting, 'email': v.get('brixen_email') or ''}
    subject_out, body_text, body_html = app_mod.build_client_notification_email(
        client,
        'Client Compliance Update',
        message,
        subject=subject,
        badge='Compliance advisory',
        alert_label='Compliance advisory',
        greeting_name=greeting,
        extra_message=f"{issue_extra}\n\n{plain_close}",
        structured_html=structured,
        company_name=company if company != 'your company' else None,
        company_number=number or None,
        cta_label='View on Companies House' if ch_url else None,
        cta_url=ch_url or None,
        extra_cta_label='Message us on WhatsApp' if wa_url else None,
        extra_cta_url=wa_url or None,
        extra_cta_background='#25D366' if wa_url else None,
        suppress_default_cta=not bool(ch_url),
        layout='activity',
        email_category='COMPLIANCE',
        footer_note=eng.COMPLIANCE_DISCLAIMER,
        use_graphic_footer=True,
        header_style='premium',
    )
    lowered = f"{message}\n{issue_extra}\n{plain_close}".lower()
    banned = (
        'you must fix', 'fix this now', 'take action immediately', 'failure to do so will',
        'you need to immediately',
    )
    if any(token in lowered for token in banned):
        return None, None, None, 'TONE_VALIDATION_FAILED', []
    if eng.count_words(f"{message}\n{issue_extra}\n{plain_close}") > 220:
        return None, None, None, 'CONTENT_TOO_LONG', []
    return subject_out, body_text, body_html, None, destinations


def last_sent_log(company_id, fingerprint):
    return query_db(
        """
        SELECT * FROM compliance_notification_log
        WHERE company_id = ?
          AND notification_type = ?
          AND issue_fingerprint = ?
          AND status = 'SENT'
        ORDER BY id DESC
        LIMIT 1;
        """,
        (company_id, NOTIFICATION_TYPE, fingerprint),
        one=True,
    )


def within_interval(sent_at, interval_hours):
    when = parse_utc_naive(sent_at)
    if not when:
        return False
    return (datetime.datetime.utcnow() - when) < datetime.timedelta(hours=interval_hours)


def log_compliance_notification(
    *,
    company_id,
    notification_id,
    fingerprint,
    issue_summary,
    recipient,
    status,
    blocked_reason=None,
    message_id=None,
    trigger_source='system',
    attempt=1,
    sent_by_user_id=None,
    error_category=None,
    payload_json=None,
    track_token=None,
):
    execute_db(
        """
        INSERT INTO compliance_notification_log (
            company_id, notification_id, notification_type, issue_fingerprint, issue_summary,
            recipient, status, blocked_reason, message_id, trigger_source, attempt,
            sent_by_user_id, error_category, payload_json, track_token, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'));
        """,
        (
            company_id,
            notification_id,
            NOTIFICATION_TYPE,
            fingerprint,
            issue_summary,
            recipient,
            status,
            blocked_reason,
            message_id,
            trigger_source,
            attempt,
            sent_by_user_id,
            error_category,
            payload_json,
            track_token,
        ),
    )


def record_compliance_engagement(token, target_url):
    """Record CTA click for a compliance email. Returns redirect URL or None."""
    token = str(token or '').strip()
    target = str(target_url or '').strip()
    if not token or not target:
        return None
    if not (target.startswith('https://') or target.startswith('http://')):
        return None
    row = query_db(
        """
        SELECT id, payload_json, first_clicked_at, click_count
        FROM compliance_notification_log
        WHERE track_token = ? AND status = 'SENT'
        ORDER BY id DESC
        LIMIT 1;
        """,
        (token,),
        one=True,
    )
    if not row:
        return None
    allowed = []
    try:
        payload = json.loads(row['payload_json'] or '{}')
        allowed = list(payload.get('destinations') or [])
    except Exception:
        allowed = []
    if target not in allowed:
        return None
    execute_db(
        """
        UPDATE compliance_notification_log
        SET click_count = COALESCE(click_count, 0) + 1,
            first_clicked_at = COALESCE(first_clicked_at, datetime('now'))
        WHERE id = ?;
        """,
        (row['id'],),
    )
    return target


def is_valid_client_notify_email(value):
    """
    Real deliverable addresses only: company domains, Gmail, Hotmail, Outlook, etc.
    Never placeholders / local fakes / intake.unassigned.
    """
    email = str(value or '').strip().lower()
    if not email or '@' not in email:
        return False
    if email.endswith('@brixen-pending.local') or email.endswith('.local'):
        return False
    local, _, domain = email.partition('@')
    if not local or not domain or '.' not in domain:
        return False
    if ' ' in email or email.count('@') != 1:
        return False
    if local.startswith('intake.unassigned') or domain.endswith('brixen-pending.local'):
        return False
    # Basic RFC-ish shape (same as portal STAFF_EMAIL_RE).
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return False
    blocked_domains = {
        'example.com', 'example.org', 'example.net', 'test.com', 'localhost',
        'invalid', 'email.com',
    }
    if domain in blocked_domains:
        return False
    return True


def resolve_compliance_recipient(company, settings=None):
    settings = settings or compliance_alert_settings()
    rec = business_email_record(company)
    email = rec.get('email') or ''
    if settings.get('test_mode'):
        test_to = str(settings.get('test_recipient') or '').strip().lower()
        if is_valid_client_notify_email(test_to):
            return test_to, None
        return None, 'INVALID_EMAIL'
    # Hard gate: never send without a real client/company email on the record
    # (company domain, Gmail, Hotmail, Outlook, etc. — never placeholders).
    if not is_valid_client_notify_email(email):
        return None, 'NO_VALID_EMAIL'
    if settings.get('verified_only') and not rec.get('email_verified'):
        return None, 'UNVERIFIED_EMAIL'
    return email, None


def evaluate_compliance_send(company, *, force=False, test=False, actor=None, trigger_source='system'):
    """
    Decide whether a compliance notification may be sent.
    Returns dict with ok/blocked_reason/issues/fingerprint/recipient/vars.
    """
    settings = compliance_alert_settings()
    if not test and not force and not settings.get('enabled'):
        return {'ok': False, 'blocked_reason': 'ALERTS_DISABLED'}

    company_id = (company or {}).get('id')
    if not company_id:
        return {'ok': False, 'blocked_reason': 'INSUFFICIENT_VERIFIED_INFORMATION'}

    import app as app_mod
    if app_mod.is_pending_company_number((company or {}).get('company_number')):
        return {'ok': False, 'blocked_reason': 'INSUFFICIENT_VERIFIED_INFORMATION'}

    issues = collect_compliance_attention_issues(company)
    fingerprint = issue_fingerprint(issues)
    if not fingerprint:
        return {'ok': False, 'blocked_reason': 'NO_ATTENTION_REQUIRED', 'issues': []}

    summary_lines = issue_summary_lines(issues)
    if not summary_lines:
        return {'ok': False, 'blocked_reason': 'INSUFFICIENT_VERIFIED_INFORMATION', 'issues': issues}

    recipient, block = resolve_compliance_recipient(company, settings if not test else {**settings, 'test_mode': True})
    if block:
        return {'ok': False, 'blocked_reason': block, 'issues': issues, 'fingerprint': fingerprint}

    if not force and not test:
        prev = last_sent_log(company_id, fingerprint)
        if prev and within_interval(prev.get('created_at'), settings.get('interval_hours') or DEFAULT_INTERVAL_HOURS):
            return {
                'ok': False,
                'blocked_reason': 'WITHIN_24H_WINDOW',
                'issues': issues,
                'fingerprint': fingerprint,
                'recipient': recipient,
            }

    client = app_mod.resolve_client_user((company or {}).get('user_id'))
    first, full = director_greeting_name(company, allow_client_fallback=False, client=client)
    brand = brand_contact_block()
    vars_map = {
        'client_name': str((client or {}).get('full_name') or '') if client else '',
        'director_name': first or full,
        'company_name': str((company or {}).get('name') or '').strip(),
        'company_number': str((company or {}).get('company_number') or '').strip(),
        'reg_office': str((company or {}).get('reg_office') or '').strip(),
        'issue_title': summary_lines[0],
        'issue_summary': '\n'.join(f'• {line}' for line in summary_lines),
        'due_date': str((issues[0] or {}).get('due') or '') if issues else '',
        'days_overdue': '',
        **brand,
    }
    return {
        'ok': True,
        'issues': issues,
        'fingerprint': fingerprint,
        'recipient': recipient,
        'vars': vars_map,
        'settings': settings,
        'client': client,
    }


def send_compliance_attention_email(company, *, force=False, test=False, actor=None, trigger_source='system'):
    decision = evaluate_compliance_send(company, force=force, test=test, actor=actor, trigger_source=trigger_source)
    company_id = (company or {}).get('id')
    notification_id = str(uuid.uuid4())
    track_token = secrets.token_urlsafe(24)
    fingerprint = decision.get('fingerprint') or ''
    summary = decision.get('vars', {}).get('issue_summary') if decision.get('vars') else ''

    if not decision.get('ok'):
        reason = decision.get('blocked_reason') or 'BLOCKED'
        if company_id:
            log_compliance_notification(
                company_id=company_id,
                notification_id=notification_id,
                fingerprint=fingerprint,
                issue_summary=summary or reason,
                recipient=decision.get('recipient'),
                status='BLOCKED',
                blocked_reason=reason,
                trigger_source=trigger_source,
                sent_by_user_id=(actor or {}).get('id'),
            )
        return {
            'ok': False,
            'blocked_reason': reason,
            'notification_id': notification_id,
            'issues': decision.get('issues') or [],
        }

    subject, body_text, body_html, render_err, destinations = render_compliance_email_template(
        decision['vars'],
        test=test,
        issues=decision.get('issues') or [],
        track_token=track_token,
    )
    if render_err:
        log_compliance_notification(
            company_id=company_id,
            notification_id=notification_id,
            fingerprint=fingerprint,
            issue_summary=summary,
            recipient=decision.get('recipient'),
            status='BLOCKED',
            blocked_reason=render_err,
            trigger_source=trigger_source,
            sent_by_user_id=(actor or {}).get('id'),
        )
        return {'ok': False, 'blocked_reason': render_err, 'notification_id': notification_id}

    import app as app_mod

    try:
        ok, detail = app_mod.EmailService.send_notification_email(
            decision['recipient'], subject, body_text, body_html
        )
    except Exception as err:
        log_compliance_notification(
            company_id=company_id,
            notification_id=notification_id,
            fingerprint=fingerprint,
            issue_summary=summary,
            recipient=decision['recipient'],
            status='FAILED',
            blocked_reason=None,
            trigger_source=trigger_source,
            sent_by_user_id=(actor or {}).get('id'),
            error_category='PROVIDER_ERROR',
            payload_json=json.dumps({'error': str(err)[:400]}),
        )
        return {'ok': False, 'blocked_reason': 'PROVIDER_ERROR', 'notification_id': notification_id, 'error': str(err)}

    if not ok:
        category = 'INVALID_EMAIL' if 'recipient' in str(detail).lower() else 'PROVIDER_ERROR'
        log_compliance_notification(
            company_id=company_id,
            notification_id=notification_id,
            fingerprint=fingerprint,
            issue_summary=summary,
            recipient=decision['recipient'],
            status='FAILED',
            trigger_source=trigger_source,
            sent_by_user_id=(actor or {}).get('id'),
            error_category=category,
            payload_json=json.dumps({'detail': str(detail)[:400]}),
        )
        return {'ok': False, 'blocked_reason': category, 'notification_id': notification_id, 'error': detail}

    message_id = str(detail or '') if detail and detail is not True else None
    log_compliance_notification(
        company_id=company_id,
        notification_id=notification_id,
        fingerprint=fingerprint,
        issue_summary=summary,
        recipient=decision['recipient'],
        status='SENT',
        message_id=message_id,
        trigger_source=trigger_source,
        sent_by_user_id=(actor or {}).get('id'),
        track_token=track_token,
        payload_json=json.dumps({
            'subject': subject,
            'test': bool(test),
            'destinations': destinations or [],
            'issues': [
                {'code': i.get('code'), 'title': i.get('title')}
                for i in (decision.get('issues') or [])
            ],
        }),
    )
    execute_db(
        """
        UPDATE companies
        SET ch_alert_fingerprint = ?,
            ch_alert_sent_at = datetime('now'),
            compliance_last_notification_at = datetime('now'),
            compliance_last_notification_id = ?
        WHERE id = ?;
        """,
        (fingerprint, notification_id, company_id),
    )
    return {
        'ok': True,
        'notification_id': notification_id,
        'track_token': track_token,
        'recipient': decision['recipient'],
        'fingerprint': fingerprint,
        'issues': decision['issues'],
        'subject': subject,
        'message_id': message_id,
    }


def find_rmk_trading_company():
    row = query_db(
        """
        SELECT * FROM companies
        WHERE upper(name) LIKE '%RMK%TRADING%'
           OR upper(name) = 'RMK TRADING'
           OR upper(name) = 'RMK TRADING LTD'
           OR upper(name) = 'RMKR TRADING LTD'
        ORDER BY CASE WHEN upper(name) LIKE 'RMK %' THEN 0 ELSE 1 END, id DESC
        LIMIT 1;
        """,
        one=True,
    )
    return dict(row) if row else None


def send_rmk_trading_test_email(actor=None, recipient_override=None):
    company = find_rmk_trading_company()
    if not company:
        return {'ok': False, 'blocked_reason': 'COMPANY_NOT_FOUND', 'message': 'RMK TRADING company record not found.'}
    issues = collect_compliance_attention_issues(company)
    if not issues:
        return {
            'ok': False,
            'blocked_reason': 'NO_ATTENTION_REQUIRED',
            'message': f"{company.get('name')} has no verified attention-required issue.",
            'company': {'id': company.get('id'), 'name': company.get('name')},
        }
    recipient = str(recipient_override or compliance_alert_settings().get('test_recipient') or '').strip().lower()
    if not is_valid_client_notify_email(recipient):
        return {
            'ok': False,
            'blocked_reason': 'INVALID_EMAIL',
            'message': 'Enter a valid test recipient (e.g. Gmail / Outlook / company email).',
            'company': {'id': company.get('id'), 'name': company.get('name')},
        }
    upsert_setting('compliance_alerts_test_recipient', recipient)
    result = send_compliance_attention_email(
        dict(company),
        force=True,
        test=True,
        actor=actor,
        trigger_source='rmk_test',
    )
    result['company'] = {
        'id': company.get('id'),
        'name': company.get('name'),
        'company_number': company.get('company_number'),
        'director': company.get('director'),
    }
    result['issues'] = issues
    result['recipient'] = recipient
    if not result.get('ok') and result.get('blocked_reason') == 'NO_ATTENTION_REQUIRED':
        result['message'] = f"{company.get('name')} has no verified attention-required issue."
    return result


def companies_needing_compliance_scan(limit=500):
    rows = query_db(
        """
        SELECT * FROM companies
        WHERE company_number IS NOT NULL
          AND trim(company_number) != ''
          AND lower(company_number) NOT LIKE 'pending%'
        ORDER BY id ASC
        LIMIT ?;
        """,
        (limit,),
    ) or []
    return [dict(r) for r in rows]


def run_daily_compliance_alert_pass(*, force=False):
    settings = compliance_alert_settings()
    if not settings.get('enabled') and not force:
        return {'ran': False, 'reason': 'ALERTS_DISABLED', 'sent': 0, 'blocked': 0}
    sent = 0
    blocked = 0
    details = []
    for company in companies_needing_compliance_scan():
        result = send_compliance_attention_email(company, force=False, test=False, trigger_source='daily_scheduler')
        if result.get('ok'):
            sent += 1
        else:
            blocked += 1
        details.append({'company_id': company.get('id'), 'name': company.get('name'), **{k: result.get(k) for k in ('ok', 'blocked_reason', 'notification_id')}})
    return {'ran': True, 'sent': sent, 'blocked': blocked, 'details': details}


_compliance_worker_started = False
_compliance_worker_lock = None
_last_compliance_run_date = None


def start_compliance_alert_worker():
    global _compliance_worker_started, _compliance_worker_lock
    if os.environ.get('CRM_TESTING') == '1':
        return False
    import threading
    if _compliance_worker_lock is None:
        _compliance_worker_lock = threading.Lock()
    with _compliance_worker_lock:
        if _compliance_worker_started:
            return False

        def _loop():
            global _last_compliance_run_date
            import time
            while True:
                try:
                    ensure_compliance_alert_settings()
                    settings = compliance_alert_settings()
                    now = karachi_now()
                    run_key = now.date().isoformat()
                    if (
                        settings.get('enabled')
                        and now.hour == int(settings.get('send_hour') or DEFAULT_SEND_HOUR)
                        and _last_compliance_run_date != run_key
                    ):
                        # Stay inside the 11:00 hour Asia/Karachi; mark date before run for idempotency.
                        _last_compliance_run_date = run_key
                        print(f"[ComplianceAlerts] Starting daily pass at {now.isoformat()}")
                        summary = run_daily_compliance_alert_pass()
                        print(f"[ComplianceAlerts] Pass complete: {summary.get('sent')} sent, {summary.get('blocked')} blocked")
                except Exception as err:
                    print(f"[ComplianceAlerts] Worker error: {err}")
                time.sleep(30)

        thread = threading.Thread(target=_loop, name='compliance-alerts', daemon=True)
        thread.start()
        _compliance_worker_started = True
        print('[ComplianceAlerts] Asia/Karachi daily worker started')
        return True
