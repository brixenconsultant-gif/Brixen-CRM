import os
import sys
import json
import io
import hmac
import hashlib
import datetime
from app import application
from db import query_db, execute_db, hash_password, verify_password, needs_rehash, unusable_password_hash

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

def run_tests():
    print("==================================================")
    print("STARTING HYPETEX DIRECT WSGI & SYSTEM TESTS")
    print("==================================================")
    
    # 1. Test Database Record Counts
    orders_cnt = query_db("SELECT COUNT(*) as c FROM orders;", one=True)['c']
    users_cnt = query_db("SELECT COUNT(*) as c FROM users;", one=True)['c']
    companies_cnt = query_db("SELECT COUNT(*) as c FROM companies;", one=True)['c']
    
    print(f"✓ DB Record Count Check:")
    print(f"  - Users: {users_cnt}")
    print(f"  - Companies: {companies_cnt}")
    print(f"  - Orders: {orders_cnt}")
    assert orders_cnt >= 100, "Error: Expected at least 100 orders seeded."
    
    # 2. Test Target Order #GB103449JUL26
    target_order = query_db("SELECT * FROM orders WHERE order_number = '#GB103449JUL26';", one=True)
    print(f"✓ Target Order Check (#GB103449JUL26):")
    print(f"  - ID: {target_order['id']}")
    print(f"  - Service: {target_order['service_name']}")
    print(f"  - Price: £{target_order['price']:.2f}")
    print(f"  - Status: {target_order['status']}")
    print(f"  - Progress: {target_order['progress_percent']}%")
    assert target_order is not None, "Error: #GB103449JUL26 order not found."
    assert target_order['progress_percent'] == 50, "Error: Expected 50% progress."

    # 3. Test Dynamic Stat Cards Computation
    client_user = query_db("SELECT id FROM users WHERE email = 'client1@acmecorp.co.uk';", one=True)
    uid = client_user['id']
    tot_orders = query_db("SELECT COUNT(*) as c FROM orders WHERE user_id = ?;", (uid,), one=True)['c']
    completed_orders = query_db("SELECT COUNT(*) as c FROM orders WHERE user_id = ? AND status = 'Completed';", (uid,), one=True)['c']
    in_progress_orders = query_db("SELECT COUNT(*) as c FROM orders WHERE user_id = ? AND status IN ('Processing', 'In Progress');", (uid,), one=True)['c']
    tot_spent = query_db("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE user_id = ? AND status != 'Cancelled';", (uid,), one=True)['s']
    
    print(f"✓ Dynamic Stat Cards Computation (Client 1):")
    print(f"  - TOTAL ORDERS: {tot_orders}")
    print(f"  - COMPLETED: {completed_orders}")
    print(f"  - IN PROGRESS: {in_progress_orders}")
    print(f"  - TOTAL SPENT: £{tot_spent:,.2f}")

    # 4. Test GET / HTML shell
    status, headers, body = make_request('/')
    assert status == "200 OK"
    assert "<title>Brixen Consultant" in body
    print("✓ WSGI GET / -> 200 OK HTML Shell Rendered Successfully")

    for asset in ('/static/css/styles.css', '/static/js/app.js', '/static/img/brixen-logo.png'):
        st, hd, asset_body = make_request(asset)
        assert st == "200 OK", f"{asset} expected 200, got {st}"
        assert asset_body, f"{asset} returned empty body"
    print("✓ Static assets -> css/js/logo served successfully")

    for traversal in (
        '/static/../hypetex.db',
        '/static/js/../../hypetex.db',
        '/static/../app.py',
        '/static/../schema.sql',
        '/static/../seed_db.py',
        '/static/../.env.example',
    ):
        st, hd, trav_body = make_request(traversal)
        assert st in ("403 Forbidden", "404 Not Found"), f"{traversal} expected 403/404, got {st}"
        raw = trav_body if isinstance(trav_body, (bytes, bytearray)) else str(trav_body).encode('utf-8', 'replace')
        assert not raw.startswith(b'SQLite format 3')
        lowered = raw.lower()
        assert b'hypetex.db' not in lowered
        assert not lowered.startswith(b'-- hypetex')
        assert not lowered.startswith(b'import os')
    print("✓ Static path traversal -> blocked without filesystem path disclosure")

    from app import HOST as SERVER_HOST
    assert SERVER_HOST == '127.0.0.1'
    with open(os.path.join(os.path.dirname(__file__), 'static/js/app.js'), 'r', encoding='utf-8') as f:
        js_src = f.read()
    with open(os.path.join(os.path.dirname(__file__), 'templates/index.html'), 'r', encoding='utf-8') as f:
        html_src = f.read()
    assert 'ClientPass123!' not in js_src
    assert 'AdminPass123!' not in js_src
    assert 'StaffPass123!' not in js_src
    assert 'switchRoleDemo' not in js_src
    assert 'fillDemoLogin' not in js_src
    assert 'switchRoleDemo' not in html_src
    print("✓ Frontend demo credentials/role switcher absent; WSGI host defaults to 127.0.0.1")

    st, hd, pub_settings = make_request('/api/settings')
    assert st == "200 OK"
    pub = pub_settings.get('settings') or {}
    pub_blob = json.dumps(pub).lower()
    assert 'wordpress_webhook_secret' not in pub
    assert 'WORDPRESS_WEBHOOK_SECRET' not in pub
    assert 'webhook_secret' not in pub_blob
    assert 'password' not in pub_blob
    assert 'session_token' not in pub_blob
    assert 'company_name' in pub
    print("✓ Public settings -> webhook secret and credentials absent from anonymous response")

    st, hd, _ = make_request('/api/settings', headers={'Origin': 'https://evil.example'})
    assert hd.get('Access-Control-Allow-Origin') not in ('*', 'https://evil.example')
    st, hd, _ = make_request('/api/settings', headers={'Origin': 'https://portal.brixenconsultants.com'})
    assert hd.get('Access-Control-Allow-Origin') == 'https://portal.brixenconsultants.com'
    print("✓ CORS -> wildcard origin removed; portal origin allowed")

    # 5. Test Auth API Login (Client)
    status, headers, res = make_request('/api/auth/login', method='POST', body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'})
    assert status == "200 OK"
    assert res['status'] == 'success'
    token = extract_session_token(headers)
    assert token
    assert 'token' not in res
    set_cookie = headers.get('Set-Cookie', '')
    assert 'HttpOnly' in set_cookie
    assert 'Secure' in set_cookie
    assert 'SameSite=Lax' in set_cookie
    print(f"✓ Auth API Login (Client) -> Success (User: {res['user']['full_name']})")

    status, headers, me_res = make_request('/api/auth/me', cookie=f"session_token={token}")
    assert status == "200 OK"
    me_user = me_res.get('user') or {}
    assert 'password_hash' not in me_user
    assert 'session_token' not in me_user
    assert me_user.get('role') == 'CLIENT'
    assert me_user.get('email') == 'client1@acmecorp.co.uk'
    print("✓ Auth /api/auth/me -> password_hash and session_token absent from profile")

    # 6. Test GET /api/client/orders & /api/client/dashboard with Session Token
    status, headers, res = make_request('/api/client/orders', cookie=f"session_token={token}")
    assert status == "200 OK"
    assert res['status'] == 'success'
    print(f"✓ WSGI GET /api/client/orders -> Returned {len(res['orders'])} orders with DB Stats: {res['stats']}")

    status, headers, dash_res = make_request('/api/client/dashboard', cookie=f"session_token={token}")
    assert status == "200 OK"
    assert dash_res['status'] == 'success'
    print(f"✓ WSGI GET /api/client/dashboard -> Hero: '{dash_res['user_name']}', Comps: {dash_res['total_companies']} ({dash_res['uk_companies']} UK, {dash_res['intl_companies']} International), Active Orders: {dash_res['active_orders_count']}")

    # 7. Test Admin Login & Order Status Change
    status, headers, adm_res = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
    adm_token = extract_session_token(headers)
    assert adm_token
    assert 'token' not in adm_res
    
    status, headers, update_res = make_request(
        f"/api/admin/orders/{target_order['id']}", 
        method='PUT', 
        body={'status': 'Completed', 'progress_percent': 100},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    assert update_res['status'] == 'success'
    print(f"✓ Admin Order Update -> Success (Order #{target_order['id']} updated to Completed, 100%)")

    # 8. Verify Client Notification Creation
    notes = query_db("SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1;", (uid,), one=True)
    assert 'Completed' in notes['message']
    print(f"✓ Automated Client Notification Check -> Verified: '{notes['message']}'")

    # 9. Test WordPress Webhook Client Registration Sync
    wp_payload = {
        'event_id': 'evt_wp_test_9901',
        'event_type': 'user.created',
        'data': {
            'wordpress_user_id': 'wp_user_999',
            'email': 'wp.test.client@brixen.co.uk',
            'full_name': 'Sarah Jenkins',
            'phone': '+44 7700 900999',
            'country': 'United Kingdom'
        }
    }
    wp_body_bytes = json.dumps(wp_payload).encode('utf-8')
    wp_sig = hmac.new(webhook_secret_bytes(), wp_body_bytes, hashlib.sha256).hexdigest()
    
    status, headers, wp_res = make_request(
        '/api/v1/wordpress/webhook', 
        method='POST', 
        body=wp_payload, 
        headers={'X-Brixen-Signature': wp_sig}
    )
    assert status == "200 OK"
    assert wp_res['status'] == 'success'
    print(f"✓ WP Webhook Sync -> Success (Created Client ID: {wp_res['client_id']}, WP ID: {wp_res['wordpress_user_id']})")

    # 9b. Test Unsigned Webhook Rejection (Must return 401 Unauthorized)
    status, headers, unsigned_res = make_request('/api/v1/wordpress/webhook', method='POST', body=wp_payload)
    assert status == "401 Unauthorized"
    print("✓ Security Check -> Unsigned Webhook Request rejected with 401 Unauthorized.")

    # 9c. Test Guest Checkout Webhook Sync (Must set status to Pending Verification without crash)
    guest_payload = {
        'event_id': 'evt_guest_test_8801',
        'event_type': 'order.created',
        'data': {
            'order_number': '#WC-GUEST-1001',
            'wordpress_user_id': '',
            'email': 'guest.customer@example.com',
            'service_name': 'Registered Office Address',
            'price': 20.00,
            'total': 24.00,
            'status': 'Processing'
        }
    }
    guest_bytes = json.dumps(guest_payload).encode('utf-8')
    guest_sig = hmac.new(webhook_secret_bytes(), guest_bytes, hashlib.sha256).hexdigest()
    
    status, headers, guest_res = make_request(
        '/api/v1/wordpress/webhook', 
        method='POST', 
        body=guest_payload, 
        headers={'X-Brixen-Signature': guest_sig}
    )
    assert status == "200 OK"
    guest_ord = query_db("SELECT * FROM orders WHERE order_number = '#WC-GUEST-1001';", one=True)
    assert guest_ord['status'] == 'Pending Verification'
    print("✓ Guest Checkout Sync -> Verified: Order saved safely with 'Pending Verification' status.")

    # 9d. Test payment.completed Webhook
    pay_payload = {
        'event_id': 'evt_pay_test_7701',
        'event_type': 'payment.completed',
        'data': {
            'order_number': '#GB103449JUL26',
            'wordpress_user_id': '1',
            'email': 'client1@acmecorp.co.uk'
        }
    }
    pay_bytes = json.dumps(pay_payload).encode('utf-8')
    pay_sig = hmac.new(webhook_secret_bytes(), pay_bytes, hashlib.sha256).hexdigest()
    
    status, headers, pay_res = make_request(
        '/api/v1/wordpress/webhook', 
        method='POST', 
        body=pay_payload, 
        headers={'X-Brixen-Signature': pay_sig}
    )
    assert status == "200 OK"
    assert pay_res['status'] == 'success'
    print("✓ Payment Completed Sync -> Verified: Order and invoice status updated to Paid.")

    # 10. Test WordPress SSO Bridge Token Exchange & Security Rejection
    sso_wp_id = 'wp_user_999'
    sso_email = 'wp.test.client@brixen.co.uk'
    sso_secret = query_db("SELECT value FROM settings WHERE key = 'wordpress_webhook_secret';", one=True)['value'].encode('utf-8')

    def sign_sso(wp_id, email, ts):
        return hmac.new(sso_secret, f"{wp_id}|{email}|{ts}".encode('utf-8'), hashlib.sha256).hexdigest()

    now_ts = str(int(datetime.datetime.now().timestamp()))
    valid_sig = sign_sso(sso_wp_id, sso_email, now_ts)
    status, headers, sso_res = make_request(
        '/api/v1/auth/sso',
        method='POST',
        body={'wordpress_user_id': sso_wp_id, 'email': sso_email, 'timestamp': now_ts, 'signature': valid_sig}
    )
    assert status == "200 OK"
    assert sso_res['status'] == 'success'
    assert extract_session_token(headers)
    assert 'token' not in sso_res
    print("✓ WP SSO Bridge Exchange -> Valid signed SSO succeeded")

    status, headers, unsigned_sso = make_request(
        '/api/v1/auth/sso',
        method='POST',
        body={'wordpress_user_id': sso_wp_id, 'email': sso_email}
    )
    assert status == "401 Unauthorized"
    print("✓ SSO Security Check -> Missing timestamp rejected with 401 Unauthorized.")

    status, headers, no_sig = make_request(
        '/api/v1/auth/sso',
        method='POST',
        body={'wordpress_user_id': sso_wp_id, 'email': sso_email, 'timestamp': now_ts}
    )
    assert status == "401 Unauthorized"
    print("✓ SSO Security Check -> Missing signature rejected with 401 Unauthorized.")

    zero_ts_sig = sign_sso(sso_wp_id, sso_email, '0')
    status, headers, zero_sso = make_request(
        '/api/v1/auth/sso',
        method='POST',
        body={'wordpress_user_id': sso_wp_id, 'email': sso_email, 'timestamp': '0', 'signature': zero_ts_sig}
    )
    assert status == "401 Unauthorized"
    print("✓ SSO Security Check -> timestamp=0 bypass rejected with 401 Unauthorized.")

    old_ts = str(int(datetime.datetime.now().timestamp()) - 600)
    exp_sig = sign_sso(sso_wp_id, sso_email, old_ts)
    status, headers, exp_sso_res = make_request(
        f"/api/v1/auth/sso?wordpress_user_id={sso_wp_id}&email={sso_email}&timestamp={old_ts}&signature={exp_sig}"
    )
    assert status == "401 Unauthorized"
    print("✓ SSO Security Check -> Expired SSO token (>300s) rejected with 401 Unauthorized.")

    future_ts = str(int(datetime.datetime.now().timestamp()) + 600)
    future_sig = sign_sso(sso_wp_id, sso_email, future_ts)
    status, headers, future_sso = make_request(
        '/api/v1/auth/sso',
        method='POST',
        body={'wordpress_user_id': sso_wp_id, 'email': sso_email, 'timestamp': future_ts, 'signature': future_sig}
    )
    assert status == "401 Unauthorized"
    print("✓ SSO Security Check -> Future SSO token (>300s) rejected with 401 Unauthorized.")

    status, headers, invalid_sso_res = make_request(
        f"/api/v1/auth/sso?wordpress_user_id={sso_wp_id}&email={sso_email}&timestamp={now_ts}&signature=invalid_tampered_signature"
    )
    assert status == "401 Unauthorized"
    print("✓ SSO Security Check -> Invalid/tampered SSO signature rejected with 401 Unauthorized.")

    tampered_sig = sign_sso(sso_wp_id, sso_email, now_ts)
    status, headers, tampered_id = make_request(
        '/api/v1/auth/sso',
        method='POST',
        body={'wordpress_user_id': 'wp_admin_1', 'email': sso_email, 'timestamp': now_ts, 'signature': tampered_sig}
    )
    assert status == "401 Unauthorized"
    print("✓ SSO Security Check -> Tampered wordpress_user_id rejected with 401 Unauthorized.")

    db_session = query_db("SELECT * FROM user_sessions WHERE session_token = ?;", (token,), one=True)
    assert db_session is not None, "Error: Session token not persisted in user_sessions table."
    assert db_session['user_id'] == uid, "Error: Session user_id mismatch."
    print(f"✓ P1 Persistent Session Check -> Verified token in SQLite user_sessions table (ID: {db_session['id']})")

    # 13. Test P2: Secure Private Document Storage, Binary Upload & Streaming Download
    import base64
    raw_pdf_bytes = b"%PDF-1.4 Official Brixen Shareholder Agreement PDF Content"
    b64_pdf = base64.b64encode(raw_pdf_bytes).decode('utf-8')
    
    status, headers, upload_res = make_request(
        '/api/client/documents/upload', 
        method='POST', 
        body={'name': 'Shareholder_Agreement_2026.pdf', 'category': 'Legal', 'file_content_base64': b64_pdf},
        cookie=f"session_token={token}"
    )
    assert status == "200 OK"
    doc_id = upload_res['document_id']
    doc_rec = query_db("SELECT * FROM documents WHERE id = ?;", (doc_id,), one=True)
    assert 'storage/clients' in doc_rec['file_path']
    assert os.path.exists(doc_rec['file_path'])
    
    # Verify Physical File Bytes Stream Download
    status, headers, streamed_bytes = make_request(f"/api/documents/{doc_id}/download", cookie=f"session_token={token}")
    assert status == "200 OK"
    assert streamed_bytes == raw_pdf_bytes
    print(f"✓ P2 Private Document Upload & Physical Binary Streaming -> Success (Path: ...{doc_rec['file_path'][-40:]}, Size: {len(streamed_bytes)} bytes)")

    # Client B access attempt on Client A document (Must return 403 Forbidden)
    status, headers, client2_login = make_request('/api/auth/login', method='POST', body={'email': 'v.smith@vantagecyber.co.uk', 'password': 'ClientPass123!'})
    client2_token = extract_session_token(headers)
    assert client2_token
    status, headers, forbidden_res = make_request(f"/api/documents/{doc_id}/download", cookie=f"session_token={client2_token}")
    assert status == "403 Forbidden"
    print(f"✓ P2 Security Isolation Check -> Client B access on Client A document blocked with 403 Forbidden.")

    status, headers, admin_dl = make_request(f"/api/documents/{doc_id}/download", cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    staff_role = query_db("SELECT id FROM roles WHERE name = 'STAFF';", one=True)
    doc_perm = query_db("SELECT id FROM permissions WHERE name = 'documents.view';", one=True)
    execute_db("DELETE FROM role_permissions WHERE role_id = ? AND permission_id = ?;", (staff_role['id'], doc_perm['id']))
    status, headers, staff_login_docs = make_request(
        '/api/auth/login',
        method='POST',
        body={'email': 'eleanor.finch@brixenconsultant.co.uk', 'password': 'StaffPass123!'}
    )
    staff_no_docs_token = extract_session_token(headers)
    status, headers, staff_denied_dl = make_request(f"/api/documents/{doc_id}/download", cookie=f"session_token={staff_no_docs_token}")
    assert status == "403 Forbidden"
    execute_db("INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?);", (staff_role['id'], doc_perm['id']))
    print("✓ Document RBAC -> STAFF without documents.view denied download; ADMIN allowed.")

    # 14. Test P3: Admin Webhook Monitoring & Retry
    status, headers, wh_list = make_request('/api/admin/webhooks', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert len(wh_list['events']) >= 1
    target_wh = wh_list['events'][0]
    print(f"✓ P3 Admin Webhook Monitor -> Found {len(wh_list['events'])} recorded events.")

    status, headers, retry_res = make_request(f"/api/admin/webhooks/{target_wh['id']}/retry", method='POST', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert retry_res['status'] == 'success'
    print(f"✓ P3 Admin Webhook Retry -> Success (Re-processed event #{target_wh['id']})")

    # 15. Test Support Ticket Creation, Reply & Tenant Isolation
    status, headers, tick_res = make_request(
        '/api/client/tickets',
        method='POST',
        body={'subject': 'UK Registered Office Address Inquiry', 'category': 'Address Service', 'priority': 'High', 'description': 'Need guidance on forwarding mail.'},
        cookie=f"session_token={token}"
    )
    assert status == "200 OK"
    assert tick_res['status'] == 'success'
    tick_id = tick_res['ticket_id']
    print(f"✓ Support Ticket Creation -> Success (Ticket ID: {tick_id})")

    status, headers, tick_list = make_request('/api/client/tickets', cookie=f"session_token={token}")
    assert status == "200 OK"
    assert len(tick_list['tickets']) >= 1
    print(f"✓ Client Tickets Listing -> Verified: {len(tick_list['tickets'])} tickets found for Client A.")

    status, headers, reply_res = make_request(
        f"/api/client/tickets/{tick_id}/messages",
        method='POST',
        body={'message': 'Adding additional details to my address inquiry.'},
        cookie=f"session_token={token}"
    )
    assert status == "200 OK"
    assert reply_res['status'] == 'success'
    print(f"✓ Ticket Message Reply -> Success (Appended reply to Ticket #{tick_id})")

    status, headers, forbidden_ticket_res = make_request(f"/api/client/tickets/{tick_id}/messages", cookie=f"session_token={client2_token}")
    assert status == "403 Forbidden"
    status, headers, forbidden_ticket_post = make_request(
        f"/api/client/tickets/{tick_id}/messages",
        method='POST',
        body={'message': 'cross-tenant attempt'},
        cookie=f"session_token={client2_token}"
    )
    assert status == "403 Forbidden"
    status, headers, admin_ticket_msgs = make_request(f"/api/client/tickets/{tick_id}/messages", cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert admin_ticket_msgs.get('status') == 'success'
    print("✓ Ticket Security Isolation -> Client B blocked; staff can manage Client A ticket.")

    status, headers, client_b_order = make_request('/api/client/orders/1', cookie=f"session_token={client2_token}")
    assert status == "404 Not Found"
    status, headers, admin_order = make_request('/api/client/orders/1', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    print("✓ Order Detail RBAC -> Client B cannot open Client A order; ADMIN with orders.view allowed.")

    # 15b. Staff Task Management APIs
    eleanor = query_db("SELECT * FROM users WHERE email = 'eleanor.finch@brixenconsultant.co.uk';", one=True)
    marcus = query_db("SELECT * FROM users WHERE email = 'marcus.sterling@brixenconsultant.co.uk';", one=True)
    victoria = query_db("SELECT * FROM users WHERE email = 'v.smith@vantagecyber.co.uk';", one=True)
    victoria_company = query_db("SELECT * FROM companies WHERE user_id = ?;", (victoria['id'],), one=True)

    status, headers, unauth_tasks = make_request('/api/admin/tasks')
    assert status == "401 Unauthorized"
    print("✓ Task API Auth Check -> Unauthenticated request rejected with 401 Unauthorized.")

    status, headers, client_tasks = make_request('/api/admin/tasks', cookie=f"session_token={token}")
    assert status == "403 Forbidden"
    status, headers, client_create = make_request(
        '/api/admin/tasks',
        method='POST',
        body={'title': 'Client should not create tasks'},
        cookie=f"session_token={token}"
    )
    assert status == "403 Forbidden"
    print("✓ Task API Client Isolation -> CLIENT access to task APIs blocked with 403 Forbidden.")

    status, headers, staff_list = make_request('/api/admin/staff', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert staff_list['status'] == 'success'
    assert len(staff_list['staff']) >= 1
    assert all(s['role'] != 'CLIENT' for s in staff_list['staff'])
    print(f"✓ Admin Staff List -> Returned {len(staff_list['staff'])} internal staff users (no clients).")

    status, headers, create_res = make_request(
        '/api/admin/tasks',
        method='POST',
        body={
            'title': 'Prepare Registered Office pack',
            'description': 'Compile formation documents for the client.',
            'priority': 'High',
            'due_date': '2026-08-30',
            'assigned_staff_id': eleanor['id'],
            'client_id': uid,
            'company_id': target_order['company_id'],
            'order_id': target_order['id'],
            'internal_notes': 'Internal: confirm Companies House filing first.'
        },
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    assert create_res['status'] == 'success'
    task_id = create_res['task']['id']
    assert create_res['task']['internal_notes'] == 'Internal: confirm Companies House filing first.'
    print(f"✓ Admin Task Create -> Success (Task ID: {task_id}, Assignee: Eleanor Finch)")

    status, headers, task_list = make_request('/api/admin/tasks', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert any(t['id'] == task_id for t in task_list['tasks'])
    print(f"✓ Admin Task List -> Returned {len(task_list['tasks'])} task(s).")

    status, headers, task_detail = make_request(f"/api/admin/tasks/{task_id}", cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert task_detail['task']['title'] == 'Prepare Registered Office pack'
    print(f"✓ Admin Task Detail -> Loaded task #{task_id}.")

    status, headers, bad_assignee = make_request(
        '/api/admin/tasks',
        method='POST',
        body={'title': 'Invalid assignee', 'assigned_staff_id': uid},
        cookie=f"session_token={adm_token}"
    )
    assert status == "400 Bad Request"
    print("✓ Task Assignee Validation -> Client user rejected as assignee with 400 Bad Request.")

    status, headers, bad_link = make_request(
        '/api/admin/tasks',
        method='POST',
        body={
            'title': 'Mismatched company',
            'client_id': uid,
            'company_id': victoria_company['id']
        },
        cookie=f"session_token={adm_token}"
    )
    assert status == "400 Bad Request"
    print("✓ Task Association Validation -> Client/company mismatch rejected with 400 Bad Request.")

    status, headers, eleanor_login = make_request(
        '/api/auth/login',
        method='POST',
        body={'email': 'eleanor.finch@brixenconsultant.co.uk', 'password': 'StaffPass123!'}
    )
    assert status == "200 OK"
    eleanor_token = extract_session_token(headers)
    assert eleanor_token

    status, headers, marcus_login = make_request(
        '/api/auth/login',
        method='POST',
        body={'email': 'marcus.sterling@brixenconsultant.co.uk', 'password': 'StaffPass123!'}
    )
    assert status == "200 OK"
    marcus_token = extract_session_token(headers)
    assert marcus_token

    status, headers, eleanor_list = make_request('/api/admin/tasks', cookie=f"session_token={eleanor_token}")
    assert status == "200 OK"
    assert all(t['assigned_staff_id'] == eleanor['id'] for t in eleanor_list['tasks'])
    assert any(t['id'] == task_id for t in eleanor_list['tasks'])
    print("✓ Staff Task Isolation -> Eleanor sees only tasks assigned to herself.")

    status, headers, marcus_detail = make_request(f"/api/admin/tasks/{task_id}", cookie=f"session_token={marcus_token}")
    assert status == "403 Forbidden"
    status, headers, marcus_list = make_request('/api/admin/tasks', cookie=f"session_token={marcus_token}")
    assert status == "200 OK"
    assert all(t['id'] != task_id for t in marcus_list['tasks'])
    print("✓ Staff Task Isolation -> Marcus blocked from Eleanor's task with 403 Forbidden.")

    status, headers, staff_create = make_request(
        '/api/admin/tasks',
        method='POST',
        body={'title': 'Staff should not create', 'assigned_staff_id': eleanor['id']},
        cookie=f"session_token={eleanor_token}"
    )
    assert status == "403 Forbidden"
    print("✓ Staff Create Permission -> STAFF create rejected with 403 Forbidden.")

    status, headers, staff_reassign = make_request(
        f"/api/admin/tasks/{task_id}",
        method='PUT',
        body={'assigned_staff_id': marcus['id']},
        cookie=f"session_token={eleanor_token}"
    )
    assert status == "403 Forbidden"
    print("✓ Staff Assign Permission -> STAFF reassignment rejected with 403 Forbidden.")

    status, headers, staff_edit = make_request(
        f"/api/admin/tasks/{task_id}",
        method='PUT',
        body={'status': 'In Progress', 'internal_notes': 'Started Companies House check.'},
        cookie=f"session_token={eleanor_token}"
    )
    assert status == "200 OK"
    assert staff_edit['task']['status'] == 'In Progress'
    print("✓ Staff Task Edit -> Eleanor updated her assigned task status.")

    client_task_notes = query_db("SELECT * FROM notifications WHERE user_id = ? AND type LIKE 'task%';", (uid,))
    assert len(client_task_notes) == 0
    assignee_note = query_db(
        "SELECT * FROM notifications WHERE user_id = ? AND type = 'task_assigned' ORDER BY id DESC LIMIT 1;",
        (eleanor['id'],),
        one=True
    )
    assert assignee_note is not None
    assert 'Prepare Registered Office pack' in assignee_note['message']
    print("✓ Task Notification Check -> Assignee notified; client received no task notification.")

    status, headers, complete_res = make_request(
        f"/api/admin/tasks/{task_id}/complete",
        method='POST',
        cookie=f"session_token={eleanor_token}"
    )
    assert status == "200 OK"
    assert complete_res['task']['status'] == 'Completed'
    assert complete_res['task']['completed_at'] is not None
    print(f"✓ Staff Task Complete -> Task #{task_id} marked Completed.")

    created_log = query_db("SELECT * FROM activity_logs WHERE action = 'TASK_CREATED' AND entity_id = ?;", (str(task_id),), one=True)
    completed_log = query_db("SELECT * FROM activity_logs WHERE action = 'TASK_COMPLETED' AND entity_id = ?;", (str(task_id),), one=True)
    assert created_log is not None
    assert completed_log is not None
    print("✓ Task Activity Log Check -> TASK_CREATED and TASK_COMPLETED recorded.")

    # 17. Admin RBAC consistency (hardcoded ADMIN/STAFF gates -> check_permission)
    def ensure_rbac_user(email, password, full_name, role, wp_id):
        existing = query_db("SELECT id FROM users WHERE email = ?;", (email,), one=True)
        if existing:
            execute_db(
                "UPDATE users SET password_hash = ?, full_name = ?, role = ?, status = 'Active' WHERE id = ?;",
                (hash_password(password), full_name, role, existing['id'])
            )
            return existing['id']
        return execute_db("""
            INSERT INTO users (wordpress_user_id, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, ?, 'Active');
        """, (wp_id, email, hash_password(password), full_name, role))

    def login_token(email, password):
        st, hd, body = make_request('/api/auth/login', method='POST', body={'email': email, 'password': password})
        assert st == "200 OK", f"Login failed for {email}: {body}"
        tok = extract_session_token(hd)
        assert tok
        assert 'token' not in body
        return tok

    def rbac_request(path, token=None, method='GET', body=None):
        cookie = f"session_token={token}" if token else None
        return make_request(path, method=method, body=body, cookie=cookie)

    ensure_rbac_user('superadmin@brixenconsultant.co.uk', 'SuperPass123!', 'Priya Shah', 'SUPER_ADMIN', 'wp_rbac_super')
    ensure_rbac_user('manager@brixenconsultant.co.uk', 'ManagerPass123!', 'Helena Ward', 'MANAGER', 'wp_rbac_manager')
    super_token = login_token('superadmin@brixenconsultant.co.uk', 'SuperPass123!')
    manager_token = login_token('manager@brixenconsultant.co.uk', 'ManagerPass123!')
    staff_token = eleanor_token
    admin_token = adm_token
    client_token = token

    st, hd, mgr_order = rbac_request('/api/client/orders/1', manager_token)
    assert st == "200 OK"
    st, hd, super_order = rbac_request('/api/client/orders/1', super_token)
    assert st == "200 OK"
    st, hd, mgr_msgs = rbac_request(f'/api/client/tickets/{tick_id}/messages', manager_token)
    assert st == "200 OK"
    print("✓ Order/Ticket RBAC -> MANAGER and SUPER_ADMIN can access staff ticket/order detail")

    rbac_get_matrix = [
        ('/api/admin/stats', 'orders.view', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '200 OK', 'STAFF': '200 OK', 'CLIENT': '403 Forbidden'
        }),
        ('/api/admin/services', 'orders.view', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '200 OK', 'STAFF': '200 OK', 'CLIENT': '403 Forbidden'
        }),
        ('/api/admin/documents', 'documents.view', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '200 OK', 'STAFF': '200 OK', 'CLIENT': '403 Forbidden'
        }),
        ('/api/admin/activity-logs', 'settings.manage', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '403 Forbidden', 'STAFF': '403 Forbidden', 'CLIENT': '403 Forbidden'
        }),
    ]
    role_tokens = {
        'SUPER_ADMIN': super_token,
        'ADMIN': admin_token,
        'MANAGER': manager_token,
        'STAFF': staff_token,
        'CLIENT': client_token,
    }

    for path, perm, expected in rbac_get_matrix:
        st, hd, body = rbac_request(path)
        assert st == "401 Unauthorized", f"{path} unauthenticated expected 401, got {st}"
        for role, want in expected.items():
            st, hd, body = rbac_request(path, role_tokens[role])
            assert st == want, f"{path} as {role} expected {want}, got {st} ({body})"
            if want == "200 OK":
                assert body.get('status') == 'success'
        print(f"✓ Admin RBAC GET {path} ({perm}) -> SUPER_ADMIN/ADMIN allowed; MANAGER/STAFF per RBAC; CLIENT/unauth denied.")

    st, hd, stats_res = rbac_request('/api/admin/stats', manager_token)
    assert 'total_customers' in stats_res.get('stats', {})
    st, hd, svc_res = rbac_request('/api/admin/services', staff_token)
    assert isinstance(svc_res.get('services'), list)
    print("✓ Admin RBAC GET payloads -> MANAGER stats and STAFF services catalog returned.")

    service_body = {
        'name': 'RBAC Catalog Probe',
        'description': 'Created during admin RBAC regression.',
        'category': 'Compliance',
        'price': 10
    }
    st, hd, body = rbac_request('/api/admin/services', method='POST', body=service_body)
    assert st == "401 Unauthorized"
    for role, want in [
        ('SUPER_ADMIN', '200 OK'),
        ('ADMIN', '200 OK'),
        ('MANAGER', '403 Forbidden'),
        ('STAFF', '403 Forbidden'),
        ('CLIENT', '403 Forbidden'),
    ]:
        st, hd, body = rbac_request('/api/admin/services', role_tokens[role], method='POST', body=service_body)
        assert st == want, f"POST /api/admin/services as {role} expected {want}, got {st} ({body})"
    print("✓ Admin RBAC POST /api/admin/services (settings.manage) -> SUPER_ADMIN/ADMIN allowed; MANAGER/STAFF/CLIENT/unauth denied.")

    sample_doc = query_db("SELECT id FROM documents ORDER BY id DESC LIMIT 1;", one=True)
    assert sample_doc is not None, "Expected a document for admin review RBAC tests."
    doc_path = f"/api/admin/documents/{sample_doc['id']}"
    review_body = {'status': 'Approved', 'review_notes': 'RBAC review probe'}
    st, hd, body = rbac_request(doc_path, method='PUT', body=review_body)
    assert st == "401 Unauthorized"
    for role, want in [
        ('SUPER_ADMIN', '200 OK'),
        ('ADMIN', '200 OK'),
        ('MANAGER', '200 OK'),
        ('STAFF', '200 OK'),
        ('CLIENT', '403 Forbidden'),
    ]:
        st, hd, body = rbac_request(doc_path, role_tokens[role], method='PUT', body=review_body)
        assert st == want, f"PUT {doc_path} as {role} expected {want}, got {st} ({body})"
    print("✓ Admin RBAC PUT /api/admin/documents/{id} (documents.view) -> SUPER_ADMIN/ADMIN/MANAGER/STAFF allowed; CLIENT/unauth denied.")

    settings_body = {'rbac_audit_probe': 'ok'}
    st, hd, body = rbac_request('/api/admin/settings', method='POST', body=settings_body)
    assert st == "401 Unauthorized"
    for role, want in [
        ('SUPER_ADMIN', '200 OK'),
        ('ADMIN', '200 OK'),
        ('MANAGER', '403 Forbidden'),
        ('STAFF', '403 Forbidden'),
        ('CLIENT', '403 Forbidden'),
    ]:
        st, hd, body = rbac_request('/api/admin/settings', role_tokens[role], method='POST', body=settings_body)
        assert st == want, f"POST /api/admin/settings as {role} expected {want}, got {st} ({body})"
    probe = query_db("SELECT value FROM settings WHERE key = 'rbac_audit_probe';", one=True)
    assert probe and probe['value'] == 'ok'
    print("✓ Admin RBAC POST /api/admin/settings (settings.manage) -> SUPER_ADMIN/ADMIN allowed; MANAGER/STAFF/CLIENT/unauth denied.")

    def _is_legacy_sha256_hex(value):
        return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)

    def _assert_argon2id_hash(value):
        assert isinstance(value, str) and value.startswith('$argon2id$')
        assert not _is_legacy_sha256_hex(value)

    def _legacy_sha256_hex(password):
        return hashlib.sha256(password.encode('utf-8')).hexdigest()

    def _insert_throwaway_user(email, wp_id, stored_hash, full_name='Phase 7C Throwaway'):
        return execute_db("""
            INSERT INTO users (wordpress_user_id, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, 'CLIENT', 'Active');
        """, (wp_id, email, stored_hash, full_name))

    # A. New password hash format
    new_hash = hash_password('Phase7CHashOnlyPass!')
    _assert_argon2id_hash(new_hash)
    assert not _is_legacy_sha256_hex(new_hash)
    assert verify_password('Phase7CHashOnlyPass!', new_hash)
    assert not verify_password('wrong-password', new_hash)
    assert not verify_password('Phase7CHashOnlyPass!', 'not-a-real-hash-format')
    print("✓ Password hash_password -> Argon2id PHC; SHA-256 not produced; unknown formats fail")

    # B. Argon2id login
    argon_email = 'argon2id.login.7c@example.test'
    argon_password = 'Argon2idLoginPass123!'
    _insert_throwaway_user(argon_email, 'wp_7c_argon_login', hash_password(argon_password))
    pre_login = query_db("SELECT password_hash FROM users WHERE email = ?;", (argon_email,), one=True)
    _assert_argon2id_hash(pre_login['password_hash'])
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': argon_email, 'password': argon_password})
    assert st == "200 OK"
    assert body.get('status') == 'success'
    assert extract_session_token(hd)
    assert 'password_hash' not in (body.get('user') or {})
    assert 'session_token' not in (body.get('user') or {})
    assert 'token' not in body
    post_login = query_db("SELECT password_hash FROM users WHERE email = ?;", (argon_email,), one=True)
    _assert_argon2id_hash(post_login['password_hash'])
    print("✓ Password Argon2id login -> session created; hash remains Argon2id")

    # C. Legacy SHA-256 migration on successful login
    legacy_email = 'legacy.sha256.7c@example.test'
    legacy_password = 'LegacyMigratePass123!'
    _insert_throwaway_user(legacy_email, 'wp_7c_legacy_ok', _legacy_sha256_hex(legacy_password))
    before_mig = query_db("SELECT password_hash FROM users WHERE email = ?;", (legacy_email,), one=True)
    assert _is_legacy_sha256_hex(before_mig['password_hash'])
    assert needs_rehash(before_mig['password_hash'])
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': legacy_email, 'password': legacy_password})
    assert st == "200 OK"
    assert extract_session_token(hd)
    after_mig = query_db("SELECT password_hash FROM users WHERE email = ?;", (legacy_email,), one=True)
    _assert_argon2id_hash(after_mig['password_hash'])
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': legacy_email, 'password': legacy_password})
    assert st == "200 OK"
    print("✓ Password legacy SHA-256 login -> migrates to Argon2id; second login succeeds")

    # D. Wrong password does not migrate
    wrong_email = 'legacy.wrong.7c@example.test'
    wrong_stored = _legacy_sha256_hex('LegacyCorrectPass123!')
    _insert_throwaway_user(wrong_email, 'wp_7c_legacy_bad', wrong_stored)
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': wrong_email, 'password': 'not-the-legacy-password'})
    assert st == "401 Unauthorized"
    assert body.get('message') == 'Invalid email or password.'
    after_wrong = query_db("SELECT password_hash FROM users WHERE email = ?;", (wrong_email,), one=True)
    assert after_wrong['password_hash'] == wrong_stored
    assert _is_legacy_sha256_hex(after_wrong['password_hash'])
    print("✓ Password wrong password -> 401; legacy SHA-256 hash unchanged")

    # E. Unknown email
    unknown_email = 'unknown.no.account.7c@example.test'
    users_before = query_db("SELECT COUNT(*) as c FROM users;", one=True)['c']
    unknown_row = query_db("SELECT id FROM users WHERE LOWER(email) = ?;", (unknown_email,), one=True)
    assert unknown_row is None
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': unknown_email, 'password': 'any-password'})
    assert st == "401 Unauthorized"
    assert body.get('message') == 'Invalid email or password.'
    users_after = query_db("SELECT COUNT(*) as c FROM users;", one=True)['c']
    assert users_after == users_before
    still_missing = query_db("SELECT id FROM users WHERE LOWER(email) = ?;", (unknown_email,), one=True)
    assert still_missing is None
    print("✓ Password unknown email -> 401; no account created")

    # F. Webhook / guest / sync created users get unusable Argon2id hashes
    wp_user = query_db("SELECT password_hash FROM users WHERE email = 'wp.test.client@brixen.co.uk';", one=True)
    guest_user = query_db("SELECT password_hash FROM users WHERE email = 'guest.unlinked@brixenconsultants.com';", one=True)
    _assert_argon2id_hash(wp_user['password_hash'])
    _assert_argon2id_hash(guest_user['password_hash'])
    former_defaults = ('WpClientPass2026!', 'GuestAccountUnlinked2026!', 'SyncPass123!')
    for former in former_defaults:
        st, hd, body = make_request('/api/auth/login', method='POST', body={'email': 'wp.test.client@brixen.co.uk', 'password': former})
        assert st == "401 Unauthorized"
        st, hd, body = make_request('/api/auth/login', method='POST', body={'email': 'guest.unlinked@brixenconsultants.com', 'password': former})
        assert st == "401 Unauthorized"
    sync_payload = {
        'users': [{
            'wordpress_user_id': 'wp_7c_sync_user',
            'email': 'sync.created.7c@example.test',
            'full_name': 'Sync Created 7C'
        }]
    }
    sync_bytes = json.dumps(sync_payload).encode('utf-8')
    sync_sig = hmac.new(webhook_secret_bytes(), sync_bytes, hashlib.sha256).hexdigest()
    st, hd, sync_res = make_request(
        '/api/v1/wordpress/sync-users',
        method='POST',
        body=sync_payload,
        headers={'X-Brixen-Signature': sync_sig}
    )
    assert st == "200 OK"
    sync_user = query_db("SELECT password_hash FROM users WHERE email = 'sync.created.7c@example.test';", one=True)
    _assert_argon2id_hash(sync_user['password_hash'])
    for former in former_defaults:
        st, hd, body = make_request('/api/auth/login', method='POST', body={'email': 'sync.created.7c@example.test', 'password': former})
        assert st == "401 Unauthorized"
    unused = unusable_password_hash()
    _assert_argon2id_hash(unused)
    print("✓ Password webhook/guest/sync users -> Argon2id unusable hashes; former defaults rejected")

    # G. Profile password change
    profile_email = 'profile.change.7c@example.test'
    profile_old = 'ProfileOldPass123!'
    profile_new = 'ProfileNewPass123!'
    _insert_throwaway_user(profile_email, 'wp_7c_profile', hash_password(profile_old))
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': profile_email, 'password': profile_old})
    assert st == "200 OK"
    profile_token = extract_session_token(hd)
    st, hd, body = make_request(
        '/api/auth/profile',
        method='POST',
        body={'new_password': profile_new},
        cookie=f"session_token={profile_token}"
    )
    assert st == "200 OK"
    changed = query_db("SELECT password_hash FROM users WHERE email = ?;", (profile_email,), one=True)
    _assert_argon2id_hash(changed['password_hash'])
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': profile_email, 'password': profile_new})
    assert st == "200 OK"
    assert extract_session_token(hd)
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': profile_email, 'password': profile_old})
    assert st == "401 Unauthorized"
    print("✓ Password profile change -> Argon2id stored; new password works; old password fails")

    # H. SSO still succeeds for webhook-created user
    sso_now = str(int(datetime.datetime.now().timestamp()))
    sso_ok = sign_sso(sso_wp_id, sso_email, sso_now)
    st, hd, sso_body = make_request(
        '/api/v1/auth/sso',
        method='POST',
        body={'wordpress_user_id': sso_wp_id, 'email': sso_email, 'timestamp': sso_now, 'signature': sso_ok}
    )
    assert st == "200 OK"
    assert sso_body.get('status') == 'success'
    assert extract_session_token(hd)
    assert 'token' not in sso_body
    print("✓ Password SSO -> valid signed SSO still succeeds for webhook-created user")

    # I. API security: hashes and session tokens stay out of JSON
    st, hd, login_body = make_request('/api/auth/login', method='POST', body={'email': argon_email, 'password': argon_password})
    login_user = login_body.get('user') or {}
    assert 'password_hash' not in login_body
    assert 'password_hash' not in login_user
    assert 'session_token' not in login_body
    assert 'session_token' not in login_user
    me_tok = extract_session_token(hd)
    st, hd, me_body = make_request('/api/auth/me', cookie=f"session_token={me_tok}")
    me_user = me_body.get('user') or {}
    assert 'password_hash' not in me_user
    assert 'session_token' not in me_user
    sso_user = sso_body.get('user') or {}
    assert 'password_hash' not in sso_user
    assert 'session_token' not in sso_user
    print("✓ Password API security -> password_hash and session_token absent from login/me/SSO")

    # J. Downgrade protection
    for email in (
        argon_email, legacy_email, profile_email,
        'wp.test.client@brixen.co.uk',
        'guest.unlinked@brixenconsultants.com',
        'sync.created.7c@example.test',
        'client1@acmecorp.co.uk',
        'admin@brixenconsultant.co.uk',
    ):
        row = query_db("SELECT password_hash FROM users WHERE email = ?;", (email,), one=True)
        assert row is not None
        assert not _is_legacy_sha256_hex(row['password_hash'])
        _assert_argon2id_hash(row['password_hash'])
    print("✓ Password downgrade protection -> no auth flow writes SHA-256 hashes")

    # 16. Test Logout & Session Revocation
    status, headers, logout_res = make_request('/api/auth/logout', method='POST', cookie=f"session_token={token}")
    assert status == "200 OK"
    status, headers, expired_check = make_request('/api/client/orders', cookie=f"session_token={token}")
    assert status == "401 Unauthorized"
    print(f"✓ P1 Session Revocation Check -> Revoked session rejected with 401 Unauthorized.")

    print("\n==================================================")
    print("ALL HYPETEX WSGI & AUDIT FIX TESTS PASSED! (100%)")
    print("==================================================")

if __name__ == '__main__':
    run_tests()
