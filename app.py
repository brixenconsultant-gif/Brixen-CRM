import os
import sys
import json
import time
import re
import gzip
import urllib.parse
import urllib.request
import urllib.error
import base64
import mimetypes
from wsgiref.simple_server import make_server, WSGIServer
from socketserver import ThreadingMixIn
from concurrent.futures import ThreadPoolExecutor
from db import query_db, execute_db, hash_password, verify_password, needs_rehash, unusable_password_hash, get_db, ensure_schema

class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """Handle static + API requests in parallel (local feel on the live portal)."""
    daemon_threads = True
    block_on_close = False


BULK_JOB_POOL = ThreadPoolExecutor(max_workers=4)

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
import secrets
import unicodedata
import email
import email.policy
import threading

# Storage directory for private client files (P2)
STORAGE_DIR = os.path.join(BASE_DIR, 'storage')
os.makedirs(STORAGE_DIR, exist_ok=True)

# P1: Persistent Database-Backed Session Management
# Absolute cookie lifetime (refresh keeps you signed in). No auto-logout.
SESSION_COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def create_db_session(user_id):
    token = str(uuid.uuid4())
    execute_db("""
        INSERT INTO user_sessions (session_token, user_id, expires_at, last_activity_at)
        VALUES (?, ?, datetime('now', '+30 days'), datetime('now'));
    """, (token, user_id))
    return token

def revoke_db_session(token):
    if not token: return
    execute_db("""
        UPDATE user_sessions SET revoked_at = datetime('now') WHERE session_token = ?;
    """, (token,))


def touch_db_session(token):
    if not token:
        return
    try:
        execute_db(
            """UPDATE user_sessions
               SET last_activity_at = datetime('now'),
                   expires_at = datetime('now', '+30 days')
             WHERE session_token = ? AND revoked_at IS NULL;""",
            (token,),
        )
    except Exception:
        pass


def session_token_from_environ(environ):
    cookie_str = environ.get('HTTP_COOKIE', '')
    if 'session_token=' in cookie_str:
        for c in cookie_str.split(';'):
            c = c.strip()
            if c.startswith('session_token='):
                return c.split('=', 1)[1]
    auth_hdr = environ.get('HTTP_AUTHORIZATION', '')
    if auth_hdr.startswith('Bearer '):
        return auth_hdr.split(' ', 1)[1]
    return None


def get_current_user(environ):
    token = session_token_from_environ(environ)
    if not token:
        return None

    session_rec = query_db("""
        SELECT s.session_token, u.*
        FROM user_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.session_token = ?
          AND s.revoked_at IS NULL
          AND s.expires_at > datetime('now');
    """, (token,), one=True)

    if not session_rec:
        return None

    # Keep session alive across refresh and normal API use.
    touch_db_session(token)
    return dict(session_rec)

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


# Keep in sync with email_engine.EMAIL_FONT_STACK (safe system fonts only).
EMAIL_FONT_STACK = (
    '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif'
)
CONGRATULATIONS_IMAGE_NAME = 'congratulations.png'
CONGRATULATIONS_IMAGE_CID = 'brixen-congratulations'
CONGRATULATIONS_HERO_WIDTH = 504
CONGRATULATIONS_HERO_HEIGHT = 280
WORDMARK_IMAGE_NAME = 'brixen-logo.png'
WORDMARK_IMAGE_CID = 'brixen-wordmark'
FAVICON_IMAGE_NAME = 'favicon.png'
FAVICON_IMAGE_CID = 'brixen-favicon'


def congratulations_image_path():
    return os.path.join(STATIC_DIR, 'img', CONGRATULATIONS_IMAGE_NAME)


def congratulations_image_src():
    return f"{portal_base_url()}/static/img/{CONGRATULATIONS_IMAGE_NAME}"


def congratulations_hero_html():
    src = html_escape(congratulations_image_src())
    width = CONGRATULATIONS_HERO_WIDTH
    height = CONGRATULATIONS_HERO_HEIGHT
    return (
        f'<table role="presentation" width="{width}" cellspacing="0" cellpadding="0" border="0" align="center" style="width:{width}px;max-width:100%;">'
        f'<tr><td background="{src}" bgcolor="#ffffff" width="{width}" height="{height}" valign="top" '
        f'style="background-color:#ffffff;background-image:url({src});background-repeat:no-repeat;'
        f'background-position:center top;background-size:contain;width:{width}px;height:{height}px;'
        f'font-size:1px;line-height:{height}px;color:#ffffff;">&nbsp;</td></tr></table>'
    )


def wordmark_image_path():
    return os.path.join(STATIC_DIR, 'img', WORDMARK_IMAGE_NAME)


def wordmark_image_src():
    return f"{portal_base_url()}/static/img/{WORDMARK_IMAGE_NAME}"


def favicon_image_path():
    return os.path.join(STATIC_DIR, 'img', FAVICON_IMAGE_NAME)


def hosted_favicon_url():
    return f"{portal_base_url()}/static/img/{FAVICON_IMAGE_NAME}"


def _image_mime_subtype(path, payload=b''):
    head = payload[:16] if payload else b''
    if not head and path and os.path.isfile(path):
        with open(path, 'rb') as fh:
            head = fh.read(16)
    if head.startswith(b'\x89PNG'):
        return 'png'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    guessed = mimetypes.guess_type(path or '')[0] or ''
    if guessed.startswith('image/'):
        return guessed.split('/', 1)[1]
    return 'png'


def inline_images_for_html(body_html):
    html = body_html or ''
    images = []
    if f'cid:{WORDMARK_IMAGE_CID}' in html:
        path = wordmark_image_path()
        if os.path.isfile(path):
            images.append({
                'cid': WORDMARK_IMAGE_CID,
                'path': path,
                'filename': WORDMARK_IMAGE_NAME,
            })
    if f'cid:{CONGRATULATIONS_IMAGE_CID}' in html:
        path = congratulations_image_path()
        if os.path.isfile(path):
            images.append({
                'cid': CONGRATULATIONS_IMAGE_CID,
                'path': path,
                'filename': CONGRATULATIONS_IMAGE_NAME,
            })
    if f'cid:{FAVICON_IMAGE_CID}' in html:
        path = favicon_image_path()
        if os.path.isfile(path):
            images.append({
                'cid': FAVICON_IMAGE_CID,
                'path': path,
                'filename': 'apple-touch-icon.png',
            })
    return images


def favicon_inline_image():
    path = favicon_image_path()
    if not os.path.isfile(path):
        return None
    return {
        'cid': FAVICON_IMAGE_CID,
        'path': path,
        'filename': 'apple-touch-icon.png',
    }


def parse_smtp_identity(from_addr, display_name='Brixen Consultants'):
    from email.utils import parseaddr
    name, addr = parseaddr(str(from_addr or '').strip())
    addr = (addr or str(from_addr or '').strip()).strip()
    name = (name or display_name or 'Brixen Consultants').strip()
    return name, addr


def build_outbound_email(recipient_email, subject, body_text, body_html=None, inline_images=None):
    from email.mime.image import MIMEImage
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr, formatdate, make_msgid

    brand = brand_settings()
    cfg = smtp_settings()
    _name, envelope_from = parse_smtp_identity(
        cfg.get('from_addr'),
        'Brixen Consultants',
    )
    display_name = 'Brixen Consultants'
    reply_to = str(brand.get('support_email') or envelope_from or '').strip()
    domain = 'brixenconsultants.com'
    if '@' in envelope_from:
        domain = envelope_from.rsplit('@', 1)[-1].lower() or domain

    images = list(inline_images or [])
    known = {str(item.get('cid') or '') for item in images}
    for item in inline_images_for_html(body_html):
        if item['cid'] not in known:
            images.append(item)
            known.add(item['cid'])
    favicon = favicon_inline_image()
    if favicon and f'cid:{FAVICON_IMAGE_CID}' in str(body_html or '') and favicon['cid'] not in known:
        images.append(favicon)
        known.add(favicon['cid'])
    attached = []
    for item in images:
        path = item.get('path')
        if not path or not os.path.isfile(path):
            continue
        with open(path, 'rb') as fh:
            payload = fh.read()
        if not payload:
            continue
        attached.append({
            'cid': item.get('cid') or 'image',
            'filename': item.get('filename') or os.path.basename(path),
            'subtype': item.get('subtype') or _image_mime_subtype(path, payload),
            'payload': payload,
        })

    if body_html and attached:
        msg = MIMEMultipart('related')
        alt = MIMEMultipart('alternative')
        alt.attach(MIMEText(body_text or '', 'plain', 'utf-8'))
        alt.attach(MIMEText(body_html, 'html', 'utf-8'))
        msg.attach(alt)
        for item in attached:
            img = MIMEImage(item['payload'], _subtype=item['subtype'])
            img.add_header('Content-ID', f"<{item['cid']}>")
            img.add_header('Content-Disposition', 'inline')
            if img.get_param('name'):
                img.del_param('name')
            msg.attach(img)
    elif body_html:
        msg = MIMEMultipart('alternative')
        msg.attach(MIMEText(body_text or '', 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    else:
        msg = MIMEText(body_text or '', 'plain', 'utf-8')

    msg['Subject'] = subject or ''
    msg['From'] = formataddr((display_name, envelope_from))
    msg['To'] = recipient_email
    if reply_to and '@' in reply_to:
        msg['Reply-To'] = formataddr((display_name, reply_to))
    msg['Date'] = formatdate(localtime=False)
    msg['Message-ID'] = make_msgid(domain=domain)
    msg['Organization'] = 'Brixen Consultants'
    msg['Content-Language'] = 'en-GB'
    for junk_header in (
        'Auto-Submitted',
        'X-Auto-Response-Suppress',
        'X-Mailer',
        'X-Priority',
        'X-MSMail-Priority',
        'Precedence',
        'Importance',
        'List-Unsubscribe',
        'List-Unsubscribe-Post',
    ):
        if junk_header in msg:
            del msg[junk_header]
    return msg, envelope_from


def _is_transient_smtp_error(err):
    import smtplib
    if isinstance(err, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError)):
        return True
    if isinstance(err, smtplib.SMTPResponseException) and 400 <= int(getattr(err, 'smtp_code', 0) or 0) < 500:
        return True
    return False


def _queue_outbound_email(recipient_email, subject, body_text, body_html):
    return execute_db(
        """
        INSERT INTO email_outbox (recipient, subject, body_text, body_html, status)
        VALUES (?, ?, ?, ?, 'queued');
        """,
        (recipient_email, subject or '', body_text or '', body_html or ''),
    )


def _mark_outbound_email(outbox_id, status, attempts=None, error=None, message_id=None):
    if not outbox_id:
        return
    execute_db(
        """
        UPDATE email_outbox
        SET status = ?,
            attempts = COALESCE(?, attempts),
            last_error = ?,
            message_id = COALESCE(?, message_id),
            sent_at = CASE WHEN ? = 'sent' THEN CURRENT_TIMESTAMP ELSE sent_at END
        WHERE id = ?;
        """,
        (status, attempts, error, message_id, status, outbox_id),
    )


def _smtp_deliver(recipient_email, subject, body_text, body_html, inline_images=None):
    import smtplib
    cfg = smtp_settings()
    smtp_host = cfg['host']
    smtp_port = cfg['port']
    smtp_user = cfg['user']
    smtp_pass = cfg['password']
    if not smtp_host or not smtp_user or not smtp_pass:
        raise RuntimeError('SMTP not configured')
    msg, envelope_from = build_outbound_email(
        recipient_email,
        subject,
        body_text,
        body_html,
        inline_images=inline_images,
    )
    port = int(smtp_port)
    timeout = 20
    if port == 465:
        with smtplib.SMTP_SSL(smtp_host, port, timeout=timeout) as server:
            server.login(smtp_user, smtp_pass)
            refused = server.sendmail(envelope_from, [recipient_email], msg.as_bytes())
    else:
        with smtplib.SMTP(smtp_host, port, timeout=timeout) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            refused = server.sendmail(envelope_from, [recipient_email], msg.as_bytes())
    if refused:
        raise RuntimeError(f'SMTP refused recipient: {refused}')
    return str(msg['Message-ID'] or '')


class EmailService:
    @staticmethod
    def send_notification_email(recipient_email, subject, body_text, body_html=None, inline_images=None):
        recipient_email = str(recipient_email or '').strip()
        if not recipient_email or '@' not in recipient_email:
            print(f"[EmailService Error] Missing recipient for '{subject}'")
            return False, "Missing recipient"
        outbox_id = None
        try:
            outbox_id = _queue_outbound_email(recipient_email, subject, body_text, body_html)
        except Exception as err:
            print(f"[EmailService Error] Could not queue email to {recipient_email}: {err}")
        if not smtp_configured():
            print(f"[EmailService Log] Email to {recipient_email} ('{subject}') - SMTP not configured.")
            return False, "SMTP not configured"
        import time
        last_error = None
        attempts = 0
        for attempts in range(1, 4):
            try:
                message_id = _smtp_deliver(
                    recipient_email,
                    subject,
                    body_text,
                    body_html,
                    inline_images=inline_images,
                )
                _mark_outbound_email(outbox_id, 'sent', attempts=attempts, message_id=message_id)
                print(f"[EmailService Success] Dispatched email to {recipient_email} id={outbox_id}")
                return True, "Delivered"
            except Exception as err:
                last_error = str(err)
                print(f"[EmailService Error] Attempt {attempts} to {recipient_email}: {err}")
                _mark_outbound_email(outbox_id, 'queued' if _is_transient_smtp_error(err) else 'failed', attempts=attempts, error=last_error)
                if attempts >= 3 or not _is_transient_smtp_error(err):
                    break
                time.sleep(1)
        _mark_outbound_email(outbox_id, 'failed', attempts=attempts, error=last_error)
        return False, last_error or 'Send failed'

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
        return f'session_token=; {flags}; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT'
    # Persist across browser restarts/refreshes.
    return f'session_token={token}; {flags}; Max-Age={SESSION_COOKIE_MAX_AGE}'


def origin_cookie_header(token=None, clear=False, environ=None):
    flags = _cookie_flag_suffix(environ)
    if clear:
        return f'origin_session=; {flags}; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT'
    return f'origin_session={token}; {flags}; Max-Age={SESSION_COOKIE_MAX_AGE}'

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
    session_rec = query_db("""
        SELECT s.session_token, u.*
        FROM user_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.session_token = ?
          AND s.revoked_at IS NULL
          AND s.expires_at > datetime('now');
    """, (token,), one=True)
    return dict(session_rec) if session_rec else None

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
        'b2b_id': src.get('b2b_id'),
        'client_type': src.get('client_type') or 'B2B',
        'theme_preference': src.get('theme_preference') or 'system',
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
    # Browsers often send empty type for .js from some paths; keep MIME explicit.
    lower = filepath.lower()
    if lower.endswith('.js'):
        ctype = 'text/javascript; charset=utf-8'
    elif lower.endswith('.css'):
        ctype = 'text/css; charset=utf-8'
    elif lower.endswith('.html'):
        ctype = 'text/html; charset=utf-8'

    with open(filepath, 'rb') as f:
        content = f.read()

    headers = [
        ('Content-Type', ctype),
        ('Cache-Control', 'no-cache, no-store, must-revalidate'),
        ('Pragma', 'no-cache'),
        ('Expires', '0')
    ]
    accept_enc = (environ.get('HTTP_ACCEPT_ENCODING') or '').lower()
    compressible = lower.endswith(('.js', '.css', '.html', '.svg', '.json', '.txt', '.map'))
    if compressible and 'gzip' in accept_enc and len(content) > 512:
        content = gzip.compress(content, compresslevel=5)
        headers.append(('Content-Encoding', 'gzip'))
        headers.append(('Vary', 'Accept-Encoding'))

    headers.append(('Content-Length', str(len(content))))

    qs = environ.get('QUERY_STRING') or ''
    versioned = 'v=' in qs
    if lower.endswith('.html'):
        headers.append(('Cache-Control', 'no-store, no-cache, must-revalidate'))
        headers.append(('Pragma', 'no-cache'))
    elif versioned and lower.endswith(('.js', '.css', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.woff2')):
        # Cache-busted assets (?v=…) can be cached hard — new deploys bump the version.
        headers.append(('Cache-Control', 'public, max-age=31536000, immutable'))
    elif lower.endswith(('.js', '.css', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.woff2')):
        headers.append(('Cache-Control', 'public, max-age=300'))
    else:
        headers.append(('Cache-Control', 'private, max-age=60'))

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


def parse_multipart_form(environ):
    """
    Parse multipart/form-data into (fields_dict, files_dict).
    files_dict values are {'filename': str, 'content_type': str, 'bytes': bytes}.
    """
    content_type = environ.get('CONTENT_TYPE') or ''
    if 'multipart/form-data' not in content_type.lower():
        return {}, {}
    try:
        content_length = int(environ.get('CONTENT_LENGTH', 0) or 0)
    except (TypeError, ValueError):
        content_length = 0
    if content_length <= 0:
        return {}, {}
    body = environ['wsgi.input'].read(content_length)
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode('utf-8', errors='ignore')
    try:
        msg = email.message_from_bytes(header + body, policy=email.policy.default)
    except Exception:
        return {}, {}
    fields = {}
    files = {}
    if not msg.is_multipart():
        return {}, {}
    for part in msg.iter_parts():
        name = part.get_param('name', header='content-disposition')
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = b''
        if isinstance(payload, str):
            payload = payload.encode('utf-8', errors='ignore')
        if filename:
            files[name] = {
                'filename': filename,
                'content_type': part.get_content_type() or 'application/octet-stream',
                'bytes': payload,
            }
        else:
            try:
                fields[name] = payload.decode('utf-8', errors='replace')
            except Exception:
                fields[name] = ''
    return fields, files


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
    'accountancy.manage': ('Accountancy', 'Accounts', 'Compliance'),
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
    return bool(user and user.get('role') in ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF'))

def can_view_revenue(user):
    return bool(user and user.get('role') in ('SUPER_ADMIN', 'ADMIN'))


def strip_order_finance(row):
    if not row:
        return row
    out = dict(row)
    for key in (
        'price', 'vat', 'total', 'payment_mode', 'payment_status',
        'unit_price', 'line_total', 'amount', 'tax', 'payment_method',
        'deposit_amount', 'amount_paid', 'amount_due',
    ):
        out.pop(key, None)
    return out

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
    'Accountancy',
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
    'id', 'wordpress_user_id', 'email', 'notification_email', 'full_name', 'phone', 'country',
    'role', 'department', 'status', 'avatar_url', 'created_at', 'is_b2b', 'account_type'
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


def ensure_internal_staff_have_all_departments():
    payload = store_departments(list(STAFF_DEPARTMENTS))
    rows = query_db(
        "SELECT id, department FROM users WHERE role IN ('STAFF', 'MANAGER', 'ADMIN', 'SUPER_ADMIN');"
    ) or []
    for row in rows:
        current = set(departments_from_user(row))
        if current == set(STAFF_DEPARTMENTS):
            continue
        execute_db("UPDATE users SET department = ? WHERE id = ?;", (payload, row['id']))


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


def calculate_staff_incentive_grade(completed_count, total_assigned, on_time_count, overdue_count):
    completed_count = int(completed_count or 0)
    total_assigned = int(total_assigned or 0)
    on_time_count = int(on_time_count or 0)
    overdue_count = int(overdue_count or 0)
    
    completion_rate = (completed_count / total_assigned * 100.0) if total_assigned > 0 else (100.0 if completed_count > 0 else 0.0)
    sla_rate = (on_time_count / completed_count * 100.0) if completed_count > 0 else 100.0
    
    if completion_rate >= 90.0 and sla_rate >= 85.0 and completed_count >= 1:
        grade = 'A+'
        status_label = 'Top Performer (Grade A+)'
        bonus_badge = '100% Incentive Bonus Eligible'
    elif completion_rate >= 80.0 and sla_rate >= 75.0:
        grade = 'A'
        status_label = 'High Performer (Grade A)'
        bonus_badge = '85% Incentive Bonus Eligible'
    elif completion_rate >= 65.0:
        grade = 'B'
        status_label = 'Good Standing (Grade B)'
        bonus_badge = 'Standard Incentive Bonus'
    elif completion_rate >= 40.0:
        grade = 'C'
        status_label = 'Needs Improvement (Grade C)'
        bonus_badge = 'Partial Incentive Tier'
    else:
        grade = 'D'
        status_label = 'Underperforming (Grade D)'
        bonus_badge = 'Incentive Locked'
        
    return {
        'grade': grade,
        'status_label': status_label,
        'bonus_badge': bonus_badge,
        'completion_rate': round(completion_rate, 1),
        'sla_rate': round(sla_rate, 1)
    }

def public_staff_user(row):
    if not row:
        return None
    src = dict(row)
    public = {key: src.get(key) for key in STAFF_PUBLIC_FIELDS}
    depts = departments_from_user(src)
    public['departments'] = depts
    public['department'] = depts[0] if depts else None
    if public.get('role') == 'CLIENT':
        if public.get('is_b2b') is None:
            public['is_b2b'] = 1
        if not public.get('account_type'):
            public['account_type'] = 'B2B Client (Brixen Website Panel)'
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
INVOICE_STATUSES = ('Paid', 'Pending', 'Partial Paid', 'Overdue', 'Cancelled')
INVOICE_PAYMENT_TIMINGS = ('Advance', 'Deposit', 'After work')
INVOICE_BANK_GBP = {
    'currency': 'GBP',
    'account_name': 'Brixen Consultants LTD',
    'account_number': '32546658',
    'sort_code': '04-06-05',
    'pay_url': 'https://pay.tide.co/pay/f2dfe046-691c-49a5-8b58-340c6c23453c',
}
INVOICE_BANK_PKR = {
    'currency': 'PKR',
    'account_name': 'BRIXEN CONSULTANTS',
    'bank_name': 'UBL',
    'iban': 'PK42UNIL0109000343170125',
}


def can_delete_orders(user):
    return bool(user and user.get('role') in ('SUPER_ADMIN', 'ADMIN'))


def can_edit_order_price(user):
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


def normalize_notify_email(value):
    email = str(value or '').strip().lower()
    if not email or not STAFF_EMAIL_RE.match(email):
        return ''
    return email


def parse_team_notification_emails(raw=None):
    if raw is None:
        row = query_db("SELECT value FROM settings WHERE key = 'team_notification_emails';", one=True)
        raw = (row or {}).get('value') if row else ''
    text = str(raw or '').replace(';', ',').replace('\n', ',')
    out = []
    seen = set()
    for part in text.split(','):
        email = normalize_notify_email(part)
        if not email or email in seen:
            continue
        seen.add(email)
        out.append(email)
    return out


def team_notification_recipients():
    return parse_team_notification_emails()


def email_team_about_clients(subject, body_text, body_html=None):
    """Send the same alert to every configured team notification mailbox."""
    recipients = team_notification_recipients()
    if not recipients:
        return {'sent': 0, 'recipients': []}
    sent_count = 0
    for email in recipients:
        ok, _status = EmailService.send_notification_email(email, subject, body_text, body_html)
        if ok:
            sent_count += 1
    return {'sent': sent_count, 'recipients': recipients}


def client_notification_recipient(client, email_to=None):
    """
    Resolve outbound mail for a client.
    When email_to is set (company/order work), that address wins so each company
    can receive its own mail even if the portal login email is different.
    """
    client_rec = resolve_client_user(client)
    explicit = normalize_notify_email(email_to) if email_to is not None else ''
    if explicit:
        return explicit
    if email_to is not None and str(email_to).strip() == '':
        return ''
    preferred = normalize_notify_email((client_rec or {}).get('notification_email'))
    if preferred:
        return preferred
    return str((client_rec or {}).get('email') or '').strip()


def load_company_for_notify(company=None, order=None):
    if isinstance(company, dict) and company.get('id'):
        if company.get('registered_email') is not None or company.get('name'):
            return company
        company_id = company.get('id')
    elif company:
        company_id = company
    else:
        company_id = (order or {}).get('company_id')
    if not company_id:
        return None
    row = query_db(
        "SELECT id, user_id, name, registered_email, director FROM companies WHERE id = ?;",
        (company_id,),
        one=True,
    )
    return dict(row) if row else None


def resolve_work_notification_email(*, client=None, company=None, order=None, email_to=None):
    """
    Company/order work emails go to the company-relevant address first:
      1. Explicit email_to
      2. companies.registered_email
      3. company form / owner email
      4. order owner_form_email
      5. client notification_email / login email
    Portal login stays on the customer; mail follows the company.
    """
    explicit = normalize_notify_email(email_to) if email_to is not None else ''
    if explicit:
        return explicit

    company_rec = load_company_for_notify(company, order)
    if company_rec:
        registered = normalize_notify_email(company_rec.get('registered_email'))
        if not registered:
            registered = normalize_notify_email(resolve_company_registered_email(company_rec))
        if registered:
            return registered
        form_email = normalize_notify_email(company_form_email(company_rec.get('id')))
        if form_email:
            return form_email

    if order:
        try:
            connector = real_order_connector(order)
            form_email = normalize_notify_email((connector or {}).get('owner_form_email'))
            if form_email:
                return form_email
        except Exception:
            form_email = normalize_notify_email((order or {}).get('owner_form_email'))
            if form_email:
                return form_email

    return client_notification_recipient(client or order or company_rec)


def order_prefix_value():
    row = query_db("SELECT value FROM settings WHERE key = 'order_prefix';", one=True)
    prefix = str((row or {}).get('value') or '#GB').strip()
    return prefix or '#GB'


def next_manual_order_number():
    prefix = order_prefix_value()
    highest = 0
    for row in query_db("SELECT order_number FROM orders;") or []:
        number = str(row.get('order_number') or '').strip()
        match = re.search(r'(\d+)$', number)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1}"


def create_manual_crm_order(data, actor=None):
    """Create an order in the CRM without a website signup. Emails use company/owner addresses."""
    extras = dict(data) if isinstance(data, dict) else {}
    if actor and actor.get('role') == 'CLIENT':
        extras['client_mode'] = 'existing'
        extras['client_id'] = actor['id']
    client_id, client, client_err = ensure_client_for_manual_company(extras, actor)
    if client_err:
        return None, client_err

    line_items = extras.get('line_items')
    if line_items and isinstance(line_items, list) and len(line_items) > 0:
        total_price = 0
        valid_items = []
        for item in line_items:
            pn = str(item.get('product_name') or '').strip()
            if not pn:
                continue
            try:
                p = float(item.get('price') or 0)
            except (ValueError, TypeError):
                p = 0
            if p < 0:
                return None, 'Price cannot be negative'
            valid_items.append({'product_name': pn, 'price': p})
            total_price += p
        if not valid_items:
            return None, 'Enter at least one product name'
        
        extras = dict(extras)
        extras['price'] = round(total_price, 2)
        if len(valid_items) == 1:
            service_name = valid_items[0]['product_name']
        else:
            service_name = "Multiple Products"
        service_id = None
    else:
        service_name = (extras.get('service_name') or extras.get('product') or '').strip()
        service_id = optional_record_id(extras.get('service_id'))
        if service_id:
            service = query_db("SELECT id, name, price FROM services WHERE id = ?;", (service_id,), one=True)
            if not service:
                return None, 'Service not found'
            if not service_name:
                service_name = str(service.get('name') or '').strip()
            if extras.get('price') in (None, '') and service.get('price') is not None:
                extras = dict(extras)
                extras['price'] = service.get('price')
        if not service_name:
            return None, 'Enter the service / product name'

    company_id = optional_record_id(extras.get('company_id'))
    company = None
    if company_id:
        company = query_db("SELECT * FROM companies WHERE id = ?;", (company_id,), one=True)
        if not company or int(company.get('user_id') or 0) != int(client_id):
            return None, 'Company does not belong to this client'
    else:
        company_name = (extras.get('company_name') or extras.get('name') or '').strip()
        if company_name:
            company_id, company_err = create_manual_company({
                **extras,
                'client_mode': 'existing',
                'client_id': client_id,
                'name': company_name,
                'registered_email': extras.get('company_email') or extras.get('registered_email') or extras.get('owner_form_email'),
            }, actor)
            if company_err:
                return None, company_err
            company = query_db("SELECT * FROM companies WHERE id = ?;", (company_id,), one=True)

    try:
        price = float(extras.get('price') if extras.get('price') not in (None, '') else 0)
    except (TypeError, ValueError):
        return None, 'Enter a valid price'
    if price < 0:
        return None, 'Price cannot be negative'
    vat_rate = 0.0
    try:
        if extras.get('vat') not in (None, ''):
            vat = float(extras.get('vat'))
        else:
            vat = round(price * vat_rate, 2)
    except (TypeError, ValueError):
        return None, 'Enter a valid VAT amount'
    total = round(price + vat, 2)
    if extras.get('total') not in (None, ''):
        try:
            total = float(extras.get('total'))
        except (TypeError, ValueError):
            return None, 'Enter a valid total'

    owner_name = (extras.get('owner_name') or extras.get('director') or (client or {}).get('full_name') or '').strip()
    owner_email = normalize_notify_email(
        extras.get('company_email')
        or extras.get('owner_form_email')
        or extras.get('registered_email')
        or (company or {}).get('registered_email')
        or (client or {}).get('email')
    )
    if not owner_email:
        return None, 'Enter the company notification email for this order'

    status = (extras.get('status') or 'Processing').strip() or 'Processing'
    allowed_status = {
        'Pending', 'Pending Verification', 'Processing', 'In Progress', 'Completed', 'Cancelled'
    }
    if status not in allowed_status:
        status = 'Processing'
    payment_mode = (extras.get('payment_mode') or 'Manual CRM').strip() or 'Manual CRM'
    notes = (extras.get('notes') or '').strip() or 'Created manually in CRM (no website signup).'
    order_number = (extras.get('order_number') or '').strip() or next_manual_order_number()
    if query_db("SELECT id FROM orders WHERE order_number = ?;", (order_number,), one=True):
        order_number = next_manual_order_number()

    order_id = execute_db(
        """
        INSERT INTO orders (
            order_number, user_id, company_id, service_id, service_name,
            price, vat, total, status, progress_percent, notes, payment_mode,
            owner_name, owner_form_email, assigned_staff_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            order_number,
            client_id,
            company_id,
            service_id,
            service_name,
            price,
            vat,
            total,
            status,
            20 if status not in ('Completed', 'Cancelled') else (100 if status == 'Completed' else 0),
            notes,
            payment_mode,
            owner_name or None,
            owner_email,
            (actor or {}).get('id'),
        ),
    )

    if company_id and owner_email:
        persist_company_registered_email(
            {'id': company_id, 'user_id': client_id, 'name': (company or {}).get('name'), 'registered_email': owner_email},
            owner_email,
        )
        execute_db(
            """
            INSERT INTO company_owners (
                order_id, company_id, full_name, form_email, source
            ) VALUES (?, ?, ?, ?, 'manual_crm');
            """,
            (order_id, company_id, owner_name or (client or {}).get('full_name') or 'Owner', owner_email),
        )
    else:
        execute_db(
            """
            INSERT INTO company_owners (
                order_id, company_id, full_name, form_email, source
            ) VALUES (?, ?, ?, ?, 'manual_crm');
            """,
            (order_id, company_id, owner_name or (client or {}).get('full_name') or 'Owner', owner_email),
        )
    
    line_items = extras.get('line_items')
    if line_items and isinstance(line_items, list) and len(line_items) > 0:
        for idx, item in enumerate(line_items):
            pn = str(item.get('product_name') or '').strip()
            if not pn: continue
            try: p = float(item.get('price') or 0)
            except (ValueError, TypeError): p = 0
            execute_db(
                """
                INSERT INTO order_line_items (
                    order_id, product_name, category_name, quantity, unit_price, line_total, sort_order
                ) VALUES (?, ?, ?, 1, ?, ?, ?);
                """,
                (order_id, pn, 'Manual CRM Order', p, p, idx)
            )
    else:
        execute_db(
            """
            INSERT INTO order_line_items (
                order_id, product_name, category_name, quantity, unit_price, line_total, sort_order
            ) VALUES (?, ?, ?, 1, ?, ?, 0);
            """,
            (order_id, service_name, 'Manual CRM Order', price, price)
        )

    ensure_order_timeline(order_id)
    ensure_invoice_for_order(order_id)
    if actor:
        log_activity(actor, 'ORDER_CREATED_MANUAL', 'orders', str(order_id), f"Manual CRM order {order_number} for {owner_email}")
    notify_staff_new_order(order_number, (client or {}).get('full_name') or owner_name or owner_email)

    created = query_db(
        """
        SELECT o.*, u.email, u.full_name, c.name as company_name
        FROM orders o
        JOIN users u ON o.user_id = u.id
        LEFT JOIN companies c ON o.company_id = c.id
        WHERE o.id = ?;
        """,
        (order_id,),
        one=True,
    )
    if status == 'Completed' and created:
        notify_product_completed(created)
    return dict(created) if created else {'id': order_id, 'order_number': order_number}, None


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
        'legal_name': 'Brixen Consultants Ltd',
        'company_number': '17314564',
        'registered_office': '57 Wellesley Road, Ilford, United Kingdom, IG1 4JZ',
        'website': 'https://brixenconsultants.com',
        'ico_number': 'ZB941411',
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


PORTAL_FAVICON_URL = 'https://brixenconsultants.com/wp-content/uploads/2025/11/Brixen-Consultants.png'


def brand_favicon_url():
    return PORTAL_FAVICON_URL


def email_favicon_html():
    url = html_escape(PORTAL_FAVICON_URL)
    return (
        f'  <link rel="icon" type="image/png" href="{url}">\n'
        f'  <link rel="apple-touch-icon" href="{url}">'
    )


def email_document_head_html(title, extra_css=''):
    css = str(extra_css or '').strip()
    base_css = (
        'html,body{margin:0!important;padding:0!important;width:100%!important;}'
        'img{max-width:100%!important;height:auto!important;}'
        'table{border-collapse:collapse;}'
        '.email-shell{width:100%!important;max-width:600px!important;}'
        '.email-pad{padding-left:24px!important;padding-right:24px!important;}'
        '@media only screen and (max-width:620px){'
        '.email-shell{width:100%!important;}'
        '.email-pad{padding-left:20px!important;padding-right:20px!important;}'
        '}'
    )
    style = f'  <style type="text/css">{base_css}{css}</style>\n'
    return (
        '<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '  <meta name="color-scheme" content="light">\n'
        '  <meta name="supported-color-schemes" content="light">\n'
        f'{email_favicon_html()}\n'
        f'{style}'
        f'  <title>{html_escape(title)}</title>\n'
        '</head>'
    )


def html_escape(value):
    text = '' if value is None else str(value)
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&#39;')
    )


def email_legal_lines(brand=None):
    import email_engine as _email_engine

    brand = brand or brand_settings()
    legal_name = _email_engine.normalize_legal_name(brand.get('legal_name') or 'Brixen Consultants Ltd')
    office = brand.get('registered_office') or '57 Wellesley Road, Ilford, United Kingdom, IG1 4JZ'
    number = brand.get('company_number') or '17314564'
    website = brand.get('website') or 'https://brixenconsultants.com'
    ico = brand.get('ico_number') or 'ZB941411'
    return {
        'legal_name': legal_name,
        'registered_office': office,
        'company_number': number,
        'website': website,
        'ico_number': ico,
    }


def registration_next_steps_text():
    return (
        "🏦 Bank account — open Tide or Wise\n"
        "🏠 Registered office and mail from £20\n"
        "📄 Confirmation statement and yearly accounts\n"
        "📊 VAT and bookkeeping\n"
        "🎁 Free consultation — WhatsApp us to book it"
    )


def registration_whatsapp_url(wa_href=None):
    base = str(wa_href or 'https://wa.me/447360515317').strip() or 'https://wa.me/447360515317'
    if 'text=' in base:
        return base
    text = urllib.parse.quote('Hi Brixen, I would like a free consultation for my new company.')
    sep = '&' if '?' in base else '?'
    return f"{base}{sep}text={text}"


def apple_email_button_html(url, label, background='#003971'):
    bg = html_escape(background or '#003971')
    href = html_escape(url)
    text = html_escape(label)
    return (
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin:0 auto;border-collapse:separate;">'
        f'<tr><td align="center" bgcolor="{bg}" style="background-color:{bg};border-radius:980px;">'
        f'<a href="{href}" target="_blank" style="display:inline-block;padding:11px 22px;border-radius:980px;'
        f'background-color:{bg};color:#ffffff;text-decoration:none;font-size:14px;line-height:1.25;'
        f'font-weight:600;letter-spacing:-0.01em;font-family:{EMAIL_FONT_STACK};">'
        f'<font color="#ffffff">{text}</font></a></td></tr></table>'
    )


def email_tone_from_context(*, layout='', badge='', headline='', alert_label='', message='', extra=''):
    blob = f"{layout} {badge} {headline} {alert_label} {message} {extra}".lower()
    if any(token in blob for token in (
        'attention', 'overdue', 'action needed', 'warning', 'strike-off', 'strike off',
        'urgent', 'default address', 'fix that', 'alert', 'proposal to strike',
    )):
        return 'alert'
    if 'invoice' in blob or 'payment details' in blob or 'payment requested' in blob or str(layout or '').lower() == 'invoice':
        if any(token in blob for token in ('paid', 'payment received', 'deposit received')):
            return 'success'
        return 'payment'
    if any(token in blob for token in (
        'complete', 'completed', 'is done', 'verified', 'congratulations', 'registered',
        'uploaded', 'filed', 'success', 'great news', 'ready',
    )):
        return 'success'
    return 'info'


EMAIL_TONE_META = {
    'alert': {
        'label': 'Action required',
        'accent': '#b45309',
        'hero_copy': 'Please review the details below.',
    },
    'payment': {
        'label': 'Payment',
        'accent': '#003971',
        'hero_copy': 'Your invoice is ready to download.',
    },
    'success': {
        'label': 'Update complete',
        'accent': '#047857',
        'hero_copy': 'An update from Brixen Consultants.',
    },
    'info': {
        'label': 'Account update',
        'accent': '#003971',
        'hero_copy': 'An update on your account.',
    },
}


def email_tone_meta(tone):
    return EMAIL_TONE_META.get(str(tone or 'info').lower()) or EMAIL_TONE_META['info']


def email_hero_header_html(headline, *, tone='info', badge='', navy='#003971', gold='#c5a572', title_size='18px', wordmark_url=None, compact=True):
    meta = email_tone_meta(tone)
    navy = html_escape(navy or '#003971')
    gold = html_escape(gold or '#c5a572')
    title = html_escape(str(headline or meta['label']).strip() or meta['label'])
    chip = html_escape(str(badge or meta['label']).strip() or meta['label'])
    size = html_escape(str(title_size or '18px'))
    mark = html_escape(str(wordmark_url or wordmark_image_src()).strip())
    pad = '18px 24px 16px' if compact else '28px 32px 24px'
    return f"""
          <tr>
            <td align="center" bgcolor="{navy}" style="padding:{pad};background-color:{navy};">
              <p style="margin:0;font-family:{EMAIL_FONT_STACK};font-size:12px;line-height:1.3;letter-spacing:0.06em;text-transform:uppercase;font-weight:700;color:{gold};">
                <font color="{gold}">Brixen Consultants</font>
              </p>
              <p style="margin:10px 0 0;font-family:{EMAIL_FONT_STACK};font-size:11px;line-height:1.3;letter-spacing:0.04em;text-transform:uppercase;color:#ffffff;">
                <font color="#ffffff">{chip}</font>
              </p>
              <p style="margin:8px 0 0;font-family:{EMAIL_FONT_STACK};font-size:{size};line-height:1.3;letter-spacing:-0.02em;font-weight:600;color:#ffffff;">
                <font color="#ffffff">{title}</font>
              </p>
            </td>
          </tr>
          <tr>
            <td align="center" bgcolor="#ffffff" style="padding:16px 24px 4px;background:#ffffff;">
              <img src="{mark}" alt="Brixen Consultants" width="110" style="display:block;margin:0 auto;border:0;outline:none;text-decoration:none;width:110px;height:auto;">
            </td>
          </tr>"""


def key_points_html(points, navy, *, tone='info'):
    navy = html_escape(navy or '#003971')
    meta = email_tone_meta(tone)
    accent = html_escape(meta['accent'] if tone == 'alert' else navy)
    rows = []
    for index, point in enumerate(points or []):
        text = str(point or '').strip()
        if not text:
            continue
        top = '10px' if index else '0'
        weight = '600' if (index == 0 or (text.startswith(('⚠️', '✅', '📌')) and len(text) < 80)) else '400'
        color = '#1d1d1f' if weight == '600' else '#6e6e73'
        rows.append(
            f'<p style="margin:{top} 0 0;font-family:{EMAIL_FONT_STACK};font-size:15px;line-height:1.5;'
            f'letter-spacing:-0.016em;font-weight:{weight};color:{color};text-align:left;">{html_escape(text)}</p>'
        )
    return ''.join(rows)


def email_facts_text(sections):
    lines = []
    for section in sections or []:
        title = str((section or {}).get('title') or '').strip()
        if title:
            if lines:
                lines.append('')
            lines.append(title)
        for label, value in (section or {}).get('rows') or []:
            label = str(label or '').strip()
            value = str(value or '').strip()
            if not label and not value:
                continue
            lines.append(f'{label}: {value}' if label else value)
    return '\n'.join(lines)


def email_facts_html(sections, navy='#003971', gold='#c5a572'):
    navy = html_escape(navy or '#003971')
    gold = html_escape(gold or '#c5a572')
    parts = []
    for index, section in enumerate(sections or []):
        title = str((section or {}).get('title') or '').strip()
        rows = (section or {}).get('rows') or []
        usable = []
        for label, value in rows:
            label = str(label or '').strip()
            value = str(value or '').strip()
            if label or value:
                usable.append((label, value))
        if not title and not usable:
            continue
        top = '6px' if index == 0 else '22px'
        block = []
        if title:
            block.append(
                f'<p style="margin:{top} 0 8px;font-family:{EMAIL_FONT_STACK};font-size:12px;line-height:1.3;'
                f'letter-spacing:0.12em;text-transform:uppercase;color:{gold};"><font color="{gold}">{html_escape(title)}</font></p>'
            )
            top = '0'
        block.append(
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
            f'style="margin:{top} 0 0;border-collapse:collapse;">'
        )
        for row_index, (label, value) in enumerate(usable):
            rule = 'border-top:1px solid #eee8dc;' if row_index else 'border-top:0;'
            block.append(
                '<tr><td style="padding:11px 0;' + rule + 'vertical-align:top;">'
                f'<p style="margin:0 0 4px;font-family:{EMAIL_FONT_STACK};font-size:11px;line-height:1.3;'
                f'letter-spacing:0.08em;text-transform:uppercase;color:{gold};"><font color="{gold}">{html_escape(label)}</font></p>'
                f'<p style="margin:0;font-family:{EMAIL_FONT_STACK};font-size:16px;line-height:1.45;'
                f'letter-spacing:-0.022em;font-weight:600;color:{navy};word-break:break-word;">'
                f'<font color="{navy}">{html_escape(value)}</font></p>'
                '</td></tr>'
            )
        block.append('</table>')
        parts.append(''.join(block))
    return ''.join(parts)


BRIXEN_EMAIL_BRAND = {
    'navy': '#003971',
    'gold': '#c5a572',
    'ivory': '#f3efe8',
    'white': '#ffffff',
    'ink': '#1d1d1f',
    'muted': '#6e6e73',
    'line': '#d2d2d7',
}

_INTERNAL_INVOICE_GREETING_NAMES = frozenset({
    'brixen',
    'brixen consultant',
    'brixen consultants',
    'brixen consultants ltd',
    'brixen consultants limited',
    'the brixen consultants team',
})


def is_internal_invoice_greeting(name):
    text = re.sub(r'\s+', ' ', str(name or '').strip()).lower()
    if not text:
        return True
    if text in _INTERNAL_INVOICE_GREETING_NAMES:
        return True
    return text.startswith('brixen consultant')


def invoice_email_greeting_name(order=None, invoice=None, client=None):
    company_name = (order or {}).get('company_name') or (invoice or {}).get('company_name')
    connector = real_order_connector(order) if order else {}
    candidates = (
        connector.get('owner_name'),
        (invoice or {}).get('owner_name'),
        (order or {}).get('owner_name'),
        (client or {}).get('full_name'),
        (order or {}).get('full_name'),
        (order or {}).get('client_name'),
        (invoice or {}).get('client_name'),
    )
    for name in candidates:
        text = str(name or '').strip()
        if not text:
            continue
        if is_internal_invoice_greeting(text):
            continue
        if looks_like_placeholder_director(text, company_name):
            continue
        return text
    return 'there'


def invoice_payment_request_copy(*, include_gbp=True, include_pkr=True):
    if include_gbp and include_pkr:
        settle = (
            'You may settle this invoice using either the GBP or PKR payment details provided below.'
        )
    else:
        settle = 'Please use the payment details below to settle your invoice.'
    return (
        'Thank you for your order with Brixen Consultants.\n\n'
        'Your invoice details are below.\n\n'
        f'{settle}'
    )


def invoice_request_email_subject(invoice=None, order=None):
    company = brand_settings()['company_name']
    invoice_number = str((invoice or {}).get('invoice_number') or '').strip()
    order_number = str((order or {}).get('order_number') or '').strip()
    if invoice_number:
        return f"Invoice {invoice_number} — {company}"
    if order_number:
        return f"Invoice {order_number} — {company}"
    return f"Invoice — {company}"


def invoice_receipt_payload(order, invoice=None, *, include_bank=False):
    invoice = invoice or {}
    order = order or {}
    money = invoice_money_state(invoice)
    items = invoice_line_items_for_document(invoice)
    if not items:
        items = [{
            'description': str(order.get('service_name') or invoice.get('service_name') or 'Professional services').strip() or 'Professional services',
            'quantity': 1,
            'amount': invoice.get('amount') or money['total'],
        }]
    try:
        subtotal = float(invoice.get('amount') or 0)
    except (TypeError, ValueError):
        subtotal = 0.0
    try:
        tax = float(invoice.get('tax') or 0)
    except (TypeError, ValueError):
        tax = 0.0
    if subtotal <= 0.004 and money['total']:
        subtotal = max(0.0, round(money['total'] - tax, 2))
    connector = real_order_connector(order) if order else {}
    fx = None
    if include_bank and invoice_client_is_in_pakistan(invoice):
        fx = invoice_pakistan_fx_note(money['amount_due'] if money['amount_due'] > 0.004 else money['total'])
    created = invoice.get('created_at') or invoice.get('due_date') or order.get('created_at')
    fully_paid = bool(money.get('fully_paid'))
    show_due = float(money.get('amount_due') or 0) > 0.004
    show_paid = float(money.get('amount_paid') or 0) > 0.004
    if fully_paid:
        title = 'Payment received'
    elif show_paid:
        title = 'Deposit received' if money.get('timing') == 'Deposit' else 'Invoice'
    else:
        title = 'Invoice'
    customer_name = invoice_email_greeting_name(order, invoice)
    customer_email = str(
        connector.get('owner_form_email') or invoice.get('owner_form_email') or order.get('email') or ''
    ).strip()
    return {
        'kicker': 'Thank you for your order',
        'title': title,
        'date': format_invoice_email_datetime(created),
        'invoice_number': str(invoice.get('invoice_number') or '').strip(),
        'order_number': str(order.get('order_number') or '').strip(),
        'customer_name': customer_name,
        'customer_email': customer_email,
        'status_label': 'Paid' if fully_paid else ('Amount due' if show_due else str(money.get('display_status') or '')),
        'items': items,
        'subtotal': format_invoice_money(subtotal),
        'tax': format_invoice_money(tax),
        'show_tax': tax > 0.004,
        'total': format_invoice_money(money['total']),
        'amount_due': format_invoice_money(money['amount_due']),
        'amount_paid': format_invoice_money(money['amount_paid']),
        'show_due': show_due,
        'show_paid': show_paid,
        'fully_paid': fully_paid,
        'include_bank': include_bank,
        'include_gbp': include_bank,
        'include_pkr': include_bank,
        'gbp': dict(INVOICE_BANK_GBP),
        'pkr': dict(INVOICE_BANK_PKR),
        'fx': fx or {},
        'owner_name': customer_name,
        'owner_email': customer_email,
        'support_email': (brand_settings().get('support_email') or 'contact@brixenconsultants.com'),
    }


def format_invoice_email_datetime(raw):
    text = str(raw or '').strip()
    dt = None
    if text:
        for candidate, fmt in (
            (text[:19], '%Y-%m-%d %H:%M:%S'),
            (text[:16], '%Y-%m-%d %H:%M'),
            (text[:10], '%Y-%m-%d'),
        ):
            try:
                dt = datetime.datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        dt = datetime.datetime.now()
    return f"{dt.day} {dt.strftime('%B %Y')}"


def invoice_receipt_money_row(label, value, *, muted=False, strong=False):
    navy = BRIXEN_EMAIL_BRAND['navy']
    ink = BRIXEN_EMAIL_BRAND['ink']
    muted_color = BRIXEN_EMAIL_BRAND['muted']
    color = muted_color if muted else (navy if strong else ink)
    size = '16px' if strong else '14px'
    weight = '700' if strong else ('400' if muted else '500')
    pad = '8px' if strong else '5px'
    return (
        '<tr>'
        f'<td style="padding:{pad} 12px {pad} 0;font-family:{EMAIL_FONT_STACK};font-size:{size};line-height:1.4;'
        f'font-weight:{weight};color:{color};"><font color="{color}">{html_escape(label)}</font></td>'
        f'<td align="right" style="padding:{pad} 0;font-family:{EMAIL_FONT_STACK};font-size:{size};line-height:1.4;'
        f'font-weight:{weight};color:{color};white-space:nowrap;"><font color="{color}">{html_escape(value)}</font></td>'
        '</tr>'
    )


def invoice_receipt_item_row(item):
    ink = BRIXEN_EMAIL_BRAND['ink']
    muted = BRIXEN_EMAIL_BRAND['muted']
    name = str((item or {}).get('description') or '').strip() or 'Service'
    try:
        qty = int((item or {}).get('quantity') or 1) or 1
    except (TypeError, ValueError):
        qty = 1
    amount = format_invoice_money((item or {}).get('amount'))
    unit = (item or {}).get('unit_price')
    meta_parts = [f'Qty {qty}']
    try:
        unit_value = float(unit)
    except (TypeError, ValueError):
        unit_value = None
    if unit_value is not None and (qty != 1 or unit_value > 0.004):
        meta_parts.append(format_invoice_money(unit_value))
    meta = ' · '.join(meta_parts)
    return (
        '<tr>'
        f'<td style="padding:9px 12px 9px 0;vertical-align:top;font-family:{EMAIL_FONT_STACK};">'
        f'<p style="margin:0;font-size:14px;line-height:1.4;font-weight:500;color:{ink};"><font color="{ink}">{html_escape(name)}</font></p>'
        f'<p style="margin:3px 0 0;font-size:12px;line-height:1.35;color:{muted};"><font color="{muted}">{html_escape(meta)}</font></p>'
        '</td>'
        f'<td align="right" valign="top" style="padding:9px 0;white-space:nowrap;font-family:{EMAIL_FONT_STACK};'
        f'font-size:14px;line-height:1.4;font-weight:500;color:{ink};"><font color="{ink}">{html_escape(amount)}</font></td>'
        '</tr>'
    )


def invoice_receipt_field(label, value):
    if not str(value or '').strip():
        return ''
    ink = BRIXEN_EMAIL_BRAND['ink']
    muted = BRIXEN_EMAIL_BRAND['muted']
    return (
        f'<p style="margin:0 0 2px;font-family:{EMAIL_FONT_STACK};font-size:12px;line-height:1.35;color:{muted};">'
        f'<font color="{muted}">{html_escape(label)}</font></p>'
        f'<p style="margin:0 0 12px;font-family:{EMAIL_FONT_STACK};font-size:14px;line-height:1.4;font-weight:500;color:{ink};word-break:break-word;">'
        f'<font color="{ink}">{html_escape(value)}</font></p>'
    )


def invoice_receipt_pay_card(title, fields_html):
    navy = BRIXEN_EMAIL_BRAND['navy']
    ivory = BRIXEN_EMAIL_BRAND['ivory']
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 12px;">'
        f'<tr><td class="invoice-pad" bgcolor="{ivory}" style="padding:16px 16px 4px;background-color:{ivory};">'
        f'<p style="margin:0 0 12px;font-family:{EMAIL_FONT_STACK};font-size:12px;line-height:1.3;font-weight:700;color:{navy};">'
        f'<font color="{navy}">{html_escape(title)}</font></p>'
        f'{fields_html}'
        f'</td></tr></table>'
    )


def invoice_receipt_html(receipt, greeting_html, message_html, wordmark_url, cta_html, footer_html):
    navy = BRIXEN_EMAIL_BRAND['navy']
    gold = BRIXEN_EMAIL_BRAND['gold']
    ivory = BRIXEN_EMAIL_BRAND['ivory']
    ink = BRIXEN_EMAIL_BRAND['ink']
    muted = BRIXEN_EMAIL_BRAND['muted']
    line = BRIXEN_EMAIL_BRAND['line']
    receipt = receipt or {}
    item_rows = [invoice_receipt_item_row(item) for item in (receipt.get('items') or [])]
    items_html = ''.join(item_rows) or invoice_receipt_item_row({
        'description': 'Professional services',
        'quantity': 1,
        'amount': receipt.get('total') or '£0.00',
    })
    totals = [invoice_receipt_money_row('Subtotal', receipt.get('subtotal') or '£0.00', muted=True)]
    if receipt.get('show_tax'):
        totals.append(invoice_receipt_money_row('VAT', receipt.get('tax') or '£0.00', muted=True))
    if receipt.get('show_paid') and not receipt.get('fully_paid'):
        totals.append(invoice_receipt_money_row('Paid', receipt.get('amount_paid') or '£0.00', muted=True))
    totals.append(
        f'<tr><td colspan="2" style="padding:8px 0 0;border-top:1px solid {line};font-size:0;line-height:0;">&nbsp;</td></tr>'
    )
    totals.append(invoice_receipt_money_row('Total', receipt.get('total') or '£0.00', strong=True))
    if receipt.get('fully_paid'):
        totals.append(invoice_receipt_money_row('Paid', receipt.get('amount_paid') or receipt.get('total') or '£0.00', strong=True))
    elif receipt.get('show_due'):
        totals.append(invoice_receipt_money_row('Amount due', receipt.get('amount_due') or '£0.00', strong=True))
    meta_bits = []
    if receipt.get('date'):
        meta_bits.append(str(receipt.get('date')))
    if receipt.get('invoice_number'):
        meta_bits.append(f"Invoice {receipt['invoice_number']}")
    if receipt.get('order_number'):
        meta_bits.append(f"Order {receipt['order_number']}")
    meta_html = html_escape('  ·  '.join(meta_bits))
    invoice_tone = 'success' if receipt.get('fully_paid') else 'payment'
    details = ''
    if receipt.get('include_bank'):
        gbp = receipt.get('gbp') or {}
        pkr = receipt.get('pkr') or {}
        fx = receipt.get('fx') or {}
        gbp_html = invoice_receipt_pay_card('Pay in GBP', ''.join([
            invoice_receipt_field('Account name', gbp.get('account_name')),
            invoice_receipt_field('Account number', gbp.get('account_number')),
            invoice_receipt_field('Sort code', gbp.get('sort_code')),
        ]))
        pkr_fields = [
            invoice_receipt_field('Account name', pkr.get('account_name')),
            invoice_receipt_field('Bank name', pkr.get('bank_name')),
            invoice_receipt_field('IBAN', pkr.get('iban')),
        ]
        if fx.get('pkr_display') or fx.get('math'):
            pkr_fields.append(invoice_receipt_field(
                'Amount payable in PKR',
                fx.get('pkr_display') or fx.get('math'),
            ))
        pkr_html = invoice_receipt_pay_card('Pay in PKR', ''.join(pkr_fields))
        details = f"""
          <tr>
            <td class="invoice-pad" style="padding:18px 24px 4px;background:#ffffff;">
              <p style="margin:0 0 10px;font-family:{EMAIL_FONT_STACK};font-size:13px;line-height:1.35;font-weight:700;color:{navy};"><font color="{navy}">Payment details</font></p>
              {gbp_html}
              {pkr_html}
            </td>
          </tr>"""
    extra_css = (
        '@media only screen and (max-width:620px){'
        '.invoice-shell{width:100%!important;max-width:100%!important;}'
        '.invoice-pad{padding-left:18px!important;padding-right:18px!important;}'
        '}'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
{email_document_head_html(str(receipt.get('title') or 'Invoice'), extra_css)}
<body style="margin:0;padding:0;background:{ivory};font-family:{EMAIL_FONT_STACK};color:{ink};-webkit-font-smoothing:antialiased;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="{ivory}" style="background:{ivory};padding:20px 10px;">
    <tr>
      <td align="center">
        <table class="invoice-shell" role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:560px;max-width:560px;background:#ffffff;border-radius:18px;overflow:hidden;">
          {email_hero_header_html(
              receipt.get('title') or 'Invoice',
              tone=invoice_tone,
              badge=receipt.get('kicker') or ('Payment received' if receipt.get('fully_paid') else 'Invoice'),
              navy=navy,
              gold=gold,
              title_size='20px',
              wordmark_url=wordmark_url,
          )}
          <tr>
            <td class="invoice-pad" align="center" style="padding:0 24px 10px;background:#ffffff;">
              <p style="margin:0;font-family:{EMAIL_FONT_STACK};font-size:12px;line-height:1.45;color:{muted};"><font color="{muted}">{meta_html}</font></p>
            </td>
          </tr>
          <tr>
            <td class="invoice-pad" align="left" style="padding:4px 24px 14px;background:#ffffff;">
              <p style="margin:0;font-family:{EMAIL_FONT_STACK};font-size:16px;line-height:1.45;letter-spacing:-0.022em;font-weight:400;color:{ink};"><font color="{ink}">Hi {greeting_html},</font></p>
              {f'<p style="margin:4px 0 0;font-family:{EMAIL_FONT_STACK};font-size:13px;line-height:1.4;color:{muted};"><font color="{muted}">{html_escape(receipt.get("customer_email"))}</font></p>' if str(receipt.get('customer_email') or '').strip() else ''}
              <p style="margin:8px 0 0;font-family:{EMAIL_FONT_STACK};font-size:14px;line-height:1.5;color:{muted};"><font color="{muted}">{message_html}</font></p>
            </td>
          </tr>
          <tr>
            <td class="invoice-pad" style="padding:4px 24px 6px;background:#ffffff;">
              <p style="margin:0 0 8px;font-family:{EMAIL_FONT_STACK};font-size:13px;line-height:1.35;font-weight:700;color:{navy};"><font color="{navy}">Invoice summary</font></p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-top:1px solid {line};">
                {items_html}
              </table>
            </td>
          </tr>
          <tr>
            <td class="invoice-pad" style="padding:0 24px 8px;background:#ffffff;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                {''.join(totals)}
              </table>
            </td>
          </tr>
          {details}
          <tr>
            <td class="invoice-pad" align="left" style="padding:10px 24px 6px;background:#ffffff;">
              <p style="margin:0;font-family:{EMAIL_FONT_STACK};font-size:13px;line-height:1.35;font-weight:700;color:{navy};"><font color="{navy}">Need help?</font></p>
              <p style="margin:6px 0 0;font-family:{EMAIL_FONT_STACK};font-size:13px;line-height:1.5;color:{muted};">
                <font color="{muted}">If you have any questions about this invoice or your order, contact Brixen Consultants support.</font>
              </p>
            </td>
          </tr>
          <tr>
            <td class="invoice-pad" align="center" style="padding:12px 24px 12px;background:#ffffff;">
              {cta_html}
            </td>
          </tr>
          <tr>
            <td class="invoice-pad" align="left" style="padding:8px 24px 20px;background:#ffffff;">
              {footer_html}
            </td>
          </tr>
          <tr><td bgcolor="{navy}" style="height:4px;line-height:4px;font-size:0;background-color:{navy};">&nbsp;</td></tr>
          <tr><td bgcolor="{gold}" style="height:2px;line-height:2px;font-size:0;background-color:{gold};">&nbsp;</td></tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def registration_offers_html(navy, wa_href):
    navy = html_escape(navy or '#003971')
    gold = '#c5a572'
    whatsapp_url = registration_whatsapp_url(wa_href)
    return f"""
              <p style="margin:0 0 8px;font-family:{EMAIL_FONT_STACK};font-size:12px;line-height:1.3;letter-spacing:0.12em;text-transform:uppercase;color:{gold};">Your next-step strategy</p>
              <p style="margin:0 0 16px;font-family:{EMAIL_FONT_STACK};font-size:16px;line-height:1.47;letter-spacing:-0.022em;color:#6e6e73;">A simple plan to start trading, stay compliant, and grow from day one.</p>
              {key_points_html(registration_next_steps_text().splitlines(), navy)}
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:22px;">
                <tr>
                  <td align="center">
                    {apple_email_button_html(whatsapp_url, 'WhatsApp for a free consultation', '#25D366')}
                  </td>
                </tr>
              </table>"""


def build_email_footer_text(brand, support_email=None, phone_display=None, footer_note=None):
    import email_engine as _email_engine

    brand = dict(brand or {})
    if support_email:
        brand['support_email'] = support_email
    if phone_display:
        brand['support_phone'] = phone_display
    text = _email_engine.email_signature_text(brand)
    note = (footer_note or '').strip()
    if note:
        text = f"{text}\n\n{note}"
    return f"\n{text}\n"


def build_email_footer_html(brand, support_email=None, support_phone=None, tel_href=None, wa_href=None, footer_note=None, accent=None, tone='default'):
    """
    Legacy helper kept for invoice/celebration navy panels.
    Signature + disclaimer now live in the white body for activity emails.
    This returns compact contact lines only (no KIND REGARDS block).
    """
    import email_engine as _email_engine

    brand = dict(brand or {})
    if support_email:
        brand['support_email'] = support_email
    if support_phone:
        brand['support_phone'] = support_phone
    c = _email_engine.brand_contact_fields(brand)
    light = str(tone or '').strip().lower() == 'light'
    ink = '#ffffff' if light else (accent or '#003971')
    gold = '#c5a572'
    muted = gold if light else '#6e6e73'
    parts = []
    if c['email']:
        parts.append(
            f'<p style="margin:0;text-align:center;font-size:13px;line-height:1.45;font-family:{EMAIL_FONT_STACK};">'
            f'<a href="mailto:{html_escape(c["email"])}" style="color:{ink};text-decoration:none;">'
            f'<font color="{ink}">{html_escape(c["email"])}</font></a></p>'
        )
    if c['phone']:
        tel = tel_href or f"+{c['phone_digits']}"
        parts.append(
            f'<p style="margin:6px 0 0;text-align:center;font-size:13px;line-height:1.45;font-family:{EMAIL_FONT_STACK};">'
            f'<a href="tel:{html_escape(tel)}" style="color:{ink};text-decoration:none;">'
            f'<font color="{ink}">{html_escape(c["phone"])}</font></a></p>'
        )
    if c['website_label']:
        parts.append(
            f'<p style="margin:6px 0 0;text-align:center;font-size:13px;line-height:1.45;font-family:{EMAIL_FONT_STACK};">'
            f'<a href="{html_escape(c["website"])}" style="color:{gold};text-decoration:none;">'
            f'<font color="{gold}">{html_escape(c["website_label"])}</font></a></p>'
        )
    note = (footer_note or '').strip()
    # Never put long compliance disclaimers inside the navy panel.
    if note and 'Companies House' not in note:
        parts.append(
            f'<p style="margin:14px 0 0;text-align:center;font-size:11px;line-height:1.45;'
            f'color:{muted};font-family:{EMAIL_FONT_STACK};"><font color="{muted}">{html_escape(note)}</font></p>'
        )
    return '\n'.join(parts)


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
    extra_cta_label=None,
    extra_cta_url=None,
    extra_cta_background=None,
    detail_title=None,
    detail_value=None,
    extra_message=None,
    email_sections=None,
    invoice_receipt=None,
    footer_note=None,
    layout='activity',
    greeting_name=None,
    email_category=None,
    company_name=None,
    company_number=None,
    suppress_default_cta=False,
    structured_html=None,
    use_graphic_footer=False,
    header_style=None,
):
    import email_engine as _email_engine

    brand = brand_settings()
    category = _email_engine.normalize_email_category(
        email_category or (
            'INVOICE' if str(layout or '').strip().lower() == 'invoice'
            else 'ACCOUNT' if str(layout or '').strip().lower() == 'celebration'
            else 'ORDER'
        )
    )
    # Explicit footer_note wins; None uses category default; '' suppresses.
    if footer_note is None:
        resolved_note = _email_engine.category_disclaimer(category)
    else:
        resolved_note = str(footer_note).strip()
    greeting = str(greeting_name or (client or {}).get('full_name') or 'there').strip() or 'there'
    client_name = html_escape(greeting)
    message_text = (message or '').strip()
    message_html = html_escape(message_text).replace('\n', '<br>')
    extra = (extra_message or '').strip()
    sections = [section for section in (email_sections or []) if section]
    if sections and not extra:
        extra = email_facts_text(sections)
    invoice_layout = str(layout or '').strip().lower() == 'invoice'
    if str(layout or '').strip().lower() == 'celebration' and not extra:
        extra = registration_next_steps_text()
    panel_url = cta_url or client_website_url()
    wordmark_url = html_escape(wordmark_image_src())
    primary = html_escape(brand['primary_color'])
    support_email = html_escape(brand['support_email'])
    phone_digits = re.sub(r'\D', '', brand.get('support_phone') or '447360515317') or '447360515317'
    phone_display = _email_engine.format_uk_phone_display(phone_digits)
    support_phone = html_escape(phone_display)
    tel_href = html_escape(f"+{phone_digits}" if not phone_digits.startswith('+') else phone_digits)
    wa_href = html_escape(f"https://wa.me/{phone_digits}")
    email_subject = subject or f"{headline} — {brand['company_name']}"

    body_text = f"{'Dear' if str(header_style or '').strip().lower() == 'premium' else 'Hi'} {greeting},\n\n{message_text}\n"
    if detail_title and detail_value and not company_number:
        body_text += f"\n{detail_title}: {detail_value}\n"
    if extra:
        body_text += f"\n{extra}\n"
    if company_name or company_number:
        body_text += "\n"
        if company_name:
            body_text += f"Company: {company_name}\n"
        if company_number:
            body_text += f"Company number: {company_number}\n"
    if str(layout or '').strip().lower() == 'celebration':
        body_text += "\nDownload your certificate of incorporation.\n"
        body_text += f"\nWhatsApp for a free consultation:\n{registration_whatsapp_url('https://wa.me/' + phone_digits)}\n"
    if cta_label and panel_url and not suppress_default_cta:
        body_text += f"\n{cta_label}:\n{panel_url}\n"
    if (not invoice_layout) and extra_cta_label and extra_cta_url:
        body_text += f"\n{extra_cta_label}:\n{extra_cta_url}\n"
    body_text += build_email_footer_text(brand, brand['support_email'], phone_display, resolved_note)

    signature_html = _email_engine.email_signature_html(brand, align='left')
    disclaimer_html = _email_engine.email_disclaimer_html(resolved_note)
    footer_html = build_email_footer_html(
        brand, support_email, support_phone, tel_href, wa_href, None, primary,
        tone='light',
    )

    if str(layout or '').strip().lower() == 'celebration':
        navy = primary or '#003971'
        gold = '#c5a572'
        number_line = ''
        if detail_value:
            number_line = (
                f'<p style="margin:16px 0 0;text-align:center;font-family:{EMAIL_FONT_STACK};font-size:14px;'
                f'line-height:1.4;letter-spacing:-0.016em;color:#86868b;">'
                f'Company number {html_escape(detail_value)}</p>'
            )
        body_html = f"""<!DOCTYPE html>
<html lang="en">
{email_document_head_html(email_subject)}
<body style="margin:0;padding:0;background:#f3efe8;font-family:{EMAIL_FONT_STACK};color:#1c1915;-webkit-font-smoothing:antialiased;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f3efe8" style="background:#f3efe8;padding:36px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:560px;max-width:560px;background:#ffffff;">
          <tr><td style="height:3px;line-height:3px;font-size:0;background:{gold};">&nbsp;</td></tr>
          <tr>
            <td align="center" style="padding:32px 40px 10px;background:#ffffff;">
              <img src="{wordmark_url}" alt="Brixen Consultants" width="148" style="display:block;margin:0 auto;border:0;outline:none;text-decoration:none;width:148px;height:auto;">
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:14px 40px 6px;background:#ffffff;">
              <table role="presentation" width="56" cellspacing="0" cellpadding="0" border="0" align="center">
                <tr><td style="height:1px;line-height:1px;font-size:0;background:{gold};">&nbsp;</td></tr>
              </table>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:6px 40px 4px;background:#ffffff;">
              <p style="margin:0;font-family:{EMAIL_FONT_STACK};font-size:16px;line-height:1.4;letter-spacing:-0.022em;color:#1d1d1f;">Congratulations</p>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:10px 28px 4px;background:#ffffff;">
              {congratulations_hero_html()}
            </td>
          </tr>
          <tr>
            <td align="left" style="padding:22px 40px 8px;background:#ffffff;">
              <p style="margin:0;font-family:{EMAIL_FONT_STACK};font-size:17px;line-height:1.47;letter-spacing:-0.022em;color:#1d1d1f;">Hi {client_name},</p>
              <p style="margin:16px 0 0;font-family:{EMAIL_FONT_STACK};font-size:16px;line-height:1.47;letter-spacing:-0.022em;color:#6e6e73;">{message_html}</p>
              <p style="margin:16px 0 0;font-family:{EMAIL_FONT_STACK};font-size:16px;line-height:1.47;letter-spacing:-0.022em;color:#1d1d1f;">Download your certificate of incorporation from Companies House.</p>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:22px 40px 4px;background:#ffffff;">
              {apple_email_button_html(panel_url, cta_label, navy)}
              {number_line}
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:22px 40px 8px;background:#ffffff;">
              <table role="presentation" width="56" cellspacing="0" cellpadding="0" border="0" align="center">
                <tr><td style="height:1px;line-height:1px;font-size:0;background:{gold};">&nbsp;</td></tr>
              </table>
            </td>
          </tr>
          <tr>
            <td align="left" style="padding:8px 40px 16px;background:#ffffff;">
              {registration_offers_html(navy, wa_href)}
            </td>
          </tr>
          <tr>
            <td align="left" style="padding:8px 40px 28px;background:#ffffff;">
              {signature_html}
              {disclaimer_html}
            </td>
          </tr>
          <tr>
            <td align="center" bgcolor="#003971" style="padding:18px 40px 16px;background-color:#003971;color:#ffffff;">
              {footer_html}
            </td>
          </tr>
          <tr><td style="height:3px;line-height:3px;font-size:0;background:{gold};">&nbsp;</td></tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
        return email_subject, body_text, body_html

    navy = '#003971'
    gold = '#c5a572'
    if invoice_layout and invoice_receipt:
        cta_html = apple_email_button_html(panel_url, cta_label, navy) if cta_label and panel_url else ''
        body_html = invoice_receipt_html(
            invoice_receipt,
            client_name,
            message_html,
            wordmark_url,
            cta_html,
            signature_html + disclaimer_html,
        )
        return email_subject, body_text, body_html

    points = [line.strip() for line in extra.splitlines() if line.strip()] if not sections else []
    tone = email_tone_from_context(
        layout=layout,
        badge=badge,
        headline=headline,
        alert_label=alert_label,
        message=message_text,
        extra=extra,
    )
    hero_title = str(headline or badge or 'Account update').strip()
    chip = str(badge or email_tone_meta(tone)['label']).strip()
    if structured_html:
        facts_block = structured_html
    else:
        facts_block = email_facts_html(sections, navy, gold) if sections else key_points_html(points, navy, tone=tone)
    company_block = _email_engine.email_company_details_html(company_name, company_number)
    if not company_block and detail_title and detail_value and 'company number' in str(detail_title).lower():
        company_block = _email_engine.email_company_details_html(None, detail_value)

    buttons = []
    if cta_label and panel_url and not suppress_default_cta:
        buttons.append((panel_url, cta_label, navy))
    if extra_cta_label and extra_cta_url:
        buttons.append((extra_cta_url, extra_cta_label, extra_cta_background or navy))
    cta_stack = _email_engine.email_cta_stack_html(buttons, apple_email_button_html)

    greeting_prefix = 'Dear' if str(header_style or '').strip().lower() == 'premium' else 'Hi'
    body_align = 'justify' if str(header_style or '').strip().lower() == 'premium' else 'left'
    body_size = '17px' if str(header_style or '').strip().lower() == 'premium' else '15px'
    greet_size = '18px' if str(header_style or '').strip().lower() == 'premium' else '16px'
    body_inner = f"""
              <p style="margin:0;font-family:{EMAIL_FONT_STACK};font-size:{greet_size};line-height:1.4;color:#111827;text-align:left;">{greeting_prefix} {client_name},</p>
              <p style="margin:8px 0 0;font-family:{EMAIL_FONT_STACK};font-size:{body_size};line-height:1.5;color:#4b5563;text-align:{body_align};">{message_html}</p>
              <div style="margin:10px 0 0;text-align:left;">{facts_block}</div>
              {f'<div style="margin:12px 0 0;text-align:left;">{company_block}</div>' if company_block else ''}
              {f'<div style="margin:14px 0 0;text-align:center;">{cta_stack}</div>' if cta_stack else ''}
    """
    graphic_footer = ''
    body_disclaimer = disclaimer_html
    if use_graphic_footer:
        graphic_footer = _email_engine.email_graphic_footer_html(
            brand,
            wordmark_url=wordmark_image_src(),
            disclaimer='',  # keep disclaimer after signature, not in a heavy footer
        )
    if str(header_style or '').strip().lower() == 'premium':
        hero_html = _email_engine.email_premium_header_html(
            wordmark_url=wordmark_image_src(),
            eyebrow='Compliance advisory' if category in ('COMPLIANCE', 'COMPANIES_HOUSE', 'KYC') else (chip or 'Account update'),
        )
    else:
        hero_html = email_hero_header_html(
            hero_title, tone=tone, badge=chip, navy=navy, gold=gold,
            title_size='18px', wordmark_url=wordmark_url, compact=True,
        )
    # Prefer email_engine shell which uses responsive max-width.
    body_html = _email_engine.build_transactional_shell_html(
        subject=email_subject,
        head_html=email_document_head_html(email_subject),
        hero_html=hero_html,
        body_inner_html=body_inner,
        signature_html=signature_html,
        disclaimer_html=body_disclaimer,
        footer_html=graphic_footer,
    )
    return email_subject, body_text, body_html


def build_client_document_email(client, doc_name, client_message=None, company_name=None, order_number=None):
    doc_name = doc_name or 'Document'
    extra_lines = [f'Document:\n📄 {doc_name}']
    if company_name:
        extra_lines.append(f'Company:\n{company_name}')
    if order_number:
        extra_lines.append(f'Order:\n{order_number}')
    note = (client_message or '').strip()
    if note:
        extra_lines.append(f'Message:\n{note}')
    message = 'Brixen Consultants has uploaded a new document to your client portal.'
    return build_client_notification_email(
        client,
        'A new document is available in your Brixen client portal',
        message,
        subject=f"New document ready: A new document is available in your Brixen client portal — {doc_name}",
        badge='Documents uploaded',
        cta_label='View in Messages & Files',
        cta_url=client_website_url(),
        extra_message='\n\n'.join(extra_lines),
        email_category='DOCUMENT',
        company_name=company_name,
        layout='activity',
    )


def resolve_client_user(client_or_id):
    if isinstance(client_or_id, dict):
        user_id = client_or_id.get('user_id') or client_or_id.get('id')
        if user_id:
            row = query_db(
                "SELECT id, email, notification_email, full_name, role FROM users WHERE id = ? AND role = 'CLIENT';",
                (user_id,),
                one=True,
            )
            if row:
                return dict(row)
        if client_or_id.get('role') == 'CLIENT' and client_or_id.get('email'):
            out = dict(client_or_id)
            out.setdefault('notification_email', client_or_id.get('notification_email') or '')
            return out
        if client_or_id.get('email') and user_id:
            return {
                'id': user_id,
                'email': client_or_id['email'],
                'notification_email': client_or_id.get('notification_email') or '',
                'full_name': client_or_id.get('full_name') or '',
                'role': 'CLIENT',
            }
        client_or_id = user_id or client_or_id.get('id')
    if not client_or_id:
        return None
    row = query_db(
        "SELECT id, email, notification_email, full_name, role FROM users WHERE id = ? AND role = 'CLIENT';",
        (client_or_id,),
        one=True,
    )
    return dict(row) if row else None


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
    extra_cta_label=None,
    extra_cta_url=None,
    detail_title=None,
    detail_value=None,
    extra_message=None,
    email_sections=None,
    invoice_receipt=None,
    footer_note=None,
    email_to=None,
    badge=None,
    alert_label=None,
    layout=None,
    greeting_name=None,
    company=None,
    order=None,
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
            'extra_cta_label': extra_cta_label,
            'extra_cta_url': extra_cta_url,
            'detail_title': detail_title,
            'detail_value': detail_value,
            'extra_message': extra_message,
            'email_sections': email_sections,
            'invoice_receipt': invoice_receipt,
            'badge': badge,
            'alert_label': alert_label,
            'layout': layout or 'activity',
            'greeting_name': greeting_name,
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

    recipient = resolve_work_notification_email(
        client=client_rec,
        company=company,
        order=order if isinstance(order, dict) else (client if isinstance(client, dict) and client.get('order_number') else None),
        email_to=email_to,
    )
    # When the caller is an order row, use it for company email resolution.
    if not recipient and isinstance(client, dict) and (client.get('company_id') or client.get('owner_form_email')):
        recipient = resolve_work_notification_email(client=client_rec, order=client, email_to=email_to)
    if not recipient:
        return {
            'notification_created': True,
            'email_sent': False,
            'email_status': 'No company owner email',
        }
    sent, status = EmailService.send_notification_email(
        recipient,
        email_subject,
        email_text,
        email_html,
    )
    return {
        'notification_created': True,
        'email_sent': bool(sent),
        'email_status': status,
        'email_to': recipient,
    }


def classify_product_activity(service_name, category=''):
    text = f"{service_name or ''} {category or ''}".lower()
    if any(token in text for token in ('formation', 'incorporat', 'company registration', 'register a company')):
        return 'formation'
    if any(token in text for token in ('identity', 'idv', 'kyc')):
        return 'identity'
    if 'psc' in text or 'person of significant' in text:
        return 'psc'
    if any(token in text for token in ('account', 'confirmation statement', 'bookkeep', 'compliance')):
        return 'accounts'
    if 'vat' in text:
        return 'vat'
    if any(token in text for token in ('bank', 'tide', 'wise')):
        return 'bank'
    if any(token in text for token in ('registered office', 'mail', 'virtual office', 'address')):
        return 'office'
    if any(token in text for token in ('document', 'file', 'certificate')):
        return 'files'
    return 'service'


def notify_activity_done(
    client,
    *,
    title,
    headline,
    message,
    points,
    subject,
    ntype='activity',
    link='/documents',
    cta_label='Open Client Panel',
    cta_url=None,
    extra_cta_label=None,
    extra_cta_url=None,
    detail_title=None,
    detail_value=None,
    email_to=None,
    greeting_name=None,
    layout='activity',
    email_sections=None,
    invoice_receipt=None,
    company=None,
    order=None,
):
    lines = [str(point).strip() for point in (points or []) if str(point).strip()]
    extra = '\n'.join(lines)
    order_row = order if isinstance(order, dict) else (client if isinstance(client, dict) and client.get('order_number') else None)
    company_row = company if company is not None else None
    resolved_to = email_to
    if resolved_to is None:
        resolved_to = resolve_work_notification_email(client=client, company=company_row, order=order_row)
    return notify_client(
        client,
        title,
        message,
        ntype,
        link,
        email_subject=subject,
        email_headline=headline,
        extra_message=extra or None,
        email_sections=email_sections,
        invoice_receipt=invoice_receipt,
        cta_label=cta_label,
        cta_url=cta_url or client_website_url(),
        extra_cta_label=extra_cta_label,
        extra_cta_url=extra_cta_url,
        detail_title=detail_title,
        detail_value=detail_value,
        email_to=resolved_to,
        greeting_name=greeting_name,
        layout=layout or 'activity',
        footer_note='This email is about an update on your Brixen Consultants service.',
        company=company_row,
        order=order_row,
    )


def product_completion_points(order):
    service = str((order or {}).get('service_name') or 'Your order').strip()
    kind = classify_product_activity(service)
    company = str((order or {}).get('company_name') or '').strip()
    number = str((order or {}).get('order_number') or '').strip()
    points = {
        'formation': [
            '✅ Company formation is complete',
            '📄 Your company files are ready',
            '🏦 You can now open a UK business bank account',
        ],
        'office': [
            '✅ Registered office is active',
            '📬 Mail handling is in place',
            '🏢 Banks and Companies House can use this address',
        ],
        'accounts': [
            '✅ Accounts work is complete',
            '📄 Confirmation statement and yearly accounts are filed',
            '📅 We will remind you before the next deadline',
        ],
        'vat': [
            '✅ VAT registration is complete',
            '🧾 Your VAT details are ready for invoices',
        ],
        'bank': [
            '✅ Business bank account setup is complete',
            '🏦 Tide or Wise is ready for payments',
        ],
        'identity': [
            '✅ Identity verification is complete',
            '🪪 Companies House can now accept your filings',
        ],
        'psc': [
            '✅ PSC verification is complete',
            '👥 People with significant control are confirmed',
        ],
        'files': [
            '✅ Your company files are ready',
            '📁 Open Messages & Files to download them',
        ],
        'service': [
            f'✅ {service} is complete',
        ],
    }.get(kind, [f'✅ {service} is complete'])
    if company:
        points.append(f'🏢 {company}')
    if number:
        points.append(f'📦 Order {number}')
    return points


def notify_product_completed(order):
    if not order:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'No order'}
    service = str(order.get('service_name') or 'Your order').strip()
    return notify_activity_done(
        order,
        title=f'{service} is complete',
        headline=f'{service} is done',
        message='Good news — this order is now Completed.',
        points=product_completion_points(order),
        subject=f"{service} is complete — {brand_settings()['company_name']}",
        ntype='order_complete',
        link='/orders',
        cta_label='View in your client panel',
        detail_title='Order',
        detail_value=order.get('order_number'),
        order=order,
        company=load_company_for_notify(order=order),
    )


def payment_notify_email_for_order(order):
    return real_order_connector(order).get('owner_form_email') or ''


def notify_payment_received(order, invoice=None):
    if not order:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'No order'}
    connector = real_order_connector(order)
    service = str(order.get('service_name') or 'Your order').strip()
    number = str(order.get('order_number') or '').strip()
    invoice_number = str((invoice or {}).get('invoice_number') or '').strip()
    money = invoice_money_state(invoice or {})
    currency = money['currency']
    timing = money['timing']
    remaining = money['amount_due']
    deposit = remaining > 0.004
    if deposit:
        title = 'Deposit received'
        headline = 'We’ve received your deposit'
        message = 'Thank you. Your deposit is in. The remaining balance is due when the work is done.'
    elif timing == 'Advance':
        title = 'Advance payment received'
        headline = 'We’ve received your advance payment'
        message = 'Thank you. Your advance payment is in and we will start this order.'
    else:
        title = 'Payment received'
        headline = 'We’ve received your payment'
        message = 'Thank you. Your payment for this completed work is in.'
    email_to = connector.get('owner_form_email') or ''
    invoice_url = ''
    if invoice and invoice.get('id'):
        invoice_url = invoice_public_document_url(invoice['id'])
    return notify_activity_done(
        order,
        title=title,
        headline=headline,
        message=message,
        points=[],
        subject=f"{title} — {invoice_number or number or service} — {brand_settings()['company_name']}",
        ntype='payment_received',
        link='/invoices',
        cta_label='Download invoice' if invoice_url else 'Track your order',
        cta_url=invoice_url or None,
        detail_title='Invoice' if invoice_number else 'Order',
        detail_value=invoice_number or number or service,
        email_to=email_to,
        greeting_name=invoice_email_greeting_name(order, invoice),
        layout='invoice',
        email_sections=invoice_email_sections(order, invoice, include_bank=False),
        invoice_receipt=invoice_receipt_payload(order, invoice, include_bank=False),
    )


def payment_account_points():
    gbp = INVOICE_BANK_GBP
    pkr = INVOICE_BANK_PKR
    return [
        f'GBP — {gbp["account_name"]}',
        f'Account number {gbp["account_number"]} · Sort code {gbp["sort_code"]}',
        gbp['pay_url'],
        f'PKR — {pkr["account_name"]}',
        f'Bank name {pkr["bank_name"]}',
        f'IBAN {pkr["iban"]}',
    ]


def invoice_email_sections(order, invoice=None, *, include_bank=False):
    money = invoice_money_state(invoice or {})
    currency = money['currency']
    invoice_number = str((invoice or {}).get('invoice_number') or '').strip()
    service = str((order or {}).get('service_name') or 'Your order').strip()
    number = str((order or {}).get('order_number') or '').strip()
    summary = []
    due = float(money.get('amount_due') or 0)
    if include_bank or due > 0.004:
        summary.append(('Amount due', format_invoice_money(money['amount_due'], currency)))
    else:
        summary.append(('Amount', format_invoice_money(money['total'], currency)))
    if invoice_number:
        summary.append(('Invoice', f"{invoice_number} — {format_invoice_money(money['total'], currency)}"))
    if include_bank and money['timing'] == 'Deposit' and money['deposit_amount']:
        summary.append(('Deposit now', format_invoice_money(money['deposit_amount'], currency)))
    if (not include_bank) and float(money.get('amount_paid') or 0) > 0.004:
        summary.append(('Paid', format_invoice_money(money['amount_paid'], currency)))
    if service:
        summary.append(('Service', service))
    if number:
        summary.append(('Order', number))
    sections = [{'title': 'Invoice', 'rows': summary}]
    if not include_bank:
        return sections
    gbp = INVOICE_BANK_GBP
    pkr = INVOICE_BANK_PKR
    sections.append({
        'title': 'Pay in GBP',
        'rows': [
            ('Account name', gbp['account_name']),
            ('Account number', gbp['account_number']),
            ('Sort code', gbp['sort_code']),
        ],
    })
    pkr_rows = [
        ('Account name', pkr['account_name']),
        ('Bank name', pkr['bank_name']),
        ('IBAN', pkr['iban']),
    ]
    if invoice_client_is_in_pakistan(invoice or {}):
        fx = invoice_pakistan_fx_note(due)
        if fx:
            pkr_rows.append(('If you pay from Pakistan', fx['math']))
            pkr_rows.append(('Send to UBL', fx['pkr_display']))
    sections.append({'title': 'Pay in PKR', 'rows': pkr_rows})
    return sections


def notify_payment_requested(order, invoice=None):
    if not order:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'No order'}
    connector = real_order_connector(order)
    service = str(order.get('service_name') or 'Your order').strip()
    number = str(order.get('order_number') or '').strip()
    invoice_number = str((invoice or {}).get('invoice_number') or '').strip()
    money = invoice_money_state(invoice or {})
    due = money['amount_due']
    if due <= 0.004:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'Nothing due'}
    email_to = connector.get('owner_form_email') or ''
    if not invoice or not invoice.get('id'):
        return {'notification_created': False, 'email_sent': False, 'email_status': 'No invoice'}
    invoice_url = invoice_public_document_url(invoice['id'])
    return notify_activity_done(
        order,
        title='Invoice',
        headline='Invoice',
        message=invoice_payment_request_copy(include_gbp=True, include_pkr=True),
        points=[],
        subject=invoice_request_email_subject(invoice, order),
        ntype='payment_requested',
        link='/invoices',
        cta_label='Download invoice',
        cta_url=invoice_url,
        detail_title='Invoice' if invoice_number else 'Order',
        detail_value=invoice_number or number or service,
        email_to=email_to,
        greeting_name=invoice_email_greeting_name(order, invoice),
        layout='invoice',
        email_sections=invoice_email_sections(order, invoice, include_bank=True),
        invoice_receipt=invoice_receipt_payload(order, invoice, include_bank=True),
    )


def notify_compliance_activity(company, events):
    if not company or not events:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'No activity'}
    name = str(company.get('name') or 'Your company').strip()
    points = [f'🏢 {name}']
    titles = []
    if 'identity' in events:
        titles.append('Identity verification')
        points.append('✅ Identity verification is complete')
        points.append('🪪 Companies House can now accept your filings')
    if 'psc' in events:
        titles.append('PSC verification')
        points.append('✅ PSC verification is complete')
        points.append('👥 People with significant control are confirmed')
    headline = ' and '.join(titles) + ' is done' if titles else 'Compliance update'
    return notify_activity_done(
        company.get('user_id') or company,
        title=headline,
        headline=headline,
        message='A required company check is now complete.',
        points=points,
        subject=f"{headline} — {name} — {brand_settings()['company_name']}",
        ntype='compliance_complete',
        link='/companies',
        cta_label='View your company',
        detail_title='Company',
        detail_value=name,
        company=company,
    )


def notify_accounts_filed(company, filing=None):
    if not company:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'No company'}
    name = str(company.get('name') or 'Your company').strip()
    period = str((filing or {}).get('period_end') or '').strip()
    confirmation = str((filing or {}).get('confirmation_number') or '').strip()
    points = [
        '✅ Yearly accounts are filed',
        f'🏢 {name}',
    ]
    if period:
        points.append(f'📅 Period ending {period}')
    if confirmation:
        points.append(f'📄 Confirmation {confirmation}')
    points.append('📁 Company files are in your client panel')
    return notify_activity_done(
        company.get('user_id') or company,
        title='Yearly accounts filed',
        headline='Your yearly accounts are filed',
        message='Good news — the accounts for this company are now filed.',
        points=points,
        subject=f"Yearly accounts filed — {name} — {brand_settings()['company_name']}",
        ntype='accounts_filed',
        link='/companies',
        cta_label='View your company',
        detail_title='Company',
        detail_value=name,
        company=company,
    )


def portal_alias_urls():
    return (
        'https://portal.brixenconsultants.com',
        'http://127.0.0.1:5050',
    )


_UK_COUNTRY_ALIASES = {
    'uk', 'gb', 'gbr', 'united kingdom', 'great britain', 'britain',
    'england', 'scotland', 'wales', 'northern ireland', 'n. ireland',
    'n ireland', 'eng',
}
_UK_POSTCODE_RE = re.compile(r'^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$', re.I)
_UK_COMPANY_NUMBER_RE = re.compile(r'^(\d{6,8}|[A-Z]{2}\d{5,6})$', re.I)
_UK_OFFICE_RE = re.compile(
    r'united kingdom|\bgreat britain\b|\benland\b|\bscotland\b|\bwales\b|'
    r'\bnorthern ireland\b|\buk\b|\bgb\b|\blondon\b',
    re.I,
)


def looks_like_uk_company_number(company_number):
    num = str(company_number or '').strip().upper().replace(' ', '')
    return bool(num) and bool(_UK_COMPANY_NUMBER_RE.fullmatch(num))


def _token_is_uk_country(value):
    text = str(value or '').strip()
    if not text:
        return False
    if text.lower() in _UK_COUNTRY_ALIASES:
        return True
    return bool(_UK_POSTCODE_RE.fullmatch(text))


def company_country_label(row):
    """Always return a stable jurisdiction label. Never show GB/England/PK/postcode fragments."""
    src = dict(row or {})
    number = str(src.get('company_number') or '').strip()
    if is_pending_company_number(number) or looks_like_uk_company_number(number):
        return 'United Kingdom'
    country = str(src.get('country') or '').strip()
    if _token_is_uk_country(country):
        return 'United Kingdom'
    office = str(src.get('reg_office') or '')
    if office:
        last = office.split(',')[-1].strip()
        if _token_is_uk_country(last) or _UK_OFFICE_RE.search(office) or _UK_POSTCODE_RE.search(office):
            return 'United Kingdom'
    if country and country.lower() not in ('—', '-', 'none', 'n/a', 'pk', 'pakistan'):
        if len(country) <= 3 or country.lower() in _UK_COUNTRY_ALIASES:
            return 'United Kingdom'
    return 'United Kingdom'


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


def delete_company_card(company_id, actor=None):
    """Remove a company portfolio card. Linked orders are reassigned or hidden."""
    company = query_db("SELECT * FROM companies WHERE id = ?;", (company_id,), one=True)
    if not company:
        return None, 'Company not found'
    record_dismissed_company_card(company)
    replacement = match_company_for_client(company.get('user_id'), company.get('name'), exclude_id=company_id)
    replacement_row = query_db(
        "SELECT id, company_number FROM companies WHERE id = ?;",
        (replacement,),
        one=True,
    ) if replacement else None
    if replacement_row and not is_pending_company_number(replacement_row.get('company_number')):
        linked_orders = query_db("SELECT id FROM orders WHERE company_id = ?;", (company_id,)) or []
        for order in linked_orders:
            attach_order_to_company(order['id'], replacement_row['id'])
    else:
        execute_db(
            "UPDATE orders SET company_id = NULL, portfolio_hidden = 1 WHERE company_id = ?;",
            (company_id,),
        )
        execute_db("UPDATE company_owners SET company_id = NULL WHERE company_id = ?;", (company_id,))
    execute_db("DELETE FROM companies WHERE id = ?;", (company_id,))
    if actor:
        log_activity(
            actor,
            'COMPANY_DELETED',
            'companies',
            str(company_id),
            f"Deleted company {company.get('name')} ({company.get('company_number') or 'no number'})",
        )
    return company, None


def bulk_delete_company_cards(company_ids, actor=None):
    deleted = []
    errors = []
    seen = set()
    for raw in company_ids or []:
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            errors.append({'id': raw, 'message': 'Invalid company id'})
            continue
        if cid in seen:
            continue
        seen.add(cid)
        company, err = delete_company_card(cid, actor=actor)
        if err:
            errors.append({'id': cid, 'message': err})
        else:
            deleted.append({'id': cid, 'name': (company or {}).get('name')})
    return {'deleted': deleted, 'errors': errors, 'deleted_count': len(deleted)}


def bulk_update_company_cards(company_ids, data, actor=None):
    data = data or {}
    updated = []
    errors = []
    seen = set()
    allowed_status = {
        'active': 'Active',
        'dissolved': 'Dissolved',
        'liquidation': 'Liquidation',
        'closed': 'Closed',
        'strike off proposed': 'Strike Off Proposed',
    }
    status_raw = data.get('status')
    status_val = None
    if status_raw is not None and str(status_raw).strip() != '':
        status_val = allowed_status.get(str(status_raw).strip().lower())
        if not status_val:
            return None, 'Select a valid company status'

    client_id = None
    if 'client_id' in data and data.get('client_id') not in (None, ''):
        try:
            client_id = int(data.get('client_id'))
        except (TypeError, ValueError):
            return None, 'Select a valid client'
        client = query_db(
            "SELECT id FROM users WHERE id = ? AND role = 'CLIENT';",
            (client_id,),
            one=True,
        )
        if not client:
            return None, 'Client not found'

    package = None
    if 'package' in data:
        package = str(data.get('package') or '').strip() or None

    for raw in company_ids or []:
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            errors.append({'id': raw, 'message': 'Invalid company id'})
            continue
        if cid in seen:
            continue
        seen.add(cid)
        company = query_db("SELECT id, name FROM companies WHERE id = ?;", (cid,), one=True)
        if not company:
            errors.append({'id': cid, 'message': 'Company not found'})
            continue
        sets = []
        params = []
        if status_val is not None:
            sets.append('status = ?')
            params.append(status_val)
        if client_id is not None:
            sets.append('user_id = ?')
            params.append(client_id)
        if 'package' in data:
            sets.append('package = ?')
            params.append(package)
        if not sets:
            errors.append({'id': cid, 'message': 'No changes requested'})
            continue
        params.append(cid)
        execute_db(f"UPDATE companies SET {', '.join(sets)} WHERE id = ?;", params)
        updated.append({'id': cid, 'name': company.get('name')})
    if actor and updated:
        log_activity(
            actor,
            'COMPANY_BULK_UPDATED',
            'companies',
            ','.join(str(u['id']) for u in updated[:40]),
            f"Bulk updated {len(updated)} compan{'y' if len(updated) == 1 else 'ies'}",
        )
    return {'updated': updated, 'errors': errors, 'updated_count': len(updated)}, None


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


def parse_sic_codes(raw):
    if isinstance(raw, (list, tuple)):
        parts = [str(item or '') for item in raw]
    else:
        parts = re.split(r'[,;/|\n]+', str(raw or ''))
    codes = []
    seen = set()
    for part in parts:
        code = re.sub(r'[^A-Za-z0-9]', '', str(part or '').strip())
        if code.isdigit() and len(code) == 4:
            code = code.zfill(5)
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


_SIC_LABELS_CACHE = None
SIC_LABELS_GZ = (
    'H4sIAAAAAAAC/7V925LrNpLgryj8sO0ToeopkboUp5/s49tM2DNeH/c49pGiIIkukpB5qTryxv775g0gAIKs8mzsQ7frCJmJWyLvAP/3F4+bzebxi3/94vtWv5bNZaXPq0K1Kq+61Zfqc6Fu/aotC/VhvarUZajLRg/dqmj1rVvlzWmly2rVKXXqvlgjqSQghajckgYtL+qi+vxYKaZTq0o33XrVat3zL/1wVK2Q3QbI3XDJ21WRN0J8F7T3+pgXhebGfdB4Lo+t4ikwQBYA6P6q2lWjm4cbrETTlHnlgCfhcl3a/KakLZx/D2hlAfg4o2442n+f26HsBSlcmqLsW1hkFyRcgJuulQAw6V43ysPYRSfVt0oRwnHori6BZjCI4XLpSuUX3naX/iEAO6oX1eYXd2GTp3DbYO4KNjlvdZ33ZbFendrhQv3frnlb54Uaeloeh0h8d2I7kz4i6M9V3vSrG/yaX6AT3VDblnbtl7zshMwpL9s7sFDfV8xE2ySA4I4YQtbsfM4rLXu9TQP4q2474WZGVX8MZWOgtwF0kQPLMzT9WcoR2u4CwO6q1I3gLjqX1d/uQyBYHfUvt/Ii7Yeg/aaHqm/v3JhFJ5o3ZQ2nnkB2tJI/lZ/VaXWGjQFA+n1Pq/hpuN1026/yoi9fyr6ESZ91SxuB634aCrvs+2QDCN8BCaG/Ouq8PWHHPPNWCVw2T1gwR9KrL4Wbr3lD45sl/mHV/F39vfg7d0I79rPu+gfgthfV9TzmsTsGo636BEINuwSGpXXCgYRMdaBl+mFoeoBYw+HKbzfTe6uqvAcSnWpfgOuDTkCIUCdlBY2wNUOrHMaBvmBwwJ4BUkJIP+rLRTYkYZb/Pkc02c3XsgJWkfOCQuxV65NZPKazfXS3UUbYgdS0PSNYyorhpxwog2wpu6v0mrKY/w5Ar68wydZrTFys/I8hl/lxY4gZAOw2j8gw3yDLFxp2tDYnCFoSaPnPm2pgZ3HrsPlVt8/S8S4Rpm1kIary0pQ9kd3zcn/7GbaIOQhPYDucFAgSkMmVGmqCSyJwTQ6jg74uOQ3ksAn6KVuEa3mYB5m+bQXUphxqVmkgIvDvETjzgUfNc1ZtiyK3BiVZWYQn3pL/OeRtezc4bQPyo+lFyRwH2H9sIpUAWruslfx5ud+6oV6vimtePbPaQB5lwrQxsLptbuYNeu1FieYiEQ2y/x+4IUaCVfmdBdhzrquSjsRTFky/uKraKkCYVF9W5Z8wR9zXVsTNU5ZMV/2m8p4b02ljl1fSSAv4n7RsMjLs6A+7QOPxzzaPC8LLsgFrw3HLhSxRyJYo6NlBAC6wNS3Mz6NAoSWFfcXTx2tV85QBNnkTVgS6g5MKjpGRQpBxHWhXFMCZenyzKzzcazwtXQ8KOm/EYNNVNXQFU0k37xhwn/esOwE+YRnRDGdYRRR+2A9aF4RpbcPV7wMbkIDi7PRtcbhTMtzpdhPpFOxXns85lxXZxsZW5+1FJBoeh7IuKzBA1anEQVrU3QaF148l6H1YoFIOWQHWdO0rRgRFafb10JMQRChQ852agmWRMQunAX1vL3exkaPuoREQyJ5G+H2blw0SqAx37mk4ASqYyfnzmWSteAQ0UP774Zh3aB2AcmEKsc6BY1qYmLFR8R/+kA+xPcF+TyBpQvYApbG65aQYL9CtMZ6eZX8PsREA1z4z4LHsioFFmA8i7IPqfoF4GuUKsDF0U65BZOtThaZtAUIb/+fxyRlZB84O/u5N/yk2fXJuuJmsp6C50KD5hGXgT5Tg8FtzVsQ1inQ3oiYzlGPAKfbzq8qdkyUNyUSoFPp8FidC/gS/puvLfuhlrZ62j7FhN6cS9ZQsjco7jaJSUHYRFNiYG9hw6Bwa5juBLDK97CMoV13ri2pAxTBrCg3SaYaC6hU4GSPnPmWPc2fs7JpOoy7ZPGabpfGe0SEmpeAYpzzqLFlCBC1EfRIsyHSE/aaExaWTCt4x7tv5bgQfCJ/mNPpWLTtnG5Tq005eyYoDz4sdVgaMcXVRnkQmySKQQH0VQwyQtrPrhZbLicdLy9DihsNfxjkUArHNPirVcus+etJI4UPrIXZi9LkHV7JsnuF033wVyHbGiqxN1zk7avTrTtKAtEEVRmhLKME7t+lGtKblLeZpMP6N3dOrz7AKioMNjMSq9jeVv/gw3MqW/HdgPpApPWmPctwzGLi4vqxmNIjanjy2Y1sWghYTIbRe56GVvgwknvVANhZ58wI2EEnENZhdxTP8R/V8CgAlnaJcQcqpqwYnxB9/GhnIq35RaDuv+uGMIwdxCkeAt6kdzMDSWR0Yh4+Ln/YEDIgBphv8f/9qdHmj+l7EXZrFGJNdKBgnd5ODCVtg1KrOT3KeRghYGw6ZgSMIB7piqvvZ89Kr4tpY87hsTmBjtRjU8JcuWyDAcI5s2m5inFKpnFesQsSOAaNiAjyqV8UKaLuJbZqYH6r5W7fSIPRbBzyZA3/VUYRthD4DDs3JA0xiQ42Czq8WwpDs5N3h/SxQ4YFf5i9iEpMFdu/RHBm4u/T95/Kqu1JU7jadH2Uc2eGoHe/wr3ljfY1TK/bwuNX/GH8kiLsyBjKPfBfd/mq4XOicXAHpmF8kFntV4Eg+KxQBJzB0MDQBP1/zFpQCMdMuumCgy3qzMeKDf8pfxfBk273Kjch8FYW8T2JLCscLNARFw+TAI7xYobe8Uaxg90lsVnnXqfpYEWT7x4BattK6FYx0divImQb18DeWNE0vE/9dl8Z42ifb6CnSJwxUaPDLEVR6mt90a2QI8sRKdXkPhNkzbAV4xK9mEUsUYoADp6sUW+MQFQS3obpxa9QSAcuAdT/9RfE0gk42UVO0BalLYa4YoigMNlVyls5AaFaax2gES3iIbu+ociRYUYKr4drxpMdBTvZgQYFz1pViqh6ie8/jAHfFsZEP8W3Oq4qgGWR+e93di05zFD1PJlJQUliRVJB67Qi2Y4CYTr8hPGxElR/lKABc5hJyu5BggXpA56czXvRDDR5tbgOCDErz/rpsTjORTQZLKG73i/Ktr04PhIDaV5QsQCYRyBcwOvUEMo1AFrq+oRJZ0WARUCI7E+Z8hv+ifeGabhkP9CcxCzF91aqzCfFgc2bDDD34oT1asByPMgEie1QxP1YNtC7UamJdnXt2PwDZ5DF6FB19fwEx1jFkjMFBeMsmlRfyoRh0XnCVjW4vcA4KOHgd/L8dGiPOW/CLaDHzaIzpSSKphJW4kOyrb7j9ghszgkBuga9QdDBc5N8aDy14TbWgRE39ewPjRP+tHY5HmmkENaqMbgo7Az5z3YD8Mu6byyjAe4+xUwaiqAcz7yUn41n5fj445njSoL2WiYmriykpQzWZObvIRuDBENR2E7Xac84DncAgaC+WDbax2A0Im9xaBzddiVfhusOMHNUCqj0PtUxOBOcEcxdjafX5VoGV8yLMHI1IXarBNMcYGOQRTK3kwylw84J1snejmEvAk4y6ks0D2+7GNQPdFgPkIxAkJl0W2cTZLETw1y2JigJh5v7eqjH7Dc5sq1AIOaLXhvcDJCY9v04C6w4/idpZcibxvz0GtNjgWvOARFHos/gmQCNZogHqH8d6kZAGQMd23FhZoE49fMZYsJlMN86k0ujynmEywHd5JzA06E/X/DaGkG1EOQKeRm2OqtKvLtQ2yuvQ7HBaOt2iafc8O0Jdg0gwOmZ0FqnplXOnSRrlQlBqmC/RIBa95Ym6KwXoQjhFK3YlKRpesb0GCEk0QltSJGBEADMNVJloadMjjh6O0TN69lV+J3rbpQH4hpyb2RL7iUkkCySs9XcuP2OboKQLKGXTDbDjWjSY/JOSviUFB4TG9h1+vKHpLfp2QX4F8I74SnfxpcIl4faoA1fWyjgFXU+WaZLuN/GIbAHyRY19Uxbf28ihvWmxTNL97FF3/Zc3aKRRXs1P94eaqg3MoBg6tuK1bmF/hVzUIuH6nnGd9u+zy03XazspTndYOpzu/zj0nO7vHPlxduN2lG4ljGwzTcrlxzZHDemzyEKMB8NLlAaugE9MPDPCL9sFLUY5ak7CKAl8sO3W6gcgrO80injUgyQ/LEp5w/+I3DNKwPMIzHFZ8yooClckEiL5iMf6BC6rKRiSTdyyhKFmsLAr4/PkMLZXdHLLG4OlFgwNPQLDCH0l/gJAbGP9vJYsJ7db2QxVlDaz3vkJNwCiwXxVYaVbOdSTZhrEj8Cw69WfIJpZV5fNBE6GcruNZ8O27cZM5jTVHwLvg8wqLs2A1h3MfZAFZkHxEbXhWJLALUnQMu4KG19OU1Verr0sC0NsA4iZ8gSC3kUVL8+JhcFgbBuwhzjQMf7OFKKujzZyGTb0pF87S5ZwkriMbHo8JFfFYrwFC2qU70c0aJn3dgvWRZ83qOI4OajL1uo5CUf440gfo+lPzPyCpY5VFdC9DQ+HA7zqnhMR3uC2MZqvKr+ZXFZe1wNoO+GVHVdtfQcuHMmnm4T/MFKU12NhEpyxB3uCZA7/AB/h9URONQqaob2Qtt6x4vjVOsK8BHnv4vJgjXYorsaZTnbR7G4x9BUHVQAgqsG0CRqhQBXeiOZfe62l92h2hGXdqR3q0E1zQkqAG1ej9jCQGZtfzKSyNJpca5Vj9hRXTLBzTqg1BsQumpjA/LpqjPfcgQZ6xbzyFfMTrobYLWgITviQCPaEiKsd9tHzqSpV9JjCLthfb4xXuY9HhzU4TicXi6JXjJHEozAcrZGTr0CaX0mBYRzuZlTrPups99AP5iqvLGXhX7crlrlSKDzvJckeEsqig8CDUnCqziKsnCpC29f6L3S2jc8Y7Mcayz/tKvH67GJOvbOUtcq7oeVKQsUyFzNtY59YZtCTgeVEjsR1IKaGoy09JYs9zaMHE9xt0nhC7P/TsLdv9vb+oUeTW3lvy1EKkjMEG40TlC2rDWCZtVk8DDmabJ38hCyU38jbD0ZwiPG0vtmwQFF2yI4l2co2qgdosQgRaAhN/FmyzVGAfKjz8Te/66d44OPSUOSMrD8ZhwmiJocF+YC7rFmBjcqMz0mbNx0qExGm8ayCoVL2d6zggD+Pg82am+2zB43oJHFbFsulStnAvCiGWhw3won6tuwN0HRXhSkGA9hkVpo63DbuM/wDZbwpCxrpzGgDPAYnZeLjySEqKixp0jZ0ctxtPOwWt+Ska4po4spVZd6YrqKcPx6jBczs8Y1FIZbxxvgUZ5vmguUiEslqj/iPMTdetkWbn0EuvKgrel+8pnf8SxCZcrRIsMLaOjBWpL7eGUgsV30b6ptQi+WmUTu1lP4VoJiG7nO56/KSVy9mbPESFkov0ynJW/nPmKEtqe4DFtKe9aeo9SqVBFifkRcmHsP/WB3hv3LUnpK43XLuTZeYwq0mXPUUz3eez1gpKJaHpDlHTWmuAr2tzj9wH7GFpF17wIVQDQ1uNOCekrnSC8urhdY2a/yCEeIqUOdMZ95KYtFVPUhAwpnraCU9RQ2R/NJKlT6WZnBcrRPw5A1w2j1Tzz926ZoeLsHtvBdlbHZLhBHmJ2zMyHGVo2byOCjU0b4D8BStHwowrMH/lMWqDuEQ9Ndav0w5keuHQnDLdBJK4iAV1h9fzf6jmaya0aPJT1i2InNM35wjltitxxtLzIm2zGssgARi2zeJSQnO2qsqMeU2vjf/FC0w8slFs8MhmcVkGhdDUbjfZ5UFD6K7gTnyxtmIp1nJMDCSnHbApFgD4ahPqLi/BO+xuOJufWDm8dBHOQOmwAuYFh+EYISt4NiQqyw5trp8ML8IToS3DFmCSJe0q2/kNa6FFRu3FMmUravBsgUjg2MgYQHSlDLQSSV7/LWT+oGjcDORenHHvWBKKnlkF+dWkaUuJex4hQEbjnKpLI0nTXFJX/M7OuUaRoaZPRtFkFsmxTOhRyMgoOulO9BepPW9gKHLn+lj1EYCl93E88VG8pYmLtFwCcmgEKBoDqNkCIn5g3IvqQARfIuLwVuIDqHhS9dARr6wRyWV0t24jqX1uOobV2DKzac0XrT7XKLD0oSgcRHX92jL8Ng3jwvhApeY5CM/AdM/mzs7uqQjkiZRE+x39aoqWwNmttLJ0ABebIBlXXJJzXsoRJmxHjo6mb7LlCbx4BvuTmdzjymYJNGyGX1Gea8bOfF5W2By+JKP+e+7wZ+tXPKh1w4nJLvoRBw/8sQZLmdOclkYbXNZjSx+TUJriWwdUTkaWPdKlMXwCoBSyZL+om54QCkKNRNBYujEh/aOraRHx9bAfzKepqv6U8mWhkgxwJ0DSBePsARDNei1+JLQSrKUi03mcYwHEogmxj0s477n+EuWd5zdeB94nBhz+L/BtoO9ZS/dOfGNGVsckXebSGLJ8bIZJuHYLYy0LonFo1CpuWRgvfIY1FZonVSsOerH8H1JaExiPWDFE90pH/AadH9t9XC50mILVur1iDfxpkB88D9RmJ0Ob9nyRRPshdQTniIa456r+3+jKHsB2ouvwKydKi977AjhwAifQFahlYg/ibP70WIbJ+Wa/wlmGk7nFcO5DJtMYWNwSSTEfiqBuTosgZmnnyTLeDGc1NwoAcHQm0zba6s4EAbtctAL8EHRScGio5YV9VhYmma8Mr8oCh5xIGe8CznWVlG/KINg+WiITsXgVmpuvgEjvNI3U2Vny1tA/vyuWP5gnfqGltLJKktUV7Votlo0A55EwK0j6QEnZkt9YHQpzD1HsCXAAhLoJArNRhIjUDX6peXKR2lg3DSGC3L3dLGhkkbqmLdSnBNCD1hr19/t8rA7gzERwUrejeUG5UzI2wuUM8UsOg5OVrmbJHonBJQqB2COSkxjvqhuh2Kl5laU0jeqBkdffJ2tqdUpqU7BllJxE4sIvMd/ap2a8qNu2X/bymX0b0flUjryliH4rm011EeKYV/NnVmQJQ+eLJmiOprWK3mYQMr9WC4rMGPjw/bvXEoewaHZfYd16pz+hNYVHUxLgG5P/Jxz8aD8hKz/fZX/aX9xBmlPF4ZxKmVDsLZ6gTB4x3/R+kwL6j5BAI0Z9vmpyM+YeV+pVhV2xFk21s6KQ1l2KqgNciTFuPWgz4hsXimpd0ZLnA+FZOJCrwhxEgdn6Pj+0FtI2aODJHGSKZgxP0fVz5aqtYCmKLzHv101/IDke6O6PNi448cERPj2IDT+Ora86ZBjJKJeHrfj9xjbe5aupA2/onJTEOByytwIF95CsPoBHxzAlzXAK1zbS3AexOivM9upk7XRt5J1lN7AKdMV3sstG7qB0smukcmwpkcR1qaMI7g/5RYuA9X0HVT7sj5K5MWeE1ftAZntO8hYq23tjsdJho0GqzFDmfjuXTMXz23tlMrJ5bwraHsqoKSlAPu71s2FbXSgvn/PCshlsjXfEiNxCD2u7TUeL7TlbNrhPUP3Ym6dG3RjIk8unznSI6Azhk+QA3OvpBWoZO8YSo7l26XqycAZp5EEB5ifwCjBRBwap6DfDnvND1/xVvL7M2d5CWsr15A8YucK49+dvfYkQ2ZD14PEI+TcGt7KHSMP5oqF7DAEcNo7d18IPp1OZeZZBICdjtS+GxG+FwHQ09HyS0pj8YO6yK0xeR4hfGIBiJDiemt4/PgDHnH3Di+Lj/HCrxBMQoJ4wXNNt4rX5m60W/ZfFRqg0Rp0riYDod1kepELwAC3n8Dx5Xr/Xj57FLFL90DiMCHB9+lRbuZr554/P5vFSE8TpPGmulss/OajHUCLzsp/gHvhHrdXb1feOLTbKZ85N1ehfcpbRrRIYsTeDwTgdMIVOUxGYwJOUSUWXg6CPz5+I/k4ujNErfaWolPc0cBcyuKKP3ewFVigU+V3dZLOsrAzTPfrNdnBL5ya5wtk/8MNTjgVyjZ36j1HZUcpo/74zd+QxPJYB35Kg0ra6XY/NNcfeKDTo48KhjnDFoJLRYPc+fDV1nbK03LBQ/LvXY1lAQI8ZezgQsMoMLdTDnYUlHs3O5rfBgJTbnYrNGyYkIGzCXvE44EIOtncQF16WxYhI++GEbXdJnJQJQ26Hq+jxZKhVlhJsf5WylI8YkG8bOqHBfTIViNi++nIPMvMsUWCIY3BRaASUQBB5hCApnKf837rwLyn2pTQ23MDhkBrO9ehSanB4TC2oxhRd1Oz3KlXIz3oorRDXkY6ZXcJvLtxb4Dbz4jS5VQ442YzuEtIh81meg7dV6emVwwFL5uR+YN5QdCa8R5eTLFbY9l7WAyAp/v7SsLf293AiDdXLYJ5RowVsU2xtJ4dbS5QkPLXBeY87CLib3pdDwDnthNdoZYjVSpAOUTET2fUNpjmNwLjqph5PUmuGkIeTFSdPDhqA8uzCRDBN8I6oteyv070K/K+NTfUScMRy41vf9g4nnz7nj4IK5mMacYYRDpzNJIIjbiRuEQljY1k0U5ZoraNUKOXo9b8bNMaDe4hsLzmLLKljnaxjuyWLSDuI4ihNbmEP7PfYpctYKaPkZ7zoZcMLgmOBfRtjGUcledoOiw/7zwtt0Q32UTYSB9RzNuCWwOaRdZuSTO6+jxOMo0uChqYozE50pufxi62PPYhlgW82ClyJCNdLR7NuiVKsamIvbWmh3DWHC6UAgGqo+HA4SLZ2JF6y/ZdIJfFtntqa4m4dfCcvfdtSmNHrr16/i+xvjhGGObNdD9Eh0m97WO7edT6eWlq+9hWjq80mOfu3hYu+9hO0lQcprSvIiwOKbZ5bg58bV5ppbJEFJJ8J0QaKXsqD+XZ0gkgG5N+frp7YVCHqCQxHuACXkxS2CjUEl4yxfMCVkvIJhN6Uw1dtSJDo1sSBYdtZJxXU/UpSRmAymLqcywBgE266luOvyyNkhkdffy/e504jxxDZ7uo8GYfz73Qb68+LkwvpsEkjLWWGJZEwdbuQxBreshOAljGtH1LaR0Okc6i7uASkafN1AJzEoUwaeBepFJajJBljneuWSi5UAxBsjlFjJmKN3dsrv+xPKKUUjfoKyY0YZ3LPwbaKhPfkZ8cSeUsQhbhN6nzU5jSerjyc+Jm3BPuMh3ymJ4mp/gFX4nBdJWYa3n7rPhK30LEyDO+YdnfT3QamJ5Ejw5P2fvpaT+AfciiE6xJ/7R4K440H+kTbPg39Cga1TPujJXGiylruzYDwfo+HgciS0Xlz/g8FcbqKV881pas2XcZ2mNOCbaMs1LftYqyWz4wAaTkYv4TEcwXBwgbXzZ1usCCPovICXxgeidxTWmVVntatruDa1R30s94IqiD9dgV1xC3Gh8d6b2uK3tjhCpniN8inaLW5j4/cGdc6JB/LuGwqDH/m3lPAs/0QoBs0Jp1G0cAk8ZEP8MIP9b6BW+sOpUKGZdw/WrRkAfwNnRV0jXv3aO8bKZycz+y42uDZkgcuHbHtJNqyxDnLGOMYaRSMFRxEGiJ+NYFXaC54efWP4GMPQ38PJohiwnDEDYxfvC74BNv1SPtnNnH8iu/JeFReY+Ro42Mp9BWgMO5wtrtc15gbYMpng3m6GevkXDy3yTsDX9KNv1vkg2ORECXH13z6NKhNzuKgY2y4TgvQidR6HGPnM7z3koCMVQNkclUjoO5+pwX11lqPlRA06kLmHwOAVVOKUWQvQ5WRK7fWftmJxUun94iEzCCERo7eYHmTXxvvx1sMvg+5u1Fj9dr3sF4CSWoIniLfJVs0zjWG2zjyMVAzHeTt+sRw7xnq0kGOWtC0hmdfFDn3SgWV/oInphdlZTrs36ElkaqMMC8aTtpw5n/s6lirTvu+Afdm0im0TZoMNS1PtlOdtzJD6BVTsC0dINemTxQVUkGD8Gwv/+lh/6KD272XE+Fv49MeBUiYz7QFARi3nXSswmn4DPqPVcKO/UY9A4O3zzgf5pvurB31Zo30HY7DiTyECa97DfjIhYKQ7qwGfjxCDIVsNlZxwBAejwrA5qSwnxWDzkecDK8seLcBNcoPMK/gobm0Un6+1u8WoUfveHAvc8ne5ezGH/Ukzu5xG1nAKbq0TTQM+LDsTJv/JhHIZkNpK7ya7BlVzeEMsVIO6mi/Nn+SAnnEquOtDWh0VDDJgDphV46xfIeUQQQLp7yQPBVjUbh85543a4ygMkE0N7xlk8pYfKhs2j2bpw+mRoUIOMaKyMxf4GlGDTsTDJM5HYzmEPMBt78lcu4pOonzY/wlFyW6zyl6PeccTXVf1HAYQmKWGtMkQLopc3Bv1lC4lsE3kjWEtswGa8pOfxMzwLNNDI7/25xiDDObxludoaLaNvH6Gr/rqLDF+vPfx6TmZmCP3Mcshdb8xfMVeMNgPxU8Pss1MhkI4O3pZEORkBZCnJ/K6nEZRpqDaATC10h77+NILXaIFmqCqs538Zw5eWb0PjGJklpeksKi+fIecpZqFeqtFef8En6Fh8A4Zpkc3xOYyWy0OMvdMjhpvJqU0I8j5Sw4Y13NVnj0tNgutKXO0uNCiRucZ8MXmqD5aQ7ppVTPh3iOEKgjPcY/fjU3hTa5r37wYc1qcvwkdcQk3OK6rgim6LiX9l//g8QsCsY63R6aTYz1Ig1aE29vTxk/lHeygHfUj71BC0UJfk65/of/HfiXnbrdFEq0/mWDYevxi7CNPlVj1WxeTOiJRM0Rx7NIaUTJC+DOoe2naB50mYObTdBO5dY91k6PuwscjZB1sY88uHdTUkjq1k2WPJJPIpfLuw7gZwuIOaLfJjpeqEFYs6qc77AKLmVaJ/600gjS6dvqnlQzYlK/+zQQrxdbHtBbff3yDD3E2D8Cs4KbbBezSwAH4vv7Ibg/UvDwXyv+iPIWujpglYcXca88513BdoPR5A/cy0Xl/I6lpP2i6z70SIpHJJ8qRw7m+4EPqN3IY9UqPvLkyWjuVwEo3S4QcrBVTG09LgHrAlZLeA4vjZk5A4sGQEyoS8BkuFmFoECleMCOal6ZHqMD4H5rl5hEApfkAQ5dS6pgGsvN6B+LM+KHmxscQ7ckJh0fTVtNJ4KNIAPHTQlLl7YLB+XxNSAxvuYjTw7tzcV0iesh8E32Y3fPJ7EMeS3lwpnu2LsMqMngP+it1ByTAublfQFqalvdW7bDJ9BU2ARBn69b3Yl7Y6Jmf9L2fHHqU55jUygXvJqML7IXqpXw0Muy0Ey3uSKWv3MFvXecw7ymRE6RMhGnq6mfAdlILt+RvU9sany9WC/hGMK0/Hwvzbu2SR48zp5Y/WbRBIZ5QcJ1HzVoebgJZuSSOjlvz5OgqoJWmVmpj5fyyPdHDGeqhAZT1U1RwongD6+ok8bhONIJVY9Ch9SuWZl7JVzs3KBpEJTFMuUV/y2AnEbvRFJ6Bk7oV+ji97xMx/9qspfpY3uW2DVbGlEC/w41SI36Mi+FXe7txz/I6b5h0iuSl38SMPIoZnR1yQ7zAphoj5itMrTAOg5Pit1iwKwM/zZNb+g5SCWtD/0K5iPUgDWEZDjkBVsFNkvSS2Zo4DIXz51ZIDZEmnO/H2KG4duWcN5htaGPb2v2uIKdnUhz454o9mwd8eJAczb2bczMJLUgWbFAtklfJrNt05J30l15aUJP4IYeZ5L3jce369AaklAzX5TABgZNv1sayHtu7q8PJMNRmLjmXJrDgMw4xoZguatMq7ez6s7H4FDYoq4OkWfq+PTjN68PEXsmiSYOCn1aHAzAS8j9E4y5ouTtADm1R9nOkQ5efxLQyNhFpC8DjVW7cmqpEZ3vWDyloPVozQ5iLn/E30VolX0obym90x3hjPXpVDN+XO+8cnRcDTJH9Xmm4MHea7WTZpOeIrgWB78DPZKm4MZ4j+FFoIm0xtoLspdwFI6m1Xtv/dyEKP453gXSwYJlhfZLFE1fhOMFPENE4PTMMBhmz3ysXqh+zq8ibOcDtAJf/wVTggYC6BJX9Q9Cpe5n8q07xus549XTBIDIXtDAGscR5eArNSV/IaQO74R/F+KwoZ5+OXig62MHAW6GMNSubR4a+9wMA8QRNHRt3p2zWIQ4i93n0Cy1H/rRnbt4yjj0xEHWwBpv8gSlvODs/YsoNlmvif+voofgHCLZxE9m0e3nz7v7FCDunaikS5NNl4gPilXPhzSpTX/KzXfMdrpAu23niIbiyBsTeIHprpdWPrZhGk4tG0yT2QmlRqS2C2MAxM+41jmSbxjFH7uKCCQPb7FR7O3A8AIudDNLa4QGQWCvGL440gJxRtlTgbW+Ow+u4kc516YeULOmITmzS4sMNlEIsROXHg83D0WjXM9YjSOicSymB+CtYz1rdJ3micYQYW4Fo7CezJWQg1LSpXtI0YssnV4SuUT8DVd/en00KKmhUk7A689a/wawILzU5hc6SGTT7vJp7djXUo4/VesbWa/QbchSBYJ0vSAgMrwMuDNQQHLPEulfXk7LPf0aD5+Wb6gQ9FZT9YdwpOtb5BWqR6JRySfbG0DhlEksxiASHj6o8brA/SRSxsijSY1n6T+4Ht+9W+8HOW8MiFwZNv+Rq+Lj2COfYMg5Pq4d9pn4MiwkMca+S5gWTfqPgufTS/nh1eZ7Zd8gumRovkGrTaqYxdM9VmqAvppb879/Ph4eBt+tD5BfLfsmxmyFeZdKycC8jKPS4KJLCyUBBR4Puli4FMZfFFVT94SkL7iWy4GchB+pXcTrBOOz19OD4fjr/NN1dJ8Cqvj8AaiJbHQrvH6A+hss4lA27dgHKGDsBHKHHM7giDMBwHjVKh5jDyce+Y99CBZC7NOiydaAu3mpIjJnvsBLb+3rbHILsP4dhFI5qq/ojkHwluB1JfqDe8tTNlS7YWo16ux9tA2seTg3tJpb/btYBMj7zUGNdVKIYuUJG2DMhZaE5mAVCdp1KUwuzNuubTwe0XqzNEcf95cPvLvA6aB+IT/DrYI13YGkFsncsBlhHxp6qwm0nLLl0y+w1fk4+dG3AvKEFUdftYnWKcAfmc/UfxgPo9md4TaEyPFI22pxw9cMxqBSnyPmkoxdGEM6Tk0+QwHpndHEIx6o1FcRuAlkNICwkldWoXZPNSOVzAi0OcPoBNDHRy404AqagGc73J8Ynuf817GFwgh+WUdw8pBI5eOyxO/XXHVOuSH3c45nxbbOYU7foX8W9OES+ifXqIjdSI/6I6zIX4vUibykxSXN0Pb8fcl6pCh9r56NOXoN8qYFlNoKdYzPnX3NgavCTu1c0BuipcNIxEiPtxhYyIfVKJVOlNDaeMYAwSdTKAJKohVUbUHhbJKlBjoc/ODIjRiMw6uosU6GRw9CBFFXaTv6oJueeO3zaq7cRPxrt2JSLhzbxcIjSwice9PfPLRfHbh8EahHnq/tOjNYcibwB+vJX42J78/BN0zjFdv8hd6t2OHyfK5V615x1iuMUML89ZEpaNEv8Xg+TkXjFjRw9ByYLmNpK5Xv0hP23gcgmFuzgMd20mwAhsTE0+l506DVo6JASPQd0WCxnTS/bXE2k86KvTdWe+FGb/oDX0GsuZ7isuLUwBU+Rusus9HSfunxogj38jPQbtIUJriiEqs+cng5RP33+f1cXwty+QhPEiJDHozkbCIv5QSI/StF/ZITQFYJi91fVf2ZJSEBLKpqQTTV1fddgqzIWxTIaD7vhSLbH/QLNOCVH8NW8XWpa3Ow+cVpHiP8dzUlYUP9EHsSGZiO/ldHt2iEXYhMZdSK3xhCN//MdaiLZLNtrFl9B4nXcSOGb78hNTQWKAsMtRWVeWFvoc1JZrFhkRvsxV0dXwCnz3OVBTMjd1ZyF34JOm7vhyT7cK3SWe+9UKwyaSL6Kdasl0SEI3efOTwm7yiwWcw7C94GXXxHSUAD95EtRcfx67oJ6oLE5SdjyL3pdbydZPJMxqZfPFqxFiKJjrbs2fx/Vs+PsL+5am9P3xw/WrzZoN8scC98JPtWW7+AN1636WXD62Dl9Pfx7c4GSOVVLAyzkO0KgkA2dS+3vkKKE734aimQm3vlkwtuUSZPP4ZJAfN6sC6ds65dh+X5OVs6Btr2dOjZyV0Y4TMy+Blotf/2aAlgv5kX9I0aROkCNKfjXw1mCIwzrBQ2WP6mK2UTMJZE7oy9f8HyllsfWD3WwyMtqjEylBEyNOMJ9mLjIs/vsFaMJC1Hzmx8MX/+b/5Xw7akpYAAA=='
)


def sic_code_labels():
    global _SIC_LABELS_CACHE
    if _SIC_LABELS_CACHE is None:
        labels = {}
        blob = str(SIC_LABELS_GZ or '').strip()
        if blob and blob != 'SIC_LABELS_GZ_PLACEHOLDER':
            try:
                labels = json.loads(gzip.decompress(base64.b64decode(blob)))
            except Exception:
                labels = {}
        _SIC_LABELS_CACHE = labels if isinstance(labels, dict) else {}
    return _SIC_LABELS_CACHE


def sic_activities_from_codes(raw):
    labels = sic_code_labels()
    items = []
    for code in parse_sic_codes(raw):
        lookup = code.zfill(5) if code.isdigit() else code
        items.append({
            'code': lookup,
            'description': str(labels.get(lookup) or labels.get(code) or '').strip(),
        })
    return items


def companies_house_email_from_data(data):
    if not isinstance(data, dict):
        return ''
    for key in ('registered_email_address', 'registered_email', 'email', 'contact_email'):
        value = str(data.get(key) or '').strip().lower()
        if is_usable_form_email(value):
            return value
    return ''


def normalize_registered_email(value):
    text = str(value or '').strip().lower()
    if not text:
        return '', None
    if text.endswith('@brixen-pending.local') or text.endswith('.local'):
        return None, 'Enter a real company or personal email (Gmail, Outlook, Hotmail, etc.)'
    if not is_usable_form_email(text) or not STAFF_EMAIL_RE.match(text):
        return None, 'Enter a valid registered email'
    # Reject obvious placeholders even if they look like emails.
    try:
        import compliance_alerts as _ca
        if not _ca.is_valid_client_notify_email(text):
            return None, 'Enter a real company or personal email (Gmail, Outlook, Hotmail, etc.)'
    except Exception:
        pass
    return text, None


def is_placeholder_company_email(value):
    text = str(value or '').strip().lower()
    return (not text) or text.endswith('@brixen-pending.local')


def company_notify_email_locked(company):
    try:
        return int((company or {}).get('registered_email_locked') or 0) == 1
    except (TypeError, ValueError):
        return False


def company_list_order_sql():
    # Newest formed first (incorporation date), then newest CRM card, then id.
    return (
        "CASE "
        "WHEN inc_date IS NOT NULL AND length(trim(inc_date)) >= 10 THEN date(inc_date) "
        "WHEN created_at IS NOT NULL THEN date(created_at) "
        "ELSE date('1970-01-01') END DESC, "
        "id DESC"
    )


def client_account_email(user_id):
    if not user_id:
        return ''
    row = query_db("SELECT email FROM users WHERE id = ?;", (user_id,), one=True)
    email = str((row or {}).get('email') or '').strip()
    return email if is_usable_form_email(email) else ''


def registered_email_for_client(client, extras=None):
    extras = extras if isinstance(extras, dict) else {}
    for value in (
        extras.get('registered_email'),
        extras.get('new_client_email'),
        extras.get('client_email'),
        (client or {}).get('email'),
    ):
        email = str(value or '').strip()
        if is_usable_form_email(email):
            return email
    return client_account_email((client or {}).get('id'))


def resolve_company_registered_email(company):
    stored = str((company or {}).get('registered_email') or '').strip()
    if is_usable_form_email(stored):
        return stored
    form_email = company_form_email((company or {}).get('id'))
    if is_usable_form_email(form_email):
        return form_email
    user_id = (company or {}).get('user_id')
    name = (company or {}).get('name')
    if user_id and name:
        extras = query_db(
            """
            SELECT owner_form_email, checkout_form_json
            FROM orders
            WHERE user_id = ?
            ORDER BY id DESC;
            """,
            (user_id,),
        ) or []
        for row in extras:
            raw = row.get('checkout_form_json')
            fields = []
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        fields = parsed
                except (TypeError, ValueError):
                    fields = []
            if not fields:
                continue
            if not order_matches_company_name(company_name_from_checkout_fields(fields), name):
                continue
            profile = owner_profile_from_fields(fields)
            if is_usable_form_email(profile.get('form_email')):
                return str(profile.get('form_email') or '').strip()
            if is_usable_form_email(row.get('owner_form_email')):
                return str(row.get('owner_form_email') or '').strip()
    client_email = str((company or {}).get('client_email') or '').strip()
    if is_usable_form_email(client_email):
        return client_email
    return client_account_email(user_id)


def persist_company_registered_email(company, ch_email=None):
    company_id = (company or {}).get('id')
    if not company_id:
        return False
    email = str(ch_email or company.get('registered_email') or '').strip().lower()
    if not is_usable_form_email(email):
        email = str(resolve_company_registered_email(company) or '').strip().lower()
    if not is_usable_form_email(email):
        return False
    current = str((company or {}).get('registered_email') or '').strip()
    if current.lower() == email:
        return False
    execute_db("UPDATE companies SET registered_email = ? WHERE id = ?;", (email, company_id))
    company['registered_email'] = email
    return True


def public_client_company(row, deadlines=None, for_staff=False, resolve_owner=False):
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
        'identity_verified': str(src.get('identity_verified') or 'Not started').strip() or 'Not started',
        'psc_verified': str(src.get('psc_verified') or 'Not started').strip() or 'Not started',
        'sic_codes': str(src.get('sic_codes') or '').strip(),
        'sic_activities': sic_activities_from_codes(src.get('sic_codes')),
        'registered_email': str(src.get('registered_email') or '').strip(),
        'whatsapp_number': str(src.get('whatsapp_number') or '').strip(),
        'deadlines': list(deadlines or []),
        'b2b_id': str(src.get('b2b_id') or '').strip(),
        'client_type': str(src.get('client_type') or 'B2B').strip() or 'B2B',
    }
    if is_placeholder_company_email(payload['registered_email']):
        payload['registered_email'] = ''
        payload['registered_email_locked'] = False
        payload['business_email_verified'] = False
    else:
        payload['registered_email_locked'] = company_notify_email_locked(src)
        payload['business_email_verified'] = int(src.get('business_email_verified') or 0) == 1
    payload['business_email_verified_at'] = src.get('business_email_verified_at')
    payload['business_email_source'] = str(src.get('business_email_source') or '').strip()
    payload['business_email_updated_at'] = src.get('business_email_updated_at')
    if not payload['registered_email'] and not payload['registered_email_locked']:
        suggestion = resolve_company_registered_email(src) or ''
        if suggestion and not is_placeholder_company_email(suggestion):
            payload['registered_email'] = suggestion
            # Suggestions stay editable until staff saves once.
    directors = []
    if (
        resolve_owner
        and looks_like_uk_company_number(src.get('company_number'))
        and not is_pending_company_number(src.get('company_number'))
    ):
        live_directors = fetch_companies_house_active_directors(src.get('company_number'))
        if live_directors:
            directors = sync_company_directors_from_list(src.get('id'), live_directors)
    if not directors:
        directors = company_directors_list(src)
    if not directors:
        single = registered_company_owner_name(src, resolve_owner=False)
        if single:
            directors = [single]
    payload['directors'] = directors
    display_directors = ' · '.join(directors)
    payload['owner_name'] = display_directors
    if display_directors:
        payload['director'] = display_directors
    attention = company_attention_issues(src)
    payload['attention'] = attention
    payload['needs_attention'] = bool(attention)
    payload['accounts_next_due'] = companies_house_iso_date(src.get('accounts_next_due'))
    payload['confirmation_next_due'] = companies_house_iso_date(src.get('confirmation_next_due'))
    if for_staff:
        payload['authentication_code'] = str(src.get('authentication_code') or '').strip()
        payload['activation_code'] = str(src.get('activation_code') or '').strip()
        payload['identity_verified_at'] = src.get('identity_verified_at')
        payload['psc_verified_at'] = src.get('psc_verified_at')
    return payload


ACCOUNTS_TYPES = ('Micro-entity', 'Small', 'Dormant', 'Abridged', 'Full')
ACCOUNTS_STATUSES = ('Not started', 'Requested', 'In progress', 'Ready to file', 'Filed', 'Overdue')
IDENTITY_STATUSES = ('Not started', 'In progress', 'Verified', 'Failed')
PSC_STATUSES = ('Not started', 'In progress', 'Verified', 'Update required')
BOOK_CATEGORIES = (
    'Turnover',
    'Other income',
    'Cost of sales',
    'Staff costs',
    'Other operating charges',
    'Tax',
    'Dividends / drawings',
    'Bank charges',
    'Director loan',
    'VAT',
    'Other',
)
BOOK_OUTFLOW_CATEGORIES = frozenset({
    'Cost of sales',
    'Staff costs',
    'Other operating charges',
    'Tax',
    'Dividends / drawings',
    'Bank charges',
    'VAT',
})
BOOK_SOURCES = ('bank_statement', 'manual')
UK_POSTCODE_RE = re.compile(r'\b[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}\b', re.I)
COMPANY_STAFF_SELECT = (
    "id, name, company_number, status, inc_date, director, reg_office, package, account_status, "
    "created_at, user_id, utr_number, authentication_code, activation_code, "
    "identity_verified, identity_verified_at, psc_verified, psc_verified_at, "
    "sic_codes, registered_email, registered_email_locked, business_email_verified, "
    "business_email_verified_by, business_email_verified_at, business_email_source, business_email_updated_at, "
    "whatsapp_number, accounts_next_due, accounts_overdue, "
    "confirmation_next_due, confirmation_overdue, ch_attention_json, "
    "ch_alert_fingerprint, ch_alert_sent_at, compliance_last_notification_at, compliance_last_notification_id, "
    "b2b_id, client_type"
)
COMPANY_LIST_SELECT = (
    "id, name, company_number, status, inc_date, director, reg_office, package, account_status, "
    "created_at, user_id, utr_number, identity_verified, psc_verified, "
    "sic_codes, registered_email, registered_email_locked, business_email_verified, "
    "business_email_verified_by, business_email_verified_at, business_email_source, business_email_updated_at, "
    "whatsapp_number, accounts_next_due, accounts_overdue, "
    "confirmation_next_due, confirmation_overdue, ch_attention_json, "
    "b2b_id, client_type"
)


def normalize_pending_company_name(name):
    text = re.sub(r'\s+', ' ', str(name or '').strip())
    if len(text) < 2 or len(text) > 160 or text.lower() in ('united kingdom', 'uk', 'none', 'n/a'):
        return None, 'Enter the company name the customer wants to apply for'
    return text, None


def rename_pending_company(company_id, name, companies_house_number=None):
    current = query_db(f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;", (company_id,), one=True)
    if not current:
        return None, 'Company not found'
    if not is_pending_company_number(current.get('company_number')):
        return None, 'This company is already registered at Companies House. File a formal name change instead.'
    text, error = normalize_pending_company_name(name)
    if error:
        return None, error
    if str(current.get('name') or '').strip().lower() != text.lower():
        clash = match_company_for_client(current.get('user_id'), text)
        if clash and int(clash) != int(company_id):
            return None, 'This client already has a company with that name'
    execute_db(
        "UPDATE companies SET name = ?, ch_checked_at = NULL WHERE id = ?;",
        (text, company_id),
    )
    sync_company_name_onto_linked_orders(company_id, text)
    ch_number = normalize_company_number(companies_house_number)
    if ch_number and looks_like_uk_company_number(ch_number):
        profile, _profile_err = fetch_companies_house_profile(ch_number)
        payload = profile or {'company_number': ch_number, 'name': text}
        if apply_companies_house_profile_to_company(company_id, payload):
            notify_company_registered(company_id)
    else:
        company_row = query_db(
            "SELECT id, name, company_number, director, user_id, ch_checked_at FROM companies WHERE id = ?;",
            (company_id,),
            one=True,
        )
        if company_row:
            sync_pending_company_from_companies_house(company_row)
    return query_db(f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;", (company_id,), one=True), None


def relink_company_from_companies_house(company_id, name, companies_house_number):
    current = query_db(f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;", (company_id,), one=True)
    if not current:
        return None, 'Company not found'
    if is_pending_company_number(current.get('company_number')):
        return None, 'Use Save name on pending companies instead.'
    ch_number = normalize_company_number(companies_house_number)
    if not ch_number or not looks_like_uk_company_number(ch_number):
        return None, 'Select the official Companies House record first.'
    text, error = normalize_pending_company_name(name)
    if error:
        return None, error
    profile, profile_err = fetch_companies_house_profile(ch_number)
    if not profile:
        return None, profile_err or 'Could not load that Companies House record.'
    if normalize_company_name_key(text) != normalize_company_name_key(profile.get('name')):
        return None, 'The name must match the selected Companies House record.'
    if not apply_companies_house_profile_to_company(company_id, profile):
        return None, 'That Companies House number is already linked to another company card.'
    company_row = query_db(f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;", (company_id,), one=True)
    if company_row:
        sync_registered_company_from_companies_house(company_row)
        sync_company_name_onto_linked_orders(company_id, company_row.get('name'))
        company_row = query_db(f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;", (company_id,), one=True)
    return company_row, None


def update_company_compliance(company_id, data, actor=None):
    current = query_db(f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;", (company_id,), one=True)
    if not current:
        return None, 'Company not found'
    data = data or {}
    sets = []
    params = []
    now = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')
    import compliance_alerts as _ca
    if data.get('unverify_business_email'):
        _, email_err = _ca.unverify_company_business_email(company_id, actor=actor, source='staff_unverify')
        if email_err:
            return None, email_err
    elif data.get('verify_business_email') and 'registered_email' not in data:
        _, email_err = _ca.verify_company_business_email(company_id, actor=actor, source='staff_verify')
        if email_err:
            return None, email_err
    elif 'registered_email' in data:
        _, email_err = _ca.set_company_business_email(
            company_id,
            data.get('registered_email'),
            actor=actor,
            source='staff',
            verify=bool(data.get('verify_business_email')),
        )
        if email_err:
            return None, email_err
    if 'utr_number' in data:
        utr, utr_err = normalize_compliance_field(data.get('utr_number'), 15)
        if utr_err:
            return None, 'UTR number: ' + utr_err
        sets.append('utr_number = ?')
        params.append(utr or None)
    if 'authentication_code' in data:
        auth, auth_err = normalize_compliance_field(data.get('authentication_code'), 12)
        if auth_err:
            return None, 'Authentication code: ' + auth_err
        sets.append('authentication_code = ?')
        params.append(auth or None)
    if 'activation_code' in data:
        activation, activation_err = normalize_compliance_field(data.get('activation_code'), 40)
        if activation_err:
            return None, 'Personal 11 dijits Code: ' + activation_err
        sets.append('activation_code = ?')
        params.append(activation or None)
    if 'identity_verified' in data:
        identity = str(data.get('identity_verified') or '').strip() or 'Not started'
        if identity not in IDENTITY_STATUSES:
            return None, 'Select a valid identity verification status'
        sets.append('identity_verified = ?')
        params.append(identity)
        if identity == 'Verified':
            if str(current.get('identity_verified') or '') != 'Verified':
                sets.append('identity_verified_at = ?')
                params.append(now)
        else:
            sets.append('identity_verified_at = ?')
            params.append(None)
    if 'psc_verified' in data:
        psc = str(data.get('psc_verified') or '').strip() or 'Not started'
        if psc not in PSC_STATUSES:
            return None, 'Select a valid PSC verification status'
        sets.append('psc_verified = ?')
        params.append(psc)
        if psc == 'Verified':
            if str(current.get('psc_verified') or '') != 'Verified':
                sets.append('psc_verified_at = ?')
                params.append(now)
        else:
            sets.append('psc_verified_at = ?')
            params.append(None)
    if 'whatsapp_number' in data:
        existing_wa = str(current.get('whatsapp_number') or '').strip()
        if existing_wa:
            incoming = str(data.get('whatsapp_number') or '').strip()
            if incoming and incoming != existing_wa:
                return None, 'WhatsApp number is locked after the first save and cannot be changed.'
            # Ignore blank / same value once locked.
        else:
            wa, wa_err = normalize_whatsapp_number(data.get('whatsapp_number'))
            if wa_err:
                return None, wa_err
            if not wa:
                return None, 'Enter a WhatsApp number'
            sets.append('whatsapp_number = ?')
            params.append(wa)
    if sets:
        params.append(company_id)
        execute_db(f"UPDATE companies SET {', '.join(sets)} WHERE id = ?;", params)
    updated = query_db(f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;", (company_id,), one=True)
    events = []
    if updated and 'identity_verified' in data:
        if str(updated.get('identity_verified') or '') == 'Verified' and str(current.get('identity_verified') or '') != 'Verified':
            events.append('identity')
    if updated and 'psc_verified' in data:
        if str(updated.get('psc_verified') or '') == 'Verified' and str(current.get('psc_verified') or '') != 'Verified':
            events.append('psc')
    if events:
        notify_compliance_activity(updated, events)
    return updated, None


def is_bank_statement_document(doc):
    category = str((doc or {}).get('category') or '').lower()
    name = str((doc or {}).get('name') or '').lower()
    return 'bank statement' in category or 'bank statement' in name


def company_accounts_address(company):
    record = company_registered_office_record((company or {}).get('id'), company)
    if record and record.get('address'):
        postcode = str(record.get('postcode') or '').strip()
        address = str(record.get('address') or '').strip()
        if postcode and postcode.lower() not in address.lower():
            address = f"{address}, {postcode}"
        return address
    return registered_address_from_company_row(company) or ''


def parse_book_amount(data):
    raw = str((data or {}).get('amount') or '').strip().replace(',', '').replace('£', '')
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None, 'Enter a valid amount'
    amount = round(amount, 2)
    if amount == 0:
        return None, 'Amount cannot be zero'
    direction = str((data or {}).get('direction') or '').strip().lower()
    category = str((data or {}).get('category') or 'Other').strip()
    if direction in ('out', 'expense', 'debit', 'payment'):
        amount = -abs(amount)
    elif direction in ('in', 'income', 'credit', 'receipt'):
        amount = abs(amount)
    elif amount > 0 and category in BOOK_OUTFLOW_CATEGORIES:
        amount = -amount
    return amount, None


def public_book_entry(row):
    if not row:
        return None
    amount = round(float(row.get('amount') or 0), 2)
    return {
        'id': row.get('id'),
        'company_id': row.get('company_id'),
        'period_end': row.get('period_end'),
        'entry_date': row.get('entry_date'),
        'description': row.get('description') or '',
        'amount': amount,
        'category': row.get('category') or 'Other',
        'source': row.get('source') or 'manual',
        'created_at': row.get('created_at'),
    }


def book_entry_totals(entries):
    by_category = {name: 0.0 for name in BOOK_CATEGORIES}
    inflows = 0.0
    outflows = 0.0
    for item in entries or []:
        amount = float((item or {}).get('amount') or 0)
        category = str((item or {}).get('category') or 'Other').strip() or 'Other'
        if category not in by_category:
            by_category[category] = 0.0
        by_category[category] = round(by_category[category] + amount, 2)
        if amount >= 0:
            inflows += amount
        else:
            outflows += amount
    return {
        'by_category': [{'category': name, 'amount': round(by_category.get(name, 0.0), 2)} for name in BOOK_CATEGORIES if by_category.get(name)],
        'inflows': round(inflows, 2),
        'outflows': round(outflows, 2),
        'net': round(inflows + outflows, 2),
        'count': len(entries or []),
    }


def accountancy_readiness(company, current=None, book_count=0, statement_count=0, for_client=False):
    issues = []
    address = company_accounts_address(company)
    address_ok = bool(address)
    has_postcode = bool(UK_POSTCODE_RE.search(address or ''))
    if not address_ok:
        issues.append({
            'code': 'address',
            'severity': 'block',
            'title': 'Registered office missing',
            'detail': 'Companies Act 2006 s.86 requires a UK registered office that can receive documents.',
        })
    elif not has_postcode:
        issues.append({
            'code': 'address_postcode',
            'severity': 'warn',
            'title': 'Registered office incomplete',
            'detail': 'Add the full UK address with postcode before filing at Companies House.',
        })
    identity = str((company or {}).get('identity_verified') or 'Not started').strip() or 'Not started'
    if identity not in IDENTITY_STATUSES:
        identity = 'Not started'
    if identity != 'Verified':
        issues.append({
            'code': 'identity',
            'severity': 'block',
            'title': 'Identity verification outstanding',
            'detail': 'Directors must complete Companies House identity verification (ECCTA 2023) before the company can file.',
        })
    psc = str((company or {}).get('psc_verified') or 'Not started').strip() or 'Not started'
    if psc not in PSC_STATUSES:
        psc = 'Not started'
    if psc != 'Verified':
        issues.append({
            'code': 'psc',
            'severity': 'block',
            'title': 'PSC register not verified',
            'detail': 'People with Significant Control must be confirmed on the PSC register (Companies Act 2006 Part 21A).',
        })
    has_auth = bool(str((company or {}).get('authentication_code') or '').strip())
    has_personal = bool(str((company or {}).get('activation_code') or '').strip())
    has_utr = bool(str((company or {}).get('utr_number') or '').strip())
    if not for_client:
        if not has_auth:
            issues.append({
                'code': 'auth_code',
                'severity': 'block',
                'title': 'Companies House authentication code missing',
                'detail': 'The company authentication code is required to file accounts online at Companies House.',
            })
        if not has_personal:
            issues.append({
                'code': 'personal_code',
                'severity': 'warn',
                'title': 'Personal 11 dijits Code missing',
                'detail': 'Store the WebFiling personal code used to access this company online.',
            })
    if not has_utr:
        issues.append({
            'code': 'utr',
            'severity': 'warn',
            'title': 'UTR missing',
            'detail': 'HMRC still needs the Unique Taxpayer Reference for the Corporation Tax return after Companies House accounts are filed.',
        })
    if not str((company or {}).get('director') or '').strip():
        issues.append({
            'code': 'director',
            'severity': 'warn',
            'title': 'Director not named',
            'detail': 'Yearly accounts must be approved by a director before they are filed.',
        })
    if (current or {}).get('status') == 'Overdue':
        issues.append({
            'code': 'overdue',
            'severity': 'block',
            'title': 'Accounts overdue',
            'detail': 'Private company accounts are due 9 months after the year end (Companies Act 2006 s.442). Late filing penalties apply.',
        })
    if int(statement_count or 0) < 1:
        issues.append({
            'code': 'statements',
            'severity': 'warn',
            'title': 'No bank statements',
            'detail': 'Upload the period bank statements, then post each line into the cash book before preparing micro-entity accounts.',
        })
    if int(book_count or 0) < 1:
        issues.append({
            'code': 'books',
            'severity': 'warn',
            'title': 'No bookkeeping entries',
            'detail': 'Post bank statement lines (turnover, costs, drawings) and review FRS 105 totals before filing.',
        })
    if for_client:
        issues = [item for item in issues if item.get('code') in ('address', 'address_postcode', 'identity', 'psc', 'overdue', 'utr')]
    blocking = [item for item in issues if item.get('severity') == 'block']
    payload = {
        'address': {'ok': address_ok and has_postcode, 'value': address},
        'identity': {'status': identity, 'ok': identity == 'Verified'},
        'psc': {'status': psc, 'ok': psc == 'Verified'},
        'utr': {'ok': has_utr},
        'issues': issues,
        'blocking_count': len(blocking),
        'issue_count': len(issues),
        'can_file': len(blocking) == 0,
    }
    if not for_client:
        payload['authentication_code'] = {'ok': has_auth}
        payload['personal_code'] = {'ok': has_personal}
    return payload


def accountancy_statement_counts(company_ids):
    counts = {}
    ids = [int(cid) for cid in (company_ids or []) if cid]
    if not ids:
        return counts
    placeholders = ','.join('?' for _ in ids)
    rows = query_db(
        f"""
        SELECT company_id, name, category
        FROM documents
        WHERE company_id IN ({placeholders});
        """,
        ids,
    ) or []
    for row in rows:
        if not is_bank_statement_document(row):
            continue
        cid = int(row['company_id'])
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def accountancy_book_counts(company_ids):
    counts = {}
    ids = [int(cid) for cid in (company_ids or []) if cid]
    if not ids:
        return counts
    placeholders = ','.join('?' for _ in ids)
    rows = query_db(
        f"""
        SELECT company_id, period_end, COUNT(*) AS n
        FROM company_book_entries
        WHERE company_id IN ({placeholders})
        GROUP BY company_id, period_end;
        """,
        ids,
    ) or []
    for row in rows:
        counts[(int(row['company_id']), str(row.get('period_end') or ''))] = int(row.get('n') or 0)
    return counts


def public_bank_statement(doc):
    if not doc:
        return None
    return {
        'id': doc.get('id'),
        'name': doc.get('name'),
        'category': doc.get('category'),
        'file_type': doc.get('file_type'),
        'file_size': doc.get('file_size'),
        'created_at': doc.get('created_at'),
    }


def list_company_book_entries(company_id, period_end):
    rows = query_db(
        """
        SELECT * FROM company_book_entries
        WHERE company_id = ? AND period_end = ?
        ORDER BY entry_date ASC, id ASC;
        """,
        (company_id, period_end),
    ) or []
    return [public_book_entry(row) for row in rows]


def insert_company_book_entry(company_id, data):
    company = query_db("SELECT id, user_id, inc_date, company_number FROM companies WHERE id = ?;", (company_id,), one=True)
    if not company:
        return None, 'Company not found'
    current = current_accounts_period(
        company.get('inc_date'),
        query_db("SELECT * FROM company_accounts WHERE company_id = ? ORDER BY period_end DESC;", (company_id,)) or [],
    )
    period_end = parse_iso_date(data.get('period_end')) or parse_iso_date(current.get('period_end'))
    entry_date = parse_iso_date(data.get('entry_date')) or datetime.date.today()
    if not period_end:
        return None, 'Enter the accounting year end'
    description = str(data.get('description') or '').strip()
    if not description:
        return None, 'Enter a description from the bank statement'
    if len(description) > 200:
        return None, 'Keep the description to 200 characters'
    category = str(data.get('category') or 'Other').strip() or 'Other'
    if category not in BOOK_CATEGORIES:
        return None, 'Select a valid accounts heading'
    source = str(data.get('source') or 'manual').strip() or 'manual'
    if source not in BOOK_SOURCES:
        source = 'manual'
    amount, amount_err = parse_book_amount(data)
    if amount_err:
        return None, amount_err
    entry_id = execute_db(
        """
        INSERT INTO company_book_entries (
            company_id, period_end, entry_date, description, amount, category, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (company_id, period_end.isoformat(), entry_date.isoformat(), description, amount, category, source),
    )
    return query_db("SELECT * FROM company_book_entries WHERE id = ?;", (entry_id,), one=True), None


def company_books_workspace(company_id, period_end=None, for_client=False):
    company = query_db(
        f"""
        SELECT c.id, c.name, c.company_number, c.inc_date, c.user_id, c.status, c.director,
               c.reg_office, c.utr_number, c.authentication_code, c.activation_code,
               c.identity_verified, c.psc_verified,
               u.full_name as client_name, u.email as client_email
        FROM companies c
        LEFT JOIN users u ON c.user_id = u.id
        WHERE c.id = ?;
        """,
        (company_id,),
        one=True,
    )
    if not company:
        return None, 'Company not found'
    if not for_client:
        sync_registered_company_from_companies_house(company)
        latest = query_db("SELECT director FROM companies WHERE id = ?;", (company_id,), one=True) or {}
        if latest.get('director'):
            company['director'] = latest.get('director')
    filings = query_db(
        "SELECT * FROM company_accounts WHERE company_id = ? ORDER BY period_end DESC, id DESC;",
        (company_id,),
    ) or []
    current = current_accounts_period(company.get('inc_date'), filings)
    period = parse_iso_date(period_end) or parse_iso_date(current.get('period_end'))
    period_value = period.isoformat() if period else str(current.get('period_end') or '')
    entries = list_company_book_entries(company_id, period_value) if period_value else []
    statements = [
        public_bank_statement(doc)
        for doc in (documents_for_company_portfolio(company_id) or [])
        if is_bank_statement_document(doc)
    ]
    readiness = accountancy_readiness(
        company,
        current=current,
        book_count=len(entries),
        statement_count=len(statements),
        for_client=for_client,
    )
    people = accountancy_people_fields(company, for_client=for_client)
    return {
        'company': {
            'id': company['id'],
            'name': company.get('name'),
            'company_number': company.get('company_number'),
            'director': people['director'],
            'director_email': people['director_email'],
            'owner_name': people['owner_name'],
            'client_name': people['client_name'],
            'client_email': people['client_email'],
            'client_id': company.get('user_id'),
        },
        'current': current,
        'period_end': period_value,
        'readiness': readiness,
        'entries': entries,
        'totals': book_entry_totals(entries),
        'statements': statements,
        'book_categories': list(BOOK_CATEGORIES),
        'identity_statuses': list(IDENTITY_STATUSES),
        'psc_statuses': list(PSC_STATUSES),
    }, None


def parse_iso_date(value):
    text = str(value or '').strip()[:10]
    if len(text) < 10:
        return None
    try:
        return datetime.datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def last_day_of_month(year, month):
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


def add_calendar_months(value, months):
    month_index = value.month - 1 + int(months)
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return last_day_of_month(year, month)


def first_accounts_year_end(inc_date):
    year_end = last_day_of_month(inc_date.year, inc_date.month)
    if year_end <= inc_date:
        year_end = last_day_of_month(inc_date.year + 1, inc_date.month)
    return year_end


def accounts_periods_for_company(inc_date, today=None):
    inc = parse_iso_date(inc_date) or datetime.date.today()
    today = today or datetime.date.today()
    year_end = first_accounts_year_end(inc)
    start = inc
    first = True
    periods = []
    while year_end.year <= today.year + 1 and len(periods) < 12:
        due = add_calendar_months(inc, 21) if first else add_calendar_months(year_end, 9)
        periods.append({
            'period_start': start.isoformat(),
            'period_end': year_end.isoformat(),
            'due_date': due.isoformat(),
        })
        start = year_end + datetime.timedelta(days=1)
        year_end = last_day_of_month(year_end.year + 1, year_end.month)
        first = False
        if due < today - datetime.timedelta(days=400) and len(periods) > 1:
            continue
    return periods


def public_accounts_filing(row, for_client=False):
    if not row:
        return None
    src = dict(row)
    payload = {
        'id': src.get('id'),
        'company_id': src.get('company_id'),
        'period_start': src.get('period_start'),
        'period_end': src.get('period_end'),
        'due_date': src.get('due_date'),
        'accounts_type': src.get('accounts_type') or 'Micro-entity',
        'status': src.get('status') or 'Not started',
        'confirmation_number': src.get('confirmation_number') or '',
        'filed_at': src.get('filed_at'),
    }
    if not for_client:
        payload['notes'] = src.get('notes') or ''
        payload['user_id'] = src.get('user_id')
    return payload


def accounts_status_for_period(stored_status, due_date, today=None):
    today = today or datetime.date.today()
    status = str(stored_status or 'Not started').strip() or 'Not started'
    if status == 'Filed':
        return 'Filed'
    due = parse_iso_date(due_date)
    if due and due < today:
        return 'Overdue'
    return status if status in ACCOUNTS_STATUSES else 'Not started'


def current_accounts_period(inc_date, filings, today=None):
    today = today or datetime.date.today()
    stored = {str(row.get('period_end')): row for row in (filings or [])}
    for period in accounts_periods_for_company(inc_date, today):
        row = stored.get(period['period_end'])
        status = accounts_status_for_period((row or {}).get('status'), (row or {}).get('due_date') or period['due_date'], today)
        if status != 'Filed':
            merged = dict(period)
            if row:
                merged.update(public_accounts_filing(row) or {})
                merged['period_start'] = row.get('period_start') or period['period_start']
                merged['period_end'] = row.get('period_end') or period['period_end']
                merged['due_date'] = row.get('due_date') or period['due_date']
            merged['status'] = status
            return merged
    if filings:
        latest = filings[0]
        payload = public_accounts_filing(latest) or {}
        payload['status'] = accounts_status_for_period(payload.get('status'), payload.get('due_date'), today)
        return payload
    periods = accounts_periods_for_company(inc_date, today)
    current = dict(periods[-1] if periods else {})
    current['status'] = accounts_status_for_period('Not started', current.get('due_date'), today)
    return current


def list_accountancy_rows(user_id=None, for_client=False):
    if not for_client:
        schedule_portfolio_ch_sync(user_id=user_id)
    sql = """
        SELECT c.id, c.name, c.company_number, c.inc_date, c.user_id, c.status, c.director,
               c.reg_office, c.utr_number, c.authentication_code, c.activation_code,
               c.identity_verified, c.psc_verified,
               u.full_name as client_name, u.email as client_email
        FROM companies c
        LEFT JOIN users u ON c.user_id = u.id
    """
    params = []
    if user_id:
        sql += " WHERE c.user_id = ?"
        params.append(user_id)
    sql += " ORDER BY c.name COLLATE NOCASE;"
    companies = filter_companies_for_portfolio_list(query_db(sql, params) or [])
    rows = []
    stats = {'overdue': 0, 'due_soon': 0, 'in_progress': 0, 'filed': 0, 'issues': 0, 'idv_outstanding': 0}
    today = datetime.date.today()
    soon = today + datetime.timedelta(days=60)
    listed_ids = [row['id'] for row in companies if not is_pending_company_number(row.get('company_number'))]
    statement_counts = accountancy_statement_counts(listed_ids)
    book_counts = accountancy_book_counts(listed_ids)
    for company in companies:
        if is_pending_company_number(company.get('company_number')):
            continue
        filings = query_db(
            """
            SELECT * FROM company_accounts
            WHERE company_id = ?
            ORDER BY period_end DESC, id DESC;
            """,
            (company['id'],),
        ) or []
        current = current_accounts_period(company.get('inc_date'), filings, today)
        status = current.get('status') or 'Not started'
        due = parse_iso_date(current.get('due_date'))
        if status == 'Filed':
            stats['filed'] += 1
        elif status == 'Overdue':
            stats['overdue'] += 1
        elif status in ('In progress', 'Ready to file', 'Requested'):
            stats['in_progress'] += 1
        elif due and today <= due <= soon:
            stats['due_soon'] += 1
        period_end = str(current.get('period_end') or '')
        book_count = int(book_counts.get((int(company['id']), period_end), 0) or 0)
        statement_count = int(statement_counts.get(int(company['id']), 0) or 0)
        readiness = accountancy_readiness(
            company,
            current=current,
            book_count=book_count,
            statement_count=statement_count,
            for_client=for_client,
        )
        if readiness.get('blocking_count'):
            stats['issues'] += 1
        if not (readiness.get('identity') or {}).get('ok'):
            stats['idv_outstanding'] += 1
        people = accountancy_people_fields(company, for_client=for_client)
        rows.append({
            'id': company['id'],
            'name': company.get('name'),
            'company_number': company.get('company_number'),
            'inc_date': company.get('inc_date'),
            'director': people['director'],
            'director_email': people['director_email'],
            'owner_name': people['owner_name'],
            'client_name': people['client_name'],
            'client_email': people['client_email'],
            'client_id': company.get('user_id'),
            'current': current,
            'filings': [public_accounts_filing(item, for_client=for_client) for item in filings],
            'readiness': readiness,
            'book_count': book_count,
            'statement_count': statement_count,
        })
    return rows, stats


def upsert_company_accounts_filing(company_id, data, actor=None, requested=False):
    company = query_db(
        "SELECT id, user_id, name, inc_date, company_number FROM companies WHERE id = ?;",
        (company_id,),
        one=True,
    )
    if not company:
        return None, 'Company not found'
    if is_pending_company_number(company.get('company_number')):
        return None, 'Register the company number before filing accounts'
    current = current_accounts_period(
        company.get('inc_date'),
        query_db("SELECT * FROM company_accounts WHERE company_id = ? ORDER BY period_end DESC;", (company_id,)) or [],
    )
    period_start = parse_iso_date(data.get('period_start')) or parse_iso_date(current.get('period_start'))
    period_end = parse_iso_date(data.get('period_end')) or parse_iso_date(current.get('period_end'))
    due_date = parse_iso_date(data.get('due_date')) or parse_iso_date(current.get('due_date'))
    if not period_start or not period_end:
        return None, 'Enter the accounting period dates'
    if period_end < period_start:
        return None, 'Period end must be after period start'
    accounts_type = str(data.get('accounts_type') or 'Micro-entity').strip()
    if accounts_type not in ACCOUNTS_TYPES:
        return None, 'Select a valid accounts type'
    status = str(data.get('status') or ('Requested' if requested else 'In progress')).strip()
    if requested:
        status = 'Requested'
    if status not in ACCOUNTS_STATUSES:
        return None, 'Select a valid filing status'
    if status != 'Filed' and due_date and due_date < datetime.date.today():
        status = 'Overdue'
    notes = str(data.get('notes') or '').strip()
    confirmation = str(data.get('confirmation_number') or '').strip()
    filed_at = datetime.datetime.now().isoformat(sep=' ', timespec='seconds') if status == 'Filed' else None
    existing = query_db(
        "SELECT id, status FROM company_accounts WHERE company_id = ? AND period_end = ?;",
        (company_id, period_end.isoformat()),
        one=True,
    )
    was_filed = str((existing or {}).get('status') or '') == 'Filed'
    if existing:
        execute_db(
            """
            UPDATE company_accounts
            SET period_start = ?, due_date = ?, accounts_type = ?, status = ?,
                confirmation_number = ?, notes = ?, filed_at = COALESCE(?, filed_at),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (
                period_start.isoformat(),
                due_date.isoformat() if due_date else None,
                accounts_type,
                status,
                confirmation or None,
                notes or None,
                filed_at,
                existing['id'],
            ),
        )
        filing_id = existing['id']
    else:
        filing_id = execute_db(
            """
            INSERT INTO company_accounts (
                company_id, user_id, period_start, period_end, due_date,
                accounts_type, status, confirmation_number, notes, filed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                company_id,
                company['user_id'],
                period_start.isoformat(),
                period_end.isoformat(),
                due_date.isoformat() if due_date else None,
                accounts_type,
                status,
                confirmation or None,
                notes or None,
                filed_at,
            ),
        )
    filing = query_db("SELECT * FROM company_accounts WHERE id = ?;", (filing_id,), one=True)
    if actor:
        log_activity(
            actor,
            'ACCOUNTS_FILED' if status == 'Filed' else 'ACCOUNTS_UPDATED',
            'company_accounts',
            str(filing_id),
            f"{status} yearly accounts for company {company_id} to {period_end.isoformat()}",
        )
    if status == 'Filed' and not was_filed and not requested:
        notify_accounts_filed(company, filing)
    return filing, None


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
        or str(src.get('review_notes') or '').strip() == 'Smart Document Intake'
        or str(src.get('lifecycle_status') or '').upper() in (
            'CUSTOMER_UPLOADS', 'PROCESSING', 'REVIEW_REQUIRED', 'READY_FOR_APPROVAL', 'QUARANTINE'
        )
    )
    lifecycle = str(src.get('lifecycle_status') or '').strip() or (
        'CUSTOMER_UPLOADS' if is_customer_upload else 'POSTED_DOCUMENTS'
    )
    is_posted_raw = src.get('is_posted')
    if is_posted_raw is None:
        is_posted_val = 0 if is_customer_upload else 1
    else:
        is_posted_val = int(is_posted_raw)
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
        'file_hash': src.get('file_hash'),
        'ocr_status': src.get('ocr_status') or 'COMPLETED',
        'identity_status': src.get('identity_status') or 'COMPLETED',
        'crm_status': src.get('crm_status') or 'MATCHED',
        'ch_status': src.get('ch_status') or 'NOT_APPLICABLE',
        'overall_status': src.get('overall_status') or 'COMPLETED',
        'lifecycle_status': lifecycle,
        'is_posted': is_posted_val,
        'ocr_confidence': float(src.get('ocr_confidence') if src.get('ocr_confidence') is not None else 100.0),
        'classification_confidence': float(src.get('classification_confidence') if src.get('classification_confidence') is not None else 100.0),
        'identity_confidence': float(src.get('identity_confidence') if src.get('identity_confidence') is not None else 100.0),
        'customer_match_confidence': float(src.get('customer_match_confidence') if src.get('customer_match_confidence') is not None else 100.0),
        'company_match_confidence': float(src.get('company_match_confidence') if src.get('company_match_confidence') is not None else 100.0),
        'duplicate_confidence': float(src.get('duplicate_confidence') if src.get('duplicate_confidence') is not None else 0.0),
        'uploaded_at': src.get('uploaded_at') or src.get('created_at'),
        'processed_at': src.get('processed_at'),
        'matched_at': src.get('matched_at'),
        'approved_at': src.get('approved_at'),
        'posted_at': src.get('posted_at'),
        'company_number': src.get('company_number') or src.get('companies_house_number'),
    }
    if not for_client:
        out['review_notes'] = src.get('review_notes')
        meta = None
        if src.get('match_meta_json'):
            try:
                meta = json.loads(src.get('match_meta_json'))
            except Exception:
                meta = None
        out['match_meta'] = meta
        out['matching_evidence'] = (meta or {}).get('matching_evidence') or (meta or {}).get('reasons') or []
        out['conflicting_evidence'] = (meta or {}).get('conflicting_evidence') or []
        out['score_gap'] = (meta or {}).get('score_gap')
        out['top_candidates'] = (meta or {}).get('top_candidates') or []
    return out


def delete_document_record(doc_id, actor=None):
    doc = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
    if not doc:
        return None, 'Document not found'
    remove_stored_document_files([doc.get('file_path')])
    execute_db("DELETE FROM documents WHERE id = ?;", (doc_id,))
    if actor:
        log_activity(
            actor,
            'DOCUMENT_DELETED',
            'documents',
            str(doc_id),
            f"Deleted document {doc.get('name')} (company {doc.get('company_id') or 'none'})",
        )
    return doc, None


def schedule_company_detail_refresh(company_id):
    """Refresh CH/director data after company detail is already returned."""
    def _job():
        try:
            company = query_db(
                f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;",
                (company_id,),
                one=True,
            )
            if not company:
                return
            sync_pending_companies_from_companies_house(company_id=company_id, limit=1)
            sync_registered_company_from_companies_house(company)
            persist_company_registered_email(company)
            if looks_like_uk_company_number(company.get('company_number')):
                live_directors = fetch_companies_house_active_directors(company.get('company_number'))
                if live_directors:
                    sync_company_directors_from_list(company_id, live_directors)
            ensure_company_order_documents(company_id)
        except Exception as exc:
            print(f"[Company detail refresh] {company_id}: {exc}")

    try:
        BULK_JOB_POOL.submit(_job)
    except Exception as exc:
        print(f"[Company detail refresh] schedule failed: {exc}")


def client_greeting_label(now=None):
    hour = (now or datetime.datetime.now()).hour
    if hour < 12:
        return 'Good morning'
    if hour < 17:
        return 'Good afternoon'
    return 'Good evening'


def build_client_pending_actions(uid):
    actions = []
    invoices = query_db(
        """
        SELECT i.id, i.invoice_number, i.status, i.total, i.order_id, o.order_number, o.service_name
        FROM invoices i
        LEFT JOIN orders o ON i.order_id = o.id
        WHERE i.user_id = ? AND i.status IN ('Pending', 'Partial Paid', 'Overdue')
        ORDER BY CASE i.status WHEN 'Overdue' THEN 0 WHEN 'Partial Paid' THEN 1 ELSE 2 END, i.due_date ASC
        LIMIT 4;
        """,
        (uid,),
    ) or []
    for inv in invoices:
        status = inv.get('status') or 'Pending'
        number = inv.get('invoice_number') or 'Invoice'
        if status == 'Overdue':
            title = 'Payment overdue'
            detail = f"{number} is overdue. Please settle the remaining balance."
        elif status == 'Partial Paid':
            title = 'Payment pending'
            detail = f"{number} is partially paid. A balance is still outstanding."
        else:
            title = 'Payment pending'
            detail = f"{number} is awaiting payment."
        actions.append({
            'kind': 'payment',
            'icon': 'credit-card',
            'title': title,
            'detail': detail,
            'action_label': 'Download invoice',
            'view': 'client-invoices',
            'order_id': None,
            'document_id': None,
        })

    awaiting_orders = query_db(
        """
        SELECT id, order_number, service_name, status
        FROM orders
        WHERE user_id = ?
          AND status IN ('Pending', 'Pending Verification')
          AND service_name NOT LIKE '%Proxy%'
          AND service_name NOT LIKE '%Registered Agent%'
        ORDER BY created_at DESC
        LIMIT 4;
        """,
        (uid,),
    ) or []
    for order in awaiting_orders:
        service = (order.get('service_name') or 'your order').strip()
        number = order.get('order_number') or 'Order'
        if order.get('status') == 'Pending Verification':
            title = 'Information required'
            detail = f"{number} needs information before we can continue {service}."
        else:
            title = 'Order awaiting action'
            detail = f"{number} is waiting for your next step on {service}."
        actions.append({
            'kind': 'order',
            'icon': 'shopping-bag',
            'title': title,
            'detail': detail,
            'action_label': 'View Order',
            'view': None,
            'order_id': order.get('id'),
            'document_id': None,
        })

    docs = query_db(
        """
        SELECT id, name, status, company_id
        FROM documents
        WHERE user_id = ? AND client_visible = 1 AND status IN ('Requires Update', 'Rejected')
        ORDER BY created_at DESC
        LIMIT 3;
        """,
        (uid,),
    ) or []
    for doc in docs:
        name = doc.get('name') or 'Document'
        if doc.get('status') == 'Rejected':
            detail = f"{name} was not accepted. Please upload a replacement."
        else:
            detail = f"Please upload the requested document for your order."
        actions.append({
            'kind': 'document',
            'icon': 'file-warning',
            'title': 'Document required',
            'detail': detail,
            'action_label': 'View document',
            'view': 'client-documents',
            'order_id': None,
            'document_id': doc.get('id'),
        })

    overdue_addr = query_db(
        "SELECT COUNT(*) as c FROM addresses WHERE user_id = ? AND expiry_date < date('now') AND status != 'Active';",
        (uid,),
        one=True,
    )
    if overdue_addr and overdue_addr.get('c'):
        count = int(overdue_addr['c'])
        actions.append({
            'kind': 'compliance',
            'icon': 'calendar-clock',
            'title': 'Compliance deadline approaching',
            'detail': f"{count} registered address {'service has' if count == 1 else 'services have'} expired and need{'s' if count == 1 else ''} renewal.",
            'action_label': 'View addresses',
            'view': 'client-addresses',
            'order_id': None,
            'document_id': None,
        })

    failed_id = query_db(
        """
        SELECT name FROM companies
        WHERE user_id = ? AND identity_verified = 'Failed'
        ORDER BY created_at DESC
        LIMIT 2;
        """,
        (uid,),
    ) or []
    for company in failed_id:
        cname = company.get('name') or 'your company'
        actions.append({
            'kind': 'information',
            'icon': 'shield-alert',
            'title': 'Information required',
            'detail': f"Identity verification failed for {cname}. Please contact support.",
            'action_label': 'Contact Support',
            'view': 'client-support',
            'order_id': None,
            'document_id': None,
        })

    psc_due = query_db(
        """
        SELECT name FROM companies
        WHERE user_id = ? AND psc_verified = 'Update required'
        ORDER BY created_at DESC
        LIMIT 2;
        """,
        (uid,),
    ) or []
    for company in psc_due:
        cname = company.get('name') or 'your company'
        actions.append({
            'kind': 'information',
            'icon': 'users',
            'title': 'Information required',
            'detail': f"PSC details need an update for {cname}.",
            'action_label': 'View company',
            'view': 'client-companies',
            'order_id': None,
            'document_id': None,
        })

    return actions[:6]


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


def document_mime_type(file_path, file_type=None):
    lower = str(file_path or '').lower()
    hint = str(file_type or '').lower()
    if lower.endswith('.pdf') or 'pdf' in hint:
        return 'application/pdf'
    if lower.endswith('.png') or 'png' in hint:
        return 'image/png'
    if lower.endswith(('.jpg', '.jpeg')) or 'jpeg' in hint or 'jpg' in hint:
        return 'image/jpeg'
    if lower.endswith('.gif') or 'gif' in hint:
        return 'image/gif'
    if lower.endswith('.webp') or 'webp' in hint:
        return 'image/webp'
    if lower.endswith('.txt'):
        return 'text/plain; charset=utf-8'
    if lower.endswith('.docx'):
        return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    if lower.endswith('.doc'):
        return 'application/msword'
    if lower.endswith('.zip'):
        return 'application/zip'
    return 'application/octet-stream'


def sniff_document_mime(file_path, file_type=None):
    mime = document_mime_type(file_path, file_type)
    try:
        with open(file_path, 'rb') as handle:
            head = handle.read(16)
    except OSError:
        return mime
    if head.startswith(b'%PDF'):
        return 'application/pdf'
    if head.startswith(b'\x89PNG'):
        return 'image/png'
    if head.startswith(b'\xff\xd8'):
        return 'image/jpeg'
    if head.startswith(b'GIF8'):
        return 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return mime


def parse_byte_range(range_header, file_size):
    text = str(range_header or '').strip()
    if not text.startswith('bytes=') or file_size <= 0:
        return None
    spec = text[6:].split(',', 1)[0].strip()
    if '-' not in spec:
        return None
    start_s, end_s = spec.split('-', 1)
    try:
        if start_s == '':
            length = int(end_s)
            if length <= 0:
                return None
            start = max(file_size - length, 0)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
    except ValueError:
        return None
    if start < 0 or start >= file_size:
        return None
    end = min(end, file_size - 1)
    if end < start:
        return None
    return start, end


def document_download_filename(doc, file_path):
    safe_name = os.path.basename(str((doc or {}).get('name') or 'document')) or 'document'
    ext = os.path.splitext(file_path or '')[1]
    if not os.path.splitext(safe_name)[1] and ext:
        safe_name = f"{safe_name}{ext}"
    return safe_name.replace('"', '').replace('\r', '').replace('\n', '')


def user_can_stream_document(user, doc):
    if not user or not doc:
        return False, '401 Unauthorized', 'Not authenticated'
    if user.get('role') == 'CLIENT':
        if doc.get('user_id') != user.get('id') or not client_can_access_document(doc):
            return False, '403 Forbidden', 'Access denied to target document'
        return True, None, None
    if not check_permission(user, 'documents.view'):
        return False, '403 Forbidden', 'Insufficient permissions'
    return True, None, None


def stream_stored_document(start_response, doc, inline=False, environ=None):
    file_path = (doc or {}).get('file_path')
    if not file_path or not os.path.exists(file_path):
        return None
    file_size = os.path.getsize(file_path)
    mime = sniff_document_mime(file_path, (doc or {}).get('file_type'))
    safe_name = document_download_filename(doc, file_path)
    ascii_name = safe_name.encode('ascii', 'replace').decode('ascii') or 'document'
    disposition = 'inline' if inline else 'attachment'
    quoted = urllib.parse.quote(safe_name)
    headers = [
        ('Content-Type', mime),
        ('Accept-Ranges', 'bytes'),
        ('Content-Disposition', f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"),
        ('X-Content-Type-Options', 'nosniff'),
        ('Cache-Control', 'private, no-store'),
    ]
    range_req = parse_byte_range((environ or {}).get('HTTP_RANGE', ''), file_size) if inline else None
    if range_req:
        start, end = range_req
        length = end - start + 1
        with open(file_path, 'rb') as handle:
            handle.seek(start)
            content = handle.read(length)
        headers.append(('Content-Length', str(length)))
        headers.append(('Content-Range', f'bytes {start}-{end}/{file_size}'))
        start_response("206 Partial Content", headers)
        return [content]
    with open(file_path, 'rb') as handle:
        content = handle.read()
    headers.append(('Content-Length', str(len(content))))
    start_response("200 OK", headers)
    return [content]


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


DOCUMENT_LIFECYCLE_STATES = (
    'CUSTOMER_UPLOADS',
    'PROCESSING',
    'REVIEW_REQUIRED',
    'READY_FOR_APPROVAL',
    'POSTED_DOCUMENTS',
    'QUARANTINE',
)
DOC_AI_MODEL_VERSION = 'v2.0'


def log_document_audit(
    document_id,
    actor_type,
    actor_id,
    actor_name,
    action,
    previous_state=None,
    new_state=None,
    reason_evidence=None,
    ai_model_version=DOC_AI_MODEL_VERSION,
):
    evidence = reason_evidence
    if evidence is not None and not isinstance(evidence, str):
        evidence = json.dumps(evidence)
    execute_db(
        """
        INSERT INTO document_audit_log (
            document_id, actor_type, actor_id, actor_name, action,
            previous_state, new_state, ai_model_version, reason_evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            document_id,
            actor_type or 'AI',
            actor_id,
            actor_name or 'System',
            action,
            previous_state,
            new_state,
            ai_model_version,
            evidence,
        ),
    )


def find_document_by_file_hash(file_hash, exclude_id=None):
    if not file_hash:
        return None
    if exclude_id:
        return query_db(
            "SELECT id, name, user_id, lifecycle_status, is_posted FROM documents WHERE file_hash = ? AND id != ? ORDER BY id DESC LIMIT 1;",
            (file_hash, exclude_id),
            one=True,
        )
    return query_db(
        "SELECT id, name, user_id, lifecycle_status, is_posted FROM documents WHERE file_hash = ? ORDER BY id DESC LIMIT 1;",
        (file_hash,),
        one=True,
    )


def _file_bytes_look_corrupt(file_bytes, filename=''):
    if not file_bytes or len(file_bytes) < 32:
        return True, 'Empty or truncated file'
    ext = document_extension(filename)
    head = file_bytes[:16]
    if ext == '.pdf' and not head.startswith(b'%PDF'):
        return True, 'PDF header missing'
    if ext in ('.png',) and not head.startswith(b'\x89PNG'):
        return True, 'PNG header missing'
    if ext in ('.jpg', '.jpeg') and not (head.startswith(b'\xff\xd8') or head.startswith(b'\xff\xd8\xff')):
        return True, 'JPEG header missing'
    return False, None


def _classification_confidence_for_category(category, raw_text, scan_method):
    cat = category or 'Company Documents'
    text_len = len(raw_text or '')
    if cat in ('ID Document', 'Passport'):
        base = 88.0 if text_len > 40 else 55.0
    elif cat in ('Bank statement', 'Proof of Address', 'Certificate of Incorporation'):
        base = 82.0 if text_len > 40 else 50.0
    else:
        base = 45.0 if text_len > 80 else 25.0
    if scan_method == 'ocr':
        base = min(100.0, base + 5.0)
    return round(base, 1)


def triage_uploaded_document(file_bytes, filename, user_id, client_id=None, actor=None, run_company_match=True):
    """
    AI triage for customer uploads. Never marks is_posted.
    Returns a result dict used by insert/update handlers.
    """
    name = (filename or 'upload.bin').strip() or 'upload.bin'
    file_hash = hashlib.sha256(file_bytes or b'').hexdigest() if file_bytes else None
    reasons = []
    conflicting = []
    result = {
        'lifecycle_status': 'REVIEW_REQUIRED',
        'is_posted': 0,
        'file_hash': file_hash,
        'category': 'Company Documents',
        'ocr_confidence': 0.0,
        'classification_confidence': 0.0,
        'identity_confidence': 0.0,
        'customer_match_confidence': 0.0,
        'company_match_confidence': 0.0,
        'duplicate_confidence': 0.0,
        'company_id': None,
        'match_meta': {},
        'extracted_name': None,
        'extracted_dob': None,
        'extracted_nationality': None,
        'review_notes': 'AI triage',
        'matching_evidence': reasons,
        'conflicting_evidence': conflicting,
        'quarantine_reason': None,
        'ai_model_version': DOC_AI_MODEL_VERSION,
    }

    corrupt, corrupt_reason = _file_bytes_look_corrupt(file_bytes, name)
    if corrupt:
        result.update({
            'lifecycle_status': 'QUARANTINE',
            'classification_confidence': 0.0,
            'quarantine_reason': corrupt_reason or 'Unreadable file',
            'review_notes': f"Quarantined: {corrupt_reason or 'Unreadable file'}",
        })
        conflicting.append(result['quarantine_reason'])
        return result

    dup = find_document_by_file_hash(file_hash)
    if dup:
        result.update({
            'lifecycle_status': 'QUARANTINE',
            'duplicate_confidence': 100.0,
            'quarantine_reason': f"Exact duplicate of document #{dup['id']}",
            'review_notes': f"Quarantined: exact duplicate of document #{dup['id']} ({dup.get('name')})",
            'classification_confidence': 100.0,
        })
        conflicting.append(result['quarantine_reason'])
        return result

    # Reuse intake OCR / classification pipeline
    parsed = None
    try:
        b64 = base64.b64encode(file_bytes).decode('ascii')
        parsed = extract_document_text_and_metadata(name, b64)
        if isinstance(parsed, dict) and parsed.get('error'):
            raise RuntimeError(parsed.get('error'))
    except Exception as exc:
        result.update({
            'lifecycle_status': 'QUARANTINE',
            'quarantine_reason': f'OCR failed: {exc}',
            'review_notes': f'Quarantined: OCR failed ({exc})',
        })
        conflicting.append(str(exc))
        return result

    category = (parsed or {}).get('category') or 'Company Documents'
    quality = (parsed or {}).get('extraction_quality') or {}
    ocr_conf = float(quality.get('score') or 0)
    scan_method = (parsed or {}).get('scan_method') or 'none'
    raw_preview = (parsed or {}).get('text_preview') or ''
    class_conf = _classification_confidence_for_category(category, raw_preview, scan_method)
    person_name = (parsed or {}).get('extracted_name')
    dob = (parsed or {}).get('extracted_dob')
    nationality = (parsed or {}).get('extracted_nationality')

    identity_conf = 0.0
    if person_name:
        identity_conf += 50.0
        reasons.append('Person name extracted')
    if dob:
        identity_conf += 35.0
        reasons.append('Date of birth extracted')
    if nationality:
        identity_conf += 15.0
        reasons.append('Nationality extracted')
    identity_conf = min(100.0, identity_conf)

    customer_conf = 0.0
    target_client_id = client_id or user_id
    if target_client_id and person_name:
        client_row = query_db("SELECT id, full_name FROM users WHERE id = ?;", (target_client_id,), one=True)
        if client_row:
            client_key = normalize_match_person_name(client_row.get('full_name'))
            person_key = normalize_match_person_name(person_name)
            if client_key and person_key and client_key == person_key:
                customer_conf = 95.0
                reasons.append('Exact CRM customer name match')
            elif person_key and client_key and (person_key in client_key or client_key in person_key):
                customer_conf = 70.0
                reasons.append('Partial CRM customer name match')
            else:
                customer_conf = 40.0
                conflicting.append('Extracted name differs from CRM customer')
        else:
            customer_conf = 20.0
    elif target_client_id:
        customer_conf = 55.0
        reasons.append('Uploaded under known customer account')

    company_conf = 0.0
    company_id = None
    match_meta = {}
    score_gap = 0
    if run_company_match and person_name:
        try:
            company_obj, status, message, ranked, meta = resolve_intake_company_match(
                target_client_id,
                person_name,
                extracted_dob=dob,
                extracted_nationality=nationality,
                actor=actor,
            )
            match_meta = meta or {}
            match_meta['status'] = status
            match_meta['message'] = message
            match_meta['top_candidates'] = ranked or []
            company_conf = float((meta or {}).get('confidence') or 0)
            score_gap = int((meta or {}).get('score_gap') or 0)
            if company_obj:
                company_id = company_obj.get('id')
                reasons.append(f"Company match: {company_obj.get('name')}")
            if status == 'review_required':
                conflicting.append(message or 'Ambiguous company match')
            elif status == 'not_applicable' and person_name:
                conflicting.append(message or 'No safe company match')
        except Exception as exc:
            conflicting.append(f'Company match error: {exc}')
            match_meta = {'error': str(exc)}

    # Junk / irrelevant: unreadable text and generic category
    if class_conf < 30 and ocr_conf < 20 and not person_name:
        result.update({
            'lifecycle_status': 'QUARANTINE',
            'category': category,
            'ocr_confidence': ocr_conf,
            'classification_confidence': class_conf,
            'identity_confidence': identity_conf,
            'customer_match_confidence': customer_conf,
            'company_match_confidence': company_conf,
            'quarantine_reason': 'Irrelevant or unreadable content',
            'review_notes': 'Quarantined: irrelevant or unreadable content',
            'match_meta': match_meta,
            'extracted_name': person_name,
            'extracted_dob': dob,
            'extracted_nationality': nationality,
        })
        conflicting.append('Irrelevant or unreadable content')
        return result

    lifecycle = 'REVIEW_REQUIRED'
    if company_conf >= 75 and score_gap >= 20 and not any('Incompatible DOB' in (c or '') for c in conflicting):
        lifecycle = 'READY_FOR_APPROVAL'
        reasons.append(f'High-confidence company match (gap={score_gap})')
    elif identity_conf >= 75 and customer_conf >= 70 and company_conf < 65:
        # Strong person identity but no safe company — still review for company link
        lifecycle = 'REVIEW_REQUIRED'
        reasons.append('Identity strong; company assignment needs review')
    elif identity_conf >= 85 and customer_conf >= 90 and (not run_company_match or company_conf >= 75):
        lifecycle = 'READY_FOR_APPROVAL'
    elif not person_name and class_conf >= 70:
        lifecycle = 'REVIEW_REQUIRED'
        reasons.append('Document classified; person identity incomplete')

    result.update({
        'lifecycle_status': lifecycle,
        'category': category,
        'ocr_confidence': ocr_conf,
        'classification_confidence': class_conf,
        'identity_confidence': identity_conf,
        'customer_match_confidence': customer_conf,
        'company_match_confidence': company_conf,
        'company_id': company_id,
        'match_meta': match_meta,
        'extracted_name': person_name,
        'extracted_dob': dob,
        'extracted_nationality': nationality,
        'review_notes': f"AI triage → {lifecycle}",
        'matching_evidence': reasons,
        'conflicting_evidence': conflicting,
    })
    return result


def apply_triage_to_document_row(doc_id, triage, actor=None, previous_state='CUSTOMER_UPLOADS'):
    if not doc_id or not triage:
        return
    meta = triage.get('match_meta') or {}
    meta['matching_evidence'] = triage.get('matching_evidence') or []
    meta['conflicting_evidence'] = triage.get('conflicting_evidence') or []
    new_state = triage.get('lifecycle_status') or 'REVIEW_REQUIRED'
    execute_db(
        """
        UPDATE documents SET
            lifecycle_status = ?,
            is_posted = 0,
            file_hash = COALESCE(?, file_hash),
            category = COALESCE(?, category),
            company_id = COALESCE(?, company_id),
            ocr_confidence = ?,
            classification_confidence = ?,
            identity_confidence = ?,
            customer_match_confidence = ?,
            company_match_confidence = ?,
            duplicate_confidence = ?,
            processed_at = CURRENT_TIMESTAMP,
            matched_at = CASE WHEN ? >= 65 THEN CURRENT_TIMESTAMP ELSE matched_at END,
            processed_by_id = ?,
            matched_by_id = ?,
            match_meta_json = ?,
            review_notes = ?,
            overall_status = ?
        WHERE id = ?;
        """,
        (
            new_state,
            triage.get('file_hash'),
            triage.get('category'),
            triage.get('company_id'),
            float(triage.get('ocr_confidence') or 0),
            float(triage.get('classification_confidence') or 0),
            float(triage.get('identity_confidence') or 0),
            float(triage.get('customer_match_confidence') or 0),
            float(triage.get('company_match_confidence') or 0),
            float(triage.get('duplicate_confidence') or 0),
            float(triage.get('company_match_confidence') or 0),
            (actor or {}).get('id') if isinstance(actor, dict) else None,
            (actor or {}).get('id') if isinstance(actor, dict) else None,
            json.dumps(meta),
            triage.get('review_notes') or 'AI triage',
            new_state,
            doc_id,
        ),
    )
    action = 'AI_QUARANTINE' if new_state == 'QUARANTINE' else 'AI_TRIAGE'
    log_document_audit(
        doc_id,
        'AI',
        (actor or {}).get('id') if isinstance(actor, dict) else None,
        (actor or {}).get('full_name') if isinstance(actor, dict) else 'AI Triage',
        action,
        previous_state,
        new_state,
        {
            'ocr_confidence': triage.get('ocr_confidence'),
            'classification_confidence': triage.get('classification_confidence'),
            'identity_confidence': triage.get('identity_confidence'),
            'customer_match_confidence': triage.get('customer_match_confidence'),
            'company_match_confidence': triage.get('company_match_confidence'),
            'duplicate_confidence': triage.get('duplicate_confidence'),
            'matching_evidence': triage.get('matching_evidence'),
            'conflicting_evidence': triage.get('conflicting_evidence'),
            'quarantine_reason': triage.get('quarantine_reason'),
        },
    )


def store_client_document_file(client_id, order_id, ext, file_bytes):
    cid = str(client_id) if (client_id is not None and str(client_id).strip() != '' and str(client_id).strip() != 'None') else 'unassigned'
    order_folder = str(order_id or 'general')
    storage_root = os.path.abspath(STORAGE_DIR)
    client_dir = os.path.abspath(os.path.join(STORAGE_DIR, 'clients', cid, 'orders', order_folder, 'documents'))
    try:
        os.makedirs(client_dir, exist_ok=True)
    except Exception as exc:
        return None, f"Storage directory error: {exc}"
    target_path = os.path.abspath(os.path.join(client_dir, f"{uuid.uuid4().hex}{ext}"))
    if not target_path.startswith(storage_root):
        return None, 'Path traversal attempt blocked'
    try:
        with open(target_path, 'wb') as handle:
            handle.write(file_bytes)
    except Exception as exc:
        return None, f"Failed to save document file: {exc}"
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
                status, uploaded_by, review_notes, client_visible,
                file_hash, lifecycle_status, is_posted, uploaded_at, uploaded_by_id, overall_status
            ) VALUES (?, ?, ?, ?, 'Checkout Upload', ?, ?, ?, 'Pending Review', 'Customer Upload', ?, 0,
                      ?, 'CUSTOMER_UPLOADS', 0, CURRENT_TIMESTAMP, ?, 'CUSTOMER_UPLOADS');
        """, (
            client_id,
            company_id,
            order_id,
            safe_name,
            target_path,
            DOCUMENT_TYPE_LABELS.get(ext, 'Document'),
            format_document_size(len(file_bytes)),
            f'Checkout source: {url}',
            hashlib.sha256(file_bytes).hexdigest(),
            client_id,
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


def notify_client_document_uploaded(client, doc_name, client_message=None, company_name=None, order_number=None, company=None, order=None, company_id=None):
    extra = (client_message or '').strip()
    message = 'Brixen Consultants has uploaded a new document to your client portal. Log in to view and download it.'
    if extra:
        message = f"{message} {extra}"
    subject, email_text, email_html = build_client_document_email(client, doc_name, client_message, company_name=company_name, order_number=order_number)
    company_row = company or (load_company_for_notify(company_id) if company_id else None)
    return notify_client(
        client,
        'New document available',
        message,
        'document_uploaded',
        '/documents',
        email_subject=subject,
        email_text=email_text,
        email_html=email_html,
        company=company_row,
        order=order,
    )


def public_line_item(row, for_client=False, include_finance=True):
    if not row:
        return None
    src = dict(row)
    item = {
        'product_name': src.get('product_name'),
        'category_name': src.get('category_name'),
        'quantity': src.get('quantity'),
    }
    if include_finance:
        item['unit_price'] = src.get('unit_price')
        item['line_total'] = src.get('line_total')
    if not for_client:
        item.update({
            'woocommerce_product_id': src.get('woocommerce_product_id'),
            'woocommerce_variation_id': src.get('woocommerce_variation_id'),
            'sku': src.get('sku'),
            'category_id': src.get('category_id'),
        })
    return item


def normalize_retail_price(amount):
    """Round up to the next whole pound when the price ends in 9 (159.99->160, 149->150)."""
    try:
        value = round(float(amount or 0), 2)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return value
    import math
    cents = int(round(value * 100))
    pounds_whole = int(value)
    if cents % 10 == 9:
        return float(math.ceil(value))
    if value == pounds_whole and pounds_whole % 10 == 9:
        return float(pounds_whole + 1)
    return value


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
            if unit is not None:
                unit = normalize_retail_price(unit)
            if total is not None:
                total = normalize_retail_price(total)
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
        if unit is not None:
            unit = normalize_retail_price(unit)
        if total is not None:
            total = normalize_retail_price(total)
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
    text = re.sub(r'\band\b', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    suffixes = (
        'limited', 'ltd', 'llp', 'plc', 'inc', 'incorporated', 'corp', 'corporation',
        'company', 'co', 'llc', 'cyf', 'ccc',
    )
    parts = text.split()
    while parts and parts[-1] in suffixes:
        parts.pop()
    return ' '.join(parts)


def company_name_keys_are_near_duplicate(left, right):
    a = str(left or '').strip()
    b = str(right or '').strip()
    if not a or not b:
        return False
    if a == b:
        return True
    if abs(len(a) - len(b)) != 1:
        return False
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    extra = 0
    i = 0
    for ch in longer:
        if i < len(shorter) and shorter[i] == ch:
            i += 1
            continue
        extra += 1
        if extra > 1:
            return False
    return i == len(shorter)


def _pick_company_match(rows):
    if not rows:
        return None
    registered = [row for row in rows if not is_pending_company_number(row.get('company_number'))]
    chosen = registered[0] if registered else rows[0]
    return chosen.get('id')


def match_company_for_client(client_id, company_name, exclude_id=None):
    if not company_name or not str(company_name).strip():
        return None
    skip_id = optional_record_id(exclude_id) if exclude_id is not None else None
    needle_raw = str(company_name).strip()
    sql = "SELECT id, name, company_number FROM companies WHERE user_id = ? AND LOWER(name) = LOWER(?)"
    params = [client_id, needle_raw]
    if skip_id:
        sql += " AND id != ?"
        params.append(skip_id)
    sql += ";"
    exact = query_db(sql, params)
    picked = _pick_company_match(exact or [])
    if picked:
        return picked
    needle = normalize_company_name_key(company_name)
    if not needle:
        return None
    same = []
    near = []
    for row in query_db("SELECT id, name, company_number FROM companies WHERE user_id = ?;", (client_id,)) or []:
        if skip_id and int(row['id']) == int(skip_id):
            continue
        key = normalize_company_name_key(row.get('name'))
        if key == needle:
            same.append(row)
        elif company_name_keys_are_near_duplicate(key, needle):
            near.append(row)
    return _pick_company_match(same) or _pick_company_match(near)


def parse_order_checkout_fields(row):
    raw = (row or {}).get('checkout_form_json')
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def match_company_for_client_by_number(client_id, company_number):
    number = normalize_company_number(company_number)
    if not looks_like_uk_company_number(number) or is_pending_company_number(number):
        return None
    if client_id:
        row = query_db(
            "SELECT id FROM companies WHERE user_id = ? AND UPPER(REPLACE(COALESCE(company_number,''), ' ', '')) = ?;",
            (client_id, number),
            one=True,
        )
        if row:
            return row['id']
    row = query_db(
        "SELECT id FROM companies WHERE UPPER(REPLACE(COALESCE(company_number,''), ' ', '')) = ? LIMIT 1;",
        (number,),
        one=True,
    )
    return row['id'] if row else None


def ensure_order_company_link(order_row, fields=None):
    order_id = (order_row or {}).get('id')
    current_id = (order_row or {}).get('company_id')
    if current_id:
        return current_id
    if not order_id:
        return None
    fields = fields if fields is not None else parse_order_checkout_fields(order_row)
    user_id = (order_row or {}).get('user_id')
    number = checkout_form_company_number(fields)
    name = company_name_from_checkout_fields(fields)
    company_id = match_company_for_client_by_number(user_id, number) if number else None
    if not company_id and name:
        company_id = match_company_for_client(user_id, name)
    if company_id:
        attach_order_to_company(order_id, company_id)
        return company_id
    return None


def order_display_company_name(row):
    name = str((row or {}).get('company_name') or '').strip()
    if name:
        return name
    return company_name_from_checkout_fields(parse_order_checkout_fields(row))


def attach_order_to_company(order_id, company_id):
    if not order_id or not company_id:
        return False
    execute_db(
        """
        UPDATE orders
        SET company_id = ?, portfolio_hidden = 0, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """,
        (company_id, order_id),
    )
    execute_db("UPDATE company_owners SET company_id = ? WHERE order_id = ?;", (company_id, order_id))
    order = query_db(
        "SELECT id, company_id, checkout_form_json FROM orders WHERE id = ?;",
        (order_id,),
        one=True,
    )
    fields = []
    raw = (order or {}).get('checkout_form_json')
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                fields = parsed
        except (TypeError, ValueError):
            fields = []
    updated = apply_current_company_name_to_checkout_fields(order, fields)
    execute_db(
        "UPDATE orders SET checkout_form_json = ? WHERE id = ?;",
        (json.dumps(updated), order_id),
    )
    return True


def heal_orders_hidden_after_duplicate_company_delete():
    rows = query_db(
        """
        SELECT id, user_id, company_id, woocommerce_order_id, checkout_form_json
        FROM orders
        WHERE COALESCE(portfolio_hidden, 0) = 1;
        """
    ) or []
    linked = 0
    for row in rows:
        company_id = optional_record_id(row.get('company_id'))
        company = query_db(
            "SELECT id, name, company_number FROM companies WHERE id = ?;",
            (company_id,),
            one=True,
        ) if company_id else None
        if company and not is_pending_company_number(company.get('company_number')):
            attach_order_to_company(row['id'], company['id'])
            linked += 1
            continue
        wc_id = str(row.get('woocommerce_order_id') or '').strip()
        dismissed = None
        if wc_id:
            dismissed = query_db(
                """
                SELECT name_key FROM dismissed_company_cards
                WHERE user_id = ? AND woocommerce_order_id = ?
                ORDER BY id DESC LIMIT 1;
                """,
                (row['user_id'], wc_id),
                one=True,
            )
        search_name = str((dismissed or {}).get('name_key') or '').strip()
        if not search_name:
            raw = row.get('checkout_form_json')
            try:
                fields = json.loads(raw) if raw else []
            except (TypeError, ValueError):
                fields = []
            for field in fields if isinstance(fields, list) else []:
                if not isinstance(field, dict):
                    continue
                if checkout_field_key(field.get('label')) in CHECKOUT_COMPANY_NAME_KEYS:
                    search_name = str(field.get('value') or '').strip()
                    if search_name:
                        break
        if not search_name:
            continue
        matched = match_company_for_client(row['user_id'], search_name)
        if not matched:
            continue
        matched_row = query_db(
            "SELECT id, company_number FROM companies WHERE id = ?;",
            (matched,),
            one=True,
        )
        if not matched_row or is_pending_company_number(matched_row.get('company_number')):
            continue
        attach_order_to_company(row['id'], matched_row['id'])
        linked += 1
    return linked


def absorb_company_into(source_id, target_id):
    source_id = optional_record_id(source_id)
    target_id = optional_record_id(target_id)
    if not source_id or not target_id or source_id == target_id:
        return False
    source = query_db("SELECT id, user_id FROM companies WHERE id = ?;", (source_id,), one=True)
    target = query_db("SELECT id, user_id FROM companies WHERE id = ?;", (target_id,), one=True)
    if not source or not target or source.get('user_id') != target.get('user_id'):
        return False
    execute_db("UPDATE orders SET company_id = ?, portfolio_hidden = 0 WHERE company_id = ?;", (target_id, source_id))
    execute_db("UPDATE documents SET company_id = ? WHERE company_id = ?;", (target_id, source_id))
    execute_db("UPDATE company_owners SET company_id = ? WHERE company_id = ?;", (target_id, source_id))
    execute_db("UPDATE support_tickets SET company_id = ? WHERE company_id = ?;", (target_id, source_id))
    execute_db("UPDATE tasks SET company_id = ? WHERE company_id = ?;", (target_id, source_id))
    execute_db("UPDATE company_directors SET company_id = ? WHERE company_id = ?;", (target_id, source_id))
    execute_db("UPDATE addresses SET company_id = ? WHERE company_id = ?;", (target_id, source_id))
    execute_db("UPDATE proxies SET company_id = ? WHERE company_id = ?;", (target_id, source_id))
    execute_db("UPDATE registered_agents SET company_id = ? WHERE company_id = ?;", (target_id, source_id))
    execute_db(
        """
        UPDATE company_accounts
        SET company_id = ?
        WHERE company_id = ?
          AND period_end NOT IN (SELECT period_end FROM company_accounts WHERE company_id = ?);
        """,
        (target_id, source_id, target_id),
    )
    execute_db("DELETE FROM company_accounts WHERE company_id = ?;", (source_id,))
    execute_db("UPDATE company_book_entries SET company_id = ? WHERE company_id = ?;", (target_id, source_id))
    execute_db("DELETE FROM companies WHERE id = ?;", (source_id,))
    return True


def merge_pending_order_company_duplicates():
    """Keep one company card per website order number.

    Pending REG-{order} cards stay behind when the same order is later
    attached to the registered name (Love Wales Ltd / REG-15310 vs RABEXA LTD).
    """
    pending = query_db(
        """
        SELECT id, user_id, company_number
        FROM companies
        WHERE UPPER(COALESCE(company_number, '')) LIKE 'REG-%';
        """
    ) or []
    merged = 0
    for row in pending:
        number = str(row.get('company_number') or '').strip()
        wc_id = number[4:] if number.upper().startswith('REG-') else ''
        if not wc_id or not wc_id.isdigit():
            continue
        order = query_db(
            """
            SELECT id, company_id, user_id
            FROM orders
            WHERE woocommerce_order_id = ? OR order_number = ? OR order_number = ?
            LIMIT 1;
            """,
            (wc_id, f'#{wc_id}', wc_id),
            one=True,
        )
        target_id = optional_record_id((order or {}).get('company_id'))
        if not order or not target_id or target_id == optional_record_id(row['id']):
            continue
        if optional_record_id(order.get('user_id')) != optional_record_id(row.get('user_id')):
            continue
        target = query_db(
            "SELECT id, user_id, company_number FROM companies WHERE id = ?;",
            (target_id,),
            one=True,
        )
        if not target or is_pending_company_number(target.get('company_number')):
            continue
        if absorb_company_into(row['id'], target['id']):
            merged += 1
    return merged


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
    meta = o_data.get('meta') or o_data.get('meta_data') or {}
    if isinstance(meta, dict):
        for key in ('_cfs_company_name', 'proposed_company_name', 'company_name', 'billing_company', '_billing_company'):
            value = meta.get(key)
            if value and str(value).strip():
                return str(value).strip()
    if isinstance(meta, list):
        preferred = {'_cfs_company_name', 'proposed_company_name', 'company_name', 'billing_company', '_billing_company'}
        for item in meta:
            if not isinstance(item, dict):
                continue
            key = str(item.get('key') or item.get('id') or '')
            if key in preferred:
                value = item.get('value')
                if value and str(value).strip():
                    return str(value).strip()
    for key in ('company_name', 'billing_company', 'proposed_company_name', 'proposed_name', 'company'):
        value = o_data.get(key)
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


def ensure_smart_intake_client(extracted_name=None, extracted_email=None, extracted_phone=None):
    """Always return a CLIENT user row so documents.user_id is never NULL."""
    name = (extracted_name or '').strip()
    email = (extracted_email or '').strip() or None
    phone = (extracted_phone or '').strip() or None

    client_user = None
    if name:
        client_user = query_db(
            "SELECT * FROM users WHERE role = 'CLIENT' AND lower(full_name) = lower(?);",
            (name,),
            one=True,
        )
        if not client_user:
            like_name = '%' + '%'.join(name.lower().split()) + '%'
            client_user = query_db(
                "SELECT * FROM users WHERE role = 'CLIENT' AND lower(full_name) LIKE ? ORDER BY id DESC LIMIT 1;",
                (like_name,),
                one=True,
            )
        if not client_user:
            clean_username = re.sub(r'[^a-z0-9]', '.', name.lower().strip()).strip('.') or 'intake.client'
            provisional_email = email or f"{clean_username}@brixen-pending.local"
            user_id = execute_db("""
                INSERT INTO users (full_name, email, password_hash, role, status, phone, created_at)
                VALUES (?, ?, ?, 'CLIENT', 'Active', ?, CURRENT_TIMESTAMP);
            """, (name, provisional_email, unusable_password_hash(), phone))
            client_user = query_db("SELECT * FROM users WHERE id = ?;", (user_id,), one=True)
        elif client_user:
            updates = []
            params = []
            if email and 'brixen-pending.local' in (client_user.get('email') or ''):
                updates.append('email = ?')
                params.append(email)
            if phone and not client_user.get('phone'):
                updates.append('phone = ?')
                params.append(phone)
            if updates:
                params.append(client_user['id'])
                execute_db(f"UPDATE users SET {', '.join(updates)} WHERE id = ?;", tuple(params))
                client_user = query_db("SELECT * FROM users WHERE id = ?;", (client_user['id'],), one=True)
        return client_user

    client_user = query_db(
        "SELECT * FROM users WHERE email = 'intake.unassigned@brixen-pending.local';",
        one=True,
    )
    if client_user:
        return client_user
    user_id = execute_db("""
        INSERT INTO users (full_name, email, password_hash, role, status, created_at)
        VALUES (?, ?, ?, 'CLIENT', 'Active', CURRENT_TIMESTAMP);
    """, (
        'Unassigned Document Intake',
        'intake.unassigned@brixen-pending.local',
        unusable_password_hash(),
    ))
    return query_db("SELECT * FROM users WHERE id = ?;", (user_id,), one=True)


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


_CH_API_CACHE = {}
_CH_API_CACHE_TTL = 600  # 10 minutes cache TTL for batch processing


def cached_companies_house_request(path_qs):
    now = time.time()
    if path_qs in _CH_API_CACHE:
        cached_time, res, err = _CH_API_CACHE[path_qs]
        if now - cached_time < _CH_API_CACHE_TTL:
            return res, err
    res, err = companies_house_request(path_qs)
    if res is not None or err:
        _CH_API_CACHE[path_qs] = (now, res, err)
    return res, err


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


def search_companies_house_officers(person_name, extracted_dob=None):
    """Return company candidates for a person (CRM + CH officers with company numbers)."""
    q = (person_name or '').strip()
    if len(q) < 3:
        return [], 'Enter at least 3 characters'

    matches = []
    seen_nums = set()

    def add_match(name, company_number, address='', company_id=None, appointment_count=1,
                  officer_name='', officer_dob=None, source='companies_house', company_status=''):
        num = normalize_company_number(company_number)
        if not num and not company_id:
            return
        key = num or f'crm:{company_id}'
        if key in seen_nums:
            return
        seen_nums.add(key)
        matches.append({
            'company_id': company_id,
            'name': (name or f'Company {num or company_id}').strip(),
            'company_number': num or '',
            'address': (address or '').strip() or 'United Kingdom',
            'appointment_count': appointment_count or 1,
            'source': source,
            'officer_name': (officer_name or '').strip(),
            'officer_dob': officer_dob if isinstance(officer_dob, dict) else None,
            'company_status': (company_status or '').strip().lower(),
        })

    local_matches = query_db("""
        SELECT id, name, company_number, director, reg_office, status, user_id
        FROM companies
        WHERE lower(director) LIKE lower(?) OR lower(name) LIKE lower(?)
        ORDER BY id DESC LIMIT 12;
    """, (f"%{q}%", f"%{q}%")) or []
    for comp in local_matches:
        add_match(
            comp.get('name'),
            comp.get('company_number'),
            comp.get('reg_office') or 'Existing CRM company',
            company_id=comp.get('id'),
            officer_name=comp.get('director') or '',
            source='crm',
            company_status=comp.get('status') or '',
        )

    # Pull enough officer hits — common names bury the DOB-correct person past page size 8.
    path_qs = '/search/officers?' + urllib.parse.urlencode({'q': q, 'items_per_page': 20})
    payload, error = cached_companies_house_request(path_qs)
    if payload and isinstance(payload, dict):
        items = list(payload.get('items') or [])

        def officer_dob_priority(item):
            """Prefer officers whose CH birth month/year matches passport DOB."""
            dob = item.get('date_of_birth') if isinstance(item.get('date_of_birth'), dict) else None
            pts, _reason = score_intake_dob_match(extracted_dob, dob)
            # Compatible DOB first (25/35/10), then unknown (0), incompatible last (-50).
            return pts

        if extracted_dob:
            items.sort(key=officer_dob_priority, reverse=True)

        for item in items:
            links = item.get('links') or {}
            self_link = (links.get('self') or '').strip()
            if self_link.startswith('http'):
                parsed = urllib.parse.urlparse(self_link)
                self_link = parsed.path or ''
            if not self_link.startswith('/'):
                continue
            address = (item.get('address_snippet') or '').strip()
            officer_name = (item.get('title') or '').strip()
            officer_dob = item.get('date_of_birth') if isinstance(item.get('date_of_birth'), dict) else None
            app_payload, _app_err = cached_companies_house_request(self_link)
            if not app_payload or not isinstance(app_payload, dict):
                continue
            for appt in app_payload.get('items') or []:
                appointed_to = appt.get('appointed_to') or {}
                c_name = (appointed_to.get('company_name') or appt.get('company_name') or '').strip()
                c_num = (appointed_to.get('company_number') or appt.get('company_number') or '').strip()
                if not c_num:
                    continue
                add_match(
                    c_name,
                    c_num,
                    address,
                    officer_name=officer_name,
                    officer_dob=officer_dob,
                    source='companies_house',
                )

    return matches, None


def normalize_match_person_name(name):
    text = unicodedata.normalize('NFKC', str(name or ''))
    text = text.replace(',', ' ')
    text = re.sub(r'[^\w\s\-]', ' ', text, flags=re.UNICODE)
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text


def match_name_tokens(name):
    return [t for t in normalize_match_person_name(name).split() if len(t) > 1]


def score_intake_name_match(extracted_name, candidate_person_name):
    a = normalize_match_person_name(extracted_name)
    b = normalize_match_person_name(candidate_person_name)
    if not a or not b:
        return 0, None
    if a == b:
        return 50, 'Exact normalized full name'
    ta, tb = match_name_tokens(a), match_name_tokens(b)
    if not ta or not tb:
        return 0, None
    if set(ta) == set(tb):
        return 50, 'Exact name tokens (order-independent)'
    overlap = set(ta) & set(tb)
    if len(overlap) >= 2 and (set(ta).issubset(set(tb)) or set(tb).issubset(set(ta))):
        return 35, 'Very strong fuzzy name match'
    if len(overlap) >= 2:
        return 35, 'Very strong fuzzy name match'
    if len(overlap) == 1:
        return 15, 'Partial/weak name match'
    return 0, None


def parse_extracted_dob_parts(dob_value):
    """Parse passport/OCR DOB into day/month/year ints. Returns dict or None."""
    raw = str(dob_value or '').strip()
    if not raw:
        return None
    named = re.match(
        r'^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{4})$',
        raw,
        re.IGNORECASE,
    )
    months = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    if named:
        return {
            'day': int(named.group(1)),
            'month': months[named.group(2)[:3].lower()],
            'year': int(named.group(3)),
        }
    slash = re.match(r'^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})$', raw)
    if slash:
        return {'day': int(slash.group(1)), 'month': int(slash.group(2)), 'year': int(slash.group(3))}
    return None


def score_intake_dob_match(extracted_dob, ch_officer_dob):
    """Score using only CH-provided DOB granularity (never invent CH DOB)."""
    extracted = parse_extracted_dob_parts(extracted_dob)
    if not extracted or not isinstance(ch_officer_dob, dict):
        return 0, None
    try:
        ch_year = int(ch_officer_dob['year']) if ch_officer_dob.get('year') is not None else None
        ch_month = int(ch_officer_dob['month']) if ch_officer_dob.get('month') is not None else None
        ch_day = int(ch_officer_dob['day']) if ch_officer_dob.get('day') is not None else None
    except (TypeError, ValueError):
        return 0, None
    if ch_year is None:
        return 0, None
    if extracted['year'] != ch_year:
        return -50, 'Incompatible DOB year'
    if ch_month is not None and extracted['month'] != ch_month:
        return -50, 'Incompatible DOB month'
    if ch_day is not None and extracted.get('day') is not None and extracted['day'] != ch_day:
        return -50, 'Incompatible DOB day'
    if ch_day is not None and ch_month is not None:
        return 35, 'Exact available DOB match'
    if ch_month is not None:
        return 25, 'Compatible month/year DOB'
    return 10, 'Compatible DOB year only'


def _company_status_score(status_text):
    status = (status_text or '').strip().lower()
    if not status:
        return 0, None
    if status in ('active', 'live', 'open'):
        return 10, 'Active company'
    if any(x in status for x in ('dissolved', 'liquidation', 'closed', 'removed', 'inactive')):
        return -20, 'Dissolved/inactive company'
    return 0, None


def company_status_hidden_from_list(status_text):
    """Hide dissolved / closed companies from portfolio lists (DB rows kept)."""
    status = str(status_text or '').strip().lower()
    if not status:
        return False
    return any(token in status for token in (
        'dissolved', 'liquidation', 'liquidat', 'converted-closed', 'closed', 'removed', 'inactive',
    ))


def filter_companies_for_portfolio_list(rows):
    return [row for row in (rows or []) if not company_status_hidden_from_list((row or {}).get('status'))]


def score_intake_company_candidate(candidate, client_id, person_name, extracted_dob, extracted_nationality=None):
    score = 0
    reasons = []

    # 1. Person Name Matching (Transposition & Token Overlap)
    person_fields = [
        candidate.get('officer_name'),
        candidate.get('director'),
    ]
    best_name_score = 0
    best_name_reason = None
    for field in person_fields:
        pts, reason = score_intake_name_match(person_name, field)
        if pts > best_name_score:
            best_name_score = pts
            best_name_reason = reason
    if best_name_score == 0:
        cname_pts, _ = score_intake_name_match(person_name, candidate.get('name'))
        if cname_pts >= 15:
            best_name_score = 5
            best_name_reason = 'Person name weakly related to company name'
    if best_name_score:
        score += best_name_score
        if best_name_reason:
            reasons.append(best_name_reason)

    # 2. DOB Compatibility Matching (Month & Year)
    dob_pts, dob_reason = score_intake_dob_match(extracted_dob, candidate.get('officer_dob'))
    if dob_pts:
        score += dob_pts
        if dob_reason:
            reasons.append(dob_reason)

    # 3. Nationality / Country Compatibility (identity triad)
    cand_nat = (candidate.get('officer_nationality') or candidate.get('nationality') or '').strip().lower()
    ext_nat = (extracted_nationality or '').strip().lower()
    if ext_nat and cand_nat:
        if (
            ext_nat in cand_nat
            or cand_nat in ext_nat
            or (ext_nat.startswith('pak') and 'pak' in cand_nat)
            or (ext_nat.startswith('brit') and 'brit' in cand_nat)
            or (ext_nat.startswith('ind') and 'ind' in cand_nat)
        ):
            score += 20
            reasons.append('Nationality matched')
        else:
            score -= 8
            reasons.append('Nationality differs from officer record')
    elif ext_nat and not cand_nat:
        reasons.append(f'Nationality from document: {extracted_nationality}')

    # 4. Existing CRM Relationship
    cid = candidate.get('company_id')
    if client_id and cid:
        owned = query_db(
            "SELECT id FROM companies WHERE id = ? AND user_id = ?;",
            (cid, client_id),
            one=True,
        )
        if owned:
            score += 40
            reasons.append('Existing CRM client already associated with company')
        order_link = query_db(
            "SELECT id FROM orders WHERE user_id = ? AND company_id = ? LIMIT 1;",
            (client_id, cid),
            one=True,
        )
        if order_link:
            score += 30
            reasons.append('Existing order associated with company')

    # 5. Companies House Officer Source
    if candidate.get('source') == 'companies_house' and candidate.get('company_number'):
        score += 15
        reasons.append('Verified Companies House officer appointment')
    elif candidate.get('source') == 'crm' and best_name_score >= 35:
        score += 25
        reasons.append('Strong CRM director/name relationship')

    # 6. Active Company Status
    status_pts, status_reason = _company_status_score(candidate.get('company_status'))
    if status_pts:
        score += status_pts
        if status_reason:
            reasons.append(status_reason)

    final_score = max(0, min(100, score))

    if final_score >= 75:
        label = 'HIGH'
    elif final_score >= 60:
        label = 'MEDIUM'
    else:
        label = 'LOW'

    return {
        'score': final_score,
        'confidence_label': label,
        'reasons': reasons,
        'candidate': candidate,
    }


def enrich_candidate_company_status(candidate, status_cache):
    num = normalize_company_number(candidate.get('company_number'))
    if not num:
        return candidate
    if num in status_cache:
        candidate['company_status'] = status_cache[num]
        return candidate
    if candidate.get('company_status'):
        status_cache[num] = candidate['company_status']
        return candidate
    payload, _err = cached_companies_house_request('/company/' + urllib.parse.quote(num))
    status = ''
    if payload and isinstance(payload, dict):
        status = str(payload.get('company_status') or '').strip().lower()
    status_cache[num] = status
    candidate['company_status'] = status
    return candidate


def enrich_candidate_company_officers(candidate, person_name, officer_cache):
    num = normalize_company_number(candidate.get('company_number'))
    if not num or candidate.get('officer_name'):
        return candidate
    if num in officer_cache:
        matched_officer = officer_cache[num]
        if matched_officer:
            candidate['officer_name'] = matched_officer.get('name') or ''
            candidate['officer_dob'] = matched_officer.get('dob')
        return candidate

    payload, _err = cached_companies_house_request(f'/company/{urllib.parse.quote(num)}/officers')
    matched = None
    if payload and isinstance(payload, dict):
        for item in payload.get('items') or []:
            off_name = (item.get('name') or '').strip()
            pts, _ = score_intake_name_match(person_name, off_name)
            if pts >= 35:
                matched = {
                    'name': off_name,
                    'dob': item.get('date_of_birth') if isinstance(item.get('date_of_birth'), dict) else None,
                }
                break
    officer_cache[num] = matched
    if matched:
        candidate['officer_name'] = matched.get('name') or ''
        candidate['officer_dob'] = matched.get('dob')
    return candidate


def resolve_intake_company_match(client_id, person_name, extracted_dob=None, extracted_nationality=None, actor=None):
    """
    Two-Stage Identity Resolution Engine:
    Stage 1: Resolve Person / Officer Identity against Companies House & CRM.
    Stage 2: Evaluate & rank officer company appointments with confidence score and gap analysis.
    Returns (company_row_or_None, status, message, ranked_candidates, match_meta)
    """
    name = (person_name or '').strip()
    empty_meta = {
        'confidence': 0,
        'confidence_label': 'LOW',
        'score': 0,
        'reasons': [],
        'score_gap': 0,
        'top_candidates': [],
    }
    if not name:
        return None, 'not_applicable', 'No person name to match against companies.', [], empty_meta

    # Check existing CRM client company relationships
    owned = []
    if client_id:
        owned = query_db(
            "SELECT * FROM companies WHERE user_id = ? ORDER BY id DESC LIMIT 5;",
            (client_id,),
        ) or []
    if len(owned) == 1:
        row = owned[0]
        meta = {
            'confidence': 95,
            'confidence_label': 'HIGH',
            'score': 95,
            'reasons': [
                'Existing CRM client already associated with company',
                'Single company relationship on client file',
            ],
            'score_gap': 95,
            'top_candidates': [{
                'name': row.get('name'),
                'company_number': row.get('company_number'),
                'score': 95,
                'confidence_label': 'HIGH',
                'reasons': ['Existing CRM client already associated with company'],
            }],
        }
        if actor:
            log_activity(
                actor,
                'DOCUMENT_AUTO_MATCHED',
                'companies',
                str(row['id']),
                f"Auto-matched client #{client_id} to existing CRM company #{row.get('company_number') or row['id']} (score=95)",
            )
        return row, 'matched', f"Automatically matched {row.get('name')} (existing CRM relationship)", [], meta

    raw_candidates, _err = search_companies_house_officers(name, extracted_dob=extracted_dob)
    if not raw_candidates:
        if actor:
            log_activity(
                actor,
                'DOCUMENT_MATCH_REVIEW_REQUIRED',
                'users',
                str(client_id or ''),
                f"No reliable company candidates for client #{client_id}",
            )
        return None, 'not_applicable', (
            f'No reliable company candidates for {name}. Document filed under client profile.'
        ), [], empty_meta

    status_cache = {}
    officer_cache = {}
    scored = []
    for cand in raw_candidates:
        enrich_candidate_company_status(cand, status_cache)
        enrich_candidate_company_officers(cand, name, officer_cache)
        if cand.get('company_id') and not cand.get('officer_name'):
            row = query_db("SELECT director FROM companies WHERE id = ?;", (cand['company_id'],), one=True)
            if row:
                cand['officer_name'] = row.get('director') or ''
        scored.append(score_intake_company_candidate(
            cand, client_id, name, extracted_dob, extracted_nationality=extracted_nationality
        ))

    scored.sort(key=lambda x: x['score'], reverse=True)
    top = scored[0]
    second = scored[1] if len(scored) > 1 else None
    top_score = top['score']
    second_score = second['score'] if second else 0
    score_gap = top_score - second_score

    ranked_preview = []
    for item in scored[:5]:
        c = item['candidate']
        ranked_preview.append({
            'name': c.get('name'),
            'company_number': c.get('company_number'),
            'company_id': c.get('company_id'),
            'address': c.get('address'),
            'score': item['score'],
            'confidence_label': item['confidence_label'],
            'reasons': item['reasons'],
            'source': c.get('source'),
            'officer_dob': c.get('officer_dob'),
        })

    meta = {
        'confidence': top_score,
        'confidence_label': top['confidence_label'],
        'score': top_score,
        'reasons': top['reasons'],
        'score_gap': score_gap,
        'top_candidates': ranked_preview,
    }

    # Auto-assign ONLY when top score >= 75 AND confidence gap >= 20 (or no second candidate)
    auto_ok = False
    if top_score >= 75 and (score_gap >= 20 or second is None) and not any(
        'Incompatible DOB' in (r or '') for r in top['reasons']
    ):
        auto_ok = True

    if auto_ok:
        chosen = top['candidate']
        company_obj = None
        if chosen.get('company_id'):
            company_obj = query_db("SELECT * FROM companies WHERE id = ?;", (chosen['company_id'],), one=True)
            if company_obj and client_id and not company_obj.get('user_id'):
                execute_db("UPDATE companies SET user_id = ? WHERE id = ?;", (client_id, company_obj['id']))
                company_obj = query_db("SELECT * FROM companies WHERE id = ?;", (company_obj['id'],), one=True)
        if not company_obj and chosen.get('company_number'):
            company_obj = auto_import_companies_house_from_intake(
                chosen.get('name'),
                chosen.get('company_number'),
                client_user_id=client_id,
            )
        if company_obj:
            msg = (
                f"Automatically matched {company_obj.get('name')} "
                f"(Confidence: {meta['confidence']}% — {meta['confidence_label']})"
            )
            if actor:
                log_activity(
                    actor,
                    'DOCUMENT_AUTO_MATCHED',
                    'companies',
                    str(company_obj['id']),
                    f"Auto-matched client #{client_id} to CH/CRM #{company_obj.get('company_number') or company_obj['id']} (score={top_score}, gap={score_gap})",
                )
            return company_obj, 'matched', msg, ranked_preview, meta

        status = 'review_required'
        msg = (
            f"Review required: top candidates have similar confidence "
            f"({ranked_preview[0]['name']} {top_score}% vs {ranked_preview[1]['name']} {second_score}%)."
        )
    elif top_score < 65:
        status = 'not_applicable'
        dob_blocked = any('Incompatible DOB' in (r or '') for r in (top.get('reasons') or []))
        if dob_blocked:
            msg = (
                f'No safe company match for {name}: Companies House officers with this name have different '
                f'birth details than the passport DOB ({extracted_dob or "unknown"}). '
                f'Best score {top_score}%. Document filed under the client; company left unassigned.'
            )
        else:
            msg = (
                f'No high-confidence company match for {name} (best {top_score}%). '
                'Document filed under the client; company left unassigned.'
            )
    else:
        status = 'review_required'
        msg = f'Review required for {name}: insufficient score gap for safe auto-assignment.'

    if actor:
        log_activity(
            actor,
            'DOCUMENT_MATCH_REVIEW_REQUIRED',
            'users',
            str(client_id or ''),
            f"Match review client #{client_id}: top={top_score} second={second_score} gap={score_gap}",
        )
    return None, status, msg, ranked_preview, meta


def pick_unique_intake_company(client_id, person_name, extracted_dob=None, actor=None):
    """Backward-compatible wrapper around resolve_intake_company_match."""
    company_obj, status, message, candidates, _meta = resolve_intake_company_match(
        client_id, person_name, extracted_dob=extracted_dob, actor=actor,
    )
    return company_obj, status, message, candidates


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


INTAKE_NAME_NOISE = frozenset({
    'name', 'of', 'the', 'and', 'for', 'passport', 'republic', 'islamic', 'pakistan',
    'united', 'kingdom', 'great', 'britain', 'nationality', 'gender', 'date', 'birth',
    'place', 'issue', 'expiry', 'type', 'code', 'authority', 'holder', 'customer',
    'account', 'statement', 'period', 'page', 'bank', 'ltd', 'limited', 'company',
    'director', 'subscriber', 'each', 'mr', 'mrs', 'ms', 'miss', 'dr', 'dob',
    'cnic', 'nic', 'nid', 'identity', 'card', 'document', 'utility', 'bill',
    'given', 'names', 'surname', 'father', 'husband', 'wife', 'son', 'daughter',
    'number', 'no', 'inc', 'corp', 'holdings', 'services', 'solutions', 'certificate',
    'incorporation', 'articles', 'association', 'confirmation', 'hmrc', 'vat',
})
INTAKE_FILENAME_IGNORE = frozenset({
    'bank', 'statement', 'passport', 'cnic', 'id', 'proof', 'address', 'doc', 'docx',
    'pdf', 'png', 'jpg', 'jpeg', 'certificate', 'incorporation', 'utility', 'bill',
    'ltd', 'limited', 'company', 'scan', 'copy', 'front', 'back', 'photo',
})


def _printable_text_ratio(text):
    if not text:
        return 0.0
    good = sum(1 for ch in text if ch.isprintable() or ch.isspace())
    return good / max(len(text), 1)


def _bytes_as_loose_text(file_bytes):
    if not file_bytes:
        return ''
    sample = file_bytes[:12000]
    if b'\x00' in sample[:400] and not sample.startswith(b'%PDF'):
        return ''
    try:
        text = file_bytes.decode('utf-8', errors='ignore')
    except Exception:
        text = ''
    if _printable_text_ratio(text) >= 0.82:
        return text
    if sample.startswith(b'%PDF'):
        return text
    return ''


def _mrz_quality_score(text):
    compact = re.sub(r'[^A-Z0-9<]', '', (text or '').upper())
    if not compact:
        return 0
    score = compact.count('<')
    if '<<' in compact:
        score += 25
    if re.search(r'(?:P<|[A-Z]{3})[A-Z]+<<[A-Z<]{2,}', compact):
        score += 40
    if re.search(r'[A-Z]{3}\d{6}\d[MF<]', compact):
        score += 30
    if any(code in compact for code in ('PAK', 'GBR', 'IND', 'USA', 'IRL', 'CAN', 'AUS')):
        score += 12
    return score


def _prepare_ocr_gray(image, sharpen=1.6, median=False, threshold=None, min_edge=2200):
    from PIL import Image, ImageOps, ImageEnhance, ImageFilter
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    if sharpen:
        gray = ImageEnhance.Sharpness(gray).enhance(sharpen)
    contrast = ImageEnhance.Contrast(gray)
    gray = contrast.enhance(1.15)
    if median:
        gray = gray.filter(ImageFilter.MedianFilter(size=3))
    if threshold is not None:
        gray = gray.point(lambda px: 255 if px > threshold else 0)
    width, height = gray.size
    edge = max(width, height)
    if edge < min_edge:
        scale = min_edge / edge
        resample = getattr(Image, 'Resampling', Image).LANCZOS if hasattr(Image, 'LANCZOS') else Image.BICUBIC
        gray = gray.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            resample=resample,
        )
    return gray


def _tesseract_string(image, psm='6'):
    try:
        import pytesseract
        return pytesseract.image_to_string(
            image,
            lang='eng',
            config=f'--oem 3 --psm {psm}',
        ) or ''
    except Exception:
        return ''


def _ocr_image_to_text(image, high_quality=True):
    """High-quality OCR for phone photos / scans that are not PDF text."""
    try:
        from PIL import ImageOps
    except Exception:
        return ''
    try:
        width, height = image.size
        candidates = []
        min_edge = 2600 if high_quality else 1800

        # Full page — avoid median blur (it destroys thin MRZ strokes).
        full = _prepare_ocr_gray(image, sharpen=1.55, median=False, min_edge=min_edge)
        for psm in ('6', '4', '3'):
            chunk = _tesseract_string(full, psm)
            if chunk.strip():
                candidates.append(chunk)

        # Sparse / mixed layout pass (invoices, statements, ID cards).
        if high_quality:
            sparse = _prepare_ocr_gray(image, sharpen=1.35, median=False, min_edge=min_edge)
            for psm in ('11', '12'):
                chunk = _tesseract_string(sparse, psm)
                if chunk.strip():
                    candidates.append(chunk)

        # Mild denoise pass for noisy phone photos.
        soft = _prepare_ocr_gray(image, sharpen=1.2, median=True, min_edge=min_edge)
        chunk = _tesseract_string(soft, '6')
        if chunk.strip():
            candidates.append(chunk)

        # High-contrast binarized pass for faded scans.
        if high_quality:
            for thresh in (145, 160):
                bin_img = _prepare_ocr_gray(
                    image, sharpen=1.3, median=False, threshold=thresh, min_edge=min_edge,
                )
                chunk = _tesseract_string(bin_img, '6')
                if chunk.strip():
                    candidates.append(chunk)

        # Biodata page is usually the lower half of an open passport photo.
        if height >= 900:
            biodata = image.crop((0, int(height * 0.42), width, height))
            bio_gray = _prepare_ocr_gray(biodata, sharpen=1.7, median=False, min_edge=max(min_edge, 2200))
            for psm in ('6', '4'):
                chunk = _tesseract_string(bio_gray, psm)
                if chunk.strip():
                    candidates.append(chunk)
            bio_bin = _prepare_ocr_gray(
                biodata, sharpen=1.2, median=False, threshold=150, min_edge=max(min_edge, 2200),
            )
            chunk = _tesseract_string(bio_bin, '6')
            if chunk.strip():
                candidates.append(chunk)

        # Thin MRZ strip at the bottom — high contrast, large scale.
        if height >= 700:
            mrz_band = image.crop((0, int(height * 0.82), width, height))
            mrz_gray = _prepare_ocr_gray(mrz_band, sharpen=2.0, median=False, min_edge=max(min_edge, 2600))
            for psm in ('6', '7'):
                chunk = _tesseract_string(mrz_gray, psm)
                if chunk.strip():
                    candidates.append(chunk)
            mrz_bin = _prepare_ocr_gray(
                mrz_band, sharpen=1.0, median=False, threshold=140, min_edge=max(min_edge, 2800),
            )
            chunk = _tesseract_string(mrz_bin, '6')
            if chunk.strip():
                candidates.append(chunk)

        if not candidates:
            return ''

        # Prefer the pass that preserves MRZ structure; fall back to longest text.
        return max(
            candidates,
            key=lambda t: (_mrz_quality_score(t), len(re.sub(r'\s+', '', t))),
        )
    except Exception:
        return ''


def _ocr_image_bytes(file_bytes, high_quality=True):
    try:
        import io
        from PIL import Image, ImageOps
        image = Image.open(io.BytesIO(file_bytes))
        image.load()
        image = ImageOps.exif_transpose(image)
        if image.mode not in ('L', 'RGB'):
            image = image.convert('RGB')
        return _ocr_image_to_text(image, high_quality=high_quality)
    except Exception:
        return ''


def _extract_pdf_text(file_bytes, high_quality=True):
    text = ''
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        text = '\n'.join((page.extract_text() or '') for page in reader.pages[:8])
    except Exception:
        text = ''
    if len(re.sub(r'\s+', '', text)) >= 40:
        return text, 'pdf-text'
    ocr_chunks = []
    try:
        import pypdfium2
        pdf = pypdfium2.PdfDocument(file_bytes)
        page_count = min(len(pdf), 4 if high_quality else 3)
        render_scale = 3.2 if high_quality else 2.5
        for index in range(page_count):
            page = pdf[index]
            bitmap = page.render(scale=render_scale)
            pil_image = bitmap.to_pil()
            ocr_chunks.append(_ocr_image_to_text(pil_image, high_quality=high_quality))
            try:
                page.close()
            except Exception:
                pass
        try:
            pdf.close()
        except Exception:
            pass
    except Exception:
        ocr_chunks = []
    ocr_text = '\n'.join(chunk for chunk in ocr_chunks if chunk).strip()
    if ocr_text:
        return ((text + '\n' + ocr_text).strip() if text.strip() else ocr_text), 'ocr'
    if text.strip():
        return text, 'pdf-text'
    loose = _bytes_as_loose_text(file_bytes)
    return loose, ('embedded-text' if loose.strip() else 'none')


def _extract_docx_text(file_bytes):
    try:
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            xml = archive.read('word/document.xml')
        text = re.sub(r'</w:p>', '\n', xml.decode('utf-8', errors='ignore'))
        text = re.sub(r'<[^>]+>', ' ', text)
        return re.sub(r'[ \t]+', ' ', text)
    except Exception:
        return ''


def _title_person_name(raw):
    words = [w for w in re.split(r'\s+', str(raw or '').strip()) if w]
    return ' '.join(w[:1].upper() + w[1:].lower() if w.isalpha() else w for w in words)


def _looks_like_ocr_gibberish(word):
    w = re.sub(r"[^a-z]", '', str(word or '').lower())
    if len(w) < 2:
        return True
    if re.search(r'(.)\1{2,}', w):
        return True
    vowels = sum(1 for ch in w if ch in 'aeiou')
    if len(w) >= 4 and vowels == 0:
        return True
    if len(w) >= 6 and vowels / len(w) < 0.18:
        return True
    if len(set(w)) <= 2 and len(w) >= 4:
        return True
    return False


def _valid_person_name(raw):
    text = re.sub(r'\s+', ' ', str(raw or '').strip())
    text = re.sub(r'[^A-Za-z\s\'\-]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    words = [w for w in text.split() if w]
    if len(words) < 2 or len(words) > 4:
        return None
    if len(text) < 5 or len(text) > 48:
        return None
    if sum(1 for w in words if len(w) >= 3) < 1:
        return None
    lowered = [w.lower().strip("'-") for w in words]
    if any(w in INTAKE_NAME_NOISE for w in lowered):
        return None
    if any(_looks_like_ocr_gibberish(w) for w in lowered):
        return None
    if not all(re.fullmatch(r"[A-Za-z][A-Za-z'\-]{0,24}", w) for w in words):
        return None
    return _title_person_name(' '.join(words))


def _compact_mrz_text(text):
    """Normalize noisy OCR into MRZ-friendly A-Z0-9< lines."""
    def repair_line(compact):
        if not compact:
            return compact
        # Common OCR: P misread as >, ), }, F, etc. before nationality code.
        if re.match(r'^[>\}\)F]<(?:PAK|GBR|IND|USA|IRL|CAN|AUS|DEU|FRA)', compact):
            compact = 'P' + compact[1:]
        elif re.match(r'^[>\}\)F](?:PAK|GBR|IND|USA|IRL|CAN|AUS|DEU|FRA)', compact) and ('<<' in compact or re.search(r'[KFXH]<', compact)):
            compact = 'P<' + compact[1:]
        elif compact.startswith('PC') and compact[2:5] in ('PAK', 'GBR', 'IND', 'USA', 'IRL', 'CAN', 'AUS'):
            compact = 'P<' + compact[2:]
        # << between surname and given is often OCR'd as K< / F< / X< / H<.
        compact = re.sub(
            r'(P<[A-Z]{3}[A-Z]{2,})[KFXH]<([A-Z]{2,})',
            r'\1<<\2',
            compact,
        )
        compact = re.sub(
            r'((?:PAK|GBR|IND|USA|IRL|CAN|AUS|DEU|FRA)[A-Z]{2,})[KFXH]<([A-Z]{2,})',
            r'\1<<\2',
            compact,
        )
        # Fillers after the name are often OCR'd as S/5/E/K between '<' marks.
        if '<<' in compact:
            head, _, tail = compact.partition('<<')
            tail = re.sub(r'(?<=<)[S5EKFX]+', lambda m: '<' * len(m.group(0)), tail)
            compact = head + '<<' + tail
        return compact

    cleaned_lines = []
    for raw_line in str(text or '').upper().replace('£', '<').replace('¢', '<').splitlines():
        compact = re.sub(r'[^A-Z0-9<]', '', raw_line)
        if not compact:
            continue
        cleaned_lines.append(repair_line(compact))
    blob = '\n'.join(cleaned_lines)
    # Also keep a fully compacted blob for cross-line MRZ recovery.
    full = repair_line(re.sub(r'[^A-Z0-9<]', '', str(text or '').upper().replace('£', '<').replace('¢', '<')))
    return blob + ('\n' + full if full and full not in blob else '')


def _parse_mrz_name(text):
    compact = _compact_mrz_text(text)
    match = re.search(r'P<[A-Z]{3}([A-Z]+)<<([A-Z<]{2,})', compact)
    if not match:
        # Recover when leading P< was lost but nationality+surname<<given remains.
        match = re.search(r'(?:^|[^A-Z])(?:PAK|GBR|IND|USA|IRL|CAN|AUS|DEU|FRA)([A-Z]{2,})<<([A-Z<]{2,})', compact)
        if not match:
            return None
        surname = match.group(1).replace('<', ' ').strip()
        given_raw = match.group(2)
    else:
        surname = match.group(1).replace('<', ' ').strip()
        given_raw = match.group(2)
    # Keep only real given-name tokens; ignore trailing OCR junk after fillers (e.g. ...<<<C<).
    given_match = re.match(r'^([A-Z]+(?:<[A-Z]+)*)', given_raw or '')
    given = given_match.group(1).replace('<', ' ').strip() if given_match else ''
    surname_words = [w for w in surname.split() if len(w) >= 2]
    given_words = [w for w in given.split() if len(w) >= 2]
    return _valid_person_name((' '.join(given_words + surname_words)).strip())


def _parse_mrz_dob(text):
    compact = _compact_mrz_text(text)
    match = re.search(r'[A-Z]{3}(\d{6})\d[MF<]', compact)
    if not match:
        return None
    yymmdd = match.group(1)
    year = int(yymmdd[0:2])
    month = int(yymmdd[2:4])
    day = int(yymmdd[4:6])
    if month < 1 or month > 12 or day < 1 or day > 31:
        return None
    full_year = 1900 + year if year >= 30 else 2000 + year
    return f'{day:02d}/{month:02d}/{full_year}'


def _parse_mrz_passport_num(text):
    compact = re.sub(r'[^A-Z0-9<\n]', '', text.upper())
    match = re.search(r'\b([A-Z0-9]{8,10})[A-Z]{3}\d{6}', compact)
    if match:
        return match.group(1).replace('<', '').strip()
    match_labeled = re.search(
        r'(?:passport\s*no\.?|passport\s*number|doc(?:ument)?\s*no\.?)\s*[:\-]?\s*([A-Z0-9]{7,12})',
        text,
        re.IGNORECASE,
    )
    if match_labeled:
        return match_labeled.group(1).strip()
    # Pakistani-style passport numbers often appear near PAK on the biodata page.
    near_pak = re.search(r'\b([A-Z]{1,2}\d{7,8})\b', compact)
    if near_pak and re.search(r'PAK|PASSPORT', compact):
        return near_pak.group(1)
    return None


def _parse_mrz_nationality(text):
    compact = _compact_mrz_text(text)
    match = re.search(r'P<([A-Z]{3})', compact)
    if not match:
        match = re.search(r'(?:^|[^A-Z])(PAK|GBR|IND|USA|IRL|CAN|AUS|DEU|FRA|NLD|ESP|ITA|POL|BGD|NGA|ZAF)[A-Z]+<<', compact)
    if match:
        code = match.group(1)
        country_map = {
            'PAK': 'Pakistani', 'GBR': 'British', 'USA': 'American',
            'IND': 'Indian', 'CAN': 'Canadian', 'AUS': 'Australian',
            'DEU': 'German', 'FRA': 'French', 'IRL': 'Irish',
            'NLD': 'Dutch', 'ESP': 'Spanish', 'ITA': 'Italian',
            'POL': 'Polish', 'BGD': 'Bangladeshi', 'NGA': 'Nigerian',
            'ZAF': 'South African',
        }
        return country_map.get(code, code)
    return None


def _extract_nationality(text):
    """Pull nationality from MRZ, labelled fields, or common passport/ID wording."""
    mrz = _parse_mrz_nationality(text)
    if mrz:
        return mrz
    blob = str(text or '')
    labeled = re.search(
        r'(?:nationality|citizenship|citizen of|nationalité)\s*[:\-]?\s*([A-Za-z][A-Za-z\s\-]{2,30})',
        blob,
        re.IGNORECASE,
    )
    if labeled:
        raw = re.sub(r'\s+', ' ', labeled.group(1)).strip(' ,.-')
        # Stop at next field labels that often follow on the same OCR line.
        raw = re.split(
            r'\b(?:sex|gender|date of birth|d\.?o\.?b|place of birth|passport|document)\b',
            raw,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(' ,.-')
        if raw and len(raw) >= 3 and raw.lower() not in INTAKE_NAME_NOISE:
            return raw.title()
    word_map = {
        r'\bpakistani\b': 'Pakistani',
        r'\bbritish\b|\bbritish citizen\b': 'British',
        r'\bindian\b': 'Indian',
        r'\bamerican\b|\bunited states\b': 'American',
        r'\birish\b': 'Irish',
        r'\bcanadian\b': 'Canadian',
        r'\baustralian\b': 'Australian',
        r'\bbangladeshi\b': 'Bangladeshi',
        r'\bnigerian\b': 'Nigerian',
        r'\bpolish\b': 'Polish',
    }
    for pattern, label in word_map.items():
        if re.search(pattern, blob, re.IGNORECASE):
            return label
    code_only = re.search(r'\b(PAK|GBR|IND|USA|IRL|CAN|AUS|BGD|NGA)\b', blob.upper())
    if code_only:
        return {
            'PAK': 'Pakistani', 'GBR': 'British', 'IND': 'Indian', 'USA': 'American',
            'IRL': 'Irish', 'CAN': 'Canadian', 'AUS': 'Australian',
            'BGD': 'Bangladeshi', 'NGA': 'Nigerian',
        }.get(code_only.group(1), code_only.group(1))
    return None


def _extract_person_name(text, filename_clean):
    mrz_name = _parse_mrz_name(text)
    if mrz_name:
        return mrz_name

    # Pakistani passport OCR layout often stacks:
    #   NASEEM ;
    #   Given Names
    #   PASSPORT ASIA
    stacked = re.search(
        r'(?ims)(?:^|\n)\s*([A-Za-z]{2,25})\s*;?\s*\n\s*Given Names?\s*\n\s*'
        r'(?:PASSPORT\s+|TYPE\s+P?\s*)?([A-Za-z]{2,25}(?:\s+[A-Za-z]{2,25}){0,2})\b',
        text,
    )
    if stacked:
        surname = stacked.group(1).strip()
        given = stacked.group(2).strip()
        if given.upper().startswith('PASSPORT '):
            given = given[9:].strip()
        if surname.lower() not in INTAKE_NAME_NOISE and given.lower() not in INTAKE_NAME_NOISE:
            candidate = _valid_person_name(f'{given} {surname}')
            if candidate:
                return candidate

    labeled = [
        r'(?:surname|family name)\s*[:\-]?\s*([A-Za-z][A-Za-z\'\-\s]{1,40})',
        r'(?:given names?|forenames?|first names?)\s*[:\-]?\s*([A-Za-z][A-Za-z\'\-\s]{1,40})',
        r'(?:account holder|customer name|client name|cardholder name|name of account holder)\s*[:\-]?\s*([A-Za-z][A-Za-z\'\-\s]{2,50})',
        r'(?:full name|holder name|passenger name)\s*[:\-]?\s*([A-Za-z][A-Za-z\'\-\s]{2,50})',
        r'(?:^|\n)\s*name\s*[:\-]\s*([A-Za-z][A-Za-z\'\-\s]{2,50})',
        r'\bname\s*[:\-]\s*([A-Za-z][A-Za-z\'\-\s]{2,50})',
    ]
    surname = None
    given = None
    for pattern in labeled[:2]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = _valid_person_name(match.group(1))
            if 'surname' in pattern or 'family' in pattern:
                surname = match.group(1).strip()
            else:
                given = match.group(1).strip()
            if candidate and 'name' not in pattern[:20]:
                pass
    if surname and given:
        if given.upper().startswith('PASSPORT '):
            given = given[9:].strip()
        combined = _valid_person_name(f'{given} {surname}')
        if combined:
            return combined
    for pattern in labeled:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if not match:
            continue
        raw = match.group(1).strip()
        if raw.upper().startswith('PASSPORT '):
            raw = raw[9:].strip()
        candidate = _valid_person_name(raw)
        if candidate:
            return candidate
    fallback = re.search(
        r'(?:director|client|customer|holder)\.?\s*[:\-]\s*([A-Za-z]+(?:\s+[A-Za-z]+){1,3})',
        text,
        re.IGNORECASE,
    )
    if fallback:
        candidate = _valid_person_name(fallback.group(1))
        if candidate:
            return candidate
    cleaned_fn = re.sub(r'[\._\-\(\)\[\]]', ' ', filename_clean)
    words = [w for w in cleaned_fn.split() if w.lower() not in INTAKE_FILENAME_IGNORE and w.isalpha() and len(w) >= 2]
    if len(words) >= 2:
        return _valid_person_name(' '.join(words[:3]))
    return None


def _normalize_display_dob(raw):
    text = re.sub(r'\s+', ' ', str(raw or '').strip())
    if not text:
        return None
    months = {
        'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
        'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
        'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
        'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
    }
    named = re.match(
        r'^(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})$',
        text,
        re.IGNORECASE,
    )
    if named:
        day = int(named.group(1))
        month = months.get(named.group(2).lower())
        year = int(named.group(3))
        if month and 1 <= day <= 31 and 1900 <= year <= 2100:
            return f'{day:02d}/{month:02d}/{year}'
    slash = re.match(r'^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})$', text)
    if slash:
        day, month, year = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
        if year < 100:
            year += 2000 if year < 30 else 1900
        if 1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100:
            return f'{day:02d}/{month:02d}/{year}'
    return text


def _extract_dob(text):
    mrz_dob = _parse_mrz_dob(text)
    if mrz_dob:
        return mrz_dob
    labeled = re.search(
        r'(?:date of birth|birth date|d\.?o\.?b\.?|born on|born)\s*[:\-]?\s*'
        r'(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2}|'
        r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{2,4})',
        text,
        re.IGNORECASE,
    )
    if labeled:
        return _normalize_display_dob(labeled.group(1))
    named = re.search(
        r'\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(?:19\d{2}|20[0-3]\d))\b',
        text,
        re.IGNORECASE,
    )
    if named:
        return _normalize_display_dob(named.group(1))
    loose = re.search(r'\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.](?:19\d{2}|20[01]\d))\b', text)
    if loose:
        return _normalize_display_dob(loose.group(1))
    return None


# Common bank/fintech names OCR picks up from statements — never auto-create these
# as the client's company unless the document filename itself carries that number.
_BANK_NOISE_COMPANY_NAMES = frozenset({
    'REVOLUT LTD', 'REVOLUT LIMITED', 'MONZO BANK LIMITED', 'MONZO LTD',
    'STARLING BANK LIMITED', 'STARLING BANK LTD', 'WISE PAYMENTS LIMITED',
    'TRANSFERWISE LTD', 'PAYPAL (EUROPE)', 'BARCLAYS BANK UK PLC',
    'BANK OF SCOTLAND PLC', 'BANK OF SCOTLAND', 'LLOYDS BANK PLC',
    'HSBC UK BANK PLC', 'NATWEST', 'NATIONAL WESTMINSTER BANK PLC',
    'SANTANDER UK PLC', 'THE ROYAL BANK OF SCOTLAND PLC',
})
_BANK_NOISE_COMPANY_NUMBERS = frozenset({
    '08804411',  # Revolut
    '09465813',  # Monzo
    '09064104',  # Starling
    'SC327000',  # Bank of Scotland
})


def extract_company_number_from_filename(filename):
    """Pull a UK company number from CH-style filenames (newinc/profile/proof)."""
    base = urllib.parse.unquote(os.path.basename(str(filename or '')).strip())
    if not base:
        return None
    patterns = (
        r'^((?:SC|NI|OC|SO)\d{6}|\d{8})(?=_)',
        r'(?i)(?:company[_\s\-]?profile|address[_\s\-]?proof)[_\s\-]?((?:SC|NI|OC|SO)\d{6}|\d{8})\b',
        r'(?:^|[^A-Za-z0-9])((?:SC|NI|OC|SO)\d{6})(?=[^0-9A-Za-z]|$)',
        r'(?:^|[^0-9])(\d{8})(?=[_\-\s.]|$)',
    )
    for pat in patterns:
        match = re.search(pat, base)
        if not match:
            continue
        num = normalize_company_number(match.group(1))
        if looks_like_uk_company_number(num):
            return num
    return None


def _extract_company_number(text, filename=None):
    match = re.search(
        r'(?:company(?:\s+(?:registration|registered))?\s+(?:number|no\.?)|co\.?\s*no\.?|registration no\.?|reg(?:istered)? no\.?)\s*[:\-]?\s*([0-9]{8}|[A-Za-z]{2}[0-9]{6})',
        text or '',
        re.IGNORECASE,
    )
    if match:
        return normalize_company_number(match.group(1))
    # Filename often carries the definitive CH number when OCR text is weak/empty.
    return extract_company_number_from_filename(filename or text)


def _is_bank_noise_company_candidate(name=None, company_number=None):
    num = normalize_company_number(company_number) if company_number else ''
    if num and num in _BANK_NOISE_COMPANY_NUMBERS:
        return True
    label = re.sub(r'\s+', ' ', str(name or '').strip()).upper()
    return bool(label) and label in _BANK_NOISE_COMPANY_NAMES


def parse_document_match_meta(doc):
    raw = (doc or {}).get('match_meta_json')
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def document_complete_company_signals(doc):
    """
    Return (company_number, company_name, source) when a document has enough
    data to place it on the Companies list. Prefer filename CH numbers.
    """
    if not doc:
        return None, None, None
    if doc.get('company_id'):
        existing = query_db(
            "SELECT name, company_number FROM companies WHERE id = ?;",
            (doc['company_id'],),
            one=True,
        )
        if existing:
            return (
                normalize_company_number(existing.get('company_number')),
                existing.get('name'),
                'linked',
            )

    fname_num = extract_company_number_from_filename(doc.get('name'))
    if fname_num:
        return fname_num, None, 'filename'

    meta = parse_document_match_meta(doc)
    for cand in (meta.get('top_candidates') or []):
        if not isinstance(cand, dict):
            continue
        num = normalize_company_number(cand.get('company_number'))
        name = (cand.get('name') or '').strip() or None
        if not looks_like_uk_company_number(num):
            continue
        if num.startswith('REG') or _is_bank_noise_company_candidate(name, num):
            continue
        score = float(cand.get('score') or doc.get('company_match_confidence') or 0)
        if score < 70:
            continue
        return num, name, 'match_meta'

    return None, None, None


def ensure_company_for_document(doc, actor=None, *, promote_lifecycle=True):
    """
    Create/link a CRM company for a document that already has complete company
    signals (filename CH number, prior link, or high-confidence match).
    Returns (company_row_or_None, status_string).
    """
    if not doc or not doc.get('id'):
        return None, 'missing_document'

    if doc.get('company_id'):
        existing = query_db("SELECT * FROM companies WHERE id = ?;", (doc['company_id'],), one=True)
        if existing:
            # Repair junk REG companies when filename carries a real CH number.
            fname_num = extract_company_number_from_filename(doc.get('name'))
            existing_num = normalize_company_number(existing.get('company_number'))
            if fname_num and (
                not looks_like_uk_company_number(existing_num)
                or str(existing_num or '').startswith('REG')
            ):
                repaired = auto_import_companies_house_from_intake(
                    None, fname_num, client_user_id=doc.get('user_id'),
                )
                if repaired and repaired.get('id') != existing.get('id'):
                    execute_db(
                        "UPDATE documents SET company_id = ?, company_match_confidence = 100, matched_at = CURRENT_TIMESTAMP, matched_by_id = ? WHERE id = ?;",
                        (repaired['id'], (actor or {}).get('id'), doc['id']),
                    )
                    return repaired, 'repaired'
            return existing, 'already_linked'

    company_num, company_name, source = document_complete_company_signals(doc)
    if not company_num and not company_name:
        return None, 'incomplete'

    company = auto_import_companies_house_from_intake(
        company_name, company_num, client_user_id=doc.get('user_id'),
    )
    if not company:
        return None, 'import_failed'

    new_lifecycle = None
    if promote_lifecycle and (doc.get('lifecycle_status') or '') in (
        'QUARANTINE', 'REVIEW_REQUIRED', 'CUSTOMER_UPLOADS', 'PROCESSING',
    ):
        new_lifecycle = 'READY_FOR_APPROVAL'

    if new_lifecycle:
        execute_db(
            """
            UPDATE documents SET
                company_id = ?,
                company_match_confidence = 100,
                lifecycle_status = ?,
                matched_at = CURRENT_TIMESTAMP,
                matched_by_id = ?,
                review_notes = COALESCE(?, review_notes)
            WHERE id = ?;
            """,
            (
                company['id'],
                new_lifecycle,
                (actor or {}).get('id'),
                f"Linked to {company.get('name')} via {source}",
                doc['id'],
            ),
        )
    else:
        execute_db(
            """
            UPDATE documents SET
                company_id = ?,
                company_match_confidence = 100,
                matched_at = CURRENT_TIMESTAMP,
                matched_by_id = ?,
                review_notes = COALESCE(?, review_notes)
            WHERE id = ?;
            """,
            (
                company['id'],
                (actor or {}).get('id'),
                f"Linked to {company.get('name')} via {source}",
                doc['id'],
            ),
        )

    log_document_audit(
        doc['id'],
        'HUMAN' if actor else 'SYSTEM',
        (actor or {}).get('id'),
        (actor or {}).get('full_name') or 'system',
        'COMPANY_LINKED',
        doc.get('lifecycle_status'),
        new_lifecycle or doc.get('lifecycle_status'),
        {
            'source': source,
            'company_id': company.get('id'),
            'company_name': company.get('name'),
            'company_number': company.get('company_number'),
        },
    )
    return company, 'linked'


def _extract_company_name(text):
    match = re.search(
        r'\b([A-Z][A-Za-z0-9\&\'\.\-]+(?:\s+[A-Z][A-Za-z0-9\&\'\.\-]+){0,6}\s+(?:LTD|LIMITED|LLP|PLC))\b',
        text,
    )
    if not match:
        match = re.search(
            r'([A-Za-z0-9\&\'\.\-]{2,40}(?:\s+[A-Za-z0-9\&\'\.\-]{2,40}){0,5}\s+(?:LTD|LIMITED|LLP|PLC))',
            text,
            re.IGNORECASE,
        )
    if not match:
        return None
    name = re.sub(r'\s+', ' ', match.group(1)).strip(' ,.-')
    lowered = name.lower()
    if lowered.startswith(('bank statement', 'name of', 'certificate of')):
        return None
    return name


def _extract_email_phone(text):
    email_match = re.search(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b', text)
    phone_match = re.search(r'(?:\+44\s?7\d{3}|\(?0\d{4}\)?)\s?\d{3}\s?\d{3}\b|\+92[0-9\s\-]{10,14}|\b03\d{2}[-\s]?\d{7}\b', text)
    email = email_match.group(0) if email_match else None
    if email and email.lower().endswith('@brixen-pending.local'):
        email = None
    return email, (phone_match.group(0).strip() if phone_match else None)


def _looks_like_passport_text(filename_clean, text):
    lower_text = f'{filename_clean}\n{text}'.lower()
    compact = _compact_mrz_text(text)
    if '<<' in compact and re.search(r'(?:P<|[A-Z]{3})[A-Z]+<<[A-Z<]{2,}', compact):
        return True
    if re.search(r'\b\d{5}-\d{7}-\d\b', text):  # Pakistani CNIC / citizenship no.
        return True
    if any(k in lower_text for k in (
        'passport', 'cnic', 'id card', 'driving licence', 'driving license',
        'national identity', 'nicop', 'islamic republic', 'pakistani',
        'machine readable', 'given names', 'surname',
    )):
        return True
    if _mrz_quality_score(text) >= 40:
        return True
    return False


def _classify_intake_document(filename_clean, text):
    lower_text = f'{filename_clean}\n{text}'.lower()
    if _looks_like_passport_text(filename_clean, text):
        return 'ID Document'
    if any(k in lower_text for k in ('invoice', 'tax invoice', 'amount due', 'vat number')) and not any(
        k in lower_text for k in ('passport', 'bank statement', 'certificate of incorporation')
    ):
        return 'Invoice'
    if any(k in lower_text for k in ('statement', 'sort code', 'iban', 'account number', 'hsbc', 'barclays', 'lloyds', 'natwest', 'tide', 'revolut', 'monzo', 'santander')):
        return 'Bank statement'
    if any(k in lower_text for k in ('utility', 'council tax', 'tenancy', 'proof of address', 'water bill', 'electric', 'gas bill')):
        return 'Proof of Address'
    if any(k in lower_text for k in ('certificate of incorporation', 'companies house', 'articles of association', 'confirmation statement', 'utr')):
        return 'Certificate of Incorporation'
    return 'Company Documents'


def _intake_extraction_quality(person_name, dob, passport_num, nationality, cat, scan_method, raw_text):
    """Score identity extraction only: name, date of birth, nationality."""
    hits = 0
    total = 3
    if person_name:
        hits += 1
    if dob:
        hits += 1
    if nationality:
        hits += 1
    pct = int(round((hits / total) * 100))
    label = 'High' if pct >= 67 else ('Medium' if pct >= 34 else ('Low' if hits else 'None'))
    return {
        'score': pct,
        'label': label,
        'fields_found': hits,
        'fields_checked': total,
        'identity_fields': {
            'name': bool(person_name),
            'dob': bool(dob),
            'nationality': bool(nationality),
        },
        'ocr_used': scan_method == 'ocr',
        'mrz_score': _mrz_quality_score(raw_text or ''),
        'passport_number_seen': bool(passport_num),
        'document_category': cat,
    }


def _extract_bank_account_holder_name(text):
    """Pull account-holder / customer name from UK bank statement OCR text."""
    blob = str(text or '')
    patterns = [
        r'(?im)^\s*account\s*holder\s*[:\-]?\s*([A-Z][A-Za-z\'\-\s]{2,60})\s*$',
        r'(?im)^\s*account\s*name\s*[:\-]?\s*([A-Z][A-Za-z\'\-\s]{2,60})\s*$',
        r'(?im)^\s*customer\s*name\s*[:\-]?\s*([A-Z][A-Za-z\'\-\s]{2,60})\s*$',
        r'(?im)^\s*name\s*[:\-]\s*([A-Z][A-Za-z\'\-\s]{2,60})\s*$',
        r'(?i)account\s*holder\s*[:\-]?\s*([A-Za-z][A-Za-z\'\-\s]{2,60})',
        r'(?i)account\s*name\s*[:\-]?\s*([A-Za-z][A-Za-z\'\-\s]{2,60})',
        r'(?i)customer\s*(?:name)?\s*[:\-]?\s*([A-Za-z][A-Za-z\'\-\s]{2,60})',
        r'(?i)(?:mr|mrs|ms|miss)\s+([A-Za-z][A-Za-z\'\-]+(?:\s+[A-Za-z][A-Za-z\'\-]+){1,3})\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, blob)
        if not match:
            continue
        raw = re.sub(r'\s+', ' ', match.group(1)).strip(' ,.-')
        # Skip company-style account names here; those are handled as company names.
        if re.search(r'\b(?:ltd|limited|llp|plc|cic)\b', raw, re.IGNORECASE):
            continue
        if any(tok in raw.lower() for tok in ('statement', 'period', 'sort code', 'account number', 'iban')):
            continue
        candidate = _valid_person_name(raw)
        if candidate:
            return candidate
    return None


def intake_person_names_match(left, right):
    """True when passport/statement names are the same person (exact or strong fuzzy)."""
    pts, reason = score_intake_name_match(left, right)
    return pts >= 35, pts, reason


def find_batch_identity_for_name(person_name, batch_identities):
    best = None
    best_pts = 0
    best_reason = None
    for identity in batch_identities or []:
        if not isinstance(identity, dict):
            continue
        ok, pts, reason = intake_person_names_match(person_name, identity.get('full_name'))
        if ok and pts > best_pts:
            best = identity
            best_pts = pts
            best_reason = reason
    return best, best_pts, best_reason


def find_identity_mentioned_in_text(text, batch_identities):
    """If structured name OCR missed, detect a known batch identity name inside document text."""
    blob = normalize_match_person_name(text)
    if not blob:
        return None, 0, None
    best = None
    best_pts = 0
    best_reason = None
    for identity in batch_identities or []:
        if not isinstance(identity, dict):
            continue
        name = identity.get('full_name')
        tokens = match_name_tokens(name)
        if len(tokens) < 2:
            continue
        if all(tok in blob for tok in tokens):
            pts = 50 if set(tokens) == set(match_name_tokens(name)) else 35
            if pts > best_pts:
                best = identity
                best_pts = pts
                best_reason = 'Name found inside document text'
    return best, best_pts, best_reason


def find_recent_passport_identity_for_name(person_name, limit=40):
    """Link any document name to a recently ingested passport/ID / intake identity in CRM."""
    if not (person_name or '').strip():
        return None, 0, None
    rows = query_db(
        """
        SELECT d.id, d.user_id, d.company_id, d.category, d.match_meta_json, d.ch_status,
               u.full_name as client_name, c.name as company_name, c.company_number
        FROM documents d
        LEFT JOIN users u ON d.user_id = u.id
        LEFT JOIN companies c ON d.company_id = c.id
        WHERE COALESCE(d.is_posted, 0) = 0
          AND COALESCE(d.lifecycle_status, '') != 'QUARANTINE'
          AND (
                LOWER(COALESCE(d.category, '')) IN ('id document', 'passport')
                OR LOWER(COALESCE(d.review_notes, '')) LIKE '%smart document intake%'
              )
        ORDER BY d.id DESC
        LIMIT ?;
        """,
        (limit,),
    ) or []
    best = None
    best_pts = 0
    best_reason = None
    for row in rows:
        meta = {}
        if row.get('match_meta_json'):
            try:
                meta = json.loads(row['match_meta_json']) or {}
            except Exception:
                meta = {}
        candidates = [
            row.get('client_name'),
            meta.get('extracted_name'),
            meta.get('person_name'),
        ]
        for top in (meta.get('top_candidates') or []):
            if isinstance(top, dict) and top.get('officer_name'):
                candidates.append(top.get('officer_name'))
        for cand_name in candidates:
            ok, pts, reason = intake_person_names_match(person_name, cand_name)
            if ok and pts > best_pts:
                best_pts = pts
                best_reason = reason
                best = {
                    'full_name': cand_name,
                    'dob': meta.get('extracted_dob') or meta.get('dob'),
                    'nationality': meta.get('extracted_nationality') or meta.get('nationality'),
                    'client_id': row.get('user_id'),
                    'company_id': row.get('company_id'),
                    'company_name': row.get('company_name'),
                    'company_number': row.get('company_number'),
                    'source_document_id': row.get('id'),
                    'source': 'recent_id_or_intake',
                    'confidence': meta.get('confidence') or pts,
                }
    return best, best_pts, best_reason


def extract_document_text_and_metadata(filename, b64_content, high_quality=True):
    file_bytes, decode_err = decode_document_base64(b64_content)
    if decode_err or not file_bytes:
        return {'error': decode_err or 'Empty file'}

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    ocr_version = 'v2.2-identity' if high_quality else 'v2.0'

    # SHA-256 Deduplication Cache Check (<5ms hit)
    cached_entry = query_db(
        "SELECT extracted_text, metadata_json FROM ocr_cache WHERE file_hash = ? AND processing_version = ?;",
        (file_hash, ocr_version),
        one=True,
    )
    if cached_entry:
        try:
            cached_meta = json.loads(cached_entry['metadata_json'])
            cached_meta['file_bytes'] = file_bytes
            cached_meta['file_hash'] = file_hash
            cached_meta['cached'] = True
            return cached_meta
        except Exception:
            pass

    filename_clean = (filename or 'document.pdf').strip()
    ext = os.path.splitext(filename_clean)[1].lower()
    scan_method = 'none'
    raw_text = ''

    if ext == '.pdf':
        raw_text, scan_method = _extract_pdf_text(file_bytes, high_quality=high_quality)
    elif ext in ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff'):
        raw_text = _ocr_image_bytes(file_bytes, high_quality=high_quality)
        scan_method = 'ocr' if raw_text.strip() else 'none'
    elif ext in ('.doc', '.docx'):
        raw_text = _extract_docx_text(file_bytes)
        scan_method = 'docx-text' if raw_text.strip() else 'none'
    elif ext in ('.txt',):
        raw_text = _bytes_as_loose_text(file_bytes)
        scan_method = 'embedded-text'

    if not raw_text.strip():
        raw_text = _bytes_as_loose_text(file_bytes)
        if raw_text.strip() and scan_method == 'none':
            scan_method = 'embedded-text'

    combined_text = f"{filename_clean}\n{raw_text}"
    # Core identity triad — name, date of birth, nationality — drives matching.
    person_name = _extract_person_name(combined_text, filename_clean)
    dob = _extract_dob(combined_text)
    nationality = _extract_nationality(combined_text)
    company_number = _extract_company_number(combined_text, filename_clean)
    company_name = _extract_company_name(combined_text)
    email, phone = _extract_email_phone(combined_text)

    cat = _classify_intake_document(filename_clean, raw_text)
    is_passport = cat in ('ID Document', 'Passport') or 'passport' in filename_clean.lower() or _looks_like_passport_text(filename_clean, raw_text)
    if is_passport and cat == 'Company Documents':
        cat = 'ID Document'
    if cat == 'Bank statement':
        holder = _extract_bank_account_holder_name(combined_text)
        if holder:
            person_name = holder
    passport_num = _parse_mrz_passport_num(combined_text) if is_passport else None
    # Identity documents: ignore noisy company numbers — match via name/DOB/nationality.
    identity_primary = is_passport or cat in ('ID Document', 'Passport')
    if identity_primary:
        company_number = None
        company_name = None
    doc_type = 'Passport' if is_passport else cat
    quality = _intake_extraction_quality(person_name, dob, passport_num, nationality, cat, scan_method, raw_text)

    result_dict = {
        'file_bytes': file_bytes,
        'file_hash': file_hash,
        'filename': filename_clean,
        'ext': ext,
        'category': cat,
        'doc_type': doc_type,
        'extracted_name': person_name,
        'extracted_dob': dob,
        'extracted_passport_num': passport_num,
        'extracted_nationality': nationality,
        'extracted_company_number': company_number,
        'extracted_company_name': company_name,
        'extracted_email': email,
        'extracted_phone': phone,
        'identity_primary': identity_primary,
        'scan_method': scan_method,
        'extraction_quality': quality,
        'text_preview': re.sub(r'\s+', ' ', raw_text).strip()[:1200],
        'cached': False,
        'high_quality_ocr': bool(high_quality),
    }

    # Save to ocr_cache table
    try:
        cache_copy = dict(result_dict)
        cache_copy.pop('file_bytes', None)
        execute_db("""
            INSERT OR REPLACE INTO ocr_cache (file_hash, extracted_text, metadata_json, processing_version)
            VALUES (?, ?, ?, ?);
        """, (file_hash, raw_text, json.dumps(cache_copy), ocr_version))
    except Exception:
        pass

    return result_dict


INTAKE_ALLOWED_EXTENSIONS = frozenset({
    '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff',
})
INTAKE_JUNK_BASENAMES = frozenset({
    '.ds_store', 'thumbs.db', 'desktop.ini', '.localized', 'ehthumbs.db',
})
_INTAKE_PASSPORT_RE = re.compile(r'passport', re.I)
_INTAKE_ID_CARD_RE = re.compile(
    r'(?:\bid\b[\s_-]?card|identity\s*card|national\s*id|cnic|nicop|\bnid\b|\bnic\b|id[\s_-]?verif)',
    re.I,
)
_INTAKE_LICENCE_RE = re.compile(r'driving\s*licen[cs]e|\bdvla\b|\blicen[cs]e\b', re.I)
_INTAKE_STATEMENT_RE = re.compile(
    r'bank\s*statement|\bstatement\b|account\s*statement|balance\s*statement',
    re.I,
)
_INTAKE_COMPANY_RE = re.compile(
    r'certificate\s*of\s*incorporat|incorporat|newinc|company[\s_-]?profile|'
    r'confirmation\s*statement|psc0[0-9]|ap0[0-9]|tm0[0-9]|\baa_|'
    r'memorandum|articles\s*of\s*association|companies\s*house|company\s*doc|'
    r'share\s*cert|officer|director\s*list|subscriber',
    re.I,
)
_INTAKE_COMPANY_NUM_RE = re.compile(
    r'(?:^|[^0-9A-Za-z])((?:SC|NI|OC|SO)\d{6}|\d{8})(?=[_\-\s.]|$)',
    re.I,
)
_INTAKE_BLOCKED_RE = re.compile(r'\breceipt\b', re.I)
_INTAKE_INVOICE_RE = re.compile(r'invoice|tax\s*invoice', re.I)
_INTAKE_CONTENT_INVOICE_RE = re.compile(
    r'\binvoice\b|\btax invoice\b|\breceipt\b|\bamount due\b|\bvat number\b',
    re.I,
)
INTAKE_ID_KINDS = frozenset({'Passport', 'Identity Card', 'Driving Licence'})
INTAKE_ALLOWED_KINDS = frozenset({
    'Passport', 'Identity Card', 'Driving Licence',
    'Bank Statement', 'Company Document', 'Invoice',
})


def normalize_intake_document_kind(raw):
    text = re.sub(r'\s+', ' ', str(raw or '').strip()).lower()
    if not text or text in ('auto', 'auto-detect', 'document'):
        return None
    if text in ('passport',):
        return 'Passport'
    if text in ('identity card', 'id card', 'id', 'id document'):
        return 'Identity Card'
    if text in ('driving licence', 'driving license', 'licence', 'license'):
        return 'Driving Licence'
    if text in ('bank statement', 'statement'):
        return 'Bank Statement'
    if text in ('company document', 'company documents', 'company'):
        return 'Company Document'
    if text in ('invoice', 'invoices'):
        return 'Invoice'
    # Already-normalized labels
    for kind in INTAKE_ALLOWED_KINDS:
        if text == kind.lower():
            return kind
    return None


def intake_kind_to_category(kind):
    if kind in ('Passport', 'Identity Card', 'Driving Licence'):
        return 'ID Document'
    if kind == 'Bank Statement':
        return 'Bank statement'
    if kind == 'Invoice':
        return 'Invoice'
    if kind == 'Company Document':
        return 'Company Documents'
    return 'Company Documents'


def classify_intake_document_kind(path_label):
    text = str(path_label or '')
    if _INTAKE_PASSPORT_RE.search(text):
        return 'Passport'
    if _INTAKE_ID_CARD_RE.search(text):
        return 'Identity Card'
    if _INTAKE_LICENCE_RE.search(text):
        return 'Driving Licence'
    if _INTAKE_STATEMENT_RE.search(text):
        return 'Bank Statement'
    if _INTAKE_COMPANY_RE.search(text) or _INTAKE_COMPANY_NUM_RE.search(text):
        return 'Company Document'
    if _INTAKE_INVOICE_RE.search(text):
        return 'Invoice'
    return None


def classify_intake_document_kind_from_content(filename, text):
    """Detect allowed doc type from OCR/text when the filename is generic (e.g. Screenshot)."""
    blob = f"{filename or ''}\n{text or ''}"
    lower = blob.lower()
    if _looks_like_passport_text(filename, text) or _INTAKE_PASSPORT_RE.search(blob):
        return 'Passport'
    if (
        'islamic republic of pakistan' in lower
        or 'ministry of interior' in lower
        or re.search(r'p<[a-z]{3}[a-z<]+<<', lower)
        or 'given names' in lower and 'surname' in lower and 'nationality' in lower
    ):
        return 'Passport'
    if (
        _INTAKE_ID_CARD_RE.search(blob)
        or 'national identity' in lower
        or 'citizenship number' in lower
        or re.search(r'\b\d{5}-\d{7}-\d\b', blob)  # Pakistani CNIC
    ):
        return 'Identity Card'
    if _INTAKE_LICENCE_RE.search(blob) or 'driving licence' in lower or 'driving license' in lower:
        return 'Driving Licence'
    if (
        _INTAKE_STATEMENT_RE.search(blob)
        or 'sort code' in lower
        or 'iban' in lower
        or 'account number' in lower
        or 'account holder' in lower
    ):
        return 'Bank Statement'
    if (
        _INTAKE_COMPANY_RE.search(blob)
        or _INTAKE_COMPANY_NUM_RE.search(blob)
        or 'companies house' in lower
        or 'certificate of incorporation' in lower
    ):
        return 'Company Document'
    if _INTAKE_CONTENT_INVOICE_RE.search(blob):
        return 'Invoice'
    return classify_intake_document_kind(filename)


def assess_intake_file_relevance(
    filename, *, source_mode='CUSTOMER_UPLOADS', folder_path=None, size_bytes=None, forced_kind=None,
):
    """Return (ok: bool, reason: str). Screenshots allowed; invoices allowed when selected/detected."""
    path = str(folder_path or filename or '').strip() or 'document.pdf'
    base = os.path.basename(urllib.parse.unquote(str(filename or path)))
    lower_base = base.lower()
    lower_path = path.lower().replace('\\', '/')
    ext = os.path.splitext(base)[1].lower()
    mode = (source_mode or 'CUSTOMER_UPLOADS').strip().upper() or 'CUSTOMER_UPLOADS'
    forced = normalize_intake_document_kind(forced_kind)

    if ext not in INTAKE_ALLOWED_EXTENSIONS:
        return False, f'Unsupported type ({ext or "unknown"}) — use PDF, JPEG, or PNG'
    if lower_base in INTAKE_JUNK_BASENAMES:
        return False, 'System/junk file'
    if any(part in lower_path for part in ('__macosx', '/.git/', '/.svn/', '/node_modules/', '/.trash/')):
        return False, 'Ignored folder path'
    if size_bytes is not None:
        try:
            size_n = int(size_bytes)
        except (TypeError, ValueError):
            size_n = None
        if size_n is not None and size_n < 800:
            return False, 'File too small / empty'
        if size_n is not None and size_n > 45 * 1024 * 1024:
            return False, 'File too large (max 45MB)'

    if forced:
        if mode == 'ID_ONLY_DISCOVERY' and forced not in INTAKE_ID_KINDS:
            return False, 'ID-Only mode: passport / ID card / driving licence only'
        return True, forced

    # Soft-skip bare receipts unless filename also looks like an invoice.
    if _INTAKE_BLOCKED_RE.search(f'{lower_path} {lower_base}') and not _INTAKE_INVOICE_RE.search(
        f'{lower_path} {lower_base}'
    ):
        return False, 'Receipts skipped — select Invoice type to include them'

    kind = classify_intake_document_kind(f'{lower_path} {lower_base}')
    if kind:
        if mode == 'ID_ONLY_DISCOVERY' and kind not in INTAKE_ID_KINDS:
            return False, 'ID-Only mode: passport / ID card / driving licence only'
        return True, kind

    # Generic Screenshot_*.png / camera images: queue and verify with OCR on process.
    if ext in INTAKE_ALLOWED_EXTENSIONS:
        return True, 'Scan queued — high-quality OCR will confirm document type'
    return False, 'Only passports, licences, ID cards, bank statements, company docs, or invoices'


def process_smart_intake_files(files_data, user, batch_identities=None, source_mode='CUSTOMER_UPLOADS'):
    """
    Process one or more Smart Intake files.
    Each file is OCR'd and matched independently so mixed batches stay accurate
    and callers can process one file per HTTP request (avoids nginx 120s 504s).

    batch_identities: prior ID/passport identities from the same browser batch so
    any other document containing the same person name inherits that client + company
    (same form / same person filing).
    Returns (payload_dict, http_status_or_None).
    """
    if not files_data or not isinstance(files_data, list):
        return {'status': 'error', 'message': 'No files provided for intake'}, "400 Bad Request"

    identities = []
    for item in (batch_identities or []):
        if isinstance(item, dict) and (item.get('full_name') or item.get('client_id')):
            identities.append(dict(item))

    file_results = []
    filed_documents = []
    filing_errors = []
    skipped_irrelevant = []
    last_client = None
    last_company = None
    last_match_meta = {}
    last_ch_status = 'no_ch_match'
    last_ch_msg = ''
    last_dob = None
    last_nationality = None
    any_ocr = False
    methods = []

    for file_item in files_data:
        fname = (file_item.get('file_name') or 'document.pdf').strip()
        folder_path = (file_item.get('folder_path') or fname).strip()
        forced_kind = normalize_intake_document_kind(
            file_item.get('document_type')
            or file_item.get('forced_document_type')
        )
        allowed_types = []
        raw_allowed = file_item.get('allowed_types')
        if isinstance(raw_allowed, str) and raw_allowed.strip():
            try:
                raw_allowed = json.loads(raw_allowed)
            except Exception:
                raw_allowed = [p.strip() for p in raw_allowed.split(',') if p.strip()]
        if isinstance(raw_allowed, (list, tuple)):
            for item in raw_allowed:
                kind = normalize_intake_document_kind(item)
                if kind and kind not in allowed_types:
                    allowed_types.append(kind)
        hq_ocr = str(file_item.get('hq_ocr') if file_item.get('hq_ocr') is not None else '1').strip().lower() not in (
            '0', 'false', 'no', '',
        )
        b64 = file_item.get('file_content_base64', '')
        size_bytes = None
        if b64:
            try:
                size_bytes = int(len(b64) * 0.75)
            except Exception:
                size_bytes = None
        ok_relevant, reason = assess_intake_file_relevance(
            fname,
            source_mode=source_mode,
            folder_path=folder_path,
            size_bytes=size_bytes,
            forced_kind=forced_kind,
        )
        if not ok_relevant:
            skipped_irrelevant.append({'file_name': fname, 'reason': reason})
            filing_errors.append(f"{fname}: skipped — {reason}")
            file_results.append({
                'file_name': fname,
                'filing_status': {'status': 'skipped', 'message': reason},
                'document_processing': {'status': 'skipped', 'reason': reason},
                'companies_house_matching': {'status': 'not_applicable', 'message': reason},
            })
            continue
        res = extract_document_text_and_metadata(fname, b64, high_quality=hq_ocr)
        if 'error' in res:
            filing_errors.append(f"{fname}: {res.get('error')}")
            file_results.append({
                'file_name': fname,
                'filing_status': {'status': 'failed', 'message': res.get('error')},
                'document_processing': {'status': 'failed'},
                'companies_house_matching': {'status': 'failed', 'message': res.get('error')},
            })
            continue

        # Confirm allowed type from OCR when filename was generic (Screenshot / photo).
        content_kind = None
        if res.get('doc_type') in ('Passport', 'ID Document') or res.get('category') == 'ID Document':
            content_kind = 'Passport'
        elif res.get('category') == 'Bank statement':
            content_kind = 'Bank Statement'
        elif res.get('category') == 'Invoice':
            content_kind = 'Invoice'
        elif res.get('extracted_company_number') or res.get('extracted_company_name'):
            content_kind = 'Company Document'
        elif res.get('extracted_passport_num') or (
            res.get('extracted_name') and res.get('extracted_dob') and res.get('extracted_nationality')
        ):
            content_kind = 'Passport'
        if not content_kind:
            content_kind = classify_intake_document_kind_from_content(
                fname, res.get('text_preview') or '',
            )
        name_kind = classify_intake_document_kind(f'{folder_path} {fname}')
        # Admin-selected type wins; otherwise filename then OCR content.
        final_kind = forced_kind or name_kind or content_kind
        if allowed_types:
            if forced_kind and forced_kind in allowed_types:
                final_kind = forced_kind
            elif final_kind and final_kind not in allowed_types:
                skip_msg = (
                    f'Skipped ({final_kind}) — not in selected options: '
                    + ', '.join(allowed_types)
                )
                skipped_irrelevant.append({'file_name': fname, 'reason': skip_msg})
                filing_errors.append(f"{fname}: skipped — {skip_msg}")
                file_results.append({
                    'file_name': fname,
                    'filing_status': {'status': 'skipped', 'message': skip_msg},
                    'document_processing': {'status': 'skipped', 'reason': skip_msg},
                    'companies_house_matching': {'status': 'not_applicable', 'message': skip_msg},
                })
                continue
            elif not final_kind:
                skip_msg = (
                    'Not recognised within selected options: '
                    + ', '.join(allowed_types)
                )
                skipped_irrelevant.append({'file_name': fname, 'reason': skip_msg})
                filing_errors.append(f"{fname}: skipped — {skip_msg}")
                file_results.append({
                    'file_name': fname,
                    'filing_status': {'status': 'skipped', 'message': skip_msg},
                    'document_processing': {'status': 'skipped', 'reason': skip_msg},
                    'companies_house_matching': {'status': 'not_applicable', 'message': skip_msg},
                })
                continue
        if not final_kind:
            skip_msg = (
                'Not recognised as passport, ID card, driving licence, bank statement, '
                'company document, or invoice — pick a type and retry'
            )
            skipped_irrelevant.append({'file_name': fname, 'reason': skip_msg})
            filing_errors.append(f"{fname}: skipped — {skip_msg}")
            file_results.append({
                'file_name': fname,
                'filing_status': {'status': 'skipped', 'message': skip_msg},
                'document_processing': {'status': 'skipped', 'reason': skip_msg},
                'companies_house_matching': {'status': 'not_applicable', 'message': skip_msg},
            })
            continue
        if (source_mode or '').upper() == 'ID_ONLY_DISCOVERY' and final_kind not in INTAKE_ID_KINDS:
            skip_msg = 'ID-Only mode: passport / ID card / driving licence only'
            skipped_irrelevant.append({'file_name': fname, 'reason': skip_msg})
            filing_errors.append(f"{fname}: skipped — {skip_msg}")
            file_results.append({
                'file_name': fname,
                'filing_status': {'status': 'skipped', 'message': skip_msg},
                'document_processing': {'status': 'skipped', 'reason': skip_msg},
                'companies_house_matching': {'status': 'not_applicable', 'message': skip_msg},
            })
            continue

        extracted_client_name = res.get('extracted_name')
        extracted_dob = res.get('extracted_dob')
        extracted_company_name = res.get('extracted_company_name')
        extracted_company_num = res.get('extracted_company_number')
        extracted_email = res.get('extracted_email')
        extracted_phone = res.get('extracted_phone')
        extracted_nat = res.get('extracted_nationality')
        cat = intake_kind_to_category(final_kind) if final_kind else (res.get('category') or 'Company Documents')
        is_bank_statement = cat == 'Bank statement' or final_kind == 'Bank Statement'
        is_passport_doc = (
            (res.get('doc_type') in ('Passport', 'ID Document'))
            or (cat in ('ID Document', 'Passport'))
            or final_kind in INTAKE_ID_KINDS
            or bool(res.get('identity_primary'))
        )
        # ID / passport / licence: match only from name + DOB + nationality.
        if is_passport_doc:
            extracted_company_name = None
            extracted_company_num = None
        any_ocr = any_ocr or (res.get('scan_method') == 'ocr')
        methods.append(res.get('scan_method'))

        linked_identity = None
        linked_pts = 0
        linked_reason = None
        if extracted_client_name:
            linked_identity, linked_pts, linked_reason = find_batch_identity_for_name(
                extracted_client_name, identities
            )
            # Any non-ID supporting doc can also match a recent ID/passport in CRM
            if not linked_identity and not is_passport_doc:
                linked_identity, linked_pts, linked_reason = find_recent_passport_identity_for_name(
                    extracted_client_name
                )
        if not linked_identity and identities:
            text_blob = ' '.join([
                fname,
                res.get('text_preview') or '',
                extracted_company_name or '',
            ])
            linked_identity, linked_pts, linked_reason = find_identity_mentioned_in_text(
                text_blob, identities
            )
            if linked_identity and not extracted_client_name:
                extracted_client_name = linked_identity.get('full_name')

        # Prefer the ID/passport identity's stronger DOB/nationality when names match.
        if linked_identity:
            if not extracted_dob and linked_identity.get('dob'):
                extracted_dob = linked_identity.get('dob')
            if not extracted_nat and linked_identity.get('nationality'):
                extracted_nat = linked_identity.get('nationality')

        preferred_client_id = None
        if linked_identity and linked_identity.get('client_id'):
            preferred_client_id = linked_identity.get('client_id')

        if preferred_client_id:
            client_user = query_db("SELECT * FROM users WHERE id = ?;", (preferred_client_id,), one=True)
            if not client_user:
                client_user = ensure_smart_intake_client(
                    extracted_name=extracted_client_name or linked_identity.get('full_name'),
                    extracted_email=extracted_email,
                    extracted_phone=extracted_phone,
                )
        else:
            client_user = ensure_smart_intake_client(
                extracted_name=extracted_client_name,
                extracted_email=extracted_email,
                extracted_phone=extracted_phone,
            )
        if not client_user or not client_user.get('id'):
            filing_errors.append(f"{fname}: could not create client profile")
            file_results.append({
                'file_name': fname,
                'filing_status': {'status': 'failed', 'message': 'Could not create client profile'},
                'document_processing': {
                    'status': 'success',
                    'doc_detected': res.get('doc_type') or res.get('category'),
                    'extracted_name': extracted_client_name,
                    'extracted_dob': extracted_dob,
                    'passport_number': res.get('extracted_passport_num'),
                    'nationality': extracted_nat,
                    'extraction_quality': res.get('extraction_quality') or {},
                    'text_preview': res.get('text_preview') or '',
                },
                'companies_house_matching': {'status': 'failed', 'message': 'Client profile missing'},
            })
            continue

        client_id = client_user['id']
        name_detected = bool(extracted_client_name)
        last_client = client_user
        last_dob = extracted_dob or last_dob
        last_nationality = extracted_nat or last_nationality

        company_obj = None
        ch_status = 'no_ch_match'
        ch_msg = 'No Companies House match from name / date of birth / nationality yet.'
        ch_candidates = []
        match_meta = {
            'confidence': 0,
            'confidence_label': 'LOW',
            'score': 0,
            'reasons': [],
            'score_gap': 0,
            'top_candidates': [],
            'extracted_name': extracted_client_name,
            'extracted_dob': extracted_dob,
            'extracted_nationality': extracted_nat,
            'match_basis': 'name_dob_nationality',
        }

        id_only_intake = is_passport_doc and not (extracted_company_num or extracted_company_name)

        # Same person / same form: reuse company already matched to the ID/passport name.
        if linked_identity and linked_identity.get('company_id') and not (
            extracted_company_num or extracted_company_name
        ):
            company_obj = query_db(
                "SELECT * FROM companies WHERE id = ?;",
                (linked_identity['company_id'],),
                one=True,
            )
            if company_obj:
                ch_status = 'matched'
                ch_msg = (
                    f"Matched from identity (name"
                    f"{' + DOB' if extracted_dob or linked_identity.get('dob') else ''}"
                    f"{' + nationality' if extracted_nat or linked_identity.get('nationality') else ''}"
                    f") → {company_obj.get('name')}"
                )
                match_meta = {
                    'confidence': max(80, int(linked_identity.get('confidence') or linked_pts or 80)),
                    'confidence_label': 'HIGH',
                    'score': max(80, int(linked_identity.get('confidence') or linked_pts or 80)),
                    'reasons': [
                        f"Name matched prior ID/passport ({linked_reason or 'name match'})",
                        'Company linked from name / DOB / nationality identity',
                    ],
                    'score_gap': 80,
                    'top_candidates': [{
                        'name': company_obj.get('name'),
                        'company_number': company_obj.get('company_number'),
                        'score': max(80, int(linked_identity.get('confidence') or 80)),
                        'confidence_label': 'HIGH',
                        'reasons': ['Matched via name + DOB + nationality'],
                    }],
                    'extracted_name': extracted_client_name,
                    'extracted_dob': extracted_dob,
                    'extracted_nationality': extracted_nat,
                    'match_basis': 'name_dob_nationality',
                    'linked_from': linked_identity.get('source') or 'batch_id',
                    'linked_document_id': linked_identity.get('source_document_id'),
                    'same_person_form': True,
                }

        if not company_obj and (extracted_company_num or extracted_company_name) and not is_passport_doc:
            company_obj = auto_import_companies_house_from_intake(
                extracted_company_name,
                extracted_company_num,
                client_user_id=client_id,
            )
            if company_obj:
                ch_status = 'matched'
                ch_msg = f"Automatically matched {company_obj['name']} (#{company_obj.get('company_number', '')})"
                reasons = ['Company name/number present on document']
                if linked_identity:
                    reasons.insert(0, f"Same person as ID/passport ({linked_identity.get('full_name')})")
                match_meta = {
                    'confidence': 100,
                    'confidence_label': 'HIGH',
                    'score': 100,
                    'reasons': reasons,
                    'score_gap': 100,
                    'top_candidates': [{
                        'name': company_obj.get('name'),
                        'company_number': company_obj.get('company_number'),
                        'score': 100,
                        'confidence_label': 'HIGH',
                        'reasons': reasons,
                    }],
                    'extracted_name': extracted_client_name,
                    'extracted_dob': extracted_dob,
                    'extracted_nationality': extracted_nat,
                    'match_basis': 'company_number_or_name',
                    'same_person_form': bool(linked_identity),
                }
            else:
                ch_status = 'no_ch_match'
                ch_msg = 'No Companies House match found for company name/number.'
        elif not company_obj and extracted_client_name:
            resolve_dob = extracted_dob or (linked_identity or {}).get('dob')
            resolve_nat = extracted_nat or (linked_identity or {}).get('nationality')
            company_obj, ch_status, ch_msg, ch_candidates, match_meta = resolve_intake_company_match(
                client_id,
                extracted_client_name,
                extracted_dob=resolve_dob,
                extracted_nationality=resolve_nat,
                actor=user,
            )
            match_meta = match_meta or {}
            match_meta['extracted_name'] = extracted_client_name
            match_meta['extracted_dob'] = resolve_dob
            match_meta['extracted_nationality'] = resolve_nat
            match_meta['match_basis'] = 'name_dob_nationality'
            if company_obj and ch_status == 'matched':
                ch_msg = (
                    f"Matched from name"
                    f"{' + DOB' if resolve_dob else ''}"
                    f"{' + nationality' if resolve_nat else ''}"
                    f" → {company_obj.get('name')}"
                )
            if linked_identity:
                reasons = list(match_meta.get('reasons') or [])
                reasons.insert(0, f"Same person as ID/passport ({linked_reason or 'name match'})")
                match_meta['reasons'] = reasons
                match_meta['same_person_form'] = True
        elif not company_obj and id_only_intake:
            ch_status = 'not_applicable'
            ch_msg = 'Passport/ID processed. Need name (and ideally DOB / nationality) to find Companies House details.'

        company_id = company_obj['id'] if company_obj else None
        last_company = company_obj
        last_match_meta = match_meta or {}
        last_ch_status = ch_status
        last_ch_msg = ch_msg

        ext = res['ext']
        file_bytes = res['file_bytes']
        doc_type = final_kind or res.get('doc_type', cat)
        doc_id = None
        store_err = None

        target_path, store_err = store_client_document_file(client_id, None, ext, file_bytes)
        if store_err or not target_path:
            filing_errors.append(f"{fname}: {store_err or 'could not save file'}")
        else:
            file_size_str = format_document_size(len(file_bytes))
            f_hash = res.get('file_hash') or hashlib.sha256(file_bytes).hexdigest()
            stage_ts = json.dumps({'upload': time.time(), 'ocr': time.time(), 'ch': time.time()})
            meta_json = json.dumps(match_meta)
            # Successful intake always lands in Customer Uploads for staff triage.
            # OCR / CH match evidence is kept on the row; only duplicates go to Quarantine.
            overall_st = 'COMPLETED' if ch_status == 'matched' else (
                'REVIEW_REQUIRED' if ch_status == 'review_required' else 'COMPLETED'
            )
            lifecycle = 'CUSTOMER_UPLOADS'
            company_conf = float((match_meta or {}).get('confidence') or 0)
            identity_conf = 40.0
            if extracted_client_name:
                identity_conf += 30.0
            if extracted_dob:
                identity_conf += 15.0
            if extracted_nat:
                identity_conf += 15.0
            if linked_identity:
                identity_conf = max(identity_conf, 85.0)
            identity_conf = min(100.0, identity_conf)
            ocr_conf = float(((res.get('extraction_quality') or {}).get('score') or 70))
            prior = find_document_by_file_hash(f_hash)
            dup_conf = 0.0
            if prior:
                lifecycle = 'QUARANTINE'
                dup_conf = 100.0
                overall_st = 'QUARANTINE'
            customer_conf = 95.0 if linked_identity else (80.0 if name_detected else 40.0)
            doc_id = execute_db("""
                INSERT INTO documents (
                    user_id, company_id, name, category, file_path, file_type, file_size, status, uploaded_by, review_notes, client_visible, shared_at,
                    file_hash, ocr_status, identity_status, crm_status, ch_status, overall_status, stage_timestamps_json, match_meta_json,
                    lifecycle_status, is_posted, ocr_confidence, classification_confidence, identity_confidence,
                    customer_match_confidence, company_match_confidence, duplicate_confidence,
                    uploaded_at, processed_at, matched_at, uploaded_by_id, processed_by_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending Review', ?, 'Smart Document Intake', 0, NULL,
                        ?, 'COMPLETED', 'COMPLETED', ?, ?, ?, ?, ?,
                        ?, 0, ?, ?, ?, ?, ?, ?,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?);
            """, (
                client_id, company_id, fname, cat, target_path,
                DOCUMENT_TYPE_LABELS.get(ext, 'Document'), file_size_str, user['full_name'],
                f_hash, 'MATCHED' if name_detected or linked_identity else 'UNASSIGNED', ch_status, overall_st, stage_ts, meta_json,
                lifecycle,
                ocr_conf, 85.0, identity_conf,
                customer_conf, company_conf, dup_conf,
                user['id'], user['id'],
            ))
            log_document_audit(
                doc_id, 'AI', user['id'], user.get('full_name'), 'INTAKE_STORED',
                None, lifecycle, {
                    'ch_status': ch_status,
                    'company_match_confidence': company_conf,
                    'duplicate_confidence': dup_conf,
                    'linked_passport': bool(linked_identity),
                    'linked_reason': linked_reason,
                    'stored_in': 'CUSTOMER_UPLOADS' if lifecycle == 'CUSTOMER_UPLOADS' else lifecycle,
                },
            )
            filed_documents.append({
                'id': doc_id,
                'name': fname,
                'category': cat,
                'file_size': file_size_str,
                'scan_method': res.get('scan_method') or 'none',
                'lifecycle_status': lifecycle,
                'client_id': client_id,
                'company_id': company_id,
                'linked_passport': bool(linked_identity),
            })

            # Grow batch identity registry (passports first, also statements that resolve a company)
            if extracted_client_name or (linked_identity and linked_identity.get('full_name')):
                identity_record = {
                    'full_name': extracted_client_name or linked_identity.get('full_name'),
                    'dob': extracted_dob or (linked_identity or {}).get('dob'),
                    'nationality': extracted_nat or (linked_identity or {}).get('nationality'),
                    'client_id': client_id,
                    'company_id': company_id,
                    'company_name': company_obj.get('name') if company_obj else (linked_identity or {}).get('company_name'),
                    'company_number': (
                        (company_obj.get('company_number') or company_obj.get('companies_house_number'))
                        if company_obj else (linked_identity or {}).get('company_number')
                    ),
                    'source_document_id': doc_id,
                    'source': 'passport' if is_passport_doc else ('bank_statement' if is_bank_statement else 'document'),
                    'confidence': match_meta.get('confidence') or linked_pts or 0,
                }
                # Replace weaker same-name identity; keep passport entries preferred.
                replaced = False
                for idx, existing in enumerate(identities):
                    ok, pts, _ = intake_person_names_match(existing.get('full_name'), identity_record['full_name'])
                    if ok:
                        if is_passport_doc or not existing.get('company_id'):
                            identities[idx] = {**existing, **{k: v for k, v in identity_record.items() if v}}
                        elif identity_record.get('company_id') and not existing.get('company_id'):
                            identities[idx] = {**existing, **{k: v for k, v in identity_record.items() if v}}
                        replaced = True
                        break
                if not replaced:
                    identities.append(identity_record)

        file_results.append({
            'file_name': fname,
            'file_size': format_document_size(len(file_bytes)),
            'doc_type': doc_type,
            'scan_method': res.get('scan_method') or 'none',
            'extraction_quality': res.get('extraction_quality') or {},
            'text_preview': res.get('text_preview') or '',
            'document_processing': {
                'status': 'success',
                'doc_detected': doc_type,
                'extracted_name': extracted_client_name,
                'extracted_dob': extracted_dob,
                'passport_number': res.get('extracted_passport_num'),
                'nationality': extracted_nat,
                'extraction_quality': res.get('extraction_quality') or {},
                'text_preview': res.get('text_preview') or '',
                'linked_passport_name': (linked_identity or {}).get('full_name') if linked_identity else None,
            },
            'client_matching': {
                'status': 'matched' if name_detected or linked_identity else 'unassigned',
                'client_id': client_id,
                'client_name': client_user.get('full_name') or 'Unassigned Document Intake',
                'email': client_user.get('email'),
                'linked_from_passport': bool(linked_identity),
            },
            'companies_house_matching': {
                'status': ch_status,
                'company_name': company_obj['name'] if company_obj else None,
                'company_number': (
                    company_obj.get('company_number') or company_obj.get('companies_house_number')
                    if company_obj else None
                ),
                'message': ch_msg,
                'candidates': ch_candidates,
                'confidence': match_meta.get('confidence'),
                'confidence_label': match_meta.get('confidence_label'),
                'score': match_meta.get('score'),
                'score_gap': match_meta.get('score_gap'),
                'reasons': match_meta.get('reasons') or [],
                'top_candidates': match_meta.get('top_candidates') or ch_candidates or [],
                'linked_from_passport': bool(linked_identity),
            },
            'filing_status': {
                'status': 'auto_filed' if doc_id else 'failed',
                'doc_id': doc_id,
                'message': (
                    f"Auto-filed to Client #{client_id}" if doc_id
                    else (store_err or 'Filing failed')
                ),
            },
        })

    if not filed_documents and not file_results:
        return {'status': 'error', 'message': 'Could not parse uploaded files'}, "400 Bad Request"

    if not filed_documents:
        if skipped_irrelevant and len(skipped_irrelevant) == len(file_results):
            return {
                'status': 'error',
                'message': 'No relevant documents to process. Only bank statements, driving licences, company docs, passports, or ID cards (PDF/image) are accepted.',
                'skipped_irrelevant': skipped_irrelevant,
                'file_results': file_results,
                'filed_documents': [],
                'batch_identities': identities,
            }, "400 Bad Request"
        detail = '; '.join(filing_errors[:3]) if filing_errors else 'Unknown storage error'
        return {
            'status': 'error',
            'message': f'Scanned the file(s) but could not save them to storage: {detail}',
            'file_results': file_results,
            'filed_documents': [],
            'batch_identities': identities,
            'skipped_irrelevant': skipped_irrelevant,
        }, "500 Internal Server Error"

    if filed_documents:
        log_activity(
            user,
            'SMART_INTAKE',
            'documents',
            str(filed_documents[0]['id']),
            f"Processed Smart Intake ({len(filed_documents)} files)",
        )

    client_user = last_client or {}
    company_obj = last_company
    match_meta = last_match_meta or {}
    missing_fields = []
    if 'brixen-pending.local' in (client_user.get('email') or ''):
        missing_fields.append('Email Address')
    if client_user and not client_user.get('phone'):
        missing_fields.append('Phone Number')

    msg = f"Smart Intake stored {len(filed_documents)} document(s) in Customer Uploads."
    if filing_errors:
        msg += f" {len(filing_errors)} file(s) failed."
    if skipped_irrelevant:
        msg += f" {len(skipped_irrelevant)} irrelevant file(s) skipped (not stored)."
    linked_count = sum(1 for fr in file_results if (fr.get('companies_house_matching') or {}).get('linked_from_passport'))
    if linked_count:
        msg += f" {linked_count} filed on the same person form via ID/passport name match."

    return {
        'status': 'success',
        'message': msg,
        'scan': {'ocr_used': any_ocr, 'methods': methods},
        'client': {
            'id': client_user.get('id'),
            'full_name': client_user.get('full_name') or 'Unassigned Document Intake',
            'dob': last_dob or 'Not detected',
            'nationality': last_nationality or 'Not detected',
            'email': client_user.get('email'),
        },
        'company': {
            'id': company_obj['id'] if company_obj else None,
            'name': company_obj['name'] if company_obj else None,
            'company_number': (
                company_obj.get('companies_house_number') or company_obj.get('company_number')
                if company_obj else None
            ),
            'ch_status': last_ch_status,
            'ch_message': last_ch_msg,
            'confidence': match_meta.get('confidence'),
            'confidence_label': match_meta.get('confidence_label'),
            'match_score': match_meta.get('score'),
            'match_reasons': match_meta.get('reasons') or [],
            'top_candidates': match_meta.get('top_candidates') or [],
        },
        'missing_fields': missing_fields,
        'file_results': file_results,
        'filed_documents': filed_documents,
        'batch_identities': identities,
        'skipped_irrelevant': skipped_irrelevant,
    }, None


def auto_import_companies_house_from_intake(company_name, company_number, client_user_id=None):
    ch_num = normalize_company_number(company_number) if company_number else None
    if not ch_num and company_name:
        matches, _err = search_companies_house(company_name)
        if matches and len(matches) > 0:
            ch_num = matches[0].get('company_number')
            
    uid = client_user_id or 1
    if not ch_num:
        if company_name:
            existing_by_name = query_db("SELECT * FROM companies WHERE lower(name) = lower(?);", (company_name.strip(),), one=True)
            if existing_by_name:
                if client_user_id and not existing_by_name.get('user_id'):
                    execute_db("UPDATE companies SET user_id = ? WHERE id = ?;", (client_user_id, existing_by_name['id']))
                return query_db("SELECT * FROM companies WHERE id = ?;", (existing_by_name['id'],), one=True)
            comp_id = execute_db("""
                INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, created_at)
                VALUES (?, ?, ?, 'Active', DATE('now'), 'Director', 'United Kingdom', CURRENT_TIMESTAMP);
            """, (uid, company_name.strip(), f"REG-{secrets.token_hex(4).upper()}"))
            return query_db("SELECT * FROM companies WHERE id = ?;", (comp_id,), one=True)
        return None
        
    existing = query_db("SELECT * FROM companies WHERE company_number = ?;", (ch_num,), one=True)
    if existing:
        if client_user_id and not existing.get('user_id'):
            execute_db("UPDATE companies SET user_id = ? WHERE id = ?;", (client_user_id, existing['id']))
        return query_db("SELECT * FROM companies WHERE id = ?;", (existing['id'],), one=True)
        
    profile, err = fetch_companies_house_profile(ch_num)
    if profile:
        comp_id = execute_db("""
            INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, created_at)
            VALUES (?, ?, ?, ?, ?, 'Director', ?, CURRENT_TIMESTAMP);
        """, (
            uid,
            profile.get('name') or company_name or 'Companies House Entity',
            ch_num,
            profile.get('status') or 'Active',
            profile.get('incorporation_date') or '2026-01-01',
            profile.get('registered_address') or 'United Kingdom'
        ))
        apply_companies_house_profile_to_company(comp_id, profile)
        return query_db("SELECT * FROM companies WHERE id = ?;", (comp_id,), one=True)
        
    comp_id = execute_db("""
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, created_at)
        VALUES (?, ?, ?, 'Active', DATE('now'), 'Director', 'United Kingdom', CURRENT_TIMESTAMP);
    """, (uid, company_name or f"Company #{ch_num}", ch_num))
    return query_db("SELECT * FROM companies WHERE id = ?;", (comp_id,), one=True)


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


def companies_house_iso_date(value):
    text = str(value or '').strip()[:10]
    if len(text) == 10 and text[4] == '-' and text[7] == '-':
        try:
            datetime.date.fromisoformat(text)
            return text
        except ValueError:
            return ''
    return ''


def format_uk_long_date(value):
    text = companies_house_iso_date(value)
    if not text:
        return ''
    day = datetime.date.fromisoformat(text)
    return f"{day.day} {day.strftime('%B %Y')}"


def is_companies_house_default_address(office, postcode=None):
    blob = ' '.join(str(part or '') for part in (office, postcode)).lower()
    compact = re.sub(r'[^a-z0-9]', '', blob)
    if 'companieshousedefaultaddress' in compact:
        return True
    if 'pobox4385' in compact and 'cf148lh' in compact:
        return True
    return False


def companies_house_date_is_due(due_date, today=None, *, overdue_only=False):
    due = companies_house_iso_date(due_date)
    if not due:
        return False
    day = (today or datetime.date.today()).isoformat()
    return due < day if overdue_only else due <= day


def companies_house_flag_due(flag, due_date, today=None, *, overdue_only=False):
    if flag:
        return True
    return companies_house_date_is_due(due_date, today=today, overdue_only=overdue_only)


def companies_house_strike_off_proposed(status=None, status_detail=None):
    blob = f"{status or ''} {status_detail or ''}".lower().replace('_', '-')
    return 'strike-off' in blob or 'strike off' in blob


def companies_house_attention_issues(source, today=None):
    src = source if isinstance(source, dict) else {}
    today = today or datetime.date.today()
    office = str(src.get('reg_office') or src.get('registered_office') or '').strip()
    postcode = str(src.get('postcode') or '').strip()
    accounts_due = companies_house_iso_date(src.get('accounts_next_due'))
    confirmation_due = companies_house_iso_date(src.get('confirmation_next_due'))
    accounts_overdue = companies_house_flag_due(
        src.get('accounts_overdue'), accounts_due, today=today, overdue_only=True
    )
    confirmation_pending = companies_house_flag_due(
        src.get('confirmation_overdue') or src.get('confirmation_pending'),
        confirmation_due,
        today=today,
        overdue_only=False,
    )
    issues = []
    if accounts_overdue:
        issues.append({
            'code': 'accounts_overdue',
            'title': 'Accounts overdue',
            'detail': f"Due {format_uk_long_date(accounts_due)}" if accounts_due else 'File the overdue accounts at Companies House.',
            'due': accounts_due,
        })
    if confirmation_pending:
        overdue = companies_house_flag_due(
            src.get('confirmation_overdue'), confirmation_due, today=today, overdue_only=True
        )
        issues.append({
            'code': 'confirmation_pending',
            'title': 'Confirmation statement overdue' if overdue else 'Confirmation statement pending',
            'detail': f"Due {format_uk_long_date(confirmation_due)}" if confirmation_due else 'File the confirmation statement at Companies House.',
            'due': confirmation_due,
        })
    if is_companies_house_default_address(office, postcode):
        issues.append({
            'code': 'default_address',
            'title': 'Default Companies House address',
            'detail': 'Change the registered office away from the Companies House default address in Cardiff.',
        })
    if companies_house_strike_off_proposed(src.get('status'), src.get('status_detail')):
        issues.append({
            'code': 'strike_off',
            'title': 'Strike-off proposed',
            'detail': 'Companies House has proposed to strike this company off the register.',
        })
    return issues


def companies_house_attention_fingerprint(issues):
    codes = sorted({str((item or {}).get('code') or '').strip() for item in (issues or []) if (item or {}).get('code')})
    return '|'.join(codes)


def parse_company_attention_json(raw):
    text = str(raw or '').strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    issues = []
    for item in data:
        if not isinstance(item, dict) or not item.get('code'):
            continue
        issues.append({
            'code': str(item.get('code') or '').strip(),
            'title': str(item.get('title') or '').strip(),
            'detail': str(item.get('detail') or '').strip(),
            'due': companies_house_iso_date(item.get('due')),
        })
    return issues


def company_attention_issues(company):
    src = company if isinstance(company, dict) else {}
    stored = parse_company_attention_json(src.get('ch_attention_json'))
    issues = list(stored or [])
    seen = {item.get('code') for item in issues}
    for item in companies_house_attention_issues(src):
        if item.get('code') in seen:
            continue
        issues.append(item)
        seen.add(item.get('code'))
    return issues


def companies_house_attention_points(issues):
    points = []
    for item in issues or []:
        code = str((item or {}).get('code') or '')
        title = str((item or {}).get('title') or '').strip()
        due = format_uk_long_date((item or {}).get('due'))
        if code == 'accounts_overdue':
            points.append(f"⚠️ Accounts overdue{f' — due {due}' if due else ''}")
        elif code == 'confirmation_pending':
            label = title or 'Confirmation statement pending'
            points.append(f"📄 {label}{f' — due {due}' if due else ''}")
        elif code == 'default_address':
            points.append('🏠 Registered office is the Companies House default address in Cardiff')
        elif code == 'strike_off':
            points.append('⚠️ Companies House has proposed to strike this company off')
        elif title:
            points.append(f"⚠️ {title}")
    return points


def public_companies_house_profile(data):
    if not isinstance(data, dict):
        return None
    name = (data.get('company_name') or '').strip()
    number = str(data.get('company_number') or '').strip()
    if not name or not number:
        return None
    accounts = data.get('accounts') if isinstance(data.get('accounts'), dict) else {}
    confirmation = data.get('confirmation_statement') if isinstance(data.get('confirmation_statement'), dict) else {}
    office = companies_house_registered_office(data.get('registered_office_address'))
    postcode = str((data.get('registered_office_address') or {}).get('postal_code') or '').strip()
    accounts_due = companies_house_iso_date(accounts.get('next_due'))
    confirmation_due = companies_house_iso_date(confirmation.get('next_due'))
    return {
        'name': name,
        'company_number': number,
        'status': str(data.get('company_status') or '').strip(),
        'status_detail': str(data.get('company_status_detail') or '').strip(),
        'inc_date': str(data.get('date_of_creation') or '')[:10],
        'reg_office': office,
        'sic_codes': ', '.join(
            str(code).strip() for code in (data.get('sic_codes') or []) if str(code).strip()
        ),
        'postcode': postcode,
        'company_type': str(data.get('type') or '').strip(),
        'registered_email': companies_house_email_from_data(data),
        'accounts_next_due': accounts_due,
        'accounts_overdue': companies_house_flag_due(accounts.get('overdue'), accounts_due, overdue_only=True),
        'confirmation_next_due': confirmation_due,
        'confirmation_overdue': companies_house_flag_due(confirmation.get('overdue'), confirmation_due, overdue_only=True),
        'confirmation_pending': companies_house_flag_due(confirmation.get('overdue'), confirmation_due, overdue_only=False),
        'default_address': is_companies_house_default_address(office, postcode),
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


def fetch_companies_house_active_directors(company_number):
    number = normalize_company_number(company_number)
    if not number:
        return []
    queries = (
        urllib.parse.urlencode({'items_per_page': 100, 'register_type': 'directors'}),
        urllib.parse.urlencode({'items_per_page': 100}),
    )
    for qs in queries:
        payload, error = companies_house_request(
            '/company/' + urllib.parse.quote(number) + '/officers?' + qs
        )
        if error or not isinstance(payload, dict):
            err = str(error or '')
            if 'rejected' in err or 'rate limit' in err:
                global _CH_REGISTERED_SYNC_BLOCKED, _CH_PENDING_SYNC_BLOCKED
                _CH_REGISTERED_SYNC_BLOCKED = True
                _CH_PENDING_SYNC_BLOCKED = True
            continue
        names = []
        seen = set()
        for item in payload.get('items') or []:
            if not isinstance(item, dict) or item.get('resigned_on'):
                continue
            role = str(item.get('officer_role') or '').lower()
            if role and 'director' not in role:
                continue
            pretty = pretty_director_name(format_companies_house_officer_name(item.get('name')))
            if not pretty or pretty.lower() == 'director':
                continue
            key = pretty.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(pretty)
        if names:
            return names
    return []


def fetch_companies_house_primary_director(company_number):
    names = fetch_companies_house_active_directors(company_number)
    return names[0] if names else None


def company_directors_list(company):
    company_id = (company or {}).get('id')
    names = []
    seen = set()

    def _add(raw):
        pretty = pretty_director_name(raw)
        if not pretty or pretty.lower() == 'director':
            return
        key = pretty.lower()
        if key in seen:
            return
        seen.add(key)
        names.append(pretty)

    if company_id:
        rows = query_db(
            "SELECT name FROM company_directors WHERE company_id = ? ORDER BY id ASC;",
            (company_id,),
        ) or []
        for row in rows:
            _add((row or {}).get('name'))
    if names:
        return names
    raw = str((company or {}).get('director') or '').strip()
    if raw:
        for part in re.split(r'[,;&|·]|\band\b|\n+', raw, flags=re.IGNORECASE):
            _add(part)
    return names


def sync_company_directors_from_list(company_id, names):
    cleaned = []
    seen = set()
    for raw in names or []:
        pretty = pretty_director_name(raw)
        if not pretty or pretty.lower() == 'director':
            continue
        key = pretty.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(pretty)
    if not company_id or not cleaned:
        return []
    execute_db("DELETE FROM company_directors WHERE company_id = ?;", (company_id,))
    today = datetime.date.today().isoformat()
    for name in cleaned:
        execute_db(
            """
            INSERT INTO company_directors (company_id, name, role, nationality, appointed_date)
            VALUES (?, ?, 'Director', 'British', ?);
            """,
            (company_id, name, today),
        )
    execute_db(
        "UPDATE companies SET director = ?, ch_checked_at = CURRENT_TIMESTAMP WHERE id = ?;",
        (cleaned[0], company_id),
    )
    return cleaned


def checkout_value_is_blank(value, *, address=False):
    text = str(value or '').strip()
    if not text or text in {'—', '-', '–'}:
        return True
    low = text.lower()
    if low in {'n/a', 'na', 'none', 'null', 'unknown', 'director'}:
        return True
    if address and low in {'united kingdom', 'uk', 'gb', 'great britain', 'england', 'london'}:
        return True
    return False


CHECKOUT_COMPANY_NUMBER_KEYS = frozenset({
    'company number',
    'companies house number',
    'company registration number',
    'registration number',
    'registered number',
    'ch number',
})
CHECKOUT_REGISTERED_ADDRESS_KEYS = frozenset({
    'registered address',
    'registered office',
    'registered office address',
    'registered company address',
})


def checkout_form_company_number(fields, company=None):
    number = normalize_company_number((company or {}).get('company_number'))
    if looks_like_uk_company_number(number) and not is_pending_company_number(number):
        return number
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        key = checkout_field_key(field.get('label'))
        if key in CHECKOUT_COMPANY_NUMBER_KEYS:
            found = normalize_company_number(field.get('value'))
            if looks_like_uk_company_number(found) and not is_pending_company_number(found):
                return found
    return ''


def companies_house_snapshot(company_number):
    number = normalize_company_number(company_number)
    if not looks_like_uk_company_number(number) or is_pending_company_number(number):
        return None
    if not companies_house_api_key():
        return None
    global _CH_REGISTERED_SYNC_BLOCKED
    if _CH_REGISTERED_SYNC_BLOCKED:
        return None
    profile, error = fetch_companies_house_profile(number)
    if error:
        err = str(error or '')
        if 'rejected' in err or 'rate limit' in err:
            _CH_REGISTERED_SYNC_BLOCKED = True
        return None
    directors = fetch_companies_house_active_directors(number)
    director = directors[0] if directors else ''
    status = companies_house_status_value((profile or {}).get('status'), (profile or {}).get('status_detail'))
    office = str((profile or {}).get('reg_office') or '').strip()
    if office.lower() in {'united kingdom', 'uk'}:
        office = ''
    return {
        'name': str((profile or {}).get('name') or '').strip(),
        'company_number': number,
        'status': status,
        'status_detail': str((profile or {}).get('status_detail') or '').strip(),
        'inc_date': str((profile or {}).get('inc_date') or '')[:10],
        'reg_office': office,
        'sic_codes': str((profile or {}).get('sic_codes') or '').strip(),
        'director': director,
        'directors': directors,
        'postcode': str((profile or {}).get('postcode') or '').strip(),
        'company_type': str((profile or {}).get('company_type') or '').strip(),
        'registered_email': str((profile or {}).get('registered_email') or '').strip(),
        'accounts_next_due': companies_house_iso_date((profile or {}).get('accounts_next_due')),
        'accounts_overdue': bool((profile or {}).get('accounts_overdue')),
        'confirmation_next_due': companies_house_iso_date((profile or {}).get('confirmation_next_due')),
        'confirmation_overdue': bool((profile or {}).get('confirmation_overdue')),
        'confirmation_pending': bool((profile or {}).get('confirmation_pending')),
        'default_address': bool((profile or {}).get('default_address')),
    }


def fill_company_blanks_from_snapshot(company, snapshot):
    if not company or not snapshot or not company.get('id'):
        return False
    updates = []
    params = []
    number = snapshot.get('company_number')
    if number and is_pending_company_number(company.get('company_number')):
        updates.append('company_number = ?')
        params.append(number)
    if snapshot.get('name') and checkout_value_is_blank(company.get('name')):
        updates.append('name = ?')
        params.append(snapshot['name'])
    if snapshot.get('status') and checkout_value_is_blank(company.get('status')):
        updates.append('status = ?')
        params.append(snapshot['status'])
    inc = str(snapshot.get('inc_date') or '')[:10]
    if len(inc) >= 10 and checkout_value_is_blank(company.get('inc_date')):
        updates.append('inc_date = ?')
        params.append(inc)
    if snapshot.get('reg_office') and checkout_value_is_blank(company.get('reg_office'), address=True):
        updates.append('reg_office = ?')
        params.append(snapshot['reg_office'])
    if snapshot.get('director') and looks_like_placeholder_director(company.get('director'), company.get('name')):
        updates.append('director = ?')
        params.append(snapshot['director'])
    if snapshot.get('sic_codes'):
        updates.append('sic_codes = ?')
        params.append(snapshot['sic_codes'])
    email = str(snapshot.get('registered_email') or '').strip()
    if email and checkout_value_is_blank(company.get('registered_email')):
        updates.append('registered_email = ?')
        params.append(email)
    if updates:
        updates.append('ch_checked_at = CURRENT_TIMESTAMP')
        params.append(company['id'])
        execute_db(f"UPDATE companies SET {', '.join(updates)} WHERE id = ?;", tuple(params))
        if snapshot.get('director') and looks_like_placeholder_director(company.get('director'), company.get('name')):
            persist_official_director(company['id'], snapshot['director'])
    persist_companies_house_filing_state(company, snapshot)
    return bool(updates)


def fill_checkout_blanks_from_companies_house(order_row, fields):
    fields = [dict(field) for field in (fields or []) if isinstance(field, dict)]
    company = None
    company_id = (order_row or {}).get('company_id')
    if not company_id:
        company_id = ensure_order_company_link(order_row, fields)
        if company_id and isinstance(order_row, dict):
            order_row['company_id'] = company_id
    if company_id:
        company = query_db(
            "SELECT id, name, company_number, status, inc_date, director, reg_office FROM companies WHERE id = ?;",
            (company_id,),
            one=True,
        )
    number = checkout_form_company_number(fields, company)
    if not number:
        return fields
    by_key = {}
    for field in fields:
        by_key[checkout_field_key(field.get('label'))] = field.get('value')
    needs_live = (
        checkout_value_is_blank(by_key.get('sic code'))
        or checkout_value_is_blank(
            by_key.get('registered address') or by_key.get('registered office') or by_key.get('registered office address'),
            address=True,
        )
        or checkout_value_is_blank(by_key.get('incorporation date'))
        or checkout_value_is_blank(by_key.get('company status'))
        or checkout_value_is_blank(by_key.get('company number'))
        or (
            checkout_value_is_blank(by_key.get('director name'))
            and looks_like_placeholder_director((company or {}).get('director'), (company or {}).get('name'))
        )
    )
    snapshot = companies_house_snapshot(number) if needs_live else None
    if not snapshot:
        if company:
            snapshot = {
                'name': str(company.get('name') or '').strip(),
                'company_number': number,
                'status': str(company.get('status') or '').strip(),
                'inc_date': str(company.get('inc_date') or '')[:10],
                'reg_office': str(company.get('reg_office') or '').strip(),
                'sic_codes': '',
                'director': official_company_director(company) or '',
            }
            if checkout_value_is_blank(snapshot.get('reg_office'), address=True):
                snapshot['reg_office'] = ''
        else:
            return fields
    values = {
        'company number': snapshot.get('company_number') or number,
        'desired company name': snapshot.get('name') or '',
        'company name': snapshot.get('name') or '',
        'registered address': snapshot.get('reg_office') or '',
        'registered office': snapshot.get('reg_office') or '',
        'registered office address': snapshot.get('reg_office') or '',
        'sic code': snapshot.get('sic_codes') or '',
        'incorporation date': snapshot.get('inc_date') or '',
        'company status': snapshot.get('status') or '',
        'director name': snapshot.get('director') or '',
    }
    owner = get_company_owner_for_order(order_row, fields) if order_row else {}
    owner_name = str((owner or {}).get('full_name') or '').strip()
    if owner_name and not looks_like_placeholder_director(owner_name, snapshot.get('name')):
        values['director name'] = ''
    changed = False
    seen = set()
    for field in fields:
        key = checkout_field_key(field.get('label'))
        seen.add(key)
        fill_value = values.get(key) or ''
        if not fill_value:
            continue
        address_field = key in CHECKOUT_REGISTERED_ADDRESS_KEYS
        if checkout_value_is_blank(field.get('value'), address=address_field):
            field['value'] = fill_value
            changed = True
    official_fields = [
        ('company number', 'Company number'),
        ('registered address', 'Registered address'),
        ('sic code', 'SIC code'),
        ('incorporation date', 'Incorporation date'),
        ('company status', 'Company status'),
        ('director name', 'Director name'),
    ]
    for key, label in official_fields:
        if key in seen:
            continue
        fill_value = values.get(key) or ''
        if not fill_value:
            continue
        fields.append({'label': label, 'value': fill_value})
        seen.add(key)
        changed = True
    if company:
        fill_company_blanks_from_snapshot(company, snapshot)
    if changed and (order_row or {}).get('id'):
        execute_db(
            "UPDATE orders SET checkout_form_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (json.dumps(fields), order_row['id']),
        )
        if snapshot.get('director') and looks_like_placeholder_director(owner_name, snapshot.get('name')):
            persist_official_director(company_id, snapshot['director'])
            upsert_company_owner_from_fields(order_row['id'], company_id, fields)
    return fields


def companies_house_company_url(company_number):
    number = normalize_company_number(company_number)
    if not number:
        return 'https://find-and-update.company-information.service.gov.uk'
    return f'https://find-and-update.company-information.service.gov.uk/company/{number}'


def companies_house_filing_history_url(company_number):
    number = normalize_company_number(company_number)
    if not number:
        return 'https://find-and-update.company-information.service.gov.uk'
    return f"{companies_house_company_url(number)}/filing-history"


def persist_companies_house_filing_state(company, snapshot, send_email=True):
    company_id = (company or {}).get('id')
    if not company_id or is_pending_company_number((snapshot or {}).get('company_number') or (company or {}).get('company_number')):
        return []
    source = dict(company or {})
    if isinstance(snapshot, dict):
        for key, value in snapshot.items():
            if key == 'directors':
                continue
            if value in (None, ''):
                continue
            source[key] = value
        for key in ('accounts_overdue', 'confirmation_overdue', 'confirmation_pending', 'default_address'):
            if key in snapshot:
                source[key] = snapshot.get(key)
        if snapshot.get('reg_office'):
            source['reg_office'] = snapshot['reg_office']
        if snapshot.get('status'):
            source['status'] = snapshot['status']
        if snapshot.get('status_detail') is not None:
            source['status_detail'] = snapshot.get('status_detail')
    issues = companies_house_attention_issues(source)
    accounts_due = companies_house_iso_date(source.get('accounts_next_due'))
    confirmation_due = companies_house_iso_date(source.get('confirmation_next_due'))
    accounts_overdue = 1 if any(item.get('code') == 'accounts_overdue' for item in issues) else 0
    confirmation_overdue = 1 if any(item.get('code') == 'confirmation_pending' for item in issues) else 0
    status = str(source.get('status') or (company or {}).get('status') or 'Active').strip() or 'Active'
    office = str(source.get('reg_office') or (company or {}).get('reg_office') or '').strip()
    execute_db(
        """
        UPDATE companies
        SET accounts_next_due = ?, accounts_overdue = ?, confirmation_next_due = ?,
            confirmation_overdue = ?, ch_attention_json = ?, status = ?,
            reg_office = CASE WHEN ? != '' THEN ? ELSE reg_office END,
            ch_checked_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """,
        (
            accounts_due or None,
            accounts_overdue,
            confirmation_due or None,
            confirmation_overdue,
            json.dumps(issues),
            status,
            office,
            office,
            company_id,
        ),
    )
    company['ch_attention_json'] = json.dumps(issues)
    company['accounts_next_due'] = accounts_due
    company['accounts_overdue'] = accounts_overdue
    company['confirmation_next_due'] = confirmation_due
    company['confirmation_overdue'] = confirmation_overdue
    if office:
        company['reg_office'] = office
    company['status'] = status
    if send_email:
        # When automated compliance alerts are enabled, the Asia/Karachi daily
        # worker owns outbound mail. Sync only refreshes stored attention state.
        try:
            import compliance_alerts as _ca
            alerts_on = _ca.compliance_alert_settings().get('enabled')
        except Exception:
            alerts_on = False
        if alerts_on:
            pass
        else:
            notify_companies_house_attention(company_id, issues)
    return issues


def build_companies_house_attention_email(client, company, issues, greeting_name=None):
    name = str((company or {}).get('name') or 'Your company').strip() or 'Your company'
    number = str((company or {}).get('company_number') or '').strip()
    extra = '\n'.join(companies_house_attention_points(issues))
    return build_client_notification_email(
        client,
        f'Action needed for {name} at Companies House',
        f'We are contacting you regarding {name}.',
        subject=f'Action Required — {name}',
        badge='Attention required',
        greeting_name=greeting_name,
        extra_message=extra,
        company_name=name,
        company_number=number or None,
        cta_label='View on Companies House' if number else None,
        cta_url=companies_house_company_url(number) if number else None,
        suppress_default_cta=not bool(number),
        extra_cta_label='Open Client Panel',
        extra_cta_url=client_website_url(),
        layout='activity',
        email_category='COMPANIES_HOUSE',
    )


def notify_companies_house_attention(company_id, issues=None, email_to=None, force=False):
    company = query_db(f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;", (company_id,), one=True)
    if not company or is_pending_company_number(company.get('company_number')):
        return {'notification_created': False, 'email_sent': False, 'email_status': 'Not registered'}
    issues = list(issues if issues is not None else company_attention_issues(company))
    fingerprint = companies_house_attention_fingerprint(issues)
    if not fingerprint:
        execute_db(
            "UPDATE companies SET ch_alert_fingerprint = '', ch_attention_json = ? WHERE id = ?;",
            (json.dumps([]), company_id),
        )
        return {'notification_created': False, 'email_sent': False, 'email_status': 'No issues'}
    current_fp = str(company.get('ch_alert_fingerprint') or '').strip()
    if not force and current_fp == fingerprint:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'Already sent'}
    client = resolve_client_user(company.get('user_id'))
    if not client:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'Client not found'}
    form_email = email_to or company_form_email(company['id'], fallback=client.get('email'))
    if not form_email:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'No form email'}
    director_name = official_company_director(company) or client.get('full_name') or 'there'
    email_subject, email_text, email_html = build_companies_house_attention_email(
        client,
        company,
        issues,
        greeting_name=director_name,
    )
    result = notify_client(
        client,
        f"Action needed for {company.get('name')} at Companies House",
        f"Companies House currently shows an issue that needs fixing for {company.get('name')}. Please action this now so the company stays in good standing.",
        'warning',
        '/companies',
        email_subject=email_subject,
        email_text=email_text,
        email_html=email_html,
        email_to=form_email,
    )
    if result.get('notification_created') or result.get('email_sent') or force:
        execute_db(
            "UPDATE companies SET ch_alert_fingerprint = ?, ch_alert_sent_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (fingerprint, company_id),
        )
    return {
        **result,
        'email_to': form_email,
        'company_number': company.get('company_number'),
        'issues': [item.get('code') for item in issues],
    }


def send_companies_house_attention_preview_email(recipient, company=None, issues=None):
    recipient = str(recipient or '').strip()
    if not recipient or '@' not in recipient:
        return False, 'Enter a valid email address'
    company = dict(company or {})
    name = str(company.get('name') or 'LUMINAVEST LTD').strip() or 'LUMINAVEST LTD'
    number = str(company.get('company_number') or '15203368').strip() or '15203368'
    company['name'] = name
    company['company_number'] = number
    issues = list(issues or company_attention_issues(company) or companies_house_attention_issues(company))
    if not issues:
        issues = [
            {'code': 'accounts_overdue', 'title': 'Accounts overdue', 'detail': 'Due 31 July 2026', 'due': '2026-07-31'},
            {
                'code': 'default_address',
                'title': 'Default Companies House address',
                'detail': 'Change the registered office away from the Companies House default address in Cardiff.',
            },
            {
                'code': 'strike_off',
                'title': 'Strike-off proposed',
                'detail': 'Companies House has proposed to strike this company off the register.',
            },
        ]
    director = str(company.get('director') or 'Director').strip() or 'Director'
    _, body_text, body_html = build_companies_house_attention_email(
        {'full_name': director, 'email': recipient},
        company,
        issues,
        greeting_name=director,
    )
    return EmailService.send_notification_email(
        recipient,
        f'Action needed for {name} at Companies House',
        body_text,
        body_html,
    )


def is_incorporation_certificate_filing(item):
    if not isinstance(item, dict):
        return False
    typ = str(item.get('type') or '').upper()
    desc = str(item.get('description') or '').lower().replace('_', '-')
    if typ in ('NEWINC', 'CERTINC'):
        return True
    return 'certificate-of-incorporation' in desc or 'certificate of incorporation' in desc


def companies_house_http(url, accept='application/json', timeout=20):
    key = companies_house_api_key()
    if not key or not str(url or '').startswith('https://'):
        return None, None, 'Companies House API key is not configured'
    token = base64.b64encode(f'{key}:'.encode('ascii')).decode('ascii')
    req = urllib.request.Request(url, headers={
        'Authorization': f'Basic {token}',
        'Accept': accept,
        'User-Agent': 'BrixenCRM/1.0',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), str(resp.headers.get('Content-Type') or ''), None
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return None, None, 'Companies House rejected the API key'
        if exc.code == 404:
            return None, None, 'Companies House document not found'
        return None, None, 'Companies House document lookup failed'
    except Exception:
        return None, None, 'Could not reach Companies House'


def filing_transaction_id(item):
    if not isinstance(item, dict):
        return None
    tx = str(item.get('transaction_id') or '').strip()
    if re.match(r'^[A-Za-z0-9]+$', tx):
        return tx
    links = item.get('links') if isinstance(item.get('links'), dict) else {}
    match = re.search(r'/filing-history/([A-Za-z0-9]+)', str(links.get('self') or ''))
    return match.group(1) if match else None


def companies_house_public_filing_pdf_url(company_number, transaction_id):
    number = normalize_company_number(company_number)
    tx = str(transaction_id or '').strip()
    if not number or not re.match(r'^[A-Za-z0-9]+$', tx):
        return None
    return (
        f'https://find-and-update.company-information.service.gov.uk/company/{number}'
        f'/filing-history/{tx}/document?format=pdf&download=1'
    )


def fetch_companies_house_public_pdf(url):
    if not str(url or '').startswith('https://find-and-update.company-information.service.gov.uk/'):
        return None, 'Invalid Companies House document link'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'BrixenCRM/1.0',
        'Accept': 'application/pdf',
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception:
        return None, 'Could not download the Companies House certificate'
    if not data or not data.startswith(b'%PDF'):
        return None, 'Companies House did not return a PDF certificate'
    return data, None


def fetch_companies_house_filing_history(company_number):
    number = normalize_company_number(company_number)
    if not number:
        return None, 'Missing company number'
    queries = (
        urllib.parse.urlencode({'items_per_page': 50, 'category': 'incorporation'}),
        urllib.parse.urlencode({'items_per_page': 50}),
    )
    last_error = None
    for qs in queries:
        payload, error = companies_house_request(
            '/company/' + urllib.parse.quote(number) + '/filing-history?' + qs
        )
        if error or not isinstance(payload, dict):
            last_error = error or 'Filing history unavailable'
            continue
        return payload, None
    return None, last_error or 'Filing history unavailable'


def fetch_companies_house_incorporation_certificate(company_number):
    number = normalize_company_number(company_number)
    if not number:
        return None, None, 'Missing company number'
    payload, error = fetch_companies_house_filing_history(number)
    if error or not isinstance(payload, dict):
        return None, None, error or 'Filing history unavailable'
    filing = None
    for item in payload.get('items') or []:
        if is_incorporation_certificate_filing(item):
            filing = item
            break
    if not filing:
        return None, None, 'Certificate of incorporation not found at Companies House'
    filename = f"{number} Certificate of Incorporation.pdf"
    links = filing.get('links') if isinstance(filing.get('links'), dict) else {}
    metadata_url = str(links.get('document_metadata') or '').strip()
    if metadata_url.startswith('https://document-api.company-information.service.gov.uk/'):
        raw, _ctype, meta_err = companies_house_http(metadata_url, accept='application/json')
        if not meta_err and raw:
            try:
                metadata = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                metadata = {}
            doc_links = metadata.get('links') if isinstance(metadata.get('links'), dict) else {}
            document_url = str(doc_links.get('document') or metadata_url.rstrip('/') + '/content').strip()
            if document_url.startswith('https://document-api.company-information.service.gov.uk/'):
                pdf, _content_type, doc_err = companies_house_http(document_url, accept='application/pdf', timeout=30)
                if not doc_err and pdf and pdf.startswith(b'%PDF'):
                    return pdf, filename, None
    public_url = companies_house_public_filing_pdf_url(number, filing_transaction_id(filing))
    if public_url:
        pdf, pub_err = fetch_companies_house_public_pdf(public_url)
        if pdf:
            return pdf, filename, None
        return None, None, pub_err
    return None, None, 'Companies House certificate link missing'


def existing_incorporation_certificate_document(company_id):
    if not company_id:
        return None
    return query_db(
        """
        SELECT * FROM documents
        WHERE company_id = ?
          AND LOWER(COALESCE(category, '')) = 'companies house certificate'
        ORDER BY id DESC;
        """,
        (company_id,),
        one=True,
    )


def store_incorporation_certificate_document(company, pdf_bytes, filename):
    if not company or not pdf_bytes:
        return None
    existing = existing_incorporation_certificate_document(company.get('id'))
    if existing and existing.get('file_path') and os.path.isfile(existing.get('file_path')):
        return existing
    path, err = store_client_document_file(company.get('user_id'), None, '.pdf', pdf_bytes)
    if err or not path:
        return None
    name = str(filename or '').strip() or f"{company.get('company_number')} Certificate of Incorporation.pdf"
    official_name = str(company.get('name') or '').strip()
    if official_name:
        name = f"{official_name} Certificate of Incorporation.pdf"
    doc_id = execute_db(
        """
        INSERT INTO documents (
            user_id, company_id, name, category, file_path, file_type, file_size,
            status, uploaded_by, review_notes, client_visible, shared_at
        ) VALUES (?, ?, ?, 'Companies House Certificate', ?, 'PDF', ?, 'Approved', 'Companies House', ?, 1, CURRENT_TIMESTAMP);
        """,
        (
            company.get('user_id'),
            company.get('id'),
            name,
            path,
            format_document_size(len(pdf_bytes)),
            'Official Companies House certificate of incorporation.',
        ),
    )
    return query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)


def incorporation_download_secret():
    return (
        wordpress_integration_secret()
        or os.environ.get('INCORPORATION_DOWNLOAD_SECRET')
        or companies_house_api_key()
        or None
    )


def sign_incorporation_download(document_id, company_id, expires):
    secret = incorporation_download_secret()
    if not secret:
        return None
    payload = f"{int(document_id)}|{int(company_id)}|{int(expires)}"
    return hmac.new(secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()


def incorporation_certificate_download_url(document, company_number=None):
    if document and document.get('id') and document.get('company_id'):
        expires = int(datetime.datetime.now().timestamp()) + (30 * 24 * 60 * 60)
        signature = sign_incorporation_download(document['id'], document['company_id'], expires)
        if signature:
            qs = urllib.parse.urlencode({
                'document_id': document['id'],
                'company_id': document['company_id'],
                'expires': expires,
                'signature': signature,
            })
            return f"{portal_base_url()}/api/public/incorporation-certificate?{qs}"
    return companies_house_filing_history_url(company_number)


def ensure_incorporation_certificate(company):
    existing = existing_incorporation_certificate_document((company or {}).get('id'))
    if existing and existing.get('file_path') and os.path.isfile(existing.get('file_path')):
        return existing
    pdf, filename, _error = fetch_companies_house_incorporation_certificate((company or {}).get('company_number'))
    if not pdf:
        return None
    return store_incorporation_certificate_document(company, pdf, filename)


def pretty_director_name(name):
    text = str(name or '').strip()
    if not text:
        return 'Director'
    parts = [part for part in re.split(r'\s+', text) if part]
    if any(part.isalpha() and part == part.upper() and len(part) > 1 for part in parts):
        return text.title()
    return text


def resolve_companies_house_director(company):
    live = fetch_companies_house_primary_director((company or {}).get('company_number'))
    stored = str((company or {}).get('director') or '').strip()
    name = pretty_director_name(live or stored)
    if name and company and company.get('id') and name != stored:
        execute_db("UPDATE companies SET director = ?, ch_checked_at = CURRENT_TIMESTAMP WHERE id = ?;", (name, company['id']))
    return name or 'Director'


CH_REGISTERED_RECHECK_SECONDS = 12 * 3600
CH_REGISTERED_SYNC_BATCH = 15
_CH_REGISTERED_SYNC_BLOCKED = False


def looks_like_placeholder_director(name, company_name=None):
    text = str(name or '').strip()
    if not text or text.lower() in {'director', 'unknown', 'n/a', 'na', 'none', '-', '—'}:
        return True
    if '@' in text:
        return True
    name_key = normalize_company_name_key(text)
    full_company = normalize_company_name_key(company_name)
    company_key = re.sub(r'\b(LTD|LIMITED|PLC|LLP|CIC)\b', '', full_company).strip()
    if company_key and name_key and (name_key == full_company or name_key == company_key):
        return True
    if re.search(r'\d', text) and ' ' not in text:
        return True
    if ' ' not in text and text == text.lower():
        return True
    return False


def persist_official_director(company_id, name):
    if not company_id or not name:
        return ''
    parts = [p.strip() for p in re.split(r'[,;&|]|\band\b', str(name), flags=re.IGNORECASE) if p.strip()]
    cleaned = []
    seen = set()
    for p in parts:
        pretty = pretty_director_name(p)
        if pretty and pretty.lower() != 'director' and pretty.lower() not in seen:
            seen.add(pretty.lower())
            cleaned.append(pretty)
    if not cleaned:
        return ''
    combined_name = ', '.join(cleaned)
    execute_db(
        "UPDATE companies SET director = ?, ch_checked_at = CURRENT_TIMESTAMP WHERE id = ?;",
        (combined_name, company_id),
    )
    sync_company_directors_from_list(company_id, cleaned)
    owners = query_db(
        "SELECT id, full_name, order_id FROM company_owners WHERE company_id = ?;",
        (company_id,),
    ) or []
    for owner in owners:
        if looks_like_placeholder_director(owner.get('full_name')):
            execute_db(
                "UPDATE company_owners SET full_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (pretty, owner['id']),
            )
        if owner.get('order_id') and looks_like_placeholder_director(
            (query_db("SELECT owner_name FROM orders WHERE id = ?;", (owner['order_id'],), one=True) or {}).get('owner_name')
        ):
            execute_db(
                "UPDATE orders SET owner_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (pretty, owner['order_id']),
            )
    linked_orders = query_db(
        "SELECT id, owner_name FROM orders WHERE company_id = ?;",
        (company_id,),
    ) or []
    for order in linked_orders:
        if looks_like_placeholder_director(order.get('owner_name')):
            execute_db(
                "UPDATE orders SET owner_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (pretty, order['id']),
            )
    return pretty


def sync_registered_company_from_companies_house(company):
    if not company or is_pending_company_number(company.get('company_number')):
        return False
    snapshot = companies_house_snapshot(company.get('company_number'))
    if snapshot:
        live_directors = snapshot.get('directors') or []
        if live_directors:
            sync_company_directors_from_list(company.get('id'), live_directors)
        elif snapshot.get('director'):
            persist_official_director(company.get('id'), snapshot.get('director'))
        fill_company_blanks_from_snapshot(company, snapshot)
        persist_company_registered_email(company, snapshot.get('registered_email'))
        persist_companies_house_filing_state(company, snapshot)
        return True
    mark_company_ch_checked(company.get('id'))
    return False


def hydrate_company_from_companies_house(company, extras=None):
    """Fill a standalone company card from Companies House. No order is required."""
    if not company or not company.get('id'):
        return False
    extras = extras if isinstance(extras, dict) else {}
    number = normalize_company_number(
        extras.get('company_number') or company.get('company_number')
    )
    if not looks_like_uk_company_number(number) or is_pending_company_number(number):
        number = ''
        search_name = str(extras.get('name') or company.get('name') or '').strip()
        if len(normalize_company_name_key(search_name)) >= 4:
            matches, error = search_companies_house(search_name)
            if not error:
                match = companies_house_match_for_name(search_name, matches)
                if match:
                    number = normalize_company_number(match.get('company_number'))
    if not looks_like_uk_company_number(number) or is_pending_company_number(number):
        return False
    snapshot = companies_house_snapshot(number)
    if not snapshot:
        return False
    profile = {
        'name': snapshot.get('name') or company.get('name'),
        'company_number': number,
        'status': snapshot.get('status') or company.get('status'),
        'inc_date': snapshot.get('inc_date'),
        'reg_office': snapshot.get('reg_office') or company.get('reg_office'),
        'sic_codes': snapshot.get('sic_codes') or company.get('sic_codes'),
        'registered_email': snapshot.get('registered_email') or company.get('registered_email'),
    }
    applied = apply_companies_house_profile_to_company(
        company.get('id'),
        profile,
        director=snapshot.get('director') or None,
    )
    if snapshot.get('directors'):
        sync_company_directors_from_list(company.get('id'), snapshot['directors'])
    elif snapshot.get('director'):
        persist_official_director(company.get('id'), snapshot['director'])
    persist_companies_house_filing_state(company, snapshot)
    return applied


def registered_companies_due_for_director_sync(limit=CH_REGISTERED_SYNC_BATCH, user_id=None):
    sql = """
        SELECT id, name, company_number, director, user_id, ch_checked_at, ch_attention_json, reg_office
        FROM companies
    """
    params = []
    if user_id:
        sql += " WHERE user_id = ?"
        params.append(user_id)
    sql += " ORDER BY name COLLATE NOCASE;"
    rows = query_db(sql, params) or []
    cutoff = datetime.datetime.now() - datetime.timedelta(seconds=CH_REGISTERED_RECHECK_SECONDS)
    due = []
    for row in rows:
        if is_pending_company_number(row.get('company_number')):
            continue
        placeholder = looks_like_placeholder_director(row.get('director'), row.get('name'))
        unchecked = row.get('ch_attention_json') is None
        checked = str(row.get('ch_checked_at') or '').strip()
        stale = True
        if checked:
            try:
                checked_at = datetime.datetime.fromisoformat(checked[:19])
                stale = checked_at < cutoff
            except ValueError:
                stale = True
        if placeholder or stale or unchecked:
            due.append(row)
        if len(due) >= int(limit or CH_REGISTERED_SYNC_BATCH):
            break
    return due


def sync_registered_companies_from_companies_house(limit=CH_REGISTERED_SYNC_BATCH, user_id=None):
    global _CH_REGISTERED_SYNC_BLOCKED
    updated = 0
    if _CH_REGISTERED_SYNC_BLOCKED or not companies_house_api_key():
        return 0
    for company in registered_companies_due_for_director_sync(limit=limit, user_id=user_id):
        if _CH_REGISTERED_SYNC_BLOCKED:
            break
        if sync_registered_company_from_companies_house(company):
            updated += 1
    return updated


_CH_LIST_SYNC_LAST = 0.0
_CH_LIST_SYNC_LOCK = threading.Lock()


def schedule_portfolio_ch_sync(user_id=None):
    """
    Companies House refresh must never block page-open APIs.
    Run a small batch in the background at most once every 45s.
    """
    global _CH_LIST_SYNC_LAST
    now = time.time()
    with _CH_LIST_SYNC_LOCK:
        if (now - _CH_LIST_SYNC_LAST) < 45:
            return False
        _CH_LIST_SYNC_LAST = now

    def _job():
        try:
            sync_pending_companies_from_companies_house(user_id=user_id, limit=2)
            sync_registered_companies_from_companies_house(limit=3, user_id=user_id)
        except Exception as exc:
            print(f"[CH list sync] {exc}")

    try:
        BULK_JOB_POOL.submit(_job)
        return True
    except Exception as exc:
        print(f"[CH list sync] schedule failed: {exc}")
        return False


def official_company_director(company):
    stored = str((company or {}).get('director') or '').strip()
    company_name = (company or {}).get('name')
    if stored and not looks_like_placeholder_director(stored, company_name):
        pretty = pretty_director_name(stored)
        return '' if pretty.lower() == 'director' else pretty
    owner = company_owner_for_company((company or {}).get('id'))
    form_name = str((owner or {}).get('full_name') or '').strip()
    if form_name and not looks_like_placeholder_director(form_name, company_name):
        pretty = pretty_director_name(form_name)
        return '' if pretty.lower() == 'director' else pretty
    return ''


def registered_company_owner_name(company, resolve_owner=False):
    if is_pending_company_number((company or {}).get('company_number')):
        return ''
    stored = str((company or {}).get('director') or '').strip()
    company_name = (company or {}).get('name')
    if stored and not looks_like_placeholder_director(stored, company_name):
        pretty = pretty_director_name(stored)
        return '' if pretty.lower() == 'director' else pretty
    if resolve_owner:
        return official_company_director(company) or ''
    return ''


def order_owner_display_name(row):
    company_name = (row or {}).get('company_name')
    owner = str((row or {}).get('owner_name') or '').strip()
    if owner and not looks_like_placeholder_director(owner, company_name):
        pretty = pretty_director_name(owner)
        return '' if pretty.lower() == 'director' else pretty
    director = str((row or {}).get('company_director') or '').strip()
    if (
        director
        and not is_pending_company_number((row or {}).get('company_number'))
        and not looks_like_placeholder_director(director, company_name)
    ):
        pretty = pretty_director_name(director)
        return '' if pretty.lower() == 'director' else pretty
    return owner


def connector_order_id(order_row):
    row = order_row or {}
    if row.get('invoice_number') is not None or row.get('file_type') or row.get('file_path'):
        return optional_record_id(row.get('order_id'))
    return optional_record_id(row.get('id'))


def persist_order_connector(order_row):
    """Store company owner and the company-creation email from the form."""
    row = dict(order_row or {})
    order_id = connector_order_id(row)
    if not order_id:
        return row
    has_name = str(row.get('owner_name') or '').strip() and not looks_like_placeholder_director(
        row.get('owner_name'), row.get('company_name')
    )
    has_email = is_usable_form_email(row.get('owner_form_email'))
    if has_name and has_email:
        return row
    fields = parse_order_checkout_fields(row)
    if not fields:
        return row
    upsert_company_owner_from_fields(order_id, row.get('company_id'), fields, replace_name=False)
    latest = query_db(
        "SELECT owner_name, owner_form_email, company_id FROM orders WHERE id = ?;",
        (order_id,),
        one=True,
    ) or {}
    row['owner_name'] = latest.get('owner_name') or row.get('owner_name')
    row['owner_form_email'] = latest.get('owner_form_email') or row.get('owner_form_email')
    if latest.get('company_id'):
        row['company_id'] = latest.get('company_id')
    return row


def real_order_connector(order_row):
    """Company owner and the email used to create the company. Never the website signup."""
    row = persist_order_connector(dict(order_row or {}))
    name = order_owner_display_name(row) or ''
    email = str((row or {}).get('owner_form_email') or '').strip()
    if not is_usable_form_email(email):
        email = ''
    company_id = optional_record_id((row or {}).get('company_id'))
    if (not name or not email) and company_id:
        owner = company_owner_for_company(company_id)
        if not name:
            stored = str((owner or {}).get('full_name') or '').strip()
            if stored and not looks_like_placeholder_director(stored, (row or {}).get('company_name')):
                pretty = pretty_director_name(stored)
                name = '' if pretty.lower() == 'director' else pretty
        if not email and is_usable_form_email((owner or {}).get('form_email')):
            email = str(owner['form_email']).strip()
        if not email:
            found = company_form_email(company_id) or ''
            email = found if is_usable_form_email(found) else ''
    if not name:
        director = str((row or {}).get('company_director') or '').strip()
        if director and not looks_like_placeholder_director(director, (row or {}).get('company_name')):
            pretty = pretty_director_name(director)
            name = '' if pretty.lower() == 'director' else pretty
    return {
        'owner_name': name,
        'owner_form_email': email if is_usable_form_email(email) else '',
    }


def apply_connector_display(row):
    connector = real_order_connector(row)
    out = dict(row or {})
    out['owner_name'] = connector['owner_name']
    out['owner_form_email'] = connector['owner_form_email']
    out['client_name'] = connector['owner_name']
    out['client_email'] = connector['owner_form_email']
    out.pop('checkout_form_json', None)
    return out


def compact_company_token(name):
    text = re.sub(r'[^a-z0-9]+', '', str(name or '').lower())
    for suffix in ('limited', 'ltd', 'llp', 'plc', 'cic'):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
    return text


COMMON_PERSON_EMAIL_TOKENS = frozenset({
    'muhammad', 'mohammed', 'mohammad', 'ahmed', 'ahmad', 'ali', 'khan', 'bin', 'binti',
})


def email_belongs_to_company(email, company_name=None):
    if not is_usable_form_email(email):
        return False
    local = re.sub(r'[^a-z0-9]+', '', str(email).split('@')[0].lower())
    token = compact_company_token(company_name)
    if token and len(token) >= 4 and local:
        if token[:5] in local or local[:5] in token:
            return True
    return False


def email_belongs_to_person(email, person_name=None):
    if not is_usable_form_email(email):
        return False
    local = re.sub(r'[^a-z0-9]+', '', str(email).split('@')[0].lower())
    if not local:
        return False
    for part in re.split(r'[^a-z0-9]+', str(person_name or '').lower()):
        if len(part) < 5 or part in COMMON_PERSON_EMAIL_TOKENS:
            continue
        if part in local:
            return True
    return False


def company_name_from_checkout_fields(fields):
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        if checkout_field_key(field.get('label')) in CHECKOUT_COMPANY_NAME_KEYS:
            value = str(field.get('value') or '').strip()
            if value and value.lower() not in {'united kingdom', 'uk', 'none', 'n/a', '-', '—'}:
                return value
    return ''


def order_matches_company_name(order_name, company_name):
    left = normalize_company_name_key(order_name)
    right = normalize_company_name_key(company_name)
    if not left or not right:
        return False
    return left == right or company_name_keys_are_near_duplicate(left, right)


def company_contact_email(company):
    company_id = (company or {}).get('id')
    name = (company or {}).get('name')
    email = company_form_email(company_id)
    if email:
        return email
    user_id = (company or {}).get('user_id')
    if user_id and name:
        extras = query_db(
            """
            SELECT owner_form_email, checkout_form_json
            FROM orders
            WHERE user_id = ?
            ORDER BY id DESC;
            """,
            (user_id,),
        ) or []
        for row in extras:
            raw = row.get('checkout_form_json')
            fields = []
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        fields = parsed
                except (TypeError, ValueError):
                    fields = []
            if not order_matches_company_name(company_name_from_checkout_fields(fields), name):
                continue
            profile = owner_profile_from_fields(fields)
            if is_usable_form_email(profile.get('form_email')):
                return str(profile.get('form_email') or '').strip()
            if is_usable_form_email(row.get('owner_form_email')):
                return str(row.get('owner_form_email') or '').strip()
    client_email = (company or {}).get('client_email')
    director = official_company_director(company) or (company or {}).get('director')
    if email_belongs_to_company(client_email, name) or email_belongs_to_person(client_email, director):
        return str(client_email).strip()
    return ''


def companies_house_status_value(raw_status, status_detail=None):
    raw = str(raw_status or 'active').lower()
    if companies_house_strike_off_proposed(raw, status_detail):
        if raw == 'dissolved':
            return 'Dissolved'
        return 'Strike off proposed'
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


_CH_PENDING_SYNC_BLOCKED = False
_CH_REGISTERED_SYNC_BLOCKED = False
CH_PENDING_RECHECK_SECONDS = 15 * 60
CH_PENDING_SYNC_BATCH = 5
CH_REGISTERED_RECHECK_SECONDS = 12 * 3600
CH_REGISTERED_SYNC_BATCH = 15


def companies_house_match_for_name(company_name, matches):
    needle = normalize_company_name_key(company_name)
    if not needle or len(needle) < 4:
        return None
    hits = []
    for match in matches or []:
        if normalize_company_name_key((match or {}).get('name')) == needle:
            hits.append(match)
    if matches and normalize_company_name_key((matches[0] or {}).get('name')) == needle:
        return matches[0]
    if len(hits) == 1:
        return hits[0]
    active = [item for item in hits if str(item.get('status') or '').lower() == 'active']
    if len(active) == 1:
        return active[0]
    return None


def apply_companies_house_profile_to_company(company_id, profile, director=None):
    number = normalize_company_number((profile or {}).get('company_number'))
    name = str((profile or {}).get('name') or '').strip()
    if not company_id or not number or not name:
        return False
    clash = query_db(
        "SELECT id, user_id, company_number FROM companies WHERE company_number = ? AND id != ?;",
        (number, company_id),
        one=True,
    )
    if clash:
        pending = query_db("SELECT id, user_id FROM companies WHERE id = ?;", (company_id,), one=True)
        if (
            pending
            and clash.get('user_id') == pending.get('user_id')
            and not is_pending_company_number(clash.get('company_number'))
            and absorb_company_into(company_id, clash['id'])
        ):
            return True
        execute_db("UPDATE companies SET ch_checked_at = CURRENT_TIMESTAMP WHERE id = ?;", (company_id,))
        return False
    current = query_db("SELECT director, sic_codes, registered_email FROM companies WHERE id = ?;", (company_id,), one=True) or {}
    director_name = director or fetch_companies_house_primary_director(number) or current.get('director') or 'Director'
    inc_date = str((profile or {}).get('inc_date') or '')[:10]
    if len(inc_date) < 10:
        inc_date = datetime.date.today().isoformat()
    sic_codes = str((profile or {}).get('sic_codes') or current.get('sic_codes') or '').strip()
    registered_email = str((profile or {}).get('registered_email') or current.get('registered_email') or '').strip()
    execute_db(
        """
        UPDATE companies
        SET name = ?, company_number = ?, status = ?, inc_date = ?, director = ?,
            reg_office = ?, sic_codes = ?, registered_email = ?, ch_checked_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """,
        (
            name,
            number,
            companies_house_status_value((profile or {}).get('status'), (profile or {}).get('status_detail')),
            inc_date,
            director_name,
            (profile or {}).get('reg_office') or 'United Kingdom',
            sic_codes or None,
            registered_email or None,
            company_id,
        ),
    )
    return True


def mark_company_ch_checked(company_id):
    if company_id:
        execute_db("UPDATE companies SET ch_checked_at = CURRENT_TIMESTAMP WHERE id = ?;", (company_id,))


def sync_pending_company_from_companies_house(company):
    global _CH_PENDING_SYNC_BLOCKED
    if _CH_PENDING_SYNC_BLOCKED or not company:
        return False, 'blocked'
    if not companies_house_api_key():
        return False, 'not_configured'
    if not is_pending_company_number(company.get('company_number')):
        return False, 'already_registered'
    name = str(company.get('name') or '').strip()
    if len(normalize_company_name_key(name)) < 4:
        mark_company_ch_checked(company.get('id'))
        return False, 'name_too_short'
    matches, error = search_companies_house(name)
    if error:
        if 'rejected' in error or 'not configured' in error:
            _CH_PENDING_SYNC_BLOCKED = True
            return False, error
        mark_company_ch_checked(company.get('id'))
        return False, error
    match = companies_house_match_for_name(name, matches)
    if not match:
        mark_company_ch_checked(company.get('id'))
        return False, 'no_match'
    profile, profile_err = fetch_companies_house_profile(match.get('company_number'))
    payload = profile or match
    if apply_companies_house_profile_to_company(company.get('id'), payload):
        sync_company_name_onto_linked_orders(company.get('id'), (payload or {}).get('name'))
        notify_company_registered(company.get('id'))
        return True, None
    return False, profile_err or 'not_applied'


def pending_companies_due_for_ch_sync(user_id=None, limit=CH_PENDING_SYNC_BATCH):
    sql = """
        SELECT id, name, company_number, director, user_id, created_at, ch_checked_at
        FROM companies
        WHERE (company_number IS NULL OR TRIM(company_number) = '' OR UPPER(company_number) LIKE 'REG-%')
    """
    params = []
    if user_id:
        sql += " AND user_id = ?"
        params.append(user_id)
    sql += " ORDER BY created_at DESC;"
    rows = []
    cutoff = datetime.datetime.now() - datetime.timedelta(seconds=CH_PENDING_RECHECK_SECONDS)
    for row in query_db(sql, params) or []:
        checked = str(row.get('ch_checked_at') or '').strip()
        if checked:
            try:
                checked_at = datetime.datetime.fromisoformat(checked[:19])
            except ValueError:
                checked_at = None
            if checked_at and checked_at > cutoff:
                continue
        rows.append(row)
        if len(rows) >= int(limit or CH_PENDING_SYNC_BATCH):
            break
    return rows


def sync_pending_companies_from_companies_house(user_id=None, company_id=None, limit=CH_PENDING_SYNC_BATCH):
    global _CH_PENDING_SYNC_BLOCKED
    updated = 0
    if not _CH_PENDING_SYNC_BLOCKED and companies_house_api_key():
        if company_id:
            company = query_db(
                "SELECT id, name, company_number, director, user_id, ch_checked_at FROM companies WHERE id = ?;",
                (company_id,),
                one=True,
            )
            pending = [company] if company else []
        else:
            pending = pending_companies_due_for_ch_sync(user_id=user_id, limit=limit)
        for company in pending:
            if _CH_PENDING_SYNC_BLOCKED:
                break
            ok, _error = sync_pending_company_from_companies_house(company)
            if ok:
                updated += 1
    notify_recent_company_registrations(user_id=user_id, company_id=company_id)
    return updated


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

        director = fetch_companies_house_primary_director(number) or 'Director'
        mapped_status = companies_house_status_value(profile.get('status'), profile.get('status_detail'))
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
            INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status, registered_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                registered_email_for_client(client, data) or None,
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
    if actor and actor.get('role') == 'CLIENT':
        client_id = actor['id']
        client = query_db("SELECT id, full_name, email, role FROM users WHERE id = ?;", (client_id,), one=True)
        if not client:
            return None, None, 'Client account not found'
        return client_id, client, None
    client_id = optional_record_id(extras.get('client_id') or extras.get('user_id'))
    if client_id:
        client = query_db("SELECT id, full_name, email, role FROM users WHERE id = ?;", (client_id,), one=True)
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

    existing = query_db("SELECT id, full_name, email, role FROM users WHERE LOWER(email) = ?;", (new_email,), one=True)
    if existing:
        if existing.get('role') != 'CLIENT':
            return None, None, 'That email belongs to a staff account'
        return existing['id'], existing, None

    local_id = f"local_client_{uuid.uuid4().hex[:16]}"
    client_id = execute_db("""
        INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, country, role, status, last_synced_at)
        VALUES (?, ?, ?, ?, ?, 'United Kingdom', 'CLIENT', 'Active', CURRENT_TIMESTAMP);
    """, (local_id, new_email, unusable_password_hash(), new_name, new_phone))
    client = query_db("SELECT id, full_name, email, role FROM users WHERE id = ?;", (client_id,), one=True)
    if actor:
        log_activity(actor, 'USER_CREATED', 'users', str(client_id), f"Created client {new_email} while adding company")
    return client_id, client, None


def generate_next_b2b_id():
    max_num = 0
    for row in query_db("SELECT b2b_id FROM companies WHERE b2b_id LIKE 'B2B-%';") or []:
        try:
            num = int((row['b2b_id'] or '').replace('B2B-', ''))
            if num > max_num:
                max_num = num
        except (ValueError, AttributeError):
            pass
    for row in query_db("SELECT b2b_id FROM users WHERE b2b_id LIKE 'B2B-%';") or []:
        try:
            num = int((row['b2b_id'] or '').replace('B2B-', ''))
            if num > max_num:
                max_num = num
        except (ValueError, AttributeError):
            pass
    return f"B2B-{(max_num + 1):06d}"


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
    form_email = registered_email_for_client(client, extras)
    b2b_id = generate_next_b2b_id()
    company_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status, registered_email, b2b_id, client_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'B2B');
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
            form_email or None,
            b2b_id,
        ),
    )
    created = query_db(
        "SELECT id, name, company_number, status, inc_date, director, reg_office FROM companies WHERE id = ?;",
        (company_id,),
        one=True,
    )
    hydrate_company_from_companies_house(created, extras)
    persist_company_registered_email({'id': company_id, 'user_id': client_id, 'name': name, 'registered_email': form_email}, form_email)
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
    created = query_db("SELECT company_number FROM companies WHERE id = ?;", (company_id,), one=True)
    persist_company_registered_email(
        {'id': company_id, 'user_id': order.get('user_id'), 'name': name},
        order.get('owner_form_email') or order.get('client_email'),
    )
    if created and not is_pending_company_number(created.get('company_number')):
        notify_company_registered(company_id)
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
    if wc_id:
        pending = query_db(
            "SELECT id, name FROM companies WHERE user_id = ? AND company_number = ?;",
            (client_id, f'REG-{wc_id}'),
            one=True,
        )
        if pending:
            if company_name and str(pending.get('name') or '').strip() != company_name:
                execute_db("UPDATE companies SET name = ? WHERE id = ?;", (company_name, pending['id']))
            return pending['id']
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
    b2b_id = generate_next_b2b_id()
    company_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status, b2b_id, client_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, 'B2B');
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
            b2b_id,
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
                "UPDATE orders SET company_id = COALESCE(company_id, ?), portfolio_hidden = CASE WHEN ? IS NOT NULL THEN 0 ELSE portfolio_hidden END WHERE order_number = ? AND user_id = ?;",
                (company_id, company_id, order_num, client['id']),
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


def build_admin_dashboard_payload(user):
    """Rich overview payload for the Apple-style admin dashboard."""
    tot_customers = int(query_db("SELECT COUNT(*) as c FROM users WHERE role = 'CLIENT';", one=True)['c'] or 0)
    tot_companies = int(query_db(
        "SELECT COUNT(*) as c FROM companies WHERE COALESCE(status, '') NOT IN ('Dissolved', 'Liquidation', 'Closed');",
        one=True,
    )['c'] or 0)
    tot_orders = int(query_db("SELECT COUNT(*) as c FROM orders;", one=True)['c'] or 0)
    pending_orders = int(query_db(
        "SELECT COUNT(*) as c FROM orders WHERE status IN ('Pending', 'Pending Verification', 'Processing', 'In Progress');",
        one=True,
    )['c'] or 0)
    completed_orders = int(query_db("SELECT COUNT(*) as c FROM orders WHERE status = 'Completed';", one=True)['c'] or 0)
    open_tickets = int(query_db(
        "SELECT COUNT(*) as c FROM support_tickets WHERE status IN ('Open', 'In Progress');",
        one=True,
    )['c'] or 0)
    tot_docs = int(query_db("SELECT COUNT(*) as c FROM documents;", one=True)['c'] or 0)
    pending_docs = int(query_db(
        "SELECT COUNT(*) as c FROM documents WHERE status IN ('Pending Review', 'Pending') OR overall_status IN ('REVIEW_REQUIRED', 'PENDING');",
        one=True,
    )['c'] or 0)
    staff_count = int(query_db(
        "SELECT COUNT(*) as c FROM users WHERE role IN ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF');",
        one=True,
    )['c'] or 0)

    today = datetime.date.today()
    today_orders = int(query_db(
        "SELECT COUNT(*) as c FROM orders WHERE date(created_at) = date('now', 'localtime');",
        one=True,
    )['c'] or 0)
    today_customers = int(query_db(
        "SELECT COUNT(*) as c FROM users WHERE role = 'CLIENT' AND date(created_at) = date('now', 'localtime');",
        one=True,
    )['c'] or 0)
    month_orders = int(query_db(
        "SELECT COUNT(*) as c FROM orders WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime');",
        one=True,
    )['c'] or 0)

    def _pct(part, whole):
        whole = float(whole or 0)
        if whole <= 0:
            return 0
        return int(round(100.0 * float(part or 0) / whole))

    stats = {
        'total_customers': tot_customers,
        'total_companies': tot_companies,
        'total_orders': tot_orders,
        'pending_orders': pending_orders,
        'completed_orders': completed_orders,
        'open_tickets': open_tickets,
        'total_documents': tot_docs,
        'pending_documents': pending_docs,
        'staff_count': staff_count,
        'today_orders': today_orders,
        'today_customers': today_customers,
        'month_orders': month_orders,
        'completion_rate': _pct(completed_orders, tot_orders),
        'pending_rate': _pct(pending_orders, tot_orders),
    }

    charts = {
        'monthly': [],
        'orders_daily': [],
        'orders_weekly': [],
        'orders_yearly': [],
    }

    # Daily order counts for the current month (User Stat / Monthly)
    daily_rows = query_db("""
        SELECT CAST(strftime('%d', created_at) AS INTEGER) AS day_n, COUNT(*) AS cnt
        FROM orders
        WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')
        GROUP BY strftime('%d', created_at)
        ORDER BY day_n;
    """) or []
    by_day = {int(r['day_n']): int(r['cnt'] or 0) for r in daily_rows if r.get('day_n')}
    days_in_month = (datetime.date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1) - datetime.timedelta(days=1)).day
    charts['orders_daily'] = [
        {'label': str(d), 'cnt': by_day.get(d, 0)}
        for d in range(1, days_in_month + 1)
        if d <= today.day or d % 2 == 1
    ]
    # Keep chart readable: show every day up to today
    charts['orders_daily'] = [{'label': str(d), 'cnt': by_day.get(d, 0)} for d in range(1, today.day + 1)]

    # Last 7 days
    week_rows = query_db("""
        SELECT date(created_at) AS d, COUNT(*) AS cnt
        FROM orders
        WHERE date(created_at) >= date('now', 'localtime', '-6 days')
        GROUP BY date(created_at)
        ORDER BY d;
    """) or []
    by_week = {str(r['d']): int(r['cnt'] or 0) for r in week_rows if r.get('d')}
    for offset in range(6, -1, -1):
        day = today - datetime.timedelta(days=offset)
        key = day.isoformat()
        charts['orders_weekly'].append({
            'label': day.strftime('%a'),
            'cnt': by_week.get(key, 0),
        })

    # Last 12 months order counts
    year_rows = query_db("""
        SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS cnt
        FROM orders
        GROUP BY strftime('%Y-%m', created_at);
    """) or []
    by_year_month = {str(r['month']): int(r['cnt'] or 0) for r in year_rows if r.get('month')}
    for offset in range(11, -1, -1):
        year, month = shift_calendar_month(today.year, today.month, -offset)
        key = f'{year:04d}-{month:02d}'
        charts['orders_yearly'].append({
            'label': datetime.date(year, month, 1).strftime('%b'),
            'cnt': by_year_month.get(key, 0),
        })

    # Recent activity feed for top carousel
    activity = []
    for row in (query_db("""
        SELECT o.id, o.order_number, o.service_name, o.status, o.created_at, u.full_name AS client_name
        FROM orders o
        LEFT JOIN users u ON u.id = o.user_id
        ORDER BY o.created_at DESC LIMIT 4;
    """) or []):
        activity.append({
            'kind': 'order',
            'icon': 'shopping-bag',
            'tone': 'blue',
            'title': row.get('service_name') or 'New order',
            'subtitle': f"Order {row.get('order_number') or row.get('id')} · {row.get('status') or 'Pending'}",
            'meta_time': _format_activity_time(row.get('created_at')),
            'meta_tag': 'Order',
            'meta_user': row.get('client_name') or 'Client',
            'view': 'admin-orders',
        })
    while len(activity) < 4:
        for row in (query_db("""
            SELECT id, full_name, email, created_at FROM users
            WHERE role = 'CLIENT' ORDER BY created_at DESC LIMIT 4;
        """) or []):
            if len(activity) >= 4:
                break
            activity.append({
                'kind': 'customer',
                'icon': 'user-plus',
                'tone': 'orange',
                'title': f"New client {row.get('full_name') or 'Customer'}",
                'subtitle': row.get('email') or 'Client account created',
                'meta_time': _format_activity_time(row.get('created_at')),
                'meta_tag': 'Client',
                'meta_user': row.get('full_name') or 'Staff',
                'view': 'admin-customers',
            })
        break
    while len(activity) < 4:
        activity.append({
            'kind': 'placeholder',
            'icon': 'sparkles',
            'tone': 'green',
            'title': 'Portal activity',
            'subtitle': 'New orders and clients will appear here.',
            'meta_time': '—',
            'meta_tag': 'System',
            'meta_user': 'Brixen',
            'view': 'admin-dashboard',
        })

    # Horizontal / vertical bar metrics
    breakdown = [
        {'key': 'customers', 'label': 'Clients', 'value': tot_customers, 'tone': 'purple'},
        {'key': 'companies', 'label': 'Companies', 'value': tot_companies, 'tone': 'red'},
        {'key': 'orders', 'label': 'Orders', 'value': tot_orders, 'tone': 'orange'},
        {'key': 'tickets', 'label': 'Open tickets', 'value': open_tickets, 'tone': 'blue'},
    ]
    max_break = max([b['value'] for b in breakdown] + [1])
    for item in breakdown:
        item['pct'] = _pct(item['value'], max_break) if max_break else 0

    rings = [
        {
            'key': 'completed',
            'label': 'Completed',
            'pct': stats['completion_rate'],
            'value': completed_orders,
            'tone': 'purple',
            'view': 'admin-orders',
        },
        {
            'key': 'pending',
            'label': 'In progress',
            'pct': stats['pending_rate'],
            'value': pending_orders,
            'tone': 'orange',
            'view': 'admin-orders',
        },
        {
            'key': 'documents',
            'label': 'Documents',
            'pct': _pct(tot_docs - pending_docs, tot_docs) if tot_docs else 0,
            'value': tot_docs,
            'tone': 'blue',
            'view': 'admin-documents',
        },
        {
            'key': 'companies',
            'label': 'Companies',
            'pct': min(100, _pct(tot_companies, max(tot_customers, 1) * 2)),
            'value': tot_companies,
            'tone': 'green',
            'view': 'admin-companies',
        },
    ]

    ops = {
        'grade': stats['completion_rate'],
        'pending_orders': pending_orders,
        'open_tickets': open_tickets,
        'pending_documents': pending_docs,
        'gauge_value': pending_orders + open_tickets,
        'gauge_label': 'Open work',
    }

    if can_view_revenue(user):
        total_revenue = float(query_db(
            "SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status != 'Cancelled';",
            one=True,
        )['s'] or 0)
        pending_payments = float(query_db(
            "SELECT COALESCE(SUM(total), 0) as s FROM invoices WHERE status = 'Pending';",
            one=True,
        )['s'] or 0)
        stats['total_revenue'] = f"£{total_revenue:,.2f}"
        stats['pending_payments'] = f"£{pending_payments:,.2f}"
        stats['total_revenue_raw'] = round(total_revenue, 2)
        ops['revenue'] = stats['total_revenue']
        charts['monthly'] = build_monthly_revenue_chart(6)

    return {
        'stats': stats,
        'charts': charts,
        'activity': activity[:4],
        'breakdown': breakdown,
        'rings': rings,
        'ops': ops,
    }


def _format_activity_time(raw):
    if not raw:
        return '—'
    text = str(raw)
    try:
        # SQLite timestamps: YYYY-MM-DD HH:MM:SS
        if ' ' in text:
            return text.split(' ')[1][:5]
        return text[:10]
    except Exception:
        return text[:5]


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


def search_like_term(raw):
    term = (raw or '').strip()
    term = term.replace('\\', '').replace('%', '').replace('_', '')
    if len(term) < 2:
        return None
    return f'%{term}%'


def _search_hit(item_id, item_type, title, subtitle, extra=None):
    row = {
        'id': item_id,
        'type': item_type,
        'title': title or '',
        'subtitle': subtitle or '',
    }
    if extra:
        row.update(extra)
    return row


def _manager_client_scope_sql(user, alias='u'):
    if user and user.get('role') == 'MANAGER':
        return (
            f" AND {alias}.id IN (SELECT user_id FROM orders WHERE assigned_staff_id = ? OR assigned_staff_id IS NULL OR assigned_staff_id IN (SELECT id FROM users WHERE role = 'STAFF'))",
            [user['id']]
        )
    return '', []


def run_portal_search(user, query):
    empty = {
        'customers': [],
        'orders': [],
        'companies': [],
        'documents': [],
        'invoices': [],
    }
    like = search_like_term(query)
    if not user or not like:
        return empty

    is_client = user.get('role') == 'CLIENT'
    uid = user['id']
    limit = 8
    customers, orders, companies, documents, invoices = [], [], [], [], []

    if is_client:
        order_rows = query_db("""
            SELECT o.id, o.order_number, o.service_name, c.name as company_name
            FROM orders o
            LEFT JOIN companies c ON o.company_id = c.id
            WHERE o.user_id = ?
              AND (o.order_number LIKE ? OR o.service_name LIKE ? OR IFNULL(c.name, '') LIKE ?)
            ORDER BY o.created_at DESC
            LIMIT ?;
        """, (uid, like, like, like, limit)) or []
        for row in order_rows:
            subtitle = row.get('service_name') or ''
            if row.get('company_name'):
                subtitle = f"{subtitle} · {row['company_name']}".strip(' ·')
            orders.append(_search_hit(row['id'], 'order', row.get('order_number'), subtitle))

        company_rows = query_db("""
            SELECT id, name, company_number
            FROM companies
            WHERE user_id = ?
              AND (name LIKE ? OR company_number LIKE ? OR director LIKE ?)
            ORDER BY created_at DESC
            LIMIT ?;
        """, (uid, like, like, like, limit)) or []
        for row in company_rows:
            companies.append(_search_hit(row['id'], 'company', row.get('name'), row.get('company_number')))

        doc_rows = query_db("""
            SELECT d.id, d.name, d.file_type, d.category, o.order_number
            FROM documents d
            LEFT JOIN orders o ON d.order_id = o.id
            WHERE d.user_id = ? AND d.client_visible = 1
              AND (d.name LIKE ? OR d.category LIKE ? OR IFNULL(o.order_number, '') LIKE ?)
            ORDER BY d.created_at DESC
            LIMIT ?;
        """, (uid, like, like, like, limit)) or []
        for row in doc_rows:
            subtitle = row.get('category') or ''
            if row.get('order_number'):
                subtitle = f"{subtitle} · {row['order_number']}".strip(' ·')
            documents.append(_search_hit(
                row['id'], 'document', row.get('name'), subtitle,
                extra={'file_type': row.get('file_type') or ''}
            ))

        invoice_rows = query_db("""
            SELECT i.id, i.invoice_number, i.status, o.order_number
            FROM invoices i
            LEFT JOIN orders o ON i.order_id = o.id
            WHERE i.user_id = ?
              AND (i.invoice_number LIKE ? OR IFNULL(o.order_number, '') LIKE ?)
            ORDER BY i.created_at DESC
            LIMIT ?;
        """, (uid, like, like, limit)) or []
        for row in invoice_rows:
            subtitle = row.get('status') or ''
            if row.get('order_number'):
                subtitle = f"{subtitle} · {row['order_number']}".strip(' ·')
            invoices.append(_search_hit(row['id'], 'invoice', row.get('invoice_number'), subtitle))
        return {
            'customers': customers,
            'orders': orders,
            'companies': companies,
            'documents': documents,
            'invoices': invoices,
        }

    if check_permission(user, 'clients.view'):
        sql = """
            SELECT u.id, u.full_name, u.email, u.phone, u.b2b_id
            FROM users u
            WHERE u.role = 'CLIENT'
              AND (u.full_name LIKE ? OR u.email LIKE ? OR IFNULL(u.phone, '') LIKE ? OR IFNULL(u.b2b_id, '') LIKE ?)
        """
        params = [like, like, like, like]
        scope_sql, scope_params = _manager_client_scope_sql(user, 'u')
        sql += scope_sql
        params.extend(scope_params)
        sql += " ORDER BY u.full_name COLLATE NOCASE LIMIT ?;"
        params.append(limit)
        for row in query_db(sql, params) or []:
            subtitle = row.get('b2b_id') or row.get('email') or ''
            if row.get('b2b_id') and row.get('email'):
                subtitle = f"{row['b2b_id']} · {row['email']}"
            if row.get('phone'):
                subtitle = f"{subtitle} · {row['phone']}".strip(' ·')
            customers.append(_search_hit(row['id'], 'customer', row.get('full_name'), subtitle))

    if check_permission(user, 'orders.view'):
        clauses = [
            "(o.order_number LIKE ? OR o.service_name LIKE ? OR u.full_name LIKE ? OR u.email LIKE ? OR IFNULL(c.name, '') LIKE ? OR IFNULL(c.b2b_id, '') LIKE ? OR IFNULL(u.b2b_id, '') LIKE ?)"
        ]
        params = [like, like, like, like, like, like, like]
        scope_sql, scope_params = manager_order_scope(user)
        if scope_sql:
            clauses.append(scope_sql)
            params.extend(scope_params)
        sql = """
            SELECT o.id, o.order_number, o.service_name, u.full_name as client_name, c.name as company_name, c.b2b_id as company_b2b_id
            FROM orders o
            JOIN users u ON o.user_id = u.id
            LEFT JOIN companies c ON o.company_id = c.id
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY o.created_at DESC
            LIMIT ?;
        """
        params.append(limit)
        for row in query_db(sql, params) or []:
            bits = [row.get('client_name') or '', row.get('service_name') or '']
            if row.get('company_b2b_id'):
                bits.append(row['company_b2b_id'])
            elif row.get('company_name'):
                bits.append(row['company_name'])
            orders.append(_search_hit(row['id'], 'order', row.get('order_number'), ' · '.join([b for b in bits if b])))

    if user.get('role') in INTERNAL_STAFF_ROLES:
        sql = """
            SELECT c.id, c.name, c.company_number, c.b2b_id, u.full_name as client_name
            FROM companies c
            JOIN users u ON c.user_id = u.id
            WHERE (c.name LIKE ? OR c.company_number LIKE ? OR c.director LIKE ? OR IFNULL(c.b2b_id, '') LIKE ? OR u.full_name LIKE ? OR u.email LIKE ?)
        """
        params = [like, like, like, like, like, like]
        scope_sql, scope_params = _manager_client_scope_sql(user, 'u')
        sql += scope_sql
        params.extend(scope_params)
        sql += " ORDER BY c.created_at DESC LIMIT ?;"
        params.append(limit)
        for row in query_db(sql, params) or []:
            subtitle = row.get('b2b_id') or row.get('company_number') or ''
            if row.get('b2b_id') and row.get('company_number'):
                subtitle = f"{row['b2b_id']} · {row['company_number']}"
            if row.get('client_name'):
                subtitle = f"{subtitle} · {row['client_name']}".strip(' ·')
            companies.append(_search_hit(row['id'], 'company', row.get('name'), subtitle))

    if check_permission(user, 'documents.view'):
        sql = """
            SELECT d.id, d.name, d.file_type, u.full_name as client_name, o.order_number
            FROM documents d
            LEFT JOIN users u ON d.user_id = u.id
            LEFT JOIN orders o ON d.order_id = o.id
            WHERE (d.name LIKE ? OR d.category LIKE ? OR IFNULL(u.full_name, '') LIKE ? OR IFNULL(u.email, '') LIKE ? OR IFNULL(o.order_number, '') LIKE ?)
        """
        params = [like, like, like, like, like]
        scope_sql, scope_params = _manager_client_scope_sql(user, 'u')
        sql += scope_sql
        params.extend(scope_params)
        sql += " ORDER BY d.created_at DESC LIMIT ?;"
        params.append(limit)
        for row in query_db(sql, params) or []:
            bits = [row.get('client_name') or '', row.get('order_number') or '']
            documents.append(_search_hit(
                row['id'], 'document', row.get('name'), ' · '.join([b for b in bits if b]),
                extra={'file_type': row.get('file_type') or ''}
            ))

    if check_permission(user, 'invoices.view'):
        sql = """
            SELECT i.id, i.invoice_number, i.status, u.full_name as client_name, o.order_number
            FROM invoices i
            LEFT JOIN users u ON i.user_id = u.id
            LEFT JOIN orders o ON i.order_id = o.id
            WHERE (i.invoice_number LIKE ? OR IFNULL(o.order_number, '') LIKE ? OR IFNULL(u.full_name, '') LIKE ? OR IFNULL(u.email, '') LIKE ?)
        """
        params = [like, like, like, like]
        scope_sql, scope_params = _manager_client_scope_sql(user, 'u')
        sql += scope_sql
        params.extend(scope_params)
        sql += " ORDER BY i.created_at DESC LIMIT ?;"
        params.append(limit)
        for row in query_db(sql, params) or []:
            bits = [row.get('client_name') or '', row.get('status') or '', row.get('order_number') or '']
            invoices.append(_search_hit(row['id'], 'invoice', row.get('invoice_number'), ' · '.join([b for b in bits if b])))

    return {
        'customers': customers,
        'orders': orders,
        'companies': companies,
        'documents': documents,
        'invoices': invoices,
    }


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
            OR c.name LIKE ? OR o.owner_name LIKE ? OR o.owner_form_email LIKE ?
            OR EXISTS (SELECT 1 FROM order_line_items li WHERE li.order_id = o.id AND (li.product_name LIKE ? OR li.category_name LIKE ?))
        )""")
        params.extend([term, term, term, term, term, term, term, term, term])

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

    payment_status = _qs_first(qs, 'payment_status') if can_view_revenue(user) else ''
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
    subject = f"New website order {order_number} — Brixen Consultants"
    body = (
        f"A new website order is in the CRM.\n\n"
        f"Order: {order_number}\n"
        f"Client: {client_name}\n\n"
        f"Open orders: {portal_page_url()}#admin-orders"
    )
    email_team_about_clients(subject, body)


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


def normalize_whatsapp_number(value):
    """Normalize a WhatsApp contact number for company cards."""
    text = str(value or '').strip()
    if not text:
        return '', None
    formatted = format_uk_phone(text)
    if formatted:
        return formatted, None
    digits = re.sub(r'\D', '', text)
    if len(digits) < 8 or len(digits) > 15:
        return None, 'Enter a valid WhatsApp number'
    if digits.startswith('00'):
        digits = digits[2:]
    return f'+{digits}', None


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
    'customer name checkout': 'director name',
    'customer': 'director name',
    'director': 'director name',
    'dob': 'date of birth',
    'phone': 'uk contact number',
    'email': 'email form',
    'role': 'company role',
    'sic': 'sic code',
    'desired company name': 'desired company name',
    'package': 'package',
    'company number': 'company number',
    'companies house number': 'company number',
    'registered office': 'registered address',
    'registered office address': 'registered address',
    'passport cnic': 'passport cnic',
    'passport number': 'passport cnic',
    'passport no': 'passport cnic',
    'passport': 'passport cnic',
    'cnic': 'passport cnic',
    'id number': 'passport cnic',
}

HOME_ADDRESS_META = (
    '_cfs_address_street',
    '_cfs_address_line2',
    '_cfs_address_city',
    '_cfs_address_state',
    '_cfs_address_zip',
    '_cfs_address_country',
)
REGISTERED_ADDRESS_META = (
    '_cfs_registered_address_street',
    '_cfs_registered_address_line2',
    '_cfs_registered_address_city',
    '_cfs_registered_address_state',
    '_cfs_registered_address_zip',
    '_cfs_registered_address_country',
)
ADDRESS_META_KEYS = set(HOME_ADDRESS_META + REGISTERED_ADDRESS_META)
ADDRESS_PART_RANK = {
    'street': 0,
    'line 1': 0,
    'address line 1': 0,
    'line2': 1,
    'line 2': 1,
    'address line 2': 1,
    'city': 2,
    'state': 3,
    'county': 3,
    'county / state': 3,
    'zip': 4,
    'postcode': 4,
    'postal': 4,
    'country': 5,
}
ADDRESS_GROUP_LABELS = {
    'registered address': 'Registered address',
    'registered company address': 'Registered address',
    'registered address (form)': 'Registered address',
    'billing address': 'Billing address',
    'shipping address': 'Shipping address',
    'director address': 'Director address',
    'director home address': 'Director address',
    'address': 'Address',
}


def checkout_field_key(label):
    raw = re.sub(r'[^a-z0-9]+', ' ', str(label or '').strip().lower())
    raw = re.sub(r'\s+', ' ', raw).strip()
    return CHECKOUT_LABEL_ALIASES.get(raw, raw)


def _join_address_parts(parts):
    seen = []
    for part in parts:
        text = str(part or '').strip()
        if text and text not in seen:
            seen.append(text)
    return ', '.join(seen)


def checkout_address_part_key(label):
    low = re.sub(r'\s+', ' ', str(label or '').strip().lower())
    if not low:
        return None, None
    if low in ADDRESS_GROUP_LABELS:
        return ADDRESS_GROUP_LABELS[low], None
    match = re.match(
        r'^(registered(?:\s+company)?\s+address|billing\s+address|shipping\s+address|director(?:\s+home)?\s+address|address)\s+(.+)$',
        low,
    )
    if match:
        group = ADDRESS_GROUP_LABELS.get(match.group(1), match.group(1).title())
        rest = match.group(2).strip().replace('county/state', 'county / state')
        if rest not in ADDRESS_PART_RANK:
            last = rest.split()[-1]
            rest = last if last in ADDRESS_PART_RANK else rest
        if rest in ADDRESS_PART_RANK:
            return group, rest
        return None, None
    if low in ADDRESS_PART_RANK or low in ('address line 1', 'address line 2', 'county / state'):
        return 'Address', low
    return None, None


def coalesce_checkout_address_fields(fields):
    if not fields:
        return fields
    grouped = {}
    result = []
    for field in fields:
        if not isinstance(field, dict):
            result.append(field)
            continue
        group, part = checkout_address_part_key(field.get('label'))
        value = str(field.get('value') or '').strip()
        if not group or not value:
            result.append(field)
            continue
        if part is None:
            if group not in grouped:
                grouped[group] = {'pos': len(result), 'parts': {}, 'ready': value}
                result.append(None)
            else:
                grouped[group]['ready'] = grouped[group].get('ready') or value
            continue
        if group not in grouped:
            grouped[group] = {'pos': len(result), 'parts': {}}
            result.append(None)
        rank = ADDRESS_PART_RANK.get(part, 50)
        grouped[group]['parts'].setdefault(rank, value)
    for group, info in grouped.items():
        line = info.get('ready') or _join_address_parts(
            info['parts'][key] for key in sorted(info['parts'])
        )
        result[info['pos']] = {'label': group, 'value': line}
    return [item for item in result if item]


def normalize_checkout_form_fields(fields):
    coalesced = coalesce_checkout_address_fields(fields or [])
    seen = set()
    out = []
    for field in coalesced:
        if not isinstance(field, dict):
            continue
        label = str(field.get('label') or '').strip()
        value = str(field.get('value') or '').strip()
        if not label or not value:
            continue
        key = checkout_field_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({'label': label, 'value': value})
    return out


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
        def meta_address_line(keys):
            return _join_address_parts(str(meta.get(key) or '').strip() for key in keys)

        registered_line = meta_address_line(REGISTERED_ADDRESS_META)
        home_line = meta_address_line(HOME_ADDRESS_META)
        for mk, mv in meta.items():
            if mk in CHECKOUT_META_SKIP or mk in ADDRESS_META_KEYS:
                continue
            if re.search(r'_(?:registered_)?address_(street|line2|city|state|zip|country)$', str(mk)):
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
            if mk.startswith('_cfs_') or mk.startswith('_apff_'):
                label = CHECKOUT_META_LABELS.get(mk) or mk.replace('_cfs_', '').replace('_apff_', '').replace('_', ' ').title()
                add(label, text)
        if registered_line:
            add('Registered address', registered_line)
        if home_line and home_line != registered_line:
            add('Director address', home_line)

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
    if billing and checkout_field_key('Registered address') not in seen and checkout_field_key('Billing address') not in seen:
        add('Billing address', billing)

    phone = billing_phone_from_webhook_payload(data)
    if phone and checkout_field_key('UK contact number') not in seen:
        add('UK contact number', phone)
    dob = date_of_birth_from_webhook_payload(data)
    if dob and checkout_field_key('Date of birth') not in seen:
        add('Date of birth', dob)
    form_email = None
    if isinstance(meta, dict):
        form_email = str(meta.get('_cfs_registered_email') or '').strip()
    if form_email and checkout_field_key('Email (form)') not in seen:
        add('Email (form)', form_email)

    return normalize_checkout_form_fields(fields)


CHECKOUT_COMPANY_NAME_KEYS = frozenset({
    'desired company name',
    'company name',
    'proposed company name',
    'proposed name',
    'billing company',
})


def apply_current_company_name_to_checkout_fields(order_row, fields):
    company_id = (order_row or {}).get('company_id')
    name = ''
    if company_id:
        company = query_db("SELECT name FROM companies WHERE id = ?;", (company_id,), one=True)
        name = str((company or {}).get('name') or '').strip()
    if not name:
        return list(fields or [])
    updated = []
    found = False
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        item = dict(field)
        if checkout_field_key(item.get('label')) in CHECKOUT_COMPANY_NAME_KEYS:
            item['value'] = name
            found = True
        updated.append(item)
    if not found:
        updated.insert(0, {'label': 'Desired company name', 'value': name})
    return updated


def sync_company_name_onto_linked_orders(company_id, name=None):
    if not company_id:
        return
    if not str(name or '').strip():
        row = query_db("SELECT name FROM companies WHERE id = ?;", (company_id,), one=True)
        name = str((row or {}).get('name') or '').strip()
    if not str(name or '').strip():
        return
    orders = query_db(
        "SELECT id, company_id, checkout_form_json FROM orders WHERE company_id = ?;",
        (company_id,),
    ) or []
    for order in orders:
        fields = []
        raw = order.get('checkout_form_json')
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    fields = parsed
            except (TypeError, ValueError):
                fields = []
        updated = apply_current_company_name_to_checkout_fields(order, fields)
        execute_db(
            "UPDATE orders SET checkout_form_json = ? WHERE id = ?;",
            (json.dumps(updated), order['id']),
        )


def _checkout_fields_with_live_company_name(order_row, fields):
    overlaid = apply_current_company_name_to_checkout_fields(order_row, fields)
    overlaid = fill_checkout_blanks_from_companies_house(order_row, overlaid)
    order_id = (order_row or {}).get('id')
    if order_id and overlaid:
        execute_db(
            "UPDATE orders SET checkout_form_json = ? WHERE id = ?;",
            (json.dumps(overlaid), order_id),
        )
    return overlaid


CHECKOUT_FORM_EMAIL_KEYS = frozenset({
    'email form',
    'registered email',
})


def is_usable_form_email(value):
    email = str(value or '').strip()
    return bool(email and '@' in email and '.' in email.split('@')[-1])


def owner_profile_from_fields(fields):
    profile = {
        'full_name': '',
        'form_email': '',
        'phone': '',
        'date_of_birth': '',
        'nationality': '',
        'passport_cnic': '',
        'address': '',
    }
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        key = checkout_field_key(field.get('label'))
        value = str(field.get('value') or '').strip()
        if not value:
            continue
        if key == 'director name':
            profile['full_name'] = value
        elif key in CHECKOUT_FORM_EMAIL_KEYS and is_usable_form_email(value):
            profile['form_email'] = value
        elif key == 'uk contact number':
            profile['phone'] = value
        elif key == 'date of birth':
            profile['date_of_birth'] = value
        elif key == 'issuance country':
            profile['nationality'] = value
        elif key == 'passport cnic':
            profile['passport_cnic'] = value
        elif key in ('director address', 'director home address'):
            profile['address'] = value
    return profile


def company_owner_public(row):
    if not row:
        return {
            'full_name': '',
            'form_email': '',
            'phone': '',
            'date_of_birth': '',
            'nationality': '',
            'passport_cnic': '',
            'address': '',
        }
    return {
        'full_name': str(row.get('full_name') or '').strip(),
        'form_email': str(row.get('form_email') or '').strip(),
        'phone': str(row.get('phone') or '').strip(),
        'date_of_birth': str(row.get('date_of_birth') or '').strip(),
        'nationality': str(row.get('nationality') or '').strip(),
        'passport_cnic': str(row.get('passport_cnic') or '').strip(),
        'address': str(row.get('address') or '').strip(),
    }


def upsert_company_owner_from_fields(order_id, company_id, fields, replace_name=False):
    if not order_id:
        return company_owner_public(None)
    incoming = owner_profile_from_fields(fields)
    current = query_db("SELECT * FROM company_owners WHERE order_id = ?;", (order_id,), one=True) or {}
    current_name = str(current.get('full_name') or '').strip()
    incoming_name = incoming['full_name']
    if incoming_name and (
        replace_name or not current_name or looks_like_placeholder_director(current_name)
    ):
        owner_name = incoming_name
    else:
        owner_name = current_name or incoming_name
    merged = {
        'full_name': owner_name,
        'form_email': incoming['form_email'] or str(current.get('form_email') or '').strip(),
        'phone': incoming['phone'] or str(current.get('phone') or '').strip(),
        'date_of_birth': incoming['date_of_birth'] or str(current.get('date_of_birth') or '').strip(),
        'nationality': incoming['nationality'] or str(current.get('nationality') or '').strip(),
        'passport_cnic': incoming['passport_cnic'] or str(current.get('passport_cnic') or '').strip(),
        'address': incoming['address'] or str(current.get('address') or '').strip(),
        'company_id': company_id or current.get('company_id'),
    }
    existing_id = current.get('id')
    if existing_id:
        execute_db(
            """
            UPDATE company_owners
            SET company_id = ?, full_name = ?, form_email = ?, phone = ?, date_of_birth = ?,
                nationality = ?, passport_cnic = ?, address = ?, source = 'formation_form',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (
                merged['company_id'], merged['full_name'], merged['form_email'], merged['phone'],
                merged['date_of_birth'], merged['nationality'], merged['passport_cnic'],
                merged['address'], existing_id,
            ),
        )
    else:
        execute_db(
            """
            INSERT INTO company_owners (
                order_id, company_id, full_name, form_email, phone, date_of_birth,
                nationality, passport_cnic, address, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'formation_form');
            """,
            (
                order_id, merged['company_id'], merged['full_name'], merged['form_email'],
                merged['phone'], merged['date_of_birth'], merged['nationality'],
                merged['passport_cnic'], merged['address'],
            ),
        )
    execute_db(
        "UPDATE orders SET owner_name = ?, owner_form_email = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
        (merged['full_name'] or None, merged['form_email'] or None, order_id),
    )
    if merged['company_id'] and merged['full_name']:
        execute_db("UPDATE companies SET director = ? WHERE id = ?;", (merged['full_name'], merged['company_id']))
        director_row = query_db(
            "SELECT id FROM company_directors WHERE company_id = ? AND LOWER(name) = LOWER(?) LIMIT 1;",
            (merged['company_id'], merged['full_name']),
            one=True,
        )
        if not director_row:
            execute_db(
                """
                INSERT INTO company_directors (company_id, name, role, nationality, appointed_date)
                VALUES (?, ?, 'Director', ?, date('now'));
                """,
                (merged['company_id'], merged['full_name'], merged['nationality'] or 'British'),
            )
    return company_owner_public(merged)


def get_company_owner_for_order(order_row, fields=None):
    order_id = (order_row or {}).get('id')
    stored = query_db("SELECT * FROM company_owners WHERE order_id = ?;", (order_id,), one=True) if order_id else None
    if stored and (stored.get('full_name') or stored.get('form_email')):
        return company_owner_public(stored)
    profile = owner_profile_from_fields(fields)
    if order_id and (profile['full_name'] or profile['form_email']):
        return upsert_company_owner_from_fields(order_id, (order_row or {}).get('company_id'), fields)
    if not profile['full_name']:
        profile['full_name'] = str((order_row or {}).get('owner_name') or '').strip()
    if not profile['form_email']:
        profile['form_email'] = str((order_row or {}).get('owner_form_email') or '').strip()
    return profile


def company_owner_for_company(company_id):
    if not company_id:
        return company_owner_public(None)
    stored = query_db(
        """
        SELECT full_name, form_email, phone, date_of_birth, nationality, passport_cnic, address
        FROM company_owners
        WHERE company_id = ?
          AND (COALESCE(full_name, '') != '' OR COALESCE(form_email, '') != '')
        ORDER BY id DESC LIMIT 1;
        """,
        (company_id,),
        one=True,
    )
    if stored:
        return company_owner_public(stored)
    order = query_db(
        """
        SELECT owner_name AS full_name, owner_form_email AS form_email, checkout_form_json
        FROM orders
        WHERE company_id = ?
        ORDER BY id DESC;
        """,
        (company_id,),
    ) or []
    form_name = ''
    form_email = ''
    for row in order:
        if not form_name:
            form_name = str(row.get('full_name') or '').strip()
        if not form_email and is_usable_form_email(row.get('form_email')):
            form_email = str(row.get('form_email') or '').strip()
        raw = row.get('checkout_form_json')
        if not raw:
            continue
        try:
            fields = json.loads(raw)
        except (TypeError, ValueError):
            continue
        profile = owner_profile_from_fields(fields)
        if not form_name:
            form_name = profile.get('full_name') or ''
        if not form_email and is_usable_form_email(profile.get('form_email')):
            form_email = profile.get('form_email') or ''
        if form_name and form_email:
            break
    if not form_email:
        form_email = company_form_email(company_id) or ''
    company = query_db("SELECT director FROM companies WHERE id = ?;", (company_id,), one=True) or {}
    return {
        **company_owner_public(None),
        'full_name': form_name or str(company.get('director') or '').strip(),
        'form_email': form_email,
    }


def accountancy_people_fields(company, for_client=False):
    director_name = official_company_director(company)
    director_email = company_contact_email(company)
    if for_client:
        return {
            'director': director_name,
            'director_email': director_email,
            'owner_name': director_name,
            'client_name': (company or {}).get('client_name'),
            'client_email': (company or {}).get('client_email'),
        }
    return {
        'director': director_name,
        'director_email': director_email,
        'owner_name': director_name,
        'client_name': director_name or (company or {}).get('client_name'),
        'client_email': director_email,
    }


def backfill_company_owners_from_orders():
    rows = query_db(
        "SELECT id, company_id, order_number, woocommerce_order_id, checkout_form_json FROM orders;"
    ) or []
    for row in rows:
        fields = []
        raw = row.get('checkout_form_json')
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    fields = parsed
            except (TypeError, ValueError):
                fields = []
        if not fields:
            payload = webhook_payload_for_order(row.get('order_number'), row.get('woocommerce_order_id'))
            if payload:
                fields = persist_order_checkout_form(row['id'], payload)
                continue
        if fields:
            upsert_company_owner_from_fields(row['id'], row.get('company_id'), fields)


def company_form_email(company_id, fallback=None):
    if company_id:
        owner = query_db(
            """
            SELECT form_email FROM company_owners
            WHERE company_id = ? AND COALESCE(form_email, '') != ''
            ORDER BY id DESC LIMIT 1;
            """,
            (company_id,),
            one=True,
        )
        if owner and is_usable_form_email(owner.get('form_email')):
            return str(owner['form_email']).strip()
        order_owner = query_db(
            """
            SELECT owner_form_email FROM orders
            WHERE company_id = ? AND COALESCE(owner_form_email, '') != ''
            ORDER BY id DESC LIMIT 1;
            """,
            (company_id,),
            one=True,
        )
        if order_owner and is_usable_form_email(order_owner.get('owner_form_email')):
            return str(order_owner['owner_form_email']).strip()
    orders = query_db(
        "SELECT checkout_form_json FROM orders WHERE company_id = ? ORDER BY id DESC;",
        (company_id,),
    ) or []
    for order in orders:
        raw = order.get('checkout_form_json')
        if not raw:
            continue
        try:
            fields = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(fields, list):
            continue
        for field in fields:
            if not isinstance(field, dict):
                continue
            if checkout_field_key(field.get('label')) not in CHECKOUT_FORM_EMAIL_KEYS:
                continue
            email = str(field.get('value') or '').strip()
            if is_usable_form_email(email):
                return email
    fallback_email = str(fallback or '').strip()
    if is_usable_form_email(fallback_email):
        return fallback_email
    return None


def set_order_form_email(order_id, email):
    email = str(email or '').strip()
    if not order_id or '@' not in email or '.' not in email:
        return False
    order = query_db("SELECT id, company_id, order_number, woocommerce_order_id, checkout_form_json FROM orders WHERE id = ?;", (order_id,), one=True)
    if not order:
        return False
    fields = []
    raw = order.get('checkout_form_json')
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                fields = parsed
        except (TypeError, ValueError):
            fields = []
    found = False
    old_values = set()
    updated_fields = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        item = dict(field)
        if checkout_field_key(item.get('label')) == 'email form':
            found = True
            previous = str(item.get('value') or '').strip()
            if previous and previous.lower() != email.lower():
                old_values.add(previous)
            item['value'] = email
        updated_fields.append(item)
    if not found:
        updated_fields.append({'label': 'Email (form)', 'value': email})
    execute_db(
        "UPDATE orders SET checkout_form_json = ? WHERE id = ?;",
        (json.dumps(updated_fields), order_id),
    )
    needles = {str(order.get('order_number') or '').strip(), str(order.get('woocommerce_order_id') or '').strip()}
    needles = {item for item in needles if item}
    events = query_db(
        "SELECT id, payload FROM webhook_events WHERE event_type IN ('order.created', 'order.updated', 'payment.completed') ORDER BY id DESC LIMIT 400;"
    ) or []
    for event in events:
        try:
            payload = json.loads(event.get('payload') or '')
        except (TypeError, ValueError):
            continue
        data = payload.get('data', payload) if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            continue
        data_order = str(data.get('order_number') or '')
        data_wc = str(data.get('woocommerce_order_id') or '')
        if needles and data_order not in needles and data_wc not in needles and not any(n in data_order for n in needles):
            continue
        previous = str(data.get('email') or '').strip()
        if previous and previous.lower() != email.lower():
            old_values.add(previous)
        data['email'] = email
        meta = data.get('meta')
        if isinstance(meta, dict):
            previous_meta = str(meta.get('_cfs_registered_email') or '').strip()
            if previous_meta and previous_meta.lower() != email.lower():
                old_values.add(previous_meta)
            meta['_cfs_registered_email'] = email
        notes = data.get('order_notes')
        if isinstance(notes, list) and old_values:
            replaced = []
            for note in notes:
                text = str(note or '')
                for old in old_values:
                    text = text.replace(old, email)
                replaced.append(text)
            data['order_notes'] = replaced
        if isinstance(payload, dict) and 'data' in payload:
            payload['data'] = data
        else:
            payload = data
        execute_db("UPDATE webhook_events SET payload = ? WHERE id = ?;", (json.dumps(payload), event['id']))
    upsert_company_owner_from_fields(order_id, order.get('company_id'), updated_fields)
    return True


def normalize_staff_checkout_fields(raw_fields):
    if not isinstance(raw_fields, list):
        return None, 'Checkout details must be a list of fields'
    if len(raw_fields) > 40:
        return None, 'Too many checkout fields'
    fields = []
    seen = set()
    for item in raw_fields:
        if not isinstance(item, dict):
            continue
        label = str(item.get('label') or '').strip()
        value = str(item.get('value') or '').strip()
        if not label:
            continue
        if len(label) > 80 or len(value) > 2000:
            return None, 'A checkout field is too long'
        key = checkout_field_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        fields.append({'label': label, 'value': value})
    if not fields:
        return None, 'Enter at least one checkout detail'
    return normalize_checkout_form_fields(fields), None


def apply_staff_checkout_form_update(order, raw_fields):
    fields, err = normalize_staff_checkout_fields(raw_fields)
    if err:
        return False, err
    order_id = order.get('id')
    company_id = order.get('company_id')
    company_name = director_name = package = registered_address = form_email = None
    for field in fields:
        key = checkout_field_key(field.get('label'))
        value = str(field.get('value') or '').strip()
        if not value:
            continue
        if key in CHECKOUT_COMPANY_NAME_KEYS:
            company_name = value
        elif key == 'director name':
            director_name = value
        elif key == 'package':
            package = value
        elif key in CHECKOUT_REGISTERED_ADDRESS_KEYS:
            registered_address = value
        elif key == 'email form':
            form_email = value
    execute_db(
        "UPDATE orders SET checkout_form_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
        (json.dumps(fields), order_id),
    )
    if form_email:
        set_order_form_email(order_id, form_email)
    upsert_company_owner_from_fields(order_id, company_id, fields, replace_name=True)
    if company_id:
        updates = []
        params = []
        if company_name:
            updates.append('name = ?')
            params.append(company_name)
        if director_name:
            updates.append('director = ?')
            params.append(director_name)
        if package:
            updates.append('package = ?')
            params.append(package)
        if registered_address:
            updates.append('reg_office = ?')
            params.append(registered_address)
        if updates:
            params.append(company_id)
            execute_db(f"UPDATE companies SET {', '.join(updates)} WHERE id = ?;", tuple(params))
        if company_name:
            sync_company_name_onto_linked_orders(company_id, company_name)
    return True, None


def notify_company_registered(company_id):
    company = query_db(
        """
        SELECT id, user_id, name, company_number, director, registration_notified_at
        FROM companies WHERE id = ?;
        """,
        (company_id,),
        one=True,
    )
    if not company or is_pending_company_number(company.get('company_number')):
        return {'notification_created': False, 'email_sent': False, 'email_status': 'Not registered'}
    if str(company.get('registration_notified_at') or '').strip():
        return {'notification_created': False, 'email_sent': False, 'email_status': 'Already sent'}
    client = resolve_client_user(company.get('user_id'))
    if not client:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'Client not found'}
    form_email = company_form_email(company['id'], fallback=client.get('email'))
    if not form_email:
        return {'notification_created': False, 'email_sent': False, 'email_status': 'No form email'}
    name = str(company.get('name') or 'Your company').strip()
    number = str(company.get('company_number') or '').strip()
    director_name = resolve_companies_house_director(company)
    ensure_incorporation_certificate(company)
    download_url = companies_house_filing_history_url(number)
    result = notify_client(
        client,
        'Congratulations — your company is registered',
        f"We've got great news, {name} is now officially registered with Companies House.",
        'success',
        '/companies',
        email_to=form_email,
        email_subject=f"Congratulations {name} is now registered with Companies House",
        email_headline=name,
        greeting_name=director_name,
        badge='Company registered',
        alert_label='Great news',
        layout='celebration',
        detail_title='Company number',
        detail_value=number,
        cta_label='Download now',
        cta_url=download_url,
        footer_note='This email is about your UK company registration with Brixen Consultants.',
    )
    if result.get('email_sent'):
        execute_db(
            "UPDATE companies SET registration_notified_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (company_id,),
        )
    return {
        **result,
        'email_to': form_email,
        'company_number': number,
    }


def send_congratulations_preview_email(recipient, company_name='Example Holdings Ltd', company_number='12345678', director_name='Alex Director'):
    recipient = str(recipient or '').strip()
    if not recipient or '@' not in recipient:
        return False, 'Enter a valid email address'
    name = str(company_name or 'Example Holdings Ltd').strip() or 'Example Holdings Ltd'
    number = str(company_number or '12345678').strip() or '12345678'
    director = str(director_name or 'Director').strip() or 'Director'
    download_url = companies_house_filing_history_url(number)
    _, body_text, body_html = build_client_notification_email(
        {'full_name': 'Brixen Team', 'email': recipient},
        name,
        f"We've got great news, {name} is now officially registered with Companies House.",
        subject=f"Congratulations {name} is now registered with Companies House",
        greeting_name=director,
        badge='Company registered',
        alert_label='Great news',
        layout='celebration',
        detail_title='Company number',
        detail_value=number,
        cta_label='Download now',
        cta_url=download_url,
        footer_note='This email is about your UK company registration with Brixen Consultants.',
    )
    return EmailService.send_notification_email(
        recipient,
        f"Congratulations {name} is now registered with Companies House",
        body_text,
        body_html,
    )


REGISTRATION_NOTICE_HOURS = 24
REGISTRATION_NOTICE_INTERVAL_SECONDS = 300


def companies_due_for_registration_notice(user_id=None, company_id=None, hours=REGISTRATION_NOTICE_HOURS):
    hours = max(1, int(hours or REGISTRATION_NOTICE_HOURS))
    sql = """
        SELECT id, name, company_number, inc_date, created_at, ch_checked_at, registration_notified_at
        FROM companies
        WHERE (company_number IS NOT NULL AND TRIM(company_number) != '' AND UPPER(company_number) NOT LIKE 'REG-%')
          AND (registration_notified_at IS NULL OR TRIM(COALESCE(registration_notified_at, '')) = '')
          AND date(inc_date) >= date('now', ?)
    """
    params = [f'-{hours} hours']
    if user_id:
        sql += " AND user_id = ?"
        params.append(user_id)
    if company_id:
        sql += " AND id = ?"
        params.append(company_id)
    sql += " ORDER BY inc_date DESC, id DESC;"
    return query_db(sql, params) or []


def notify_recent_company_registrations(user_id=None, company_id=None, hours=REGISTRATION_NOTICE_HOURS):
    sent = 0
    for row in companies_due_for_registration_notice(user_id=user_id, company_id=company_id, hours=hours):
        result = notify_company_registered(row['id'])
        if result.get('email_sent'):
            sent += 1
    return sent


def run_automatic_registration_notices():
    try:
        sync_pending_companies_from_companies_house()
    except Exception as err:
        print(f"[RegistrationNotice] Companies House sync failed: {err}")
    try:
        return notify_recent_company_registrations()
    except Exception as err:
        print(f"[RegistrationNotice] Congratulations send failed: {err}")
        return 0


_registration_notice_started = False
_registration_notice_lock = None


def start_registration_notice_worker():
    global _registration_notice_started, _registration_notice_lock
    if os.environ.get('CRM_TESTING') == '1':
        return False
    import threading
    if _registration_notice_lock is None:
        _registration_notice_lock = threading.Lock()
    with _registration_notice_lock:
        if _registration_notice_started:
            return False

        def _loop():
            import time
            while True:
                run_automatic_registration_notices()
                time.sleep(REGISTRATION_NOTICE_INTERVAL_SECONDS)

        thread = threading.Thread(target=_loop, name='registration-notices', daemon=True)
        thread.start()
        _registration_notice_started = True
        print("[RegistrationNotice] Automatic 24-hour congratulations worker started")
        return True


def persist_order_checkout_form(order_id, o_data):
    if not order_id:
        return []
    fields = checkout_form_fields_from_payload(o_data)
    if fields:
        execute_db(
            "UPDATE orders SET checkout_form_json = ? WHERE id = ?;",
            (json.dumps(fields), order_id),
        )
        order = query_db("SELECT company_id FROM orders WHERE id = ?;", (order_id,), one=True) or {}
        upsert_company_owner_from_fields(order_id, order.get('company_id'), fields)
    return fields


def webhook_payload_for_order(order_number, woocommerce_order_id=None):
    needle = str(order_number or '').strip()
    wc_id = str(woocommerce_order_id or '').strip()
    rows = query_db("""
        SELECT payload FROM webhook_events
        WHERE event_type IN ('order.created', 'order.updated', 'payment.completed')
        ORDER BY id DESC LIMIT 400;
    """) or []
    fallback = None
    for row in rows:
        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError):
            continue
        data = payload.get('data', payload)
        if not isinstance(data, dict):
            continue
        data_order = str(data.get('order_number') or '')
        data_wc = str(data.get('woocommerce_order_id') or data.get('id') or '')
        if needle and data_order != needle and needle not in data_order:
            if not wc_id or data_wc != wc_id:
                continue
        meta = data.get('meta') if isinstance(data.get('meta'), dict) else {}
        if meta or data.get('order_notes'):
            if meta.get('_cfs_registered_email') or meta.get('_cfs_director_name'):
                return data
            if fallback is None:
                fallback = data
    return fallback


def load_order_checkout_form(order_row):
    if not order_row:
        return []
    order_id = order_row.get('id')
    stored = order_row.get('checkout_form_json')
    if stored:
        try:
            parsed = json.loads(stored)
            if isinstance(parsed, list) and parsed:
                coalesced = normalize_checkout_form_fields(parsed)
                if coalesced != parsed and order_id:
                    execute_db(
                        "UPDATE orders SET checkout_form_json = ? WHERE id = ?;",
                        (json.dumps(coalesced), order_id),
                    )
                if order_id:
                    upsert_company_owner_from_fields(order_id, order_row.get('company_id'), coalesced)
                return _checkout_fields_with_live_company_name(order_row, coalesced)
        except (TypeError, ValueError):
            pass
    payload = webhook_payload_for_order(order_row.get('order_number'), order_row.get('woocommerce_order_id'))
    if payload:
        fields = persist_order_checkout_form(order_id, payload)
        if fields:
            return _checkout_fields_with_live_company_name(order_row, fields)
    wc_order_id = str(order_row.get('woocommerce_order_id') or '').strip()
    if wc_order_id:
        wp_data = fetch_wordpress_order_payload(wc_order_id)
        if wp_data:
            fields = persist_order_checkout_form(order_id, wp_data)
            if fields:
                return _checkout_fields_with_live_company_name(order_row, fields)
    return _checkout_fields_with_live_company_name(order_row, [])


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


def invoice_prefix_value():
    row = query_db("SELECT value FROM settings WHERE key = 'invoice_prefix';", one=True)
    prefix = str((row or {}).get('value') or 'INV-').strip()
    return prefix or 'INV-'


def next_invoice_number():
    prefix = invoice_prefix_value()
    highest = 0
    for row in query_db("SELECT invoice_number FROM invoices;") or []:
        number = str(row.get('invoice_number') or '').strip()
        tail = number[len(prefix):] if number.startswith(prefix) else ''
        match = re.search(r'(\d+)$', number)
        digits = tail if tail.isdigit() else (match.group(1) if match else '')
        if str(digits).isdigit():
            highest = max(highest, int(digits))
    return f"{prefix}{highest + 1:04d}"


def invoice_status_for_order(order):
    status = str((order or {}).get('status') or '')
    if status in ('Cancelled', 'Refunded'):
        return 'Cancelled'
    return 'Pending'


def invoice_status_value(raw):
    text = ' '.join(str(raw or '').strip().lower().replace('_', ' ').replace('-', ' ').split())
    if text in ('partial paid', 'part paid', 'partial', 'partpaid'):
        return 'Partial Paid'
    if text == 'paid':
        return 'Paid'
    if text == 'pending':
        return 'Pending'
    if text == 'overdue':
        return 'Overdue'
    if text == 'cancelled':
        return 'Cancelled'
    return ''


def invoice_timing_value(raw):
    text = str(raw or '').strip().lower()
    if text in ('deposit', 'part', 'partial', 'part paid', '50%', 'advance deposit'):
        return 'Deposit'
    if text in ('advance', 'upfront', 'full', 'full advance', 'pay first'):
        return 'Advance'
    if text in ('after work', 'on completion', 'completion', 'after'):
        return 'After work'
    return ''


def invoice_timing_for_order(order, paid=False):
    method = str((order or {}).get('payment_mode') or '').strip()
    if method == 'Website Charge' or paid:
        return 'Advance'
    return 'After work'


def ensure_invoice_for_order(order_id, paid=False):
    order_id = optional_record_id(order_id)
    if not order_id:
        return None
    order = query_db("SELECT * FROM orders WHERE id = ?;", (order_id,), one=True)
    if not order:
        return None
    existing = query_db("SELECT id, status, paid_at, payment_timing, total FROM invoices WHERE order_id = ? ORDER BY id DESC LIMIT 1;", (order_id,), one=True)
    if existing:
        if paid and existing.get('status') != 'Paid':
            timing = invoice_timing_value(existing.get('payment_timing')) or invoice_timing_for_order(order, paid=True)
            execute_db(
                """
                UPDATE invoices
                SET status = 'Paid', paid_at = CURRENT_TIMESTAMP, payment_timing = ?, amount_paid = ?
                WHERE id = ?;
                """,
                (timing, float(existing.get('total') or 0), existing['id']),
            )
        return existing['id']
    status = 'Paid' if paid else invoice_status_for_order(order)
    due_date = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    paid_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') if status == 'Paid' else None
    method = str(order.get('payment_mode') or '').strip() or 'Website Charge'
    timing = invoice_timing_for_order(order, paid=paid)
    amount = float(order.get('price') or 0)
    tax = float(order.get('vat') or 0)
    total = float(order.get('total') or 0)
    amount_paid = total if status == 'Paid' else 0
    for _ in range(8):
        try:
            return execute_db(
                """
                INSERT INTO invoices (
                    invoice_number, order_id, user_id, amount, tax, total, status, due_date, paid_at,
                    payment_method, payment_timing, deposit_amount, amount_paid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    next_invoice_number(),
                    order['id'],
                    order['user_id'],
                    amount,
                    tax,
                    total,
                    status,
                    due_date,
                    paid_at,
                    method,
                    timing,
                    0,
                    amount_paid,
                ),
            )
        except Exception as exc:
            if 'UNIQUE' not in str(exc).upper() and 'unique' not in str(exc).lower():
                raise
    return None


def backfill_invoices_from_orders():
    rows = query_db(
        """
        SELECT o.id
        FROM orders o
        LEFT JOIN invoices i ON i.order_id = o.id
        WHERE i.id IS NULL
        ORDER BY o.id;
        """
    ) or []
    created = 0
    for row in rows:
        if ensure_invoice_for_order(row['id']):
            created += 1
    return created


def invoice_row_for_staff(invoice_id):
    row = query_db(
        """
        SELECT i.id, i.invoice_number, i.order_id, i.user_id, i.amount, i.tax, i.total,
               i.status, i.due_date, i.paid_at, i.payment_method, i.payment_timing,
               i.deposit_amount, i.amount_paid, i.created_at,
               o.order_number, o.service_name, o.company_id, o.owner_name, o.owner_form_email,
               o.checkout_form_json, c.name as company_name, c.director as company_director,
               c.company_number, c.reg_office as company_address,
               u.country as client_country, u.address as client_address,
               co.address as owner_address, co.nationality as owner_nationality,
               co.passport_cnic as owner_cnic
        FROM invoices i
        LEFT JOIN orders o ON i.order_id = o.id
        LEFT JOIN companies c ON o.company_id = c.id
        LEFT JOIN users u ON i.user_id = u.id
        LEFT JOIN company_owners co ON co.order_id = o.id
        WHERE i.id = ?;
        """,
        (invoice_id,),
        one=True,
    )
    if not row:
        return None
    pakistan = invoice_client_is_in_pakistan(row)
    out = enrich_invoice_row(apply_connector_display(row))
    if out is not None:
        out['client_in_pakistan'] = pakistan
    return out


def html_document_response(start_response, html, status="200 OK"):
    body = (html or '').encode('utf-8')
    start_response(status, [
        ('Content-Type', 'text/html; charset=utf-8'),
        ('Content-Length', str(len(body))),
        ('Cache-Control', 'private, no-store'),
        ('X-Content-Type-Options', 'nosniff'),
    ])
    return [body]


def invoice_document_secret():
    return (
        os.environ.get('INVOICE_DOCUMENT_SECRET')
        or incorporation_download_secret()
        or wordpress_integration_secret()
        or 'brixen-invoice-document'
    )


def sign_invoice_document(invoice_id, expires):
    payload = f"invoice|{int(invoice_id)}|{int(expires)}"
    return hmac.new(str(invoice_document_secret()).encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()


def verify_invoice_document_link(invoice_id, expires, signature):
    try:
        invoice_id = int(invoice_id)
        expires = int(expires)
    except (TypeError, ValueError):
        return False
    expected = sign_invoice_document(invoice_id, expires)
    if not expected or not signature:
        return False
    if not hmac.compare_digest(str(signature).strip(), expected):
        return False
    return expires >= int(datetime.datetime.now().timestamp())


def invoice_download_filename(invoice_id, invoice_number=None):
    number = str(invoice_number or '').strip()
    if not number:
        try:
            row = query_db(
                "SELECT invoice_number FROM invoices WHERE id = ?;",
                (int(invoice_id),),
                one=True,
            )
        except (TypeError, ValueError):
            row = None
        number = str((row or {}).get('invoice_number') or f'INV-{invoice_id}').strip() or f'INV-{invoice_id}'
    return f"Invoice {number}.pdf"


def invoice_public_document_url(invoice_id, days=90):
    expires = int(datetime.datetime.now().timestamp()) + (max(int(days), 1) * 24 * 60 * 60)
    signature = sign_invoice_document(invoice_id, expires)
    # Filename MUST be the last path segment so iOS Safari / Mail use
    # "Invoice INV-0003.pdf" when Content-Disposition is ignored.
    # Path-only signing avoids email clients breaking & into &amp;.
    filename = urllib.parse.quote(invoice_download_filename(invoice_id))
    return (
        f"{portal_base_url()}/invoice/{int(invoice_id)}/"
        f"{int(expires)}/{signature}/{filename}"
    )


def invoice_currency_code(payment_method):
    return 'GBP'


def invoice_bank_details(payment_method):
    return dict(INVOICE_BANK_GBP)


_PAKISTAN_PLACE_RE = re.compile(
    r'\b(pakistan|pakistani|islamabad|lahore|karachi|rawalpindi|faisalabad|peshawar|'
    r'multan|quetta|sialkot|gujranwala|hyderabad|punjab|sindh|balochistan|\bpk\b)\b',
    re.I,
)
_GBP_PKR_RATE_CACHE = {'rate': None, 'fetched_at': 0}


def looks_like_pakistan(value):
    text = str(value or '').strip()
    if not text:
        return False
    if text.lower() in ('pk', 'pak', 'pakistan', 'islamic republic of pakistan'):
        return True
    if _PAKISTAN_PLACE_RE.search(text):
        return True
    digits = re.sub(r'\D', '', text)
    return len(digits) == 13


def invoice_client_is_in_pakistan(invoice):
    if str((invoice or {}).get('payment_method') or '').startswith('PKR'):
        return True
    blobs = [
        (invoice or {}).get('client_country'),
        (invoice or {}).get('client_address'),
        (invoice or {}).get('owner_address'),
        (invoice or {}).get('owner_nationality'),
        (invoice or {}).get('owner_cnic'),
    ]
    for field in parse_order_checkout_fields(invoice):
        label = str(field.get('label') or '').lower()
        if 'registered' in label:
            continue
        key = checkout_field_key(field.get('label'))
        if (
            key in ('country', 'issuance country', 'director address', 'director home address', 'address', 'nationality', 'passport cnic')
            or 'home' in key
            or key.endswith('country')
        ):
            blobs.append(field.get('value'))
    return any(looks_like_pakistan(item) for item in blobs)


def gbp_to_pkr_rate():
    if os.environ.get('CRM_TESTING'):
        try:
            return float(os.environ.get('CRM_TEST_GBP_PKR') or 370)
        except (TypeError, ValueError):
            return 370.0
    now = datetime.datetime.now().timestamp()
    cached = _GBP_PKR_RATE_CACHE.get('rate')
    if cached and now - float(_GBP_PKR_RATE_CACHE.get('fetched_at') or 0) < 3600:
        return cached
    rate = None
    for url, pick in (
        ('https://open.er-api.com/v6/latest/GBP', lambda data: (data.get('rates') or {}).get('PKR')),
        ('https://api.exchangerate.host/latest?base=GBP&symbols=PKR', lambda data: (data.get('rates') or {}).get('PKR')),
    ):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'BrixenConsultantsCRM/1.0'})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            candidate = float(pick(data) or 0)
            if candidate > 50:
                rate = candidate
                break
        except (urllib.error.URLError, TimeoutError, ValueError, TypeError, OSError, json.JSONDecodeError):
            continue
    if rate:
        _GBP_PKR_RATE_CACHE['rate'] = round(float(rate), 4)
        _GBP_PKR_RATE_CACHE['fetched_at'] = now
        return _GBP_PKR_RATE_CACHE['rate']
    return cached


def format_pkr_money(amount):
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        value = 0.0
    return f"Rs {value:,.2f}"


def invoice_pakistan_fx_note(pounds):
    rate = gbp_to_pkr_rate()
    try:
        gbp_amount = float(pounds or 0)
    except (TypeError, ValueError):
        gbp_amount = 0.0
    if not rate or gbp_amount <= 0:
        return None
    rupees = round(gbp_amount * float(rate), 2)
    return {
        'gbp_display': format_invoice_money(gbp_amount, 'GBP'),
        'rate_display': f"{float(rate):,.2f}",
        'pkr_display': format_pkr_money(rupees),
        'math': f"{format_invoice_money(gbp_amount, 'GBP')} × {float(rate):,.2f} = {format_pkr_money(rupees)}",
    }


def parse_invoice_money(raw):
    try:
        return round(float(str(raw).replace('£', '').replace('Rs', '').replace(',', '').strip()), 2)
    except (TypeError, ValueError, AttributeError):
        return None


def default_deposit_amount(total):
    try:
        value = float(total or 0)
    except (TypeError, ValueError):
        value = 0.0
    return round(value * 0.5, 2)


def invoice_money_state(invoice):
    invoice = invoice or {}
    currency = 'GBP'
    try:
        total = float(invoice.get('total') or 0)
    except (TypeError, ValueError):
        total = 0.0
    try:
        paid = float(invoice.get('amount_paid') or 0)
    except (TypeError, ValueError):
        paid = 0.0
    try:
        deposit = float(invoice.get('deposit_amount') or 0)
    except (TypeError, ValueError):
        deposit = 0.0
    status = str(invoice.get('status') or 'Pending')
    if status == 'Paid':
        paid = total
    paid = max(0.0, round(paid, 2))
    due = max(0.0, round(total - paid, 2))
    timing = invoice_timing_value(invoice.get('payment_timing')) or 'After work'
    display = status
    if status == 'Partial Paid' or (
        status not in ('Paid', 'Cancelled', 'Overdue') and paid > 0.004 and due > 0.004
    ):
        display = 'Partial Paid'
    return {
        'currency': currency,
        'total': round(total, 2),
        'amount_paid': paid,
        'amount_due': due,
        'deposit_amount': max(0.0, round(deposit, 2)),
        'timing': timing,
        'status': status,
        'display_status': display,
        'fully_paid': due <= 0.004 and total > 0 and status == 'Paid',
    }


def enrich_invoice_row(row):
    if not row:
        return None
    out = dict(row)
    money = invoice_money_state(out)
    out.update(money)
    return out


def format_invoice_money(amount, currency='GBP'):
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        value = 0.0
    return f"£{value:,.2f}"


def format_invoice_uk_date(raw):
    text = str(raw or '').strip()
    if not text:
        return datetime.date.today().strftime('%d/%m/%Y')
    for candidate, fmt in (
        (text[:19], '%Y-%m-%d %H:%M:%S'),
        (text[:10], '%Y-%m-%d'),
        (text[:10], '%d/%m/%Y'),
    ):
        try:
            return datetime.datetime.strptime(candidate, fmt).strftime('%d/%m/%Y')
        except ValueError:
            continue
    return text


def invoice_phone_display(brand=None):
    brand = brand or brand_settings()
    digits = re.sub(r'\D', '', str((brand or {}).get('support_phone') or '447360515317')) or '447360515317'
    return f"+{digits}" if not digits.startswith('+') else digits


def invoice_line_items_for_document(invoice):
    order_id = optional_record_id((invoice or {}).get('order_id'))
    rows = []
    if order_id:
        rows = query_db(
            """
            SELECT product_name, quantity, unit_price, line_total
            FROM order_line_items
            WHERE order_id = ?
            ORDER BY sort_order ASC, id ASC;
            """,
            (order_id,),
        ) or []
    items = []
    for row in rows:
        name = str(row.get('product_name') or '').strip()
        if not name:
            continue
        qty = int(row.get('quantity') or 1) or 1
        amount = row.get('line_total')
        if amount is None and row.get('unit_price') is not None:
            amount = float(row.get('unit_price') or 0) * qty
        items.append({
            'description': name,
            'quantity': qty,
            'unit_price': row.get('unit_price'),
            'amount': amount,
        })
    if not items:
        fallback_amount = (invoice or {}).get('total') or (invoice or {}).get('amount') or 0
        items.append({
            'description': str((invoice or {}).get('service_name') or 'Professional services').strip() or 'Professional services',
            'quantity': 1,
            'unit_price': fallback_amount,
            'amount': fallback_amount,
        })
    return items


def invoice_document_payload(invoice_id):
    invoice = invoice_row_for_staff(invoice_id)
    if not invoice:
        return None
    brand = brand_settings()
    money = invoice_money_state(invoice)
    currency = 'GBP'
    fx_pounds = money['amount_due'] if money['amount_due'] > 0.004 else money['total']
    pakistan = bool(invoice.get('client_in_pakistan')) or invoice_client_is_in_pakistan(invoice)
    return {
        'invoice': invoice,
        'brand': brand,
        'bank': invoice_bank_details(invoice.get('payment_method')),
        'logo_url': wordmark_image_src(),
        'owner_name': invoice.get('owner_name') or '',
        'owner_email': invoice.get('owner_form_email') or '',
        'company_name': invoice.get('company_name') or '',
        'company_address': invoice.get('company_address') or '',
        'line_items': invoice_line_items_for_document(invoice),
        'currency': currency,
        'total_display': format_invoice_money(money['total'], currency),
        'paid_display': format_invoice_money(money['amount_paid'], currency),
        'amount_due_display': format_invoice_money(money['amount_due'], currency),
        'deposit_display': format_invoice_money(money['deposit_amount'], currency) if money['timing'] == 'Deposit' and money['deposit_amount'] else '',
        'timing': money['timing'],
        'invoice_date': format_invoice_uk_date(invoice.get('created_at')),
        'paid': money['amount_due'] <= 0.004 and money['status'] == 'Paid',
        'part_paid': money['display_status'] == 'Partial Paid',
        'pakistan_fx': invoice_pakistan_fx_note(fx_pounds) if pakistan else None,
    }


def render_invoice_document_html(payload):
    brand = payload['brand']
    invoice = payload['invoice']
    bank = payload['bank']
    navy = '#003971'
    muted = '#6b7280'
    rule = '#d4d4d8'
    ink = '#1f2937'
    legal_name = html_escape((brand.get('legal_name') or 'Brixen Consultants Ltd').upper())
    trading_name = html_escape((brand.get('company_name') or 'Brixen Consultants').upper())
    office = html_escape(brand.get('registered_office') or '')
    email = html_escape(brand.get('support_email') or 'contact@brixenconsultants.com')
    phone = html_escape(invoice_phone_display(brand))
    company_no = html_escape(brand.get('company_number') or '')
    ico = html_escape(brand.get('ico_number') or '')
    invoice_number = html_escape(invoice.get('invoice_number') or '')
    title = html_escape(f"Invoice {invoice.get('invoice_number') or ''}".strip())
    to_lines = []
    for value in (payload.get('owner_name'), payload.get('company_name'), payload.get('company_address'), payload.get('owner_email')):
        text = str(value or '').strip()
        if text and text not in to_lines:
            to_lines.append(text)
    to_html = '<br>'.join(html_escape(line) for line in to_lines) or '—'
    item_rows = []
    for item in payload.get('line_items') or []:
        item_rows.append(
            '<tr>'
            f'<td>{html_escape(item.get("description") or "")}</td>'
            f'<td class="qty">{html_escape(str(item.get("quantity") or 1))}</td>'
            f'<td class="amt">{html_escape(format_invoice_money(item.get("amount"), "GBP"))}</td>'
            '</tr>'
        )
    if payload.get('paid'):
        paid_mark = '<span class="paid">Paid</span>'
    elif payload.get('part_paid'):
        paid_mark = (
            '<span class="paid">Deposit received</span>'
            if payload.get('timing') == 'Deposit'
            else '<span class="paid">Partial Paid</span>'
        )
    else:
        paid_mark = ''
    extra_totals = ''
    if payload.get('timing') == 'Deposit' and payload.get('deposit_display'):
        extra_totals += (
            f'<tr><td class="k">Deposit now</td><td class="v">{html_escape(payload["deposit_display"])}</td></tr>'
        )
    if float((payload.get('invoice') or {}).get('amount_paid') or 0) > 0.004 or payload.get('paid'):
        extra_totals += (
            f'<tr><td class="k">Paid so far</td><td class="v">{html_escape(payload["paid_display"])}</td></tr>'
        )
    extra_totals += (
        f'<tr><td class="k">Amount due</td><td class="v">{html_escape(payload["amount_due_display"])}</td></tr>'
    )
    logo_url = html_escape(payload.get('logo_url') or wordmark_image_src())
    gbp = INVOICE_BANK_GBP
    pkr = INVOICE_BANK_PKR
    pay_html = (
        '<h3 class="pay-head">GBP</h3>'
        '<table class="pay" role="presentation"><tr>'
        f'<td><div class="k">Account title</div><div class="v">{html_escape(gbp["account_name"])}</div></td>'
        f'<td><div class="k">Account number</div><div class="v">{html_escape(gbp["account_number"])}</div></td>'
        f'<td><div class="k">Sort code</div><div class="v">{html_escape(gbp["sort_code"])}</div></td>'
        '</tr></table>'
        f'<div class="pay-link"><a href="{html_escape(gbp["pay_url"])}">Pay now in £</a></div>'
        '<h3 class="pay-head">PKR</h3>'
        '<table class="pay" role="presentation"><tr>'
        f'<td><div class="k">Account title</div><div class="v">{html_escape(pkr["account_name"])}</div></td>'
        f'<td><div class="k">Bank name</div><div class="v">{html_escape(pkr["bank_name"])}</div></td>'
        f'<td><div class="k">IBAN</div><div class="v">{html_escape(pkr["iban"])}</div></td>'
        '</tr></table>'
    )
    fx = payload.get('pakistan_fx') or {}
    if fx.get('math'):
        pay_html += (
            '<div class="fx">'
            f'<p class="fx-title">If you pay from Pakistan</p>'
            f'<p class="fx-math">{html_escape(fx["math"])}</p>'
            f'<p class="fx-copy">Send <strong>{html_escape(fx["pkr_display"])}</strong> to the UBL account. '
            f'That is {html_escape(fx["gbp_display"])} at today\'s GBP to PKR rate.</p>'
            '</div>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  {email_favicon_html()}
  <style>
    @page {{ size: A4; margin: 14mm 14mm 16mm; }}
    html, body {{ margin: 0; padding: 0; background: #efeae2; color: {ink};
      font-family: Arial, Helvetica, sans-serif; }}
    .toolbar {{ max-width: 210mm; margin: 16px auto 0; text-align: right; }}
    .toolbar button {{ background: {navy}; color: #fff; border: 0; border-radius: 980px;
      padding: 8px 16px; font: 600 13px/1.2 Arial, Helvetica, sans-serif; cursor: pointer; }}
    .sheet {{ width: 210mm; min-height: 297mm; margin: 12px auto 24px; background: #fff;
      padding: 18mm 16mm 20mm; box-sizing: border-box; position: relative; }}
    .head {{ width: 100%; border-collapse: collapse; }}
    .head td {{ vertical-align: top; }}
    .brand {{ width: 42%; padding-right: 12px; }}
    .legal {{ width: 58%; text-align: right; font-size: 11px; line-height: 1.45; color: {ink}; }}
    .logo {{ display: block; width: 168px; max-width: 100%; height: auto; }}
    .label {{ font-size: 11px; font-weight: 700; color: {ink}; margin: 0 0 2px; }}
    .value {{ font-size: 11px; color: {ink}; }}
    hr {{ border: 0; border-top: 1px solid {rule}; margin: 18px 0; }}
    h1 {{ margin: 0 0 18px; font-size: 28px; font-weight: 800; letter-spacing: 0.04em; color: {ink}; }}
    .meta {{ width: 100%; border-collapse: collapse; }}
    .meta td {{ width: 25%; vertical-align: top; padding-right: 14px; }}
    .meta .k {{ font-size: 11px; font-weight: 700; color: {ink}; margin-bottom: 6px; }}
    .meta .v {{ font-size: 13px; color: {ink}; line-height: 1.4; }}
    table.items {{ width: 100%; border-collapse: collapse; margin-top: 28px; }}
    table.items th {{ text-align: left; font-size: 11px; font-weight: 600; color: {muted};
      padding: 0 0 8px; border-bottom: 1px solid {rule}; }}
    table.items th.qty, table.items td.qty, table.items th.amt, table.items td.amt {{ text-align: right; }}
    table.items td {{ padding: 14px 0; font-size: 13px; border-bottom: 1px solid {rule}; vertical-align: top; }}
    .totals {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
    .totals td {{ padding-top: 14px; font-size: 13px; }}
    .totals .k {{ font-weight: 700; text-align: right; width: 80%; }}
    .totals .v {{ font-weight: 700; text-align: right; }}
    .paid {{ display: inline-block; margin-left: 8px; color: #15803d; font-size: 12px; font-weight: 700; }}
    h2 {{ margin: 28px 0 16px; font-size: 22px; font-weight: 800; letter-spacing: 0.04em; color: {ink}; }}
    .pay-head {{ margin: 18px 0 10px; font-size: 13px; font-weight: 800; letter-spacing: 0.06em; color: {ink}; }}
    .fx {{ margin-top: 16px; }}
    .fx-title {{ margin: 0 0 6px; font-size: 13px; font-weight: 800; color: {ink}; }}
    .fx-math {{ margin: 0 0 6px; font-size: 15px; font-weight: 700; color: {ink}; }}
    .fx-copy {{ margin: 0; font-size: 12px; line-height: 1.45; color: {ink}; }}
    .pay {{ width: 100%; border-collapse: collapse; }}
    .pay td {{ width: 33%; vertical-align: top; padding-right: 14px; }}
    .pay .k {{ font-size: 11px; font-weight: 700; margin-bottom: 6px; }}
    .pay .v {{ font-size: 13px; }}
    .pay-link {{ margin-top: 16px; }}
    .pay-link a {{ display: inline-block; background: {navy}; color: #fff; text-decoration: none;
      font-size: 13px; font-weight: 600; letter-spacing: -0.022em; padding: 7px 14px; border-radius: 980px; }}
    .pg {{ position: absolute; right: 16mm; bottom: 10mm; font-size: 11px; color: {muted}; }}
    @media print {{
      html, body {{ background: #fff; }}
      .toolbar {{ display: none !important; }}
      .sheet {{ margin: 0; min-height: auto; box-shadow: none; }}
    }}
  </style>
</head>
<body>
  <div class="toolbar no-print"><button type="button" onclick="window.print()">Print / Save PDF</button></div>
  <article class="sheet">
    <table class="head" role="presentation">
      <tr>
        <td class="brand">
          <img class="logo" src="{logo_url}" alt="Brixen Consultants">
        </td>
        <td class="legal">
          <div class="label">Registered Business name</div>
          <div class="value">{legal_name}</div>
          <div class="value">{office}</div>
          <div class="value">{email}</div>
          <div class="value">{phone}</div>
          <div class="value">Company No - {company_no}</div>
          <div class="value">ICO Reg No - {ico}</div>
        </td>
      </tr>
    </table>
    <hr>
    <h1>INVOICE</h1>
    <table class="meta" role="presentation">
      <tr>
        <td><div class="k">Reference</div><div class="v">{invoice_number}</div></td>
        <td><div class="k">Amount due</div><div class="v">{html_escape(payload['amount_due_display'])}{paid_mark}</div></td>
        <td><div class="k">Invoice Date</div><div class="v">{html_escape(payload['invoice_date'])}</div></td>
        <td><div class="k">To</div><div class="v">{to_html}</div></td>
      </tr>
    </table>
    <table class="items">
      <thead>
        <tr><th>Description</th><th class="qty">Qty</th><th class="amt">Amount</th></tr>
      </thead>
      <tbody>
        {''.join(item_rows)}
      </tbody>
    </table>
    <table class="totals" role="presentation">
      <tr>
        <td class="k">Total</td>
        <td class="v">{html_escape(payload['total_display'])}</td>
      </tr>
      {extra_totals}
    </table>
    <hr>
    <h2>PAYMENT METHODS</h2>
    {pay_html}
    <div class="pg">Page 1 of 1</div>
  </article>
</body>
</html>"""


def pdf_literal(text):
    raw = str(text or '')
    encoded = raw.encode('latin-1', 'replace').decode('latin-1')
    return encoded.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def pdf_simple_document(lines, *, page_width=595.28, page_height=841.89, margin=48):
    """Build a minimal single-page PDF (Helvetica) with no third-party deps."""
    content = ['BT', '/F1 11 Tf', f'1 0 0 1 {margin:.2f} {page_height - margin:.2f} Tm', '16 TL']
    for line in lines or []:
        kind = 'text'
        text = line
        size = 11
        if isinstance(line, dict):
            kind = str(line.get('kind') or 'text')
            text = line.get('text') or ''
            size = float(line.get('size') or 11)
        if kind == 'gap':
            content.append(f'0 -{float(text or 10):.2f} Td')
            continue
        if kind == 'bold':
            content.append(f'/F2 {size:.2f} Tf')
        else:
            content.append(f'/F1 {size:.2f} Tf')
        content.append(f'({pdf_literal(text)}) Tj')
        content.append('T*')
    content.append('ET')
    stream = '\n'.join(content).encode('latin-1', 'replace')
    objects = []
    objects.append(b'1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n')
    objects.append(b'2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n')
    objects.append(
        (
            f'3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width:.2f} {page_height:.2f}] '
            f'/Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>endobj\n'
        ).encode('latin-1')
    )
    objects.append(f'4 0 obj<< /Length {len(stream)} >>stream\n'.encode('latin-1') + stream + b'\nendstream\nendobj\n')
    objects.append(b'5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>endobj\n')
    objects.append(b'6 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>endobj\n')
    out = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref = len(out)
    out.extend(f'xref\n0 {len(offsets)}\n'.encode('latin-1'))
    out.extend(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        out.extend(f'{offset:010d} 00000 n \n'.encode('latin-1'))
    out.extend(
        f'trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode('latin-1')
    )
    return bytes(out)


def render_invoice_document_pdf(payload):
    """Professional Tide-style invoice PDF. Prefers fpdf2; falls back to simple PDF."""
    try:
        from fpdf import FPDF
    except Exception:
        return _render_invoice_document_pdf_simple(payload)

    brand = (payload or {}).get('brand') or {}
    invoice = (payload or {}).get('invoice') or {}
    gbp = INVOICE_BANK_GBP
    pkr = INVOICE_BANK_PKR
    navy = (0, 57, 113)
    ink = (31, 41, 55)
    muted = (107, 114, 128)
    rule = (212, 212, 216)

    class InvoicePDF(FPDF):
        def footer(self):
            self.set_y(-14)
            self.set_font('Helvetica', '', 9)
            self.set_text_color(*muted)
            self.cell(0, 8, 'Page 1 of 1', align='R')

    pdf = InvoicePDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(16, 16, 16)

    logo_path = wordmark_image_path()
    if not os.path.exists(logo_path):
        logo_path = os.path.join(STATIC_DIR, 'img', 'brixen-logo.png')
    left = pdf.get_x()
    top = pdf.get_y()
    if os.path.exists(logo_path):
        try:
            pdf.image(logo_path, x=left, y=top, w=48)
        except Exception:
            pass

    def money(value):
        return str(value or '').replace('£', 'GBP ')

    # Logo left, company details right (former trading-name column) — no trading name.
    pdf.set_xy(left + 100, top)
    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(*ink)
    pdf.multi_cell(
        78,
        4.2,
        'Registered Business name\n' + (brand.get('legal_name') or 'Brixen Consultants Ltd').upper(),
        align='R',
    )
    pdf.set_font('Helvetica', '', 9)
    legal_block = '\n'.join([
        str(brand.get('registered_office') or ''),
        str(brand.get('support_email') or 'contact@brixenconsultants.com'),
        invoice_phone_display(brand),
        f"Company No - {brand.get('company_number') or ''}",
        f"ICO Reg No - {brand.get('ico_number') or ''}",
    ])
    pdf.set_x(left + 100)
    pdf.multi_cell(78, 4.2, legal_block, align='R')

    pdf.set_y(max(pdf.get_y(), top + 36) + 4)
    pdf.set_draw_color(*rule)
    pdf.line(16, pdf.get_y(), 194, pdf.get_y())
    pdf.ln(6)

    pdf.set_font('Helvetica', 'B', 22)
    pdf.set_text_color(*ink)
    pdf.cell(0, 10, 'INVOICE', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)

    meta = [
        ('Reference', str(invoice.get('invoice_number') or '')),
        ('Amount due', money(payload.get('amount_due_display') or '')),
        ('Invoice Date', str(payload.get('invoice_date') or '')),
        ('To', ''),
    ]
    col_w = 44.5
    y0 = pdf.get_y()
    to_bottom = y0
    for index, (label, value) in enumerate(meta):
        x = 16 + index * col_w
        pdf.set_xy(x, y0)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(col_w - 2, 5, label)
        pdf.set_xy(x, y0 + 5)
        pdf.set_font('Helvetica', '', 10)
        if label == 'To':
            to_lines = []
            for field in (
                payload.get('owner_name'),
                payload.get('company_name'),
                payload.get('company_address'),
                payload.get('owner_email'),
            ):
                text = str(field or '').strip()
                if text and text not in to_lines:
                    to_lines.append(text)
            pdf.multi_cell(col_w - 2, 4.5, '\n'.join(to_lines) or '-')
            to_bottom = max(to_bottom, pdf.get_y())
        else:
            mark = ''
            if label == 'Amount due' and payload.get('paid'):
                mark = '  Paid'
            pdf.multi_cell(col_w - 2, 4.5, f"{value}{mark}")
            to_bottom = max(to_bottom, pdf.get_y())

    pdf.set_y(max(to_bottom, y0 + 28) + 6)
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(*muted)
    pdf.cell(110, 7, 'Description', border='B')
    pdf.cell(30, 7, 'Qty', border='B', align='R')
    pdf.cell(38, 7, 'Amount', border='B', align='R', ln=1)
    pdf.set_text_color(*ink)
    pdf.set_font('Helvetica', '', 11)
    for item in payload.get('line_items') or []:
        desc = str(item.get('description') or '').strip() or 'Professional services'
        qty = str(item.get('quantity') or 1)
        amount = money(format_invoice_money(item.get('amount'), 'GBP'))
        y = pdf.get_y()
        pdf.multi_cell(110, 8, desc, border='B')
        h = pdf.get_y() - y
        pdf.set_xy(16 + 110, y)
        pdf.cell(30, max(h, 8), qty, border='B', align='R')
        pdf.cell(38, max(h, 8), amount, border='B', align='R', new_x='LMARGIN', new_y='NEXT')

    pdf.ln(2)
    totals = [('Total', money(payload.get('total_display') or ''))]
    if payload.get('timing') == 'Deposit' and payload.get('deposit_display'):
        totals.append(('Deposit now', money(payload.get('deposit_display'))))
    if float(invoice.get('amount_paid') or 0) > 0.004 or payload.get('paid'):
        totals.append(('Paid so far', money(payload.get('paid_display') or '')))
    totals.append(('Amount due', money(payload.get('amount_due_display') or '')))
    for label, value in totals:
        pdf.set_font('Helvetica', 'B', 11)
        pdf.cell(140, 7, label, align='R')
        pdf.cell(38, 7, str(value or ''), align='R', new_x='LMARGIN', new_y='NEXT')

    pdf.ln(4)
    pdf.set_draw_color(*rule)
    pdf.line(16, pdf.get_y(), 194, pdf.get_y())
    pdf.ln(6)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 8, 'PAYMENT METHODS', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)

    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 6, 'GBP', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 10)
    pay_y = pdf.get_y()
    for index, (label, value) in enumerate((
        ('Account title', gbp['account_name']),
        ('Account number', gbp['account_number']),
        ('Sort code', gbp['sort_code']),
    )):
        x = 16 + index * 60
        pdf.set_xy(x, pay_y)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(58, 5, label, new_x='LMARGIN', new_y='NEXT')
        pdf.set_x(x)
        pdf.set_font('Helvetica', '', 10)
        pdf.multi_cell(58, 5, str(value))
    pdf.set_y(max(pdf.get_y(), pay_y + 14) + 4)
    # Apple-style pill: small navy capsule (padding ~7x14, fully rounded).
    pill_label = 'Pay now in GBP'
    pdf.set_font('Helvetica', 'B', 10)
    pill_w = pdf.get_string_width(pill_label) + 10  # ~14px horizontal padding
    pill_h = 7.5
    pill_x = 16
    pill_y = pdf.get_y()
    pdf.set_fill_color(*navy)
    try:
        pdf.rounded_rect(pill_x, pill_y, pill_w, pill_h, pill_h / 2, style='F')
    except Exception:
        pdf.rect(pill_x, pill_y, pill_w, pill_h, style='F')
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(pill_x, pill_y + 0.6)
    pdf.cell(pill_w, pill_h - 1.2, pill_label, align='C', link=gbp['pay_url'])
    pdf.set_text_color(*ink)
    pdf.set_y(pill_y + pill_h + 8)

    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 6, 'PKR', new_x='LMARGIN', new_y='NEXT')
    pay_y = pdf.get_y()
    for index, (label, value) in enumerate((
        ('Account title', pkr['account_name']),
        ('Bank name', pkr['bank_name']),
        ('IBAN', pkr['iban']),
    )):
        x = 16 + index * 60
        pdf.set_xy(x, pay_y)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(58, 5, label, new_x='LMARGIN', new_y='NEXT')
        pdf.set_x(x)
        pdf.set_font('Helvetica', '', 10)
        pdf.multi_cell(58, 5, str(value))
    pdf.set_y(max(pdf.get_y(), pay_y + 14) + 2)

    fx = payload.get('pakistan_fx') or {}
    if fx.get('math'):
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(0, 6, 'If you pay from Pakistan', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 6, money(fx.get('math') or ''), new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', '', 9)
        pdf.multi_cell(
            0,
            5,
            money(
                f"Send {fx.get('pkr_display') or ''} to the UBL account. "
                f"That is {fx.get('gbp_display') or ''} at today's GBP to PKR rate."
            ),
        )

    out = pdf.output()
    return bytes(out) if isinstance(out, (bytes, bytearray)) else bytes(out)


def _render_invoice_document_pdf_simple(payload):
    brand = (payload or {}).get('brand') or {}
    invoice = (payload or {}).get('invoice') or {}
    gbp = INVOICE_BANK_GBP
    pkr = INVOICE_BANK_PKR
    lines = [
        {'kind': 'bold', 'size': 16, 'text': (brand.get('legal_name') or 'Brixen Consultants Ltd')},
        str(brand.get('registered_office') or ''),
        str(brand.get('support_email') or 'contact@brixenconsultants.com'),
        invoice_phone_display(brand),
        f"Company No {brand.get('company_number') or ''}".strip(),
        f"ICO Reg No {brand.get('ico_number') or ''}".strip(),
        {'kind': 'gap', 'text': 14},
        {'kind': 'bold', 'size': 22, 'text': 'INVOICE'},
        {'kind': 'gap', 'text': 8},
        {'kind': 'bold', 'text': f"Reference  {invoice.get('invoice_number') or ''}"},
        f"Invoice date  {payload.get('invoice_date') or ''}",
        f"Amount due  {payload.get('amount_due_display') or ''}",
    ]
    if payload.get('paid'):
        lines.append('Status  Paid')
    elif payload.get('part_paid'):
        lines.append('Status  Partial Paid')
    lines.append({'kind': 'gap', 'text': 10})
    lines.append({'kind': 'bold', 'text': 'Bill to'})
    for value in (
        payload.get('owner_name'),
        payload.get('company_name'),
        payload.get('company_address'),
        payload.get('owner_email'),
    ):
        text = str(value or '').strip()
        if text:
            lines.append(text)
    lines.append({'kind': 'gap', 'text': 12})
    lines.append({'kind': 'bold', 'text': 'Description'})
    for item in payload.get('line_items') or []:
        desc = str(item.get('description') or '').strip() or 'Professional services'
        qty = item.get('quantity') or 1
        amount = format_invoice_money(item.get('amount'), 'GBP')
        lines.append(f"{desc}  x{qty}  {amount}")
    lines.append({'kind': 'gap', 'text': 10})
    lines.append({'kind': 'bold', 'text': f"Total  {payload.get('total_display') or ''}"})
    if payload.get('timing') == 'Deposit' and payload.get('deposit_display'):
        lines.append(f"Deposit now  {payload.get('deposit_display')}")
    if float(invoice.get('amount_paid') or 0) > 0.004 or payload.get('paid'):
        lines.append(f"Paid so far  {payload.get('paid_display') or ''}")
    lines.append(f"Amount due  {payload.get('amount_due_display') or ''}")
    lines.append({'kind': 'gap', 'text': 14})
    lines.append({'kind': 'bold', 'size': 14, 'text': 'Payment methods'})
    lines.append({'kind': 'gap', 'text': 6})
    lines.append({'kind': 'bold', 'text': 'GBP'})
    lines.append(f"Account name  {gbp['account_name']}")
    lines.append(f"Account number  {gbp['account_number']}")
    lines.append(f"Sort code  {gbp['sort_code']}")
    lines.append(f"Pay online  {gbp['pay_url']}")
    lines.append({'kind': 'gap', 'text': 8})
    lines.append({'kind': 'bold', 'text': 'PKR'})
    lines.append(f"Account name  {pkr['account_name']}")
    lines.append(f"Bank name  {pkr['bank_name']}")
    lines.append(f"IBAN  {pkr['iban']}")
    fx = payload.get('pakistan_fx') or {}
    if fx.get('math'):
        lines.append({'kind': 'gap', 'text': 8})
        lines.append({'kind': 'bold', 'text': 'If you pay from Pakistan'})
        lines.append(fx.get('math'))
        lines.append(f"Send {fx.get('pkr_display') or ''} to the UBL account.")
    lines.append({'kind': 'gap', 'text': 16})
    lines.append('Page 1 of 1')
    return pdf_simple_document([line for line in lines if line not in ('', None)])


def pdf_document_response(start_response, pdf_bytes, filename='invoice.pdf', status="200 OK", *, head_only=False):
    body = pdf_bytes or b''
    raw = str(filename or 'invoice.pdf').strip() or 'invoice.pdf'
    # Keep spaces for "Invoice INV-0003.pdf"; strip path/control chars only.
    safe = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]+', '', raw).strip(' .') or 'invoice.pdf'
    if not safe.lower().endswith('.pdf'):
        safe = f"{safe}.pdf"
    ascii_name = safe.encode('ascii', 'replace').decode('ascii') or 'invoice.pdf'
    quoted = urllib.parse.quote(safe)
    # filename* first so Safari/Chrome prefer the spaced name; plain filename as fallback.
    start_response(status, [
        ('Content-Type', 'application/pdf'),
        ('Content-Length', str(len(body))),
        (
            'Content-Disposition',
            f"attachment; filename*=UTF-8''{quoted}; filename=\"{ascii_name}\"",
        ),
        ('Cache-Control', 'private, no-store'),
        ('X-Content-Type-Options', 'nosniff'),
    ])
    return [] if head_only else [body]


def serve_invoice_document(start_response, invoice_id, *, head_only=False):
    payload = invoice_document_payload(invoice_id)
    if not payload:
        start_response("404 Not Found", [('Content-Type', 'text/plain; charset=utf-8')])
        return [b'Invoice not found']
    number = str(((payload.get('invoice') or {}).get('invoice_number') or f'INV-{invoice_id}')).strip()
    filename = invoice_download_filename(invoice_id, number)
    return pdf_document_response(
        start_response,
        render_invoice_document_pdf(payload),
        filename=filename,
        head_only=head_only,
    )


def update_admin_invoice(invoice_id, data, actor=None):
    invoice = query_db("SELECT * FROM invoices WHERE id = ?;", (invoice_id,), one=True)
    if not invoice:
        return None, 'Invoice not found', None
    data = data if isinstance(data, dict) else {}
    status = invoice.get('status')
    if 'status' in data:
        status = str(data.get('status') or '').strip()
        status = invoice_status_value(status) or status
        if status not in INVOICE_STATUSES:
            return None, 'Choose Paid, Partial Paid, Pending, Overdue, or Cancelled', None
    method = invoice.get('payment_method')
    if 'payment_method' in data:
        method = str(data.get('payment_method') or '').strip() or method
        if method and method not in ORDER_PAYMENT_MODES and method != 'Credit Card (Stripe)':
            return None, 'Invalid payment method', None
    previous_timing = invoice_timing_value(invoice.get('payment_timing')) or 'After work'
    timing = previous_timing
    if 'payment_timing' in data:
        timing = invoice_timing_value(data.get('payment_timing'))
        if timing not in INVOICE_PAYMENT_TIMINGS:
            return None, 'Choose After work, full advance, or a deposit', None
    due_date = invoice.get('due_date')
    if 'due_date' in data:
        raw_due = str(data.get('due_date') or '').strip()
        if raw_due:
            try:
                due_date = datetime.date.fromisoformat(raw_due[:10]).isoformat()
            except ValueError:
                return None, 'Enter a valid due date', None
    amount = float(invoice.get('amount') or 0)
    tax = float(invoice.get('tax') or 0)
    total = float(invoice.get('total') or 0)
    if 'total' in data or 'amount' in data:
        if not can_edit_order_price(actor):
            return None, 'Only an administrator can change invoice totals', None
        raw_amount = data.get('total') if 'total' in data else data.get('amount')
        parsed_total = parse_invoice_money(raw_amount)
        if parsed_total is None:
            return None, 'Enter a valid amount', None
        total = parsed_total
        if total < 0 or total > 100000:
            return None, 'Amount must be between £0 and £100,000', None
        if total > 0 and amount >= 0 and float(invoice.get('total') or 0) > 0:
            ratio = min(1.0, max(0.0, amount / float(invoice.get('total') or total)))
        else:
            ratio = 1.0
        amount = round(total * ratio, 2)
        tax = round(total - amount, 2)
    old_paid = invoice_money_state(invoice)['amount_paid']
    deposit_amount = float(invoice.get('deposit_amount') or 0)
    if 'deposit_amount' in data:
        parsed_deposit = parse_invoice_money(data.get('deposit_amount'))
        if parsed_deposit is None:
            return None, 'Enter a valid deposit amount', None
        if parsed_deposit < 0 or parsed_deposit > total:
            return None, 'Deposit must be between 0 and the invoice total', None
        deposit_amount = parsed_deposit
    if timing == 'Deposit':
        if deposit_amount <= 0.004:
            deposit_amount = default_deposit_amount(total)
        if deposit_amount + 0.004 >= total and total > 0:
            timing = 'Advance'
            deposit_amount = 0
    else:
        deposit_amount = 0
    amount_paid = old_paid
    if 'amount_paid' in data:
        parsed_paid = parse_invoice_money(data.get('amount_paid'))
        if parsed_paid is None:
            return None, 'Enter a valid amount received', None
        if parsed_paid < 0 or parsed_paid > total:
            return None, 'Amount received must be between 0 and the invoice total', None
        amount_paid = parsed_paid
    if status == 'Paid':
        amount_paid = total
    elif amount_paid + 0.004 >= total and total > 0:
        status = 'Paid'
        amount_paid = total
    elif status == 'Partial Paid' and amount_paid <= 0.004:
        if deposit_amount > 0.004:
            amount_paid = min(deposit_amount, total)
        elif old_paid > 0.004:
            amount_paid = old_paid
    elif status not in ('Cancelled', 'Overdue') and amount_paid > 0.004:
        status = 'Partial Paid'
    was_paid = str(invoice.get('status') or '') == 'Paid'
    paid_at = invoice.get('paid_at')
    if status == 'Paid' and not was_paid:
        paid_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    elif status != 'Paid':
        paid_at = None
    execute_db(
        """
        UPDATE invoices
        SET status = ?, payment_method = ?, payment_timing = ?, due_date = ?, amount = ?, tax = ?, total = ?,
            paid_at = ?, deposit_amount = ?, amount_paid = ?
        WHERE id = ?;
        """,
        (status, method, timing, due_date, amount, tax, total, paid_at, deposit_amount, amount_paid, invoice_id),
    )
    if method in ORDER_PAYMENT_MODES and invoice.get('order_id'):
        execute_db(
            "UPDATE orders SET payment_mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (method, invoice['order_id']),
        )
    mail = None
    received_more = amount_paid > old_paid + 0.004
    order = None
    if invoice.get('order_id') and (
        (received_more and status != 'Cancelled')
        or data.get('send_payment_details')
        or (
            timing in ('Advance', 'Deposit')
            and previous_timing == 'After work'
            and amount_paid + 0.004 < total
            and status != 'Cancelled'
        )
    ):
        order = query_db(
            """
            SELECT o.*, u.email, u.full_name
            FROM orders o
            JOIN users u ON o.user_id = u.id
            WHERE o.id = ?;
            """,
            (invoice.get('order_id'),),
            one=True,
        )
    if received_more and status != 'Cancelled':
        mail = notify_payment_received(order, invoice=invoice_row_for_staff(invoice_id))
    elif order and (
        data.get('send_payment_details')
        or (timing in ('Advance', 'Deposit') and previous_timing == 'After work' and amount_paid + 0.004 < total)
    ):
        mail = notify_payment_requested(order, invoice=invoice_row_for_staff(invoice_id))
    updated = invoice_row_for_staff(invoice_id)
    return updated, None, mail


def upsert_woocommerce_order(o_data):
    order_num = o_data.get('order_number') or f"#GB{int(datetime.datetime.now().timestamp())}"
    wp_user_id = str(o_data.get('wordpress_user_id') or '')
    email = (o_data.get('email') or '').strip()
    line_items = parse_line_items_from_payload(o_data)
    incoming_service = (o_data.get('service_name') or '').strip()
    if not incoming_service and line_items:
        incoming_service = ', '.join(item['product_name'] for item in line_items)
    service_name = incoming_service or 'Corporate Formation Service'
    price = normalize_retail_price(float(o_data.get('price', 150.00)))
    total = normalize_retail_price(float(o_data.get('total', price * 1.2)))
    vat = round(total - price, 2)
    status = o_data.get('status') or 'Processing'
    if status not in ('Pending', 'Processing', 'In Progress', 'Completed', 'Cancelled', 'Refunded', 'Pending Verification'):
        status = 'Processing'
    wc_order_id = str(o_data.get('woocommerce_order_id') or '') or None
    company_name = extract_order_company_name(o_data) or o_data.get('company_name') or o_data.get('billing_company')
    company_number = o_data.get('company_number') or o_data.get('registration_number')
    meta = o_data.get('meta') if isinstance(o_data.get('meta'), dict) else {}
    if not company_number:
        company_number = meta.get('_cfs_company_number') or meta.get('company_number')

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
        company_id = match_company_for_client_by_number(cid, company_number)
        if not company_id:
            company_id = match_company_for_client(cid, company_name)
        if not company_id:
            company_id = ensure_company_from_registration_order(
                cid, client_name, o_data, service_name, line_items, wc_order_id
            )
    service_id = match_service_id(line_items[0]['product_name'] if line_items else service_name)

    existing_ord = query_db("SELECT id, service_name, status FROM orders WHERE order_number = ?;", (order_num,), one=True)
    previous_status = (existing_ord or {}).get('status')
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
                portfolio_hidden = CASE WHEN ? IS NOT NULL THEN 0 ELSE portfolio_hidden END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
        """, (status, total, price, vat, service_name, company_id, service_id, wc_order_id, billing_phone or None, checkout_dob, company_id, existing_ord['id']))
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
    ensure_invoice_for_order(ord_id)
    if created is False:
        latest = query_db("SELECT id, price, vat, total FROM orders WHERE id = ?;", (ord_id,), one=True)
        if latest:
            execute_db(
                """
                UPDATE invoices
                SET amount = ?, tax = ?, total = ?
                WHERE order_id = ? AND status != 'Paid';
                """,
                (float(latest.get('price') or 0), float(latest.get('vat') or 0), float(latest.get('total') or 0), ord_id),
            )
    if not guest_pending and status == 'Completed' and previous_status != 'Completed':
        completed_order = query_db(
            """
            SELECT o.*, u.email, u.full_name, c.name as company_name
            FROM orders o
            JOIN users u ON o.user_id = u.id
            LEFT JOIN companies c ON o.company_id = c.id
            WHERE o.id = ?;
            """,
            (ord_id,),
            one=True,
        )
        notify_product_completed(completed_order)
    return {
        'order_id': ord_id,
        'order_number': order_num,
        'created': created,
        'guest': guest_pending,
        'attachments_imported': attachments_imported,
    }


def backfill_normalize_retail_prices():
    for row in query_db("SELECT id, price, vat, total FROM orders;"):
        price = float(row.get('price') or 0)
        total = float(row.get('total') or 0)
        new_price = normalize_retail_price(price)
        new_total = normalize_retail_price(total)
        if new_price == price and new_total == total:
            continue
        if new_total != total:
            vat = round(new_total - new_price, 2)
            final_total = new_total
        else:
            vat = round(total - new_price, 2)
            final_total = total
        execute_db(
            "UPDATE orders SET price = ?, vat = ?, total = ? WHERE id = ?;",
            (new_price, vat, final_total, row['id']),
        )
        execute_db(
            """
            UPDATE invoices
            SET amount = ?, tax = ?, total = ?
            WHERE order_id = ? AND status != 'Paid';
            """,
            (new_price, vat, final_total, row['id']),
        )

    for row in query_db("SELECT id, unit_price, line_total, quantity FROM order_line_items;"):
        unit = normalize_retail_price(row.get('unit_price'))
        line = normalize_retail_price(row.get('line_total'))
        if unit == float(row.get('unit_price') or 0) and line == float(row.get('line_total') or 0):
            continue
        qty = max(int(row.get('quantity') or 1), 1)
        if unit != float(row.get('unit_price') or 0) and line == float(row.get('line_total') or 0):
            line = round(unit * qty, 2)
        execute_db(
            "UPDATE order_line_items SET unit_price = ?, line_total = ? WHERE id = ?;",
            (unit, line, row['id']),
        )


ensure_schema()
try:
    backfill_normalize_retail_prices()
except Exception:
    pass
try:
    backfill_companies_from_registration_webhooks()
except Exception:
    pass
try:
    backfill_company_owners_from_orders()
except Exception:
    pass
try:
    heal_orders_hidden_after_duplicate_company_delete()
except Exception:
    pass
try:
    merge_pending_order_company_duplicates()
except Exception:
    pass
try:
    ensure_internal_staff_have_all_departments()
except Exception:
    pass
try:
    backfill_invoices_from_orders()
except Exception:
    pass

def application(environ, start_response):
    start_registration_notice_worker()
    try:
        import compliance_alerts as _compliance_alerts
        _compliance_alerts.ensure_compliance_alert_settings()
        _compliance_alerts.start_compliance_alert_worker()
    except Exception as err:
        print(f"[ComplianceAlerts] Failed to start worker: {err}")
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
    if path in ('/favicon.ico', '/apple-touch-icon.png', '/apple-touch-icon-precomposed.png'):
        icon_name = 'apple-touch-icon.png' if 'apple' in path else FAVICON_IMAGE_NAME
        icon_path = os.path.join(STATIC_DIR, 'img', icon_name)
        if not os.path.isfile(icon_path):
            icon_path = favicon_image_path()
        return serve_static(environ, start_response, icon_path, allowed_root=STATIC_DIR)
    if path == '/' or path == '/index.html':
        return serve_static(environ, start_response, os.path.join(TEMPLATES_DIR, 'index.html'), allowed_root=TEMPLATES_DIR)
    elif path.startswith('/static/'):
        rel_path = urllib.parse.unquote(path[len('/static/'):]).replace('\\', '/')
        if not rel_path or '\x00' in rel_path:
            start_response("404 Not Found", [('Content-Type', 'text/plain; charset=utf-8')])
            return [b"Not Found"]
        candidate = os.path.join(STATIC_DIR, rel_path)
        return serve_static(environ, start_response, candidate, allowed_root=STATIC_DIR)

    if path.startswith('/invoice/') and method in ('GET', 'HEAD'):
        parts = [urllib.parse.unquote(p) for p in path.split('/') if p]
        # Supported:
        #   /invoice/{id}
        #   /invoice/{id}/...?expires=&signature=
        #   /invoice/{id}/{expires}/{signature}/Invoice INV-0003.pdf   (preferred)
        #   /invoice/{id}/Invoice-INV-0003.pdf/{expires}/{signature}   (legacy)
        if len(parts) not in (2, 3, 5):
            start_response("404 Not Found", [('Content-Type', 'text/plain; charset=utf-8')])
            return [b'Invoice not found']
        try:
            invoice_id = int(parts[1])
        except (TypeError, ValueError):
            start_response("404 Not Found", [('Content-Type', 'text/plain; charset=utf-8')])
            return [b'Invoice not found']
        if len(parts) == 5:
            # Preferred: .../{expires}/{signature}/Invoice INV-0003.pdf
            # Legacy:    .../Invoice-....pdf/{expires}/{signature}
            if parts[2].isdigit():
                expires = parts[2]
                signature = parts[3]
            else:
                expires = parts[3]
                signature = parts[4]
        else:
            # Repair email clients that request literal &amp; in the query string.
            raw_qs = (environ.get('QUERY_STRING') or '').replace('&amp;', '&')
            qs = urllib.parse.parse_qs(raw_qs)
            expires = _qs_first(qs, 'expires') or '0'
            signature = _qs_first(qs, 'signature') or ''
        if not verify_invoice_document_link(invoice_id, expires, signature):
            start_response("403 Forbidden", [('Content-Type', 'text/plain; charset=utf-8')])
            return [b'Invoice link is invalid or expired']
        return serve_invoice_document(start_response, invoice_id, head_only=(method == 'HEAD'))

    if path == '/api/public/incorporation-certificate' and method == 'GET':
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        try:
            document_id = int((_qs_first(qs, 'document_id') or '0'))
            company_id = int((_qs_first(qs, 'company_id') or '0'))
            expires = int((_qs_first(qs, 'expires') or '0'))
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Invalid certificate link'}, "400 Bad Request")
        signature = str(_qs_first(qs, 'signature') or '').strip()
        expected = sign_incorporation_download(document_id, company_id, expires)
        if not expected or not signature or not hmac.compare_digest(signature, expected):
            return json_response(start_response, {'status': 'error', 'message': 'Invalid certificate link'}, "403 Forbidden")
        if expires < int(datetime.datetime.now().timestamp()):
            return json_response(start_response, {'status': 'error', 'message': 'Certificate link expired'}, "403 Forbidden")
        doc = query_db(
            """
            SELECT * FROM documents
            WHERE id = ? AND company_id = ?
              AND LOWER(COALESCE(category, '')) = 'companies house certificate';
            """,
            (document_id, company_id),
            one=True,
        )
        if not doc:
            return json_response(start_response, {'status': 'error', 'message': 'Certificate not found'}, "404 Not Found")
        streamed = stream_stored_document(start_response, doc, inline=False, environ=environ)
        if streamed is not None:
            return streamed
        return json_response(start_response, {'status': 'error', 'message': 'Certificate file not found'}, "404 Not Found")

    # Compliance email CTA engagement (click tracking — no open pixels).
    if path.startswith('/api/public/compliance-engage/') and method == 'GET':
        token = path.rsplit('/', 1)[-1].strip()
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        target = (_qs_first(qs, 'to') or '').strip()
        import compliance_alerts as _ca
        redirect_to = _ca.record_compliance_engagement(token, target)
        if not redirect_to:
            return json_response(start_response, {'status': 'error', 'message': 'Invalid engagement link'}, "404 Not Found")
        start_response('302 Found', [
            ('Location', redirect_to),
            ('Cache-Control', 'no-store'),
        ])
        return [b'']

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
                existing_ord = query_db(
                    """
                    SELECT o.*, u.email, u.full_name
                    FROM orders o
                    JOIN users u ON o.user_id = u.id
                    WHERE o.order_number = ?;
                    """,
                    (order_num,),
                    one=True,
                )
                if existing_ord:
                    already_paid = query_db(
                        "SELECT id FROM invoices WHERE order_id = ? AND status = 'Paid';",
                        (existing_ord['id'],),
                        one=True,
                    )
                    execute_db("""
                        UPDATE orders SET status = CASE WHEN status = 'Pending Verification' THEN status ELSE 'Processing' END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?;
                    """, (existing_ord['id'],))
                    ensure_invoice_for_order(existing_ord['id'], paid=True)
                    guest = query_db("SELECT id FROM users WHERE email = 'guest.unlinked@brixenconsultants.com';", one=True)
                    is_guest = guest and int(existing_ord.get('user_id') or 0) == int(guest['id'])
                    if not is_guest and not already_paid:
                        paid_invoice = query_db(
                            "SELECT * FROM invoices WHERE order_id = ? ORDER BY id DESC LIMIT 1;",
                            (existing_ord['id'],),
                            one=True,
                        )
                        notify_payment_received(existing_ord, invoice=paid_invoice)
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
        if not client_rec and email:
            name = email.split('@')[0].replace('.', ' ').replace('_', ' ').replace('-', ' ').title()
            db_execute("""
                INSERT INTO users (full_name, email, role, status, wordpress_user_id, created_at, updated_at)
                VALUES (?, ?, 'CLIENT', 'ACTIVE', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            """, (name, email, wp_id))
            client_rec = find_client_for_wordpress(wp_id, email)

        if not client_rec:
            return json_response(start_response, {'status': 'error', 'message': 'Client record not found for SSO'}, "404 Not Found")
            
        token = create_db_session(client_rec['id'])
        log_activity(client_rec, 'Client logged in via WordPress SSO', 'users', client_rec['id'])

        if method == 'GET':
            start_response("302 Found", [
                ('Location', '/#dashboard'),
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
        target_user = query_db("SELECT id, wordpress_user_id, email, notification_email, full_name, phone, country, address, avatar_url, role, status, last_synced_at, created_at FROM users WHERE id = ?;", (cid,), one=True)
        if not target_user:
            return json_response(start_response, {'status': 'error', 'message': 'Client not found'}, "404 Not Found")
            
        companies = query_db("SELECT * FROM companies WHERE user_id = ? ORDER BY id DESC;", (cid,))
        companies = filter_companies_for_portfolio_list(companies)
        orders = [dict(row) for row in query_db("SELECT o.*, c.name as company_name FROM orders o LEFT JOIN companies c ON o.company_id = c.id WHERE o.user_id = ? ORDER BY o.created_at DESC;", (cid,))]
        invoices = [enrich_invoice_row(dict(row)) for row in query_db("SELECT * FROM invoices WHERE user_id = ? ORDER BY created_at DESC;", (cid,))]
        if not can_view_revenue(user):
            orders = [strip_order_finance(row) for row in orders]
            invoices = [strip_order_finance(row) for row in invoices]
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
    if (path.startswith('/api/documents/') or path.startswith('/api/client/documents/')) and method == 'GET' and (path.endswith('/download') or path.endswith('/view')):
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")

        parts = path.split('/')
        if path.startswith('/api/client/documents/'):
            doc_id = parts[4] if len(parts) > 4 else ''
        else:
            doc_id = parts[3] if len(parts) > 3 else ''
        doc = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
        if not doc:
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")

        allowed, status, message = user_can_stream_document(user, doc)
        if not allowed:
            return json_response(start_response, {'status': 'error', 'message': message}, status)

        streamed = stream_stored_document(start_response, doc, inline=path.endswith('/view'), environ=environ)
        if streamed is not None:
            return streamed
        return json_response(start_response, {'status': 'error', 'message': 'Document file not found'}, "404 Not Found")

    if path == '/api/search' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        return json_response(start_response, {
            'status': 'success',
            'results': run_portal_search(user, _qs_first(qs, 'q')),
        })

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
    # API: User Preferences (Theme, UI settings)
    # ----------------------------------------------------
    if path == '/api/user/preferences' and method in ('GET', 'POST'):
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if method == 'POST':
            data = parse_body(environ)
            theme = str(data.get('theme') or data.get('theme_preference') or 'system').lower().strip()
            if theme not in ('light', 'dark', 'system'):
                return json_response(start_response, {'status': 'error', 'message': 'Invalid theme option'}, "400 Bad Request")
            execute_db("UPDATE users SET theme_preference = ? WHERE id = ?;", (theme, user['id']))
            user['theme_preference'] = theme
            return json_response(start_response, {'status': 'success', 'theme_preference': theme})
        else:
            current_theme = user.get('theme_preference') or 'system'
            return json_response(start_response, {'status': 'success', 'theme_preference': current_theme})

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
        # Slide cookie Max-Age on every successful me check so refresh never drops the session.
        token = session_token_from_environ(environ)
        extra = [('Set-Cookie', session_cookie_header(token, environ=environ))] if token else None
        return json_response(start_response, {'status': 'success', 'user': u_dict}, extra_headers=extra)

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
        uk_comps = comp_count
        intl_comps = 0
        companies = query_db(f"""
            SELECT id, name, company_number, status, inc_date, director, reg_office, package, account_status,
                   created_at, utr_number, identity_verified, psc_verified, registered_email, registered_email_locked, whatsapp_number
            FROM companies WHERE user_id = ? ORDER BY {company_list_order_sql()};
        """, (uid,))
        public_companies = [public_client_company(row) for row in companies]

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

        visible_docs = query_db("""
            SELECT d.*, c.name as company_name, o.order_number
            FROM documents d
            LEFT JOIN companies c ON d.company_id = c.id
            LEFT JOIN orders o ON d.order_id = o.id
            WHERE d.user_id = ? AND d.client_visible = 1
            ORDER BY d.created_at DESC;
        """, (uid,)) or []
        public_docs = [public_document(row, for_client=True) for row in visible_docs]
        pending_actions = build_client_pending_actions(uid)

        display_name = (user.get('full_name') or '').strip() or user.get('email') or 'there'
        now_dt = datetime.datetime.now()
        now_str = now_dt.strftime("%d %B %Y")

        return json_response(start_response, {
            'status': 'success',
            'current_date': now_str,
            'greeting': client_greeting_label(now_dt),
            'user_name': display_name,
            'total_companies': comp_count,
            'uk_companies': uk_comps,
            'intl_companies': intl_comps,
            'overdue_deadlines': overdue_cnt,
            'upcoming_deadlines': upcoming_cnt,
            'active_orders_count': len(enhanced_orders),
            'documents_count': len(public_docs),
            'pending_actions_count': len(pending_actions),
            'pending_actions': pending_actions,
            'active_orders': enhanced_orders,
            'companies': public_companies,
            'recent_documents': public_docs[:4],
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
        if user['role'] != 'CLIENT':
            ensure_invoice_for_order(oid)
        invoice = query_db("SELECT * FROM invoices WHERE order_id = ? ORDER BY id DESC LIMIT 1;", (oid,), one=True)
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
        if not order_out.get('company_id'):
            linked_id = ensure_order_company_link(order, checkout_form)
            if linked_id:
                order_out['company_id'] = linked_id
                named = query_db("SELECT name, company_number FROM companies WHERE id = ?;", (linked_id,), one=True) or {}
                order_out['company_name'] = named.get('name') or order_out.get('company_name')
                order_out['company_number'] = named.get('company_number') or order_out.get('company_number')
        if not str(order_out.get('company_name') or '').strip():
            order_out['company_name'] = company_name_from_checkout_fields(checkout_form)
        order_out['checkout_form'] = checkout_form
        if not for_client:
            order_out['company_owner'] = get_company_owner_for_order(order, checkout_form)
            order_out['portal_login_email'] = order.get('client_email')
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

        include_finance = for_client or can_view_revenue(user)
        invoice_out = None
        if invoice:
            money = invoice_money_state(invoice)
            invoice_out = {
                'id': invoice.get('id'),
                'invoice_number': invoice.get('invoice_number'),
                'status': money['display_status'],
                'due_date': invoice.get('due_date'),
                'paid_at': invoice.get('paid_at'),
                'payment_timing': money['timing'],
                'document_url': f"/api/admin/invoices/{int(invoice['id'])}/document" if invoice.get('id') and not for_client else f"/api/client/invoices/{int(invoice['id'])}/document" if invoice.get('id') else '',
            }
            if include_finance:
                invoice_out.update({
                    'amount': invoice.get('amount'),
                    'tax': invoice.get('tax'),
                    'total': money['total'],
                    'payment_method': invoice.get('payment_method'),
                    'deposit_amount': money['deposit_amount'],
                    'amount_paid': money['amount_paid'],
                    'amount_due': money['amount_due'],
                })
        if not include_finance:
            order_out = strip_order_finance(order_out)

        return json_response(start_response, {
            'status': 'success',
            'order': order_out,
            'timeline': timeline,
            'invoice': invoice_out,
            'documents': [public_document(d, for_client=for_client) for d in documents],
            'line_items': [public_line_item(r, for_client=for_client, include_finance=include_finance) for r in line_rows],
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
                   created_at, user_id, utr_number, authentication_code, activation_code,
                   identity_verified, identity_verified_at, psc_verified, psc_verified_at
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
        ch_synced = 0
        schedule_portfolio_ch_sync()
        comps = query_db(f"""
            SELECT {COMPANY_STAFF_SELECT}
            FROM companies ORDER BY {company_list_order_sql()};
        """)
        comps = filter_companies_for_portfolio_list(comps)
        deadlines_map = upcoming_deadlines_by_company(None, [row['id'] for row in comps])
        uk_count = len(comps or [])
        clients = query_db("SELECT id, full_name, email FROM users WHERE role = 'CLIENT' AND status = 'Active' ORDER BY full_name COLLATE NOCASE;") or []
        return json_response(start_response, {
            'status': 'success',
            'companies': [public_client_company(row, deadlines_map.get(int(row['id']), []), for_staff=True) for row in comps],
            'pending_registrations': pending_registration_orders(for_client=False),
            'clients': [{'id': row['id'], 'full_name': row.get('full_name') or '', 'email': row.get('email') or ''} for row in clients],
            'companies_house_configured': bool(companies_house_api_key()),
            'companies_house_synced': ch_synced,
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

    if path == '/api/admin/companies/bulk-delete' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] not in ('SUPER_ADMIN', 'ADMIN'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ) or {}
        ids = data.get('company_ids') or data.get('ids') or []
        if not isinstance(ids, list) or not ids:
            return json_response(start_response, {'status': 'error', 'message': 'Select at least one company'}, "400 Bad Request")
        if len(ids) > 200:
            return json_response(start_response, {'status': 'error', 'message': 'Delete up to 200 companies at a time'}, "400 Bad Request")
        summary = bulk_delete_company_cards(ids, actor=user)
        return json_response(start_response, {
            'status': 'success',
            'message': f"Deleted {summary['deleted_count']} compan{'y' if summary['deleted_count'] == 1 else 'ies'}.",
            **summary,
        })

    if path == '/api/admin/companies/bulk-update' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] not in ('SUPER_ADMIN', 'ADMIN', 'MANAGER'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ) or {}
        ids = data.get('company_ids') or data.get('ids') or []
        if not isinstance(ids, list) or not ids:
            return json_response(start_response, {'status': 'error', 'message': 'Select at least one company'}, "400 Bad Request")
        if len(ids) > 200:
            return json_response(start_response, {'status': 'error', 'message': 'Update up to 200 companies at a time'}, "400 Bad Request")
        summary, error = bulk_update_company_cards(ids, data, actor=user)
        if error:
            return json_response(start_response, {'status': 'error', 'message': error}, "400 Bad Request")
        return json_response(start_response, {
            'status': 'success',
            'message': f"Updated {summary['updated_count']} compan{'y' if summary['updated_count'] == 1 else 'ies'}.",
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
        previous = query_db("SELECT name FROM companies WHERE id = ?;", (company_id,), one=True)
        if 'name' in (data or {}):
            current = query_db(f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;", (company_id,), one=True)
            if current and not is_pending_company_number(current.get('company_number')):
                company, error = relink_company_from_companies_house(
                    company_id,
                    data.get('name'),
                    data.get('companies_house_number'),
                )
            else:
                company, error = rename_pending_company(
                    company_id,
                    data.get('name'),
                    data.get('companies_house_number'),
                )
            if error:
                status_code = "404 Not Found" if error == 'Company not found' else "400 Bad Request"
                return json_response(start_response, {'status': 'error', 'message': error}, status_code)
        company, error = update_company_compliance(company_id, data, actor=user)
        if error:
            status_code = "404 Not Found" if error == 'Company not found' else "400 Bad Request"
            return json_response(start_response, {'status': 'error', 'message': error}, status_code)
        deadlines_map = upcoming_deadlines_by_company(company.get('user_id'), [company_id])
        old_name = str((previous or {}).get('name') or '').strip()
        new_name = str((company or {}).get('name') or '').strip()
        if 'name' in (data or {}) and old_name and new_name and old_name.lower() != new_name.lower():
            log_activity(user, 'COMPANY_UPDATED', 'companies', str(company_id), f"Renamed company from {old_name} to {new_name}")
        else:
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
            f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ?;",
            (company_id,),
            one=True,
        )
        if not company:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        # Return DB state immediately — CH / WP refresh runs in background.
        schedule_company_detail_refresh(company_id)
        owner_id = company.get('user_id')
        deadlines_map = upcoming_deadlines_by_company(owner_id, [company_id])
        registered_office = company_registered_office_record(company_id, company)
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
            'company': public_client_company(company, deadlines_map.get(company_id, []), for_staff=True, resolve_owner=False),
            'client_id': company.get('user_id'),
            'registered_office': registered_office,
            'orders': [public_client_order_summary(row) for row in orders],
            'documents': [public_document(row, for_client=False) for row in documents],
            'tickets': [public_client_ticket_summary(row) for row in tickets],
            'email_verification_history': [
                dict(row) for row in (__import__('compliance_alerts').email_verification_history(company_id))
            ],
            'notification_history': [
                dict(row) for row in (__import__('compliance_alerts').compliance_notification_history(company_id))
            ],
            'compliance_contact': (__import__('compliance_alerts').brand_contact_block()),
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
        company, error = delete_company_card(company_id, actor=user)
        if error:
            status_code = "404 Not Found" if error == 'Company not found' else "400 Bad Request"
            return json_response(start_response, {'status': 'error', 'message': error}, status_code)
        return json_response(start_response, {
            'status': 'success',
            'message': f"Company {company.get('name')} deleted.",
            'company_id': company_id,
        })

    if path == '/api/client/companies' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        ch_synced = 0
        schedule_portfolio_ch_sync(user_id=user['id'])
        comps = query_db(
            f"""
            SELECT {COMPANY_LIST_SELECT}
            FROM companies WHERE user_id = ? ORDER BY {company_list_order_sql()};
            """,
            (user['id'],),
        )
        comps = filter_companies_for_portfolio_list(comps)
        deadlines_map = upcoming_deadlines_by_company(user['id'], [row['id'] for row in comps])
        return json_response(start_response, {
            'status': 'success',
            'companies': [public_client_company(row, deadlines_map.get(int(row['id']), [])) for row in comps],
            'pending_registrations': pending_registration_orders(user_id=user['id'], for_client=True),
            'companies_house_synced': ch_synced,
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
        owned = query_db("SELECT id FROM companies WHERE id = ? AND user_id = ?;", (company_id, user['id']), one=True)
        if owned:
            sync_pending_companies_from_companies_house(company_id=company_id, user_id=user['id'], limit=1)
        company = query_db(
            f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ? AND user_id = ?;",
            (company_id, user['id']),
            one=True,
        )
        if not company:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        sync_registered_company_from_companies_house(company)
        persist_company_registered_email(company)
        company = query_db(
            f"SELECT {COMPANY_STAFF_SELECT} FROM companies WHERE id = ? AND user_id = ?;",
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
            'company': public_client_company(company, deadlines_map.get(company_id, []), resolve_owner=True),
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
        invoices = []
        for row in invs or []:
            item = enrich_invoice_row(dict(row)) or dict(row)
            if item.get('id'):
                item['document_url'] = f"/api/client/invoices/{int(item['id'])}/document"
            invoices.append(item)
        return json_response(start_response, {'status': 'success', 'invoices': invoices})

    if path.startswith('/api/client/invoices/') and path.endswith('/document') and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        parts = [p for p in path.split('/') if p]
        if len(parts) != 5:
            return json_response(start_response, {'status': 'error', 'message': 'Invoice not found'}, "404 Not Found")
        try:
            invoice_id = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Invoice not found'}, "404 Not Found")
        owned = query_db(
            "SELECT id FROM invoices WHERE id = ? AND user_id = ?;",
            (invoice_id, user['id']),
            one=True,
        )
        if not owned:
            return json_response(start_response, {'status': 'error', 'message': 'Invoice not found'}, "404 Not Found")
        return serve_invoice_document(start_response, invoice_id)

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
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        triage = triage_uploaded_document(
            file_bytes, upload_filename or doc_name, user['id'],
            client_id=user['id'], actor=user, run_company_match=True,
        )
        resolved_company_id = company_id or triage.get('company_id')
        lifecycle = triage.get('lifecycle_status') or 'REVIEW_REQUIRED'
        doc_id = execute_db("""
            INSERT INTO documents (
                user_id, company_id, order_id, name, category, file_path, file_type, file_size,
                status, uploaded_by, review_notes, client_visible,
                file_hash, lifecycle_status, is_posted,
                ocr_confidence, classification_confidence, identity_confidence,
                customer_match_confidence, company_match_confidence, duplicate_confidence,
                uploaded_at, processed_at, uploaded_by_id, processed_by_id, match_meta_json, overall_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending Review', 'Customer Upload', ?, 0,
                    ?, ?, 0, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?, ?, ?);
        """, (
            user['id'], resolved_company_id, order_id, doc_name,
            triage.get('category') or category, target_path,
            DOCUMENT_TYPE_LABELS.get(ext, 'Document'), file_size_str,
            triage.get('review_notes') or 'Awaiting staff approval.',
            file_hash, lifecycle,
            float(triage.get('ocr_confidence') or 0),
            float(triage.get('classification_confidence') or 0),
            float(triage.get('identity_confidence') or 0),
            float(triage.get('customer_match_confidence') or 0),
            float(triage.get('company_match_confidence') or 0),
            float(triage.get('duplicate_confidence') or 0),
            user['id'], user['id'],
            json.dumps(triage.get('match_meta') or {}),
            lifecycle,
        ))
        log_document_audit(
            doc_id, 'AI', user['id'], user['full_name'], 'CUSTOMER_UPLOAD_TRIAGED',
            'CUSTOMER_UPLOADS', lifecycle, {
                'matching_evidence': triage.get('matching_evidence'),
                'conflicting_evidence': triage.get('conflicting_evidence'),
                'quarantine_reason': triage.get('quarantine_reason'),
            },
        )
        log_activity(user, 'DOCUMENT_UPLOAD', 'documents', str(doc_id), f"Uploaded document {doc_name} → {lifecycle}")
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

        return json_response(start_response, {
            'status': 'success',
            'message': 'Document uploaded successfully and queued for review.',
            'document_id': doc_id,
            'lifecycle_status': lifecycle,
        })



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
        email_team_about_clients(
            f"New support ticket {tnum} — Brixen Consultants",
            (
                f"A client opened a support ticket.\n\n"
                f"Ticket: {tnum}\n"
                f"Client: {user.get('full_name') or user.get('email')}\n"
                f"Subject: {subject}\n"
                f"Priority: {priority}\n\n"
                f"Open support: {portal_page_url()}#admin-tickets"
            ),
        )
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
        payload = build_admin_dashboard_payload(user)
        return json_response(start_response, {
            'status': 'success',
            **payload,
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

    if path == '/api/admin/customers/notification-email' and method == 'POST':
        if not user or not check_permission(user, 'clients.edit'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        customer_id, err = to_optional_int(data.get('customer_id'), 'customer_id')
        if err or not customer_id:
            return json_response(start_response, {'status': 'error', 'message': 'Customer is required'}, "400 Bad Request")
        raw_email = (data.get('notification_email') or '').strip()
        if raw_email:
            notification_email = normalize_notify_email(raw_email)
            if not notification_email:
                return json_response(start_response, {'status': 'error', 'message': 'Enter a valid notification email.'}, "400 Bad Request")
        else:
            notification_email = None
        target = query_db("SELECT id, email, full_name, role FROM users WHERE id = ?;", (customer_id,), one=True)
        if not target or target.get('role') != 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Customer not found'}, "404 Not Found")
        execute_db("UPDATE users SET notification_email = ? WHERE id = ?;", (notification_email, customer_id))
        log_activity(
            user,
            'CUSTOMER_NOTIFICATION_EMAIL_UPDATE',
            'users',
            str(customer_id),
            f"Notification email for {target.get('email')} set to {notification_email or '(login email)'}",
        )
        return json_response(start_response, {
            'status': 'success',
            'message': 'Notification email saved. All client emails for this customer will use it.',
            'notification_email': notification_email or '',
            'effective_email': notification_email or target.get('email') or '',
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

    if path in ('/api/admin/orders', '/api/client/orders') and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Authentication required'}, "401 Unauthorized")
        if user.get('role') != 'CLIENT' and not check_permission(user, 'orders.edit'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ)
        data = dict(data if isinstance(data, dict) else {})
        if user.get('role') == 'CLIENT':
            data['client_mode'] = 'existing'
            data['client_id'] = user['id']
        created, err = create_manual_crm_order(data, actor=user)
        if err:
            return json_response(start_response, {'status': 'error', 'message': err}, "400 Bad Request")
        return json_response(start_response, {
            'status': 'success',
            'message': f"Order {created.get('order_number')} created successfully.",
            'order': apply_connector_display(dict(created)) if created else created,
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
                   o.owner_name, o.owner_form_email, o.checkout_form_json,
                   u.full_name as client_name, u.email as client_email, c.name as company_name,
                   c.director as company_director, c.company_number,
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

        listed = []
        for row in orders:
            item = dict(row)
            if not item.get('company_id'):
                linked_id = ensure_order_company_link(item)
                if linked_id:
                    item['company_id'] = linked_id
                    linked = query_db(
                        "SELECT name, director, company_number FROM companies WHERE id = ?;",
                        (linked_id,),
                        one=True,
                    ) or {}
                    item['company_name'] = linked.get('name') or item.get('company_name')
                    item['company_director'] = linked.get('director') or item.get('company_director')
                    item['company_number'] = linked.get('company_number') or item.get('company_number')
            item['company_name'] = order_display_company_name(item)
            item.pop('checkout_form_json', None)
            display_owner = order_owner_display_name(item)
            if display_owner:
                item['owner_name'] = display_owner
            if not can_view_revenue(user):
                item = strip_order_finance(item)
            listed.append(item)

        return json_response(start_response, {
            'status': 'success',
            'orders': listed,
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
            if not can_view_revenue(user):
                return json_response(start_response, {'status': 'error', 'message': 'Only an administrator can change payment mode'}, "403 Forbidden")
            payment_mode = (data.get('payment_mode') or '').strip() or None
            if payment_mode and payment_mode not in ORDER_PAYMENT_MODES:
                return json_response(start_response, {'status': 'error', 'message': 'Invalid payment mode'}, "400 Bad Request")
            execute_db("UPDATE orders SET payment_mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (payment_mode, oid))
            execute_db(
                "UPDATE invoices SET payment_method = ? WHERE order_id = ? AND status != 'Paid';",
                (payment_mode, oid),
            )
        if 'total' in data or 'price' in data:
            if not can_edit_order_price(user):
                return json_response(start_response, {'status': 'error', 'message': 'Only an administrator can change order prices'}, "403 Forbidden")
            raw_amount = data.get('total') if 'total' in data else data.get('price')
            try:
                new_total = round(float(str(raw_amount).replace('£', '').replace(',', '').strip()), 2)
            except (TypeError, ValueError, AttributeError):
                return json_response(start_response, {'status': 'error', 'message': 'Enter a valid price'}, "400 Bad Request")
            if new_total < 0 or new_total > 100000:
                return json_response(start_response, {'status': 'error', 'message': 'Price must be between £0 and £100,000'}, "400 Bad Request")
            old_total = float(existing.get('total') or 0)
            old_price = float(existing.get('price') or 0)
            if old_total > 0 and old_price >= 0:
                ratio = min(1.0, max(0.0, old_price / old_total))
            else:
                ratio = 1.0
            new_price = round(new_total * ratio, 2)
            new_vat = round(new_total - new_price, 2)
            execute_db(
                "UPDATE orders SET total = ?, price = ?, vat = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (new_total, new_price, new_vat, oid),
            )
            ensure_invoice_for_order(oid)
            execute_db(
                """
                UPDATE invoices
                SET amount = ?, tax = ?, total = ?
                WHERE order_id = ? AND status != 'Paid';
                """,
                (new_price, new_vat, new_total, oid),
            )
        checkout_updated = False
        if 'checkout_form' in data:
            ok, form_err = apply_staff_checkout_form_update(existing, data.get('checkout_form'))
            if not ok:
                return json_response(start_response, {'status': 'error', 'message': form_err}, "400 Bad Request")
            checkout_updated = True

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
        if 'total' in data or 'price' in data:
            history_parts.append(f"Price updated to £{new_total:.2f}")
        if checkout_updated:
            history_parts.append("Checkout form details updated")
        if history_parts:
            append_order_history(oid, " · ".join(history_parts))
            
        target_order = query_db(
            """
            SELECT o.*, u.email, u.full_name, c.name as company_name
            FROM orders o
            JOIN users u ON o.user_id = u.id
            LEFT JOIN companies c ON o.company_id = c.id
            WHERE o.id = ?;
            """,
            (oid,),
            one=True,
        )
        old_status = existing.get('status')
        if target_order and status is not None and status != old_status:
            if status == 'Completed':
                notify_product_completed(target_order)
            else:
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
                    extra_message=f"✅ {target_order.get('service_name') or 'Your order'}\n📦 Order {target_order['order_number']}",
                    layout='activity',
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
        company_id = optional_record_id(existing.get('company_id'))
        company = query_db(
            "SELECT id, name, company_number, status, inc_date, director, reg_office FROM companies WHERE id = ?;",
            (company_id,),
            one=True,
        ) if company_id else None
        form_number = checkout_form_company_number(parse_order_checkout_fields(existing), company)
        owner_name = str(existing.get('owner_name') or '').strip()
        if (
            company
            and looks_like_placeholder_director(company.get('director'), company.get('name'))
            and owner_name
            and not looks_like_placeholder_director(owner_name, company.get('name'))
        ):
            persist_official_director(company_id, owner_name)
        doc_rows = query_db("SELECT file_path FROM documents WHERE order_id = ?;", (oid,))
        file_paths = [row.get('file_path') for row in doc_rows]
        execute_db("DELETE FROM orders WHERE id = ?;", (oid,))
        remove_stored_document_files(file_paths)
        leftover = query_db(
            "SELECT id, name, company_number, status, inc_date, director, reg_office FROM companies WHERE id = ?;",
            (company_id,),
            one=True,
        ) if company_id else None
        if leftover:
            hydrate_company_from_companies_house(leftover, {'company_number': form_number} if form_number else {})
        log_activity(user, 'ORDER_DELETE', 'orders', str(oid), f"Deleted order {existing.get('order_number')}")
        return json_response(start_response, {
            'status': 'success',
            'message': 'Order deleted. The company card was kept.',
            'company_id': company_id,
            'company_kept': bool(leftover),
        })

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
        backfill_invoices_from_orders()
        invoices = [
            apply_connector_display(row)
            for row in query_db("""
            SELECT i.id, i.invoice_number, i.order_id, i.user_id, i.amount, i.tax, i.total,
                   i.status, i.due_date, i.paid_at, i.payment_method, i.payment_timing,
                   i.deposit_amount, i.amount_paid, i.created_at,
                   o.order_number, o.service_name, o.company_id, o.owner_name, o.owner_form_email,
                   o.checkout_form_json, c.name as company_name, c.director as company_director,
                   c.company_number
            FROM invoices i
            LEFT JOIN orders o ON i.order_id = o.id
            LEFT JOIN companies c ON o.company_id = c.id
            ORDER BY i.created_at DESC;
        """)
        ]
        invoices = [enrich_invoice_row(row) for row in invoices]
        if not can_view_revenue(user):
            invoices = [strip_order_finance(row) for row in invoices]
        for row in invoices:
            if row.get('id'):
                row['document_url'] = f"/api/admin/invoices/{int(row['id'])}/document"
        return json_response(start_response, {'status': 'success', 'invoices': invoices})

    if path.startswith('/api/admin/invoices/') and path.endswith('/document') and method == 'GET':
        denied = require_permission(start_response, user, 'invoices.view')
        if denied:
            return denied
        parts = [p for p in path.split('/') if p]
        if len(parts) != 5:
            return json_response(start_response, {'status': 'error', 'message': 'Invoice not found'}, "404 Not Found")
        try:
            invoice_id = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Invoice not found'}, "404 Not Found")
        return serve_invoice_document(start_response, invoice_id)

    if path.startswith('/api/admin/invoices/') and method in ('PUT', 'PATCH'):
        denied = require_permission(start_response, user, 'invoices.view')
        if denied:
            return denied
        parts = [p for p in path.split('/') if p]
        if len(parts) != 4:
            return json_response(start_response, {'status': 'error', 'message': 'Invoice not found'}, "404 Not Found")
        try:
            invoice_id = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Invoice not found'}, "404 Not Found")
        updated, error, mail = update_admin_invoice(invoice_id, parse_body(environ), actor=user)
        if error:
            status_code = "404 Not Found" if error == 'Invoice not found' else "400 Bad Request"
            if 'administrator' in error:
                status_code = "403 Forbidden"
            return json_response(start_response, {'status': 'error', 'message': error}, status_code)
        payload = dict(updated or {})
        if not can_view_revenue(user):
            payload = strip_order_finance(payload)
        log_activity(
            user,
            'INVOICE_UPDATED',
            'invoices',
            str(invoice_id),
            f"Updated invoice {payload.get('invoice_number')}",
        )
        return json_response(start_response, {
            'status': 'success',
            'invoice': payload,
            'email_sent': bool((mail or {}).get('email_sent')),
            'email_status': (mail or {}).get('email_status'),
        })

    if path == '/api/admin/accountancy' and method == 'GET':
        denied = require_permission(start_response, user, 'accountancy.manage')
        if denied:
            return denied
        rows, stats = list_accountancy_rows(for_client=False)
        return json_response(start_response, {
            'status': 'success',
            'companies': rows,
            'stats': stats,
            'accounts_types': list(ACCOUNTS_TYPES),
            'accounts_statuses': list(ACCOUNTS_STATUSES),
            'identity_statuses': list(IDENTITY_STATUSES),
            'psc_statuses': list(PSC_STATUSES),
            'book_categories': list(BOOK_CATEGORIES),
        })

    if path == '/api/admin/accountancy' and method == 'POST':
        denied = require_permission(start_response, user, 'accountancy.manage')
        if denied:
            return denied
        data = parse_body(environ)
        try:
            company_id = int(data.get('company_id'))
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Select a company'}, "400 Bad Request")
        filing, error = upsert_company_accounts_filing(company_id, data, actor=user)
        if error:
            status_code = "404 Not Found" if error == 'Company not found' else "400 Bad Request"
            return json_response(start_response, {'status': 'error', 'message': error}, status_code)
        return json_response(start_response, {
            'status': 'success',
            'filing': public_accounts_filing(filing),
        })

    if path.startswith('/api/admin/accountancy/') and '/books' in path:
        denied = require_permission(start_response, user, 'accountancy.manage')
        if denied:
            return denied
        parts = [p for p in path.split('/') if p]
        # api, admin, accountancy, <company_id>, books [, <entry_id>]
        if len(parts) < 5 or parts[4] != 'books':
            return json_response(start_response, {'status': 'error', 'message': 'Books workspace not found'}, "404 Not Found")
        try:
            company_id = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        period_end = ((qs.get('period_end') or [None])[0] or '').strip() or None
        if method == 'GET':
            workspace, error = company_books_workspace(company_id, period_end=period_end)
            if error:
                status_code = "404 Not Found" if error == 'Company not found' else "400 Bad Request"
                return json_response(start_response, {'status': 'error', 'message': error}, status_code)
            return json_response(start_response, {'status': 'success', **workspace})
        if method == 'POST':
            data = parse_body(environ)
            if period_end and not data.get('period_end'):
                data['period_end'] = period_end
            entry, error = insert_company_book_entry(company_id, data)
            if error:
                status_code = "404 Not Found" if error == 'Company not found' else "400 Bad Request"
                return json_response(start_response, {'status': 'error', 'message': error}, status_code)
            workspace, _ = company_books_workspace(company_id, period_end=entry.get('period_end') if entry else period_end)
            return json_response(start_response, {
                'status': 'success',
                'entry': public_book_entry(entry),
                **(workspace or {}),
            })
        if method == 'DELETE':
            if len(parts) != 6:
                return json_response(start_response, {'status': 'error', 'message': 'Book entry not found'}, "404 Not Found")
            try:
                entry_id = int(parts[5])
            except (TypeError, ValueError):
                return json_response(start_response, {'status': 'error', 'message': 'Book entry not found'}, "404 Not Found")
            existing = query_db(
                "SELECT id, period_end FROM company_book_entries WHERE id = ? AND company_id = ?;",
                (entry_id, company_id),
                one=True,
            )
            if not existing:
                return json_response(start_response, {'status': 'error', 'message': 'Book entry not found'}, "404 Not Found")
            execute_db("DELETE FROM company_book_entries WHERE id = ? AND company_id = ?;", (entry_id, company_id))
            workspace, _ = company_books_workspace(company_id, period_end=existing.get('period_end') or period_end)
            return json_response(start_response, {'status': 'success', **(workspace or {})})
        return json_response(start_response, {'status': 'error', 'message': 'Method not allowed'}, "405 Method Not Allowed")

    if path.startswith('/api/admin/accountancy/') and method == 'PUT':
        denied = require_permission(start_response, user, 'accountancy.manage')
        if denied:
            return denied
        parts = [p for p in path.split('/') if p]
        if len(parts) != 4:
            return json_response(start_response, {'status': 'error', 'message': 'Filing not found'}, "404 Not Found")
        try:
            filing_id = int(parts[3])
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Filing not found'}, "404 Not Found")
        existing = query_db("SELECT * FROM company_accounts WHERE id = ?;", (filing_id,), one=True)
        if not existing:
            return json_response(start_response, {'status': 'error', 'message': 'Filing not found'}, "404 Not Found")
        data = parse_body(environ)
        payload = dict(existing)
        payload.update(data or {})
        filing, error = upsert_company_accounts_filing(existing['company_id'], payload, actor=user)
        if error:
            return json_response(start_response, {'status': 'error', 'message': error}, "400 Bad Request")
        return json_response(start_response, {
            'status': 'success',
            'filing': public_accounts_filing(filing),
        })

    if path == '/api/client/accountancy' and method == 'GET':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] != 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        rows, stats = list_accountancy_rows(user_id=user['id'], for_client=True)
        return json_response(start_response, {'status': 'success', 'companies': rows, 'stats': stats})

    if path == '/api/client/accountancy' and method == 'POST':
        if not user:
            return json_response(start_response, {'status': 'error', 'message': 'Not authenticated'}, "401 Unauthorized")
        if user['role'] != 'CLIENT':
            return json_response(start_response, {'status': 'error', 'message': 'Use the staff Accountancy menu to file accounts'}, "403 Forbidden")
        data = parse_body(environ)
        try:
            company_id = int(data.get('company_id'))
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Select a company'}, "400 Bad Request")
        company = query_db("SELECT id FROM companies WHERE id = ? AND user_id = ?;", (company_id, user['id']), one=True)
        if not company:
            return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
        filing, error = upsert_company_accounts_filing(company_id, data, actor=user, requested=True)
        if error:
            return json_response(start_response, {'status': 'error', 'message': error}, "400 Bad Request")
        return json_response(start_response, {
            'status': 'success',
            'filing': public_accounts_filing(filing, for_client=True),
        })

    if path == '/api/admin/documents' and method == 'GET':
        denied = require_permission(start_response, user, 'documents.view')
        if denied:
            return denied
        docs = query_db("""
            SELECT d.*, c.name as company_name, o.order_number, o.company_id as order_company_id,
                   o.owner_name, o.owner_form_email, o.checkout_form_json,
                   c.director as company_director, c.company_number
            FROM documents d
            LEFT JOIN companies c ON d.company_id = c.id
            LEFT JOIN orders o ON d.order_id = o.id
            ORDER BY d.created_at DESC;
        """)
        listed = []
        for doc in docs or []:
            row = dict(doc)
            if not row.get('company_id') and row.get('order_company_id'):
                row['company_id'] = row.get('order_company_id')
            listed.append(public_document(apply_connector_display(row)))
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
            client_visible_flag = 1
            if 'client_visible' in data:
                cv_val = data.get('client_visible')
                if cv_val is False or cv_val == 0 or str(cv_val).lower() in ('0', 'false', 'off', 'no'):
                    client_visible_flag = 0
            review_notes = (data.get('review_notes') or '').strip() or ('Delivered to client portal' if client_visible_flag else 'Internal staff document')
            # Staff intentional delivery posts immediately; customer-sourced categories stay unposted.
            staff_post_now = category not in ('Checkout Upload',) and (
                data.get('post_immediately') in (True, 1, '1', 'true', 'True')
                or category in ('Posted Documents', 'Order Documents', 'Certificate of Incorporation',
                                'Memorandum & Articles', 'Share Certificate')
                or client_visible_flag == 1
            )
            lifecycle = 'POSTED_DOCUMENTS' if staff_post_now else 'READY_FOR_APPROVAL'
            is_posted = 1 if staff_post_now else 0
            file_hash = hashlib.sha256(file_bytes).hexdigest()
            doc_id = execute_db("""
                INSERT INTO documents (
                    user_id, company_id, order_id, name, category, file_path, file_type, file_size,
                    status, uploaded_by, review_notes, client_visible, shared_at,
                    file_hash, lifecycle_status, is_posted, uploaded_at, posted_at, uploaded_by_id, posted_by_id,
                    ocr_confidence, classification_confidence, identity_confidence,
                    customer_match_confidence, company_match_confidence, overall_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Approved', ?, ?, ?, ?,
                        ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?,
                        100, 100, 100, 100, 100, ?);
            """, (
                target_client['id'], resolved_company_id, order_id, doc_name, category, target_path,
                DOCUMENT_TYPE_LABELS.get(ext, 'Document'), file_size_str, user['full_name'], review_notes,
                client_visible_flag,
                (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') if client_visible_flag == 1 else None),
                file_hash, lifecycle, is_posted,
                (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') if is_posted else None),
                user['id'], (user['id'] if is_posted else None),
                lifecycle,
            ))
            if is_posted:
                log_document_audit(
                    doc_id, 'HUMAN', user['id'], user['full_name'], 'STAFF_POSTED',
                    None, 'POSTED_DOCUMENTS', {'category': category},
                )
            log_activity(user, 'DOCUMENT_UPLOAD', 'documents', str(doc_id), f"Uploaded document {doc_name} for client #{target_client['id']} (client_visible={client_visible_flag})")
            
            notification_created = False
            email_sent = False
            email_status = 'not_applicable'

            if client_visible_flag == 1:
                company_obj = query_db("SELECT id, user_id, name, registered_email FROM companies WHERE id = ?;", (resolved_company_id,), one=True) if resolved_company_id else None
                order_obj = query_db("SELECT id, order_number, company_id, owner_form_email, user_id FROM orders WHERE id = ?;", (order_id,), one=True) if order_id else None
                comp_name = company_obj['name'] if company_obj else None
                ord_num = order_obj['order_number'] if order_obj else None
                
                delivery = notify_client_document_uploaded(
                    target_client,
                    doc_name,
                    client_message,
                    company_name=comp_name,
                    order_number=ord_num,
                    company=company_obj,
                    order=order_obj,
                    company_id=resolved_company_id,
                )
                notification_created = delivery['notification_created']
                email_sent = delivery['email_sent']
                email_status = delivery['email_status']

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
                'message': 'Document uploaded successfully.' if client_visible_flag else 'Document stored. It is saved as an internal document and not visible to the client.',
                'document': public_document(created),
                'notification_created': notification_created,
                'email_sent': email_sent,
                'email_status': email_status,
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

    if path == '/api/admin/documents/intake/process' and method == 'POST':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        try:
            ctype = (environ.get('CONTENT_TYPE') or '').lower()
            files_data = []
            batch_identities = []
            source_mode = 'CUSTOMER_UPLOADS'
            if 'multipart/form-data' in ctype:
                fields, files = parse_multipart_form(environ)
                source_mode = (fields.get('source_mode') or 'CUSTOMER_UPLOADS').strip().upper()
                try:
                    batch_identities = json.loads(fields.get('batch_identities') or '[]')
                except Exception:
                    batch_identities = []
                upload = files.get('file') or files.get('document')
                if upload and upload.get('bytes'):
                    fname = (
                        (fields.get('file_name') or '').strip()
                        or upload.get('filename')
                        or 'document.pdf'
                    )
                    folder_path = (fields.get('folder_path') or fname).strip()
                    files_data = [{
                        'file_name': fname,
                        'folder_path': folder_path,
                        'document_type': (fields.get('document_type') or '').strip(),
                        'allowed_types': (fields.get('allowed_types') or '').strip(),
                        'hq_ocr': (fields.get('hq_ocr') or '1').strip(),
                        'file_content_base64': base64.b64encode(upload['bytes']).decode('ascii'),
                    }]
            else:
                data = parse_body(environ)
                files_data = data.get('files') or []
                batch_identities = data.get('batch_identities') or []
                source_mode = (data.get('source_mode') or 'CUSTOMER_UPLOADS').strip().upper()
                # Propagate top-level document_type onto each file when not set per-file.
                top_type = (data.get('document_type') or '').strip()
                if top_type:
                    for item in files_data:
                        if isinstance(item, dict) and not item.get('document_type'):
                            item['document_type'] = top_type
                for item in files_data:
                    if isinstance(item, dict) and item.get('hq_ocr') is None:
                        item['hq_ocr'] = data.get('hq_ocr', '1')
            payload, err_status = process_smart_intake_files(
                files_data, user,
                batch_identities=batch_identities,
                source_mode=source_mode,
            )
            if err_status:
                return json_response(start_response, payload, err_status)
            return json_response(start_response, payload)
        except Exception as err:
            return json_response(start_response, {
                'status': 'error',
                'message': f'Smart intake failed: {err}',
            }, "500 Internal Server Error")

    if path == '/api/admin/documents/intake/link-company' and method == 'POST':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        try:
            data = parse_body(environ)
            client_id = data.get('client_id')
            company_id = data.get('company_id')
            company_number = normalize_company_number(data.get('company_number'))
            document_ids = data.get('document_ids') or []
            if not client_id:
                return json_response(start_response, {
                    'status': 'error',
                    'message': 'client_id is required',
                }, "400 Bad Request")
            company_obj = None
            if company_id:
                company_obj = query_db("SELECT * FROM companies WHERE id = ?;", (int(company_id),), one=True)
                if company_obj and client_id and not company_obj.get('user_id'):
                    execute_db("UPDATE companies SET user_id = ? WHERE id = ?;", (int(client_id), company_obj['id']))
                    company_obj = query_db("SELECT * FROM companies WHERE id = ?;", (company_obj['id'],), one=True)
            if not company_obj:
                if not company_number:
                    return json_response(start_response, {
                        'status': 'error',
                        'message': 'company_number or company_id is required',
                    }, "400 Bad Request")
                company_obj = auto_import_companies_house_from_intake(
                    data.get('company_name'),
                    company_number,
                    client_user_id=int(client_id),
                )
            if not company_obj:
                return json_response(start_response, {
                    'status': 'error',
                    'message': f'Could not import Companies House company #{company_number}',
                }, "404 Not Found")
            linked = 0
            for raw_id in document_ids:
                try:
                    doc_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                row = query_db("SELECT id, user_id FROM documents WHERE id = ?;", (doc_id,), one=True)
                if not row or int(row['user_id']) != int(client_id):
                    continue
                execute_db("UPDATE documents SET company_id = ? WHERE id = ?;", (company_obj['id'], doc_id))
                linked += 1
            display_num = (
                company_obj.get('companies_house_number')
                or company_obj.get('company_number')
                or company_number
                or ''
            )
            log_activity(
                user,
                'SMART_INTAKE_LINK',
                'companies',
                str(company_obj['id']),
                f"Linked intake docs to company #{company_obj['id']} for client #{client_id}",
            )
            return json_response(start_response, {
                'status': 'success',
                'message': f"Linked to {company_obj.get('name')} (#{display_num})",
                'company': {
                    'id': company_obj['id'],
                    'name': company_obj.get('name'),
                    'company_number': display_num,
                },
                'documents_linked': linked,
            })
        except Exception as err:
            return json_response(start_response, {
                'status': 'error',
                'message': f'Could not link company: {err}',
            }, "500 Internal Server Error")

    # Document Lifecycle & AI Triage Approval Endpoints
    if path == '/api/admin/documents/lifecycle' and method == 'GET':
        denied = require_permission(start_response, user, 'documents.view')
        if denied:
            return denied
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        stage = (qs.get('stage') or [''])[0].strip().upper()

        valid_stages = ('CUSTOMER_UPLOADS', 'PROCESSING', 'REVIEW_REQUIRED', 'READY_FOR_APPROVAL', 'POSTED_DOCUMENTS', 'QUARANTINE')
        if stage and stage in valid_stages:
            if stage == 'POSTED_DOCUMENTS':
                docs = query_db("SELECT d.*, u.full_name as client_name, u.email as client_email, c.name as company_name, c.company_number FROM documents d LEFT JOIN users u ON d.user_id = u.id LEFT JOIN companies c ON d.company_id = c.id WHERE d.is_posted = 1 ORDER BY d.id DESC LIMIT 200;")
            else:
                docs = query_db("SELECT d.*, u.full_name as client_name, u.email as client_email, c.name as company_name, c.company_number FROM documents d LEFT JOIN users u ON d.user_id = u.id LEFT JOIN companies c ON d.company_id = c.id WHERE d.lifecycle_status = ? AND COALESCE(d.is_posted, 0) = 0 ORDER BY d.id DESC LIMIT 200;", (stage,))
        else:
            docs = query_db("SELECT d.*, u.full_name as client_name, u.email as client_email, c.name as company_name, c.company_number FROM documents d LEFT JOIN users u ON d.user_id = u.id LEFT JOIN companies c ON d.company_id = c.id ORDER BY d.id DESC LIMIT 200;")

        counts = {
            'CUSTOMER_UPLOADS': query_db("SELECT COUNT(*) as c FROM documents WHERE lifecycle_status = 'CUSTOMER_UPLOADS' AND COALESCE(is_posted, 0) = 0;", one=True)['c'],
            'PROCESSING': query_db("SELECT COUNT(*) as c FROM documents WHERE lifecycle_status = 'PROCESSING' AND COALESCE(is_posted, 0) = 0;", one=True)['c'],
            'REVIEW_REQUIRED': query_db("SELECT COUNT(*) as c FROM documents WHERE lifecycle_status = 'REVIEW_REQUIRED' AND COALESCE(is_posted, 0) = 0;", one=True)['c'],
            'READY_FOR_APPROVAL': query_db("SELECT COUNT(*) as c FROM documents WHERE lifecycle_status = 'READY_FOR_APPROVAL' AND COALESCE(is_posted, 0) = 0;", one=True)['c'],
            'POSTED_DOCUMENTS': query_db("SELECT COUNT(*) as c FROM documents WHERE is_posted = 1 OR lifecycle_status = 'POSTED_DOCUMENTS';", one=True)['c'],
            'QUARANTINE': query_db("SELECT COUNT(*) as c FROM documents WHERE lifecycle_status = 'QUARANTINE';", one=True)['c'],
        }

        return json_response(start_response, {
            'status': 'success',
            'counts': counts,
            'documents': [public_document(d) for d in (docs or [])]
        })

    m_appr = re.match(r'^/api/admin/documents/(\d+)/approve$', path)
    if m_appr and method == 'POST':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        doc_id = int(m_appr.group(1))
        doc = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
        if not doc:
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")

        # Complete company data → ensure it appears on the Companies list by name/number.
        linked_company, link_status = ensure_company_for_document(doc, actor=user, promote_lifecycle=False)
        doc = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True) or doc

        conn = get_db()
        try:
            conn.execute("""
                UPDATE documents SET
                    lifecycle_status = 'POSTED_DOCUMENTS',
                    is_posted = 1,
                    status = 'Approved',
                    approved_at = CURRENT_TIMESTAMP,
                    approved_by_id = ?,
                    posted_at = CURRENT_TIMESTAMP,
                    posted_by_id = ?
                WHERE id = ?;
            """, (user['id'], user['id'], doc_id))
            conn.execute("""
                INSERT INTO document_audit_log (document_id, actor_type, actor_id, actor_name, action, previous_state, new_state, reason_evidence_json)
                VALUES (?, 'HUMAN', ?, ?, 'POST_APPROVED', ?, 'POSTED_DOCUMENTS', ?);
            """, (
                doc_id, user['id'], user['full_name'], doc.get('lifecycle_status'),
                json.dumps({
                    'approved_by': user['full_name'],
                    'company_link': link_status,
                    'company_id': (linked_company or {}).get('id'),
                    'company_name': (linked_company or {}).get('name'),
                }),
            ))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return json_response(start_response, {'status': 'error', 'message': f'Atomic posting failed: {exc}'}, "500 Internal Server Error")
        finally:
            conn.close()

        updated = query_db("SELECT d.*, u.full_name as client_name, c.name as company_name FROM documents d LEFT JOIN users u ON d.user_id = u.id LEFT JOIN companies c ON d.company_id = c.id WHERE d.id = ?;", (doc_id,), one=True)
        return json_response(start_response, {
            'status': 'success',
            'message': f"Document '{doc['name']}' approved and posted atomically.",
            'document': public_document(updated),
            'company': {
                'id': (linked_company or {}).get('id'),
                'name': (linked_company or {}).get('name'),
                'company_number': (linked_company or {}).get('company_number'),
                'link_status': link_status,
            } if linked_company else None,
        })

    m_rej = re.match(r'^/api/admin/documents/(\d+)/reject$', path)
    if m_rej and method == 'POST':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        doc_id = int(m_rej.group(1))
        doc = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
        if not doc:
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")
        body = parse_body(environ) or {}
        reason = (body.get('reason') or 'Rejected by staff').strip()
        execute_db(
            "UPDATE documents SET lifecycle_status = 'QUARANTINE', is_posted = 0, status = 'Rejected', review_notes = ? WHERE id = ?;",
            (reason, doc_id),
        )
        log_document_audit(
            doc_id, 'HUMAN', user['id'], user['full_name'], 'REJECT_QUARANTINE',
            doc.get('lifecycle_status'), 'QUARANTINE', {'reason': reason},
        )
        return json_response(start_response, {'status': 'success', 'message': 'Document moved to Quarantine.'})

    m_reassign = re.match(r'^/api/admin/documents/(\d+)/reassign$', path)
    if m_reassign and method == 'POST':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        doc_id = int(m_reassign.group(1))
        doc = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
        if not doc:
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")
        data = parse_body(environ) or {}
        new_client_id = optional_record_id(data.get('client_id'))
        new_company_id = optional_record_id(data.get('company_id'))
        new_category = (data.get('category') or '').strip() or None
        prev = doc.get('lifecycle_status')
        if new_client_id:
            client_ok = query_db("SELECT id FROM users WHERE id = ?;", (new_client_id,), one=True)
            if not client_ok:
                return json_response(start_response, {'status': 'error', 'message': 'Client not found'}, "404 Not Found")
        if new_company_id:
            company_ok = query_db("SELECT id, user_id FROM companies WHERE id = ?;", (new_company_id,), one=True)
            if not company_ok:
                return json_response(start_response, {'status': 'error', 'message': 'Company not found'}, "404 Not Found")
            if new_client_id and int(company_ok['user_id']) != int(new_client_id):
                return json_response(start_response, {'status': 'error', 'message': 'Company does not belong to client'}, "400 Bad Request")
        execute_db(
            """
            UPDATE documents SET
                user_id = COALESCE(?, user_id),
                company_id = COALESCE(?, company_id),
                category = COALESCE(?, category),
                lifecycle_status = 'READY_FOR_APPROVAL',
                is_posted = 0,
                matched_at = CURRENT_TIMESTAMP,
                matched_by_id = ?,
                review_notes = COALESCE(?, review_notes)
            WHERE id = ?;
            """,
            (
                new_client_id, new_company_id, new_category, user['id'],
                (data.get('review_notes') or None), doc_id,
            ),
        )
        log_document_audit(
            doc_id, 'HUMAN', user['id'], user['full_name'], 'REASSIGNED',
            prev, 'READY_FOR_APPROVAL', {
                'client_id': new_client_id,
                'company_id': new_company_id,
                'category': new_category,
            },
        )
        # Optional reprocess OCR match
        if data.get('reprocess'):
            file_path = doc.get('file_path')
            abs_path = file_path if os.path.isabs(file_path or '') else os.path.join(BASE_DIR, file_path or '')
            if abs_path and os.path.isfile(abs_path):
                with open(abs_path, 'rb') as fh:
                    fb = fh.read()
                triage = triage_uploaded_document(
                    fb, doc.get('name') or 'document', doc.get('user_id'),
                    client_id=new_client_id or doc.get('user_id'),
                    actor=user, run_company_match=True,
                )
                if new_company_id:
                    triage['company_id'] = new_company_id
                apply_triage_to_document_row(doc_id, triage, actor=user, previous_state='READY_FOR_APPROVAL')
        updated = query_db(
            "SELECT d.*, u.full_name as client_name, c.name as company_name FROM documents d LEFT JOIN users u ON d.user_id = u.id LEFT JOIN companies c ON d.company_id = c.id WHERE d.id = ?;",
            (doc_id,), one=True,
        )
        return json_response(start_response, {
            'status': 'success',
            'message': 'Document reassigned.',
            'document': public_document(updated),
        })

    if path == '/api/admin/documents/promote-to-companies' and method == 'POST':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        data = parse_body(environ) or {}
        doc_ids = data.get('document_ids') or []
        include_all_complete = bool(data.get('all_complete'))

        if doc_ids:
            placeholders = ','.join('?' for _ in doc_ids)
            docs = query_db(
                f"SELECT * FROM documents WHERE id IN ({placeholders}) ORDER BY id ASC;",
                tuple(int(x) for x in doc_ids),
            ) or []
        elif include_all_complete:
            docs = query_db(
                """
                SELECT * FROM documents
                WHERE COALESCE(is_posted, 0) = 0
                  AND COALESCE(lifecycle_status, '') IN (
                      'READY_FOR_APPROVAL', 'REVIEW_REQUIRED', 'QUARANTINE',
                      'CUSTOMER_UPLOADS', 'PROCESSING'
                  )
                ORDER BY id ASC
                LIMIT 500;
                """
            ) or []
        else:
            return json_response(
                start_response,
                {'status': 'error', 'message': 'Provide document_ids or all_complete=true'},
                "400 Bad Request",
            )

        linked = []
        skipped = []
        failed = []
        companies_seen = {}
        for doc in docs:
            num, name, source = document_complete_company_signals(doc)
            if not num and not name and not doc.get('company_id'):
                skipped.append({'id': doc.get('id'), 'name': doc.get('name'), 'reason': 'incomplete'})
                continue
            try:
                company, status = ensure_company_for_document(doc, actor=user, promote_lifecycle=True)
            except Exception as exc:
                failed.append({'id': doc.get('id'), 'name': doc.get('name'), 'error': str(exc)})
                continue
            if not company:
                skipped.append({'id': doc.get('id'), 'name': doc.get('name'), 'reason': status})
                continue
            companies_seen[company['id']] = {
                'id': company['id'],
                'name': company.get('name'),
                'company_number': company.get('company_number'),
            }
            linked.append({
                'document_id': doc.get('id'),
                'document_name': doc.get('name'),
                'status': status,
                'source': source,
                'company_id': company.get('id'),
                'company_name': company.get('name'),
                'company_number': company.get('company_number'),
            })

        return json_response(start_response, {
            'status': 'success',
            'message': (
                f"Added {len(companies_seen)} companies from {len(linked)} documents "
                f"({len(skipped)} skipped, {len(failed)} failed)."
            ),
            'linked_count': len(linked),
            'skipped_count': len(skipped),
            'failed_count': len(failed),
            'companies': list(companies_seen.values()),
            'linked': linked,
            'skipped': skipped[:50],
            'failed': failed[:50],
        })

    if path == '/api/admin/documents/bulk-approve' and method == 'POST':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        data = parse_body(environ)
        doc_ids = data.get('document_ids') or []
        override = bool(data.get('override_review_required'))
        if not doc_ids:
            return json_response(start_response, {'status': 'error', 'message': 'No document_ids provided'}, "400 Bad Request")

        job_id = f"job_{uuid.uuid4().hex[:12]}"

        def run_bulk_approval_job():
            approved = 0
            failed = 0
            skipped = 0
            errors = []
            for did in doc_ids:
                d = query_db("SELECT * FROM documents WHERE id = ?;", (did,), one=True)
                if not d:
                    skipped += 1
                    continue
                # Default: ONLY READY_FOR_APPROVAL. Override required for anything else.
                if d.get('lifecycle_status') == 'READY_FOR_APPROVAL' or (
                    override and d.get('lifecycle_status') in (
                        'REVIEW_REQUIRED', 'CUSTOMER_UPLOADS', 'PROCESSING', 'READY_FOR_APPROVAL'
                    )
                ):
                    try:
                        ensure_company_for_document(d, actor=user, promote_lifecycle=False)
                    except Exception as link_exc:
                        errors.append(f"Doc #{did} company link: {link_exc}")
                    conn = get_db()
                    try:
                        conn.execute(
                            """
                            UPDATE documents SET lifecycle_status = 'POSTED_DOCUMENTS', is_posted = 1,
                                status = 'Approved', approved_at = CURRENT_TIMESTAMP, posted_at = CURRENT_TIMESTAMP,
                                approved_by_id = ?, posted_by_id = ?
                            WHERE id = ?;
                            """,
                            (user['id'], user['id'], did),
                        )
                        conn.execute(
                            "INSERT INTO document_audit_log (document_id, actor_type, actor_id, actor_name, action, previous_state, new_state) VALUES (?, 'HUMAN', ?, ?, 'BULK_APPROVED', ?, 'POSTED_DOCUMENTS');",
                            (did, user['id'], user['full_name'], d.get('lifecycle_status')),
                        )
                        conn.commit()
                        approved += 1
                    except Exception as e:
                        conn.rollback()
                        failed += 1
                        errors.append(f"Doc #{did}: {e}")
                    finally:
                        conn.close()
                else:
                    skipped += 1

            execute_db("""
                UPDATE bulk_approval_jobs SET status = 'COMPLETED', approved_count = ?, failed_count = ?, skipped_count = ?, errors_json = ?, completed_at = CURRENT_TIMESTAMP WHERE job_id = ?;
            """, (approved, failed, skipped, json.dumps(errors), job_id))

        execute_db("""
            INSERT INTO bulk_approval_jobs (job_id, user_id, total_count, status) VALUES (?, ?, ?, 'PROCESSING');
        """, (job_id, user['id'], len(doc_ids)))

        BULK_JOB_POOL.submit(run_bulk_approval_job)

        return json_response(start_response, {
            'status': 'success',
            'job_id': job_id,
            'total': len(doc_ids),
            'message': f"Bulk approval job '{job_id}' started for {len(doc_ids)} documents."
        })

    if path == '/api/admin/documents/bulk-quarantine' and method == 'POST':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        data = parse_body(environ) or {}
        doc_ids = data.get('document_ids') or []
        reason = (data.get('reason') or 'Bulk quarantined by staff').strip()
        moved = 0
        for did in doc_ids:
            d = query_db("SELECT id, lifecycle_status FROM documents WHERE id = ?;", (did,), one=True)
            if not d or d.get('lifecycle_status') == 'POSTED_DOCUMENTS':
                continue
            execute_db(
                "UPDATE documents SET lifecycle_status = 'QUARANTINE', is_posted = 0, status = 'Rejected', review_notes = ? WHERE id = ?;",
                (reason, did),
            )
            log_document_audit(
                did, 'HUMAN', user['id'], user['full_name'], 'BULK_QUARANTINE',
                d.get('lifecycle_status'), 'QUARANTINE', {'reason': reason},
            )
            moved += 1
        return json_response(start_response, {
            'status': 'success',
            'moved': moved,
            'message': f'Moved {moved} document(s) to Quarantine.',
        })

    m_job = re.match(r'^/api/admin/documents/bulk-jobs/([a-zA-Z0-9_\-]+)$', path)
    if m_job and method == 'GET':
        denied = require_permission(start_response, user, 'documents.view')
        if denied:
            return denied
        job_id = m_job.group(1)
        job = query_db("SELECT * FROM bulk_approval_jobs WHERE job_id = ?;", (job_id,), one=True)
        if not job:
            return json_response(start_response, {'status': 'error', 'message': 'Job not found'}, "404 Not Found")
        return json_response(start_response, {'status': 'success', 'job': job})

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
            company_id=doc.get('company_id'),
            order_number=doc.get('order_number'),
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

    m_doc_del = re.match(r'^/api/admin/documents/(\d+)$', path)
    if m_doc_del and method == 'DELETE':
        denied = require_permission(start_response, user, 'documents.upload')
        if denied:
            return denied
        try:
            doc_id = int(m_doc_del.group(1))
        except (TypeError, ValueError):
            return json_response(start_response, {'status': 'error', 'message': 'Document not found'}, "404 Not Found")
        doc, error = delete_document_record(doc_id, actor=user)
        if error:
            status_code = "404 Not Found" if error == 'Document not found' else "400 Bad Request"
            return json_response(start_response, {'status': 'error', 'message': error}, status_code)
        return json_response(start_response, {
            'status': 'success',
            'message': f"Deleted {doc.get('name') or 'document'}.",
            'document_id': doc_id,
        })

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
        elif role in INTERNAL_STAFF_ROLES and not departments:
            departments = list(STAFF_DEPARTMENTS)
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
        client_type = (data.get('client_type') or '').strip().lower()
        if role == 'CLIENT':
            if client_type == 'b2c' or data.get('is_b2b') == 0 or data.get('is_b2b') == '0':
                is_b2b_val = 0
                acct_type_val = 'Normal Client (Standard Website Account)'
                success_msg = 'Normal Client account created for Brixen Official Website. They can sign in with this email and password.'
            else:
                is_b2b_val = 1
                acct_type_val = 'B2B Client (Brixen Website Panel)'
                success_msg = 'B2B Client account created for Brixen Official Website Client Panel. They can sign in with this email and password.'
        else:
            is_b2b_val = 0
            acct_type_val = 'Internal Staff'
            success_msg = 'User created. They can sign in with this email and password.'

        user_id = execute_db("""
            INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, country, role, status, department, is_b2b, account_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (local_id, email, hash_password(password), full_name, phone, country, role, status_val, department, is_b2b_val, acct_type_val))
        created = query_db("""
            SELECT id, wordpress_user_id, email, full_name, phone, country, role, department, status, avatar_url, created_at, is_b2b, account_type
            FROM users WHERE id = ?;
        """, (user_id,), one=True)
        log_activity(user, 'USER_CREATED', 'users', str(user_id), f"Created user {email} ({role} - {acct_type_val})")
        public_user = public_staff_user(created)
        return json_response(start_response, {
            'status': 'success',
            'message': success_msg,
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

    if path == '/api/admin/tasks/incentives-report' and method == 'GET':
        denied = task_auth_error(start_response, user, 'tasks.view')
        if denied:
            return denied
        qs = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        selected_month = qs.get('month', [None])[0] or datetime.datetime.now().strftime('%Y-%m')
        
        staff_rows = query_db("SELECT * FROM users WHERE role IN ('STAFF', 'MANAGER', 'ADMIN', 'SUPER_ADMIN') AND status = 'Active' ORDER BY full_name ASC;") or []
        report = []
        for s in staff_rows:
            sid = s['id']
            assigned_c = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND strftime('%Y-%m', created_at) = ?;", (sid, selected_month), one=True)['c']
            completed_c = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status = 'Completed' AND strftime('%Y-%m', completed_at) = ?;", (sid, selected_month), one=True)['c']
            on_time_c = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status = 'Completed' AND strftime('%Y-%m', completed_at) = ? AND (due_date IS NULL OR date(completed_at) <= date(due_date));", (sid, selected_month), one=True)['c']
            overdue_c = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status NOT IN ('Completed', 'Cancelled') AND due_date IS NOT NULL AND date(due_date) < date('now');", (sid,), one=True)['c']
            pending_c = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status = 'Pending';", (sid,), one=True)['c']
            in_prog_c = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status = 'In Progress';", (sid,), one=True)['c']

            grade_info = calculate_staff_incentive_grade(completed_c, assigned_c, on_time_c, overdue_c)
            report.append({
                'staff': public_staff_user(s),
                'assigned_count': assigned_c,
                'completed_count': completed_c,
                'on_time_count': on_time_c,
                'overdue_count': overdue_c,
                'pending_count': pending_c,
                'in_progress_count': in_prog_c,
                'grade': grade_info['grade'],
                'grade_label': grade_info['status_label'],
                'bonus_badge': grade_info['bonus_badge'],
                'completion_rate': grade_info['completion_rate'],
                'sla_rate': grade_info['sla_rate']
            })
            
        grade_order = {'A+': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4}
        report.sort(key=lambda r: (grade_order.get(r['grade'], 5), -r['completed_count']))

        return json_response(start_response, {
            'status': 'success',
            'month': selected_month,
            'leaderboard': report
        })

    if path.startswith('/api/admin/tasks/') and path.endswith('/audit-logs') and method == 'GET':
        denied = task_auth_error(start_response, user, 'tasks.view')
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

        logs = query_db("""
            SELECT a.*, u.full_name AS actor_name, u.role AS actor_role
            FROM activity_logs a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE a.entity_type = 'tasks' AND a.entity_id = ?
            ORDER BY a.created_at DESC;
        """, (str(task_id),)) or []
        
        return json_response(start_response, {'status': 'success', 'task_id': task_id, 'audit_logs': [dict(l) for l in logs]})

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
            log_activity(user, 'TASK_REASSIGNED', 'tasks', str(task_id), f"Reassigned task '{title}' to staff ID #{assigned_staff_id}")
        if status_val != task['status']:
            log_activity(user, 'TASK_STATUS_UPDATED', 'tasks', str(task_id), f"Status changed from '{task['status']}' to '{status_val}'")
        updated = fetch_task(task_id)
        return json_response(start_response, {'status': 'success', 'message': 'Task updated successfully', 'task': dict(updated)})

    if path == '/api/staff/my-dashboard' and method == 'GET':
        if not user or user.get('role') not in ('STAFF', 'MANAGER', 'ADMIN', 'SUPER_ADMIN'):
            return json_response(start_response, {'status': 'error', 'message': 'Access denied to staff dashboard'}, "403 Forbidden")
        
        staff_id = user['id']
        pending_count = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status = 'Pending';", (staff_id,), one=True)['c']
        in_progress_count = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status = 'In Progress';", (staff_id,), one=True)['c']
        completed_month = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status = 'Completed' AND strftime('%Y-%m', completed_at) = strftime('%Y-%m', 'now');", (staff_id,), one=True)['c']
        completed_total = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status = 'Completed';", (staff_id,), one=True)['c']
        overdue_count = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status NOT IN ('Completed', 'Cancelled') AND due_date IS NOT NULL AND date(due_date) < date('now');", (staff_id,), one=True)['c']
        total_assigned_month = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now');", (staff_id,), one=True)['c']
        on_time_month = query_db("SELECT COUNT(*) AS c FROM tasks WHERE assigned_staff_id = ? AND status = 'Completed' AND strftime('%Y-%m', completed_at) = strftime('%Y-%m', 'now') AND (due_date IS NULL OR date(completed_at) <= date(due_date));", (staff_id,), one=True)['c']

        grade_info = calculate_staff_incentive_grade(completed_month, total_assigned_month, on_time_month, overdue_count)

        my_tasks_sql = TASK_DETAIL_SQL + " WHERE t.assigned_staff_id = ? AND t.status NOT IN ('Completed', 'Cancelled') ORDER BY CASE WHEN t.due_date IS NOT NULL AND date(t.due_date) < date('now') THEN 1 ELSE 2 END, t.created_at DESC LIMIT 20;"
        active_tasks = [dict(r) for r in (query_db(my_tasks_sql, (staff_id,)) or [])]

        return json_response(start_response, {
            'status': 'success',
            'staff': public_staff_user(user),
            'metrics': {
                'pending_tasks': pending_count,
                'in_progress_tasks': in_progress_count,
                'completed_this_month': completed_month,
                'completed_all_time': completed_total,
                'overdue_tasks': overdue_count,
                'total_assigned_month': total_assigned_month,
                'on_time_month': on_time_month,
                'grade': grade_info['grade'],
                'grade_label': grade_info['status_label'],
                'bonus_badge': grade_info['bonus_badge'],
                'completion_rate': grade_info['completion_rate'],
                'sla_rate': grade_info['sla_rate']
            },
            'my_tasks': active_tasks
        })



    if path == '/api/admin/settings' and method == 'GET':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        rows = query_db("SELECT key, value FROM settings;") or []
        allowed = {
            'company_name', 'support_email', 'currency', 'order_prefix', 'invoice_prefix',
            'support_phone', 'logo_url', 'smtp_host', 'smtp_port', 'smtp_user', 'smtp_from',
            'uk_formfill_pro_url', 'team_notification_emails',
            'compliance_alerts_enabled', 'compliance_alerts_send_hour', 'compliance_alerts_timezone',
            'compliance_alerts_interval_hours', 'compliance_alerts_verified_only',
            'compliance_alerts_whatsapp_enabled', 'compliance_alerts_test_mode',
            'compliance_alerts_test_recipient', 'compliance_alerts_website', 'compliance_alerts_whatsapp_url',
        }
        settings_dict = {row['key']: row['value'] for row in rows if row['key'] in allowed}
        settings_dict['companies_house_configured'] = bool(companies_house_api_key())
        settings_dict['smtp_configured'] = smtp_configured()
        if not settings_dict.get('uk_formfill_pro_url'):
            settings_dict['uk_formfill_pro_url'] = uk_formfill_pro_url()
        if 'team_notification_emails' not in settings_dict:
            settings_dict['team_notification_emails'] = ''
        try:
            import compliance_alerts as _ca
            _ca.ensure_compliance_alert_settings()
            settings_dict['compliance_alerts'] = _ca.compliance_alert_settings()
        except Exception:
            settings_dict['compliance_alerts'] = {}
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
        template = str(data.get('template') or 'document').strip().lower()
        if template in ('congratulations', 'company-registered', 'registration'):
            _, test_text, test_html = build_client_notification_email(
                {'full_name': 'Brixen Team', 'email': recipient},
                'Example Holdings Ltd',
                "We've got great news, Example Holdings Ltd is now officially registered with Companies House.",
                subject='Congratulations Example Holdings Ltd is now registered with Companies House',
                greeting_name='Alex Director',
                badge='Company registered',
                alert_label='Great news',
                layout='celebration',
                detail_title='Company number',
                detail_value='12345678',
                cta_label='Download now',
                cta_url=companies_house_filing_history_url('12345678'),
                footer_note='This email is about your UK company registration with Brixen Consultants.',
            )
            test_subject = 'Brixen test: company registration congratulations'
        else:
            _, test_text, test_html = build_client_document_email(
                {'full_name': 'Test Recipient', 'email': recipient},
                'Sample_Client_Document.pdf',
                'This is a preview of the branded customer notification email.',
            )
            test_subject = 'Brixen test: premium document notification preview'
        sent, status = EmailService.send_notification_email(
            recipient,
            test_subject,
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
        data = parse_body(environ) or {}
        writable = {
            'company_name', 'support_email', 'currency', 'order_prefix', 'invoice_prefix',
            'support_phone', 'logo_url', 'smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from',
            'uk_formfill_pro_url', 'team_notification_emails', 'companies_house_api_key',
            'compliance_alerts_enabled', 'compliance_alerts_send_hour', 'compliance_alerts_timezone',
            'compliance_alerts_interval_hours', 'compliance_alerts_verified_only',
            'compliance_alerts_whatsapp_enabled', 'compliance_alerts_test_mode',
            'compliance_alerts_test_recipient', 'compliance_alerts_website', 'compliance_alerts_whatsapp_url',
            'rbac_audit_probe',
        }
        for k, v in data.items():
            if k not in writable:
                continue
            if k == 'companies_house_api_key' and not str(v or '').strip():
                continue
            if k == 'smtp_pass' and not str(v or '').strip():
                continue
            if k == 'team_notification_emails':
                cleaned = ', '.join(parse_team_notification_emails(v))
                execute_db("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);", (k, cleaned))
                continue
            if k in (
                'compliance_alerts_enabled',
                'compliance_alerts_verified_only',
                'compliance_alerts_whatsapp_enabled',
                'compliance_alerts_test_mode',
            ):
                vv = str(v).strip().lower()
                v = '1' if vv in ('1', 'true', 'yes', 'on') else '0'
            execute_db("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);", (k, str(v)))
        log_activity(user, 'SETTINGS_UPDATE', 'settings', '1', 'Updated admin system settings')
        return json_response(start_response, {
            'status': 'success',
            'message': 'System settings saved.',
            'smtp_configured': smtp_configured(),
        })

    if path == '/api/admin/compliance-alerts/test-rmk' and method == 'POST':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        if user.get('role') not in ('SUPER_ADMIN', 'ADMIN'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        data = parse_body(environ) or {}
        import compliance_alerts as _ca
        result = _ca.send_rmk_trading_test_email(
            actor=user,
            recipient_override=(data.get('email') or data.get('recipient') or '').strip() or None,
        )
        if not result.get('ok'):
            msg = result.get('message') or result.get('blocked_reason') or 'Test email blocked'
            return json_response(start_response, {'status': 'error', 'message': msg, **result}, "400 Bad Request")
        log_activity(user, 'COMPLIANCE_TEST_EMAIL', 'companies', str((result.get('company') or {}).get('id') or ''), 'RMK TRADING compliance test email')
        return json_response(start_response, {'status': 'success', 'message': 'Test compliance email sent.', **result})

    if path == '/api/admin/compliance-alerts/run' and method == 'POST':
        denied = require_permission(start_response, user, 'settings.manage')
        if denied:
            return denied
        if user.get('role') not in ('SUPER_ADMIN', 'ADMIN'):
            return json_response(start_response, {'status': 'error', 'message': 'Insufficient permissions'}, "403 Forbidden")
        import compliance_alerts as _ca
        summary = _ca.run_daily_compliance_alert_pass(force=True)
        log_activity(user, 'COMPLIANCE_ALERT_PASS', 'settings', '1', f"Manual compliance pass sent={summary.get('sent')}")
        return json_response(start_response, {'status': 'success', 'summary': summary})

    start_response("404 Not Found", [('Content-Type', 'application/json')])
    return [json.dumps({'status': 'error', 'message': f'Route {path} not found'}).encode('utf-8')]

def run():
    global PORT
    port_to_try = PORT
    for attempt in range(5):
        try:
            print(f"Hypetex Limited Server starting on http://{HOST}:{port_to_try}")
            httpd = make_server(HOST, port_to_try, application, server_class=ThreadingWSGIServer)
            httpd.serve_forever()
            break
        except OSError as e:
            if getattr(e, 'errno', None) == 48 or 'Address already in use' in str(e):
                print(f"Port {port_to_try} busy, trying port {port_to_try + 1}...")
                port_to_try += 1
            else:
                raise e

start_registration_notice_worker()
try:
    import compliance_alerts as _compliance_alerts_boot
    _compliance_alerts_boot.ensure_compliance_alert_settings()
    _compliance_alerts_boot.start_compliance_alert_worker()
except Exception as _boot_err:
    print(f"[ComplianceAlerts] Boot failed: {_boot_err}")

if __name__ == '__main__':
    run()
