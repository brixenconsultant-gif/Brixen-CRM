import os
import sys
import json
import hmac
import hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, extract_session_token, webhook_secret_bytes, seed_demo

class TestOrders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()

    def test_01_client_orders_list(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'})
        token = extract_session_token(headers)
        
        st, hd, orders_res = make_request('/api/client/orders', cookie=f"session_token={token}")
        self.assertEqual(st, '200 OK')
        self.assertEqual(orders_res.get('status'), 'success')
        self.assertTrue(isinstance(orders_res.get('orders'), list))

    def test_02_admin_order_update(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
        admin_token = extract_session_token(headers)

        st1, _, res1 = make_request('/api/admin/orders/1', method='PUT', body={'status': 'Completed', 'progress_percent': 100}, cookie=f"session_token={admin_token}")
        self.assertEqual(st1, '200 OK')
        self.assertEqual(res1.get('status'), 'success')

        order1 = query_db("SELECT status, progress_percent FROM orders WHERE id = 1;", one=True)
        self.assertEqual(order1['status'], 'Completed')
        self.assertEqual(order1['progress_percent'], 100)

    def test_03_wordpress_webhook_order_sync(self):
        payload = {
            'event': 'order.created',
            'event_id': 'evt_test_order_001',
            'timestamp': 1750000000,
            'order': {
                'id': 99991,
                'number': '#GB99991FEB26',
                'status': 'pending',
                'total': '45.00',
                'line_items': [{'name': 'Company Formation Package'}],
                'billing': {
                    'first_name': 'Guest',
                    'last_name': 'User',
                    'email': 'guest.user@example.com'
                }
            }
        }
        raw_body = json.dumps(payload).encode('utf-8')
        sig = hmac.new(webhook_secret_bytes(), raw_body, hashlib.sha256).hexdigest()

        st, _, res = make_request(
            '/api/v1/wordpress/webhook',
            method='POST',
            body=payload,
            headers={'X-Brixen-Signature': sig, 'X-Brixen-Event': 'order.created'}
        )
        self.assertEqual(st, '200 OK')
        self.assertEqual(res.get('status'), 'success')

if __name__ == '__main__':
    unittest.main()
