# ANTIGRAVITY NEXT TASK — Brixen Consultants CRM / Client Portal

**Inspection date:** 2026-08-12  
**Project path:** `/Users/simple/.gemini/antigravity/scratch/hypetex-portal`  
**Local server:** `http://127.0.0.1:5050/` (Python 3.14 WSGI, PID bound to this directory)  
**Inspected by:** Cursor (read-only review — no application code modified)

---

## Git Status

| Item | Status |
|---|---|
| Git repository | **Not initialized** — project has no `.git` directory |
| Commits / branches | N/A |
| Uncommitted changes | N/A |
| Version control | **Required before production** — no history, rollback, or collaboration trail exists |

**Recommendation:** Initialize git, add a `.gitignore` (`hypetex.db`, `storage/`, `__pycache__/`, `.env*`), and commit the current working baseline before further Antigravity changes.

---

## Project Structure

```
hypetex-portal/
├── app.py                          # Main WSGI application (~1,216 lines, all API routes)
├── db.py                           # SQLite connection, WAL mode, password hashing
├── schema.sql                      # 16-table relational schema
├── seed_db.py                      # Demo/seed data (115 orders, 11 users, RBAC, settings)
├── test_app.py                     # 15 integration tests (direct WSGI, no pytest)
├── hypetex.db                      # Runtime SQLite database (seeded demo data)
├── AGENT_HANDOFF.md                # Antigravity handoff notes (claims 100% tests passing)
├── PRODUCTION_DEPLOYMENT.md        # Hostinger/nginx/systemd deployment guide
├── templates/
│   └── index.html                  # Single-page shell (~882 lines, client + admin views)
├── static/
│   ├── css/styles.css              # Brixen liquid-glass design system
│   ├── js/app.js                   # Frontend router & API client (~1,057 lines)
│   └── img/brixen-logo.png         # Official logo asset
├── storage/clients/                # Private per-client document storage
└── wordpress_plugin/
    ├── brixen-crm-sync.zip         # Packaged plugin for WP upload
    └── brixen-crm-sync/
        ├── brixen-crm-sync.php           # Plugin entrypoint & WP/WC hooks
        ├── class-brixen-webhook-sender.php
        ├── class-brixen-bulk-sync.php    # Exists but NOT wired into plugin
        └── readme.txt
```

**Legacy naming:** Internal codename `Hypetex` / `hypetex.db` still appears in `schema.sql`, `app.py` startup message, and `static/js/app.js` header comments. Branding in the UI is mostly Brixen Consultants.

---

## Antigravity's Latest Changes (Summary)

Based on `AGENT_HANDOFF.md`, Antigravity brain walkthrough (`brain/6d586972-589e-47b2-ab5d-889e218bc964/walkthrough.md`), and code review:

| Area | What Antigravity built |
|---|---|
| **UI** | Light corporate "liquid glass" portal — client sidebar, admin CMS sidebar, order cards, dashboards, role-switch demo dropdown |
| **Backend** | Python WSGI monolith with 40+ JSON API routes (auth, client, admin, WordPress integration) |
| **Database** | SQLite with WAL, foreign keys, RBAC tables, webhook event log, persistent sessions |
| **WordPress webhooks** | `/api/v1/wordpress/webhook` for `user.created`, `user.updated`, `order.created`, `order.updated` |
| **Security (partial)** | HMAC-SHA256 verification, idempotency via `webhook_events.event_id`, cross-tenant document isolation (403) |
| **Guest checkout (intent)** | Guest WooCommerce orders routed to `guest.unlinked@brixenconsultants.com` instead of auto-creating clients |
| **SSO API** | `/api/v1/auth/sso` issues session token from `wordpress_user_id` + email |
| **WordPress plugin** | Hooks for user register/update, WC checkout, order status, payment complete; admin settings page; SSO shortcode |
| **Ops docs** | `PRODUCTION_DEPLOYMENT.md` (nginx, systemd, certbot, backups) |
| **Tests** | `test_app.py` — 15 end-to-end WSGI tests covering auth, orders, webhooks, documents, sessions |

---

## 1. What Is Currently Completed

### Backend / API
- [x] Session-based auth (login, logout, `/api/auth/me`, profile update)
- [x] Persistent DB-backed sessions (`user_sessions` table, 30-day expiry, revocation)
- [x] Client APIs: dashboard, orders (filter/sort/pagination), companies, addresses, invoices, payments, documents, services, tickets, messages, notifications
- [x] Admin APIs: stats/charts, customers, orders (status/progress update + client notification), services CRUD, document review, activity logs, settings
- [x] WordPress webhook receiver with idempotency logging
- [x] WordPress user sync on `user.created` / `user.updated`
- [x] WooCommerce order sync on `order.created` / `order.updated` (when client exists)
- [x] Admin webhook monitor + retry (`/api/admin/webhooks`, `/api/admin/webhooks/{id}/retry`)
- [x] Bulk import endpoints `/api/v1/wordpress/sync-users` and `/api/v1/wordpress/sync-orders` (admin-session auth)
- [x] Email notification abstraction (`EmailService`) — logs when SMTP not configured
- [x] Activity logging for key admin/client actions
- [x] RBAC schema + permission checks on admin routes

### Frontend
- [x] Full single-page portal UI wired to live APIs
- [x] Client views: Orders, Dashboard, Companies, Addresses, Invoices, Payments, Documents, Services, Support, Messages, Profile
- [x] Admin views: Dashboard, Customers, Orders Manager, Services, Documents Review, Audit Logs, Settings
- [x] Demo role switcher (client1, client2, admin)
- [x] Order detail modal with progress timeline
- [x] Admin order progress slider + status update

### WordPress Plugin
- [x] Real-time webhook sender with HMAC-SHA256 signing
- [x] WP Admin settings page (CRM URL + webhook secret)
- [x] WooCommerce hooks: checkout processed, status changed, payment complete
- [x] `[brixen_client_portal_button]` shortcode (basic link)
- [x] Packaged `brixen-crm-sync.zip` for upload

### Data / Seeding
- [x] Rich demo dataset: 115 orders, 5 companies, staff users, invoices, tickets, notifications
- [x] Target test order `#GB103449JUL26` seeded at 50% progress

---

## 2. What Remains Incomplete

### Critical (blocks production / live WordPress sync)

| Gap | Detail |
|---|---|
| **Guest checkout is broken** | Webhook sets `status = 'Pending Verification'` but `orders.status` CHECK constraint only allows `Pending`, `Processing`, `In Progress`, `Completed`, `Cancelled`, `Refunded`. **Guest WooCommerce orders crash with `IntegrityError`.** |
| **Webhook signature optional** | If `X-Brixen-Signature` header is omitted, webhook is accepted without auth. Verified: unauthenticated POST creates clients. |
| **Raw secret accepted as signature** | `verify_webhook_signature()` treats the plaintext secret as a valid signature (not just HMAC digest). |
| **`payment.completed` not handled** | Plugin fires the event; CRM returns generic `{ message: 'Event payment.completed received' }` without updating order/invoice state. |
| **SSO shortcode incomplete** | `[brixen_client_portal_button]` renders a plain link to CRM URL — does **not** call `/api/v1/auth/sso` or pass a signed WP token. Users must log in separately. |
| **Bulk sync not wired** | `class-brixen-bulk-sync.php` exists but is never `require_once`'d; no WP Admin UI to trigger historical sync. |
| **Bulk sync auth mismatch** | WP bulk sync POSTs HMAC-signed payloads to `/api/v1/wordpress/sync-*`, but CRM endpoints require an **admin session cookie** — WordPress cannot authenticate. Will return `403 Forbidden`. |
| **No production login UI** | Frontend auto-logs in as demo client with hardcoded passwords in `static/js/app.js`. No login form for real users. |
| **Production env vars not wired** | `PRODUCTION_DEPLOYMENT.md` documents `DATABASE_URL`, `STORAGE_PATH`, `WORDPRESS_WEBHOOK_SECRET`, `CRM_BASE_URL`, etc., but `app.py`/`db.py` only read SMTP vars. Paths and secrets are hardcoded. |
| **Document upload is a stub** | `/api/client/documents/upload` accepts JSON `{ name, category }` and writes a placeholder text file — no multipart/binary upload. |
| **Document download returns JSON** | `/api/documents/{id}/download` returns metadata JSON, not a streamed file. Frontend `<a href>` links will not deliver PDFs correctly. |
| **`orders.updated_at` missing from schema** | Webhook order update SQL references `updated_at` column that does not exist in `schema.sql`. Updates may fail silently or error depending on SQLite mode. |

### Important (quality / security / ops)

| Gap | Detail |
|---|---|
| **SHA-256 password hashing** | No salt, no bcrypt/argon2 — unsuitable for production credentials |
| **Hardcoded demo passwords in JS** | `ClientPass123!`, `AdminPass123!` visible in client-side source |
| **CORS `Access-Control-Allow-Origin: *`** | On all JSON responses |
| **Session cookie missing `Secure` flag** | Only `HttpOnly; SameSite=Lax` — needed for HTTPS production |
| **Server binds `0.0.0.0`** | `make_server('0.0.0.0', PORT, ...)` exposes dev server on all interfaces |
| **SMTP not configured** | All emails log to stdout only |
| **Hardcoded `http://127.0.0.1:5050` in email bodies** | Notification emails link to localhost |
| **Two-factor auth** | Column exists in schema; no implementation |
| **`sslverify => false`** | WordPress plugin disables SSL verification on outbound webhooks |
| **Email domain inconsistency** | Mix of `brixenconsultant.co.uk` (no **s**) and `brixenconsultants.com` across seed data, templates, and settings |
| **No git / CI** | No version control, no automated test runner in CI |
| **Test isolation** | Tests mutate DB state (admin updates order #1 to 100%). Re-running `test_app.py` without `seed_db.py` fails on assertion at line 55. |
| **No rate limiting / CSRF** | Auth and webhook endpoints unprotected against abuse |

---

## 3. Bugs & Risks Found

### Confirmed bugs (reproduced during inspection)

1. **Guest order webhook → SQLite CHECK constraint failure**
   - Trigger: `order.created` for unregistered guest email
   - Error: `CHECK constraint failed: status IN ('Pending', ...)`
   - Impact: All guest WooCommerce checkouts fail to sync

2. **Webhook accepts unsigned requests**
   - Trigger: POST to `/api/v1/wordpress/webhook` with no `X-Brixen-Signature`
   - Result: `200 OK`, client created
   - Impact: Anyone who discovers the endpoint can inject fake users/orders

3. **Tests fail on dirty database**
   - Trigger: Run `python3 test_app.py` without prior `python3 seed_db.py`
   - Error: `AssertionError: Expected 50% progress` (order already mutated by previous run)

4. **`orders.updated_at` column referenced but absent**
   - Location: `app.py` line ~331
   - Impact: Order update webhooks may error on existing orders

### Security risks

| Risk | Severity |
|---|---|
| Unsigned / weakly verified webhooks | **Critical** |
| Guest checkout crash (data loss on WC orders) | **Critical** |
| SSO endpoint has no WP token verification — anyone with email + wp_id can get session | **High** |
| SHA-256 passwords, demo creds in JS | **High** |
| Plaintext webhook secret comparison bypass | **High** |
| SQLite single-file DB on same host as app (concurrency limits at scale) | **Medium** |
| CORS wildcard + no CSRF on cookie-auth endpoints | **Medium** |
| `sslverify => false` in WP plugin | **Medium** (production) |

### Operational risks

- No git history — changes from Antigravity/Cursor are not tracked
- `hypetex.db` contains all production-like demo data with no migration tooling
- Deployment guide references env vars the application does not consume
- Live WordPress site must **not** be pointed at this dev server until security fixes land

---

## 4. What Antigravity Should Implement Next

**Priority order — complete as one focused sprint before any production deployment or live WordPress connection:**

### Sprint A — Integration & security fixes (must-do)

1. **Fix guest checkout order status**
   - Map guest orders to `Pending` (or extend schema CHECK to include `Pending Verification`)
   - Add test case for guest checkout webhook

2. **Enforce mandatory HMAC on all webhook/bulk-sync endpoints**
   - Reject requests with missing/invalid signature (always, not only when header present)
   - Remove plaintext-secret acceptance in `verify_webhook_signature()`
   - Add `ENV=production` flag to disable bypass

3. **Implement `payment.completed` handler**
   - Update order status, create/update invoice record, trigger client notification

4. **Fix SSO end-to-end**
   - WordPress: generate time-limited signed token (HMAC with shared secret)
   - CRM: validate token on `/api/v1/auth/sso` before issuing session
   - Shortcode: redirect through signed SSO URL, not plain CRM link

5. **Wire bulk sync properly**
   - `require_once` bulk sync class in plugin; add WP Admin "Run Historical Sync" buttons
   - CRM bulk endpoints: accept HMAC service auth (same secret as webhooks), not admin cookie

6. **Fix schema drift**
   - Add `updated_at` to `orders` table OR remove from UPDATE SQL
   - Align email domains to `brixenconsultants.com`

### Sprint B — Production readiness

7. **Real login page** — remove auto-login and hardcoded passwords from `app.js`
8. **Wire production environment variables** — `DATABASE_URL`, `STORAGE_PATH`, `PORT`, `CRM_BASE_URL`, `WORDPRESS_WEBHOOK_SECRET`
9. **Multipart document upload + binary download streaming**
10. **Upgrade password hashing** to bcrypt with migration path for seeded users
11. **Secure cookies** — add `Secure` flag when HTTPS; tighten CORS to `CRM_BASE_URL`
12. **Initialize git** and add `.gitignore`
13. **Fix test isolation** — use transaction rollback or dedicated test DB; admin update test should not break progress assertion on re-run

### Sprint C — Pre-launch (after Sprint A+B)

14. Configure SMTP and replace hardcoded portal URLs in email templates
15. Set `sslverify => true` in WP plugin for production
16. Staging smoke-test against a **staging** WordPress instance (not live `brixenconsultants.com`)

---

## 5. Files Antigravity Should Modify

| File | Why |
|---|---|
| `app.py` | Guest status fix, mandatory HMAC, `payment.completed`, SSO token validation, bulk-sync HMAC auth, env vars, document streaming, `updated_at` fix |
| `schema.sql` | Add `updated_at` to orders (if chosen), optional status enum expansion, migration notes |
| `db.py` | Read `DATABASE_URL` from env; bcrypt password hashing |
| `test_app.py` | Guest checkout test, unsigned webhook rejection test, SSO test, test isolation fix, `payment.completed` test |
| `seed_db.py` | Normalize email domains; bcrypt hashes if hashing changes |
| `static/js/app.js` | Remove demo auto-login; add login form; fix document download handling |
| `templates/index.html` | Login view panel; SSO redirect landing if needed |
| `wordpress_plugin/brixen-crm-sync/brixen-crm-sync.php` | Wire bulk sync; real SSO redirect; admin sync UI |
| `wordpress_plugin/brixen-crm-sync/class-brixen-webhook-sender.php` | SSO token generation; `sslverify` production flag |
| `wordpress_plugin/brixen-crm-sync/class-brixen-bulk-sync.php` | Fix auth headers to match CRM HMAC service auth |
| `PRODUCTION_DEPLOYMENT.md` | Align documented env vars with what app actually reads |
| `.gitignore` | **New file** — exclude db, storage, caches, secrets |
| `requirements.txt` | **New file** — if bcrypt or gunicorn added |

---

## 6. Files Antigravity Should NOT Modify

| File / Area | Reason |
|---|---|
| `static/css/styles.css` | Approved Brixen liquid-glass design system — no visual overhaul per `AGENT_HANDOFF.md` |
| `static/img/brixen-logo.png` | Approved brand asset |
| `hypetex.db` | Runtime data — modify via migrations/seeds, not hand-editing |
| `storage/clients/**` | Existing uploaded demo documents — preserve client file paths |
| Live WordPress site (`brixenconsultants.com`) | **Do not touch** until staging validation complete |
| `AGENT_HANDOFF.md` | Historical record — append new handoff doc instead of overwriting |
| Antigravity brain logs (`~/.gemini/antigravity/brain/**`) | System-generated; not part of project source |

**Caution on large files:** Avoid unrelated refactors in `templates/index.html` (882 lines) and `app.py` (1,216 lines) — make targeted, test-backed edits only.

---

## 7. Tests That Must Pass After the Work

Run from project root:

```bash
python3 seed_db.py && python3 test_app.py
```

All existing tests must continue to pass, plus new tests for fixes:

| # | Test | Current |
|---|---|---|
| 1 | DB record counts (≥100 orders) | ✓ |
| 2 | Target order `#GB103449JUL26` at 50% progress | ✓ (requires fresh seed) |
| 3 | Client stat card computation | ✓ |
| 4 | `GET /` returns HTML shell | ✓ |
| 5 | Client login API | ✓ |
| 6 | `GET /api/client/orders` with session | ✓ |
| 7 | `GET /api/client/dashboard` with session | ✓ |
| 8 | Admin order update (status + progress) | ✓ |
| 9 | Client notification after admin update | ✓ |
| 10 | WP webhook `user.created` | ✓ |
| 11 | WP webhook idempotency (duplicate ignored) | ✓ |
| 12 | WP bulk order sync (admin session) | ✓ |
| 13 | Persistent session in `user_sessions` | ✓ |
| 14 | Document upload + cross-tenant 403 | ✓ |
| 15 | Webhook monitor + retry | ✓ |
| 16 | Session revocation → 401 | ✓ |
| **NEW** | Guest checkout webhook succeeds (no CHECK error) | ✗ missing |
| **NEW** | Unsigned webhook → 401 | ✗ missing |
| **NEW** | `payment.completed` updates order/invoice | ✗ missing |
| **NEW** | SSO with valid signed token → session | ✗ missing |
| **NEW** | `test_app.py` passes without re-seed (isolation) | ✗ missing |

**Pass criteria:** `ALL HYPETEX WSGI & AUDIT FIX TESTS PASSED! (100%)` plus all new tests green.

---

## 8. Production Deployment Readiness

### Verdict: **NOT READY FOR PRODUCTION**

| Criterion | Status |
|---|---|
| Core UI & API functional locally | ✅ Yes |
| Automated tests pass (with fresh seed) | ✅ Yes (15/15) |
| WordPress plugin packaged | ✅ Yes (`brixen-crm-sync.zip`) |
| Guest WooCommerce sync | ❌ Broken (CHECK constraint) |
| Webhook security | ❌ Unsigned requests accepted |
| SSO | ❌ Not functional end-to-end |
| Bulk historical sync | ❌ Not wired / auth broken |
| Real user authentication UI | ❌ Demo auto-login only |
| Document upload/download | ❌ Stub implementation |
| Production config (env, HTTPS, SMTP) | ❌ Not wired |
| Password security | ❌ SHA-256, no salt |
| Version control | ❌ No git |
| Live WordPress connection | ❌ **Do not connect yet** |

**Safe to continue:** Local development and Antigravity iteration on `http://127.0.0.1:5050/`  
**Not safe yet:** Pointing `brixenconsultants.com` webhooks at any public CRM URL

---

## Quick Reference — Test Credentials (Dev Only)

| Role | Email | Password |
|---|---|---|
| Client | `client1@acmecorp.co.uk` | `ClientPass123!` |
| Client 2 | `v.smith@vantagecyber.co.uk` | `ClientPass123!` |
| Admin | `admin@brixenconsultant.co.uk` | `AdminPass123!` |
| Webhook secret (dev) | — | `brixen_wp_secret_key_998877` (from `settings` table) |

---

*This document was generated by inspection only. No application source files were modified. Re-seeding (`seed_db.py`) was run once to verify the test suite baseline.*
