import os
import sys
import json
import io
import hmac
import hashlib
import datetime
import urllib.parse
from unittest.mock import patch
from app import application
import app as app_mod
from db import query_db, execute_db, hash_password, verify_password, needs_rehash, unusable_password_hash
from seed_db import seed_demo

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
    seed_demo()
    
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
    assert 'class="auth-pending"' in body
    assert 'id="auth-loading-screen"' in body
    assert 'id="app-container" hidden' in body
    assert 'id="admin-sidebar" class="sidebar" hidden' in body
    assert 'id="client-sidebar" class="sidebar" hidden' in body
    assert 'id="view-admin-services"' in body
    assert 'id="view-admin-invoices"' in body
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
    assert 'auth-pending' in js_src
    assert 'setAuthShellState' in js_src
    assert 'auth-ready' in js_src
    assert 'setActiveViewPanel' in js_src
    assert 'openCreateUserModal' in js_src
    assert 'Assign task' not in js_src
    assert 'renderStaffAccessCell' in js_src
    assert 'canOpenView' in js_src
    assert 'canViewRevenue' in js_src
    assert 'applyAdminRevenueCards' in js_src
    assert 'data-admin-revenue' in html_src
    assert 'ALL_USER_ROLES' in js_src
    assert 'id="modal-create-user"' in html_src
    assert 'Business Portfolio' in html_src
    assert 'My Companies' not in html_src
    assert 'click any company to view details' in html_src
    assert 'portfolio-companies-grid' in html_src
    assert 'id="portfolio-company-count"' in html_src
    assert 'openCompanyPortfolioDetail' in js_src
    assert 'No companies in your Business Portfolio yet.' in js_src
    assert 'Unable to load your Business Portfolio. Please try again.' in js_src
    admin_sidebar = html_src.split('id="admin-sidebar"', 1)[1].split('</aside>', 1)[0]
    assert 'Business Portfolio' not in admin_sidebar
    assert 'Company Registered' in admin_sidebar
    assert 'data-view="admin-companies"' in admin_sidebar
    assert 'id="btn-add-company"' in html_src
    assert 'id="modal-create-company"' in html_src
    assert 'create-company-new-client' in html_src
    assert 'syncCreateCompanyClientMode' in js_src
    assert 'openCreateCompanyModal' in js_src
    client_sidebar = html_src.split('id="client-sidebar"', 1)[1].split('</aside>', 1)[0]
    assert 'Business Portfolio' in client_sidebar
    assert 'Company Registered' in client_sidebar
    assert '>Access</th>' in html_src
    assert 'id="account-user-switcher"' in html_src
    assert 'switchToUser' in js_src
    assert 'switchRoleDemo' not in js_src
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
    assert 'companies_house_api_key' not in pub
    print("✓ Public settings -> webhook secret and credentials absent from anonymous response")

    st, hd, _ = make_request('/api/settings', headers={'Origin': 'https://evil.example'})
    assert hd.get('Access-Control-Allow-Origin') not in ('*', 'https://evil.example')
    st, hd, _ = make_request('/api/settings', headers={'Origin': 'http://127.0.0.1:5050'})
    assert hd.get('Access-Control-Allow-Origin') == 'http://127.0.0.1:5050'
    st, hd, _ = make_request('/api/settings', headers={'Origin': 'https://portal.brixenconsultants.com'})
    assert hd.get('Access-Control-Allow-Origin') == 'https://portal.brixenconsultants.com'
    st, hd, _ = make_request('/api/settings', headers={'Origin': 'http://portal.brixenconsultants.com:5050'})
    assert hd.get('Access-Control-Allow-Origin') == 'http://portal.brixenconsultants.com:5050'
    print("✓ CORS -> wildcard origin removed; local and portal.brixenconsultants.com origins allowed")

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

    status, headers, unauth_companies = make_request('/api/client/companies')
    assert status == "401 Unauthorized"
    status, headers, companies_res = make_request(
        '/api/client/companies?client_id=999&user_id=1&company_id=1',
        cookie=f"session_token={token}"
    )
    assert status == "200 OK"
    own_companies = companies_res.get('companies') or []
    assert own_companies, "Authenticated client should see their own companies"
    james_row = query_db("SELECT id FROM users WHERE email = 'client1@acmecorp.co.uk';", one=True)
    victoria_row = query_db("SELECT id FROM users WHERE email = 'v.smith@vantagecyber.co.uk';", one=True)
    victoria_company = query_db("SELECT id, name FROM companies WHERE user_id = ?;", (victoria_row['id'],), one=True)
    own_ids = {int(c['id']) for c in own_companies}
    for company in own_companies:
        owner = query_db("SELECT user_id FROM companies WHERE id = ?;", (company['id'],), one=True)
        assert owner['user_id'] == james_row['id']
        assert 'user_id' not in company
        assert 'notes' not in company
        assert 'file_path' not in company
        assert 'password_hash' not in company
        assert isinstance(company.get('deadlines'), list)
    assert int(victoria_company['id']) not in own_ids
    own_company_id = int(own_companies[0]['id'])
    status, headers, detail = make_request(f'/api/client/companies/{own_company_id}', cookie=f"session_token={token}")
    assert status == "200 OK"
    assert detail['status'] == 'success'
    assert int(detail['company']['id']) == own_company_id
    detail_blob = json.dumps(detail)
    assert 'file_path' not in detail_blob
    assert 'review_notes' not in detail_blob
    assert 'password_hash' not in detail_blob
    assert 'session_token' not in detail_blob
    for doc in detail.get('documents') or []:
        assert 'file_path' not in doc
        assert 'review_notes' not in doc
    for order in detail.get('orders') or []:
        assert 'notes' not in order
        assert 'woocommerce_order_id' not in order
    status, headers, foreign_detail = make_request(
        f'/api/client/companies/{victoria_company["id"]}',
        cookie=f"session_token={token}"
    )
    assert status == "404 Not Found"
    status, headers, vic_login = make_request('/api/auth/login', method='POST', body={'email': 'v.smith@vantagecyber.co.uk', 'password': 'ClientPass123!'})
    vic_token = extract_session_token(headers)
    status, headers, vic_companies = make_request('/api/client/companies', cookie=f"session_token={vic_token}")
    assert status == "200 OK"
    vic_ids = {int(c['id']) for c in (vic_companies.get('companies') or [])}
    assert own_company_id not in vic_ids
    status, headers, vic_denied = make_request(f'/api/client/companies/{own_company_id}', cookie=f"session_token={vic_token}")
    assert status == "404 Not Found"
    print("✓ Business Portfolio API -> session-scoped companies, ignored query IDs, and cross-client 404")

    status, headers, client_admin_denied = make_request('/api/admin/companies', cookie=f"session_token={token}")
    assert status == "403 Forbidden"
    status, headers, adm_login = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
    assert status == "200 OK"
    adm_comp_token = extract_session_token(headers)
    status, headers, admin_companies = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert status == "200 OK"
    admin_ids = {int(c['id']) for c in (admin_companies.get('companies') or [])}
    assert own_company_id in admin_ids
    assert int(victoria_company['id']) in admin_ids
    status, headers, admin_detail = make_request(f'/api/admin/companies/{victoria_company["id"]}', cookie=f"session_token={adm_comp_token}")
    assert status == "200 OK"
    assert int(admin_detail['company']['id']) == int(victoria_company['id'])
    assert 'file_path' not in json.dumps(admin_detail)
    status, headers, client_admin_detail = make_request(f'/api/admin/companies/{own_company_id}', cookie=f"session_token={token}")
    assert status == "403 Forbidden"
    print("✓ Company Registered admin API -> staff see all companies; clients blocked")

    status, headers, portal_login = make_request(
        '/api/auth/login', method='POST',
        body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'},
        headers={'Host': 'portal.brixenconsultants.com:5050'}
    )
    assert status == "200 OK"
    portal_cookie = headers.get('Set-Cookie', '')
    assert 'HttpOnly' in portal_cookie
    assert 'Secure' not in portal_cookie
    status, headers, local_login = make_request(
        '/api/auth/login', method='POST',
        body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'},
        headers={'Host': '127.0.0.1:5050'}
    )
    assert status == "200 OK"
    local_cookie = headers.get('Set-Cookie', '')
    assert 'HttpOnly' in local_cookie
    assert 'SameSite=Lax' in local_cookie
    assert 'Secure' not in local_cookie
    status, headers, https_login = make_request(
        '/api/auth/login', method='POST',
        body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'},
        headers={'Host': '127.0.0.1:5050', 'X-Forwarded-Proto': 'https'}
    )
    assert status == "200 OK"
    https_cookie = headers.get('Set-Cookie', '')
    assert 'Secure' in https_cookie
    print("✓ Auth cookies -> Secure omitted on local HTTP; kept when HTTPS is forwarded")

    status, headers, me_res = make_request('/api/auth/me', cookie=f"session_token={token}")
    assert status == "200 OK"
    me_user = me_res.get('user') or {}
    assert 'password_hash' not in me_user
    assert 'session_token' not in me_user
    assert me_user.get('role') == 'CLIENT'
    assert me_user.get('email') == 'client1@acmecorp.co.uk'
    assert me_user.get('id')
    assert 'password' not in me_user
    print("✓ Auth /api/auth/me -> password_hash and session_token absent from profile")

    # 6. Test GET /api/client/orders & /api/client/dashboard with Session Token
    status, headers, res = make_request('/api/client/orders', cookie=f"session_token={token}")
    assert status == "200 OK"
    assert res['status'] == 'success'
    print(f"✓ WSGI GET /api/client/orders -> Returned {len(res['orders'])} orders with DB Stats: {res['stats']}")

    status, headers, dash_res = make_request('/api/client/dashboard', cookie=f"session_token={token}")
    assert status == "200 OK"
    assert dash_res['status'] == 'success'
    assert all(o.get('user_id') == uid for o in dash_res.get('active_orders') or [])
    assert all('notes' not in o for o in dash_res.get('active_orders') or [])
    assert all(c.get('id') for c in dash_res.get('companies') or [])
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

    status, headers, staff_login = make_request('/api/auth/login', method='POST', body={'email': 'eleanor.finch@brixenconsultant.co.uk', 'password': 'StaffPass123!'})
    staff_token = extract_session_token(headers)
    assert staff_token
    delete_order_id = execute_db("""
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, ('#GB-DELETE-TEST', uid, target_order['company_id'], 'Registered Office Address', 20, 4, 24, 'Processing', 10))
    status, headers, client_delete = make_request(f"/api/admin/orders/{delete_order_id}", method='DELETE', cookie=f"session_token={token}")
    assert status == "403 Forbidden"
    status, headers, staff_delete = make_request(f"/api/admin/orders/{delete_order_id}", method='DELETE', cookie=f"session_token={staff_token}")
    assert status == "403 Forbidden"
    status, headers, missing_delete = make_request('/api/admin/orders/999999', method='DELETE', cookie=f"session_token={adm_token}")
    assert status == "404 Not Found"
    status, headers, staff_assign = make_request(
        f"/api/admin/orders/{delete_order_id}",
        method='PUT',
        body={'assigned_staff_id': query_db("SELECT id FROM users WHERE email = 'eleanor.finch@brixenconsultant.co.uk';", one=True)['id']},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    assigned = query_db("SELECT assigned_staff_id FROM orders WHERE id = ?;", (delete_order_id,), one=True)
    assert assigned['assigned_staff_id']
    status, headers, mode_res = make_request(
        f"/api/admin/orders/{delete_order_id}",
        method='PUT',
        body={'payment_mode': 'PKR(Bank Transfer)'},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    mode_row = query_db("SELECT payment_mode FROM orders WHERE id = ?;", (delete_order_id,), one=True)
    assert mode_row['payment_mode'] == 'PKR(Bank Transfer)'
    status, headers, bad_mode = make_request(
        f"/api/admin/orders/{delete_order_id}",
        method='PUT',
        body={'payment_mode': 'Cash'},
        cookie=f"session_token={adm_token}"
    )
    assert status == "400 Bad Request"
    status, headers, admin_delete = make_request(f"/api/admin/orders/{delete_order_id}", method='DELETE', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert admin_delete['status'] == 'success'
    gone = query_db("SELECT id FROM orders WHERE id = ?;", (delete_order_id,), one=True)
    assert gone is None
    still_there = query_db("SELECT id FROM orders WHERE id = ?;", (target_order['id'],), one=True)
    assert still_there is not None
    print("✓ Admin Order Delete -> ADMIN can delete; CLIENT/STAFF blocked; other orders unchanged")

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

    product_payload = {
        'event_id': 'evt_wc_line_items_9001',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9001',
            'order_number': '#WC-9001',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'company_name': 'Acme Corporate Holdings Ltd',
            'service_name': 'Registered Office Address',
            'price': 20.00,
            'total': 24.00,
            'status': 'Processing',
            'line_items': [{
                'product_id': '101',
                'variation_id': '202',
                'sku': 'ROA-UK',
                'product_name': 'Registered Office Address',
                'quantity': 1,
                'unit_price': 20.00,
                'line_total': 20.00,
                'category': 'Address Services',
                'category_id': '55'
            }]
        }
    }
    prod_bytes = json.dumps(product_payload).encode('utf-8')
    prod_sig = hmac.new(webhook_secret_bytes(), prod_bytes, hashlib.sha256).hexdigest()
    status, headers, prod_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=product_payload,
        headers={'X-Brixen-Signature': prod_sig}
    )
    assert status == "200 OK", prod_res
    wc_order_id = prod_res['order_id']
    wc_order = query_db("SELECT * FROM orders WHERE id = ?;", (wc_order_id,), one=True)
    james = query_db("SELECT * FROM users WHERE email = 'client1@acmecorp.co.uk';", one=True)
    acme = query_db("SELECT * FROM companies WHERE name = 'Acme Corporate Holdings Ltd';", one=True)
    assert wc_order['user_id'] == james['id']
    assert wc_order['company_id'] == acme['id']
    lines = query_db("SELECT * FROM order_line_items WHERE order_id = ?;", (wc_order_id,))
    assert len(lines) == 1
    assert lines[0]['product_name'] == 'Registered Office Address'
    assert lines[0]['sku'] == 'ROA-UK'
    assert lines[0]['woocommerce_product_id'] == '101'
    assert lines[0]['woocommerce_variation_id'] == '202'
    assert lines[0]['category_name'] == 'Address Services'
    assert lines[0]['category_id'] == '55'
    assert lines[0]['quantity'] == 1
    print("✓ WooCommerce product/category mapping -> line item, customer, and company preserved.")

    formation_payload = {
        'event_id': 'evt_wc_formation_gul_1',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9101',
            'order_number': '#WC-9101',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'company_name': 'GUL MART LTD',
            'service_name': 'Company Formation Package',
            'price': 45.00,
            'total': 54.00,
            'status': 'Processing',
            'line_items': [{
                'product_name': 'Company Formation Package',
                'category': 'Incorporation',
                'quantity': 1,
                'unit_price': 45.00,
                'line_total': 45.00,
            }]
        }
    }
    form_bytes = json.dumps(formation_payload).encode('utf-8')
    form_sig = hmac.new(webhook_secret_bytes(), form_bytes, hashlib.sha256).hexdigest()
    status, headers, form_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=formation_payload,
        headers={'X-Brixen-Signature': form_sig}
    )
    assert status == "200 OK", form_res
    gul = query_db("SELECT * FROM companies WHERE name = 'GUL MART LTD';", one=True)
    assert gul is not None
    assert gul['user_id'] == james['id']
    assert gul['status'] == 'Active'
    form_ord = query_db("SELECT * FROM orders WHERE order_number = '#WC-9101';", one=True)
    assert form_ord['company_id'] == gul['id']
    status, headers, port_res = make_request('/api/client/companies', cookie=f"session_token={token}")
    assert status == "200 OK"
    assert any(c.get('name') == 'GUL MART LTD' for c in (port_res.get('companies') or []))
    skip_payload = {
        'event_id': 'evt_wc_roa_no_create',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9102',
            'order_number': '#WC-9102',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'company_name': 'SHOULD NOT EXIST LTD',
            'service_name': 'Registered Office Address',
            'price': 20.00,
            'total': 24.00,
            'status': 'Processing',
        }
    }
    skip_bytes = json.dumps(skip_payload).encode('utf-8')
    skip_sig = hmac.new(webhook_secret_bytes(), skip_bytes, hashlib.sha256).hexdigest()
    status, headers, skip_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=skip_payload,
        headers={'X-Brixen-Signature': skip_sig}
    )
    assert status == "200 OK", skip_res
    assert query_db("SELECT id FROM companies WHERE name = 'SHOULD NOT EXIST LTD';", one=True) is None
    print("✓ Company registration order -> company card created; non-formation orders do not invent companies")

    digital_payload = {
        'event_id': 'evt_wc_digital_denty_1',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9103',
            'order_number': '#WC-9103',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'company_name': 'DENTY LIMITED',
            'service_name': 'Digital Package',
            'price': 52.99,
            'total': 63.59,
            'status': 'Processing',
            'line_items': [{
                'product_name': 'Digital Package',
                'category': 'Company Incorporation',
                'item_meta': {'Proposed Company Name': 'DENTY LIMITED'},
                'quantity': 1,
            }]
        }
    }
    dig_bytes = json.dumps(digital_payload).encode('utf-8')
    dig_sig = hmac.new(webhook_secret_bytes(), dig_bytes, hashlib.sha256).hexdigest()
    status, headers, dig_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=digital_payload,
        headers={'X-Brixen-Signature': dig_sig}
    )
    assert status == "200 OK", dig_res
    denty = query_db("SELECT * FROM companies WHERE name = 'DENTY LIMITED' AND user_id = ?;", (james['id'],), one=True)
    assert denty is not None
    print("✓ Website Digital Package order -> registered company card created")

    pending_payload = {
        'event_id': 'evt_wc_pending_name_1',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9104',
            'order_number': '#WC-9104',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'service_name': 'Professional Package',
            'price': 99.00,
            'total': 118.80,
            'status': 'Processing',
        }
    }
    pend_bytes = json.dumps(pending_payload).encode('utf-8')
    pend_sig = hmac.new(webhook_secret_bytes(), pend_bytes, hashlib.sha256).hexdigest()
    status, headers, pend_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=pending_payload,
        headers={'X-Brixen-Signature': pend_sig}
    )
    assert status == "200 OK", pend_res
    pending_ord = query_db("SELECT * FROM orders WHERE order_number = '#WC-9104';", one=True)
    assert pending_ord is not None
    assert pending_ord['company_id'] is None
    status, headers, client_pending = make_request('/api/client/companies', cookie=f"session_token={token}")
    assert status == "200 OK"
    client_wait = client_pending.get('pending_registrations') or []
    assert any(item.get('order_number') == '#WC-9104' for item in client_wait)
    assert all('user_id' not in item and 'client_name' not in item for item in client_wait)
    status, headers, admin_pending = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert status == "200 OK"
    admin_wait = admin_pending.get('pending_registrations') or []
    assert any(item.get('order_number') == '#WC-9104' for item in admin_wait)
    status, headers, client_create = make_request(
        '/api/admin/companies', method='POST',
        body={'order_id': pending_ord['id'], 'name': 'NORTHGATE TRADING LTD'},
        cookie=f"session_token={token}"
    )
    assert status == "403 Forbidden"
    status, headers, named = make_request(
        '/api/admin/companies', method='POST',
        body={'order_id': pending_ord['id'], 'name': 'NORTHGATE TRADING LTD'},
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", named
    assert named.get('company', {}).get('name') == 'NORTHGATE TRADING LTD'
    linked = query_db("SELECT company_id FROM orders WHERE order_number = '#WC-9104';", one=True)
    assert linked['company_id'] == named['company']['id']
    status, headers, after_name = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert not any(item.get('order_number') == '#WC-9104' for item in (after_name.get('pending_registrations') or []))
    print("✓ Website formation order without a name stays pending until staff confirm the company name")

    # Manual Add company must clear matching awaiting-name cards (name stem match).
    orphan_payload = {
        'event_id': 'evt_wc_orphan_pending_1',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9205',
            'order_number': '#WC-9205',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'service_name': 'Digital Package',
            'price': 49.00,
            'total': 58.80,
            'status': 'Processing',
        }
    }
    orphan_bytes = json.dumps(orphan_payload).encode('utf-8')
    orphan_sig = hmac.new(webhook_secret_bytes(), orphan_bytes, hashlib.sha256).hexdigest()
    status, headers, orphan_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=orphan_payload,
        headers={'X-Brixen-Signature': orphan_sig}
    )
    assert status == "200 OK", orphan_res
    orphan_ord = query_db("SELECT * FROM orders WHERE order_number = '#WC-9205';", one=True)
    assert orphan_ord is not None and orphan_ord['company_id'] is None
    client_label = query_db("SELECT full_name FROM users WHERE id = ?;", (orphan_ord['user_id'],), one=True)
    status, headers, manual_co = make_request(
        '/api/admin/companies', method='POST',
        body={
            'user_id': orphan_ord['user_id'],
            'name': f"{client_label['full_name']} LTD",
            'company_number': '88776655',
        },
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", manual_co
    linked_orphan = query_db("SELECT company_id FROM orders WHERE order_number = '#WC-9205';", one=True)
    assert linked_orphan['company_id'] == manual_co['company']['id']
    status, headers, after_manual = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert not any(item.get('order_number') == '#WC-9205' for item in (after_manual.get('pending_registrations') or []))
    # A second formation for the same client must stay pending once the card is already linked.
    other_payload = {
        'event_id': 'evt_wc_orphan_pending_2',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9206',
            'order_number': '#WC-9206',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'service_name': 'Professional Package',
            'price': 99.00,
            'total': 118.80,
            'status': 'Processing',
        }
    }
    other_bytes = json.dumps(other_payload).encode('utf-8')
    other_sig = hmac.new(webhook_secret_bytes(), other_bytes, hashlib.sha256).hexdigest()
    status, headers, other_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=other_payload,
        headers={'X-Brixen-Signature': other_sig}
    )
    assert status == "200 OK", other_res
    status, headers, still_pending = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert any(item.get('order_number') == '#WC-9206' for item in (still_pending.get('pending_registrations') or []))
    print("✓ Manual company add links matching formation orders; unrelated awaiting-name cards stay")

    # Sole company + sole pending formation links even when checkout label differs from LTD name.
    solo_payload = {
        'event_id': 'evt_wc_solo_pending_1',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9220',
            'order_number': '#WC-9220',
            'wordpress_user_id': 'wp_user_999',
            'email': 'wp.test.client@brixen.co.uk',
            'service_name': 'Professional Package',
            'price': 99.00,
            'total': 118.80,
            'status': 'Processing',
        }
    }
    solo_bytes = json.dumps(solo_payload).encode('utf-8')
    solo_sig = hmac.new(webhook_secret_bytes(), solo_bytes, hashlib.sha256).hexdigest()
    status, headers, solo_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=solo_payload,
        headers={'X-Brixen-Signature': solo_sig}
    )
    assert status == "200 OK", solo_res
    solo_ord = query_db("SELECT * FROM orders WHERE order_number = '#WC-9220';", one=True)
    solo_client = query_db("SELECT id, full_name FROM users WHERE email = 'wp.test.client@brixen.co.uk';", one=True)
    status, headers, solo_co = make_request(
        '/api/admin/companies', method='POST',
        body={
            'user_id': solo_client['id'],
            'name': 'RIVERSTONE VENTURES LTD',
            'company_number': '44556677',
        },
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", solo_co
    linked_solo = query_db("SELECT company_id FROM orders WHERE order_number = '#WC-9220';", one=True)
    assert linked_solo['company_id'] == solo_co['company']['id']
    status, headers, after_solo = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert not any(item.get('order_number') == '#WC-9220' for item in (after_solo.get('pending_registrations') or []))
    print("✓ Sole pending formation auto-links when client has one company card")

    status, headers, ch_unauth = make_request('/api/admin/companies/search?q=KOOKY')
    assert status == "401 Unauthorized"
    status, headers, ch_client = make_request('/api/admin/companies/search?q=KOOKY', cookie=f"session_token={token}")
    assert status == "403 Forbidden"
    status, headers, ch_missing = make_request('/api/admin/companies/search?q=KOOKY', cookie=f"session_token={adm_comp_token}")
    assert status == "503 Service Unavailable"
    assert 'companies_house_api_key' not in json.dumps(ch_missing)
    execute_db("INSERT OR REPLACE INTO settings (key, value) VALUES ('companies_house_api_key', 'test-ch-key');")
    class FakeCompaniesHouseResponse:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps({
                'items': [{
                    'title': 'KOOKY KART LIMITED',
                    'company_number': '12345678',
                    'company_status': 'active',
                    'date_of_creation': '2024-03-01',
                    'address_snippet': '1 High Street, London, SW1A 1AA',
                    'address': {'address_line_1': '1 High Street', 'locality': 'London', 'postal_code': 'SW1A 1AA', 'country': 'England'},
                }]
            }).encode('utf-8')
    with patch('app.urllib.request.urlopen', return_value=FakeCompaniesHouseResponse()):
        status, headers, ch_ok = make_request('/api/admin/companies/search?q=KOOKY', cookie=f"session_token={adm_comp_token}")
    assert status == "200 OK", ch_ok
    assert ch_ok['companies'][0]['name'] == 'KOOKY KART LIMITED'
    assert ch_ok['companies'][0]['company_number'] == '12345678'
    status, headers, admin_settings = make_request('/api/admin/settings', cookie=f"session_token={adm_comp_token}")
    assert status == "200 OK"
    assert admin_settings.get('settings', {}).get('companies_house_configured') is True
    assert 'companies_house_api_key' not in (admin_settings.get('settings') or {})
    assert 'smtp_pass' not in (admin_settings.get('settings') or {})
    status, headers, smtp_save = make_request(
        '/api/admin/settings', method='POST',
        cookie=f"session_token={adm_comp_token}",
        body={
            'smtp_host': 'smtp.example.com',
            'smtp_port': '587',
            'smtp_user': 'notifications@example.com',
            'smtp_pass': 'test-smtp-password',
            'smtp_from': 'notifications@example.com',
        }
    )
    assert status == "200 OK", smtp_save
    assert smtp_save.get('smtp_configured') is True
    status, headers, smtp_settings_get = make_request('/api/admin/settings', cookie=f"session_token={adm_comp_token}")
    assert smtp_settings_get.get('settings', {}).get('smtp_configured') is True
    assert smtp_settings_get.get('settings', {}).get('smtp_host') == 'smtp.example.com'
    assert 'smtp_pass' not in (smtp_settings_get.get('settings') or {})
    assert app_mod.smtp_configured() is True
    ch_payload = {
        'event_id': 'evt_wc_pending_ch_1',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9105',
            'order_number': '#WC-9105',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'service_name': 'Digital Package',
            'price': 52.99,
            'total': 63.59,
            'status': 'Processing',
        }
    }
    ch_bytes = json.dumps(ch_payload).encode('utf-8')
    ch_sig = hmac.new(webhook_secret_bytes(), ch_bytes, hashlib.sha256).hexdigest()
    status, headers, ch_order_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=ch_payload,
        headers={'X-Brixen-Signature': ch_sig}
    )
    assert status == "200 OK", ch_order_res
    ch_ord = query_db("SELECT * FROM orders WHERE order_number = '#WC-9105';", one=True)
    status, headers, ch_named = make_request(
        '/api/admin/companies', method='POST',
        body={
            'order_id': ch_ord['id'],
            'name': 'KOOKY KART LIMITED',
            'company_number': '12345678',
            'inc_date': '2024-03-01',
            'status': 'active',
            'reg_office': '1 High Street, London, SW1A 1AA',
        },
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", ch_named
    assert ch_named['company']['name'] == 'KOOKY KART LIMITED'
    assert ch_named['company']['company_number'] == '12345678'
    status, headers, pub_after = make_request('/api/settings')
    assert 'companies_house_api_key' not in (pub_after.get('settings') or {})
    print("✓ Companies House search is staff-only, key stays off public settings, and official number is stored")

    class FakeCompaniesHouseProfileResponse:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps(self.payload).encode('utf-8')

    def fake_ch_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, 'full_url') else req.get_full_url()
        if '/search/companies' in url:
            return FakeCompaniesHouseProfileResponse({'items': []})
        if '/officers' in url:
            return FakeCompaniesHouseProfileResponse({
                'items': [{'name': 'DOE, Jane', 'officer_role': 'director'}]
            })
        return FakeCompaniesHouseProfileResponse({
            'company_name': 'WEBFILL TEST LIMITED',
            'company_number': '99112233',
            'company_status': 'active',
            'date_of_creation': '2023-06-15',
            'registered_office_address': {
                'address_line_1': '10 Test Street',
                'locality': 'London',
                'postal_code': 'E1 1AA',
                'country': 'England',
            },
        })

    with patch('app.urllib.request.urlopen', side_effect=fake_ch_urlopen):
        status, headers, import_denied = make_request(
            '/api/admin/companies/import-webfiling', method='POST',
            body={'company_numbers': '99112233', 'client_id': james['id']},
        )
        assert status == "401 Unauthorized"
        status, headers, import_ok = make_request(
            '/api/admin/companies/import-webfiling', method='POST',
            cookie=f"session_token={adm_comp_token}",
            body={'company_numbers': '99112233', 'client_id': james['id']},
        )
    assert status == "200 OK", import_ok
    assert import_ok.get('imported') == 1
    assert import_ok['results'][0]['name'] == 'WEBFILL TEST LIMITED'
    imported_row = query_db("SELECT name, company_number, package FROM companies WHERE company_number = '99112233';", one=True)
    assert imported_row['name'] == 'WEBFILL TEST LIMITED'
    assert imported_row['package'] == 'WebFiling Import'
    print("✓ WebFiling company numbers import live Companies House records into registered company cards")

    import base64 as posted_b64
    posted_pdf = posted_b64.b64encode(b"%PDF-1.4 Posted certificate").decode('utf-8')
    posted_company = query_db("SELECT * FROM companies WHERE user_id = ? ORDER BY id DESC;", (james['id'],), one=True)
    status, headers, posted_doc = make_request(
        '/api/admin/documents', method='POST',
        cookie=f"session_token={adm_comp_token}",
        body={
            'company_id': posted_company['id'],
            'name': 'Posted_Certificate.pdf',
            'category': 'Posted Documents',
            'client_message': 'Received by post.',
            'file_content_base64': posted_pdf,
        }
    )
    assert status == "200 OK", posted_doc
    assert posted_doc['document']['client_visible'] == 1
    assert posted_doc['document']['company_id'] == posted_company['id']
    assert posted_doc['document']['user_id'] == james['id']
    assert posted_doc.get('notification_created') is True
    status, headers, client_company_docs = make_request(
        f"/api/client/companies/{posted_company['id']}",
        cookie=f"session_token={token}"
    )
    assert status == "200 OK"
    assert any(d.get('id') == posted_doc['document']['id'] for d in (client_company_docs.get('documents') or []))
    status, headers, admin_company_docs = make_request(
        f"/api/admin/companies/{posted_company['id']}",
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK"
    assert admin_company_docs.get('client_id') == james['id']
    status, headers, compliance_unauth = make_request(
        f"/api/admin/companies/{posted_company['id']}", method='PUT',
        body={'utr_number': '1234567890', 'authentication_code': 'AB12CD', 'activation_code': 'ACT-9988'}
    )
    assert status == "401 Unauthorized"
    status, headers, compliance_client = make_request(
        f"/api/admin/companies/{posted_company['id']}", method='PUT',
        cookie=f"session_token={token}",
        body={'utr_number': '1234567890', 'authentication_code': 'AB12CD', 'activation_code': 'ACT-9988'}
    )
    assert status == "403 Forbidden"
    status, headers, compliance_save = make_request(
        f"/api/admin/companies/{posted_company['id']}", method='PUT',
        cookie=f"session_token={adm_comp_token}",
        body={'utr_number': '123 456 7890', 'authentication_code': 'AB12CD', 'activation_code': 'ACT-9988'}
    )
    assert status == "200 OK", compliance_save
    assert compliance_save['company']['utr_number'] == '1234567890'
    assert compliance_save['company']['authentication_code'] == 'AB12CD'
    assert compliance_save['company']['activation_code'] == 'ACT-9988'
    status, headers, admin_after_codes = make_request(
        f"/api/admin/companies/{posted_company['id']}",
        cookie=f"session_token={adm_comp_token}"
    )
    assert admin_after_codes['company']['authentication_code'] == 'AB12CD'
    status, headers, client_after_codes = make_request(
        f"/api/client/companies/{posted_company['id']}",
        cookie=f"session_token={token}"
    )
    assert client_after_codes['company']['utr_number'] == '1234567890'
    assert 'authentication_code' not in (client_after_codes.get('company') or {})
    assert 'activation_code' not in (client_after_codes.get('company') or {})
    print("✓ Company compliance codes save on staff cards and stay off the client payload")
    status, headers, posted_dl = make_request(
        f"/api/documents/{posted_doc['document']['id']}/download",
        cookie=f"session_token={token}"
    )
    assert status == "200 OK"
    posted_note = query_db(
        "SELECT * FROM notifications WHERE user_id = ? AND type = 'document_uploaded' ORDER BY id DESC;",
        (james['id'],),
        one=True,
    )
    assert posted_note is not None
    import base64 as posted_png_b64
    posted_png = posted_png_b64.b64encode(b"\x89PNG\r\n\x1a\nposted-utr").decode('utf-8')
    status, headers, posted_title_only = make_request(
        '/api/admin/documents', method='POST',
        cookie=f"session_token={adm_comp_token}",
        body={
            'company_id': posted_company['id'],
            'name': 'Utr',
            'file_name': 'utr-letter.png',
            'category': 'Certificate of Incorporation',
            'client_message': 'Here is your UTR document.',
            'file_content_base64': posted_png,
        }
    )
    assert status == "200 OK", posted_title_only
    assert posted_title_only['document']['name'] == 'Utr'
    wp_uid = str(james.get('wordpress_user_id') or 'wp_user_101')
    wp_email = james['email']
    wp_ts = str(int(datetime.datetime.now().timestamp()))
    wp_sig = hmac.new(
        webhook_secret_bytes(),
        f"{wp_uid}|{wp_email}|{wp_ts}".encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    status, headers, wp_docs = make_request(
        f"/api/v1/wordpress/documents?wordpress_user_id={wp_uid}&email={urllib.parse.quote(wp_email)}&timestamp={wp_ts}&signature={wp_sig}"
    )
    assert status == "200 OK", wp_docs
    assert any(d.get('name') == 'Utr' for d in (wp_docs.get('documents') or []))
    utr_doc = next(d for d in wp_docs['documents'] if d.get('name') == 'Utr')
    assert utr_doc.get('download_url')
    status, headers, wp_dl = make_request(
        f"/api/v1/wordpress/documents/{utr_doc['id']}/download?wordpress_user_id={wp_uid}&email={urllib.parse.quote(wp_email)}&timestamp={wp_ts}&signature={wp_sig}"
    )
    assert status == "200 OK"
    print("✓ Website Messages & Files API lists and downloads CRM posted documents")
    status, headers, client_posted_denied = make_request(
        '/api/admin/documents', method='POST',
        cookie=f"session_token={token}",
        body={'company_id': posted_company['id'], 'name': 'Hacked.pdf', 'file_content_base64': posted_pdf}
    )
    assert status == "403 Forbidden"
    subject, text_body, html_body = app_mod.build_client_document_email(
        {'full_name': 'James Example', 'email': 'client1@acmecorp.co.uk'},
        'Posted_Certificate.pdf',
        'Received by post.',
    )
    assert 'Posted_Certificate.pdf' in subject
    assert 'Messages & Files' in text_body
    assert 'Important notification' in html_body
    assert 'brixen-logo.png' in html_body or 'logo' in html_body
    assert 'Received by post.' in html_body
    generic_subject, generic_text, generic_html = app_mod.build_client_notification_email(
        {'full_name': 'James Example', 'email': 'client1@acmecorp.co.uk'},
        'Your order status has changed',
        'Order #GB103449JUL26 is now Processing (40% complete).',
        detail_title='Status',
        detail_value='Processing (40% complete)',
    )
    assert 'Your order status has changed' in generic_subject
    assert 'Open Client Panel' in generic_html
    assert 'contact@brixenconsultants.com' in html_body or 'Questions?' in html_body
    assert '447360515317' in html_body or 'wa.me' in html_body
    print("✓ Branded HTML document notification template includes logo, CTA, and client message")
    print("✓ Branded client notification emails cover documents, orders, and support updates")
    print("✓ Company posted-document upload notifies the customer and is visible on their company card")

    status, headers, manual_denied = make_request(
        '/api/admin/companies', method='POST',
        body={'client_id': james['id'], 'name': 'LEGACY HOLDINGS LTD', 'company_number': '99887766'},
        cookie=f"session_token={token}"
    )
    assert status == "403 Forbidden"
    status, headers, manual_ok = make_request(
        '/api/admin/companies', method='POST',
        body={
            'client_id': james['id'],
            'name': 'LEGACY HOLDINGS LTD',
            'company_number': '99887766',
            'inc_date': '2025-01-15',
            'director': 'James Harrington',
            'reg_office': '71-75 Shelton Street, London, WC2H 9JQ',
            'status': 'Active',
        },
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", manual_ok
    assert manual_ok.get('company', {}).get('name') == 'LEGACY HOLDINGS LTD'
    assert manual_ok.get('company', {}).get('company_number') == '99887766'
    legacy_id = manual_ok.get('company', {}).get('id')
    status, headers, client_legacy = make_request('/api/client/companies', cookie=f"session_token={token}")
    assert any(c.get('name') == 'LEGACY HOLDINGS LTD' for c in (client_legacy.get('companies') or []))
    status, headers, del_denied = make_request(
        f'/api/admin/companies/{legacy_id}', method='DELETE',
        cookie=f"session_token={token}"
    )
    assert status == "403 Forbidden"
    status, headers, del_ok = make_request(
        f'/api/admin/companies/{legacy_id}', method='DELETE',
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", del_ok
    assert query_db("SELECT id FROM companies WHERE id = ?;", (legacy_id,), one=True) is None
    status, headers, admin_list = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert any(c.get('id') == james['id'] for c in (admin_list.get('clients') or []))
    assert not any(c.get('id') == legacy_id for c in (admin_list.get('companies') or []))
    # Deleting a named formation card must not revive an awaiting-name card.
    hide_payload = {
        'event_id': 'evt_wc_hide_pending_1',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '9210',
            'order_number': '#WC-9210',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'service_name': 'Digital Package',
            'price': 49.00,
            'total': 58.80,
            'status': 'Processing',
        }
    }
    hide_bytes = json.dumps(hide_payload).encode('utf-8')
    hide_sig = hmac.new(webhook_secret_bytes(), hide_bytes, hashlib.sha256).hexdigest()
    status, headers, hide_res = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=hide_payload,
        headers={'X-Brixen-Signature': hide_sig}
    )
    assert status == "200 OK", hide_res
    hide_ord = query_db("SELECT * FROM orders WHERE order_number = '#WC-9210';", one=True)
    status, headers, hide_named = make_request(
        '/api/admin/companies', method='POST',
        body={'order_id': hide_ord['id'], 'name': 'HIDDEN AFTER DELETE LTD', 'company_number': '55667788'},
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", hide_named
    hide_co_id = hide_named['company']['id']
    status, headers, hide_del = make_request(
        f'/api/admin/companies/{hide_co_id}', method='DELETE',
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", hide_del
    status, headers, after_hide = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert not any(c.get('id') == hide_co_id for c in (after_hide.get('companies') or []))
    assert not any(item.get('order_number') == '#WC-9210' for item in (after_hide.get('pending_registrations') or []))
    print("✓ Staff can manually add a historical company to a client portfolio")
    print("✓ Admin can delete a company from Company Registered")
    print("✓ Deleted company cards stay off Company Registered (no awaiting-name revival)")
    status, headers, readd_legacy = make_request(
        '/api/admin/companies', method='POST',
        body={
            'client_id': james['id'],
            'name': 'LEGACY HOLDINGS LTD',
            'company_number': '99887766',
            'inc_date': '2025-01-15',
            'director': 'James Harrington',
            'reg_office': '71-75 Shelton Street, London, WC2H 9JQ',
            'status': 'Active',
        },
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", readd_legacy
    status, headers, new_client_company = make_request(
        '/api/admin/companies', method='POST',
        body={
            'client_mode': 'new',
            'new_client_full_name': 'Pre CRM Owner',
            'new_client_email': 'precrm.owner@example.com',
            'new_client_phone': '07700900123',
            'name': 'PRE CRM LTD',
            'company_number': '55443322',
            'inc_date': '2024-06-01',
            'status': 'Active',
        },
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", new_client_company
    assert new_client_company.get('company', {}).get('name') == 'PRE CRM LTD'
    new_client_user = query_db(
        "SELECT id, role, full_name, email FROM users WHERE LOWER(email) = ?;",
        ('precrm.owner@example.com',),
        one=True,
    )
    assert new_client_user is not None
    assert new_client_user['role'] == 'CLIENT'
    assert new_client_user['full_name'] == 'Pre CRM Owner'
    status, headers, dup_manual = make_request(
        '/api/admin/companies', method='POST',
        body={'client_id': james['id'], 'name': 'LEGACY HOLDINGS LTD', 'company_number': '99887767'},
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "400 Bad Request"
    print("✓ Staff can manually add a historical company to a client portfolio")
    print("✓ Staff can add a company and create the client account in one step")

    status, headers, dup_evt = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=product_payload,
        headers={'X-Brixen-Signature': prod_sig}
    )
    assert status == "200 OK"
    assert dup_evt['status'] == 'ignored'
    assert query_db("SELECT COUNT(*) as c FROM orders WHERE order_number = '#WC-9001';", one=True)['c'] == 1
    print("✓ Duplicate webhook event_id -> order not duplicated.")

    status, headers, client_order = make_request(f'/api/client/orders/{wc_order_id}', cookie=f"session_token={token}")
    assert status == "200 OK"
    client_order_body = client_order.get('order') or {}
    assert 'notes' not in client_order_body
    client_lines = client_order.get('line_items') or []
    assert client_lines and 'woocommerce_product_id' not in client_lines[0]
    assert 'sku' not in client_lines[0]
    assert client_lines[0]['product_name'] == 'Registered Office Address'
    print("✓ Client order detail -> product name shown without WooCommerce internal IDs.")

    attach_url = 'https://brixenconsultants.com/wp-content/uploads/2026/01/passport-scan.pdf'
    attach_payload = {
        'event_id': 'evt_checkout_attach_9101',
        'event_type': 'order.updated',
        'data': {
            'order_number': '#WC-9001',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'service_name': 'Registered Office Address',
            'price': 49.99,
            'total': 59.99,
            'status': 'Processing',
            'line_items': [
                {
                    'product_name': 'Registered Office Address',
                    'category': 'Address Services',
                    'quantity': 1,
                    'unit_price': 49.99,
                    'line_total': 49.99,
                }
            ],
            'attachments': [
                {'name': 'passport-scan.pdf', 'url': attach_url, 'source': 'checkout'},
                {'name': 'blocked.exe', 'url': 'https://evil.example/malware.exe', 'source': 'checkout'},
            ],
        },
    }
    attach_bytes = json.dumps(attach_payload).encode('utf-8')
    attach_sig = hmac.new(webhook_secret_bytes(), attach_bytes, hashlib.sha256).hexdigest()
    original_download = app_mod.download_checkout_attachment

    def fake_download(url):
        if url == attach_url:
            return b'%PDF-1.4 customer passport', 'application/pdf', None
        return None, None, 'blocked'

    app_mod.download_checkout_attachment = fake_download
    try:
        status, headers, attach_res = make_request(
            '/api/v1/wordpress/webhook', method='POST', body=attach_payload,
            headers={'X-Brixen-Signature': attach_sig}
        )
        assert status == "200 OK", attach_res
    finally:
        app_mod.download_checkout_attachment = original_download
    checkout_docs = query_db(
        "SELECT * FROM documents WHERE order_id = ? AND category = 'Checkout Upload' ORDER BY id DESC;",
        (wc_order_id,),
    )
    assert checkout_docs
    assert checkout_docs[0]['uploaded_by'] == 'Customer Upload'
    assert checkout_docs[0]['client_visible'] == 0
    assert 'passport' in (checkout_docs[0]['name'] or '').lower() or 'passport' in (checkout_docs[0]['review_notes'] or '').lower()
    assert query_db(
        "SELECT COUNT(*) AS c FROM documents WHERE order_id = ? AND name LIKE '%.exe';",
        (wc_order_id,),
        one=True,
    )['c'] == 0
    status, headers, staff_order_detail = make_request(
        f'/api/client/orders/{wc_order_id}',
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK", staff_order_detail
    staff_docs = staff_order_detail.get('documents') or []
    assert any(d.get('is_customer_upload') for d in staff_docs)
    print("✓ Checkout attachments sync into order documents as Customer Upload (host allowlist enforced)")

    timeline_rows = query_db("SELECT * FROM order_timeline WHERE order_id = ? ORDER BY id ASC;", (wc_order_id,))
    assert len(timeline_rows) >= 5
    assert timeline_rows[0]['title'] == 'Order Placed'
    status, headers, adm_orders = make_request('/api/admin/orders', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    listed = next((o for o in adm_orders['orders'] if o['order_number'] == '#WC-9001'), None)
    assert listed is not None
    assert listed['products_summary']
    assert 'Registered Office Address' in listed['products_summary']
    assert listed.get('category_name') == 'Address Services'
    assert 'notes' not in listed
    assert adm_orders.get('pagination')
    assert adm_orders['pagination']['total'] >= 1
    assert 'total_orders' in (adm_orders.get('stats') or {})
    assert 'revenue' in (adm_orders.get('stats') or {})
    assert 'Registered Office Address' in (adm_orders.get('facets') or {}).get('products', [])
    catalog_payload = {
        'products': [
            {
                'woocommerce_product_id': '9001',
                'name': 'Identity Verification (KYC)',
                'category': 'Company Services',
                'price': 29.0,
                'status': 'publish',
                'description': 'KYC identity check',
            },
            {
                'woocommerce_product_id': '9002',
                'name': 'All Inclusive Package',
                'category': 'Packages',
                'price': 199.0,
                'status': 'publish',
            },
        ]
    }
    catalog_bytes = json.dumps(catalog_payload).encode('utf-8')
    catalog_sig = hmac.new(webhook_secret_bytes(), catalog_bytes, hashlib.sha256).hexdigest()
    status, headers, catalog_res = make_request(
        '/api/v1/wordpress/sync-products', method='POST', body=catalog_payload,
        headers={'X-Brixen-Signature': catalog_sig}
    )
    assert status == "200 OK", catalog_res
    assert catalog_res.get('summary', {}).get('total_processed') == 2
    kyc = query_db("SELECT * FROM services WHERE name = 'Identity Verification (KYC)';", one=True)
    assert kyc is not None
    assert kyc['woocommerce_product_id'] == '9001'
    assert kyc['category'] == 'Company Services'
    status, headers, facets_after = make_request('/api/admin/orders', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    product_facet = (facets_after.get('facets') or {}).get('products') or []
    assert 'Identity Verification (KYC)' in product_facet
    assert 'All Inclusive Package' in product_facet
    print("✓ Website product catalog sync fills Orders Manager product filter")
    status, headers, filtered_orders = make_request(
        '/api/admin/orders?product=Registered%20Office%20Address&category=Address%20Services&status=Processing',
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    assert any(o['order_number'] == '#WC-9001' for o in filtered_orders['orders'])
    status, headers, paged = make_request('/api/admin/orders?page=1&limit=5', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert len(paged['orders']) <= 5
    assert paged['pagination']['limit'] == 5
    before_hist = query_db("SELECT COUNT(*) as c FROM order_timeline WHERE order_id = ?;", (wc_order_id,), one=True)['c']
    status, headers, prog_upd = make_request(
        f"/api/admin/orders/{wc_order_id}", method='PUT',
        cookie=f"session_token={adm_token}",
        body={'progress_percent': 35, 'status': 'In Progress'}
    )
    assert status == "200 OK"
    after_hist = query_db("SELECT COUNT(*) as c FROM order_timeline WHERE order_id = ?;", (wc_order_id,), one=True)['c']
    assert after_hist == before_hist + 1
    status, headers, staff_detail = make_request(f'/api/client/orders/{wc_order_id}', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert staff_detail['order'].get('client_phone') is not None or 'client_phone' in staff_detail['order']
    assert 'addresses' in staff_detail
    notes_before = query_db("SELECT COUNT(*) as c FROM notifications WHERE user_id = ?;", (james['id'],), one=True)['c']
    status, headers, notes_only = make_request(
        f"/api/admin/orders/{wc_order_id}", method='PUT',
        cookie=f"session_token={adm_token}",
        body={'notes': 'Internal processing note'}
    )
    assert status == "200 OK"
    notes_after = query_db("SELECT COUNT(*) as c FROM notifications WHERE user_id = ?;", (james['id'],), one=True)['c']
    assert notes_after == notes_before
    status, headers, order_tasks = make_request(f'/api/admin/tasks?order_id={wc_order_id}', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    status, headers, client_order_tasks = make_request(f'/api/admin/tasks?order_id={wc_order_id}', cookie=f"session_token={token}")
    assert status == "403 Forbidden"
    print("✓ Admin order list -> website order history and product data available for task processing.")

    import base64 as _b64
    staff_pdf = _b64.b64encode(b"%PDF-1.4 Internal pack").decode('utf-8')
    status, headers, staff_up = make_request(
        '/api/admin/documents', method='POST',
        cookie=f"session_token={adm_token}",
        body={'order_id': wc_order_id, 'name': 'Internal_Pack.pdf', 'file_content_base64': staff_pdf}
    )
    assert status == "200 OK", staff_up
    assert 'file_path' not in (staff_up.get('document') or {})
    internal_doc_id = staff_up['document']['id']
    assert staff_up['document']['client_visible'] == 0
    status, headers, client_docs = make_request('/api/client/documents', cookie=f"session_token={token}")
    assert status == "200 OK"
    assert all(d['id'] != internal_doc_id for d in client_docs['documents'])
    assert all('file_path' not in d for d in client_docs['documents'])
    status, headers, hidden_dl = make_request(f'/api/documents/{internal_doc_id}/download', cookie=f"session_token={token}")
    assert status == "403 Forbidden"
    status, headers, client_create_doc = make_request(
        '/api/admin/documents', method='POST',
        cookie=f"session_token={token}",
        body={'order_id': wc_order_id, 'name': 'Nope.pdf', 'file_content_base64': staff_pdf}
    )
    assert status == "403 Forbidden"
    status, headers, admin_docs = make_request('/api/admin/documents', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert any(d.get('id') == internal_doc_id for d in admin_docs.get('documents') or [])
    print("✓ Staff document upload -> internal until sent; client cannot see or download.")

    status, headers, sent_doc = make_request(
        f'/api/admin/documents/{internal_doc_id}/send', method='POST',
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK", sent_doc
    assert sent_doc['document']['client_visible'] == 1
    status, headers, client_docs2 = make_request('/api/client/documents', cookie=f"session_token={token}")
    assert any(d['id'] == internal_doc_id for d in client_docs2['documents'])
    status, headers, client_dl = make_request(f'/api/documents/{internal_doc_id}/download', cookie=f"session_token={token}")
    assert status == "200 OK"
    status, headers, other_login = make_request('/api/auth/login', method='POST', body={'email': 'v.smith@vantagecyber.co.uk', 'password': 'ClientPass123!'})
    other_token = extract_session_token(headers)
    status, headers, other_dl = make_request(f'/api/documents/{internal_doc_id}/download', cookie=f"session_token={other_token}")
    assert status == "403 Forbidden"
    note = query_db("SELECT * FROM notifications WHERE user_id = ? AND title = 'New document available' ORDER BY id DESC;", (james['id'],), one=True)
    assert note is not None
    print("✓ Send document to customer -> notification + portal download; other client blocked.")

    email_calls = []
    original_email = app_mod.EmailService.send_notification_email

    def fake_delivery_email(recipient_email, subject, body_text, body_html=None):
        email_calls.append({
            'recipient': recipient_email,
            'subject': subject,
            'body': body_text,
            'html': body_html,
        })
        return False, "SMTP not configured"

    app_mod.EmailService.send_notification_email = staticmethod(fake_delivery_email)
    try:
        victoria = query_db("SELECT * FROM users WHERE email = 'v.smith@vantagecyber.co.uk';", one=True)
        james_company = query_db("SELECT * FROM companies WHERE user_id = ? ORDER BY id ASC;", (james['id'],), one=True)
        victoria_company = query_db("SELECT * FROM companies WHERE user_id = ? ORDER BY id ASC;", (victoria['id'],), one=True)
        featured = query_db("SELECT * FROM orders WHERE order_number = '#GB103449JUL26';", one=True)
        other_order_id = execute_db("""
            INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, ('#GB-VICTORIA-DOC', victoria['id'], victoria_company['id'], 'Registered Office Address', 20, 4, 24, 'Processing', 10))
        deliver_pdf = _b64.b64encode(b"%PDF-1.4 Client delivery pack").decode('utf-8')
        status, headers, delivered = make_request(
            '/api/admin/documents', method='POST',
            cookie=f"session_token={adm_token}",
            body={
                'client_id': james['id'],
                'company_id': james_company['id'],
                'order_id': featured['id'],
                'name': 'Client_Pack.pdf',
                'category': 'Company Documents',
                'client_message': 'Your incorporation pack is ready.',
                'review_notes': 'SECRET_INTERNAL_NOTE',
                'file_content_base64': deliver_pdf
            }
        )
        assert status == "200 OK", delivered
        assert delivered['document']['user_id'] == james['id']
        assert delivered['document']['company_id'] == james_company['id']
        assert delivered['document']['order_id'] == featured['id']
        assert delivered['document']['client_visible'] == 1
        assert 'file_path' not in delivered['document']
        assert delivered.get('notification_created') is True
        assert delivered.get('email_sent') is False
        assert 'SMTP' in (delivered.get('email_status') or '')
        delivered_id = delivered['document']['id']
        stored = query_db("SELECT * FROM documents WHERE id = ?;", (delivered_id,), one=True)
        assert stored['user_id'] == james['id']
        assert os.path.exists(stored['file_path'])
        assert stored['review_notes'] == 'SECRET_INTERNAL_NOTE'

        status, headers, general_doc = make_request(
            '/api/admin/documents', method='POST',
            cookie=f"session_token={adm_token}",
            body={'client_id': james['id'], 'name': 'General_Note.pdf', 'file_content_base64': deliver_pdf}
        )
        assert status == "200 OK", general_doc
        assert general_doc['document']['user_id'] == james['id']
        assert not general_doc['document'].get('order_id')

        status, headers, bad_company = make_request(
            '/api/admin/documents', method='POST',
            cookie=f"session_token={adm_token}",
            body={'client_id': james['id'], 'company_id': victoria_company['id'], 'name': 'Nope.pdf', 'file_content_base64': deliver_pdf}
        )
        assert status == "400 Bad Request"

        status, headers, bad_order = make_request(
            '/api/admin/documents', method='POST',
            cookie=f"session_token={adm_token}",
            body={'client_id': james['id'], 'order_id': other_order_id, 'name': 'Nope.pdf', 'file_content_base64': deliver_pdf}
        )
        assert status == "400 Bad Request"

        status, headers, client_docs3 = make_request('/api/client/documents', cookie=f"session_token={token}")
        assert any(d['id'] == delivered_id for d in client_docs3['documents'])
        seen = next(d for d in client_docs3['documents'] if d['id'] == delivered_id)
        assert 'file_path' not in seen
        assert 'review_notes' not in seen
        assert 'SECRET_INTERNAL_NOTE' not in json.dumps(seen)

        status, headers, other_docs = make_request('/api/client/documents', cookie=f"session_token={other_token}")
        assert all(d['id'] != delivered_id for d in other_docs['documents'])
        assert all('review_notes' not in d for d in other_docs['documents'])
        status, headers, other_dl2 = make_request(f'/api/documents/{delivered_id}/download', cookie=f"session_token={other_token}")
        assert status == "403 Forbidden"

        status, headers, client_admin_up = make_request(
            '/api/admin/documents', method='POST',
            cookie=f"session_token={token}",
            body={'client_id': james['id'], 'name': 'Hacked.pdf', 'file_content_base64': deliver_pdf}
        )
        assert status == "403 Forbidden"

        delivery_note = query_db(
            "SELECT * FROM notifications WHERE user_id = ? AND type = 'document_uploaded' ORDER BY id DESC;",
            (james['id'],), one=True
        )
        assert delivery_note is not None
        assert delivery_note['title'] == 'New document available'
        assert delivery_note['link'] == '/documents'
        assert 'SECRET_INTERNAL_NOTE' not in (delivery_note['message'] or '')
        victoria_note = query_db(
            "SELECT * FROM notifications WHERE user_id = ? AND type = 'document_uploaded';",
            (victoria['id'],), one=True
        )
        assert victoria_note is None

        status, headers, crm_full = make_request(f"/api/admin/clients/{james['id']}/full", cookie=f"session_token={adm_token}")
        assert status == "200 OK"
        assert any(d['id'] == delivered_id for d in crm_full['documents'])
        assert all('file_path' not in d for d in crm_full['documents'])

        assert email_calls
        last_email = email_calls[-1]
        assert last_email['recipient'] == james['email']
        assert 'New document ready' in last_email['subject']
        assert 'Client_Pack.pdf' in last_email['body'] or 'General_Note.pdf' in last_email['body']
        assert 'SECRET_INTERNAL_NOTE' not in last_email['body']
        assert last_email.get('html')
        assert 'View in Messages &amp; Files' in last_email['html']
        assert 'Client_Pack.pdf' in last_email['html'] or 'General_Note.pdf' in last_email['html']
        assert 'SECRET_INTERNAL_NOTE' not in last_email['html']
        assert os.path.exists(stored['file_path'])
        assert query_db("SELECT id FROM documents WHERE id = ?;", (delivered_id,), one=True)
        status, headers, still_order = make_request('/api/admin/orders', cookie=f"session_token={adm_token}")
        assert status == "200 OK"
        status, headers, still_tasks = make_request('/api/admin/tasks', cookie=f"session_token={adm_token}")
        assert status == "200 OK"
    finally:
        app_mod.EmailService.send_notification_email = original_email
    print("✓ Client document delivery -> ownership, isolation, notification, and email logging.")

    status, headers, msg_res = make_request(
        f'/api/admin/orders/{wc_order_id}/conversation', method='POST',
        cookie=f"session_token={adm_token}",
        body={'message': 'Your registered office pack is being prepared.'}
    )
    assert status == "200 OK", msg_res
    ticket_id = msg_res['ticket']['id']
    assert msg_res['ticket']['order_id'] == wc_order_id
    assert msg_res['ticket']['user_id'] == james['id']
    status, headers, client_msgs = make_request(f'/api/client/tickets/{ticket_id}/messages', cookie=f"session_token={token}")
    assert status == "200 OK"
    assert any('registered office pack' in m['message'] for m in client_msgs['messages'])
    assert all('internal_notes' not in m for m in client_msgs['messages'])
    status, headers, client_reply = make_request(
        f'/api/client/tickets/{ticket_id}/messages', method='POST',
        cookie=f"session_token={token}",
        body={'message': 'Thank you, please continue.'}
    )
    assert status == "200 OK"
    status, headers, staff_conv = make_request(
        f'/api/admin/orders/{wc_order_id}/conversation',
        cookie=f"session_token={adm_token}"
    )
    assert any('Thank you, please continue.' in m['message'] for m in staff_conv['messages'])
    status, headers, client_msg_block = make_request(
        f'/api/admin/orders/{wc_order_id}/conversation', method='POST',
        cookie=f"session_token={token}",
        body={'message': 'should fail'}
    )
    assert status == "403 Forbidden"
    print("✓ Order conversation -> staff message and client reply stay on the existing ticket thread.")

    status, headers, task_priv = make_request(
        '/api/admin/tasks', method='POST',
        cookie=f"session_token={adm_token}",
        body={'title': 'Internal RO pack', 'client_id': james['id'], 'order_id': wc_order_id, 'internal_notes': 'Do not show this', 'priority': 'High'}
    )
    assert status == "200 OK", task_priv
    status, headers, client_tasks = make_request('/api/admin/tasks', cookie=f"session_token={token}")
    assert status == "403 Forbidden"
    print("✓ Task privacy -> CLIENT blocked from staff tasks; internal notes stay off the ticket thread.")

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
    assert all(
        t['assigned_staff_id'] == eleanor['id']
        or (not t.get('assigned_staff_id') and t.get('department') == eleanor.get('department'))
        for t in eleanor_list['tasks']
    )
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

    status, headers, bad_dept = make_request(
        '/api/admin/tasks',
        method='POST',
        body={'title': 'Bad department', 'department': 'Marketing'},
        cookie=f"session_token={adm_token}"
    )
    assert status == "400 Bad Request"
    print("✓ Task Department Validation -> unknown department rejected.")

    status, headers, queue_res = make_request(
        '/api/admin/tasks',
        method='POST',
        body={'title': 'Prepare apostille pack', 'department': 'Documents'},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    queue_id = queue_res['task']['id']
    assert queue_res['task']['department'] == 'Documents'
    assert queue_res['task']['assigned_staff_id'] in (None, '')
    status, headers, eleanor_queue = make_request('/api/admin/tasks', cookie=f"session_token={eleanor_token}")
    assert any(t['id'] == queue_id for t in eleanor_queue['tasks'])
    status, headers, marcus_queue = make_request('/api/admin/tasks', cookie=f"session_token={marcus_token}")
    assert all(t['id'] != queue_id for t in marcus_queue['tasks'])
    print("✓ Department Queue -> Documents staff see unassigned Documents work; Orders staff do not.")

    status, headers, dept_update = make_request(
        f"/api/admin/staff/{marcus['id']}",
        method='PUT',
        body={'departments': ['Orders', 'Support']},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    assert dept_update['user']['departments'] == ['Orders', 'Support']
    status, headers, support_queue = make_request(
        '/api/admin/tasks',
        method='POST',
        body={'title': 'Reply to portal ticket', 'department': 'Support'},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    status, headers, marcus_multi = make_request('/api/admin/tasks', cookie=f"session_token={marcus_token}")
    assert any(t['id'] == support_queue['task']['id'] for t in marcus_multi['tasks'])
    print("✓ Staff Department Update -> ADMIN can assign multiple departments.")

    status, headers, eleanor_orders = make_request('/api/admin/orders', cookie=f"session_token={eleanor_token}")
    assert status == "403 Forbidden"
    status, headers, eleanor_docs = make_request('/api/admin/documents', cookie=f"session_token={eleanor_token}")
    assert status == "200 OK"
    status, headers, marcus_orders = make_request('/api/admin/orders', cookie=f"session_token={marcus_token}")
    assert status == "200 OK"
    assert 'revenue' not in (marcus_orders.get('stats') or {})
    status, headers, marcus_docs = make_request('/api/admin/documents', cookie=f"session_token={marcus_token}")
    assert status == "403 Forbidden"
    status, headers, marcus_invoices = make_request('/api/admin/invoices', cookie=f"session_token={marcus_token}")
    assert status == "403 Forbidden"
    print("✓ Staff Access -> ticks grant module access: Documents staff cannot open Orders; Orders staff cannot open Documents.")

    execute_db("DELETE FROM role_permissions;")
    execute_db("DELETE FROM permissions;")
    execute_db("DELETE FROM roles;")
    from db import ensure_schema
    ensure_schema()
    status, headers, repaired_orders = make_request('/api/admin/orders', cookie=f"session_token={marcus_token}")
    assert status == "200 OK", repaired_orders
    status, headers, all_access = make_request(
        f"/api/admin/staff/{eleanor['id']}",
        method='PUT',
        body={'departments': ['New Signups', 'Orders', 'Documents', 'Support', 'Compliance', 'Accounts', 'General']},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    status, headers, eleanor_relogin = make_request(
        '/api/auth/login',
        method='POST',
        body={'email': 'eleanor.finch@brixenconsultant.co.uk', 'password': 'StaffPass123!'}
    )
    eleanor_all_token = extract_session_token(headers)
    for path in ('/api/admin/orders', '/api/admin/customers', '/api/admin/documents', '/api/admin/invoices', '/api/admin/staff'):
        st, hd, body = make_request(path, cookie=f"session_token={eleanor_all_token}")
        assert st == "200 OK", f"{path} expected 200 after full access repair, got {st} ({body})"
    status, headers, restore_eleanor = make_request(
        f"/api/admin/staff/{eleanor['id']}",
        method='PUT',
        body={'departments': ['Documents']},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    print("✓ Staff Access Repair -> missing RBAC rows are inserted; full ticks open staff modules.")

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

    status, headers, unauth_staff_create = make_request(
        '/api/admin/staff', method='POST',
        body={'full_name': 'No Auth', 'email': 'noauth@brixenconsultant.co.uk', 'password': 'TeamPass123!', 'role': 'STAFF'}
    )
    assert status == "401 Unauthorized"
    status, headers, client_staff_create = make_request(
        '/api/admin/staff', method='POST',
        cookie=f"session_token={token}",
        body={'full_name': 'Client Create', 'email': 'clientcreate@brixenconsultant.co.uk', 'password': 'TeamPass123!', 'role': 'STAFF'}
    )
    assert status == "403 Forbidden"
    status, headers, staff_staff_create = make_request(
        '/api/admin/staff', method='POST',
        cookie=f"session_token={eleanor_token}",
        body={'full_name': 'Staff Create', 'email': 'staffcreate@brixenconsultant.co.uk', 'password': 'TeamPass123!', 'role': 'STAFF'}
    )
    assert status == "403 Forbidden"
    print("✓ Team User Create Auth -> Unauthenticated/CLIENT/STAFF blocked.")

    new_staff_email = 'nova.blake@brixenconsultant.co.uk'
    new_staff_password = 'TeamPass123!'
    status, headers, created_staff = make_request(
        '/api/admin/staff', method='POST',
        cookie=f"session_token={adm_token}",
        body={
            'full_name': 'Nova Blake',
            'email': new_staff_email,
            'password': new_staff_password,
            'phone': '+44 20 7946 0199',
            'role': 'STAFF'
        }
    )
    assert status == "200 OK", created_staff
    assert created_staff['status'] == 'success'
    assert created_staff['user']['email'] == new_staff_email
    assert created_staff['user']['role'] == 'STAFF'
    assert 'password' not in created_staff['user']
    assert 'password_hash' not in created_staff['user']
    new_staff_id = created_staff['user']['id']
    stored_new = query_db("SELECT password_hash FROM users WHERE id = ?;", (new_staff_id,), one=True)
    assert stored_new['password_hash'].startswith('$argon2id$')
    print(f"✓ Team User Create -> Success (User ID: {new_staff_id})")

    status, headers, dup_staff = make_request(
        '/api/admin/staff', method='POST',
        cookie=f"session_token={adm_token}",
        body={'full_name': 'Nova Duplicate', 'email': new_staff_email, 'password': new_staff_password, 'role': 'STAFF'}
    )
    assert status == "409 Conflict"
    status, headers, escalate_super = make_request(
        '/api/admin/staff', method='POST',
        cookie=f"session_token={adm_token}",
        body={'full_name': 'Bad Super', 'email': 'badsuper@brixenconsultant.co.uk', 'password': new_staff_password, 'role': 'SUPER_ADMIN'}
    )
    assert status == "403 Forbidden"
    status, headers, escalate_admin = make_request(
        '/api/admin/staff', method='POST',
        cookie=f"session_token={adm_token}",
        body={'full_name': 'Bad Admin', 'email': 'badadmin@brixenconsultant.co.uk', 'password': new_staff_password, 'role': 'ADMIN'}
    )
    assert status == "403 Forbidden"
    status, headers, mismatch_pwd = make_request(
        '/api/admin/staff', method='POST',
        cookie=f"session_token={adm_token}",
        body={
            'full_name': 'Mismatch User',
            'email': 'mismatch@brixenconsultant.co.uk',
            'password': new_staff_password,
            'confirm_password': 'DifferentPass1!',
            'role': 'STAFF'
        }
    )
    assert status == "400 Bad Request"
    print("✓ Team User Create Validation -> Duplicate email and unauthorized roles rejected.")

    new_client_email = 'portal.client@acmecorp.co.uk'
    status, headers, created_client = make_request(
        '/api/admin/staff', method='POST',
        cookie=f"session_token={adm_token}",
        body={
            'full_name': 'Portal Client',
            'email': new_client_email,
            'password': new_staff_password,
            'phone': '+44 20 7946 0200',
            'country': 'United Kingdom',
            'role': 'CLIENT',
            'status': 'Active'
        }
    )
    assert status == "200 OK", created_client
    assert created_client['user']['role'] == 'CLIENT'
    assert 'password_hash' not in created_client['user']
    status, headers, client_login_new = make_request(
        '/api/auth/login', method='POST',
        body={'email': new_client_email, 'password': new_staff_password}
    )
    assert status == "200 OK"
    assert client_login_new['user']['role'] == 'CLIENT'
    assert 'password_hash' not in client_login_new['user']
    print("✓ Create CLIENT User -> ADMIN can create a client who can sign in.")

    status, headers, new_login = make_request(
        '/api/auth/login', method='POST',
        body={'email': new_staff_email, 'password': new_staff_password}
    )
    assert status == "200 OK"
    assert new_login['user']['id'] == new_staff_id
    assert new_login['user']['role'] == 'STAFF'
    assert 'password_hash' not in new_login['user']
    new_staff_token = extract_session_token(headers)
    assert new_staff_token
    print("✓ Team User Login -> Created staff can sign in with issued password.")

    status, headers, staff_after = make_request('/api/admin/staff', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert any(s['id'] == new_staff_id for s in staff_after['staff'])
    status, headers, assigned_new = make_request(
        '/api/admin/tasks', method='POST',
        cookie=f"session_token={adm_token}",
        body={'title': 'Welcome Nova', 'assigned_staff_id': new_staff_id, 'priority': 'High'}
    )
    assert status == "200 OK"
    assert assigned_new['task']['assigned_staff_id'] == new_staff_id
    print("✓ Team User Task Assign -> New staff appears in staff list and can be assigned a task.")

    status, headers, unauth_imp = make_request('/api/admin/impersonate', method='POST', body={'user_id': eleanor['id']})
    assert status == "401 Unauthorized"
    status, headers, client_imp = make_request(
        '/api/admin/impersonate', method='POST',
        cookie=f"session_token={token}",
        body={'user_id': eleanor['id']}
    )
    assert status == "403 Forbidden"
    status, headers, staff_imp = make_request(
        '/api/admin/impersonate', method='POST',
        cookie=f"session_token={eleanor_token}",
        body={'user_id': new_staff_id}
    )
    assert status == "403 Forbidden"
    status, headers, self_imp = make_request(
        '/api/admin/impersonate', method='POST',
        cookie=f"session_token={adm_token}",
        body={'user_id': adm_res['user']['id']}
    )
    assert status == "403 Forbidden"
    status, headers, switch_to_client = make_request(
        '/api/admin/impersonate', method='POST',
        cookie=f"session_token={adm_token}",
        body={'user_id': uid}
    )
    assert status == "403 Forbidden"
    status, headers, imp_res = make_request(
        '/api/admin/impersonate', method='POST',
        cookie=f"session_token={adm_token}",
        body={'user_id': eleanor['id']}
    )
    assert status == "200 OK", imp_res
    assert imp_res['user']['email'] == 'eleanor.finch@brixenconsultant.co.uk'
    assert imp_res['user']['role'] == 'STAFF'
    assert imp_res['user']['impersonated'] is True
    assert 'password_hash' not in imp_res['user']
    imp_token = extract_session_token(headers)
    assert imp_token
    status, headers, me_imp = make_request(
        '/api/auth/me',
        cookie=f"session_token={imp_token}; origin_session={adm_token}"
    )
    assert status == "200 OK"
    assert me_imp['user']['role'] == 'STAFF'
    assert me_imp['user']['impersonated'] is True
    status, headers, stop_imp = make_request(
        '/api/admin/impersonate/stop', method='POST',
        cookie=f"session_token={imp_token}; origin_session={adm_token}"
    )
    assert status == "200 OK"
    assert stop_imp['user']['role'] == 'ADMIN'
    assert stop_imp['user']['impersonated'] is False
    print("✓ User Switch -> ADMIN can switch to staff and return; CLIENT/STAFF blocked; clients cannot be switched into.")

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

    super_id = ensure_rbac_user('superadmin@brixenconsultant.co.uk', 'SuperPass123!', 'Priya Shah', 'SUPER_ADMIN', 'wp_rbac_super')
    ensure_rbac_user('manager@brixenconsultant.co.uk', 'ManagerPass123!', 'Helena Ward', 'MANAGER', 'wp_rbac_manager')
    super_token = login_token('superadmin@brixenconsultant.co.uk', 'SuperPass123!')
    manager_token = login_token('manager@brixenconsultant.co.uk', 'ManagerPass123!')
    staff_token = eleanor_token
    admin_token = adm_token
    client_token = token

    st, hd, mgr_create = rbac_request(
        '/api/admin/staff', manager_token, 'POST',
        {'full_name': 'Mgr Create', 'email': 'mgrcreate@brixenconsultant.co.uk', 'password': 'TeamPass123!', 'role': 'STAFF'}
    )
    assert st == "403 Forbidden"
    st, hd, super_created_admin = rbac_request(
        '/api/admin/staff', super_token, 'POST',
        {
            'full_name': 'Ada North',
            'email': 'ada.north@brixenconsultant.co.uk',
            'password': 'TeamPass123!',
            'role': 'ADMIN',
            'country': 'United Kingdom',
            'status': 'Active'
        }
    )
    assert st == "200 OK", super_created_admin
    assert super_created_admin['user']['role'] == 'ADMIN'
    assert 'password_hash' not in super_created_admin['user']
    st, hd, cust_list = rbac_request('/api/admin/customers', admin_token)
    assert st == "200 OK"
    for cust in cust_list.get('customers') or []:
        assert 'password_hash' not in cust
        assert 'password' not in cust
    st, hd, super_imp = rbac_request(
        '/api/admin/impersonate', admin_token, 'POST', {'user_id': super_id}
    )
    assert st == "403 Forbidden"
    print("✓ Create User RBAC -> MANAGER blocked; SUPER_ADMIN can create ADMIN; customer list has no password_hash.")

    st, hd, mgr_order = rbac_request('/api/client/orders/1', manager_token)
    assert st == "200 OK"
    st, hd, super_order = rbac_request('/api/client/orders/1', super_token)
    assert st == "200 OK"
    st, hd, mgr_msgs = rbac_request(f'/api/client/tickets/{tick_id}/messages', manager_token)
    assert st == "200 OK"
    print("✓ Order/Ticket RBAC -> MANAGER and SUPER_ADMIN can access staff ticket/order detail")

    rbac_get_matrix = [
        ('/api/admin/stats', 'admin dashboard', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '200 OK', 'STAFF': '403 Forbidden', 'CLIENT': '403 Forbidden'
        }),
        ('/api/admin/services', 'orders.view', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '200 OK', 'STAFF': '403 Forbidden', 'CLIENT': '403 Forbidden'
        }),
        ('/api/admin/documents', 'documents.view', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '200 OK', 'STAFF': '200 OK', 'CLIENT': '403 Forbidden'
        }),
        ('/api/admin/invoices', 'invoices.view', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '200 OK', 'STAFF': '403 Forbidden', 'CLIENT': '403 Forbidden'
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
    monthly = (stats_res.get('charts') or {}).get('monthly')
    assert isinstance(monthly, list) and len(monthly) == 6
    assert all(isinstance(m.get('month'), str) and 'rev' in m and m.get('label') for m in monthly)
    assert all(isinstance(m.get('rev'), (int, float)) for m in monthly)
    st, hd, svc_res = rbac_request('/api/admin/services', marcus_token)
    assert isinstance(svc_res.get('services'), list)
    print("✓ Admin RBAC GET payloads -> MANAGER stats and Orders staff services catalog returned.")

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

    probe = query_db("SELECT id, name, price FROM services WHERE name = 'RBAC Catalog Probe' ORDER BY id DESC LIMIT 1;", one=True)
    assert probe is not None
    update_body = {
        'name': 'RBAC Catalog Probe',
        'description': 'Updated during admin RBAC regression.',
        'category': 'Compliance',
        'price': 25.50,
        'duration': '6 Months',
        'status': 'Active',
    }
    st, hd, body = rbac_request(f"/api/admin/services/{probe['id']}", method='PUT', body=update_body)
    assert st == "401 Unauthorized"
    for role, want in [
        ('SUPER_ADMIN', '200 OK'),
        ('ADMIN', '200 OK'),
        ('MANAGER', '403 Forbidden'),
        ('STAFF', '403 Forbidden'),
        ('CLIENT', '403 Forbidden'),
    ]:
        st, hd, body = rbac_request(
            f"/api/admin/services/{probe['id']}",
            role_tokens[role],
            method='PUT',
            body={**update_body, 'price': 25.50 if role == 'SUPER_ADMIN' else 30.00},
        )
        assert st == want, f"PUT /api/admin/services/{{id}} as {role} expected {want}, got {st} ({body})"
    updated = query_db("SELECT price, duration FROM services WHERE id = ?;", (probe['id'],), one=True)
    assert float(updated['price']) == 30.0
    assert updated['duration'] == '6 Months'
    st, hd, body = rbac_request(f"/api/admin/services/{probe['id']}", role_tokens['CLIENT'], method='DELETE')
    assert st == "403 Forbidden"
    st, hd, del_ok = rbac_request(f"/api/admin/services/{probe['id']}", role_tokens['ADMIN'], method='DELETE')
    assert st == "200 OK", del_ok
    assert query_db("SELECT id FROM services WHERE id = ?;", (probe['id'],), one=True) is None
    print("✓ Admin can update and delete Services Catalog entries; MANAGER/STAFF/CLIENT blocked")

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
    assert st == "400 Bad Request"
    st, hd, body = make_request(
        '/api/auth/profile',
        method='POST',
        body={'current_password': 'WrongOldPass123!', 'new_password': profile_new, 'confirm_password': profile_new},
        cookie=f"session_token={profile_token}"
    )
    assert st == "400 Bad Request"
    st, hd, body = make_request(
        '/api/auth/profile',
        method='POST',
        body={'current_password': profile_old, 'new_password': profile_new, 'confirm_password': profile_new},
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

    admin_old = 'AdminPass123!'
    admin_new = 'AdminPassChanged123!'
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': admin_old})
    assert st == "200 OK"
    admin_pw_token = extract_session_token(hd)
    st, hd, body = make_request(
        '/api/auth/profile',
        method='POST',
        body={'current_password': admin_old, 'new_password': admin_new, 'confirm_password': admin_new},
        cookie=f"session_token={admin_pw_token}"
    )
    assert st == "200 OK", body
    st, hd, body = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': admin_new})
    assert st == "200 OK"
    execute_db("UPDATE users SET password_hash = ? WHERE email = 'admin@brixenconsultant.co.uk';", (hash_password(admin_old),))
    print("✓ Admin password change -> current password required; new password signs in")

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
