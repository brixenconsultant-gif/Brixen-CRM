"""Unit checks for genuine business-data protection."""
import os
import unittest

os.environ.setdefault('CRM_TESTING', '1')

import data_protection as dp


class DataProtectionTests(unittest.TestCase):
    def test_official_company_numbers_lock(self):
        self.assertTrue(dp.company_is_genuine({'company_number': '16900714'}))
        self.assertTrue(dp.company_is_genuine({'company_number': 'SC123456'}))
        self.assertFalse(dp.company_is_genuine({'company_number': 'REG-15310'}))
        self.assertFalse(dp.company_is_genuine({'company_number': '13810022'}))  # known dummy seed

    def test_orders_with_woocommerce_id_lock(self):
        self.assertTrue(dp.order_is_genuine({'woocommerce_order_id': '15310', 'order_number': '#15310'}))
        self.assertFalse(dp.order_is_genuine({'order_number': '#GB1034001', 'woocommerce_order_id': ''}))

    def test_auto_delete_blocked_for_genuine_company(self):
        msg = dp.refuse_auto_delete_company({'company_number': '16900714'})
        self.assertTrue(msg)
        self.assertIsNone(dp.refuse_auto_delete_company({'company_number': 'REG-999'}))

    def test_manual_delete_requires_super_admin_force(self):
        company = {'company_number': '16900714', 'data_locked': 1}
        self.assertTrue(dp.refuse_manual_delete_company(company, force=False, actor={'role': 'ADMIN'}))
        self.assertTrue(dp.refuse_manual_delete_company(company, force=True, actor={'role': 'ADMIN'}))
        self.assertIsNone(dp.refuse_manual_delete_company(company, force=True, actor={'role': 'SUPER_ADMIN'}))

    def test_production_wipe_blocked(self):
        with self.assertRaises(RuntimeError):
            old = os.environ.get('CRM_ENV')
            os.environ['CRM_ENV'] = 'production'
            try:
                dp.assert_safe_to_wipe_database()
            finally:
                if old is None:
                    os.environ.pop('CRM_ENV', None)
                else:
                    os.environ['CRM_ENV'] = old


if __name__ == '__main__':
    unittest.main()
