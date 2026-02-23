#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Intelligent Enrichment — Elestio VPS Deployment Script
# For a VPS that ALREADY runs other web apps
# Usage: sudo bash setup.sh yourdomain.com
# ═══════════════════════════════════════════════════════════════

set -e

DOMAIN=${1:-""}
APP_DIR="/opt/enrichment"
APP_USER="enrichment"
APP_PORT=8000
SECRET_KEY=$(openssl rand -hex 32)

if [ -z "$DOMAIN" ]; then
    echo "Usage: sudo bash setup.sh yourdomain.com"
    echo "  The domain/subdomain that will point to this app"
    exit 1
fi

echo "═══════════════════════════════════════════"
echo "  Intelligent Enrichment — Elestio Deploy"
echo "  Domain: $DOMAIN"
echo "═══════════════════════════════════════════"

# ── Check if port 8000 is already taken ──
if ss -tlnp | grep -q ":$APP_PORT "; then
    echo "Port $APP_PORT is already in use. Trying 8010..."
    APP_PORT=8010
    if ss -tlnp | grep -q ":$APP_PORT "; then
        echo "Port $APP_PORT also in use. Trying 8020..."
        APP_PORT=8020
    fi
fi
echo "Using port: $APP_PORT"

# ── 1. System dependencies for Playwright/Chromium ──
echo -e "\n[1/6] Installing system dependencies..."
apt update -qq
apt install -y \
    python3 python3-pip python3-venv \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libwayland-client0 xvfb fonts-liberation fonts-noto-color-emoji

# ── 2. Create app user ──
echo -e "\n[2/6] Creating app user..."
id -u $APP_USER &>/dev/null || useradd -m -s /bin/bash $APP_USER

# ── 3. Setup app directory ──
echo -e "\n[3/6] Setting up app directory..."
mkdir -p $APP_DIR/data/jobs $APP_DIR/data/linkedin_cookies
cp -r ./* $APP_DIR/ 2>/dev/null || true
chown -R $APP_USER:$APP_USER $APP_DIR

# ── 4. Python venv + Playwright ──
echo -e "\n[4/6] Installing Python dependencies & Playwright..."
cd $APP_DIR
sudo -u $APP_USER python3 -m venv venv
sudo -u $APP_USER bash -c "
    source $APP_DIR/venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    playwright install chromium
"

# ── 5. Systemd service ──
echo -e "\n[5/6] Creating systemd service..."
cat > /etc/systemd/system/enrichment.service << EOF
[Unit]
Description=Intelligent Enrichment Web App
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

# ── 6. Nginx — ADD new server block (don't touch existing sites) ──
echo -e "\n[6/6] Adding Nginx server block for $DOMAIN..."
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
nginx -t && systemctl reload nginx

# ── Allow 443 if not already (for certbot later) ──
ufw allow 443/tcp 2>/dev/null || true

echo ""
echo "═══════════════════════════════════════════"
echo "  SETUP COMPLETE"
echo "═══════════════════════════════════════════"
echo ""
echo "  App running at: http://$DOMAIN"
echo "  Internal port:  $APP_PORT"
echo ""
echo "  Login:"
echo "    Email:    admin@intelligentenrichment.com"
echo "    Password: ChangeMe123!"
echo ""
echo "  NEXT STEPS:"
echo "  1. Point $DOMAIN DNS (A record) to this server IP"
echo "  2. Copy enrichmentdata.json to $APP_DIR/"
echo "  3. (Optional) Copy client_secret_*.json to $APP_DIR/"
echo "  4. Restart: sudo systemctl restart enrichment"
echo "  5. Enable HTTPS: sudo certbot --nginx -d $DOMAIN"
echo ""
echo "  USEFUL COMMANDS:"
echo "    Status:  sudo systemctl status enrichment"
echo "    Logs:    sudo journalctl -u enrichment -f"
echo "    Restart: sudo systemctl restart enrichment"
echo "═══════════════════════════════════════════"
