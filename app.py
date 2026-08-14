import os
import sys
import json
import urllib.parse
import mimetypes
from wsgiref.simple_server import make_server
from db import query_db, execute_db, hash_password, verify_password, needs_rehash, unusable_password_hash, get_db

PORT = int(os.environ.get('PORT', '5050'))
HOST = os.environ.get('HOST', '127.0.0.1')
ALLOWED_CORS_ORIGINS = frozenset({
    'https://brixenconsultants.com',
    'https://www.brixenconsultants.com',
    'https://portal.brixenconsultants.com',
    'http://127.0.0.1:5050',
    'http://localhost:5050',
})
INTERNAL_STAFF_ROLES = ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')

import uuid
import datetime
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

# P4: Email Notification Gateway Abstraction
class EmailService:
    @staticmethod
    def send_notification_email(recipient_email, subject, body_text):
        smtp_host = os.environ.get('SMTP_HOST')
        smtp_port = os.environ.get('SMTP_PORT', '587')
        smtp_user = os.environ.get('SMTP_USER')
        smtp_pass = os.environ.get('SMTP_PASS')
        smtp_from = os.environ.get('SMTP_FROM', 'noreply@brixenconsultants.com')
        
        if not smtp_host or not smtp_user:
            print(f"[EmailService Log] Email to {recipient_email} ('{subject}') - SMTP not configured.")
            return False, "SMTP not configured"
            
        import smtplib
        from email.mime.text import MIMEText
        try:
            msg = MIMEText(body_text)
            msg['Subject'] = subject
            msg['From'] = smtp_from
            msg['To'] = recipient_email
            
            with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
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

def session_cookie_header(token=None, clear=False):
    if clear:
        return 'session_token=; Path=/; HttpOnly; Secure; SameSite=Lax; Expires=Thu, 01 Jan 1970 00:00:00 GMT'
    return f'session_token={token}; Path=/; HttpOnly; Secure; SameSite=Lax'

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
        
    start_response("200 OK", [
        ('Content-Type', ctype),
        ('Content-Length', str(len(content)))
    ])
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
    return row is not None

def require_permission(start_response, user, permission_name):
    if not user:
        return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
    if not check_permission(user, permission_name):
        return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
    return None

TASK_PRIORITIES = ('Low', 'Medium', 'High', 'Urgent')
TASK_STATUSES = ('Open', 'In Progress', 'Completed', 'Cancelled')
TASK_ASSIGNABLE_ROLES = ('STAFF', 'MANAGER', 'ADMIN', 'SUPER_ADMIN')
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

def staff_can_access_task(user, task):
    if not user or not task:
        return False
    if user['role'] == 'STAFF':
        return task.get('assigned_staff_id') == user['id']
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
        f"Hello {recipient['full_name']},\n\n{message}\n\nLog in to the Brixen Portal to review: http://127.0.0.1:5050"
    )

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
            phone = u_data.get('phone', '')
            country = u_data.get('country', 'United Kingdom')
            
            if not email:
                return json_response(start_response, {'status': 'error', 'message': 'Email required'}, "400 Bad Request")
                
            # Match existing user by wordpress_user_id or email
            existing_user = query_db("SELECT id FROM users WHERE wordpress_user_id = ? OR email = ?;", (wp_user_id, email), one=True)
            if existing_user:
                execute_db("""
                    UPDATE users 
                    SET wordpress_user_id = ?, full_name = ?, phone = ?, country = ?, last_synced_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                """, (wp_user_id, full_name, phone, country, existing_user['id']))
                action_msg = "Updated existing client from WordPress webhook"
                client_id = existing_user['id']
            else:
                pwd_hash = unusable_password_hash()
                client_id = execute_db("""
                    INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, country, role, status, last_synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'CLIENT', 'Active', CURRENT_TIMESTAMP);
                """, (wp_user_id, email, pwd_hash, full_name, phone, country))
                action_msg = "Created new client from WordPress webhook"
                
                # Auto-create notification for new client
                execute_db("""
                    INSERT INTO notifications (user_id, title, message, type)
                    VALUES (?, 'Welcome to Brixen Consultants', 'Your client portal has been connected to your website account.', 'system');
                """, (client_id,))
                
            log_activity(None, action_msg, 'users', client_id, f"WP User ID: {wp_user_id}, Email: {email}")
            return json_response(start_response, {
                'status': 'success',
                'message': action_msg,
                'client_id': client_id,
                'wordpress_user_id': wp_user_id
            })
            
        elif event_type in ('order.created', 'order.updated'):
            o_data = payload.get('data', payload)
            order_num = o_data.get('order_number') or f"#GB{int(datetime.datetime.now().timestamp())}"
            wp_user_id = str(o_data.get('wordpress_user_id') or '')
            email = o_data.get('email', '')
            service_name = o_data.get('service_name', 'Corporate Formation Service')
            price = float(o_data.get('price', 150.00))
            total = float(o_data.get('total', price * 1.2))
            vat = total - price
            status = o_data.get('status', 'Processing')
            
            # Locate client by wordpress_user_id first
            client_rec = None
            if wp_user_id:
                client_rec = query_db("SELECT id FROM users WHERE wordpress_user_id = ?;", (wp_user_id,), one=True)
            if not client_rec and email:
                client_rec = query_db("SELECT id FROM users WHERE email = ?;", (email,), one=True)
                
            if client_rec:
                cid = client_rec['id']
                order_notes = o_data.get('notes', '')
            else:
                # Safe Guest Checkout Handling:
                # Do NOT auto-create permanent CRM clients from unverified guest orders.
                # Bind order to a guest account or mark for staff verification.
                guest_user = query_db("SELECT id FROM users WHERE email = 'guest.unlinked@brixenconsultants.com';", one=True)
                if not guest_user:
                    pwd_hash = unusable_password_hash()
                    cid = execute_db("""
                        INSERT INTO users (wordpress_user_id, email, password_hash, full_name, role, status, last_synced_at)
                        VALUES ('guest_unlinked', 'guest.unlinked@brixenconsultants.com', ?, 'Unlinked Guest Customer', 'CLIENT', 'Active', CURRENT_TIMESTAMP);
                    """, (pwd_hash,))
                else:
                    cid = guest_user['id']
                order_notes = f"Guest Order (Email: {email}) - Pending Staff Verification"
                status = 'Pending Verification'
                
            existing_ord = query_db("SELECT id FROM orders WHERE order_number = ?;", (order_num,), one=True)
            if existing_ord:
                execute_db("""
                    UPDATE orders SET status = ?, total = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;
                """, (status, total, existing_ord['id']))
                ord_id = existing_ord['id']
            else:
                ord_id = execute_db("""
                    INSERT INTO orders (order_number, user_id, service_name, price, vat, total, status, progress_percent, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 20, ?);
                """, (order_num, cid, service_name, price, vat, total, status, order_notes))
                
            return json_response(start_response, {'status': 'success', 'order_id': ord_id, 'order_number': order_num})
            
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
            order_num = o.get('order_number') or f"#WC-{o.get('id', '')}"
            wp_user_id = str(o.get('wordpress_user_id') or '')
            email = o.get('email', '')
            service_name = o.get('service_name', 'Corporate Formation Service')
            price = float(o.get('price', 150.00))
            total = float(o.get('total', price * 1.2))
            vat = total - price
            status = o.get('status', 'Processing')
            
            client_rec = query_db("SELECT id FROM users WHERE wordpress_user_id = ? OR email = ?;", (wp_user_id, email), one=True)
            if not client_rec:
                if not email: continue
                pwd_hash = unusable_password_hash()
                cid = execute_db("""
                    INSERT INTO users (wordpress_user_id, email, password_hash, full_name, role, status, last_synced_at)
                    VALUES (?, ?, ?, ?, 'CLIENT', 'Active', CURRENT_TIMESTAMP);
                """, (wp_user_id, email, pwd_hash, o.get('full_name', email.split('@')[0])))
            else:
                cid = client_rec['id']
                
            existing_ord = query_db("SELECT id FROM orders WHERE order_number = ?;", (order_num,), one=True)
            if existing_ord:
                execute_db("""
                    UPDATE orders SET status = ?, total = ? WHERE id = ?;
                """, (status, total, existing_ord['id']))
                updated_cnt += 1
            else:
                execute_db("""
                    INSERT INTO orders (order_number, user_id, service_name, price, vat, total, status, progress_percent)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 20);
                """, (order_num, cid, service_name, price, vat, total, status))
                synced_cnt += 1
                
        return json_response(start_response, {
            'status': 'success',
            'summary': {
                'created': synced_cnt,
                'updated': updated_cnt,
                'total_processed': len(order_list)
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

        try:
            ts_int = int(timestamp)
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Invalid timestamp'}, "401 Unauthorized")
        if abs(int(datetime.datetime.now().timestamp()) - ts_int) > 300:
            return json_response(start_response, {'status': 'error', 'message': 'SSO token expired'}, "401 Unauthorized")

        expected_payload = f"{wp_id}|{email}|{timestamp}".encode('utf-8')
        expected_sig = hmac.new(secret.encode('utf-8'), expected_payload, hashlib.sha256).hexdigest()
        provided_sig = sig.replace('sha256=', '').strip()
        try:
            sig_ok = hmac.compare_digest(provided_sig, expected_sig)
        except (TypeError, ValueError):
            sig_ok = False
        if not sig_ok:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid SSO signature'}, "401 Unauthorized")

        client_rec = query_db("SELECT id, email, full_name, role FROM users WHERE wordpress_user_id = ? OR email = ?;", (wp_id, email), one=True)
        if not client_rec:
            return json_response(start_response, {'status': 'error', 'message': 'Client record not found for SSO'}, "404 Not Found")
            
        token = create_db_session(client_rec['id'])
        log_activity(client_rec, 'Client logged in via WordPress SSO', 'users', client_rec['id'])

        if method == 'GET':
            start_response("302 Found", [
                ('Location', '/'),
                ('Set-Cookie', session_cookie_header(token))
            ])
            return [b""]
        else:
            return json_response(start_response, {
                'status': 'success',
                'user': dict(client_rec)
            }, extra_headers=[('Set-Cookie', session_cookie_header(token))])

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
        documents = query_db("SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC;", (cid,))
        tickets = query_db("SELECT * FROM support_tickets WHERE user_id = ? ORDER BY created_at DESC;", (cid,))
        notifications = query_db("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC;", (cid,))
        logs = query_db("SELECT * FROM activity_logs WHERE user_id = ? ORDER BY created_at DESC LIMIT 50;", (cid,))
        
        return json_response(start_response, {
            'status': 'success',
            'client': dict(target_user),
            'companies': companies,
            'orders': orders,
            'invoices': invoices,
            'documents': documents,
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
            
        # Security: CLIENT may only download their own files; staff need documents.view
        if user['role'] == 'CLIENT':
            if doc['user_id'] != user['id']:
                return json_response(start_response, {'status': 'error', 'message': 'Access denied to target document'}, "403 Forbidden")
        elif not check_permission(user, 'documents.view'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
            
        file_path = doc['file_path']
        if os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                content = f.read()
            mime_type = 'application/pdf' if file_path.lower().endswith('.pdf') else 'application/octet-stream'
            safe_name = os.path.basename(doc['name'])
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
            'support_phone', 'currency', 'vat_rate', 'order_prefix', 'invoice_prefix'
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
        
        u_dict = dict(found_user)
        u_dict.pop('password_hash', None)
        u_dict.pop('session_token', None)
        
        cookie_header = ('Set-Cookie', session_cookie_header(token))
        return json_response(start_response, {'status': 'success', 'user': u_dict}, extra_headers=[cookie_header])

    if path == '/api/auth/me' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        src = dict(user)
        u_dict = {
            'wordpress_user_id': src.get('wordpress_user_id'),
            'email': src.get('email'),
            'full_name': src.get('full_name'),
            'phone': src.get('phone'),
            'country': src.get('country'),
            'status': src.get('status'),
            'role': src.get('role')
        }
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
        cookie_header = ('Set-Cookie', session_cookie_header(clear=True))
        return json_response(start_response, {'status': 'success', 'message': 'Logged out'}, extra_headers=[cookie_header])

    if path == '/api/auth/profile' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        data = parse_body(environ)
        full_name = data.get('full_name', user['full_name'])
        phone = data.get('phone', user['phone'])
        country = data.get('country', user['country'])
        address = data.get('address', user['address'])
        
        execute_db("""
            UPDATE users SET full_name = ?, phone = ?, country = ?, address = ? WHERE id = ?;
        """, (full_name, phone, country, address, user['id']))
        
        if data.get('new_password'):
            execute_db("UPDATE users SET password_hash = ? WHERE id = ?;", (hash_password(data['new_password']), user['id']))
            
        log_activity(user, 'PROFILE_UPDATE', 'users', str(user['id']), 'Updated profile settings.')
        return json_response(start_response, {'status': 'success', 'message': 'Profile updated successfully'})

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
        
        # Deadlines calculations
        overdue_cnt = query_db("SELECT COUNT(*) as c FROM addresses WHERE user_id = ? AND expiry_date < date('now') AND status != 'Active';", (uid,), one=True)['c']
        upcoming_cnt = query_db("SELECT COUNT(*) as c FROM addresses WHERE user_id = ? AND expiry_date BETWEEN date('now') AND date('now', '+30 days');", (uid,), one=True)['c']
        
        # Active orders (incomplete)
        active_orders = query_db("""
            SELECT o.*, c.name as company_name 
            FROM orders o 
            LEFT JOIN companies c ON o.company_id = c.id 
            WHERE o.user_id = ? 
              AND o.status IN ('Processing', 'In Progress', 'Pending') 
              AND o.service_name NOT LIKE '%Proxy%' 
              AND o.service_name NOT LIKE '%Registered Agent%'
            ORDER BY o.created_at DESC;
        """, (uid,))
        
        # Enhance active orders with timeline stage states and services sub-table
        enhanced_orders = []
        for ord_row in active_orders:
            o_dict = dict(ord_row)
            p = o_dict['progress_percent']
            
            # 5 Stages: Received, In Progress, Finance, QC Ready, Delivered
            o_dict['stages'] = [
                {'name': 'Received', 'status': 'completed' if p >= 20 else 'current'},
                {'name': 'In Progress', 'status': 'completed' if p >= 50 else ('current' if p >= 20 else 'pending')},
                {'name': 'Finance', 'status': 'completed' if p >= 75 else ('current' if p >= 50 else 'pending')},
                {'name': 'QC Ready', 'status': 'completed' if p >= 90 else ('current' if p >= 75 else 'pending')},
                {'name': 'Delivered', 'status': 'completed' if p == 100 else ('current' if p >= 90 else 'pending')}
            ]
            
            # Sub-services breakdown
            if 'Office' in o_dict['service_name']:
                o_dict['service_items'] = [
                    {'name': 'UK - Address Service', 'status': 'In Progress' if p < 100 else 'Done'},
                    {'name': 'Mail Handling Clearance', 'status': 'Done' if p >= 50 else 'Pending'}
                ]
                o_dict['done_items_count'] = 1 if p >= 50 else 0
                o_dict['total_items_count'] = 2
            elif 'Formation' in o_dict['service_name']:
                o_dict['service_items'] = [
                    {'name': 'Companies House Incorporation', 'status': 'In Progress' if p < 100 else 'Done'},
                    {'name': 'Share Structure & Articles', 'status': 'Done' if p >= 50 else 'Pending'}
                ]
                o_dict['done_items_count'] = 1 if p >= 50 else 0
                o_dict['total_items_count'] = 2
            else:
                o_dict['service_items'] = [
                    {'name': o_dict['service_name'], 'status': 'In Progress' if p < 100 else 'Done'}
                ]
                o_dict['done_items_count'] = 1 if p == 100 else 0
                o_dict['total_items_count'] = 1
                
            enhanced_orders.append(o_dict)
            
        now_str = datetime.datetime.now().strftime("%d %B %Y")
        
        return json_response(start_response, {
            'status': 'success',
            'current_date': now_str,
            'user_name': user['full_name'],
            'total_companies': comp_count,
            'uk_companies': uk_comps,
            'intl_companies': intl_comps,
            'overdue_deadlines': overdue_cnt,
            'upcoming_deadlines': upcoming_cnt,
            'active_orders_count': len(enhanced_orders),
            'active_orders': enhanced_orders
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
        sql = "SELECT o.*, c.name as company_name FROM orders o LEFT JOIN companies c ON o.company_id = c.id WHERE o.user_id = ? AND o.service_name NOT LIKE '%Proxy%' AND o.service_name NOT LIKE '%Registered Agent%'"
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
            SELECT o.*, c.name as company_name, u.full_name as client_name, u.email as client_email, s.full_name as assigned_staff_name
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
            
        timeline = query_db("SELECT * FROM order_timeline WHERE order_id = ? ORDER BY id ASC;", (oid,))
        invoice = query_db("SELECT * FROM invoices WHERE order_id = ?;", (oid,), one=True)
        documents = query_db("SELECT * FROM documents WHERE order_id = ?;", (oid,))
        
        return json_response(start_response, {
            'status': 'success',
            'order': order,
            'timeline': timeline,
            'invoice': invoice,
            'documents': documents
        })

    # ----------------------------------------------------
    # API: Companies, Addresses, Invoices, Documents, etc.
    # ----------------------------------------------------
    if path == '/api/client/companies' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        comps = query_db("SELECT * FROM companies WHERE user_id = ? ORDER BY created_at DESC;", (user['id'],))
        return json_response(start_response, {'status': 'success', 'companies': comps})

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
            WHERE d.user_id = ? ORDER BY d.created_at DESC;
        """, (user['id'],))
        return json_response(start_response, {'status': 'success', 'documents': docs})

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
        
        # P2: Private storage structure & path traversal defense
        safe_filename = os.path.basename(doc_name)
        allowed_exts = ('.pdf', '.png', '.jpg', '.jpeg', '.doc', '.docx', '.zip')
        if not safe_filename.lower().endswith(allowed_exts):
            return json_response(start_response, {'status': 'error', 'message': 'Invalid file format'}, "400 Bad Request")
            
        client_dir = os.path.abspath(os.path.join(STORAGE_DIR, 'clients', str(user['id']), 'orders', str(order_id or 'general'), 'documents'))
        os.makedirs(client_dir, exist_ok=True)
        target_path = os.path.abspath(os.path.join(client_dir, safe_filename))
        
        if not target_path.startswith(os.path.abspath(STORAGE_DIR)):
            return json_response(start_response, {'status': 'error', 'message': 'Path traversal attempt blocked'}, "400 Bad Request")
            
        # Write actual binary file bytes to disk
        if b64_content:
            import base64
            try:
                file_bytes = base64.b64decode(b64_content)
            except Exception:
                return json_response(start_response, {'status': 'error', 'message': 'Invalid base64 payload'}, "400 Bad Request")
        else:
            file_bytes = f"Sample Document Content for {doc_name}".encode('utf-8')
            
        with open(target_path, 'wb') as f:
            f.write(file_bytes)
            
        file_size_str = f"{len(file_bytes) / 1024:.1f} KB" if len(file_bytes) >= 1024 else f"{len(file_bytes)} B"
            
        doc_id = execute_db("""
            INSERT INTO documents (user_id, company_id, order_id, name, category, file_path, file_type, file_size, status, uploaded_by, review_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (user['id'], company_id, order_id, doc_name, category, target_path, 'PDF Document', file_size_str, 'Pending Review', user['full_name'], 'Awaiting compliance officer audit.'))
        
        log_activity(user, 'DOCUMENT_UPLOAD', 'documents', str(doc_id), f"Uploaded document {doc_name}")
        
        # P4: Portal notification & Email dispatch
        note_title = "Document Uploaded"
        note_msg = f"New document '{doc_name}' uploaded successfully and queued for review."
        execute_db("INSERT INTO notifications (user_id, title, message, type) VALUES (?, ?, ?, 'DOCUMENT');", (user['id'], note_title, note_msg))
        EmailService.send_notification_email(user['email'], 'Document Uploaded - Brixen Consultants Portal', f"Hello {user['full_name']},\n\nYour document '{doc_name}' has been uploaded to your secure Brixen Consultants portal.\n\nLog in to review: http://127.0.0.1:5050")
        
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
                else:
                    execute_db("UPDATE support_tickets SET status = 'Waiting for Customer' WHERE id = ?;", (tid,))
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
        denied = require_permission(start_response, user, 'orders.view')
        if denied:
            return denied
            
        tot_customers = query_db("SELECT COUNT(*) as c FROM users WHERE role = 'CLIENT';", one=True)['c']
        tot_companies = query_db("SELECT COUNT(*) as c FROM companies;", one=True)['c']
        tot_orders = query_db("SELECT COUNT(*) as c FROM orders;", one=True)['c']
        pending_orders = query_db("SELECT COUNT(*) as c FROM orders WHERE status IN ('Pending', 'Processing', 'In Progress');", one=True)['c']
        completed_orders = query_db("SELECT COUNT(*) as c FROM orders WHERE status = 'Completed';", one=True)['c']
        total_revenue = query_db("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status != 'Cancelled';", one=True)['s']
        pending_payments = query_db("SELECT COALESCE(SUM(total), 0) as s FROM invoices WHERE status = 'Pending';", one=True)['s']
        open_tickets = query_db("SELECT COUNT(*) as c FROM support_tickets WHERE status IN ('Open', 'In Progress');", one=True)['c']
        
        # Monthly charts simulation data from real orders
        month_rows = query_db("""
            SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as cnt, SUM(total) as rev
            FROM orders
            GROUP BY month
            ORDER BY month ASC
            LIMIT 6;
        """)
        
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
                'monthly': month_rows
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
        return json_response(start_response, {'status': 'success', 'customers': customers})

    if path == '/api/admin/customers/status' and method == 'POST':
        if not user or not check_permission(user, 'clients.edit'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        cid = data.get('customer_id')
        new_status = data.get('status')
        execute_db("UPDATE users SET status = ? WHERE id = ?;", (new_status, cid))
        log_activity(user, 'CUSTOMER_STATUS_CHANGE', 'users', str(cid), f"Updated status to {new_status}")
        return json_response(start_response, {'status': 'success', 'message': f'Customer status updated to {new_status}'})

    if path == '/api/admin/orders' and method == 'GET':
        if not user or not check_permission(user, 'orders.view'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        sql = """
            SELECT o.*, u.full_name as client_name, u.email as client_email, c.name as company_name
            FROM orders o
            JOIN users u ON o.user_id = u.id
            LEFT JOIN companies c ON o.company_id = c.id
        """
        params = []
        if user['role'] == 'MANAGER':
            sql += " WHERE (o.assigned_staff_id = ? OR o.assigned_staff_id IS NULL OR o.assigned_staff_id IN (SELECT id FROM users WHERE role = 'STAFF'))"
            params.append(user['id'])
        sql += " ORDER BY o.created_at DESC;"
        orders = query_db(sql, params)
        return json_response(start_response, {'status': 'success', 'orders': orders})

    if path.startswith('/api/admin/orders/') and method == 'PUT':
        if not user or not check_permission(user, 'orders.edit'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        oid = path.split('/')[-1]
        data = parse_body(environ)
        
        status = data.get('status')
        progress = data.get('progress_percent')
        notes = data.get('notes')
        
        if status is not None:
            execute_db("UPDATE orders SET status = ? WHERE id = ?;", (status, oid))
        if progress is not None:
            execute_db("UPDATE orders SET progress_percent = ? WHERE id = ?;", (int(progress), oid))
        if notes is not None:
            execute_db("UPDATE orders SET notes = ? WHERE id = ?;", (notes, oid))
            
        target_order = query_db("SELECT o.*, u.email, u.full_name FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = ?;", (oid,), one=True)
        if target_order:
            # Auto trigger notification for client
            msg = f"Order {target_order['order_number']} status changed to '{target_order['status']}' ({target_order['progress_percent']}% complete)."
            execute_db("""
                INSERT INTO notifications (user_id, title, message, type, link)
                VALUES (?, ?, ?, ?, ?);
            """, (target_order['user_id'], 'Order Status Updated', msg, 'order_update', '/orders'))
            
            # P4: Email notification trigger
            EmailService.send_notification_email(target_order['email'], f"Order Status Updated - {target_order['order_number']}", f"Hello {target_order['full_name']},\n\nYour order {target_order['order_number']} has been updated to '{target_order['status']}' ({target_order['progress_percent']}% complete).\n\nLog in to your Brixen Portal to track progress: http://127.0.0.1:5050")
            
        log_activity(user, 'ORDER_UPDATE', 'orders', str(oid), f"Updated order #{oid} status to {status}")
        return json_response(start_response, {'status': 'success', 'message': 'Order updated successfully'})

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
        sid = execute_db("""
            INSERT INTO services (name, description, category, price, duration, status, featured, vat_rate, renewal_period)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (data['name'], data['description'], data['category'], float(data['price']), data.get('duration', '12 Months'), 'Active', 1 if data.get('featured') else 0, float(data.get('vat_rate', 0.20)), data.get('renewal_period', 'Annual')))
        log_activity(user, 'SERVICE_CREATED', 'services', str(sid), f"Created service {data['name']}")
        return json_response(start_response, {'status': 'success', 'message': 'Service created.'})

    if path == '/api/admin/documents' and method == 'GET':
        denied = require_permission(start_response, user, 'documents.view')
        if denied:
            return denied
        docs = query_db("""
            SELECT d.*, u.full_name as client_name, u.email as client_email, c.name as company_name
            FROM documents d
            JOIN users u ON d.user_id = u.id
            LEFT JOIN companies c ON d.company_id = c.id
            ORDER BY d.created_at DESC;
        """)
        return json_response(start_response, {'status': 'success', 'documents': docs})

    if path.startswith('/api/admin/documents/') and method == 'PUT':
        denied = require_permission(start_response, user, 'documents.view')
        if denied:
            return denied
        doc_id = path.split('/')[-1]
        data = parse_body(environ)
        status = data.get('status')
        review_notes = data.get('review_notes', '')
        
        execute_db("UPDATE documents SET status = ?, review_notes = ? WHERE id = ?;", (status, review_notes, doc_id))
        
        target_doc = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
        if target_doc:
            msg = f"Document '{target_doc['name']}' review status: {status}."
            execute_db("""
                INSERT INTO notifications (user_id, title, message, type, link)
                VALUES (?, ?, ?, ?, ?);
            """, (target_doc['user_id'], 'Document Reviewed', msg, 'document_review', '/documents'))
            
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
        staff_rows = query_db("""
            SELECT id, wordpress_user_id, email, full_name, phone, role, status, avatar_url
            FROM users
            WHERE role IN ('STAFF', 'MANAGER', 'ADMIN', 'SUPER_ADMIN')
              AND status = 'Active'
            ORDER BY full_name ASC;
        """)
        return json_response(start_response, {'status': 'success', 'staff': staff_rows})

    if path == '/api/admin/tasks' and method == 'GET':
        denied = task_auth_error(start_response, user, 'tasks.view')
        if denied:
            return denied
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        sql = TASK_DETAIL_SQL + " WHERE 1=1"
        params = []

        if user['role'] == 'STAFF':
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
                title, description, priority, status, due_date,
                assigned_staff_id, created_by_id, client_id, company_id, order_id,
                internal_notes, updated_at
            ) VALUES (?, ?, ?, 'Open', ?, ?, ?, ?, ?, ?, ?, datetime('now'));
        """, (title, description, priority, due_date, assigned_staff_id, user['id'], client_id, company_id, order_id, internal_notes))

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
        client_id = task['client_id']
        company_id = task['company_id']
        order_id = task['order_id']

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
                    assigned_staff_id = ?, client_id = ?, company_id = ?, order_id = ?,
                    internal_notes = ?, completed_at = ?, updated_at = datetime('now')
                WHERE id = ?;
            """, (title, description, priority, status_val, due_date, assigned_staff_id, client_id, company_id, order_id, internal_notes, completed_at, task_id))
        else:
            execute_db(f"""
                UPDATE tasks SET
                    title = ?, description = ?, priority = ?, status = ?, due_date = ?,
                    assigned_staff_id = ?, client_id = ?, company_id = ?, order_id = ?,
                    internal_notes = ?, completed_at = {completed_at_sql}, updated_at = datetime('now')
                WHERE id = ?;
            """, (title, description, priority, status_val, due_date, assigned_staff_id, client_id, company_id, order_id, internal_notes, task_id))

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

    if path == '/api/admin/settings' and method == 'POST':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        data = parse_body(environ)
        for k, v in data.items():
            execute_db("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);", (k, str(v)))
        log_activity(user, 'SETTINGS_UPDATE', 'settings', '1', 'Updated admin system settings')
        return json_response(start_response, {'status': 'success', 'message': 'System settings saved.'})

    start_response("404 Not Found", [('Content-Type', 'application/json')])
    return [json.dumps({'status': 'error', 'message': f'Route {path} not found'}).encode('utf-8')]

def run():
    print(f"Hypetex Limited Server starting on http://{HOST}:{PORT}")
    httpd = make_server(HOST, PORT, application)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")

if __name__ == '__main__':
    run()
