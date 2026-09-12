import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from test_helpers import make_request, query_db, execute_db, extract_session_token, hash_password, verify_password, needs_rehash, unusable_password_hash, seed_demo

class TestAuthentication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed_demo()

    def test_01_auth_login_client(self):
        status, headers, body = make_request(
            '/api/auth/login',
            method='POST',
            body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'}
        )
        self.assertEqual(status, '200 OK')
        self.assertEqual(body.get('status'), 'success')
        self.assertEqual(body.get('user', {}).get('email'), 'client1@acmecorp.co.uk')
        self.assertNotIn('password_hash', body.get('user', {}))

    def test_02_auth_me_profile(self):
        status, headers, body = make_request(
            '/api/auth/login',
            method='POST',
            body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'}
        )
        token = extract_session_token(headers)
        self.assertIsNotNone(token)
        
        st, hd, profile = make_request('/api/auth/me', cookie=f"session_token={token}")
        self.assertEqual(st, '200 OK')
        self.assertEqual(profile.get('user', {}).get('role'), 'ADMIN')
        self.assertNotIn('password_hash', profile.get('user', {}))
        self.assertNotIn('session_token', profile.get('user', {}))

    def test_03_argon2_password_hashing(self):
        h = hash_password('SecureSecret123!')
        self.assertTrue(h.startswith('$argon2id$'))
        self.assertTrue(verify_password('SecureSecret123!', h))
        self.assertFalse(verify_password('WrongSecret', h))

    def test_04_legacy_sha256_migration(self):
        import hashlib
        legacy_hash = hashlib.sha256('LegacyPass123!'.encode('utf-8')).hexdigest()
        execute_db("INSERT OR REPLACE INTO users (id, email, full_name, password_hash, role, status) VALUES (999, 'legacy.user@example.com', 'Legacy User', ?, 'CLIENT', 'Active');", (legacy_hash,))
        
        user_before = query_db("SELECT password_hash FROM users WHERE id = 999;", one=True)
        self.assertTrue(needs_rehash(user_before['password_hash']))
        
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'legacy.user@example.com', 'password': 'LegacyPass123!'})
        self.assertEqual(status, '200 OK')
        
        user_after = query_db("SELECT password_hash FROM users WHERE id = 999;", one=True)
        self.assertTrue(user_after['password_hash'].startswith('$argon2id$'))
        self.assertFalse(needs_rehash(user_after['password_hash']))

    def test_05_unusable_password_hashes(self):
        unusable = unusable_password_hash()
        self.assertTrue(unusable.startswith('$argon2id$'))
        self.assertFalse(verify_password('ClientPass123!', unusable))

    def test_06_session_revocation(self):
        status, headers, body = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
        token = extract_session_token(headers)
        
        st1, _, _ = make_request('/api/auth/me', cookie=f"session_token={token}")
        self.assertEqual(st1, '200 OK')
        
        st2, _, _ = make_request('/api/auth/logout', method='POST', cookie=f"session_token={token}")
        self.assertEqual(st2, '200 OK')
        
        st3, _, _ = make_request('/api/auth/me', cookie=f"session_token={token}")
        self.assertEqual(st3, '401 Unauthorized')

if __name__ == '__main__':
    unittest.main()
