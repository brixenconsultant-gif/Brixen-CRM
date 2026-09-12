# ============================================================
# Brixen CRM — Production Docker Image (with seed data)
# ============================================================
# Includes the SQLite database and client uploads baked into
# the image as seed data. On first run, the entrypoint copies
# them into the persistent Docker volumes so your data survives
# container rebuilds.
# ============================================================

FROM python:3.12-slim

# ── System dependencies ────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libjpeg62-turbo \
        libpng16-16 \
        curl \
        sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# ── App directory ──────────────────────────────────────────
WORKDIR /app

# ── Python dependencies (cached layer) ────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────
COPY app.py db.py schema.sql seed_db.py ./
COPY email_engine.py compliance_alerts.py product_catalog_config.py ./
COPY templates/ templates/
COPY scripts/ scripts/
COPY tests/ tests/
COPY test_app.py test_ci_smoke.py ./

# ── Runtime data directories ───────────────────────────────
# Production database and client files are mounted from the VPS.
RUN mkdir -p /app/seed /app/data /app/storage/clients

# ── Entrypoint ─────────────────────────────────────────────
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# ── Runtime configuration ─────────────────────────────────
ENV HOST=0.0.0.0
ENV PORT=5050
ENV DATABASE_URL=/app/data/hypetex.db

EXPOSE 5050

# ── Healthcheck ────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5050/ || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
