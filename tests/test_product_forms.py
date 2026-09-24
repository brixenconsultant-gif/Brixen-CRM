import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest

import product_catalog_config as catalog


class TestProductForms(unittest.TestCase):
    def test_website_products_use_short_form(self):
        for name in (
            'Business Website',
            'Business Email and Website',
            'Business Email',
            'Web Design & Marketing',
            'Website Design',
        ):
            profile = catalog.get_product_form_profile(name, 'Web Design & Marketing')
            config = catalog.get_product_form_config(name, 'Web Design & Marketing')
            field_ids = [f['id'] for f in config['fields']]
            self.assertEqual(profile, catalog.FORM_PROFILE_WEBSITE, name)
            self.assertFalse(catalog.service_needs_standard_kyc_docs(name, 'Web Design & Marketing'), name)
            self.assertIn('company_name', field_ids, name)
            self.assertIn('domain_name', field_ids, name)
            self.assertIn('access_email', field_ids, name)
            self.assertIn('access_email_password', field_ids, name)
            self.assertIn('logo', field_ids, name)
            logo = next(f for f in config['fields'] if f['id'] == 'logo')
            self.assertFalse(logo.get('required'), name)
            self.assertFalse(any(d.get('required') for d in config.get('document_requirements') or []), name)

    def test_formation_packages_keep_company_form(self):
        for name in ('Digital Package', 'Professional Package', 'All Inclusive Package', 'Company Formation Package'):
            self.assertEqual(catalog.get_product_form_profile(name), catalog.FORM_PROFILE_FORMATION, name)
            self.assertTrue(catalog.service_needs_standard_kyc_docs(name), name)

    def test_existing_company_services(self):
        cases = {
            'Registered Office Address': catalog.FORM_PROFILE_EXISTING_COMPANY,
            'Tide Business Bank': catalog.FORM_PROFILE_BANK,
            'Identity Verification': catalog.FORM_PROFILE_IDENTITY,
            'VAT Registration Service': catalog.FORM_PROFILE_COMPLIANCE,
            'Confirmation Statement Service': catalog.FORM_PROFILE_COMPLIANCE,
            'Virtual Numbers': catalog.FORM_PROFILE_COMMS,
            '24/7 Call Answering': catalog.FORM_PROFILE_COMMS,
        }
        for name, expected in cases.items():
            self.assertEqual(catalog.get_product_form_profile(name), expected, name)
            if expected in (catalog.FORM_PROFILE_COMMS, catalog.FORM_PROFILE_GENERIC) or name in (
                'VAT Registration Service',
                'Confirmation Statement Service',
                'Registered Office Address',
            ):
                self.assertFalse(catalog.service_needs_standard_kyc_docs(name), name)
            else:
                self.assertTrue(catalog.service_needs_standard_kyc_docs(name), name)

    def test_confirmation_statement_skips_director_dob(self):
        for name in (
            'Confirmation Statement Service',
            'Confirmation Statement',
            'Annual Compliance Filing',
        ):
            self.assertTrue(catalog.service_skips_director_dob(name), name)
            config = catalog.get_product_form_config(name)
            field_ids = [f.get('id') for f in config.get('fields') or []]
            self.assertNotIn('dob', field_ids, name)
            self.assertNotIn('director_dob', field_ids, name)
        self.assertFalse(catalog.service_skips_director_dob('Company Formation Package'))
        self.assertFalse(catalog.service_skips_director_dob('Tide Business Bank'))

    def test_registered_office_and_ad01_need_no_docs(self):
        for name in (
            'Registered Office Address',
            'AD01 Form',
            'AD01 - Change of Registered Office',
            'Change of registered office address',
        ):
            config = catalog.get_product_form_config(name)
            self.assertFalse(catalog.service_needs_standard_kyc_docs(name), name)
            self.assertFalse(any(d.get('required') for d in config.get('document_requirements') or []), name)

    def test_sole_trader_skips_companies_house(self):
        for name in ('Sole Trader', 'Sole Trade Registration', 'Sole Trader VAT'):
            self.assertTrue(catalog.service_is_sole_trader(name), name)
            self.assertFalse(catalog.service_needs_companies_house(name), name)
        self.assertTrue(catalog.service_needs_companies_house('Confirmation Statement Service'))
        self.assertFalse(catalog.service_is_sole_trader('Confirmation Statement Service'))

    def test_website_does_not_need_credentials_or_companies_house(self):
        self.assertFalse(catalog.service_needs_access_credentials('Business Email and Website'))
        self.assertFalse(catalog.service_needs_companies_house('Business Email and Website'))
        self.assertTrue(catalog.service_needs_companies_house('Registered Office Address'))
        self.assertTrue(catalog.service_needs_access_credentials('Tide Business Bank'))

    def test_personal_physical_bank_is_personal_only(self):
        for name in ('Personal Physical Banks', 'Personal Physical Bank', 'Personal Bank Account'):
            self.assertEqual(catalog.get_product_form_profile(name), catalog.FORM_PROFILE_PERSONAL_BANK, name)
            self.assertTrue(catalog.service_is_personal_bank(name), name)
            self.assertFalse(catalog.service_needs_standard_kyc_docs(name), name)
            self.assertFalse(catalog.service_needs_companies_house(name), name)
            self.assertFalse(catalog.service_needs_access_credentials(name), name)
            config = catalog.get_product_form_config(name)
            field_ids = [f.get('id') for f in config.get('fields') or []]
            self.assertIn('full_name', field_ids, name)
            self.assertIn('dob', field_ids, name)
            self.assertIn('home_address', field_ids, name)
            self.assertIn('email', field_ids, name)
            self.assertIn('phone', field_ids, name)
            self.assertNotIn('company_name', field_ids, name)
            self.assertNotIn('company_number', field_ids, name)
            self.assertNotIn('trading_proof', field_ids, name)
            docs = config.get('document_requirements') or []
            self.assertFalse(any(d.get('required') for d in docs), name)
            self.assertFalse(any(str(d.get('id') or '') in ('proof_of_address', 'bank_statement') for d in docs), name)
        self.assertEqual(catalog.get_product_form_profile('Tide Business Bank'), catalog.FORM_PROFILE_BANK)
        self.assertTrue(catalog.service_needs_standard_kyc_docs('Tide Business Bank'))
        self.assertTrue(catalog.service_needs_companies_house('Business Bank Account Assistance'))


if __name__ == '__main__':
    unittest.main()
