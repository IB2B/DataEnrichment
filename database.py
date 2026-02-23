import sqlite3, json, hashlib, time
from datetime import datetime
from config import DB_PATH, DEFAULT_EMAIL, DEFAULT_PASSWORD


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            sheet_url TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            sheet_name TEXT DEFAULT 'Cleaned_Data',
            status TEXT DEFAULT 'queued',
            total_companies INTEGER DEFAULT 0,
            processed INTEGER DEFAULT 0,
            found_people INTEGER DEFAULT 0,
            total_people INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            rate REAL DEFAULT 0,
            eta TEXT DEFAULT '',
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            error_message TEXT DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            company_name TEXT DEFAULT '',
            province TEXT DEFAULT '',
            website TEXT DEFAULT '',
            email TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            title TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_results_job ON results(job_id);

        CREATE TABLE IF NOT EXISTS linkedin_scrapes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            search_url TEXT,
            status TEXT DEFAULT 'running',
            total_scraped INTEGER DEFAULT 0,
            current_page INTEGER DEFAULT 0,
            max_pages INTEGER DEFAULT 0,
            started_at TEXT,
            finished_at TEXT,
            error_message TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS linkedin_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scrape_id INTEGER NOT NULL,
            full_name TEXT DEFAULT '',
            job_title TEXT DEFAULT '',
            company TEXT DEFAULT '',
            location TEXT DEFAULT '',
            profile_url TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            website TEXT DEFAULT '',
            website_email TEXT DEFAULT '',
            google_email TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (scrape_id) REFERENCES linkedin_scrapes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_li_results_scrape ON linkedin_results(scrape_id);

        CREATE TABLE IF NOT EXISTS website_scrapes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            urls TEXT,
            status TEXT DEFAULT 'running',
            total_urls INTEGER DEFAULT 0,
            processed INTEGER DEFAULT 0,
            started_at TEXT,
            finished_at TEXT,
            error_message TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS website_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scrape_id INTEGER NOT NULL,
            url TEXT DEFAULT '',
            emails TEXT DEFAULT '',
            phones TEXT DEFAULT '',
            names TEXT DEFAULT '',
            social_links TEXT DEFAULT '',
            logo_url TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (scrape_id) REFERENCES website_scrapes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_ws_results_scrape ON website_results(scrape_id);

        CREATE TABLE IF NOT EXISTS google_maps_scrapes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            search_url TEXT,
            status TEXT DEFAULT 'running',
            total_found INTEGER DEFAULT 0,
            total_scraped INTEGER DEFAULT 0,
            scrape_emails INTEGER DEFAULT 0,
            error_message TEXT DEFAULT '',
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS google_maps_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scrape_id INTEGER NOT NULL,
            name TEXT DEFAULT '',
            category TEXT DEFAULT '',
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            rating TEXT DEFAULT '',
            reviews_count TEXT DEFAULT '',
            website TEXT DEFAULT '',
            email TEXT DEFAULT '',
            google_maps_url TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (scrape_id) REFERENCES google_maps_scrapes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_gmaps_results_scrape ON google_maps_results(scrape_id);

        CREATE TABLE IF NOT EXISTS google_tokens (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            token_expiry TEXT NOT NULL,
            google_email TEXT DEFAULT ''
        );
    """)
    conn.commit()

    # Migrations — add columns to existing tables
    try:
        conn.execute("ALTER TABLE website_results ADD COLUMN logo_url TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # Column already exists

    for col in ("email", "phone", "website", "website_email", "google_email"):
        try:
            conn.execute(f"ALTER TABLE linkedin_results ADD COLUMN {col} TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass  # Column already exists

    # Create default admin account if not exists
    existing = conn.execute("SELECT id FROM users WHERE email=?", (DEFAULT_EMAIL,)).fetchone()
    if not existing:
        pw_hash = hash_password(DEFAULT_PASSWORD)
        conn.execute("INSERT INTO users (email, password_hash, name) VALUES (?,?,?)",
                     (DEFAULT_EMAIL, pw_hash, "Admin"))
        conn.commit()
        print(f"   Created default account: {DEFAULT_EMAIL} / {DEFAULT_PASSWORD}")

    conn.close()


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def verify_user(email, password):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    if user and user["password_hash"] == hash_password(password):
        return dict(user)
    return None


def get_user(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def change_password(user_id, new_password):
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (hash_password(new_password), user_id))
    conn.commit()
    conn.close()


# ─── Settings ───

def get_setting(key, default=""):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()


# ─── Jobs ───

def create_job(user_id, sheet_url, sheet_id, sheet_name):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO jobs (user_id, sheet_url, sheet_id, sheet_name) VALUES (?,?,?,?)",
        (user_id, sheet_url, sheet_id, sheet_name))
    job_id = cur.lastrowid
    conn.commit()
    conn.close()
    return job_id


def get_job(job_id):
    conn = get_db()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return dict(job) if job else None


def get_all_jobs():
    conn = get_db()
    jobs = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(j) for j in jobs]


def get_active_jobs():
    conn = get_db()
    jobs = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(j) for j in jobs]


def get_running_count():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE status='running'").fetchone()["c"]
    conn.close()
    return count


def update_job(job_id, **kwargs):
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def delete_job(job_id):
    conn = get_db()
    conn.execute("DELETE FROM results WHERE job_id=?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    conn.commit()
    conn.close()


# ─── Results ───

def save_results(job_id, people_list):
    """Save a batch of people results. Each item: {company_name, province, website, people: [...]}"""
    conn = get_db()
    rows = []
    for company in people_list:
        for person in company.get("people", []):
            rows.append((
                job_id,
                company.get("company_name", ""),
                company.get("province", ""),
                company.get("website", ""),
                person.get("email", ""),
                person.get("first_name", ""),
                person.get("last_name", ""),
                person.get("title", ""),
            ))
    if rows:
        conn.executemany(
            "INSERT INTO results (job_id, company_name, province, website, email, first_name, last_name, title) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    conn.close()
    return len(rows)


def get_results(job_id, search="", limit=500, offset=0):
    conn = get_db()
    if search:
        like = f"%{search}%"
        rows = conn.execute(
            "SELECT * FROM results WHERE job_id=? AND "
            "(company_name LIKE ? OR email LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR title LIKE ?) "
            "ORDER BY id LIMIT ? OFFSET ?",
            (job_id, like, like, like, like, like, limit, offset)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM results WHERE job_id=? ORDER BY id LIMIT ? OFFSET ?",
            (job_id, limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_results_count(job_id, search=""):
    conn = get_db()
    if search:
        like = f"%{search}%"
        count = conn.execute(
            "SELECT COUNT(*) as c FROM results WHERE job_id=? AND "
            "(company_name LIKE ? OR email LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR title LIKE ?)",
            (job_id, like, like, like, like, like)).fetchone()["c"]
    else:
        count = conn.execute("SELECT COUNT(*) as c FROM results WHERE job_id=?", (job_id,)).fetchone()["c"]
    conn.close()
    return count


def get_results_csv(job_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT company_name, province, website, email, first_name, last_name, title "
        "FROM results WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    conn.close()
    lines = ["Company,Province,Website,Email,First Name,Last Name,Title"]
    for r in rows:
        line = ",".join(f'"{v}"' for v in [r["company_name"], r["province"], r["website"],
                                            r["email"], r["first_name"], r["last_name"], r["title"]])
        lines.append(line)
    return "\n".join(lines)


# ─── Google OAuth Tokens ───

def save_google_tokens(access_token, refresh_token, token_expiry, google_email=""):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO google_tokens (id, access_token, refresh_token, token_expiry, google_email) "
        "VALUES (1, ?, ?, ?, ?)",
        (access_token, refresh_token, token_expiry, google_email))
    conn.commit()
    conn.close()


def get_google_tokens():
    conn = get_db()
    row = conn.execute("SELECT * FROM google_tokens WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else None


def delete_google_tokens():
    conn = get_db()
    conn.execute("DELETE FROM google_tokens WHERE id=1")
    conn.commit()
    conn.close()


# ─── LinkedIn Scrapes ───

def create_linkedin_scrape(user_id, search_url, max_pages):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO linkedin_scrapes (user_id, search_url, max_pages, started_at) VALUES (?,?,?,?)",
        (user_id, search_url, max_pages, datetime.now().isoformat()))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def get_linkedin_scrape(scrape_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM linkedin_scrapes WHERE id=?", (scrape_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_linkedin_scrapes():
    conn = get_db()
    rows = conn.execute("SELECT * FROM linkedin_scrapes ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_linkedin_scrape(scrape_id, **kwargs):
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [scrape_id]
    conn.execute(f"UPDATE linkedin_scrapes SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def delete_linkedin_scrape(scrape_id):
    conn = get_db()
    conn.execute("DELETE FROM linkedin_results WHERE scrape_id=?", (scrape_id,))
    conn.execute("DELETE FROM linkedin_scrapes WHERE id=?", (scrape_id,))
    conn.commit()
    conn.close()


def save_linkedin_results(scrape_id, people):
    conn = get_db()
    rows = [(scrape_id, p.get("full_name",""), p.get("job_title",""), p.get("company",""),
             p.get("location",""), p.get("profile_url",""),
             p.get("email",""), p.get("phone",""), p.get("website",""),
             p.get("website_email",""), p.get("google_email","")) for p in people]
    if rows:
        conn.executemany(
            "INSERT INTO linkedin_results (scrape_id, full_name, job_title, company, location, profile_url, "
            "email, phone, website, website_email, google_email) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    conn.close()
    return len(rows)


def get_linkedin_results(scrape_id, search="", limit=1000, offset=0):
    conn = get_db()
    if search:
        like = f"%{search}%"
        rows = conn.execute(
            "SELECT * FROM linkedin_results WHERE scrape_id=? AND "
            "(full_name LIKE ? OR job_title LIKE ? OR company LIKE ? OR location LIKE ? "
            "OR email LIKE ? OR phone LIKE ? OR website LIKE ?) "
            "ORDER BY id LIMIT ? OFFSET ?",
            (scrape_id, like, like, like, like, like, like, like, limit, offset)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM linkedin_results WHERE scrape_id=? ORDER BY id LIMIT ? OFFSET ?",
            (scrape_id, limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_linkedin_results_count(scrape_id, search=""):
    conn = get_db()
    if search:
        like = f"%{search}%"
        c = conn.execute(
            "SELECT COUNT(*) as c FROM linkedin_results WHERE scrape_id=? AND "
            "(full_name LIKE ? OR job_title LIKE ? OR company LIKE ? OR location LIKE ? "
            "OR email LIKE ? OR phone LIKE ? OR website LIKE ?)",
            (scrape_id, like, like, like, like, like, like, like)).fetchone()["c"]
    else:
        c = conn.execute("SELECT COUNT(*) as c FROM linkedin_results WHERE scrape_id=?", (scrape_id,)).fetchone()["c"]
    conn.close()
    return c


def get_linkedin_results_csv(scrape_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT full_name, job_title, company, location, profile_url, "
        "email, phone, website, website_email, google_email "
        "FROM linkedin_results WHERE scrape_id=? ORDER BY id",
        (scrape_id,)).fetchall()
    conn.close()
    lines = ["Full Name,Job Title,Company,Location,Profile URL,Email,Phone,Website,Website Email,Google Email"]
    for r in rows:
        line = ",".join(f'"{v}"' for v in [
            r["full_name"], r["job_title"], r["company"], r["location"], r["profile_url"],
            r["email"], r["phone"], r["website"], r["website_email"], r["google_email"]])
        lines.append(line)
    return "\n".join(lines)


# ─── Website Scrapes ───

def create_website_scrape(user_id, urls_json, total_urls):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO website_scrapes (user_id, urls, total_urls, started_at) VALUES (?,?,?,?)",
        (user_id, urls_json, total_urls, datetime.now().isoformat()))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def get_website_scrape(scrape_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM website_scrapes WHERE id=?", (scrape_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_website_scrapes():
    conn = get_db()
    rows = conn.execute("SELECT * FROM website_scrapes ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_website_scrape(scrape_id, **kwargs):
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [scrape_id]
    conn.execute(f"UPDATE website_scrapes SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def delete_website_scrape(scrape_id):
    conn = get_db()
    conn.execute("DELETE FROM website_results WHERE scrape_id=?", (scrape_id,))
    conn.execute("DELETE FROM website_scrapes WHERE id=?", (scrape_id,))
    conn.commit()
    conn.close()


def save_website_result(scrape_id, url, emails, phones, names, social_links, logo_url=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO website_results (scrape_id, url, emails, phones, names, social_links, logo_url) VALUES (?,?,?,?,?,?,?)",
        (scrape_id, url, emails, phones, names, social_links, logo_url))
    conn.commit()
    conn.close()


def get_website_results(scrape_id, search="", limit=500, offset=0):
    conn = get_db()
    if search:
        like = f"%{search}%"
        rows = conn.execute(
            "SELECT * FROM website_results WHERE scrape_id=? AND "
            "(url LIKE ? OR emails LIKE ? OR phones LIKE ? OR names LIKE ?) ORDER BY id LIMIT ? OFFSET ?",
            (scrape_id, like, like, like, like, limit, offset)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM website_results WHERE scrape_id=? ORDER BY id LIMIT ? OFFSET ?",
            (scrape_id, limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_website_results_count(scrape_id, search=""):
    conn = get_db()
    if search:
        like = f"%{search}%"
        c = conn.execute(
            "SELECT COUNT(*) as c FROM website_results WHERE scrape_id=? AND "
            "(url LIKE ? OR emails LIKE ? OR phones LIKE ? OR names LIKE ?)",
            (scrape_id, like, like, like, like)).fetchone()["c"]
    else:
        c = conn.execute("SELECT COUNT(*) as c FROM website_results WHERE scrape_id=?", (scrape_id,)).fetchone()["c"]
    conn.close()
    return c


def get_website_results_csv(scrape_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT url, emails, phones, names, social_links, logo_url FROM website_results WHERE scrape_id=? ORDER BY id",
        (scrape_id,)).fetchall()
    conn.close()
    lines = ["URL,Emails,Phones,Names,Social Links,Logo URL"]
    for r in rows:
        line = ",".join(f'"{v}"' for v in [r["url"], r["emails"], r["phones"], r["names"], r["social_links"], r["logo_url"]])
        lines.append(line)
    return "\n".join(lines)


# ─── Google Maps Scrapes ───

def create_google_maps_scrape(user_id, search_url, scrape_emails=0):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO google_maps_scrapes (user_id, search_url, scrape_emails, started_at) VALUES (?,?,?,?)",
        (user_id, search_url, scrape_emails, datetime.now().isoformat()))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def get_google_maps_scrape(scrape_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM google_maps_scrapes WHERE id=?", (scrape_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_google_maps_scrapes():
    conn = get_db()
    rows = conn.execute("SELECT * FROM google_maps_scrapes ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_google_maps_scrape(scrape_id, **kwargs):
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [scrape_id]
    conn.execute(f"UPDATE google_maps_scrapes SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def delete_google_maps_scrape(scrape_id):
    conn = get_db()
    conn.execute("DELETE FROM google_maps_results WHERE scrape_id=?", (scrape_id,))
    conn.execute("DELETE FROM google_maps_scrapes WHERE id=?", (scrape_id,))
    conn.commit()
    conn.close()


def save_google_maps_results(scrape_id, businesses):
    conn = get_db()
    rows = [(scrape_id, b.get("name",""), b.get("category",""), b.get("address",""),
             b.get("phone",""), b.get("rating",""), b.get("reviews_count",""),
             b.get("website",""), b.get("email",""), b.get("google_maps_url","")) for b in businesses]
    if rows:
        conn.executemany(
            "INSERT INTO google_maps_results (scrape_id, name, category, address, phone, rating, "
            "reviews_count, website, email, google_maps_url) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    conn.close()
    return len(rows)


def get_google_maps_results(scrape_id, search="", limit=500, offset=0):
    conn = get_db()
    if search:
        like = f"%{search}%"
        rows = conn.execute(
            "SELECT * FROM google_maps_results WHERE scrape_id=? AND "
            "(name LIKE ? OR category LIKE ? OR address LIKE ? OR phone LIKE ? OR email LIKE ? OR website LIKE ?) "
            "ORDER BY id LIMIT ? OFFSET ?",
            (scrape_id, like, like, like, like, like, like, limit, offset)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM google_maps_results WHERE scrape_id=? ORDER BY id LIMIT ? OFFSET ?",
            (scrape_id, limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_google_maps_results_count(scrape_id, search=""):
    conn = get_db()
    if search:
        like = f"%{search}%"
        c = conn.execute(
            "SELECT COUNT(*) as c FROM google_maps_results WHERE scrape_id=? AND "
            "(name LIKE ? OR category LIKE ? OR address LIKE ? OR phone LIKE ? OR email LIKE ? OR website LIKE ?)",
            (scrape_id, like, like, like, like, like, like)).fetchone()["c"]
    else:
        c = conn.execute("SELECT COUNT(*) as c FROM google_maps_results WHERE scrape_id=?", (scrape_id,)).fetchone()["c"]
    conn.close()
    return c


def get_google_maps_results_csv(scrape_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT name, category, address, phone, rating, reviews_count, website, email, google_maps_url "
        "FROM google_maps_results WHERE scrape_id=? ORDER BY id",
        (scrape_id,)).fetchall()
    conn.close()
    lines = ["Name,Category,Address,Phone,Rating,Reviews,Website,Email,Google Maps URL"]
    for r in rows:
        line = ",".join(f'"{v}"' for v in [
            r["name"], r["category"], r["address"], r["phone"], r["rating"],
            r["reviews_count"], r["website"], r["email"], r["google_maps_url"]])
        lines.append(line)
    return "\n".join(lines)
