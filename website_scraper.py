"""
Website Scraper — aiohttp + BeautifulSoup engine
Extracts emails, phone numbers, names, and social media links from websites.
"""

import re, asyncio, random, logging
from datetime import datetime
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

import database as db
from enrichment_worker import KNOWN_FIRST_NAMES, is_name

log = logging.getLogger("enrichment.webscraper")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-.]?)?"
    r"(?:\(?\d{2,4}\)?[\s\-.]?)"
    r"\d{3,4}[\s\-.]?\d{3,4}"
)
SOCIAL_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:linkedin\.com/(?:in|company)/[^\s\"'<>]+|"
    r"facebook\.com/[^\s\"'<>]+|"
    r"twitter\.com/[^\s\"'<>]+|"
    r"x\.com/[^\s\"'<>]+|"
    r"instagram\.com/[^\s\"'<>]+)",
    re.I,
)

JUNK_DOMAINS = {"example.com", "sentry.io", "wixpress.com", "wordpress.org", "w3.org",
                "schema.org", "googleapis.com", "google.com", "cloudflare.com", "gravatar.com"}

TEAM_KW = re.compile(
    r"chi.siamo|about|team|staff|contatt|contact|azienda|company|persone|people", re.I)

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
]


def _clean_email(e):
    e = e.lower().strip()
    domain = e.split("@")[-1]
    if domain in JUNK_DOMAINS:
        return None
    if domain.endswith((".png", ".jpg", ".css", ".js", ".gif")):
        return None
    if len(e.split("@")[0]) < 2:
        return None
    return e


def _clean_phone(p):
    digits = re.sub(r"[^\d+]", "", p)
    if len(digits) < 7 or len(digits) > 16:
        return None
    return p.strip()


def _extract_logo(soup, base_url=""):
    """Extract logo URL from the page using common patterns."""
    # 1. Open Graph image (often the logo or brand image)
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return urljoin(base_url, og["content"])

    # 2. <img> tags with logo-related class/id/alt
    for img in soup.find_all("img", src=True):
        attrs_text = " ".join([
            img.get("class", [""])[0] if isinstance(img.get("class"), list) else img.get("class", ""),
            img.get("id", ""),
            img.get("alt", ""),
        ]).lower()
        if "logo" in attrs_text:
            return urljoin(base_url, img["src"])

    # 3. <link rel="icon"> (favicon)
    for rel in ["icon", "shortcut icon", "apple-touch-icon"]:
        link = soup.find("link", rel=lambda r: r and rel in " ".join(r).lower() if isinstance(r, list) else r and rel in r.lower())
        if link and link.get("href"):
            return urljoin(base_url, link["href"])

    # 4. Fallback: /favicon.ico
    if base_url:
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"

    return ""


def _extract_from_soup(soup, base_url=""):
    text = soup.get_text(separator=" ", strip=True)
    domain = urlparse(base_url).netloc.lower().replace("www.", "") if base_url else ""

    # Emails
    emails = set()
    for e in EMAIL_RE.findall(text):
        cleaned = _clean_email(e)
        if cleaned:
            emails.add(cleaned)
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("mailto:"):
            e = a["href"].replace("mailto:", "").split("?")[0].strip()
            cleaned = _clean_email(e)
            if cleaned:
                emails.add(cleaned)

    # Phones
    phones = set()
    for p in PHONE_RE.findall(text):
        cleaned = _clean_phone(p)
        if cleaned:
            phones.add(cleaned)
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("tel:"):
            raw = a["href"].replace("tel:", "").strip()
            if raw:
                phones.add(raw)

    # Names
    names = set()
    for tag in soup.find_all(["h2", "h3", "h4", "h5", "strong", "b", "span"]):
        t = tag.get_text(strip=True)
        if is_name(t):
            names.add(t)

    # Social links
    social = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if SOCIAL_RE.match(href):
            social.add(href.split("?")[0])
    for m in SOCIAL_RE.findall(text):
        social.add(m.split("?")[0])

    # Logo
    logo_url = _extract_logo(soup, base_url)

    return emails, phones, names, social, logo_url


def _find_subpages(soup, base_url):
    """Find internal team/contact/about pages."""
    domain = urlparse(base_url).netloc.lower()
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True).lower()
        if TEAM_KW.search(text) or TEAM_KW.search(href):
            if href.startswith("/"):
                href = urljoin(base_url, href)
            elif not href.startswith("http"):
                href = urljoin(base_url, href)
            if domain in urlparse(href).netloc.lower():
                links.add(href)
    # Also try common paths
    for path in ["/contact", "/contatti", "/chi-siamo", "/about", "/team"]:
        links.add(urljoin(base_url.rstrip("/") + "/", path))
    return list(links)[:3]


async def _fetch_page(session, url, timeout=8):
    headers = {"User-Agent": random.choice(UA_LIST),
               "Accept": "text/html,*/*;q=0.8",
               "Accept-Language": "it-IT,it;q=0.9,en;q=0.7"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout),
                               ssl=False, allow_redirects=True) as resp:
            if resp.status == 200:
                text = await resp.text(errors="replace")
                if len(text) > 300:
                    return BeautifulSoup(text, "lxml")
    except Exception:
        pass
    return None


async def _scrape_one_url(session, url, scrape_id):
    """Scrape a single URL + its sub-pages."""
    if not url.startswith("http"):
        url = "https://" + url

    all_emails, all_phones, all_names, all_social = set(), set(), set(), set()
    logo_url = ""

    soup = await _fetch_page(session, url)
    if soup:
        emails, phones, names, social, logo = _extract_from_soup(soup, url)
        all_emails.update(emails)
        all_phones.update(phones)
        all_names.update(names)
        all_social.update(social)
        if logo:
            logo_url = logo

        # Follow sub-pages
        subpages = _find_subpages(soup, url)
        for sub_url in subpages:
            sub_soup = await _fetch_page(session, sub_url, timeout=6)
            if sub_soup:
                e, p, n, s, _ = _extract_from_soup(sub_soup, url)
                all_emails.update(e)
                all_phones.update(p)
                all_names.update(n)
                all_social.update(s)
            await asyncio.sleep(0.1)

    db.save_website_result(
        scrape_id, url,
        ", ".join(sorted(all_emails)),
        ", ".join(sorted(all_phones)),
        ", ".join(sorted(all_names)),
        ", ".join(sorted(all_social)),
        logo_url,
    )
    return len(all_emails)


async def run_website_scrape(scrape_id: int):
    """Main entry point for website scraping."""
    import json

    scrape = db.get_website_scrape(scrape_id)
    if not scrape:
        log.error(f"Website scrape #{scrape_id} not found")
        return

    urls = json.loads(scrape["urls"])
    total = len(urls)
    log.info(f"Website scrape #{scrape_id} starting — {total} URLs")

    try:
        connector = aiohttp.TCPConnector(limit=20, limit_per_host=3, ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=connector) as session:
            sem = asyncio.Semaphore(20)
            processed = 0

            async def bounded(url):
                async with sem:
                    return await _scrape_one_url(session, url, scrape_id)

            # Process in batches
            for i in range(0, total, 20):
                batch = urls[i:i+20]
                await asyncio.gather(*[bounded(u) for u in batch])
                processed += len(batch)
                db.update_website_scrape(scrape_id, processed=processed)

                # Check if stopped
                current = db.get_website_scrape(scrape_id)
                if current and current["status"] != "running":
                    break

        db.update_website_scrape(scrape_id, status="done", processed=processed,
                                 finished_at=datetime.now().isoformat())
        log.info(f"Website scrape #{scrape_id} DONE — {processed}/{total} URLs processed")

    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}"
        log.error(f"Website scrape #{scrape_id} FAILED: {error_msg}")
        log.error(traceback.format_exc())
        db.update_website_scrape(scrape_id, status="error", error_message=error_msg[:500],
                                 finished_at=datetime.now().isoformat())
