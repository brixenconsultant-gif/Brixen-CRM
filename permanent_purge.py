"""
Permanent manual-delete ledger for Brixen CRM.

When staff manually delete a customer, company, or order:
  1. Hard-delete the live row tree from hypetex.db
  2. Record fingerprints in storage/permanent_purge_ledger.json
     (OUTSIDE the main DB so a DB restore cannot bring them back)
  3. Scrub the same fingerprints from known backup .db files
  4. On startup, enforce the ledger against live again

Never restore a manually purged email, company number, or order number.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()

_UK_NUM_RE = re.compile(r'[^A-Z0-9]', re.I)


def _base_dir():
    return os.path.dirname(os.path.abspath(__file__))


def storage_dir():
    env = os.environ.get('STORAGE_PATH')
    if env:
        return env
    return os.path.join(_base_dir(), 'storage')


def ledger_path():
    return os.path.join(storage_dir(), 'permanent_purge_ledger.json')


def normalize_email(value):
    return str(value or '').strip().lower()


def normalize_company_number(value):
    return _UK_NUM_RE.sub('', str(value or '').strip().upper())


def normalize_order_number(value):
    return str(value or '').strip().upper()


def _empty_ledger():
    return {
        'version': 1,
        'emails': [],
        'company_numbers': [],
        'order_numbers': [],
        'wordpress_user_ids': [],
        'events': [],
    }


def load_ledger():
    path = ledger_path()
    if not os.path.isfile(path):
        return _empty_ledger()
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh) or {}
    except Exception:
        return _empty_ledger()
    out = _empty_ledger()
    for key in ('emails', 'company_numbers', 'order_numbers', 'wordpress_user_ids', 'events'):
        val = data.get(key)
        if isinstance(val, list):
            out[key] = val
    return out


def save_ledger(ledger):
    path = ledger_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(ledger, fh, indent=2, sort_keys=True)
        fh.write('\n')
    os.replace(tmp, path)


def _unique_append(bucket, value):
    if not value:
        return False
    if value in bucket:
        return False
    bucket.append(value)
    return True


def record_purge_event(*, kind, actor=None, emails=None, company_numbers=None,
                       order_numbers=None, wordpress_user_ids=None, detail=None):
    """Append fingerprints to the permanent ledger (survives hypetex.db restore)."""
    with _LOCK:
        ledger = load_ledger()
        changed = False
        email_list = [normalize_email(e) for e in (emails or []) if normalize_email(e)]
        num_list = [normalize_company_number(n) for n in (company_numbers or []) if normalize_company_number(n)]
        order_list = [normalize_order_number(o) for o in (order_numbers or []) if normalize_order_number(o)]
        wp_list = [str(w).strip() for w in (wordpress_user_ids or []) if str(w or '').strip()]

        for e in email_list:
            changed = _unique_append(ledger['emails'], e) or changed
        for n in num_list:
            changed = _unique_append(ledger['company_numbers'], n) or changed
        for o in order_list:
            changed = _unique_append(ledger['order_numbers'], o) or changed
        for w in wp_list:
            changed = _unique_append(ledger['wordpress_user_ids'], w) or changed

        ledger['events'].append({
            'at': datetime.now(timezone.utc).isoformat(),
            'kind': str(kind or 'manual_delete'),
            'actor_id': (actor or {}).get('id'),
            'actor_email': (actor or {}).get('email'),
            'emails': email_list,
            'company_numbers': num_list,
            'order_numbers': order_list,
            'wordpress_user_ids': wp_list,
            'detail': detail or '',
        })
        # Keep event history bounded.
        if len(ledger['events']) > 5000:
            ledger['events'] = ledger['events'][-5000:]
        save_ledger(ledger)
        return {
            'emails': email_list,
            'company_numbers': num_list,
            'order_numbers': order_list,
            'wordpress_user_ids': wp_list,
            'changed': changed,
        }


def is_purged_email(email):
    return normalize_email(email) in set(load_ledger().get('emails') or [])


def is_purged_company_number(company_number):
    return normalize_company_number(company_number) in set(load_ledger().get('company_numbers') or [])


def is_purged_order_number(order_number):
    return normalize_order_number(order_number) in set(load_ledger().get('order_numbers') or [])


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
        (name,),
    ).fetchone()
    return bool(row)


def _safe_exec(conn, sql, params=()):
    try:
        conn.execute(sql, params)
        return True
    except sqlite3.Error:
        return False


def hard_delete_order_ids(conn, order_ids):
    ids = [int(x) for x in order_ids if str(x).isdigit() or isinstance(x, int)]
    if not ids:
        return []
    placeholders = ','.join('?' for _ in ids)
    doc_paths = []
    if _table_exists(conn, 'documents'):
        rows = conn.execute(
            f"SELECT file_path FROM documents WHERE order_id IN ({placeholders});",
            ids,
        ).fetchall()
        doc_paths = [r[0] for r in rows if r and r[0]]
        _safe_exec(conn, f"DELETE FROM documents WHERE order_id IN ({placeholders});", ids)
    for table, col in (
        ('invoices', 'order_id'),
        ('order_line_items', 'order_id'),
        ('order_timeline', 'order_id'),
        ('tasks', 'order_id'),
    ):
        if _table_exists(conn, table):
            _safe_exec(conn, f"DELETE FROM {table} WHERE {col} IN ({placeholders});", ids)
    if _table_exists(conn, 'orders'):
        _safe_exec(conn, f"DELETE FROM orders WHERE id IN ({placeholders});", ids)
    return doc_paths


def hard_delete_company_ids(conn, company_ids):
    ids = [int(x) for x in company_ids if str(x).isdigit() or isinstance(x, int)]
    if not ids:
        return []
    placeholders = ','.join('?' for _ in ids)
    doc_paths = []

    order_ids = []
    if _table_exists(conn, 'orders'):
        order_ids = [
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM orders WHERE company_id IN ({placeholders});",
                ids,
            ).fetchall()
        ]
    doc_paths.extend(hard_delete_order_ids(conn, order_ids))

    if _table_exists(conn, 'documents'):
        rows = conn.execute(
            f"SELECT file_path FROM documents WHERE company_id IN ({placeholders});",
            ids,
        ).fetchall()
        doc_paths.extend(r[0] for r in rows if r and r[0])
        _safe_exec(conn, f"DELETE FROM documents WHERE company_id IN ({placeholders});", ids)

    if _table_exists(conn, 'support_tickets'):
        if _table_exists(conn, 'support_messages'):
            _safe_exec(
                conn,
                f"""
                DELETE FROM support_messages
                WHERE ticket_id IN (
                    SELECT id FROM support_tickets WHERE company_id IN ({placeholders})
                );
                """,
                ids,
            )
        _safe_exec(conn, f"DELETE FROM support_tickets WHERE company_id IN ({placeholders});", ids)

    for table in (
        'tasks', 'company_directors', 'company_owners', 'addresses', 'proxies',
        'registered_agents', 'company_accounts', 'company_book_entries',
        'company_email_verification_history', 'compliance_notification_log',
        'dismissed_company_cards',
    ):
        if _table_exists(conn, table):
            # dismissed_company_cards has no company_id — skip unless present
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table});").fetchall()}
            if 'company_id' in cols:
                _safe_exec(conn, f"DELETE FROM {table} WHERE company_id IN ({placeholders});", ids)

    if _table_exists(conn, 'invoices'):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices);").fetchall()}
        if 'company_id' in cols:
            _safe_exec(conn, f"DELETE FROM invoices WHERE company_id IN ({placeholders});", ids)

    if _table_exists(conn, 'companies'):
        _safe_exec(conn, f"DELETE FROM companies WHERE id IN ({placeholders});", ids)
    return doc_paths


def hard_delete_client_ids(conn, user_ids):
    ids = [int(x) for x in user_ids if str(x).isdigit() or isinstance(x, int)]
    if not ids:
        return []
    placeholders = ','.join('?' for _ in ids)
    doc_paths = []

    company_ids = []
    if _table_exists(conn, 'companies'):
        company_ids = [
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM companies WHERE user_id IN ({placeholders});",
                ids,
            ).fetchall()
        ]
    doc_paths.extend(hard_delete_company_ids(conn, company_ids))

    order_ids = []
    if _table_exists(conn, 'orders'):
        order_ids = [
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM orders WHERE user_id IN ({placeholders});",
                ids,
            ).fetchall()
        ]
    doc_paths.extend(hard_delete_order_ids(conn, order_ids))

    if _table_exists(conn, 'documents'):
        rows = conn.execute(
            f"SELECT file_path FROM documents WHERE user_id IN ({placeholders});",
            ids,
        ).fetchall()
        doc_paths.extend(r[0] for r in rows if r and r[0])
        _safe_exec(conn, f"DELETE FROM documents WHERE user_id IN ({placeholders});", ids)

    if _table_exists(conn, 'support_tickets'):
        if _table_exists(conn, 'support_messages'):
            _safe_exec(
                conn,
                f"""
                DELETE FROM support_messages
                WHERE ticket_id IN (
                    SELECT id FROM support_tickets WHERE user_id IN ({placeholders})
                );
                """,
                ids,
            )
        _safe_exec(conn, f"DELETE FROM support_tickets WHERE user_id IN ({placeholders});", ids)

    for table in ('invoices', 'notifications', 'user_sessions', 'addresses', 'tasks'):
        if _table_exists(conn, table):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table});").fetchall()}
            if 'user_id' in cols:
                _safe_exec(conn, f"DELETE FROM {table} WHERE user_id IN ({placeholders});", ids)

    if _table_exists(conn, 'users'):
        _safe_exec(
            conn,
            f"DELETE FROM users WHERE role = 'CLIENT' AND id IN ({placeholders});",
            ids,
        )
    return doc_paths


def scrub_connection(conn, ledger=None):
    """Remove any ledger-matching rows from an open sqlite connection."""
    ledger = ledger or load_ledger()
    emails = [normalize_email(e) for e in (ledger.get('emails') or []) if normalize_email(e)]
    numbers = [normalize_company_number(n) for n in (ledger.get('company_numbers') or []) if normalize_company_number(n)]
    orders = [normalize_order_number(o) for o in (ledger.get('order_numbers') or []) if normalize_order_number(o)]
    wps = [str(w).strip() for w in (ledger.get('wordpress_user_ids') or []) if str(w or '').strip()]

    summary = {'clients': 0, 'companies': 0, 'orders': 0, 'doc_paths': []}

    if _table_exists(conn, 'users') and (emails or wps):
        client_ids = set()
        for email in emails:
            for row in conn.execute(
                "SELECT id FROM users WHERE role='CLIENT' AND lower(email)=lower(?);",
                (email,),
            ).fetchall():
                client_ids.add(int(row[0]))
        for wp in wps:
            for row in conn.execute(
                "SELECT id FROM users WHERE role='CLIENT' AND wordpress_user_id=?;",
                (wp,),
            ).fetchall():
                client_ids.add(int(row[0]))
        if client_ids:
            summary['doc_paths'].extend(hard_delete_client_ids(conn, sorted(client_ids)))
            summary['clients'] += len(client_ids)

    if _table_exists(conn, 'companies') and numbers:
        company_ids = set()
        for num in numbers:
            for row in conn.execute(
                """
                SELECT id FROM companies
                WHERE upper(replace(replace(coalesce(company_number,''),' ',''),'-','')) = ?
                """,
                (num,),
            ).fetchall():
                company_ids.add(int(row[0]))
        if company_ids:
            summary['doc_paths'].extend(hard_delete_company_ids(conn, sorted(company_ids)))
            summary['companies'] += len(company_ids)

    if _table_exists(conn, 'orders') and orders:
        order_ids = set()
        for onum in orders:
            for row in conn.execute(
                "SELECT id FROM orders WHERE upper(trim(coalesce(order_number,''))) = ?;",
                (onum,),
            ).fetchall():
                order_ids.add(int(row[0]))
        if order_ids:
            summary['doc_paths'].extend(hard_delete_order_ids(conn, sorted(order_ids)))
            summary['orders'] += len(order_ids)

    conn.commit()
    return summary


def scrub_database_file(db_path, ledger=None):
    path = os.path.abspath(db_path)
    if not os.path.isfile(path) or not path.endswith('.db'):
        return None
    # Skip empty / sidecars
    if os.path.getsize(path) < 100:
        return None
    try:
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute('PRAGMA foreign_keys = OFF;')
            return scrub_connection(conn, ledger=ledger)
        finally:
            conn.close()
    except sqlite3.Error as err:
        return {'error': str(err), 'path': path}


def discover_backup_databases(app_dir=None):
    root = app_dir or _base_dir()
    found = []
    candidates = [
        root,
        os.path.join(root, 'backups'),
        os.path.join(root, 'backups', 'db'),
    ]
    live = os.path.abspath(os.path.join(root, 'hypetex.db'))
    for folder in candidates:
        if not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.endswith('.db'):
                continue
            if name.endswith('-wal') or name.endswith('-shm'):
                continue
            full = os.path.abspath(os.path.join(folder, name))
            if full == live:
                continue
            # Only CRM sqlite backups
            if 'hypetex' not in name.lower() and 'safety' not in name.lower():
                continue
            found.append(full)
    return sorted(set(found))


def scrub_all_backups(app_dir=None, ledger=None):
    ledger = ledger or load_ledger()
    results = []
    for path in discover_backup_databases(app_dir=app_dir):
        summary = scrub_database_file(path, ledger=ledger)
        if summary:
            summary = dict(summary)
            summary['path'] = path
            results.append(summary)
    return results


def enforce_live_database(db_path=None):
    """Re-apply permanent purges to the live DB (blocks accidental restore)."""
    try:
        import db as db_mod
        path = db_path or getattr(db_mod, 'DB_PATH', None) or os.path.join(_base_dir(), 'hypetex.db')
    except Exception:
        path = db_path or os.path.join(_base_dir(), 'hypetex.db')
    if not os.path.isfile(path):
        return {'skipped': True}
    ledger = load_ledger()
    if not any(ledger.get(k) for k in ('emails', 'company_numbers', 'order_numbers', 'wordpress_user_ids')):
        return {'empty': True}
    return scrub_database_file(path, ledger=ledger) or {'empty': True}


def purge_company_from_live(*, company, actor=None, scrub_backups=True):
    from db import get_db

    company = dict(company or {})
    cid = company.get('id')
    if not cid:
        return {'error': 'missing company id'}

    orders = []
    try:
        from db import query_db
        orders = query_db(
            "SELECT id, order_number FROM orders WHERE company_id = ?;",
            (cid,),
        ) or []
    except Exception:
        orders = []

    conn = get_db()
    doc_paths = hard_delete_company_ids(conn, [cid])
    conn.commit()

    event = record_purge_event(
        kind='company',
        actor=actor,
        company_numbers=[company.get('company_number')],
        order_numbers=[o.get('order_number') for o in orders],
        detail=f"Permanently deleted company {company.get('name')} ({company.get('company_number')})",
    )
    backup_results = scrub_all_backups() if scrub_backups else []
    return {'event': event, 'doc_paths': doc_paths, 'backups': backup_results}


def purge_order_from_live(*, order, actor=None, scrub_backups=True):
    from db import get_db

    order = dict(order or {})
    oid = order.get('id')
    if not oid:
        return {'error': 'missing order id'}
    conn = get_db()
    doc_paths = hard_delete_order_ids(conn, [oid])
    conn.commit()
    event = record_purge_event(
        kind='order',
        actor=actor,
        order_numbers=[order.get('order_number')],
        detail=f"Permanently deleted order {order.get('order_number')}",
    )
    backup_results = scrub_all_backups() if scrub_backups else []
    return {'event': event, 'doc_paths': doc_paths, 'backups': backup_results}


def purge_clients_from_live(*, clients, actor=None, scrub_backups=True):
    """clients: list of dicts with id/email/wordpress_user_id and optional companies/orders."""
    from db import get_db, query_db

    clients = list(clients or [])
    if not clients:
        return {'deleted': 0}

    emails = []
    wps = []
    company_numbers = []
    order_numbers = []
    ids = []
    for c in clients:
        ids.append(int(c['id']))
        emails.append(c.get('email'))
        wps.append(c.get('wordpress_user_id'))
        for row in (query_db("SELECT company_number FROM companies WHERE user_id=?;", (c['id'],)) or []):
            company_numbers.append(row.get('company_number'))
        for row in (query_db("SELECT order_number FROM orders WHERE user_id=?;", (c['id'],)) or []):
            order_numbers.append(row.get('order_number'))

    conn = get_db()
    doc_paths = hard_delete_client_ids(conn, ids)
    conn.commit()

    event = record_purge_event(
        kind='client',
        actor=actor,
        emails=emails,
        company_numbers=company_numbers,
        order_numbers=order_numbers,
        wordpress_user_ids=wps,
        detail=f"Permanently deleted {len(ids)} client account(s)",
    )
    backup_results = scrub_all_backups() if scrub_backups else []
    return {
        'deleted': len(ids),
        'event': event,
        'doc_paths': doc_paths,
        'backups': backup_results,
    }
