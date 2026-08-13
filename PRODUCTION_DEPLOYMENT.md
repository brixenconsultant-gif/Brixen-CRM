# BRIXEN CONSULTANTS CRM — PRODUCTION DEPLOYMENT GUIDE

This document provides complete instructions for deploying the **Brixen Consultants CRM / Client Portal** and connecting it to the official WordPress + WooCommerce website (`https://brixenconsultants.com/`).

---

## 1. Server Requirements
- **Operating System**: Ubuntu 22.04 LTS or 24.04 LTS Linux Server.
- **Python**: Python 3.10+ with `wsgiref` or `gunicorn` / `uvicorn`.
- **Web Server**: Nginx (reverse proxy with Let's Encrypt TLS/SSL).
- **SQLite**: SQLite 3.35+ with WAL mode enabled.
- **Process Manager**: Systemd (`brixen-crm.service`).

---

## 2. Environment Variables (`.env.production`)

```bash
# Web & Server Configuration
WORDPRESS_BASE_URL="https://brixenconsultants.com"
CRM_BASE_URL="https://portal.brixenconsultants.com"
PORT=5050
HOST="127.0.0.1"

# Cryptographic Webhook Secret (Generated 32-byte Hex Token)
WORDPRESS_WEBHOOK_SECRET="e9f4c3d8a1b2c5d7e0f3a6b9c2d5e8f1a4b7c0d3e6f9a2b5c8d1e4f7a0b3c6d9"

# Storage & Database Paths (Outside Public Web Root)
STORAGE_PATH="/var/www/brixen-crm/storage"
DATABASE_URL="/var/www/brixen-crm/hypetex.db"

# SMTP Transactional Email Credentials
SMTP_HOST="smtp.hostinger.com"
SMTP_PORT=587
SMTP_USER="notifications@brixenconsultants.com"
SMTP_PASS="<SecureSMTPPassword>"
```

---

## 3. Domain & DNS Requirements

Create an **A Record** in Hostinger / Cloudflare DNS:

| Type | Host / Subdomain | Target / Value | TTL |
|---|---|---|---|
| **A** | `portal` | `[SERVER_PUBLIC_IPV4]` | Auto / 300s |

---

## 4. HTTPS & SSL Configuration

Generate a free TLS/SSL certificate using Certbot:

```bash
sudo apt update && sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d portal.brixenconsultants.com
```

---

## 5. Database Setup (SQLite Hardening)

```bash
# Create directory outside web root
sudo mkdir -p /var/www/brixen-crm/backups
sudo chown -R www-data:www-data /var/www/brixen-crm

# Initialize Schema
cd /var/www/brixen-crm
python3 db.py

# Verify WAL mode & permissions
sqlite3 /var/www/brixen-crm/hypetex.db "PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;"
sudo chmod 600 /var/www/brixen-crm/hypetex.db
```

---

## 6. Storage Setup

```bash
sudo mkdir -p /var/www/brixen-crm/storage/clients
sudo chmod -R 750 /var/www/brixen-crm/storage
```

---

## 7. Process Manager (Systemd Service)

Create `/etc/systemd/system/brixen-crm.service`:

```ini
[Unit]
Description=Brixen Consultants CRM WSGI Service
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/brixen-crm
EnvironmentFile=/var/www/brixen-crm/.env.production
ExecStart=/usr/bin/python3 /var/www/brixen-crm/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable & start service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable brixen-crm
sudo systemctl start brixen-crm
```

---

## 8. Reverse Proxy (Nginx Configuration)

Create `/etc/nginx/sites-available/brixen-crm`:

```nginx
server {
    server_name portal.brixenconsultants.com;

    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Block direct access to storage & db files
    location ~* \.(db|sql|log|env)$ {
        deny all;
        return 404;
    }
}
```

Enable Nginx site:
```bash
sudo ln -s /etc/nginx/sites-available/brixen-crm /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 9. WordPress Plugin Installation

1. Download `brixen-crm-sync.zip` from `wordpress_plugin/brixen-crm-sync.zip`.
2. Log into WP Admin (`https://brixenconsultants.com/wp-admin`).
3. Navigate to **Plugins → Add New → Upload Plugin**.
4. Upload `brixen-crm-sync.zip` and click **Activate Plugin**.

---

## 10. Webhook Configuration

1. In WP Admin, navigate to **WP Admin → Brixen CRM**.
2. Enter:
   - **CRM Base URL**: `https://portal.brixenconsultants.com`
   - **Webhook Secret**: `e9f4c3d8a1b2c5d7e0f3a6b9c2d5e8f1a4b7c0d3e6f9a2b5c8d1e4f7a0b3c6d9`
3. Click **Save Changes**.

---

## 11. WooCommerce Configuration

1. Go to **WooCommerce → Settings → Accounts & Privacy**.
2. Recommended: Enable **"Allow customers to create an account during checkout"**.
3. Verify payment gateways are active (Stripe, PayPal, or Bank Transfer).

---

## 12. Production Smoke-Test Checklist

- [ ] New WP user registration creates CRM client.
- [ ] WooCommerce order creation syncs order to CRM.
- [ ] HMAC signature verification blocks invalid payloads (`401 Unauthorized`).
- [ ] Replay attack with duplicate event ID returns `status: 'ignored'`.
- [ ] Staff progress update to 50% updates Client Portal.
- [ ] Document upload creates private file in `storage/clients/{id}/documents/`.
- [ ] Unauthorized client download returns `403 Forbidden`.

---

## 13. Backup Procedure

Add to `crontab -e`:

```bash
# Daily SQLite Database Backup at 2:00 AM
0 2 * * * sqlite3 /var/www/brixen-crm/hypetex.db ".backup '/var/www/brixen-crm/backups/hypetex_$(date +\%Y\%m\%d_\%H\%M\%S).db'"
```

---

## 14. Rollback Plan

- **WordPress Rollback**: Deactivate `Brixen Consultants CRM & WooCommerce Sync` plugin in WP Admin. WordPress and WooCommerce core functionality operate 100% independently.
- **CRM Rollback**: Stop Systemd service (`sudo systemctl stop brixen-crm`).
