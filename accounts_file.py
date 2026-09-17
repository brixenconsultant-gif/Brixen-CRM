"""UK books for a director who is not an accountant.

Three simple paths: traded (micro-entity from a bank statement), dormant
(slept all year — no statement), then Companies House and a plain-English
HMRC Corporation Tax helper. Live gateways when credentials exist; otherwise
a sandbox receipt plus a download / GOV.UK upload.
"""
from __future__ import annotations

import calendar
import csv
import datetime
import hashlib
import html as html_lib
import io
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import sqlite3

import db as db_mod
from db import execute_db, get_db, query_db


def _release(conn):
    """Do not close the live thread-local connection. Local tests open a fresh one."""
    if conn is None or hasattr(db_mod, 'close_db'):
        return
    try:
        conn.close()
    except Exception:
        pass


def _db():
    conn = get_db()
    try:
        conn.execute('SELECT 1')
        return conn
    except sqlite3.ProgrammingError:
        closer = getattr(db_mod, 'close_db', None)
        if callable(closer):
            closer()
        return get_db()

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
    filing_kind TEXT NOT NULL DEFAULT '',
    hmrc_utr TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS ledger_statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    filename TEXT,
    source TEXT NOT NULL DEFAULT 'csv',
    opening_balance REAL,
    closing_balance REAL,
    row_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES ledger_books(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ledger_statements_book ON ledger_statements(book_id);

CREATE TABLE IF NOT EXISTS ledger_bank_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    statement_id INTEGER,
    txn_date DATE NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    balance REAL,
    category_code TEXT,
    category_label TEXT,
    confidence TEXT NOT NULL DEFAULT 'low',
    needs_review INTEGER NOT NULL DEFAULT 1,
    posted_journal_id INTEGER,
    source_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, source_hash),
    FOREIGN KEY (book_id) REFERENCES ledger_books(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ledger_bank_lines_book ON ledger_bank_lines(book_id, needs_review);

CREATE TABLE IF NOT EXISTS ledger_ch_filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    mode TEXT NOT NULL DEFAULT 'sandbox',
    status TEXT NOT NULL DEFAULT 'accepted',
    receipt TEXT,
    message TEXT,
    pack_kind TEXT DEFAULT 'filleted',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES ledger_books(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ledger_ch_filings_book ON ledger_ch_filings(book_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ledger_hmrc_filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    mode TEXT NOT NULL DEFAULT 'sandbox',
    status TEXT NOT NULL DEFAULT 'recorded',
    receipt TEXT,
    message TEXT,
    tax_estimate REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES ledger_books(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ledger_hmrc_filings_book ON ledger_hmrc_filings(book_id, created_at DESC);
"""

MICRO_FROM = datetime.date(2025, 4, 6)
THRESHOLDS_NEW = (1_000_000.0, 500_000.0, 10)
THRESHOLDS_OLD = (632_000.0, 316_000.0, 10)
CT_LOWER = 50_000.0
CT_UPPER = 250_000.0
CT_SMALL_RATE = 0.19
CT_MAIN_RATE = 0.25
CT_MARGINAL_FRACTION = 3.0 / 200.0
HMRC_CT_URL = 'https://www.gov.uk/file-your-company-accounts-and-tax-return'
HMRC_DORMANT_URL = 'https://www.gov.uk/dormant-company/dormant-for-corporation-tax'

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

SAMPLE_STATEMENT_CSV = """Date,Description,Money in,Money out,Balance
01/04/2025,Opening balance,,,12000.00
05/04/2025,Stripe Payout,6200.00,,18200.00
12/04/2025,Office rent April,,600.00,17600.00
28/04/2025,Staff wages April,,2400.00,15200.00
18/05/2025,ACME LTD INV-104,8400.00,,23600.00
12/05/2025,Office rent May,,600.00,23000.00
22/05/2025,Hiscox insurance,,480.00,22520.00
20/06/2025,Screwfix materials,,1800.00,20720.00
28/07/2025,Staff wages July,,2400.00,18320.00
03/09/2025,Stripe Payout,5000.00,,23320.00
04/10/2025,Shell fuel,,1200.00,22120.00
28/10/2025,Staff wages October,,2400.00,19720.00
15/11/2025,Starling monthly fee,,192.00,19528.00
14/01/2026,Invoice 221 Thorn,5000.00,,24528.00
28/01/2026,Staff wages January,,2400.00,22128.00
12/03/2026,Office rent remainder,,6000.00,16128.00
15/03/2026,Companies House / solicitor,,320.00,15808.00
"""

DIRECTOR_CATEGORIES = (
    ('4000', 'Sales'),
    ('4900', 'Other money in'),
    ('5000', 'Materials and stock'),
    ('7000', 'Wages'),
    ('7002', 'Director pay'),
    ('7100', 'Rent'),
    ('7200', 'Light and heat'),
    ('7300', 'Vehicle and fuel'),
    ('7400', 'Travel'),
    ('7500', 'Stationery'),
    ('7600', 'Professional fees'),
    ('7601', 'Accountancy'),
    ('7700', 'Bank charges'),
    ('7800', 'Insurance'),
    ('8000', 'Interest'),
    ('8100', 'Tax'),
    ('2200', 'VAT paid'),
    ('2220', 'Corporation tax paid'),
    ('3200', 'Dividends'),
)

CATEGORY_RULES = (
    (r'\b(salary|wages|payroll|paye|staff pay)\b', '7000', 'Wages'),
    (r'\b(director(?:\'s)? pay|directors? remuneration)\b', '7002', 'Director pay'),
    (r'\bdividends?\b', '3200', 'Dividends'),
    (r'\b(rent|landlord)\b', '7100', 'Rent'),
    (r'\b(british gas|edf|e\.?on\b|octopus energy|sse\b|thames water|virgin media|vodafone|\bbt\b|light and heat)\b', '7200', 'Light and heat'),
    (r'\b(shell|bp\b|tesco petrol|fuel|petrol|parking|halfords|mot\b)\b', '7300', 'Vehicle and fuel'),
    (r'\b(trainline|uber|tfl\b|easyjet|ryanair|booking\.com)\b', '7400', 'Travel'),
    (r'\b(insurance|aviva|axa|hiscox)\b', '7800', 'Insurance'),
    (r'\b(companies house|solicitor|lawyer|legal|accountant|accountancy)\b', '7600', 'Professional fees'),
    (r'\b(bank charge|monthly (?:account )?fee|starling.*fee|tide fee|monzo plus)\b', '7700', 'Bank charges'),
    (r'\b(corporation tax|hmrc ct)\b', '2220', 'Corporation tax paid'),
    (r'\b(hmrc vat|vat payment)\b', '2200', 'VAT paid'),
    (r'\bhmrc\b', '8100', 'Tax'),
    (r'\b(screwfix|toolstation|materials|stock)\b', '5000', 'Materials and stock'),
    (r'\b(tesco|sainsbury|amazon|ebay)\b', '5000', 'Materials and stock'),
    (r'\b(stripe|sumup|square|shopify|invoice|payout|client payment|sales)\b', '4000', 'Sales'),
)

WEBFILING_URL = 'https://ewf.companieshouse.gov.uk/'
CH_GATEWAY_DEFAULT = 'https://xmlgw.companieshouse.gov.uk/v1-0/xmlgw/Gateway'

_DATE_HEADERS = {'date', 'transaction date', 'posted', 'completed date', 'date started', 'txn date', 'value date'}
_DESC_HEADERS = {'description', 'narrative', 'details', 'counter party', 'counterparty', 'name', 'transaction description', 'reference'}
_AMOUNT_HEADERS = {'amount', 'value', 'transaction amount'}
_IN_HEADERS = {'money in', 'paid in', 'credit', 'inflow', 'credit amount', 'amount in', 'paid in (gbp)'}
_OUT_HEADERS = {'money out', 'paid out', 'debit', 'outflow', 'debit amount', 'amount out', 'paid out (gbp)'}
_BALANCE_HEADERS = {'balance', 'running balance', 'account balance'}



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


def corporation_tax_estimate(profit):
    """FY 2023+ UK Corporation Tax for a single company with no associated companies.

    Director English: 19% up to £50,000, 25% from £250,000, a blend in between.
    """
    taxable = money(max(0.0, money(profit)))
    if taxable <= 0:
        return {
            'profit': 0.0,
            'tax': 0.0,
            'band': 'none',
            'rate_pct': 0.0,
            'plain': 'No Corporation Tax to pay on these books — there is no taxable profit.',
        }
    if taxable <= CT_LOWER:
        tax = money(taxable * CT_SMALL_RATE)
        return {
            'profit': taxable,
            'tax': tax,
            'band': 'small',
            'rate_pct': 19.0,
            'plain': 'Small-profits rate: 19p in the pound, because profit is £50,000 or less.',
        }
    if taxable >= CT_UPPER:
        tax = money(taxable * CT_MAIN_RATE)
        return {
            'profit': taxable,
            'tax': tax,
            'band': 'main',
            'rate_pct': 25.0,
            'plain': 'Main rate: 25p in the pound, because profit is £250,000 or more.',
        }
    tax = money(taxable * CT_MAIN_RATE - (CT_UPPER - taxable) * CT_MARGINAL_FRACTION)
    return {
        'profit': taxable,
        'tax': tax,
        'band': 'marginal',
        'rate_pct': money((tax / taxable) * 100) if taxable else 0.0,
        'plain': 'A blend between 19% and 25%, because profit sits between £50,000 and £250,000.',
    }


def ct_dates(period_end):
    end = _parse_date(period_end)
    if not end:
        return None, None
    payment_due = add_months(end, 9) + datetime.timedelta(days=1)
    return_due = add_months(end, 12)
    return payment_due.isoformat(), return_due.isoformat()


def _book_kind(book):
    return str((book or {}).get('filing_kind') or '').strip().lower()


def ensure_ledger_schema():
    conn = _db()
    conn.executescript(LEDGER_SCHEMA_SQL)
    cols = {row[1] for row in conn.execute('PRAGMA table_info(ledger_books)').fetchall()}
    if 'filing_kind' not in cols:
        conn.execute("ALTER TABLE ledger_books ADD COLUMN filing_kind TEXT NOT NULL DEFAULT '';")
    if 'hmrc_utr' not in cols:
        conn.execute("ALTER TABLE ledger_books ADD COLUMN hmrc_utr TEXT NOT NULL DEFAULT '';")
    conn.commit()
    _release(conn)


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
        for b in (query_db("SELECT company_id, registered_name, period_end, sample_loaded, filing_kind FROM ledger_books;") or [])
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
            'filing_kind': (book or {}).get('filing_kind') or '',
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
    conn = _db()
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
    _release(conn)


def _clear_books(book_id):
    conn = _db()
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
    conn.execute("DELETE FROM ledger_bank_lines WHERE book_id = ?;", (book_id,))
    conn.execute("DELETE FROM ledger_statements WHERE book_id = ?;", (book_id,))
    conn.execute("DELETE FROM ledger_ch_filings WHERE book_id = ?;", (book_id,))
    try:
        conn.execute("DELETE FROM ledger_hmrc_filings WHERE book_id = ?;", (book_id,))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    _release(conn)


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
    conn = _db()
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
    _release(conn)
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
    if existing:
        _clear_books(existing['id'])
        execute_db(
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


def choose_path(company_id, data, user_id=None):
    """Director picks traded (micro) or dormant. Does not require a bank statement."""
    kind = str((data or {}).get('kind') or (data or {}).get('filing_kind') or '').strip().lower()
    if kind not in ('traded', 'dormant'):
        return None, 'Choose whether the company traded this year, or slept with no sales.'
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    book = _book_for_company(company_id)
    if not book:
        ws, error = start_blank_books(company_id, user_id)
        if error:
            return None, error
        book = _book_for_company(company_id)
    execute_db(
        "UPDATE ledger_books SET filing_kind=?, updated_at=CURRENT_TIMESTAMP WHERE id=?;",
        (kind, book['id']),
    )
    return workspace(company_id, user_id)


def reset_books(company_id, user_id=None):
    """Wipe the accounts file for this company. Documents, orders and invoices stay."""
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    ensure_ledger_schema()
    book = _book_for_company(company_id)
    if book:
        _clear_books(book['id'])
        execute_db("DELETE FROM ledger_books WHERE id = ?;", (book['id'],))
    return workspace(company_id, user_id)


def load_sample_books(company_id, user_id=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    ensure_ledger_schema()
    existing = _book_for_company(company_id)
    directors_json = json.dumps(SAMPLE_ORG['directors'])
    notes_json = json.dumps(SAMPLE_ORG['notes'])
    exclusions_json = json.dumps({key: False for key, _ in EXCLUSION_FIELDS})
    if existing:
        _clear_books(existing['id'])
        execute_db(
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
    execute_db("UPDATE ledger_books SET filing_kind='traded', updated_at=CURRENT_TIMESTAMP WHERE id=?;", (book_id,))
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
            prior_period_end=?, employees=?, prior_employees=?,             is_first_accounts=?,
            exclusions_json=?, notes_json=?, hmrc_utr=?, updated_at=CURRENT_TIMESTAMP
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
            re.sub(r'\s+', '', str(data.get('hmrc_utr') if data.get('hmrc_utr') is not None else book.get('hmrc_utr') or ''))[:15],
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
        'filing_kind': _book_kind(book),
        'hmrc_utr': str(book.get('hmrc_utr') or '').strip(),
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
            'detail': f"Private company deadline {_uk_long(due) if due else org['filing_due']} (9 months after year end, unless first long period).",
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
    dormant = str(org.get('filing_kind') or '') == 'dormant'
    can_file_micro = (size['qualifies'] and not flagged) or dormant
    notes = org.get('notes') or {}
    employees = int(org.get('employees') or 0)
    director_names = ', '.join(d.get('name') for d in (org.get('directors') or []) if d.get('name')) or 'the director'
    if dormant:
        statements = [
            {
                'id': 'dormant_year',
                'title': 'The company slept all year',
                'body': (
                    f"The company was dormant throughout the year ended {_uk_long(org.get('period_end'))}. "
                    'There were no significant accounting transactions (Companies Act 2006 section 1169).'
                ),
                'law': 'CA 2006 s1169.',
            },
            {
                'id': 'audit_s480',
                'title': 'Audit exemption — dormant company',
                'body': (
                    f"For the year ending {_uk_long(org.get('period_end'))} the company was entitled to exemption "
                    'from audit under section 480 of the Companies Act 2006 relating to dormant companies.'
                ),
                'law': 'CA 2006 s480.',
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
                    'The directors acknowledge their responsibilities for complying with the requirements of the Companies Act 2006 '
                    'with respect to accounting records and the preparation of accounts.'
                ),
                'law': 'CA 2006 s475.',
            },
        ]
        filing_note = (
            'Dormant accounts are a short balance sheet plus these statements. '
            'No bank statement is needed. File at Companies House, then tell HMRC the company slept.'
        )
    else:
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
        filing_note = (
            'A micro-entity may currently omit the profit and loss account and directors’ report from the Companies House copy. '
            'Delivery of a profit and loss account becomes mandatory for accounts delivered on or after 1 April 2028.'
        )
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
        'can_file_dormant': dormant,
        'filing_kind': 'dormant' if dormant else 'traded',
        'size': size,
        'exclusions': [{'key': key, 'label': label, 'flagged': bool(org.get('exclusions', {}).get(key))} for key, label in EXCLUSION_FIELDS],
        'exclusion_labels': flagged,
        'statements': statements,
        'notes': statutory_notes,
        'signed_by': director_names,
        'filing_note': filing_note,
        'ixbrl_gap': (
            'The draft iXBRL file uses FRC-style concept names and is labelled as a draft. '
            'It is not a validated Companies House software-filing package (Accounts TIS v5.9 from 1 April 2026).'
        ),
    }


def director_categories():
    return [{'code': code, 'label': label} for code, label in DIRECTOR_CATEGORIES]


def _norm_header(value):
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())


def _parse_amount(value):
    text = str(value or '').strip()
    if not text or text in {'.', '-'}:
        return 0.0
    negative = text.startswith('(') and text.endswith(')')
    text = text.replace('£', '').replace(',', '').replace('(', '').replace(')', '').strip().rstrip('.')
    if text.startswith('+'):
        text = text[1:]
    if text.endswith('-') and text[:-1].replace('.', '', 1).isdigit():
        negative = True
        text = text[:-1]
    if text.startswith('-'):
        negative = True
        text = text[1:]
    try:
        amount = money(text)
    except (TypeError, ValueError):
        return 0.0
    return money(-amount if negative else amount)


def parse_uk_date(value, default_year=None):
    text = str(value or '').strip().rstrip('.')
    if not text:
        return None
    # UK sort codes look like 23-08-01. They are not dates.
    if re.fullmatch(r'\d{2}-\d{2}-\d{2}', text.split()[0]):
        return None
    iso = _parse_date(text[:10])
    if iso and (len(text) < 11 or (len(text) >= 10 and text[4] == '-' and text[0:4].isdigit() and len(text[0:4]) == 4)):
        return iso
    for fmt in (
        '%d/%m/%Y', '%d/%m/%y', '%d-%m-%Y',
        '%d %b %Y', '%d %B %Y', '%d %b %y', '%d %B %y',
        '%d-%b-%Y', '%d-%b-%y', '%Y-%m-%d',
    ):
        try:
            return datetime.datetime.strptime(text[:24].strip(), fmt).date()
        except ValueError:
            continue
    match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', text)
    if match:
        token = match.group(0)
        if '-' in token and len(match.group(3)) == 2:
            match = None
        else:
            day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            if year < 100:
                year += 2000
            try:
                return datetime.date(year, month, day)
            except ValueError:
                return None
    match = re.search(r'(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{2,4})', text)
    if match:
        chunk = f"{match.group(1)} {match.group(2)} {match.group(3)}"
        for fmt in ('%d %b %Y', '%d %B %Y', '%d %b %y', '%d %B %y'):
            try:
                return datetime.datetime.strptime(chunk, fmt).date()
            except ValueError:
                continue
    if default_year:
        match = re.match(r'(\d{1,2})\s+([A-Za-z]{3,9})\b', text)
        if match:
            chunk = f"{match.group(1)} {match.group(2)} {default_year}"
            for fmt in ('%d %b %Y', '%d %B %Y', '%d %b %y', '%d %B %y'):
                try:
                    return datetime.datetime.strptime(chunk, fmt).date()
                except ValueError:
                    continue
    return None


def categorise_line(description, amount):
    text = str(description or '')
    lowered = text.lower()
    if re.search(r'\bopening balance\b', lowered):
        return None
    for pattern, code, label in CATEGORY_RULES:
        if re.search(pattern, lowered):
            return {'code': code, 'label': label, 'confidence': 'high', 'needs_review': 0}
    if money(amount) > 0:
        return {'code': '4000', 'label': 'Sales', 'confidence': 'low', 'needs_review': 1}
    return {'code': '7600', 'label': 'Professional fees', 'confidence': 'low', 'needs_review': 1}


def _pick_column(headers, names):
    for index, header in enumerate(headers):
        if header in names:
            return index
    return None


def parse_statement_csv(text):
    raw = (text or '').replace('\ufeff', '').strip()
    if not raw:
        return None, 'Paste or upload a bank statement first.'
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',\t;|')
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(raw), dialect)
    rows = [row for row in reader if any(str(cell).strip() for cell in row)]
    if not rows:
        return None, 'That file does not contain any statement rows.'
    headers = [_norm_header(cell) for cell in rows[0]]
    has_header = bool(set(headers) & (_DATE_HEADERS | _DESC_HEADERS | _AMOUNT_HEADERS | _IN_HEADERS | _OUT_HEADERS))
    body = rows[1:] if has_header else rows
    if not has_header:
        headers = ['date', 'description', 'amount', 'balance'][:len(rows[0])]
        body = rows
        if len(rows[0]) >= 3 and parse_uk_date(rows[0][0]) is None:
            headers = [_norm_header(cell) for cell in rows[0]]
            body = rows[1:]
            has_header = True
    date_i = _pick_column(headers, _DATE_HEADERS)
    desc_i = _pick_column(headers, _DESC_HEADERS)
    amount_i = _pick_column(headers, _AMOUNT_HEADERS)
    in_i = _pick_column(headers, _IN_HEADERS)
    out_i = _pick_column(headers, _OUT_HEADERS)
    bal_i = _pick_column(headers, _BALANCE_HEADERS)
    if date_i is None:
        date_i = 0
    if desc_i is None:
        desc_i = 1 if len(headers) > 1 else 0
    parsed = []
    opening = None
    for row in body:
        if date_i >= len(row):
            continue
        day = parse_uk_date(row[date_i])
        description = str(row[desc_i] if desc_i is not None and desc_i < len(row) else '').strip()
        if not day:
            continue
        if re.search(r'\bopening balance\b', description.lower()):
            if bal_i is not None and bal_i < len(row):
                opening = _parse_amount(row[bal_i])
            continue
        amount = 0.0
        if in_i is not None or out_i is not None:
            incoming = _parse_amount(row[in_i]) if in_i is not None and in_i < len(row) else 0.0
            outgoing = _parse_amount(row[out_i]) if out_i is not None and out_i < len(row) else 0.0
            amount = money(abs(incoming) - abs(outgoing))
        elif amount_i is not None and amount_i < len(row):
            amount = _parse_amount(row[amount_i])
        elif len(row) > 2:
            amount = _parse_amount(row[2])
        if amount == 0:
            continue
        balance = _parse_amount(row[bal_i]) if bal_i is not None and bal_i < len(row) else None
        parsed.append({
            'txn_date': day.isoformat(),
            'description': description or 'Bank transaction',
            'amount': amount,
            'balance': balance,
        })
    if not parsed:
        return None, 'No dated bank transactions were found. Use a CSV with date, description and amount (or money in / money out).'
    if opening is None and parsed[0].get('balance') is not None:
        opening = money(parsed[0]['balance'] - parsed[0]['amount'])
    closing = parsed[-1].get('balance')
    if closing is None and opening is not None:
        closing = money(opening + sum(row['amount'] for row in parsed))
    return {
        'lines': parsed,
        'opening_balance': opening,
        'closing_balance': closing,
    }, None


_MONEY_RE = re.compile(r'(?<![\w])[+-]?£?[\d,]+\.\d{2}(?![\d])')
_DATE_TOKEN_RE = re.compile(
    r'\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b'
)
_SKIP_STATEMENT_LINE = re.compile(
    r'^(sort code|account number|iban|bic|swift/?bic|column|blank\.?|your transactions|your account|type\.?|'
    r'page \d+|logo,|if you think something|prudential|registered office|document requested by|'
    r'description \(gbp\)|date description|money in|money out|account holder|account name|'
    r'uk sort code|sort code)$',
    re.I,
)
_WISE_AMOUNT_FIRST = re.compile(r'^(sent money|received money|card transaction|card cash)\b', re.I)
_IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.heic', '.webp', '.gif', '.tif', '.tiff')
_AUTO_IMPORT_MAX_BYTES = 1_600_000


def _line_money_values(line):
    return [_parse_amount(m.group(0)) for m in _MONEY_RE.finditer(line or '')]


def _sign_from_words(text, amount):
    amount = money(amount)
    if amount < 0:
        return amount
    lowered = str(text or '').lower()
    if re.search(r'\b(received|incoming|money in|deposit|faster payments in|fpi|credit)\b', lowered):
        return abs(amount)
    if re.search(r'\b(sent money|outgoing|money out|card transaction|paid|payment|direct debit|standing order|\bdeb\b|ddt|s/o)\b', lowered):
        return -abs(amount)
    return amount


def _pack_parsed_lines(parsed, opening=None):
    if not parsed:
        return None, 'No dated bank transactions were found. Export CSV from your bank, or paste the rows.'
    if len(parsed) >= 2:
        first = _parse_date(parsed[0]['txn_date'])
        last = _parse_date(parsed[-1]['txn_date'])
        if first and last and first > last:
            parsed = list(reversed(parsed))
    if opening is None and parsed[0].get('balance') is not None:
        opening = money(parsed[0]['balance'] - parsed[0]['amount'])
    closing = parsed[-1].get('balance')
    if closing is None and opening is not None:
        closing = money(opening + sum(row['amount'] for row in parsed))
    return {
        'lines': parsed,
        'opening_balance': opening,
        'closing_balance': closing,
    }, None


def _parse_labeled_bank_lines(raw_lines):
    labels = {
        'date': re.compile(r'^date\.?$', re.I),
        'description': re.compile(r'^description\.?$', re.I),
        'type': re.compile(r'^type\.?$', re.I),
        'money_in': re.compile(r'^money in', re.I),
        'money_out': re.compile(r'^money out', re.I),
        'balance': re.compile(r'^balance', re.I),
    }
    fields = {}
    rows = []
    opening = None
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        if re.match(r'^column\.?$', line, re.I):
            i += 1
            continue
        match_key = None
        for key, pattern in labels.items():
            if pattern.search(line) and len(line) < 48:
                match_key = key
                break
        if match_key:
            value = ''
            if i + 1 < len(raw_lines):
                nxt = raw_lines[i + 1]
                if not any(pat.search(nxt) and len(nxt) < 48 for pat in labels.values()) and not re.match(r'^column\.?$', nxt, re.I):
                    value = nxt
                    i += 1
            if re.match(r'^blank\.?$', value, re.I):
                value = ''
            fields[match_key] = value
            if match_key == 'balance' and fields.get('date'):
                day = parse_uk_date(fields.get('date'))
                incoming = abs(_parse_amount(fields.get('money_in')))
                outgoing = abs(_parse_amount(fields.get('money_out')))
                amount = money(incoming - outgoing)
                if day and amount != 0:
                    rows.append({
                        'txn_date': day.isoformat(),
                        'description': (fields.get('description') or 'Bank transaction').strip(' .'),
                        'amount': amount,
                        'balance': _parse_amount(fields.get('balance')) or None,
                    })
                fields = {}
        else:
            if re.search(r'balance on \d{1,2}', line, re.I):
                amounts = _line_money_values(line)
                if amounts and opening is None:
                    opening = amounts[-1]
        i += 1
    return _pack_parsed_lines(rows, opening) if rows else (None, None)


def _line_has_words(text):
    return bool(re.search(r'[A-Za-z]{3,}', text or ''))


def _strip_leading_date(line):
    text = str(line or '').strip()
    text = re.sub(r'^\d{1,2}/\d{1,2}/\d{2,4}\s*', '', text)
    text = re.sub(r'^\d{4}-\d{2}-\d{2}\s*', '', text)
    text = re.sub(r'^\d{1,2}\s+[A-Za-z]{3,9}(?:\s+\d{2,4})?\s*', '', text)
    return text.strip(' |-')


def _append_bank_row(parsed, day, description, amounts):
    if not day or not amounts:
        return
    amount = _sign_from_words(description, amounts[0])
    if amount == 0:
        return
    balance = amounts[-1] if len(amounts) > 1 else None
    parsed.append({
        'txn_date': day.isoformat() if hasattr(day, 'isoformat') else str(day),
        'description': (description or 'Bank transaction')[:240],
        'amount': money(amount),
        'balance': money(balance) if balance is not None else None,
    })


def parse_bank_text(text):
    raw_lines = [re.sub(r'\s+', ' ', ln).strip() for ln in str(text or '').splitlines()]
    raw_lines = [ln for ln in raw_lines if ln]
    labeled, labeled_error = _parse_labeled_bank_lines(raw_lines)
    if labeled and len(labeled.get('lines') or []) >= 2:
        return labeled, None
    parsed = []
    opening = None
    pending_date = None
    pending_desc = ''
    pending_amounts = None
    implied_year = None
    for line in raw_lines:
        lowered = line.lower()
        if _SKIP_STATEMENT_LINE.match(line) or lowered.startswith('page ') or re.match(r'^\d+\s*/\s*\d+$', line):
            continue
        if re.fullmatch(r'\d{2}-\d{2}-\d{2}', line):
            continue
        if re.search(r'^date\b.*\b(description|amount|balance|paid)\b', lowered) and not _line_money_values(line):
            continue
        if 'opening balance' in lowered or 'brought forward' in lowered or re.search(r'balance on \d{1,2}', lowered):
            amounts = _line_money_values(line)
            if amounts and opening is None:
                opening = amounts[-1]
            continue
        if re.search(r'\bgbp on\b', lowered) and _line_money_values(line):
            continue
        amounts = _line_money_values(line)
        date_tokens = _DATE_TOKEN_RE.findall(line)
        if len(date_tokens) >= 2 and (not amounts or not _line_has_words(_MONEY_RE.sub('', line))):
            for token in date_tokens:
                day = parse_uk_date(token)
                if day:
                    implied_year = day.year
            continue
        date_chunk = line.split('|')[0].strip().rstrip('.')
        day = parse_uk_date(date_chunk, implied_year) or parse_uk_date(line[:32], implied_year)
        if day:
            implied_year = day.year
        if day and not amounts:
            if pending_amounts:
                held_amounts, held_desc = pending_amounts
                _append_bank_row(parsed, day, held_desc, held_amounts)
                pending_amounts = None
                pending_date = None
                pending_desc = ''
                continue
            pending_date = day
            pending_desc = _strip_leading_date(line)
            continue
        if amounts:
            desc = _MONEY_RE.sub('', line).strip(' |-')
            if pending_date:
                combined = (pending_desc + ' ' + desc).strip()
                if not _line_has_words(combined) and len(amounts) < 2:
                    pending_date = None
                    pending_desc = ''
                    continue
                _append_bank_row(parsed, pending_date, combined or desc, amounts)
                pending_date = None
                pending_desc = ''
                continue
            if _WISE_AMOUNT_FIRST.search(line) or (not day and _line_has_words(desc) and pending_amounts is None and not parsed):
                pending_amounts = (amounts, desc)
                continue
            if day:
                _append_bank_row(parsed, day, _strip_leading_date(desc) or desc, amounts)
                pending_date = None
                pending_desc = ''
                continue
            if parsed:
                last_day = _parse_date(parsed[-1]['txn_date'])
                _append_bank_row(parsed, last_day, desc, amounts)
                continue
            if _line_has_words(desc):
                pending_amounts = (amounts, desc)
            continue
        if pending_date and _line_has_words(line):
            pending_desc = (pending_desc + ' ' + line).strip()
    packed, error = _pack_parsed_lines(parsed, opening)
    if packed:
        return packed, None
    if labeled and labeled.get('lines'):
        return labeled, None
    return None, labeled_error or error or 'No dated bank transactions were found in that statement. Export CSV from your bank, or paste the rows.'


def parse_statement_pdf(payload):
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            PdfReader = None
    if not PdfReader:
        return None, 'PDF reading is not available on this server. Export CSV from your bank.'
    try:
        reader = PdfReader(io.BytesIO(payload))
        text = '\n'.join((page.extract_text() or '') for page in reader.pages)
    except Exception:
        return None, 'That PDF could not be read. Export CSV from your bank, or paste the transactions.'
    if not text.strip():
        return None, 'This PDF has no readable text (it may be a scan). Export CSV from your bank.'
    looks_csv = text.count(',') >= 8 or text.count('\t') >= 4
    error = None
    if looks_csv:
        parsed, error = parse_statement_csv(text)
        if parsed and len(parsed.get('lines') or []) >= 3:
            return parsed, None
    text_parsed, text_error = parse_bank_text(text)
    if text_parsed:
        return text_parsed, None
    return None, text_error or error or 'No dated bank transactions were found in that PDF. Export CSV from your bank.'


def _source_hash(txn_date, description, amount, index):
    raw = f"{txn_date}|{str(description or '').strip().lower()}|{money(amount):.2f}|{index}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:40]


def _delete_journal(journal_id):
    if not journal_id:
        return
    conn = _db()
    conn.execute("DELETE FROM ledger_journal_lines WHERE journal_id = ?;", (journal_id,))
    conn.execute("DELETE FROM ledger_journals WHERE id = ?;", (journal_id,))
    conn.commit()
    _release(conn)


def _post_bank_journal(book_id, line, category_code, description):
    amount = money(line['amount'])
    if amount == 0:
        return None, 'Amount is zero.'
    if amount > 0:
        rows = (
            ('1200', amount, 0.0, description),
            (category_code, 0.0, amount, description),
        )
    else:
        abs_amt = abs(amount)
        rows = (
            (category_code, abs_amt, 0.0, description),
            ('1200', 0.0, abs_amt, description),
        )
    return _write_journal(
        book_id,
        line['txn_date'],
        description[:180] or 'Bank transaction',
        rows,
        'BANK',
    )


def _ensure_books(company_id, user_id=None):
    book = _book_for_company(company_id)
    if book:
        return book, None
    ws, error = start_blank_books(company_id, user_id)
    if error:
        return None, error
    return _book_for_company(company_id), None


def _is_image_statement(name, file_type=''):
    lower = str(name or '').lower()
    ctype = str(file_type or '').lower()
    return lower.endswith(_IMAGE_EXTS) or any(token in ctype for token in ('jpeg', 'jpg', 'png', 'image/', 'heic', 'webp'))


def is_statement_document(doc):
    category = str((doc or {}).get('category') or '').lower()
    name = str((doc or {}).get('name') or '').lower()
    slug = re.sub(r'[^a-z0-9]+', ' ', name)
    if 'bank statement' in category or category in ('bank statement', 'statement'):
        return True
    if 'bank statement' in name or 'bank statement' in slug:
        return True
    if re.search(r'\b(monzo|starling|revolut|wise|tide|barclays|hsbc|lloyds|natwest|halifax|santander|bos)\b', slug) and 'statement' in slug:
        return True
    if 'statement' in slug and re.search(r'\.(pdf|csv|txt|jpg|jpeg|png)$', name):
        return True
    if name.endswith('.csv'):
        return True
    return False


def _list_statement_documents(company_id, user_id=None):
    company = query_db("SELECT id, user_id FROM companies WHERE id = ?;", (company_id,), one=True) or {}
    owner_id = company.get('user_id')
    order_rows = query_db("SELECT id FROM orders WHERE company_id = ?;", (company_id,)) or []
    order_ids = [int(row['id']) for row in order_rows if row.get('id')]
    clauses = ["d.company_id = ?"]
    params = [company_id]
    if order_ids:
        placeholders = ','.join('?' for _ in order_ids)
        clauses.append(f"d.order_id IN ({placeholders})")
        params.extend(order_ids)
    if owner_id:
        clauses.append("(d.user_id = ? AND (d.company_id IS NULL OR d.company_id = 0))")
        params.append(owner_id)
    where = [f"({' OR '.join(clauses)})"]
    if user_id is not None:
        where.insert(0, "d.user_id = ?")
        params = [user_id, *params]
    sql = f"""
        SELECT d.id, d.name, d.category, d.file_path, d.file_type, d.created_at, d.company_id
        FROM documents d
        WHERE {' AND '.join(where)}
        ORDER BY d.id DESC;
    """
    rows = query_db(sql, tuple(params)) or []
    out = []
    for row in rows:
        if is_statement_document(row):
            out.append({
                'id': int(row['id']),
                'name': row.get('name') or 'Statement',
                'category': row.get('category') or '',
                'file_path': row.get('file_path') or '',
                'file_type': row.get('file_type') or '',
                'created_at': row.get('created_at'),
            })
    def _statement_rank(row):
        image = 1 if _is_image_statement(row.get('name'), row.get('file_type')) else 0
        path = _resolve_document_path(row.get('file_path'))
        missing = 0 if path and path.is_file() else 1
        huge = 1 if path and path.stat().st_size > 400_000 else 0
        return (missing, image, huge, -int(row['id']))

    out.sort(key=_statement_rank)
    return out


def _resolve_document_path(file_path):
    text = str(file_path or '').strip()
    if not text:
        return None
    path = Path(text)
    if path.is_file():
        return path
    parent = path.parent
    if parent.is_dir():
        matches = [item for item in parent.glob(path.name + '*') if item.is_file()]
        if matches:
            matches.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            return matches[0]
    return None


def _read_statement_file(doc):
    path = _resolve_document_path(doc.get('file_path'))
    if not path:
        return None, None, 0
    try:
        return path.read_bytes(), path.name, path.stat().st_size
    except OSError:
        return None, None, 0


def parse_statement_bytes(payload, filename='', content_type=''):
    lower = str(filename or '').lower()
    ctype = str(content_type or '').lower()
    if _is_image_statement(lower, ctype):
        return None, 'That file is a photo, not a statement export. Download a PDF or CSV from your bank, or paste the rows.', 'image'
    if lower.endswith('.pdf') or ctype.endswith('pdf') or (payload or b'')[:5] == b'%PDF-':
        parsed, error = parse_statement_pdf(payload)
        return parsed, error, 'pdf'
    try:
        csv_text = (payload or b'').decode('utf-8')
        if '\x00' in csv_text[:400]:
            raise UnicodeError('binary')
        parsed, error = parse_statement_csv(csv_text)
        if parsed:
            return parsed, None, 'csv'
        text_parsed, text_error = parse_bank_text(csv_text)
        if text_parsed:
            return text_parsed, None, 'csv'
        return None, text_error or error, 'csv'
    except (UnicodeError, UnicodeDecodeError):
        parsed, error = parse_statement_pdf(payload)
        return parsed, error, 'pdf'


def import_statement(company_id, data, user_id=None, files=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    files = files or {}
    filename = str(data.get('filename') or '').strip()
    source = 'csv'
    parsed = None
    error = None
    upload = files.get('file') or files.get('statement') or files.get('csv')
    csv_text = data.get('csv_text') or data.get('paste') or data.get('text') or ''
    if not upload and not str(csv_text or '').strip():
        docs = _list_statement_documents(company_id, user_id)
        wanted = _int_id(data.get('document_id'))
        if wanted:
            docs = [row for row in docs if int(row['id']) == wanted]
            if not docs:
                return None, 'That statement is not on this company file.'
        last_error = None
        oversized = []
        for doc in docs:
            payload, stored_name, size = _read_statement_file(doc)
            if not payload:
                last_error = f"{doc['name']} is on file but the file is missing from storage."
                continue
            if _is_image_statement(doc.get('name'), doc.get('file_type')):
                last_error = f"{doc['name']} is a photo. Download a PDF or CSV from your bank, or paste the rows."
                continue
            if not wanted and size > _AUTO_IMPORT_MAX_BYTES:
                oversized.append(doc['name'])
                continue
            filename = filename or doc.get('name') or stored_name
            parsed, error, source = parse_statement_bytes(payload, filename, doc.get('file_type'))
            if parsed:
                break
            last_error = f"{doc['name']}: {error}"
        if not parsed and oversized and not wanted:
            for doc in docs:
                if doc.get('name') not in oversized:
                    continue
                payload, stored_name, _size = _read_statement_file(doc)
                if not payload:
                    continue
                filename = filename or doc.get('name') or stored_name
                parsed, error, source = parse_statement_bytes(payload, filename, doc.get('file_type'))
                if parsed:
                    break
                last_error = f"{doc['name']}: {error}"
        if not parsed:
            if docs:
                return None, last_error or 'We have a statement on file but could not read the rows. Export CSV from your bank, or paste the transactions.'
            return None, 'Paste or upload a bank statement first. If you already uploaded one in Documents, open Accounts and we will use it.'
    if upload:
        filename = filename or upload.get('filename') or 'statement'
        payload = upload.get('bytes') or b''
        parsed, error, source = parse_statement_bytes(payload, filename, upload.get('content_type'))
    if parsed is None and str(csv_text or '').strip():
        parsed, error = parse_statement_csv(csv_text)
        if not parsed:
            text_parsed, text_error = parse_bank_text(csv_text)
            if text_parsed:
                parsed, error = text_parsed, None
            elif text_error:
                error = text_error
        source = 'csv'
    if not parsed:
        return None, error or 'Paste or upload a bank statement first.'
    book, error = _ensure_books(company_id, user_id)
    if error:
        return None, error
    opening = data.get('opening_balance')
    if opening in (None, ''):
        opening = parsed.get('opening_balance')
    else:
        opening = _parse_amount(opening)
    closing = parsed.get('closing_balance')
    lines = parsed['lines']
    if opening is None:
        opening = 0.0
    first_date = lines[0]['txn_date']
    last_date = lines[-1]['txn_date']
    if money(opening) != 0:
        bank_open = query_db(
            "SELECT opening_debit, opening_credit FROM ledger_nominals WHERE book_id = ? AND code = '1200';",
            (book['id'],),
            one=True,
        ) or {}
        if money(bank_open.get('opening_debit')) == 0 and money(bank_open.get('opening_credit')) == 0:
            execute_db(
                "UPDATE ledger_nominals SET opening_debit = ? WHERE book_id = ? AND code = '1200';",
                (money(opening), book['id']),
            )
            execute_db(
                "UPDATE ledger_nominals SET opening_credit = ? WHERE book_id = ? AND code = '3100';",
                (money(opening), book['id']),
            )
    existing = {
        row['source_hash']
        for row in (query_db("SELECT source_hash FROM ledger_bank_lines WHERE book_id = ?;", (book['id'],)) or [])
    }
    stmt_id = execute_db(
        """
        INSERT INTO ledger_statements (book_id, filename, source, opening_balance, closing_balance, row_count)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (book['id'], filename or 'pasted-statement.csv', source, money(opening) if opening is not None else None, money(closing) if closing is not None else None, len(lines)),
    )
    imported = 0
    skipped = 0
    for index, line in enumerate(lines):
        digest = _source_hash(line['txn_date'], line['description'], line['amount'], index)
        if digest in existing:
            skipped += 1
            continue
        category = categorise_line(line['description'], line['amount'])
        if not category:
            skipped += 1
            continue
        journal_id, error = _post_bank_journal(book['id'], line, category['code'], line['description'])
        if error:
            return None, error
        execute_db(
            """
            INSERT INTO ledger_bank_lines (
                book_id, statement_id, txn_date, description, amount, balance,
                category_code, category_label, confidence, needs_review, posted_journal_id, source_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                book['id'], stmt_id, line['txn_date'], line['description'], money(line['amount']),
                money(line['balance']) if line.get('balance') is not None else None,
                category['code'], category['label'], category['confidence'], int(category['needs_review']),
                journal_id, digest,
            ),
        )
        existing.add(digest)
        imported += 1
    current = _book_for_company(company_id)
    if current and not current.get('sample_loaded'):
        start = _parse_date(current.get('period_start'))
        end = _parse_date(current.get('period_end'))
        first = _parse_date(first_date)
        last = _parse_date(last_date)
        updates = []
        values = []
        if first and (not start or first < start):
            updates.append('period_start=?')
            values.append(first.isoformat())
        if last and (not end or last > end):
            updates.append('period_end=?')
            values.append(last.isoformat())
        if updates:
            values.append(current['id'])
            execute_db(
                "UPDATE ledger_books SET " + ", ".join(updates) + ", updated_at=CURRENT_TIMESTAMP WHERE id=?;",
                tuple(values),
            )
    execute_db(
        "UPDATE ledger_books SET filing_kind='traded', updated_at=CURRENT_TIMESTAMP WHERE id=? AND (filing_kind IS NULL OR filing_kind='');",
        (book['id'],),
    )
    ws, error = workspace(company_id, user_id)
    if error:
        return None, error
    ws['import_result'] = {
        'imported': imported,
        'skipped': skipped,
        'filename': filename or 'pasted-statement.csv',
        'source': source,
    }
    return ws, None


def recategorise_line(company_id, data, user_id=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    book = _book_for_company(company_id)
    if not book:
        return None, 'Upload a bank statement first.'
    line_id = _int_id(data.get('line_id') or data.get('id'))
    code = str(data.get('category_code') or data.get('code') or '').strip()
    if not line_id:
        return None, 'Select a transaction to recategorise.'
    labels = {item['code']: item['label'] for item in director_categories()}
    if code not in labels:
        return None, 'Choose a category from the list.'
    row = query_db(
        "SELECT * FROM ledger_bank_lines WHERE id = ? AND book_id = ?;",
        (line_id, book['id']),
        one=True,
    )
    if not row:
        return None, 'Transaction not found.'
    _delete_journal(row.get('posted_journal_id'))
    journal_id, error = _post_bank_journal(
        book['id'],
        {'txn_date': row['txn_date'], 'amount': row['amount']},
        code,
        row['description'],
    )
    if error:
        return None, error
    execute_db(
        """
        UPDATE ledger_bank_lines
        SET category_code=?, category_label=?, confidence='high', needs_review=0, posted_journal_id=?
        WHERE id=?;
        """,
        (code, labels[code], journal_id, line_id),
    )
    return workspace(company_id, user_id)


def confirm_bank_review(company_id, user_id=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    book = _book_for_company(company_id)
    if not book:
        return None, 'Upload a bank statement first.'
    execute_db("UPDATE ledger_bank_lines SET needs_review=0 WHERE book_id = ?;", (book['id'],))
    return workspace(company_id, user_id)


def load_sample_statement(company_id, user_id=None):
    started, error = start_blank_books(company_id, user_id)
    if error:
        return None, error
    return import_statement(
        company_id,
        {'csv_text': SAMPLE_STATEMENT_CSV, 'filename': 'sample-starling.csv', 'opening_balance': 12000},
        user_id,
    )


def _bank_payload(book, accounts=None, company_id=None, user_id=None):
    portal_documents = []
    cid = company_id or (book.get('company_id') if book else None)
    if cid:
        portal_documents = [{
            'id': row['id'],
            'name': row['name'],
            'category': row['category'],
            'created_at': row.get('created_at'),
        } for row in _list_statement_documents(cid, user_id)]
    if not book:
        return {
            'statements': [],
            'lines': [],
            'reconciliation': None,
            'needs_review': 0,
            'imported': 0,
            'categories': director_categories(),
            'portal_documents': portal_documents,
        }
    statements = query_db(
        """
        SELECT id, filename, source, opening_balance, closing_balance, row_count, created_at
        FROM ledger_statements WHERE book_id = ? ORDER BY id;
        """,
        (book['id'],),
    ) or []
    lines = query_db(
        """
        SELECT id, statement_id, txn_date, description, amount, balance, category_code, category_label,
               confidence, needs_review, posted_journal_id
        FROM ledger_bank_lines WHERE book_id = ? ORDER BY txn_date, id;
        """,
        (book['id'],),
    ) or []
    public_lines = []
    for row in lines:
        public_lines.append({
            'id': int(row['id']),
            'date': row['txn_date'],
            'description': row['description'],
            'amount': money(row['amount']),
            'balance': money(row['balance']) if row['balance'] is not None else None,
            'category_code': row['category_code'],
            'category_label': row['category_label'] or '',
            'confidence': row['confidence'],
            'needs_review': bool(row['needs_review']),
            'money_in': money(row['amount']) if money(row['amount']) > 0 else 0.0,
            'money_out': money(-row['amount']) if money(row['amount']) < 0 else 0.0,
        })
    opening = None
    stated_closing = None
    if statements:
        opening = statements[0].get('opening_balance')
        stated_closing = statements[-1].get('closing_balance')
    movement = money(sum(item['amount'] for item in public_lines))
    summed_closing = money((opening or 0) + movement) if opening is not None else None
    statement_agrees = None
    if stated_closing is not None and summed_closing is not None:
        statement_agrees = abs(stated_closing - summed_closing) < 0.02
    ledger_bank = 0.0
    if accounts:
        ledger_bank = money(sum(a['ytd_net'] for a in accounts if a['account_type'] == 'BANK'))
    target = stated_closing if stated_closing is not None else summed_closing
    books_agrees = abs(ledger_bank - target) < 0.02 if target is not None else None
    return {
        'statements': [{
            'id': int(row['id']),
            'filename': row.get('filename') or '',
            'source': row.get('source') or 'csv',
            'opening_balance': money(row['opening_balance']) if row['opening_balance'] is not None else None,
            'closing_balance': money(row['closing_balance']) if row['closing_balance'] is not None else None,
            'row_count': int(row['row_count'] or 0),
            'created_at': row.get('created_at'),
        } for row in statements],
        'lines': public_lines,
        'reconciliation': {
            'statement_opening': money(opening) if opening is not None else None,
            'statement_closing': money(stated_closing) if stated_closing is not None else None,
            'summed_closing': summed_closing,
            'statement_agrees': statement_agrees,
            'ledger_bank': ledger_bank,
            'difference': money(ledger_bank - target) if target is not None else None,
            'books_agrees': books_agrees,
        },
        'needs_review': sum(1 for item in public_lines if item['needs_review']),
        'imported': len(public_lines),
        'categories': director_categories(),
        'portal_documents': portal_documents,
    }


def _filing_payload(book, company, ye=None):
    filings = []
    if book:
        filings = query_db(
            """
            SELECT mode, status, receipt, message, pack_kind, created_at
            FROM ledger_ch_filings WHERE book_id = ? ORDER BY id DESC LIMIT 8;
            """,
            (book['id'],),
        ) or []
    number = (book or {}).get('company_number') or (company or {}).get('company_number') or ''
    has_auth = bool(str((company or {}).get('authentication_code') or '').strip())
    needed = []
    if not str(number).strip():
        needed.append({
            'key': 'company_number',
            'label': 'Company number',
            'reason': 'Needed on the accounts pack and for Companies House.',
        })
    if not has_auth:
        needed.append({
            'key': 'authentication_code',
            'label': 'Companies House authentication code',
            'reason': 'Needed only if you want this software to send the pack. You can still download it and upload it in WebFiling.',
        })
    presenter = bool(os.environ.get('CH_PRESENTER_ID') and (os.environ.get('CH_PRESENTER_AUTH') or os.environ.get('CH_XML_GATEWAY_PASSWORD')))
    return {
        'company_number': number,
        'has_authentication_code': has_auth,
        'has_presenter_credentials': presenter,
        'webfiling_url': WEBFILING_URL,
        'needed': needed,
        'can_file_micro': bool(ye and ye.get('can_file_micro')),
        'can_file_dormant': bool(ye and ye.get('can_file_dormant')),
        'last_filings': [{
            'mode': row.get('mode'),
            'status': row.get('status'),
            'receipt': row.get('receipt'),
            'message': row.get('message'),
            'pack_kind': row.get('pack_kind'),
            'created_at': row.get('created_at'),
        } for row in filings],
    }


def _hmrc_payload(book, company, org=None, pl=None):
    org = org or (_public_org(book) if book else {})
    kind = str(org.get('filing_kind') or _book_kind(book) or '')
    profit = money((pl or {}).get('profit') or 0)
    if kind == 'dormant':
        profit = 0.0
    estimate = corporation_tax_estimate(profit)
    payment_due, return_due = ct_dates(org.get('period_end'))
    utr = str((book or {}).get('hmrc_utr') or org.get('hmrc_utr') or '').strip()
    filings = []
    if book:
        try:
            filings = query_db(
                """
                SELECT mode, status, receipt, message, tax_estimate, created_at
                FROM ledger_hmrc_filings WHERE book_id = ? ORDER BY id DESC LIMIT 8;
                """,
                (book['id'],),
            ) or []
        except sqlite3.OperationalError:
            filings = []
    needed = []
    if not utr:
        needed.append({
            'key': 'hmrc_utr',
            'label': 'HMRC tax number (UTR)',
            'reason': 'On the letter HMRC sent when the company was registered for Corporation Tax. Needed only if you file the Company Tax Return yourself.',
        })
    if kind == 'dormant':
        steps = [
            'You usually pay nothing if the company slept all year and made no profit.',
            'Tell HMRC the company is dormant (GOV.UK link below) if they do not already know.',
            'You do not normally send a Company Tax Return for a fully dormant year once HMRC have been told.',
        ]
        headline = 'Usually no Corporation Tax — the company slept.'
    else:
        steps = [
            'Check the profit figure from your books.',
            f"Pay any Corporation Tax by {_uk_long(payment_due) if payment_due else '9 months and 1 day after year end'}.",
            f"Send the Company Tax Return (CT600) by {_uk_long(return_due) if return_due else '12 months after year end'}.",
        ]
        headline = estimate['plain']
    return {
        'kind': kind or 'traded',
        'headline': headline,
        'profit': estimate['profit'],
        'tax': estimate['tax'],
        'band': estimate['band'],
        'rate_pct': estimate['rate_pct'],
        'plain': estimate['plain'],
        'payment_due': payment_due,
        'return_due': return_due,
        'utr': utr,
        'has_utr': bool(utr),
        'needed': needed,
        'steps': steps,
        'file_url': HMRC_CT_URL,
        'dormant_url': HMRC_DORMANT_URL,
        'disclaimer': (
            'This is a guide from the books in this portal. It is not a filed Company Tax Return '
            'and not Making Tax Digital software. Download or record a sandbox receipt, then send the live return on GOV.UK if you need to.'
        ),
        'last_filings': [{
            'mode': row.get('mode'),
            'status': row.get('status'),
            'receipt': row.get('receipt'),
            'message': row.get('message'),
            'tax_estimate': money(row['tax_estimate']) if row.get('tax_estimate') is not None else None,
            'created_at': row.get('created_at'),
        } for row in filings],
    }


def _ch_gateway_xml(company_number, auth_code, presenter_id, presenter_auth, period_end, filing_type='MicroEntity'):
    def esc(value):
        return html_lib.escape(str(value or ''), quote=True)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<GovTalkMessage xmlns="http://www.govtalk.gov.uk/CM/envelope">',
        '<Header><MessageDetails><Class>Accounts</Class><Qualifier>request</Qualifier><Function>submit</Function></MessageDetails>',
        '<SenderDetails><IDAuthentication><SenderID>',
        esc(presenter_id),
        '</SenderID><Authentication><Method>clear</Method><Value>',
        esc(presenter_auth),
        '</Value></Authentication></IDAuthentication></SenderDetails></Header>',
        '<GovTalkDetails><Keys><Key Type="CompanyNumber">',
        esc(company_number),
        '</Key><Key Type="AuthenticationCode">',
        esc(auth_code),
        '</Key></Keys></GovTalkDetails>',
        '<Body><Accounts FilingType="',
        esc(filing_type or 'MicroEntity'),
        '" PeriodEnd="',
        esc(period_end),
        '">Brixen FRS 105 HTML pack. Not a validated TIS v5.9 iXBRL submission.</Accounts></Body>',
        '</GovTalkMessage>',
    ]
    return ''.join(parts)


def submit_companies_house(company_id, data, user_id=None):
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    book = _book_for_company(company_id)
    if not book:
        return None, 'Choose Start first: did the company trade this year, or did it sleep?'
    if _book_kind(book) != 'dormant':
        imported = query_db(
            "SELECT COUNT(*) AS n FROM ledger_bank_lines WHERE book_id = ?;",
            (book['id'],),
            one=True,
        ) or {}
        if int(imported.get('n') or 0) <= 0:
            return None, 'Add the bank statement and check the numbers before sending them to Companies House.'
    pack_kind = str(data.get('pack_kind') or 'filleted').strip() or 'filleted'
    if pack_kind not in ('members', 'filleted', 'ixbrl'):
        pack_kind = 'filleted'
    payload, error, _ctype, filename = export_pack(company_id, pack_kind, user_id)
    if error:
        return None, error
    company_number = re.sub(r'\D', '', str(data.get('company_number') or book.get('company_number') or company.get('company_number') or ''))[:8]
    auth_code = str(data.get('authentication_code') or company.get('authentication_code') or '').strip()
    presenter_id = str(data.get('presenter_id') or os.environ.get('CH_PRESENTER_ID') or '').strip()
    presenter_auth = str(
        data.get('presenter_auth') or data.get('api_key') or os.environ.get('CH_PRESENTER_AUTH')
        or os.environ.get('CH_XML_GATEWAY_PASSWORD') or ''
    ).strip()
    gateway = (os.environ.get('CH_XML_GATEWAY_URL') or CH_GATEWAY_DEFAULT).strip()
    if auth_code and not str(company.get('authentication_code') or '').strip():
        execute_db("UPDATE companies SET authentication_code = ? WHERE id = ?;", (auth_code, company_id))
    if company_number and not str(book.get('company_number') or '').strip():
        execute_db("UPDATE ledger_books SET company_number = ? WHERE id = ?;", (company_number, book['id']))
    mode = 'sandbox'
    status = 'accepted'
    receipt = f"BRIXEN-SANDBOX-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}-{book['id']}"
    message = (
        'No Companies House software-filing credentials on this server. '
        'Download the Companies House pack and upload it in WebFiling. '
        'This sandbox receipt is not a Companies House filing.'
    )
    live_ready = bool(company_number and auth_code and presenter_id and presenter_auth)
    if live_ready and not gateway.startswith('mock:'):
        xml = _ch_gateway_xml(
            company_number, auth_code, presenter_id, presenter_auth, book.get('period_end'),
            'Dormant' if _book_kind(book) == 'dormant' else 'MicroEntity',
        )
        try:
            request = urllib.request.Request(
                gateway,
                data=xml.encode('utf-8'),
                headers={'Content-Type': 'text/xml; charset=utf-8'},
                method='POST',
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                body = response.read().decode('utf-8', errors='replace')
            mode = 'live'
            status = 'submitted'
            receipt_match = re.search(r'(?:ReceiptNumber|CorrelationID)>([^<]+)', body)
            receipt = (receipt_match.group(1).strip() if receipt_match else f'CH-{book["id"]}-{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}')
            message = f'Companies House gateway accepted the submission. Pack {filename} was sent.'
        except Exception as exc:
            mode = 'sandbox'
            status = 'sandbox'
            message = (
                f'The Companies House gateway did not accept the filing ({exc}). '
                'A sandbox receipt was issued instead. Download the pack and upload it in WebFiling.'
            )
    elif live_ready and gateway.startswith('mock:'):
        mode = 'sandbox'
        message = 'Mock Companies House gateway: sandbox receipt issued. Download the pack or use WebFiling for a real filing.'
    execute_db(
        """
        INSERT INTO ledger_ch_filings (book_id, mode, status, receipt, message, pack_kind)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (book['id'], mode, status, receipt, message, pack_kind),
    )
    ws, error = workspace(company_id, user_id)
    if error:
        return None, error
    ws['ch_submit'] = {
        'mode': mode,
        'status': status,
        'receipt': receipt,
        'message': message,
        'pack_kind': pack_kind,
        'filename': filename,
        'webfiling_url': WEBFILING_URL,
        'bytes': len(payload or b''),
    }
    return ws, None


def submit_hmrc(company_id, data, user_id=None):
    """Record a Corporation Tax helper send. Live CT gateway only if secrets exist; otherwise sandbox."""
    company = _company_row(company_id, user_id)
    if not company:
        return None, 'Company not found'
    book = _book_for_company(company_id)
    if not book:
        ws, error = start_blank_books(company_id, user_id)
        if error:
            return None, error
        book = _book_for_company(company_id)
    utr = re.sub(r'\s+', '', str(data.get('hmrc_utr') or data.get('utr') or book.get('hmrc_utr') or ''))[:15]
    if utr:
        execute_db("UPDATE ledger_books SET hmrc_utr=?, updated_at=CURRENT_TIMESTAMP WHERE id=?;", (utr, book['id']))
        book = _book_for_company(company_id)
    ws, error = workspace(company_id, user_id)
    if error:
        return None, error
    hmrc = ws.get('hmrc') or {}
    tax = money(hmrc.get('tax') or 0)
    mode = 'sandbox'
    status = 'recorded'
    receipt = f"BRIXEN-HMRC-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}-{book['id']}"
    if _book_kind(book) == 'dormant':
        message = (
            'Recorded as dormant for Corporation Tax. This is not a live HMRC filing. '
            'Tell HMRC on GOV.UK if they do not already know the company slept.'
        )
    else:
        message = (
            'Sandbox receipt for the Company Tax Return helper. This is not a filed CT600. '
            'Pay any tax shown and send the live return on GOV.UK.'
        )
    execute_db(
        """
        INSERT INTO ledger_hmrc_filings (book_id, mode, status, receipt, message, tax_estimate)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (book['id'], mode, status, receipt, message, tax),
    )
    ws, error = workspace(company_id, user_id)
    if error:
        return None, error
    ws['hmrc_submit'] = {
        'mode': mode,
        'status': status,
        'receipt': receipt,
        'message': message,
        'tax': tax,
        'file_url': HMRC_CT_URL,
        'dormant_url': HMRC_DORMANT_URL,
    }
    return ws, None


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
        period = _default_period_from_company(company)
        stub_org = {
            'registered_name': crm['name'],
            'company_number': crm['company_number'],
            'period_start': period['period_start'],
            'period_end': period['period_end'],
            'filing_kind': '',
            'hmrc_utr': '',
        }
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
            'bank': _bank_payload(None, company_id=company_id, user_id=user_id),
            'filing': _filing_payload(None, company),
            'hmrc': _hmrc_payload(None, company, stub_org, {'profit': 0}),
            'next_step': 'choose',
            'filing_kind': '',
        }, None
    accounts = _nominal_balances(book)
    org = _public_org(book)
    if not org.get('filing_kind'):
        has_bank = query_db(
            "SELECT id FROM ledger_bank_lines WHERE book_id = ? LIMIT 1;",
            (book['id'],),
            one=True,
        )
        if has_bank:
            execute_db(
                "UPDATE ledger_books SET filing_kind='traded', updated_at=CURRENT_TIMESTAMP WHERE id=?;",
                (book['id'],),
            )
            org['filing_kind'] = 'traded'
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
    bank = _bank_payload(book, accounts, company_id=company_id, user_id=user_id)
    filing = _filing_payload(book, company, ye)
    hmrc = _hmrc_payload(book, company, org, pl)
    kind = org.get('filing_kind') or ''
    if not kind:
        next_step = 'choose'
    elif kind == 'dormant':
        next_step = 'file'
    elif not bank['imported']:
        next_step = 'import_existing' if bank.get('portal_documents') else 'upload'
    elif bank['needs_review']:
        next_step = 'review'
    else:
        next_step = 'file'
    rec = bank.get('reconciliation') or {}
    if rec.get('books_agrees') is False:
        home['watch_items'] = list(home.get('watch_items') or [])
        home['watch_items'].insert(0, {
            'tone': 'warn',
            'title': 'Bank does not yet match the statement',
            'detail': f"Books show £{abs(rec.get('ledger_bank') or 0):,.2f}; the statement closes at £{abs(rec.get('statement_closing') or rec.get('summed_closing') or 0):,.2f}.",
        })
    elif rec.get('books_agrees'):
        home['watch_items'] = list(home.get('watch_items') or [])
        home['watch_items'].insert(0, {
            'tone': 'ok',
            'title': 'Bank matches the statement',
            'detail': f"Closing balance £{(rec.get('ledger_bank') or 0):,.2f}.",
        })
    if bank['needs_review']:
        home['watch_items'] = list(home.get('watch_items') or [])
        home['watch_items'].insert(0, {
            'tone': 'warn',
            'title': f"{bank['needs_review']} transaction(s) need a quick look",
            'detail': 'Open Review and confirm each category before you file.',
        })
    home['next_step'] = next_step
    home['needs_review'] = bank['needs_review']
    home['imported'] = bank['imported']
    home['filing_kind'] = kind
    if kind == 'dormant':
        kept = [
            item for item in (home.get('watch_items') or [])
            if 'bank' not in (item.get('title') or '').lower()
            and 'transaction' not in (item.get('title') or '').lower()
        ]
        home['watch_items'] = [{
            'tone': 'ok',
            'title': 'This company slept all year',
            'detail': 'No bank statement is needed. File the short dormant pack at Companies House, then tell HMRC.',
        }] + kept
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
        'bank': bank,
        'filing': filing,
        'hmrc': hmrc,
        'next_step': next_step,
        'filing_kind': kind,
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
    dormant = str(org.get('filing_kind') or '') == 'dormant' or bool(ye.get('can_file_dormant'))
    if dormant:
        title = 'Dormant company accounts for filing at Companies House' if filleted else 'Dormant company accounts for the members'
        brand = 'Brixen Consultants · dormant company accounts'
        pl_block = f"""
        <h2>Profit and loss account</h2>
        <p>The company was dormant throughout the year ended {_esc(_uk_long(org['period_end']))} and made neither a profit nor a loss.</p>
        """
    else:
        title = 'Accounts for filing at Companies House' if filleted else 'Accounts for the members'
        brand = 'Brixen Consultants · FRS 105 micro-entity accounts'
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
  <div class="brand">{_esc(brand)}</div>
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
    <p><strong>These accounts have been prepared in accordance with the {'dormant company' if dormant else 'micro-entity'} provisions.</strong></p>
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
    files = {}
    if isinstance(body, dict):
        files = body.pop('_files', None) or {}
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
    if action in ('sample-statement', 'sample_statement') and method == 'POST':
        ws, error = load_sample_statement(company_id, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action in ('import-statement', 'import_statement') and method == 'POST':
        ws, error = import_statement(company_id, body, owner_filter, files=files)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action in ('recategorise', 'recategorize') and method == 'POST':
        ws, error = recategorise_line(company_id, body, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action in ('confirm-review', 'confirm_review') and method == 'POST':
        ws, error = confirm_bank_review(company_id, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action in ('submit-companies-house', 'submit_companies_house') and method == 'POST':
        ws, error = submit_companies_house(company_id, body, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action in ('choose-path', 'choose_path') and method == 'POST':
        ws, error = choose_path(company_id, body, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action in ('submit-hmrc', 'submit_hmrc') and method == 'POST':
        ws, error = submit_hmrc(company_id, body, owner_filter)
        if error:
            status = '404 Not Found' if error == 'Company not found' else '400 Bad Request'
            return ('json', status, {'status': 'error', 'message': error})
        return ('json', '200 OK', ws)
    if action in ('reset-books', 'reset_books') and method == 'POST':
        ws, error = reset_books(company_id, owner_filter)
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
