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
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 15000;")
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
    if 'form_config_json' not in service_cols:
        conn.execute("ALTER TABLE services ADD COLUMN form_config_json TEXT")
    if 'document_requirements_json' not in service_cols:
        conn.execute("ALTER TABLE services ADD COLUMN document_requirements_json TEXT")
    if 'estimated_delivery_time' not in service_cols:
        conn.execute("ALTER TABLE services ADD COLUMN estimated_delivery_time TEXT DEFAULT '24-48 Hours'")

    if 'checkout_form_json' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN checkout_form_json TEXT")
    if 'order_form_values_json' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN order_form_values_json TEXT")
    if 'is_draft' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0")
    if 'current_step' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN current_step INTEGER NOT NULL DEFAULT 1")
    if 'b2b_client_id' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN b2b_client_id TEXT")
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if 'department' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN department TEXT")
    if 'date_of_birth' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN date_of_birth TEXT")
    if 'notification_email' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN notification_email TEXT")
    if 'is_b2b' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_b2b INTEGER NOT NULL DEFAULT 0")
    if 'account_type' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN account_type TEXT DEFAULT 'Normal Client'")
    if 'b2b_id' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN b2b_id TEXT")
    if 'client_type' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN client_type TEXT DEFAULT 'Normal'")

    # Safe Audit: Ensure standard retail/individual clients default to Normal Customer (is_b2b = 0, b2b_id = NULL)
    conn.execute("""
        UPDATE users 
        SET b2b_id = NULL, client_type = 'Normal', is_b2b = 0 
        WHERE role = 'CLIENT' AND (is_b2b = 0 OR client_type = 'Normal' OR (account_type IS NOT NULL AND account_type NOT LIKE '%B2B%'));
    """)
    if 'theme_preference' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN theme_preference TEXT DEFAULT 'system'")
    if 'checkout_phone' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN checkout_phone TEXT")
    if 'access_email' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN access_email TEXT")
    if 'access_email_password' not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN access_email_password TEXT")
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
    if 'registered_email_locked' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN registered_email_locked INTEGER NOT NULL DEFAULT 0")
    if 'whatsapp_number' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN whatsapp_number TEXT")
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
    if 'business_email_verified' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN business_email_verified INTEGER NOT NULL DEFAULT 0")
    if 'business_email_verified_by' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN business_email_verified_by INTEGER")
    if 'business_email_verified_at' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN business_email_verified_at TIMESTAMP")
    if 'business_email_source' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN business_email_source TEXT")
    if 'business_email_updated_at' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN business_email_updated_at TIMESTAMP")
    if 'compliance_last_notification_at' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN compliance_last_notification_at TIMESTAMP")
    if 'compliance_last_notification_id' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN compliance_last_notification_id TEXT")
    if 'b2b_id' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN b2b_id TEXT")
    if 'client_type' not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN client_type TEXT DEFAULT 'B2B'")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_b2b_id ON companies(b2b_id) WHERE b2b_id IS NOT NULL;")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_b2b_id ON users(b2b_id) WHERE b2b_id IS NOT NULL;")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS company_email_verification_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            email TEXT,
            action TEXT NOT NULL,
            source TEXT,
            note TEXT,
            actor_user_id INTEGER,
            actor_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_company_email_hist_company
            ON company_email_verification_history(company_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS compliance_notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            notification_id TEXT NOT NULL,
            notification_type TEXT NOT NULL,
            issue_fingerprint TEXT,
            issue_summary TEXT,
            recipient TEXT,
            status TEXT NOT NULL,
            blocked_reason TEXT,
            message_id TEXT,
            trigger_source TEXT,
            attempt INTEGER NOT NULL DEFAULT 1,
            sent_by_user_id INTEGER,
            error_category TEXT,
            payload_json TEXT,
            track_token TEXT,
            first_clicked_at TIMESTAMP,
            click_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_compliance_log_company
            ON compliance_notification_log(company_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_compliance_log_fp
            ON compliance_notification_log(company_id, issue_fingerprint, status, created_at DESC);
    """)
    # Engagement tracking for compliance emails (click CTAs — no open pixels).
    compliance_log_cols = {row[1] for row in conn.execute("PRAGMA table_info(compliance_notification_log)").fetchall()}
    if 'track_token' not in compliance_log_cols:
        conn.execute("ALTER TABLE compliance_notification_log ADD COLUMN track_token TEXT")
    if 'first_clicked_at' not in compliance_log_cols:
        conn.execute("ALTER TABLE compliance_notification_log ADD COLUMN first_clicked_at TIMESTAMP")
    if 'click_count' not in compliance_log_cols:
        conn.execute("ALTER TABLE compliance_notification_log ADD COLUMN click_count INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_compliance_log_token ON compliance_notification_log(track_token)"
    )
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

    # Document Decoupled Columns & High-Performance Indexes Migration
    conn = get_db()
    try:
        doc_cols = {info[1] for info in conn.execute("PRAGMA table_info(documents)").fetchall()}
        new_cols = [
            ('file_hash', 'TEXT'),
            ('ocr_status', "TEXT DEFAULT 'COMPLETED'"),
            ('identity_status', "TEXT DEFAULT 'COMPLETED'"),
            ('crm_status', "TEXT DEFAULT 'MATCHED'"),
            ('ch_status', "TEXT DEFAULT 'NOT_APPLICABLE'"),
            ('overall_status', "TEXT DEFAULT 'COMPLETED'"),
            ('stage_timestamps_json', 'TEXT'),
            ('match_meta_json', 'TEXT'),
            ('lifecycle_status', "TEXT DEFAULT 'POSTED_DOCUMENTS'"),
            ('is_posted', 'INTEGER DEFAULT 1'),
            ('ocr_confidence', 'REAL DEFAULT 100.0'),
            ('classification_confidence', 'REAL DEFAULT 100.0'),
            ('identity_confidence', 'REAL DEFAULT 100.0'),
            ('customer_match_confidence', 'REAL DEFAULT 100.0'),
            ('company_match_confidence', 'REAL DEFAULT 100.0'),
            ('duplicate_confidence', 'REAL DEFAULT 0.0'),
            ('uploaded_at', 'TIMESTAMP'),
            ('processed_at', 'TIMESTAMP'),
            ('matched_at', 'TIMESTAMP'),
            ('approved_at', 'TIMESTAMP'),
            ('posted_at', 'TIMESTAMP'),
            ('uploaded_by_id', 'INTEGER'),
            ('processed_by_id', 'INTEGER'),
            ('matched_by_id', 'INTEGER'),
            ('approved_by_id', 'INTEGER'),
            ('posted_by_id', 'INTEGER'),
        ]
        for col_name, col_type in new_cols:
            if col_name not in doc_cols:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_type};")
        # Backfill uploaded_at for rows that predate the column
        try:
            conn.execute("UPDATE documents SET uploaded_at = COALESCE(uploaded_at, created_at) WHERE uploaded_at IS NULL;")
        except Exception:
            pass

        # Indexes for fast querying
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_company_id ON documents(company_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_lifecycle ON documents(lifecycle_status, is_posted);")

        # Backfill customer / intake docs that should not appear as posted
        try:
            conn.execute("""
                UPDATE documents
                SET lifecycle_status = 'CUSTOMER_UPLOADS', is_posted = 0
                WHERE (uploaded_by = 'Customer Upload' OR category = 'Checkout Upload')
                  AND COALESCE(is_posted, 1) = 1
                  AND status IN ('Pending Review', 'Requires Update');
            """)
            conn.execute("""
                UPDATE documents
                SET lifecycle_status = CASE
                        WHEN COALESCE(ch_status, '') IN ('review_required', 'REVIEW_REQUIRED') THEN 'REVIEW_REQUIRED'
                        WHEN COALESCE(ch_status, '') IN ('matched', 'MATCHED') THEN 'READY_FOR_APPROVAL'
                        ELSE 'REVIEW_REQUIRED'
                    END,
                    is_posted = 0,
                    status = CASE WHEN status = 'Approved' THEN 'Pending Review' ELSE status END
                WHERE review_notes = 'Smart Document Intake'
                  AND COALESCE(is_posted, 1) = 1
                  AND COALESCE(lifecycle_status, 'POSTED_DOCUMENTS') = 'POSTED_DOCUMENTS'
                  AND COALESCE(client_visible, 0) = 0;
            """)
        except Exception:
            pass

        # OCR Content Hash Cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ocr_cache (
                file_hash TEXT PRIMARY KEY,
                extracted_text TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                processing_version TEXT DEFAULT 'v2.0',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Asynchronous Batch Intake Queue
        conn.execute("""
            CREATE TABLE IF NOT EXISTS intake_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_hash TEXT,
                status TEXT NOT NULL DEFAULT 'QUEUED',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                result_json TEXT,
                folder_path TEXT,
                source_mode TEXT DEFAULT 'CUSTOMER_UPLOADS',
                entity_graph_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP
            );
        """)
        iq_cols = {info[1] for info in conn.execute("PRAGMA table_info(intake_queue)").fetchall()}
        for col_name, col_type in (('folder_path', 'TEXT'), ('source_mode', "TEXT DEFAULT 'CUSTOMER_UPLOADS'"), ('entity_graph_json', 'TEXT')):
            if col_name not in iq_cols:
                conn.execute(f"ALTER TABLE intake_queue ADD COLUMN {col_name} {col_type};")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_intake_queue_batch ON intake_queue(batch_id, status);")

        # Observability Metrics
        conn.execute("""
            CREATE TABLE IF NOT EXISTS intake_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT,
                document_id INTEGER,
                file_hash TEXT,
                extraction_method TEXT,
                ocr_duration_ms INTEGER,
                ch_duration_ms INTEGER,
                total_duration_ms INTEGER,
                ch_cache_hit INTEGER DEFAULT 0,
                match_score INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Document Audit Log Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                actor_type TEXT NOT NULL DEFAULT 'AI',
                actor_id INTEGER,
                actor_name TEXT NOT NULL,
                action TEXT NOT NULL,
                previous_state TEXT,
                new_state TEXT,
                ai_model_version TEXT DEFAULT 'v2.0',
                reason_evidence_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_audit_doc_id ON document_audit_log(document_id);")

        # Bulk Approval Jobs Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bulk_approval_jobs (
                job_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                total_count INTEGER NOT NULL DEFAULT 0,
                approved_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'PROCESSING',
                errors_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
        """)
        conn.commit()
    except Exception as exc:
        print(f"[Schema Migration Note] {exc}")
    finally:
        conn.close()

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
