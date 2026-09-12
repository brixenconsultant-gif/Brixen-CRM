import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, extract_session_token, seed_demo

class TestDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()

    def test_01_admin_documents_list(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
        admin_token = extract_session_token(headers)

        st, hd, res = make_request('/api/admin/documents', cookie=f"session_token={admin_token}")
        self.assertEqual(st, '200 OK')
        self.assertEqual(res.get('status'), 'success')
        self.assertTrue(isinstance(res.get('documents'), list))

    def test_02_client_document_isolation(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'})
        client1_token = extract_session_token(headers)

        status2, headers2, body2 = make_request('/api/auth/login', method='POST', body={'email': 'v.smith@vantagecyber.co.uk', 'password': 'ClientPass123!'})
        client2_token = extract_session_token(headers2)

        doc1 = query_db("SELECT id FROM documents WHERE user_id = (SELECT id FROM users WHERE email = 'client1@acmecorp.co.uk') LIMIT 1;", one=True)
        if doc1:
            st, _, _ = make_request(f"/api/client/documents/{doc1['id']}/download", cookie=f"session_token={client2_token}")
            self.assertEqual(st, '403 Forbidden')

if __name__ == '__main__':
    unittest.main()
