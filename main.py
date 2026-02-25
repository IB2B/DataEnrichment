"""
Intelligent Enrichment — Web App
FastAPI + Jinja2 + SQLite
"""

import re, asyncio, logging, traceback, json, secrets, shutil
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import jwt

import database as db
import enrichment_worker as worker
import linkedin_scraper
import website_scraper
import google_maps_scraper
from config import (SECRET_KEY, ALGORITHM, TOKEN_EXPIRE_HOURS, MAX_CONCURRENT_JOBS,
                    BASE_DIR, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("enrichment")

app = FastAPI(title="Intelligent Enrichment")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Background job tracking
running_tasks = {}
running_linkedin_task = {}  # Only one at a time
running_scraper_tasks = {}
running_manual_login_task = {}  # For manual LinkedIn login flow
running_gmaps_tasks = {}

# Google OAuth 2.0 constants
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_SCOPES = "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.readonly"


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════

def create_token(user_id: int):
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"user_id": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(request: Request):
    token = request.cookies.get("token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user = db.get_user(payload["user_id"])
        return user
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def require_login(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


# ═══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    db.init_db()
    log.info("Database initialized")
    # Reset any stuck running jobs from previous crash
    for job in db.get_active_jobs():
        if job["status"] == "running":
            db.update_job(job["id"], status="error", error_message="Server restarted",
                          finished_at=datetime.now().isoformat())
            log.info(f"Reset stuck job #{job['id']}")
    # Reset stuck LinkedIn scrapes
    for s in db.get_all_linkedin_scrapes():
        if s["status"] == "running":
            db.update_linkedin_scrape(s["id"], status="error", error_message="Server restarted",
                                      finished_at=datetime.now().isoformat())
    # Reset stuck website scrapes
    for s in db.get_all_website_scrapes():
        if s["status"] == "running":
            db.update_website_scrape(s["id"], status="error", error_message="Server restarted",
                                     finished_at=datetime.now().isoformat())
    # Reset stuck Google Maps scrapes
    for s in db.get_all_google_maps_scrapes():
        if s["status"] == "running":
            db.update_google_maps_scrape(s["id"], status="error", error_message="Server restarted",
                                         finished_at=datetime.now().isoformat())
    asyncio.create_task(job_scheduler())
    log.info("Job scheduler started")


def task_done_callback(job_id, task):
    """Called when a job task finishes — logs errors."""
    try:
        exc = task.exception()
        if exc:
            error_msg = f"{type(exc).__name__}: {str(exc)}"
            log.error(f"Job #{job_id} crashed: {error_msg}")
            log.error(traceback.format_exception(type(exc), exc, exc.__traceback__))
            db.update_job(job_id, status="error", error_message=error_msg[:500],
                          finished_at=datetime.now().isoformat())
    except asyncio.CancelledError:
        log.info(f"Job #{job_id} was cancelled")
    except asyncio.InvalidStateError:
        pass
    finally:
        running_tasks.pop(job_id, None)


async def job_scheduler():
    """Background loop: picks up queued jobs and runs them."""
    while True:
        try:
            running = db.get_running_count()
            if running < MAX_CONCURRENT_JOBS:
                active = db.get_active_jobs()
                queued = [j for j in active if j["status"] == "queued"]
                for job in queued[:MAX_CONCURRENT_JOBS - running]:
                    log.info(f"Starting job #{job['id']} — sheet: {job['sheet_id']}")
                    task = asyncio.create_task(worker.run_enrichment(job["id"]))
                    task.add_done_callback(lambda t, jid=job["id"]: task_done_callback(jid, t))
                    running_tasks[job["id"]] = task
        except Exception as e:
            log.error(f"Scheduler error: {e}")
        await asyncio.sleep(3)


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE OAUTH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_google_redirect_uri(request: Request) -> str:
    """Build the OAuth callback URL from the current request."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}/auth/google/callback"


async def refresh_google_token(tokens: dict) -> Optional[dict]:
    """Refresh the Google OAuth access token using the refresh token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        })
    if resp.status_code != 200:
        log.error(f"Token refresh failed: {resp.text}")
        return None
    data = resp.json()
    new_expiry = (datetime.utcnow() + timedelta(seconds=data["expires_in"])).isoformat()
    db.save_google_tokens(
        access_token=data["access_token"],
        refresh_token=tokens["refresh_token"],
        token_expiry=new_expiry,
        google_email=tokens.get("google_email", ""),
    )
    return db.get_google_tokens()


async def get_valid_google_token() -> Optional[str]:
    """Get a valid (non-expired) Google access token, refreshing if needed."""
    tokens = db.get_google_tokens()
    if not tokens:
        return None
    # Check if expired (with 60s buffer)
    try:
        expiry = datetime.fromisoformat(tokens["token_expiry"])
        if datetime.utcnow() > expiry - timedelta(seconds=60):
            tokens = await refresh_google_token(tokens)
            if not tokens:
                return None
    except (ValueError, KeyError):
        tokens = await refresh_google_token(tokens)
        if not tokens:
            return None
    return tokens["access_token"]


# ═══════════════════════════════════════════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.verify_user(email, password)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})
    token = create_token(user["id"])
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("token", token, httponly=True, max_age=TOKEN_EXPIRE_HOURS * 3600)
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("token")
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, msg: str = "", error: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    jobs = db.get_all_jobs()
    active = [j for j in jobs if j["status"] in ("queued", "running")]
    recent = [j for j in jobs if j["status"] in ("done", "error")][:10]
    google_tokens = db.get_google_tokens()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "active_jobs": active,
        "recent_jobs": recent, "page": "dashboard",
        "google_connected": google_tokens is not None,
        "google_email": google_tokens["google_email"] if google_tokens else "",
        "oauth_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "msg": msg, "error": error,
    })


@app.get("/results", response_class=HTMLResponse)
async def results_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    jobs = db.get_all_jobs()
    done_jobs = [j for j in jobs if j["status"] == "done"]
    for j in done_jobs:
        j["result_count"] = db.get_results_count(j["id"])
    return templates.TemplateResponse("results.html", {
        "request": request, "user": user, "jobs": done_jobs, "page": "results"
    })


@app.get("/results/{job_id}", response_class=HTMLResponse)
async def result_detail(request: Request, job_id: int, search: str = "", page: int = 1):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    job = db.get_job(job_id)
    if not job:
        return RedirectResponse("/results", status_code=302)
    per_page = 10
    if page < 1:
        page = 1
    offset = (page - 1) * per_page
    total = db.get_results_count(job_id, search=search)
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * per_page
    results = db.get_results(job_id, search=search, limit=per_page, offset=offset)
    return templates.TemplateResponse("result_detail.html", {
        "request": request, "user": user, "job": job, "results": results,
        "total": total, "search": search, "page": "results",
        "current_page": page, "total_pages": total_pages,
    })


@app.get("/results/{job_id}/export")
async def export_csv(request: Request, job_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    csv_data = db.get_results_csv(job_id)
    return StreamingResponse(
        iter([csv_data]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=enrichment_{job_id}.csv"}
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, msg: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    settings = {
        "max_people": db.get_setting("max_people", "5"),
        "workers": db.get_setting("workers", "150"),
        "default_sheet": db.get_setting("default_sheet", "Cleaned_Data"),
        "linkedin_email": db.get_setting("linkedin_email", ""),
        "linkedin_password": db.get_setting("linkedin_password", ""),
        "page_delay_min": db.get_setting("page_delay_min", "3"),
        "page_delay_max": db.get_setting("page_delay_max", "5"),
    }
    google_tokens = db.get_google_tokens()
    return templates.TemplateResponse("settings.html", {
        "request": request, "user": user, "settings": settings,
        "msg": msg, "page": "settings",
        "google_connected": google_tokens is not None,
        "google_email": google_tokens["google_email"] if google_tokens else "",
    })


@app.post("/settings/password")
async def change_password(request: Request, current_password: str = Form(...),
                          new_password: str = Form(...), confirm_password: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if new_password != confirm_password:
        return RedirectResponse("/settings?msg=Passwords+don't+match", status_code=302)
    if not db.verify_user(user["email"], current_password):
        return RedirectResponse("/settings?msg=Current+password+is+wrong", status_code=302)
    db.change_password(user["id"], new_password)
    return RedirectResponse("/settings?msg=Password+changed+successfully", status_code=302)


@app.post("/settings/general")
async def save_settings(request: Request, max_people: str = Form("5"),
                        workers: str = Form("150"), default_sheet: str = Form("Cleaned_Data")):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db.set_setting("max_people", max_people)
    db.set_setting("workers", workers)
    db.set_setting("default_sheet", default_sheet)
    return RedirectResponse("/settings?msg=Settings+saved", status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE OAUTH ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/auth/google")
async def google_auth_redirect(request: Request):
    """Redirects to Google OAuth consent screen."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return RedirectResponse("/?error=Google+OAuth+not+configured", status_code=302)

    state = secrets.token_urlsafe(32)
    # Store state in a cookie for CSRF validation
    redirect_uri = get_google_redirect_uri(request)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    response = RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=302)
    response.set_cookie("oauth_state", state, httponly=True, max_age=600)
    return response


@app.get("/auth/google/callback")
async def google_auth_callback(request: Request, code: str = "", error: str = "", state: str = ""):
    """Handles Google OAuth callback — exchanges code for tokens."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    if error:
        return RedirectResponse(f"/?error=Google+auth+denied:+{error}", status_code=302)

    # Validate state
    saved_state = request.cookies.get("oauth_state", "")
    if not state or state != saved_state:
        return RedirectResponse("/?error=Invalid+OAuth+state", status_code=302)

    redirect_uri = get_google_redirect_uri(request)

    # Exchange authorization code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })

    if token_resp.status_code != 200:
        log.error(f"Google token exchange failed: {token_resp.text}")
        return RedirectResponse("/?error=Google+token+exchange+failed", status_code=302)

    token_data = token_resp.json()
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    token_expiry = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()

    # Fetch user info (email)
    google_email = ""
    async with httpx.AsyncClient() as client:
        info_resp = await client.get(GOOGLE_USERINFO_URL,
                                     headers={"Authorization": f"Bearer {access_token}"})
        if info_resp.status_code == 200:
            google_email = info_resp.json().get("email", "")

    # Store tokens in database
    db.save_google_tokens(access_token, refresh_token, token_expiry, google_email)
    log.info(f"Google OAuth connected: {google_email}")

    response = RedirectResponse("/?msg=Google+account+connected", status_code=302)
    response.delete_cookie("oauth_state")
    return response


@app.post("/api/disconnect-google")
async def disconnect_google(request: Request):
    """Revokes and clears stored Google OAuth tokens."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)

    tokens = db.get_google_tokens()
    if tokens:
        # Try to revoke the token at Google
        try:
            async with httpx.AsyncClient() as client:
                await client.post(GOOGLE_REVOKE_URL,
                                  params={"token": tokens["access_token"]})
        except Exception:
            pass  # Best-effort revocation
        db.delete_google_tokens()
        log.info("Google OAuth disconnected")

    return JSONResponse({"ok": True})


# ═══════════════════════════════════════════════════════════════════════════════
# SHEETS API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/sheets")
async def list_sheets(request: Request, q: str = ""):
    """Returns JSON list of user's Google Sheets (via Drive API)."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)

    access_token = await get_valid_google_token()
    if not access_token:
        raise HTTPException(401, detail="Google account not connected")

    # Search for spreadsheet files via Drive API
    query = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    if q:
        query += f" and name contains '{q}'"
    params = {
        "q": query,
        "orderBy": "modifiedTime desc",
        "pageSize": 50,
        "fields": "files(id,name,modifiedTime,owners)",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )

    if resp.status_code == 401:
        # Token expired and refresh failed
        db.delete_google_tokens()
        raise HTTPException(401, detail="Google token expired, please reconnect")

    if resp.status_code != 200:
        raise HTTPException(502, detail=f"Google Drive API error: {resp.status_code}")

    files = resp.json().get("files", [])
    sheets = []
    for f in files:
        sheets.append({
            "id": f["id"],
            "name": f["name"],
            "lastModified": f.get("modifiedTime", ""),
        })
    return sheets


@app.get("/api/sheets/{sheet_id}/tabs")
async def get_sheet_tabs(request: Request, sheet_id: str):
    """Returns JSON list of tab names for a selected Google Sheet."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)

    access_token = await get_valid_google_token()
    if not access_token:
        raise HTTPException(401, detail="Google account not connected")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "sheets.properties.title"},
        )

    if resp.status_code == 401:
        db.delete_google_tokens()
        raise HTTPException(401, detail="Google token expired, please reconnect")

    if resp.status_code != 200:
        raise HTTPException(502, detail=f"Google Sheets API error: {resp.status_code}")

    data = resp.json()
    tabs = [s["properties"]["title"] for s in data.get("sheets", [])]
    return tabs


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

def extract_sheet_id(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else ""


@app.post("/api/start")
async def start_enrichment(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)

    form = await request.form()

    # New OAuth flow: sheet_id + tab come from dropdowns
    sheet_id = form.get("sheet_id", "").strip()
    sheet_name = form.get("sheet_name", "").strip() or form.get("sheet_tab", "").strip()

    # Legacy fallback: raw URL
    if not sheet_id:
        sheet_url = form.get("sheet_url", "").strip()
        if sheet_url:
            sheet_id = extract_sheet_id(sheet_url)

    if not sheet_id:
        return RedirectResponse("/?error=No+sheet+selected", status_code=302)
    if not sheet_name:
        sheet_name = "Cleaned_Data"

    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    job_id = db.create_job(user["id"], sheet_url, sheet_id, sheet_name)
    return RedirectResponse("/", status_code=302)


@app.get("/api/job/{job_id}")
async def job_status(request: Request, job_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    return job


@app.post("/api/job/{job_id}/cancel")
async def cancel_job(request: Request, job_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    db.update_job(job_id, status="cancelled", finished_at=datetime.now().isoformat())
    if job_id in running_tasks:
        running_tasks[job_id].cancel()
        del running_tasks[job_id]
    return RedirectResponse("/", status_code=302)


@app.post("/api/job/{job_id}/delete")
async def delete_job_route(request: Request, job_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    db.delete_job(job_id)
    referer = request.headers.get("referer", "/results")
    return RedirectResponse(referer, status_code=302)


@app.post("/settings/linkedin")
async def save_linkedin_settings(request: Request,
                                  linkedin_email: str = Form(""),
                                  linkedin_password: str = Form(""),
                                  page_delay_min: str = Form("3"),
                                  page_delay_max: str = Form("5")):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db.set_setting("linkedin_email", linkedin_email)
    db.set_setting("linkedin_password", linkedin_password)
    db.set_setting("page_delay_min", page_delay_min)
    db.set_setting("page_delay_max", page_delay_max)
    return RedirectResponse("/settings?msg=LinkedIn+settings+saved", status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# LINKEDIN SCRAPER PAGES + API
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/linkedin", response_class=HTMLResponse)
async def linkedin_page(request: Request, error: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    all_scrapes = db.get_all_linkedin_scrapes()
    active = None
    scrapes = []
    for s in all_scrapes:
        if s["status"] == "running":
            active = s
        else:
            scrapes.append(s)
    return templates.TemplateResponse("linkedin.html", {
        "request": request, "user": user, "page": "linkedin",
        "active": active, "scrapes": scrapes[:20], "error": error,
    })


@app.get("/linkedin/{scrape_id}", response_class=HTMLResponse)
async def linkedin_detail(request: Request, scrape_id: int, search: str = "", page: int = 1):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    scrape = db.get_linkedin_scrape(scrape_id)
    if not scrape:
        return RedirectResponse("/linkedin", status_code=302)
    per_page = 10
    if page < 1:
        page = 1
    total = db.get_linkedin_results_count(scrape_id, search=search)
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    results = db.get_linkedin_results(scrape_id, search=search, limit=per_page, offset=offset)
    return templates.TemplateResponse("linkedin_detail.html", {
        "request": request, "user": user, "page": "linkedin",
        "scrape": scrape, "results": results, "total": total, "search": search,
        "current_page": page, "total_pages": total_pages,
    })


@app.get("/linkedin/{scrape_id}/export")
async def linkedin_export(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    csv_data = db.get_linkedin_results_csv(scrape_id)
    return StreamingResponse(
        iter([csv_data]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=linkedin_{scrape_id}.csv"})


@app.post("/api/linkedin/start")
async def start_linkedin_scrape(request: Request, search_url: str = Form(...),
                                 max_pages: int = Form(10)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    # Only allow one LinkedIn scrape at a time
    if running_linkedin_task:
        return RedirectResponse("/linkedin?error=A+LinkedIn+scrape+is+already+running", status_code=302)
    if not search_url.strip():
        return RedirectResponse("/linkedin?error=Please+enter+a+search+URL", status_code=302)
    max_pages = max(1, min(max_pages, 100))
    scrape_id = db.create_linkedin_scrape(user["id"], search_url.strip(), max_pages)
    task = asyncio.create_task(linkedin_scraper.run_linkedin_scrape(scrape_id))
    running_linkedin_task[scrape_id] = task

    def _done(t, sid=scrape_id):
        running_linkedin_task.pop(sid, None)
        try:
            exc = t.exception()
            if exc:
                db.update_linkedin_scrape(sid, status="error",
                    error_message=f"{type(exc).__name__}: {str(exc)}"[:500],
                    finished_at=datetime.now().isoformat())
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass

    task.add_done_callback(_done)
    return RedirectResponse("/linkedin", status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# LINKEDIN MANUAL LOGIN (SESSION MANAGEMENT)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/linkedin/manual-login")
async def start_manual_login(request: Request):
    """Opens a headed browser for manual LinkedIn login."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    # Prevent duplicate
    if running_manual_login_task.get("task") and not running_manual_login_task["task"].done():
        return JSONResponse({"ok": False, "error": "Manual login already in progress"})

    status = {"status": "starting", "message": "Starting browser..."}
    running_manual_login_task["status"] = status
    task = asyncio.create_task(linkedin_scraper.run_manual_login(status))
    running_manual_login_task["task"] = task

    def _done(t):
        try:
            exc = t.exception()
            if exc:
                status["status"] = "error"
                status["message"] = f"Error: {type(exc).__name__}: {str(exc)}"
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass

    task.add_done_callback(_done)
    return JSONResponse({"ok": True})


@app.get("/api/linkedin/manual-login/status")
async def manual_login_status(request: Request):
    """Polling endpoint for manual login progress."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    status = running_manual_login_task.get("status", {})
    task = running_manual_login_task.get("task")
    # If there's no active task, report idle regardless of stale status
    if not task or task.done():
        final_status = status.get("status", "idle")
        # Only return done/error once, then reset to idle
        if final_status in ("done", "error"):
            msg = status.get("message", "")
            running_manual_login_task.pop("status", None)
            return JSONResponse({"status": final_status, "message": msg})
        return JSONResponse({"status": "idle", "message": ""})
    return JSONResponse({
        "status": status.get("status", "idle"),
        "message": status.get("message", ""),
    })


@app.get("/api/linkedin/session-status")
async def linkedin_session_status(request: Request):
    """Checks if saved LinkedIn cookies exist (database or file-based)."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    # Check database cookie first
    li_at = db.get_setting("linkedin_li_at", "")
    if li_at:
        return JSONResponse({"has_session": True})
    # Fallback: check file-based cookies
    from config import LINKEDIN_COOKIES_DIR
    has_session = LINKEDIN_COOKIES_DIR.exists() and any(LINKEDIN_COOKIES_DIR.iterdir())
    return JSONResponse({"has_session": has_session})


@app.post("/api/linkedin/save-cookie")
async def save_linkedin_cookie(request: Request):
    """Save a li_at cookie value to the database. The scraper injects it at runtime."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    try:
        body = await request.json()
        li_at = body.get("li_at", "").strip()
        if not li_at:
            return JSONResponse({"ok": False, "error": "Cookie value is empty"})
        if len(li_at) < 50:
            return JSONResponse({"ok": False, "error": "Cookie value looks too short. Make sure you copied the full li_at value."})

        # Save to database — scraper will inject it at runtime
        db.set_setting("linkedin_li_at", li_at)

        # Clear any old browser cookies so the scraper starts fresh with the new cookie
        from config import LINKEDIN_COOKIES_DIR
        if LINKEDIN_COOKIES_DIR.exists():
            shutil.rmtree(LINKEDIN_COOKIES_DIR, ignore_errors=True)
            LINKEDIN_COOKIES_DIR.mkdir(exist_ok=True)

        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Error: {str(e)}"})


@app.post("/api/linkedin/clear-session")
async def clear_linkedin_session(request: Request):
    """Deletes saved LinkedIn cookies to force re-login."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    # Clear database cookie
    db.set_setting("linkedin_li_at", "")
    # Clear file-based cookies
    from config import LINKEDIN_COOKIES_DIR
    if LINKEDIN_COOKIES_DIR.exists():
        shutil.rmtree(LINKEDIN_COOKIES_DIR, ignore_errors=True)
        LINKEDIN_COOKIES_DIR.mkdir(exist_ok=True)
    return JSONResponse({"ok": True})


@app.get("/api/linkedin/{scrape_id}")
async def linkedin_status(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    scrape = db.get_linkedin_scrape(scrape_id)
    if not scrape:
        raise HTTPException(404)
    return scrape


@app.post("/api/linkedin/{scrape_id}/stop")
async def stop_linkedin_scrape(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    db.update_linkedin_scrape(scrape_id, status="stopped", finished_at=datetime.now().isoformat())
    if scrape_id in running_linkedin_task:
        running_linkedin_task[scrape_id].cancel()
        running_linkedin_task.pop(scrape_id, None)
    return RedirectResponse("/linkedin", status_code=302)


@app.post("/api/linkedin/{scrape_id}/delete")
async def delete_linkedin_scrape_route(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    if scrape_id in running_linkedin_task:
        running_linkedin_task[scrape_id].cancel()
        running_linkedin_task.pop(scrape_id, None)
    db.delete_linkedin_scrape(scrape_id)
    referer = request.headers.get("referer", "/linkedin")
    return RedirectResponse(referer, status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSITE SCRAPER PAGES + API
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/scraper", response_class=HTMLResponse)
async def scraper_page(request: Request, error: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    all_scrapes = db.get_all_website_scrapes()
    active = None
    scrapes = []
    for s in all_scrapes:
        if s["status"] == "running":
            active = s
        else:
            scrapes.append(s)
    return templates.TemplateResponse("scraper.html", {
        "request": request, "user": user, "page": "scraper",
        "active": active, "scrapes": scrapes[:20], "error": error,
    })


@app.get("/scraper/{scrape_id}", response_class=HTMLResponse)
async def scraper_detail(request: Request, scrape_id: int, search: str = "", page: int = 1):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    scrape = db.get_website_scrape(scrape_id)
    if not scrape:
        return RedirectResponse("/scraper", status_code=302)
    per_page = 10
    if page < 1:
        page = 1
    total = db.get_website_results_count(scrape_id, search=search)
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    results = db.get_website_results(scrape_id, search=search, limit=per_page, offset=offset)
    return templates.TemplateResponse("scraper_detail.html", {
        "request": request, "user": user, "page": "scraper",
        "scrape": scrape, "results": results, "total": total, "search": search,
        "current_page": page, "total_pages": total_pages,
    })


@app.get("/scraper/{scrape_id}/export")
async def scraper_export(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    csv_data = db.get_website_results_csv(scrape_id)
    return StreamingResponse(
        iter([csv_data]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=scrape_{scrape_id}.csv"})


@app.post("/api/scraper/start")
async def start_website_scrape(request: Request, urls: str = Form(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    url_list = [u.strip() for u in urls.strip().splitlines() if u.strip()]
    if not url_list:
        return RedirectResponse("/scraper?error=Please+enter+at+least+one+URL", status_code=302)
    urls_json = json.dumps(url_list)
    scrape_id = db.create_website_scrape(user["id"], urls_json, len(url_list))
    task = asyncio.create_task(website_scraper.run_website_scrape(scrape_id))
    running_scraper_tasks[scrape_id] = task

    def _done(t, sid=scrape_id):
        running_scraper_tasks.pop(sid, None)
        try:
            exc = t.exception()
            if exc:
                db.update_website_scrape(sid, status="error",
                    error_message=f"{type(exc).__name__}: {str(exc)}"[:500],
                    finished_at=datetime.now().isoformat())
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass

    task.add_done_callback(_done)
    return RedirectResponse("/scraper", status_code=302)


@app.get("/api/scraper/{scrape_id}")
async def scraper_status(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    scrape = db.get_website_scrape(scrape_id)
    if not scrape:
        raise HTTPException(404)
    return scrape


@app.post("/api/scraper/{scrape_id}/delete")
async def delete_website_scrape_route(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    if scrape_id in running_scraper_tasks:
        running_scraper_tasks[scrape_id].cancel()
        running_scraper_tasks.pop(scrape_id, None)
    db.delete_website_scrape(scrape_id)
    referer = request.headers.get("referer", "/scraper")
    return RedirectResponse(referer, status_code=302)


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE MAPS SCRAPER PAGES + API
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/google-maps", response_class=HTMLResponse)
async def google_maps_page(request: Request, error: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    all_scrapes = db.get_all_google_maps_scrapes()
    active = None
    scrapes = []
    for s in all_scrapes:
        if s["status"] == "running":
            active = s
        else:
            scrapes.append(s)
    return templates.TemplateResponse("google_maps.html", {
        "request": request, "user": user, "page": "google_maps",
        "active": active, "scrapes": scrapes[:20], "error": error,
    })


@app.post("/api/google-maps/start")
async def start_google_maps_scrape(request: Request, search_url: str = Form(...),
                                    scrape_emails: str = Form("")):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    if running_gmaps_tasks:
        return RedirectResponse("/google-maps?error=A+Google+Maps+scrape+is+already+running", status_code=302)
    if not search_url.strip():
        return RedirectResponse("/google-maps?error=Please+enter+a+search+URL", status_code=302)
    do_emails = 1 if scrape_emails else 0
    scrape_id = db.create_google_maps_scrape(user["id"], search_url.strip(), do_emails)
    task = asyncio.create_task(google_maps_scraper.run_google_maps_scrape(scrape_id))
    running_gmaps_tasks[scrape_id] = task

    def _done(t, sid=scrape_id):
        running_gmaps_tasks.pop(sid, None)
        try:
            exc = t.exception()
            if exc:
                db.update_google_maps_scrape(sid, status="error",
                    error_message=f"{type(exc).__name__}: {str(exc)}"[:500],
                    finished_at=datetime.now().isoformat())
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass

    task.add_done_callback(_done)
    return RedirectResponse("/google-maps", status_code=302)


@app.get("/google-maps/{scrape_id}", response_class=HTMLResponse)
async def google_maps_detail(request: Request, scrape_id: int, search: str = "", page: int = 1):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    scrape = db.get_google_maps_scrape(scrape_id)
    if not scrape:
        return RedirectResponse("/google-maps", status_code=302)
    per_page = 10
    if page < 1:
        page = 1
    total = db.get_google_maps_results_count(scrape_id, search=search)
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    results = db.get_google_maps_results(scrape_id, search=search, limit=per_page, offset=offset)
    return templates.TemplateResponse("google_maps_detail.html", {
        "request": request, "user": user, "page": "google_maps",
        "scrape": scrape, "results": results, "total": total, "search": search,
        "current_page": page, "total_pages": total_pages,
    })


@app.get("/google-maps/{scrape_id}/export")
async def google_maps_export(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    csv_data = db.get_google_maps_results_csv(scrape_id)
    return StreamingResponse(
        iter([csv_data]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=google_maps_{scrape_id}.csv"})


@app.get("/api/google-maps/{scrape_id}")
async def google_maps_status(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    scrape = db.get_google_maps_scrape(scrape_id)
    if not scrape:
        raise HTTPException(404)
    return scrape


@app.post("/api/google-maps/{scrape_id}/stop")
async def stop_google_maps_scrape(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    db.update_google_maps_scrape(scrape_id, status="stopped", finished_at=datetime.now().isoformat())
    if scrape_id in running_gmaps_tasks:
        running_gmaps_tasks[scrape_id].cancel()
        running_gmaps_tasks.pop(scrape_id, None)
    return RedirectResponse("/google-maps", status_code=302)


@app.post("/api/google-maps/{scrape_id}/delete")
async def delete_google_maps_scrape_route(request: Request, scrape_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    if scrape_id in running_gmaps_tasks:
        running_gmaps_tasks[scrape_id].cancel()
        running_gmaps_tasks.pop(scrape_id, None)
    db.delete_google_maps_scrape(scrape_id)
    referer = request.headers.get("referer", "/google-maps")
    return RedirectResponse(referer, status_code=302)


@app.get("/api/diagnose")
async def diagnose(request: Request):
    """Diagnostic endpoint — checks credentials file, Google connection, etc."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)
    from config import CREDS_FILE, PROXY_FILE
    from pathlib import Path
    checks = {}

    # Check credentials file
    creds_path = Path(CREDS_FILE)
    checks["creds_file_exists"] = creds_path.exists()
    if creds_path.exists():
        try:
            creds_data = json.loads(creds_path.read_text())
            checks["service_account_email"] = creds_data.get("client_email", "NOT FOUND")
            checks["project_id"] = creds_data.get("project_id", "NOT FOUND")
        except Exception as e:
            checks["creds_parse_error"] = str(e)

    # Check proxy file
    proxy_path = Path(PROXY_FILE)
    checks["proxy_file_exists"] = proxy_path.exists()
    if proxy_path.exists():
        lines = [l.strip() for l in proxy_path.read_text().strip().split() if l.strip()]
        checks["proxy_count"] = len(lines)

    # Check Google OAuth
    google_tokens = db.get_google_tokens()
    checks["google_oauth_connected"] = google_tokens is not None
    if google_tokens:
        checks["google_oauth_email"] = google_tokens.get("google_email", "")

    # Try Google auth (service account)
    try:
        from google.oauth2.service_account import Credentials
        import gspread
        SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
        gc = gspread.authorize(creds)
        checks["google_auth"] = "OK"
    except Exception as e:
        checks["google_auth"] = f"FAILED: {e}"

    # Check installed packages
    for pkg in ["gspread", "aiohttp", "bs4", "lxml", "httpx"]:
        try:
            __import__(pkg)
            checks[f"pkg_{pkg}"] = "installed"
        except ImportError:
            checks[f"pkg_{pkg}"] = "MISSING"

    return checks


# ═══════════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
