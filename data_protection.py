"""
Genuine business-data protection for Brixen CRM.

Real client orders, registered companies, and invoices must never be wiped by
automatic startup jobs, seed scripts, or bulk cleanup. Manual deletion of locked
records requires an explicit Super Admin force flag.
"""
from __future__ import annotations

import os
import re

# Known demo / seed identifiers only — never treat these as production locks.
KNOWN_DUMMY_EMAILS = frozenset({
    'client1@acmecorp.co.uk',
    'v.smith@vantagecyber.co.uk',
    'oliver@quantumhorizon.com',
    'sophie@apexlogistics.co.uk',
    'david@nexusbiotech.co.uk',
    'wp.test.client@brixen.co.uk',
    'precrm.owner@example.com',
    'portal.client@acmecorp.co.uk',
    'argon2id.login.7c@example.test',
    'legacy.sha256.7c@example.test',
    'legacy.wrong.7c@example.test',
    'sync.created.7c@example.test',
    'profile.change.7c@example.test',
    'john.normal.452a27@example.com',
})

KNOWN_DUMMY_COMPANY_NUMBERS = frozenset({
    '13810022', '13820033', '13830044', '13840055', '13850066',
    '16967197', '16821965', '16991988', '17002492', '17001469',
    '17096366', '17095941', '17105420', '17125999', '14892011',
    '15012933', '15124088', '15239100', '15340199', '15451233',
})

DUMMY_ORDER_NUMBER_PREFIXES = ('#GB1034',)

# Official-looking UK company numbers (not REG-/pending placeholders).
_UK_COMPANY_NUMBER_RE = re.compile(
    r'^(?:[0-9]{8}|[A-Z]{2}[0-9]{6}|[A-Z]{1,2}[0-9]{5,8})$',
    re.IGNORECASE,
)

LOCKED_MESSAGE = (
    'This genuine business record is locked and cannot be deleted automatically. '
    'A Super Admin must confirm force_delete_genuine to remove it.'
)


def database_path():
    try:
        import db as db_mod
        return str(getattr(db_mod, 'DB_PATH', '') or '')
    except Exception:
        return os.environ.get('DATABASE_URL') or ''


def is_production_database(path=None):
    target = str(path or database_path() or '').strip()
    if os.environ.get('CRM_ENV', '').strip().lower() == 'production':
        return True
    if '/var/www/brixen-crm' in target.replace('\\', '/'):
        return True
    return False


def protection_enabled():
    """Protection is on everywhere except intentional test wipes."""
    if os.environ.get('ALLOW_GENUINE_DELETE', '').strip() == '1' and os.environ.get('CRM_TESTING') == '1':
        return False
    return True


def normalize_company_number(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').strip().upper())


def is_pending_placeholder_number(company_number):
    num = str(company_number or '').strip().upper()
    return (not num) or num.startswith('REG-') or num.startswith('PENDING')


def looks_like_official_company_number(company_number):
    num = normalize_company_number(company_number)
    if not num or is_pending_placeholder_number(num):
        return False
    if num in KNOWN_DUMMY_COMPANY_NUMBERS:
        return False
    return bool(_UK_COMPANY_NUMBER_RE.match(num))


def is_demo_email(email):
    return str(email or '').strip().lower() in KNOWN_DUMMY_EMAILS


def is_demo_order_number(order_number):
    text = str(order_number or '').strip().upper()
    return any(text.startswith(prefix.upper()) for prefix in DUMMY_ORDER_NUMBER_PREFIXES)


def _row_flag(row, key):
    if not row:
        return 0
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 1 if row.get(key) else 0


def company_is_genuine(company):
    """Registered / live company cards that must never be auto-deleted."""
    if not company:
        return False
    if _row_flag(company, 'data_locked'):
        return True
    number = company.get('company_number')
    if looks_like_official_company_number(number):
        return True
    if company.get('registration_notified_at') and looks_like_official_company_number(number):
        return True
    if str(company.get('inc_date') or '').strip() and looks_like_official_company_number(number):
        return True
    return False


def order_is_genuine(order):
    """Website / paid / invoiced orders that must never be auto-deleted."""
    if not order:
        return False
    if _row_flag(order, 'data_locked'):
        return True
    if is_demo_order_number(order.get('order_number')) and not str(order.get('woocommerce_order_id') or '').strip():
        return False
    if str(order.get('woocommerce_order_id') or '').strip():
        return True
    status = str(order.get('status') or '').strip().lower()
    if status in {'completed', 'processing', 'in progress', 'documents required', 'information required'}:
        # Still require a signal it is not pure seed junk.
        if str(order.get('order_number') or '').strip() and not is_demo_order_number(order.get('order_number')):
            return True
    return False


def invoice_is_genuine(invoice):
    if not invoice:
        return False
    if _row_flag(invoice, 'data_locked'):
        return True
    status = str(invoice.get('status') or '').strip().lower()
    if status in {'paid', 'partial paid', 'overdue', 'pending'}:
        return True
    if invoice.get('order_id'):
        return True
    return False


def refuse_auto_delete_company(company):
    if not protection_enabled():
        return None
    if company_is_genuine(company):
        return LOCKED_MESSAGE
    # Pending REG-* cards may still be absorbed; empty placeholders may be removed.
    return None


def refuse_auto_delete_order(order):
    if not protection_enabled():
        return None
    if order_is_genuine(order):
        return LOCKED_MESSAGE
    return None


def refuse_auto_delete_invoice(invoice):
    if not protection_enabled():
        return None
    if invoice_is_genuine(invoice):
        return LOCKED_MESSAGE
    return None


def actor_is_super_admin(actor):
    role = str((actor or {}).get('role') or '').strip().upper()
    return role in {'SUPER_ADMIN', 'SUPERADMIN'}


def refuse_manual_delete_company(company, *, force=False, actor=None):
    if not protection_enabled():
        return None
    if not company_is_genuine(company):
        return None
    if force and actor_is_super_admin(actor):
        return None
    return LOCKED_MESSAGE


def refuse_manual_delete_order(order, *, force=False, actor=None):
    if not protection_enabled():
        return None
    if not order_is_genuine(order):
        return None
    if force and actor_is_super_admin(actor):
        return None
    return LOCKED_MESSAGE


def refuse_manual_delete_invoice(invoice, *, force=False, actor=None):
    if not protection_enabled():
        return None
    if not invoice_is_genuine(invoice):
        return None
    if force and actor_is_super_admin(actor):
        return None
    return LOCKED_MESSAGE


def client_has_locked_business_data(user_id):
    """True when a client still owns genuine companies, orders, or invoices."""
    if not user_id or not protection_enabled():
        return False
    try:
        from db import query_db
    except Exception:
        return False
    company = query_db(
        """
        SELECT id, company_number, registration_notified_at, inc_date, data_locked
        FROM companies WHERE user_id = ?
        """,
        (user_id,),
    ) or []
    if any(company_is_genuine(row) for row in company):
        return True
    orders = query_db(
        """
        SELECT id, order_number, woocommerce_order_id, status, data_locked
        FROM orders WHERE user_id = ?
        """,
        (user_id,),
    ) or []
    if any(order_is_genuine(row) for row in orders):
        return True
    invoices = query_db(
        """
        SELECT id, order_id, status, data_locked
        FROM invoices WHERE user_id = ?
        """,
        (user_id,),
    ) or []
    if any(invoice_is_genuine(row) for row in invoices):
        return True
    return False


def refuse_client_account_purge(user_id, *, force=False, actor=None):
    if not protection_enabled():
        return None
    if not client_has_locked_business_data(user_id):
        return None
    if force and actor_is_super_admin(actor):
        return None
    return (
        'This client has locked genuine orders, companies, or invoices. '
        'Account purge blocked unless a Super Admin sets force_delete_genuine.'
    )


def assert_safe_to_wipe_database():
    """Hard stop for seed_db / destructive scripts against live or genuine data."""
    path = database_path()
    if is_production_database(path):
        raise RuntimeError(
            f'Refused to wipe production database at {path}. '
            'Never run seed_db.py or full DELETE wipes on live CRM data.'
        )
    # Disposable test DBs may be wiped. Production is already blocked above.
    if os.environ.get('CRM_TESTING') == '1':
        return
    if os.environ.get('ALLOW_DESTRUCTIVE_SEED') == '1':
        return
    try:
        from db import query_db
        locked = query_db(
            """
            SELECT
              (SELECT COUNT(*) FROM companies WHERE COALESCE(data_locked, 0) = 1) AS companies,
              (SELECT COUNT(*) FROM orders WHERE COALESCE(data_locked, 0) = 1) AS orders,
              (SELECT COUNT(*) FROM invoices WHERE COALESCE(data_locked, 0) = 1) AS invoices
            ;
            """,
            one=True,
        )
        if locked and (
            int(locked.get('companies') or 0)
            or int(locked.get('orders') or 0)
            or int(locked.get('invoices') or 0)
        ):
            raise RuntimeError(
                'Refused to wipe database: locked genuine records exist. '
                'Use a separate local DB, or set ALLOW_DESTRUCTIVE_SEED=1 only for disposable data.'
            )
    except RuntimeError:
        raise
    except Exception:
        # Fresh / empty DB has no tables yet — wipe is fine.
        return


def lock_genuine_records():
    """Backfill data_locked=1 for existing genuine companies, orders, invoices."""
    from db import execute_db, query_db

    companies = query_db(
        "SELECT id, company_number, registration_notified_at, inc_date, data_locked FROM companies;"
    ) or []
    locked_companies = 0
    for row in companies:
        if _row_flag(row, 'data_locked'):
            continue
        if company_is_genuine({**dict(row), 'data_locked': 0}):
            execute_db("UPDATE companies SET data_locked = 1 WHERE id = ?;", (row['id'],))
            locked_companies += 1

    orders = query_db(
        "SELECT id, order_number, woocommerce_order_id, status, data_locked FROM orders;"
    ) or []
    locked_orders = 0
    for row in orders:
        if _row_flag(row, 'data_locked'):
            continue
        if order_is_genuine({**dict(row), 'data_locked': 0}):
            execute_db("UPDATE orders SET data_locked = 1 WHERE id = ?;", (row['id'],))
            locked_orders += 1

    invoices = query_db(
        "SELECT id, order_id, status, data_locked FROM invoices;"
    ) or []
    locked_invoices = 0
    for row in invoices:
        if _row_flag(row, 'data_locked'):
            continue
        if invoice_is_genuine({**dict(row), 'data_locked': 0}):
            execute_db("UPDATE invoices SET data_locked = 1 WHERE id = ?;", (row['id'],))
            locked_invoices += 1

    return {
        'companies': locked_companies,
        'orders': locked_orders,
        'invoices': locked_invoices,
    }
