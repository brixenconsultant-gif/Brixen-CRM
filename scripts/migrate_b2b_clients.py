import sqlite3
import os
import sys

def migrate():
    db_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'hypetex.db')
    if not os.path.exists(db_file):
        return

    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from db import hash_password

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Clear stale b2b_id flags on other accounts
    cursor.execute("UPDATE users SET is_b2b = 0, client_type = 'Normal', b2b_id = NULL WHERE LOWER(full_name) NOT LIKE '%tanish%' AND LOWER(full_name) NOT LIKE '%ibad%'")

    # Ensure Tanish exists
    cursor.execute("SELECT id FROM users WHERE LOWER(full_name) LIKE '%tanish%' OR LOWER(email) LIKE '%tanish%'")
    tanish = cursor.fetchone()
    if tanish:
        cursor.execute("UPDATE users SET is_b2b = 1, client_type = 'B2B', b2b_id = 'B2B-000001', role = 'CLIENT' WHERE id = ?", (tanish[0],))
    else:
        cursor.execute("""
            INSERT INTO users (full_name, email, password_hash, role, status, is_b2b, client_type, b2b_id)
            VALUES ('Tanish', 'tanish@brixenconsultants.com', ?, 'CLIENT', 'Active', 1, 'B2B', 'B2B-000001')
        """, (hash_password('TanishPass123!'),))

    # Ensure Ibad Ahmed exists
    cursor.execute("SELECT id FROM users WHERE LOWER(full_name) LIKE '%ibad%' OR LOWER(email) LIKE '%ibad%'")
    ibad = cursor.fetchone()
    if ibad:
        cursor.execute("UPDATE users SET is_b2b = 1, client_type = 'B2B', b2b_id = 'B2B-000002', role = 'CLIENT' WHERE id = ?", (ibad[0],))
    else:
        cursor.execute("""
            INSERT INTO users (full_name, email, password_hash, role, status, is_b2b, client_type, b2b_id)
            VALUES ('Ibad Ahmed', 'ibad.ahmed@brixenconsultants.com', ?, 'CLIENT', 'Active', 1, 'B2B', 'B2B-000002')
        """, (hash_password('IbadPass123!'),))

    conn.commit()
    conn.close()

if __name__ == '__main__':
    migrate()
