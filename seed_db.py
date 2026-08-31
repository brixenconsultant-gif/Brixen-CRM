import os
import random
import datetime
from db import init_db, execute_db, query_db, hash_password, unusable_password_hash

def seed():
    db_file = os.path.join(os.path.dirname(__file__), 'hypetex.db')
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except Exception as e:
            pass
    init_db()
    print("Seeding database for Brixen Consultant...")
    
    # Clear existing data for clean re-seed
    tables = ['webhook_events', 'role_permissions', 'permissions', 'roles', 'activity_logs', 'notifications',
              'support_messages', 'support_tickets', 'registered_agents', 'proxies', 'documents', 'addresses',
              'invoices', 'order_timeline', 'order_line_items', 'tasks', 'company_owners', 'orders', 'services', 'company_directors',
              'companies', 'user_sessions', 'users', 'settings']
    for t in tables:
        execute_db(f"DELETE FROM {t};")
    execute_db("DELETE FROM sqlite_sequence;")

    # 1. System Settings for Brixen Consultant
    settings = [
        ('company_name', 'Brixen Consultants'),
        ('logo_url', '/static/img/logo.svg'),
        ('primary_color', '#003971'),
        ('support_email', 'contact@brixenconsultants.com'),
        ('support_phone', '447360515317'),
        ('currency', '£'),
        ('vat_rate', '0.20'),
        ('order_prefix', '#GB'),
        ('invoice_prefix', 'INV-2026-'),
        ('wordpress_webhook_secret', 'CHANGE_ME_IN_LOCAL_ENV'),
        ('portal_url', 'https://portal.brixenconsultants.com')
    ]
    for k, v in settings:
        execute_db("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);", (k, v))

    # 1b. RBAC Roles & Permissions
    roles_list = [
        ('SUPER_ADMIN', 'Full unrestricted system & technical administration access'),
        ('ADMIN', 'Manage clients, orders, staff, documents, and financial records'),
        ('MANAGER', 'Oversee team assignments, assigned clients, and order workflows'),
        ('STAFF', 'Access assigned clients and orders to process documentation'),
        ('CLIENT', 'Portal user accessing owned companies, orders, and documents')
    ]
    role_map = {}
    for rname, rdesc in roles_list:
        rid = execute_db("INSERT INTO roles (name, description) VALUES (?, ?);", (rname, rdesc))
        role_map[rname] = rid

    permissions_list = [
        ('clients.view', 'View client records'),
        ('clients.create', 'Create new client profiles'),
        ('clients.edit', 'Modify client profiles'),
        ('clients.delete', 'Remove client profiles'),
        ('orders.view', 'View orders'),
        ('orders.create', 'Create orders'),
        ('orders.edit', 'Update order statuses and progress'),
        ('orders.assign', 'Assign staff to orders'),
        ('documents.view', 'View documents'),
        ('documents.upload', 'Upload new documents'),
        ('documents.send', 'Send document notifications to client'),
        ('documents.delete', 'Delete documents'),
        ('invoices.view', 'View invoices'),
        ('invoices.create', 'Create invoices'),
        ('accountancy.manage', 'File and track company yearly accounts'),
        ('notifications.send', 'Send notifications'),
        ('staff.manage', 'Manage internal staff accounts'),
        ('settings.manage', 'Modify system & integration settings'),
        ('tasks.view', 'View staff tasks'),
        ('tasks.create', 'Create staff tasks'),
        ('tasks.edit', 'Edit staff tasks'),
        ('tasks.assign', 'Assign staff to tasks'),
        ('tasks.complete', 'Mark staff tasks complete')
    ]
    perm_map = {}
    for pname, pdesc in permissions_list:
        pid = execute_db("INSERT INTO permissions (name, description) VALUES (?, ?);", (pname, pdesc))
        perm_map[pname] = pid

    # Link permissions to roles
    for pname in perm_map:
        execute_db("INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?);", (role_map['SUPER_ADMIN'], perm_map[pname]))
        execute_db("INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?);", (role_map['ADMIN'], perm_map[pname]))
    
    # Manager & Staff restricted permissions
    for pname in ['clients.view', 'clients.edit', 'clients.create', 'orders.view', 'orders.edit', 'documents.view', 'documents.upload', 'documents.send', 'invoices.view', 'accountancy.manage']:
        execute_db("INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?);", (role_map['MANAGER'], perm_map[pname]))
        execute_db("INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?);", (role_map['STAFF'], perm_map[pname]))

    # Staff Task Management: Manager gets full task workflow; Staff can view/edit/complete assigned work
    for pname in ['tasks.view', 'tasks.create', 'tasks.edit', 'tasks.assign', 'tasks.complete']:
        execute_db("INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?);", (role_map['MANAGER'], perm_map[pname]))
    for pname in ['tasks.view', 'tasks.edit', 'tasks.complete']:
        execute_db("INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?);", (role_map['STAFF'], perm_map[pname]))

    # 2. Local staff only — no dummy customers or orders
    admin_id = execute_db("""
        INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, country, address, avatar_url, role, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, ('wp_admin_1', 'admin@brixenconsultant.co.uk', hash_password('AdminPass123!'), 'Alexander Vance', '+44 20 7123 4567', 'United Kingdom', '100 Bishopsgate, London EC2N 4AG', '', 'ADMIN', 'Active'))

    staff_id = execute_db("""
        INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, country, address, role, status, department)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, ('wp_staff_1', 'eleanor.finch@brixenconsultant.co.uk', hash_password('StaffPass123!'), 'Eleanor Finch', '+44 20 7946 0101', 'United Kingdom', '20 Fenchurch St, London EC3M 3BY', 'STAFF', 'Active', 'Documents'))
    staff_ids = [staff_id]

    execute_db("""
        INSERT INTO users (wordpress_user_id, email, password_hash, full_name, role, status)
        VALUES (?, ?, ?, ?, 'CLIENT', 'Pending');
    """, ('guest_unlinked', 'guest.unlinked@brixenconsultants.com', unusable_password_hash(), 'Unlinked Guest Customer'))

    services_list = [
        ('Registered Office Address', 'Official Companies House registered address in Central London.', 'Address Services', 20.00),
        ('Company Formation Package', 'Complete UK Limited Company formation including digital documents.', 'Incorporation', 45.00),
        ('Mail Forwarding Service', 'Prime London business address with mail scanning and forwarding.', 'Address Services', 99.00),
        ('Annual Compliance Filing', 'Confirmation Statement and Companies House annual filing.', 'Compliance', 149.00),
        ('VAT Registration Service', 'UK HMRC VAT registration and advisor setup.', 'Tax & Financials', 199.00)
    ]
    service_ids = []
    for name, desc, cat, price in services_list:
        sid = execute_db("""
            INSERT INTO services (name, description, category, price, duration, status, featured, vat_rate, renewal_period)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (name, desc, cat, price, '12 Months', 'Active', 1, 0.20, 'Annual'))
        service_ids.append((sid, name, price))

    print("Seeded a clean local CRM (staff accounts and catalog only; no dummy orders).")
    return {'admin_id': admin_id, 'staff_ids': staff_ids, 'service_ids': service_ids}


def seed_demo():
    """Test-only dummy data. Do not use for the local working portal."""
    ctx = seed()
    staff_ids = list(ctx['staff_ids'])
    service_ids = ctx['service_ids']

    extra_staff = [
        ('Marcus Sterling', 'marcus.sterling@brixenconsultant.co.uk', '+44 20 7946 0102', 'wp_staff_2', 'Orders'),
        ('Sophia Montgomery', 'sophia.m@brixenconsultant.co.uk', '+44 20 7946 0103', 'wp_staff_3', 'New Signups'),
        ('Julian Thorne', 'julian.t@brixenconsultant.co.uk', '+44 20 7946 0104', 'wp_staff_4', 'Support'),
        ('Clara Oswald', 'clara.o@brixenconsultant.co.uk', '+44 20 7946 0105', 'wp_staff_5', 'Compliance')
    ]
    for name, email, phone, wpid, department in extra_staff:
        sid = execute_db("""
            INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, country, address, role, status, department)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (wpid, email, hash_password('StaffPass123!'), name, phone, 'United Kingdom', '20 Fenchurch St, London EC3M 3BY', 'STAFF', 'Active', department))
        staff_ids.append(sid)

    client_data = [
        ('James Harrington', 'client1@acmecorp.co.uk', '+44 7700 900001', 'Acme Holdings Ltd', '15 Regent Street, London W1B 4LR', 'wp_user_101'),
        ('Victoria Smith', 'v.smith@vantagecyber.co.uk', '+44 7700 900002', 'Vantage Cyber Tech Ltd', '45 Hanover Square, Edinburgh EH2 2PJ', 'wp_user_102'),
        ('Oliver Cromwell', 'oliver@quantumhorizon.com', '+44 7700 900003', 'Quantum Horizon Holdings Ltd', '88 Deansgate, Manchester M3 2ER', 'wp_user_103'),
        ('Sophie Ellis', 'sophie@apexlogistics.co.uk', '+44 7700 900004', 'Apex Global Logistics Ltd', '12 St Mary Axe, London EC3A 8AA', 'wp_user_104'),
        ('David Miller', 'david@nexusbiotech.co.uk', '+44 7700 900005', 'Nexus Biotech Ventures Ltd', '33 Cambridge Science Park, CB4 0FZ', 'wp_user_105')
    ]
    client_ids = []
    for name, email, phone, cname, addr, wpid in client_data:
        cid = execute_db("""
            INSERT INTO users (wordpress_user_id, email, password_hash, full_name, phone, country, address, role, status, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
        """, (wpid, email, hash_password('ClientPass123!'), name, phone, 'United Kingdom', addr, 'CLIENT', 'Active'))
        client_ids.append((cid, name, cname))
    primary_client_id = client_ids[0][0]

    company_records = []
    company_seed_data = [
        ("Acme Corporate Holdings Ltd", "13810022"),
        ("Vantage Cyber Tech Ltd", "13820033"),
        ("Quantum Horizon Holdings Ltd", "13830044"),
        ("Apex Global Logistics Ltd", "13840055"),
        ("Nexus Biotech Ventures Ltd", "13850066"),
        ("GUL MART LTD", "16967197"),
        ("TRAVEL TO UMRAH LTD", "16821965"),
        ("DENTY LIMITED", "16991988"),
        ("NEXY RETAIL LIMITED", "17002492"),
        ("AUD TRADERS LIMITED", "17001469"),
        ("CROWN VINTAGE LIMITED", "17096366"),
        ("MAQSOOD MI TRADING LTD", "17095941"),
        ("RIZWAN MART SHORT LTD", "17105420"),
        ("EURO TRADE 5D LTD", "17125999"),
        ("BRIXEN CONSULTANTS LIMITED", "14892011"),
        ("ALPHA MERCHANTS UK LTD", "15012933"),
        ("SKYLINE VENTURES UK LTD", "15124088"),
        ("NOVA CAPITAL HOLDINGS LTD", "15239100"),
        ("ZENITH DIGITAL MEDIA LTD", "15340199"),
        ("OCEANIC PHARMA LIMITED", "15451233"),
        ("KINETIC ENERGY SOLUTIONS LTD", "15562344"),
        ("PULSE COMMERCE UK LTD", "15673455"),
        ("VERTEX INFRASTRUCTURE LTD", "15784566"),
        ("SOLARIS LOGISTICS UK LTD", "15895677"),
        ("HERITAGE REAL ESTATE LTD", "16006788"),
        ("SILVERLINE GLOBAL TRADING LTD", "16117899"),
        ("PIONEER TELECOM UK LTD", "16229010"),
        ("BEACON FINANCIAL SUITE LTD", "16340121"),
        ("CRESTVIEW MANAGEMENT LTD", "16451232"),
        ("INTEGRITY COMPLIANCE SERVICES LTD", "16562343"),
        ("ORION CYBER SECURITY LTD", "16673454"),
        ("SUMMIT HEALTHCARE UK LTD", "16784565"),
        ("TRIDENT CAPITAL ADVISORS LTD", "16895676")
    ]
    for i, (cname, cnum) in enumerate(company_seed_data):
        cid, client_name, _ = client_ids[i % len(client_ids)]
        inc_date = f"202{min(i % 5 + 1, 5)}-0{(i % 9) + 1}-15"
        comp_id = execute_db("""
            INSERT INTO companies (user_id, name, company_number, status, inc_date, director, reg_office, package, account_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (cid, cname, cnum, 'Active', inc_date, client_name, '71-75 Shelton Street, Covent Garden, London, WC2H 9JQ', 'Premium Corporate Suite', 'Good Standing'))
        company_records.append((comp_id, cid, cname))
        execute_db("""
            INSERT INTO company_directors (company_id, name, role, nationality, appointed_date)
            VALUES (?, ?, ?, ?, ?);
        """, (comp_id, client_name, 'Managing Director', 'British', inc_date))

    # 5. Orders - 115 Orders for Primary Client (James Harrington)!
    order_records = []
    month_names = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
    
    # 1st Featured Order: #GB103449JUL26
    oid1 = execute_db("""
        INSERT INTO orders (order_number, user_id, company_id, service_id, service_name, price, vat, total, status, progress_percent, delivery_label, assigned_staff_id, expected_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, ('#GB103449JUL26', primary_client_id, company_records[0][0], service_ids[0][0], 'Registered Office Address', 20.00, 4.00, 24.00, 'Processing', 50, 'Priority Processing', staff_ids[0], '2026-08-30', '2026-07-28 10:15:00'))
    
    order_records.append((oid1, '#GB103449JUL26', primary_client_id, 'Registered Office Address', 20.00, 24.00, 'Processing', '2026-07-28'))
    
    steps1 = [
        ("Order Placed", "Completed", "2026-07-28 10:15:00"),
        ("Payment Confirmed", "Completed", "2026-07-28 10:16:00"),
        ("Processing", "Current", "2026-07-28 10:30:00"),
        ("Documents Received", "Pending", None),
        ("Service Completed", "Pending", None)
    ]
    for t_title, t_status, t_date in steps1:
        execute_db("INSERT INTO order_timeline (order_id, title, status, step_date) VALUES (?, ?, ?, ?);", (oid1, t_title, t_status, t_date))
        
    execute_db("""
        INSERT INTO invoices (invoice_number, order_id, user_id, amount, tax, total, status, due_date, paid_at, payment_method, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, ('INV-2026-1001', oid1, primary_client_id, 20.00, 4.00, 24.00, 'Paid', '2026-08-28', '2026-07-28 10:16:00', 'Credit Card (Stripe)', '2026-07-28 10:15:00'))

    statuses_distribution = (['Completed'] * 97) + (['Processing'] * 4) + (['In Progress'] * 4) + (['Pending'] * 9)
    random.shuffle(statuses_distribution)
    
    current_running_spent = 24.00
    target_final_spent = 2608.00
    num_items = len(statuses_distribution)
    per_item_total = round((target_final_spent - 24.00) / num_items, 2)
    
    for idx, stat in enumerate(statuses_distribution):
        i = idx + 2
        if idx == num_items - 1:
            item_total = round(target_final_spent - current_running_spent, 2)
        else:
            item_total = per_item_total
            
        item_price = round(item_total / 1.20, 2)
        item_vat = round(item_total - item_price, 2)
        current_running_spent += item_total
        
        rand_num = 103450 + i
        rand_mon = month_names[i % 12]
        order_num = f"#GB{rand_num}{rand_mon}26"
        
        svc_id, svc_name, _ = service_ids[i % len(service_ids)]
        comp_id = company_records[i % len(company_records)][0]
        
        if stat == 'Completed':
            prog = 100
        elif stat in ('Processing', 'In Progress'):
            prog = random.choice([25, 50, 75])
        else:
            prog = 0
            
        order_date = f"2026-{(i%7)+1:02d}-{(i%25)+1:02d} 11:20:00"
        
        oid = execute_db("""
            INSERT INTO orders (order_number, user_id, company_id, service_id, service_name, price, vat, total, status, progress_percent, delivery_label, assigned_staff_id, expected_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (order_num, primary_client_id, comp_id, svc_id, svc_name, item_price, item_vat, item_total, stat, prog, 'Standard Mail Service', staff_ids[i%len(staff_ids)], '2026-09-01', order_date))
        
        steps = [
            ("Order Placed", "Completed", order_date),
            ("Payment Confirmed", "Completed", order_date),
            ("Processing", "Completed" if prog >= 50 else ("Current" if prog > 0 else "Pending"), order_date),
            ("Documents Received", "Completed" if prog >= 75 else "Pending", order_date),
            ("Service Completed", "Completed" if prog == 100 else "Pending", order_date)
        ]
        for t_title, t_status, t_date in steps:
            execute_db("INSERT INTO order_timeline (order_id, title, status, step_date) VALUES (?, ?, ?, ?);", (oid, t_title, t_status, t_date))

        inv_num = f"INV-2026-{1001+i}"
        inv_status = 'Paid' if stat == 'Completed' else 'Pending'
        execute_db("""
            INSERT INTO invoices (invoice_number, order_id, user_id, amount, tax, total, status, due_date, paid_at, payment_method, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (inv_num, oid, primary_client_id, item_price, item_vat, item_total, inv_status, '2026-08-30', order_date if inv_status == 'Paid' else None, 'Credit Card (Stripe)', order_date))

    # 6. Addresses for Primary Client
    execute_db("""
        INSERT INTO addresses (user_id, company_id, type, line1, line2, city, postal_code, country, start_date, expiry_date, status, service_name, price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (primary_client_id, company_records[0][0], 'Registered Office', '71-75 Shelton Street', 'Covent Garden', 'London', 'WC2H 9JQ', 'United Kingdom', '2026-01-15', '2027-01-14', 'Active', 'Registered Office Address', 20.00))

    execute_db("""
        INSERT INTO addresses (user_id, company_id, type, line1, line2, city, postal_code, country, start_date, expiry_date, status, service_name, price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (primary_client_id, company_records[0][0], 'Virtual Office', '100 Bishopsgate', 'Suite 402', 'London', 'EC2N 4AG', 'United Kingdom', '2026-02-01', '2027-01-31', 'Active', 'Virtual Office Service', 99.00))

    # 7. Documents for Primary Client
    doc_categories = ['Certificate of Incorporation', 'Proof of Address', 'ID Document', 'Company Documents']
    for i in range(12):
        cat = doc_categories[i % len(doc_categories)]
        doc_name = f"{cat.replace(' ', '_')}_GB1034{i+49}.pdf"
        execute_db("""
            INSERT INTO documents (user_id, company_id, order_id, name, category, file_path, file_type, file_size, status, uploaded_by, review_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (primary_client_id, company_records[0][0], oid1, doc_name, cat, f"/static/uploads/{doc_name}", 'PDF Document', f"{250 + i*40} KB", 'Approved' if i%3!=0 else 'Pending Review', 'Customer Upload', 'Verified by Brixen Consultant Compliance Officer.'))

    # 8. Proxies & Registered Agents
    execute_db("""
        INSERT INTO proxies (user_id, company_id, order_id, proxy_type, start_date, expiry_date, status, assigned_person)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """, (primary_client_id, company_records[0][0], oid1, 'Nominee Proxy Director', '2026-01-01', '2027-01-01', 'Active', 'Brixen Nominees Ltd'))

    execute_db("""
        INSERT INTO registered_agents (user_id, company_id, agent_name, start_date, renewal_date, status, package)
        VALUES (?, ?, ?, ?, ?, ?, ?);
    """, (primary_client_id, company_records[0][0], 'Brixen Nominees Ltd', '2026-01-01', '2027-01-01', 'Active', 'Full Corporate Agent'))

    # 9. Support Tickets
    tid = execute_db("""
        INSERT INTO support_tickets (ticket_number, user_id, order_id, company_id, subject, category, priority, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """, ("TICK-2026-8801", primary_client_id, oid1, company_records[0][0], "Question regarding Order #GB103449JUL26", "Orders", "High", "In Progress"))
    
    execute_db("""
        INSERT INTO support_messages (ticket_id, sender_id, sender_name, sender_role, message)
        VALUES (?, ?, ?, ?, ?);
    """, (tid, primary_client_id, 'James Harrington', 'CLIENT', "Hello Brixen Consultant Team, I am checking on the progress of order #GB103449JUL26 for Registered Office Address."))
    
    execute_db("""
        INSERT INTO support_messages (ticket_id, sender_id, sender_name, sender_role, message)
        VALUES (?, ?, ?, ?, ?);
    """, (tid, staff_ids[0], 'Eleanor Finch', 'STAFF', "Hello James, your order is currently in Processing (50%). All compliance documents have been verified."))

    # 10. Notifications
    execute_db("""
        INSERT INTO notifications (user_id, title, message, type, link)
        VALUES (?, ?, ?, ?, ?);
    """, (primary_client_id, 'Order Status Updated', 'Your order #GB103449JUL26 progress is now 50% (Processing).', 'order_update', '/orders'))

    execute_db("""
        INSERT INTO notifications (user_id, title, message, type, link)
        VALUES (?, ?, ?, ?, ?);
    """, (primary_client_id, 'Invoice Generated', 'Invoice INV-2026-1001 for £24.00 has been issued.', 'invoice_generated', '/invoices'))

    print("Seeded demo data for automated tests only.")

if __name__ == '__main__':
    seed()
