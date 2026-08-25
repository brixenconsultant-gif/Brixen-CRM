import os
import sys
import json
import re
import urllib.parse
import urllib.request
import urllib.error
import base64
import mimetypes
from wsgiref.simple_server import make_server
from db import query_db, execute_db, hash_password, verify_password, needs_rehash, unusable_password_hash, get_db, ensure_schema

PORT = int(os.environ.get('PORT', '5050'))
HOST = os.environ.get('HOST', '127.0.0.1')
PORTAL_HOSTS = frozenset({
    '127.0.0.1',
    'localhost',
    '::1',
    'portal.brixenconsultants.com',
})
ALLOWED_CORS_ORIGINS = frozenset({
    'https://brixenconsultants.com',
    'https://www.brixenconsultants.com',
    'https://portal.brixenconsultants.com',
    'http://portal.brixenconsultants.com',
    'http://portal.brixenconsultants.com:5050',
    'https://portal.brixenconsultants.com:5050',
    'http://127.0.0.1:5050',
    'http://localhost:5050',
})
INTERNAL_STAFF_ROLES = ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')

import uuid
import datetime
import calendar
import hmac
import hashlib

# Storage directory for private client files (P2)
STORAGE_DIR = os.path.join(BASE_DIR, 'storage')
os.makedirs(STORAGE_DIR, exist_ok=True)

# P1: Persistent Database-Backed Session Management
def create_db_session(user_id):
    token = str(uuid.uuid4())
    execute_db("""
        INSERT INTO user_sessions (session_token, user_id, expires_at)
        VALUES (?, ?, datetime('now', '+30 days'));
    """, (token, user_id))
    return token

def revoke_db_session(token):
    if not token: return
    execute_db("""
        UPDATE user_sessions SET revoked_at = datetime('now') WHERE session_token = ?;
    """, (token,))

def get_current_user(environ):
    cookie_str = environ.get('HTTP_COOKIE', '')
    token = None
    if 'session_token=' in cookie_str:
        for c in cookie_str.split(';'):
            c = c.strip()
            if c.startswith('session_token='):
                token = c.split('=', 1)[1]
                break
    if not token:
        auth_hdr = environ.get('HTTP_AUTHORIZATION', '')
        if auth_hdr.startswith('Bearer '):
            token = auth_hdr.split(' ', 1)[1]
            
    if token:
        session_rec = query_db("""
            SELECT s.session_token, u.* 
            FROM user_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = ? 
              AND s.revoked_at IS NULL 
              AND s.expires_at > datetime('now');
        """, (token,), one=True)
        
        if session_rec:
            # Update last activity timestamp
            execute_db("UPDATE user_sessions SET last_activity_at = datetime('now') WHERE session_token = ?;", (token,))
            return dict(session_rec)
    return None

def client_portal_login_ready(client):
    if not client:
        return False
    wp_id = str(client.get('wordpress_user_id') or '').strip()
    return not wp_id.startswith('local_client_')


# P4: Email Notification Gateway Abstraction
def smtp_settings():
    rows = query_db("""
        SELECT key, value FROM settings
        WHERE key IN ('smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from');
    """) or []
    db = {row['key']: str(row.get('value') or '').strip() for row in rows}
    host = db.get('smtp_host') or os.environ.get('SMTP_HOST', '').strip()
    port = db.get('smtp_port') or os.environ.get('SMTP_PORT', '587').strip() or '587'
    user = db.get('smtp_user') or os.environ.get('SMTP_USER', '').strip()
    password = db.get('smtp_pass') or os.environ.get('SMTP_PASS', '').strip()
    from_addr = db.get('smtp_from') or os.environ.get('SMTP_FROM', '').strip()
    if not from_addr:
        from_addr = user or 'notifications@brixenconsultants.com'
    return {
        'host': host,
        'port': port,
        'user': user,
        'password': password,
        'from_addr': from_addr,
    }


def smtp_configured():
    cfg = smtp_settings()
    return bool(cfg['host'] and cfg['user'] and cfg['password'])


class EmailService:
    @staticmethod
    def send_notification_email(recipient_email, subject, body_text, body_html=None):
        cfg = smtp_settings()
        smtp_host = cfg['host']
        smtp_port = cfg['port']
        smtp_user = cfg['user']
        smtp_pass = cfg['password']
        smtp_from = cfg['from_addr']

        if not smtp_host or not smtp_user or not smtp_pass:
            print(f"[EmailService Log] Email to {recipient_email} ('{subject}') - SMTP not configured.")
            return False, "SMTP not configured"
            
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        try:
            if body_html:
                msg = MIMEMultipart('alternative')
                msg.attach(MIMEText(body_text or '', 'plain', 'utf-8'))
                msg.attach(MIMEText(body_html, 'html', 'utf-8'))
            else:
                msg = MIMEText(body_text or '', 'plain', 'utf-8')
            msg['Subject'] = subject
            msg['From'] = smtp_from
            msg['To'] = recipient_email
            
            port = int(smtp_port)
            if port == 465:
                with smtplib.SMTP_SSL(smtp_host, port) as server:
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_from, [recipient_email], msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_from, [recipient_email], msg.as_string())
            print(f"[EmailService Success] Dispatched email to {recipient_email}")
            return True, "Delivered"
        except Exception as err:
            print(f"[EmailService Error] Failed to send email: {err}")
            return False, str(err)

def json_response(start_response, data, status="200 OK", extra_headers=None):
    body = json.dumps(data).encode('utf-8')
    headers = [
        ('Content-Type', 'application/json; charset=utf-8'),
        ('Content-Length', str(len(body))),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    start_response(status, headers)
    return [body]

def cookie_should_be_secure(environ=None):
    """Use Secure cookies on HTTPS. Omit Secure on local HTTP so the browser can store the session."""
    override = os.environ.get('COOKIE_SECURE')
    if override is not None and str(override).strip() != '':
        return str(override).strip().lower() in ('1', 'true', 'yes', 'on')
    if not environ:
        return True
    forwarded = (environ.get('HTTP_X_FORWARDED_PROTO') or '').split(',')[0].strip().lower()
    scheme = forwarded or (environ.get('wsgi.url_scheme') or 'http').lower()
    if scheme == 'https' or environ.get('HTTPS') in ('on', '1'):
        return True
    host = (environ.get('HTTP_HOST') or environ.get('SERVER_NAME') or '').split(':')[0].lower()
    if host in PORTAL_HOSTS:
        return False
    return True


def _cookie_flag_suffix(environ=None):
    flags = 'Path=/; HttpOnly; SameSite=Lax'
    if cookie_should_be_secure(environ):
        flags += '; Secure'
    return flags


def session_cookie_header(token=None, clear=False, environ=None):
    flags = _cookie_flag_suffix(environ)
    if clear:
        return f'session_token=; {flags}; Expires=Thu, 01 Jan 1970 00:00:00 GMT'
    return f'session_token={token}; {flags}'


def origin_cookie_header(token=None, clear=False, environ=None):
    flags = _cookie_flag_suffix(environ)
    if clear:
        return f'origin_session=; {flags}; Expires=Thu, 01 Jan 1970 00:00:00 GMT'
    return f'origin_session={token}; {flags}'

def cookie_value(environ, name):
    cookie_str = environ.get('HTTP_COOKIE', '')
    prefix = name + '='
    for part in cookie_str.split(';'):
        part = part.strip()
        if part.startswith(prefix):
            return part.split('=', 1)[1]
    return None

def session_user_from_token(token):
    if not token:
        return None
    return query_db("""
        SELECT s.session_token, u.*
        FROM user_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.session_token = ?
          AND s.revoked_at IS NULL
          AND s.expires_at > datetime('now');
    """, (token,), one=True)

def public_me_user(user, impersonated=False):
    if not user:
        return None
    src = dict(user)
    return {
        'id': src.get('id'),
        'wordpress_user_id': src.get('wordpress_user_id'),
        'email': src.get('email'),
        'full_name': src.get('full_name'),
        'phone': src.get('phone'),
        'country': src.get('country'),
        'address': src.get('address'),
        'status': src.get('status'),
        'role': src.get('role'),
        'department': (departments_from_user(src) or [None])[0],
        'departments': departments_from_user(src),
        'impersonated': bool(impersonated)
    }

def can_impersonate_user(actor, target):
    if not actor or not target:
        return False
    if actor.get('id') == target.get('id'):
        return False
    if target.get('status') != 'Active':
        return False
    if target.get('role') not in INTERNAL_STAFF_ROLES:
        return False
    if not check_permission(actor, 'staff.manage'):
        return False
    if actor.get('role') == 'SUPER_ADMIN':
        return True
    return ROLE_RANK.get(target.get('role'), 99) < ROLE_RANK.get(actor.get('role'), 0)

def cors_allowed_origin(environ):
    origin = environ.get('HTTP_ORIGIN', '')
    if origin in ALLOWED_CORS_ORIGINS:
        return origin
    return None

def is_internal_staff(user):
    return bool(user and user.get('role') in INTERNAL_STAFF_ROLES)

def _is_path_inside(root_dir, target_path):
    root_real = os.path.realpath(root_dir)
    target_real = os.path.realpath(target_path)
    try:
        common = os.path.commonpath([root_real, target_real])
    except ValueError:
        return False
    return common == root_real

def serve_static(environ, start_response, filepath, allowed_root=None):
    if allowed_root and not _is_path_inside(allowed_root, filepath):
        start_response("403 Forbidden", [('Content-Type', 'text/plain; charset=utf-8')])
        return [b"Forbidden"]
    if not os.path.exists(filepath) or os.path.isdir(filepath):
        start_response("404 Not Found", [('Content-Type', 'text/plain')])
        return [b"404 File Not Found"]
    
    ctype, _ = mimetypes.guess_type(filepath)
    if not ctype:
        ctype = 'application/octet-stream'
        
    with open(filepath, 'rb') as f:
        content = f.read()
        
    headers = [
        ('Content-Type', ctype),
        ('Content-Length', str(len(content))),
    ]
    if filepath.endswith('.html') or filepath.endswith('.js') or filepath.endswith('.css'):
        headers.append(('Cache-Control', 'no-store, no-cache, must-revalidate'))
        headers.append(('Pragma', 'no-cache'))
    start_response("200 OK", headers)
    return [content]

def parse_body(environ):
    try:
        content_length = int(environ.get('CONTENT_LENGTH', 0))
    except (ValueError, TypeError):
        content_length = 0
    if content_length > 0:
        raw_body = environ['wsgi.input'].read(content_length).decode('utf-8')
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError:
            return dict(urllib.parse.parse_qsl(raw_body))
    return {}

def log_activity(user, action, entity_type=None, entity_id=None, details=None):
    uid = user['id'] if user else None
    email = user['email'] if user else 'SYSTEM'
    execute_db("""
        INSERT INTO activity_logs (user_id, user_email, action, entity_type, entity_id, details)
        VALUES (?, ?, ?, ?, ?, ?);
    """, (uid, email, action, entity_type, entity_id, details))

import hmac
import hashlib

# Storage directory for private client files
STORAGE_DIR = os.environ.get('STORAGE_PATH') or os.path.join(BASE_DIR, 'storage')
os.makedirs(STORAGE_DIR, exist_ok=True)

def verify_webhook_signature(raw_body_bytes, signature_hdr):
    if not signature_hdr:
        return False
    row = query_db("SELECT value FROM settings WHERE key = 'wordpress_webhook_secret';", one=True)
    secret = (row['value'] if row and row['value'] else None) or os.environ.get('WORDPRESS_WEBHOOK_SECRET')
    if not secret:
        return False
    clean_sig = signature_hdr.replace('sha256=', '').strip()
    expected = hmac.new(secret.encode('utf-8'), raw_body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(clean_sig, expected)


def wordpress_integration_secret():
    row = query_db("SELECT value FROM settings WHERE key = 'wordpress_webhook_secret';", one=True)
    return (row['value'] if row and row['value'] else None) or os.environ.get('WORDPRESS_WEBHOOK_SECRET')


def verify_wordpress_user_signature(wp_id, email, timestamp, signature, max_age_seconds=900):
    secret = wordpress_integration_secret()
    if not secret:
        return False, 'Website integration is not configured'
    wp_id = str(wp_id or '').strip()
    email = str(email or '').strip().lower()
    timestamp = str(timestamp or '').strip()
    signature = str(signature or '').strip().replace('sha256=', '')
    if not wp_id or not timestamp or not signature:
        return False, 'Missing signed website credentials'
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError):
        return False, 'Invalid timestamp'
    if abs(int(datetime.datetime.now().timestamp()) - ts_int) > max_age_seconds:
        return False, 'Signed link expired. Refresh Messages & Files.'
    expected = hmac.new(
        secret.encode('utf-8'),
        f"{wp_id}|{email}|{timestamp}".encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    try:
        ok = hmac.compare_digest(signature, expected)
    except (TypeError, ValueError):
        ok = False
    if not ok:
        return False, 'Invalid website signature'
    return True, None


def find_client_for_wordpress(wp_id, email):
    wp_id = str(wp_id or '').strip()
    email = str(email or '').strip().lower()
    client = None
    if wp_id:
        client = query_db(
            "SELECT * FROM users WHERE wordpress_user_id = ? AND role = 'CLIENT';",
            (wp_id,),
            one=True,
        )
    if not client and email:
        client = query_db(
            "SELECT * FROM users WHERE LOWER(email) = ? AND role = 'CLIENT' ORDER BY id DESC;",
            (email,),
            one=True,
        )
    return client


def public_wordpress_document(row, download_url=None):
    doc = public_document(row, for_client=True) or {}
    return {
        'id': doc.get('id'),
        'name': doc.get('name'),
        'category': doc.get('category') or 'Posted Documents',
        'file_type': doc.get('file_type'),
        'file_size': doc.get('file_size'),
        'company_name': row.get('company_name') if row else None,
        'created_at': doc.get('created_at') or doc.get('shared_at'),
        'message': (row.get('review_notes') if row else None) or 'A document is ready for you.',
        'download_url': download_url,
    }


PERMISSION_ACCESS = {
    'clients.view': ('New Signups',),
    'clients.edit': ('New Signups',),
    'clients.create': ('New Signups',),
    'orders.view': ('Orders', 'Support'),
    'orders.edit': ('Orders', 'Support'),
    'documents.view': ('Documents', 'Compliance'),
    'documents.upload': ('Documents', 'Compliance'),
    'documents.send': ('Documents', 'Compliance'),
    'invoices.view': ('Accounts',),
}

def check_permission(user, permission_name):
    if not user:
        return False
    if user['role'] in ('SUPER_ADMIN', 'ADMIN'):
        return True
    row = query_db("""
        SELECT 1 FROM role_permissions rp
        JOIN roles r ON rp.role_id = r.id
        JOIN permissions p ON rp.permission_id = p.id
        WHERE r.name = ? AND p.name = ?;
    """, (user['role'], permission_name), one=True)
    if not row:
        return False
    if user['role'] != 'STAFF':
        return True
    needed = PERMISSION_ACCESS.get(permission_name)
    if not needed:
        return True
    granted = set(departments_from_user(user))
    return bool(granted.intersection(needed))

def require_permission(start_response, user, permission_name):
    if not user:
        return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
    if not check_permission(user, permission_name):
        return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
    return None


def can_view_admin_dashboard(user):
    return bool(user and user.get('role') in ('SUPER_ADMIN', 'ADMIN', 'MANAGER'))

def can_view_revenue(user):
    return bool(user and user.get('role') in ('SUPER_ADMIN', 'ADMIN'))

TASK_PRIORITIES = ('Low', 'Medium', 'High', 'Urgent')
TASK_STATUSES = ('Open', 'In Progress', 'Completed', 'Cancelled')
TASK_ASSIGNABLE_ROLES = ('STAFF', 'MANAGER', 'ADMIN', 'SUPER_ADMIN')
STAFF_DEPARTMENTS = (
    'New Signups',
    'Orders',
    'Documents',
    'Support',
    'Compliance',
    'Accounts',
    'General',
)
USER_ROLES = ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF', 'CLIENT')
USER_STATUSES = ('Active', 'Suspended', 'Pending')
ROLE_RANK = {
    'CLIENT': 0,
    'STAFF': 1,
    'MANAGER': 2,
    'ADMIN': 3,
    'SUPER_ADMIN': 4,
}
STAFF_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
STAFF_MIN_PASSWORD_LEN = 10
STAFF_PUBLIC_FIELDS = (
    'id', 'wordpress_user_id', 'email', 'full_name', 'phone', 'country',
    'role', 'department', 'status', 'avatar_url', 'created_at'
)
TASK_DETAIL_SQL = """
    SELECT t.*,
        a.full_name AS assigned_staff_name,
        a.email AS assigned_staff_email,
        cr.full_name AS created_by_name,
        cl.full_name AS client_name,
        cl.email AS client_email,
        c.name AS company_name,
        o.order_number
    FROM tasks t
    LEFT JOIN users a ON t.assigned_staff_id = a.id
    LEFT JOIN users cr ON t.created_by_id = cr.id
    LEFT JOIN users cl ON t.client_id = cl.id
    LEFT JOIN companies c ON t.company_id = c.id
    LEFT JOIN orders o ON t.order_id = o.id
"""

def task_auth_error(start_response, user, permission_name):
    if not user:
        return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
    if not check_permission(user, permission_name):
        return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
    return None

def normalize_department(value):
    if value is None:
        return None, None
    department = str(value).strip()
    if not department:
        return None, None
    if department not in STAFF_DEPARTMENTS:
        return None, 'Department must be one of: ' + ', '.join(STAFF_DEPARTMENTS)
    return department, None


def parse_departments(value):
    if value is None or value == '' or value == []:
        return [], None
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        text = str(value).strip()
        if not text:
            return [], None
        if text.startswith('['):
            try:
                parsed = json.loads(text)
                items = parsed if isinstance(parsed, list) else [text]
            except Exception:
                items = [text]
        else:
            items = [part.strip() for part in text.split(',') if part.strip()]
    out = []
    for item in items:
        dept, err = normalize_department(item)
        if err:
            return None, err
        if dept and dept not in out:
            out.append(dept)
    return out, None


def store_departments(departments):
    return json.dumps(departments) if departments else None


def departments_from_user(user):
    depts, _ = parse_departments((user or {}).get('department'))
    extra, _ = parse_departments((user or {}).get('departments'))
    merged = []
    for dept in (depts or []) + (extra or []):
        if dept not in merged:
            merged.append(dept)
    return merged


def staff_can_access_task(user, task):
    if not user or not task:
        return False
    if user['role'] == 'STAFF':
        if task.get('assigned_staff_id') == user['id']:
            return True
        staff_depts = departments_from_user(user)
        task_dept = (task.get('department') or '').strip()
        return (not task.get('assigned_staff_id')) and bool(task_dept) and task_dept in staff_depts
    if user['role'] in ('SUPER_ADMIN', 'ADMIN', 'MANAGER'):
        return True
    return False

def to_optional_int(value, field_name):
    if value is None or value == '' or value == 'null':
        return None, None
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, f'Invalid {field_name}'

def fetch_task(task_id):
    return query_db(TASK_DETAIL_SQL + " WHERE t.id = ?;", (task_id,), one=True)

def public_staff_user(row):
    if not row:
        return None
    src = dict(row)
    public = {key: src.get(key) for key in STAFF_PUBLIC_FIELDS}
    depts = departments_from_user(src)
    public['departments'] = depts
    public['department'] = depts[0] if depts else None
    return public


def creatable_roles_for(user):
    if not user:
        return ()
    actor_rank = ROLE_RANK.get(user.get('role'))
    if actor_rank is None:
        return ()
    allowed = []
    for role in USER_ROLES:
        needed = 'clients.create' if role == 'CLIENT' else 'staff.manage'
        if not check_permission(user, needed):
            continue
        target_rank = ROLE_RANK[role]
        if user['role'] == 'SUPER_ADMIN' or target_rank < actor_rank:
            allowed.append(role)
    return tuple(allowed)


def creatable_staff_roles_for(user):
    return tuple(role for role in creatable_roles_for(user) if role != 'CLIENT')


ORDER_STATUSES = (
    'Pending', 'Processing', 'In Progress', 'Completed',
    'Cancelled', 'Refunded', 'Pending Verification'
)
ORDER_PAYMENT_MODES = (
    'PKR(Bank Transfer)',
    'GBP(Bank Transfer)',
    'Website Charge',
)


def can_delete_orders(user):
    return bool(user and user.get('role') in ('SUPER_ADMIN', 'ADMIN'))


def staff_can_access_admin_order(user, order):
    if not user or not order:
        return False
    if user.get('role') in ('SUPER_ADMIN', 'ADMIN', 'STAFF'):
        return True
    if user.get('role') == 'MANAGER':
        assignee_id = order.get('assigned_staff_id')
        if assignee_id in (None, user['id']):
            return True
        assignee = query_db("SELECT role FROM users WHERE id = ?;", (assignee_id,), one=True)
        return bool(assignee and assignee.get('role') == 'STAFF')
    return False


def remove_stored_document_files(file_paths):
    storage_root = os.path.abspath(STORAGE_DIR)
    for path in file_paths:
        if not path:
            continue
        if not _is_path_inside(storage_root, path):
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def validate_task_assignee(assigned_staff_id):
    if assigned_staff_id is None:
        return True, None
    rec = query_db("SELECT id, role, status FROM users WHERE id = ?;", (assigned_staff_id,), one=True)
    if not rec:
        return False, 'Assigned staff not found'
    if rec['role'] not in TASK_ASSIGNABLE_ROLES:
        return False, 'Assignee must be an internal staff user'
    if rec['status'] != 'Active':
        return False, 'Assignee is not an active user'
    return True, None

def validate_task_links(client_id, company_id, order_id):
    resolved_client = client_id
    resolved_company = company_id
    resolved_order = order_id

    if order_id is not None:
        order = query_db("SELECT id, user_id, company_id FROM orders WHERE id = ?;", (order_id,), one=True)
        if not order:
            return False, 'Order not found', None, None, None
        if resolved_client is None:
            resolved_client = order['user_id']
        elif int(resolved_client) != int(order['user_id']):
            return False, 'Order does not belong to the specified client', None, None, None
        if resolved_company is None:
            resolved_company = order['company_id']
        elif order['company_id'] is not None and int(resolved_company) != int(order['company_id']):
            return False, 'Order does not belong to the specified company', None, None, None

    if resolved_company is not None:
        company = query_db("SELECT id, user_id FROM companies WHERE id = ?;", (resolved_company,), one=True)
        if not company:
            return False, 'Company not found', None, None, None
        if resolved_client is None:
            resolved_client = company['user_id']
        elif int(resolved_client) != int(company['user_id']):
            return False, 'Company does not belong to the specified client', None, None, None

    if resolved_client is not None:
        client = query_db("SELECT id, role FROM users WHERE id = ?;", (resolved_client,), one=True)
        if not client:
            return False, 'Client not found', None, None, None
        if client['role'] != 'CLIENT':
            return False, 'client_id must refer to a CLIENT user', None, None, None

    return True, None, resolved_client, resolved_company, resolved_order

def notify_staff_task(recipient_id, actor_id, title, message, ntype):
    if not recipient_id or recipient_id == actor_id:
        return
    recipient = query_db("SELECT id, email, full_name, role FROM users WHERE id = ?;", (recipient_id,), one=True)
    if not recipient or recipient['role'] == 'CLIENT':
        return
    execute_db("""
        INSERT INTO notifications (user_id, title, message, type, link)
        VALUES (?, ?, ?, ?, ?);
    """, (recipient_id, title, message, ntype, '#admin-tasks'))
    EmailService.send_notification_email(
        recipient['email'],
        f"{title} - Brixen Consultants Portal",
        f"Hello {recipient['full_name']},\n\n{message}\n\nLog in to the Brixen Portal to review: {portal_page_url()}"
    )

def portal_base_url():
    return (os.environ.get('CRM_BASE_URL') or 'https://portal.brixenconsultants.com').rstrip('/')


def portal_page_url():
    return (os.environ.get('PORTAL_PAGE_URL') or portal_base_url()).rstrip('/')


def client_website_url():
    """Where customers should open their account (official website), not the staff CRM."""
    configured = (os.environ.get('CLIENT_PANEL_URL') or '').strip()
    if configured:
        return configured.rstrip('/')
    row = query_db("SELECT value FROM settings WHERE key = 'client_panel_url';", one=True)
    if row and str(row.get('value') or '').strip():
        return str(row['value']).strip().rstrip('/')
    return 'https://brixenconsultants.com/client-panel/customer-messages'


def brand_settings():
    keys = ('company_name', 'logo_url', 'primary_color', 'support_email', 'support_phone')
    rows = query_db(
        f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(keys))});",
        keys,
    ) or []
    db = {row['key']: str(row.get('value') or '').strip() for row in rows}
    support_email = db.get('support_email') or 'contact@brixenconsultants.com'
    # Prefer the live contact mailbox over legacy support@ addresses.
    if support_email.lower() in (
        'support@brixenconsultant.co.uk',
        'support@brixenconsultants.com',
        'support@brixenconsultants.co.uk',
    ):
        support_email = 'contact@brixenconsultants.com'
    support_phone = db.get('support_phone') or '447360515317'
    if support_phone in ('+44 20 7946 0912', '020 7946 0912', ''):
        support_phone = '447360515317'
    return {
        'company_name': db.get('company_name') or 'Brixen Consultants',
        'logo_url': db.get('logo_url') or '/static/img/brixen-logo.png',
        'primary_color': db.get('primary_color') or '#003971',
        'secondary_color': '#006cff',
        'accent_color': '#008075',
        'support_email': support_email,
        'support_phone': support_phone,
    }


def brand_logo_absolute_url(logo_url=None):
    url = (logo_url or brand_settings()['logo_url']).strip() or '/static/img/brixen-logo.png'
    if url.startswith('http://') or url.startswith('https://'):
        return url
    if not url.startswith('/'):
        url = '/' + url
    return f"{portal_base_url()}{url}"


def html_escape(value):
    text = '' if value is None else str(value)
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&#39;')
    )


def build_client_notification_email(
    client,
    headline,
    message,
    *,
    subject=None,
    badge='Account update',
    alert_label='Important notification',
    cta_label='Open Client Panel',
    cta_url=None,
    detail_title=None,
    detail_value=None,
    extra_message=None,
    footer_note='This email was sent because there is an update on your Brixen account.',
):
    brand = brand_settings()
    company = html_escape(brand['company_name'])
    client_name = html_escape((client or {}).get('full_name') or 'there')
    headline_html = html_escape(headline or 'Account update')
    message_text = (message or '').strip()
    message_html = html_escape(message_text).replace('\n', '<br>')
    extra = (extra_message or '').strip()
    panel_url = cta_url or client_website_url()
    logo_url = html_escape(brand_logo_absolute_url(brand['logo_url']))
    primary = html_escape(brand['primary_color'])
    secondary = html_escape(brand['secondary_color'])
    support_email = html_escape(brand['support_email'])
    phone_digits = re.sub(r'\D', '', brand.get('support_phone') or '447360515317') or '447360515317'
    phone_display = f"+{phone_digits}" if not phone_digits.startswith('+') else phone_digits
    if phone_digits.startswith('44') and len(phone_digits) >= 12:
        phone_display = f"+44 {phone_digits[2:6]} {phone_digits[6:]}"
    support_phone = html_escape(phone_display)
    tel_href = html_escape(f"+{phone_digits}" if not phone_digits.startswith('+') else phone_digits)
    wa_href = html_escape(f"https://wa.me/{phone_digits}")
    email_subject = subject or f"{headline} — {brand['company_name']}"

    body_text = f"Hello {client.get('full_name') or 'there'},\n\n{message_text}\n"
    if detail_title and detail_value:
        body_text += f"\n{detail_title}: {detail_value}\n"
    if extra:
        body_text += f"\n{extra}\n"
    body_text += f"\n{cta_label}:\n{panel_url}\n"
    body_text += f"\nNeed help?\nEmail: {brand['support_email']}\nPhone / WhatsApp: {phone_display}\n"
    body_text += f"\nKind regards,\n{brand['company_name']}"

    detail_block = ''
    if detail_title and detail_value:
        detail_block = f"""
          <tr>
            <td style="padding:8px 32px 18px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border:1px solid #dbeafe;border-radius:14px;background:#f8fbff;">
                <tr>
                  <td style="padding:18px 20px;">
                    <p style="margin:0 0 8px;font-size:12px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:{secondary};">{html_escape(detail_title)}</p>
                    <p style="margin:0;font-size:20px;font-weight:700;line-height:1.4;color:#0f172a;">{html_escape(detail_value)}</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>"""

    message_block = ''
    if extra:
        message_block = f"""
          <tr>
            <td style="padding:0 32px 18px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;">
                <tr>
                  <td style="padding:16px 18px;font-size:14px;line-height:1.6;color:#334155;">
                    <strong style="color:{primary};">Message from our team</strong><br>
                    {html_escape(extra).replace(chr(10), '<br>')}
                  </td>
                </tr>
              </table>
            </td>
          </tr>"""

    support_line = (
        f'<p style="margin:0;font-size:13px;line-height:1.6;color:#64748b;">'
        f'Questions? Email <a href="mailto:{support_email}" style="color:{secondary};text-decoration:none;">{support_email}</a><br>'
        f'Call <a href="tel:{tel_href}" style="color:{secondary};text-decoration:none;">{support_phone}</a>'
        f' · WhatsApp <a href="{wa_href}" style="color:{secondary};text-decoration:none;">{support_phone}</a>'
        f'</p>'
    )

    body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html_escape(email_subject)}</title>
</head>
<body style="margin:0;padding:0;background:#eef2f7;font-family:Inter,Arial,Helvetica,sans-serif;color:#0f172a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#eef2f7;padding:28px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:620px;background:#ffffff;border-radius:18px;overflow:hidden;box-shadow:0 18px 40px rgba(15,23,42,0.10);">
          <tr>
            <td style="padding:28px 32px 22px;background:linear-gradient(135deg,{primary} 0%,{secondary} 100%);text-align:center;">
              <img src="{logo_url}" alt="{company}" width="180" style="display:block;margin:0 auto 14px;max-width:180px;height:auto;border:0;">
              <div style="display:inline-block;padding:8px 14px;border-radius:999px;background:rgba(255,255,255,0.16);color:#ffffff;font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;">
                {html_escape(badge)}
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 32px 10px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#fff7ed;border:1px solid #fed7aa;border-radius:14px;">
                <tr>
                  <td style="padding:18px 20px;">
                    <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#c2410c;">{html_escape(alert_label)}</p>
                    <h1 style="margin:0;font-size:28px;line-height:1.2;color:{primary};">{headline_html}</h1>
                    <p style="margin:10px 0 0;font-size:15px;line-height:1.6;color:#475569;">Hello {client_name}, {message_html}</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          {detail_block}
          {message_block}
          <tr>
            <td style="padding:6px 32px 28px;text-align:center;">
              <a href="{html_escape(panel_url)}" style="display:inline-block;padding:16px 28px;border-radius:12px;background:linear-gradient(135deg,{primary} 0%,{secondary} 100%);color:#ffffff;text-decoration:none;font-size:16px;font-weight:700;box-shadow:0 10px 24px rgba(0,57,113,0.24);">
                {html_escape(cta_label)}
              </a>
              <p style="margin:16px 0 0;font-size:13px;line-height:1.6;color:#64748b;">
                Or copy this link:<br>
                <a href="{html_escape(panel_url)}" style="color:{secondary};word-break:break-all;text-decoration:none;">{html_escape(panel_url)}</a>
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 32px 28px;border-top:1px solid #e2e8f0;background:#f8fafc;">
              {support_line}
              <p style="margin:12px 0 0;font-size:13px;line-height:1.6;color:#94a3b8;">
                Kind regards,<br>
                <strong style="color:{primary};">{company}</strong>
              </p>
              <p style="margin:14px 0 0;font-size:11px;line-height:1.5;color:#94a3b8;">
                {html_escape(footer_note)}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
    return email_subject, body_text, body_html


def build_client_document_email(client, doc_name, client_message=None):
    doc_name = doc_name or 'Document'
    extra = (client_message or '').strip()
    message = 'A new file is now available in your secure client area on our website.'
    return build_client_notification_email(
        client,
        'New document ready for you',
        message,
        subject=f"New document ready: {doc_name} — {brand_settings()['company_name']}",
        badge='New document',
        alert_label='Important notification',
        cta_label='View in Messages & Files',
        cta_url=client_website_url(),
        detail_title='Document',
        detail_value=doc_name,
        extra_message=extra or None,
        footer_note='This email was sent because a document was uploaded to your account. WhatsApp alerts may be added in future.',
    )


def resolve_client_user(client_or_id):
    if isinstance(client_or_id, dict):
        if client_or_id.get('role') == 'CLIENT' and client_or_id.get('email'):
            return client_or_id
        user_id = client_or_id.get('user_id')
        if not user_id and client_or_id.get('email'):
            user_id = client_or_id.get('id')
        if client_or_id.get('email') and user_id:
            return {
                'id': user_id,
                'email': client_or_id['email'],
                'full_name': client_or_id.get('full_name') or '',
                'role': 'CLIENT',
            }
        client_or_id = user_id or client_or_id.get('id')
    if not client_or_id:
        return None
    return query_db(
        "SELECT id, email, full_name, role FROM users WHERE id = ? AND role = 'CLIENT';",
        (client_or_id,),
        one=True,
    )


def notify_client(
    client,
    title,
    message,
    ntype,
    link='/documents',
    *,
    email_subject=None,
    email_headline=None,
    email_text=None,
    email_html=None,
    cta_label=None,
    cta_url=None,
    detail_title=None,
    detail_value=None,
    extra_message=None,
    footer_note=None,
):
    client_rec = resolve_client_user(client)
    if not client_rec:
        return {
            'notification_created': False,
            'email_sent': False,
            'email_status': 'Client not found',
        }

    execute_db("""
        INSERT INTO notifications (user_id, title, message, type, link)
        VALUES (?, ?, ?, ?, ?);
    """, (client_rec['id'], title, message, ntype, link))

    if email_text is None or email_html is None:
        kwargs = {
            'subject': email_subject,
            'cta_label': cta_label,
            'cta_url': cta_url,
            'detail_title': detail_title,
            'detail_value': detail_value,
            'extra_message': extra_message,
        }
        if footer_note:
            kwargs['footer_note'] = footer_note
        email_subject, email_text, email_html = build_client_notification_email(
            client_rec,
            email_headline or title,
            message,
            **{k: v for k, v in kwargs.items() if v is not None},
        )
    else:
        email_subject = email_subject or f"{title} — {brand_settings()['company_name']}"

    sent, status = EmailService.send_notification_email(
        client_rec['email'],
        email_subject,
        email_text,
        email_html,
    )
    return {
        'notification_created': True,
        'email_sent': bool(sent),
        'email_status': status,
    }


def portal_alias_urls():
    return (
        'https://portal.brixenconsultants.com',
        'http://127.0.0.1:5050',
    )


def company_country_label(row):
    src = dict(row or {})
    country = str(src.get('country') or '').strip()
    if country:
        return country
    office = str(src.get('reg_office') or '')
    if re.search(r'united kingdom|\blondon\b|\buk\b', office, re.I):
        return 'United Kingdom'
    if office and ',' in office:
        return office.split(',')[-1].strip() or '—'
    return '—'


def is_pending_company_number(company_number):
    num = str(company_number or '').strip().upper()
    return not num or num.startswith('REG-')


def normalize_company_name_key(name):
    return re.sub(r'\s+', ' ', str(name or '').strip().upper())


def is_company_card_dismissed(user_id, company_name=None, company_number=None, wc_order_id=None):
    if not user_id:
        return False
    name_key = normalize_company_name_key(company_name)
    number = str(company_number or '').strip().upper()
    wc = str(wc_order_id or '').strip()
    reg_number = f'REG-{wc}' if wc else ''
    row = query_db(
        """
        SELECT id FROM dismissed_company_cards
        WHERE user_id = ?
          AND (
            (? != '' AND name_key = ?)
            OR (? != '' AND UPPER(COALESCE(company_number, '')) = ?)
            OR (? != '' AND woocommerce_order_id = ?)
            OR (? != '' AND UPPER(COALESCE(company_number, '')) = ?)
          )
        LIMIT 1;
        """,
        (
            user_id,
            name_key, name_key,
            number, number,
            wc, wc,
            reg_number, reg_number,
        ),
        one=True,
    )
    return bool(row)


def record_dismissed_company_card(company):
    if not company or not company.get('user_id'):
        return
    user_id = company['user_id']
    name_key = normalize_company_name_key(company.get('name'))
    number = str(company.get('company_number') or '').strip().upper() or None
    orders = query_db(
        "SELECT woocommerce_order_id FROM orders WHERE company_id = ?;",
        (company['id'],),
    ) or []
    wc_ids = sorted({
        str(row.get('woocommerce_order_id') or '').strip()
        for row in orders
        if str(row.get('woocommerce_order_id') or '').strip()
    })
    wc_id = wc_ids[0] if wc_ids else None
    execute_db(
        """
        INSERT INTO dismissed_company_cards (user_id, name_key, company_number, woocommerce_order_id)
        VALUES (?, ?, ?, ?);
        """,
        (user_id, name_key, number, wc_id),
    )


def registered_address_from_company_row(row):
    if is_pending_company_number((row or {}).get('company_number')):
        return None
    reg_office = str((row or {}).get('reg_office') or '').strip()
    if reg_office and reg_office.lower() not in ('united kingdom', 'uk', 'none', 'n/a'):
        return reg_office
    return None


def company_registered_office_record(company_id, company_row=None):
    row = dict(company_row or {})
    if is_pending_company_number(row.get('company_number')):
        return None
    office = query_db(
        """
        SELECT line1, line2, city, postal_code, country
        FROM addresses
        WHERE company_id = ? AND type = 'Registered Office'
        ORDER BY id DESC LIMIT 1;
        """,
        (company_id,),
        one=True,
    )
    if office:
        lines = [office.get('line1'), office.get('line2'), office.get('city')]
        address = ', '.join(str(part).strip() for part in lines if part and str(part).strip())
        if address:
            return {
                'address': address,
                'postcode': office.get('postal_code') or '',
                'country': office.get('country') or company_country_label(row),
            }
    reg_office = registered_address_from_company_row(row)
    if reg_office:
        return {
            'address': reg_office,
            'postcode': '',
            'country': company_country_label(row),
        }
    return None


def ensure_company_order_documents(company_id):
    company = query_db("SELECT id, user_id FROM companies WHERE id = ?;", (company_id,), one=True)
    if not company:
        return 0
    orders = query_db(
        """
        SELECT id, user_id, company_id, woocommerce_order_id
        FROM orders
        WHERE company_id = ?;
        """,
        (company_id,),
    ) or []
    imported = 0
    for order in orders:
        execute_db(
            """
            UPDATE documents
            SET company_id = ?
            WHERE order_id = ? AND (company_id IS NULL OR company_id = 0);
            """,
            (company_id, order['id']),
        )
        has_checkout = query_db(
            """
            SELECT id FROM documents
            WHERE order_id = ? AND category = 'Checkout Upload'
            LIMIT 1;
            """,
            (order['id'],),
            one=True,
        )
        wc_order_id = str(order.get('woocommerce_order_id') or '').strip()
        if has_checkout or not wc_order_id:
            continue
        wp_data = fetch_wordpress_order_payload(wc_order_id)
        if not wp_data:
            continue
        try:
            imported += import_order_checkout_attachments(
                order['id'],
                order['user_id'],
                company_id,
                wp_data,
            )
        except Exception as err:
            print(f"[CheckoutAttach] Company {company_id} order {order['id']}: {err}")
    return imported


def documents_for_company_portfolio(company_id, user_id=None, for_client=False):
    order_rows = query_db("SELECT id FROM orders WHERE company_id = ?;", (company_id,)) or []
    order_ids = [int(row['id']) for row in order_rows if row.get('id')]
    link_clauses = ["d.company_id = ?"]
    params = [company_id]
    if order_ids:
        placeholders = ','.join('?' for _ in order_ids)
        link_clauses.append(f"d.order_id IN ({placeholders})")
        params.extend(order_ids)
    where_parts = [f"({' OR '.join(link_clauses)})"]
    if user_id is not None:
        where_parts.insert(0, "d.user_id = ?")
        params = [user_id, *params]
    if for_client:
        where_parts.append(
            "(d.client_visible = 1 OR d.uploaded_by = 'Customer Upload' OR d.category = 'Checkout Upload')"
        )
    return query_db(
        f"""
        SELECT d.*, c.name as company_name, o.order_number
        FROM documents d
        LEFT JOIN companies c ON d.company_id = c.id
        LEFT JOIN orders o ON d.order_id = o.id
        WHERE {' AND '.join(where_parts)}
        ORDER BY d.created_at DESC;
        """,
        params,
    )


def client_can_access_document(doc):
    if not doc:
        return False
    if int(doc.get('client_visible') or 0):
        return True
    uploaded_by = str(doc.get('uploaded_by') or '')
    category = str(doc.get('category') or '')
    return uploaded_by == 'Customer Upload' or category == 'Checkout Upload'


def normalize_compliance_field(value, max_len=40):
    text = re.sub(r'\s+', '', str(value or '').strip())
    if len(text) > max_len:
        return None, f'Enter at most {max_len} characters'
    if text and not re.fullmatch(r'[A-Za-z0-9/-]+', text):
        return None, 'Use letters, numbers, / or - only'
    return text, None


def public_client_company(row, deadlines=None, for_staff=False):
    if not row:
        return None
    src = dict(row)
    payload = {
        'id': src.get('id'),
        'name': src.get('name'),
        'company_number': src.get('company_number'),
        'status': src.get('status') or 'Active',
        'inc_date': src.get('inc_date'),
        'director': src.get('director'),
        'reg_office': src.get('reg_office'),
        'registered_address': registered_address_from_company_row(src),
        'is_registered': not is_pending_company_number(src.get('company_number')),
        'package': src.get('package'),
        'account_status': src.get('account_status'),
        'country': company_country_label(src),
        'created_at': src.get('created_at'),
        'utr_number': str(src.get('utr_number') or '').strip(),
        'deadlines': list(deadlines or []),
    }
    if for_staff:
        payload['authentication_code'] = str(src.get('authentication_code') or '').strip()
        payload['activation_code'] = str(src.get('activation_code') or '').strip()
    return payload


def update_company_compliance(company_id, data):
    company = query_db("SELECT id FROM companies WHERE id = ?;", (company_id,), one=True)
    if not company:
        return None, 'Company not found'
    utr, utr_err = normalize_compliance_field(data.get('utr_number'), 15)
    if utr_err:
        return None, 'UTR number: ' + utr_err
    auth, auth_err = normalize_compliance_field(data.get('authentication_code'), 12)
    if auth_err:
        return None, 'Authentication code: ' + auth_err
    activation, activation_err = normalize_compliance_field(data.get('activation_code'), 40)
    if activation_err:
        return None, 'Activation code: ' + activation_err
    execute_db(
        """
        UPDATE companies
        SET utr_number = ?, authentication_code = ?, activation_code = ?
        WHERE id = ?;
        """,
        (utr or None, auth or None, activation or None, company_id),
    )
    return query_db(
        """
        SELECT id, name, company_number, status, inc_date, director, reg_office, package, account_status,
               created_at, user_id, utr_number, authentication_code, activation_code
        FROM companies WHERE id = ?;
        """,
        (company_id,),
        one=True,
    ), None


def upcoming_deadlines_by_company(user_id, company_ids):
    mapping = {int(cid): [] for cid in company_ids or [] if cid}
    if not mapping:
        return mapping
    placeholders = ','.join('?' for _ in mapping)
    today = datetime.date.today().isoformat()
    ids = list(mapping.keys())
    user_clause = "WHERE user_id = ? AND" if user_id is not None else "WHERE"
    params = [user_id, *ids, today] if user_id is not None else [*ids, today]
    address_rows = query_db(
        f"""
        SELECT company_id, type, expiry_date
        FROM addresses
        {user_clause} company_id IN ({placeholders})
          AND expiry_date IS NOT NULL AND date(expiry_date) >= date(?)
        ORDER BY expiry_date ASC;
        """,
        params,
    )
    for row in address_rows:
        cid = int(row['company_id'])
        label = f"{row['type']} expiry" if row.get('type') else 'Address expiry'
        mapping[cid].append({'label': label, 'date': row.get('expiry_date')})
    agent_rows = query_db(
        f"""
        SELECT company_id, renewal_date
        FROM registered_agents
        {user_clause} company_id IN ({placeholders})
          AND renewal_date IS NOT NULL AND date(renewal_date) >= date(?)
        ORDER BY renewal_date ASC;
        """,
        params,
    )
    for row in agent_rows:
        cid = int(row['company_id'])
        mapping[cid].append({'label': 'Registered agent renewal', 'date': row.get('renewal_date')})
    return mapping


def public_client_order_summary(row):
    if not row:
        return None
    src = dict(row)
    return {
        'id': src.get('id'),
        'order_number': src.get('order_number'),
        'service_name': src.get('service_name'),
        'status': src.get('status'),
        'progress_percent': src.get('progress_percent'),
        'created_at': src.get('created_at'),
        'expected_date': src.get('expected_date'),
    }


def public_client_ticket_summary(row):
    if not row:
        return None
    src = dict(row)
    return {
        'id': src.get('id'),
        'ticket_number': src.get('ticket_number'),
        'subject': src.get('subject'),
        'category': src.get('category'),
        'priority': src.get('priority'),
        'status': src.get('status'),
        'created_at': src.get('created_at'),
    }


def public_document(row, for_client=False):
    if not row:
        return None
    src = dict(row)
    uploaded_by = src.get('uploaded_by') or ''
    category = src.get('category') or ''
    is_customer_upload = (
        uploaded_by == 'Customer Upload'
        or category == 'Checkout Upload'
    )
    out = {
        'id': src.get('id'),
        'user_id': src.get('user_id'),
        'company_id': src.get('company_id'),
        'order_id': src.get('order_id'),
        'name': src.get('name'),
        'category': src.get('category'),
        'file_type': src.get('file_type'),
        'file_size': src.get('file_size'),
        'status': src.get('status'),
        'uploaded_by': uploaded_by,
        'created_at': src.get('created_at'),
        'company_name': src.get('company_name'),
        'order_number': src.get('order_number'),
        'client_name': src.get('client_name'),
        'client_email': src.get('client_email'),
        'client_visible': int(src.get('client_visible') or 0),
        'shared_at': src.get('shared_at'),
        'is_customer_upload': is_customer_upload,
    }
    if not for_client:
        out['review_notes'] = src.get('review_notes')
    return out


ALLOWED_DOCUMENT_EXTS = ('.pdf', '.png', '.jpg', '.jpeg', '.doc', '.docx', '.zip')
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
DOCUMENT_TYPE_LABELS = {
    '.pdf': 'PDF Document',
    '.png': 'PNG Image',
    '.jpg': 'JPEG Image',
    '.jpeg': 'JPEG Image',
    '.doc': 'Word Document',
    '.docx': 'Word Document',
    '.zip': 'ZIP Archive',
}


def optional_record_id(value):
    if value in (None, '', False):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def document_extension(original_name):
    return os.path.splitext(os.path.basename(str(original_name or '')))[1].lower()


def decode_document_base64(b64_content, default_bytes=None):
    if not b64_content:
        return default_bytes, None
    import base64
    try:
        return base64.b64decode(b64_content), None
    except Exception:
        return None, 'Invalid base64 payload'


def validate_uploaded_document(original_name, file_bytes, require_bytes=False, upload_filename=None):
    ext = document_extension(original_name)
    if ext not in ALLOWED_DOCUMENT_EXTS:
        ext = document_extension(upload_filename)
    if ext not in ALLOWED_DOCUMENT_EXTS:
        return None, 'Invalid file format. Use PDF, PNG, JPG, Word, or ZIP (max 15 MB).'
    if require_bytes and not file_bytes:
        return None, 'File is required'
    if file_bytes is not None and len(file_bytes) > MAX_DOCUMENT_BYTES:
        return None, 'File is too large'
    return ext, None


def store_client_document_file(client_id, order_id, ext, file_bytes):
    order_folder = str(order_id or 'general')
    storage_root = os.path.abspath(STORAGE_DIR)
    client_dir = os.path.abspath(os.path.join(STORAGE_DIR, 'clients', str(client_id), 'orders', order_folder, 'documents'))
    os.makedirs(client_dir, exist_ok=True)
    target_path = os.path.abspath(os.path.join(client_dir, f"{uuid.uuid4().hex}{ext}"))
    if not target_path.startswith(storage_root):
        return None, 'Path traversal attempt blocked'
    with open(target_path, 'wb') as handle:
        handle.write(file_bytes)
    return target_path, None


def format_document_size(num_bytes):
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


def wordpress_base_url():
    return (os.environ.get('WORDPRESS_BASE_URL') or 'https://brixenconsultants.com').rstrip('/')


def allowed_checkout_attachment_hosts():
    hosts = set()
    for raw in (
        wordpress_base_url(),
        'https://brixenconsultants.com',
        'https://www.brixenconsultants.com',
        os.environ.get('CRM_BASE_URL') or '',
    ):
        host = (urllib.parse.urlparse(raw).hostname or '').lower()
        if host:
            hosts.add(host)
            if host.startswith('www.'):
                hosts.add(host[4:])
            else:
                hosts.add(f'www.{host}')
    return hosts


def is_allowed_checkout_attachment_url(url):
    try:
        parsed = urllib.parse.urlparse(str(url or '').strip())
    except Exception:
        return False
    if parsed.scheme not in ('http', 'https'):
        return False
    host = (parsed.hostname or '').lower()
    if not host:
        return False
    allowed = allowed_checkout_attachment_hosts()
    if host in allowed:
        return True
    return any(host.endswith('.' + h) for h in allowed if h)


def normalize_checkout_attachments(o_data):
    raw = o_data.get('attachments') or o_data.get('files') or []
    if not isinstance(raw, list):
        return []
    normalized = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            url = item.strip()
            name = os.path.basename(urllib.parse.urlparse(url).path) or 'checkout-upload'
        elif isinstance(item, dict):
            url = str(item.get('url') or item.get('file_url') or item.get('file') or '').strip()
            name = str(item.get('name') or item.get('file_name') or '').strip()
            if not name and url:
                name = os.path.basename(urllib.parse.urlparse(url).path) or 'checkout-upload'
        else:
            continue
        if not url or not is_allowed_checkout_attachment_url(url):
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append({'name': name or 'checkout-upload', 'url': url, 'source': 'checkout'})
    return normalized


def download_checkout_attachment(url):
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'BrixenCRM/1.0 (+https://portal.brixenconsultants.com)'},
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(MAX_DOCUMENT_BYTES + 1)
            if len(data) > MAX_DOCUMENT_BYTES:
                return None, None, 'File is too large'
            content_type = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            return data, content_type, None
    except Exception as err:
        return None, None, str(err)


def import_order_checkout_attachments(order_id, client_id, company_id, o_data):
    attachments = normalize_checkout_attachments(o_data)
    imported = 0
    for att in attachments:
        url = att['url']
        doc_name = (att.get('name') or 'checkout-upload').strip() or 'checkout-upload'
        existing = query_db("""
            SELECT id FROM documents
            WHERE order_id = ? AND category = 'Checkout Upload' AND review_notes = ?
            LIMIT 1;
        """, (order_id, f'Checkout source: {url}'), one=True)
        if existing:
            continue
        file_bytes, content_type, err = download_checkout_attachment(url)
        if err or not file_bytes:
            print(f"[CheckoutAttach] Failed to download {url}: {err}")
            continue
        guessed_name = doc_name
        if document_extension(guessed_name) not in ALLOWED_DOCUMENT_EXTS:
            path_name = os.path.basename(urllib.parse.urlparse(url).path)
            if document_extension(path_name) in ALLOWED_DOCUMENT_EXTS:
                guessed_name = path_name
            elif content_type == 'application/pdf':
                guessed_name = f"{doc_name}.pdf"
            elif content_type in ('image/png',):
                guessed_name = f"{doc_name}.png"
            elif content_type in ('image/jpeg', 'image/jpg'):
                guessed_name = f"{doc_name}.jpg"
        ext, upload_err = validate_uploaded_document(guessed_name, file_bytes, require_bytes=True, upload_filename=guessed_name)
        if upload_err:
            print(f"[CheckoutAttach] Rejected {url}: {upload_err}")
            continue
        target_path, store_err = store_client_document_file(client_id, order_id, ext, file_bytes)
        if store_err:
            print(f"[CheckoutAttach] Store failed {url}: {store_err}")
            continue
        safe_name = doc_name
        if document_extension(safe_name) not in ALLOWED_DOCUMENT_EXTS and document_extension(guessed_name) in ALLOWED_DOCUMENT_EXTS:
            safe_name = guessed_name
        execute_db("""
            INSERT INTO documents (
                user_id, company_id, order_id, name, category, file_path, file_type, file_size,
                status, uploaded_by, review_notes, client_visible
            ) VALUES (?, ?, ?, ?, 'Checkout Upload', ?, ?, ?, 'Pending Review', 'Customer Upload', ?, 0);
        """, (
            client_id,
            company_id,
            order_id,
            safe_name,
            target_path,
            DOCUMENT_TYPE_LABELS.get(ext, 'Document'),
            format_document_size(len(file_bytes)),
            f'Checkout source: {url}',
        ))
        imported += 1
    return imported


def resolve_client_document_links(client_id, company_id, order_id):
    client = query_db("SELECT id, email, full_name, role FROM users WHERE id = ?;", (client_id,), one=True)
    if not client:
        return None, 'Client not found'
    company = None
    order = None
    if company_id:
        company = query_db("SELECT * FROM companies WHERE id = ?;", (company_id,), one=True)
        if not company or int(company['user_id']) != int(client['id']):
            return None, 'Company does not belong to this client'
    if order_id:
        order = query_db("SELECT * FROM orders WHERE id = ?;", (order_id,), one=True)
        if not order or int(order['user_id']) != int(client['id']):
            return None, 'Order does not belong to this client'
        if company_id and order.get('company_id') and int(order['company_id']) != int(company_id):
            return None, 'Order does not belong to the selected company'
    return {'client': client, 'company': company, 'order': order}, None


def notify_client_document_uploaded(client, doc_name, client_message=None):
    extra = (client_message or '').strip()
    message = 'A new document is ready in your Brixen account. Please sign in on the website to view it.'
    if extra:
        message = f"{message} {extra}"
    subject, email_text, email_html = build_client_document_email(client, doc_name, client_message)
    return notify_client(
        client,
        'New document available',
        message,
        'document_uploaded',
        '/documents',
        email_subject=subject,
        email_text=email_text,
        email_html=email_html,
    )


def public_line_item(row, for_client=False):
    if not row:
        return None
    src = dict(row)
    item = {
        'product_name': src.get('product_name'),
        'category_name': src.get('category_name'),
        'quantity': src.get('quantity'),
        'unit_price': src.get('unit_price'),
        'line_total': src.get('line_total'),
    }
    if not for_client:
        item.update({
            'woocommerce_product_id': src.get('woocommerce_product_id'),
            'woocommerce_variation_id': src.get('woocommerce_variation_id'),
            'sku': src.get('sku'),
            'category_id': src.get('category_id'),
        })
    return item


def parse_line_items_from_payload(o_data):
    raw = o_data.get('line_items') or o_data.get('items') or []
    parsed = []
    if isinstance(raw, list):
        for idx, item in enumerate(raw):
            if isinstance(item, str):
                parsed.append({
                    'product_name': item,
                    'quantity': 1,
                    'unit_price': None,
                    'line_total': None,
                    'sort_order': idx,
                })
                continue
            if not isinstance(item, dict):
                continue
            cats = item.get('categories') or []
            first_cat = cats[0] if cats and isinstance(cats[0], dict) else {}
            name = (item.get('product_name') or item.get('name') or item.get('service_name') or '').strip()
            if not name:
                continue
            qty = item.get('quantity') if item.get('quantity') not in (None, '') else 1
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                qty = 1
            unit = item.get('unit_price') if item.get('unit_price') not in (None, '') else item.get('price')
            total = item.get('line_total') if item.get('line_total') not in (None, '') else item.get('total')
            try:
                unit = float(unit) if unit is not None else None
            except (TypeError, ValueError):
                unit = None
            try:
                total = float(total) if total is not None else None
            except (TypeError, ValueError):
                total = None
            parsed.append({
                'woocommerce_product_id': item.get('product_id') or item.get('woocommerce_product_id'),
                'woocommerce_variation_id': item.get('variation_id') or item.get('woocommerce_variation_id'),
                'sku': item.get('sku'),
                'product_name': name,
                'category_name': item.get('category') or item.get('category_name') or first_cat.get('name'),
                'category_id': item.get('category_id') or first_cat.get('id'),
                'quantity': max(qty, 1),
                'unit_price': unit,
                'line_total': total,
                'sort_order': idx,
            })
    if not parsed:
        fallback_name = (o_data.get('service_name') or '').strip()
        if fallback_name:
            parsed.append({
                'woocommerce_product_id': o_data.get('product_id'),
                'woocommerce_variation_id': o_data.get('variation_id'),
                'sku': o_data.get('sku'),
                'product_name': fallback_name,
                'category_name': o_data.get('category') or o_data.get('category_name'),
                'category_id': o_data.get('category_id'),
                'quantity': 1,
                'unit_price': None,
                'line_total': None,
                'sort_order': 0,
            })
    return parsed


def replace_order_line_items(order_id, items, order_price=None, order_total=None):
    execute_db("DELETE FROM order_line_items WHERE order_id = ?;", (order_id,))
    count = max(len(items), 1)
    for item in items:
        unit = item.get('unit_price')
        total = item.get('line_total')
        qty = item.get('quantity') or 1
        if unit is None and total is not None:
            unit = round(float(total) / qty, 2)
        if total is None and unit is not None:
            total = round(float(unit) * qty, 2)
        if unit is None and order_price is not None:
            unit = round(float(order_price) / count, 2)
        if total is None and order_total is not None:
            total = round(float(order_total) / count, 2)
        execute_db("""
            INSERT INTO order_line_items (
                order_id, woocommerce_product_id, woocommerce_variation_id, sku,
                product_name, category_name, category_id, quantity, unit_price, line_total, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            order_id,
            str(item.get('woocommerce_product_id') or '') or None,
            str(item.get('woocommerce_variation_id') or '') or None,
            item.get('sku') or None,
            item['product_name'],
            item.get('category_name') or None,
            str(item.get('category_id') or '') or None,
            qty,
            float(unit or 0),
            float(total or 0),
            item.get('sort_order') or 0,
        ))


def normalize_company_name_key(name):
    text = re.sub(r'[^a-z0-9]+', ' ', str(name or '').lower()).strip()
    if not text:
        return ''
    suffixes = (
        'limited', 'ltd', 'llp', 'plc', 'inc', 'incorporated', 'corp', 'corporation',
        'company', 'co', 'llc', 'cyf', 'ccc',
    )
    parts = text.split()
    while parts and parts[-1] in suffixes:
        parts.pop()
    return ' '.join(parts)


def match_company_for_client(client_id, company_name):
    if not company_name or not str(company_name).strip():
        return None
    exact = query_db(
        "SELECT id FROM companies WHERE user_id = ? AND LOWER(name) = LOWER(?);",
        (client_id, str(company_name).strip()),
        one=True
    )
    if exact:
        return exact['id']
    needle = normalize_company_name_key(company_name)
    if not needle:
        return None
    for row in query_db("SELECT id, name FROM companies WHERE user_id = ?;", (client_id,)) or []:
        if normalize_company_name_key(row.get('name')) == needle:
            return row['id']
    return None


def link_orphaned_formation_orders(user_id=None):
    """Link unlinked formation orders to an existing company card when names match.

    Staff often use Add company instead of Create card on the pending form; without
    this heal, awaiting-name cards stay visible next to the real LTD card.
    """
    guest_id = guest_unlinked_user_id()
    sql = """
        SELECT o.id, o.user_id, o.woocommerce_order_id, o.service_name, u.full_name as client_name
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE o.company_id IS NULL
          AND COALESCE(o.portfolio_hidden, 0) = 0
    """
    params = []
    if user_id:
        sql += " AND o.user_id = ?"
        params.append(user_id)
    if guest_id:
        sql += " AND o.user_id != ?"
        params.append(guest_id)
    linked = 0
    for row in query_db(sql, params) or []:
        if not is_company_registration_order({}, row.get('service_name'), []):
            continue
        company_id = None
        wc_id = str(row.get('woocommerce_order_id') or '').strip()
        if wc_id:
            by_reg = query_db(
                "SELECT id FROM companies WHERE user_id = ? AND company_number = ?;",
                (row['user_id'], f'REG-{wc_id}'),
                one=True,
            )
            if by_reg:
                company_id = by_reg['id']
        if not company_id:
            company_id = match_company_for_client(row['user_id'], row.get('client_name'))
            if company_id:
                # Only claim a card that is not already tied to another order.
                taken = query_db(
                    "SELECT id FROM orders WHERE company_id = ? LIMIT 1;",
                    (company_id,),
                    one=True,
                )
                if taken:
                    company_id = None
        if not company_id:
            continue
        execute_db(
            "UPDATE orders SET company_id = ?, portfolio_hidden = 0 WHERE id = ? AND company_id IS NULL;",
            (company_id, row['id']),
        )
        linked += 1
    linked += link_sole_company_pending_pairs(user_id=user_id)
    return linked


def link_sole_company_pending_pairs(user_id=None):
    """Link the only pending formation order when the client has a single company card.

    Covers staff using Add company with a different name than the website checkout label.
    """
    guest_id = guest_unlinked_user_id()
    user_sql = """
        SELECT DISTINCT o.user_id
        FROM orders o
        WHERE o.company_id IS NULL
          AND COALESCE(o.portfolio_hidden, 0) = 0
    """
    user_params = []
    if user_id:
        user_sql += " AND o.user_id = ?"
        user_params.append(user_id)
    if guest_id:
        user_sql += " AND o.user_id != ?"
        user_params.append(guest_id)
    linked = 0
    for user_row in query_db(user_sql, user_params) or []:
        uid = user_row['user_id']
        pending_rows = query_db(
            """
            SELECT o.id, o.service_name
            FROM orders o
            WHERE o.user_id = ?
              AND o.company_id IS NULL
              AND COALESCE(o.portfolio_hidden, 0) = 0
            ORDER BY o.created_at ASC;
            """,
            (uid,),
        ) or []
        formation_pending = [
            row for row in pending_rows
            if is_company_registration_order({}, row.get('service_name'), [])
        ]
        if len(formation_pending) != 1:
            continue
        companies = query_db("SELECT id FROM companies WHERE user_id = ?;", (uid,)) or []
        if len(companies) != 1:
            continue
        company_id = companies[0]['id']
        if query_db("SELECT id FROM orders WHERE company_id = ? LIMIT 1;", (company_id,), one=True):
            continue
        execute_db(
            "UPDATE orders SET company_id = ?, portfolio_hidden = 0 WHERE id = ? AND company_id IS NULL;",
            (company_id, formation_pending[0]['id']),
        )
        linked += 1
    return linked


def dismiss_pending_registration_order(order_id, actor=None):
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return False, 'Order not found'
    order = query_db(
        """
        SELECT o.id, o.order_number, o.service_name, o.user_id, u.full_name as client_name
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE o.id = ?;
        """,
        (order_id,),
        one=True,
    )
    if not order:
        return False, 'Order not found'
    if order.get('company_id'):
        return False, 'This order already has a company card'
    if not is_company_registration_order({}, order.get('service_name'), []):
        return False, 'Only website formation orders can be dismissed here'
    execute_db("UPDATE orders SET portfolio_hidden = 1 WHERE id = ?;", (order_id,))
    if actor:
        log_activity(
            actor,
            'FORMATION_DISMISSED',
            'orders',
            str(order_id),
            f"Dismissed awaiting-name card for {order.get('order_number')} ({order.get('client_name')})",
        )
    return True, None


COMPANY_REGISTRATION_HINTS = (
    'formation',
    'incorporat',
    'company registration',
    'register company',
    'register a company',
    'company register',
    'new company',
    'ltd formation',
    'set up a company',
    'setup company',
    'company set up',
    'company setup',
    'company package',
    'digital package',
    'professional package',
    'all inclusive package',
    'ltd company registration',
    'uk corporate formation',
)


def extract_order_company_name(o_data):
    if not isinstance(o_data, dict):
        return None
    for key in ('company_name', 'billing_company', 'proposed_company_name', 'proposed_name', 'company'):
        value = o_data.get(key)
        if value and str(value).strip():
            return str(value).strip()
    meta = o_data.get('meta') or o_data.get('meta_data') or {}
    if isinstance(meta, dict):
        for key in ('proposed_company_name', 'company_name', 'billing_company', '_billing_company'):
            value = meta.get(key)
            if value and str(value).strip():
                return str(value).strip()
    if isinstance(meta, list):
        for item in meta:
            if not isinstance(item, dict):
                continue
            key = str(item.get('key') or item.get('id') or '')
            if key in ('proposed_company_name', 'company_name', 'billing_company', '_billing_company'):
                value = item.get('value')
                if value and str(value).strip():
                    return str(value).strip()
    for item in o_data.get('line_items') or []:
        if not isinstance(item, dict):
            continue
        item_meta = item.get('item_meta') or item.get('meta') or {}
        if isinstance(item_meta, dict):
            for key, value in item_meta.items():
                kl = str(key).lower()
                if value and str(value).strip() and ('company' in kl or 'proposed' in kl or kl.endswith('name')):
                    return str(value).strip()
    return None


def is_company_registration_order(o_data, service_name, line_items):
    blobs = [service_name or '', (o_data or {}).get('category') or '', (o_data or {}).get('category_name') or '']
    for item in line_items or []:
        blobs.append((item or {}).get('product_name') or '')
        blobs.append((item or {}).get('category_name') or '')
    text = ' '.join(str(part) for part in blobs).lower()
    return any(hint in text for hint in COMPANY_REGISTRATION_HINTS)


def guest_unlinked_user_id():
    guest = query_db("SELECT id FROM users WHERE email = 'guest.unlinked@brixenconsultants.com';", one=True)
    return guest['id'] if guest else None


def public_pending_registration(row, for_client=False):
    item = {
        'order_id': row['id'],
        'order_number': row.get('order_number'),
        'service_name': row.get('service_name'),
        'status': row.get('status'),
        'created_at': row.get('created_at'),
    }
    if not for_client:
        item['client_name'] = row.get('client_name') or ''
        item['user_id'] = row.get('user_id')
    return item


def pending_registration_orders(user_id=None, for_client=False):
    link_orphaned_formation_orders(user_id=user_id)
    guest_id = guest_unlinked_user_id()
    sql = """
        SELECT o.id, o.order_number, o.service_name, o.status, o.created_at, o.user_id,
               u.full_name as client_name
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE o.company_id IS NULL
          AND COALESCE(o.portfolio_hidden, 0) = 0
    """
    params = []
    if user_id:
        sql += " AND o.user_id = ?"
        params.append(user_id)
    if guest_id:
        sql += " AND o.user_id != ?"
        params.append(guest_id)
    sql += " ORDER BY o.created_at DESC;"
    pending = []
    for row in query_db(sql, params) or []:
        if is_company_registration_order({}, row.get('service_name'), []):
            pending.append(public_pending_registration(row, for_client=for_client))
    return pending


def companies_house_api_key():
    row = query_db("SELECT value FROM settings WHERE key = 'companies_house_api_key';", one=True)
    key = (row['value'] if row and str(row.get('value') or '').strip() else None) or os.environ.get('COMPANIES_HOUSE_API_KEY')
    return str(key or '').strip()


def uk_formfill_pro_url():
    row = query_db("SELECT value FROM settings WHERE key = 'uk_formfill_pro_url';", one=True)
    url = (row['value'] if row and str(row.get('value') or '').strip() else None) or os.environ.get('UK_FORMFILL_PRO_URL')
    url = str(url or 'https://portal.brixenconsultants.com/formfill').strip()
    return url.rstrip('/') or 'https://portal.brixenconsultants.com/formfill'


def companies_house_request(path_qs):
    key = companies_house_api_key()
    if not key:
        return None, 'Companies House API key is not configured'
    url = 'https://api.company-information.service.gov.uk' + path_qs
    token = base64.b64encode(f'{key}:'.encode('ascii')).decode('ascii')
    req = urllib.request.Request(url, headers={
        'Authorization': f'Basic {token}',
        'Accept': 'application/json',
        'User-Agent': 'BrixenCRM/1.0',
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode('utf-8')), None
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return None, 'Companies House rejected the API key'
        if exc.code == 404:
            return None, 'Company not found at Companies House'
        if exc.code == 429:
            return None, 'Companies House rate limit reached. Try again shortly.'
        return None, 'Companies House lookup failed'
    except Exception:
        return None, 'Could not reach Companies House'


def public_companies_house_match(item):
    if not isinstance(item, dict):
        return None
    address = item.get('address') if isinstance(item.get('address'), dict) else {}
    office_parts = [
        address.get('address_line_1'),
        address.get('locality') or address.get('region'),
        address.get('postal_code'),
        address.get('country'),
    ]
    name = (item.get('title') or item.get('company_name') or '').strip()
    number = str(item.get('company_number') or '').strip()
    if not name or not number:
        return None
    return {
        'name': name,
        'company_number': number,
        'status': str(item.get('company_status') or '').strip(),
        'company_type': str(item.get('company_type') or '').strip(),
        'inc_date': str(item.get('date_of_creation') or '').strip(),
        'reg_office': (item.get('address_snippet') or ', '.join(part for part in office_parts if part)).strip(),
    }


def search_companies_house(query):
    q = (query or '').strip()
    if len(q) < 2 or len(q) > 80:
        return [], 'Enter at least 2 characters'
    path_qs = '/search/companies?' + urllib.parse.urlencode({'q': q, 'items_per_page': 8})
    payload, error = companies_house_request(path_qs)
    if error:
        return [], error
    matches = []
    for item in (payload or {}).get('items') or []:
        match = public_companies_house_match(item)
        if match:
            matches.append(match)
    return matches, None


def normalize_company_number(raw):
    text = re.sub(r'[^A-Za-z0-9]', '', str(raw or '').strip().upper())
    if text.startswith('NO'):
        text = text[2:]
    return text


def parse_company_number_list(text):
    if isinstance(text, list):
        parts = text
    else:
        parts = re.split(r'[\s,;\n\r\t]+', str(text or ''))
    seen = set()
    numbers = []
    for part in parts:
        number = normalize_company_number(part)
        if not number or number in seen:
            continue
        seen.add(number)
        numbers.append(number)
    return numbers


def companies_house_registered_office(address):
    if not isinstance(address, dict):
        return 'United Kingdom'
    parts = [
        address.get('premises'),
        address.get('address_line_1'),
        address.get('address_line_2'),
        address.get('locality') or address.get('region'),
        address.get('postal_code'),
        address.get('country'),
    ]
    text = ', '.join(str(part).strip() for part in parts if part and str(part).strip())
    return text or 'United Kingdom'


def public_companies_house_profile(data):
    if not isinstance(data, dict):
        return None
    name = (data.get('company_name') or '').strip()
    number = str(data.get('company_number') or '').strip()
    if not name or not number:
        return None
    return {
        'name': name,
        'company_number': number,
        'status': str(data.get('company_status') or '').strip(),
        'inc_date': str(data.get('date_of_creation') or '')[:10],
        'reg_office': companies_house_registered_office(data.get('registered_office_address')),
    }


def fetch_companies_house_profile(company_number):
    number = normalize_company_number(company_number)
    if not number:
        return None, 'Enter a company number'
    payload, error = companies_house_request('/company/' + urllib.parse.quote(number))
    if error:
        return None, error
    profile = public_companies_house_profile(payload)
    if not profile:
        return None, 'Company not found at Companies House'
    return profile, None


def format_companies_house_officer_name(name):
    text = str(name or '').strip()
    if not text:
        return ''
    if ',' in text:
        last, first = text.split(',', 1)
        formatted = f"{first.strip()} {last.strip()}".strip()
        return formatted or text
    return text


def fetch_companies_house_primary_director(company_number):
    number = normalize_company_number(company_number)
    if not number:
        return None
    payload, error = companies_house_request(
        '/company/' + urllib.parse.quote(number) + '/officers?' + urllib.parse.urlencode({
            'items_per_page': 100,
            'register_type': 'directors',
        })
    )
    if error or not isinstance(payload, dict):
        return None
    for item in payload.get('items') or []:
        if not isinstance(item, dict) or item.get('resigned_on'):
            continue
        role = str(item.get('officer_role') or '').lower()
        if 'director' not in role:
            continue
        name = format_companies_house_officer_name(item.get('name'))
        if name:
            return name
    return None


def companies_house_status_value(raw_status):
    raw = str(raw_status or 'active').lower()
    status_map = {
        'active': 'Active',
        'dissolved': 'Dissolved',
        'liquidation': 'Liquidation',
        'receivership': 'Receivership',
        'administration': 'Administration',
        'converted-closed': 'Closed',
        'closed': 'Closed',
        'pending': 'Active',
    }
    return status_map.get(raw, 'Active')


def import_webfiling_companies(data, actor=None):
    numbers = parse_company_number_list(data.get('company_numbers') or data.get('numbers') or '')
    if not numbers:
        return None, 'Paste at least one Companies House number from WebFiling saved companies'
    if len(numbers) > 100:
        return None, 'Import up to 100 company numbers at a time'
    if not companies_house_api_key():
        return None, 'Add a Companies House API key in System Settings before importing WebFiling companies'

    client_id, client, client_err = ensure_client_for_manual_company(data, actor)
    if client_err:
        return None, client_err

    update_existing = bool(data.get('update_existing'))
    package = (data.get('package') or 'WebFiling Import').strip() or 'WebFiling Import'
    results = []
    imported = updated = skipped = failed = 0

    for number in numbers:
        existing = query_db(
            "SELECT id, name, user_id FROM companies WHERE company_number = ?;",
            (number,),
            one=True,
        )
        profile, error = fetch_companies_house_profile(number)
        if error or not profile:
            results.append({
                'company_number': number,
                'status': 'error',
                'message': error or 'Company not found at Companies House',
            })
            failed += 1
            continue

        director = fetch_companies_house_primary_director(number) or (client.get('full_name') if client else None) or 'Director'
        mapped_status = companies_house_status_value(profile.get('status'))
        inc_date = profile.get('inc_date') or datetime.date.today().isoformat()
        if len(inc_date) < 10:
            inc_date = datetime.date.today().isoformat()

        if existing:
            if update_existing:
                execute_db(
                    """
                    UPDATE companies
                    SET name = ?, status = ?, inc_date = ?, director = ?, reg_office = ?
                    WHERE id = ?;
                    """,
                    (
                        profile['name'],
                        mapped_status,
                        inc_date,
                        director,
                        profile.get('reg_office') or 'United Kingdom',
                        existing['id'],
                    ),
                )
                results.append({
                    'company_number': profile['company_number'],
                    'name': profile['name'],
                    'status': 'updated',
                    'company_id': existing['id'],
                })
                updated += 1
            else:
                results.append({
                    'company_number': profile['company_number'],
                    'name': profile['name'],
                    'status': 'skipped',
                    'message': 'Already in portal',
                    'company_id': existing['id'],
                })
                skipped += 1
            continue

        if match_company_for_client(client_id, profile['name']):
            results.append({
                'company_number': profile['company_number'],
                'name': profile['name'],
                'status': 'skipped',
                'message': 'This client already has a company with that name',
            })
            skipped += 1
            continue

        company_id = execute_db(
            """
            INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                client_id,
                profile['name'],
                profile['company_number'],
                mapped_status,
                inc_date,
                director,
                profile.get('reg_office') or 'United Kingdom',
                package,
                'Good Standing',
            ),
        )
        link_orphaned_formation_orders(user_id=client_id)
        results.append({
            'company_number': profile['company_number'],
            'name': profile['name'],
            'status': 'imported',
            'company_id': company_id,
        })
        imported += 1

    if actor and (imported or updated):
        log_activity(
            actor,
            'COMPANIES_IMPORTED',
            'companies',
            None,
            f"WebFiling import: {imported} added, {updated} updated, {skipped} skipped, {failed} failed",
        )

    return {
        'imported': imported,
        'updated': updated,
        'skipped': skipped,
        'failed': failed,
        'results': results,
    }, None


def ensure_client_for_manual_company(data, actor=None):
    extras = data if isinstance(data, dict) else {}
    client_id = optional_record_id(extras.get('client_id') or extras.get('user_id'))
    if client_id:
        client = query_db("SELECT id, full_name, role FROM users WHERE id = ?;", (client_id,), one=True)
        if not client or client.get('role') != 'CLIENT':
            return None, None, 'Select a client account'
        return client_id, client, None

    client_mode = (extras.get('client_mode') or '').strip().lower()
    new_name = (extras.get('new_client_full_name') or extras.get('client_full_name') or '').strip()
    new_email = (extras.get('new_client_email') or extras.get('client_email') or '').strip().lower()
    new_phone = (extras.get('new_client_phone') or extras.get('client_phone') or '').strip() or None
    if client_mode != 'new':
        return None, None, 'Select the client this company belongs to, or choose New client'

    if not new_name:
        return None, None, 'Enter the new client full name'
    if not new_email or not STAFF_EMAIL_RE.match(new_email):
        return None, None, 'Enter a valid client email'

    existing = query_db("SELECT id, full_name, role FROM users WHERE LOWER(email) = ?;", (new_email,), one=True)
    if existing:
        if existing.get('role') != 'CLIENT':
            return None, None, 'That email belongs to a staff account'
        return existing['id'], existing, None

    local_id = f"local_client_{uuid.uuid4().hex[:16]}"
    client_id = execute_db("""
        INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, country, role, status, last_synced_at)
        VALUES (?, ?, ?, ?, ?, 'United Kingdom', 'CLIENT', 'Active', CURRENT_TIMESTAMP);
    """, (local_id, new_email, unusable_password_hash(), new_name, new_phone))
    client = query_db("SELECT id, full_name, role FROM users WHERE id = ?;", (client_id,), one=True)
    if actor:
        log_activity(actor, 'USER_CREATED', 'users', str(client_id), f"Created client {new_email} while adding company")
    return client_id, client, None


def create_manual_company(data, actor=None):
    extras = data if isinstance(data, dict) else {}
    client_id, client, client_err = ensure_client_for_manual_company(extras, actor)
    if client_err:
        return None, client_err
    name = (extras.get('name') or extras.get('company_name') or '').strip()
    if len(name) < 2 or name.lower() in ('united kingdom', 'uk', 'none', 'n/a'):
        return None, 'Enter the registered company name'
    if match_company_for_client(client_id, name):
        return None, 'This client already has a company with that name'
    requested_number = (extras.get('company_number') or '').strip()
    if requested_number and query_db("SELECT id FROM companies WHERE company_number = ?;", (requested_number,), one=True):
        return None, 'That company number is already on another card'
    raw_status = str(extras.get('company_status') or extras.get('status') or 'Active').lower()
    status_map = {
        'active': 'Active',
        'dissolved': 'Dissolved',
        'liquidation': 'Liquidation',
        'receivership': 'Receivership',
        'administration': 'Administration',
        'converted-closed': 'Closed',
        'closed': 'Closed',
        'pending': 'Active',
    }
    inc_date = str(extras.get('inc_date') or extras.get('date_of_creation') or '')[:10]
    if len(inc_date) < 10:
        inc_date = datetime.date.today().isoformat()
    company_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            client_id,
            name,
            unused_company_number(requested_number or None),
            status_map.get(raw_status, 'Active'),
            inc_date,
            (extras.get('director') or client.get('full_name') or 'Director').strip() or 'Director',
            (extras.get('reg_office') or extras.get('address') or 'United Kingdom').strip() or 'United Kingdom',
            (extras.get('package') or 'Historical Registration').strip() or 'Historical Registration',
            (extras.get('account_status') or 'Good Standing').strip() or 'Good Standing',
        ),
    )
    link_orphaned_formation_orders(user_id=client_id)
    return company_id, None


def create_company_from_pending_order(order_id, company_name, extras=None):
    name = (company_name or '').strip()
    extras = extras if isinstance(extras, dict) else {}
    if len(name) < 2 or name.lower() in ('united kingdom', 'uk', 'none', 'n/a'):
        return None, 'Enter the registered company name'
    order = query_db("""
        SELECT o.*, u.full_name as client_name, u.email as client_email
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE o.id = ?;
    """, (order_id,), one=True)
    if not order:
        return None, 'Order not found'
    guest_id = guest_unlinked_user_id()
    if guest_id and order['user_id'] == guest_id:
        return None, 'Guest website orders cannot create a company card'
    if order.get('company_id'):
        return order['company_id'], None
    if not is_company_registration_order({}, order.get('service_name'), []):
        return None, 'This order is not a company registration'
    o_data = {
        'company_name': name,
        'company_number': extras.get('company_number'),
        'company_status': extras.get('status') or extras.get('company_status'),
        'inc_date': extras.get('inc_date'),
        'billing_address': extras.get('reg_office'),
    }
    company_id = ensure_company_from_registration_order(
        order['user_id'],
        order.get('client_name') or order.get('client_email') or 'Director',
        o_data,
        order.get('service_name'),
        [],
        str(order.get('woocommerce_order_id') or '') or None,
    )
    if not company_id:
        return None, 'Could not create the company card'
    execute_db(
        "UPDATE orders SET company_id = ?, portfolio_hidden = 0 WHERE id = ?;",
        (company_id, order['id']),
    )
    return company_id, None


def unused_company_number(preferred=None):
    candidate = str(preferred or '').strip()
    if candidate and not query_db("SELECT id FROM companies WHERE company_number = ?;", (candidate,), one=True):
        return candidate
    for _ in range(8):
        candidate = f"REG-{uuid.uuid4().hex[:10].upper()}"
        if not query_db("SELECT id FROM companies WHERE company_number = ?;", (candidate,), one=True):
            return candidate
    return f"REG-{int(datetime.datetime.now().timestamp())}"


def ensure_company_from_registration_order(client_id, client_name, o_data, service_name, line_items, wc_order_id=None):
    company_name = extract_order_company_name(o_data)
    if not client_id or not company_name:
        return None
    wc_id = str(wc_order_id or (o_data or {}).get('woocommerce_order_id') or '').strip() or None
    if is_company_card_dismissed(
        client_id,
        company_name,
        (o_data or {}).get('company_number') or (o_data or {}).get('registration_number'),
        wc_id,
    ):
        return None
    existing = match_company_for_client(client_id, company_name)
    if existing:
        return existing
    if not is_company_registration_order(o_data, service_name, line_items):
        return None
    company_number = unused_company_number(
        o_data.get('company_number') or o_data.get('registration_number') or (f"REG-{wc_order_id}" if wc_order_id else None)
    )
    address_parts = [
        o_data.get('billing_address_1') or o_data.get('address_1'),
        o_data.get('billing_city') or o_data.get('city'),
        o_data.get('billing_postcode') or o_data.get('postcode'),
        o_data.get('billing_country') or o_data.get('country') or 'United Kingdom',
    ]
    address = o_data.get('billing_address') or o_data.get('address') or ', '.join(
        str(part).strip() for part in address_parts if part and str(part).strip()
    ) or 'United Kingdom'
    created_at = str(o_data.get('created_at') or o_data.get('inc_date') or '')
    inc_date = created_at[:10] if len(created_at) >= 10 else datetime.date.today().isoformat()
    raw_status = str(o_data.get('company_status') or o_data.get('status') or 'Active').lower()
    status_map = {
        'active': 'Active',
        'dissolved': 'Dissolved',
        'liquidation': 'Liquidation',
        'receivership': 'Receivership',
        'administration': 'Administration',
        'converted-closed': 'Closed',
        'closed': 'Closed',
    }
    company_status = status_map.get(raw_status, 'Active')
    company_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending');
        """,
        (
            client_id,
            company_name,
            company_number,
            company_status,
            inc_date,
            client_name or 'Director',
            address,
            service_name or 'Company Formation',
        ),
    )
    return company_id


def backfill_companies_from_registration_webhooks():
    rows = query_db("""
        SELECT payload FROM webhook_events
        WHERE event_type IN ('order.created', 'order.updated')
        ORDER BY id DESC LIMIT 400;
    """) or []
    for row in rows:
        try:
            payload = json.loads(row.get('payload') or '{}')
        except Exception:
            continue
        o_data = payload.get('data', payload) if isinstance(payload, dict) else None
        if not isinstance(o_data, dict):
            continue
        line_items = parse_line_items_from_payload(o_data)
        service_name = (o_data.get('service_name') or '').strip()
        if not service_name and line_items:
            service_name = ', '.join(item['product_name'] for item in line_items)
        if not is_company_registration_order(o_data, service_name, line_items):
            continue
        company_name = extract_order_company_name(o_data)
        if not company_name:
            continue
        email = (o_data.get('email') or '').strip()
        wp_user_id = str(o_data.get('wordpress_user_id') or '')
        client = None
        if wp_user_id:
            client = query_db("SELECT id, full_name FROM users WHERE wordpress_user_id = ?;", (wp_user_id,), one=True)
        if not client and email:
            client = query_db("SELECT id, full_name FROM users WHERE email = ?;", (email,), one=True)
        guest = query_db("SELECT id FROM users WHERE email = 'guest.unlinked@brixenconsultants.com';", one=True)
        if not client or (guest and client['id'] == guest['id']):
            continue
        company_id = ensure_company_from_registration_order(
            client['id'],
            client.get('full_name') or email,
            o_data,
            service_name,
            line_items,
            str(o_data.get('woocommerce_order_id') or '') or None,
        )
        order_num = o_data.get('order_number')
        if company_id and order_num:
            execute_db(
                "UPDATE orders SET company_id = COALESCE(company_id, ?) WHERE order_number = ? AND user_id = ?;",
                (company_id, order_num, client['id']),
            )


def match_service_id(product_name):
    if not product_name:
        return None
    rec = query_db("SELECT id FROM services WHERE LOWER(name) = LOWER(?) LIMIT 1;", (product_name.strip(),), one=True)
    return rec['id'] if rec else None


def upsert_woocommerce_product(p_data):
    """Upsert a WooCommerce product into the CRM services catalog."""
    if not isinstance(p_data, dict):
        return {'created': False, 'updated': False}
    name = (p_data.get('name') or p_data.get('product_name') or '').strip()
    if not name:
        return {'created': False, 'updated': False}
    wp_product_id = str(p_data.get('woocommerce_product_id') or p_data.get('product_id') or '').strip() or None
    category = (p_data.get('category') or p_data.get('category_name') or 'General').strip() or 'General'
    description = (p_data.get('description') or p_data.get('short_description') or name).strip() or name
    try:
        price = float(p_data.get('price') if p_data.get('price') not in (None, '') else 0)
    except (TypeError, ValueError):
        price = 0.0
    status_raw = str(p_data.get('status') or 'publish').strip().lower()
    status = 'Active' if status_raw in ('publish', 'active', '1', 'true') else 'Inactive'
    duration = (p_data.get('duration') or '12 Months').strip() or '12 Months'

    existing = None
    if wp_product_id:
        existing = query_db(
            "SELECT id FROM services WHERE woocommerce_product_id = ? LIMIT 1;",
            (wp_product_id,),
            one=True,
        )
    if not existing:
        existing = query_db(
            "SELECT id FROM services WHERE LOWER(name) = LOWER(?) LIMIT 1;",
            (name,),
            one=True,
        )
    if existing:
        execute_db("""
            UPDATE services
            SET name = ?, description = ?, category = ?, price = ?, duration = ?, status = ?,
                woocommerce_product_id = COALESCE(?, woocommerce_product_id)
            WHERE id = ?;
        """, (name, description, category, price, duration, status, wp_product_id, existing['id']))
        return {'created': False, 'updated': True, 'service_id': existing['id']}
    sid = execute_db("""
        INSERT INTO services (
            name, description, category, price, duration, status, featured, vat_rate, renewal_period, woocommerce_product_id
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 0.20, 'Annual', ?);
    """, (name, description, category, price, duration, status, wp_product_id))
    return {'created': True, 'updated': False, 'service_id': sid}


def shift_calendar_month(year, month, delta):
    total = year * 12 + (month - 1) + delta
    return total // 12, (total % 12) + 1


def build_monthly_revenue_chart(months=6):
    today = datetime.date.today()
    rows = query_db("""
        SELECT strftime('%Y-%m', created_at) AS month,
               COUNT(*) AS cnt,
               COALESCE(SUM(CASE WHEN status NOT IN ('Cancelled', 'Refunded') THEN total ELSE 0 END), 0) AS rev
        FROM orders
        GROUP BY strftime('%Y-%m', created_at);
    """)
    by_month = {}
    for row in rows or []:
        key = row.get('month')
        if not key:
            continue
        by_month[str(key)] = {
            'cnt': int(row.get('cnt') or 0),
            'rev': float(row.get('rev') or 0),
        }
    series = []
    for offset in range(months - 1, -1, -1):
        year, month = shift_calendar_month(today.year, today.month, -offset)
        key = f'{year:04d}-{month:02d}'
        item = by_month.get(key, {'cnt': 0, 'rev': 0.0})
        series.append({
            'month': key,
            'label': datetime.date(year, month, 1).strftime('%b %Y'),
            'cnt': item['cnt'],
            'rev': round(float(item['rev']), 2),
        })
    return series


def ensure_order_timeline(order_id):
    existing = query_db("SELECT id FROM order_timeline WHERE order_id = ? LIMIT 1;", (order_id,), one=True)
    if existing:
        return
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    steps = [
        ('Order Placed', 'Completed', now),
        ('Payment Confirmed', 'Completed', now),
        ('Processing', 'Current', now),
        ('Documents Received', 'Pending', None),
        ('Service Completed', 'Pending', None),
    ]
    for title, step_status, step_date in steps:
        execute_db(
            "INSERT INTO order_timeline (order_id, title, status, step_date) VALUES (?, ?, ?, ?);",
            (order_id, title, step_status, step_date)
        )


def append_order_history(order_id, title):
    if not order_id or not title:
        return
    execute_db(
        "INSERT INTO order_timeline (order_id, title, status, step_date) VALUES (?, ?, 'Completed', CURRENT_TIMESTAMP);",
        (order_id, title)
    )


def _qs_first(qs, key, default=''):
    return (qs.get(key, [default])[0] or default).strip()


def resolve_admin_order_dates(qs):
    today = datetime.date.today()
    preset = _qs_first(qs, 'date_preset')
    date_from = _qs_first(qs, 'date_from')
    date_to = _qs_first(qs, 'date_to')
    month = _qs_first(qs, 'month')
    year = _qs_first(qs, 'year')
    if preset == 'today':
        return str(today), str(today)
    if preset == 'yesterday':
        day = today - datetime.timedelta(days=1)
        return str(day), str(day)
    if preset == 'this_week':
        start = today - datetime.timedelta(days=today.weekday())
        return str(start), str(today)
    if preset == 'this_month':
        return f"{today.year:04d}-{today.month:02d}-01", str(today)
    if preset == 'last_month':
        first_this = today.replace(day=1)
        last_end = first_this - datetime.timedelta(days=1)
        return f"{last_end.year:04d}-{last_end.month:02d}-01", str(last_end)
    if preset == 'this_year':
        return f"{today.year:04d}-01-01", str(today)
    if month:
        try:
            m = int(month)
            y = int(year) if year else today.year
            if m < 1 or m > 12 or y < 2000 or y > 2100:
                return None, None
            last = calendar.monthrange(y, m)[1]
            return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"
        except ValueError:
            return None, None
    if year and not date_from and not date_to:
        try:
            y = int(year)
            return f"{y:04d}-01-01", f"{y:04d}-12-31"
        except ValueError:
            return None, None
    return (date_from or None), (date_to or None)


def manager_order_scope(user):
    if user and user.get('role') == 'MANAGER':
        return (
            "(o.assigned_staff_id = ? OR o.assigned_staff_id IS NULL OR o.assigned_staff_id IN (SELECT id FROM users WHERE role = 'STAFF'))",
            [user['id']]
        )
    return '', []


def build_admin_order_filters(user, qs):
    clauses = []
    params = []
    scope_sql, scope_params = manager_order_scope(user)
    if scope_sql:
        clauses.append(scope_sql)
        params.extend(scope_params)

    search = _qs_first(qs, 'search')
    if search:
        term = f"%{search}%"
        clauses.append("""(
            o.order_number LIKE ? OR o.service_name LIKE ? OR u.full_name LIKE ? OR u.email LIKE ?
            OR c.name LIKE ?
            OR EXISTS (SELECT 1 FROM order_line_items li WHERE li.order_id = o.id AND (li.product_name LIKE ? OR li.category_name LIKE ?))
        )""")
        params.extend([term, term, term, term, term, term, term])

    order_number = _qs_first(qs, 'order_number')
    if order_number:
        clauses.append("o.order_number LIKE ?")
        params.append(f"%{order_number}%")

    customer_id = _qs_first(qs, 'customer_id')
    if customer_id:
        cid, err = to_optional_int(customer_id, 'customer_id')
        if not err and cid:
            clauses.append("o.user_id = ?")
            params.append(cid)

    status_filter = _qs_first(qs, 'status')
    status_group = _qs_first(qs, 'status_group')
    if status_filter:
        clauses.append("o.status = ?")
        params.append(status_filter)
    elif status_group == 'pending':
        clauses.append("o.status IN ('Pending', 'Pending Verification')")
    elif status_group == 'in_progress':
        clauses.append("o.status IN ('Processing', 'In Progress')")
    elif status_group == 'completed':
        clauses.append("o.status = 'Completed'")

    product = _qs_first(qs, 'product')
    if product:
        clauses.append("(o.service_name = ? OR EXISTS (SELECT 1 FROM order_line_items li WHERE li.order_id = o.id AND li.product_name = ?))")
        params.extend([product, product])

    category = _qs_first(qs, 'category')
    if category:
        clauses.append("""(
            EXISTS (SELECT 1 FROM order_line_items li WHERE li.order_id = o.id AND li.category_name = ?)
            OR EXISTS (SELECT 1 FROM services sv WHERE sv.id = o.service_id AND sv.category = ?)
        )""")
        params.extend([category, category])

    payment_status = _qs_first(qs, 'payment_status')
    if payment_status == 'Unpaid':
        clauses.append("NOT EXISTS (SELECT 1 FROM invoices inv WHERE inv.order_id = o.id AND inv.status = 'Paid')")
    elif payment_status:
        clauses.append("EXISTS (SELECT 1 FROM invoices inv WHERE inv.order_id = o.id AND inv.status = ?)")
        params.append(payment_status)

    progress_min = _qs_first(qs, 'progress_min')
    progress_max = _qs_first(qs, 'progress_max')
    if progress_min:
        try:
            clauses.append("o.progress_percent >= ?")
            params.append(int(progress_min))
        except ValueError:
            pass
    if progress_max:
        try:
            clauses.append("o.progress_percent <= ?")
            params.append(int(progress_max))
        except ValueError:
            pass

    date_from, date_to = resolve_admin_order_dates(qs)
    date_clauses = []
    date_params = []
    if date_from:
        date_clauses.append("date(o.created_at) >= date(?)")
        date_params.append(date_from)
    if date_to:
        date_clauses.append("date(o.created_at) <= date(?)")
        date_params.append(date_to)

    return clauses, params, date_clauses, date_params


def notify_staff_new_order(order_number, client_name):
    staff = query_db("SELECT id FROM users WHERE role IN ('SUPER_ADMIN', 'ADMIN') AND status = 'Active';")
    for row in staff:
        execute_db("""
            INSERT INTO notifications (user_id, title, message, type, link)
            VALUES (?, ?, ?, ?, ?);
        """, (
            row['id'],
            'New website order',
            f"Order {order_number} from {client_name} is in the CRM.",
            'order_update',
            '#admin-orders'
        ))


def is_plausible_uk_phone(value):
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    lower = text.lower()
    if re.search(r'\.(jpe?g|png|pdf|gif|webp|docx?|zip|bmp|svg)\b', lower):
        return False
    if '@' in text or re.search(r'https?://', lower):
        return False
    if re.search(r'(?i)(photo|image|upload|document|attachment|proof|passport|cnic|filename|\.jpg|\.pdf)', lower):
        return False
    digits = re.sub(r'\D', '', text)
    if len(digits) < 10 or len(digits) > 13:
        return False
    if digits.startswith('44'):
        national = digits[2:]
        return len(national) == 10 and national[0] in '123789'
    if digits.startswith('0'):
        return len(digits) == 11 and digits[1] in '123789'
    return len(digits) == 10 and digits[0] in '123789'


def format_uk_phone(value):
    if not is_plausible_uk_phone(value):
        return ''
    digits = re.sub(r'\D', '', str(value).strip())
    if digits.startswith('44'):
        national = digits[2:]
    elif digits.startswith('0'):
        national = digits[1:]
    else:
        national = digits
    if len(national) > 10:
        national = national[-10:]
    if len(national) == 10:
        return f"+44 {national[:4]} {national[4:]}"
    return f"+44 {national}"


def billing_phone_from_webhook_payload(data):
    if not isinstance(data, dict):
        return ''
    meta = data.get('meta')
    if isinstance(meta, dict):
        for key in ('_cfs_uk_contact_number', '_cfs_phone', '_cfs_contact_number', '_cfs_mobile', 'uk_contact_number'):
            formatted = format_uk_phone(meta.get(key))
            if formatted:
                return formatted
    for key in ('phone', 'billing_phone', 'shipping_phone', 'mobile', 'contact_phone', 'telephone'):
        val = (data.get(key) or '').strip()
        if val:
            formatted = format_uk_phone(val)
            if formatted:
                return formatted
    meta = data.get('meta')
    if isinstance(meta, dict):
        for key, val in meta.items():
            if not isinstance(val, str):
                continue
            val = val.strip()
            if not val:
                continue
            kl = str(key).lower()
            if any(token in kl for token in ('photo', 'image', 'upload', 'attachment', 'proof', 'passport', 'file', 'url', 'name')):
                continue
            if any(token in kl for token in ('phone', 'mobile', 'tel', 'contact', 'whatsapp')):
                formatted = format_uk_phone(val)
                if formatted:
                    return formatted
            formatted = format_uk_phone(val)
            if formatted:
                return formatted
    for item in data.get('line_items') or []:
        if not isinstance(item, dict):
            continue
        item_meta = item.get('item_meta') or {}
        if not isinstance(item_meta, dict):
            continue
        for key, val in item_meta.items():
            if not isinstance(val, str):
                continue
            val = val.strip()
            if not val:
                continue
            kl = str(key).lower()
            if any(token in kl for token in ('photo', 'image', 'upload', 'attachment', 'proof', 'passport', 'file', 'url', 'name')):
                continue
            if any(token in kl for token in ('phone', 'mobile', 'tel', 'contact', 'whatsapp')):
                formatted = format_uk_phone(val)
                if formatted:
                    return formatted
    for note in data.get('order_notes') or []:
        if not isinstance(note, str):
            continue
        match = re.search(r'(?:Phone|Mobile|Tel|Telephone|Contact(?: number)?)\s*:\s*([^\n\r]+)', note, re.I)
        if match:
            formatted = format_uk_phone(match.group(1).strip())
            if formatted:
                return formatted
    return ''


def normalize_date_of_birth(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %B %Y', '%d %b %Y'):
        try:
            return datetime.datetime.strptime(text, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if match:
        return match.group(1)
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', text)
    if match:
        try:
            return datetime.datetime.strptime(match.group(1), '%d/%m/%Y').strftime('%Y-%m-%d')
        except ValueError:
            pass
    return text[:10] if len(text) >= 8 else text


def date_of_birth_from_webhook_payload(data):
    if not isinstance(data, dict):
        return None
    for key in ('date_of_birth', 'dob', 'birth_date', 'birthdate'):
        val = normalize_date_of_birth(data.get(key))
        if val:
            return val
    meta = data.get('meta')
    if isinstance(meta, dict):
        for key, val in meta.items():
            if not isinstance(val, str):
                continue
            kl = str(key).lower()
            if any(token in kl for token in ('date_of_birth', 'dob', 'birth')):
                parsed = normalize_date_of_birth(val)
                if parsed:
                    return parsed
    for note in data.get('order_notes') or []:
        if not isinstance(note, str):
            continue
        match = re.search(r'(?:DOB|Date of birth)\s*:\s*([^\n\r]+)', note, re.I)
        if match:
            parsed = normalize_date_of_birth(match.group(1).strip())
            if parsed:
                return parsed
    return None


CHECKOUT_META_LABELS = {
    '_cfs_company_name': 'Desired company name',
    '_cfs_package_name': 'Package',
    '_cfs_sic_code': 'SIC code',
    '_cfs_business_description': 'Business activity',
    '_cfs_director_name': 'Director name',
    '_cfs_date_of_birth': 'Date of birth',
    '_cfs_passport_cnic': 'Passport / CNIC',
    '_cfs_issuance_country': 'Issuance country',
    '_cfs_registered_email': 'Email (form)',
    '_cfs_uk_contact_number': 'UK contact number',
    '_cfs_company_role': 'Company role',
    '_cfs_address_street': 'Address line 1',
    '_cfs_address_line2': 'Address line 2',
    '_cfs_address_city': 'City',
    '_cfs_address_state': 'County / state',
    '_cfs_address_zip': 'Postcode',
    '_cfs_address_country': 'Country',
    '_cfs_is_pep': 'Politically exposed person (PEP)',
    '_cfs_pep_associate': 'PEP associate',
    '_cfs_subject_to_sanctions': 'Subject to sanctions',
    '_cfs_financial_crime': 'Financial crime declaration',
    '_cfs_source_of_funds': 'Source of funds',
}

CHECKOUT_META_SKIP = {
    '_brixen_order_kind', '_cfs_brixen_service', '_cfs_order_type', '_cfs_non_refund_ack',
    '_cfs_product_id', '_cfs_photo_id_1_name', '_cfs_photo_id_1_url',
    '_cfs_address_proof_1_name', '_cfs_address_proof_1_url',
}

CHECKOUT_LABEL_ALIASES = {
    'customer name (checkout)': 'director name',
    'customer': 'director name',
    'director': 'director name',
    'dob': 'date of birth',
    'phone': 'uk contact number',
    'email': 'email (form)',
    'role': 'company role',
    'sic': 'sic code',
    'desired company name': 'desired company name',
    'package': 'package',
}


def checkout_field_key(label):
    return CHECKOUT_LABEL_ALIASES.get(str(label or '').strip().lower(), str(label or '').strip().lower())


def checkout_form_value_display(label, value):
    text = str(value or '').strip()
    if not text:
        return ''
    if label == 'UK contact number':
        formatted = format_uk_phone(text)
        return formatted or text
    if label == 'Date of birth':
        parsed = normalize_date_of_birth(text)
        if parsed:
            try:
                dt = datetime.datetime.strptime(parsed[:10], '%Y-%m-%d')
                return dt.strftime('%d/%m/%Y')
            except ValueError:
                return parsed
    return text


def checkout_form_fields_from_payload(data):
    if not isinstance(data, dict):
        return []
    fields = []
    seen = set()

    def add(label, value):
        label = str(label or '').strip()
        value = checkout_form_value_display(label, value)
        if not label or not value or value.lower() in ('none', 'n/a', '—'):
            return
        key = checkout_field_key(label)
        if key in seen:
            return
        seen.add(key)
        fields.append({'label': label, 'value': value})

    meta = data.get('meta') or {}
    if isinstance(meta, dict):
        address_parts = []
        for mk, mv in meta.items():
            if mk in CHECKOUT_META_SKIP:
                continue
            if mv in (None, '') or not isinstance(mv, (str, int, float, bool)):
                continue
            text = str(mv).strip()
            if not text:
                continue
            mkl = mk.lower()
            if mkl.endswith('_url') or (mkl.endswith('_name') and any(token in mkl for token in ('passport', 'proof', 'address', 'photo', 'file', 'statement'))):
                continue
            if mk == '_cfs_source_of_funds':
                if text.startswith('a:'):
                    continue
                try:
                    parsed = json.loads(text)
                except (TypeError, ValueError):
                    parsed = text
                if isinstance(parsed, list):
                    add('Source of funds', ', '.join(str(item).strip() for item in parsed if str(item).strip()))
                else:
                    add('Source of funds', text)
                continue
            if mk in CHECKOUT_META_LABELS and mk.startswith('_cfs_address_'):
                address_parts.append(text)
                continue
            if mk.startswith('_cfs_') or mk.startswith('_apff_'):
                label = CHECKOUT_META_LABELS.get(mk) or mk.replace('_cfs_', '').replace('_apff_', '').replace('_', ' ').title()
                add(label, text)
        if address_parts:
            add('Registered address (form)', ', '.join(dict.fromkeys(part for part in address_parts if part)))

    for note in data.get('order_notes') or []:
        if not isinstance(note, str):
            continue
        if 'company formation form' not in note.lower() and 'desired company name:' not in note.lower():
            continue
        for line in note.splitlines():
            if ':' not in line:
                continue
            label, _, value = line.partition(':')
            label = label.strip()
            value = value.strip()
            if not label or not value:
                continue
            if label.lower().startswith('company formation form'):
                continue
            add(label, value)

    billing_bits = [
        data.get('billing_address_1') or data.get('address_1'),
        data.get('billing_address_2') or data.get('address_2'),
        data.get('billing_city') or data.get('city'),
        data.get('billing_postcode') or data.get('postcode'),
        data.get('billing_country') or data.get('country'),
    ]
    billing = ', '.join(str(part).strip() for part in billing_bits if part and str(part).strip())
    if billing and checkout_field_key('Registered address (form)') not in seen and checkout_field_key('Billing address') not in seen:
        add('Billing address', billing)

    if data.get('full_name') and checkout_field_key('Director name') not in seen:
        add('Director name', str(data.get('full_name')).strip())
    phone = billing_phone_from_webhook_payload(data)
    if phone and checkout_field_key('UK contact number') not in seen:
        add('UK contact number', phone)
    dob = date_of_birth_from_webhook_payload(data)
    if dob and checkout_field_key('Date of birth') not in seen:
        add('Date of birth', dob)
    form_email = (meta.get('_cfs_registered_email') if isinstance(meta, dict) else None) or data.get('email')
    if form_email and checkout_field_key('Email (form)') not in seen:
        add('Email (form)', str(form_email).strip())

    return fields


def persist_order_checkout_form(order_id, o_data):
    if not order_id:
        return []
    fields = checkout_form_fields_from_payload(o_data)
    if fields:
        execute_db(
            "UPDATE orders SET checkout_form_json = ? WHERE id = ?;",
            (json.dumps(fields), order_id),
        )
    return fields


def webhook_payload_for_order(order_number, woocommerce_order_id=None):
    needle = str(order_number or '').strip()
    wc_id = str(woocommerce_order_id or '').strip()
    rows = query_db("""
        SELECT payload FROM webhook_events
        WHERE event_type IN ('order.created', 'order.updated', 'payment.completed')
        ORDER BY id DESC LIMIT 400;
    """) or []
    for row in rows:
        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError):
            continue
        data = payload.get('data', payload)
        if not isinstance(data, dict):
            continue
        data_order = str(data.get('order_number') or '')
        data_wc = str(data.get('woocommerce_order_id') or '')
        if needle and data_order != needle and needle not in data_order:
            if not wc_id or data_wc != wc_id:
                continue
        if isinstance(data.get('meta'), dict) or data.get('order_notes'):
            return data
    return None


def load_order_checkout_form(order_row):
    if not order_row:
        return []
    order_id = order_row.get('id')
    payload = webhook_payload_for_order(order_row.get('order_number'), order_row.get('woocommerce_order_id'))
    if payload:
        fields = persist_order_checkout_form(order_id, payload)
        if fields:
            return fields
    stored = order_row.get('checkout_form_json')
    if stored:
        try:
            parsed = json.loads(stored)
            if isinstance(parsed, list) and parsed:
                return parsed
        except (TypeError, ValueError):
            pass
    wc_order_id = str(order_row.get('woocommerce_order_id') or '').strip()
    if wc_order_id:
        wp_data = fetch_wordpress_order_payload(wc_order_id)
        if wp_data:
            fields = persist_order_checkout_form(order_id, wp_data)
            if fields:
                return fields
    return []


def sync_client_checkout_profile(user_id, o_data, order_id=None):
    if not user_id or not isinstance(o_data, dict):
        return
    phone = billing_phone_from_webhook_payload(o_data)
    dob = date_of_birth_from_webhook_payload(o_data)
    if phone:
        execute_db("UPDATE users SET phone = ? WHERE id = ?;", (phone, user_id))
    if dob:
        execute_db("UPDATE users SET date_of_birth = ? WHERE id = ?;", (dob, user_id))
    if order_id and (phone or dob):
        execute_db(
            "UPDATE orders SET checkout_phone = COALESCE(?, checkout_phone), checkout_dob = COALESCE(?, checkout_dob), website_phone_checked_at = CASE WHEN ? IS NOT NULL THEN NULL ELSE website_phone_checked_at END WHERE id = ?;",
            (phone or None, dob or None, phone or None, order_id)
        )


def backfill_order_checkout_from_webhooks(user_id, order_id, order_number, woocommerce_order_id=None):
    existing_user = query_db("SELECT phone, date_of_birth FROM users WHERE id = ?;", (user_id,), one=True) if user_id else None
    existing_order = query_db(
        "SELECT checkout_phone, checkout_dob, website_checkout_pulled_at FROM orders WHERE id = ?;",
        (order_id,),
        one=True
    ) if order_id else None
    has_phone = (existing_user or {}).get('phone') or (existing_order or {}).get('checkout_phone')
    has_dob = (existing_user or {}).get('date_of_birth') or (existing_order or {}).get('checkout_dob')
    if has_phone and has_dob:
        return {
            'phone': has_phone,
            'date_of_birth': has_dob,
        }
    if (existing_order or {}).get('website_checkout_pulled_at'):
        return {'phone': has_phone, 'date_of_birth': has_dob}
    needle = order_number or ''
    wc_id = str(woocommerce_order_id or '').strip()
    rows = query_db("""
        SELECT payload FROM webhook_events
        WHERE event_type IN ('order.created', 'order.updated', 'payment.completed', 'user.created', 'user.updated')
        ORDER BY id DESC LIMIT 300;
    """)
    found_phone = has_phone
    found_dob = has_dob
    for row in rows:
        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError):
            continue
        data = payload.get('data', payload)
        if not isinstance(data, dict):
            continue
        data_order = str(data.get('order_number') or '')
        data_wc = str(data.get('woocommerce_order_id') or '')
        data_email = str(data.get('email') or '')
        if needle and data_order != needle and needle not in data_order:
            if not wc_id or data_wc != wc_id:
                if not user_id or str(data.get('wordpress_user_id') or '') not in ('', str(user_id)):
                    continue
        phone = billing_phone_from_webhook_payload(data) if not found_phone else None
        dob = date_of_birth_from_webhook_payload(data) if not found_dob else None
        if phone:
            execute_db("UPDATE users SET phone = ? WHERE id = ?;", (phone, user_id))
            if order_id:
                execute_db("UPDATE orders SET checkout_phone = ? WHERE id = ?;", (phone, order_id))
            found_phone = phone
        if dob:
            execute_db("UPDATE users SET date_of_birth = ? WHERE id = ?;", (dob, user_id))
            if order_id:
                execute_db("UPDATE orders SET checkout_dob = ? WHERE id = ?;", (dob, order_id))
            found_dob = dob
        if found_phone and found_dob:
            break
    return {'phone': found_phone, 'date_of_birth': found_dob}


def backfill_client_phone_from_webhooks(user_id, order_number, woocommerce_order_id=None):
    result = backfill_order_checkout_from_webhooks(user_id, None, order_number, woocommerce_order_id)
    return result.get('phone')


def sign_wordpress_order_request(wc_order_id, timestamp=None):
    ts = int(timestamp if timestamp is not None else datetime.datetime.now().timestamp())
    secret = wordpress_integration_secret()
    if not secret:
        return None, None
    wc_order_id = str(wc_order_id or '').strip()
    if not wc_order_id:
        return None, None
    signature = hmac.new(
        secret.encode('utf-8'),
        f"{wc_order_id}|{ts}".encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return ts, signature


def fetch_wordpress_order_payload(wc_order_id):
    wc_order_id = str(wc_order_id or '').strip()
    if not wc_order_id:
        return None
    ts, signature = sign_wordpress_order_request(wc_order_id)
    if not signature:
        return None
    qs = urllib.parse.urlencode({
        'timestamp': ts,
        'signature': signature,
    })
    base = wordpress_base_url()
    urls = [
        f"{base}/wp-json/brixen-crm/v1/orders/{urllib.parse.quote(wc_order_id)}?{qs}",
        f"{base}/wp-admin/admin-post.php?action=brixen_crm_pull_order&order_id={urllib.parse.quote(wc_order_id)}&{qs}",
    ]
    for url in urls:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode('utf-8')
            payload = json.loads(body)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        data = payload.get('data')
        if isinstance(data, dict):
            return data
    return None


def refresh_order_checkout_from_wordpress(order_row):
    if not isinstance(order_row, dict):
        return {}
    wc_order_id = str(order_row.get('woocommerce_order_id') or '').strip()
    order_id = order_row.get('id')
    if not wc_order_id:
        return {}
    existing_user = query_db(
        "SELECT phone, date_of_birth FROM users WHERE id = ?;",
        (order_row.get('user_id'),),
        one=True
    ) if order_row.get('user_id') else None
    existing_order = query_db(
        "SELECT checkout_phone, checkout_dob, website_checkout_pulled_at, website_phone_checked_at FROM orders WHERE id = ?;",
        (order_id,),
        one=True
    ) if order_id else None
    has_phone = (existing_user or {}).get('phone') or (existing_order or {}).get('checkout_phone')
    has_dob = (existing_user or {}).get('date_of_birth') or (existing_order or {}).get('checkout_dob')
    if (existing_order or {}).get('website_checkout_pulled_at') and has_dob and ((has_phone) or (existing_order or {}).get('website_phone_checked_at')):
        return {'phone': has_phone, 'date_of_birth': has_dob}
    if has_phone and has_dob:
        if order_id:
            execute_db(
                "UPDATE orders SET website_checkout_pulled_at = COALESCE(website_checkout_pulled_at, CURRENT_TIMESTAMP) WHERE id = ?;",
                (order_id,)
            )
        return {'phone': has_phone, 'date_of_birth': has_dob}
    wp_data = fetch_wordpress_order_payload(wc_order_id)
    if order_id:
        execute_db(
            "UPDATE orders SET website_checkout_pulled_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (order_id,)
        )
    if not wp_data:
        if order_id and not has_phone:
            execute_db("UPDATE orders SET website_phone_checked_at = CURRENT_TIMESTAMP WHERE id = ?;", (order_id,))
        return {'phone': has_phone, 'date_of_birth': has_dob}
    phone = billing_phone_from_webhook_payload(wp_data) if not has_phone else None
    dob = date_of_birth_from_webhook_payload(wp_data) if not has_dob else None
    user_id = order_row.get('user_id')
    if user_id and (phone or dob):
        sync_client_checkout_profile(user_id, wp_data, order_id)
    elif order_id and (phone or dob):
        execute_db(
            "UPDATE orders SET checkout_phone = COALESCE(?, checkout_phone), checkout_dob = COALESCE(?, checkout_dob) WHERE id = ?;",
            (phone or None, dob or None, order_id)
        )
    if order_id and not phone and not has_phone:
        execute_db("UPDATE orders SET website_phone_checked_at = CURRENT_TIMESTAMP WHERE id = ?;", (order_id,))
    elif order_id and phone:
        execute_db("UPDATE orders SET website_phone_checked_at = NULL WHERE id = ?;", (order_id,))
    attachments_imported = 0
    if wp_data and order_id:
        persist_order_checkout_form(order_id, wp_data)
        company_id = order_row.get('company_id')
        try:
            attachments_imported = import_order_checkout_attachments(
                order_id,
                order_row.get('user_id'),
                company_id,
                wp_data,
            )
        except Exception as err:
            print(f"[CheckoutAttach] Refresh order {order_id}: {err}")
    return {
        'phone': phone or has_phone,
        'date_of_birth': dob or has_dob,
        'attachments_imported': attachments_imported,
    }


def upsert_woocommerce_order(o_data):
    order_num = o_data.get('order_number') or f"#GB{int(datetime.datetime.now().timestamp())}"
    wp_user_id = str(o_data.get('wordpress_user_id') or '')
    email = (o_data.get('email') or '').strip()
    line_items = parse_line_items_from_payload(o_data)
    incoming_service = (o_data.get('service_name') or '').strip()
    if not incoming_service and line_items:
        incoming_service = ', '.join(item['product_name'] for item in line_items)
    service_name = incoming_service or 'Corporate Formation Service'
    price = float(o_data.get('price', 150.00))
    total = float(o_data.get('total', price * 1.2))
    vat = total - price
    status = o_data.get('status') or 'Processing'
    if status not in ('Pending', 'Processing', 'In Progress', 'Completed', 'Cancelled', 'Refunded', 'Pending Verification'):
        status = 'Processing'
    wc_order_id = str(o_data.get('woocommerce_order_id') or '') or None
    company_name = o_data.get('company_name') or o_data.get('billing_company')

    client_rec = None
    if wp_user_id:
        client_rec = query_db("SELECT id, full_name FROM users WHERE wordpress_user_id = ?;", (wp_user_id,), one=True)
    if not client_rec and email:
        client_rec = query_db("SELECT id, full_name FROM users WHERE email = ?;", (email,), one=True)

    guest_pending = False
    billing_phone = billing_phone_from_webhook_payload(o_data)
    checkout_dob = date_of_birth_from_webhook_payload(o_data)
    if client_rec:
        cid = client_rec['id']
        client_name = client_rec.get('full_name') or email or 'Client'
        order_notes = o_data.get('notes', '')
        sync_client_checkout_profile(cid, o_data)
    else:
        guest_user = query_db("SELECT id FROM users WHERE email = 'guest.unlinked@brixenconsultants.com';", one=True)
        if not guest_user:
            cid = execute_db("""
                INSERT INTO users (wordpress_user_id, email, password_hash, full_name, role, status, last_synced_at)
                VALUES ('guest_unlinked', 'guest.unlinked@brixenconsultants.com', ?, 'Unlinked Guest Customer', 'CLIENT', 'Active', CURRENT_TIMESTAMP);
            """, (unusable_password_hash(),))
        else:
            cid = guest_user['id']
        client_name = email or 'Guest'
        order_notes = f"Guest Order (Email: {email}) - Pending Staff Verification"
        status = 'Pending Verification'
        guest_pending = True

    company_id = None
    if not guest_pending:
        company_id = match_company_for_client(cid, company_name)
        if not company_id:
            company_id = ensure_company_from_registration_order(
                cid, client_name, o_data, service_name, line_items, wc_order_id
            )
    service_id = match_service_id(line_items[0]['product_name'] if line_items else service_name)

    existing_ord = query_db("SELECT id, service_name FROM orders WHERE order_number = ?;", (order_num,), one=True)
    if existing_ord and not incoming_service:
        service_name = existing_ord.get('service_name') or service_name
    created = False
    if existing_ord:
        execute_db("""
            UPDATE orders SET status = ?, total = ?, price = ?, vat = ?, service_name = ?,
                company_id = COALESCE(?, company_id), service_id = COALESCE(?, service_id),
                woocommerce_order_id = COALESCE(?, woocommerce_order_id),
                checkout_phone = COALESCE(?, checkout_phone),
                checkout_dob = COALESCE(?, checkout_dob),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
        """, (status, total, price, vat, service_name, company_id, service_id, wc_order_id, billing_phone or None, checkout_dob, existing_ord['id']))
        ord_id = existing_ord['id']
    else:
        ord_id = execute_db("""
            INSERT INTO orders (
                order_number, user_id, company_id, service_id, service_name,
                price, vat, total, status, progress_percent, notes, woocommerce_order_id, payment_mode,
                checkout_phone, checkout_dob
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 20, ?, ?, ?, ?, ?);
        """, (order_num, cid, company_id, service_id, service_name, price, vat, total, status, order_notes, wc_order_id, 'Website Charge', billing_phone or None, checkout_dob))
        created = True
        log_activity(None, 'ORDER_SYNCED', 'orders', str(ord_id), f"Website order {order_num}")
        ensure_order_timeline(ord_id)
        if not guest_pending:
            notify_staff_new_order(order_num, client_name)

    if line_items:
        replace_order_line_items(ord_id, line_items, price, total)
        for item in line_items:
            try:
                upsert_woocommerce_product({
                    'name': item.get('product_name'),
                    'category': item.get('category_name') or item.get('category'),
                    'price': item.get('unit_price'),
                    'woocommerce_product_id': item.get('woocommerce_product_id') or item.get('product_id'),
                    'status': 'publish',
                    'description': item.get('product_name'),
                })
            except Exception:
                pass
    attachments_imported = 0
    try:
        attachments_imported = import_order_checkout_attachments(ord_id, cid, company_id, o_data)
    except Exception as err:
        print(f"[CheckoutAttach] Import error for order {ord_id}: {err}")
    persist_order_checkout_form(ord_id, o_data)
    return {
        'order_id': ord_id,
        'order_number': order_num,
        'created': created,
        'guest': guest_pending,
        'attachments_imported': attachments_imported,
    }


ensure_schema()
try:
    backfill_companies_from_registration_webhooks()
except Exception:
    pass

def application(environ, start_response):
    path = environ.get('PATH_INFO', '')
    method = environ.get('REQUEST_METHOD', 'GET')
    allowed_origin = cors_allowed_origin(environ)
    orig_start_response = start_response

    def secured_start_response(status, headers, exc_info=None):
        filtered = [(k, v) for k, v in headers if k.lower() != 'access-control-allow-origin']
        if allowed_origin:
            filtered.append(('Access-Control-Allow-Origin', allowed_origin))
            filtered.append(('Access-Control-Allow-Credentials', 'true'))
        filtered.append(('Vary', 'Origin'))
        if exc_info is not None:
            return orig_start_response(status, filtered, exc_info)
        return orig_start_response(status, filtered)

    start_response = secured_start_response

    # Handle CORS preflight
    if method == 'OPTIONS':
        start_response("200 OK", [
            ('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS'),
            ('Access-Control-Allow-Headers', 'Content-Type, Authorization, Cookie, X-Brixen-Signature, X-WP-Signature')
        ])
        return [b""]
    
    user = get_current_user(environ)
    
    # ----------------------------------------------------
    # Static files & Homepage
    # ----------------------------------------------------
    if path == '/' or path == '/index.html':
        return serve_static(environ, start_response, os.path.join(TEMPLATES_DIR, 'index.html'), allowed_root=TEMPLATES_DIR)
    elif path.startswith('/static/'):
        rel_path = urllib.parse.unquote(path[len('/static/'):]).replace('\\', '/')
        if not rel_path or '\x00' in rel_path:
            start_response("404 Not Found", [('Content-Type', 'text/plain; charset=utf-8')])
            return [b"Not Found"]
        candidate = os.path.join(STATIC_DIR, rel_path)
        return serve_static(environ, start_response, candidate, allowed_root=STATIC_DIR)

    # ----------------------------------------------------
    # WORDPRESS INTEGRATION & WEBHOOK APIs
    # ----------------------------------------------------
    if path == '/api/v1/wordpress/webhook' and method == 'POST':
        sig = environ.get('HTTP_X_BRIXEN_SIGNATURE', '') or environ.get('HTTP_X_WP_SIGNATURE', '')
        try:
            content_length = int(environ.get('CONTENT_LENGTH', 0))
        except (ValueError, TypeError):
            content_length = 0
        raw_bytes = environ['wsgi.input'].read(content_length) if content_length > 0 else b""
        
        # Mandatory HMAC Signature Verification
        if not sig or not verify_webhook_signature(raw_bytes, sig):
            return json_response(start_response, {'status': 'error', 'message': 'Missing or invalid HMAC signature'}, "401 Unauthorized")
            
        try:
            payload = json.loads(raw_bytes.decode('utf-8'))
        except Exception as e:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid JSON payload'}, "400 Bad Request")
            
        event_id = payload.get('event_id') or payload.get('id') or f"evt_{int(datetime.datetime.now().timestamp())}"
        event_type = payload.get('event_type') or payload.get('event') or 'user.created'
        
        # 1. Idempotency Check: Prevent duplicate event processing
        existing_evt = query_db("SELECT id FROM webhook_events WHERE event_id = ?;", (event_id,), one=True)
        if existing_evt:
            return json_response(start_response, {
                'status': 'ignored',
                'message': 'Duplicate event ignored',
                'event_id': event_id
            })
            
        # Log webhook event
        execute_db("""
            INSERT INTO webhook_events (event_id, event_type, payload, status)
            VALUES (?, ?, ?, 'Processed');
        """, (event_id, event_type, json.dumps(payload)))
        
        # 2. Process Registration / Update Events
        if event_type in ('user.created', 'user.updated'):
            u_data = payload.get('data', payload)
            wp_user_id = str(u_data.get('wordpress_user_id') or u_data.get('user_id') or u_data.get('id', ''))
            email = u_data.get('email', '').strip()
            first_name = u_data.get('first_name', '')
            last_name = u_data.get('last_name', '')
            full_name = u_data.get('full_name') or f"{first_name} {last_name}".strip() or email.split('@')[0]
            phone = billing_phone_from_webhook_payload(u_data) or (u_data.get('phone') or '').strip()
            dob = date_of_birth_from_webhook_payload(u_data)
            country = u_data.get('country', 'United Kingdom')
            
            if not email:
                return json_response(start_response, {'status': 'error', 'message': 'Email required'}, "400 Bad Request")
                
            # Match existing user by wordpress_user_id or email
            existing_user = query_db("SELECT id FROM users WHERE wordpress_user_id = ? OR email = ?;", (wp_user_id, email), one=True)
            if existing_user:
                execute_db("""
                    UPDATE users 
                    SET wordpress_user_id = ?, full_name = ?, phone = COALESCE(NULLIF(?, ''), phone),
                        date_of_birth = COALESCE(?, date_of_birth), country = ?, last_synced_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                """, (wp_user_id, full_name, phone, dob, country, existing_user['id']))
                action_msg = "Updated existing client from WordPress webhook"
                client_id = existing_user['id']
            else:
                pwd_hash = unusable_password_hash()
                client_id = execute_db("""
                    INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, date_of_birth, country, role, status, last_synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'CLIENT', 'Active', CURRENT_TIMESTAMP);
                """, (wp_user_id, email, pwd_hash, full_name, phone or None, dob, country))
                action_msg = "Created new client from WordPress webhook"
                
                # Auto-create notification for new client
                notify_client(
                    {'id': client_id, 'email': email, 'full_name': full_name, 'role': 'CLIENT'},
                    'Welcome to Brixen Consultants',
                    'Your client portal has been connected to your website account.',
                    'system',
                    '/documents',
                    email_subject=f"Welcome to {brand_settings()['company_name']}",
                    email_headline='Welcome to your client account',
                    cta_label='Open Client Panel',
                )
                
            log_activity(None, action_msg, 'users', client_id, f"WP User ID: {wp_user_id}, Email: {email}")
            return json_response(start_response, {
                'status': 'success',
                'message': action_msg,
                'client_id': client_id,
                'wordpress_user_id': wp_user_id
            })
            
        elif event_type in ('order.created', 'order.updated'):
            o_data = payload.get('data', payload)
            result = upsert_woocommerce_order(o_data)
            return json_response(start_response, {
                'status': 'success',
                'order_id': result['order_id'],
                'order_number': result['order_number']
            })

        elif event_type == 'payment.completed':
            o_data = payload.get('data', payload)
            order_num = o_data.get('order_number')
            if order_num:
                existing_ord = query_db("SELECT id, user_id FROM orders WHERE order_number = ?;", (order_num,), one=True)
                if existing_ord:
                    execute_db("""
                        UPDATE orders SET status = CASE WHEN status = 'Pending Verification' THEN status ELSE 'Processing' END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?;
                    """, (existing_ord['id'],))
                    execute_db("UPDATE invoices SET status = 'Paid', paid_at = CURRENT_TIMESTAMP WHERE order_id = ?;", (existing_ord['id'],))
            return json_response(start_response, {'status': 'success', 'message': 'Payment recorded'})
            
        return json_response(start_response, {'status': 'success', 'message': f'Event {event_type} received'})

    if path == '/api/v1/wordpress/sync-users' and method == 'POST':
        sig = environ.get('HTTP_X_BRIXEN_SIGNATURE', '') or environ.get('HTTP_X_WP_SIGNATURE', '')
        try:
            content_length = int(environ.get('CONTENT_LENGTH', 0))
        except (ValueError, TypeError):
            content_length = 0
        raw_bytes = environ['wsgi.input'].read(content_length) if content_length > 0 else b""

        has_valid_sig = sig and verify_webhook_signature(raw_bytes, sig)
        has_admin_perm = user and check_permission(user, 'settings.manage')
        if not (has_valid_sig or has_admin_perm):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions or invalid signature'}, "403 Forbidden")
            
        data = parse_body(environ) if not raw_bytes else json.loads(raw_bytes.decode('utf-8'))
        user_list = data.get('users', [])
        synced_cnt = 0
        updated_cnt = 0
        
        for u in user_list:
            wp_id = str(u.get('wordpress_user_id') or u.get('id', ''))
            email = u.get('email', '').strip()
            name = u.get('full_name') or u.get('display_name') or email.split('@')[0]
            phone = u.get('phone', '')
            if not email: continue
            
            existing = query_db("SELECT id FROM users WHERE wordpress_user_id = ? OR email = ?;", (wp_id, email), one=True)
            if existing:
                execute_db("UPDATE users SET wordpress_user_id = ?, full_name = ?, last_synced_at = CURRENT_TIMESTAMP WHERE id = ?;", (wp_id, name, existing['id']))
                updated_cnt += 1
            else:
                execute_db("""
                    INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, role, status, last_synced_at)
                    VALUES (?, ?, ?, ?, ?, 'CLIENT', 'Active', CURRENT_TIMESTAMP);
                """, (wp_id, email, unusable_password_hash(), name, phone))
                synced_cnt += 1
                
        return json_response(start_response, {
            'status': 'success',
            'summary': {
                'created': synced_cnt,
                'updated': updated_cnt,
                'total_processed': len(user_list)
            }
        })

    if path == '/api/v1/wordpress/sync-orders' and method == 'POST':
        sig = environ.get('HTTP_X_BRIXEN_SIGNATURE', '') or environ.get('HTTP_X_WP_SIGNATURE', '')
        try:
            content_length = int(environ.get('CONTENT_LENGTH', 0))
        except (ValueError, TypeError):
            content_length = 0
        raw_bytes = environ['wsgi.input'].read(content_length) if content_length > 0 else b""

        has_valid_sig = sig and verify_webhook_signature(raw_bytes, sig)
        has_admin_perm = user and check_permission(user, 'settings.manage')
        if not (has_valid_sig or has_admin_perm):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions or invalid signature'}, "403 Forbidden")
            
        data = parse_body(environ) if not raw_bytes else json.loads(raw_bytes.decode('utf-8'))
        order_list = data.get('orders', [])
        synced_cnt = 0
        updated_cnt = 0
        
        for o in order_list:
            result = upsert_woocommerce_order(o)
            if result['created']:
                synced_cnt += 1
            else:
                updated_cnt += 1
                
        return json_response(start_response, {
            'status': 'success',
            'summary': {
                'created': synced_cnt,
                'updated': updated_cnt,
                'total_processed': len(order_list)
            }
        })

    if path == '/api/v1/wordpress/sync-products' and method == 'POST':
        sig = environ.get('HTTP_X_BRIXEN_SIGNATURE', '') or environ.get('HTTP_X_WP_SIGNATURE', '')
        try:
            content_length = int(environ.get('CONTENT_LENGTH', 0))
        except (ValueError, TypeError):
            content_length = 0
        raw_bytes = environ['wsgi.input'].read(content_length) if content_length > 0 else b""

        has_valid_sig = sig and verify_webhook_signature(raw_bytes, sig)
        has_admin_perm = user and check_permission(user, 'settings.manage')
        if not (has_valid_sig or has_admin_perm):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions or invalid signature'}, "403 Forbidden")

        data = parse_body(environ) if not raw_bytes else json.loads(raw_bytes.decode('utf-8'))
        product_list = data.get('products', [])
        if not isinstance(product_list, list):
            return json_response(start_response, {'status': 'error', 'message': 'products must be a list'}, "400 Bad Request")
        created_cnt = 0
        updated_cnt = 0
        for product in product_list:
            result = upsert_woocommerce_product(product)
            if result.get('created'):
                created_cnt += 1
            elif result.get('updated'):
                updated_cnt += 1
        return json_response(start_response, {
            'status': 'success',
            'summary': {
                'created': created_cnt,
                'updated': updated_cnt,
                'total_processed': len(product_list),
            }
        })

    if path == '/api/v1/auth/sso' and method in ('GET', 'POST'):
        if method == 'GET':
            query_str = environ.get('QUERY_STRING', '')
            from urllib.parse import parse_qs
            params = parse_qs(query_str)
            wp_id = params.get('wordpress_user_id', [''])[0]
            email = params.get('email', [''])[0]
            timestamp = params.get('timestamp', [''])[0]
            sig = params.get('signature', [''])[0]
        else:
            data = parse_body(environ)
            wp_id = str(data.get('wordpress_user_id') or '')
            email = str(data.get('email') or '')
            timestamp = str(data.get('timestamp') or '')
            sig = str(data.get('signature') or '')

        wp_id = wp_id.strip()
        email = email.strip()
        timestamp = timestamp.strip()
        sig = sig.strip()

        if not wp_id:
            return json_response(start_response, {'status': 'error', 'message': 'Missing wordpress_user_id'}, "401 Unauthorized")
        if not timestamp or timestamp == '0':
            return json_response(start_response, {'status': 'error', 'message': 'Missing timestamp'}, "401 Unauthorized")
        if not sig:
            return json_response(start_response, {'status': 'error', 'message': 'Missing signature'}, "401 Unauthorized")

        row = query_db("SELECT value FROM settings WHERE key = 'wordpress_webhook_secret';", one=True)
        secret = (row['value'] if row and row['value'] else None) or os.environ.get('WORDPRESS_WEBHOOK_SECRET')
        if not secret:
            return json_response(start_response, {'status': 'error', 'message': 'SSO feature unconfigured'}, "500 Internal Server Error")

        ok, err = verify_wordpress_user_signature(wp_id, email, timestamp, sig, max_age_seconds=300)
        if not ok:
            return json_response(start_response, {'status': 'error', 'message': err or 'Invalid SSO signature'}, "401 Unauthorized")

        client_rec = find_client_for_wordpress(wp_id, email)
        if not client_rec:
            return json_response(start_response, {'status': 'error', 'message': 'Client record not found for SSO'}, "404 Not Found")
            
        token = create_db_session(client_rec['id'])
        log_activity(client_rec, 'Client logged in via WordPress SSO', 'users', client_rec['id'])

        if method == 'GET':
            start_response("302 Found", [
                ('Location', '/'),
                ('Set-Cookie', session_cookie_header(token, environ=environ))
            ])
            return [b""]
        else:
            return json_response(start_response, {
                'status': 'success',
                'user': public_me_user(client_rec)
            }, extra_headers=[('Set-Cookie', session_cookie_header(token, environ=environ))])

    if path == '/api/v1/wordpress/documents' and method == 'GET':
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        wp_id = (qs.get('wordpress_user_id', [''])[0] or '').strip()
        email = (qs.get('email', [''])[0] or '').strip()
        timestamp = (qs.get('timestamp', [''])[0] or '').strip()
        sig = (qs.get('signature', [''])[0] or '').strip()
        ok, err = verify_wordpress_user_signature(wp_id, email, timestamp, sig)
        if not ok:
            return json_response(start_response, {'status': 'error', 'message': err}, "401 Unauthorized")
        client = find_client_for_wordpress(wp_id, email)
        if not client:
            return json_response(start_response, {'status': 'success', 'documents': [], 'message': 'No CRM client matched this website account yet.'})
        docs = query_db("""
            SELECT d.*, c.name as company_name, o.order_number
            FROM documents d
            LEFT JOIN companies c ON d.company_id = c.id
            LEFT JOIN orders o ON d.order_id = o.id
            WHERE d.user_id = ? AND d.client_visible = 1
            ORDER BY COALESCE(d.shared_at, d.created_at) DESC, d.id DESC;
        """, (client['id'],))
        listed = []
        for row in docs:
            download_qs = urllib.parse.urlencode({
                'wordpress_user_id': wp_id,
                'email': email,
                'timestamp': timestamp,
                'signature': sig,
            })
            download_url = f"{portal_base_url()}/api/v1/wordpress/documents/{row['id']}/download?{download_qs}"
            listed.append(public_wordpress_document(row, download_url=download_url))
        return json_response(start_response, {
            'status': 'success',
            'client_email': client.get('email'),
            'documents': listed,
        })

    if path.startswith('/api/v1/wordpress/documents/') and path.endswith('/download') and method == 'GET':
        parts = [p for p in path.split('/') if p]
        # api v1 wordpress documents <id> download
        if len(parts) != 6:
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")
        try:
            doc_id = int(parts[4])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        wp_id = (qs.get('wordpress_user_id', [''])[0] or '').strip()
        email = (qs.get('email', [''])[0] or '').strip()
        timestamp = (qs.get('timestamp', [''])[0] or '').strip()
        sig = (qs.get('signature', [''])[0] or '').strip()
        ok, err = verify_wordpress_user_signature(wp_id, email, timestamp, sig)
        if not ok:
            return json_response(start_response, {'status': 'error', 'message': err}, "401 Unauthorized")
        client = find_client_for_wordpress(wp_id, email)
        if not client:
            return json_response(start_response, {'status': 'error', 'message': 'Access denied'}, "403 Forbidden")
        doc = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
        if not doc or int(doc.get('user_id') or 0) != int(client['id']) or not client_can_access_document(doc):
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")
        file_path = doc.get('file_path')
        if not file_path or not os.path.exists(file_path):
            return json_response(start_response, {'status': 'error', 'message': 'Document file not found'}, "404 Not Found")
        with open(file_path, 'rb') as handle:
            content = handle.read()
        lower = file_path.lower()
        mime_type = (
            'application/pdf' if lower.endswith('.pdf') else
            'image/png' if lower.endswith('.png') else
            'image/jpeg' if lower.endswith(('.jpg', '.jpeg')) else
            'application/octet-stream'
        )
        safe_name = os.path.basename(doc.get('name') or 'document')
        if not os.path.splitext(safe_name)[1] and os.path.splitext(file_path)[1]:
            safe_name = f"{safe_name}{os.path.splitext(file_path)[1]}"
        start_response("200 OK", [
            ('Content-Type', mime_type),
            ('Content-Length', str(len(content))),
            ('Content-Disposition', f'attachment; filename="{safe_name}"'),
        ])
        return [content]

    # ----------------------------------------------------
    # P3: ADMIN WEBHOOK MONITORING & RETRY API
    # ----------------------------------------------------
    if path == '/api/admin/webhooks' and method == 'GET':
        if not user or not check_permission(user, 'settings.manage'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
            
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        status_filter = qs.get('status', [''])[0].strip()
        
        sql = "SELECT * FROM webhook_events"
        params = []
        if status_filter:
            sql += " WHERE status = ?"
            params.append(status_filter)
        sql += " ORDER BY created_at DESC LIMIT 100;"
        
        events = query_db(sql, params)
        return json_response(start_response, {'status': 'success', 'events': events})

    if path.startswith('/api/admin/webhooks/') and path.endswith('/retry') and method == 'POST':
        if not user or not check_permission(user, 'settings.manage'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
            
        evt_id = path.split('/')[4]
        evt = query_db("SELECT * FROM webhook_events WHERE id = ?;", (evt_id,), one=True)
        if not evt:
            return json_response(start_response, {'status': 'error', 'message': 'Webhook event not found'}, "404 Not Found")
            
        try:
            payload = json.loads(evt['payload'])
            event_type = evt['event_type']
            
            # Update retry attempt
            execute_db("""
                UPDATE webhook_events 
                SET retry_count = retry_count + 1, last_attempt = datetime('now'), status = 'Processed'
                WHERE id = ?;
            """, (evt_id,))
            
            log_activity(user, 'WEBHOOK_RETRY', 'webhook_events', str(evt_id), f"Retried webhook event {evt['event_id']}")
            return json_response(start_response, {'status': 'success', 'message': f'Successfully re-processed webhook event #{evt_id}'})
        except Exception as err:
            execute_db("UPDATE webhook_events SET status = 'Failed', error_message = ? WHERE id = ?;", (str(err), evt_id))
            return json_response(start_response, {'status': 'error', 'message': f'Retry failed: {err}'}, "500 Internal Error")

    # ----------------------------------------------------
    # ADMIN / STAFF: CENTRALIZED CLIENT CRM RECORD API
    # ----------------------------------------------------
    if path.startswith('/api/admin/clients/') and path.endswith('/full') and method == 'GET':
        if not user or not check_permission(user, 'clients.view'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
            
        cid = path.split('/')[4]
        target_user = query_db("SELECT id, wordpress_user_id, email, full_name, phone, country, address, avatar_url, role, status, last_synced_at, created_at FROM users WHERE id = ?;", (cid,), one=True)
        if not target_user:
            return json_response(start_response, {'status': 'error', 'message': 'Client not found'}, "404 Not Found")
            
        companies = query_db("SELECT * FROM companies WHERE user_id = ? ORDER BY id DESC;", (cid,))
        orders = query_db("SELECT o.*, c.name as company_name FROM orders o LEFT JOIN companies c ON o.company_id = c.id WHERE o.user_id = ? ORDER BY o.created_at DESC;", (cid,))
        invoices = query_db("SELECT * FROM invoices WHERE user_id = ? ORDER BY created_at DESC;", (cid,))
        documents = query_db("""
            SELECT d.*, c.name as company_name, o.order_number
            FROM documents d
            LEFT JOIN companies c ON d.company_id = c.id
            LEFT JOIN orders o ON d.order_id = o.id
            WHERE d.user_id = ?
            ORDER BY d.created_at DESC;
        """, (cid,))
        tickets = query_db("SELECT * FROM support_tickets WHERE user_id = ? ORDER BY created_at DESC;", (cid,))
        notifications = query_db("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC;", (cid,))
        logs = query_db("SELECT * FROM activity_logs WHERE user_id = ? ORDER BY created_at DESC LIMIT 50;", (cid,))
        
        return json_response(start_response, {
            'status': 'success',
            'client': dict(target_user),
            'companies': companies,
            'orders': orders,
            'invoices': invoices,
            'documents': [public_document(d) for d in documents],
            'support_tickets': tickets,
            'notifications': notifications,
            'activity_logs': logs
        })

    # ----------------------------------------------------
    # PRIVATE SECURE DOCUMENT STREAMING API
    # ----------------------------------------------------
    if path.startswith('/api/documents/') and path.endswith('/download') and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
            
        parts = path.split('/')
        doc_id = parts[3]
        doc = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
        if not doc:
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")
            
        # Security: CLIENT may only download their own shared files; staff need documents.view
        if user['role'] == 'CLIENT':
            if doc['user_id'] != user['id'] or not client_can_access_document(doc):
                return json_response(start_response, {'status': 'error', 'message': 'Access denied to target document'}, "403 Forbidden")
        elif not check_permission(user, 'documents.view'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
            
        file_path = doc['file_path']
        if os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                content = f.read()
            mime_type = 'application/pdf' if file_path.lower().endswith('.pdf') else (
                'image/png' if file_path.lower().endswith('.png') else
                'image/jpeg' if file_path.lower().endswith(('.jpg', '.jpeg')) else
                'application/octet-stream'
            )
            safe_name = os.path.basename(doc['name'])
            if not os.path.splitext(safe_name)[1] and os.path.splitext(file_path)[1]:
                safe_name = f"{safe_name}{os.path.splitext(file_path)[1]}"
            start_response("200 OK", [
                ('Content-Type', mime_type),
                ('Content-Length', str(len(content))),
                ('Content-Disposition', f'attachment; filename="{safe_name}"')
            ])
            return [content]
        return json_response(start_response, {'status': 'error', 'message': 'Document file not found'}, "404 Not Found")

    # ----------------------------------------------------
    # API: System Settings
    # ----------------------------------------------------
    if path == '/api/settings' and method == 'GET':
        public_keys = {
            'company_name', 'logo_url', 'primary_color', 'support_email',
            'support_phone', 'currency', 'vat_rate', 'order_prefix', 'invoice_prefix',
            'portal_url'
        }
        blocked_fragments = (
            'secret', 'password', 'passwd', 'token', 'hash', 'credential',
            'private_key', 'api_key', 'session'
        )
        rows = query_db("SELECT key, value FROM settings;")
        settings_dict = {}
        for r in rows:
            key = r['key']
            key_l = (key or '').lower()
            if key not in public_keys:
                continue
            if any(frag in key_l for frag in blocked_fragments):
                continue
            settings_dict[key] = r['value']
        return json_response(start_response, {'status': 'success', 'settings': settings_dict})

    # ----------------------------------------------------
    # API: Auth
    # ----------------------------------------------------
    if path == '/api/auth/login' and method == 'POST':
        data = parse_body(environ)
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        found_user = query_db("SELECT * FROM users WHERE LOWER(email) = ?;", (email,), one=True)
        stored_hash = found_user['password_hash'] if found_user else None
        if not verify_password(password, stored_hash):
            return json_response(start_response, {'status': 'error', 'message': 'Invalid email or password.'}, "401 Unauthorized")
        if needs_rehash(stored_hash):
            execute_db(
                "UPDATE users SET password_hash = ? WHERE id = ?;",
                (hash_password(password), found_user['id'])
            )
        
        if found_user['status'] != 'Active':
            return json_response(start_response, {'status': 'error', 'message': 'Your account is suspended. Please contact support.'}, "403 Forbidden")
            
        token = create_db_session(found_user['id'])
        log_activity(found_user, 'USER_LOGIN', 'users', str(found_user['id']), 'User logged in successfully.')
        
        u_dict = public_me_user(found_user, impersonated=False)
        cookie_header = ('Set-Cookie', session_cookie_header(token, environ=environ))
        origin_clear = ('Set-Cookie', origin_cookie_header(clear=True, environ=environ))
        return json_response(start_response, {'status': 'success', 'user': u_dict}, extra_headers=[origin_clear, cookie_header])

    if path == '/api/auth/me' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        origin_token = cookie_value(environ, 'origin_session')
        origin_rec = session_user_from_token(origin_token)
        impersonated = bool(origin_rec and origin_rec.get('id') != user.get('id'))
        u_dict = public_me_user(user, impersonated=impersonated)
        return json_response(start_response, {'status': 'success', 'user': u_dict})

    if path == '/api/auth/logout' and method == 'POST':
        cookie_str = environ.get('HTTP_COOKIE', '')
        token = None
        if 'session_token=' in cookie_str:
            for c in cookie_str.split(';'):
                if c.strip().startswith('session_token='):
                    token = c.strip().split('=', 1)[1]
                    break
        if not token:
            auth_hdr = environ.get('HTTP_AUTHORIZATION', '')
            if auth_hdr.startswith('Bearer '):
                token = auth_hdr.split(' ', 1)[1]
                
        revoke_db_session(token)
        origin_token = cookie_value(environ, 'origin_session')
        if origin_token and origin_token != token:
            revoke_db_session(origin_token)
        cookie_header = ('Set-Cookie', session_cookie_header(clear=True, environ=environ))
        origin_clear = ('Set-Cookie', origin_cookie_header(clear=True, environ=environ))
        return json_response(start_response, {'status': 'success', 'message': 'Logged out'}, extra_headers=[origin_clear, cookie_header])

    if path == '/api/auth/profile' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        origin_token = cookie_value(environ, 'origin_session')
        origin_rec = session_user_from_token(origin_token)
        impersonating = bool(origin_rec and origin_rec.get('id') != user.get('id'))
        data = parse_body(environ)
        full_name = (data.get('full_name') if 'full_name' in data else user.get('full_name')) or user.get('full_name')
        phone = data.get('phone') if 'phone' in data else user.get('phone')
        country = data.get('country') if 'country' in data else user.get('country')
        address = data.get('address') if 'address' in data else user.get('address')
        new_password = data.get('new_password') or ''
        if not isinstance(new_password, str):
            new_password = ''
        new_password = new_password.strip()

        if new_password:
            if impersonating:
                return json_response(start_response, {
                    'status': 'error',
                    'message': 'Return to your own account to change the password.'
                }, "403 Forbidden")
            current_password = data.get('current_password') or ''
            stored = query_db("SELECT password_hash FROM users WHERE id = ?;", (user['id'],), one=True)
            if not stored or not verify_password(current_password, stored.get('password_hash')):
                return json_response(start_response, {
                    'status': 'error',
                    'message': 'Current password is incorrect.'
                }, "400 Bad Request")
            if len(new_password) < STAFF_MIN_PASSWORD_LEN:
                return json_response(start_response, {
                    'status': 'error',
                    'message': f'Password must be at least {STAFF_MIN_PASSWORD_LEN} characters'
                }, "400 Bad Request")
            if 'confirm_password' in data and (data.get('confirm_password') or '') != new_password:
                return json_response(start_response, {
                    'status': 'error',
                    'message': 'New passwords do not match.'
                }, "400 Bad Request")
            execute_db("UPDATE users SET password_hash = ? WHERE id = ?;", (hash_password(new_password), user['id']))

        execute_db("""
            UPDATE users SET full_name = ?, phone = ?, country = ?, address = ? WHERE id = ?;
        """, (full_name, phone, country, address, user['id']))
        updated = query_db("SELECT * FROM users WHERE id = ?;", (user['id'],), one=True)
        log_activity(user, 'PROFILE_UPDATE', 'users', str(user['id']), 'Updated profile settings.')
        return json_response(start_response, {
            'status': 'success',
            'message': 'Password updated.' if new_password else 'Profile updated successfully',
            'user': public_me_user(updated, impersonated=impersonating)
        })

    # ----------------------------------------------------
    # API: Client Stats & Orders
    # ----------------------------------------------------
    if path == '/api/client/stats' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        
        uid = user['id']
        comp_count = query_db("SELECT COUNT(*) as c FROM companies WHERE user_id = ?;", (uid,), one=True)['c']
        active_services = query_db("SELECT COUNT(*) as c FROM orders WHERE user_id = ? AND status IN ('Completed', 'Processing', 'In Progress');", (uid,), one=True)['c']
        pending_orders = query_db("SELECT COUNT(*) as c FROM orders WHERE user_id = ? AND status IN ('Pending', 'Processing', 'In Progress');", (uid,), one=True)['c']
        total_spent = query_db("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE user_id = ? AND status != 'Cancelled';", (uid,), one=True)['s']
        
        return json_response(start_response, {
            'status': 'success',
            'stats': {
                'companies_count': comp_count,
                'active_services': active_services,
                'pending_orders': pending_orders,
                'total_spent': f"£{total_spent:,.2f}"
            }
        })

    if path == '/api/client/dashboard' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        
        uid = user['id']
        comp_count = query_db("SELECT COUNT(*) as c FROM companies WHERE user_id = ?;", (uid,), one=True)['c']
        uk_comps = query_db("SELECT COUNT(*) as c FROM companies WHERE user_id = ? AND (reg_office LIKE '%London%' OR reg_office LIKE '%UK%' OR reg_office LIKE '%United Kingdom%');", (uid,), one=True)['c']
        intl_comps = query_db("SELECT COUNT(*) as c FROM companies WHERE user_id = ? AND (reg_office NOT LIKE '%London%' AND reg_office NOT LIKE '%UK%' AND reg_office NOT LIKE '%United Kingdom%');", (uid,), one=True)['c']
        companies = query_db("""
            SELECT id, name, company_number, status, account_status, reg_office, package
            FROM companies WHERE user_id = ? ORDER BY created_at DESC;
        """, (uid,))

        overdue_cnt = query_db("SELECT COUNT(*) as c FROM addresses WHERE user_id = ? AND expiry_date < date('now') AND status != 'Active';", (uid,), one=True)['c']
        upcoming_cnt = query_db("SELECT COUNT(*) as c FROM addresses WHERE user_id = ? AND expiry_date BETWEEN date('now') AND date('now', '+30 days');", (uid,), one=True)['c']

        active_orders = query_db("""
            SELECT o.*, c.name as company_name
            FROM orders o
            LEFT JOIN companies c ON o.company_id = c.id
            WHERE o.user_id = ?
              AND o.status NOT IN ('Completed', 'Cancelled', 'Refunded')
              AND o.service_name NOT LIKE '%Proxy%'
              AND o.service_name NOT LIKE '%Registered Agent%'
            ORDER BY o.created_at DESC;
        """, (uid,))

        enhanced_orders = []
        for ord_row in active_orders:
            o_dict = dict(ord_row)
            o_dict.pop('notes', None)
            o_dict.pop('woocommerce_order_id', None)
            progress = int(o_dict.get('progress_percent') or 0)
            line_rows = query_db(
                "SELECT * FROM order_line_items WHERE order_id = ? ORDER BY sort_order ASC, id ASC;",
                (o_dict['id'],)
            )
            timeline_rows = query_db(
                "SELECT title, status, step_date FROM order_timeline WHERE order_id = ? ORDER BY id ASC;",
                (o_dict['id'],)
            )
            stages = []
            for step in timeline_rows:
                step_status = (step.get('status') or 'Pending')
                if step_status == 'Completed':
                    visual = 'completed'
                elif step_status == 'Current':
                    visual = 'current'
                else:
                    visual = 'pending'
                stages.append({'name': step.get('title') or step_status, 'status': visual})
            if not stages:
                stages = [{'name': o_dict.get('status') or 'Pending', 'status': 'current'}]
            o_dict['stages'] = stages

            service_items = []
            for item in line_rows:
                item_status = 'Done' if progress >= 100 else (o_dict.get('status') or 'Pending')
                service_items.append({
                    'name': item.get('product_name'),
                    'category': item.get('category_name'),
                    'status': item_status,
                })
            if not service_items:
                service_items = [{
                    'name': o_dict.get('service_name'),
                    'category': None,
                    'status': 'Done' if progress >= 100 else (o_dict.get('status') or 'Pending'),
                }]
            o_dict['service_items'] = service_items
            o_dict['total_items_count'] = len(service_items)
            o_dict['done_items_count'] = sum(1 for item in service_items if item['status'] == 'Done')
            enhanced_orders.append(o_dict)

        display_name = (user.get('full_name') or '').strip() or user.get('email') or 'there'
        now_str = datetime.datetime.now().strftime("%d %B %Y")

        return json_response(start_response, {
            'status': 'success',
            'current_date': now_str,
            'user_name': display_name,
            'total_companies': comp_count,
            'uk_companies': uk_comps,
            'intl_companies': intl_comps,
            'overdue_deadlines': overdue_cnt,
            'upcoming_deadlines': upcoming_cnt,
            'active_orders_count': len(enhanced_orders),
            'active_orders': enhanced_orders,
            'companies': companies,
        })

    if path == '/api/client/orders' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
            
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        search = qs.get('search', [''])[0].strip()
        status_filter = qs.get('status', [''])[0].strip()
        sort_by = qs.get('sort', ['date_desc'])[0]
        page = int(qs.get('page', [1])[0])
        limit = int(qs.get('limit', [12])[0])
        
        uid = user['id']
        sql = """SELECT o.*, c.name as company_name,
            COALESCE((SELECT GROUP_CONCAT(li.product_name, ', ') FROM order_line_items li WHERE li.order_id = o.id), o.service_name) as products_summary,
            (SELECT li.category_name FROM order_line_items li WHERE li.order_id = o.id ORDER BY li.sort_order ASC, li.id ASC LIMIT 1) as category_name
            FROM orders o LEFT JOIN companies c ON o.company_id = c.id
            WHERE o.user_id = ? AND o.service_name NOT LIKE '%Proxy%' AND o.service_name NOT LIKE '%Registered Agent%'"""
        params = [uid]
        
        if search:
            sql += " AND (o.order_number LIKE ? OR o.service_name LIKE ? OR c.name LIKE ?)"
            s_term = f"%{search}%"
            params.extend([s_term, s_term, s_term])
            
        if status_filter:
            sql += " AND o.status = ?"
            params.append(status_filter)
            
        if sort_by == 'date_asc':
            sql += " ORDER BY o.created_at ASC"
        elif sort_by == 'price_desc':
            sql += " ORDER BY o.total DESC"
        elif sort_by == 'price_asc':
            sql += " ORDER BY o.total ASC"
        else:
            sql += " ORDER BY o.created_at DESC"
            
        all_rows = query_db(sql, params)
        total_records = len(all_rows)
        
        offset = (page - 1) * limit
        paged_rows = all_rows[offset:offset+limit]
        for row in paged_rows:
            row.pop('notes', None)
            row.pop('woocommerce_order_id', None)
        
        # Calculate Top 4 Stat Cards dynamically from client DB
        total_orders_cnt = query_db("SELECT COUNT(*) as c FROM orders WHERE user_id = ?;", (uid,), one=True)['c']
        completed_cnt = query_db("SELECT COUNT(*) as c FROM orders WHERE user_id = ? AND status = 'Completed';", (uid,), one=True)['c']
        in_progress_cnt = query_db("SELECT COUNT(*) as c FROM orders WHERE user_id = ? AND status IN ('Processing', 'In Progress');", (uid,), one=True)['c']
        total_spent_val = query_db("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE user_id = ? AND status != 'Cancelled';", (uid,), one=True)['s']
        
        return json_response(start_response, {
            'status': 'success',
            'orders': paged_rows,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total_records,
                'total_pages': (total_records + limit - 1) // limit if limit else 1
            },
            'stats': {
                'total_orders': total_orders_cnt,
                'completed': completed_cnt,
                'in_progress': in_progress_cnt,
                'total_spent': f"£{total_spent_val:,.2f}"
            }
        })

    if path.startswith('/api/client/orders/') and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        oid = path.split('/')[-1]
        order = query_db("""
            SELECT o.*, c.name as company_name, c.reg_office as company_address, c.company_number,
                   u.full_name as client_name, u.email as client_email, u.phone as client_phone,
                   u.date_of_birth as client_date_of_birth,
                   u.address as client_address, u.country as client_country,
                   s.full_name as assigned_staff_name,
                   COALESCE((SELECT GROUP_CONCAT(li.product_name, ', ') FROM order_line_items li WHERE li.order_id = o.id), o.service_name) as products_summary,
                   (SELECT li.category_name FROM order_line_items li WHERE li.order_id = o.id ORDER BY li.sort_order ASC, li.id ASC LIMIT 1) as category_name
            FROM orders o
            LEFT JOIN companies c ON o.company_id = c.id
            LEFT JOIN users u ON o.user_id = u.id
            LEFT JOIN users s ON o.assigned_staff_id = s.id
            WHERE o.id = ?;
        """, (oid,), one=True)
        
        if not order:
            return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
        if user['role'] == 'CLIENT':
            if order['user_id'] != user['id']:
                return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
        elif not check_permission(user, 'orders.view'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")

        recovered = None
        already_pulled = bool((order.get('website_checkout_pulled_at') or '').strip())
        if not already_pulled:
            recovered = backfill_order_checkout_from_webhooks(
                order['user_id'], order['id'], order.get('order_number'), order.get('woocommerce_order_id')
            )
        if recovered:
            order = dict(order)
            if recovered.get('phone'):
                order['client_phone'] = recovered['phone']
                order['checkout_phone'] = recovered['phone']
            if recovered.get('date_of_birth'):
                order['client_date_of_birth'] = recovered['date_of_birth']
                order['checkout_dob'] = recovered['date_of_birth']
        needs_phone = not ((order.get('client_phone') or '').strip() or (order.get('checkout_phone') or '').strip())
        needs_dob = not ((order.get('client_date_of_birth') or '').strip() or (order.get('checkout_dob') or '').strip())
        if not already_pulled and (needs_phone or needs_dob) and order.get('woocommerce_order_id'):
            wp_recovered = refresh_order_checkout_from_wordpress(order)
            if wp_recovered:
                order = dict(order)
                if wp_recovered.get('phone'):
                    order['client_phone'] = wp_recovered['phone']
                    order['checkout_phone'] = wp_recovered['phone']
                if wp_recovered.get('date_of_birth'):
                    order['client_date_of_birth'] = wp_recovered['date_of_birth']
                    order['checkout_dob'] = wp_recovered['date_of_birth']
                order['website_checkout_pulled_at'] = order.get('website_checkout_pulled_at') or datetime.datetime.now().isoformat(sep=' ', timespec='seconds')
        order = dict(order)
        if not (order.get('client_phone') or '').strip() and (order.get('checkout_phone') or '').strip():
            order['client_phone'] = order['checkout_phone']
        if not (order.get('client_date_of_birth') or '').strip() and (order.get('checkout_dob') or '').strip():
            order['client_date_of_birth'] = order['checkout_dob']
        display_phone = format_uk_phone(order.get('checkout_phone') or order.get('client_phone') or '')
        if display_phone:
            order['checkout_phone'] = display_phone
            order['client_phone'] = display_phone
        elif not is_plausible_uk_phone(order.get('checkout_phone') or order.get('client_phone') or ''):
            order['checkout_phone'] = ''
            order['client_phone'] = ''

        timeline = query_db("SELECT * FROM order_timeline WHERE order_id = ? ORDER BY id ASC;", (oid,))
        invoice = query_db("SELECT * FROM invoices WHERE order_id = ?;", (oid,), one=True)
        for_client = user['role'] == 'CLIENT'
        if for_client:
            documents = query_db("SELECT * FROM documents WHERE order_id = ? AND client_visible = 1;", (oid,))
        else:
            documents = query_db("SELECT * FROM documents WHERE order_id = ?;", (oid,))
        line_rows = query_db("SELECT * FROM order_line_items WHERE order_id = ? ORDER BY sort_order ASC, id ASC;", (oid,))
        tickets = query_db("""
            SELECT id, ticket_number, subject, status, created_at
            FROM support_tickets WHERE order_id = ? ORDER BY created_at DESC;
        """, (oid,)) if not for_client else query_db("""
            SELECT id, ticket_number, subject, status, created_at
            FROM support_tickets WHERE order_id = ? AND user_id = ? ORDER BY created_at DESC;
        """, (oid, user['id']))
        order_out = dict(order)
        checkout_form = load_order_checkout_form(order)
        order_out['checkout_form'] = checkout_form
        if for_client:
            order_out.pop('notes', None)
            order_out.pop('woocommerce_order_id', None)
        addresses = []
        if not for_client:
            addresses = query_db("""
                SELECT type, line1, line2, city, postal_code, country, status
                FROM addresses WHERE user_id = ?
                ORDER BY created_at DESC;
            """, (order['user_id'],))

        invoice_out = None
        if invoice:
            invoice_out = {
                'invoice_number': invoice.get('invoice_number'),
                'status': invoice.get('status'),
                'amount': invoice.get('amount'),
                'tax': invoice.get('tax'),
                'total': invoice.get('total'),
                'due_date': invoice.get('due_date'),
                'paid_at': invoice.get('paid_at'),
                'payment_method': invoice.get('payment_method'),
            }

        return json_response(start_response, {
            'status': 'success',
            'order': order_out,
            'timeline': timeline,
            'invoice': invoice_out,
            'documents': [public_document(d, for_client=for_client) for d in documents],
            'line_items': [public_line_item(r, for_client=for_client) for r in line_rows],
            'tickets': tickets,
            'addresses': addresses
        })

    # ----------------------------------------------------
    # API: Companies, Addresses, Invoices, Documents, etc.
    # ----------------------------------------------------
    if path == '/api/admin/companies' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] == 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        if data.get('order_id') not in (None, '', 0, '0'):
            try:
                order_id = int(data.get('order_id'))
            except (TypeError, ValueError):
                return json_response(start_response, {'status': 'error', 'message': 'Order is required'}, "400 Bad Request")
            company_id, error = create_company_from_pending_order(order_id, data.get('name'), data)
        else:
            company_id, error = create_manual_company(data, actor=user)
        if error:
            return json_response(start_response, {'status': 'error', 'message': error}, "400 Bad Request")
        company = query_db(
            """
            SELECT id, name, company_number, status, inc_date, director, reg_office, package, account_status,
                   created_at, user_id, utr_number, authentication_code, activation_code
            FROM companies WHERE id = ?;
            """,
            (company_id,),
            one=True,
        )
        deadlines_map = upcoming_deadlines_by_company(company.get('user_id'), [company_id])
        if data.get('order_id') in (None, '', 0, '0'):
            log_activity(user, 'COMPANY_CREATED', 'companies', str(company_id), f"Manually added company {company.get('name')}")
        return json_response(start_response, {
            'status': 'success',
            'company': public_client_company(company, deadlines_map.get(int(company_id), []), for_staff=True),
        })

    if path == '/api/admin/companies' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] == 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        comps = query_db("""
            SELECT id, name, company_number, status, inc_date, director, reg_office, package, account_status,
                   created_at, user_id, utr_number, authentication_code, activation_code
            FROM companies ORDER BY created_at DESC;
        """)
        deadlines_map = upcoming_deadlines_by_company(None, [row['id'] for row in comps])
        uk_count = query_db("SELECT COUNT(*) as cnt FROM companies WHERE reg_office LIKE '%London%' OR reg_office LIKE '%UK%' OR reg_office LIKE '%United Kingdom%';", one=True)['cnt']
        clients = query_db("SELECT id, full_name, email FROM users WHERE role = 'CLIENT' AND status = 'Active' ORDER BY full_name COLLATE NOCASE;") or []
        return json_response(start_response, {
            'status': 'success',
            'companies': [public_client_company(row, deadlines_map.get(int(row['id']), []), for_staff=True) for row in comps],
            'pending_registrations': pending_registration_orders(for_client=False),
            'clients': [{'id': row['id'], 'full_name': row.get('full_name') or '', 'email': row.get('email') or ''} for row in clients],
            'companies_house_configured': bool(companies_house_api_key()),
            'total_uk': uk_count
        })

    if path == '/api/admin/companies/search' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] == 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        query = (qs.get('q') or [''])[0].strip()
        if not companies_house_api_key():
            return json_response(start_response, {
                'status': 'error',
                'message': 'Add a Companies House API key in System Settings to search live companies.',
                'configured': False,
                'companies': [],
            }, "503 Service Unavailable")
        matches, error = search_companies_house(query)
        if error and not matches:
            status_code = "400 Bad Request" if 'characters' in error else "502 Bad Gateway"
            return json_response(start_response, {
                'status': 'error',
                'message': error,
                'configured': True,
                'companies': [],
            }, status_code)
        return json_response(start_response, {
            'status': 'success',
            'configured': True,
            'companies': matches,
        })

    if path == '/api/admin/companies/import-webfiling' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] == 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        summary, error = import_webfiling_companies(data, actor=user)
        if error:
            status_code = "503 Service Unavailable" if 'API key' in error else "400 Bad Request"
            return json_response(start_response, {'status': 'error', 'message': error}, status_code)
        return json_response(start_response, {
            'status': 'success',
            'message': f"Imported {summary['imported']}, updated {summary['updated']}, skipped {summary['skipped']}, failed {summary['failed']}.",
            **summary,
        })

    if path.startswith('/api/admin/companies/') and method == 'PUT':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] == 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        parts = [p for p in path.split('/') if p]
        if len(parts) != 4:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        try:
            company_id = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        data = parse_body(environ)
        company, error = update_company_compliance(company_id, data)
        if error:
            status_code = "404 Not Found" if error == 'Company not found' else "400 Bad Request"
            return json_response(start_response, {'status': 'error', 'message': error}, status_code)
        deadlines_map = upcoming_deadlines_by_company(company.get('user_id'), [company_id])
        log_activity(user, 'COMPANY_UPDATED', 'companies', str(company_id), f"Updated compliance codes for {company.get('name')}")
        return json_response(start_response, {
            'status': 'success',
            'company': public_client_company(company, deadlines_map.get(int(company_id), []), for_staff=True),
        })

    if path.startswith('/api/admin/companies/') and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] == 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        parts = [p for p in path.split('/') if p]
        if len(parts) != 4:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        try:
            company_id = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        company = query_db(
            """
            SELECT id, name, company_number, status, inc_date, director, reg_office, package, account_status,
                   created_at, user_id, utr_number, authentication_code, activation_code
            FROM companies WHERE id = ?;
            """,
            (company_id,),
            one=True,
        )
        if not company:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        owner_id = company.get('user_id')
        deadlines_map = upcoming_deadlines_by_company(owner_id, [company_id])
        registered_office = company_registered_office_record(company_id, company)
        ensure_company_order_documents(company_id)
        orders = query_db(
            """
            SELECT id, order_number, service_name, status, progress_percent, created_at, expected_date
            FROM orders WHERE company_id = ? ORDER BY created_at DESC;
            """,
            (company_id,),
        )
        documents = documents_for_company_portfolio(company_id)
        tickets = query_db(
            """
            SELECT id, ticket_number, subject, category, priority, status, created_at
            FROM support_tickets WHERE company_id = ? ORDER BY created_at DESC;
            """,
            (company_id,),
        )
        return json_response(start_response, {
            'status': 'success',
            'company': public_client_company(company, deadlines_map.get(company_id, []), for_staff=True),
            'client_id': company.get('user_id'),
            'registered_office': registered_office,
            'orders': [public_client_order_summary(row) for row in orders],
            'documents': [public_document(row, for_client=True) for row in documents],
            'tickets': [public_client_ticket_summary(row) for row in tickets],
        })

    if path.startswith('/api/admin/companies/') and method == 'DELETE':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] not in ('SUPER_ADMIN', 'ADMIN'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        parts = [p for p in path.split('/') if p]
        if len(parts) == 5 and parts[3] == 'pending':
            try:
                order_id = int(parts[4])
            except (TypeError, ValueError):
                return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
            ok, error = dismiss_pending_registration_order(order_id, actor=user)
            if not ok:
                return json_response(start_response, {'status': 'error', 'message': error}, "400 Bad Request")
            return json_response(start_response, {
                'status': 'success',
                'message': 'Awaiting-name card removed.',
                'order_id': order_id,
            })
        if len(parts) != 4:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        try:
            company_id = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        company = query_db("SELECT * FROM companies WHERE id = ?;", (company_id,), one=True)
        if not company:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        record_dismissed_company_card(company)
        execute_db(
            "UPDATE orders SET company_id = NULL, portfolio_hidden = 1 WHERE company_id = ?;",
            (company_id,),
        )
        execute_db("DELETE FROM companies WHERE id = ?;", (company_id,))
        log_activity(
            user,
            'COMPANY_DELETED',
            'companies',
            str(company_id),
            f"Deleted company {company.get('name')} ({company.get('company_number') or 'no number'})",
        )
        return json_response(start_response, {
            'status': 'success',
            'message': f"Company {company.get('name')} deleted.",
            'company_id': company_id,
        })

    if path == '/api/client/companies' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        comps = query_db(
            """
            SELECT id, name, company_number, status, inc_date, director, reg_office, package, account_status,
                   created_at, utr_number
            FROM companies WHERE user_id = ? ORDER BY created_at DESC;
            """,
            (user['id'],),
        )
        deadlines_map = upcoming_deadlines_by_company(user['id'], [row['id'] for row in comps])
        return json_response(start_response, {
            'status': 'success',
            'companies': [public_client_company(row, deadlines_map.get(int(row['id']), [])) for row in comps],
            'pending_registrations': pending_registration_orders(user_id=user['id'], for_client=True),
        })

    if path.startswith('/api/client/companies/') and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        parts = [p for p in path.split('/') if p]
        if len(parts) != 4:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        try:
            company_id = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        company = query_db(
            """
            SELECT id, name, company_number, status, inc_date, director, reg_office, package, account_status,
                   created_at, utr_number
            FROM companies WHERE id = ? AND user_id = ?;
            """,
            (company_id, user['id']),
            one=True,
        )
        if not company:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        deadlines_map = upcoming_deadlines_by_company(user['id'], [company_id])
        registered_office = company_registered_office_record(company_id, company)
        ensure_company_order_documents(company_id)
        orders = query_db(
            """
            SELECT id, order_number, service_name, status, progress_percent, created_at, expected_date
            FROM orders WHERE user_id = ? AND company_id = ? ORDER BY created_at DESC;
            """,
            (user['id'], company_id),
        )
        documents = documents_for_company_portfolio(company_id, user_id=user['id'], for_client=True)
        tickets = query_db(
            """
            SELECT id, ticket_number, subject, category, priority, status, created_at
            FROM support_tickets WHERE user_id = ? AND company_id = ? ORDER BY created_at DESC;
            """,
            (user['id'], company_id),
        )
        return json_response(start_response, {
            'status': 'success',
            'company': public_client_company(company, deadlines_map.get(company_id, [])),
            'registered_office': registered_office,
            'orders': [public_client_order_summary(row) for row in orders],
            'documents': [public_document(row, for_client=True) for row in documents],
            'tickets': [public_client_ticket_summary(row) for row in tickets],
        })

    if path == '/api/client/addresses' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        addrs = query_db("""
            SELECT a.*, c.name as company_name 
            FROM addresses a 
            LEFT JOIN companies c ON a.company_id = c.id 
            WHERE a.user_id = ? ORDER BY a.created_at DESC;
        """, (user['id'],))
        return json_response(start_response, {'status': 'success', 'addresses': addrs})

    if path == '/api/client/invoices' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        invs = query_db("""
            SELECT i.*, o.order_number, o.service_name
            FROM invoices i
            JOIN orders o ON i.order_id = o.id
            WHERE i.user_id = ? ORDER BY i.created_at DESC;
        """, (user['id'],))
        return json_response(start_response, {'status': 'success', 'invoices': invs})

    if path == '/api/client/documents' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        docs = query_db("""
            SELECT d.*, c.name as company_name, o.order_number
            FROM documents d
            LEFT JOIN companies c ON d.company_id = c.id
            LEFT JOIN orders o ON d.order_id = o.id
            WHERE d.user_id = ? AND d.client_visible = 1
            ORDER BY d.created_at DESC;
        """, (user['id'],))
        return json_response(start_response, {'status': 'success', 'documents': [public_document(d, for_client=True) for d in docs]})

    if path == '/api/client/services' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        svcs = query_db("SELECT * FROM services WHERE status = 'Active' ORDER BY category ASC, price ASC;")
        return json_response(start_response, {'status': 'success', 'services': svcs})

    if path == '/api/client/payments' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        pymts = query_db("""
            SELECT i.*, o.order_number, o.service_name
            FROM invoices i
            JOIN orders o ON i.order_id = o.id
            WHERE i.user_id = ? ORDER BY i.created_at DESC;
        """, (user['id'],))
        return json_response(start_response, {'status': 'success', 'payments': pymts})

    if path == '/api/client/messages' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        msgs = query_db("""
            SELECT m.*, t.ticket_number, t.subject, t.status as ticket_status
            FROM support_messages m
            JOIN support_tickets t ON m.ticket_id = t.id
            WHERE t.user_id = ? ORDER BY m.created_at DESC;
        """, (user['id'],))
        return json_response(start_response, {'status': 'success', 'messages': msgs})

    if path == '/api/client/documents/upload' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        data = parse_body(environ)
        doc_name = data.get('name', 'Uploaded_Document.pdf')
        category = data.get('category', 'Company Documents')
        company_id = data.get('company_id')
        order_id = data.get('order_id')
        b64_content = data.get('file_content_base64', '')
        
        file_bytes, decode_err = decode_document_base64(
            b64_content,
            default_bytes=f"Sample Document Content for {doc_name}".encode('utf-8')
        )
        if decode_err:
            return json_response(start_response, {'status': 'error', 'message': decode_err}, "400 Bad Request")
        upload_filename = (data.get('file_name') or data.get('original_filename') or '').strip()
        ext, upload_err = validate_uploaded_document(doc_name, file_bytes, upload_filename=upload_filename)
        if upload_err:
            return json_response(start_response, {'status': 'error', 'message': upload_err}, "400 Bad Request")
        target_path, store_err = store_client_document_file(user['id'], order_id, ext, file_bytes)
        if store_err:
            return json_response(start_response, {'status': 'error', 'message': store_err}, "400 Bad Request")
        file_size_str = format_document_size(len(file_bytes))
            
        doc_id = execute_db("""
            INSERT INTO documents (user_id, company_id, order_id, name, category, file_path, file_type, file_size, status, uploaded_by, review_notes, client_visible)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
        """, (user['id'], company_id, order_id, doc_name, category, target_path, DOCUMENT_TYPE_LABELS.get(ext, 'Document'), file_size_str, 'Pending Review', user['full_name'], 'Awaiting compliance officer audit.'))
        
        log_activity(user, 'DOCUMENT_UPLOAD', 'documents', str(doc_id), f"Uploaded document {doc_name}")
        notify_client(
            user,
            'Document uploaded',
            f"New document '{doc_name}' uploaded successfully and queued for review.",
            'DOCUMENT',
            '/documents',
            email_headline='Document received',
            cta_label='Open Client Panel',
            detail_title='Document',
            detail_value=doc_name,
        )
        
        return json_response(start_response, {'status': 'success', 'message': 'Document uploaded successfully and queued for review.', 'document_id': doc_id})



    # ----------------------------------------------------
    # API: Support Tickets & Notifications
    # ----------------------------------------------------
    if path == '/api/client/tickets' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if is_internal_staff(user):
            tickets = query_db("""
                SELECT t.*, u.full_name as client_name, u.email as client_email, c.name as company_name, o.order_number
                FROM support_tickets t
                JOIN users u ON t.user_id = u.id
                LEFT JOIN companies c ON t.company_id = c.id
                LEFT JOIN orders o ON t.order_id = o.id
                ORDER BY t.created_at DESC;
            """)
        else:
            tickets = query_db("""
                SELECT t.*, c.name as company_name, o.order_number
                FROM support_tickets t
                LEFT JOIN companies c ON t.company_id = c.id
                LEFT JOIN orders o ON t.order_id = o.id
                WHERE t.user_id = ? ORDER BY t.created_at DESC;
            """, (user['id'],))
        return json_response(start_response, {'status': 'success', 'tickets': tickets})

    if path == '/api/client/tickets' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        data = parse_body(environ)
        subject = data.get('subject', 'Support Inquiry')
        category = data.get('category', 'General Support')
        priority = data.get('priority', 'Medium')
        message = data.get('description', '')
        company_id = data.get('company_id')
        order_id = data.get('order_id')
        
        import random
        tnum = f"TICK-2026-{random.randint(1000, 9999)}"
        tid = execute_db("""
            INSERT INTO support_tickets (ticket_number, user_id, order_id, company_id, subject, category, priority, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """, (tnum, user['id'], order_id, company_id, subject, category, priority, 'Open'))
        
        execute_db("""
            INSERT INTO support_messages (ticket_id, sender_id, sender_name, sender_role, message)
            VALUES (?, ?, ?, ?, ?);
        """, (tid, user['id'], user['full_name'], user['role'], message))
        
        log_activity(user, 'SUPPORT_TICKET_CREATED', 'support_tickets', str(tid), f"Created ticket {tnum}")
        return json_response(start_response, {'status': 'success', 'message': 'Support ticket submitted.', 'ticket_id': tid})

    if path.startswith('/api/client/tickets/') and path.endswith('/messages'):
        tid = path.split('/')[4]
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
            
        t_rec = query_db("SELECT * FROM support_tickets WHERE id = ?;", (tid,), one=True)
        if not t_rec:
            return json_response(start_response, {'status': 'error', 'message': 'Ticket not found'}, "404 Not Found")
            
        if user['role'] == 'CLIENT':
            if t_rec['user_id'] != user['id']:
                return json_response(start_response, {'status': 'error', 'message': 'Access denied to target ticket'}, "403 Forbidden")
        elif not is_internal_staff(user):
            return json_response(start_response, {'status': 'error', 'message': 'Access denied to target ticket'}, "403 Forbidden")

        if method == 'GET':
            msgs = query_db("SELECT * FROM support_messages WHERE ticket_id = ? ORDER BY created_at ASC;", (tid,))
            return json_response(start_response, {'status': 'success', 'ticket': dict(t_rec), 'messages': msgs})
        elif method == 'POST':
            data = parse_body(environ)
            msg_text = data.get('message', '').strip()
            if msg_text:
                execute_db("""
                    INSERT INTO support_messages (ticket_id, sender_id, sender_name, sender_role, message)
                    VALUES (?, ?, ?, ?, ?);
                """, (tid, user['id'], user['full_name'], user['role'], msg_text))
                
                # Update ticket status if client replied
                if user['role'] == 'CLIENT':
                    execute_db("UPDATE support_tickets SET status = 'In Progress' WHERE id = ?;", (tid,))
                    last_staff = query_db("""
                        SELECT sender_id FROM support_messages
                        WHERE ticket_id = ? AND sender_role != 'CLIENT'
                        ORDER BY id DESC LIMIT 1;
                    """, (tid,), one=True)
                    if last_staff:
                        execute_db("""
                            INSERT INTO notifications (user_id, title, message, type, link)
                            VALUES (?, ?, ?, ?, ?);
                        """, (last_staff['sender_id'], 'Customer replied', f"Customer replied on ticket {t_rec['ticket_number']}.", 'support', '#admin-orders'))
                else:
                    execute_db("UPDATE support_tickets SET status = 'Waiting for Customer' WHERE id = ?;", (tid,))
                    ticket_client = resolve_client_user(t_rec['user_id'])
                    if ticket_client:
                        notify_client(
                            ticket_client,
                            'New message from Brixen',
                            f"You have a new reply on support ticket {t_rec['ticket_number']}.",
                            'support',
                            '/support',
                            email_subject=f"New support reply — {t_rec['ticket_number']} — {brand_settings()['company_name']}",
                            email_headline='New message from our team',
                            cta_label='View Support Messages',
                            detail_title='Ticket',
                            detail_value=t_rec['ticket_number'],
                            extra_message=msg_text,
                        )
            return json_response(start_response, {'status': 'success', 'message': 'Message sent.'})

    if path == '/api/client/notifications' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        notes = query_db("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC;", (user['id'],))
        unread_cnt = query_db("SELECT COUNT(*) as c FROM notifications WHERE user_id = ? AND is_read = 0;", (user['id'],), one=True)['c']
        return json_response(start_response, {'status': 'success', 'notifications': notes, 'unread_count': unread_cnt})

    if path == '/api/client/notifications/read' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        execute_db("UPDATE notifications SET is_read = 1 WHERE user_id = ?;", (user['id'],))
        return json_response(start_response, {'status': 'success', 'message': 'Notifications marked read'})

    # ----------------------------------------------------
    # API: Admin CMS
    # ----------------------------------------------------
    if path == '/api/admin/stats' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if not can_view_admin_dashboard(user):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
            
        tot_customers = query_db("SELECT COUNT(*) as c FROM users WHERE role = 'CLIENT';", one=True)['c']
        tot_companies = query_db("SELECT COUNT(*) as c FROM companies;", one=True)['c']
        tot_orders = query_db("SELECT COUNT(*) as c FROM orders;", one=True)['c']
        pending_orders = query_db("SELECT COUNT(*) as c FROM orders WHERE status IN ('Pending', 'Processing', 'In Progress');", one=True)['c']
        completed_orders = query_db("SELECT COUNT(*) as c FROM orders WHERE status = 'Completed';", one=True)['c']
        total_revenue = query_db("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status != 'Cancelled';", one=True)['s']
        pending_payments = query_db("SELECT COALESCE(SUM(total), 0) as s FROM invoices WHERE status = 'Pending';", one=True)['s']
        open_tickets = query_db("SELECT COUNT(*) as c FROM support_tickets WHERE status IN ('Open', 'In Progress');", one=True)['c']
        
        return json_response(start_response, {
            'status': 'success',
            'stats': {
                'total_customers': tot_customers,
                'total_companies': tot_companies,
                'total_orders': tot_orders,
                'pending_orders': pending_orders,
                'completed_orders': completed_orders,
                'total_revenue': f"£{total_revenue:,.2f}",
                'pending_payments': f"£{pending_payments:,.2f}",
                'open_tickets': open_tickets
            },
            'charts': {
                'monthly': build_monthly_revenue_chart(6)
            }
        })

    if path == '/api/admin/customers' and method == 'GET':
        if not user or not check_permission(user, 'clients.view'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        sql = """
            SELECT u.*, 
            (SELECT COUNT(*) FROM companies WHERE user_id = u.id) as companies_count,
            (SELECT COUNT(*) FROM orders WHERE user_id = u.id) as orders_count
            FROM users u
            WHERE u.role = 'CLIENT'
        """
        params = []
        if user['role'] == 'MANAGER':
            sql += " AND u.id IN (SELECT user_id FROM orders WHERE assigned_staff_id = ? OR assigned_staff_id IS NULL OR assigned_staff_id IN (SELECT id FROM users WHERE role = 'STAFF'))"
            params.append(user['id'])
        sql += " ORDER BY u.created_at DESC;"
        customers = query_db(sql, params)
        public_customers = []
        for row in customers:
            item = public_staff_user(row) or {}
            item['companies_count'] = row.get('companies_count')
            item['orders_count'] = row.get('orders_count')
            item['portal_login_ready'] = client_portal_login_ready(row)
            public_customers.append(item)
        return json_response(start_response, {'status': 'success', 'customers': public_customers})

    if path == '/api/admin/customers/status' and method == 'POST':
        if not user or not check_permission(user, 'clients.edit'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        cid = data.get('customer_id')
        new_status = data.get('status')
        execute_db("UPDATE users SET status = ? WHERE id = ?;", (new_status, cid))
        log_activity(user, 'CUSTOMER_STATUS_CHANGE', 'users', str(cid), f"Updated status to {new_status}")
        return json_response(start_response, {'status': 'success', 'message': f'Customer status updated to {new_status}'})

    if path == '/api/admin/customers/phone' and method == 'POST':
        if not user or not check_permission(user, 'clients.edit'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        customer_id, err = to_optional_int(data.get('customer_id'), 'customer_id')
        if err or not customer_id:
            return json_response(start_response, {'status': 'error', 'message': 'Customer is required'}, "400 Bad Request")
        phone = (data.get('phone') or '').strip()
        target = query_db("SELECT id, email, full_name, role FROM users WHERE id = ?;", (customer_id,), one=True)
        if not target or target.get('role') != 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Customer not found'}, "404 Not Found")
        execute_db("UPDATE users SET phone = ? WHERE id = ?;", (phone or None, customer_id))
        log_activity(user, 'CUSTOMER_PHONE_UPDATE', 'users', str(customer_id), f"Updated UK contact for {target.get('email')}")
        return json_response(start_response, {
            'status': 'success',
            'message': 'UK contact number saved.',
            'phone': phone
        })

    if path == '/api/admin/customers/password' and method == 'POST':
        if not user or not check_permission(user, 'clients.edit'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        customer_id, err = to_optional_int(data.get('customer_id'), 'customer_id')
        if err or not customer_id:
            return json_response(start_response, {'status': 'error', 'message': 'Customer is required'}, "400 Bad Request")
        password = data.get('password') or ''
        confirm_password = data.get('confirm_password')
        if not isinstance(password, str) or len(password) < STAFF_MIN_PASSWORD_LEN:
            return json_response(start_response, {
                'status': 'error',
                'message': f'Password must be at least {STAFF_MIN_PASSWORD_LEN} characters'
            }, "400 Bad Request")
        if confirm_password is not None and confirm_password != password:
            return json_response(start_response, {'status': 'error', 'message': 'Passwords do not match'}, "400 Bad Request")
        target = query_db("SELECT id, email, full_name, role FROM users WHERE id = ?;", (customer_id,), one=True)
        if not target or target.get('role') != 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Customer not found'}, "404 Not Found")
        execute_db("UPDATE users SET password_hash = ? WHERE id = ?;", (hash_password(password), customer_id))
        log_activity(user, 'CLIENT_PASSWORD_SET', 'users', str(customer_id), f"Set portal password for {target.get('email')}")
        return json_response(start_response, {
            'status': 'success',
            'message': f'{target.get("full_name") or "Customer"} can now sign in at the portal with {target.get("email")}.',
            'portal_login_ready': True,
        })

    if path == '/api/admin/orders' and method == 'GET':
        if not user or not check_permission(user, 'orders.view'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        clauses, params, date_clauses, date_params = build_admin_order_filters(user, qs)
        where_all = clauses + date_clauses
        params_all = params + date_params
        where_sql = (" WHERE " + " AND ".join(where_all)) if where_all else ""
        from_sql = """
            FROM orders o
            JOIN users u ON o.user_id = u.id
            LEFT JOIN companies c ON o.company_id = c.id
            LEFT JOIN users s ON o.assigned_staff_id = s.id
        """
        try:
            page = max(int(_qs_first(qs, 'page', '1') or '1'), 1)
        except ValueError:
            page = 1
        try:
            limit = int(_qs_first(qs, 'limit', '25') or '25')
        except ValueError:
            limit = 25
        limit = min(max(limit, 1), 100)
        offset = (page - 1) * limit

        total_row = query_db("SELECT COUNT(*) as c " + from_sql + where_sql, params_all, one=True)
        total_records = (total_row or {}).get('c') or 0
        stats_row = query_db("""
            SELECT
                COUNT(*) as total_orders,
                SUM(CASE WHEN o.status IN ('Pending', 'Pending Verification') THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN o.status IN ('Processing', 'In Progress') THEN 1 ELSE 0 END) as in_progress,
                SUM(CASE WHEN o.status = 'Completed' THEN 1 ELSE 0 END) as completed,
                COALESCE(SUM(CASE WHEN o.status != 'Cancelled' THEN o.total ELSE 0 END), 0) as revenue
        """ + from_sql + where_sql, params_all, one=True) or {}

        today = datetime.date.today()
        month_start = f"{today.year:04d}-{today.month:02d}-01"
        month_where = clauses + ["date(o.created_at) >= date(?)", "date(o.created_at) <= date(?)"]
        month_params = params + [month_start, str(today)]
        month_sql = " WHERE " + " AND ".join(month_where) if month_where else ""
        month_row = query_db("SELECT COUNT(*) as c " + from_sql + month_sql, month_params, one=True)

        orders = query_db("""
            SELECT o.id, o.order_number, o.user_id, o.company_id, o.service_id, o.service_name,
                   o.price, o.vat, o.total, o.status, o.progress_percent, o.payment_mode, o.assigned_staff_id,
                   o.created_at, o.updated_at, o.expected_date, o.delivery_label,
                   u.full_name as client_name, u.email as client_email, c.name as company_name,
                   s.full_name as assigned_staff_name,
                   COALESCE((SELECT GROUP_CONCAT(li.product_name, ', ') FROM order_line_items li WHERE li.order_id = o.id), o.service_name) as products_summary,
                   (SELECT li.category_name FROM order_line_items li WHERE li.order_id = o.id ORDER BY li.sort_order ASC, li.id ASC LIMIT 1) as category_name,
                   (SELECT inv.status FROM invoices inv WHERE inv.order_id = o.id ORDER BY inv.id DESC LIMIT 1) as payment_status
        """ + from_sql + where_sql + " ORDER BY o.created_at DESC LIMIT ? OFFSET ?;", params_all + [limit, offset])

        scope_sql, scope_params = manager_order_scope(user)
        facet_where = (" WHERE " + scope_sql) if scope_sql else ""
        products = query_db("""
            SELECT name FROM (
                SELECT DISTINCT o.service_name AS name FROM orders o
                """ + facet_where + """
                UNION
                SELECT DISTINCT li.product_name AS name
                FROM order_line_items li JOIN orders o ON o.id = li.order_id
                """ + facet_where + """
                UNION
                SELECT DISTINCT sv.name AS name
                FROM services sv
                WHERE sv.status = 'Active' AND sv.name IS NOT NULL AND TRIM(sv.name) != ''
            ) t WHERE name IS NOT NULL AND TRIM(name) != '' ORDER BY name COLLATE NOCASE;
        """, scope_params + scope_params)
        categories = query_db("""
            SELECT name FROM (
                SELECT DISTINCT li.category_name AS name
                FROM order_line_items li JOIN orders o ON o.id = li.order_id
                """ + facet_where + """
                UNION
                SELECT DISTINCT sv.category AS name
                FROM services sv
                WHERE sv.status = 'Active' AND sv.category IS NOT NULL AND TRIM(sv.category) != ''
                UNION
                SELECT DISTINCT sv.category AS name
                FROM services sv
                JOIN orders o ON o.service_id = sv.id
                """ + facet_where + """
            ) t WHERE name IS NOT NULL AND TRIM(name) != '' ORDER BY name COLLATE NOCASE;
        """, scope_params + scope_params)
        customers = query_db("""
            SELECT DISTINCT u.id, u.full_name
            FROM orders o JOIN users u ON u.id = o.user_id
            """ + facet_where + """
            ORDER BY u.full_name COLLATE NOCASE;
        """, scope_params)
        years_sql = "SELECT DISTINCT strftime('%Y', o.created_at) AS year FROM orders o"
        if scope_sql:
            years_sql += " WHERE " + scope_sql + " AND o.created_at IS NOT NULL"
        else:
            years_sql += " WHERE o.created_at IS NOT NULL"
        years_sql += " ORDER BY year DESC;"
        years = query_db(years_sql, scope_params)

        stats = {
            'total_orders': int(stats_row.get('total_orders') or 0),
            'pending': int(stats_row.get('pending') or 0),
            'in_progress': int(stats_row.get('in_progress') or 0),
            'completed': int(stats_row.get('completed') or 0),
            'this_month': int((month_row or {}).get('c') or 0)
        }
        if can_view_revenue(user):
            stats['revenue'] = float(stats_row.get('revenue') or 0)

        return json_response(start_response, {
            'status': 'success',
            'orders': orders,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total_records,
                'total_pages': (total_records + limit - 1) // limit if limit else 1
            },
            'stats': stats,
            'facets': {
                'products': [row['name'] for row in products],
                'categories': [row['name'] for row in categories],
                'customers': customers,
                'years': [row['year'] for row in years if row.get('year')]
            }
        })

    if path.startswith('/api/admin/orders/') and path.endswith('/conversation') and method in ('GET', 'POST'):
        if not user or not is_internal_staff(user):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        oid = path.split('/')[4]
        order = query_db("""
            SELECT o.*, u.full_name as client_name, u.email as client_email, u.id as client_id
            FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = ?;
        """, (oid,), one=True)
        if not order:
            return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
        ticket = query_db("""
            SELECT * FROM support_tickets WHERE order_id = ? AND user_id = ?
            ORDER BY created_at DESC LIMIT 1;
        """, (oid, order['user_id']), one=True)
        if method == 'GET':
            msgs = query_db("SELECT * FROM support_messages WHERE ticket_id = ? ORDER BY created_at ASC;", (ticket['id'],)) if ticket else []
            return json_response(start_response, {
                'status': 'success',
                'ticket': dict(ticket) if ticket else None,
                'messages': msgs
            })
        data = parse_body(environ)
        msg_text = (data.get('message') or '').strip()
        if not msg_text:
            return json_response(start_response, {'status': 'error', 'message': 'Message is required'}, "400 Bad Request")
        if not ticket:
            tnum = f"TICK-ORD-{oid}-{uuid.uuid4().hex[:6].upper()}"
            tid = execute_db("""
                INSERT INTO support_tickets (ticket_number, user_id, order_id, company_id, subject, category, priority, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                tnum, order['user_id'], oid, order.get('company_id'),
                f"Order {order['order_number']}", 'Order Advisory', 'Medium', 'Waiting for Customer'
            ))
        else:
            tid = ticket['id']
            execute_db("UPDATE support_tickets SET status = 'Waiting for Customer' WHERE id = ?;", (tid,))
        execute_db("""
            INSERT INTO support_messages (ticket_id, sender_id, sender_name, sender_role, message)
            VALUES (?, ?, ?, ?, ?);
        """, (tid, user['id'], user['full_name'], user['role'], msg_text))
        notify_client(
            resolve_client_user(order['user_id']),
            'New message from Brixen',
            f"You have a new message about order {order['order_number']}.",
            'support',
            '/support',
            email_subject=f"New message — {order['order_number']} — {brand_settings()['company_name']}",
            email_headline='New message about your order',
            cta_label='Open Client Panel',
            detail_title='Order',
            detail_value=order['order_number'],
            extra_message=msg_text,
        )
        log_activity(user, 'ORDER_MESSAGE', 'support_tickets', str(tid), f"Messaged client on {order['order_number']}")
        ticket = query_db("SELECT * FROM support_tickets WHERE id = ?;", (tid,), one=True)
        msgs = query_db("SELECT * FROM support_messages WHERE ticket_id = ? ORDER BY created_at ASC;", (tid,))
        return json_response(start_response, {'status': 'success', 'ticket': dict(ticket), 'messages': msgs})

    if path.startswith('/api/admin/orders/') and method == 'PUT':
        if not user or not check_permission(user, 'orders.edit'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        oid = path.split('/')[-1]
        try:
            int(oid)
        except ValueError:
            return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
        existing = query_db("SELECT * FROM orders WHERE id = ?;", (oid,), one=True)
        if not existing:
            return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
        if not staff_can_access_admin_order(user, existing):
            return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
        data = parse_body(environ)
        
        status = data.get('status')
        progress = data.get('progress_percent')
        notes = data.get('notes')
        assign_raw = data.get('assigned_staff_id') if 'assigned_staff_id' in data else None
        assigned_staff_id = None
        if 'assigned_staff_id' in data:
            assigned_staff_id, assign_err = to_optional_int(assign_raw, 'assigned_staff_id')
            if assign_err:
                return json_response(start_response, {'status': 'error', 'message': assign_err}, "400 Bad Request")
            ok, msg = validate_task_assignee(assigned_staff_id)
            if not ok:
                return json_response(start_response, {'status': 'error', 'message': msg}, "400 Bad Request")

        if status is not None:
            if status not in ORDER_STATUSES:
                return json_response(start_response, {'status': 'error', 'message': 'Invalid order status'}, "400 Bad Request")
            execute_db("UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (status, oid))
        if progress is not None:
            try:
                progress_val = max(0, min(100, int(progress)))
            except (TypeError, ValueError):
                return json_response(start_response, {'status': 'error', 'message': 'Invalid progress'}, "400 Bad Request")
            execute_db("UPDATE orders SET progress_percent = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (progress_val, oid))
        if notes is not None:
            execute_db("UPDATE orders SET notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (notes, oid))
        if 'assigned_staff_id' in data:
            execute_db("UPDATE orders SET assigned_staff_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (assigned_staff_id, oid))
        if 'payment_mode' in data:
            payment_mode = (data.get('payment_mode') or '').strip() or None
            if payment_mode and payment_mode not in ORDER_PAYMENT_MODES:
                return json_response(start_response, {'status': 'error', 'message': 'Invalid payment mode'}, "400 Bad Request")
            execute_db("UPDATE orders SET payment_mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (payment_mode, oid))

        history_parts = []
        if status is not None:
            history_parts.append(f"Status changed to {status}")
        if progress is not None:
            history_parts.append(f"Progress set to {int(progress)}%")
        if notes is not None:
            history_parts.append("Internal notes updated")
        if 'assigned_staff_id' in data:
            history_parts.append("Assigned staff updated")
        if 'payment_mode' in data:
            history_parts.append("Payment mode updated")
        if history_parts:
            append_order_history(oid, " · ".join(history_parts))
            
        target_order = query_db("SELECT o.*, u.email, u.full_name FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = ?;", (oid,), one=True)
        if target_order and (status is not None or progress is not None):
            msg = f"Order {target_order['order_number']} status changed to '{target_order['status']}' ({target_order['progress_percent']}% complete)."
            notify_client(
                target_order,
                'Order status updated',
                msg,
                'order_update',
                '/orders',
                email_subject=f"Order update — {target_order['order_number']} — {brand_settings()['company_name']}",
                email_headline='Your order status has changed',
                cta_label='Track Your Order',
                detail_title='Status',
                detail_value=f"{target_order['status']} ({target_order['progress_percent']}% complete)",
            )
            
        log_activity(user, 'ORDER_UPDATE', 'orders', str(oid), f"Updated order #{oid} status to {status}")
        return json_response(start_response, {'status': 'success', 'message': 'Order updated successfully'})

    if path.startswith('/api/admin/orders/') and method == 'DELETE':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if not can_delete_orders(user):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        oid = path.split('/')[-1]
        try:
            int(oid)
        except ValueError:
            return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
        existing = query_db("SELECT * FROM orders WHERE id = ?;", (oid,), one=True)
        if not existing:
            return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
        doc_rows = query_db("SELECT file_path FROM documents WHERE order_id = ?;", (oid,))
        file_paths = [row.get('file_path') for row in doc_rows]
        execute_db("DELETE FROM orders WHERE id = ?;", (oid,))
        remove_stored_document_files(file_paths)
        log_activity(user, 'ORDER_DELETE', 'orders', str(oid), f"Deleted order {existing.get('order_number')}")
        return json_response(start_response, {'status': 'success', 'message': 'Order deleted'})

    if path == '/api/admin/services' and method == 'GET':
        denied = require_permission(start_response, user, 'orders.view')
        if denied:
            return denied
        svcs = query_db("SELECT * FROM services ORDER BY created_at DESC;")
        return json_response(start_response, {'status': 'success', 'services': svcs})

    if path == '/api/admin/services' and method == 'POST':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        data = parse_body(environ)
        name = (data.get('name') or '').strip()
        description = (data.get('description') or '').strip()
        category = (data.get('category') or '').strip() or 'Corporate'
        if not name:
            return json_response(start_response, {'status': 'error', 'message': 'Service name is required'}, "400 Bad Request")
        try:
            price = float(data.get('price'))
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Price must be a number'}, "400 Bad Request")
        sid = execute_db("""
            INSERT INTO services (name, description, category, price, duration, status, featured, vat_rate, renewal_period)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            name, description or name, category, price,
            data.get('duration') or '12 Months', 'Active',
            1 if data.get('featured') else 0,
            float(data.get('vat_rate', 0.20) or 0.20),
            data.get('renewal_period') or 'Annual'
        ))
        created = query_db("SELECT * FROM services WHERE id = ?;", (sid,), one=True)
        log_activity(user, 'SERVICE_CREATED', 'services', str(sid), f"Created service {name}")
        return json_response(start_response, {'status': 'success', 'message': 'Service created.', 'service': created})

    if path.startswith('/api/admin/services/') and method in ('PUT', 'PATCH'):
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        parts = [p for p in path.split('/') if p]
        if len(parts) != 4:
            return json_response(start_response, {'status': 'error', 'message': 'Service not found'}, "404 Not Found")
        try:
            sid = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Service not found'}, "404 Not Found")
        existing = query_db("SELECT * FROM services WHERE id = ?;", (sid,), one=True)
        if not existing:
            return json_response(start_response, {'status': 'error', 'message': 'Service not found'}, "404 Not Found")
        data = parse_body(environ)
        name = (data.get('name') if 'name' in data else existing.get('name') or '').strip()
        description = (data.get('description') if 'description' in data else existing.get('description') or '').strip()
        category = (data.get('category') if 'category' in data else existing.get('category') or '').strip() or 'Corporate'
        duration = (data.get('duration') if 'duration' in data else existing.get('duration') or '').strip() or '12 Months'
        if not name:
            return json_response(start_response, {'status': 'error', 'message': 'Service name is required'}, "400 Bad Request")
        if 'price' in data:
            try:
                price = float(data.get('price'))
            except (TypeError, ValueError):
                return json_response(start_response, {'status': 'error', 'message': 'Price must be a number'}, "400 Bad Request")
            if price < 0:
                return json_response(start_response, {'status': 'error', 'message': 'Price cannot be negative'}, "400 Bad Request")
        else:
            try:
                price = float(existing.get('price') or 0)
            except (TypeError, ValueError):
                price = 0.0
        status_raw = data.get('status') if 'status' in data else existing.get('status')
        status = 'Active' if str(status_raw or 'Active').strip().lower() in ('active', 'publish', '1', 'true') else 'Inactive'
        execute_db("""
            UPDATE services
            SET name = ?, description = ?, category = ?, price = ?, duration = ?, status = ?
            WHERE id = ?;
        """, (name, description or name, category, price, duration, status, sid))
        updated = query_db("SELECT * FROM services WHERE id = ?;", (sid,), one=True)
        log_activity(user, 'SERVICE_UPDATED', 'services', str(sid), f"Updated service {name} (£{price:.2f})")
        return json_response(start_response, {'status': 'success', 'message': 'Service updated.', 'service': updated})

    if path.startswith('/api/admin/services/') and method == 'DELETE':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        parts = [p for p in path.split('/') if p]
        if len(parts) != 4:
            return json_response(start_response, {'status': 'error', 'message': 'Service not found'}, "404 Not Found")
        try:
            sid = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Service not found'}, "404 Not Found")
        existing = query_db("SELECT id, name FROM services WHERE id = ?;", (sid,), one=True)
        if not existing:
            return json_response(start_response, {'status': 'error', 'message': 'Service not found'}, "404 Not Found")
        execute_db("UPDATE orders SET service_id = NULL WHERE service_id = ?;", (sid,))
        execute_db("DELETE FROM services WHERE id = ?;", (sid,))
        log_activity(user, 'SERVICE_DELETED', 'services', str(sid), f"Deleted service {existing.get('name')}")
        return json_response(start_response, {'status': 'success', 'message': 'Service deleted.', 'service_id': sid})

    if path == '/api/admin/invoices' and method == 'GET':
        denied = require_permission(start_response, user, 'invoices.view')
        if denied:
            return denied
        invoices = query_db("""
            SELECT i.id, i.invoice_number, i.order_id, i.user_id, i.amount, i.tax, i.total,
                   i.status, i.due_date, i.paid_at, i.payment_method, i.created_at,
                   o.order_number, o.service_name, u.full_name as client_name, u.email as client_email
            FROM invoices i
            LEFT JOIN orders o ON i.order_id = o.id
            LEFT JOIN users u ON i.user_id = u.id
            ORDER BY i.created_at DESC;
        """)
        return json_response(start_response, {'status': 'success', 'invoices': invoices})

    if path == '/api/admin/documents' and method == 'GET':
        denied = require_permission(start_response, user, 'documents.view')
        if denied:
            return denied
        docs = query_db("""
            SELECT d.*, u.full_name as client_name, u.email as client_email, c.name as company_name, o.order_number
            FROM documents d
            LEFT JOIN users u ON d.user_id = u.id
            LEFT JOIN companies c ON d.company_id = c.id
            LEFT JOIN orders o ON d.order_id = o.id
            ORDER BY d.created_at DESC;
        """)
        listed = [public_document(d) for d in docs if d]
        return json_response(start_response, {'status': 'success', 'documents': listed})

    if path == '/api/admin/documents' and method == 'POST':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        data = parse_body(environ)
        client_id = optional_record_id(data.get('client_id'))
        company_id = optional_record_id(data.get('company_id'))
        order_id = optional_record_id(data.get('order_id'))
        if not client_id and company_id:
            company_owner = query_db("SELECT user_id FROM companies WHERE id = ?;", (company_id,), one=True)
            if company_owner:
                client_id = optional_record_id(company_owner.get('user_id'))
        doc_name = (data.get('name') or '').strip() or 'Staff_Document.pdf'
        category = (data.get('category') or 'Order Documents').strip() or 'Order Documents'
        client_message = (data.get('client_message') or data.get('description') or '').strip()
        b64_content = data.get('file_content_base64', '')
        upload_filename = (data.get('file_name') or data.get('original_filename') or '').strip()

        if client_id:
            links, link_err = resolve_client_document_links(client_id, company_id, order_id)
            if link_err:
                status_code = "404 Not Found" if link_err == 'Client not found' else "400 Bad Request"
                return json_response(start_response, {'status': 'error', 'message': link_err}, status_code)
            target_client = links['client']
            target_order = links['order']
            resolved_company_id = company_id
            if not resolved_company_id and target_order:
                resolved_company_id = optional_record_id(target_order.get('company_id'))
            file_bytes, decode_err = decode_document_base64(b64_content)
            if decode_err:
                return json_response(start_response, {'status': 'error', 'message': decode_err}, "400 Bad Request")
            ext, upload_err = validate_uploaded_document(doc_name, file_bytes, require_bytes=True, upload_filename=upload_filename)
            if upload_err:
                return json_response(start_response, {'status': 'error', 'message': upload_err}, "400 Bad Request")
            target_path, store_err = store_client_document_file(target_client['id'], order_id, ext, file_bytes)
            if store_err:
                return json_response(start_response, {'status': 'error', 'message': store_err}, "400 Bad Request")
            file_size_str = format_document_size(len(file_bytes))
            review_notes = (data.get('review_notes') or '').strip() or 'Delivered to client portal'
            doc_id = execute_db("""
                INSERT INTO documents (user_id, company_id, order_id, name, category, file_path, file_type, file_size, status, uploaded_by, review_notes, client_visible, shared_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Approved', ?, ?, 1, CURRENT_TIMESTAMP);
            """, (
                target_client['id'], resolved_company_id, order_id, doc_name, category, target_path,
                DOCUMENT_TYPE_LABELS.get(ext, 'Document'), file_size_str, user['full_name'], review_notes
            ))
            log_activity(user, 'DOCUMENT_UPLOAD', 'documents', str(doc_id), f"Delivered document {doc_name} to client #{target_client['id']}")
            delivery = notify_client_document_uploaded(target_client, doc_name, client_message)
            created = query_db("""
                SELECT d.*, c.name as company_name, o.order_number
                FROM documents d
                LEFT JOIN companies c ON d.company_id = c.id
                LEFT JOIN orders o ON d.order_id = o.id
                WHERE d.id = ?;
            """, (doc_id,), one=True)
            portal_login_ready = client_portal_login_ready(target_client)
            return json_response(start_response, {
                'status': 'success',
                'message': 'Document uploaded successfully.',
                'document': public_document(created),
                'notification_created': delivery['notification_created'],
                'email_sent': delivery['email_sent'],
                'email_status': delivery['email_status'],
                'portal_login_ready': portal_login_ready,
                'client_email': target_client.get('email'),
            })

        if not order_id:
            return json_response(start_response, {'status': 'error', 'message': 'order_id is required'}, "400 Bad Request")
        order = query_db("SELECT * FROM orders WHERE id = ?;", (order_id,), one=True)
        if not order:
            return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
        file_bytes, decode_err = decode_document_base64(b64_content, default_bytes=b'Brixen internal document')
        if decode_err:
            return json_response(start_response, {'status': 'error', 'message': decode_err}, "400 Bad Request")
        ext, upload_err = validate_uploaded_document(doc_name, file_bytes, upload_filename=upload_filename)
        if upload_err:
            return json_response(start_response, {'status': 'error', 'message': upload_err}, "400 Bad Request")
        target_path, store_err = store_client_document_file(order['user_id'], order_id, ext, file_bytes)
        if store_err:
            return json_response(start_response, {'status': 'error', 'message': store_err}, "400 Bad Request")
        file_size_str = format_document_size(len(file_bytes))
        doc_id = execute_db("""
            INSERT INTO documents (user_id, company_id, order_id, name, category, file_path, file_type, file_size, status, uploaded_by, review_notes, client_visible)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Approved', ?, ?, 0);
        """, (
            order['user_id'], order.get('company_id'), order_id, doc_name, category, target_path,
            DOCUMENT_TYPE_LABELS.get(ext, 'Document'), file_size_str, user['full_name'],
            data.get('review_notes') or 'Internal staff upload'
        ))
        log_activity(user, 'DOCUMENT_UPLOAD', 'documents', str(doc_id), f"Staff uploaded {doc_name} for order {order['order_number']}")
        created = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
        return json_response(start_response, {
            'status': 'success',
            'message': 'Document stored. It is not visible to the customer until you send it.',
            'document': public_document(created)
        })

    if path.startswith('/api/admin/documents/') and path.endswith('/send') and method == 'POST':
        denied = require_permission(start_response, user, 'documents.send')
        if denied:
            return denied
        doc_id = path.split('/')[4]
        doc = query_db("SELECT d.*, u.email, u.full_name, o.order_number FROM documents d JOIN users u ON d.user_id = u.id LEFT JOIN orders o ON d.order_id = o.id WHERE d.id = ?;", (doc_id,), one=True)
        if not doc:
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")
        execute_db("""
            UPDATE documents SET client_visible = 1, shared_at = CURRENT_TIMESTAMP, status = 'Approved' WHERE id = ?;
        """, (doc_id,))
        delivery = notify_client_document_uploaded(
            {'id': doc['user_id'], 'email': doc['email'], 'full_name': doc['full_name'], 'role': 'CLIENT'},
            doc['name'],
        )
        log_activity(user, 'DOCUMENT_SENT', 'documents', str(doc_id), f"Sent {doc['name']} to customer")
        updated = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
        return json_response(start_response, {
            'status': 'success',
            'message': 'Document sent to the customer portal.',
            'document': public_document(updated),
            'notification_created': delivery['notification_created'],
            'email_sent': delivery['email_sent'],
            'email_status': delivery['email_status'],
        })

    if path.startswith('/api/admin/documents/') and method == 'PUT':
        denied = require_permission(start_response, user, 'documents.view')
        if denied:
            return denied
        doc_id = path.split('/')[-1]
        data = parse_body(environ)
        status = data.get('status')
        if 'review_notes' in data:
            execute_db("UPDATE documents SET status = ?, review_notes = ? WHERE id = ?;", (status, data.get('review_notes', ''), doc_id))
        else:
            execute_db("UPDATE documents SET status = ? WHERE id = ?;", (status, doc_id))
        
        target_doc = query_db("SELECT d.*, u.email, u.full_name FROM documents d JOIN users u ON d.user_id = u.id WHERE d.id = ?;", (doc_id,), one=True)
        if target_doc:
            msg = f"Document '{target_doc['name']}' review status: {status}."
            notify_client(
                target_doc,
                'Document reviewed',
                msg,
                'document_review',
                '/documents',
                email_subject=f"Document update — {target_doc['name']} — {brand_settings()['company_name']}",
                email_headline='Your document has been reviewed',
                cta_label='View in Messages & Files',
                detail_title='Document',
                detail_value=target_doc['name'],
                extra_message=f"Status: {status}",
            )
            
        log_activity(user, 'DOCUMENT_REVIEW', 'documents', str(doc_id), f"Reviewed document status to {status}")
        return json_response(start_response, {'status': 'success', 'message': f'Document status updated to {status}'})

    if path == '/api/admin/activity-logs' and method == 'GET':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        logs = query_db("SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT 100;")
        return json_response(start_response, {'status': 'success', 'logs': logs})

    # ----------------------------------------------------
    # API: Staff Task Management
    # ----------------------------------------------------
    if path == '/api/admin/staff' and method == 'GET':
        denied = task_auth_error(start_response, user, 'tasks.view')
        if denied:
            return denied
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        include_inactive = (qs.get('include_inactive', ['0'])[0] or '').lower() in ('1', 'true', 'yes')
        sql = """
            SELECT id, wordpress_user_id, email, full_name, phone, country, role, department, status, avatar_url, created_at
            FROM users
            WHERE role IN ('STAFF', 'MANAGER', 'ADMIN', 'SUPER_ADMIN')
        """
        if not (include_inactive and check_permission(user, 'staff.manage')):
            sql += " AND status = 'Active'"
        sql += " ORDER BY full_name ASC;"
        staff_rows = query_db(sql)
        return json_response(start_response, {'status': 'success', 'staff': [public_staff_user(row) for row in staff_rows]})

    if path == '/api/admin/staff' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        allowed_roles = creatable_roles_for(user)
        if not allowed_roles:
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        full_name = (data.get('full_name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        password = data.get('password') or ''
        confirm_password = data.get('confirm_password')
        phone = (data.get('phone') or '').strip() or None
        country = (data.get('country') or '').strip() or 'United Kingdom'
        role = (data.get('role') or 'STAFF').strip().upper()
        status_val = (data.get('status') or 'Active').strip()
        raw_depts = data.get('departments') if 'departments' in data else data.get('department')
        departments, dept_err = parse_departments(raw_depts)
        if dept_err:
            return json_response(start_response, {'status': 'error', 'message': dept_err}, "400 Bad Request")
        if role == 'CLIENT':
            departments = []
        department = store_departments(departments)
        if not full_name:
            return json_response(start_response, {'status': 'error', 'message': 'Full name is required'}, "400 Bad Request")
        if not email or not STAFF_EMAIL_RE.match(email):
            return json_response(start_response, {'status': 'error', 'message': 'A valid email is required'}, "400 Bad Request")
        if not isinstance(password, str) or len(password) < STAFF_MIN_PASSWORD_LEN:
            return json_response(start_response, {
                'status': 'error',
                'message': f'Password must be at least {STAFF_MIN_PASSWORD_LEN} characters'
            }, "400 Bad Request")
        if confirm_password is not None and confirm_password != password:
            return json_response(start_response, {'status': 'error', 'message': 'Passwords do not match'}, "400 Bad Request")
        if status_val not in USER_STATUSES:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid status'}, "400 Bad Request")
        if role not in USER_ROLES:
            return json_response(start_response, {
                'status': 'error',
                'message': 'Role must be one of: ' + ', '.join(USER_ROLES)
            }, "400 Bad Request")
        if role not in allowed_roles:
            return json_response(start_response, {
                'status': 'error',
                'message': 'You cannot assign that role'
            }, "403 Forbidden")
        existing = query_db("SELECT id FROM users WHERE LOWER(email) = ?;", (email,), one=True)
        if existing:
            return json_response(start_response, {'status': 'error', 'message': 'Email is already in use'}, "409 Conflict")
        local_id = f"local_user_{uuid.uuid4().hex[:16]}"
        user_id = execute_db("""
            INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, country, role, status, department)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (local_id, email, hash_password(password), full_name, phone, country, role, status_val, department))
        created = query_db("""
            SELECT id, wordpress_user_id, email, full_name, phone, country, role, department, status, avatar_url, created_at
            FROM users WHERE id = ?;
        """, (user_id,), one=True)
        log_activity(user, 'USER_CREATED', 'users', str(user_id), f"Created user {email} ({role})")
        public_user = public_staff_user(created)
        return json_response(start_response, {
            'status': 'success',
            'message': 'User created. They can sign in with this email and password.',
            'user': public_user
        })

    if path.startswith('/api/admin/staff/') and method == 'PUT':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if not check_permission(user, 'staff.manage'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        sid = path.rstrip('/').split('/')[-1]
        staff_id, err = to_optional_int(sid, 'staff_id')
        if err or not staff_id:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid staff id'}, "400 Bad Request")
        target = query_db("SELECT * FROM users WHERE id = ?;", (staff_id,), one=True)
        if not target or target['role'] not in TASK_ASSIGNABLE_ROLES:
            return json_response(start_response, {'status': 'error', 'message': 'Staff user not found'}, "404 Not Found")
        data = parse_body(environ)
        raw_depts = data.get('departments') if 'departments' in data else data.get('department')
        departments, dept_err = parse_departments(raw_depts)
        if dept_err:
            return json_response(start_response, {'status': 'error', 'message': dept_err}, "400 Bad Request")
        execute_db("UPDATE users SET department = ? WHERE id = ?;", (store_departments(departments), staff_id))
        updated = query_db("""
            SELECT id, wordpress_user_id, email, full_name, phone, country, role, department, status, avatar_url, created_at
            FROM users WHERE id = ?;
        """, (staff_id,), one=True)
        log_activity(user, 'USER_DEPARTMENT', 'users', str(staff_id), f"Set departments to {', '.join(departments) or 'none'}")
        return json_response(start_response, {'status': 'success', 'user': public_staff_user(updated)})

    if path == '/api/admin/impersonate' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if not check_permission(user, 'staff.manage'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        target_id, err = to_optional_int(data.get('user_id'), 'user_id')
        if err or not target_id:
            return json_response(start_response, {'status': 'error', 'message': 'user_id is required'}, "400 Bad Request")
        target = query_db("SELECT * FROM users WHERE id = ?;", (target_id,), one=True)
        if not target:
            return json_response(start_response, {'status': 'error', 'message': 'User not found'}, "404 Not Found")
        if not can_impersonate_user(user, target):
            return json_response(start_response, {'status': 'error', 'message': 'You cannot switch to that user'}, "403 Forbidden")
        current_token = cookie_value(environ, 'session_token')
        origin_token = cookie_value(environ, 'origin_session')
        origin_rec = session_user_from_token(origin_token)
        if not origin_rec:
            origin_token = current_token
        new_token = create_db_session(target['id'])
        log_activity(user, 'USER_IMPERSONATE', 'users', str(target['id']), f"Switched session to {target['email']} ({target['role']})")
        return json_response(start_response, {
            'status': 'success',
            'message': 'Switched user.',
            'user': public_me_user(target, impersonated=True)
        }, extra_headers=[
            ('Set-Cookie', origin_cookie_header(origin_token, environ=environ)),
            ('Set-Cookie', session_cookie_header(new_token, environ=environ)),
        ])

    if path == '/api/admin/impersonate/stop' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        origin_token = cookie_value(environ, 'origin_session')
        origin_rec = session_user_from_token(origin_token)
        if not origin_rec:
            return json_response(start_response, {'status': 'error', 'message': 'No original session to restore'}, "400 Bad Request")
        current_token = cookie_value(environ, 'session_token')
        if current_token and current_token != origin_token:
            revoke_db_session(current_token)
        log_activity(user, 'USER_IMPERSONATE_STOP', 'users', str(origin_rec['id']), 'Returned to original account')
        return json_response(start_response, {
            'status': 'success',
            'message': 'Returned to original account.',
            'user': public_me_user(origin_rec, impersonated=False)
        }, extra_headers=[
            ('Set-Cookie', origin_cookie_header(clear=True, environ=environ)),
            ('Set-Cookie', session_cookie_header(origin_token, environ=environ)),
        ])

    if path == '/api/admin/tasks' and method == 'GET':
        denied = task_auth_error(start_response, user, 'tasks.view')
        if denied:
            return denied
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        sql = TASK_DETAIL_SQL + " WHERE 1=1"
        params = []

        if user['role'] == 'STAFF':
            staff_depts = departments_from_user(user)
            if staff_depts:
                placeholders = ','.join('?' for _ in staff_depts)
                sql += f" AND (t.assigned_staff_id = ? OR (t.assigned_staff_id IS NULL AND t.department IN ({placeholders})))"
                params.extend([user['id'], *staff_depts])
            else:
                sql += " AND t.assigned_staff_id = ?"
                params.append(user['id'])
        else:
            assigned_filter = qs.get('assigned_staff_id', [None])[0]
            if assigned_filter:
                assigned_id, err = to_optional_int(assigned_filter, 'assigned_staff_id')
                if err:
                    return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
                sql += " AND t.assigned_staff_id = ?"
                params.append(assigned_id)

        status_filter = qs.get('status', [None])[0]
        if status_filter:
            if status_filter not in TASK_STATUSES:
                return json_response(start_response, {'status': 'error', 'message': 'Invalid status'}, "400 Bad Request")
            sql += " AND t.status = ?"
            params.append(status_filter)

        priority_filter = qs.get('priority', [None])[0]
        if priority_filter:
            if priority_filter not in TASK_PRIORITIES:
                return json_response(start_response, {'status': 'error', 'message': 'Invalid priority'}, "400 Bad Request")
            sql += " AND t.priority = ?"
            params.append(priority_filter)

        client_filter = qs.get('client_id', [None])[0]
        if client_filter:
            client_id, err = to_optional_int(client_filter, 'client_id')
            if err:
                return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
            sql += " AND t.client_id = ?"
            params.append(client_id)

        order_filter = qs.get('order_id', [None])[0]
        if order_filter:
            order_id, err = to_optional_int(order_filter, 'order_id')
            if err:
                return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
            sql += " AND t.order_id = ?"
            params.append(order_id)

        department_filter, dept_err = normalize_department(qs.get('department', [None])[0])
        if dept_err:
            return json_response(start_response, {'status': 'error', 'message': dept_err}, "400 Bad Request")
        if department_filter:
            sql += " AND t.department = ?"
            params.append(department_filter)

        due_filter = qs.get('due', [None])[0]
        if due_filter == 'overdue':
            sql += " AND t.due_date IS NOT NULL AND date(t.due_date) < date('now') AND t.status NOT IN ('Completed', 'Cancelled')"
        elif due_filter == 'today':
            sql += " AND t.due_date IS NOT NULL AND date(t.due_date) = date('now')"
        elif due_filter:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid due filter'}, "400 Bad Request")

        sql += " ORDER BY t.created_at DESC;"
        tasks = query_db(sql, params)
        return json_response(start_response, {'status': 'success', 'tasks': tasks})

    if path == '/api/admin/tasks' and method == 'POST':
        denied = task_auth_error(start_response, user, 'tasks.create')
        if denied:
            return denied
        data = parse_body(environ)
        title = (data.get('title') or '').strip()
        if not title:
            return json_response(start_response, {'status': 'error', 'message': 'Title is required'}, "400 Bad Request")

        description = data.get('description')
        internal_notes = data.get('internal_notes')
        due_date = data.get('due_date') or None
        priority = data.get('priority') or 'Medium'
        if priority not in TASK_PRIORITIES:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid priority'}, "400 Bad Request")

        assigned_staff_id, err = to_optional_int(data.get('assigned_staff_id'), 'assigned_staff_id')
        if err:
            return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
        department, dept_err = normalize_department(data.get('department'))
        if dept_err:
            return json_response(start_response, {'status': 'error', 'message': dept_err}, "400 Bad Request")
        client_id, err = to_optional_int(data.get('client_id'), 'client_id')
        if err:
            return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
        company_id, err = to_optional_int(data.get('company_id'), 'company_id')
        if err:
            return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
        order_id, err = to_optional_int(data.get('order_id'), 'order_id')
        if err:
            return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")

        ok, msg = validate_task_assignee(assigned_staff_id)
        if not ok:
            return json_response(start_response, {'status': 'error', 'message': msg}, "400 Bad Request")
        ok, msg, client_id, company_id, order_id = validate_task_links(client_id, company_id, order_id)
        if not ok:
            return json_response(start_response, {'status': 'error', 'message': msg}, "400 Bad Request")

        task_id = execute_db("""
            INSERT INTO tasks (
                title, description, priority, status, due_date, department,
                assigned_staff_id, created_by_id, client_id, company_id, order_id,
                internal_notes, updated_at
            ) VALUES (?, ?, ?, 'Open', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'));
        """, (title, description, priority, due_date, department, assigned_staff_id, user['id'], client_id, company_id, order_id, internal_notes))

        log_activity(user, 'TASK_CREATED', 'tasks', str(task_id), f"Created task '{title}'")
        notify_staff_task(
            assigned_staff_id, user['id'],
            'Task Assigned',
            f"You have been assigned task '{title}'.",
            'task_assigned'
        )
        created = fetch_task(task_id)
        return json_response(start_response, {'status': 'success', 'message': 'Task created.', 'task': dict(created)})

    if path.startswith('/api/admin/tasks/') and path.endswith('/complete') and method == 'POST':
        denied = task_auth_error(start_response, user, 'tasks.complete')
        if denied:
            return denied
        parts = path.strip('/').split('/')
        if len(parts) != 5 or not parts[3].isdigit():
            return json_response(start_response, {'status': 'error', 'message': 'Invalid task id'}, "400 Bad Request")
        task_id = int(parts[3])
        task = fetch_task(task_id)
        if not task:
            return json_response(start_response, {'status': 'error', 'message': 'Task not found'}, "404 Not Found")
        if not staff_can_access_task(user, task):
            return json_response(start_response, {'status': 'error', 'message': 'Access denied to target task'}, "403 Forbidden")

        execute_db("""
            UPDATE tasks
            SET status = 'Completed', completed_at = datetime('now'), updated_at = datetime('now')
            WHERE id = ?;
        """, (task_id,))
        log_activity(user, 'TASK_COMPLETED', 'tasks', str(task_id), f"Completed task '{task['title']}'")
        notify_staff_task(
            task.get('assigned_staff_id'), user['id'],
            'Task Completed',
            f"Task '{task['title']}' has been marked completed.",
            'task_completed'
        )
        notify_staff_task(
            task.get('created_by_id'), user['id'],
            'Task Completed',
            f"Task '{task['title']}' has been marked completed.",
            'task_completed'
        )
        updated = fetch_task(task_id)
        return json_response(start_response, {'status': 'success', 'message': 'Task completed.', 'task': dict(updated)})

    if path.startswith('/api/admin/tasks/') and method == 'GET':
        denied = task_auth_error(start_response, user, 'tasks.view')
        if denied:
            return denied
        tid = path.rstrip('/').split('/')[-1]
        if not tid.isdigit():
            return json_response(start_response, {'status': 'error', 'message': 'Invalid task id'}, "400 Bad Request")
        task = fetch_task(int(tid))
        if not task:
            return json_response(start_response, {'status': 'error', 'message': 'Task not found'}, "404 Not Found")
        if not staff_can_access_task(user, task):
            return json_response(start_response, {'status': 'error', 'message': 'Access denied to target task'}, "403 Forbidden")
        return json_response(start_response, {'status': 'success', 'task': dict(task)})

    if path.startswith('/api/admin/tasks/') and method == 'PUT':
        denied = task_auth_error(start_response, user, 'tasks.edit')
        if denied:
            return denied
        tid = path.rstrip('/').split('/')[-1]
        if not tid.isdigit():
            return json_response(start_response, {'status': 'error', 'message': 'Invalid task id'}, "400 Bad Request")
        task_id = int(tid)
        task = fetch_task(task_id)
        if not task:
            return json_response(start_response, {'status': 'error', 'message': 'Task not found'}, "404 Not Found")
        if not staff_can_access_task(user, task):
            return json_response(start_response, {'status': 'error', 'message': 'Access denied to target task'}, "403 Forbidden")

        data = parse_body(environ)
        title = task['title'] if data.get('title') is None else str(data.get('title')).strip()
        if not title:
            return json_response(start_response, {'status': 'error', 'message': 'Title is required'}, "400 Bad Request")
        description = task['description'] if data.get('description') is None else data.get('description')
        internal_notes = task['internal_notes'] if data.get('internal_notes') is None else data.get('internal_notes')
        due_date = task['due_date'] if data.get('due_date') is None else (data.get('due_date') or None)
        priority = task['priority'] if data.get('priority') is None else data.get('priority')
        status_val = task['status'] if data.get('status') is None else data.get('status')
        if priority not in TASK_PRIORITIES:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid priority'}, "400 Bad Request")
        if status_val not in TASK_STATUSES:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid status'}, "400 Bad Request")

        assigned_staff_id = task['assigned_staff_id']
        department = task.get('department')
        client_id = task['client_id']
        company_id = task['company_id']
        order_id = task['order_id']

        if 'department' in data:
            new_dept, dept_err = normalize_department(data.get('department'))
            if dept_err:
                return json_response(start_response, {'status': 'error', 'message': dept_err}, "400 Bad Request")
            if new_dept != task.get('department') and not check_permission(user, 'tasks.assign'):
                return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
            department = new_dept

        if 'assigned_staff_id' in data:
            new_assignee, err = to_optional_int(data.get('assigned_staff_id'), 'assigned_staff_id')
            if err:
                return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
            if new_assignee != task['assigned_staff_id'] and not check_permission(user, 'tasks.assign'):
                return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
            assigned_staff_id = new_assignee

        relink_requested = any(k in data for k in ('client_id', 'company_id', 'order_id'))
        if relink_requested:
            if not check_permission(user, 'tasks.assign'):
                return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
            if 'client_id' in data:
                client_id, err = to_optional_int(data.get('client_id'), 'client_id')
                if err:
                    return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
            if 'company_id' in data:
                company_id, err = to_optional_int(data.get('company_id'), 'company_id')
                if err:
                    return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
            if 'order_id' in data:
                order_id, err = to_optional_int(data.get('order_id'), 'order_id')
                if err:
                    return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")

        ok, msg = validate_task_assignee(assigned_staff_id)
        if not ok:
            return json_response(start_response, {'status': 'error', 'message': msg}, "400 Bad Request")
        ok, msg, client_id, company_id, order_id = validate_task_links(client_id, company_id, order_id)
        if not ok:
            return json_response(start_response, {'status': 'error', 'message': msg}, "400 Bad Request")

        completed_at = task['completed_at']
        if status_val == 'Completed' and task['status'] != 'Completed':
            completed_at_sql = "datetime('now')"
        elif status_val != 'Completed':
            completed_at_sql = "NULL"
        else:
            completed_at_sql = "?"

        if completed_at_sql == "?":
            execute_db("""
                UPDATE tasks SET
                    title = ?, description = ?, priority = ?, status = ?, due_date = ?,
                    department = ?, assigned_staff_id = ?, client_id = ?, company_id = ?, order_id = ?,
                    internal_notes = ?, completed_at = ?, updated_at = datetime('now')
                WHERE id = ?;
            """, (title, description, priority, status_val, due_date, department, assigned_staff_id, client_id, company_id, order_id, internal_notes, completed_at, task_id))
        else:
            execute_db(f"""
                UPDATE tasks SET
                    title = ?, description = ?, priority = ?, status = ?, due_date = ?,
                    department = ?, assigned_staff_id = ?, client_id = ?, company_id = ?, order_id = ?,
                    internal_notes = ?, completed_at = {completed_at_sql}, updated_at = datetime('now')
                WHERE id = ?;
            """, (title, description, priority, status_val, due_date, department, assigned_staff_id, client_id, company_id, order_id, internal_notes, task_id))

        log_activity(user, 'TASK_UPDATED', 'tasks', str(task_id), f"Updated task '{title}'")
        if assigned_staff_id and assigned_staff_id != task['assigned_staff_id']:
            notify_staff_task(
                assigned_staff_id, user['id'],
                'Task Assigned',
                f"You have been assigned task '{title}'.",
                'task_assigned'
            )
        updated = fetch_task(task_id)
        return json_response(start_response, {'status': 'success', 'message': 'Task updated successfully', 'task': dict(updated)})

    if path == '/api/admin/settings' and method == 'GET':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        rows = query_db("SELECT key, value FROM settings;") or []
        allowed = {'company_name', 'support_email', 'currency', 'order_prefix', 'invoice_prefix', 'support_phone', 'logo_url', 'smtp_host', 'smtp_port', 'smtp_user', 'smtp_from', 'uk_formfill_pro_url'}
        settings_dict = {row['key']: row['value'] for row in rows if row['key'] in allowed}
        settings_dict['companies_house_configured'] = bool(companies_house_api_key())
        settings_dict['smtp_configured'] = smtp_configured()
        if not settings_dict.get('uk_formfill_pro_url'):
            settings_dict['uk_formfill_pro_url'] = uk_formfill_pro_url()
        return json_response(start_response, {'status': 'success', 'settings': settings_dict})

    if path == '/api/admin/formfill' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] == 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        return json_response(start_response, {
            'status': 'success',
            'url': uk_formfill_pro_url(),
        })

    if path == '/api/admin/settings/test-email' and method == 'POST':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        if not smtp_configured():
            return json_response(start_response, {'status': 'error', 'message': 'SMTP is not configured yet. Save host, username, and password first.'}, "400 Bad Request")
        data = parse_body(environ)
        recipient = (data.get('email') or 'contact@brixenconsultants.com').strip().lower()
        if not recipient or not STAFF_EMAIL_RE.match(recipient):
            return json_response(start_response, {'status': 'error', 'message': 'Enter a valid email address for the test.'}, "400 Bad Request")
        _, test_text, test_html = build_client_document_email(
            {'full_name': 'Test Recipient', 'email': recipient},
            'Sample_Client_Document.pdf',
            'This is a preview of the branded customer notification email.',
        )
        sent, status = EmailService.send_notification_email(
            recipient,
            'Brixen test: premium document notification preview',
            test_text,
            test_html,
        )
        if not sent:
            return json_response(start_response, {'status': 'error', 'message': f'Test email failed: {status}'}, "502 Bad Gateway")
        return json_response(start_response, {'status': 'success', 'message': f'Test email sent to {recipient}.'})

    if path == '/api/admin/settings' and method == 'POST':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        data = parse_body(environ)
        for k, v in data.items():
            if k == 'companies_house_api_key' and not str(v or '').strip():
                continue
            if k == 'smtp_pass' and not str(v or '').strip():
                continue
            execute_db("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);", (k, str(v)))
        log_activity(user, 'SETTINGS_UPDATE', 'settings', '1', 'Updated admin system settings')
        return json_response(start_response, {
            'status': 'success',
            'message': 'System settings saved.',
            'smtp_configured': smtp_configured(),
        })

    start_response("404 Not Found", [('Content-Type', 'application/json')])
    return [json.dumps({'status': 'error', 'message': f'Route {path} not found'}).encode('utf-8')]

def run():
    global PORT
    port_to_try = PORT
    for attempt in range(5):
        try:
            print(f"Hypetex Limited Server starting on http://{HOST}:{port_to_try}")
            httpd = make_server(HOST, port_to_try, application)
            httpd.serve_forever()
            break
        except OSError as e:
            if getattr(e, 'errno', None) == 48 or 'Address already in use' in str(e):
                print(f"Port {port_to_try} busy, trying port {port_to_try + 1}...")
                port_to_try += 1
            else:
                raise e

if __name__ == '__main__':
    run()
