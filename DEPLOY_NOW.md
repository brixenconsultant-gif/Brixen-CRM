# BRIXEN CRM — BEGINNER DEPLOYMENT GUIDE (DEPLOY_NOW.md)

Follow these step-by-step instructions to deploy the **Brixen Consultants CRM & Client Portal** to your Hostinger VPS.

---

## 1. INFORMATION & CREDENTIALS YOU NEED FIRST

Before opening the server terminal, gather these values:

1. **Hostinger VPS Server IP Address** (e.g. `187.52.116.13`)
2. **Hostinger VPS Root / User Password**
3. **Hostinger SMTP Email Password** for `notifications@brixenconsultants.com`
4. **Companies House API Key** (optional, leave blank if not using live lookup)

---

## 2. STEP 1: HOSTINGER DASHBOARD ACTIONS

### A. Point Domain to Your VPS IP
1. Log into your **Hostinger hPanel** (or Cloudflare DNS).
2. Go to **DNS Zone Editor** for `brixenconsultants.com`.
3. Add an **A Record**:
   - **Type**: `A`
   - **Name**: `portal`
   - **IPv4 Address**: `[YOUR_VPS_IP]`
   - **TTL**: `300` / Auto

### B. Enable SSH Access
1. In Hostinger hPanel, go to **VPS → SSH Access**.
2. Copy your **SSH IP Address**, **Port** (usually `22`), and **Username** (`root` or `brixen`).

---

## 3. STEP 2: COPY-AND-PASTE SERVER SETUP COMMANDS

Open your computer's Terminal (Mac/Linux) or Command Prompt / PuTTY (Windows) and connect to your server:

```bash
ssh root@[YOUR_VPS_IP]
```

Once logged into your server, copy and paste the following command blocks **one by one**:

### Command Block 1: Create Folder & Extract Files
```bash
# Create application folder
mkdir -p /var/www/brixen-crm
cd /var/www/brixen-crm

# Download or copy brixen-crm-production-v2.zip here and unzip
unzip -o brixen-crm-production-v2.zip
```

### Command Block 2: Setup Python Virtual Environment
```bash
# Create virtual environment and install packages
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install argon2-cffi requests
```

### Command Block 3: Generate Webhook Secret & Create Environment File
Run this command to create your random secret:
```bash
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "Generated Webhook Secret: $SECRET"
```

Now create `.env.production`:
```bash
cat <<EOF > /var/www/brixen-crm/.env.production
WORDPRESS_BASE_URL="https://brixenconsultants.com"
CRM_BASE_URL="https://portal.brixenconsultants.com"
PORTAL_PAGE_URL="https://portal.brixenconsultants.com"
PORT=5050
HOST="127.0.0.1"
STORAGE_PATH="/var/www/brixen-crm/storage"
DATABASE_URL="/var/www/brixen-crm/hypetex.db"
WORDPRESS_WEBHOOK_SECRET="$SECRET"
SMTP_HOST="smtp.hostinger.com"
SMTP_PORT=587
SMTP_USER="notifications@brixenconsultants.com"
SMTP_PASS="YOUR_SMTP_PASSWORD_HERE"
COMPANIES_HOUSE_API_KEY="YOUR_API_KEY_HERE"
EOF
```

> ⚠️ **Note**: Edit `/var/www/brixen-crm/.env.production` using `nano /var/www/brixen-crm/.env.production` to insert your actual Hostinger SMTP Password.

### Command Block 4: Initialize Database & Fix Permissions
```bash
# Seed initial database and create storage
.venv/bin/python3 seed_db.py
sqlite3 /var/www/brixen-crm/hypetex.db "PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;"

# Ensure secure file permissions
mkdir -p /var/www/brixen-crm/storage/clients
chmod 600 /var/www/brixen-crm/hypetex.db
chmod -R 750 /var/www/brixen-crm/storage
chown -R www-data:www-data /var/www/brixen-crm
```

---

## 4. STEP 3: CONFIGURE SYSTEMD & NGINX

### Command Block 5: Create Systemd Background Service
```bash
cat <<EOF | sudo tee /etc/systemd/system/brixen-crm.service
[Unit]
Description=Brixen Consultants CRM WSGI Service
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/brixen-crm
EnvironmentFile=/var/www/brixen-crm/.env.production
ExecStart=/var/www/brixen-crm/.venv/bin/python3 /var/www/brixen-crm/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Start service
sudo systemctl daemon-reload
sudo systemctl enable brixen-crm
sudo systemctl restart brixen-crm
```

### Command Block 6: Configure Nginx Web Server
```bash
cat <<EOF | sudo tee /etc/nginx/sites-available/brixen-crm
server {
    server_name portal.brixenconsultants.com;
    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Block public download of database, env files, and logs
    location ~* \.(db|sql|log|env)\$ {
        deny all;
        return 404;
    }
}
EOF

# Link and reload Nginx
sudo ln -sf /etc/nginx/sites-available/brixen-crm /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 5. STEP 4: INSTALL FREE SSL / HTTPS CERTIFICATE

Run these commands:
```bash
sudo apt update && sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d portal.brixenconsultants.com
```

---

## 6. STEP 5: WORDPRESS PLUGIN CONFIGURATION

1. Log into your WordPress admin: `https://brixenconsultants.com/wp-admin`.
2. Go to **Plugins → Add New → Upload Plugin**.
3. Upload `brixen-crm-sync.zip` (located inside the `wordpress_plugin/` folder of the release zip) and click **Activate**.
4. Go to **WP Admin → Brixen CRM**.
5. Fill in the settings:
   - **CRM Base URL**: `https://portal.brixenconsultants.com`
   - **Portal Page URL**: `https://portal.brixenconsultants.com`
   - **Webhook Secret**: Copy the generated secret from `.env.production` (`cat /var/www/brixen-crm/.env.production | grep WORDPRESS_WEBHOOK_SECRET`).
6. Click **Save Changes**.

---

## 7. STEP 6: VERIFICATION & TESTING

1. Open `https://portal.brixenconsultants.com` in your browser.
2. Sign in as Admin (`admin@brixenconsultant.co.uk`).
3. Place a test registration on `https://brixenconsultants.com`.
4. Verify the new user appears under **Customers / Signups** in the CRM.
5. In the CRM, open the customer file, click **Documents**, upload a document with **Visible to client** ON, and verify the client sees and downloads it securely.
