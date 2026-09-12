import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, extract_session_token, pdf_text, pdf_links, seed_demo

class TestInvoices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()

    def test_01_admin_invoices_list(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
        admin_token = extract_session_token(headers)

        st, hd, res = make_request('/api/admin/invoices', cookie=f"session_token={admin_token}")
        self.assertEqual(st, '200 OK')
        self.assertEqual(res.get('status'), 'success')
        self.assertTrue(isinstance(res.get('invoices'), list))

    def test_02_invoice_pdf_download(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
        admin_token = extract_session_token(headers)

        inv = query_db("SELECT id FROM invoices LIMIT 1;", one=True)
        self.assertIsNotNone(inv)

        st, hd, pdf_bytes = make_request(f"/api/admin/invoices/{inv['id']}/document", cookie=f"session_token={admin_token}")
        self.assertEqual(st, '200 OK')
        text = pdf_text(pdf_bytes)
        self.assertIn('INVOICE', text)

if __name__ == '__main__':
    unittest.main()
