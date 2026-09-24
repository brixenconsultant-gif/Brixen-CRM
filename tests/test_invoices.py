import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, extract_session_token, pdf_text, pdf_links, seed_demo
from app import invoice_charged_total, invoice_send_email_kind, payment_received_copy, reconcile_invoice_line_amounts

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

    def test_03_invoice_uses_order_list_price_not_catalogue(self):
        charged = invoice_charged_total({'total': 100, 'amount': 100}, {'price': 199.99, 'total': 100})
        self.assertEqual(charged, 100.0)
        items = reconcile_invoice_line_amounts(
            [{'description': 'Business Email and Website', 'quantity': 1, 'unit_price': 199.99, 'amount': 199.99}],
            100,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['amount'], 100.0)
        self.assertEqual(items[0]['unit_price'], 100.0)
        self.assertNotEqual(items[0]['amount'], 199.99)

    def test_04_full_payment_send_thanks_not_request(self):
        paid = {'status': 'Paid', 'total': 370, 'amount_paid': 370, 'payment_timing': 'After work'}
        copy = payment_received_copy(paid)
        self.assertEqual(copy['kind'], 'complete')
        self.assertIn('complete payment', copy['title'].lower())
        self.assertEqual(invoice_send_email_kind(paid, received_more=False), 'payment_received')
        deposit = {'status': 'Partial Paid', 'total': 370, 'amount_paid': 185, 'payment_timing': 'Deposit'}
        self.assertEqual(payment_received_copy(deposit)['kind'], 'deposit')
        self.assertEqual(invoice_send_email_kind(deposit, received_more=False), 'payment_requested')
        self.assertEqual(invoice_send_email_kind(deposit, received_more=True), 'payment_received')

if __name__ == '__main__':
    unittest.main()
