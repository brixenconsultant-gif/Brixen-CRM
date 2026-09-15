#!/usr/bin/env python3
"""
Re-link companies/orders that were parked under a B2B agent (e.g. Syed Ibad Ahmad)
onto personal CLIENT portal accounts.

Priority for company owner email:
  1) company_owners.form_email
  2) companies.registered_email
  3) linked order owner_form_email

Skips placeholder emails (*@brixen-pending.local) and the agent mailbox itself.
Creates a Normal CLIENT when a real personal email has no portal user yet.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from db import execute_db, hash_password, query_db, unusable_password_hash  # noqa: E402

PLACEHOLDER_EMAIL_RE = re.compile(r'@brixen-pending\.local$', re.I)
AGENT_DEFAULT = 'ibads111298@gmail.com'


def norm_email(value):
    email = str(value or '').strip().lower()
    if not email or '@' not in email:
        return ''
    if PLACEHOLDER_EMAIL_RE.search(email):
        return ''
    if email in {'noreply@brixenconsultants.com', 'intake.unassigned@brixen-pending.local'}:
        return ''
    return email


def load_clients():
    rows = query_db("SELECT id, email, full_name, role FROM users WHERE role = 'CLIENT';") or []
    by_email = {}
    for row in rows:
        email = norm_email(row.get('email'))
        if email:
            by_email[email] = dict(row)
    return by_email


def ensure_client(email, full_name, clients, *, dry_run=False):
    email = norm_email(email)
    if not email:
        return None, 'no_email'
    if email in clients:
        return clients[email], 'existing'
    existing = query_db(
        "SELECT id, email, full_name, role FROM users WHERE lower(email)=lower(?);",
        (email,),
        one=True,
    )
    if existing:
        if str(existing.get('role') or '').upper() == 'CLIENT':
            clients[email] = dict(existing)
            return clients[email], 'existing'
        return None, 'email_used_by_non_client'
    name = str(full_name or '').strip() or email.split('@')[0]
    if dry_run:
        fake = {'id': None, 'email': email, 'full_name': name}
        clients[email] = fake
        return fake, 'would_create'
    user_id = execute_db(
        """
        INSERT INTO users (
            email, password_hash, full_name, role, status,
            is_b2b, client_type, account_type
        ) VALUES (?, ?, ?, 'CLIENT', 'Active', 0, 'Normal', 'Normal Client');
        """,
        (email, unusable_password_hash(), name),
    )
    row = query_db("SELECT id, email, full_name, role FROM users WHERE id = ?;", (user_id,), one=True)
    clients[email] = dict(row)
    return clients[email], 'created'


def company_candidate_email(company, owners_by_company, orders_by_company, clients_by_name=None):
    cid = int(company['id'])
    for owner in owners_by_company.get(cid, []):
        email = norm_email(owner.get('form_email'))
        if email:
            return email, owner.get('full_name') or company.get('director'), 'company_owner'
    email = norm_email(company.get('registered_email'))
    if email:
        return email, company.get('director'), 'registered_email'
    for order in orders_by_company.get(cid, []):
        email = norm_email(order.get('owner_form_email'))
        if email:
            return email, order.get('owner_name') or company.get('director'), 'order_owner'
    # Last resort: unique director name match to an existing personal client.
    director = re.sub(r'\s+', ' ', str(company.get('director') or '').strip().lower())
    if director and clients_by_name and director in clients_by_name and len(clients_by_name[director]) == 1:
        hit = clients_by_name[director][0]
        return norm_email(hit.get('email')), hit.get('full_name') or company.get('director'), 'director_name'
    return '', company.get('director'), None


def relink(*, agent_email=AGENT_DEFAULT, dry_run=True, create_missing=True):
    agent = query_db(
        "SELECT id, email, full_name FROM users WHERE lower(email)=lower(?) AND role='CLIENT';",
        (agent_email,),
        one=True,
    )
    if not agent:
        raise SystemExit(f'Agent client not found: {agent_email}')
    agent_id = int(agent['id'])
    clients = load_clients()
    # Never re-link onto the agent mailbox as "personal".
    clients.pop(norm_email(agent_email), None)
    clients_by_name = {}
    for row in clients.values():
        key = re.sub(r'\s+', ' ', str(row.get('full_name') or '').strip().lower())
        if key:
            clients_by_name.setdefault(key, []).append(row)

    companies = query_db("SELECT * FROM companies WHERE user_id = ?;", (agent_id,)) or []
    owners = query_db("SELECT company_id, full_name, form_email FROM company_owners;") or []
    orders = query_db(
        "SELECT id, company_id, user_id, owner_name, owner_form_email FROM orders WHERE user_id = ?;",
        (agent_id,),
    ) or []

    owners_by_company = {}
    for row in owners:
        if row.get('company_id') is None:
            continue
        owners_by_company.setdefault(int(row['company_id']), []).append(row)
    orders_by_company = {}
    for row in orders:
        if row.get('company_id') is None:
            continue
        orders_by_company.setdefault(int(row['company_id']), []).append(row)

    stats = {
        'companies_total': len(companies),
        'companies_moved': 0,
        'companies_kept_on_agent': 0,
        'orders_moved': 0,
        'orders_kept_on_agent': 0,
        'clients_created': 0,
        'clients_existing': 0,
        'invoices_moved': 0,
        'documents_moved': 0,
    }
    moved = []

    for company in companies:
        email, name, source = company_candidate_email(
            company, owners_by_company, orders_by_company, clients_by_name=clients_by_name,
        )
        if not email:
            stats['companies_kept_on_agent'] += 1
            continue
        if create_missing:
            client, created = ensure_client(email, name, clients, dry_run=dry_run)
        else:
            client = clients.get(email)
            created = 'existing' if client else 'missing'
            if not client:
                stats['companies_kept_on_agent'] += 1
                continue
        if created == 'email_used_by_non_client' or not client:
            stats['companies_kept_on_agent'] += 1
            continue
        if created == 'created' or created == 'would_create':
            stats['clients_created'] += 1
        elif created == 'existing':
            stats['clients_existing'] += 1
        target_id = client.get('id')
        if dry_run:
            stats['companies_moved'] += 1
            moved.append({
                'company_id': company['id'],
                'name': company.get('name'),
                'email': email,
                'source': source,
                'target_user_id': target_id,
                'created': created,
            })
            continue
        execute_db("UPDATE companies SET user_id = ? WHERE id = ?;", (target_id, company['id']))
        execute_db(
            "UPDATE orders SET user_id = ? WHERE company_id = ? AND user_id = ?;",
            (target_id, company['id'], agent_id),
        )
        execute_db(
            """
            UPDATE invoices SET user_id = ?
            WHERE user_id = ?
              AND order_id IN (SELECT id FROM orders WHERE company_id = ?);
            """,
            (target_id, agent_id, company['id']),
        )
        execute_db(
            "UPDATE documents SET user_id = ? WHERE company_id = ? AND user_id = ?;",
            (target_id, company['id'], agent_id),
        )
        stats['companies_moved'] += 1
        moved.append({
            'company_id': company['id'],
            'name': company.get('name'),
            'email': email,
            'source': source,
            'target_user_id': target_id,
            'created': created,
        })

    # Orders still on agent with owner email but no/weak company link.
    leftover_orders = query_db(
        "SELECT id, company_id, owner_name, owner_form_email FROM orders WHERE user_id = ?;",
        (agent_id,),
    ) or []
    for order in leftover_orders:
        email = norm_email(order.get('owner_form_email'))
        if not email:
            stats['orders_kept_on_agent'] += 1
            continue
        if create_missing:
            client, created = ensure_client(email, order.get('owner_name'), clients, dry_run=dry_run)
        else:
            client = clients.get(email)
            created = 'existing' if client else 'missing'
            if not client:
                stats['orders_kept_on_agent'] += 1
                continue
        if created in ('created', 'would_create'):
            stats['clients_created'] += 1
        target_id = client.get('id')
        if dry_run:
            stats['orders_moved'] += 1
            continue
        execute_db("UPDATE orders SET user_id = ? WHERE id = ?;", (target_id, order['id']))
        execute_db("UPDATE invoices SET user_id = ? WHERE order_id = ? AND user_id = ?;", (target_id, order['id'], agent_id))
        execute_db("UPDATE documents SET user_id = ? WHERE order_id = ? AND user_id = ?;", (target_id, order['id'], agent_id))
        stats['orders_moved'] += 1

    # Refresh leftover company count under agent.
    if not dry_run:
        stats['companies_left_on_agent'] = query_db(
            "SELECT COUNT(*) AS c FROM companies WHERE user_id = ?;", (agent_id,), one=True
        )['c']
        stats['orders_left_on_agent'] = query_db(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?;", (agent_id,), one=True
        )['c']
        stats['invoices_left_on_agent'] = query_db(
            "SELECT COUNT(*) AS c FROM invoices WHERE user_id = ?;", (agent_id,), one=True
        )['c']

    return {
        'agent': dict(agent),
        'dry_run': dry_run,
        'stats': stats,
        'sample_moves': moved[:20],
        'move_count': len(moved),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent-email', default=AGENT_DEFAULT)
    parser.add_argument('--apply', action='store_true', help='Write changes (default is dry-run)')
    parser.add_argument('--no-create', action='store_true', help='Only move onto existing clients')
    args = parser.parse_args()
    result = relink(
        agent_email=args.agent_email,
        dry_run=not args.apply,
        create_missing=not args.no_create,
    )
    import json
    print(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    main()
