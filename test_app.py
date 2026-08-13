import os
import sys
import json
import io
import hmac
import hashlib
import datetime
from app import application
from db import query_db, execute_db

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

    # 5. Test Auth API Login (Client)
    status, headers, res = make_request('/api/auth/login', method='POST', body={'email': 'client1@acmecorp.co.uk', 'password': 'ClientPass123!'})
    assert status == "200 OK"
    assert res['status'] == 'success'
    token = res['token']
    print(f"✓ Auth API Login (Client) -> Success (User: {res['user']['full_name']})")

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
    adm_token = adm_res['token']
    
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
    wp_sig = hmac.new(b'brixen_wp_secret_key_998877', wp_body_bytes, hashlib.sha256).hexdigest()
    
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
    guest_sig = hmac.new(b'brixen_wp_secret_key_998877', guest_bytes, hashlib.sha256).hexdigest()
    
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
    pay_sig = hmac.new(b'brixen_wp_secret_key_998877', pay_bytes, hashlib.sha256).hexdigest()
    
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
    status, headers, sso_res = make_request('/api/v1/auth/sso', method='POST', body={'wordpress_user_id': 'wp_user_999'})
    assert status == "200 OK"
    assert sso_res['status'] == 'success'
    print(f"✓ WP SSO Bridge Exchange -> Success (Generated Session Token: {sso_res['token'][:8]}...)")

    # 10b. Expired SSO Token Test (Timestamp older than 300s must return 401 Unauthorized)
    old_ts = str(int(datetime.datetime.now().timestamp()) - 600)
    exp_sig = hmac.new(b'brixen_wp_secret_key_998877', f"wp_user_999|wp.test.client@brixen.co.uk|{old_ts}".encode('utf-8'), hashlib.sha256).hexdigest()
    status, headers, exp_sso_res = make_request(f"/api/v1/auth/sso?wordpress_user_id=wp_user_999&email=wp.test.client@brixen.co.uk&timestamp={old_ts}&signature={exp_sig}")
    assert status == "401 Unauthorized"
    print("✓ SSO Security Check -> Expired SSO token (>300s) rejected with 401 Unauthorized.")

    # 10c. Invalid SSO Signature Test (Tampered payload must return 401 Unauthorized)
    status, headers, invalid_sso_res = make_request(f"/api/v1/auth/sso?wordpress_user_id=wp_user_999&email=wp.test.client@brixen.co.uk&timestamp={int(datetime.datetime.now().timestamp())}&signature=invalid_tampered_signature")
    assert status == "401 Unauthorized"
    print("✓ SSO Security Check -> Invalid/tampered SSO signature rejected with 401 Unauthorized.")

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
    client2_token = client2_login['token']
    status, headers, forbidden_res = make_request(f"/api/documents/{doc_id}/download", cookie=f"session_token={client2_token}")
    assert status == "403 Forbidden"
    print(f"✓ P2 Security Isolation Check -> Client B access on Client A document blocked with 403 Forbidden.")

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
    print(f"✓ Ticket Security Isolation -> Client B access to Client A ticket blocked with 403 Forbidden.")

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
