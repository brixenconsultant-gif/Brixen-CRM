# Accounts File — simple director books, Companies House, HMRC tax

Kaylx Ai is **not an accountant**. They have UK **bank statements**. They want books, then a Companies House filing, then a plain-English HMRC Corporation Tax step. Ask only when a file or credential is missing.

This lives **inside the existing Accountancy tab** of the Brixen CRM/CMS portal (staff and client). It is not a separate sidebar item. Chart of accounts and journals stay under **More**.

## Three paths (this checkout)

On **Start**, pick one:

1. **It traded (micro-entity, default)** — add a UK bank statement (CSV / paste / text PDF, or a file already in Documents). Check each line in plain English. Download FRS 105 members + filleted packs, send to Companies House (live XML Gateway when credentials exist, otherwise sandbox + WebFiling).
2. **It slept (dormant)** — no bank statement. Short dormant balance sheet (CA 2006 s480 / s1169). Same send-to-CH pattern.
3. **HMRC tax** — profit, tax guide (19% / 25% / blend), pay-by date (9 months + 1 day), Company Tax Return date (12 months). Dormant: usually nothing to pay; tell HMRC. Sandbox receipt + GOV.UK link. Not a filed CT600 / MTD package.

Do not auto-import a statement until they pick **It traded**.

## Where to click (portal)

Hard-refresh, then:

| Who | Path |
| --- | --- |
| Client | **My Portfolio → Accountancy** (`#accountancy`). Inner pill **Accounts** (default). Then **It traded** / **It slept** / **HMRC tax**. Inner pill **Filing status** is the existing tracker table. |
| Staff | Sidebar **Accountancy** (`#admin-accountancy`). Inner pill **Accounts** (default). Same Start paths. **Filing status** is the staff tracker + File accounts. |

Old hashes `#accounts` / `#year-end` / `#admin-accounts` / `#admin-year-end` remap to Accountancy. There are no top-level Accounts or Year end sidebar items.

Inner Accounts tabs: **Start** → (if traded) **Bank** → **Check numbers** → **Companies House** → **HMRC tax**. Company details. More: reports, chart, journals, notes.

## What the user must provide

| Need | When |
| --- | --- |
| Path (traded / slept) | Always, on Start. |
| Bank statement (CSV, paste, text PDF, or a file already in Documents) | **Traded only.** Not for dormant. |
| Company (already in Company Registered) | Always. |
| Company number | On the pack; asked only if missing. |
| Companies House authentication code | Only for **software send**. Optional if they upload the pack in WebFiling. |
| Presenter ID + password / API key | Only for a live XML Gateway send. If absent, sandbox + WebFiling. |
| HMRC tax number (UTR) | Optional. Only if they have the letter from HMRC. Never blocks. |

Never required to start: accountant login, Xero, opening journals, CT600 software.

## Portal placement

| Surface | Where |
| --- | --- |
| Staff | Sidebar **Accountancy** only. Inner pills **Accounts** (Accounts File: traded / dormant / tax) and **Filing status** (existing tracker / cash book). |
| Clients | My Portfolio **Accountancy** only. Same inner pills. |
| Persistence | SQLite `ledger_books` (`filing_kind`, `hmrc_utr`), `ledger_nominals`, `ledger_journals`, `ledger_statements`, `ledger_bank_lines`, `ledger_ch_filings`, `ledger_hmrc_filings` keyed by CRM `company_id`. |
| APIs | `/api/admin/accounts-file…` and `/api/client/accounts-file…` — `choose-path`, `import-statement`, `sample-statement`, `recategorise`, `confirm-review`, `submit-companies-house`, `submit-hmrc`, `export/{members,filleted,ixbrl}`. |
| Engine | `accounts_file.py`. UI: `static/js/accounts-file.js` nested in `#view-client-accountancy` / `#view-admin-accountancy`. |

Do **not** add Accounts or Year end as top-level sidebar items. The old Accountancy filing tracker remains as the inner **Filing status** pane.

## Rules relied on (read 17 September 2026)

| Topic | Rule | Source |
| --- | --- | --- |
| Micro size test | 2 of 3: turnover, balance sheet total, average employees | CA 2006 s384A |
| Thresholds from 6 Apr 2025 | Turnover £1m; BS total £500k; employees 10 | SI 2024/1303 |
| Thresholds before 6 Apr 2025 | £632k / £316k / 10 | Periods beginning 30 Sep 2013–5 Apr 2025 |
| Dormant | No significant accounting transactions | CA 2006 s1169 |
| Dormant audit exemption | Dormant companies | CA 2006 s480 |
| Filing deadline (private) | 9 months from ARD | CA 2006 s442 |
| Corporation Tax rates (FY2023+) | 19% ≤ £50k; 25% ≥ £250k; marginal relief 3/200 between | FA 2021; HMRC CT rates |
| CT pay | 9 months + 1 day after period end | HMRC |
| CT600 due | 12 months after period end | HMRC |
| What to file at CH | Micro may omit P&L until accounts delivered on/after 1 April 2028 | CH guidance s9.4 |

## Honest gaps

- Draft iXBRL is **not** a validated TIS v5.9 package.
- HMRC helper is **not** a live CT600 / iXBRL software filing. Sandbox + GOV.UK.
- Live CH send posts GovTalk when presenter + company auth code exist; otherwise `BRIXEN-SANDBOX-…` + WebFiling HTML.

## Out of this slice

Bank feeds, invoicing, payroll, VAT returns, FRS 102 Section 1A, groups, charities, LLPs, TIS-valid iXBRL, Making Tax Digital CT.

## Next

1. TIS v5.9 package + validated CH software filing when a presenter is on the server.
2. Scanned PDF OCR if directors only have photos of statements.
3. Live HMRC CT software filing if/when credentials exist.
