import sqlite3
import os
import json
import hashlib
import hmac
import secrets
import re
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, VerificationError

try:
    from argon2.exceptions import InvalidHashError
except ImportError:
    from argon2.exceptions import InvalidHash as InvalidHashError

DB_PATH = os.environ.get('DATABASE_URL') or os.path.join(os.path.dirname(__file__), 'hypetex.db')
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'schema.sql')

_ARGON2_HASHER = PasswordHasher(type=Type.ID)
_LEGACY_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_DUMMY_ARGON2_HASH = None


def _is_legacy_sha256(stored_hash):
    return isinstance(stored_hash, str) and bool(_LEGACY_SHA256_RE.fullmatch(stored_hash))


def _is_argon2id(stored_hash):
    return isinstance(stored_hash, str) and stored_hash.startswith('$argon2id$')


def _dummy_argon2_verify(password):
    global _DUMMY_ARGON2_HASH
    if _DUMMY_ARGON2_HASH is None:
        _DUMMY_ARGON2_HASH = hash_password(secrets.token_urlsafe(32))
    verify_password(password if password is not None else '', _DUMMY_ARGON2_HASH)


def hash_password(password):
    if password is None:
        password = ''
    if not isinstance(password, str):
        password = str(password)
    return _ARGON2_HASHER.hash(password)


def verify_password(password, stored_hash):
    if stored_hash is None:
        _dummy_argon2_verify(password)
        return False
    if password is None:
        password = ''
    if not isinstance(password, str):
        password = str(password)
    if not isinstance(stored_hash, str) or not stored_hash:
        return False
    if _is_argon2id(stored_hash):
        try:
            return _ARGON2_HASHER.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError, ValueError):
            return False
    if _is_legacy_sha256(stored_hash):
        digest = hashlib.sha256(password.encode('utf-8')).hexdigest()
        return hmac.compare_digest(digest, stored_hash)
    return False


def needs_rehash(stored_hash):
    if _is_legacy_sha256(stored_hash):
        return True
    if _is_argon2id(stored_hash):
        try:
            return _ARGON2_HASHER.check_needs_rehash(stored_hash)
        except (InvalidHashError, TypeError, ValueError):
            return False
    return False


def unusable_password_hash():
    return hash_password(secrets.token_urlsafe(32))


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn

def init_db():
    conn = get_db()
    with open(SCHEMA_PATH, 'r') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

def query_db(query, args=(), one=False):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, args)
    rv = [dict(row) for row in cur.fetchall()]
    conn.close()
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, args)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully.")
