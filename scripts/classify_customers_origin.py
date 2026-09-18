#!/usr/bin/env python3
import sqlite3
import sys

bak = sys.argv[1] if len(sys.argv) > 1 else "/var/www/brixen-crm/hypetex-safety-before-customers-wipe-20260915_054254.db"
con = sqlite3.connect(bak)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT id, full_name, email, wordpress_user_id, account_type, last_synced_at, created_at "
    "FROM users WHERE role = 'CLIENT' ORDER BY id"
).fetchall()
print("total", len(rows))
crm = wp = other = 0
for r in rows:
    d = dict(r)
    w = str(d.get("wordpress_user_id") or "").strip()
    if w.isdigit():
        kind = "WP_NUMERIC"
        wp += 1
    elif w.startswith("local_user_"):
        kind = "CRM_LOCAL"
        crm += 1
    elif not w:
        kind = "CRM_EMPTY_WP"
        crm += 1
    else:
        kind = "OTHER"
        other += 1
    print("%4s %-14s wp=%-42r %s | %s" % (d["id"], kind, w, d["email"], d["full_name"]))
print("SUMMARY crm", crm, "wp", wp, "other", other)
