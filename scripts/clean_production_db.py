import sqlite3
import os
import shutil
import datetime

DB_PATH = '/var/www/brixen-crm/hypetex.db' if os.path.exists('/var/www/brixen-crm/hypetex.db') else 'hypetex.db'

def clean_database():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        return

    # 1. Create real-time backup snapshot
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = DB_PATH.replace('.db', f'-backup-realtime-{timestamp}.db')
    shutil.copy2(DB_PATH, backup_path)
    print(f"✓ Real-time backup snapshot saved to: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 2. Delete dummy test customers
    dummy_emails = [
        'client1@acmecorp.co.uk',
        'v.smith@vantagecyber.co.uk',
        'oliver@quantumhorizon.com',
        'sophie@apexlogistics.co.uk',
        'david@nexusbiotech.co.uk',
        'wp.test.client@brixen.co.uk',
        'precrm.owner@example.com',
        'portal.client@acmecorp.co.uk',
        'argon2id.login.7c@example.test',
        'legacy.sha256.7c@example.test',
        'legacy.wrong.7c@example.test',
        'sync.created.7c@example.test',
        'profile.change.7c@example.test',
        'john.normal.452a27@example.com'
    ]

    dummy_placeholders = ','.join(['?'] * len(dummy_emails))
    c.execute(f"DELETE FROM users WHERE email IN ({dummy_placeholders})", dummy_emails)
    deleted_users = c.rowcount
    print(f"✓ Removed {deleted_users} dummy test users.")

    # 3. Delete dummy seed companies
    dummy_company_numbers = [
        '13810022', '13820033', '13830044', '13840055', '13850066',
        '16967197', '16821965', '16991988', '17002492', '17001469',
        '17096366', '17095941', '17105420', '17125999', '14892011',
        '15012933', '15124088', '15239100', '15340199', '15451233'
    ]
    dummy_comp_placeholders = ','.join(['?'] * len(dummy_company_numbers))
    c.execute(f"DELETE FROM companies WHERE company_number IN ({dummy_comp_placeholders})", dummy_company_numbers)
    deleted_companies = c.rowcount
    print(f"✓ Removed {deleted_companies} dummy seed companies.")

    # 4. Delete dummy test orders (#GB1034...)
    c.execute("DELETE FROM orders WHERE order_number LIKE '#GB1034%'")
    deleted_orders = c.rowcount
    print(f"✓ Removed {deleted_orders} dummy test orders.")

    conn.commit()
    c.execute("VACUUM")
    conn.close()

    print("==================================================")
    print("✓ PRODUCTION DATABASE CLEANUP COMPLETED SUCCESSFULLY!")
    print("==================================================")

if __name__ == '__main__':
    clean_database()
