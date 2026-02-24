"""
Enrichment Worker — runs as a background asyncio task
Adapted from enri3.py to work within the web app
"""

import re, sys, time, random, asyncio, json, warnings, logging
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote_plus
from datetime import datetime

import aiohttp
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials

import database as db
from config import CREDS_FILE, PROXY_FILE, DEFAULT_WORKERS, DEFAULT_MAX_PEOPLE, DEFAULT_TIMEOUT

warnings.filterwarnings("ignore")
log = logging.getLogger("enrichment.worker")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

# ═══════════════════════════════════════════════════════════════════════════════
# KNOWN FIRST NAMES DATABASE (740+ names)
# ═══════════════════════════════════════════════════════════════════════════════

KNOWN_FIRST_NAMES = {
    "marco", "luca", "andrea", "giuseppe", "francesco", "alessandro", "matteo",
    "lorenzo", "stefano", "roberto", "antonio", "davide", "paolo", "simone",
    "fabio", "giovanni", "massimo", "claudio", "alberto", "enrico", "michele",
    "carlo", "mario", "sergio", "giorgio", "riccardo", "nicola", "daniele",
    "filippo", "pietro", "vittorio", "umberto", "emanuele", "salvatore",
    "vincenzo", "gianluca", "giancarlo", "pierluigi", "gianfranco", "tommaso",
    "edoardo", "gabriele", "leonardo", "diego", "cristiano", "manuel", "mirko",
    "ivan", "alex", "omar", "mauro", "luciano", "sandro", "piero", "renato",
    "aldo", "bruno", "dario", "flavio", "guido", "italo", "nino", "oscar",
    "renzo", "silvio", "tiziano", "ugo", "walter", "gianni", "franco",
    "gianluigi", "gianpaolo", "giampiero", "gianmaria", "pier", "pierpaolo",
    "piergiorgio", "pierdomenico", "massimiliano", "michelangelo", "ferdinando",
    "bartolomeo", "benedetto", "corrado", "cosimo", "damiano", "domenico",
    "donato", "edmondo", "efisio", "egidio", "elia", "elvio", "ennio",
    "ermanno", "ernesto", "evaristo", "ezio", "fabrizio", "fausto",
    "federico", "felice", "fernando", "fiorenzo", "fortunato", "fulvio",
    "gaetano", "gennaro", "gerardo", "giacinto", "giacomo", "gino",
    "giordano", "giuliano", "giulio", "gregorio", "iacopo", "igino",
    "ignazio", "jacopo", "lauro", "lazzaro", "leone", "leopoldo",
    "liberato", "livio", "lodovico", "luigi", "manlio", "marcello",
    "marino", "mariano", "martino", "maurizio",
    "mirco", "modesto", "moreno", "nando", "napoleone", "natale",
    "nazario", "nello", "nereo", "nunzio", "oliviero", "orazio",
    "oreste", "orlando", "osvaldo", "ottavio", "ottorino", "pasquale",
    "patrizio", "pellegrino", "primo", "prospero", "raffaele",
    "raffaello", "raimondo", "raniero", "remo", "rino", "rocco",
    "rodolfo", "rolando", "romano", "romeo", "rosario", "ruben",
    "ruggero", "sabato", "samuele", "sante", "santo", "sauro",
    "saverio", "sebastiano", "secondo", "serafino", "settimio", "severo",
    "silvano", "silverio", "simeone", "sirio", "tarcisio", "teodoro",
    "terenzio", "tiberio", "tito", "tomas", "tonino",
    "tullio", "ubaldo", "ulisse", "valentino", "valerio",
    "valter", "vasco", "virgilio", "virginio", "vito", "vittore", "zelindo",
    "maria", "anna", "giulia", "francesca", "sara", "laura", "valentina",
    "chiara", "elena", "paola", "silvia", "monica", "federica", "alessandra",
    "martina", "roberta", "barbara", "daniela", "cristina", "manuela", "claudia",
    "elisa", "patrizia", "cinzia", "teresa", "rosa", "nicoletta", "adriana",
    "beatrice", "carla", "delia", "emma", "fiorella", "grazia", "irene",
    "liliana", "marta", "nadia", "ornella", "pina", "rita", "sabrina",
    "tiziana", "viviana", "agata", "agnese", "alba", "alberta", "alessia",
    "alfonsina", "alida", "amalia", "ambra", "amelia", "angela", "angelica",
    "angelina", "anita", "annalisa", "annamaria", "annunziata", "antonia",
    "antonietta", "antonella", "assunta", "aurora", "benedicta", "benedetta",
    "bianca", "bruna", "brunella", "camilla", "carina", "carmela",
    "carolina", "caterina", "cecilia", "celeste", "clara", "clelia",
    "clotilde", "concetta", "cornelia", "costanza", "dalila", "debora",
    "diana", "dina", "dolores", "donatella", "dora", "edith", "edvige",
    "eleonora", "eliana", "elisabetta", "emanuela", "emilia", "enrica",
    "ester", "eugenia", "eva", "fabiana", "fabiola", "fatima", "filomena",
    "fiorenza", "flavia", "flora", "franca", "gabriella", "gaia",
    "germana", "giada", "gilda", "giorgia", "giovanna", "giselda",
    "gisella", "giuditta", "giuseppina", "gloria", "graziella", "ida",
    "ilaria", "ilda", "immacolata", "ines", "iolanda", "isabella",
    "jessica", "lara", "lea", "leda", "letizia", "lia", "licia",
    "lidia", "lina", "lisa", "livia", "lora", "lorella", "lorena",
    "loretta", "luana", "lucia", "luciana", "lucilla", "lucrezia",
    "luigia", "luisa", "maddalena", "mafalda", "margherita", "marianna",
    "marica", "mariella", "marina", "marisa", "maristella", "mara",
    "matilde", "maura", "melissa", "micaela", "michela", "milena",
    "mimma", "mirella", "miriam", "noemi", "norma", "nunzia",
    "olga", "olivia", "oriana", "orietta", "paolina", "perla",
    "pierina", "piera", "rachele", "raffaella", "ramona", "rebecca",
    "renata", "romana", "romina", "rosalia", "rosalba", "rosanna",
    "rosella", "rosetta", "rossana", "rossella", "ruth", "samanta",
    "samantha", "sandra", "santina", "serena", "silvana", "simona",
    "sonia", "stefania", "stella", "susanna", "sveva", "tamara",
    "tatiana", "tina", "vanessa", "vera", "veronica", "viola",
    "virginia", "vittoria", "wanda", "zaira", "zoe",
    "james", "john", "robert", "michael", "david", "william", "richard",
    "joseph", "thomas", "charles", "daniel", "matthew", "anthony", "mark",
    "donald", "steven", "paul", "andrew", "joshua", "kenneth", "kevin",
    "brian", "george", "timothy", "ronald", "edward", "jason", "jeffrey",
    "ryan", "jacob", "gary", "nicholas", "eric", "jonathan", "stephen",
    "larry", "justin", "scott", "brandon", "benjamin", "samuel", "raymond",
    "gregory", "frank", "alexander", "patrick", "jack", "dennis", "jerry",
    "tyler", "aaron", "jose", "adam", "nathan", "henry", "peter", "douglas",
    "zachary", "noah", "kyle", "ethan", "jeremy", "christian", "roger",
    "keith", "terry", "harry", "ralph", "sean", "jesse", "roy", "louis",
    "philip", "billy", "eugene", "russell", "randy", "howard", "carlos",
    "johnny", "martin", "craig", "gerald", "ernest", "willie", "todd",
    "dale", "guy", "alan", "wayne", "bobby",
    "victor", "bruce", "gabriel", "vincent", "joel",
    "oliver", "arthur", "lewis", "dylan", "luke", "ian", "simon",
    "max", "leon", "felix", "theo", "hugo", "axel", "lars", "sven",
    "klaus", "hans", "karl", "fritz", "wolfgang", "heinrich", "helmut",
    "dieter", "ralf", "werner", "horst", "bernd", "jens", "uwe",
    "stefan", "andreas", "markus",
    "matthias", "sebastian", "tobias", "florian", "dominik", "philipp",
    "jan", "jochen", "joachim", "thorsten", "holger", "kai",
    "pierre", "jean", "jacques", "philippe", "michel", "alain",
    "bernard", "christophe", "dominique", "emmanuel", "etienne",
    "francois", "frederic", "georges", "gerard", "guillaume", "henri",
    "hugues", "laurent", "marc", "nicolas", "olivier",
    "pascal", "rene", "serge", "stephane", "thierry",
    "yves", "xavier", "arnaud", "benoit", "cedric",
    "fabrice", "gilles", "herve", "julien", "lionel", "maxime",
    "raphael", "sebastien", "sylvain",
    "pedro", "juan", "miguel", "rafael", "javier", "angel",
    "jorge", "pablo", "raul", "ramon", "rodrigo",
    "hector", "alfonso", "cesar", "arturo", "andres",
    "ignacio", "gonzalo", "alvaro", "agustin",
    "mary", "patricia", "jennifer", "linda", "elizabeth", "barbara",
    "susan", "jessica", "sarah", "karen", "lisa", "nancy", "betty",
    "margaret", "sandra", "ashley", "dorothy", "kimberly", "emily",
    "donna", "michelle", "carol", "amanda", "melissa", "deborah",
    "stephanie", "rebecca", "sharon", "cynthia", "kathleen",
    "amy", "shirley", "brenda", "pamela", "nicole",
    "helen", "samantha", "katherine", "christine", "debra", "rachel",
    "carolyn", "janet", "catherine", "heather", "diane",
    "julie", "joyce", "victoria", "kelly",
    "lauren", "christina", "joan", "evelyn", "judith", "megan",
    "cheryl", "hannah", "jacqueline", "martha",
    "ann", "marie", "alice", "jean", "madison", "frances",
    "anne", "natalie", "sophia", "charlotte", "grace", "rose",
    "sophie", "chloe", "isabelle", "camille", "juliette", "margaux",
    "mathilde", "pauline", "manon",
    "lucie", "oceane", "aurelie", "celine", "delphine", "nathalie",
    "sylvie", "veronique", "brigitte", "monique", "colette",
    "ingrid", "helga", "petra", "monika", "sabine", "heike",
    "kerstin", "birgit", "susanne",
    "katrin", "ulrike", "renate", "christa", "erika", "gisela",
    "ana", "carmen", "isabel", "pilar", "lucia",
    "dolores", "mercedes", "beatriz", "amparo",
    "raquel", "rocio", "consuelo", "inmaculada", "soledad", "alicia",
}

# ═══════════════════════════════════════════════════════════════════════════════
# NAME VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

NON_NAME_WORDS = {
    "cookie", "cookies", "consent", "privacy", "gdpr", "policy", "tracking",
    "login", "logout", "register", "signup", "subscribe", "unsubscribe",
    "search", "menu", "header", "footer", "navigation", "sidebar",
    "homepage", "download", "upload", "click", "here", "more", "read",
    "close", "open", "view", "show", "hide", "next", "prev", "back",
    "home", "page", "site", "web", "link", "form", "submit", "send",
    "info", "admin", "support", "help", "contact", "noreply", "reply",
    "webmaster", "postmaster", "mailer", "daemon", "bounce", "abuse",
    "sales", "billing", "invoice", "accounting", "legal",
    "office", "reception", "general", "hello", "team", "staff",
    "google", "analytics", "facebook", "pixel", "tag", "script",
    "api", "webhook", "server", "system", "test", "debug", "bot",
    "automation", "cron", "scheduler", "monitor", "tracker",
    "voice", "termination", "voip", "sip", "trunk", "gateway",
    "bandwidth", "routing", "firewall", "proxy", "vpn", "dns",
    "hosting", "cloud", "storage", "backup", "cluster",
    "software", "hardware", "network", "wireless", "mobile", "desktop",
    "product", "products", "service", "services", "order", "orders",
    "shop", "store", "cart", "catalog", "catalogue", "pricing", "demo",
    "trial", "free", "premium", "basic", "standard", "enterprise",
    "custom", "development", "solution", "solutions", "platform",
    "food", "non", "nonfood", "barter", "press", "photos",
    "member", "create", "account", "your", "youraccount",
    "gasolio", "lube", "miscele", "speciali", "fertilizzanti",
    "company", "address", "business", "messaging", "corporate",
    "partner", "partnership", "associate", "associates",
    "clienti", "finali", "servizio", "servizi", "contatti", "commerciali",
    "reclami", "ordini", "acquisti", "preventivi", "assistenza",
    "prodotti", "catalogo", "listino", "prezzi", "offerte", "promozioni",
    "notizie", "eventi", "blog", "articoli", "risorse", "documenti",
    "lavora", "carriere", "opportunita", "posizioni", "aperte",
    "termini", "condizioni", "legale", "normativa", "regolamento",
    "about", "terms", "conditions", "copyright", "rights", "reserved",
    "data", "protection", "security", "compliance", "certification",
    "quality", "assurance", "control", "management", "process",
    "customer", "client", "supplier", "vendor", "dealer", "distributor",
    "technical", "commercial", "financial", "operational",
    "use", "using", "used", "user", "users", "utilizza",
}


def is_name(t):
    t = t.strip()
    if len(t) < 4 or len(t) > 50:
        return False
    w = t.split()
    if len(w) < 2 or len(w) > 4:
        return False
    for part in w:
        if len(part) < 2 or part.isdigit():
            return False
        if any(c.isdigit() for c in part):
            return False
    for part in w:
        if part.lower().rstrip(".,") in KNOWN_FIRST_NAMES:
            return True
    return False


def is_name_from_email(first, last):
    f, l = first.lower().strip(), last.lower().strip()
    if len(f) < 2 or len(l) < 2:
        return False
    if any(c.isdigit() for c in f + l):
        return False
    return f in KNOWN_FIRST_NAMES or l in KNOWN_FIRST_NAMES


# ═══════════════════════════════════════════════════════════════════════════════
# EMAIL + MATCHING (same as enri3.py)
# ═══════════════════════════════════════════════════════════════════════════════

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

GENERIC = {"info", "contact", "contatti", "admin", "support", "help", "noreply",
           "no-reply", "postmaster", "webmaster", "sales", "marketing", "office",
           "segreteria", "reception", "hr", "newsletter", "press", "commerciale",
           "vendite", "ordini", "privacy", "abuse", "billing", "jobs", "acquisti",
           "comunicazione", "pec", "amministrazione", "fatturazione", "ufficio",
           "direzione", "cert", "personale", "logistica", "produzione",
           "qualita", "export", "import", "pagamenti", "preventivi"}

JUNK_DOM = {"example.com", "sentry.io", "wixpress.com", "wordpress.org", "w3.org",
            "schema.org", "googleapis.com", "google.com", "facebook.com",
            "twitter.com", "cloudflare.com", "gravatar.com", "instagram.com"}

TITLE_PRIORITY = [
    ("ceo", 100), ("amministratore delegato", 100), ("founder", 95), ("fondatore", 95),
    ("co-founder", 94), ("co-fondatore", 94), ("titolare", 93), ("proprietario", 92),
    ("presidente", 90), ("vice presidente", 88),
    ("managing director", 85), ("direttore generale", 85), ("general manager", 85),
    ("cto", 80), ("cfo", 80), ("coo", 80), ("cmo", 80), ("cso", 80),
    ("direttore commerciale", 75), ("direttore marketing", 75),
    ("direttore tecnico", 75), ("direttore vendite", 75),
    ("direttore finanziario", 75), ("direttore", 70),
    ("responsabile commerciale", 60), ("responsabile marketing", 60),
    ("responsabile vendite", 60), ("responsabile tecnico", 60),
    ("responsabile it", 60), ("responsabile", 55),
    ("sales manager", 50), ("export manager", 50), ("account manager", 50),
    ("project manager", 48), ("manager", 45),
    ("head of", 45), ("director", 45),
    ("business development", 42),
    ("partner", 40), ("socio", 40),
    ("amministratore", 38), ("legale rappresentante", 35),
]

LINKEDIN_TITLES = (
    '"CEO" OR "Founder" OR "Fondatore" OR "Titolare" OR '
    '"Amministratore" OR "Direttore" OR "Responsabile" OR '
    '"Managing Director" OR "General Manager" OR '
    '"Direttore Commerciale" OR "Direttore Marketing" OR '
    '"Sales Manager" OR "Export Manager" OR "CTO" OR "CFO" OR '
    '"Proprietario" OR "Owner" OR "Partner"'
)

UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

TEAM_KW = re.compile(
    r"chi.siamo|about|team|staff|contatt|contact|azienda|company|persone|people|"
    r"management|leadership|direzione|organizzazione", re.I)


def title_score(t):
    tl = t.lower()
    for kw, s in TITLE_PRIORITY:
        if kw in tl:
            return s
    return 0


def ok_email(e, dom=""):
    e = e.lower()
    pre, d = e.split("@")[0], e.split("@")[-1]
    if d in JUNK_DOM or pre in GENERIC:
        return False
    if d.endswith((".png", ".jpg", ".css", ".js", ".gif")):
        return False
    if dom and d != dom:
        return False
    return len(pre) >= 2


def find_title_text(text):
    tl = text.lower()
    for kw, _ in sorted(TITLE_PRIORITY, key=lambda x: len(x[0]), reverse=True):
        if kw in tl:
            i = tl.index(kw)
            return text[i:i + len(kw) + 20].strip().rstrip(".,;:|/")[:60]
    return ""


def normalize(s):
    repl = {"\u00e0": "a", "\u00e8": "e", "\u00e9": "e", "\u00ec": "i", "\u00f2": "o", "\u00f9": "u"}
    s = s.lower().strip()
    for o, n in repl.items():
        s = s.replace(o, n)
    return re.sub(r"[^a-z]", "", s)


def email_matches_name(email, first_name, last_name):
    pre = email.split("@")[0].lower()
    f, l = normalize(first_name), normalize(last_name)
    if not f or not l:
        return False
    patterns = [f"{f}.{l}", f"{f}{l}", f"{l}.{f}", f"{l}{f}",
                f"{f[0]}.{l}", f"{f[0]}{l}", f"{f}.{l[0]}"]
    return any(p in pre for p in patterns) or l == pre


def guess_email(first, last, domain):
    f, l = normalize(first), normalize(last)
    return f"{f}.{l}@{domain}" if f and l and domain else ""


def get_domain(url):
    if not url:
        return ""
    return urlparse(url if url.startswith("http") else f"https://{url}").netloc.lower().replace("www.", "")


# ═══════════════════════════════════════════════════════════════════════════════
# PROXY POOL
# ═══════════════════════════════════════════════════════════════════════════════

class ProxyPool:
    def __init__(self):
        self.proxies = []
        self.idx = 0
        self.use_direct = False
        p = Path(PROXY_FILE)
        if not p.exists():
            self.use_direct = True
            return
        for entry in p.read_text().strip().split():
            parts = entry.strip().split(":")
            if len(parts) == 4:
                self.proxies.append(f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}")
            elif len(parts) == 2:
                self.proxies.append(f"http://{parts[0]}:{parts[1]}")
        if self.proxies:
            random.shuffle(self.proxies)
        else:
            self.use_direct = True

    def get(self):
        if self.use_direct or not self.proxies:
            return None
        self.idx = (self.idx + 1) % len(self.proxies)
        return self.proxies[self.idx]


def parse_proxy_for_playwright(proxy_url):
    """Convert 'http://user:pass@host:port' to Playwright proxy dict."""
    if not proxy_url:
        return None
    from urllib.parse import urlparse as _urlparse
    p = _urlparse(proxy_url)
    result = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        result["username"] = p.username
    if p.password:
        result["password"] = p.password
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP FETCH
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch(session, url, pp, timeout=DEFAULT_TIMEOUT, quick=False):
    hdrs = {"User-Agent": random.choice(UA),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.7"}
    to = aiohttp.ClientTimeout(total=timeout)
    attempts = 1 if quick else 3
    for attempt in range(attempts):
        proxy = pp.get()
        try:
            async with session.get(url, headers=hdrs, proxy=proxy, timeout=to,
                                   ssl=False, allow_redirects=True) as r:
                if r.status in (200, 202):
                    text = await r.text(errors="replace")
                    if len(text) > 500:
                        return BeautifulSoup(text, "lxml")
                elif r.status in (404, 403, 410, 500, 502, 503):
                    return None
        except Exception:
            pass
    # Only fall back to direct (no proxy) if no proxies are configured
    if not quick and pp.use_direct:
        try:
            async with session.get(url, headers=hdrs, timeout=to,
                                   ssl=False, allow_redirects=True) as r:
                if r.status in (200, 202):
                    text = await r.text(errors="replace")
                    if len(text) > 500:
                        return BeautifulSoup(text, "lxml")
        except Exception:
            pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSITE SCRAPING
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_emails_and_names(soup, domain=""):
    emails = []
    seen_emails = set()

    for block in soup.find_all(["div", "li", "article"],
                                class_=re.compile(r"team|member|staff|person|card|profile", re.I)):
        txt = block.get_text(separator=" ", strip=True)
        if len(txt) < 5 or len(txt) > 500:
            continue
        name = ""
        for h in block.find_all(["h2", "h3", "h4", "h5", "strong", "b", "span"]):
            if is_name(h.get_text(strip=True)):
                name = h.get_text(strip=True)
                break
        email = ""
        for a in block.find_all("a", href=True):
            if a["href"].startswith("mailto:"):
                e = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
                if ok_email(e, domain):
                    email = e
                    break
        if not email:
            for e in EMAIL_RE.findall(txt):
                if ok_email(e.lower(), domain):
                    email = e.lower()
                    break
        title = find_title_text(txt)
        if email and email not in seen_emails:
            seen_emails.add(email)
            emails.append({"email": email, "name": name, "title": title})

    for h in soup.find_all(["h2", "h3", "h4", "h5", "strong", "b"]):
        ht = h.get_text(strip=True)
        if not is_name(ht):
            continue
        email, title = "", ""
        for sib in h.find_next_siblings()[:3]:
            st = sib.get_text(strip=True)
            if not title:
                title = find_title_text(st)
            if not email:
                for e in EMAIL_RE.findall(st):
                    if ok_email(e.lower(), domain):
                        email = e.lower()
                        break
        if h.parent:
            pt = h.parent.get_text(separator=" ", strip=True)
            if not title:
                title = find_title_text(pt)
            if not email:
                for e in EMAIL_RE.findall(pt):
                    if ok_email(e.lower(), domain):
                        email = e.lower()
                        break
        if email and email not in seen_emails:
            seen_emails.add(email)
            emails.append({"email": email, "name": ht, "title": title})
        elif not email and ht:
            emails.append({"email": "", "name": ht, "title": title})

    for a in soup.find_all("a", href=True):
        if not a["href"].startswith("mailto:"):
            continue
        email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
        if not ok_email(email, domain) or email in seen_emails:
            continue
        name = a.get_text(strip=True)
        if not is_name(name):
            name = ""
        title = find_title_text(a.parent.get_text(separator=" ", strip=True)) if a.parent else ""
        seen_emails.add(email)
        emails.append({"email": email, "name": name, "title": title})

    full_text = soup.get_text(separator=" ")
    for e in EMAIL_RE.findall(full_text):
        e = e.lower()
        if ok_email(e, domain) and e not in seen_emails:
            seen_emails.add(e)
            emails.append({"email": e, "name": "", "title": ""})

    return emails


# ═══════════════════════════════════════════════════════════════════════════════
# LINKEDIN DORKING
# ═══════════════════════════════════════════════════════════════════════════════

# ── Search engine abstraction ──
# At job start, we probe which engine works and cache the result.

_working_engine = None  # "ddg_html", "ddg_lite", "google", or None


def _parse_ddg_html(soup):
    results = []
    for result in soup.find_all("div", class_="result")[:10]:
        title_el = result.find("a", class_="result__a")
        snippet_el = result.find("a", class_="result__snippet")
        if not title_el:
            continue
        results.append((
            title_el.get_text(strip=True),
            snippet_el.get_text(strip=True) if snippet_el else "",
            title_el.get("href", ""),
        ))
    return results


def _parse_ddg_lite(soup):
    results = []
    for a in soup.find_all("a", class_="result-link")[:10]:
        title = a.get_text(strip=True)
        href = a.get("href", "")
        snippet = ""
        row = a.find_parent("tr")
        if row:
            snip_td = row.find_next_sibling("tr")
            if snip_td:
                snippet = snip_td.get_text(strip=True)
        results.append((title, snippet, href))
    return results


def _parse_google(soup):
    results = []
    for div in soup.find_all("div", class_="g")[:10]:
        a = div.find("a", href=True)
        if not a:
            continue
        href = a.get("href", "")
        title = a.get_text(strip=True)
        snip_el = div.find("span", class_=re.compile(r"st|aCOpRe"))
        if not snip_el:
            snip_el = div.find("div", {"data-sncf": True})
        snippet = snip_el.get_text(strip=True) if snip_el else ""
        results.append((title, snippet, href))
    return results


async def _probe_search_engines(session, pp):
    """Test each search engine once and return the name of the first working one."""
    test_query = "Microsoft CEO"

    engines = [
        ("ddg_html", f"https://html.duckduckgo.com/html/?q={quote_plus(test_query)}", _parse_ddg_html),
        ("ddg_lite", f"https://lite.duckduckgo.com/lite/?q={quote_plus(test_query)}", _parse_ddg_lite),
        ("google", f"https://www.google.com/search?q={quote_plus(test_query)}&num=10&hl=en", _parse_google),
    ]

    for name, url, parser in engines:
        soup = await fetch(session, url, pp)
        if soup:
            results = parser(soup)
            if results:
                log.info(f"Search engine probe: {name} WORKS ({len(results)} results)")
                return name
            else:
                log.warning(f"Search engine probe: {name} returned HTML but no results (CAPTCHA?)")
        else:
            log.warning(f"Search engine probe: {name} failed to fetch")

    log.error("Search engine probe: ALL engines failed!")
    return None


async def web_search(session, query, pp):
    """Search using the working engine (determined at job start). Returns list of (title, snippet, href)."""
    global _working_engine

    if _working_engine == "ddg_html":
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        soup = await fetch(session, url, pp, quick=True)
        return _parse_ddg_html(soup) if soup else []

    elif _working_engine == "ddg_lite":
        url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
        soup = await fetch(session, url, pp, quick=True)
        return _parse_ddg_lite(soup) if soup else []

    elif _working_engine == "google":
        url = f"https://www.google.com/search?q={quote_plus(query)}&num=10&hl=en"
        soup = await fetch(session, url, pp, quick=True)
        return _parse_google(soup) if soup else []

    # No working engine — try all as last resort
    for engine_url, parser in [
        (f"https://html.duckduckgo.com/html/?q={quote_plus(query)}", _parse_ddg_html),
        (f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}", _parse_ddg_lite),
        (f"https://www.google.com/search?q={quote_plus(query)}&num=10&hl=en", _parse_google),
    ]:
        soup = await fetch(session, engine_url, pp, quick=True)
        if soup:
            results = parser(soup)
            if results:
                return results
    return []


def _parse_linkedin_people(results):
    """Parse search results into LinkedIn people entries."""
    people, seen = [], set()
    for title_text, snippet_text, href in results:
        combined = f"{title_text} {snippet_text}"
        name, job_title = "", ""
        parts = title_text.replace(" | LinkedIn", "").replace(" - LinkedIn", "").split(" - ")
        if len(parts) >= 2:
            candidate = parts[0].strip()
            if is_name(candidate):
                name = candidate
                job_title = parts[1].strip()
        elif len(parts) == 1:
            candidate = parts[0].strip()
            if is_name(candidate):
                name = candidate
        if not job_title and snippet_text:
            m = re.search(r'[·\-]\s*(.+?)\s+(?:presso|at|@|a)\s+', snippet_text, re.I)
            if m:
                job_title = m.group(1).strip()
        if not job_title:
            job_title = find_title_text(combined)
        if name and name.lower() not in seen:
            name = re.sub(r'\s*\|.*$', '', name).strip()
            name = re.sub(r'\s*-\s*LinkedIn.*$', '', name, flags=re.I).strip()
            if any(x in name.upper() for x in ["SRL", "SPA", "S.R.L", "S.P.A", "LINKEDIN"]):
                continue
            w = name.split()
            if 2 <= len(w) <= 4:
                seen.add(name.lower())
                people.append({"first_name": w[0], "last_name": " ".join(w[1:]),
                               "title": job_title[:80], "source": "linkedin"})
    return people[:DEFAULT_MAX_PEOPLE]


async def linkedin_dork(session, company_name, pp):
    clean = re.sub(r"\b(S\.?R\.?L\.?|S\.?P\.?A\.?|S\.?N\.?C\.?|S\.?A\.?S\.?|S\.?S\.?|"
                   r"SOCIETA'?\s*(PER\s*AZIONI|A\s*RESPONSABILITA'?\s*LIMITATA)?)\b",
                   "", company_name, flags=re.I).strip().rstrip(" -.,")
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) < 2:
        return []
    q = f'site:linkedin.com/in "{clean}" ({LINKEDIN_TITLES})'
    results = await web_search(session, q, pp)
    return _parse_linkedin_people(results)


# ═══════════════════════════════════════════════════════════════════════════════
# MERGE + MATCH
# ═══════════════════════════════════════════════════════════════════════════════

def merge_and_match(website_data, linkedin_people, domain):
    site_emails = [d for d in website_data if d["email"]]
    site_names = [d for d in website_data if d["name"] and not d["email"]]
    all_names = []
    for p in linkedin_people:
        if not is_name_from_email(p["first_name"], p["last_name"]):
            continue
        all_names.append({"first_name": p["first_name"], "last_name": p["last_name"],
                          "title": p["title"], "email": "", "matched": False})
    for d in site_names:
        parts = d["name"].split()
        if len(parts) >= 2:
            fn, ln = parts[0], " ".join(parts[1:])
            if not is_name_from_email(fn, ln):
                continue
            if not any(normalize(n["first_name"]) == normalize(fn) and
                       normalize(n["last_name"]) == normalize(ln) for n in all_names):
                all_names.append({"first_name": fn, "last_name": ln, "title": d["title"],
                                  "email": "", "matched": False})
    for d in site_emails:
        if d["name"]:
            parts = d["name"].split()
            if len(parts) >= 2:
                fn, ln = parts[0], " ".join(parts[1:])
                if not is_name_from_email(fn, ln):
                    continue
                existing = [n for n in all_names if normalize(n["first_name"]) == normalize(fn)
                            and normalize(n["last_name"]) == normalize(ln)]
                if existing:
                    for n in existing:
                        if not n["email"]:
                            n["email"] = d["email"]
                            n["matched"] = True
                        if not n["title"] and d["title"]:
                            n["title"] = d["title"]
                else:
                    all_names.append({"first_name": fn, "last_name": ln, "title": d["title"],
                                      "email": d["email"], "matched": True})
    unmatched = []
    for d in site_emails:
        email = d["email"]
        if any(n["email"] == email for n in all_names):
            continue
        matched = False
        for n in all_names:
            if not n["email"] and email_matches_name(email, n["first_name"], n["last_name"]):
                n["email"] = email
                n["matched"] = True
                matched = True
                if not n["title"] and d.get("title"):
                    n["title"] = d["title"]
                break
        if not matched:
            unmatched.append(d)
    for n in all_names:
        if not n["email"] and n["first_name"] and n["last_name"] and domain:
            n["email"] = guess_email(n["first_name"], n["last_name"], domain)
    people = []
    for n in all_names:
        if n["email"]:
            people.append({"email": n["email"], "first_name": n["first_name"],
                           "last_name": n["last_name"], "title": n["title"]})
    for d in unmatched:
        if not any(p["email"] == d["email"] for p in people):
            people.append({"email": d["email"], "first_name": "", "last_name": "", "title": d.get("title", "")})
    for n in all_names:
        if not n["email"] and n["first_name"]:
            if not any(normalize(p["first_name"]) == normalize(n["first_name"]) and
                       normalize(p["last_name"]) == normalize(n["last_name"]) for p in people):
                people.append({"email": "", "first_name": n["first_name"],
                               "last_name": n["last_name"], "title": n["title"]})
    people.sort(key=lambda p: title_score(p.get("title", "")), reverse=True)
    seen = set()
    deduped = []
    for p in people:
        key = p["email"] if p["email"] else f"{normalize(p['first_name'])}{normalize(p['last_name'])}"
        if key and key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped[:DEFAULT_MAX_PEOPLE]


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH WEBSITE
# ═══════════════════════════════════════════════════════════════════════════════

async def search_website(session, name, province, pp):
    q = f"{name} {province} sito ufficiale" if province else f"{name} sito ufficiale"
    results = await web_search(session, q, pp)
    skip = {"facebook.com", "linkedin.com", "twitter.com", "instagram.com",
            "youtube.com", "paginegialle.it", "wikipedia.org", "amazon.com",
            "duckduckgo.com", "google.com"}
    for title, snippet, href in results[:8]:
        if not href.startswith("http"):
            continue
        d = urlparse(href).netloc.lower().replace("www.", "")
        if any(s in d for s in skip):
            continue
        return f"https://{urlparse(href).netloc}"
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PROCESS ONE COMPANY
# ═══════════════════════════════════════════════════════════════════════════════

async def process_one(session, company, pp):
    website = company["website"]
    if not website:
        website = await search_website(session, company["name"], company["province"], pp)
        if website:
            log.info(f"[{company['name'][:30]}] Found website via search: {website}")
        else:
            log.warning(f"[{company['name'][:30]}] No website found via search")
    url = website if website and website.startswith("http") else (f"https://{website}" if website else "")
    domain = get_domain(url) if url else ""

    async def website_phase():
        if not url:
            return []
        all_data = []
        soup = await fetch(session, url, pp)
        if not soup:
            return []
        all_data.extend(scrape_emails_and_names(soup, domain))
        has_email = any(d["email"] for d in all_data)
        found_links = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True).lower()
            if TEAM_KW.search(text) or TEAM_KW.search(href):
                if href.startswith("/"):
                    href = urljoin(url.rstrip("/") + "/", href)
                elif not href.startswith("http"):
                    href = urljoin(url.rstrip("/") + "/", href)
                if domain and domain in href.lower():
                    found_links.add(href)
        for link in list(found_links)[:2]:
            soup2 = await fetch(session, link, pp, quick=True)
            if soup2:
                all_data.extend(scrape_emails_and_names(soup2, domain))
            await asyncio.sleep(0.05)
        if not found_links and not has_email:
            for path in ["/chi-siamo", "/contatti"]:
                s = await fetch(session, urljoin(url.rstrip("/") + "/", path), pp, quick=True)
                if s:
                    all_data.extend(scrape_emails_and_names(s, domain))
        return all_data

    async def linkedin_phase():
        return await linkedin_dork(session, company["name"], pp)

    web_data, li_people = await asyncio.gather(website_phase(), linkedin_phase())
    return merge_and_match(web_data, li_people, domain)


# ═══════════════════════════════════════════════════════════════════════════════
# SHEETS WRITER (comma-separated 4 columns)
# ═══════════════════════════════════════════════════════════════════════════════

PEOPLE_COLS = ["EMAILS", "FIRST NAMES", "LAST NAMES", "TITLES"]


def sheets_flush(ws, col_map, batch):
    """Write a batch of results to Google Sheets. batch = [(sheet_row, people_list), ...]"""
    updates = []
    for sr, people in batch:
        emails, firsts, lasts, titles = [], [], [], []
        for p in people:
            if p.get("email"):
                emails.append(p["email"])
            fn, ln = p.get("first_name", ""), p.get("last_name", "")
            if fn and fn.lower() not in NON_NAME_WORDS:
                firsts.append(fn)
            if ln and ln.lower() not in NON_NAME_WORDS:
                lasts.append(ln)
            if p.get("title"):
                titles.append(p["title"])
        if emails and "EMAILS" in col_map:
            updates.append(gspread.Cell(sr, col_map["EMAILS"], ", ".join(emails)))
        if firsts and "FIRST NAMES" in col_map:
            updates.append(gspread.Cell(sr, col_map["FIRST NAMES"], ", ".join(firsts)))
        if lasts and "LAST NAMES" in col_map:
            updates.append(gspread.Cell(sr, col_map["LAST NAMES"], ", ".join(lasts)))
        if titles and "TITLES" in col_map:
            updates.append(gspread.Cell(sr, col_map["TITLES"], ", ".join(titles)))

    if not updates:
        return
    for i in range(0, len(updates), 60):
        chunk = updates[i:i + 60]
        retry = 0
        while retry < 5:
            try:
                ws.update_cells(chunk, value_input_option="RAW")
                break
            except Exception:
                retry += 1
                time.sleep(30 * retry)
        time.sleep(1)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENRICHMENT RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def _get_gspread_client():
    """Get a gspread client using OAuth tokens if available, else service account."""
    tokens = db.get_google_tokens()
    if tokens:
        try:
            from google.oauth2.credentials import Credentials as OAuthCredentials
            from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
            oauth_creds = OAuthCredentials(
                token=tokens["access_token"],
                refresh_token=tokens["refresh_token"],
                token_uri="https://oauth2.googleapis.com/token",
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET,
                scopes=SCOPES,
            )
            # Check expiry and refresh if needed
            expiry_str = tokens.get("token_expiry", "")
            if expiry_str:
                from datetime import timedelta as td
                expiry = datetime.fromisoformat(expiry_str)
                if datetime.utcnow() > expiry - td(seconds=60):
                    from google.auth.transport.requests import Request
                    oauth_creds.refresh(Request())
                    new_expiry = oauth_creds.expiry.isoformat() if oauth_creds.expiry else (datetime.utcnow() + td(seconds=3600)).isoformat()
                    db.save_google_tokens(
                        access_token=oauth_creds.token,
                        refresh_token=oauth_creds.refresh_token or tokens["refresh_token"],
                        token_expiry=new_expiry,
                        google_email=tokens.get("google_email", ""),
                    )
            gc = gspread.authorize(oauth_creds)
            log.info("Using OAuth user credentials for Google Sheets")
            return gc
        except Exception as e:
            log.warning(f"OAuth auth failed, falling back to service account: {e}")

    # Fallback to service account
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    log.info("Using service account credentials for Google Sheets")
    return gc


async def run_enrichment(job_id: int):
    """Main entry point — runs full enrichment for a job."""
    job = db.get_job(job_id)
    if not job:
        log.error(f"Job #{job_id} not found in database")
        return

    log.info(f"Job #{job_id} starting — sheet_id={job['sheet_id']} sheet_name={job['sheet_name']}")
    db.update_job(job_id, status="running", started_at=datetime.now().isoformat())

    try:
        # Connect to sheet
        log.info(f"Job #{job_id} connecting to Google Sheets...")
        gc = _get_gspread_client()
        sp = gc.open_by_key(job["sheet_id"])
        log.info(f"Job #{job_id} spreadsheet opened, getting worksheet '{job['sheet_name']}'...")
        ws = sp.worksheet(job["sheet_name"]) if job["sheet_name"] else sp.sheet1
        log.info(f"Job #{job_id} worksheet loaded")

        # Setup columns
        headers = ws.row_values(1)
        col_map = {}
        for pc in PEOPLE_COLS:
            for i, h in enumerate(headers):
                if h.upper().strip() == pc.upper():
                    col_map[pc] = i + 1
                    break
        to_create = [pc for pc in PEOPLE_COLS if pc not in col_map]
        if to_create:
            next_col = len(headers) + 1
            total_needed = next_col + len(to_create) - 1
            if total_needed > ws.col_count:
                ws.resize(cols=total_needed)
            cells = []
            for name in to_create:
                cells.append(gspread.Cell(1, next_col, name))
                col_map[name] = next_col
                next_col += 1
            if cells:
                ws.update_cells(cells, value_input_option="RAW")

        # Read data
        data = ws.get_all_values()
        rows = data[1:]

        # Find company columns
        kw_map = {
            "company_name": ["RAGIONE SOCIALE", "COMPANY", "AZIENDA", "DENOMINAZIONE"],
            "province": ["PROVINCIA"],
            "website": ["WEBSITE", "SITO", "WEB", "URL"],
        }
        col = {}
        for field, keywords in kw_map.items():
            for i, h in enumerate(headers):
                if any(k in h.upper() for k in keywords):
                    col[field] = i
                    break

        if "company_name" not in col:
            log.error(f"Job #{job_id} cannot find company name column. Headers: {headers}")
            db.update_job(job_id, status="error",
                          error_message=f"Cannot find company name column. Found headers: {', '.join(headers[:10])}",
                          finished_at=datetime.now().isoformat())
            return

        log.info(f"Job #{job_id} column mapping: {col}")

        # Build company list
        companies = []
        for i, row in enumerate(rows):
            name = row[col["company_name"]].strip() if col["company_name"] < len(row) else ""
            if not name:
                continue
            prov = row[col.get("province", 999)].strip() if col.get("province", 999) < len(row) else ""
            web = row[col.get("website", 999)].strip() if col.get("website", 999) < len(row) else ""
            companies.append({"sheet_row": i + 2, "name": name, "province": prov, "website": web})

        total = len(companies)
        log.info(f"Job #{job_id} found {total} companies to enrich")
        db.update_job(job_id, total_companies=total)

        if total == 0:
            db.update_job(job_id, status="done", finished_at=datetime.now().isoformat())
            return

        # Run enrichment
        pp = ProxyPool()
        workers = DEFAULT_WORKERS
        processed = 0
        found = 0
        total_people = 0
        errors = 0
        start = time.time()
        write_batch = []

        connector = aiohttp.TCPConnector(limit=workers * 2, limit_per_host=50,
                                          ttl_dns_cache=300, enable_cleanup_closed=True)

        async with aiohttp.ClientSession(connector=connector,
                                          timeout=aiohttp.ClientTimeout(total=30)) as session:
            # Probe which search engine works from this server
            global _working_engine
            _working_engine = await _probe_search_engines(session, pp)
            if not _working_engine:
                db.update_job(job_id, status="error",
                              error_message="All search engines blocked (DuckDuckGo + Google). Need residential proxies.",
                              finished_at=datetime.now().isoformat())
                return

            for batch_start in range(0, total, workers * 2):
                batch = companies[batch_start:batch_start + workers * 2]
                sem = asyncio.Semaphore(workers)

                async def bounded(c):
                    async with sem:
                        try:
                            people = await process_one(session, c, pp)
                            return c, people, ""
                        except Exception as e:
                            return c, [], str(e)[:200]

                results = await asyncio.gather(*[bounded(c) for c in batch])
                result_batch = []

                for company, people, error in results:
                    processed += 1
                    if people:
                        found += 1
                        total_people += len(people)
                        write_batch.append((company["sheet_row"], people))
                        result_batch.append({
                            "company_name": company["name"],
                            "province": company["province"],
                            "website": company["website"],
                            "people": people,
                        })
                    if error:
                        errors += 1

                # Save results to app database
                if result_batch:
                    db.save_results(job_id, result_batch)

                # Write to Google Sheets every 100
                if len(write_batch) >= 100:
                    try:
                        sheets_flush(ws, col_map, write_batch)
                    except Exception:
                        pass
                    write_batch.clear()

                # Update job progress
                elapsed = time.time() - start
                rate = processed / max(elapsed, 1)
                remaining = (total - processed) / max(rate, 0.01)
                eta = f"{remaining / 60:.0f}m" if remaining > 60 else f"{remaining:.0f}s"

                db.update_job(job_id,
                              processed=processed,
                              found_people=found,
                              total_people=total_people,
                              errors=errors,
                              rate=round(rate, 1),
                              eta=eta)

                # Check if job was cancelled
                current = db.get_job(job_id)
                if current and current["status"] == "cancelled":
                    break

        # Final flush to sheets
        if write_batch:
            try:
                sheets_flush(ws, col_map, write_batch)
            except Exception:
                pass

        db.update_job(job_id, status="done", finished_at=datetime.now().isoformat(),
                      processed=processed, found_people=found, total_people=total_people, errors=errors)
        log.info(f"Job #{job_id} COMPLETED — {processed} processed, {found} found, {total_people} people, {errors} errors")

    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}"
        log.error(f"Job #{job_id} FAILED: {error_msg}")
        log.error(traceback.format_exc())
        db.update_job(job_id, status="error", error_message=error_msg[:500],
                      finished_at=datetime.now().isoformat())
