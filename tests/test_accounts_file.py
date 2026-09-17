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
        self.assertIn('Load sample organisation', body if isinstance(body, str) else body.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
