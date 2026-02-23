import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "app.db"

DATA_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-to-a-random-string-in-production-2024")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

DEFAULT_EMAIL = "admin@intelligentenrichment.com"
DEFAULT_PASSWORD = "ChangeMe123!"

CREDS_FILE = str(BASE_DIR / "enrichmentdata.json")
PROXY_FILE = str(BASE_DIR / "proxies.txt")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Auto-load from Google OAuth JSON file if env vars not set
if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    import glob as _glob, json as _json
    _oauth_files = _glob.glob(str(BASE_DIR / "client_secret_*.json"))
    if _oauth_files:
        try:
            _data = _json.loads(open(_oauth_files[0]).read())
            _web = _data.get("web", {})
            GOOGLE_CLIENT_ID = GOOGLE_CLIENT_ID or _web.get("client_id", "")
            GOOGLE_CLIENT_SECRET = GOOGLE_CLIENT_SECRET or _web.get("client_secret", "")
        except Exception:
            pass

MAX_CONCURRENT_JOBS = 2
DEFAULT_WORKERS = 150
DEFAULT_MAX_PEOPLE = 5
DEFAULT_TIMEOUT = 4
DEFAULT_SHEET_NAME = "Cleaned_Data"

# LinkedIn scraper
LINKEDIN_COOKIES_DIR = DATA_DIR / "linkedin_cookies"
LINKEDIN_COOKIES_DIR.mkdir(exist_ok=True)
DEFAULT_PAGE_DELAY_MIN = 3
DEFAULT_PAGE_DELAY_MAX = 5
DEFAULT_MAX_LINKEDIN_PAGES = 10

# Google Maps scraper
GMAPS_DEFAULT_CHUNK_SIZE = 5
GMAPS_DEFAULT_EXTRACT_DELAY = 1.5
