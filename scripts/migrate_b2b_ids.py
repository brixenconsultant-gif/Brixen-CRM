#!/usr/bin/env python3
"""
Idempotent Database Migration Script for Brixen CRM B2B ID Assignment.
Assigns unique, sequential B2B-000001 format identifiers to companies and B2B client records.
"""

import sys
import os
import sqlite3

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db, ensure_schema

def migrate_b2b_ids(verbose=True):
    ensure_schema()
    conn = get_db()
    cursor = conn.cursor()

    # Determine current max B2B ID number across companies and users
    max_num = 0
    
    # Check companies
    cursor.execute("SELECT b2b_id FROM companies WHERE b2b_id IS NOT NULL AND b2b_id LIKE 'B2B-%';")
    for row in cursor.fetchall():
        try:
            num = int(row[0].replace('B2B-', ''))
            if num > max_num:
                max_num = num
        except (ValueError, AttributeError):
            pass

    # Check users
    cursor.execute("SELECT b2b_id FROM users WHERE b2b_id IS NOT NULL AND b2b_id LIKE 'B2B-%';")
    for row in cursor.fetchall():
        try:
            num = int(row[0].replace('B2B-', ''))
            if num > max_num:
                max_num = num
        except (ValueError, AttributeError):
            pass

    current_counter = max_num
    companies_updated = 0
    users_updated = 0

    # Migrate companies
    cursor.execute("SELECT id, name FROM companies WHERE b2b_id IS NULL OR b2b_id = '' ORDER BY id ASC;")
    unassigned_companies = cursor.fetchall()
    for comp in unassigned_companies:
        current_counter += 1
        b2b_id = f"B2B-{current_counter:06d}"
        cursor.execute(
            "UPDATE companies SET b2b_id = ?, client_type = COALESCE(client_type, 'B2B') WHERE id = ?;",
            (b2b_id, comp['id'])
        )
        companies_updated += 1

    # Migrate B2B client users
    cursor.execute("SELECT id, full_name, email FROM users WHERE role = 'CLIENT' AND (b2b_id IS NULL OR b2b_id = '') ORDER BY id ASC;")
    unassigned_users = cursor.fetchall()
    for u in unassigned_users:
        # Check if user owns a company that already has a B2B ID
        cursor.execute("SELECT b2b_id FROM companies WHERE user_id = ? AND b2b_id IS NOT NULL LIMIT 1;", (u['id'],))
        comp_row = cursor.fetchone()
        if comp_row and comp_row['b2b_id']:
            user_b2b = comp_row['b2b_id']
        else:
            current_counter += 1
            user_b2b = f"B2B-{current_counter:06d}"
        cursor.execute(
            "UPDATE users SET b2b_id = ?, client_type = COALESCE(client_type, 'B2B') WHERE id = ?;",
            (user_b2b, u['id'])
        )
        users_updated += 1

    conn.commit()
    conn.close()

    if verbose:
        print(f"✓ B2B ID Migration Complete:")
        print(f"  - Companies updated with B2B ID: {companies_updated}")
        print(f"  - Users updated with B2B ID: {users_updated}")
        print(f"  - Latest assigned B2B ID: B2B-{current_counter:06d}")

if __name__ == '__main__':
    migrate_b2b_ids()
