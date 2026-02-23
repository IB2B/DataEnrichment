# AIDA — Intelligent Data Enrichment Platform

A self-hosted web application for automated B2B data enrichment. Feed it a Google Sheet of companies and AIDA finds decision-makers, emails, phone numbers, LinkedIn profiles, and more — all with live progress tracking.

Built with **FastAPI**, **Jinja2**, and **SQLite**.

## Features

### Google Sheets Enrichment Engine
- Connect your Google account via OAuth 2.0 — browse and select sheets directly from the UI
- Reads company data from a specified sheet tab (default: `Cleaned_Data`)
- Enriches each company with decision-maker names, emails, phone numbers, job titles, and LinkedIn URLs
- Uses a 740+ name database for intelligent name extraction from web pages
- Async worker with configurable concurrency (up to 150+ parallel workers)
- Configurable max people per company
- Live progress dashboard with real-time status updates
- Automatic job queuing and scheduling (up to 2 concurrent jobs)
- Proxy rotation support for scraping reliability

### LinkedIn Scraper
- Paste a LinkedIn Sales Navigator or search URL
- Scrapes profile data across multiple pages (configurable, up to 100 pages)
- Extracts names, titles, companies, locations, and profile URLs
- Manual browser login flow to handle LinkedIn authentication
- Persistent session management (saves cookies between scrapes)
- Configurable page delays to avoid rate limiting

### Website Scraper
- Bulk-scrape any list of website URLs
- Extracts emails, phone numbers, and contact information from web pages
- Processes multiple URLs in a single batch

### Google Maps Scraper
- Scrape Google Maps search results for local businesses
- Extracts business names, addresses, phone numbers, websites, ratings, and reviews
- Optional email extraction from business websites
- Powered by Playwright for reliable browser automation

### Results & Export
- Searchable, paginated results for every scrape type
- Export any result set to CSV with one click
- Full history of all past enrichment jobs and scrapes
- Per-job result counts and status tracking

### Settings & Configuration
- Change password
- Configure enrichment parameters (max people per company, worker count, default sheet tab)
- LinkedIn credentials and delay settings
- Google OAuth connect/disconnect
- Diagnostic endpoint (`/api/diagnose`) to verify credentials, packages, and connectivity

### Security
- JWT-based authentication with configurable token expiry
- Google OAuth 2.0 with CSRF state validation
- Automatic token refresh for Google API access
- Cookie-based session management

## Tech Stack

- **Backend:** FastAPI, Uvicorn, SQLite
- **Frontend:** Jinja2 templates, server-side rendering
- **Scraping:** aiohttp, BeautifulSoup, lxml, Playwright
- **Google APIs:** gspread, Google Sheets API, Google Drive API
- **Auth:** PyJWT, Google OAuth 2.0

## Quick Start

### 1. Deploy to a VPS (Ubuntu)

```bash
# Upload files to your VPS
scp -r webapp/* root@your-vps-ip:/root/enrichment/

# SSH in
ssh root@your-vps-ip

# Copy your Google service account credentials
cp /path/to/enrichmentdata.json /root/enrichment/

# (Optional) Copy your proxy list
cp /path/to/proxies.txt /root/enrichment/

# Run the setup script
cd /root/enrichment
bash setup.sh
```

### 2. Login

- **Email:** `admin@intelligentenrichment.com`
- **Password:** `ChangeMe123!` (change it in Settings after first login)

### 3. Connect Google Sheets

Set up OAuth to browse your sheets directly from the app. See [SETUP_OAUTH.md](SETUP_OAUTH.md) for the full guide.

### 4. Start Enriching

1. Clean your Google Sheet using the [Apps Script cleaner](#apps-script--aida-export-cleaner) (see below)
2. Go to Dashboard → select a sheet → click **Start Enrichment**
3. Watch live progress
4. Go to Results → search, view, and export extracted contacts

## Apps Script — AIDA Export Cleaner

Before running enrichment, clean your raw export data using this Google Apps Script. It consolidates multi-row company/DM blocks into one row per company.

### How to Install

1. Open your Google Sheet
2. Go to **Extensions > Apps Script**
3. Delete any existing code and paste the contents of [`apps_script/aida_cleaner.gs`](apps_script/aida_cleaner.gs)
4. Click **Save**
5. Go back to your sheet — you'll see a new menu **"AIDA Cleaner"**
6. Click **AIDA Cleaner > Clean & Consolidate Data**
7. Authorize the script when prompted

### What It Does

- Detects each company block (company row + its DM sub-rows)
- Consolidates all DM entries into one row per company (semicolon-separated)
- Keeps empty cells blank for future enrichment
- Outputs clean data to a new sheet called `Cleaned_Data`
- Includes an optional "Clean in Place" mode that overwrites the current sheet

## Project Structure

```
├── main.py                 # FastAPI app — routes, OAuth, API endpoints
├── database.py             # SQLite models and queries
├── enrichment_worker.py    # Async enrichment engine (background task)
├── linkedin_scraper.py     # LinkedIn profile scraper (Playwright)
├── website_scraper.py      # Bulk website email/phone scraper
├── google_maps_scraper.py  # Google Maps business scraper
├── config.py               # Configuration and environment variables
├── requirements.txt        # Python dependencies
├── setup.sh                # VPS deployment script
├── SETUP_OAUTH.md          # Google OAuth setup guide
├── apps_script/
│   └── aida_cleaner.gs     # Google Apps Script for sheet cleaning
├── templates/
│   ├── base.html            # Shared layout + sidebar
│   ├── login.html
│   ├── dashboard.html       # Start enrichment + live progress
│   ├── results.html         # List of past enrichments
│   ├── result_detail.html   # Searchable people table + export
│   ├── settings.html        # Password, workers, max people
│   ├── linkedin.html        # LinkedIn scraper UI
│   ├── linkedin_detail.html
│   ├── scraper.html         # Website scraper UI
│   ├── scraper_detail.html
│   ├── google_maps.html     # Google Maps scraper UI
│   └── google_maps_detail.html
└── data/                    # Auto-created at runtime
    ├── app.db               # SQLite database
    ├── jobs/                # Per-job data
    └── linkedin_cookies/    # Persistent LinkedIn session
```

## Useful Commands

```bash
# Check service status
sudo systemctl status enrichment

# View live logs
sudo journalctl -u enrichment -f

# Restart after changes
sudo systemctl restart enrichment

# Add HTTPS
sudo certbot --nginx -d yourdomain.com
```

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | JWT signing key | `change-this-to-a-random-string-in-production-2024` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | Auto-loaded from `client_secret_*.json` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | Auto-loaded from `client_secret_*.json` |

## License

This project is proprietary. All rights reserved.
