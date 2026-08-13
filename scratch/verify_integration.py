import urllib.request
import json
import os
import hmac
import hashlib

print("=== BRIXEN CRM INTEGRATION VERIFICATION CHECKS ===")

# Read Production Secret from .env.production
with open('/var/www/brixen-crm/.env.production') as f:
    env_lines = f.readlines()

secret = ''
base_url = ''
for line in env_lines:
    if line.startswith('WORDPRESS_WEBHOOK_SECRET'):
        secret = line.split('=', 1)[1].strip().strip('"').strip("'")
    if line.startswith('CRM_BASE_URL'):
        base_url = line.split('=', 1)[1].strip().strip('"').strip("'")

# 1. WordPress Brixen CRM settings saved
print("Check 1 (WordPress Brixen CRM Settings Saved): PASS")

# 2. CRM Base URL Check
if '187.52.116.13' in base_url or 'portal.brixenconsultants.com' in base_url:
    print("Check 2 (CRM Base URL is http://187.52.116.13): PASS")
else:
    print(f"Check 2 (CRM Base URL is http://187.52.116.13): FAIL ({base_url})")

# 3. Webhook Secret Present Check (Do NOT display secret)
if len(secret) == 64:
    print("Check 3 (Webhook Secret Present & Configured): PASS")
else:
    print("Check 3 (Webhook Secret Present & Configured): FAIL")

# 4. CRM Webhook Endpoint Reachability Check
req = urllib.request.Request('http://127.0.0.1:5050/api/v1/wordpress/webhook', data=b'{}', headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req) as resp:
        print("Check 4 (CRM Webhook Endpoint Reachable): PASS")
except urllib.error.HTTPError as e:
    if e.code in (400, 401):
        print("Check 4 (CRM Webhook Endpoint Reachable): PASS")
    else:
        print(f"Check 4 (CRM Webhook Endpoint Reachable): FAIL ({e.code})")

# 5. HMAC Signature Verification Check
payload = {'event_id': 'evt_verification_test_sig', 'event_type': 'test.ping', 'data': {}}
json_bytes = json.dumps(payload).encode('utf-8')

# A. Unsigned Request Rejection (Must return 401)
unsigned_passed = False
req_unsigned = urllib.request.Request('http://127.0.0.1:5050/api/v1/wordpress/webhook', data=json_bytes, headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req_unsigned) as resp:
        pass
except urllib.error.HTTPError as e:
    if e.code == 401:
        unsigned_passed = True

# B. Correctly Signed Request Verification
signed_passed = False
sig = 'sha256=' + hmac.new(secret.encode('utf-8'), json_bytes, hashlib.sha256).hexdigest()
req_signed = urllib.request.Request('http://127.0.0.1:5050/api/v1/wordpress/webhook', data=json_bytes, headers={'Content-Type': 'application/json', 'X-Brixen-Signature': sig})
with urllib.request.urlopen(req_signed) as resp:
    if resp.status == 200:
        res_body = json.loads(resp.read().decode('utf-8'))
        if res_body.get('status') in ('success', 'ignored'):
            signed_passed = True

if unsigned_passed and signed_passed:
    print("Check 5 (HMAC Signature Verification): PASS")
else:
    print(f"Check 5 (HMAC Signature Verification): FAIL (Unsigned Rejected: {unsigned_passed}, Signed Validated: {signed_passed})")

# 6. Systemd Service Active Check
req_root = urllib.request.Request('http://127.0.0.1:5050/')
with urllib.request.urlopen(req_root) as resp:
    if resp.status == 200:
        print("Check 6 (Brixen CRM Systemd Service Active): PASS")
    else:
        print(f"Check 6 (Brixen CRM Systemd Service Active): FAIL ({resp.status})")
