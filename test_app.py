import os
os.environ.setdefault('CRM_TESTING', '1')
import sys
import json
import io
import re
import hmac
import hashlib
import datetime
import urllib.parse
from unittest.mock import patch
from app import application, checkout_form_fields_from_payload, coalesce_checkout_address_fields, extract_order_company_name
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
    assert 'value="Partial Paid"' in body
    assert 'id="view-admin-accountancy"' in body
    assert 'data-view="admin-accountancy"' in body
    assert 'id="modal-accountancy-books"' in body
    assert 'id="accountancy-stat-issues"' in body
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
    with open(os.path.join(os.path.dirname(__file__), 'static/css/styles.css'), 'r', encoding='utf-8') as f:
        css_src = f.read()
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
    assert 'defaultPortalView' in js_src
    assert 'initialPortalView' in js_src
    assert 'syncViewHash' in js_src
    assert 'viewFromHash' in js_src
    assert 'data-customer-action' in js_src
    assert 'data-order-action="change-details"' in js_src
    assert 'editCheckout' in js_src
    assert 'staffCheckoutFieldsForEdit' in js_src
    assert 'Change details' in js_src
    assert 'id="staff-order-owner"' in html_src
    assert 'company_owner' in js_src
    assert 'owner_form_email' in js_src
    assert 'orders-filter-bar' in html_src
    assert 'All work states' not in html_src
    assert 'table-action-btns' in js_src
    assert 'table-action-btns' in css_src
    assert 'setClientPortalPassword(${c.id}' not in js_src
    assert 'Personal 11 dijits Code' in js_src
    assert 'Live records are checked when you open this page.' in js_src
    assert 'savePendingCompanyName' in js_src
    assert 'Type a name to check Companies House' in html_src
    assert 'portfolioDirectorNames' in js_src
    assert 'id="portfolio-detail-owner-label">Director</span>' in html_src
    assert 'data-tab="support"' not in html_src
    assert 'id="portfolio-detail-activity"' in html_src
    assert 'company.sic_activities' in js_src
    assert 'looksLikeUkCompanyNumber(company && company.company_number)' in js_src
    assert 'portfolio-rename-form' in css_src
    assert 'searchCompaniesHouseForRename' in js_src
    assert 'Activation code</label>' not in js_src
    assert 'openAccountancyBooks' in js_src
    assert 'data-accountancy-action' in js_src
    assert 'PSC verification' in js_src
    assert 'Identity verification' in js_src
    assert 'id="modal-accountancy-books"' in html_src
    assert 'Bank statement' in html_src
    assert 'Persons with Significant Control' in js_src or 'PSC register' in js_src
    assert 'acct-badge' in css_src
    assert 'accountancy-stats' in css_src
    assert 'canViewRevenue' in js_src
    assert 'applyAdminRevenueCards' in js_src
    assert 'applyAdminOrderFinanceVisibility' in js_src
    assert 'hide-order-finance' in js_src
    assert 'data-admin-revenue' in html_src
    assert '<th>Owner</th>' in html_src
    assert 'app.js?v=218.0' in html_src
    assert 'emailStaffOrderPaymentDetails' in js_src
    assert 'styles.css?v=138.0' in html_src
    assert 'capturePageScroll' in js_src
    assert 'deletePortfolioDocument' in js_src
    assert 'bulkDeleteAdminCompanies' in js_src
    assert 'bulk-edit-companies' in html_src
    assert '/api/admin/companies/bulk-delete' in open('app.py', encoding='utf-8').read()
    assert 'schedule_company_detail_refresh' in open('app.py', encoding='utf-8').read()
    assert 'LUCIDE_SRC' in js_src
    assert 'CHART_SRC' in js_src
    assert 'schedule_portfolio_ch_sync' in open('app.py', encoding='utf-8').read()
    assert 'unpkg.com/lucide' not in html_src
    assert 'cdn.jsdelivr.net/npm/chart.js' not in html_src
    assert 'portfolio-rename-form[hidden]' in css_src
    assert 'id="portal-home-hello"' in html_src
    assert 'id="portal-home-skel"' in html_src
    assert 'Needs Your Attention' in js_src
    assert 'data-company-reg-filter="attention"' in html_src
    assert 'portfolioAttentionIssues' in js_src
    assert 'updatePortfolioAttentionBanner' in js_src
    assert 'portfolio-attention-banner' in css_src
    assert 'emailAdminInvoice' in js_src
    assert 'Send invoice' in html_src
    assert 'resetClientDashboardView' in js_src
    assert 'openUploadDocumentModal' in js_src
    assert 'data.user_name || currentUser' not in js_src
    assert 'id="modal-edit-invoice"' in html_src
    assert 'openEditInvoiceModal' in js_src
    assert 'openInvoiceDocument' in js_src
    assert 'View invoice' in js_src
    assert 'id="edit-invoice-timing"' in html_src
    assert 'id="staff-order-pay-plan"' in html_src
    assert 'Pay first — deposit now' in html_src
    assert 'saveStaffOrderPaymentPlan' in js_src
    assert 'file-accounts-company-locked' in html_src
    assert 'setFileAccountsCompanyLocked' in js_src
    assert 'data-order-finance' in html_src
    assert 'hide-order-finance' in css_src
    assert 'ALL_USER_ROLES' in js_src
    assert 'id="modal-create-user"' in html_src
    assert 'id="modal-document-preview"' in html_src
    assert 'openDocumentPreview' in js_src
    assert 'zoomDocumentPreview' in js_src
    assert 'rotateDocumentPreview' in js_src
    assert 'id="document-preview-zoom"' in html_src
    assert 'id="document-preview-rotate"' in html_src
    assert 'aria-label="Rotate"' in html_src
    assert 'sniffDocumentPreviewKind' in js_src
    assert 'handleGlobalSearch' in js_src
    assert '/api/search?q=' in js_src
    assert 'openGlobalSearchResult' in js_src
    assert 'positionGlobalSearchResults' in js_src
    assert 'id="global-search-results"' in html_src
    assert 'id="top-horizontal-menu-bar"' in html_src
    assert '.top-menu-bar' in css_src
    assert 'display: none !important' in css_src
    assert 'SF Pro Text' in css_src
    assert 'SF Pro Display' in css_src
    assert 'APPLE_UI_FONT' in js_src
    assert '11px Inter' not in js_src
    assert 'id="document-preview-zoom"' in html_src
    assert 'document-preview-toolbar' in html_src
    assert 'aria-label="Zoom in"' in html_src
    assert 'aria-label="Zoom out"' in html_src
    assert '/api/documents/${id}/view' in js_src
    assert 'Business Portfolio' in html_src
    assert 'My Companies' not in html_src
    assert 'click any company to view details' in html_src
    assert 'portfolio-companies-grid' in html_src
    assert 'id="portfolio-company-count"' in html_src
    assert 'data-company-reg-filter="pending"' in html_src
    assert 'setCompanyRegFilter' in js_src
    assert 'Not registered yet' in js_src
    assert 'openCompanyPortfolioDetail' in js_src
    assert 'No companies in your Business Portfolio yet.' in js_src
    assert 'Unable to load your Business Portfolio. Please try again.' in js_src
    admin_sidebar = html_src.split('id="admin-sidebar"', 1)[1].split('</aside>', 1)[0]
    assert 'Business Portfolio' not in admin_sidebar
    assert 'Company Registered' in admin_sidebar
    assert 'data-view="admin-companies"' in admin_sidebar
    assert 'id="btn-add-company"' in html_src
    assert 'id="modal-create-company"' in html_src
    assert 'creates a company card only' in html_src
    assert 'The company card stays' in html_src
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

    from app import company_country_label, is_pending_company_number
    assert company_country_label({'country': 'GB', 'reg_office': '12 High Street, London, GB'}) == 'United Kingdom'
    assert company_country_label({'country': 'England', 'reg_office': '57 Wellesley Road, Ilford, England'}) == 'United Kingdom'
    assert company_country_label({'country': 'PK', 'company_number': '16367472'}) == 'United Kingdom'
    assert company_country_label({'country': 'WV6 0SR', 'reg_office': '85 Dunstall Hill, Wolverhampton, WV6 0SR'}) == 'United Kingdom'
    assert company_country_label({'company_number': 'REG-15311', 'reg_office': 'Pakistan'}) == 'United Kingdom'
    assert is_pending_company_number('REG-15311') is True
    assert is_pending_company_number('16367472') is False
    for company in (admin_companies.get('companies') or []):
        assert company.get('country') == 'United Kingdom'
        if str(company.get('company_number') or '').upper().startswith('REG-'):
            assert company.get('is_registered') is False
        elif company.get('company_number'):
            assert company.get('is_registered') is True
    print("✓ Company cards -> UK country only; REG- numbers marked not registered")

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
    assert dash_res['user_name'] == 'James Harrington'
    assert dash_res.get('greeting') in ('Good morning', 'Good afternoon', 'Good evening')
    assert all(o.get('user_id') == uid for o in dash_res.get('active_orders') or [])
    assert all('notes' not in o for o in dash_res.get('active_orders') or [])
    assert all(c.get('id') for c in dash_res.get('companies') or [])
    assert all('authentication_code' not in c for c in dash_res.get('companies') or [])
    assert 'documents_count' in dash_res
    assert isinstance(dash_res.get('pending_actions'), list)
    assert dash_res['pending_actions_count'] == len(dash_res['pending_actions'])
    assert all('review_notes' not in d for d in dash_res.get('recent_documents') or [])
    assert all('file_path' not in d for d in dash_res.get('recent_documents') or [])
    assert all(d.get('user_id') == uid for d in dash_res.get('recent_documents') or [])
    print(f"✓ WSGI GET /api/client/dashboard -> Hero: '{dash_res['user_name']}', Comps: {dash_res['total_companies']} ({dash_res['uk_companies']} UK, {dash_res['intl_companies']} International), Active Orders: {dash_res['active_orders_count']}")

    status, headers, dash_unauth = make_request('/api/client/dashboard')
    assert status == "401 Unauthorized"

    status, headers, vic_login = make_request('/api/auth/login', method='POST', body={'email': 'v.smith@vantagecyber.co.uk', 'password': 'ClientPass123!'})
    vic_token = extract_session_token(headers)
    vic_user = query_db("SELECT id FROM users WHERE email = 'v.smith@vantagecyber.co.uk';", one=True)
    status, headers, vic_dash = make_request('/api/client/dashboard', cookie=f"session_token={vic_token}")
    assert status == "200 OK"
    assert vic_dash['user_name'] == 'Victoria Smith'
    james_company_ids = {c['id'] for c in dash_res.get('companies') or []}
    vic_company_ids = {c['id'] for c in vic_dash.get('companies') or []}
    assert james_company_ids and vic_company_ids
    assert james_company_ids.isdisjoint(vic_company_ids)
    assert all(o.get('user_id') == vic_user['id'] for o in vic_dash.get('active_orders') or [])
    assert all(o.get('user_id') != uid for o in vic_dash.get('active_orders') or [])
    print("✓ Client dashboard isolation -> James and Victoria see only their own companies, orders, and name")

    # 7. Test Admin Login & Order Status Change
    status, headers, adm_res = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
    adm_token = extract_session_token(headers)
    assert adm_token
    assert 'token' not in adm_res

    status, headers, search_unauth = make_request('/api/search?q=Harrington')
    assert status == "401 Unauthorized"
    status, headers, search_short = make_request('/api/search?q=H', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert search_short['status'] == 'success'
    assert search_short['results']['customers'] == []
    status, headers, admin_people = make_request('/api/search?q=Harrington', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    assert any('Harrington' in (c.get('title') or '') for c in admin_people['results']['customers'])
    assert all(c.get('type') == 'customer' for c in admin_people['results']['customers'])
    status, headers, admin_email = make_request('/api/search?q=client1@acme', cookie=f"session_token={adm_token}")
    assert any('client1@acmecorp.co.uk' in (c.get('subtitle') or '') for c in admin_email['results']['customers'])
    status, headers, admin_order = make_request('/api/search?q=GB103449', cookie=f"session_token={adm_token}")
    assert any('GB103449' in (o.get('title') or '') for o in admin_order['results']['orders'])
    status, headers, client_people = make_request('/api/search?q=Harrington', cookie=f"session_token={token}")
    assert client_people['results']['customers'] == []
    status, headers, client_foreign = make_request('/api/search?q=Vantage', cookie=f"session_token={token}")
    assert client_foreign['results']['companies'] == []
    assert client_foreign['results']['customers'] == []
    status, headers, client_own = make_request('/api/search?q=Acme', cookie=f"session_token={token}")
    assert any('Acme' in (c.get('title') or '') for c in client_own['results']['companies'])
    print("✓ Global search API -> staff find clients/orders; clients scoped to own records")
    
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
    status, headers, staff_price = make_request(
        f"/api/admin/orders/{delete_order_id}",
        method='PUT',
        body={'total': 200},
        cookie=f"session_token={staff_token}"
    )
    assert status == "403 Forbidden"
    status, headers, staff_pay = make_request(
        f"/api/admin/orders/{delete_order_id}",
        method='PUT',
        body={'payment_mode': 'GBP(Bank Transfer)'},
        cookie=f"session_token={staff_token}"
    )
    assert status == "403 Forbidden"
    status, headers, price_res = make_request(
        f"/api/admin/orders/{delete_order_id}",
        method='PUT',
        body={'total': 187.50},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK", price_res
    priced = query_db("SELECT price, vat, total FROM orders WHERE id = ?;", (delete_order_id,), one=True)
    assert float(priced['total']) == 187.50
    status, headers, bad_price = make_request(
        f"/api/admin/orders/{delete_order_id}",
        method='PUT',
        body={'total': -5},
        cookie=f"session_token={adm_token}"
    )
    assert status == "400 Bad Request"
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
    wc_invoice = query_db("SELECT * FROM invoices WHERE order_id = ?;", (wc_order_id,), one=True)
    assert wc_invoice, 'website orders must create an invoice'
    assert abs(float(wc_invoice.get('total') or 0) - 24.00) < 0.01
    assert wc_invoice.get('status') == 'Pending'
    guest_invoice = query_db("SELECT * FROM invoices WHERE order_id = ?;", (guest_ord['id'],), one=True)
    assert guest_invoice, 'guest checkout must still create an invoice'
    assert guest_invoice.get('status') == 'Pending'
    print("✓ WooCommerce product/category mapping -> line item, customer, and company preserved.")
    print("✓ Website orders create invoices for Invoices Manager")
    status, headers, inv_paid = make_request(
        f"/api/admin/invoices/{wc_invoice['id']}",
        method='PUT',
        body={'status': 'Paid', 'payment_method': 'GBP(Bank Transfer)'},
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", inv_paid
    assert inv_paid.get('invoice', {}).get('status') == 'Paid'
    paid_note = query_db(
        "SELECT title, type FROM notifications WHERE user_id = ? AND type = 'payment_received' ORDER BY id DESC LIMIT 1;",
        (james['id'],),
        one=True,
    )
    assert paid_note and paid_note.get('title') in ('Payment received', 'Advance payment received')
    client_inv_denied = make_request(
        f"/api/admin/invoices/{wc_invoice['id']}",
        method='PUT',
        body={'status': 'Pending'},
        cookie=f"session_token={token}",
    )[0]
    assert client_inv_denied == "403 Forbidden"
    print("✓ Staff can mark an invoice Paid and the client is emailed")
    connector_order = execute_db(
        """
        INSERT INTO orders (order_number, user_id, service_name, price, vat, total, status, progress_percent, owner_name, owner_form_email)
        VALUES ('#WC-NAFEESA1', ?, 'VAT Registration', 30, 0, 30, 'Processing', 20, 'Muhib Ul Nabi', 'rabexauk@gmail.com');
        """,
        (james['id'],),
    )
    status, headers, connector_list = make_request('/api/admin/invoices', cookie=f"session_token={adm_comp_token}")
    assert status == "200 OK", connector_list
    connector_inv = next(
        row for row in (connector_list.get('invoices') or [])
        if row.get('order_number') == '#WC-NAFEESA1'
    )
    assert connector_inv.get('client_name') == 'Muhib Ul Nabi'
    assert connector_inv.get('owner_name') == 'Muhib Ul Nabi'
    assert connector_inv.get('client_email') == 'rabexauk@gmail.com'
    assert connector_inv.get('owner_form_email') == 'rabexauk@gmail.com'
    assert connector_inv.get('client_email') != james['email']
    assert connector_inv.get('client_name') != james['full_name']
    assert connector_inv.get('payment_timing') == 'After work'
    status, headers, connector_paid = make_request(
        f"/api/admin/invoices/{connector_inv['id']}",
        method='PUT',
        body={'status': 'Paid'},
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", connector_paid
    outbox = query_db(
        "SELECT recipient, subject FROM email_outbox WHERE recipient = 'rabexauk@gmail.com' ORDER BY id DESC LIMIT 1;",
        one=True,
    )
    assert outbox and 'Payment received' in str(outbox.get('subject') or '')
    paid_html = query_db(
        "SELECT body_html FROM email_outbox WHERE recipient = 'rabexauk@gmail.com' ORDER BY id DESC LIMIT 1;",
        one=True,
    )
    assert paid_html and 'Download invoice' in str(paid_html.get('body_html') or '')
    assert '/invoice/' in str(paid_html.get('body_html') or '')
    status, headers, inv_pdf = make_request(
        f"/api/admin/invoices/{connector_inv['id']}/document",
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", inv_pdf
    header_map = {str(k).lower(): v for k, v in (headers or {}).items()}
    assert header_map.get('content-type', '').startswith('application/pdf')
    assert 'attachment' in (header_map.get('content-disposition') or '')
    assert 'Invoice ' in (header_map.get('content-disposition') or '')
    assert '.pdf' in (header_map.get('content-disposition') or '')
    assert 'Invoice%20' in (header_map.get('content-disposition') or '') or 'Invoice ' in (header_map.get('content-disposition') or '')
    assert isinstance(inv_pdf, (bytes, bytearray))
    assert inv_pdf.startswith(b'%PDF')
    inv_text = inv_pdf.decode('latin-1', 'replace')
    assert 'INVOICE' in inv_text
    assert '17314564' in inv_text
    assert '57 Wellesley Road' in inv_text
    assert 'ZB941411' in inv_text
    assert 'Brixen Consultants' in inv_text
    assert '32546658' in inv_text
    assert '04-06-05' in inv_text
    assert 'PK42UNIL0109000343170125' in inv_text
    assert 'Bank name' in inv_text
    assert 'pay.tide.co' in inv_text
    assert 'Muhib Ul Nabi' in inv_text
    assert 'rabexauk@gmail.com' in inv_text
    assert 'SC855741' not in inv_text
    assert 'Butterbiggins' not in inv_text
    assert 'Glasgow' not in inv_text
    assert 'Payment methods' in inv_text
    pub = app_mod.invoice_public_document_url(connector_inv['id'])
    parsed = urllib.parse.urlparse(pub)
    assert 'Invoice-' in parsed.path or 'Invoice%20' in parsed.path or 'Invoice' in urllib.parse.unquote(parsed.path)
    pub_path = parsed.path if not parsed.query else f"{parsed.path}?{parsed.query}"
    status, headers, pub_pdf = make_request(pub_path)
    assert status == "200 OK"
    pub_map = {str(k).lower(): v for k, v in (headers or {}).items()}
    assert pub_map.get('content-type', '').startswith('application/pdf')
    assert 'attachment' in (pub_map.get('content-disposition') or '')
    assert 'Invoice ' in (pub_map.get('content-disposition') or '') or 'Invoice%20' in (pub_map.get('content-disposition') or '')
    assert isinstance(pub_pdf, (bytes, bytearray)) and pub_pdf.startswith(b'%PDF')
    assert '17314564' in pub_pdf.decode('latin-1', 'replace')
    assert 'Muhib Ul Nabi' in pub_pdf.decode('latin-1', 'replace')
    status, headers, bad_link = make_request(f"/invoice/{connector_inv['id']}?expires=1&signature=bad")
    assert status.startswith('403')
    status, headers, client_doc = make_request(
        f"/api/client/invoices/{wc_invoice['id']}/document",
        cookie=f"session_token={token}",
    )
    assert status == "200 OK"
    assert isinstance(client_doc, (bytes, bytearray)) and client_doc.startswith(b'%PDF')
    assert '17314564' in client_doc.decode('latin-1', 'replace')
    status, headers, anon_doc = make_request(f"/api/admin/invoices/{connector_inv['id']}/document")
    assert status in ("401 Unauthorized", "403 Forbidden")
    print("✓ Invoices and paid emails use the company owner, not the website signup")
    print("✓ Invoice document downloads as PDF with current company details")
    status, headers, deposit_saved = make_request(
        f"/api/admin/invoices/{connector_inv['id']}",
        method='PUT',
        body={'status': 'Pending', 'payment_timing': 'Deposit', 'deposit_amount': 15, 'amount_paid': 0, 'payment_method': 'GBP(Bank Transfer)'},
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", deposit_saved
    assert deposit_saved.get('invoice', {}).get('payment_timing') == 'Deposit'
    assert abs(float(deposit_saved.get('invoice', {}).get('deposit_amount') or 0) - 15) < 0.01
    status, headers, deposit_pdf = make_request(
        f"/api/admin/invoices/{connector_inv['id']}/document",
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK"
    deposit_text = deposit_pdf.decode('latin-1', 'replace') if isinstance(deposit_pdf, (bytes, bytearray)) else str(deposit_pdf)
    assert 'Deposit now' in deposit_text
    assert '32546658' in deposit_text
    status, headers, deposit_paid = make_request(
        f"/api/admin/invoices/{connector_inv['id']}",
        method='PUT',
        body={'payment_timing': 'Deposit', 'deposit_amount': 15, 'amount_paid': 15},
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", deposit_paid
    assert deposit_paid.get('invoice', {}).get('status') == 'Partial Paid'
    assert deposit_paid.get('invoice', {}).get('display_status') == 'Partial Paid'
    status, headers, named_partial = make_request(
        f"/api/admin/invoices/{connector_inv['id']}",
        method='PUT',
        body={'status': 'Partial Paid', 'amount_paid': 15},
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", named_partial
    assert named_partial.get('invoice', {}).get('status') == 'Partial Paid'
    assert named_partial.get('invoice', {}).get('display_status') == 'Partial Paid'
    assert abs(float(deposit_paid.get('invoice', {}).get('amount_due') or 0) - 15) < 0.01
    deposit_mail = query_db(
        "SELECT subject FROM email_outbox WHERE recipient = 'rabexauk@gmail.com' ORDER BY id DESC LIMIT 1;",
        one=True,
    )
    assert deposit_mail and 'Deposit received' in str(deposit_mail.get('subject') or '')
    status, headers, pkr_doc = make_request(
        f"/api/admin/invoices/{connector_inv['id']}",
        method='PUT',
        body={'payment_method': 'PKR(Bank Transfer)'},
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK"
    status, headers, pkr_pdf = make_request(
        f"/api/admin/invoices/{connector_inv['id']}/document",
        cookie=f"session_token={adm_comp_token}",
    )
    pkr_html = pkr_pdf.decode('latin-1', 'replace') if isinstance(pkr_pdf, (bytes, bytearray)) else str(pkr_pdf)
    assert 'PK42UNIL0109000343170125' in pkr_html
    assert 'UBL' in pkr_html
    assert '32546658' in pkr_html
    assert '04-06-05' in pkr_html
    assert '£' in pkr_html
    assert 'If you pay from Pakistan' in pkr_html
    assert '× 370.00 = Rs' in pkr_html
    assert 'Amount due' in pkr_html
    assert 'Rs 0.00' not in pkr_html
    status, headers, pay_mail = make_request(
        f"/api/admin/invoices/{connector_inv['id']}",
        method='PUT',
        body={'send_payment_details': True},
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", pay_mail
    details_outbox = query_db(
        "SELECT subject, body_html, body_text FROM email_outbox WHERE recipient = 'rabexauk@gmail.com' ORDER BY id DESC LIMIT 1;",
        one=True,
    )
    details_blob = f"{details_outbox.get('subject') or ''} {details_outbox.get('body_html') or ''} {details_outbox.get('body_text') or ''}"
    details_html = str(details_outbox.get('body_html') or '')
    details_subject = str(details_outbox.get('subject') or '')
    assert 'Invoice' in details_subject
    assert 'Brixen Consultants' in details_subject
    assert 'Payment details —' not in details_subject
    assert 'Thanks for your order - your invoice' not in details_subject
    assert '32546658' in details_blob
    assert '04-06-05' in details_blob
    assert 'PK42UNIL0109000343170125' in details_blob
    assert 'Pay now in £' not in details_html
    assert 'Pay in GBP' in details_html
    assert 'Pay in PKR' in details_html
    assert 'Thank you for your order' in details_html
    assert 'Hi Muhib Ul Nabi,' in details_html
    assert '👋' not in details_html
    assert 'Hello Brixen Consultant' not in details_html
    assert 'Hi Brixen Consultant' not in details_html
    assert 'rabexauk@gmail.com' in details_html
    assert 'You may settle this invoice using either the GBP or PKR' in details_html
    assert 'Pay using the GBP or PKR account below' not in details_html
    assert 'Need help?' in details_html
    assert 'Contact Support' not in details_html
    assert 'Download invoice' in details_html
    assert '/invoice/' in details_html
    assert 'Invoice summary' in details_html
    assert 'Subtotal' in details_html
    assert 'Total' in details_html
    assert 'Amount due' in details_html
    assert '#003971' in details_html
    assert '#c5a572' in details_html
    assert '#f3efe8' in details_html
    assert '#1d1d1f' in details_html
    assert '#6e6e73' in details_html
    assert '#006cff' not in details_html
    assert '#28a745' not in details_html
    assert '#007bff' not in details_html
    assert 'font-size:20px' in details_html
    assert 'Account number' in details_html
    assert 'Brixen Consultants' in details_html
    assert '-apple-system' in details_html
    assert 'brixen-logo.png' in details_html
    assert 'border-radius:999px' not in details_html
    assert '🧾' not in details_html
    assert '🔗' not in details_html
    print("✓ Staff can take a deposit and invoices show GBP or PKR account details")

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

    split_address = checkout_form_fields_from_payload({
        'meta': {
            '_cfs_company_name': 'Acme Ltd',
            '_cfs_registered_address_city': 'Wolverhampton',
            '_cfs_registered_address_country': 'United Kingdom',
            '_cfs_registered_address_state': 'West Midlands',
            '_cfs_registered_address_street': '85 Dunstall Hill',
            '_cfs_registered_address_zip': 'WV6 0SR',
        }
    })
    address_labels = [field['label'] for field in split_address]
    assert 'Registered Address City' not in address_labels
    assert address_labels.count('Registered address') == 1
    assert next(field['value'] for field in split_address if field['label'] == 'Registered address') == (
        '85 Dunstall Hill, Wolverhampton, West Midlands, WV6 0SR, United Kingdom'
    )
    merged_stored = coalesce_checkout_address_fields([
        {'label': 'Registered Address City', 'value': 'Wolverhampton'},
        {'label': 'Registered Address Country', 'value': 'United Kingdom'},
        {'label': 'Registered Address State', 'value': 'West Midlands'},
        {'label': 'Registered Address Street', 'value': '85 Dunstall Hill'},
        {'label': 'Registered Address Zip', 'value': 'WV6 0SR'},
    ])
    assert len(merged_stored) == 1
    assert merged_stored[0]['label'] == 'Registered address'
    assert     merged_stored[0]['value'] == '85 Dunstall Hill, Wolverhampton, West Midlands, WV6 0SR, United Kingdom'
    print("✓ Checkout registered address -> street, city, county, postcode, country shown as one line")
    passport_fields = checkout_form_fields_from_payload({
        'meta': {'_cfs_passport_cnic': 'CN5610475', '_cfs_company_name': 'Acme Ltd'},
        'order_notes': ['Company formation form\nPassport/CNIC: CN5610475\nPassport Number: CN5610475\n'],
    })
    passport_hits = [field for field in passport_fields if 'passport' in field['label'].lower() or 'cnic' in field['label'].lower()]
    assert len(passport_hits) == 1, passport_hits
    assert passport_hits[0]['value'] == 'CN5610475'
    print("✓ Checkout passport / CNIC is shown once")

    mixed_email_fields = checkout_form_fields_from_payload({
        'email': 'checkout-account@example.com',
        'full_name': 'Portal Account Name',
        'meta': {
            '_cfs_company_name': 'Owner Holdings Ltd',
            '_cfs_director_name': 'Muhib Ul Nabi',
            '_cfs_registered_email': 'rabexauk@gmail.com',
            '_cfs_uk_contact_number': '+44 7918 940907',
        },
    })
    mixed_map = {field['label']: field['value'] for field in mixed_email_fields}
    assert mixed_map.get('Email (form)') == 'rabexauk@gmail.com'
    assert mixed_map.get('Director name') == 'Muhib Ul Nabi'
    assert 'checkout-account@example.com' not in mixed_map.values()
    assert 'Portal Account Name' not in mixed_map.values()
    checkout_only_fields = checkout_form_fields_from_payload({
        'email': 'checkout-account@example.com',
        'full_name': 'Portal Account Name',
    })
    checkout_only_map = {field['label']: field['value'] for field in checkout_only_fields}
    assert 'Email (form)' not in checkout_only_map
    assert 'Director name' not in checkout_only_map
    print("✓ Formation form owner and form email are kept separate from checkout login")
    assert extract_order_company_name({
        'company_name': 'KOOKY KARTT LTD',
        'meta': {'_cfs_company_name': 'KOOKY KART LTD'},
    }) == 'KOOKY KART LTD'
    print("✓ Formation form company name is preferred over checkout company name")

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
            'price': 53,
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
    bella_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'Bella & Rosso Ltd', 'REG-15311', 'Active', '2026-08-25', 'Nafeesa Ishfaq', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    class FakeBellaCompaniesHouseResponse:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps(self.payload).encode('utf-8')
    def fake_bella_ch_urlopen(req, timeout=None):
        app_mod._CH_API_CACHE.clear()
        url = str(getattr(req, 'full_url', getattr(req, 'get_full_url', lambda: str(req))()))
        if '/officers' in url:
            return FakeBellaCompaniesHouseResponse({
                'items': [{'name': 'ISHFAQ, Nafeesa', 'officer_role': 'director'}]
            })
        if '/company/' in url and '/search' not in url:
            return FakeBellaCompaniesHouseResponse({
                'company_name': 'BELLA & ROSSO LTD',
                'company_number': '16620111',
                'company_status': 'active',
                'date_of_creation': '2026-08-25',
                'registered_office_address': {
                    'address_line_1': '71-75 Shelton Street',
                    'locality': 'London',
                    'postal_code': 'WC2H 9JQ',
                    'country': 'England',
                },
            })
        return FakeBellaCompaniesHouseResponse({
            'items': [{
                'title': 'BELLA & ROSSO LTD',
                'company_number': '16620111',
                'company_status': 'active',
                'date_of_creation': '2026-08-25',
                'address_snippet': '71-75 Shelton Street, London, WC2H 9JQ',
                'address': {'address_line_1': '71-75 Shelton Street', 'locality': 'London', 'postal_code': 'WC2H 9JQ', 'country': 'England'},
            }]
        })
    app_mod._CH_API_CACHE.clear()
    app_mod._CH_LIST_SYNC_LAST = 0
    with patch('app.companies_house_api_key', return_value='test_ch_key'):
        with patch('urllib.request.urlopen', side_effect=fake_bella_ch_urlopen):
            with patch('app.urllib.request.urlopen', side_effect=fake_bella_ch_urlopen):
                app_mod.sync_pending_companies_from_companies_house(limit=5)
                status, headers, bella_list = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert status == "200 OK", bella_list
    bella_row = next(row for row in (bella_list.get('companies') or []) if int(row.get('id')) == int(bella_id))
    print("DEBUG bella_row:", bella_row)
    assert bella_row['company_number'] == '16620111'
    assert bella_row['is_registered'] is True
    assert bella_row.get('owner_name') == 'Nafeesa Ishfaq'
    assert bella_list.get('companies_house_synced') is not None
    stored_bella = query_db("SELECT company_number, name, director FROM companies WHERE id = ?;", (bella_id,), one=True)
    assert stored_bella['company_number'] == '16620111'
    assert stored_bella['name'] == 'BELLA & ROSSO LTD'
    print("✓ Pending REG- company cards pick up the live Companies House number on the company list")
    congrats_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'Form Email Ltd', 'REG-16100', 'Active', '2026-08-30', 'Nafeesa Ishfaq', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    execute_db(
        """
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent, checkout_form_json)
        VALUES ('#WC-16100', ?, ?, 'Digital Package', 53, 10.59, 63.59, 'Processing', 20, ?);
        """,
        (james['id'], congrats_id, json.dumps([
            {'label': 'Desired company name', 'value': 'Form Email Ltd'},
            {'label': 'Director name', 'value': 'Nafeesa Ishfaq'},
            {'label': 'Email (form)', 'value': 'bellaandrosso@gmail.com'},
        ])),
    )
    sent_registration_emails = []
    original_send_email = app_mod.EmailService.send_notification_email
    def capture_registration_email(recipient, subject, body_text, body_html=None):
        sent_registration_emails.append({
            'to': recipient,
            'subject': subject,
            'text': body_text or '',
            'html': body_html or '',
        })
        return True, 'Delivered'
    app_mod.EmailService.send_notification_email = staticmethod(capture_registration_email)
    class FakeHttpResponse:
        def __init__(self, payload, content_type='application/json'):
            self.payload = payload
            self.headers = {'Content-Type': content_type}
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            if isinstance(self.payload, (bytes, bytearray)):
                return self.payload
            return json.dumps(self.payload).encode('utf-8')
    def fake_form_email_ch_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, 'full_url') else req.get_full_url()
        if '/search/companies' in url:
            if 'Form' not in url and 'form' not in url:
                return FakeHttpResponse({'items': []})
            return FakeHttpResponse({
                'items': [{
                    'title': 'FORM EMAIL LTD',
                    'company_number': '16100111',
                    'company_status': 'active',
                    'date_of_creation': '2026-08-30',
                    'address_snippet': '1 High Street, London, SW1A 1AA',
                    'address': {'address_line_1': '1 High Street', 'locality': 'London', 'postal_code': 'SW1A 1AA', 'country': 'England'},
                }]
            })
        if '/officers' in url:
            return FakeHttpResponse({'items': [{'name': 'ISHFAQ, Nafeesa', 'officer_role': 'director'}]})
        if '/filing-history' in url:
            return FakeHttpResponse({
                'items': [{
                    'type': 'NEWINC',
                    'transaction_id': 'MzU0TEST16100111',
                    'description': 'certificate-of-incorporation-company',
                    'links': {
                        'document_metadata': 'https://document-api.company-information.service.gov.uk/document/ABC16100111',
                    },
                }]
            })
        if 'document-api.company-information.service.gov.uk/document/ABC16100111/content' in url:
            return FakeHttpResponse(b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n', 'application/pdf')
        if 'document-api.company-information.service.gov.uk/document/ABC16100111' in url:
            return FakeHttpResponse({
                'links': {'document': 'https://document-api.company-information.service.gov.uk/document/ABC16100111/content'}
            })
        return FakeHttpResponse({
            'company_name': 'FORM EMAIL LTD',
            'company_number': '16100111',
            'company_status': 'active',
            'date_of_creation': '2026-08-30',
            'registered_office_address': {
                'address_line_1': '1 High Street',
                'locality': 'London',
                'postal_code': 'SW1A 1AA',
                'country': 'England',
            },
        })
    try:
        app_mod._CH_API_CACHE.clear()
        app_mod._CH_LIST_SYNC_LAST = 0
        with patch('app.companies_house_api_key', return_value='test_ch_key'):
            with patch('urllib.request.urlopen', side_effect=fake_form_email_ch_urlopen):
                with patch('app.urllib.request.urlopen', side_effect=fake_form_email_ch_urlopen):
                    app_mod.sync_pending_companies_from_companies_house(limit=5)
                    status, headers, congrats_list = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
        assert status == "200 OK", congrats_list
        congrats_row = next(row for row in (congrats_list.get('companies') or []) if int(row.get('id')) == int(congrats_id))
        assert congrats_row['company_number'] == '16100111'
        assert sent_registration_emails, 'expected a congratulations email'
        mail = sent_registration_emails[0]
        assert mail['to'] == 'bellaandrosso@gmail.com'
        assert 'Congratulations' in mail['subject']
        assert '16100111' in mail['text'] or '16100111' in mail['html']
        assert 'FORM EMAIL LTD' in mail['subject'] or 'FORM EMAIL LTD' in mail['html']
        combined = f"{mail['text']}\n{mail['html']}"
        assert re.search(r'Hi Nafeesa', combined, re.I)
        assert not re.search(r'Hi James', combined, re.I)
        assert 'officially registered with Companies House' in mail['text'] or 'officially registered with Companies House' in mail['html']
        assert 'Download your certificate of incorporation' in mail['html']
        assert 'Download now' in mail['html']
        assert 'https://find-and-update.company-information.service.gov.uk/company/16100111/filing-history' in mail['html']
        assert 'Your next-step strategy' in mail['html']
        assert 'Bank account' in mail['html']
        assert 'Free consultation' in mail['html']
        assert 'WhatsApp for a free consultation' in mail['html']
        assert 'wa.me' in mail['html']
        assert '🏦' in mail['html']
        assert 'border-radius:980px' in mail['html']
        assert 'padding:11px 22px' in mail['html']
        assert 'font-size:13px' in mail['html']
        assert 'Brixen Consultants' in mail['html']
        assert 'congratulations.png' in mail['html']
        assert 'congratulations.gif' not in mail['html']
        assert 'brixen-logo.png' in mail['html']
        assert 'rel="icon"' in mail['html']
        assert 'Brixen-Consultants.png' in mail['html']
        assert 'cid:brixen-favicon' not in mail['html']
        assert 'cid:brixen-congratulations' not in mail['html']
        assert 'cid:brixen-wordmark' not in mail['html']
        assert '-apple-system' in mail['html']
        assert 'bgcolor="#003971"' in mail['html']
        assert 'bgcolor="#25D366"' in mail['html']
        assert '<font color="#ffffff">' in mail['html']
        cert = query_db(
            "SELECT * FROM documents WHERE company_id = ? AND category = 'Companies House Certificate';",
            (congrats_id,),
            one=True,
        )
        assert cert is not None
        assert os.path.isfile(cert['file_path'])
        with open(cert['file_path'], 'rb') as fh:
            assert fh.read(4) == b'%PDF'
        sent_registration_emails.clear()
        with patch('app.urllib.request.urlopen', side_effect=fake_form_email_ch_urlopen):
            status, headers, congrats_again = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
        assert status == "200 OK", congrats_again
        assert sent_registration_emails == []
    finally:
        app_mod.EmailService.send_notification_email = original_send_email
    print("✓ Registration congratulations email goes to the checkout Email (form) address")
    status, headers, form_acct = make_request(
        '/api/admin/accountancy',
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", form_acct
    form_row = next(row for row in (form_acct.get('companies') or []) if int(row.get('id')) == int(congrats_id))
    assert form_row.get('director') == 'Nafeesa Ishfaq'
    assert form_row.get('owner_name') == 'Nafeesa Ishfaq'
    assert form_row.get('director_email') == 'bellaandrosso@gmail.com'
    assert form_row.get('client_name') == 'Nafeesa Ishfaq'
    assert form_row.get('client_email') == 'bellaandrosso@gmail.com'
    assert form_row.get('client_email') != james['email']
    status, headers, form_books = make_request(
        f"/api/admin/accountancy/{congrats_id}/books",
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", form_books
    assert (form_books.get('company') or {}).get('director') == 'Nafeesa Ishfaq'
    assert (form_books.get('company') or {}).get('director_email') == 'bellaandrosso@gmail.com'
    print("✓ Accountancy shows the formation-form director name and Email (form)")
    placeholder_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'PLACEHOLDER SYNC LTD', '16100999', 'Active', '2026-01-15', 'blackpearl6563', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    execute_db(
        """
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent, checkout_form_json)
        VALUES ('#WC-16100999', ?, ?, 'Digital Package', 53, 10.59, 63.59, 'Processing', 20, ?);
        """,
        (james['id'], placeholder_id, json.dumps([
            {'label': 'Desired company name', 'value': 'PLACEHOLDER SYNC LTD'},
            {'label': 'Director name', 'value': 'blackpearl6563'},
            {'label': 'Email (form)', 'value': 'placeholdersync@gmail.com'},
        ])),
    )
    class FakePlaceholderCh:
        def __init__(self, payload):
            self.payload = payload
            self.headers = {'Content-Type': 'application/json'}
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps(self.payload).encode('utf-8')
    def fake_placeholder_ch_urlopen(req, timeout=None):
        url = str(getattr(req, 'full_url', getattr(req, 'get_full_url', lambda: str(req))()))
        if '/officers' in url:
            return FakePlaceholderCh({'items': [{'name': 'KHAN, Amina', 'officer_role': 'director'}]})
        if '/company/16100999' in url and '/search' not in url:
            return FakePlaceholderCh({
                'company_name': 'PLACEHOLDER SYNC LTD',
                'company_number': '16100999',
                'company_status': 'active',
                'date_of_creation': '2026-01-15',
            })
        return FakePlaceholderCh({'items': [{
            'title': 'PLACEHOLDER SYNC LTD',
            'company_number': '16100999',
            'company_status': 'active',
            'date_of_creation': '2026-01-15',
        }]})
    app_mod._CH_API_CACHE.clear()
    app_mod._CH_LIST_SYNC_LAST = 0
    with patch('app.companies_house_api_key', return_value='test_ch_key'):
        with patch('urllib.request.urlopen', side_effect=fake_placeholder_ch_urlopen):
            with patch('app.urllib.request.urlopen', side_effect=fake_placeholder_ch_urlopen):
                app_mod.sync_pending_companies_from_companies_house(limit=50, user_id=james['id'])
                app_mod.sync_registered_companies_from_companies_house(limit=50, user_id=james['id'])
                status, headers, ph_acct = make_request(
                    '/api/admin/accountancy',
                    cookie=f"session_token={adm_comp_token}",
                )
    assert status == "200 OK", ph_acct
    ph_row = next(row for row in (ph_acct.get('companies') or []) if int(row.get('id')) == int(placeholder_id))
    print("DEBUG ph_row:", ph_row)
    assert ph_row.get('director') == 'Amina Khan'
    assert ph_row.get('director') != 'blackpearl6563'
    assert ph_row.get('director_email') == 'placeholdersync@gmail.com'
    assert ph_row.get('client_email') != james['email']
    stored_ph = query_db("SELECT director FROM companies WHERE id = ?;", (placeholder_id,), one=True)
    assert stored_ph['director'] == 'Amina Khan'
    print("✓ Accountancy replaces placeholder directors with Companies House names and keeps the company form email")
    ch_fill_company = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'Rank Rays LTD', '17208527', 'Active', '', 'Muhammad Shakeel', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    ch_fill_order = execute_db(
        """
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent, owner_name, owner_form_email, checkout_form_json)
        VALUES ('#WC-17208527', ?, ?, 'Digital Package', 53, 10.59, 63.59, 'Processing', 20, 'Muhammad Shakeel', 'shakeel.rehmat@gmail.com', ?);
        """,
        (james['id'], ch_fill_company, json.dumps([
            {'label': 'Company Name', 'value': 'Rank Rays LTD'},
            {'label': 'Company Number', 'value': '17208527'},
            {'label': 'Billing address', 'value': '85 Dunstall Hill, London, WV6 0SR, GB'},
            {'label': 'Email (form)', 'value': 'shakeel.rehmat@gmail.com'},
        ])),
    )
    execute_db(
        """
        INSERT INTO company_owners (order_id, company_id, full_name, form_email, source)
        VALUES (?, ?, 'Muhammad Shakeel', 'shakeel.rehmat@gmail.com', 'formation_form');
        """,
        (ch_fill_order, ch_fill_company),
    )
    class FakeRankRaysCh:
        def __init__(self, payload):
            self.payload = payload
            self.headers = {'Content-Type': 'application/json'}
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps(self.payload).encode('utf-8')
    def fake_rank_rays_ch(req, timeout=None):
        url = str(getattr(req, 'full_url', getattr(req, 'get_full_url', lambda: str(req))()))
        if '/officers' in url:
            return FakeRankRaysCh({'items': [
                {'name': 'SHAKEEL, Muhammad', 'officer_role': 'director'},
                {'name': 'ALI, Sara', 'officer_role': 'director'},
                {'name': 'OLD, Resigned', 'officer_role': 'director', 'resigned_on': '2024-01-01'},
                {'name': 'SECRETARY LTD', 'officer_role': 'corporate-secretary'},
            ]})
        if '/company/17208527' in url:
            return FakeRankRaysCh({
                'company_name': 'RANK RAYS LTD',
                'company_number': '17208527',
                'company_status': 'active',
                'date_of_creation': '2025-11-03',
                'sic_codes': ['73110'],
                'registered_office_address': {
                    'address_line_1': '71-75 Shelton Street',
                    'address_line_2': 'Covent Garden',
                    'locality': 'London',
                    'postal_code': 'WC2H 9JQ',
                    'country': 'United Kingdom',
                },
            })
        return FakeRankRaysCh({'items': []})
    app_mod._CH_REGISTERED_SYNC_BLOCKED = False
    with patch('app.urllib.request.urlopen', side_effect=fake_rank_rays_ch):
        status, headers, filled_order = make_request(
            f'/api/client/orders/{ch_fill_order}',
            cookie=f"session_token={adm_comp_token}",
        )
    assert status == "200 OK", filled_order
    filled_map = {
        str(field.get('label') or ''): str(field.get('value') or '')
        for field in (filled_order.get('order', {}).get('checkout_form') or [])
    }
    assert filled_map.get('Company Name') == 'Rank Rays LTD'
    assert filled_map.get('Billing address') == '85 Dunstall Hill, London, WV6 0SR, GB'
    assert '71-75 Shelton Street' in (filled_map.get('Registered address') or '')
    assert filled_map.get('SIC code') == '73110'
    assert filled_map.get('Incorporation date') == '2025-11-03'
    assert filled_map.get('Company status') == 'Active'
    assert filled_order.get('order', {}).get('company_owner', {}).get('full_name') == 'Muhammad Shakeel'
    stored_fill = query_db("SELECT reg_office, inc_date, sic_codes FROM companies WHERE id = ?;", (ch_fill_company,), one=True)
    assert '71-75 Shelton Street' in str(stored_fill.get('reg_office') or '')
    assert stored_fill.get('inc_date') == '2025-11-03'
    assert '73110' in str(stored_fill.get('sic_codes') or '')
    from app import sic_activities_from_codes, fetch_companies_house_active_directors
    sic_items = sic_activities_from_codes(stored_fill.get('sic_codes'))
    assert sic_items and sic_items[0]['code'] == '73110'
    assert 'advertising' in (sic_items[0].get('description') or '').lower()
    app_mod._CH_REGISTERED_SYNC_BLOCKED = False
    app_mod._CH_API_CACHE.clear()
    app_mod._CH_LIST_SYNC_LAST = 0
    with patch('app.companies_house_api_key', return_value='test_ch_key'):
        with patch('urllib.request.urlopen', side_effect=fake_rank_rays_ch):
            with patch('app.urllib.request.urlopen', side_effect=fake_rank_rays_ch):
                directors = fetch_companies_house_active_directors('17208527')
                assert directors == ['Muhammad Shakeel', 'Sara Ali']
                ch_comp_row = query_db("SELECT * FROM companies WHERE id = ?;", (ch_fill_company,), one=True)
                app_mod._CH_API_CACHE.clear()
                sync_res = app_mod.sync_registered_company_from_companies_house(ch_comp_row)
                app_mod._CH_REGISTERED_SYNC_BLOCKED = True
                status, headers, filled_company = make_request(
                    f"/api/admin/companies/{ch_fill_company}",
                    cookie=f"session_token={adm_comp_token}",
                )
    assert status == "200 OK", filled_company
    assert filled_company.get('company', {}).get('directors') == ['Muhammad Shakeel', 'Sara Ali']
    assert 'Muhammad Shakeel' in (filled_company.get('company', {}).get('director') or '')
    assert 'Sara Ali' in (filled_company.get('company', {}).get('director') or '')
    print("✓ Opening an order fills blank Companies House details and leaves existing owner/billing data")
    vat_unlinked = execute_db(
        """
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent, owner_name, owner_form_email, checkout_form_json)
        VALUES ('#WC-VAT15296', ?, NULL, 'VAT Registration', 0, 0, 0, 'Completed', 100, 'Muhammad Shakeel', 'shakeel.rehmat@gmail.com', ?);
        """,
        (james['id'], json.dumps([
            {'label': 'Company Name', 'value': 'Rank Rays LTD'},
            {'label': 'Company Number', 'value': '17208527'},
            {'label': 'Email (form)', 'value': 'shakeel.rehmat@gmail.com'},
        ])),
    )
    status, headers, vat_list = make_request(
        '/api/admin/orders?search=WC-VAT15296',
        cookie=f"session_token={adm_comp_token}",
    )
    assert status == "200 OK", vat_list
    vat_row = next(row for row in (vat_list.get('orders') or []) if int(row.get('id')) == int(vat_unlinked))
    assert vat_row.get('company_name') == 'Rank Rays LTD'
    linked_vat = query_db("SELECT company_id FROM orders WHERE id = ?;", (vat_unlinked,), one=True)
    assert linked_vat.get('company_id') == ch_fill_company
    print("✓ Orders table shows the form company name for VAT orders and links the Companies House card")
    class FakePreCrmCh:
        def __init__(self, payload):
            self.payload = payload
            self.headers = {'Content-Type': 'application/json'}
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps(self.payload).encode('utf-8')
    def fake_precrm_ch(req, timeout=None):
        url = req.full_url if hasattr(req, 'full_url') else req.get_full_url()
        if '/officers' in url and '18881234' in url:
            return FakePreCrmCh({'items': [{'name': 'AHMED, Sara', 'officer_role': 'director'}]})
        if '/company/18881234' in url:
            return FakePreCrmCh({
                'company_name': 'PRECRM ACCESS LTD',
                'company_number': '18881234',
                'company_status': 'active',
                'date_of_creation': '2022-04-18',
                'registered_office_address': {
                    'address_line_1': '10 Downing Street',
                    'locality': 'London',
                    'postal_code': 'SW1A 2AA',
                    'country': 'United Kingdom',
                },
            })
        return FakePreCrmCh({'items': []})
    app_mod._CH_REGISTERED_SYNC_BLOCKED = False
    with patch('app.urllib.request.urlopen', side_effect=fake_precrm_ch):
        status, headers, precrm_ok = make_request(
            '/api/admin/companies', method='POST',
            body={
                'client_id': james['id'],
                'name': 'PRECRM ACCESS LTD',
                'company_number': '18881234',
            },
            cookie=f"session_token={adm_comp_token}",
        )
    assert status == "200 OK", precrm_ok
    precrm_company = precrm_ok.get('company') or {}
    assert precrm_company.get('company_number') == '18881234'
    assert precrm_company.get('director') == 'Sara Ahmed'
    assert '10 Downing Street' in str(precrm_company.get('reg_office') or '')
    assert precrm_company.get('inc_date') == '2022-04-18'
    precrm_order = execute_db(
        """
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent, owner_name, checkout_form_json)
        VALUES ('#CRM-PRECRM1', ?, ?, 'Historical Registration', 0, 0, 0, 'Completed', 100, 'Sara Ahmed', ?);
        """,
        (james['id'], precrm_company.get('id'), json.dumps([
            {'label': 'Company Name', 'value': 'PRECRM ACCESS LTD'},
            {'label': 'Company Number', 'value': '18881234'},
        ])),
    )
    with patch('app.urllib.request.urlopen', side_effect=fake_precrm_ch):
        status, headers, precrm_del = make_request(
            f'/api/admin/orders/{precrm_order}', method='DELETE',
            cookie=f"session_token={adm_comp_token}",
        )
    assert status == "200 OK", precrm_del
    assert precrm_del.get('company_kept') is True
    assert query_db("SELECT id FROM orders WHERE id = ?;", (precrm_order,), one=True) is None
    kept = query_db(
        "SELECT name, company_number, director, reg_office, inc_date FROM companies WHERE id = ?;",
        (precrm_company.get('id'),),
        one=True,
    )
    assert kept is not None
    assert kept.get('company_number') == '18881234'
    assert kept.get('director') == 'Sara Ahmed'
    assert '10 Downing Street' in str(kept.get('reg_office') or '')
    print("✓ Historical companies pull Companies House data without an order, and deleting a dummy order keeps the card")
    assert app_mod.email_belongs_to_person('shakeel.rehmat@gmail.com', 'Muhammad Shakeel')
    assert not app_mod.email_belongs_to_person('hananmuhammad468@gmail.com', 'Hasnat Mazhar')
    recent_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'Recent Filing Ltd', '17428024', 'Active', ?, 'Test Director', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'], datetime.date.today().isoformat()),
    )
    execute_db(
        """
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent, checkout_form_json)
        VALUES ('#WC-17428024', ?, ?, 'Digital Package', 53, 10.59, 63.59, 'Processing', 20, ?);
        """,
        (james['id'], recent_id, json.dumps([{'label': 'Email (form)', 'value': 'recent24h@example.com'}])),
    )
    old_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'Old Filing Ltd', '10000024', 'Active', '2020-01-01', 'Test Director', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    recent_emails = []
    original_recent_send = app_mod.EmailService.send_notification_email
    def capture_recent_email(recipient, subject, body_text, body_html=None):
        recent_emails.append({'to': recipient, 'subject': subject})
        return True, 'Delivered'
    app_mod.EmailService.send_notification_email = staticmethod(capture_recent_email)
    try:
        due_ids = {int(row['id']) for row in app_mod.companies_due_for_registration_notice()}
        assert int(recent_id) in due_ids
        assert int(old_id) not in due_ids
        sent_count = app_mod.notify_recent_company_registrations()
        assert sent_count >= 1
        assert any(item['to'] == 'recent24h@example.com' and 'Congratulations' in item['subject'] for item in recent_emails)
        assert app_mod.notify_company_registered(recent_id).get('email_status') == 'Already sent'
        recent_order = query_db("SELECT * FROM orders WHERE company_id = ?;", (recent_id,), one=True)
        assert app_mod.set_order_form_email(recent_order['id'], 'changed-form@example.com')
        reloaded_order = query_db("SELECT * FROM orders WHERE id = ?;", (recent_order['id'],), one=True)
        loaded_form = app_mod.load_order_checkout_form(reloaded_order)
        assert any(str(field.get('value') or '') == 'changed-form@example.com' for field in loaded_form)
        assert app_mod.company_form_email(recent_id) == 'changed-form@example.com'
    finally:
        app_mod.EmailService.send_notification_email = original_recent_send
    print("✓ Companies registered in the last 24 hours get the congratulations email")
    assert os.environ.get('CRM_TESTING') == '1'
    assert app_mod.start_registration_notice_worker() is False
    nomatch_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'ZZZXQ No Such Company Ltd', 'REG-99991', 'Active', '2026-08-25', 'Test Director', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    class FakeEmptyCompaniesHouseResponse:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps({'items': []}).encode('utf-8')
    with patch('app.urllib.request.urlopen', return_value=FakeEmptyCompaniesHouseResponse()):
        status, headers, nomatch_list = make_request('/api/admin/companies', cookie=f"session_token={adm_comp_token}")
    assert status == "200 OK", nomatch_list
    nomatch_row = next(row for row in (nomatch_list.get('companies') or []) if int(row.get('id')) == int(nomatch_id))
    assert nomatch_row['company_number'] == 'REG-99991'
    assert nomatch_row['is_registered'] is False
    print("✓ Unmatched pending names stay on the Not registered list")
    maple_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'Maple & Finch Ltd', 'REG-16001', 'Active', '2026-08-20', 'Test Director', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    class FakeMapleCompaniesHouseResponse:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps(self.payload).encode('utf-8')
    def fake_maple_ch_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, 'full_url') else req.get_full_url()
        if '/search/companies' in url:
            if 'Maple' not in url and 'maple' not in url:
                return FakeMapleCompaniesHouseResponse({'items': []})
            return FakeMapleCompaniesHouseResponse({
                'items': [{
                    'title': 'MAPLE AND FINCH LIMITED',
                    'company_number': '15550001',
                    'company_status': 'active',
                    'date_of_creation': '2026-08-20',
                    'address_snippet': '10 Downing Street, London, SW1A 2AA',
                    'address': {'address_line_1': '10 Downing Street', 'locality': 'London', 'postal_code': 'SW1A 2AA', 'country': 'England'},
                }]
            })
        if '/officers' in url:
            return FakeMapleCompaniesHouseResponse({
                'items': [{'name': 'FINCH, Maple', 'officer_role': 'director'}]
            })
        return FakeMapleCompaniesHouseResponse({
            'company_name': 'MAPLE AND FINCH LIMITED',
            'company_number': '15550001',
            'company_status': 'active',
            'date_of_creation': '2026-08-20',
            'registered_office_address': {
                'address_line_1': '10 Downing Street',
                'locality': 'London',
                'postal_code': 'SW1A 2AA',
                'country': 'England',
            },
        })
    app_mod._CH_API_CACHE.clear()
    app_mod._CH_LIST_SYNC_LAST = 0
    with patch('app.companies_house_api_key', return_value='test_ch_key'):
        with patch('urllib.request.urlopen', side_effect=fake_maple_ch_urlopen):
            with patch('app.urllib.request.urlopen', side_effect=fake_maple_ch_urlopen):
                app_mod.sync_pending_companies_from_companies_house(limit=50)
                status, headers, client_sync = make_request('/api/client/companies', cookie=f"session_token={token}")
    assert status == "200 OK", client_sync
    maple_row = next(row for row in (client_sync.get('companies') or []) if int(row.get('id')) == int(maple_id))
    assert maple_row['company_number'] == '15550001'
    assert maple_row['is_registered'] is True
    assert client_sync.get('companies_house_synced') is not None
    print("✓ Client company list also promotes a live Companies House match")
    execute_db(
        """
        UPDATE companies SET ch_checked_at = CURRENT_TIMESTAMP
        WHERE company_number IS NULL OR TRIM(company_number) = '' OR UPPER(company_number) LIKE 'REG-%';
        """
    )
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
            'price': 53,
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
    imported_row = query_db("SELECT name, company_number, package, registered_email FROM companies WHERE company_number = '99112233';", one=True)
    assert imported_row['name'] == 'WEBFILL TEST LIMITED'
    assert imported_row['package'] == 'WebFiling Import'
    assert imported_row['registered_email'] == james['email']
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
    rename_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'Old Checkout Name Ltd', 'REG-16088', 'Active', '2026-08-25', 'Test Director', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    rename_order_id = execute_db(
        """
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent, checkout_form_json)
        VALUES ('#WC-RENAME-16088', ?, ?, 'Digital Package', 53, 10.59, 63.59, 'Processing', 20, ?);
        """,
        (james['id'], rename_id, json.dumps([{'label': 'Desired company name', 'value': 'Old Checkout Name Ltd'}])),
    )
    status, headers, rename_client = make_request(
        f"/api/admin/companies/{rename_id}", method='PUT',
        cookie=f"session_token={token}",
        body={'name': 'New Desired Name Ltd'}
    )
    assert status == "403 Forbidden"
    status, headers, rename_registered = make_request(
        f"/api/admin/companies/{posted_company['id']}", method='PUT',
        cookie=f"session_token={adm_comp_token}",
        body={'name': 'Should Not Change Ltd'}
    )
    assert status == "400 Bad Request"
    assert 'companies house record' in (rename_registered.get('message') or '').lower()
    status, headers, rename_blank = make_request(
        f"/api/admin/companies/{rename_id}", method='PUT',
        cookie=f"session_token={adm_comp_token}",
        body={'name': ' '}
    )
    assert status == "400 Bad Request"
    status, headers, rename_ok = make_request(
        f"/api/admin/companies/{rename_id}", method='PUT',
        cookie=f"session_token={adm_comp_token}",
        body={'name': 'New Desired Name Ltd'}
    )
    assert status == "200 OK", rename_ok
    assert rename_ok['company']['name'] == 'New Desired Name Ltd'
    assert rename_ok['company']['company_number'] == 'REG-16088'
    assert rename_ok['company']['is_registered'] is False
    stored_rename = query_db("SELECT name, company_number, ch_checked_at FROM companies WHERE id = ?;", (rename_id,), one=True)
    assert stored_rename['name'] == 'New Desired Name Ltd'
    assert stored_rename['company_number'] == 'REG-16088'
    status, headers, client_renamed = make_request('/api/client/companies', cookie=f"session_token={token}")
    assert status == "200 OK", client_renamed
    client_row = next(row for row in (client_renamed.get('companies') or []) if int(row.get('id')) == int(rename_id))
    assert client_row['name'] == 'New Desired Name Ltd'
    status, headers, client_order = make_request(f"/api/client/orders/{rename_order_id}", cookie=f"session_token={token}")
    assert status == "200 OK", client_order
    assert client_order['order']['company_name'] == 'New Desired Name Ltd'
    desired = next(
        (field for field in (client_order['order'].get('checkout_form') or []) if str(field.get('label') or '').lower() in ('desired company name', 'company name')),
        None,
    )
    assert desired and desired['value'] == 'New Desired Name Ltd'
    print("✓ Staff can rename a pending REG- company before the Companies House application")
    print("✓ Client portal and order checkout show the renamed company")
    status, headers, accountancy_unauth = make_request('/api/admin/accountancy')
    assert status == "401 Unauthorized"
    status, headers, accountancy_client = make_request(
        '/api/admin/accountancy',
        cookie=f"session_token={token}"
    )
    assert status == "403 Forbidden"
    status, headers, accountancy_list = make_request(
        '/api/admin/accountancy',
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", accountancy_list
    listed = accountancy_list.get('companies') or []
    assert any(int(row.get('id')) == int(posted_company['id']) for row in listed)
    target = next(row for row in listed if int(row.get('id')) == int(posted_company['id']))
    status, headers, accountancy_save = make_request(
        '/api/admin/accountancy', method='POST',
        cookie=f"session_token={adm_comp_token}",
        body={
            'company_id': posted_company['id'],
            'period_start': (target.get('current') or {}).get('period_start'),
            'period_end': (target.get('current') or {}).get('period_end'),
            'due_date': (target.get('current') or {}).get('due_date'),
            'accounts_type': 'Micro-entity',
            'status': 'Filed',
            'confirmation_number': 'CH-AA-1001',
        }
    )
    assert status == "200 OK", accountancy_save
    assert accountancy_save['filing']['status'] == 'Filed'
    assert accountancy_save['filing']['confirmation_number'] == 'CH-AA-1001'
    status, headers, client_accountancy = make_request(
        '/api/client/accountancy',
        cookie=f"session_token={token}"
    )
    assert status == "200 OK", client_accountancy
    client_row = next(row for row in (client_accountancy.get('companies') or []) if int(row.get('id')) == int(posted_company['id']))
    filed = [item for item in (client_row.get('filings') or []) if item.get('status') == 'Filed']
    assert filed, client_row
    assert filed[0].get('confirmation_number') == 'CH-AA-1001'
    assert 'notes' not in filed[0]
    assert 'readiness' in target
    assert 'address' in (target.get('readiness') or {})
    assert 'identity' in (target.get('readiness') or {})
    assert 'psc' in (target.get('readiness') or {})
    assert isinstance((target.get('readiness') or {}).get('issues'), list)
    assert 'authentication_code' not in target
    assert 'activation_code' not in target
    issue_codes = {item.get('code') for item in ((target.get('readiness') or {}).get('issues') or [])}
    assert 'identity' in issue_codes
    assert 'psc' in issue_codes
    assert 'authentication_code' not in client_row
    assert 'activation_code' not in client_row
    assert (client_row.get('readiness') or {}).get('identity')
    assert 'authentication_code' not in (client_row.get('readiness') or {})
    assert 'personal_code' not in (client_row.get('readiness') or {})
    print("✓ Accountancy yearly accounts can be filed by staff and shown to the client")
    status, headers, idv_only = make_request(
        f"/api/admin/companies/{posted_company['id']}", method='PUT',
        cookie=f"session_token={adm_comp_token}",
        body={'identity_verified': 'Verified', 'psc_verified': 'Verified'}
    )
    assert status == "200 OK", idv_only
    assert idv_only['company']['identity_verified'] == 'Verified'
    assert idv_only['company']['psc_verified'] == 'Verified'
    assert idv_only['company']['authentication_code'] == 'AB12CD'
    assert idv_only['company']['activation_code'] == 'ACT-9988'
    status, headers, books_unauth = make_request(f"/api/admin/accountancy/{posted_company['id']}/books")
    assert status == "401 Unauthorized"
    status, headers, books_client = make_request(
        f"/api/admin/accountancy/{posted_company['id']}/books",
        cookie=f"session_token={token}"
    )
    assert status == "403 Forbidden"
    status, headers, books_get = make_request(
        f"/api/admin/accountancy/{posted_company['id']}/books",
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", books_get
    assert isinstance(books_get.get('entries'), list)
    assert 'totals' in books_get
    assert 'readiness' in books_get
    assert 'statements' in books_get
    assert books_get['readiness']['identity']['ok'] is True
    assert books_get['readiness']['psc']['ok'] is True
    period_end = books_get.get('period_end') or (target.get('current') or {}).get('period_end')
    status, headers, books_post = make_request(
        f"/api/admin/accountancy/{posted_company['id']}/books", method='POST',
        cookie=f"session_token={adm_comp_token}",
        body={
            'period_end': period_end,
            'entry_date': period_end,
            'description': 'Client receipts from bank statement',
            'amount': '1250.50',
            'category': 'Turnover',
            'direction': 'in',
            'source': 'bank_statement',
        }
    )
    assert status == "200 OK", books_post
    assert books_post['entry']['amount'] == 1250.5
    assert books_post['totals']['inflows'] == 1250.5
    entry_id = books_post['entry']['id']
    status, headers, books_out = make_request(
        f"/api/admin/accountancy/{posted_company['id']}/books", method='POST',
        cookie=f"session_token={adm_comp_token}",
        body={
            'period_end': period_end,
            'description': 'Companies House filing fee',
            'amount': '13',
            'category': 'Other operating charges',
            'source': 'bank_statement',
        }
    )
    assert status == "200 OK", books_out
    assert books_out['entry']['amount'] == -13.0
    status, headers, books_del = make_request(
        f"/api/admin/accountancy/{posted_company['id']}/books/{entry_id}", method='DELETE',
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", books_del
    remaining = [item for item in (books_del.get('entries') or []) if int(item.get('id')) == int(entry_id)]
    assert not remaining
    print("✓ Accountancy books workspace posts statement lines and keeps IDV/PSC on the company card")
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
    assert 'Documents uploaded' in html_body
    assert 'Important notification' not in html_body
    assert 'congratulations.png' not in html_body
    assert 'congratulations.gif' not in html_body
    assert 'brixen-logo.png' in html_body
    assert 'Received by post.' in html_body
    assert '📄 Posted_Certificate.pdf' in html_body
    assert 'bgcolor="#003971"' in html_body
    assert 'border-radius:980px' in html_body
    assert 'The Brixen Consultants Team' in html_body or 'The Brixen Consultants team' in html_body
    generic_subject, generic_text, generic_html = app_mod.build_client_notification_email(
        {'full_name': 'James Example', 'email': 'client1@acmecorp.co.uk'},
        'Your order status has changed',
        'Order #GB103449JUL26 is now Processing (40% complete).',
        detail_title='Status',
        detail_value='Processing (40% complete)',
    )
    assert 'Your order status has changed' in generic_subject
    assert 'Your order status has changed' in generic_html
    assert 'Open Client Panel' in generic_html
    assert 'congratulations.png' not in generic_html
    assert 'bgcolor="#003971"' in generic_html
    assert 'brixen-logo.png' in generic_html
    assert '#e8ecf1' in generic_html or '#f5f5f7' in generic_html or '#f8fafc' in generic_html or '#f3efe8' in generic_html
    assert '-apple-system' in generic_html
    assert 'Segoe UI' in generic_html or '-apple-system' in generic_html
    assert 'brixenconsultants.com' in html_body
    assert 'Brixen-Consultants.png' in html_body
    assert 'rel="icon"' in html_body
    assert 'Brixen-Consultants.png' in html_body
    assert 'cid:brixen-favicon' not in html_body
    celeb_subject, celeb_text, celeb_html = app_mod.build_client_notification_email(
        {'full_name': 'James Example', 'email': 'client1@acmecorp.co.uk'},
        'Example Holdings Ltd',
        "We've got great news, Example Holdings Ltd is now officially registered with Companies House.",
        layout='celebration',
        greeting_name='Alex Director',
        badge='Company registered',
        detail_title='Company number',
        detail_value='12345678',
        cta_label='Download now',
        cta_url='https://find-and-update.company-information.service.gov.uk/company/12345678/filing-history',
        footer_note='This email is about your UK company registration with Brixen Consultants.',
    )
    assert 'Example Holdings Ltd' in celeb_subject or 'Example Holdings Ltd' in celeb_html
    assert 'Hi Alex Director' in celeb_html
    assert 'Hi James' not in celeb_html
    assert 'Download now' in celeb_html
    assert 'rel="icon"' in celeb_html
    assert 'Brixen-Consultants.png' in celeb_html
    assert 'brixen-logo.png' in celeb_html
    assert 'src="cid:brixen-favicon"' not in celeb_html
    assert 'cid:brixen-favicon' not in celeb_html
    assert 'certificate of incorporation' in celeb_html
    assert 'https://find-and-update.company-information.service.gov.uk/company/12345678/filing-history' in celeb_html
    assert 'align="center"' in celeb_html
    assert 'Your next-step strategy' in celeb_html
    assert 'Bank account' in celeb_html
    assert 'Registered office and mail from £20' in celeb_html
    assert 'Free consultation' in celeb_html
    assert 'WhatsApp for a free consultation' in celeb_html
    assert 'wa.me' in celeb_html
    assert '🏦' in celeb_html
    assert '🎁' in celeb_html
    assert 'border-radius:980px' in celeb_html
    assert 'padding:11px 22px' in celeb_html
    assert 'font-size:13px' in celeb_html
    activity_subject, activity_text, activity_html = app_mod.build_client_notification_email(
        {'full_name': 'James Example', 'email': 'client1@acmecorp.co.uk'},
        'Identity verification is done',
        'A required company check is now complete.',
        layout='activity',
        extra_message='✅ Identity verification is complete\n🪪 Companies House can now accept your filings',
        cta_label='View your company',
    )
    assert 'Identity verification is done' in activity_html
    assert 'congratulations.png' not in activity_html
    assert 'bgcolor="#003971"' in activity_html
    assert 'rel="icon"' in activity_html
    assert 'Brixen-Consultants.png' in activity_html
    assert 'src="cid:brixen-favicon"' not in activity_html
    assert 'cid:brixen-favicon' not in activity_html
    assert '✅ Identity verification is complete' in activity_html
    assert 'Hi James Example,' in activity_html
    assert '👋' not in activity_html
    assert 'border-radius:999px' not in activity_html
    assert '✅' in activity_html
    assert '-apple-system' in activity_html
    assert 'border-radius:980px' in activity_html
    assert 'padding:11px 22px' in activity_html
    assert app_mod.classify_product_activity('Annual Compliance Filing') == 'accounts'
    assert app_mod.classify_product_activity('Company Formation Package') == 'formation'
    assert 'The Brixen Consultants Team' in celeb_html or 'The Brixen Consultants team' in celeb_html
    assert 'contact@brixenconsultants.com' in celeb_html
    assert 'brixenconsultants.com' in celeb_html
    assert 'Brixen Consultants Ltd' in celeb_html
    assert 'Kind regards' in celeb_html
    assert 'letter-spacing:0.22em' not in celeb_html
    assert 'congratulations.png' in celeb_html
    assert 'congratulations.gif' not in celeb_html
    assert 'background-image:url(' in celeb_html
    assert not re.search(r'<img[^>]+congratulations', celeb_html, re.I)
    assert 'cid:brixen-congratulations' not in celeb_html
    assert 'cid:brixen-wordmark' not in celeb_html
    assert 'bgcolor="#003971"' in celeb_html
    assert 'bgcolor="#25D366"' in celeb_html
    assert '<font color="#ffffff">' in celeb_html
    assert '<font color="#c5a572">' in celeb_html
    assert 'color-scheme" content="light"' in celeb_html
    assert 'Company number 12345678' in celeb_html
    assert 'Company number: 12345678' in celeb_text or '12345678' in celeb_text
    assert 'Important notification' not in celeb_html
    assert '17314564' not in celeb_html
    assert 'Wellesley' not in celeb_html
    assert not app_mod.inline_images_for_html(celeb_html)
    assert app_mod.invoice_email_greeting_name(
        {'owner_name': 'Ayesha Khan', 'full_name': 'Brixen Consultant', 'company_name': 'Khan Trading Ltd'},
        {'owner_name': 'Ayesha Khan'},
        {'full_name': 'Brixen Consultant'},
    ) == 'Ayesha Khan'
    assert app_mod.invoice_email_greeting_name(
        {'full_name': 'Brixen Consultant'},
        {},
        {'full_name': 'Brixen Consultant'},
    ) == 'there'
    inv_subject, inv_text, inv_html = app_mod.build_client_notification_email(
        {'full_name': 'Brixen Consultant', 'email': 'staff@example.com'},
        'Invoice',
        app_mod.invoice_payment_request_copy(),
        layout='invoice',
        greeting_name='Ayesha Khan',
        subject='Invoice INV-2044 — Brixen Consultants',
        cta_label='Download invoice',
        cta_url='https://portal.brixenconsultants.com/invoice/2044/1/abc/Invoice%20INV-2044.pdf',
        invoice_receipt={
            'kicker': 'Thank you for your order',
            'title': 'Invoice',
            'date': '1 September 2026',
            'invoice_number': 'INV-2044',
            'order_number': '#WC-2044',
            'customer_email': 'ayesha@example.com',
            'items': [{'description': 'VAT Registration', 'quantity': 1, 'unit_price': 30, 'amount': 30}],
            'subtotal': '£30.00',
            'tax': '£0.00',
            'show_tax': False,
            'total': '£30.00',
            'amount_due': '£30.00',
            'amount_paid': '£0.00',
            'show_due': True,
            'show_paid': False,
            'fully_paid': False,
            'include_bank': True,
            'gbp': dict(app_mod.INVOICE_BANK_GBP),
            'pkr': dict(app_mod.INVOICE_BANK_PKR),
            'fx': {},
            'support_email': 'contact@brixenconsultants.com',
        },
    )
    assert inv_subject == 'Invoice INV-2044 — Brixen Consultants'
    assert 'Hi Ayesha Khan,' in inv_html
    assert '👋' not in inv_html
    assert 'ayesha@example.com' in inv_html
    assert 'Hello Brixen Consultant' not in inv_html
    assert 'Hi Brixen Consultant' not in inv_html
    assert 'Invoice INV-2044' in inv_html
    assert 'Order #WC-2044' in inv_html
    assert 'VAT Registration' in inv_html
    assert 'Qty 1' in inv_html
    assert 'Pay in GBP' in inv_html
    assert 'Pay in PKR' in inv_html
    assert 'Need help?' in inv_html
    assert 'Contact Support' not in inv_html
    assert 'Pay now in £' not in inv_html
    assert 'Download invoice' in inv_html
    assert '/invoice/2044/' in inv_html
    assert 'Invoice%20INV-2044.pdf' in inv_html or 'Invoice INV-2044.pdf' in inv_html
    assert 'font-size:20px' in inv_html
    assert '#003971' in inv_html
    assert '#f3efe8' in inv_html
    assert '#006cff' not in inv_html
    assert '#28a745' not in inv_html
    assert 'width:48%' not in inv_html
    assert 'border-radius:999px' not in inv_html
    assert '-apple-system' in inv_html
    assert 'Brixen Consultants' in inv_html
    paid_html = app_mod.invoice_receipt_html(
        {
            'kicker': 'Thank you for your order',
            'title': 'Payment received',
            'date': '1 September 2026',
            'invoice_number': 'INV-2045',
            'items': [{'description': 'VAT Registration', 'quantity': 1, 'amount': 30}],
            'subtotal': '£30.00',
            'show_tax': False,
            'total': '£30.00',
            'amount_paid': '£30.00',
            'amount_due': '£0.00',
            'show_due': False,
            'show_paid': True,
            'fully_paid': True,
            'include_bank': False,
            'support_email': 'contact@brixenconsultants.com',
        },
        'Ayesha Khan',
        'Thank you. Your payment for this completed work is in.',
        'https://example.com/static/img/brixen-logo.png',
        app_mod.apple_email_button_html('https://example.com/invoice', 'Download invoice'),
        'footer',
    )
    assert 'Amount due' not in paid_html
    assert '>Paid<' in paid_html
    assert 'Pay in GBP' not in paid_html
    mime_msg, envelope_from = app_mod.build_outbound_email(
        'contact@brixenconsultants.com',
        'Congratulations — Example Holdings Ltd is registered',
        celeb_text,
        celeb_html,
    )
    mime_blob = mime_msg.as_string()
    assert 'brixen-favicon' not in mime_blob
    assert 'Content-ID:' not in mime_blob
    assert 'filename="congratulations' not in mime_blob
    assert 'Brixen Consultants' in str(mime_msg['From'])
    assert 'LTD' not in str(mime_msg['From'])
    assert mime_msg['Reply-To']
    assert mime_msg['Message-ID']
    assert '@' in str(mime_msg['Message-ID'])
    assert envelope_from.rsplit('@', 1)[-1] in str(mime_msg['Message-ID'])
    assert mime_msg['Date']
    assert '@' in envelope_from
    assert mime_msg['Organization'] == 'Brixen Consultants'
    assert mime_msg['Content-Language'] == 'en-GB'
    assert not mime_msg['Auto-Submitted']
    assert not mime_msg['X-Mailer']
    assert not mime_msg['X-Priority']
    assert not mime_msg['Precedence']
    assert not mime_msg['List-Unsubscribe']
    queued, queued_status = app_mod.EmailService.send_notification_email(
        'queued-client@example.com',
        'Queued until SMTP is configured',
        'This should be kept in the outbox.',
        '<p>This should be kept in the outbox.</p>',
    )
    app_mod.ensure_schema()
    outbox_rows = query_db(
        "SELECT recipient, subject, status FROM email_outbox WHERE recipient = 'queued-client@example.com' ORDER BY id DESC;"
    ) or []
    assert queued_status
    assert outbox_rows, f'expected outbox row, got {outbox_rows!r} status={queued_status!r}'
    assert outbox_rows[0]['subject'] == 'Queued until SMTP is configured'
    assert outbox_rows[0]['status'] in ('queued', 'failed', 'sent')
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
    registered_snap = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'SNAPKART LTD', '17361991', 'Active', '2026-07-24', 'Test Director', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    assert app_mod.match_company_for_client(james['id'], 'SNAPKARTT LTD') == registered_snap
    typo_snap = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'SNAPKARTT LTD', 'REG-16289', 'Active', '2026-08-22', 'Test Director', 'United Kingdom', 'Digital Package', 'Pending');
        """,
        (james['id'],),
    )
    snap_form = json.dumps([
        {'label': 'Director name', 'value': 'Test Director'},
        {'label': 'Email (form)', 'value': 'owner-form@example.com'},
        {'label': 'Billing address', 'value': '85 Dunstall Hill, Wolverhampton, WV6 0SR'},
    ])
    snap_order_id = execute_db(
        """
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent, woocommerce_order_id, checkout_form_json)
        VALUES ('#WC-16289', ?, ?, 'Digital Package', 53, 10.59, 63.59, 'Completed', 100, '16289', ?);
        """,
        (james['id'], typo_snap, snap_form),
    )
    status, headers, typo_del = make_request(
        f'/api/admin/companies/{typo_snap}', method='DELETE',
        cookie=f"session_token={adm_comp_token}"
    )
    assert status == "200 OK", typo_del
    assert query_db("SELECT id FROM companies WHERE id = ?;", (typo_snap,), one=True) is None
    relinked = query_db(
        "SELECT company_id, portfolio_hidden, checkout_form_json FROM orders WHERE id = ?;",
        (snap_order_id,),
        one=True,
    )
    assert relinked['company_id'] == registered_snap
    assert int(relinked['portfolio_hidden'] or 0) == 0
    relinked_fields = {field['label']: field['value'] for field in json.loads(relinked['checkout_form_json'])}
    assert relinked_fields.get('Director name') == 'Test Director'
    assert relinked_fields.get('Email (form)') == 'owner-form@example.com'
    assert relinked_fields.get('Desired company name') == 'SNAPKART LTD'
    snap_typo_payload = {
        'event_id': 'evt_wc_snapkart_typo_1',
        'event_type': 'order.created',
        'data': {
            'woocommerce_order_id': '16290',
            'order_number': '#WC-16290',
            'wordpress_user_id': 'wp_user_101',
            'email': 'client1@acmecorp.co.uk',
            'company_name': 'SNAPKARTT LTD',
            'service_name': 'Digital Package',
            'price': 53,
            'total': 63.59,
            'status': 'Processing',
            'line_items': [{
                'product_name': 'Digital Package',
                'category': 'Company Incorporation',
                'quantity': 1,
            }],
        },
    }
    typo_bytes = json.dumps(snap_typo_payload).encode('utf-8')
    typo_sig = hmac.new(webhook_secret_bytes(), typo_bytes, hashlib.sha256).hexdigest()
    status, headers, typo_sync = make_request(
        '/api/v1/wordpress/webhook', method='POST', body=snap_typo_payload,
        headers={'X-Brixen-Signature': typo_sig}
    )
    assert status == "200 OK", typo_sync
    snap_cards = query_db(
        "SELECT id, name FROM companies WHERE user_id = ? AND name LIKE 'SNAPKART%';",
        (james['id'],),
    ) or []
    assert [row['name'] for row in snap_cards] == ['SNAPKART LTD']
    synced_typo = query_db("SELECT company_id FROM orders WHERE order_number = '#WC-16290';", one=True)
    assert synced_typo['company_id'] == registered_snap
    print("✓ Duplicate typo company card is merged into the registered company name")
    love_wales_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'Love Wales Ltd', 'REG-16310', 'Active', '2026-08-25', 'Test Director', 'United Kingdom', 'Digital Package', 'Pending');
        """,
        (james['id'],),
    )
    rabexa_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'RABEXA LTD', '17428561', 'Active', '2026-08-31', 'Muhib Ul Nabi', 'United Kingdom', 'Digital Package', 'Good Standing');
        """,
        (james['id'],),
    )
    love_order_id = execute_db(
        """
        INSERT INTO orders (order_number, user_id, company_id, service_name, price, vat, total, status, progress_percent, woocommerce_order_id, checkout_form_json)
        VALUES ('#WC-16310', ?, ?, 'Digital Package', 53, 10.59, 63.59, 'Completed', 100, '16310', ?);
        """,
        (james['id'], rabexa_id, json.dumps([
            {'label': 'Desired company name', 'value': 'RABEXA LTD'},
            {'label': 'Director name', 'value': 'Muhib Ul Nabi'},
        ])),
    )
    assert app_mod.merge_pending_order_company_duplicates() >= 1
    assert query_db("SELECT id FROM companies WHERE id = ?;", (love_wales_id,), one=True) is None
    kept = query_db("SELECT company_id FROM orders WHERE id = ?;", (love_order_id,), one=True)
    assert kept['company_id'] == rabexa_id
    assert query_db("SELECT name FROM companies WHERE id = ?;", (rabexa_id,), one=True)['name'] == 'RABEXA LTD'
    reuse_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'Love Wales Ltd', 'REG-16311', 'Active', '2026-08-25', 'Test Director', 'United Kingdom', 'Digital Package', 'Pending');
        """,
        (james['id'],),
    )
    reused = app_mod.ensure_company_from_registration_order(
        james['id'],
        'Test Director',
        {
            'company_name': 'RABEXA TWO LTD',
            'woocommerce_order_id': '16311',
            'service_name': 'Digital Package',
        },
        'Digital Package',
        [{'product_name': 'Digital Package'}],
        '16311',
    )
    assert reused == reuse_id
    assert query_db("SELECT name FROM companies WHERE id = ?;", (reuse_id,), one=True)['name'] == 'RABEXA TWO LTD'
    print("✓ Same order number keeps one company when Love Wales converts to RABEXA LTD")
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
    assert new_client_company.get('company', {}).get('registered_email') == 'precrm.owner@example.com'
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
            'price': 50,
            'total': 60,
            'status': 'Processing',
            'line_items': [
                {
                    'product_name': 'Registered Office Address',
                    'category': 'Address Services',
                    'quantity': 1,
                    'unit_price': 50,
                    'line_total': 50,
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
    assert listed.get('total') is not None
    assert 'payment_mode' in listed
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
    status, headers, form_denied = make_request(
        f"/api/admin/orders/{wc_order_id}", method='PUT',
        cookie=f"session_token={token}",
        body={'checkout_form': [{'label': 'Email (form)', 'value': 'hacked@example.com'}]}
    )
    assert status == "403 Forbidden"
    status, headers, form_upd = make_request(
        f"/api/admin/orders/{wc_order_id}", method='PUT',
        cookie=f"session_token={adm_token}",
        body={'checkout_form': [
            {'label': 'Email (form)', 'value': 'updated-form@example.com'},
            {'label': 'Director name', 'value': 'Updated Director'},
        ]}
    )
    assert status == "200 OK", form_upd
    status, headers, form_detail = make_request(f'/api/client/orders/{wc_order_id}', cookie=f"session_token={adm_token}")
    assert status == "200 OK"
    form_fields = {str(field.get('label') or ''): str(field.get('value') or '') for field in (form_detail.get('order', {}).get('checkout_form') or [])}
    assert form_fields.get('Email (form)') == 'updated-form@example.com'
    assert form_fields.get('Director name') == 'Updated Director'
    owner = form_detail.get('order', {}).get('company_owner') or {}
    assert owner.get('form_email') == 'updated-form@example.com'
    assert owner.get('full_name') == 'Updated Director'
    stored_owner = query_db("SELECT full_name, form_email FROM company_owners WHERE order_id = ?;", (wc_order_id,), one=True)
    assert stored_owner['form_email'] == 'updated-form@example.com'
    assert stored_owner['full_name'] == 'Updated Director'
    order_owner_cols = query_db("SELECT owner_name, owner_form_email FROM orders WHERE id = ?;", (wc_order_id,), one=True)
    assert order_owner_cols['owner_form_email'] == 'updated-form@example.com'
    print("✓ Staff can correct checkout form details after the order is placed")
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
    status, headers, sso_get = make_request(
        f"/api/v1/auth/sso?wordpress_user_id={sso_wp_id}&email={sso_email}&timestamp={now_ts}&signature={valid_sig}"
    )
    assert status == "302 Found"
    assert headers.get('Location') == '/#dashboard'
    assert extract_session_token(headers)
    print("✓ WP SSO Bridge Exchange -> Valid signed SSO succeeded (POST + GET redirect to dashboard)")

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
    assert 'attachment' in (headers.get('Content-Disposition') or '')
    status, headers, preview_bytes = make_request(f"/api/documents/{doc_id}/view", cookie=f"session_token={token}")
    assert status == "200 OK"
    assert preview_bytes == raw_pdf_bytes
    assert str(headers.get('Content-Disposition') or '').startswith('inline')
    assert headers.get('Content-Type') == 'application/pdf'
    assert headers.get('Accept-Ranges') == 'bytes'
    status, headers, preview_range = make_request(
        f"/api/documents/{doc_id}/view",
        cookie=f"session_token={token}",
        headers={'Range': 'bytes=0-3'}
    )
    assert status == "206 Partial Content"
    assert preview_range == b'%PDF'
    assert str(headers.get('Content-Range') or '').startswith('bytes 0-3/')
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
    for o in (marcus_orders.get('orders') or []):
        assert 'total' not in o
        assert 'price' not in o
        assert 'vat' not in o
        assert 'payment_mode' not in o
        assert 'payment_status' not in o
    marcus_oid = (marcus_orders.get('orders') or [{}])[0].get('id')
    if marcus_oid:
        status, headers, marcus_price = make_request(
            f"/api/admin/orders/{marcus_oid}",
            method='PUT',
            body={'total': 333.33},
            cookie=f"session_token={marcus_token}"
        )
        assert status == "403 Forbidden", marcus_price
        status, headers, marcus_pay = make_request(
            f"/api/admin/orders/{marcus_oid}",
            method='PUT',
            body={'payment_mode': 'GBP(Bank Transfer)'},
            cookie=f"session_token={marcus_token}"
        )
        assert status == "403 Forbidden", marcus_pay
        status, headers, marcus_detail = make_request(
            f"/api/client/orders/{marcus_oid}",
            cookie=f"session_token={marcus_token}"
        )
        assert status == "200 OK", marcus_detail
        assert 'total' not in (marcus_detail.get('order') or {})
        assert 'payment_mode' not in (marcus_detail.get('order') or {})
        for item in (marcus_detail.get('line_items') or []):
            assert 'unit_price' not in item
            assert 'line_total' not in item
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
        body={'departments': ['New Signups', 'Orders', 'Documents', 'Support', 'Compliance', 'Accounts', 'Accountancy', 'General']},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    status, headers, eleanor_relogin = make_request(
        '/api/auth/login',
        method='POST',
        body={'email': 'eleanor.finch@brixenconsultant.co.uk', 'password': 'StaffPass123!'}
    )
    eleanor_all_token = extract_session_token(headers)
    for path in ('/api/admin/orders', '/api/admin/customers', '/api/admin/documents', '/api/admin/invoices'):
        st, hd, body = make_request(path, cookie=f"session_token={eleanor_all_token}")
        assert st == "200 OK", f"{path} expected 200 after full access repair, got {st} ({body})"
    status, headers, restore_eleanor = make_request(
        f"/api/admin/staff/{eleanor['id']}",
        method='PUT',
        body={'departments': ['Documents', 'Orders']},
        cookie=f"session_token={adm_token}"
    )
    assert status == "200 OK"
    print("✓ Staff Access Repair -> missing RBAC rows are inserted; full ticks open staff modules.")

    status, headers, eleanor_relogin2 = make_request(
        '/api/auth/login',
        method='POST',
        body={'email': 'eleanor.finch@brixenconsultant.co.uk', 'password': 'StaffPass123!'}
    )
    eleanor_updated_token = extract_session_token(headers)
    status, headers, staff_edit = make_request(
        f"/api/admin/tasks/{task_id}",
        method='PUT',
        body={'status': 'In Progress', 'internal_notes': 'Started Companies House check.'},
        cookie=f"session_token={eleanor_updated_token}"
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
    assert 'total' not in (mgr_order.get('order') or {})
    assert 'payment_mode' not in (mgr_order.get('order') or {})
    st, hd, super_order = rbac_request('/api/client/orders/1', super_token)
    assert st == "200 OK"
    assert 'total' in (super_order.get('order') or {})
    st, hd, mgr_msgs = rbac_request(f'/api/client/tickets/{tick_id}/messages', manager_token)
    assert st == "200 OK"
    print("✓ Order/Ticket RBAC -> MANAGER and SUPER_ADMIN can access staff ticket/order detail")

    rbac_get_matrix = [
        ('/api/admin/stats', 'admin dashboard', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '200 OK', 'STAFF': '200 OK', 'CLIENT': '403 Forbidden'
        }),
        ('/api/admin/services', 'orders.view', {
            'SUPER_ADMIN': '200 OK', 'ADMIN': '200 OK', 'MANAGER': '200 OK', 'STAFF': '200 OK', 'CLIENT': '403 Forbidden'
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
    assert 'total_revenue' not in stats_res.get('stats', {})
    assert 'pending_payments' not in stats_res.get('stats', {})
    assert not (stats_res.get('charts') or {}).get('monthly')
    st, hd, staff_stats = rbac_request('/api/admin/stats', staff_token)
    assert 'total_customers' in staff_stats.get('stats', {})
    assert 'total_revenue' not in staff_stats.get('stats', {})
    assert not (staff_stats.get('charts') or {}).get('monthly')
    st, hd, admin_stats = rbac_request('/api/admin/stats', super_token)
    assert 'total_revenue' in admin_stats.get('stats', {})
    monthly = (admin_stats.get('charts') or {}).get('monthly')
    assert isinstance(monthly, list) and len(monthly) == 6
    assert all(isinstance(m.get('month'), str) and 'rev' in m and m.get('label') for m in monthly)
    assert all(isinstance(m.get('rev'), (int, float)) for m in monthly)
    st, hd, svc_res = rbac_request('/api/admin/services', marcus_token)
    assert isinstance(svc_res.get('services'), list)
    print("✓ Admin RBAC GET payloads -> staff/manager stats omit revenue; admin gets the chart.")

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

    # ==================================================
    # AUTOMATION TASK #001: CLIENT DOCUMENT DELIVERY WORKFLOW TESTS
    # ==================================================
    # 1. Admin Auth & Setup
    st, hd, adm_res = make_request('/api/auth/login', method='POST', body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'})
    assert st == "200 OK"
    task1_admin_tok = extract_session_token(hd)
    client_a = query_db("SELECT * FROM users WHERE email = 'client1@acmecorp.co.uk';", one=True)
    client_b = query_db("SELECT * FROM users WHERE email = 'david@nexusbiotech.co.uk';", one=True)
    assert client_a and client_b
    st, hd, client_a_login = make_request('/api/auth/login', method='POST', body={'email': client_a['email'], 'password': 'ClientPass123!'})
    assert st == "200 OK"
    client_a_tok = extract_session_token(hd)
    st, hd, client_b_login = make_request('/api/auth/login', method='POST', body={'email': client_b['email'], 'password': 'ClientPass123!'})
    assert st == "200 OK"
    client_b_tok = extract_session_token(hd)

    import base64 as t1_b64
    t1_pdf_content = b"%PDF-1.4 Task 001 Test Delivery Document Content"
    t1_b64_data = t1_b64.b64encode(t1_pdf_content).decode('utf-8')
    comp_a = query_db("SELECT * FROM companies WHERE user_id = ? ORDER BY id ASC;", (client_a['id'],), one=True)
    ord_a = query_db("SELECT * FROM orders WHERE user_id = ? ORDER BY id ASC;", (client_a['id'],), one=True)

    # 2. Admin Document Upload (Client Visible = 1)
    st, hd, doc_vis_res = make_request('/api/admin/documents', method='POST', cookie=f"session_token={task1_admin_tok}", body={
        'client_id': client_a['id'],
        'company_id': comp_a['id'] if comp_a else None,
        'order_id': ord_a['id'] if ord_a else None,
        'name': 'Articles_of_Association_Task001.pdf',
        'category': 'Company Documents',
        'client_message': 'Please review your uploaded Articles of Association.',
        'file_content_base64': t1_b64_data,
        'client_visible': True
    })
    assert st == "200 OK", doc_vis_res
    assert doc_vis_res['status'] == 'success'
    vis_doc = doc_vis_res['document']
    assert vis_doc['client_visible'] == 1
    assert vis_doc['user_id'] == client_a['id']
    assert doc_vis_res.get('notification_created') is True

    # 3. Non-Client Visible Document Upload (Client Visible = 0)
    st, hd, doc_hid_res = make_request('/api/admin/documents', method='POST', cookie=f"session_token={task1_admin_tok}", body={
        'client_id': client_a['id'],
        'name': 'Internal_Compliance_Memo_Task001.pdf',
        'category': 'Compliance',
        'file_content_base64': t1_b64_data,
        'client_visible': False
    })
    assert st == "200 OK", doc_hid_res
    hid_doc = doc_hid_res['document']
    assert hid_doc['client_visible'] == 0

    # 4. Client A Lists Documents (See visible, hide internal)
    st, hd, client_a_docs = make_request('/api/client/documents', cookie=f"session_token={client_a_tok}")
    assert st == "200 OK"
    client_a_doc_ids = [d['id'] for d in client_a_docs.get('documents') or []]
    assert vis_doc['id'] in client_a_doc_ids
    assert hid_doc['id'] not in client_a_doc_ids

    # 5. Client B Cross-Access List Isolation (Client B cannot see Client A's document)
    st, hd, client_b_docs = make_request('/api/client/documents', cookie=f"session_token={client_b_tok}")
    assert st == "200 OK"
    client_b_doc_ids = [d['id'] for d in client_b_docs.get('documents') or []]
    assert vis_doc['id'] not in client_b_doc_ids
    assert hid_doc['id'] not in client_b_doc_ids

    # 6. Client A Downloads Visible Document
    st, hd, dl_vis_bytes = make_request(f"/api/client/documents/{vis_doc['id']}/download", cookie=f"session_token={client_a_tok}")
    assert st == "200 OK"
    assert dl_vis_bytes == t1_pdf_content

    # 7. Client A Download Protection (Hidden Document -> 403 Forbidden)
    st, hd, dl_hid_res = make_request(f"/api/client/documents/{hid_doc['id']}/download", cookie=f"session_token={client_a_tok}")
    assert st == "403 Forbidden"

    # 8. Client B Download Protection (Cross-Client -> 403 Forbidden)
    st, hd, dl_cross_res = make_request(f"/api/client/documents/{vis_doc['id']}/download", cookie=f"session_token={client_b_tok}")
    assert st == "403 Forbidden"

    # 9. In-App Notification Check
    t1_note = query_db("SELECT * FROM notifications WHERE user_id = ? AND type = 'document_uploaded' ORDER BY id DESC;", (client_a['id'],), one=True)
    assert t1_note is not None
    assert t1_note['title'] == 'New document available'
    assert 'Brixen Consultants has uploaded a new document to your client portal' in t1_note['message']

    # 10. Transactional Email Outbox Check
    t1_email = query_db("SELECT * FROM email_outbox WHERE recipient = ? ORDER BY id DESC;", (client_a['email'],), one=True)
    assert t1_email is not None
    assert 'A new document is available in your Brixen client portal' in t1_email['subject']
    assert 'Articles_of_Association_Task001.pdf' in (t1_email['body_html'] or t1_email['body_text'])

    # 11. Activity Audit Log Check
    t1_audit = query_db("SELECT * FROM activity_logs WHERE entity_type = 'documents' AND entity_id = ?;", (str(vis_doc['id']),), one=True)
    assert t1_audit is not None
    assert t1_audit['action'] == 'DOCUMENT_UPLOAD'

    # 12. Security Validation Checks
    st, hd, err_invalid_client = make_request('/api/admin/documents', method='POST', cookie=f"session_token={task1_admin_tok}", body={'client_id': 999999, 'name': 'Fail.pdf', 'file_content_base64': t1_b64_data})
    assert st == "404 Not Found"

    st, hd, err_unauth_client = make_request('/api/admin/documents', method='POST', cookie=f"session_token={client_a_tok}", body={'client_id': client_a['id'], 'name': 'Fail.pdf', 'file_content_base64': t1_b64_data})
    assert st == "403 Forbidden"

    print("✓ AUTOMATION TASK #001: Client Document Delivery Workflow verified 100%")

    assert app_mod.is_companies_house_default_address(
        '15203368 - COMPANIES HOUSE DEFAULT ADDRESS, Cardiff, CF14 8LH'
    )
    assert not app_mod.is_companies_house_default_address(
        '71-75 Shelton Street, Covent Garden, London, WC2H 9JQ'
    )
    luminavest_issues = app_mod.companies_house_attention_issues({
        'reg_office': 'PO Box 4385, 15203368 - COMPANIES HOUSE DEFAULT ADDRESS, Cardiff, CF14 8LH',
        'accounts_next_due': '2026-07-31',
        'accounts_overdue': True,
        'confirmation_next_due': '2027-07-09',
        'confirmation_overdue': False,
        'status': 'Active',
        'status_detail': 'active-proposal-to-strike-off',
    }, today=datetime.date(2026, 9, 3))
    luminavest_codes = [item['code'] for item in luminavest_issues]
    assert luminavest_codes == ['accounts_overdue', 'default_address', 'strike_off']
    pending_cs = app_mod.companies_house_attention_issues({
        'confirmation_next_due': '2026-09-03',
        'confirmation_overdue': False,
    }, today=datetime.date(2026, 9, 3))
    assert any(item['code'] == 'confirmation_pending' for item in pending_cs)
    future_cs = app_mod.companies_house_attention_issues({
        'confirmation_next_due': '2027-07-09',
        'confirmation_overdue': False,
    }, today=datetime.date(2026, 9, 3))
    assert future_cs == []
    assert app_mod.companies_house_status_value('active', 'active-proposal-to-strike-off') == 'Strike off proposed'
    preview_subject, preview_text, preview_html = app_mod.build_companies_house_attention_email(
        {'full_name': 'Muhammad Huzaifa Sheikh', 'email': 'brixenconsultant@gmail.com'},
        {'name': 'LUMINAVEST LTD', 'company_number': '15203368'},
        luminavest_issues,
        greeting_name='Muhammad Huzaifa Sheikh',
    )
    assert 'LUMINAVEST LTD' in preview_subject
    assert 'Attention required' in preview_html
    assert 'Accounts overdue' in preview_html
    assert 'default address' in preview_html.lower()
    assert 'strike' in preview_html.lower()
    assert '⚠️' in preview_html
    assert 'Hi Muhammad Huzaifa Sheikh,' in preview_html
    assert '👋' not in preview_html
    assert 'border-radius:999px' not in preview_html
    assert 'Action required' in preview_html or 'Attention required' in preview_html
    assert 'border-radius:980px' in preview_html
    assert 'bgcolor="#003971"' in preview_html
    assert 'View on Companies House' in preview_html
    assert 'Open Client Panel' in preview_html
    assert '15203368' in preview_html
    assert '-apple-system' in preview_html
    attention_client = query_db("SELECT id, email, full_name FROM users WHERE role = 'CLIENT' ORDER BY id ASC LIMIT 1;", one=True)
    attention_company_id = execute_db(
        """
        INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
        VALUES (?, 'ATTENTION DEMO LTD', '15990001', 'Active', '2024-01-01', 'Alex Director',
                'PO Box 4385, COMPANIES HOUSE DEFAULT ADDRESS, Cardiff, CF14 8LH', 'Standard Corporate', 'Good Standing');
        """,
        (attention_client['id'],),
    )
    captured_attention = []
    original_attention_send = app_mod.EmailService.send_notification_email
    def capture_attention_email(recipient_email, subject, body_text, body_html=None, inline_images=None):
        captured_attention.append({
            'to': recipient_email,
            'subject': subject,
            'text': body_text,
            'html': body_html,
        })
        return True, 'Delivered'
    app_mod.EmailService.send_notification_email = staticmethod(capture_attention_email)
    try:
        first = app_mod.persist_companies_house_filing_state(
            {'id': attention_company_id, 'user_id': attention_client['id'], 'name': 'ATTENTION DEMO LTD', 'company_number': '15990001',
             'director': 'Alex Director', 'reg_office': 'PO Box 4385, COMPANIES HOUSE DEFAULT ADDRESS, Cardiff, CF14 8LH'},
            {
                'company_number': '15990001',
                'name': 'ATTENTION DEMO LTD',
                'status': 'Strike off proposed',
                'status_detail': 'active-proposal-to-strike-off',
                'reg_office': 'PO Box 4385, COMPANIES HOUSE DEFAULT ADDRESS, Cardiff, CF14 8LH',
                'accounts_next_due': '2026-07-31',
                'accounts_overdue': True,
                'confirmation_next_due': '2026-08-01',
                'confirmation_overdue': True,
                'confirmation_pending': True,
            },
        )
        assert {item['code'] for item in first} == {'accounts_overdue', 'confirmation_pending', 'default_address', 'strike_off'}
        assert len(captured_attention) == 1
        assert 'ATTENTION DEMO LTD' in captured_attention[0]['subject']
        assert 'Confirmation statement' in (captured_attention[0]['html'] or '')
        second = app_mod.notify_companies_house_attention(attention_company_id, first)
        assert second.get('email_status') == 'Already sent'
        assert len(captured_attention) == 1
        stored_attention = query_db(
            "SELECT ch_attention_json, accounts_overdue, confirmation_overdue, status, ch_alert_fingerprint FROM companies WHERE id = ?;",
            (attention_company_id,),
            one=True,
        )
        assert int(stored_attention.get('accounts_overdue') or 0) == 1
        assert int(stored_attention.get('confirmation_overdue') or 0) == 1
        assert stored_attention.get('status') == 'Strike off proposed'
        assert 'accounts_overdue' in (stored_attention.get('ch_alert_fingerprint') or '')
        public_attention = app_mod.public_client_company(query_db(
            f"SELECT {app_mod.COMPANY_LIST_SELECT} FROM companies WHERE id = ?;",
            (attention_company_id,),
            one=True,
        ))
        assert public_attention.get('needs_attention') is True
        assert len(public_attention.get('attention') or []) >= 3
    finally:
        app_mod.EmailService.send_notification_email = original_attention_send
    # ==================================================
    # AUTOMATION TASK: SMART DOCUMENT INTAKE WORKFLOW TESTS
    # ==================================================
    import base64 as stk_b64
    stk_bank_pdf = b"%PDF-1.4 Name: James Harrington DOB: 12/05/1984 Bank Statement Company No: 12345678 JAMES HARRINGTON LTD" + b" % " + b"X" * 900
    stk_id_pdf = b"%PDF-1.4 Passport Name: James Harrington DOB: 12/05/1984 ID Document" + b" % " + b"X" * 900
    
    st, hd, intake_res = make_request('/api/admin/documents/intake/process', method='POST', cookie=f"session_token={task1_admin_tok}", body={
        'files': [
            {'file_name': 'Bank_Statement_James_Harrington_LTD.pdf', 'file_content_base64': stk_b64.b64encode(stk_bank_pdf).decode('utf-8')},
            {'file_name': 'Passport_James_Harrington.pdf', 'file_content_base64': stk_b64.b64encode(stk_id_pdf).decode('utf-8')},
        ]
    })
    assert st == "200 OK", intake_res
    assert intake_res['status'] == 'success'
    assert intake_res['client']['full_name'] == 'James Harrington'
    assert len(intake_res['filed_documents']) == 2
    assert intake_res['filed_documents'][0]['category'] == 'Bank statement'
    assert intake_res['filed_documents'][1]['category'] == 'ID Document'
    assert all(d.get('lifecycle_status') == 'CUSTOMER_UPLOADS' for d in intake_res['filed_documents']), intake_res['filed_documents']
    assert intake_res['client']['dob'] == '12/05/1984'

    garbage = app_mod.extract_document_text_and_metadata(
        'scan.png',
        stk_b64.b64encode(b'Name: K Ge NATIONALITY PAKISTAN').decode('utf-8'),
    )
    assert garbage.get('extracted_name') in (None, '')

    mrz_text = (
        "P<GBRHARRINGTON<<JAMES<<<<<<<<<<<<<<<<<<<<<<<<<<<\n"
        "1234567890GBR8405128M3001013<<<<<<<<<<<<<<04"
    )
    mrz = app_mod.extract_document_text_and_metadata(
        'passport.pdf',
        stk_b64.b64encode(('%PDF-1.4\n' + mrz_text).encode('utf-8')).decode('utf-8'),
    )
    assert mrz.get('extracted_name') == 'James Harrington'
    assert mrz.get('extracted_dob') == '12/05/1984'

    noisy_pak = (
        "PAKISTANI\n22 APR 2003\n36101-4253620-3\nKHANEWAL, PAK\n"
        ">< PAKHUSSAIN<<AYAZ<<<<<<<<<<<<<<<<<<<<<<<<<<<\n"
        "TF69162011PAK0304225M29120513610142536203<86\n"
    )
    noisy = app_mod.extract_document_text_and_metadata(
        'PHOTO-2026-08-15-16-53-38.jpg',
        stk_b64.b64encode(noisy_pak.encode('utf-8')).decode('utf-8'),
    )
    assert noisy.get('extracted_name') == 'Ayaz Hussain', noisy
    assert noisy.get('extracted_dob') == '22/04/2003', noisy
    assert noisy.get('doc_type') == 'Passport', noisy
    assert noisy.get('extracted_nationality') == 'Pakistani', noisy
    assert (noisy.get('extraction_quality') or {}).get('score', 0) >= 75

    naseem_text = (
        "NASEEM ;\nGiven Names\nPASSPORT ASIA\nPAKISTANI\n03 SEP 1978\n"
        "P<PAKNASEEMK<ASIA<<<<<<<<<<<<<<<<<<<<<<<<<<<\n"
        "DE98572118PAK7809039F30101493410124007210<38\n"
    )
    naseem = app_mod.extract_document_text_and_metadata(
        'dthjklns.pdf',
        stk_b64.b64encode(('%PDF-1.4\n' + naseem_text).encode('utf-8')).decode('utf-8'),
    )
    assert naseem.get('extracted_name') == 'Asia Naseem', naseem
    assert naseem.get('extracted_dob') == '03/09/1978', naseem
    assert naseem.get('extracted_passport_num') in ('DE9857211', 'DE98572118'), naseem

    # Confidence matching engine (mocked CH — no live API)
    assert app_mod.normalize_match_person_name('Ali, Qurban') == 'ali qurban'
    assert app_mod.score_intake_name_match('Qurban Ali', 'QURBAN ALI')[0] == 50
    assert app_mod.score_intake_name_match('Qurban Ali', 'Ali, Qurban')[0] == 50
    assert app_mod.score_intake_dob_match('01/04/2002', {'month': 4, 'year': 2002})[0] == 25
    assert app_mod.score_intake_dob_match('01/04/2002', {'day': 1, 'month': 4, 'year': 2002})[0] == 35
    assert app_mod.score_intake_dob_match('01/04/2002', {'month': 5, 'year': 2002})[0] == -50

    strong = app_mod.score_intake_company_candidate({
        'name': 'ACME LTD',
        'company_number': '12345678',
        'officer_name': 'Qurban Ali',
        'officer_dob': {'month': 4, 'year': 2002},
        'source': 'companies_house',
        'company_status': 'active',
    }, None, 'Qurban Ali', '01/04/2002')
    weak = app_mod.score_intake_company_candidate({
        'name': 'OTHER LTD',
        'company_number': '87654321',
        'officer_name': 'Qurban',
        'source': 'companies_house',
        'company_status': 'dissolved',
    }, None, 'Qurban Ali', '01/04/2002')
    assert strong['score'] >= 80, strong
    assert strong['score'] - weak['score'] >= 15, (strong['score'], weak['score'])

    with patch.object(app_mod, 'search_companies_house_officers', return_value=([
        {
            'name': 'ALPHA LTD', 'company_number': '11111111', 'officer_name': 'Qurban Ali',
            'officer_dob': {'month': 4, 'year': 2002}, 'source': 'companies_house', 'company_status': 'active',
        },
        {
            'name': 'BETA LTD', 'company_number': '22222222', 'officer_name': 'Qurban Ali',
            'officer_dob': {'month': 4, 'year': 2002}, 'source': 'companies_house', 'company_status': 'active',
        },
    ], None)):
        with patch.object(app_mod, 'enrich_candidate_company_status', side_effect=lambda c, cache: c):
            _co, st, _msg, ranked, meta = app_mod.resolve_intake_company_match(
                None, 'Qurban Ali', extracted_dob='01/04/2002', actor=None,
            )
            assert st == 'review_required', (st, meta)
            assert len(ranked) >= 2

    with patch.object(app_mod, 'search_companies_house_officers', return_value=([
        {
            'name': 'WINNER LTD', 'company_number': '33333333', 'officer_name': 'Qurban Ali',
            'officer_dob': {'day': 1, 'month': 4, 'year': 2002}, 'source': 'companies_house', 'company_status': 'active',
        },
        {
            'name': 'WEAK LTD', 'company_number': '44444444', 'officer_name': 'Someone Else',
            'source': 'companies_house', 'company_status': 'dissolved',
        },
    ], None)):
        with patch.object(app_mod, 'enrich_candidate_company_status', side_effect=lambda c, cache: c):
            with patch.object(app_mod, 'auto_import_companies_house_from_intake', return_value={
                'id': 999, 'name': 'WINNER LTD', 'company_number': '33333333',
            }):
                co, st, _msg, ranked, meta = app_mod.resolve_intake_company_match(
                    None, 'Qurban Ali', extracted_dob='01/04/2002', actor=None,
                )
                assert st == 'matched', (st, meta)
                assert co and co.get('name') == 'WINNER LTD'
                assert meta.get('confidence', 0) >= 80

    gibberish = app_mod._valid_person_name('Sskkkksess Naseemk Asia')
    assert gibberish is None

    anon_pdf = b"%PDF-1.4 Bank Statement Tide Account Balance only" + b" % " + b"X" * 900
    st, hd, anon_intake = make_request('/api/admin/documents/intake/process', method='POST', cookie=f"session_token={task1_admin_tok}", body={
        'files': [
            {'file_name': 'PHOTO-2026-08-15-16-53-38.jpg', 'file_content_base64': stk_b64.b64encode(anon_pdf).decode('utf-8')},
        ]
    })
    assert st == "200 OK", anon_intake
    assert anon_intake['status'] == 'success'
    assert len(anon_intake['filed_documents']) == 1
    assert anon_intake['client']['id']
    assert 'Unassigned' in (anon_intake['client']['full_name'] or '')

    # Security check: Client role denied access
    st, hd, err_client_intake = make_request('/api/admin/documents/intake/process', method='POST', cookie=f"session_token={client_a_tok}", body={
        'files': [{'file_name': 'Test.pdf', 'file_content_base64': stk_b64.b64encode(stk_bank_pdf).decode('utf-8')}]
    })
    assert st == "403 Forbidden"
    print("✓ AUTOMATION TASK: Smart Document Intake & Auto-Filing Workflow verified 100%")

    # ==================================================
    # AUTOMATION TASK: STAFF DASHBOARD & INCENTIVES TESTS
    # ==================================================
    st, hd, staff_dash = make_request('/api/staff/my-dashboard', method='GET', cookie=f"session_token={task1_admin_tok}")
    assert st == "200 OK", staff_dash
    assert staff_dash['status'] == 'success'
    assert 'metrics' in staff_dash
    assert staff_dash['metrics']['grade'] in ('A+', 'A', 'B', 'C', 'D')
    assert 'my_tasks' in staff_dash

    # Client role blocked from staff dashboard
    st, hd, client_dash_err = make_request('/api/staff/my-dashboard', method='GET', cookie=f"session_token={client_a_tok}")
    assert st == "403 Forbidden"

    # Team Incentives Report API Test
    st, hd, team_rep = make_request('/api/admin/tasks/incentives-report', method='GET', cookie=f"session_token={task1_admin_tok}")
    assert st == "200 OK", team_rep
    assert team_rep['status'] == 'success'
    assert 'leaderboard' in team_rep
    assert len(team_rep['leaderboard']) > 0
    assert 'grade' in team_rep['leaderboard'][0]

    # Task Audit Logs API Test
    task_row = query_db("SELECT id FROM tasks ORDER BY id ASC LIMIT 1;", one=True)
    task_id = task_row['id'] if task_row else 1
    st, hd, task_logs = make_request(f'/api/admin/tasks/{task_id}/audit-logs', method='GET', cookie=f"session_token={task1_admin_tok}")
    assert st == "200 OK", task_logs
    assert task_logs['status'] == 'success'
    assert 'audit_logs' in task_logs

    print("✓ AUTOMATION TASK: Team Audit Logs, Incentives & Staff Dashboard verified 100%")

    print("\n==================================================")
    print("ALL HYPETEX WSGI & AUDIT FIX TESTS PASSED! (100%)")
    print("==================================================")

if __name__ == '__main__':
    run_tests()
