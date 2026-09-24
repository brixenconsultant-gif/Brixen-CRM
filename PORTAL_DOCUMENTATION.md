# Brixen Consultants CMS & Client Portal

Full product documentation for the live system at **https://portal.brixenconsultants.com**.

This portal is the Brixen Consultants CRM (staff) and client workspace. It is linked to the public website **https://brixenconsultants.com** (WordPress + WooCommerce) for signups, checkout orders, and Normal-customer website login.

---

## 1. What this system is

The CMS is a single-page Flask/WSGI application (`app.py`) with:

- Staff CRM for customers, companies, orders, invoices, documents, tasks, and accountancy
- B2B client portal login (same domain)
- Normal (retail) customers who sign in on the **website**, not this CRM
- Companies House lookup, KYC documents, invoices, and transactional email
- WordPress webhook + SSO so website orders and users appear in the CRM

**Live URL:** https://portal.brixenconsultants.com  
**Website:** https://brixenconsultants.com  
**App process:** systemd `brixen-crm` on `127.0.0.1:5050` behind Nginx/SSL  
**Database:** SQLite file `hypetex.db` (never overwritten by code deploys)

---

## 2. Who can sign in, and where

| Account type | How they are created | Where they log in | What they see |
|---|---|---|---|
| **Super Admin / Admin / Manager / Staff** | Staff → Create Account, or existing portal user | This CRM portal | Staff sidebar (Overview, Customers, Companies, Orders, etc.) |
| **B2B Customer** | Create Customer → B2B, or converted client | This CRM portal | Client sidebar (Dashboard, Company Registered, Orders, …) |
| **Normal Customer** | Create Customer → Normal, or website signup | **Website only** (`brixenconsultants.com` client panel / my-account) | Not this CRM. Website login is provisioned in WordPress |

Rules coded in the app:

- Normal clients are blocked from CRM portal login (`client_can_use_crm_portal`).
- B2B clients can use the CRM portal; they get a unique `B2B-XXXXXX` ID.
- Passwords must be at least **10 characters**.
- Forgot password emails a reset link for B2B and staff. Website customers are directed to the website login.
- Super Admin can force-delete locked genuine records. Other staff cannot wipe locked orders/companies/invoices.

---

## 3. Staff CRM — screens and what they do

### 3.1 Overview (admin dashboard)

Performance snapshot: sales, customers, products, revenue charts, top performers. Uses batched queries (not N+1) so the dashboard stays fast.

### 3.2 Customers / Signups

Lists all `CLIENT` users (Normal and B2B), with company/order counts.

**Create Customer**

1. Choose **Normal** or **B2B**.
2. Enter name, email, phone, country, password (min 10 characters, confirmed).
3. Submit creates/updates the CRM user.

Behaviour:

- **B2B:** CRM portal account + `B2B-XXXXXX`. They sign in here.
- **Normal:** CRM record + WordPress website login via `/wp-json/brixen-crm/v1/provision-customer`. They sign in on the website only.
- If the email **already exists** as a client, Create Customer **updates** name, phone, and password instead of failing with “Could not create customer”.
- If WordPress sync fails, the CRM record is still saved and a warning is shown (not a hard red failure that hides the saved customer).
- Permanently deleted emails cannot be recreated (see §11).

Staff can also set phone, notification email, portal/website password, status, and delete (permanent for Super Admin / authorised staff).

### 3.3 Company Registered

UK company cards for the whole book (staff) or the logged-in B2B client.

**Default front view**

- Opens on **registered companies only** (Companies House records).
- **Newest registration first** (`created_at`, then incorporation date, then id).
- Pending / “not registered yet” cards are on the **Not registered** or **All** filters, not on the landing view.

**Filters**

| Filter | Shows |
|---|---|
| Companies House | Official numbered UK companies, newest first |
| Attention | Overdue accounts, confirmation statement, default CH address, strike-off |
| Not registered | Awaiting a Companies House number / name confirmation |
| All | Registered companies first, then pending |

**Click a company**

Opens the company sheet on **Overview**: name, number, registered address, status, country, incorporation date, director(s), verified business email, WhatsApp.

Other tabs: Registered Office, Business Activity (SIC), Compliance (UTR / auth / activation / IDV / PSC), Orders / Services, Documents.

Staff can:

- Add company (Companies House search)
- Import WebFiling company numbers
- Bulk edit / bulk delete (delete is permanent for Super Admin)
- Confirm pending formation names so a card becomes a registered company

Genuine locked registered companies are not auto-deleted by scripts.

### 3.4 Accountancy

Identity verification, PSC, registered office, year end, due dates, cash book, bank-statement upload, and yearly accounts filing (Companies Act / Companies House workflow).

### 3.5 Orders Manager

Every website and CRM order.

- Search/filter; product summary from line items
- Open an order: customer, company owner, products, documents, payment plan, internal notes, conversation
- Super Admin can change price (listed price = total; **no VAT**)
- Super Admin can force-delete locked genuine orders
- Manual Super Admin deletes are **permanent** (ledger + backup scrub)

**Create New Order** (5-step wizard)

1. **Customer** — staff pick a client (clients skip this; they are the customer).
2. **Service** — select **one or more products**. A tray shows the selection.
3. **Details** — one shared form. Duplicate fields from different products are asked **once**. Required wins if any selected product needs the field.
4. **Documents** — merged checklist. For packages, banks, address, identity / formation services: **ID** and **Proof of Address** required; **Additional Documents** optional.
5. **Review** — listed total, **No VAT**, then submit.

The result is **one order** with multiple **line items**.

Drafts can be saved. B2B prices show as custom rate until staff set them.

### 3.6 Tasks & Team

Internal tasks by department, assignee, client/company/order links. Team list for staff accounts. Incentive / SLA report on the Performance dashboard.

### 3.7 Services Catalog

CRM catalogue (name, category, price, duration, status). Website WooCommerce prices stay separate unless products are synced.

New services store `vat_rate = 0`. Order totals do not add VAT.

### 3.8 Payment / Invoices

Invoices linked to orders. Timing: After work, Advance, or Deposit.

- Amount = listed price
- Tax/VAT = **0**
- Total = price
- GBP Tide and PKR UBL details on payment emails
- Paid / part-paid / overdue; send invoice / payment-received emails to the **company owner** email, not necessarily the portal login

### 3.9 Documents Review

Lifecycle:

Customer Uploads → Processing → Review Required → Ready for Approval → Posted Documents  
Quarantine for problem files.

Staff can approve, quarantine, promote into the company vault, and download.

### 3.10 Smart Intake

Drop unorganised files (passports, statements, formation packs). OCR / profile match against Companies House. Google Drive folder import is supported.

### 3.11 Audit Logs

Staff actions: logins, creates, deletes, password sets, document reviews.

### 3.12 System Settings

Brand, logo, support email/phone, prefixes, SMTP, Companies House API key, compliance alert toggle (default off until enabled).

### 3.13 Performance

Staff leaderboard, SLA on-time %, incentive tier.

---

## 4. B2B client portal — screens

Available only to B2B clients who log in at the portal.

| Screen | Purpose |
|---|---|
| Dashboard | Greeting from the **browser local clock** (morning / afternoon / evening). Empty state is a glossy welcome. Recent orders/companies. |
| Company Registered | Same card model as staff, scoped to that client. Newest registered first. Click for official details. |
| Accountancy | IDV, PSC, year end, due dates for their companies. |
| Addresses | Registered / trading / virtual office subscriptions. |
| Orders | History + **Create New Order** (same multi-product wizard, customer step skipped). |
| Invoices / Payments | Their bills and receipts (no VAT line). |
| Documents | Vault upload/download. |
| Services | Active catalogue. |
| Order Support / Messages | Tickets and advisor thread. |
| Profile | Contact details and password. |

Normal customers never see this CRM UI.

---

## 5. Create New Order — detailed rules

### 5.1 Multi-product

- Tick as many catalogue products as needed.
- Selected chips can be removed without restarting.
- Submit stores `line_items` (product name, category, price) on one order.
- Order title is the product name if one item, or **Multiple Products** if several.
- Notes include the product list.

### 5.2 Merged requirements

If two products need the same field (for example company name or SIC), it appears **once**. If either product marks it required, it stays required.

Repeatable sections (shareholders, etc.) are merged by id and asked once.

### 5.3 Documents

Packages, banks, address, identity, KYC, incorporation, formation, mail forwarding, all-inclusive, Tide:

| Document | Required? |
|---|---|
| ID (passport / driving licence / photo ID) | Yes |
| Proof of address (utility / bank statement, last 3 months) | Yes |
| Additional documents | **Optional** |

Other products: union of their own document lists; same id asked once.

### 5.4 Pricing — no VAT

- Wizard review: listed price only, labelled **No VAT**.
- CRM create, drafts, website ingest: `vat = 0`, `total = price`.
- Existing orders and invoices were stripped so totals equal the net price.
- Editing an order price no longer splits 20% VAT.
- Invoice emails hide the VAT row when tax is zero.

This is **not** VAT Registration as a product (that catalogue item still exists as a service name).

---

## 6. WordPress / website integration

Plugin: `wordpress_plugin/brixen-crm-sync` on brixenconsultants.com.

| Direction | What happens |
|---|---|
| Website checkout → CRM | Signed webhook creates/updates client, order, line items, company when it is a formation order |
| Website user → CRM | User sync / SSO (`/api/v1/auth/sso`) |
| CRM → Website | Provision Normal customer WordPress login (`provision-customer`) |
| Products | Optional WooCommerce product sync into CRM services |

Website formation orders wait on **Company Registered** until staff confirm the company name. Named companies become cards. Companies House fills official details when a number is matched.

Guest checkout without a matching user is held as pending verification, not assigned to a real client.

---

## 7. Companies House

- Live name/number search in Create Order, Add Company, and rename-before-registration.
- After a company is selected, search must **not** loop (select closes the live-search UI).
- Registered cards show official number, address, directors, status.
- Attention flags from CH (overdue accounts, confirmation statement, default address, strike-off).
- Optional compliance emails (off until enabled in settings). Daily window is configured; only verified business emails receive them.
- Brixen is **not** Companies House. Compliance emails use a small disclaimer only on COMPLIANCE / COMPANIES_HOUSE / KYC notices.

---

## 8. Email (transactional)

Shared engine: `email_engine.py`.

Every outbound email has a category: `COMPLIANCE`, `COMPANIES_HOUSE`, `KYC`, `DOCUMENT`, `CUSTOMER_UPLOAD`, `INVOICE`, `PAYMENT`, `ORDER`, `ACCOUNT`, `SECURITY`, `STAFF`, `SYSTEM`, `MARKETING`.

- From: **Brixen Consultants**
- Reply-To: `contact@brixenconsultants.com`
- Matching plain-text part; no open-tracking pixels
- Signature: Kind regards → The Brixen Consultants Team → Brixen Consultants Ltd → contact from System Settings
- Compliance disclaimer **only** on company-record compliance notices, small, after the signature

Invoices, welcome, password, and ordinary account emails do **not** carry the Companies House disclaimer.

---

## 9. Documents and KYC

- Client and staff upload (PDF, JPG, PNG, DOC, DOCX; additional also ZIP).
- Files stored under `storage/` (not in git, not rsynced to production as a replace).
- Order wizard attaches uploaded files to the new order.
- Lifecycle review before they become Posted Documents in the company vault.
- Customer checkout uploads appear on the order and in Customer Uploads.

---

## 10. Roles, permissions, and Super Admin

Roles: `SUPER_ADMIN`, `ADMIN`, `MANAGER`, `STAFF`, `CLIENT`.

- Super Admin / owner has full access, including **force-delete** of locked genuine orders, companies, and customers.
- Price edits on orders/invoices are administrator-only.
- Record delete is restricted to administrator accounts.
- Staff departments gate tasks.
- Managers see a filtered customer/order set where coded.

Genuine-record lock (`data_protection.py`): real client orders, numbered UK companies, and invoices are not wiped by seed scripts or automatic cleanup. Super Admin must explicitly force-delete.

---

## 11. Permanent manual deletes

When Super Admin / staff **manually delete** a customer, company, or order:

1. The live row tree is hard-deleted from `hypetex.db`.
2. Fingerprints (email, company number, WordPress user id, order number) are written to `storage/permanent_purge_ledger.json` **outside** the database.
3. Matching rows are scrubbed from backup `.db` files.
4. On startup, the ledger is enforced again.

Those fingerprints are **never restored** from backups or chat context. Disaster recovery of *other* data is allowed; purged emails / numbers stay gone.

Do not recreate a client with a purged email, or re-import a purged company/order number, unless the owner explicitly asks for a **new** account (not an undelete).

---

## 12. Database and performance

- SQLite with WAL, `synchronous=NORMAL`, large cache, mmap.
- Thread-local connections (`db.py`).
- Covering indexes on hot paths (companies by user/created, orders, line items, etc.).
- Dashboard and company lists avoid N+1 queries.

Passwords: Argon2id (legacy hashes upgraded on login).

---

## 13. Main code files

| File | Role |
|---|---|
| `app.py` | HTTP API, auth, orders, companies, invoices, WordPress, email send |
| `db.py` | SQLite, schema migrate, indexes, password hashing |
| `schema.sql` | Tables: users, companies, orders, invoices, documents, tasks, … |
| `templates/index.html` | Single-page UI |
| `static/js/app.js` | All portal behaviour (wizard, portfolio, customers, …) |
| `static/css/styles.css` | Theme, portfolio, wizard tray |
| `product_catalog_config.py` | Per-product forms and KYC document rules |
| `email_engine.py` | HTML/text transactional layouts |
| `data_protection.py` | Genuine-record locks |
| `permanent_purge.py` | Manual-delete ledger |
| `compliance_alerts.py` | Scheduled CH attention emails |
| `wordpress_plugin/brixen-crm-sync/` | Website plugin (deployed on WordPress, not via CRM rsync) |

---

## 14. How production is updated

After a code change, the app is published to the live portal.

**Copied:** `app.py`, `db.py`, `schema.sql`, `data_protection.py`, `permanent_purge.py`, `templates/index.html`, `static/js/app.js`, `static/css/styles.css`  
**Never copied:** `hypetex.db`, `.env*`, `.admin_credentials`, `storage/`, `venv/`, logs, `__pycache__`, `.git`, `wordpress_plugin/`, `seed_db.py`

Steps: timestamped backup of app files → rsync those files → `chown brixen:brixen` → `systemctl restart brixen-crm` → confirm `127.0.0.1:5050` and HTML asset versions.

Do not change Nginx, SSL, or the systemd unit as part of a normal feature deploy. Do not run `seed_db.py` on production.

---

## 15. API map (high level)

**Auth:** `/api/auth/login`, `/logout`, `/me`, `/forgot-password`, `/reset-password`, `/profile`  
**SSO:** `/api/v1/auth/sso`  
**WordPress:** `/api/v1/wordpress/webhook`, `sync-users`, `sync-orders`, `sync-products`, `documents`  
**Client:** `/api/client/dashboard`, `orders`, `companies`, `invoices`, `documents`, `tickets`, `notifications`, `accountancy`  
**Staff:** `/api/admin/customers`, `companies`, `orders`, `staff`, `invoices`, `documents`, `intake`, `settings`, `tasks`, `accountancy`  
**Orders:** `POST /api/admin/orders` and `POST /api/client/orders` (Create New Order), `POST /api/orders/draft`

All mutating APIs require a session cookie (and staff permissions where applicable).

---

## 16. Business rules to remember

1. **Normal = website login. B2B = this portal.**
2. **Create New Order** can include many products; questions and KYC files are asked once.
3. **ID + Proof required; Additional optional.**
4. **No VAT on orders or invoices.** Listed price is the total.
5. **Company Registered** landing page = registered companies, newest first. Click a card for official details.
6. **Manual deletes are permanent.** Purged emails/numbers never come back from backup.
7. **Locked genuine data** is not auto-wiped. Super Admin force-delete only.
8. **Do not restore Ibad-reassigned confidential companies** or purged records unless the owner explicitly asks for new data.
9. Emails are transactional, UK professional, category-driven. No Companies House disclaimer on invoices or password mail.
10. Website plugin and production database are not replaced by a normal CRM file deploy.

---

## 17. Typical staff workflows

**New Normal customer for website login**  
Customers → Create Customer → Normal → save. They use the website. If the email already exists, details are updated.

**New B2B portal client**  
Create Customer → B2B → they sign in at portal.brixenconsultants.com.

**Place an order in CRM**  
Orders → Create New Order → customer → tick products → one details form → ID + Proof → submit. Total has no VAT.

**See a newly registered company**  
Company Registered (default Companies House filter). Newest card at the top. Click for Overview.

**Website formation arrived without a name**  
Company Registered → Not registered → confirm the name / match Companies House → card becomes registered.

**Remove a client for good**  
Super Admin delete. Ledger records the email. That address cannot be used again.

---

*This document describes the CMS as implemented in the Brixen portal codebase and published to https://portal.brixenconsultants.com.*
