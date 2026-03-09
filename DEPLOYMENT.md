# AIDA — Deployment Guide

Complete guide for deploying the AIDA Data Enrichment Platform on a Linux VPS (Ubuntu/Debian).

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Deploy (New VPS)](#quick-deploy-new-vps)
- [Deploy to an Existing VPS](#deploy-to-an-existing-vps)
- [Manual Deployment](#manual-deployment)
- [Environment Variables](#environment-variables)
- [Nginx Configuration](#nginx-configuration)
- [SSL / HTTPS](#ssl--https)
- [Google OAuth Setup](#google-oauth-setup)
- [Post-Deployment Checklist](#post-deployment-checklist)
- [Updating the Application](#updating-the-application)
- [Troubleshooting](#troubleshooting)
- [Service Management](#service-management)
- [Architecture Overview](#architecture-overview)

---

## Prerequisites

| Requirement | Minimum |
|---|---|
| OS | Ubuntu 20.04+ / Debian 11+ |
| RAM | 2 GB |
| Disk | 2 GB free |
| Python | 3.10+ |
| Nginx | Installed and running |
| Ports | 80, 443 open |
| DNS | A record pointing your domain to the server IP |

### Required Files

| File | Purpose | Required? |
|---|---|---|
| `enrichmentdata.json` | Google service account credentials for Sheets API | Yes (for enrichment) |
| `client_secret_*.json` | Google OAuth 2.0 client credentials | Optional (for Google Sheets browser) |
| `proxies.txt` | Proxy list for scraping (one per line, `http://ip:port`) | Optional |

---

## Quick Deploy (New VPS)

For a fresh Ubuntu VPS with nothing else running:

```bash
# 1. Upload project files to the server
scp -r webapp/* root@your-vps-ip:/root/enrichment/

# 2. SSH into the server
ssh root@your-vps-ip

# 3. Upload credentials
scp enrichmentdata.json root@your-vps-ip:/root/enrichment/
scp client_secret_*.json root@your-vps-ip:/root/enrichment/   # optional
scp proxies.txt root@your-vps-ip:/root/enrichment/             # optional

# 4. Run the automated setup
cd /root/enrichment
sudo bash setup.sh yourdomain.com
```

The `setup.sh` script handles everything: system dependencies, Python venv, Playwright, systemd services, and Nginx.

---

## Deploy to an Existing VPS

If the server already hosts other applications (e.g., WordPress, Docker containers):

```bash
# Upload files
scp -r webapp/* root@your-vps-ip:/tmp/enrichment-source/

# SSH in and run the safe deploy script
ssh root@your-vps-ip
cd /tmp/enrichment-source
sudo bash deploy-zmachine.sh
```

The `deploy-zmachine.sh` script is designed to be non-destructive:
- Creates a **new** Nginx server block (does not modify existing sites)
- Creates a **new** systemd service
- Uses a **dedicated** app user (`enrichment`)
- Auto-detects port conflicts (tries 8000, 8010, 8020)
- Rolls back Nginx changes if the config test fails

---

## Manual Deployment

Step-by-step instructions if you prefer not to use the automated scripts.

### 1. Install System Dependencies

```bash
sudo apt update
sudo apt install -y \
    python3 python3-pip python3-venv \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libwayland-client0 xvfb fonts-liberation fonts-noto-color-emoji \
    nginx
```

### 2. Create App User and Directory

```bash
sudo useradd -m -s /bin/bash enrichment
sudo mkdir -p /opt/enrichment/data/jobs /opt/enrichment/data/linkedin_cookies
```

### 3. Copy Application Files

```bash
sudo cp -r /path/to/webapp/* /opt/enrichment/
sudo cp /path/to/enrichmentdata.json /opt/enrichment/
sudo chown -R enrichment:enrichment /opt/enrichment
```

### 4. Set Up Python Environment

```bash
cd /opt/enrichment
sudo -u enrichment python3 -m venv venv
sudo -u enrichment bash -c "
    source /opt/enrichment/venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    pip install 'scrapling[all]'
    scrapling install
    playwright install chromium
"
```

### 5. Create Xvfb Service

Playwright needs a display server even on a headless VPS:

```bash
sudo cat > /etc/systemd/system/xvfb.service << 'EOF'
[Unit]
Description=X Virtual Frame Buffer (display :99)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

### 6. Create Application Service

```bash
SECRET_KEY=$(openssl rand -hex 32)

sudo cat > /etc/systemd/system/enrichment.service << EOF
[Unit]
Description=AIDA Data Enrichment Web App
After=network.target xvfb.service
Requires=xvfb.service

[Service]
Type=simple
User=enrichment
WorkingDirectory=/opt/enrichment
ExecStart=/opt/enrichment/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=SECRET_KEY=$SECRET_KEY
Environment=DISPLAY=:99

[Install]
WantedBy=multi-user.target
EOF
```

### 7. Start Services

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb
sudo systemctl enable --now enrichment
```

### 8. Configure Nginx

See [Nginx Configuration](#nginx-configuration) below.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | JWT signing key (generate with `openssl rand -hex 32`) | `change-this-to-a-random-string-in-production-2024` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | Auto-loaded from `client_secret_*.json` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | Auto-loaded from `client_secret_*.json` |
| `DISPLAY` | X display for Playwright (set to `:99` with Xvfb) | Not set |

Environment variables are set in the systemd service file at `/etc/systemd/system/enrichment.service` under the `Environment=` directives.

To update an environment variable:

```bash
sudo systemctl edit enrichment
# Add under [Service]:
# Environment=SECRET_KEY=your-new-key
sudo systemctl restart enrichment
```

---

## Nginx Configuration

Create the Nginx server block:

```bash
sudo cat > /etc/nginx/sites-available/enrichment << 'NGINX'
server {
    listen 80;
    server_name yourdomain.com;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
    }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/enrichment /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Replace `yourdomain.com` with your actual domain and `8000` with the port if different.

---

## SSL / HTTPS

Use Certbot with the Nginx plugin:

```bash
# Install certbot
sudo apt install -y certbot python3-certbot-nginx

# Obtain certificate and auto-configure Nginx
sudo certbot --nginx -d yourdomain.com

# Verify auto-renewal
sudo certbot renew --dry-run
```

Certbot will automatically modify the Nginx config to redirect HTTP to HTTPS.

---

## Google OAuth Setup

To enable the Google Sheets browser in the app (connect your Google account and browse sheets directly):

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable the **Google Sheets API** and **Google Drive API**
4. Create OAuth 2.0 credentials (Web application type)
5. Add your redirect URI: `https://yourdomain.com/oauth/google/callback`
6. Download the JSON file and place it at `/opt/enrichment/client_secret_*.json`
7. Restart the service: `sudo systemctl restart enrichment`

For the full guide, see [SETUP_OAUTH.md](SETUP_OAUTH.md).

---

## Post-Deployment Checklist

After deploying, verify the following:

- [ ] **DNS**: A record points to your server IP
- [ ] **Service running**: `sudo systemctl status enrichment` shows `active (running)`
- [ ] **App reachable**: Visit `https://yourdomain.com` in a browser
- [ ] **Login works**: Use default credentials (see below), then change the password
- [ ] **HTTPS active**: Certbot configured and redirecting HTTP to HTTPS
- [ ] **Credentials uploaded**: `enrichmentdata.json` is in `/opt/enrichment/`
- [ ] **OAuth configured**: `client_secret_*.json` is in `/opt/enrichment/` (if using Google Sheets browser)
- [ ] **Diagnostics**: Visit `https://yourdomain.com/api/diagnose` to verify all components

### Default Credentials

| Field | Value |
|---|---|
| Email | `admin@intelligentenrichment.com` |
| Password | `ChangeMe123!` |

**Change the password immediately** after first login via Settings.

---

## Updating the Application

### If deployed via git clone (deploy-zmachine.sh)

```bash
cd /opt/enrichment
sudo -u enrichment git pull origin main
sudo systemctl restart enrichment
```

### If deployed via file copy (setup.sh)

```bash
# Upload new files
scp -r webapp/* root@your-vps-ip:/opt/enrichment/

# Fix ownership and restart
ssh root@your-vps-ip "chown -R enrichment:enrichment /opt/enrichment && sudo systemctl restart enrichment"
```

### If dependencies changed

```bash
cd /opt/enrichment
sudo -u enrichment bash -c "
    source venv/bin/activate
    pip install -r requirements.txt
"
sudo systemctl restart enrichment
```

---

## Troubleshooting

### Service won't start

```bash
# Check service status and recent logs
sudo systemctl status enrichment
sudo journalctl -u enrichment -n 50 --no-pager
```

### Port already in use

```bash
# Find what's using the port
sudo ss -tlnp | grep :8000

# Change the port in the service file
sudo systemctl edit enrichment
# Set: ExecStart=/opt/enrichment/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8010 --workers 1
# Also update the Nginx proxy_pass port
```

### Playwright / Chromium errors

```bash
# Verify Xvfb is running
sudo systemctl status xvfb

# Reinstall Playwright browsers
cd /opt/enrichment
sudo -u enrichment bash -c "source venv/bin/activate && playwright install chromium"

# Check for missing system libraries
sudo -u enrichment bash -c "source venv/bin/activate && python -c 'from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(); b.close(); p.stop(); print(\"OK\")'"
```

### Google OAuth not working

- Verify `client_secret_*.json` exists in `/opt/enrichment/`
- Check redirect URI matches exactly: `https://yourdomain.com/oauth/google/callback`
- Run diagnostics: `curl http://localhost:8000/api/diagnose`

### Permission errors

```bash
# Fix file ownership
sudo chown -R enrichment:enrichment /opt/enrichment
sudo chown -R enrichment:enrichment /opt/enrichment/data
```

### Database issues

The SQLite database is stored at `/opt/enrichment/data/app.db`. To reset:

```bash
sudo systemctl stop enrichment
sudo -u enrichment rm /opt/enrichment/data/app.db
sudo systemctl start enrichment
# A fresh database will be created on startup
```

---

## Service Management

```bash
# Start / stop / restart
sudo systemctl start enrichment
sudo systemctl stop enrichment
sudo systemctl restart enrichment

# Check status
sudo systemctl status enrichment

# View live logs
sudo journalctl -u enrichment -f

# View last 100 log lines
sudo journalctl -u enrichment -n 100 --no-pager

# Xvfb management
sudo systemctl status xvfb
sudo systemctl restart xvfb
```

---

## Architecture Overview

```
Internet
    |
    v
[ Nginx ] --reverse proxy--> [ Uvicorn :8000 ]
                                   |
                              [ FastAPI App ]
                              /      |       \
                    [ SQLite ]  [ Playwright ]  [ aiohttp ]
                    (app.db)    (LinkedIn,       (Enrichment
                                 Google Maps)     Worker)
                                     |
                                [ Xvfb :99 ]
                              (Virtual Display)
```

- **Nginx** terminates SSL and proxies requests to the app
- **Uvicorn** runs the FastAPI application (single worker)
- **Playwright** drives Chromium for LinkedIn and Google Maps scraping (requires Xvfb)
- **aiohttp** handles async HTTP requests for the enrichment worker
- **SQLite** stores all application data (users, jobs, results, settings)

### Directory Structure on Server

```
/opt/enrichment/
  main.py                    # FastAPI application
  database.py                # SQLite models and queries
  enrichment_worker.py       # Async enrichment engine
  linkedin_scraper.py        # LinkedIn scraper (Playwright)
  website_scraper.py         # Bulk website scraper
  google_maps_scraper.py     # Google Maps scraper
  config.py                  # Configuration
  requirements.txt           # Python dependencies
  enrichmentdata.json        # Google service account creds
  client_secret_*.json       # Google OAuth creds (optional)
  proxies.txt                # Proxy list (optional)
  venv/                      # Python virtual environment
  templates/                 # Jinja2 HTML templates
  apps_script/               # Google Apps Script
  data/
    app.db                   # SQLite database
    jobs/                    # Per-job working data
    linkedin_cookies/        # Persistent LinkedIn sessions
```
