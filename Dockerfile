# ============================================================
# Brixen CRM — Production Docker Image
# ============================================================
# Multi-stage build: slim Python base + only runtime deps.
# SQLite database and client uploads are stored on a named
# volume mounted at /app/data.
# ============================================================

FROM python:3.12-slim AS base

# ── System dependencies ────────────────────────────────────
# tesseract-ocr  → pytesseract OCR
# libpdfium      → pypdfium2 rendering (wheels ship their own, but
#                   we keep ldd deps satisfied)
# curl           → healthcheck
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
    && rm -rf /var/lib/apt/lists/*

# ── App directory ──────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────
COPY app.py db.py schema.sql seed_db.py ./
COPY email_engine.py compliance_alerts.py ./
COPY templates/ templates/
COPY static/ static/
COPY scripts/ scripts/

# ── Data directory (will be a Docker volume) ───────────────
RUN mkdir -p /app/data /app/storage/clients

# ── Runtime configuration ─────────────────────────────────
ENV HOST=0.0.0.0
ENV PORT=5050
ENV DATABASE_URL=/app/data/hypetex.db

EXPOSE 5050

# ── Healthcheck ────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5050/ || exit 1

# ── Entrypoint ─────────────────────────────────────────────
CMD ["python", "app.py"]
