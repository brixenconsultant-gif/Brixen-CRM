-- Hypetex Limited CMS & Client Portal Database Schema

PRAGMA foreign_keys = ON;

-- 1. System Settings
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 2. Users (SUPER_ADMIN, ADMIN, MANAGER, STAFF, CLIENT)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wordpress_user_id TEXT UNIQUE,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    country TEXT DEFAULT 'United Kingdom',
    address TEXT,
    avatar_url TEXT,
    notification_email TEXT,
    role TEXT NOT NULL DEFAULT 'CLIENT' CHECK(role IN ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF', 'CLIENT')),
    department TEXT,
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Suspended', 'Pending')),
    two_factor_enabled INTEGER DEFAULT 0,
    last_synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2b. RBAC Roles & Permissions
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

-- 2c. Webhook Events (Idempotency, Retries & Processing Log)
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Processed' CHECK(status IN ('Processed', 'Pending', 'Failed', 'Retrying')),
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2d. Persistent User Sessions (survives application restarts)
CREATE TABLE IF NOT EXISTS user_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 3. Companies
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    company_number TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active',
    inc_date DATE NOT NULL,
    director TEXT NOT NULL,
    reg_office TEXT NOT NULL,
    package TEXT DEFAULT 'Standard Corporate',
    account_status TEXT DEFAULT 'Good Standing',
    utr_number TEXT,
    authentication_code TEXT,
    activation_code TEXT,
    identity_verified TEXT NOT NULL DEFAULT 'Not started',
    identity_verified_at TIMESTAMP,
    psc_verified TEXT NOT NULL DEFAULT 'Not started',
    psc_verified_at TIMESTAMP,
    ch_checked_at TIMESTAMP,
    registration_notified_at TIMESTAMP,
    sic_codes TEXT,
    registered_email TEXT,
    registered_email_locked INTEGER NOT NULL DEFAULT 0,
    whatsapp_number TEXT,
    accounts_next_due DATE,
    accounts_overdue INTEGER NOT NULL DEFAULT 0,
    confirmation_next_due DATE,
    confirmation_overdue INTEGER NOT NULL DEFAULT 0,
    ch_attention_json TEXT,
    ch_alert_fingerprint TEXT,
    ch_alert_sent_at TIMESTAMP,
    business_email_verified INTEGER NOT NULL DEFAULT 0,
    business_email_verified_by INTEGER,
    business_email_verified_at TIMESTAMP,
    business_email_source TEXT,
    business_email_updated_at TIMESTAMP,
    compliance_last_notification_at TIMESTAMP,
    compliance_last_notification_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);


-- 3b. Dismissed company cards (staff deleted; block auto-recreation from webhooks)
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

-- 4. Company Directors
CREATE TABLE IF NOT EXISTS company_directors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'Director',
    nationality TEXT DEFAULT 'British',
    appointed_date DATE NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

-- 4b. Real company owners (formation-form person and form email; not portal or checkout login)
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

-- 5. Addresses
CREATE TABLE IF NOT EXISTS addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    company_id INTEGER,
    type TEXT NOT NULL CHECK(type IN ('Registered Office', 'Trading Address', 'Correspondence Address', 'Virtual Office')),
    line1 TEXT NOT NULL,
    line2 TEXT,
    city TEXT NOT NULL,
    postal_code TEXT NOT NULL,
    country TEXT DEFAULT 'United Kingdom',
    start_date DATE NOT NULL,
    expiry_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Pending Renewal', 'Expired')),
    service_name TEXT DEFAULT 'Registered Office Address',
    price REAL DEFAULT 20.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
);

-- 6. Services Catalog
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL,
    duration TEXT DEFAULT '12 Months',
    status TEXT NOT NULL DEFAULT 'Active',
    featured INTEGER DEFAULT 0,
    vat_rate REAL DEFAULT 0.20,
    renewal_period TEXT DEFAULT 'Annual',
    woocommerce_product_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. Orders
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    company_id INTEGER,
    service_id INTEGER,
    service_name TEXT NOT NULL,
    price REAL NOT NULL,
    vat REAL NOT NULL DEFAULT 0.00,
    total REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Pending', 'Processing', 'In Progress', 'Completed', 'Cancelled', 'Refunded', 'Pending Verification')),
    progress_percent INTEGER NOT NULL DEFAULT 0,
    delivery_label TEXT DEFAULT 'Standard Processing',
    assigned_staff_id INTEGER,
    expected_date DATE,
    notes TEXT,
    woocommerce_order_id TEXT,
    payment_mode TEXT,
    portfolio_hidden INTEGER NOT NULL DEFAULT 0,
    owner_name TEXT,
    owner_form_email TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL,
    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE SET NULL,
    FOREIGN KEY (assigned_staff_id) REFERENCES users(id) ON DELETE SET NULL
);

-- 7b. WooCommerce line items (actual website product data; not invented categories)
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

-- 8. Order Timeline Steps
CREATE TABLE IF NOT EXISTS order_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Completed', 'Current', 'Pending')),
    step_date TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
);

-- 9. Invoices
CREATE TABLE IF NOT EXISTS invoices (
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

-- 10. Documents
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    company_id INTEGER,
    order_id INTEGER,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Approved' CHECK(status IN ('Pending Review', 'Approved', 'Rejected', 'Requires Update')),
    uploaded_by TEXT NOT NULL,
    review_notes TEXT,
    client_visible INTEGER NOT NULL DEFAULT 1,
    shared_at TIMESTAMP,
    file_hash TEXT,
    ocr_status TEXT DEFAULT 'COMPLETED',
    identity_status TEXT DEFAULT 'COMPLETED',
    crm_status TEXT DEFAULT 'MATCHED',
    ch_status TEXT DEFAULT 'NOT_APPLICABLE',
    overall_status TEXT DEFAULT 'COMPLETED',
    stage_timestamps_json TEXT,
    match_meta_json TEXT,
    lifecycle_status TEXT DEFAULT 'POSTED_DOCUMENTS',
    is_posted INTEGER DEFAULT 1,
    ocr_confidence REAL DEFAULT 100.0,
    classification_confidence REAL DEFAULT 100.0,
    identity_confidence REAL DEFAULT 100.0,
    customer_match_confidence REAL DEFAULT 100.0,
    company_match_confidence REAL DEFAULT 100.0,
    duplicate_confidence REAL DEFAULT 0.0,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    matched_at TIMESTAMP,
    approved_at TIMESTAMP,
    posted_at TIMESTAMP,
    uploaded_by_id INTEGER,
    processed_by_id INTEGER,
    matched_by_id INTEGER,
    approved_by_id INTEGER,
    posted_by_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_lifecycle ON documents(lifecycle_status, is_posted);
CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash);

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
CREATE INDEX IF NOT EXISTS idx_doc_audit_doc_id ON document_audit_log(document_id);

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

-- 11. Proxies
CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    order_id INTEGER,
    proxy_type TEXT NOT NULL,
    start_date DATE NOT NULL,
    expiry_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Pending', 'Expired', 'Cancelled')),
    assigned_person TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
);

-- 12. Registered Agents
CREATE TABLE IF NOT EXISTS registered_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    agent_name TEXT NOT NULL DEFAULT 'Hypetex Nominees Ltd',
    start_date DATE NOT NULL,
    renewal_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active',
    package TEXT DEFAULT 'Full Corporate Agent',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

-- 13. Support Tickets
CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_number TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    order_id INTEGER,
    company_id INTEGER,
    subject TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL CHECK(priority IN ('Low', 'Medium', 'High', 'Urgent')),
    status TEXT NOT NULL CHECK(status IN ('Open', 'In Progress', 'Waiting for Customer', 'Resolved', 'Closed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
);

-- 14. Support Messages
CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    sender_name TEXT NOT NULL,
    sender_role TEXT NOT NULL,
    message TEXT NOT NULL,
    attachment_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_id) REFERENCES support_tickets(id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 15. Notifications
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    type TEXT NOT NULL,
    link TEXT,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 16. Activity Logs
CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    user_email TEXT,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 17. Staff Tasks (internal; not client-visible)
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT NOT NULL DEFAULT 'Medium' CHECK(priority IN ('Low', 'Medium', 'High', 'Urgent')),
    status TEXT NOT NULL DEFAULT 'Open' CHECK(status IN ('Open', 'In Progress', 'Completed', 'Cancelled')),
    due_date DATE,
    department TEXT,
    assigned_staff_id INTEGER,
    created_by_id INTEGER,
    client_id INTEGER,
    company_id INTEGER,
    order_id INTEGER,
    internal_notes TEXT,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (assigned_staff_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (created_by_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
);

-- 17b. Company yearly accounts filings
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

-- 17c. Cash book lines posted from bank statements (FRS 105 headings)
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

-- 18. Outbound customer emails (retry until SMTP accepts)
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

-- 19. Verified business email history
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

-- 20. Compliance notification audit log
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
CREATE INDEX IF NOT EXISTS idx_compliance_log_token
    ON compliance_notification_log(track_token);

