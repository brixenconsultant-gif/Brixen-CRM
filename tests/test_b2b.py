import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, extract_session_token, seed_demo

class TestB2BAndCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()

    def test_01_catalog_products_api(self):
        st, _, res = make_request('/api/catalog/products')
        self.assertEqual(st, '200 OK')
        self.assertEqual(res.get('status'), 'success')
        self.assertTrue(isinstance(res.get('products'), list))

    def test_02_b2b_customer_classification(self):
        b2b_user = query_db("SELECT id, is_b2b, client_type, b2b_id FROM users WHERE is_b2b = 1 LIMIT 1;", one=True)
        if b2b_user:
            self.assertEqual(b2b_user['is_b2b'], 1)
            self.assertEqual(b2b_user['client_type'], 'B2B')
            self.assertTrue(b2b_user['b2b_id'].startswith('B2B-'))

if __name__ == '__main__':
    unittest.main()
