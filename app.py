import os
import sys
import json
import urllib.parse
import mimetypes
from wsgiref.simple_server import make_server
from db import query_db, execute_db, hash_password, get_db

PORT = 5050
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
        ('Access-Control-Allow-Origin', '*'),
        ('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS'),
        ('Access-Control-Allow-Headers', 'Content-Type, Authorization, Cookie')
    ]
    if extra_headers:
        headers.extend(extra_headers)
    start_response(status, headers)
    return [body]

def serve_static(environ, start_response, filepath):
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

def application(environ, start_response):
    path = environ.get('PATH_INFO', '')
    method = environ.get('REQUEST_METHOD', 'GET')
    
    # Handle CORS preflight
    if method == 'OPTIONS':
        start_response("200 OK", [
            ('Access-Control-Allow-Origin', '*'),
            ('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS'),
            ('Access-Control-Allow-Headers', 'Content-Type, Authorization, Cookie, X-Brixen-Signature, X-WP-Signature')
        ])
        return [b""]
    
    user = get_current_user(environ)
    
    # ----------------------------------------------------
    # Static files & Homepage
    # ----------------------------------------------------
    if path == '/' or path == '/index.html':
        return serve_static(environ, start_response, os.path.join(TEMPLATES_DIR, 'index.html'))
    elif path.startswith('/static/'):
        rel_path = path[len('/static/'):]
        return serve_static(environ, start_response, os.path.join(STATIC_DIR, rel_path))

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
                pwd_hash = hash_password("WpClientPass2026!")
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
                    pwd_hash = hash_password("GuestAccountUnlinked2026!")
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
                """, (wp_id, email, hash_password('SyncPass123!'), name, phone))
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
                pwd_hash = hash_password("SyncPass123!")
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
            timestamp = params.get('timestamp', ['0'])[0]
            sig = params.get('signature', [''])[0]
        else:
            data = parse_body(environ)
            wp_id = str(data.get('wordpress_user_id', ''))
            email = data.get('email', '').strip()
            timestamp = str(data.get('timestamp', '0'))
            sig = data.get('signature', '')

        row = query_db("SELECT value FROM settings WHERE key = 'wordpress_webhook_secret';", one=True)
        secret = (row['value'] if row and row['value'] else None) or os.environ.get('WORDPRESS_WEBHOOK_SECRET')
        if not secret and timestamp != '0':
            return json_response(start_response, {'status': 'error', 'message': 'SSO feature unconfigured'}, "500 Internal Server Error")
        
        # Verify signed token timestamp expiration (300s window) & HMAC signature
        if timestamp != '0':
            try:
                ts_int = int(timestamp)
                if abs(int(datetime.datetime.now().timestamp()) - ts_int) > 300:
                    return json_response(start_response, {'status': 'error', 'message': 'SSO token expired'}, "401 Unauthorized")
            except ValueError:
                return json_response(start_response, {'status': 'error', 'message': 'Invalid timestamp'}, "400 Bad Request")

            expected_payload = f"{wp_id}|{email}|{timestamp}".encode('utf-8')
            expected_sig = hmac.new(secret.encode('utf-8'), expected_payload, hashlib.sha256).hexdigest()
            if not sig or not hmac.compare_digest(sig.replace('sha256=', ''), expected_sig):
                return json_response(start_response, {'status': 'error', 'message': 'Invalid SSO signature'}, "401 Unauthorized")

        client_rec = query_db("SELECT id, email, full_name, role FROM users WHERE wordpress_user_id = ? OR email = ?;", (wp_id, email), one=True)
        if not client_rec:
            return json_response(start_response, {'status': 'error', 'message': 'Client record not found for SSO'}, "404 Not Found")
            
        token = create_db_session(client_rec['id'])
        log_activity(client_rec, 'Client logged in via WordPress SSO', 'users', client_rec['id'])

        if method == 'GET':
            start_response("302 Found", [
                ('Location', '/'),
                ('Set-Cookie', f'session_token={token}; Path=/; HttpOnly; SameSite=Lax')
            ])
            return [b""]
        else:
            return json_response(start_response, {
                'status': 'success',
                'token': token,
                'user': client_rec
            }, extra_headers=[('Set-Cookie', f'session_token={token}; Path=/; HttpOnly; SameSite=Lax')])

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
            
        # Security check: Client can ONLY access their OWN documents
        if user['role'] == 'CLIENT' and doc['user_id'] != user['id']:
            return json_response(start_response, {'status': 'error', 'message': 'Access denied to target document'}, "403 Forbidden")
            
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
        else:
            return json_response(start_response, {
                'status': 'success',
                'document': dict(doc),
                'download_url': doc['file_path']
            })

    # ----------------------------------------------------
    # API: System Settings
    # ----------------------------------------------------
    if path == '/api/settings' and method == 'GET':
        rows = query_db("SELECT key, value FROM settings;")
        settings_dict = {r['key']: r['value'] for r in rows}
        return json_response(start_response, {'status': 'success', 'settings': settings_dict})

    # ----------------------------------------------------
    # API: Auth
    # ----------------------------------------------------
    if path == '/api/auth/login' and method == 'POST':
        data = parse_body(environ)
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        
        phash = hash_password(password)
        found_user = query_db("SELECT * FROM users WHERE LOWER(email) = ? AND password_hash = ?;", (email, phash), one=True)
        if not found_user:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid email or password.'}, "401 Unauthorized")
        
        if found_user['status'] != 'Active':
            return json_response(start_response, {'status': 'error', 'message': 'Your account is suspended. Please contact support.'}, "403 Forbidden")
            
        token = create_db_session(found_user['id'])
        log_activity(found_user, 'USER_LOGIN', 'users', str(found_user['id']), 'User logged in successfully.')
        
        u_dict = dict(found_user)
        del u_dict['password_hash']
        
        cookie_header = ('Set-Cookie', f'session_token={token}; Path=/; HttpOnly; SameSite=Lax')
        return json_response(start_response, {'status': 'success', 'user': u_dict, 'token': token}, extra_headers=[cookie_header])

    if path == '/api/auth/me' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        u_dict = dict(user)
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
        cookie_header = ('Set-Cookie', 'session_token=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT')
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
            WHERE o.id = ? AND (o.user_id = ? OR ? IN ('ADMIN', 'STAFF'));
        """, (oid, user['id'], user['role']), one=True)
        
        if not order:
            return json_response(start_response, {'status': 'error', 'message': 'Order not found'}, "404 Not Found")
            
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
        if user['role'] in ('ADMIN', 'STAFF'):
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
            
        if user['role'] == 'CLIENT' and t_rec['user_id'] != user['id']:
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
        if not user or user['role'] not in ('ADMIN', 'STAFF'):
            return json_response(start_response, {'status': 'error', 'message': 'Admin access required'}, "403 Forbidden")
            
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
        if not user or user['role'] not in ('ADMIN', 'STAFF'):
            return json_response(start_response, {'status': 'error', 'message': 'Admin access required'}, "403 Forbidden")
        svcs = query_db("SELECT * FROM services ORDER BY created_at DESC;")
        return json_response(start_response, {'status': 'success', 'services': svcs})

    if path == '/api/admin/services' and method == 'POST':
        if not user or user['role'] not in ('ADMIN', 'STAFF'):
            return json_response(start_response, {'status': 'error', 'message': 'Admin access required'}, "403 Forbidden")
        data = parse_body(environ)
        sid = execute_db("""
            INSERT INTO services (name, description, category, price, duration, status, featured, vat_rate, renewal_period)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (data['name'], data['description'], data['category'], float(data['price']), data.get('duration', '12 Months'), 'Active', 1 if data.get('featured') else 0, float(data.get('vat_rate', 0.20)), data.get('renewal_period', 'Annual')))
        log_activity(user, 'SERVICE_CREATED', 'services', str(sid), f"Created service {data['name']}")
        return json_response(start_response, {'status': 'success', 'message': 'Service created.'})

    if path == '/api/admin/documents' and method == 'GET':
        if not user or user['role'] not in ('ADMIN', 'STAFF'):
            return json_response(start_response, {'status': 'error', 'message': 'Admin access required'}, "403 Forbidden")
        docs = query_db("""
            SELECT d.*, u.full_name as client_name, u.email as client_email, c.name as company_name
            FROM documents d
            JOIN users u ON d.user_id = u.id
            LEFT JOIN companies c ON d.company_id = c.id
            ORDER BY d.created_at DESC;
        """)
        return json_response(start_response, {'status': 'success', 'documents': docs})

    if path.startswith('/api/admin/documents/') and method == 'PUT':
        if not user or user['role'] not in ('ADMIN', 'STAFF'):
            return json_response(start_response, {'status': 'error', 'message': 'Admin access required'}, "403 Forbidden")
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
        if not user or user['role'] not in ('ADMIN', 'STAFF'):
            return json_response(start_response, {'status': 'error', 'message': 'Admin access required'}, "403 Forbidden")
        logs = query_db("SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT 100;")
        return json_response(start_response, {'status': 'success', 'logs': logs})

    if path == '/api/admin/settings' and method == 'POST':
        if not user or user['role'] != 'ADMIN':
            return json_response(start_response, {'status': 'error', 'message': 'Admin privilege required'}, "403 Forbidden")
        data = parse_body(environ)
        for k, v in data.items():
            execute_db("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);", (k, str(v)))
        log_activity(user, 'SETTINGS_UPDATE', 'settings', '1', 'Updated admin system settings')
        return json_response(start_response, {'status': 'success', 'message': 'System settings saved.'})

    start_response("404 Not Found", [('Content-Type', 'application/json')])
    return [json.dumps({'status': 'error', 'message': f'Route {path} not found'}).encode('utf-8')]

def run():
    print(f"Hypetex Limited Server starting on http://127.0.0.1:{PORT}")
    httpd = make_server('0.0.0.0', PORT, application)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")

if __name__ == '__main__':
    run()
