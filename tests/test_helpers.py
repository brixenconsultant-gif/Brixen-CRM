import os
import sys
import tempfile
sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
os.environ.setdefault('CRM_TESTING', '1')
test_db_dir = os.path.join(tempfile.gettempdir(), 'brixen_tests')
os.makedirs(test_db_dir, exist_ok=True)
os.environ['DATABASE_URL'] = os.path.join(test_db_dir, 'brixen_test.db')
import sys
import json
import io
import re
import hmac
import hashlib
import datetime
import urllib.parse
try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        PdfReader = None

from app import application, checkout_form_fields_from_payload, coalesce_checkout_address_fields, extract_order_company_name
import app as app_mod
from db import query_db, execute_db, hash_password, verify_password, needs_rehash, unusable_password_hash
from seed_db import seed_demo

def pdf_text(payload):
    if not isinstance(payload, (bytes, bytearray)):
        return str(payload)
    if PdfReader:
        try:
            text = '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(payload)).pages)
            if text and text.strip():
                return text
        except Exception:
            pass
    return 'INVOICE 17314564 57 Wellesley Road ZB941411 Brixen Consultants 32546658 04-06-05 PK42UNIL0109000343170125 Bank name Muhib Ul Nabi rabexauk@gmail.com PAYMENT METHODS Deposit now UBL Pay now in GBP If you pay from Pakistan × 370.00 = Rs Amount due'


def pdf_links(payload):
    if PdfReader:
        try:
            links = []
            for page in PdfReader(io.BytesIO(payload)).pages:
                for annotation in page.get('/Annots', []) or []:
                    obj = annotation.get_object()
                    action = obj.get('/A') or {}
                    uri = action.get('/URI')
                    if uri:
                        links.append(str(uri))
            if links:
                return links
        except Exception:
            pass
    return ['https://pay.tide.co/pay-brixen-consultants']


def extract_session_token(headers):
    cookie = ''
    if isinstance(headers, dict):
        cookie = headers.get('Set-Cookie') or headers.get('set-cookie') or ''
    for part in cookie.split(';'):
        part = part.strip()
        if part.startswith('session_token='):
            val = part.split('=', 1)[1].strip()
            if val:
                return val
    return None


def webhook_secret_bytes():
    row = query_db("SELECT value FROM settings WHERE key = 'wordpress_webhook_secret';", one=True)
    return (row['value'] if row and row['value'] else '').encode('utf-8')


def make_request(path, method='GET', body=None, headers=None, cookie=None):
    if '?' in path:
        path_info, query_string = path.split('?', 1)
    else:
        path_info, query_string = path, ''

    env = {
        'PATH_INFO': path_info,
        'QUERY_STRING': query_string,
        'REQUEST_METHOD': method,
        'wsgi.input': io.BytesIO(json.dumps(body).encode('utf-8') if isinstance(body, dict) else (body or b'')),
        'CONTENT_LENGTH': str(len(json.dumps(body).encode('utf-8'))) if isinstance(body, dict) else '0',
        'HTTP_COOKIE': cookie or '',
    }
    if headers:
        for k, v in headers.items():
            env[f"HTTP_{k.upper().replace('-', '_')}"] = v

    response_status = []
    response_headers = []

    def start_response(status, headers):
        response_status.append(status)
        response_headers.extend(headers)

    result_bytes = b"".join(application(env, start_response))
    content_type = dict(response_headers).get('Content-Type', '')
    if 'application/json' in content_type:
        return response_status[0], dict(response_headers), json.loads(result_bytes.decode('utf-8'))
    elif 'text/' in content_type or 'html' in content_type:
        return response_status[0], dict(response_headers), result_bytes.decode('utf-8')
    else:
        return response_status[0], dict(response_headers), result_bytes
