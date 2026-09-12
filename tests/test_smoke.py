import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, seed_demo

class TestSmokeAndSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()

    def test_01_db_record_counts(self):
        orders_cnt = query_db("SELECT COUNT(*) as c FROM orders;", one=True)['c']
        users_cnt = query_db("SELECT COUNT(*) as c FROM users;", one=True)['c']
        companies_cnt = query_db("SELECT COUNT(*) as c FROM companies;", one=True)['c']
        
        self.assertGreaterEqual(orders_cnt, 100, "Expected at least 100 orders seeded.")
        self.assertGreaterEqual(users_cnt, 5)
        self.assertGreaterEqual(companies_cnt, 5)

    def test_02_target_order_check(self):
        target_order = query_db("SELECT * FROM orders WHERE order_number = '#GB103449JUL26';", one=True)
        self.assertIsNotNone(target_order, "Target order #GB103449JUL26 not found.")
        self.assertEqual(target_order['progress_percent'], 50)

    def test_03_wsgi_homepage_shell(self):
        status, headers, body = make_request('/')
        self.assertEqual(status, "200 OK")
        self.assertIn("<title>Brixen Consultant", body)
        self.assertIn('class="auth-pending"', body)
        self.assertIn('id="app-container" hidden', body)

    def test_04_static_assets(self):
        for asset in ('/static/css/styles.css', '/static/js/app.js', '/static/img/brixen-logo.png'):
            st, hd, asset_body = make_request(asset)
            self.assertEqual(st, "200 OK", f"{asset} expected 200, got {st}")
            self.assertTrue(bool(asset_body), f"{asset} returned empty body")

    def test_05_path_traversal_protection(self):
        for traversal in (
            '/static/../hypetex.db',
            '/static/js/../../hypetex.db',
            '/static/../app.py',
            '/static/../schema.sql',
            '/static/../seed_db.py',
            '/static/../.env.example',
        ):
            st, hd, trav_body = make_request(traversal)
            self.assertIn(st, ("403 Forbidden", "404 Not Found"))
            raw = trav_body if isinstance(trav_body, (bytes, bytearray)) else str(trav_body).encode('utf-8', 'replace')
            self.assertFalse(raw.startswith(b'SQLite format 3'))
            lowered = raw.lower()
            self.assertNotIn(b'hypetex.db', lowered)

if __name__ == '__main__':
    unittest.main()
