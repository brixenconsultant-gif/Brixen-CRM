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

# ── First-run: copy seed client files if storage is empty ──
STORAGE_DIR="/app/storage/clients"
if [ -d "$SEED_STORAGE" ] && [ -z "$(ls -A $STORAGE_DIR 2>/dev/null)" ]; then
    echo "[entrypoint] Seeding client storage..."
    cp -r "$SEED_STORAGE"/* "$STORAGE_DIR"/ 2>/dev/null || true
    echo "[entrypoint] Client storage seeded."
fi

echo "[entrypoint] Starting Brixen CRM on ${HOST:-0.0.0.0}:${PORT:-5050}..."
exec python app.py
