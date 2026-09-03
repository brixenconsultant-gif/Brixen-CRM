# BRIXEN CONSULTANTS CRM — CURSOR + ANTIGRAVITY AGENT HANDOFF

**STATUS**: `READY FOR CURSOR / AUDIT FIXES COMPLETED & VERIFIED`

---

## 1. Project Overview & Responsibility Allocation
- **Single Source of Truth**: All work operates directly in `/Users/simple/.gemini/antigravity/scratch/hypetex-portal`.
- **Lead Integration / Architecture Agent**: Antigravity
- **Assisting Agent**: Cursor

---

## 2. Completed Audit Fixes & Enhancements

### P0 — Security & Authentication
- [x] **Mandatory Webhook Signature Enforcement**: Webhook receiver (`/api/v1/wordpress/webhook`) strictly rejects unsigned payloads with `401 Unauthorized`.
- [x] **Constant-Time HMAC Comparison**: Plaintext secret bypass removed from `verify_webhook_signature()`. Uses `hmac.compare_digest`.
- [x] **Production Login Form & Auto-Login Removal**: Removed silent auto-login from `static/js/app.js`. Added `#view-login` form UI in `templates/index.html`. Unauthenticated requests display the sign-in modal.

### P1 — Data & Integration
- [x] **Guest Checkout Constraint Fix**: `schema.sql` orders table `CHECK(status IN (...))` expanded to include `'Pending Verification'`. Added `updated_at TIMESTAMP` column. Unlinked guest WooCommerce checkouts store safely as `Pending Verification` with zero crash.
- [x] **End-to-End `payment.completed` Event Handling**: `payment.completed` webhook updates order status to `Processing`, creates/updates invoice record to `Paid`, and notifies customer automatically.
- [x] **Signed WordPress SSO Token Exchange**: `[brixen_client_portal_button]` generates time-limited signed SSO redirect URLs (`wordpress_user_id`, `email`, `timestamp`, `signature`). `/api/v1/auth/sso` verifies HMAC signature & 300s timestamp window before logging user in and setting session cookie.
- [x] **Bulk Sync HMAC Authorization**: `/api/v1/wordpress/sync-users` and `/api/v1/wordpress/sync-orders` accept EITHER Admin session cookie OR valid `X-Brixen-Signature` HMAC header. `class-brixen-bulk-sync.php` wired into plugin.

### P2 — Document Management & Streaming
- [x] **Physical Binary Streaming Downloads**: `/api/documents/{id}/download` streams physical file bytes with proper `Content-Type` and `Content-Disposition` headers.
- [x] **Strict Tenant Isolation**: Client B requesting Client A's document returns `403 Forbidden`.

### P3 — Reliability & Testing
- [x] **Expanded Integration & Regression Test Suite**: `test_app.py` includes tests for unsigned webhook rejection, guest checkout sync, payment completed processing, signed SSO exchange, document binary downloads, and cross-tenant isolation.
- [x] **100% Test Pass Rate**: Executed `python3 seed_db.py && python3 test_app.py` — **100% Passed**.

---

## 3. Key Files & Modification Restrictions

| File / Path | Purpose | Modification Rule |
|---|---|---|
| [`app.py`](file:///Users/simple/.gemini/antigravity/scratch/hypetex-portal/app.py) | Main WSGI Application, API Endpoints, Webhook Receiver & RBAC Auth Engine | Maintain targeted changes. Run `python3 test_app.py` after editing. |
| [`db.py`](file:///Users/simple/.gemini/antigravity/scratch/hypetex-portal/db.py) | SQLite Connection, WAL Mode & Transaction Handler | Preserve WAL mode and 5000ms busy timeout. |
| [`schema.sql`](file:///Users/simple/.gemini/antigravity/scratch/hypetex-portal/schema.sql) | Relational Database Schema Definition | `orders.status` includes `'Pending Verification'`. |
| [`static/css/styles.css`](file:///Users/simple/.gemini/antigravity/scratch/hypetex-portal/static/css/styles.css) | Brixen Consultants Design System | Do NOT overhaul approved light corporate visual layout. |
| [`wordpress_plugin/brixen-crm-sync/`](file:///Users/simple/.gemini/antigravity/scratch/hypetex-portal/wordpress_plugin/brixen-crm-sync/) | WordPress & WooCommerce Integration Plugin | Rebuilt package at [`wordpress_plugin/brixen-crm-sync.zip`](file:///Users/simple/.gemini/antigravity/scratch/hypetex-portal/wordpress_plugin/brixen-crm-sync.zip). |

---

## 4. Automated Test Suite Verification
- **Run Test Suite**: `python3 seed_db.py && python3 test_app.py`
- **Current Status**: **100% Passed (19/19 direct system & regression tests)**.

---

## 5. Primary Architecture Directive: CRM Portal Native Feature Development

- **Rule**: All future features, UI modules, intake tools, document filing pipelines, analytics, customer tools, and staff management workflows MUST be developed directly inside the Brixen CRM Portal (`portal.brixenconsultants.com`) codebase (`app.py`, `templates/index.html`, `static/js/app.js`).
- **Purpose**: Eliminates the need to update or re-upload the WordPress plugin (`brixen-crm-sync.zip`) for new developments. The WordPress plugin remains a fixed, stable, 1-time setup event bridge for user registration, WooCommerce checkouts, and signed SSO redirects.
