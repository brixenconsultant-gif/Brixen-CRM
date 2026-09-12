import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, extract_session_token, seed_demo

class TestRBAC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()

    def test_01_admin_endpoint_permissions(self):
        st, _, _ = make_request('/api/admin/stats')
        self.assertEqual(st, '401 Unauthorized')

        _, hd, _ = make_request('/api/auth/login', method='POST', body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'})
        client_token = extract_session_token(hd)
        st_c, _, _ = make_request('/api/admin/stats', cookie=f"session_token={client_token}")
        self.assertEqual(st_c, '403 Forbidden')

        _, hd_adm, _ = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
        admin_token = extract_session_token(hd_adm)
        st_a, _, body_a = make_request('/api/admin/stats', cookie=f"session_token={admin_token}")
        self.assertEqual(st_a, '200 OK')
        self.assertEqual(body_a.get('status'), 'success')

if __name__ == '__main__':
    unittest.main()
