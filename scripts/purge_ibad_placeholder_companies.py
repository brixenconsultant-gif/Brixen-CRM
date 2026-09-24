#!/usr/bin/env python3
"""
Permanently delete companies still parked on a B2B agent account when there is
no usable personal client email (registered / owner / order email).

Default agent: ibads111298@gmail.com
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from db import execute_db, query_db  # noqa: E402

PLACEHOLDER_EMAIL_RE = re.compile(r'@brixen-pending\.local$', re.I)
AGENT_DEFAULT = 'ibads111298@gmail.com'


def usable_email(value, *, agent_email):
    email = str(value or '').strip().lower()
    if not email or '@' not in email:
        return False
    if PLACEHOLDER_EMAIL_RE.search(email):
        return False
    if email == str(agent_email or '').strip().lower():
        return False
    return True


def company_has_real_email(company, owners_by_company, orders_by_company, agent_email):
    cid = int(company['id'])
    emails = [company.get('registered_email')]
    emails.extend(owners_by_company.get(cid, []))
    emails.extend(orders_by_company.get(cid, []))
    return any(usable_email(e, agent_email=agent_email) for e in emails)


def delete_company_tree(company_id):
    """Hard-delete a company and dependent CRM rows. Returns doc file paths removed from DB."""
    docs = query_db("SELECT id, file_path FROM documents WHERE company_id = ?;", (company_id,)) or []
    paths = [d.get('file_path') for d in docs if d.get('file_path')]
    order_ids = [
        int(r['id'])
        for r in (query_db("SELECT id FROM orders WHERE company_id = ?;", (company_id,)) or [])
    ]

    # Ticket messages first.
    execute_db(
        """
        DELETE FROM support_messages
        WHERE ticket_id IN (SELECT id FROM support_tickets WHERE company_id = ?);
        """,
        (company_id,),
    )
    execute_db("DELETE FROM support_tickets WHERE company_id = ?;", (company_id,))
    execute_db("DELETE FROM tasks WHERE company_id = ?;", (company_id,))
    execute_db("DELETE FROM company_directors WHERE company_id = ?;", (company_id,))
    execute_db("DELETE FROM company_owners WHERE company_id = ?;", (company_id,))
    execute_db("DELETE FROM addresses WHERE company_id = ?;", (company_id,))
    execute_db("DELETE FROM proxies WHERE company_id = ?;", (company_id,))
    execute_db("DELETE FROM registered_agents WHERE company_id = ?;", (company_id,))
    execute_db("DELETE FROM company_accounts WHERE company_id = ?;", (company_id,))
    execute_db("DELETE FROM company_book_entries WHERE company_id = ?;", (company_id,))
    try:
        execute_db("DELETE FROM company_email_verification_history WHERE company_id = ?;", (company_id,))
    except Exception:
        pass
    try:
        execute_db("DELETE FROM compliance_notification_log WHERE company_id = ?;", (company_id,))
    except Exception:
        pass
    execute_db("DELETE FROM documents WHERE company_id = ?;", (company_id,))

    if order_ids:
        placeholders = ','.join('?' for _ in order_ids)
        execute_db(f"DELETE FROM invoices WHERE order_id IN ({placeholders});", order_ids)
        execute_db(f"DELETE FROM order_line_items WHERE order_id IN ({placeholders});", order_ids)
        execute_db(f"DELETE FROM order_timeline WHERE order_id IN ({placeholders});", order_ids)
        execute_db(f"DELETE FROM documents WHERE order_id IN ({placeholders});", order_ids)
        execute_db(f"DELETE FROM orders WHERE id IN ({placeholders});", order_ids)

    execute_db("DELETE FROM companies WHERE id = ?;", (company_id,))
    return paths


def purge(*, agent_email=AGENT_DEFAULT, dry_run=True):
    agent = query_db(
        "SELECT id, email, full_name FROM users WHERE lower(email)=lower(?) AND role='CLIENT';",
        (agent_email,),
        one=True,
    )
    if not agent:
        raise SystemExit(f'Agent not found: {agent_email}')
    agent_id = int(agent['id'])

    companies = query_db("SELECT * FROM companies WHERE user_id = ?;", (agent_id,)) or []
    owners = query_db("SELECT company_id, form_email FROM company_owners;") or []
    orders = query_db(
        "SELECT id, company_id, owner_form_email FROM orders WHERE user_id = ?;",
        (agent_id,),
    ) or []

    owners_by_company = {}
    for row in owners:
        if row.get('company_id') is None:
            continue
        owners_by_company.setdefault(int(row['company_id']), []).append(row.get('form_email'))
    orders_by_company = {}
    for row in orders:
        if row.get('company_id') is None:
            continue
        orders_by_company.setdefault(int(row['company_id']), []).append(row.get('owner_form_email'))

    trash = []
    keep = []
    for company in companies:
        if company_has_real_email(company, owners_by_company, orders_by_company, agent_email):
            keep.append(company)
        else:
            trash.append(company)

    result = {
        'agent': dict(agent),
        'dry_run': dry_run,
        'keep_count': len(keep),
        'trash_count': len(trash),
        'deleted_ids': [],
        'sample_trash': [
            {
                'id': c['id'],
                'name': c.get('name'),
                'company_number': c.get('company_number'),
                'registered_email': c.get('registered_email'),
            }
            for c in trash[:15]
        ],
    }
    if dry_run:
        return result

    removed_files = []
    for company in trash:
        paths = delete_company_tree(int(company['id']))
        removed_files.extend(paths)
        result['deleted_ids'].append(int(company['id']))

    # Best-effort unlink stored files.
    for path in removed_files:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass

    result['companies_left_on_agent'] = query_db(
        "SELECT COUNT(*) AS c FROM companies WHERE user_id = ?;", (agent_id,), one=True
    )['c']
    result['orders_left_on_agent'] = query_db(
        "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?;", (agent_id,), one=True
    )['c']
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent-email', default=AGENT_DEFAULT)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    import json
    print(json.dumps(purge(agent_email=args.agent_email, dry_run=not args.apply), indent=2, default=str))


if __name__ == '__main__':
    main()
