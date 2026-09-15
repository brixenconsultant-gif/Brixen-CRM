#!/usr/bin/env python3
"""
Restore the pre-wipe backup, then delete ONLY CRM-created CLIENT accounts.

Keep: customers with numeric WordPress user ids (website sync).
Delete: local_user_*, local_client_*, empty wordpress_user_id, CRM test/guest placeholders.

If a CRM-created customer owns companies/orders and no matching website customer
exists by email, reassign that data to the Unlinked Guest holder so website
customers stay intact and business records are not destroyed with the CRM login.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime

APP = '/var/www/brixen-crm'
LIVE = os.path.join(APP, 'hypetex.db')
BACKUP = os.path.join(APP, 'hypetex-safety-before-customers-wipe-20260915_054254.db')
GUEST_EMAIL = 'guest.unlinked@brixenconsultants.com'


def is_website_customer(row):
    wp = str((row or {}).get('wordpress_user_id') or '').strip()
    return wp.isdigit()


def is_crm_created(row):
    return not is_website_customer(row)


def main():
    if not os.path.exists(BACKUP):
        raise SystemExit('Missing backup: %s' % BACKUP)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    pre = os.path.join(APP, 'hypetex-safety-before-restore-selective-customers-%s.db' % ts)
    shutil.copy2(LIVE, pre)
    print('snapshot_current', pre)

    for suffix in ('-wal', '-shm'):
        path = LIVE + suffix
        if os.path.exists(path):
            os.remove(path)
    shutil.copy2(BACKUP, LIVE)
    print('restored_from', BACKUP)

    sys.path.insert(0, APP)
    os.chdir(APP)
    from db import execute_db, query_db, unusable_password_hash
    import app as app_mod

    clients = query_db(
        """
        SELECT id, email, full_name, wordpress_user_id, created_at
        FROM users
        WHERE role = 'CLIENT'
        ORDER BY id;
        """
    ) or []

    keep_rows = [r for r in clients if is_website_customer(r)]
    delete_rows = [r for r in clients if is_crm_created(r)]
    keep_by_email = {
        str(r.get('email') or '').strip().lower(): r
        for r in keep_rows
        if str(r.get('email') or '').strip()
    }

    guest = query_db(
        "SELECT id, email, full_name, role FROM users WHERE lower(email)=lower(?);",
        (GUEST_EMAIL,),
        one=True,
    )
    if guest and str(guest.get('role')) == 'CLIENT':
        guest_id = int(guest['id'])
    else:
        guest_id = execute_db(
            """
            INSERT INTO users (
                wordpress_user_id, email, password_hash, full_name, role, status,
                is_b2b, client_type, account_type
            ) VALUES ('guest_unlinked', ?, ?, 'Unlinked Guest Customer', 'CLIENT', 'Active', 1, 'B2B', 'B2B Client (Brixen Website Panel)');
            """,
            (GUEST_EMAIL, unusable_password_hash()),
        )
        # If email existed as non-client, fall back to lookup.
        if not guest_id:
            guest = query_db(
                "SELECT id FROM users WHERE lower(email)=lower(?);",
                (GUEST_EMAIL,),
                one=True,
            )
            guest_id = int(guest['id'])

    print('website_keep', len(keep_rows))
    print('crm_candidates', len(delete_rows))

    delete_ids = []
    for row in delete_rows:
        cid = int(row['id'])
        email = str(row.get('email') or '').strip().lower()
        # Never delete the holding guest account itself in this pass if we need it.
        if email == GUEST_EMAIL:
            print('HOLD_GUEST', cid, email)
            continue

        companies = int((query_db(
            "SELECT COUNT(*) AS c FROM companies WHERE user_id = ?;", (cid,), one=True
        ) or {}).get('c') or 0)
        orders = int((query_db(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?;", (cid,), one=True
        ) or {}).get('c') or 0)

        target = keep_by_email.get(email)
        target_id = int(target['id']) if target else guest_id
        target_label = 'website:%s' % target['email'] if target else 'guest_holder'

        if companies or orders:
            execute_db("UPDATE companies SET user_id = ? WHERE user_id = ?;", (target_id, cid))
            execute_db("UPDATE orders SET user_id = ? WHERE user_id = ?;", (target_id, cid))
            execute_db("UPDATE invoices SET user_id = ? WHERE user_id = ?;", (target_id, cid))
            execute_db("UPDATE documents SET user_id = ? WHERE user_id = ?;", (target_id, cid))
            print('REASSIGN', cid, email, '->', target_label, 'companies', companies, 'orders', orders)

        delete_ids.append(cid)
        print('DELETE', cid, row.get('wordpress_user_id'), email, row.get('full_name'))

    actor = query_db(
        "SELECT id, email, full_name, role FROM users WHERE role = 'SUPER_ADMIN' ORDER BY id LIMIT 1;",
        one=True,
    ) or {'id': 1, 'role': 'SUPER_ADMIN', 'full_name': 'System', 'email': 'admin@brixenconsultants.com'}

    deleted = app_mod.remove_customer_accounts(delete_ids, actor=actor, force_delete_genuine=True)
    print('deleted_count', deleted)
    print('clients_left', query_db("SELECT COUNT(*) AS c FROM users WHERE role = 'CLIENT';", one=True))
    print('companies_left', query_db("SELECT COUNT(*) AS c FROM companies;", one=True))
    print('orders_left', query_db("SELECT COUNT(*) AS c FROM orders;", one=True))
    left = query_db(
        """
        SELECT id, email, full_name, wordpress_user_id,
               (SELECT COUNT(*) FROM companies c WHERE c.user_id = u.id) AS companies
        FROM users u
        WHERE role = 'CLIENT'
        ORDER BY id;
        """
    ) or []
    for row in left:
        print('LEFT', dict(row))


if __name__ == '__main__':
    main()
