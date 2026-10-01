import sqlite3
import db

def remove_email(email):
    try:
        conn = sqlite3.connect(db.DB_PATH)
        cursor = conn.cursor()
        
        # Checking if user exists in the users table
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        if user:
            print(f"Found user with ID: {user[0]}")
            # Delete from users
            cursor.execute("DELETE FROM users WHERE email = ?", (email,))
            print(f"Deleted {email} from users table.")
            
        else:
            print(f"User with email {email} not found in users table.")
            
        # Also check and delete from companies or company_owners if necessary
        cursor.execute("DELETE FROM company_owners WHERE form_email = ?", (email,))
        if cursor.rowcount > 0:
            print(f"Deleted {email} from company_owners table. ({cursor.rowcount} rows)")
            
        cursor.execute("DELETE FROM companies WHERE registered_email = ?", (email,))
        if cursor.rowcount > 0:
            print(f"Deleted {email} from companies table (registered_email). ({cursor.rowcount} rows)")

        # Commit all changes
        conn.commit()
        print("Database changes committed successfully.")
        
    except sqlite3.Error as e:
        print("SQLite error:", e)
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    target_email = "wapasge632@gmail.com"
    remove_email(target_email)
