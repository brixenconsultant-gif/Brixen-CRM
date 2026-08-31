#!/bin/bash
# Daily SQLite backup for Brixen CRM on Hostinger VPS.
# Keeps 14 days of snapshots under /var/www/brixen-crm/backups/db

set -euo pipefail

DB_PATH="${DATABASE_URL:-/var/www/brixen-crm/hypetex.db}"
BACKUP_DIR="/var/www/brixen-crm/backups/db"
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d_%H%M%S)"
TARGET="$BACKUP_DIR/hypetex_${TS}.db"

if [[ ! -f "$DB_PATH" ]]; then
  echo "Database not found: $DB_PATH" >&2
  exit 1
fi

sqlite3 "$DB_PATH" ".backup '$TARGET'"
chmod 600 "$TARGET"
chown brixen:brixen "$TARGET" 2>/dev/null || true
find "$BACKUP_DIR" -name 'hypetex_*.db' -mtime +"$RETENTION_DAYS" -delete

echo "Backup saved: $TARGET"
