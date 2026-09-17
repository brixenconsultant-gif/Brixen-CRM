import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, extract_session_token, seed_demo
import accounts_file


def _login(email, password):
    status, headers, body = make_request('/api/auth/login', method='POST', body={'email': email, 'password': password})
    token = extract_session_token(headers)
    return token, status, body


class TestAccountsFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()
        cls.admin_token, _, _ = _login('admin@brixenconsultant.co.uk', 'AdminPass123!')
        cls.client_token, _, _ = _login('client1@acmecorp.co.uk', 'ClientPass123!')
        company = query_db("SELECT id FROM companies ORDER BY id LIMIT 1;", one=True)
        cls.company_id = int(company['id'])
        client_company = query_db(
            "SELECT c.id FROM companies c JOIN users u ON u.id = c.user_id WHERE u.email = ? ORDER BY c.id LIMIT 1;",
            ('client1@acmecorp.co.uk',),
            one=True,
        )
        cls.client_company_id = int(client_company['id']) if client_company else cls.company_id

    def _admin_cookie(self):
        return f"session_token={self.admin_token}"

    def _client_cookie(self):
        return f"session_token={self.client_token}"

    def test_01_portal_shell_has_accounts_nav(self):
        status, headers, body = make_request('/')
        self.assertEqual(status, '200 OK')
        self.assertIn('data-view="client-accounts"', body)
        self.assertIn('data-view="client-year-end"', body)
        self.assertIn('data-view="admin-accounts"', body)
        self.assertIn('data-view="admin-year-end"', body)
        self.assertIn('/static/js/accounts-file.js', body)
        self.assertNotIn('Xero', body)

    def test_02_list_and_empty_workspace(self):
        st, hd, res = make_request('/api/admin/accounts-file', cookie=self._admin_cookie())
        self.assertEqual(st, '200 OK')
        self.assertEqual(res.get('status'), 'success')
        self.assertTrue(res.get('companies'))
        self.assertIn('has_authentication_code', res['companies'][0])
        self.assertIn('has_utr', res['companies'][0])
        self.assertIn('utr_number', res['companies'][0])
        st, hd, res = make_request(f'/api/admin/accounts-file/{self.company_id}', cookie=self._admin_cookie())
        self.assertEqual(st, '200 OK')
        self.assertTrue(res.get('empty'))

    def test_03_sample_books_tb_reports_and_micro(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/sample',
            method='POST',
            body={},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertFalse(res.get('empty'))
        self.assertEqual(res['organisation']['registered_name'], 'Willow & Thorn Consulting Ltd')
        tb = res['trial_balance']
        self.assertTrue(tb['agrees'], tb)
        self.assertEqual(tb['total_debit'], tb['total_credit'])
        self.assertAlmostEqual(res['profit_and_loss']['profit'], 36514.00, places=2)
        self.assertTrue(res['balance_sheet']['balances'], res['balance_sheet'])
        self.assertTrue(res['year_end']['can_file_micro'])
        # Year begins 1 April 2025, before SI 2024/1303, so the £632k / £316k / 10 limits apply.
        self.assertFalse(res['year_end']['size']['from_6_apr_2025'])
        self.assertEqual(res['organisation']['filing_due'], '2026-12-31')
        self.assertGreaterEqual(len(res['journals']), 17)
        self.assertTrue(any(a['code'] == '4000' for a in res['accounts']))

    def test_04_journal_must_balance(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/journals',
            method='POST',
            body={
                'journal_date': '2026-03-31',
                'narration': 'Unbalanced',
                'lines': [
                    {'code': '1200', 'debit': 10, 'credit': 0},
                    {'code': '4000', 'debit': 0, 'credit': 5},
                ],
            },
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '400 Bad Request')
        self.assertIn('equal', (res.get('message') or '').lower())

    def test_05_balanced_journal_updates_tb(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/journals',
            method='POST',
            body={
                'journal_date': '2026-03-31',
                'reference': 'YE-18',
                'narration': 'Reclassify sundry bank charges',
                'lines': [
                    {'code': '7700', 'debit': 12, 'credit': 0, 'description': 'Charges'},
                    {'code': '1200', 'debit': 0, 'credit': 12, 'description': 'Bank'},
                ],
            },
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertTrue(res['trial_balance']['agrees'])

    def test_06_exports_contain_statutory_wording(self):
        for kind, needle in (
            ('members', 'These accounts have been prepared in accordance with the micro-entity provisions'),
            ('filleted', 'not to deliver a copy of the profit and loss account'),
            ('ixbrl', 'DRAFT iXBRL'),
        ):
            st, hd, body = make_request(
                f'/api/admin/accounts-file/{self.company_id}/export/{kind}',
                cookie=self._admin_cookie(),
            )
            self.assertEqual(st, '200 OK')
            text = body if isinstance(body, str) else body.decode('utf-8')
            self.assertIn(needle, text)
            self.assertIn('Willow &amp; Thorn Consulting Ltd', text)
            self.assertNotIn('Xero', text)

    def test_07_client_can_open_own_company_not_admin_list(self):
        st, hd, res = make_request('/api/client/accounts-file', cookie=self._client_cookie())
        self.assertEqual(st, '200 OK', res)
        ids = {int(row['id']) for row in res.get('companies') or []}
        self.assertIn(self.client_company_id, ids)
        st, hd, res = make_request('/api/admin/accounts-file', cookie=self._client_cookie())
        self.assertEqual(st, '403 Forbidden')

    def test_08_size_test_helper_thresholds(self):
        import datetime
        result = accounts_file.size_test(84000, 66244, 2, datetime.date(2025, 4, 6), is_first=True)
        self.assertTrue(result['qualifies'])
        self.assertEqual(result['thresholds']['turnover'], 1_000_000)
        old = accounts_file.size_test(84000, 66244, 2, datetime.date(2025, 4, 1), is_first=True)
        self.assertEqual(old['thresholds']['turnover'], 632_000)

    def test_09_static_accounts_script(self):
        st, hd, body = make_request('/static/js/accounts-file.js')
        self.assertEqual(st, '200 OK')
        text = body if isinstance(body, str) else body.decode('utf-8')
        self.assertIn('File at Companies House', text)
        self.assertIn('It traded', text)
        self.assertIn('It slept', text)
        self.assertIn('HMRC tax', text)
        self.assertIn('Select company', text)
        self.assertIn('UTR number', text)
        self.assertIn('select-filter accounts-company-input', text)
        self.assertIn('Upload the latest', text)
        self.assertIn('Compile from this file', text)
        self.assertNotIn('Use the statement already on file', text)
        self.assertNotIn('maybeAutoImport', text)
        self.assertNotIn('Load sample organisation', text)
        self.assertNotIn('Try a sample statement', text)

    def test_10_sample_statement_books_and_rec(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/sample-statement',
            method='POST',
            body={},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertFalse(res.get('empty'))
        bank = res.get('bank') or {}
        self.assertGreaterEqual(bank.get('imported') or 0, 10)
        rec = bank.get('reconciliation') or {}
        self.assertTrue(res['trial_balance']['agrees'], res['trial_balance'])
        self.assertTrue(res['balance_sheet']['balances'], res['balance_sheet'])
        self.assertAlmostEqual(rec.get('statement_closing') or 0, 15808.00, places=2)
        self.assertAlmostEqual(rec.get('ledger_bank') or 0, 15808.00, places=2)
        self.assertTrue(rec.get('books_agrees'), rec)
        self.assertTrue(rec.get('statement_agrees'), rec)
        self.assertAlmostEqual(res['profit_and_loss']['profit'], 3808.00, places=2)
        self.assertTrue(any(line['category_code'] == '4000' for line in bank.get('lines') or []))

    def test_11_csv_paste_and_recategorise(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/start',
            method='POST',
            body={},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        csv_text = (
            'Date,Description,Amount,Balance\n'
            '01/04/2026,Opening balance,0,1000.00\n'
            '02/04/2026,Stripe Payout,250.00,1250.00\n'
            '03/04/2026,Mystery shop,-40.00,1210.00\n'
        )
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/import-statement',
            method='POST',
            body={'csv_text': csv_text, 'filename': 'mini.csv', 'opening_balance': 1000},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        lines = (res.get('bank') or {}).get('lines') or []
        mystery = next((row for row in lines if 'Mystery' in (row.get('description') or '')), None)
        self.assertIsNotNone(mystery)
        self.assertTrue(mystery.get('needs_review'))
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/recategorise',
            method='POST',
            body={'line_id': mystery['id'], 'category_code': '7500'},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        updated = next(row for row in res['bank']['lines'] if row['id'] == mystery['id'])
        self.assertEqual(updated['category_code'], '7500')
        self.assertFalse(updated['needs_review'])
        self.assertTrue(res['trial_balance']['agrees'])

    def test_12_submit_companies_house_sandbox(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/submit-companies-house',
            method='POST',
            body={'pack_kind': 'filleted'},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        submit = res.get('ch_submit') or {}
        self.assertEqual(submit.get('mode'), 'sandbox')
        self.assertTrue(str(submit.get('receipt') or '').startswith('BRIXEN-SANDBOX-'))
        self.assertTrue((res.get('filing') or {}).get('last_filings'))
        self.assertIn('ewf.companieshouse.gov.uk', (res.get('filing') or {}).get('webfiling_url') or '')

    def test_13_pdf_statement_when_text_is_present(self):
        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font('Helvetica', size=10)
        for row in (
            '01/05/2026 Opening balance 500.00',
            '02/05/2026 Stripe Payout 100.00 600.00',
            '03/05/2026 Office rent -80.00 520.00',
        ):
            pdf.cell(0, 8, row)
            pdf.ln()
        payload = pdf.output()
        if isinstance(payload, bytearray):
            payload = bytes(payload)
        parsed, error = accounts_file.parse_statement_pdf(payload)
        self.assertIsNone(error, error)
        self.assertGreaterEqual(len(parsed['lines']), 2)
        self.assertTrue(any('Stripe' in line['description'] for line in parsed['lines']))

    def test_14_missing_file_asks_for_statement(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/import-statement',
            method='POST',
            body={'csv_text': ''},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '400 Bad Request')
        self.assertIn('statement', (res.get('message') or '').lower())

    def test_15_uk_bank_text_layouts(self):
        monzo = (
            "Personal Account statement\n"
            "01/08/2026 - 10/08/2026\n"
            "£1,477.66\n"
            "Date Description (GBP) Amount Balance\n"
            "10/08/2026 Stripe Payout\n"
            "REF-1 250.00 1,477.66\n"
            "09/08/2026 Office rent Acme Ltd\n"
            "REF-2 -80.00 1,227.66\n"
            "08/08/2026 Tesco Stores\n"
            "REF-3 -12.50 1,307.66\n"
        )
        parsed, error = accounts_file.parse_bank_text(monzo)
        self.assertIsNone(error, error)
        self.assertEqual(len(parsed['lines']), 3)
        self.assertEqual(parsed['lines'][0]['txn_date'], '2026-08-08')
        self.assertTrue(any('Stripe' in row['description'] for row in parsed['lines']))
        self.assertAlmostEqual(parsed['lines'][-1]['amount'], 250.00, places=2)
        self.assertAlmostEqual(parsed['opening_balance'], 1320.16, places=2)

        wise = (
            "GBP statement\n"
            "12 Sep 2025 [GMT+00:00] - 10 Sep 2026 [GMT+00:00]\n"
            "Description Incoming Outgoing Amount\n"
            "Sent money to Supplier Ltd -120.00 50.00\n"
            "10 Sep 2026 | Transfer TR-1\n"
            "Received money from Stripe 200.00 170.00\n"
            "9 Sep 2026 | Transfer TR-2\n"
        )
        parsed, error = accounts_file.parse_bank_text(wise)
        self.assertIsNone(error, error)
        self.assertEqual(len(parsed['lines']), 2)
        sent = next(row for row in parsed['lines'] if 'Supplier' in row['description'])
        received = next(row for row in parsed['lines'] if 'Stripe' in row['description'])
        self.assertLess(sent['amount'], 0)
        self.assertGreater(received['amount'], 0)
        self.assertEqual(sent['txn_date'], '2026-09-10')
        self.assertEqual(received['txn_date'], '2026-09-09')

        bos = (
            "Column\nDate\n03 Aug 26.\nDescription\nTESCO STORES 070076.\nType\nDEB.\n"
            "Money In (£)\nblank.\nMoney Out (£)\n2.00.\nBalance (£)\n22.24.\n"
            "Date\n04 Aug 26.\nDescription\nSTRIPE PAYOUT.\nType\nFPI.\n"
            "Money In (£)\n100.00.\nMoney Out (£)\nblank.\nBalance (£)\n122.24.\n"
        )
        parsed, error = accounts_file.parse_bank_text(bos)
        self.assertIsNone(error, error)
        self.assertEqual(len(parsed['lines']), 2)
        self.assertEqual(parsed['lines'][0]['txn_date'], '2026-08-03')
        self.assertAlmostEqual(parsed['lines'][0]['amount'], -2.00, places=2)
        self.assertAlmostEqual(parsed['lines'][1]['amount'], 100.00, places=2)
        self.assertIsNone(accounts_file.parse_uk_date('23-08-01'))
        self.assertEqual(accounts_file.parse_uk_date('10/08/2026').isoformat(), '2026-08-10')
        wise_sort = (
            "UK sort code\n"
            "23-08-01\n"
            "Sent money to Supplier Ltd -120.00 50.00\n"
            "10 Sep 2026 | Transfer TR-1\n"
            "Received money from Stripe 200.00 170.00\n"
            "9 Sep 2026 | Transfer TR-2\n"
        )
        parsed, error = accounts_file.parse_bank_text(wise_sort)
        self.assertIsNone(error, error)
        self.assertTrue(all(not row['txn_date'].startswith('2001') for row in parsed['lines']))
        self.assertTrue(accounts_file.is_statement_document({
            'name': 'Monzo_bank_statement_2026-08-01-2026-08-10_5996.pdf',
            'category': 'Order Documents',
        }))
        self.assertTrue(accounts_file.is_statement_document({
            'name': 'statement_133367598_GBP_2025-09-12_2026-09-10.pdf',
            'category': 'Order Documents',
        }))

    def test_16_portal_document_becomes_books(self):
        import os
        import tempfile
        from db import execute_db, query_db as qdb
        csv_text = (
            "Date,Description,Amount,Balance\n"
            "02/05/2026,Stripe Payout,400.00,1400.00\n"
            "03/05/2026,Office rent,-90.00,1310.00\n"
        )
        path = os.path.join(tempfile.gettempdir(), 'brixen_portal_statement.csv')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(csv_text)
        owner = qdb("SELECT user_id FROM companies WHERE id = ?;", (self.company_id,), one=True)
        doc_id = execute_db(
            """
            INSERT INTO documents (
                user_id, company_id, name, category, file_path, file_type, file_size,
                status, uploaded_by, client_visible
            ) VALUES (?, ?, 'Monzo_bank_statement_portal.csv', 'Order Documents', ?, 'CSV', '1 KB',
                      'Approved', 'Customer Upload', 1);
            """,
            (owner['user_id'], self.company_id, path),
        )
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/import-statement',
            method='POST',
            body={'document_id': doc_id},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertGreaterEqual((res.get('import_result') or {}).get('imported') or 0, 2)
        self.assertFalse(res.get('empty'))
        self.assertTrue((res.get('bank') or {}).get('imported'))

    def test_17_reset_books_clears_ledger_keeps_company(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/reset-books',
            method='POST',
            body={},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertTrue(res.get('empty'))
        self.assertFalse((res.get('bank') or {}).get('imported'))
        st, hd, res = make_request(f'/api/admin/accounts-file/{self.company_id}', cookie=self._admin_cookie())
        self.assertEqual(st, '200 OK', res)
        self.assertTrue(res.get('company'))
        self.assertTrue(res.get('empty'))

    def test_18_import_after_reset_does_not_500(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/start',
            method='POST',
            body={},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        csv_text = (
            'Date,Description,Amount,Balance\n'
            '02/06/2026,Stripe Payout,150.00,1150.00\n'
            '03/06/2026,Office rent,-50.00,1100.00\n'
        )
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/import-statement',
            method='POST',
            body={'csv_text': csv_text, 'filename': 'after-reset.csv', 'opening_balance': 1000},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertGreaterEqual((res.get('import_result') or {}).get('imported') or 0, 2)

    def test_19_dormant_path_files_without_bank(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/reset-books',
            method='POST',
            body={},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/choose-path',
            method='POST',
            body={'kind': 'dormant'},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertEqual(res.get('filing_kind'), 'dormant')
        self.assertEqual(res.get('next_step'), 'file')
        self.assertTrue(res['year_end']['can_file_dormant'])
        self.assertTrue(any(stt['id'] == 'dormant_year' for stt in res['year_end']['statements']))
        st, hd, body = make_request(
            f'/api/admin/accounts-file/{self.company_id}/export/filleted',
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK')
        text = body if isinstance(body, str) else body.decode('utf-8')
        self.assertIn('dormant throughout the year', text)
        self.assertIn('dormant company', text.lower())
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/submit-companies-house',
            method='POST',
            body={'pack_kind': 'filleted'},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertTrue((res.get('ch_submit') or {}).get('receipt'))

    def test_20_corporation_tax_bands(self):
        small = accounts_file.corporation_tax_estimate(40000)
        self.assertEqual(small['band'], 'small')
        self.assertAlmostEqual(small['tax'], 7600.00, places=2)
        main = accounts_file.corporation_tax_estimate(250000)
        self.assertEqual(main['band'], 'main')
        self.assertAlmostEqual(main['tax'], 62500.00, places=2)
        mid = accounts_file.corporation_tax_estimate(100000)
        self.assertEqual(mid['band'], 'marginal')
        self.assertAlmostEqual(mid['tax'], 22750.00, places=2)
        none = accounts_file.corporation_tax_estimate(0)
        self.assertEqual(none['band'], 'none')
        self.assertEqual(none['tax'], 0)

    def test_21_traded_path_hmrc_helper_sandbox(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/choose-path',
            method='POST',
            body={'kind': 'traded'},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        csv_text = (
            'Date,Description,Amount,Balance\n'
            '02/06/2026,Stripe Payout,2000.00,3000.00\n'
            '03/06/2026,Office rent,-200.00,2800.00\n'
        )
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/import-statement',
            method='POST',
            body={'csv_text': csv_text, 'filename': 'hmrc-path.csv', 'opening_balance': 1000},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertEqual(res.get('filing_kind'), 'traded')
        hmrc = res.get('hmrc') or {}
        self.assertIn(hmrc.get('band'), ('none', 'small', 'marginal', 'main'))
        self.assertTrue(hmrc.get('payment_due'))
        self.assertTrue(hmrc.get('return_due'))
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/submit-hmrc',
            method='POST',
            body={'hmrc_utr': '1234567890'},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertTrue((res.get('hmrc_submit') or {}).get('receipt', '').startswith('BRIXEN-HMRC-'))
        self.assertEqual((res.get('organisation') or {}).get('hmrc_utr'), '1234567890')

    def test_22_empty_workspace_asks_to_choose(self):
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/reset-books',
            method='POST',
            body={},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertTrue(res.get('empty'))
        self.assertEqual(res.get('next_step'), 'choose')
        self.assertTrue(res.get('hmrc'))

    def test_22_does_not_auto_import_portal_statement(self):
        import os
        import tempfile
        from db import execute_db, query_db as qdb
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/reset-books',
            method='POST',
            body={},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        csv_text = (
            "Date,Description,Amount,Balance\n"
            "02/05/2026,Stripe Payout,400.00,1400.00\n"
            "03/05/2026,Office rent,-90.00,1310.00\n"
        )
        path = os.path.join(tempfile.gettempdir(), 'brixen_old_portal_statement.csv')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(csv_text)
        owner = qdb("SELECT user_id FROM companies WHERE id = ?;", (self.company_id,), one=True)
        execute_db(
            """
            INSERT INTO documents (
                user_id, company_id, name, category, file_path, file_type, file_size,
                status, uploaded_by, client_visible
            ) VALUES (?, ?, 'Old_Monzo_bank_statement.csv', 'Order Documents', ?, 'CSV', '1 KB',
                      'Approved', 'Customer Upload', 1);
            """,
            (owner['user_id'], self.company_id, path),
        )
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/choose-path',
            method='POST',
            body={'kind': 'traded'},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertEqual(res.get('next_step'), 'upload')
        self.assertFalse((res.get('bank') or {}).get('imported'))
        self.assertTrue((res.get('bank') or {}).get('portal_documents'))
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}/import-statement',
            method='POST',
            body={},
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '400 Bad Request', res)
        self.assertIn('latest bank statement', (res.get('message') or '').lower())
        st, hd, res = make_request(
            f'/api/admin/accounts-file/{self.company_id}',
            cookie=self._admin_cookie(),
        )
        self.assertEqual(st, '200 OK', res)
        self.assertFalse((res.get('bank') or {}).get('imported'))
        self.assertEqual(res.get('next_step'), 'upload')


if __name__ == '__main__':
    unittest.main()
