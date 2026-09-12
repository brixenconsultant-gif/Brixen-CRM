import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, extract_session_token, seed_demo

class TestCompanies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()

    def test_01_client_portfolio_api(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'})
        token = extract_session_token(headers)
        
        st, hd, res = make_request('/api/client/companies', cookie=f"session_token={token}")
        self.assertEqual(st, '200 OK')
        self.assertEqual(res.get('status'), 'success')
        self.assertTrue(isinstance(res.get('companies'), list))
        self.assertGreater(len(res.get('companies')), 0)

    def test_02_admin_registered_companies_api(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
        admin_token = extract_session_token(headers)

        st, hd, res = make_request('/api/admin/companies', cookie=f"session_token={admin_token}")
        self.assertEqual(st, '200 OK')
        self.assertEqual(res.get('status'), 'success')
        self.assertTrue(isinstance(res.get('companies'), list))

    def test_03_companies_house_search_api(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
        admin_token = extract_session_token(headers)

        st, hd, res = make_request('/api/admin/companies/search?q=Brixen', cookie=f"session_token={admin_token}")
        self.assertIn(st, ('200 OK', '503 Service Unavailable'))
        if st == '200 OK':
            self.assertEqual(res.get('status'), 'success')

if __name__ == '__main__':
    unittest.main()
