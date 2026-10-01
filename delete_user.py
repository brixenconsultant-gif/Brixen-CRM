python3 -c "
import sqlite3, json, os

email = 'waqasge632@gmail.com'.strip().lower()

# 1. Database se delete
db_path = 'hypetex.db'

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('DELETE FROM users WHERE LOWER(email) = ?;', (email,))
    print(f'Database: Deleted {cur.rowcount} row(s) from users table.')
    conn.commit()
    conn.close()
else:
    print('hypetex.db nahi mila is directory me')

# 2. Ledger se email remove
ledger_paths = [
    'storage/permanent_purge_ledger.json',
    'backend/services/storage/permanent_purge_ledger.json',
    'backend/storage/permanent_purge_ledger.json',
    'permanent_purge_ledger.json'
]
for lp in ledger_paths:
    if os.path.exists(lp):
        with open(lp, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if 'emails' in data:
            before_len = len(data['emails'])
            data['emails'] = [e for e in data['emails'] if e.lower() != email]
            if len(data['emails']) < before_len:
                with open(lp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                print(f'Ledger: {email} removed from {lp}')
"
