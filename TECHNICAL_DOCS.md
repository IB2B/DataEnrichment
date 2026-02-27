# AIDA — Technical Documentation

Complete technical reference for the AIDA Intelligent Data Enrichment Platform.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack & Dependencies](#tech-stack--dependencies)
- [Module Reference](#module-reference)
- [Database Schema](#database-schema)
- [API Reference](#api-reference)
- [Enrichment Pipeline](#enrichment-pipeline)
- [Scraper Modules](#scraper-modules)
- [Authentication & Security](#authentication--security)
- [Frontend Architecture](#frontend-architecture)
- [Configuration Reference](#configuration-reference)
- [Deployment](#deployment)
- [Proxy Configuration](#proxy-configuration)

---

## Architecture Overview

```
                         ┌─────────────────────────────┐
                         │        Nginx (reverse proxy) │
                         │        SSL termination       │
                         └─────────────┬───────────────┘
                                       │ :80 / :443
                                       ▼
                         ┌─────────────────────────────┐
                         │   Uvicorn ASGI Server        │
                         │   (1 worker, async)          │
                         └─────────────┬───────────────┘
                                       │
                         ┌─────────────▼───────────────┐
                         │       FastAPI Application     │
                         │         (main.py)             │
                         ├───────────────────────────────┤
                         │  Jinja2 SSR  │  REST API      │
                         │  (templates) │  (/api/*)      │
                         └──────┬───────┴──────┬────────┘
                                │              │
              ┌─────────────────┼──────────────┼─────────────────┐
              ▼                 ▼              ▼                 ▼
    ┌──────────────┐  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │  Enrichment  │  │  LinkedIn    │ │  Website     │ │  Google Maps │
    │  Worker      │  │  Scraper     │ │  Scraper     │ │  Scraper     │
    │  (asyncio)   │  │  (Playwright)│ │  (aiohttp)   │ │  (Playwright)│
    └──────┬───────┘  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
           │                 │                │                │
           ▼                 ▼                ▼                ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │                     SQLite Database (WAL mode)                   │
    │                        data/app.db                               │
    └──────────────────────────────────────────────────────────────────┘
           │
           ▼
    ┌──────────────┐
    │ Google Sheets │  (gspread — results written back to sheets)
    └──────────────┘
```

The application is a single-process async Python server. All scraping jobs run as background `asyncio` tasks within the same process. Playwright browser instances are launched on-demand for LinkedIn and Google Maps scraping, using Xvfb as a virtual display on headless servers.

---

## Tech Stack & Dependencies

### Backend

| Package | Version | Purpose |
|---------|---------|---------|
| **fastapi** | 0.133.0 | Async web framework (routing, middleware, dependency injection) |
| **uvicorn[standard]** | 0.41.0 | ASGI server with lifespan support |
| **python-multipart** | 0.0.9 | Form data / file upload parsing |
| **jinja2** | 3.1.4 | Server-side HTML template rendering |
| **pyjwt** | 2.11.0 | JWT token creation and verification |
| **gspread** | 6.1.0 | Google Sheets API client (read/write cells) |
| **google-auth** | 2.35.0 | Google OAuth 2.0 and service account auth |
| **scrapling[all]** | 0.4 | HTTP fetching with stealth headers + HTML parsing (includes aiohttp, BeautifulSoup4, lxml) |
| **httpx** | 0.28.1 | Async HTTP client (used for OAuth token exchange) |
| **playwright** | 1.56.0 | Browser automation (Chromium) for LinkedIn and Google Maps |
| **google-search-results** | 2.4.2 | SerpAPI client for email discovery |

### Frontend

| Technology | Version | Purpose |
|------------|---------|---------|
| **Tailwind CSS** | 3.x (CDN) | Utility-first CSS framework |
| **Google Fonts (Inter)** | Latest | UI typography |
| **Vanilla JavaScript** | ES6+ | Client-side interactivity (polling, forms, modals) |

### Infrastructure

| Component | Purpose |
|-----------|---------|
| **Nginx** | Reverse proxy, SSL termination |
| **systemd** | Process management (enrichment + xvfb services) |
| **certbot** | Let's Encrypt SSL certificates |
| **Xvfb** | Virtual X11 display for headless Playwright |
| **SQLite 3** | Embedded relational database (WAL mode) |

### System Dependencies (Ubuntu)

Required for Playwright/Chromium:

```
libnss3, libnspr4, libatk1.0-0, libatk-bridge2.0-0, libcups2,
libdrm2, libdbus-1-3, libxkbcommon0, libatspi2.0-0, libxcomposite1,
libxdamage1, libxfixes3, libxrandr2, libgbm1, libpango-1.0-0,
libcairo2, libasound2, libwayland-client0, xvfb,
fonts-liberation, fonts-noto-color-emoji
```

---

## Module Reference

### main.py (~1,156 lines)

The FastAPI application entry point. Handles:

- **Route definitions** — all page renders and API endpoints
- **Authentication middleware** — JWT cookie verification on every request
- **Google OAuth 2.0 flow** — redirect, callback, token storage, refresh
- **Job lifecycle** — create, start, poll status, cancel, delete
- **Background task scheduling** — launches enrichment/scraper coroutines via `asyncio.create_task()`
- **PDF/CSV export** — generates downloadable result files
- **Google Sheets browsing** — lists user's sheets and tabs via Drive API

### database.py (~699 lines)

SQLite data access layer. Provides:

- **Schema initialization** — `init_db()` creates all 11 tables and indexes
- **CRUD functions** — `create_job()`, `get_job()`, `update_job()`, `save_results()`, etc.
- **User management** — `create_user()`, `verify_user()`, `change_password()`
- **Settings store** — key-value settings table for app configuration
- **Google token management** — singleton row for OAuth tokens
- **Result pagination** — offset/limit queries with search filtering
- **CSV export helpers** — format results for download

Database is opened in **WAL mode** with foreign keys enabled for concurrent read access during long-running enrichment jobs.

### config.py (~54 lines)

Central configuration loaded from environment variables with sensible defaults:

- File paths (database, credentials, proxies, cookies)
- Authentication settings (secret key, token expiry)
- Scraper defaults (workers, timeouts, delays, limits)
- Google OAuth credentials (auto-loaded from `client_secret_*.json`)

### enrichment_worker.py (~1,094 lines)

The core enrichment engine. See [Enrichment Pipeline](#enrichment-pipeline) for full details.

### linkedin_scraper.py (~1,300+ lines)

LinkedIn profile scraper using Playwright. See [LinkedIn Scraper](#linkedin-scraper-1).

### website_scraper.py (~500+ lines)

Bulk website scraper using aiohttp + BeautifulSoup. See [Website Scraper](#website-scraper-1).

### google_maps_scraper.py (~500+ lines)

Google Maps business scraper using Playwright. See [Google Maps Scraper](#google-maps-scraper-1).

---

## Database Schema

### Entity Relationship

```
users ──1:N──► jobs ──1:N──► results
users ──1:N──► linkedin_scrapes ──1:N──► linkedin_results
users ──1:N──► website_scrapes ──1:N──► website_results
users ──1:N──► google_maps_scrapes ──1:N──► google_maps_results
settings (key-value store)
google_tokens (singleton)
```

### Tables

#### users
| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| email | TEXT | UNIQUE NOT NULL |
| password_hash | TEXT | NOT NULL |
| name | TEXT | DEFAULT '' |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### jobs (Google Sheets enrichment jobs)
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| user_id | INTEGER | FK → users.id |
| sheet_url | TEXT | Original Google Sheet URL |
| sheet_id | TEXT | Google Sheet ID |
| sheet_name | TEXT | Tab name |
| status | TEXT | queued / running / done / error / cancelled |
| total_companies | INTEGER | Total rows to process |
| processed | INTEGER | Rows processed so far |
| found_people | INTEGER | Companies where contacts were found |
| total_people | INTEGER | Total contacts discovered |
| errors | INTEGER | Error count |
| rate | REAL | Companies/second |
| eta | TEXT | Estimated time remaining |
| error_message | TEXT | Error details (if failed) |
| started_at | TEXT | ISO timestamp |
| finished_at | TEXT | ISO timestamp |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### results (enrichment contacts)
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| job_id | INTEGER | FK → jobs.id |
| company_name | TEXT | Source company |
| province | TEXT | Province/region |
| website | TEXT | Company website |
| email | TEXT | Contact email |
| first_name | TEXT | Contact first name |
| last_name | TEXT | Contact last name |
| title | TEXT | Job title |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### linkedin_scrapes
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| user_id | INTEGER | FK → users.id |
| search_url | TEXT | LinkedIn search URL |
| status | TEXT | running / completed / error / stopped |
| total_scraped | INTEGER | Profiles found |
| current_page | INTEGER | Current page being scraped |
| max_pages | INTEGER | Maximum pages to scrape |
| error_message | TEXT | Error details |
| started_at | TEXT | ISO timestamp |
| finished_at | TEXT | ISO timestamp |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### linkedin_results
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| scrape_id | INTEGER | FK → linkedin_scrapes.id (CASCADE DELETE) |
| full_name | TEXT | Profile name |
| job_title | TEXT | Current title |
| company | TEXT | Current company |
| location | TEXT | Location |
| profile_url | TEXT | LinkedIn profile URL |
| email | TEXT | Discovered email |
| phone | TEXT | Phone number |
| website | TEXT | Personal/company website |
| website_email | TEXT | Email from website |
| google_email | TEXT | Email from SerpAPI |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### website_scrapes
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| user_id | INTEGER | FK → users.id |
| urls | TEXT | JSON array of target URLs |
| status | TEXT | running / completed / error |
| total_urls | INTEGER | Total URLs to process |
| processed | INTEGER | URLs processed |
| started_at | TEXT | ISO timestamp |
| finished_at | TEXT | ISO timestamp |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### website_results
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| scrape_id | INTEGER | FK → website_scrapes.id (CASCADE DELETE) |
| url | TEXT | Scraped URL |
| emails | TEXT | JSON array of emails |
| phones | TEXT | JSON array of phone numbers |
| names | TEXT | JSON array of names |
| social_links | TEXT | JSON array of social media URLs |
| logo_url | TEXT | Company logo URL |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### google_maps_scrapes
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| user_id | INTEGER | FK → users.id |
| search_url | TEXT | Google Maps search URL |
| status | TEXT | running / completed / error / stopped |
| total_found | INTEGER | Businesses discovered |
| total_scraped | INTEGER | Businesses with details |
| scrape_emails | INTEGER | Whether to extract emails (0/1) |
| started_at | TEXT | ISO timestamp |
| finished_at | TEXT | ISO timestamp |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### google_maps_results
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| scrape_id | INTEGER | FK → google_maps_scrapes.id (CASCADE DELETE) |
| name | TEXT | Business name |
| category | TEXT | Business category |
| address | TEXT | Full address |
| phone | TEXT | Phone number |
| rating | TEXT | Star rating |
| reviews_count | TEXT | Number of reviews |
| website | TEXT | Business website |
| email | TEXT | Email (from website) |
| google_maps_url | TEXT | Google Maps link |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP |

#### settings
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| key | TEXT | UNIQUE setting name |
| value | TEXT | Setting value |

#### google_tokens
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY (always 1) |
| access_token | TEXT | Google OAuth access token |
| refresh_token | TEXT | Google OAuth refresh token |
| token_expiry | TEXT | ISO timestamp of token expiry |
| google_email | TEXT | Connected Google account email |

### Indexes

| Index | Table | Column |
|-------|-------|--------|
| idx_jobs_status | jobs | status |
| idx_results_job | results | job_id |
| idx_li_results_scrape | linkedin_results | scrape_id |
| idx_ws_results_scrape | website_results | scrape_id |
| idx_gmaps_results_scrape | google_maps_results | scrape_id |

---

## API Reference

All API endpoints require JWT authentication via cookie (except `/login` and `/auth/google/*`).

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/login` | Login page |
| POST | `/login` | Authenticate (email + password) → sets JWT cookie |
| GET | `/logout` | Clears JWT cookie |
| GET | `/auth/google` | Redirects to Google OAuth consent screen |
| GET | `/auth/google/callback` | OAuth callback — stores tokens in DB |

### Dashboard & Enrichment Jobs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Dashboard page |
| POST | `/api/start` | Start enrichment job (body: `sheet_url`, `sheet_name`) |
| GET | `/api/job/{job_id}` | Job status JSON (polled by frontend) |
| POST | `/api/job/{job_id}/cancel` | Cancel a running job |
| POST | `/api/job/{job_id}/delete` | Delete a job and its results |

### Google Sheets

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/sheets` | List user's Google Sheets (via Drive API) |
| GET | `/api/sheets/{sheet_id}/tabs` | List tabs in a specific sheet |

### Results

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/results` | List all enrichment jobs |
| GET | `/results/{job_id}` | Paginated results page (query: `?q=search&page=1`) |
| GET | `/results/{job_id}/export` | Download results as CSV |

### LinkedIn Scraper

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/linkedin` | LinkedIn scraper page |
| POST | `/api/linkedin/start` | Start scrape (body: `search_url`) |
| GET | `/api/linkedin/{scrape_id}` | Scrape status JSON |
| GET | `/linkedin/{scrape_id}` | Results page |
| GET | `/linkedin/{scrape_id}/export` | Export results as CSV |
| POST | `/api/linkedin/manual-login` | Start manual browser login flow |
| GET | `/api/linkedin/manual-login/status` | Check login flow status |
| POST | `/api/linkedin/save-cookie` | Save `li_at` cookie value |
| GET | `/api/linkedin/session-status` | Check if saved session is valid |
| POST | `/api/linkedin/clear-session` | Clear saved LinkedIn session |
| POST | `/api/linkedin/{scrape_id}/stop` | Stop a running scrape |
| POST | `/api/linkedin/{scrape_id}/delete` | Delete scrape and results |

### Website Scraper

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/scraper` | Website scraper page |
| POST | `/api/scraper/start` | Start scrape (body: `urls` textarea) |
| GET | `/api/scraper/{scrape_id}` | Scrape status JSON |
| GET | `/scraper/{scrape_id}` | Results page |
| GET | `/scraper/{scrape_id}/export` | Export results as CSV |
| POST | `/api/scraper/{scrape_id}/delete` | Delete scrape and results |

### Google Maps Scraper

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/google-maps` | Google Maps scraper page |
| POST | `/api/google-maps/start` | Start scrape (body: `search_url`, `scrape_emails`) |
| GET | `/api/google-maps/{scrape_id}` | Scrape status JSON |
| GET | `/google-maps/{scrape_id}` | Results page |
| GET | `/google-maps/{scrape_id}/export` | Export results as CSV |
| POST | `/api/google-maps/{scrape_id}/stop` | Stop a running scrape |
| POST | `/api/google-maps/{scrape_id}/delete` | Delete scrape and results |

### Settings & Admin

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/settings` | Settings page |
| POST | `/settings/password` | Change password |
| POST | `/settings/general` | Update enrichment settings |
| POST | `/settings/linkedin` | Update LinkedIn settings |
| POST | `/api/disconnect-google` | Revoke Google connection |
| GET | `/api/diagnose` | System diagnostics (credentials, packages, connectivity) |

---

## Enrichment Pipeline

The enrichment worker (`enrichment_worker.py`) processes Google Sheet rows through a multi-stage pipeline:

### Stage 1: Setup

1. Load job from database, mark as `running`
2. Connect to Google Sheets (OAuth tokens preferred, service account fallback)
3. Read spreadsheet headers, auto-detect columns: company name, province, website
4. Create output columns (EMAILS, FIRST NAMES, LAST NAMES, TITLES) if missing
5. Build list of companies from sheet rows

### Stage 2: Search Engine Probe

- Tests Bing reachability by searching "Microsoft CEO"
- **Full mode** (Bing available): website search + LinkedIn dorking + website scraping
- **Degraded mode** (Bing down): direct website scraping only

### Stage 3: Per-Company Processing (`process_one`)

Two phases run **in parallel** per company via `asyncio.gather()`:

#### Website Phase
1. If no website URL → search Bing for `"{company} {province} sito ufficiale"`
2. Fetch homepage via Scrapling AsyncFetcher (with proxy rotation)
3. Extract emails/names/titles using 4-phase scraping:
   - **Phase 1 — Structured blocks**: CSS selectors for `div[class*="team"]`, `div[class*="member"]`, etc.
   - **Phase 2 — Name headings**: `<h2>`–`<h5>` / `<strong>` tags containing valid names → check siblings for emails
   - **Phase 3 — Mailto links**: All `<a href="mailto:...">` tags
   - **Phase 4 — Regex scan**: Full-page email regex as fallback
4. Follow up to 2 internal links matching team-related keywords (chi-siamo, contatti, about, team)

#### LinkedIn Phase
- Bing dork query: `site:linkedin.com/in "{company}" (CEO OR Founder OR ...)`
- Parse search result titles/snippets for names and job titles

### Stage 4: Merge & Match (`merge_and_match`)

1. Collect all names from both sources, deduplicate
2. Match emails to names via pattern matching (e.g., `john.smith@` → "John Smith")
3. For unmatched names, guess email as `firstname.lastname@domain`
4. Sort by title priority score (CEO=100, Founder=95, ... Manager=45)
5. Deduplicate and cap at `DEFAULT_MAX_PEOPLE` (default: 5)

### Stage 5: Output

- Save results to app database (`db.save_results`)
- Write results to Google Sheets every 100 companies (comma-separated in 4 columns)
- Update job progress (processed count, rate, ETA) after each batch
- Check for cancellation between batches

### Name Validation

The worker uses a hardcoded database of **740+ known first names** (Italian, English, French, German, Spanish) to validate extracted names. Combined with a blocklist of **200+ non-name words** (cookie, privacy, login, etc.), this prevents junk text from being treated as person names.

### Email Filtering

- **Generic prefixes rejected**: info@, contact@, admin@, support@, hr@, noreply@, etc.
- **Junk domains rejected**: google.com, facebook.com, schema.org, sentry.io, etc.
- **Domain matching**: emails optionally filtered to match the company's website domain

### Title Priority System

Titles are scored from 0–100 to prioritize decision-makers:

| Score | Titles |
|-------|--------|
| 100 | CEO, Amministratore Delegato |
| 95 | Founder, Fondatore |
| 90 | Presidente |
| 85 | Managing Director, Direttore Generale |
| 80 | CTO, CFO, COO, CMO |
| 75 | Direttore Commerciale/Marketing/Tecnico/Vendite |
| 60 | Responsabile Commerciale/Marketing/Vendite |
| 50 | Sales Manager, Export Manager, Account Manager |
| 45 | Head of, Director, Manager |
| 40 | Partner, Socio |

---

## Scraper Modules

### LinkedIn Scraper

**File:** `linkedin_scraper.py` | **Engine:** Playwright (Chromium)

**Authentication:**
- Manual browser login flow (Playwright opens visible browser for user to log in)
- `li_at` cookie paste (user provides cookie value directly)
- Persistent cookie storage in `data/linkedin_cookies/`

**Scraping Flow:**
1. Launch Chromium with saved LinkedIn cookies
2. Navigate to search URL (Sales Navigator or regular search)
3. For each page: extract profile cards via JavaScript DOM queries
4. Parse: full name, job title, company, location, profile URL
5. Optional email enrichment: visit profile page → extract contact info
6. Optional SerpAPI lookup for additional email discovery
7. Optional website crawl for email extraction

**Rate Limiting:**
- Configurable page delays (default: 3–5 seconds between pages)
- Configurable max pages (default: 10, max: 100)

### Website Scraper

**File:** `website_scraper.py` | **Engine:** aiohttp + BeautifulSoup

**Capabilities:**
- Bulk URL processing with concurrent workers
- Email extraction (regex + mailto links)
- Phone number extraction
- Logo detection (Open Graph `og:image`)
- Social media link identification (Facebook, LinkedIn, Twitter, Instagram)
- Contact page detection and priority scraping
- User-Agent rotation for anti-detection

### Google Maps Scraper

**File:** `google_maps_scraper.py` | **Engine:** Playwright (Chromium)

**3-Phase Pipeline:**
1. **Scout phase**: Scroll through Google Maps search results, collect business URLs
2. **Detail phase**: Visit each business page, extract name, address, phone, rating, reviews, website
3. **Email phase** (optional): Visit each business website, scrape emails using aiohttp pool (5 concurrent workers)

**Stealth Features:**
- JavaScript injection to mask WebDriver detection
- Realistic scroll behavior
- Configurable delays between extractions

---

## Authentication & Security

### JWT Authentication

- Tokens issued on login, stored as HTTP-only cookies
- Default expiry: 24 hours (configurable via `TOKEN_EXPIRE_HOURS`)
- Algorithm: HS256
- Secret key: `SECRET_KEY` environment variable

### Google OAuth 2.0

- PKCE flow with CSRF state parameter
- Scopes: `spreadsheets`, `drive.readonly`, `userinfo.email`
- Tokens stored in `google_tokens` table (singleton)
- Automatic refresh when tokens expire

### Password Hashing

- SHA256 hash (via `hashlib`)
- Default admin account created on first run

### Default Credentials

| Field | Value |
|-------|-------|
| Email | `admin@intelligentenrichment.com` |
| Password | `ChangeMe123!` |

---

## Frontend Architecture

### Rendering

All pages are **server-side rendered** using Jinja2 templates. No SPA framework — each page is a full HTML document extended from `base.html`.

### Template Hierarchy

```
base.html (master layout)
├── Sidebar navigation (Dashboard, Results, LinkedIn, Scraper, Google Maps, Settings)
├── Authentication state display
├── Flash messages
└── Content block → filled by child templates
    ├── login.html
    ├── dashboard.html
    ├── results.html / result_detail.html
    ├── settings.html
    ├── linkedin.html / linkedin_detail.html
    ├── scraper.html / scraper_detail.html
    └── google_maps.html / google_maps_detail.html
```

### Styling

- **Tailwind CSS 3** loaded via CDN (`<script src="https://cdn.tailwindcss.com">`)
- Dark theme with indigo accent colors
- Responsive grid layouts
- Google Fonts: Inter

### Client-Side JavaScript

Vanilla JS (no framework) handles:
- **Job polling**: `setInterval` fetches `/api/job/{id}` every 2 seconds to update progress bars
- **Google OAuth redirect**: opens OAuth URL, handles callback
- **Form submissions**: standard HTML forms + fetch for async actions
- **Search/pagination**: query string manipulation for result filtering
- **Dynamic UI**: show/hide elements, modal dialogs

---

## Configuration Reference

All settings are in `config.py` with environment variable overrides:

### File Paths

| Constant | Default | Description |
|----------|---------|-------------|
| `BASE_DIR` | (auto-detected) | Application root directory |
| `DATA_DIR` | `data/` | Runtime data directory |
| `DB_PATH` | `data/app.db` | SQLite database file |
| `CREDS_FILE` | `enrichmentdata.json` | Google service account credentials |
| `PROXY_FILE` | `proxies.txt` | Proxy list file |
| `LINKEDIN_COOKIES_DIR` | `data/linkedin_cookies/` | LinkedIn session storage |

### Authentication

| Constant | Default | Env Variable |
|----------|---------|-------------|
| `SECRET_KEY` | `change-this-to-a-random...` | `SECRET_KEY` |
| `ALGORITHM` | `HS256` | — |
| `TOKEN_EXPIRE_HOURS` | `24` | — |

### Enrichment Defaults

| Constant | Default | Description |
|----------|---------|-------------|
| `MAX_CONCURRENT_JOBS` | `2` | Max simultaneous enrichment jobs |
| `DEFAULT_WORKERS` | `150` | Concurrent HTTP workers per job |
| `DEFAULT_MAX_PEOPLE` | `5` | Max contacts per company |
| `DEFAULT_TIMEOUT` | `4` | HTTP request timeout (seconds) |

### LinkedIn Defaults

| Constant | Default | Description |
|----------|---------|-------------|
| `DEFAULT_PAGE_DELAY_MIN` | `3` | Min seconds between pages |
| `DEFAULT_PAGE_DELAY_MAX` | `5` | Max seconds between pages |
| `DEFAULT_MAX_LINKEDIN_PAGES` | `10` | Default max pages to scrape |

### Google Maps Defaults

| Constant | Default | Description |
|----------|---------|-------------|
| `GMAPS_DEFAULT_CHUNK_SIZE` | `5` | Businesses per detail-scrape batch |
| `GMAPS_DEFAULT_EXTRACT_DELAY` | `1.5` | Seconds between business detail extractions |

### Google OAuth

| Constant | Env Variable | Fallback |
|----------|-------------|----------|
| `GOOGLE_CLIENT_ID` | `GOOGLE_CLIENT_ID` | Auto-loaded from `client_secret_*.json` |
| `GOOGLE_CLIENT_SECRET` | `GOOGLE_CLIENT_SECRET` | Auto-loaded from `client_secret_*.json` |

---

## Deployment

### Prerequisites

- Ubuntu 20.04+ (or any Debian-based Linux)
- Python 3.10+
- 2GB+ RAM (Playwright/Chromium needs ~1GB)
- Open ports: 80, 443

### Automated Deployment

```bash
# Generic VPS
bash setup.sh yourdomain.com

# zmachine.pro specific
bash deploy-zmachine.sh
```

### What the Setup Script Does

1. Installs system dependencies (Python, Chromium libs, Nginx, certbot)
2. Creates `enrichment` system user
3. Sets up app directory at `/opt/enrichment`
4. Creates Python venv, installs pip packages
5. Installs Playwright Chromium browser
6. Creates systemd services:
   - `xvfb.service` — virtual display (:99, 1920x1080x24)
   - `enrichment.service` — Uvicorn on port 8000
7. Configures Nginx reverse proxy
8. Optionally sets up SSL with certbot

### Systemd Services

**xvfb.service:**
```ini
[Service]
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24
Restart=always
```

**enrichment.service:**
```ini
[Service]
User=enrichment
WorkingDirectory=/opt/enrichment
ExecStart=/opt/enrichment/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Environment=SECRET_KEY=<generated> DISPLAY=:99
Restart=always
```

### Management Commands

```bash
sudo systemctl status enrichment    # Check status
sudo journalctl -u enrichment -f    # Live logs
sudo systemctl restart enrichment   # Restart after code changes
sudo certbot --nginx -d domain.com  # Add/renew SSL
```

---

## Proxy Configuration

### Format

Create `proxies.txt` in the app root with one proxy per line:

```
# host:port:username:password (authenticated)
123.45.67.89:8080:user:pass

# host:port (unauthenticated)
123.45.67.89:8080
```

### How Proxies Are Used

- **Enrichment worker**: Scrapling `ProxyRotator` — rotates through proxy list for each HTTP request
- **LinkedIn scraper**: `ProxyPool` — round-robin with shuffle
- **Google Maps scraper**: `ProxyPool` — same round-robin
- **Fallback**: If all proxied requests fail, retries direct (no proxy) as a last resort
- **No proxies**: If `proxies.txt` is missing or empty, all requests go direct

---

## License

This project is proprietary. All rights reserved.
