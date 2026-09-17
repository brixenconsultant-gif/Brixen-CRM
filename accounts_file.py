"""UK FRS 105 micro-entity ledger and statutory year-end packs.

Lives inside the Brixen CRM portal. Not a Xero product: same information
architecture (home, chart of accounts, journals, P&L, balance sheet, trial
balance, year end) feeding Companies House micro-entity accounts.
"""
from __future__ import annotations

import calendar
import datetime
import html as html_lib
import json
import re

from db import execute_db, get_db, query_db

LEDGER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledger_books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL UNIQUE,
    registered_name TEXT NOT NULL DEFAULT '',
    company_number TEXT NOT NULL DEFAULT '',
    registered_office TEXT NOT NULL DEFAULT '',
    directors_json TEXT NOT NULL DEFAULT '[]',
    incorporation_date DATE,
    period_start DATE,
    period_end DATE,
    prior_period_start DATE,
    prior_period_end DATE,
    employees INTEGER NOT NULL DEFAULT 0,
    prior_employees INTEGER NOT NULL DEFAULT 0,
    is_first_accounts INTEGER NOT NULL DEFAULT 0,
    sample_loaded INTEGER NOT NULL DEFAULT 0,
    exclusions_json TEXT NOT NULL DEFAULT '{}',
    notes_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ledger_books_company ON ledger_books(company_id);

CREATE TABLE IF NOT EXISTS ledger_nominals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL,
    frs105_pl TEXT,
    frs105_bs TEXT,
    watched INTEGER NOT NULL DEFAULT 0,
    opening_debit REAL NOT NULL DEFAULT 0,
    opening_credit REAL NOT NULL DEFAULT 0,
    prior_debit REAL NOT NULL DEFAULT 0,
    prior_credit REAL NOT NULL DEFAULT 0,
    UNIQUE(book_id, code),
    FOREIGN KEY (book_id) REFERENCES ledger_books(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ledger_nominals_book ON ledger_nominals(book_id, code);

CREATE TABLE IF NOT EXISTS ledger_journals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    journal_date DATE NOT NULL,
    narration TEXT NOT NULL,
    reference TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES ledger_books(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ledger_journals_book ON ledger_journals(book_id, journal_date);

CREATE TABLE IF NOT EXISTS ledger_journal_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_id INTEGER NOT NULL,
    nominal_code TEXT NOT NULL,
    description TEXT,
    debit REAL NOT NULL DEFAULT 0,
    credit REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (journal_id) REFERENCES ledger_journals(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ledger_journal_lines_journal ON ledger_journal_lines(journal_id);
"""

MICRO_FROM = datetime.date(2025, 4, 6)
THRESHOLDS_NEW = (1_000_000.0, 500_000.0, 10)
THRESHOLDS_OLD = (632_000.0, 316_000.0, 10)

PL_LABELS = {
    'A': 'Turnover',
    'B': 'Other income',
    'C': 'Cost of raw materials and consumables',
    'D': 'Staff costs',
    'E': 'Depreciation and other amounts written off assets',
    'F': 'Other charges',
    'G': 'Tax',
    'H': 'Profit or loss',
}

BS_LABELS = {
    'A': 'Called up share capital not paid',
    'B': 'Fixed assets',
    'C': 'Current assets',
    'D': 'Prepayments and accrued income',
    'E': 'Creditors: amounts falling due within one year',
    'F': 'Net current assets (liabilities)',
    'G': 'Total assets less current liabilities',
    'H': 'Creditors: amounts falling due after more than one year',
    'I': 'Provisions for liabilities',
    'J': 'Accruals and deferred income',
    'K': 'Capital and reserves',
}

EXCLUSION_FIELDS = (
    ('plc', 'Public limited company'),
    ('overseas', 'Overseas company'),
    ('charity', 'Charity'),
    ('credit_insurance', 'Credit, insurance or authorised investment firm'),
    ('financial_holding', 'Financial holding company'),
    ('parent_group', 'Parent preparing group accounts'),
    ('subsidiary', 'Subsidiary included in consolidation'),
)

# code, name, type, P&L letter, BS letter, watched
UK_COA = (
    ('0010', 'Freehold property', 'FIXED_ASSET', None, 'B', 0),
    ('0020', 'Plant and machinery', 'FIXED_ASSET', None, 'B', 0),
    ('0030', 'Office equipment', 'FIXED_ASSET', None, 'B', 0),
    ('0031', 'Office equipment accumulated depreciation', 'FIXED_ASSET_CONTRA', None, 'B', 0),
    ('0040', 'Motor vehicles', 'FIXED_ASSET', None, 'B', 0),
    ('0041', 'Motor vehicles accumulated depreciation', 'FIXED_ASSET_CONTRA', None, 'B', 0),
    ('0100', 'Called up share capital not paid', 'CURRENT_ASSET', None, 'A', 0),
    ('1000', 'Stock', 'CURRENT_ASSET', None, 'C', 0),
    ('1100', 'Trade debtors', 'CURRENT_ASSET', None, 'C', 1),
    ('1103', 'Other debtors', 'CURRENT_ASSET', None, 'C', 0),
    ('1200', 'Bank current account', 'BANK', None, 'C', 1),
    ('1201', 'Bank deposit account', 'BANK', None, 'C', 0),
    ('1210', 'Petty cash', 'BANK', None, 'C', 0),
    ('1400', 'Prepayments', 'CURRENT_ASSET', None, 'D', 0),
    ('2100', 'Trade creditors', 'CURRENT_LIABILITY', None, 'E', 1),
    ('2109', 'Accruals', 'CURRENT_LIABILITY', None, 'E', 0),
    ('2200', 'VAT control', 'CURRENT_LIABILITY', None, 'E', 0),
    ('2210', 'PAYE/NIC', 'CURRENT_LIABILITY', None, 'E', 0),
    ('2220', 'Corporation tax', 'CURRENT_LIABILITY', None, 'E', 0),
    ('2300', 'Directors loan (due after one year)', 'LONG_TERM_LIABILITY', None, 'H', 0),
    ('2400', 'Bank loan (due after one year)', 'LONG_TERM_LIABILITY', None, 'H', 0),
    ('2500', 'Provisions for liabilities', 'PROVISION', None, 'I', 0),
    ('2600', 'Deferred income', 'LONG_TERM_LIABILITY', None, 'J', 0),
    ('3000', 'Share capital', 'EQUITY', None, 'K', 0),
    ('3100', 'Profit and loss account', 'EQUITY', None, 'K', 0),
    ('3200', 'Dividends', 'EQUITY', None, 'K', 0),
    ('4000', 'Sales / turnover', 'INCOME', 'A', None, 1),
    ('4900', 'Other operating income', 'INCOME', 'B', None, 0),
    ('5000', 'Materials and consumables', 'COS', 'C', None, 0),
    ('7000', 'Staff wages', 'EXPENSE', 'D', None, 1),
    ('7001', 'Employers National Insurance', 'EXPENSE', 'D', None, 0),
    ('7002', 'Directors remuneration', 'EXPENSE', 'D', None, 0),
    ('7003', 'Pension contributions', 'EXPENSE', 'D', None, 0),
    ('7100', 'Rent', 'EXPENSE', 'F', None, 0),
    ('7200', 'Light and heat', 'EXPENSE', 'F', None, 0),
    ('7300', 'Motor expenses', 'EXPENSE', 'F', None, 0),
    ('7400', 'Travel', 'EXPENSE', 'F', None, 0),
    ('7500', 'Printing and stationery', 'EXPENSE', 'F', None, 0),
    ('7600', 'Professional fees', 'EXPENSE', 'F', None, 0),
    ('7601', 'Accountancy', 'EXPENSE', 'F', None, 0),
    ('7700', 'Bank charges', 'EXPENSE', 'F', None, 0),
    ('7800', 'Insurance', 'EXPENSE', 'F', None, 0),
    ('7900', 'Depreciation', 'EXPENSE', 'E', None, 0),
    ('8000', 'Interest payable', 'EXPENSE', 'F', None, 0),
    ('8100', 'Tax on profit', 'EXPENSE', 'G', None, 0),
)

SAMPLE_ORG = {
    'registered_name': 'Willow & Thorn Consulting Ltd',
    'company_number': '14122819',
    'registered_office': '14 King Street, Manchester, M2 6AG',
    'directors': [{'name': 'Amelia Thorn', 'role': 'Director'}],
    'incorporation_date': '2023-04-12',
    'period_start': '2025-04-01',
    'period_end': '2026-03-31',
    'prior_period_start': '2024-04-01',
    'prior_period_end': '2025-03-31',
    'employees': 2,
    'prior_employees': 2,
    'is_first_accounts': 0,
    'notes': {
        'off_balance_sheet': 'The company had no off-balance sheet arrangements during the year.',
        'directors_advances': 'No advances, credits or guarantees were given to directors during the year.',
        'commitments': 'There were no capital commitments, guarantees or contingencies at the year end.',
    },
}

# Opening trial balance at 1 April 2025 (Dr, Cr)
SAMPLE_OPENING = {
    '1200': (19420.00, 0.0),
    '1100': (2400.00, 0.0),
    '0030': (8500.00, 0.0),
    '0031': (0.0, 1700.00),
    '2100': (0.0, 1200.00),
    '2220': (0.0, 1680.00),
    '3000': (0.0, 100.00),
    '3100': (0.0, 25640.00),
}

# Prior-year P&L comparatives (Dr, Cr) — not in opening TB
SAMPLE_PRIOR_PL = {
    '4000': (0.0, 71200.00),
    '5000': (3800.00, 0.0),
    '7000': (16800.00, 0.0),
    '7002': (7700.00, 0.0),
    '7900': (1700.00, 0.0),
    '7100': (7200.00, 0.0),
    '7600': (1800.00, 0.0),
    '7300': (1600.00, 0.0),
    '7800': (400.00, 0.0),
    '7700': (200.00, 0.0),
    '8100': (1680.00, 0.0),
}

SAMPLE_JOURNALS = (
    ('2025-04-30', 'YE-01', 'Cash sales received', (
        ('1200', 72000.00, 0.0, 'Receipts from clients'),
        ('4000', 0.0, 72000.00, 'Turnover'),
    )),
    ('2025-06-30', 'YE-02', 'Sales on account', (
        ('1100', 12400.00, 0.0, 'Trade debtors'),
        ('4000', 0.0, 12400.00, 'Turnover'),
    )),
    ('2025-09-30', 'YE-03', 'Debtors collected', (
        ('1200', 10800.00, 0.0, 'Receipts'),
        ('1100', 0.0, 10800.00, 'Debtors'),
    )),
    ('2025-12-15', 'YE-04', 'Materials purchased', (
        ('5000', 4200.00, 0.0, 'Consumables'),
        ('1200', 0.0, 4200.00, 'Bank'),
    )),
    ('2026-03-31', 'YE-05', 'Staff wages for the year', (
        ('7000', 18600.00, 0.0, 'Gross wages'),
        ('1200', 0.0, 18600.00, 'Bank'),
    )),
    ('2026-03-31', 'YE-06', 'Director remuneration', (
        ('7002', 8400.00, 0.0, 'Director'),
        ('1200', 0.0, 8400.00, 'Bank'),
    )),
    ('2026-03-31', 'YE-07', 'Office rent', (
        ('7100', 7200.00, 0.0, 'Rent'),
        ('1200', 0.0, 7200.00, 'Bank'),
    )),
    ('2026-03-31', 'YE-08', 'Insurance', (
        ('7800', 640.00, 0.0, 'Insurance'),
        ('1200', 0.0, 640.00, 'Bank'),
    )),
    ('2026-03-31', 'YE-09', 'Professional fees accrued', (
        ('7600', 1850.00, 0.0, 'Legal and professional'),
        ('2100', 0.0, 1850.00, 'Trade creditors'),
    )),
    ('2026-03-31', 'YE-10', 'Bank charges', (
        ('7700', 186.00, 0.0, 'Charges'),
        ('1200', 0.0, 186.00, 'Bank'),
    )),
    ('2026-03-31', 'YE-11', 'Motor expenses', (
        ('7300', 2140.00, 0.0, 'Motor'),
        ('1200', 0.0, 2140.00, 'Bank'),
    )),
    ('2026-03-31', 'YE-12', 'Suppliers paid', (
        ('2100', 2150.00, 0.0, 'Creditors'),
        ('1200', 0.0, 2150.00, 'Bank'),
    )),
    ('2026-03-31', 'YE-13', 'Year-end depreciation', (
        ('7900', 1700.00, 0.0, 'Depreciation'),
        ('0031', 0.0, 1700.00, 'Accumulated depreciation'),
    )),
    ('2026-03-31', 'YE-14', 'Accountancy accrual', (
        ('7601', 950.00, 0.0, 'Year-end accounts'),
        ('2109', 0.0, 950.00, 'Accruals'),
    )),
    ('2026-03-31', 'YE-15', 'Corporation tax charge', (
        ('8100', 2140.00, 0.0, 'Tax'),
        ('2220', 0.0, 2140.00, 'Corporation tax'),
    )),
    ('2026-01-20', 'YE-16', 'Prior year tax paid', (
        ('2220', 1680.00, 0.0, 'Corporation tax'),
        ('1200', 0.0, 1680.00, 'Bank'),
    )),
    ('2026-03-31', 'YE-17', 'Sundry income received', (
        ('1200', 120.00, 0.0, 'Bank'),
        ('4900', 0.0, 120.00, 'Other income'),
    )),
)


def money(value):
    return round(float(value or 0) + 1e-12, 2)


def _parse_json(raw, fallback):
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


def _parse_date(value):
    text = str(value or '').strip()[:10]
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def add_months(day, months):
    year = day.year + (day.month - 1 + months) // 12
    month = (day.month - 1 + months) % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(day.day, last))


def month_span(start, end):
    if not start or not end or end < start:
        return 0
    return (end.year - start.year) * 12 + (end.month - start.month) + (1 if end.day >= start.day else 0)


def thresholds_for(period_start):
    if period_start and period_start >= MICRO_FROM:
        return THRESHOLDS_NEW
    return THRESHOLDS_OLD


def size_test(turnover, bs_total, employees, period_start, prior=None, is_first=False):
    """CA 2006 s384A. Two of three. Look-back uses current thresholds when
    the year begins on/after 6 April 2025 (SI 2024/1303 reg 3)."""
    limits = thresholds_for(period_start)
    hits = 0
    details = []
    checks = (
        ('Turnover', money(turnover), limits[0]),
        ('Balance sheet total', money(bs_total), limits[1]),
        ('Average employees', int(employees or 0), limits[2]),
    )
    for label, actual, limit in checks:
        ok = actual <= limit
        hits += int(ok)
        details.append({'label': label, 'actual': actual, 'limit': limit, 'met': ok})
    this_year = hits >= 2
    if is_first:
        qualifies = this_year
        rule = 'First accounting period: the company qualifies if it meets at least two of the three conditions this year (CA 2006 s384A).'
    else:
        prior_ok = True
        if prior:
            prior_start = _parse_date(prior.get('period_start')) or period_start
            # Increased thresholds treated as if they applied in prior years
            # when testing a year beginning on/after 6 Apr 2025.
            lookback_start = period_start if (period_start and period_start >= MICRO_FROM) else prior_start
            prior_limits = thresholds_for(lookback_start)
            prior_hits = 0
            prior_hits += int(money(prior.get('turnover')) <= prior_limits[0])
            prior_hits += int(money(prior.get('bs_total')) <= prior_limits[1])
            prior_hits += int(int(prior.get('employees') or 0) <= prior_limits[2])
            prior_ok = prior_hits >= 2
        qualifies = this_year and prior_ok
        rule = 'Two consecutive years: the company must meet the conditions this year and last year to remain (or become) a micro-entity (s384A(1)–(3)).'
    return {
        'qualifies': bool(qualifies),
        'this_year': this_year,
        'hits': hits,
        'needed': 2,
        'thresholds': {'turnover': limits[0], 'bs_total': limits[1], 'employees': limits[2]},
        'details': details,
        'rule': rule,
        'from_6_apr_2025': bool(period_start and period_start >= MICRO_FROM),
    }


def filing_deadline(period_end, incorporation_date=None, is_first=False, period_start=None):
    """CA 2006 s442. Private company: 9 months from ARD. First accounts covering
    more than 12 months: later of 21 months from incorporation and 3 months from ARD."""
    if not period_end:
        return None, 'Set the accounting reference date to calculate the filing deadline.'
    if is_first and period_start and month_span(period_start, period_end) > 12 and incorporation_date:
        due = max(add_months(incorporation_date, 21), add_months(period_end, 3))
        note = 'First accounts covering more than 12 months: later of 21 months from incorporation and 3 months from the accounting reference date (s442).'
    else:
        due = add_months(period_end, 9)
        note = 'Private company accounts are due 9 months after the accounting reference date (CA 2006 s442).'
    return due.isoformat(), note


def ensure_ledger_schema():
    conn = get_db()
    conn.executescript(LEDGER_SCHEMA_SQL)
    conn.commit()


def _company_row(company_id, user_id=None):
    if user_id:
        return query_db(
            "SELECT * FROM companies WHERE id = ? AND user_id = ?;",
            (company_id, user_id),
            one=True,
        )
    return query_db("SELECT * FROM companies WHERE id = ?;", (company_id,), one=True)


def list_companies(user_id=None):
    ensure_ledger_schema()
    if user_id:
        rows = query_db(
            """
            SELECT c.id, c.name, c.company_number, c.director, c.inc_date, c.reg_office, c.user_id,
                   u.full_name AS owner_name
            FROM companies c
            LEFT JOIN users u ON u.id = c.user_id
            WHERE c.user_id = ?
            ORDER BY c.name COLLATE NOCASE;
            """,
            (user_id,),
        ) or []
    else:
        rows = query_db(
            """
            SELECT c.id, c.name, c.company_number, c.director, c.inc_date, c.reg_office, c.user_id,
                   u.full_name AS owner_name
            FROM companies c
            LEFT JOIN users u ON u.id = c.user_id
            ORDER BY c.name COLLATE NOCASE;
            """
        ) or []
    books = {
        int(b['company_id']): b
        for b in (query_db("SELECT company_id, registered_name, period_end, sample_loaded FROM ledger_books;") or [])
    }
    out = []
    for row in rows:
        book = books.get(int(row['id']))
        out.append({
            'id': int(row['id']),
            'name': row.get('name') or '',
            'company_number': row.get('company_number') or '',
            'director': row.get('director') or '',
            'inc_date': row.get('inc_date'),
            'reg_office': row.get('reg_office') or '',
            'owner_name': row.get('owner_name') or '',
            'has_books': bool(book),
            'sample_loaded': bool(book and book.get('sample_loaded')),
            'period_end': (book or {}).get('period_end'),
            'ledger_name': (book or {}).get('registered_name') or row.get('name'),
        })
    return out


def _directors_for_company(company):
    rows = query_db(
        "SELECT name, role FROM company_directors WHERE company_id = ? ORDER BY id;",
        (company['id'],),
    ) or []
    directors = [{'name': r.get('name') or '', 'role': r.get('role') or 'Director'} for r in rows if r.get('name')]
    if not directors and company.get('director'):
        directors = [{'name': company.get('director'), 'role': 'Director'}]
    return directors


def _default_period_from_company(company):
    inc = _parse_date(company.get('inc_date')) or datetime.date(2024, 4, 1)
    # Default to the year ending 31 March after the latest 31 March that is at least 6 months after incorporation.
    year = datetime.date.today().year
    period_end = datetime.date(year, 3, 31)
    if datetime.date.today() > datetime.date(year, 9, 30):
        period_end = datetime.date(year + 1, 3, 31)
    if period_end <= inc:
        period_end = datetime.date(inc.year + 1, 3, 31)
    period_start = datetime.date(period_end.year - 1, 4, 1)
    if period_start < inc:
        period_start = inc
    prior_end = period_start - datetime.timedelta(days=1)
    prior_start = datetime.date(prior_end.year - 1, prior_end.month, prior_end.day) if prior_end.month == 3 and prior_end.day == 31 else add_months(prior_end, -12) + datetime.timedelta(days=1)
    return {
        'period_start': period_start.isoformat(),
        'period_end': period_end.isoformat(),
        'prior_period_start': prior_start.isoformat(),
        'prior_period_end': prior_end.isoformat(),
        'is_first_accounts': 1 if (datetime.date.today() - inc).days < 500 else 0,
    }


def _book_for_company(company_id):
    return query_db("SELECT * FROM ledger_books WHERE company_id = ?;", (company_id,), one=True)


def _insert_coa(book_id, openings=None, priors=None, watches=None):
    openings = openings or {}
    priors = priors or {}
    watches = watches or {}
    conn = get_db()
    for code, name, atype, pl, bs, watched in UK_COA:
        od, oc = openings.get(code, (0.0, 0.0))
        pd, pc = priors.get(code, (0.0, 0.0))
        conn.execute(
            """
            INSERT INTO ledger_nominals (
                book_id, code, name, account_type, frs105_pl, frs105_bs, watched,
                opening_debit, opening_credit, prior_debit, prior_credit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                book_id, code, name, atype, pl, bs, int(watches.get(code, watched)),
                money(od), money(oc), money(pd), money(pc),
            ),
        )
    conn.commit()


def _clear_books(book_id):
    conn = get_db()
    conn.execute(
        """
        DELETE FROM ledger_journal_lines WHERE journal_id IN (
            SELECT id FROM ledger_journals WHERE book_id = ?
        );
        """,
        (book_id,),
    )
    conn.execute("DELETE FROM ledger_journals WHERE book_id = ?;", (book_id,))
    conn.execute("DELETE FROM ledger_nominals WHERE book_id = ?;", (book_id,))
    conn.commit()


def _write_journal(book_id, journal_date, narration, lines, reference=None):
    total_dr = money(sum(money(line[1]) for line in lines))
    total_cr = money(sum(money(line[2]) for line in lines))
    if total_dr != total_cr:
        return None, f'Debits (£{total_dr:,.2f}) must equal credits (£{total_cr:,.2f}).'
    if total_dr <= 0:
        return None, 'Enter at least one debit and one matching credit.'
    codes = {row['code'] for row in (query_db("SELECT code FROM ledger_nominals WHERE book_id = ?;", (book_id,)) or [])}
    for line in lines:
        if line[0] not in codes:
            return None, f'Nominal {line[0]} is not on this chart of accounts.'
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO ledger_journals (book_id, journal_date, narration, reference)
        VALUES (?, ?, ?, ?);
        """,
        (book_id, journal_date, narration, reference),
    )
    journal_id = cur.lastrowid
    for code, debit, credit, description in lines:
        conn.execute(
            """
            INSERT INTO ledger_journal_lines (journal_id, nominal_code, description, debit, credit)
            VALUES (?, ?, ?, ?, ?);
            """,
            (journal_id, code, description or '', money(debit), money(credit)),
        )
    conn.commit()
    return journal_id, None


def start_blank_books(company_id, user_id=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    ensure_ledger_schema()
    existing = _book_for_company(company_id)
    period = _default_period_from_company(company)
    payload = {
        'registered_name': company.get('name') or '',
        'company_number': company.get('company_number') or '',
        'registered_office': company.get('reg_office') or '',
        'directors_json': json.dumps(_directors_for_company(company)),
        'incorporation_date': company.get('inc_date'),
        'employees': 0,
        'prior_employees': 0,
        'is_first_accounts': period['is_first_accounts'],
        'sample_loaded': 0,
        'exclusions_json': json.dumps({key: False for key, _ in EXCLUSION_FIELDS}),
        'notes_json': json.dumps({
            'off_balance_sheet': '',
            'directors_advances': '',
            'commitments': '',
        }),
        **period,
    }
    conn = get_db()
    if existing:
        _clear_books(existing['id'])
        conn.execute(
            """
            UPDATE ledger_books SET
                registered_name=?, company_number=?, registered_office=?, directors_json=?,
                incorporation_date=?, period_start=?, period_end=?, prior_period_start=?,
                prior_period_end=?, employees=?, prior_employees=?, is_first_accounts=?,
                sample_loaded=0, exclusions_json=?, notes_json=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?;
            """,
            (
                payload['registered_name'], payload['company_number'], payload['registered_office'],
                payload['directors_json'], payload['incorporation_date'], payload['period_start'],
                payload['period_end'], payload['prior_period_start'], payload['prior_period_end'],
                payload['employees'], payload['prior_employees'], payload['is_first_accounts'],
                payload['exclusions_json'], payload['notes_json'], existing['id'],
            ),
        )
        conn.commit()
        book_id = existing['id']
    else:
        book_id = execute_db(
            """
            INSERT INTO ledger_books (
                company_id, registered_name, company_number, registered_office, directors_json,
                incorporation_date, period_start, period_end, prior_period_start, prior_period_end,
                employees, prior_employees, is_first_accounts, sample_loaded, exclusions_json, notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?);
            """,
            (
                company_id, payload['registered_name'], payload['company_number'],
                payload['registered_office'], payload['directors_json'], payload['incorporation_date'],
                payload['period_start'], payload['period_end'], payload['prior_period_start'],
                payload['prior_period_end'], payload['employees'], payload['prior_employees'],
                payload['is_first_accounts'], payload['exclusions_json'], payload['notes_json'],
            ),
        )
    _insert_coa(book_id)
    return workspace(company_id, user_id)


def load_sample_books(company_id, user_id=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    ensure_ledger_schema()
    existing = _book_for_company(company_id)
    conn = get_db()
    directors_json = json.dumps(SAMPLE_ORG['directors'])
    notes_json = json.dumps(SAMPLE_ORG['notes'])
    exclusions_json = json.dumps({key: False for key, _ in EXCLUSION_FIELDS})
    if existing:
        _clear_books(existing['id'])
        conn.execute(
            """
            UPDATE ledger_books SET
                registered_name=?, company_number=?, registered_office=?, directors_json=?,
                incorporation_date=?, period_start=?, period_end=?, prior_period_start=?,
                prior_period_end=?, employees=?, prior_employees=?, is_first_accounts=0,
                sample_loaded=1, exclusions_json=?, notes_json=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?;
            """,
            (
                SAMPLE_ORG['registered_name'], SAMPLE_ORG['company_number'], SAMPLE_ORG['registered_office'],
                directors_json, SAMPLE_ORG['incorporation_date'], SAMPLE_ORG['period_start'],
                SAMPLE_ORG['period_end'], SAMPLE_ORG['prior_period_start'], SAMPLE_ORG['prior_period_end'],
                SAMPLE_ORG['employees'], SAMPLE_ORG['prior_employees'], exclusions_json, notes_json,
                existing['id'],
            ),
        )
        conn.commit()
        book_id = existing['id']
    else:
        book_id = execute_db(
            """
            INSERT INTO ledger_books (
                company_id, registered_name, company_number, registered_office, directors_json,
                incorporation_date, period_start, period_end, prior_period_start, prior_period_end,
                employees, prior_employees, is_first_accounts, sample_loaded, exclusions_json, notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?);
            """,
            (
                company_id, SAMPLE_ORG['registered_name'], SAMPLE_ORG['company_number'],
                SAMPLE_ORG['registered_office'], directors_json, SAMPLE_ORG['incorporation_date'],
                SAMPLE_ORG['period_start'], SAMPLE_ORG['period_end'], SAMPLE_ORG['prior_period_start'],
                SAMPLE_ORG['prior_period_end'], SAMPLE_ORG['employees'], SAMPLE_ORG['prior_employees'],
                exclusions_json, notes_json,
            ),
        )
    watches = {code: 1 for code, *rest in UK_COA if rest[-1]}
    _insert_coa(book_id, SAMPLE_OPENING, SAMPLE_PRIOR_PL, watches)
    for journal_date, reference, narration, lines in SAMPLE_JOURNALS:
        _, error = _write_journal(book_id, journal_date, narration, lines, reference)
        if error:
            return None, error
    return workspace(company_id, user_id)


def save_organisation(company_id, data, user_id=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    book = _book_for_company(company_id)
    if not book:
        started, error = start_blank_books(company_id, user_id)
        if error:
            return None, error
        book = _book_for_company(company_id)
    directors = data.get('directors') if isinstance(data.get('directors'), list) else _parse_json(book.get('directors_json'), [])
    clean_directors = []
    for item in directors:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        if name:
            clean_directors.append({'name': name, 'role': str(item.get('role') or 'Director').strip() or 'Director'})
    exclusions = _parse_json(book.get('exclusions_json'), {})
    incoming_ex = data.get('exclusions') if isinstance(data.get('exclusions'), dict) else {}
    exclusions.update({key: bool(incoming_ex.get(key)) for key, _ in EXCLUSION_FIELDS if key in incoming_ex})
    notes = _parse_json(book.get('notes_json'), {})
    incoming_notes = data.get('notes') if isinstance(data.get('notes'), dict) else {}
    for key in ('off_balance_sheet', 'directors_advances', 'commitments'):
        if key in incoming_notes:
            notes[key] = str(incoming_notes.get(key) or '').strip()
    execute_db(
        """
        UPDATE ledger_books SET
            registered_name=?, company_number=?, registered_office=?, directors_json=?,
            incorporation_date=?, period_start=?, period_end=?, prior_period_start=?,
            prior_period_end=?, employees=?, prior_employees=?, is_first_accounts=?,
            exclusions_json=?, notes_json=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?;
        """,
        (
            str(data.get('registered_name') or book.get('registered_name') or '').strip(),
            re.sub(r'\D', '', str(data.get('company_number') or book.get('company_number') or ''))[:8],
            str(data.get('registered_office') or book.get('registered_office') or '').strip(),
            json.dumps(clean_directors),
            str(data.get('incorporation_date') or book.get('incorporation_date') or '')[:10] or None,
            str(data.get('period_start') or book.get('period_start') or '')[:10] or None,
            str(data.get('period_end') or book.get('period_end') or '')[:10] or None,
            str(data.get('prior_period_start') or book.get('prior_period_start') or '')[:10] or None,
            str(data.get('prior_period_end') or book.get('prior_period_end') or '')[:10] or None,
            int(data.get('employees') if data.get('employees') not in (None, '') else book.get('employees') or 0),
            int(data.get('prior_employees') if data.get('prior_employees') not in (None, '') else book.get('prior_employees') or 0),
            1 if data.get('is_first_accounts') else 0,
            json.dumps(exclusions),
            json.dumps(notes),
            book['id'],
        ),
    )
    return workspace(company_id, user_id)


def post_journal(company_id, data, user_id=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    book = _book_for_company(company_id)
    if not book:
        return None, 'Start a chart of accounts before posting journals.'
    journal_date = str(data.get('journal_date') or book.get('period_end') or '')[:10]
    if not _parse_date(journal_date):
        return None, 'Enter a valid journal date.'
    narration = str(data.get('narration') or '').strip()
    if not narration:
        return None, 'Describe the journal (for example, year-end depreciation).'
    raw_lines = data.get('lines') if isinstance(data.get('lines'), list) else []
    lines = []
    for item in raw_lines:
        if not isinstance(item, dict):
            continue
        code = str(item.get('nominal_code') or item.get('code') or '').strip()
        debit = money(item.get('debit'))
        credit = money(item.get('credit'))
        if not code or (debit <= 0 and credit <= 0):
            continue
        if debit > 0 and credit > 0:
            return None, 'A line cannot be both a debit and a credit.'
        lines.append((code, debit, credit, str(item.get('description') or narration)))
    journal_id, error = _write_journal(
        book['id'],
        journal_date,
        narration,
        lines,
        str(data.get('reference') or '').strip() or None,
    )
    if error:
        return None, error
    ws, _ = workspace(company_id, user_id)
    ws['posted_journal_id'] = journal_id
    return ws, None


def toggle_watch(company_id, code, user_id=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    book = _book_for_company(company_id)
    if not book:
        return None, 'No accounts file for this company yet.'
    row = query_db(
        "SELECT id, watched FROM ledger_nominals WHERE book_id = ? AND code = ?;",
        (book['id'], str(code).strip()),
        one=True,
    )
    if not row:
        return None, 'Nominal code not found.'
    execute_db(
        "UPDATE ledger_nominals SET watched = ? WHERE id = ?;",
        (0 if row.get('watched') else 1, row['id']),
    )
    return workspace(company_id, user_id)


def _movement_maps(book_id):
    rows = query_db(
        """
        SELECT l.nominal_code AS code,
               SUM(l.debit) AS debit,
               SUM(l.credit) AS credit
        FROM ledger_journal_lines l
        JOIN ledger_journals j ON j.id = l.journal_id
        WHERE j.book_id = ?
        GROUP BY l.nominal_code;
        """,
        (book_id,),
    ) or []
    return {r['code']: (money(r['debit']), money(r['credit'])) for r in rows}


def _nominal_balances(book):
    movements = _movement_maps(book['id'])
    accounts = query_db(
        "SELECT * FROM ledger_nominals WHERE book_id = ? ORDER BY code;",
        (book['id'],),
    ) or []
    out = []
    for row in accounts:
        code = row['code']
        move_dr, move_cr = movements.get(code, (0.0, 0.0))
        ytd_dr = money(row['opening_debit'] + move_dr)
        ytd_cr = money(row['opening_credit'] + move_cr)
        net = money(ytd_dr - ytd_cr)
        prior_net = money(row['prior_debit'] - row['prior_credit'])
        if row['account_type'] in ('INCOME',):
            prior_display = money(row['prior_credit'] - row['prior_debit'])
        elif row['account_type'] in ('COS', 'EXPENSE'):
            prior_display = money(row['prior_debit'] - row['prior_credit'])
        else:
            prior_display = prior_net
        out.append({
            'code': code,
            'name': row['name'],
            'account_type': row['account_type'],
            'frs105_pl': row.get('frs105_pl'),
            'frs105_bs': row.get('frs105_bs'),
            'watched': bool(row.get('watched')),
            'opening_debit': money(row['opening_debit']),
            'opening_credit': money(row['opening_credit']),
            'movement_debit': move_dr,
            'movement_credit': move_cr,
            'ytd_debit': ytd_dr,
            'ytd_credit': ytd_cr,
            'ytd_net': net,
            'prior_net': prior_display,
            'prior_debit': money(row['prior_debit']),
            'prior_credit': money(row['prior_credit']),
        })
    return out


def _signed_for_type(account, amount_net):
    atype = account['account_type']
    net = money(amount_net)
    if atype in ('INCOME', 'EQUITY', 'CURRENT_LIABILITY', 'LONG_TERM_LIABILITY', 'PROVISION', 'FIXED_ASSET_CONTRA'):
        return money(-net)  # credit-positive displayed as positive
    return net


def trial_balance(accounts):
    rows = []
    total_dr = 0.0
    total_cr = 0.0
    for acc in accounts:
        dr = acc['ytd_debit']
        cr = acc['ytd_credit']
        if dr == 0 and cr == 0 and acc['opening_debit'] == 0 and acc['opening_credit'] == 0:
            continue
        # Present as a single side
        net = money(dr - cr)
        if net > 0:
            debit, credit = net, 0.0
        elif net < 0:
            debit, credit = 0.0, money(-net)
        else:
            continue
        total_dr = money(total_dr + debit)
        total_cr = money(total_cr + credit)
        rows.append({
            'code': acc['code'],
            'name': acc['name'],
            'account_type': acc['account_type'],
            'debit': debit,
            'credit': credit,
            'prior_net': acc['prior_net'],
        })
    return {
        'rows': rows,
        'total_debit': total_dr,
        'total_credit': total_cr,
        'difference': money(total_dr - total_cr),
        'agrees': money(total_dr - total_cr) == 0.0,
    }


def profit_and_loss(accounts):
    current = {key: 0.0 for key in 'ABCDEFG'}
    prior = {key: 0.0 for key in 'ABCDEFG'}
    for acc in accounts:
        letter = acc.get('frs105_pl')
        if not letter:
            continue
        if acc['account_type'] == 'INCOME':
            current[letter] = money(current[letter] + money(acc['ytd_credit'] - acc['ytd_debit']))
            prior[letter] = money(prior[letter] + money(acc['prior_credit'] - acc['prior_debit']))
        else:
            current[letter] = money(current[letter] + money(acc['ytd_debit'] - acc['ytd_credit']))
            prior[letter] = money(prior[letter] + money(acc['prior_debit'] - acc['prior_credit']))
    profit = money(current['A'] + current['B'] - current['C'] - current['D'] - current['E'] - current['F'] - current['G'])
    prior_profit = money(prior['A'] + prior['B'] - prior['C'] - prior['D'] - prior['E'] - prior['F'] - prior['G'])
    lines = []
    for key in 'ABCDEFG':
        lines.append({
            'code': key,
            'label': PL_LABELS[key],
            'current': current[key],
            'prior': prior[key],
            'is_total': False,
        })
    lines.append({
        'code': 'H',
        'label': PL_LABELS['H'],
        'current': profit,
        'prior': prior_profit,
        'is_total': True,
    })
    return {
        'lines': lines,
        'turnover': current['A'],
        'prior_turnover': prior['A'],
        'profit': profit,
        'prior_profit': prior_profit,
    }


def _bs_net(account, current=True):
    if current:
        return money(account['ytd_debit'] - account['ytd_credit'])
    # Opening balances are last year's closing statement of financial position.
    return money(account['opening_debit'] - account['opening_credit'])


def _bs_amount(accounts, letter, current=True):
    total = 0.0
    for acc in accounts:
        if acc.get('frs105_bs') != letter:
            continue
        net = _bs_net(acc, current)
        atype = acc['account_type']
        if atype in ('CURRENT_LIABILITY', 'LONG_TERM_LIABILITY', 'PROVISION', 'EQUITY'):
            total = money(total + money(-net))
        else:
            total = money(total + net)
    return total


def balance_sheet(accounts, profit, prior_profit):
    # Capital and reserves: share capital + opening P&L reserve + current profit − dividends
    def capital(current=True):
        total = 0.0
        for acc in accounts:
            if acc.get('frs105_bs') != 'K':
                continue
            if acc['code'] == '3100':
                # 3100 holds opening retained earnings (prior-year close).
                net = money(acc['opening_credit'] - acc['opening_debit'])
                total = money(total + net)
            elif acc['code'] == '3200':
                net = money(acc['ytd_debit'] - acc['ytd_credit']) if current else 0.0
                total = money(total - net)
            else:
                net = money(acc['ytd_credit'] - acc['ytd_debit']) if current else money(acc['opening_credit'] - acc['opening_debit'])
                total = money(total + net)
        extra = profit if current else 0.0
        return money(total + extra)

    def pack(current):
        a = _bs_amount(accounts, 'A', current)
        b = _bs_amount(accounts, 'B', current)
        c = _bs_amount(accounts, 'C', current)
        d = _bs_amount(accounts, 'D', current)
        e = _bs_amount(accounts, 'E', current)
        h = _bs_amount(accounts, 'H', current)
        i = _bs_amount(accounts, 'I', current)
        j = _bs_amount(accounts, 'J', current)
        f = money(c + d - e)
        g = money(a + b + f)
        k = capital(current)
        return {
            'A': a, 'B': b, 'C': c, 'D': d, 'E': e, 'F': f, 'G': g, 'H': h, 'I': i, 'J': j, 'K': k,
            'net_assets': money(g - h - i - j),
        }

    cur = pack(True)
    prv = pack(False)
    lines = []
    for key in 'ABCDEFGHIJK':
        lines.append({
            'code': key,
            'label': BS_LABELS[key],
            'current': cur[key],
            'prior': prv[key],
            'is_total': key in ('F', 'G', 'K'),
        })
    bs_total = money(cur['A'] + cur['B'] + cur['C'] + cur['D'])  # aggregate assets, CA 2006 s384A
    prior_bs_total = money(prv['A'] + prv['B'] + prv['C'] + prv['D'])
    return {
        'lines': lines,
        'current': cur,
        'prior': prv,
        'bs_total': bs_total,
        'prior_bs_total': prior_bs_total,
        'balances': money(cur['net_assets'] - cur['K']) == 0.0,
        'difference': money(cur['net_assets'] - cur['K']),
    }


def _journals(book_id):
    headers = query_db(
        "SELECT * FROM ledger_journals WHERE book_id = ? ORDER BY journal_date, id;",
        (book_id,),
    ) or []
    lines = query_db(
        """
        SELECT l.*, n.name AS account_name
        FROM ledger_journal_lines l
        JOIN ledger_journals j ON j.id = l.journal_id
        LEFT JOIN ledger_nominals n ON n.book_id = j.book_id AND n.code = l.nominal_code
        WHERE j.book_id = ?
        ORDER BY l.id;
        """,
        (book_id,),
    ) or []
    by_id = {}
    for header in headers:
        by_id[header['id']] = {
            'id': header['id'],
            'journal_date': header['journal_date'],
            'narration': header['narration'],
            'reference': header.get('reference') or '',
            'lines': [],
            'debit': 0.0,
            'credit': 0.0,
        }
    for line in lines:
        bucket = by_id.get(line['journal_id'])
        if not bucket:
            continue
        debit = money(line['debit'])
        credit = money(line['credit'])
        bucket['lines'].append({
            'code': line['nominal_code'],
            'name': line.get('account_name') or line['nominal_code'],
            'description': line.get('description') or '',
            'debit': debit,
            'credit': credit,
        })
        bucket['debit'] = money(bucket['debit'] + debit)
        bucket['credit'] = money(bucket['credit'] + credit)
    return list(by_id.values())


def _public_org(book):
    due, due_note = filing_deadline(
        _parse_date(book.get('period_end')),
        _parse_date(book.get('incorporation_date')),
        bool(book.get('is_first_accounts')),
        _parse_date(book.get('period_start')),
    )
    return {
        'registered_name': book.get('registered_name') or '',
        'company_number': book.get('company_number') or '',
        'registered_office': book.get('registered_office') or '',
        'directors': _parse_json(book.get('directors_json'), []),
        'incorporation_date': book.get('incorporation_date'),
        'period_start': book.get('period_start'),
        'period_end': book.get('period_end'),
        'prior_period_start': book.get('prior_period_start'),
        'prior_period_end': book.get('prior_period_end'),
        'employees': int(book.get('employees') or 0),
        'prior_employees': int(book.get('prior_employees') or 0),
        'is_first_accounts': bool(book.get('is_first_accounts')),
        'sample_loaded': bool(book.get('sample_loaded')),
        'exclusions': _parse_json(book.get('exclusions_json'), {}),
        'notes': _parse_json(book.get('notes_json'), {}),
        'filing_due': due,
        'filing_due_note': due_note,
        'accounting_standard': 'FRS 105 (September 2024 edition)',
    }


def _home(accounts, org, tb, pl, bs, size):
    bank_accounts = [a for a in accounts if a['account_type'] == 'BANK']
    bank_total = money(sum(a['ytd_net'] for a in bank_accounts))
    money_in = money(sum(a['movement_debit'] for a in bank_accounts))
    money_out = money(sum(a['movement_credit'] for a in bank_accounts))
    watchlist = [a for a in accounts if a['watched']]
    watch_items = []
    if not tb['agrees']:
        watch_items.append({
            'tone': 'danger',
            'title': 'Trial balance does not agree',
            'detail': f"Difference of £{abs(tb['difference']):,.2f}. Correct journals before you file.",
        })
    else:
        watch_items.append({
            'tone': 'ok',
            'title': 'Trial balance agrees',
            'detail': f"Debits and credits both total £{tb['total_debit']:,.2f}.",
        })
    if org.get('filing_due'):
        due = _parse_date(org['filing_due'])
        today = datetime.date.today()
        if due and due < today:
            tone, title = 'danger', 'Accounts filing is overdue'
        elif due and (due - today).days <= 60:
            tone, title = 'warn', 'Accounts filing due within 60 days'
        else:
            tone, title = 'ok', 'Accounts filing date'
        watch_items.append({
            'tone': tone,
            'title': title,
            'detail': f"Private company deadline {due.strftime('%-d %B %Y') if due else org['filing_due']} (9 months after year end, unless first long period).",
        })
    if size['qualifies']:
        watch_items.append({
            'tone': 'ok',
            'title': 'Micro-entity size test met',
            'detail': f"{size['hits']} of 3 conditions met this year under the {'post-6 April 2025' if size['from_6_apr_2025'] else 'pre-6 April 2025'} thresholds.",
        })
    else:
        watch_items.append({
            'tone': 'warn',
            'title': 'Does not currently qualify as a micro-entity',
            'detail': size['rule'],
        })
    flagged = [key for key, _ in EXCLUSION_FIELDS if org.get('exclusions', {}).get(key)]
    if flagged:
        watch_items.append({
            'tone': 'danger',
            'title': 'Excluded from the micro-entity regime',
            'detail': 'A flagged exclusion (for example PLC, charity, or group parent) blocks FRS 105 micro accounts.',
        })
    return {
        'bank_total': bank_total,
        'bank_accounts': [{'code': a['code'], 'name': a['name'], 'balance': a['ytd_net']} for a in bank_accounts if a['ytd_net'] or a['code'] == '1200'],
        'money_in': money_in,
        'money_out': money_out,
        'watchlist': [{
            'code': a['code'],
            'name': a['name'],
            'ytd': a['ytd_net'] if a['account_type'] not in ('INCOME', 'EQUITY', 'CURRENT_LIABILITY', 'LONG_TERM_LIABILITY', 'PROVISION', 'FIXED_ASSET_CONTRA') else money(a['ytd_credit'] - a['ytd_debit']),
            'prior': a['prior_net'],
        } for a in watchlist],
        'watch_items': watch_items,
    }


def _exclusion_block(org):
    flagged = [label for key, label in EXCLUSION_FIELDS if org.get('exclusions', {}).get(key)]
    return flagged


def year_end_pack(org, pl, bs, size):
    flagged = _exclusion_block(org)
    can_file_micro = size['qualifies'] and not flagged
    notes = org.get('notes') or {}
    employees = int(org.get('employees') or 0)
    director_names = ', '.join(d.get('name') for d in (org.get('directors') or []) if d.get('name')) or 'the director'
    statements = [
        {
            'id': 'micro',
            'title': 'Micro-entity statement',
            'body': 'These accounts have been prepared in accordance with the micro-entity provisions.',
            'law': 'CA 2006 s414(3); Companies House accounts guidance section 9.4. Shown prominently, above the director’s signature.',
        },
        {
            'id': 'audit_s477',
            'title': 'Audit exemption — section 477',
            'body': (
                f"For the year ending {_uk_long(org.get('period_end'))} the company was entitled to exemption from audit "
                "under section 477 of the Companies Act 2006 relating to small companies."
            ),
            'law': 'CA 2006 s477; Companies House guidance section 11.1.',
        },
        {
            'id': 'members_s476',
            'title': 'Members have not required an audit',
            'body': 'The members have not required the company to obtain an audit of its accounts for the year in question in accordance with section 476.',
            'law': 'CA 2006 s476.',
        },
        {
            'id': 'directors_s475',
            'title': 'Directors’ responsibilities',
            'body': (
                "The directors acknowledge their responsibilities for complying with the requirements of the Companies Act 2006 "
                "with respect to accounting records and the preparation of accounts."
            ),
            'law': 'CA 2006 s475.',
        },
    ]
    statutory_notes = [
        {
            'id': 'employees',
            'title': 'Average number of employees',
            'body': (
                f"The average number of employees during the year was {employees}."
                if employees
                else 'The average number of employees during the year was nil.'
            ),
            'law': 'CA 2006 s411.',
        },
        {
            'id': 'directors_advances',
            'title': 'Directors’ advances, credits and guarantees',
            'body': notes.get('directors_advances') or 'None during the year.',
            'law': 'CA 2006 s413.',
        },
        {
            'id': 'off_balance_sheet',
            'title': 'Off-balance sheet arrangements',
            'body': notes.get('off_balance_sheet') or 'None.',
            'law': 'FRS 105 section 6; CA 2006 s410A as applied to micro-entities.',
        },
        {
            'id': 'commitments',
            'title': 'Financial commitments, guarantees and contingencies',
            'body': notes.get('commitments') or 'None at the year end.',
            'law': 'CA 2006 s472(1A); FRS 105 section 6A.',
        },
    ]
    return {
        'can_file_micro': can_file_micro,
        'size': size,
        'exclusions': [{'key': key, 'label': label, 'flagged': bool(org.get('exclusions', {}).get(key))} for key, label in EXCLUSION_FIELDS],
        'exclusion_labels': flagged,
        'statements': statements,
        'notes': statutory_notes,
        'signed_by': director_names,
        'filing_note': (
            'A micro-entity may currently omit the profit and loss account and directors’ report from the Companies House copy. '
            'Delivery of a profit and loss account becomes mandatory for accounts delivered on or after 1 April 2028.'
        ),
        'ixbrl_gap': (
            'The draft iXBRL file uses FRC-style concept names and is labelled as a draft. '
            'It is not a validated Companies House software-filing package (Accounts TIS v5.9 from 1 April 2026).'
        ),
    }


def workspace(company_id, user_id=None):
    ensure_ledger_schema()
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    book = _book_for_company(company_id)
    crm = {
        'id': int(company['id']),
        'name': company.get('name') or '',
        'company_number': company.get('company_number') or '',
        'director': company.get('director') or '',
        'reg_office': company.get('reg_office') or '',
        'inc_date': company.get('inc_date'),
    }
    if not book:
        return {
            'status': 'success',
            'empty': True,
            'company': crm,
            'organisation': None,
            'accounts': [],
            'journals': [],
            'home': None,
            'profit_and_loss': None,
            'balance_sheet': None,
            'trial_balance': None,
            'year_end': None,
        }, None
    accounts = _nominal_balances(book)
    org = _public_org(book)
    tb = trial_balance(accounts)
    pl = profit_and_loss(accounts)
    bs = balance_sheet(accounts, pl['profit'], pl['prior_profit'])
    size = size_test(
        pl['turnover'],
        bs['bs_total'],
        org['employees'],
        _parse_date(org.get('period_start')),
        prior={
            'turnover': pl['prior_turnover'],
            'bs_total': bs['prior_bs_total'],
            'employees': org['prior_employees'],
            'period_start': org.get('prior_period_start'),
        },
        is_first=org['is_first_accounts'],
    )
    ye = year_end_pack(org, pl, bs, size)
    home = _home(accounts, org, tb, pl, bs, size)
    return {
        'status': 'success',
        'empty': False,
        'company': crm,
        'organisation': org,
        'accounts': accounts,
        'journals': _journals(book['id']),
        'home': home,
        'profit_and_loss': pl,
        'balance_sheet': bs,
        'trial_balance': tb,
        'year_end': ye,
        'coa_types': sorted({row[2] for row in UK_COA}),
    }, None


def _uk_long(value):
    day = _parse_date(value)
    if not day:
        return str(value or '')
    return f"{day.day} {day.strftime('%B %Y')}"


def _gbp(value):
    amount = money(value)
    sign = '−' if amount < 0 else ''
    return f"{sign}£{abs(amount):,.2f}"


def _esc(value):
    return html_lib.escape(str(value or ''))


def _pack_css():
    return """
    body { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; color: #0f172a; margin: 0; background: #fff; }
    .sheet { max-width: 720px; margin: 24px auto; padding: 28px 32px; }
    .brand { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #003971; font-weight: 700; }
    h1 { font-size: 22px; margin: 8px 0 4px; }
    h2 { font-size: 16px; margin: 28px 0 8px; border-bottom: 1px solid #d4af37; padding-bottom: 4px; }
    p, li, td, th { font-size: 14px; line-height: 1.45; }
    .muted { color: #64748b; font-size: 13px; }
    table { width: 100%; border-collapse: collapse; margin: 8px 0 16px; }
    th, td { padding: 6px 0; border-bottom: 1px solid #e2e8f0; }
    th { text-align: left; font-weight: 600; }
    .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .total td { font-weight: 700; border-top: 2px solid #0f172a; }
    .statement { margin: 12px 0; }
    .sign { margin-top: 28px; }
    .disclaimer { margin-top: 36px; font-size: 11px; color: #64748b; }
    .bar { height: 4px; background: linear-gradient(90deg, #003971, #d4af37); margin: 12px 0 20px; }
    """


def render_statutory_html(ws, filleted=False):
    org = ws['organisation']
    pl = ws['profit_and_loss']
    bs = ws['balance_sheet']
    ye = ws['year_end']
    title = 'Accounts for filing at Companies House' if filleted else 'Accounts for the members'
    rows_pl = ''
    if not filleted:
        for line in pl['lines']:
            klass = ' class="total"' if line['is_total'] else ''
            rows_pl += (
                f"<tr{klass}><td>{_esc(line['label'])}</td>"
                f"<td class='num'>{_gbp(line['current'])}</td>"
                f"<td class='num'>{_gbp(line['prior'])}</td></tr>"
            )
        pl_block = f"""
        <h2>Profit and loss account</h2>
        <p class="muted">FRS 105 Section C format (turnover to profit or loss) for the year ended {_esc(_uk_long(org['period_end']))}.</p>
        <table>
            <thead><tr><th></th><th class="num">{_esc(_uk_long(org['period_end']))}</th><th class="num">{_esc(_uk_long(org['prior_period_end']))}</th></tr></thead>
            <tbody>{rows_pl}</tbody>
        </table>
        """
    else:
        pl_block = f"""
        <h2>Profit and loss account</h2>
        <p>The company has taken advantage of the micro-entity option not to deliver a copy of the profit and loss account to the registrar. This option ends for accounts delivered on or after 1 April 2028.</p>
        """
    rows_bs = ''
    for line in bs['lines']:
        klass = ' class="total"' if line['is_total'] else ''
        rows_bs += (
            f"<tr{klass}><td>{_esc(line['code'])}. {_esc(line['label'])}</td>"
            f"<td class='num'>{_gbp(line['current'])}</td>"
            f"<td class='num'>{_gbp(line['prior'])}</td></tr>"
        )
    notes_html = ''.join(
        f"<div class='statement'><strong>{_esc(note['title'])}.</strong> {_esc(note['body'])}</div>"
        for note in ye['notes']
    )
    statements_html = ''.join(
        f"<div class='statement'><strong>{_esc(st['title'])}.</strong> {_esc(st['body'])}</div>"
        for st in ye['statements']
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(org['registered_name'])} — {_esc(title)}</title>
<style>{_pack_css()}</style>
</head>
<body>
<div class="sheet">
  <div class="brand">Brixen Consultants · FRS 105 micro-entity accounts</div>
  <div class="bar"></div>
  <h1>{_esc(org['registered_name'])}</h1>
  <p>Company number {_esc(org['company_number'])}<br>{_esc(org['registered_office'])}</p>
  <p><strong>{_esc(title)}</strong> for the year ended {_esc(_uk_long(org['period_end']))}</p>
  {pl_block}
  <h2>Balance sheet</h2>
  <p class="muted">Format 1 items A–K as at {_esc(_uk_long(org['period_end']))}.</p>
  <table>
    <thead><tr><th></th><th class="num">{_esc(_uk_long(org['period_end']))}</th><th class="num">{_esc(_uk_long(org['prior_period_end']))}</th></tr></thead>
    <tbody>{rows_bs}</tbody>
  </table>
  <h2>Notes to the accounts</h2>
  {notes_html}
  <h2>Statements</h2>
  {statements_html}
  <div class="sign">
    <p><strong>These accounts have been prepared in accordance with the micro-entity provisions.</strong></p>
    <p>Approved by the board and signed on its behalf by</p>
    <p style="margin-top:36px;border-top:1px solid #0f172a;width:280px;padding-top:8px;">{_esc(ye['signed_by'])}<br>Director<br>{_esc(_uk_long(org['period_end']))}</p>
  </div>
  <p class="disclaimer">This is a transactional compliance pack prepared from the CRM ledger. Brixen Consultants is not Companies House. Draft iXBRL is not a validated software-filing package.</p>
</div>
</body>
</html>
"""


def render_ixbrl_draft(ws):
    org = ws['organisation']
    pl = {line['code']: line['current'] for line in ws['profit_and_loss']['lines']}
    bs = {line['code']: line['current'] for line in ws['balance_sheet']['lines']}
    ye = ws['year_end']

    def fact(name, value, unit='GBP'):
        if unit == 'GBP':
            return f'<ix:nonFraction name="{name}" contextRef="FY" unitRef="GBP" decimals="2">{money(value):.2f}</ix:nonFraction>'
        return f'<ix:nonNumeric name="{name}" contextRef="FY">{_esc(value)}</ix:nonNumeric>'

    html_doc = render_statutory_html(ws, filleted=False)
    facts = f"""
<!-- DRAFT iXBRL — not a Companies House TIS v5.9 package. Do not submit. -->
<div style="display:none">
{fact('uk-bus:EntityCurrentLegalOrRegisteredName', org['registered_name'], 'text')}
{fact('uk-bus:UKCompaniesHouseRegisteredNumber', org['company_number'], 'text')}
{fact('core:Turnover', pl.get('A'))}
{fact('core:OtherOperatingIncome', pl.get('B'))}
{fact('core:RawMaterialsAndConsumablesUsed', pl.get('C'))}
{fact('core:StaffCostsEmployeeBenefitsExpense', pl.get('D'))}
{fact('core:DepreciationAmortisationImpairment', pl.get('E'))}
{fact('core:OtherOperatingExpenses', pl.get('F'))}
{fact('core:IncomeTaxExpenseContinuingOperations', pl.get('G'))}
{fact('core:ProfitLoss', pl.get('H'))}
{fact('core:FixedAssets', bs.get('B'))}
{fact('core:CurrentAssets', bs.get('C'))}
{fact('core:CreditorsDueWithinOneYear', bs.get('E'))}
{fact('core:NetCurrentAssetsLiabilities', bs.get('F'))}
{fact('core:Equity', bs.get('K'))}
</div>
"""
    return html_doc.replace('</body>', facts + '</body>').replace(
        '<html lang="en">',
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" lang="en">',
    )


def export_pack(company_id, kind, user_id=None):
    ws, error = workspace(company_id, user_id)
    if error:
        return None, error, None, None
    if ws.get('empty'):
        return None, 'Load or start an accounts file before exporting.', None, None
    org = ws['organisation']
    slug = re.sub(r'[^A-Za-z0-9]+', '-', org['registered_name']).strip('-') or 'accounts'
    year = str(org.get('period_end') or '')[:4]
    if kind == 'members':
        body = render_statutory_html(ws, filleted=False)
        return body.encode('utf-8'), None, 'text/html; charset=utf-8', f'{slug}-{year}-members.html'
    if kind == 'filleted':
        body = render_statutory_html(ws, filleted=True)
        return body.encode('utf-8'), None, 'text/html; charset=utf-8', f'{slug}-{year}-companies-house.html'
    if kind == 'ixbrl':
        body = render_ixbrl_draft(ws)
        return body.encode('utf-8'), None, 'application/xhtml+xml; charset=utf-8', f'{slug}-{year}-draft.xhtml'
    return None, 'Unknown export type', None, None


def _int_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def route(path, method, user, body=None, query=None):
    """Return ('json', status, dict) or ('file', status, bytes, content_type, filename) or None."""
    if not path.startswith('/api/admin/accounts-file') and not path.startswith('/api/client/accounts-file'):
        return None
    body = body or {}
    query = query or {}
    is_admin = path.startswith('/api/admin/accounts-file')
    if not user:
        return ('json', '401 Unauthorized', {'status': 'error', 'message': 'Not authenticated'})
    if is_admin:
        if user.get('role') not in ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF'):
            return ('json', '403 Forbidden', {'status': 'error', 'message': 'Insufficient permissions'})
        owner_filter = None
    else:
        if user.get('role') != 'CLIENT':
            return ('json', '403 Forbidden', {'status': 'error', 'message': 'Client accounts only'})
        owner_filter = user['id']

    parts = [p for p in path.split('/') if p]
    # api, admin|client, accounts-file [, company_id, action...]
    if len(parts) == 3:
        if method != 'GET':
            return ('json', '405 Method Not Allowed', {'status': 'error', 'message': 'Method not allowed'})
        return ('json', '200 OK', {'status': 'success', 'companies': list_companies(owner_filter)})

    company_id = _int_id(parts[3] if len(parts) > 3 else None)
    if not company_id:
        return ('json', '404 Not Found', {'status': 'error', 'message': 'Company not found'})

    if len(parts) == 4 and method == 'GET':
        ws, error = workspace(company_id, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)

    action = parts[4] if len(parts) > 4 else ''
    if action == 'sample' and method == 'POST':
        ws, error = load_sample_books(company_id, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action == 'start' and method == 'POST':
        ws, error = start_blank_books(company_id, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action == 'organisation' and method == 'PUT':
        ws, error = save_organisation(company_id, body, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action == 'journals' and method == 'POST':
        ws, error = post_journal(company_id, body, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action == 'watch' and method == 'POST':
        code = body.get('code') or (parts[5] if len(parts) > 5 else '')
        ws, error = toggle_watch(company_id, code, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action == 'export' and method == 'GET':
        kind = (parts[5] if len(parts) > 5 else '') or (query.get('kind') or ['members'])[0]
        payload, error, content_type, filename = export_pack(company_id, kind, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('file', '200 OK', payload, content_type, filename)

    return ('json', '404 Not Found', {'status': 'error', 'message': 'Accounts file route not found'})
