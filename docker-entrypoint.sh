#!/bin/bash
set -e

DB_FILE="${DATABASE_URL:-/app/data/hypetex.db}"
SEED_DB="/app/seed/hypetex.db"
SEED_STORAGE="/app/seed/clients"

# ── First-run: copy seed database if no DB exists yet ──────
if [ ! -f "$DB_FILE" ] && [ -f "$SEED_DB" ]; then
    echo "[entrypoint] First run detected — seeding database..."
    cp "$SEED_DB" "$DB_FILE"
    echo "[entrypoint] Database seeded at $DB_FILE"
fi

# Initialize empty or incomplete database volumes before starting the app.
if [ ! -s "$DB_FILE" ]; then
    echo "[entrypoint] Empty database detected — creating schema and seed data..."
    python seed_db.py
elif ! sqlite3 "$DB_FILE" "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents';" | grep -q '^1$'; then
    echo "[entrypoint] Incomplete database detected — applying schema..."
    python -c 'from db import init_db; init_db()'
fi

# ── First-run: copy seed client files if storage is empty ──
STORAGE_DIR="/app/storage/clients"
if [ -d "$SEED_STORAGE" ] && [ -z "$(ls -A $STORAGE_DIR 2>/dev/null)" ]; then
    echo "[entrypoint] Seeding client storage..."
    cp -r "$SEED_STORAGE"/* "$STORAGE_DIR"/ 2>/dev/null || true
    echo "[entrypoint] Client storage seeded."
fi

echo "[entrypoint] Starting Brixen CRM on ${HOST:-0.0.0.0}:${PORT:-5050}..."
exec python app.py
