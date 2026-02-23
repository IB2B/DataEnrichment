#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Data Enrichment — Deploy to zmachine VPS
# Customized for: data-enrichment.zmachine.pro
# VPS: zmachine-u37227 (already runs zmachine.pro + britney)
#
# SAFE: Only creates NEW files/services. Does NOT touch:
#   - System nginx (just adds a new site)
#   - zmachine.pro config
#   - britney config/containers
#   - MySQL / PHP-FPM
#   - Any existing Docker containers
#
# Usage: sudo bash deploy-zmachine.sh
# ═══════════════════════════════════════════════════════════════

set -e

DOMAIN="data-enrichment.zmachine.pro"
APP_DIR="/opt/enrichment"
APP_USER="enrichment"
APP_PORT=8000
SECRET_KEY=$(openssl rand -hex 32)

echo "═══════════════════════════════════════════════════════"
echo "  Data Enrichment — Deploying to $DOMAIN"
echo "  Target: $APP_DIR"
echo "═══════════════════════════════════════════════════════"

# ── Pre-flight checks ──
echo -e "\n[0/7] Pre-flight checks..."

# Ensure we're root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run with sudo: sudo bash deploy-zmachine.sh"
    exit 1
fi

# Check disk space (need at least 2GB)
AVAIL_GB=$(df / --output=avail -BG | tail -1 | tr -dc '0-9')
if [ "$AVAIL_GB" -lt 2 ]; then
    echo "ERROR: Only ${AVAIL_GB}GB free. Need at least 2GB."
    exit 1
fi
echo "  Disk: ${AVAIL_GB}GB available — OK"

# Check nginx is running
if ! systemctl is-active --quiet nginx; then
    echo "ERROR: System nginx is not running!"
    exit 1
fi
echo "  Nginx: running — OK"

# Check port availability
if ss -tlnp | grep -q ":${APP_PORT} "; then
    echo "  Port $APP_PORT in use. Trying 8010..."
    APP_PORT=8010
    if ss -tlnp | grep -q ":${APP_PORT} "; then
        echo "  Port 8010 in use. Trying 8020..."
        APP_PORT=8020
        if ss -tlnp | grep -q ":${APP_PORT} "; then
            echo "ERROR: Ports 8000, 8010, 8020 all in use."
            exit 1
        fi
    fi
fi
echo "  Port: $APP_PORT — OK"

# ── 1. System dependencies for Playwright/Chromium ──
echo -e "\n[1/7] Installing system dependencies (if missing)..."
apt update -qq
apt install -y -qq \
    python3 python3-pip python3-venv \
    libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 \
    libcups2t64 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0t64 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 \
    libwayland-client0 xvfb fonts-liberation fonts-noto-color-emoji \
    2>/dev/null || \
apt install -y -qq \
    python3 python3-pip python3-venv \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libwayland-client0 xvfb fonts-liberation fonts-noto-color-emoji

# ── 2. Create app user ──
echo -e "\n[2/7] Creating app user..."
if id -u $APP_USER &>/dev/null; then
    echo "  User '$APP_USER' already exists — OK"
else
    useradd -m -s /bin/bash $APP_USER
    echo "  User '$APP_USER' created"
fi

# ── 3. Clone repo ──
echo -e "\n[3/7] Cloning repository..."
if [ -d "$APP_DIR/.git" ]; then
    echo "  Repo already exists. Pulling latest..."
    cd $APP_DIR
    sudo -u $APP_USER git pull origin main
else
    # Backup existing dir if any
    if [ -d "$APP_DIR" ]; then
        mv $APP_DIR ${APP_DIR}.backup.$(date +%Y%m%d_%H%M%S)
        echo "  Existing dir backed up"
    fi
    git clone https://github.com/IB2B/DataEnrichment.git $APP_DIR
    chown -R $APP_USER:$APP_USER $APP_DIR
fi

# Create data directories
mkdir -p $APP_DIR/data/jobs $APP_DIR/data/linkedin_cookies
chown -R $APP_USER:$APP_USER $APP_DIR/data

# ── 4. Python venv + dependencies ──
echo -e "\n[4/7] Setting up Python environment..."
cd $APP_DIR
if [ ! -d "$APP_DIR/venv" ]; then
    sudo -u $APP_USER python3 -m venv venv
    echo "  Virtual environment created"
fi

sudo -u $APP_USER bash -c "
    source $APP_DIR/venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -r $APP_DIR/requirements.txt
    echo '  Installing Playwright Chromium (this takes a minute)...'
    playwright install chromium
"
echo "  Python dependencies installed"

# ── 5. Systemd service ──
echo -e "\n[5/7] Creating systemd service..."
cat > /etc/systemd/system/enrichment.service << EOF
[Unit]
Description=Data Enrichment Web App (data-enrichment.zmachine.pro)
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/uvicorn main:app --host 127.0.0.1 --port $APP_PORT --workers 1
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=SECRET_KEY=$SECRET_KEY
Environment=DISPLAY=:99

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable enrichment
systemctl start enrichment

# Wait and verify it started
sleep 3
if systemctl is-active --quiet enrichment; then
    echo "  Service started on port $APP_PORT — OK"
else
    echo "  WARNING: Service may have failed. Check: journalctl -u enrichment -n 30"
fi

# ── 6. Nginx site config (new file, doesn't touch anything else) ──
echo -e "\n[6/7] Adding nginx config for $DOMAIN..."

# Safety: test existing config first
nginx -t 2>/dev/null
if [ $? -ne 0 ]; then
    echo "ERROR: Existing nginx config has errors. Fix those first!"
    echo "  Run: nginx -t"
    exit 1
fi

cat > /etc/nginx/sites-available/enrichment << NGINX
server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/enrichment /etc/nginx/sites-enabled/

# Test config BEFORE reloading (safety)
if nginx -t 2>&1; then
    systemctl reload nginx
    echo "  Nginx config added and reloaded — OK"
else
    # Rollback: remove the config if it breaks nginx
    rm -f /etc/nginx/sites-enabled/enrichment
    echo "ERROR: New nginx config caused errors. Rolled back. Nginx unchanged."
    exit 1
fi

# ── 7. SSL with Certbot ──
echo -e "\n[7/7] Setting up SSL..."
if command -v certbot &>/dev/null; then
    echo "  Running certbot for $DOMAIN..."
    echo "  (Make sure DNS A record points to this server first!)"
    certbot --nginx -d $DOMAIN --non-interactive --agree-tos --redirect \
        --email admin@zmachine.pro 2>&1 || {
        echo "  WARNING: Certbot failed. You can run it manually later:"
        echo "    sudo certbot --nginx -d $DOMAIN"
    }
else
    echo "  Certbot not found. Install it:"
    echo "    sudo apt install certbot python3-certbot-nginx"
    echo "  Then run: sudo certbot --nginx -d $DOMAIN"
fi

# ── Done ──
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  DEPLOYMENT COMPLETE"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  App:    https://$DOMAIN"
echo "  Port:   $APP_PORT (internal)"
echo "  Dir:    $APP_DIR"
echo ""
echo "  Default login:"
echo "    Email:    admin@intelligentenrichment.com"
echo "    Password: ChangeMe123!"
echo ""
echo "  REMAINING STEPS:"
echo "  1. Add DNS A record: $DOMAIN -> $(curl -s ifconfig.me 2>/dev/null || echo '<server-ip>')"
echo "  2. Copy credentials:  scp enrichmentdata.json root@<server>:$APP_DIR/"
echo "  3. (Optional) Copy:   scp client_secret_*.json root@<server>:$APP_DIR/"
echo "  4. Fix ownership:     chown $APP_USER:$APP_USER $APP_DIR/*.json"
echo "  5. Restart:           sudo systemctl restart enrichment"
echo ""
echo "  USEFUL COMMANDS:"
echo "    Status:   sudo systemctl status enrichment"
echo "    Logs:     sudo journalctl -u enrichment -f"
echo "    Restart:  sudo systemctl restart enrichment"
echo "    Update:   cd $APP_DIR && git pull && sudo systemctl restart enrichment"
echo "═══════════════════════════════════════════════════════"
