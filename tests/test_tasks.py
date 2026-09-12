import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, extract_session_token, seed_demo

class TestTasks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()

    def test_01_tasks_auth_and_access(self):
        st, _, _ = make_request('/api/admin/tasks')
        self.assertEqual(st, '401 Unauthorized')

        st_login, hd, _ = make_request('/api/auth/login', method='POST', body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'})
        client_token = extract_session_token(hd)
        st_client, _, _ = make_request('/api/admin/tasks', cookie=f"session_token={client_token}")
        self.assertEqual(st_client, '403 Forbidden')

    def test_02_admin_tasks_list(self):
        st_login, hd, _ = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
        admin_token = extract_session_token(hd)

        st, _, res = make_request('/api/admin/tasks', cookie=f"session_token={admin_token}")
        self.assertEqual(st, '200 OK')
        self.assertEqual(res.get('status'), 'success')
        self.assertTrue(isinstance(res.get('tasks'), list))

if __name__ == '__main__':
    unittest.main()
