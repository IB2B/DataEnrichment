# Intelligent Enrichment — Web App

Web interface for the AIDA People Finder enrichment engine.

## Quick Start (Ubuntu VPS)

```bash
# 1. Upload all files to your VPS
scp -r webapp/* root@your-vps-ip:/root/enrichment/

# 2. SSH into your VPS
ssh root@your-vps-ip

# 3. Copy your Google service account credentials
#    (the same enrichmentdata.json you use locally)
cp /path/to/enrichmentdata.json /root/enrichment/

# 4. (Optional) Copy your proxy list
cp /path/to/proxies.txt /root/enrichment/

# 5. Run setup
cd /root/enrichment
bash setup.sh
```

## Login

- **Email:** admin@intelligentenrichment.com
- **Password:** ChangeMe123! (change it in Settings after first login)

## How to Use

1. Clean your Google Sheet using the Apps Script extension (as before)
2. Share the sheet with the service account email (shown in Settings)
3. Go to Dashboard → paste the sheet URL → click Start Enrichment
4. Watch live progress
5. When done, go to Results → view/search/export the extracted people

## Files

```
webapp/
├── main.py                 ← FastAPI app + routes
├── database.py             ← SQLite models
├── enrichment_worker.py    ← Enrichment engine (background task)
├── config.py               ← Configuration
├── requirements.txt        ← Python dependencies
├── setup.sh                ← VPS deployment script
├── enrichmentdata.json     ← Google service account (YOU PROVIDE)
├── proxies.txt             ← Proxy list (OPTIONAL)
├── templates/
│   ├── base.html           ← Shared layout + sidebar
│   ├── login.html
│   ├── dashboard.html      ← Start enrichment + live progress
│   ├── results.html        ← List of past enrichments
│   ├── result_detail.html  ← Searchable people table + export
│   └── settings.html       ← Password, workers, max people
└── data/
    ├── app.db              ← SQLite database (auto-created)
    └── jobs/               ← Per-job data (auto-created)
```

## Useful Commands

```bash
# Check status
sudo systemctl status enrichment

# View live logs
sudo journalctl -u enrichment -f

# Restart after changes
sudo systemctl restart enrichment

# Add HTTPS
sudo certbot --nginx -d yourdomain.com
```
