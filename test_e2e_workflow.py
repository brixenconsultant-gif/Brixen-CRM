import os
import sys
import json
import io
import hmac
import hashlib
from app import application
from db import query_db, execute_db

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
        res_data = json.loads(result_bytes.decode('utf-8'))
    elif 'text/' in content_type or 'html' in content_type:
        res_data = result_bytes.decode('utf-8')
    else:
        res_data = result_bytes

    return response_status[0], dict(response_headers), res_data

def run_e2e_test():
    print("==================================================")
    print("STARTING END-TO-END BUSINESS WORKFLOW INTEGRATION TEST")
    print("==================================================")

    # Test Customer Data
    wp_user_id = "wp_e2e_1001"
    customer_email = "e2e.customer@brixenconsultants.com"
    customer_name = "Eleanor Vance"
    customer_phone = "+44 7911 123456"
    customer_country = "United Kingdom"

    # ----------------------------------------------------
    # STEP 1 & 2: Simulate WordPress Registration & Event Generation
    # ----------------------------------------------------
    print("\n[Step 1 & 2] Simulating WordPress user registration event generation...")
    user_created_payload = {
        'event_id': 'evt_e2e_user_reg_001',
        'event_type': 'user.created',
        'data': {
            'wordpress_user_id': wp_user_id,
            'email': customer_email,
            'full_name': customer_name,
            'phone': customer_phone,
            'country': customer_country
        }
    }
    payload_bytes = json.dumps(user_created_payload).encode('utf-8')
    hmac_signature = hmac.new(webhook_secret_bytes(), payload_bytes, hashlib.sha256).hexdigest()
    print(f"  - Event ID: {user_created_payload['event_id']}")
    print(f"  - User: {customer_name} ({customer_email})")
    print(f"  - Generated HMAC-SHA256 Signature: {hmac_signature[:12]}...")

    # ----------------------------------------------------
    # STEP 3: Confirm Secure Webhook Reaches CRM
    # ----------------------------------------------------
    print("\n[Step 3] Dispatching secure webhook payload to CRM endpoint `/api/v1/wordpress/webhook`...")
    status, headers, response = make_request(
        '/api/v1/wordpress/webhook',
        method='POST',
        body=user_created_payload,
        headers={'X-Brixen-Signature': hmac_signature}
    )
    print(f"  - HTTP Status: {status}")
    print(f"  - CRM Response: {response}")
    assert status == "200 OK", f"Error: Webhook request failed with status {status}"
    assert response['status'] == 'success', f"Error: Expected success status, got {response.get('status')}"
    created_client_id = response['client_id']
    print(f"  ✓ Secure Webhook received and processed successfully.")

    # ----------------------------------------------------
    # STEP 4 & 5: Confirm Client Automatic Creation & Property Mapping
    # ----------------------------------------------------
    print("\n[Step 4 & 5] Verifying CRM database for auto-created client record...")
    client_db = query_db("SELECT * FROM users WHERE id = ?;", (created_client_id,), one=True)
    assert client_db is not None, "Error: Client record not found in database."
    assert client_db['wordpress_user_id'] == wp_user_id, f"Error: WP User ID mismatch ({client_db['wordpress_user_id']} vs {wp_user_id})"
    assert client_db['email'] == customer_email, f"Error: Email mismatch ({client_db['email']} vs {customer_email})"
    assert client_db['full_name'] == customer_name, f"Error: Name mismatch ({client_db['full_name']} vs {customer_name})"
    assert client_db['phone'] == customer_phone, f"Error: Phone mismatch ({client_db['phone']} vs {customer_phone})"
    assert client_db['role'] == 'CLIENT', f"Error: Role mismatch ({client_db['role']})"
    assert client_db['status'] == 'Active', f"Error: Status mismatch ({client_db['status']})"

    print(f"  ✓ CRM Client Record Verified:")
    print(f"    - Client ID: {client_db['id']}")
    print(f"    - WP User ID: {client_db['wordpress_user_id']}")
    print(f"    - Full Name: {client_db['full_name']}")
    print(f"    - Email: {client_db['email']}")
    print(f"    - Role: {client_db['role']} | Status: {client_db['status']}")

    # ----------------------------------------------------
    # STEP 6 & 7: Simulate WooCommerce Order & Webhook Sync
    # ----------------------------------------------------
    order_number = "#WC-E2E-2001"
    service_name = "UK Corporate Formation & Registered Office"
    order_price = 150.00
    order_total = 180.00

    print(f"\n[Step 6 & 7] Simulating WooCommerce checkout order event ({order_number})...")
    order_created_payload = {
        'event_id': 'evt_e2e_order_chk_002',
        'event_type': 'order.created',
        'data': {
            'order_number': order_number,
            'wordpress_user_id': wp_user_id,
            'email': customer_email,
            'service_name': service_name,
            'price': order_price,
            'total': order_total,
            'status': 'Processing'
        }
    }
    ord_payload_bytes = json.dumps(order_created_payload).encode('utf-8')
    ord_hmac_sig = hmac.new(webhook_secret_bytes(), ord_payload_bytes, hashlib.sha256).hexdigest()

    status, headers, ord_response = make_request(
        '/api/v1/wordpress/webhook',
        method='POST',
        body=order_created_payload,
        headers={'X-Brixen-Signature': ord_hmac_sig}
    )
    print(f"  - HTTP Status: {status}")
    print(f"  - CRM Response: {ord_response}")
    assert status == "200 OK", f"Error: Order webhook failed with status {status}"
    assert ord_response['status'] == 'success', f"Error: Expected success status, got {ord_response.get('status')}"
    created_order_id = ord_response['order_id']
    print(f"  ✓ WooCommerce Order created in CRM (ID: {created_order_id}).")

    # ----------------------------------------------------
    # STEP 8: Confirm Order Linkage to Correct Client
    # ----------------------------------------------------
    print("\n[Step 8] Verifying order-client relational linkage...")
    order_db = query_db("SELECT * FROM orders WHERE id = ?;", (created_order_id,), one=True)
    assert order_db is not None, "Error: Order record not found in database."
    assert order_db['user_id'] == created_client_id, f"Error: Order user_id linkage mismatch ({order_db['user_id']} vs {created_client_id})"
    assert order_db['order_number'] == order_number, f"Error: Order number mismatch."
    assert order_db['total'] == order_total, f"Error: Order total mismatch."
    print(f"  ✓ Order Linked Correctly:")
    print(f"    - Order ID: {order_db['id']}")
    print(f"    - Order Number: {order_db['order_number']}")
    print(f"    - Linked Client ID: {order_db['user_id']} (Matches {customer_name})")
    print(f"    - Service: {order_db['service_name']} | Total: £{order_db['total']:.2f}")

    # ----------------------------------------------------
    # STEP 9: Confirm No Duplicate Client Creation
    # ----------------------------------------------------
    print("\n[Step 9] Verifying zero duplicate client accounts were created...")
    matching_clients = query_db("SELECT * FROM users WHERE email = ? OR wordpress_user_id = ?;", (customer_email, wp_user_id))
    assert len(matching_clients) == 1, f"Error: Duplicate client records found! Count: {len(matching_clients)}"
    print(f"  ✓ Exactly 1 client account exists for {customer_email} (Zero duplicates).")

    # ----------------------------------------------------
    # STEP 10: Confirm Staff CRM API Access to Customer Profile & Orders
    # ----------------------------------------------------
    print("\n[Step 10] Testing Staff CRM API access to locate and open customer profile & order history...")
    # Admin Auth Login
    status, headers, admin_login = make_request(
        '/api/auth/login',
        method='POST',
        body={'email': 'admin@brixenconsultant.co.uk', 'password': 'AdminPass123!'}
    )
    assert status == "200 OK", "Error: Admin login failed."
    assert 'token' not in admin_login
    admin_token = extract_session_token(headers)
    assert admin_token, "Error: Login did not return a session cookie."

    # Fetch Customer Detail Profile via Staff API
    status, headers, staff_client_res = make_request(
        f"/api/admin/clients/{created_client_id}/full",
        cookie=f"session_token={admin_token}"
    )
    assert status == "200 OK", f"Error: Staff API returned {status}"
    assert staff_client_res['status'] == 'success'

    client_profile = staff_client_res['client']
    client_orders = staff_client_res['orders']

    assert client_profile['id'] == created_client_id
    assert client_profile['email'] == customer_email
    assert len(client_orders) == 1
    assert client_orders[0]['order_number'] == order_number

    print(f"  ✓ Staff CRM API successfully opened Customer File #{created_client_id}:")
    print(f"    - Client Name: {client_profile['full_name']}")
    print(f"    - Email: {client_profile['email']}")
    print(f"    - Associated Orders Count: {len(client_orders)}")
    print(f"    - Latest Order: {client_orders[0]['order_number']} ({client_orders[0]['service_name']} - £{client_orders[0]['total']:.2f})")

    print("\n==================================================")
    print("ALL 10 END-TO-END BUSINESS WORKFLOW STEPS PASSED 100%!")
    print("==================================================")

if __name__ == '__main__':
    run_e2e_test()
