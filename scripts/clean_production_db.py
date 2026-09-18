import sqlite3
import os
import shutil
import datetime
import sys

# Dummy-only cleanup. Never deletes genuine locked business records.
DB_PATH = '/var/www/brixen-crm/hypetex.db' if os.path.exists('/var/www/brixen-crm/hypetex.db') else 'hypetex.db'

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_protection import (
    KNOWN_DUMMY_COMPANY_NUMBERS,
    KNOWN_DUMMY_EMAILS,
    DUMMY_ORDER_NUMBER_PREFIXES,
)


def clean_database():
    if os.environ.get('CONFIRM_CLEAN_DUMMY') != '1':
        print('Refused: set CONFIRM_CLEAN_DUMMY=1 to remove known dummy seed rows only.')
        print('This script never deletes locked genuine orders, companies, or invoices.')
        return

    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        return

    # 1. Create real-time backup snapshot
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = DB_PATH.replace('.db', f'-backup-realtime-{timestamp}.db')
    shutil.copy2(DB_PATH, backup_path)
    print(f"✓ Real-time backup snapshot saved to: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    dummy_emails = sorted(KNOWN_DUMMY_EMAILS)
    dummy_placeholders = ','.join(['?'] * len(dummy_emails))
    c.execute(
        f"DELETE FROM users WHERE email IN ({dummy_placeholders}) AND COALESCE(role, '') = 'CLIENT'",
        dummy_emails,
    )
    deleted_users = c.rowcount
    print(f"✓ Removed {deleted_users} dummy test users.")

    dummy_company_numbers = sorted(KNOWN_DUMMY_COMPANY_NUMBERS)
    dummy_comp_placeholders = ','.join(['?'] * len(dummy_company_numbers))
    # Never touch locked genuine companies, even if a number somehow overlapped.
    c.execute(
        f"""
        DELETE FROM companies
        WHERE company_number IN ({dummy_comp_placeholders})
          AND COALESCE(data_locked, 0) = 0
        """,
        dummy_company_numbers,
    )
    deleted_companies = c.rowcount
    print(f"✓ Removed {deleted_companies} dummy seed companies.")

    # Dummy test orders only (#GB1034...) and never locked genuine rows.
    for prefix in DUMMY_ORDER_NUMBER_PREFIXES:
        c.execute(
            """
            DELETE FROM orders
            WHERE order_number LIKE ?
              AND COALESCE(data_locked, 0) = 0
              AND COALESCE(woocommerce_order_id, '') = ''
            """,
            (f'{prefix}%',),
        )
    deleted_orders = c.rowcount
    print(f"✓ Removed {deleted_orders} dummy test orders.")

    conn.commit()
    c.execute("VACUUM")
    conn.close()

    print("==================================================")
    print("✓ DUMMY-ONLY DATABASE CLEANUP COMPLETED")
    print("==================================================")

if __name__ == '__main__':
    clean_database()
