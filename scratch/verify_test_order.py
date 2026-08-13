import sqlite3
import json

conn = sqlite3.connect('/var/www/brixen-crm/hypetex.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=== BRIXEN CRM CONTROLLED TEST SYNCHRONIZATION VERIFICATION ===")

# 1. Inspect Clients
clients = cursor.execute("SELECT * FROM users WHERE role = 'CLIENT' ORDER BY id DESC;").fetchall()
print(f"Client Accounts Created: {len(clients)}")
for c in clients:
    print(f"  - Client #{c['id']}: WP_ID={c['wordpress_user_id']}, Email={c['email']}, Name={c['full_name']}, Status={c['status']}")

# 2. Inspect Orders
orders = cursor.execute("SELECT * FROM orders ORDER BY id DESC;").fetchall()
print(f"Orders Created: {len(orders)}")
for o in orders:
    print(f"  - Order #{o['id']}: OrderNum={o['order_number']}, Linked_UserID={o['user_id']}, Service={o['service_name']}, Total=£{o['total']:.2f}, Status={o['status']}")

# 3. Inspect Invoices
invoices = cursor.execute("SELECT * FROM invoices ORDER BY id DESC;").fetchall()
print(f"Invoices Created: {len(invoices)}")
for i in invoices:
    print(f"  - Invoice #{i['id']}: Num={i['invoice_number']}, OrderID={i['order_id']}, Total=£{i['total']:.2f}, Status={i['status']}")

# 4. Inspect Webhook Logs
events = cursor.execute("SELECT * FROM webhook_events WHERE event_type != 'test.ping' ORDER BY id DESC LIMIT 10;").fetchall()
print(f"Webhook Events Logged: {len(events)}")
for e in events:
    print(f"  - Event #{e['id']}: ID={e['event_id']}, Type={e['event_type']}, Status={e['status']}, Time={e['created_at']}")

conn.close()
