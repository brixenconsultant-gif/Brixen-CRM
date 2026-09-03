import sqlite3
import os
import json
import hashlib
import hmac
import secrets
import re
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, VerificationError

try:
    from argon2.exceptions import InvalidHashError
except ImportError:
    from argon2.exceptions import InvalidHash as InvalidHashError

def _load_env_file():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for fname in ('.env.production', '.env'):
        fpath = os.path.join(base_dir, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            k, v = line.split('=', 1)
                            k = k.strip()
                            v = v.strip().strip("'").strip('"')
                            if k and k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass
            break

_load_env_file()

DB_PATH = os.environ.get('DATABASE_URL') or os.path.join(os.path.dirname(__file__), 'hypetex.db')
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'schema.sql')

_ARGON2_HASHER = PasswordHasher(type=Type.ID)
_LEGACY_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_DUMMY_ARGON2_HASH = None


def _is_legacy_sha256(stored_hash):
    return isinstance(stored_hash, str) and bool(_LEGACY_SHA256_RE.fullmatch(stored_hash))


def _is_argon2id(stored_hash):
    return isinstance(stored_hash, str) and stored_hash.startswith('$argon2id$')


def _dummy_argon2_verify(password):
    global _DUMMY_ARGON2_HASH
    if _DUMMY_ARGON2_HASH is None:
        _DUMMY_ARGON2_HASH = hash_password(secrets.token_urlsafe(32))
    verify_password(password if password is not None else '', _DUMMY_ARGON2_HASH)


def hash_password(password):
    if password is None:
        password = ''
    if not isinstance(password, str):
        password = str(password)
    return _ARGON2_HASHER.hash(password)


def verify_password(password, stored_hash):
    if stored_hash is None:
        _dummy_argon2_verify(password)
        return False
    if password is None:
        password = ''
    if not isinstance(password, str):
        password = str(password)
    if not isinstance(stored_hash, str) or not stored_hash:
        return False
    if _is_argon2id(stored_hash):
        try:
            return _ARGON2_HASHER.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError, ValueError):
            return False
    if _is_legacy_sha256(stored_hash):
        digest = hashlib.sha256(password.encode('utf-8')).hexdigest()
        return hmac.compare_digest(digest, stored_hash)
    return False


def needs_rehash(stored_hash):
    if _is_legacy_sha256(stored_hash):
        return True
    if _is_argon2id(stored_hash):
        try:
            return _ARGON2_HASHER.check_needs_rehash(stored_hash)
        except (InvalidHashError, TypeError, ValueError):
            return False
    return False


def unusable_password_hash():
    return hash_password(secrets.token_urlsafe(32))


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
    except sqlite3.OperationalError:
        pass
    return conn

def init_db():
    conn = get_db()
    with open(SCHEMA_PATH, 'r') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    ensure_schema()


def migrate_invoice_partial_paid_status(conn):
    """Allow Partial Paid on existing invoice tables that still use the old CHECK."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='invoices'"
    ).fetchone()
    if not row:
        return
    create_sql = row[0] or ''
    if 'Partial Paid' not in create_sql:
        cols = {info[1] for info in conn.execute("PRAGMA table_info(invoices)").fetchall()}
        copy_cols = [
            name for name in (
                'id', 'invoice_number', 'order_id', 'user_id', 'amount', 'tax', 'total',
                'status', 'due_date', 'paid_at', 'payment_method', 'payment_timing',
                'deposit_amount', 'amount_paid', 'created_at',
            )
            if name in cols
        ]
        col_sql = ', '.join(copy_cols)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript("""
            CREATE TABLE invoices_status_mig (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT UNIQUE NOT NULL,
                order_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                tax REAL NOT NULL,
                total REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('Paid', 'Pending', 'Partial Paid', 'Overdue', 'Cancelled')),
                due_date DATE NOT NULL,
                paid_at TIMESTAMP,
                payment_method TEXT DEFAULT 'Credit Card (Stripe)',
                payment_timing TEXT NOT NULL DEFAULT 'After work',
                deposit_amount REAL NOT NULL DEFAULT 0,
                amount_paid REAL NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        conn.execute(
            f"INSERT INTO invoices_status_mig ({col_sql}) SELECT {col_sql} FROM invoices"
        )
        conn.execute("DROP TABLE invoices")
        conn.execute("ALTER TABLE invoices_status_mig RENAME TO invoices")
        conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        UPDATE invoices
        SET status = 'Partial Paid'
        WHERE status = 'Pending'
          AND COALESCE(amount_paid, 0) > 0.004
          AND COALESCE(amount_paid, 0) + 0.004 < COALESCE(total, 0)
        """
    )


def ensure_schema():
    """Additive, non-destructive columns/tables for existing local databases."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS order_line_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            woocommerce_product_id TEXT,
            woocommerce_variation_id TEXT,
            sku TEXT,
            product_name TEXT NOT NULL,
            category_name TEXT,
            category_id TEXT,
            quantity INTEGER NOT NULL DEFAULT 1,
            unit_price REAL NOT NULL DEFAULT 0,
            line_total REAL NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        );
    """)
    doc_cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    if 'client_visible' not in doc_cols:
        conn.execute("ALTER TABLE documents ADD COLUMN client_visible INTEGER NOT NULL DEFAULT 1")
    if 'shared_at' not in doc_cols:
        conn.execute("ALTER TABLE documents ADD COLUMN shared_at TIMESTAMP")
    order_cols = {row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
    if 'woocommerce_order_id' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN woocommerce_order_id TEXT")
    if 'payment_mode' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN payment_mode TEXT")
    if 'portfolio_hidden' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN portfolio_hidden INTEGER NOT NULL DEFAULT 0")
    service_cols = {row[1] for row in conn.execute("PRAGMA table_info(services)").fetchall()}
    if 'woocommerce_product_id' not in service_cols:
        conn.execute("ALTER TABLE services ADD COLUMN woocommerce_product_id TEXT")
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if 'department' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN department TEXT")
    if 'date_of_birth' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN date_of_birth TEXT")
    if 'checkout_phone' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN checkout_phone TEXT")
    if 'checkout_dob' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN checkout_dob TEXT")
    if 'website_checkout_pulled_at' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN website_checkout_pulled_at TEXT")
        conn.execute("""
            UPDATE orders
            SET website_checkout_pulled_at = CURRENT_TIMESTAMP
            WHERE COALESCE(checkout_dob, '') != '' OR COALESCE(checkout_phone, '') != '';
        """)
    if 'website_phone_checked_at' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN website_phone_checked_at TEXT")
        conn.execute("""
            UPDATE orders
            SET website_phone_checked_at = website_checkout_pulled_at
            WHERE website_checkout_pulled_at IS NOT NULL
              AND COALESCE(checkout_phone, '') = '';
        """)
    if 'checkout_form_json' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN checkout_form_json TEXT")
    if 'owner_name' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN owner_name TEXT")
    if 'owner_form_email' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN owner_form_email TEXT")
    invoice_cols = {row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()}
    if 'payment_timing' not in invoice_cols:
        conn.execute("ALTER TABLE invoices ADD COLUMN payment_timing TEXT NOT NULL DEFAULT 'After work'")
        conn.execute(
            """
            UPDATE invoices
            SET payment_timing = 'Advance'
            WHERE payment_method IN ('Website Charge', 'Credit Card (Stripe)');
            """
        )
    if 'deposit_amount' not in invoice_cols:
        conn.execute("ALTER TABLE invoices ADD COLUMN deposit_amount REAL NOT NULL DEFAULT 0")
    if 'amount_paid' not in invoice_cols:
        conn.execute("ALTER TABLE invoices ADD COLUMN amount_paid REAL NOT NULL DEFAULT 0")
        conn.execute("UPDATE invoices SET amount_paid = total WHERE status = 'Paid'")
    migrate_invoice_partial_paid_status(conn)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS company_owners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL UNIQUE,
            company_id INTEGER,
            full_name TEXT NOT NULL DEFAULT '',
            form_email TEXT NOT NULL DEFAULT '',
            phone TEXT,
            date_of_birth TEXT,
            nationality TEXT,
            passport_cnic TEXT,
            address TEXT,
            source TEXT NOT NULL DEFAULT 'formation_form',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_company_owners_email ON company_owners(form_email);
        CREATE INDEX IF NOT EXISTS idx_company_owners_company ON company_owners(company_id);
    """)
    company_cols = {row[1] for row in conn.execute("PRAGMA table_info(companies)").fetchall()}
    if 'utr_number' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN utr_number TEXT")
    if 'authentication_code' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN authentication_code TEXT")
    if 'activation_code' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN activation_code TEXT")
    if 'identity_verified' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN identity_verified TEXT NOT NULL DEFAULT 'Not started'")
    if 'identity_verified_at' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN identity_verified_at TIMESTAMP")
    if 'psc_verified' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN psc_verified TEXT NOT NULL DEFAULT 'Not started'")
    if 'psc_verified_at' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN psc_verified_at TIMESTAMP")
    if 'ch_checked_at' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN ch_checked_at TIMESTAMP")
    if 'registration_notified_at' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN registration_notified_at TIMESTAMP")
    if 'sic_codes' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN sic_codes TEXT")
    if 'registered_email' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN registered_email TEXT")
    if 'accounts_next_due' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN accounts_next_due DATE")
    if 'accounts_overdue' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN accounts_overdue INTEGER NOT NULL DEFAULT 0")
    if 'confirmation_next_due' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN confirmation_next_due DATE")
    if 'confirmation_overdue' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN confirmation_overdue INTEGER NOT NULL DEFAULT 0")
    if 'ch_attention_json' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN ch_attention_json TEXT")
    if 'ch_alert_fingerprint' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN ch_alert_fingerprint TEXT")
    if 'ch_alert_sent_at' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN ch_alert_sent_at TIMESTAMP")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS company_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            due_date DATE,
            accounts_type TEXT NOT NULL DEFAULT 'Micro-entity',
            status TEXT NOT NULL DEFAULT 'Not started',
            confirmation_number TEXT,
            notes TEXT,
            filed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_company_accounts_period
            ON company_accounts(company_id, period_end);
        CREATE TABLE IF NOT EXISTS company_book_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            period_end DATE NOT NULL,
            entry_date DATE NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL DEFAULT 'Other',
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_company_book_entries_company_period
            ON company_book_entries(company_id, period_end);
    """)
    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if 'department' not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN department TEXT")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dismissed_company_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name_key TEXT NOT NULL DEFAULT '',
            company_number TEXT,
            woocommerce_order_id TEXT,
            deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_dismissed_company_cards_lookup
            ON dismissed_company_cards(user_id, name_key, company_number, woocommerce_order_id);
        CREATE TABLE IF NOT EXISTS email_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL,
            body_text TEXT,
            body_html TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            message_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_email_outbox_status
            ON email_outbox(status, created_at);
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id INTEGER NOT NULL,
            permission_id INTEGER NOT NULL,
            PRIMARY KEY (role_id, permission_id),
            FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
            FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()
    ensure_rbac()


def ensure_rbac():
    """Insert missing roles and permission links. Never deletes existing rows."""
    roles = (
        ('SUPER_ADMIN', 'Full unrestricted system & technical administration access'),
        ('ADMIN', 'Manage clients, orders, staff, documents, and financial records'),
        ('MANAGER', 'Oversee team assignments, assigned clients, and order workflows'),
        ('STAFF', 'Access assigned clients and orders to process documentation'),
        ('CLIENT', 'Portal user accessing owned companies, orders, and documents'),
    )
    permissions = (
        ('clients.view', 'View client records'),
        ('clients.create', 'Create new client profiles'),
        ('clients.edit', 'Modify client profiles'),
        ('clients.delete', 'Remove client profiles'),
        ('orders.view', 'View orders'),
        ('orders.create', 'Create orders'),
        ('orders.edit', 'Update order statuses and progress'),
        ('orders.assign', 'Assign staff to orders'),
        ('documents.view', 'View documents'),
        ('documents.upload', 'Upload new documents'),
        ('documents.send', 'Send document notifications to client'),
        ('documents.delete', 'Delete documents'),
        ('invoices.view', 'View invoices'),
        ('invoices.create', 'Create invoices'),
        ('accountancy.manage', 'File and track company yearly accounts'),
        ('notifications.send', 'Send notifications'),
        ('staff.manage', 'Manage internal staff accounts'),
        ('settings.manage', 'Modify system & integration settings'),
        ('tasks.view', 'View staff tasks'),
        ('tasks.create', 'Create staff tasks'),
        ('tasks.edit', 'Edit staff tasks'),
        ('tasks.assign', 'Assign staff to tasks'),
        ('tasks.complete', 'Mark staff tasks complete'),
    )
    for name, desc in roles:
        execute_db("INSERT OR IGNORE INTO roles (name, description) VALUES (?, ?);", (name, desc))
    for name, desc in permissions:
        execute_db("INSERT OR IGNORE INTO permissions (name, description) VALUES (?, ?);", (name, desc))

    role_map = {row['name']: row['id'] for row in query_db("SELECT id, name FROM roles;")}
    perm_map = {row['name']: row['id'] for row in query_db("SELECT id, name FROM permissions;")}
    links = []
    for pname in perm_map:
        links.append(('SUPER_ADMIN', pname))
        links.append(('ADMIN', pname))
    for pname in (
        'clients.view', 'clients.edit', 'clients.create',
        'orders.view', 'orders.edit', 'documents.view',
        'documents.upload', 'documents.send', 'invoices.view', 'accountancy.manage',
    ):
        links.append(('MANAGER', pname))
        links.append(('STAFF', pname))
    for pname in ('tasks.view', 'tasks.create', 'tasks.edit', 'tasks.assign', 'tasks.complete'):
        links.append(('MANAGER', pname))
    for pname in ('tasks.view', 'tasks.edit', 'tasks.complete'):
        links.append(('STAFF', pname))
    for role_name, perm_name in links:
        role_id = role_map.get(role_name)
        perm_id = perm_map.get(perm_name)
        if role_id and perm_id:
            execute_db(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?);",
                (role_id, perm_id),
            )

def query_db(query, args=(), one=False):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, args)
    rv = [dict(row) for row in cur.fetchall()]
    conn.close()
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, args)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully.")
