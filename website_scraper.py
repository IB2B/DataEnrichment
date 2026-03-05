"""
Website Scraper - domain-first bulk email extraction.
Based on proven Scrapling flow:
- normalize to domain
- scrape homepage first
- scrape minimal contact paths only if needed
- keep up to 5 unique emails per domain
"""

import asyncio
import html
import logging
import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

from scrapling.fetchers import AsyncFetcher

import database as db
from config import PROXY_FILE

log = logging.getLogger("enrichment.webscraper")

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
OBFUSC_AT = re.compile(r"\s*[\[\(\{]\s*(?:at|AT|@)\s*[\]\)\}]\s*")
OBFUSC_DOT = re.compile(r"\s*[\[\(\{]\s*(?:dot|DOT|\.)\s*[\]\)\}]\s*")
HTML_AT_RE = re.compile(r"&#(?:64|x40);")

JUNK_DOM = {
    "example.com", "sentry.io", "wixpress.com", "wordpress.org", "w3.org",
    "schema.org", "googleapis.com", "google.com", "facebook.com",
    "twitter.com", "cloudflare.com", "gravatar.com", "instagram.com",
    "jquery.com", "jsdelivr.net", "bootstrapcdn.com", "fontawesome.com",
    "gstatic.com", "ytimg.com", "googletagmanager.com", "doubleclick.net",
    "amazon.com", "amazonaws.com", "github.com", "linkedin.com",
}

JUNK_PREFIX = {
    "noreply", "no-reply", "donotreply", "mailer-daemon", "postmaster",
    "webmaster", "hostmaster", "abuse", "root", "daemon",
}

CONCURRENCY = 250
FETCH_TIMEOUT = 4
MAX_EMAILS_PER_DOMAIN = 5
PATHS_STEP1 = [
    "contact", "contact-us", "contatti", "about", "impressum", "kontakt",
]
CONTACT_KW = re.compile(
    r"contatt|contact|about|chi-siamo|azienda|impressum|kontakt|support|help",
    re.I,
)
MAX_DYNAMIC_CONTACT_LINKS = 3
MAX_FALLBACK_CONTACT_PATHS = 3


def _build_proxies():
    """Load proxies from proxies.txt.
    Supports:
    - ip:port:user:pass
    - ip:port
    """
    path = Path(PROXY_FILE)
    if not path.exists():
        return []

    proxies = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
            user = quote(user, safe="")
            pwd = quote(pwd, safe="")
            proxies.append(f"http://{user}:{pwd}@{ip}:{port}")
        elif len(parts) == 2:
            ip, port = parts
            proxies.append(f"http://{ip}:{port}")
    return proxies


_PROXY_LIST = _build_proxies()


def _get_proxy():
    return random.choice(_PROXY_LIST) if _PROXY_LIST else None


def _normalize_domain(url_or_domain: str) -> str:
    value = (url_or_domain or "").strip().lower()
    value = re.sub(r"^https?://", "", value)
    return value.split("/")[0].strip().rstrip(".")


def _ok_email(value: str) -> bool:
    email = value.lower().strip()
    if len(email) < 5 or len(email) > 100:
        return False
    if "@" not in email:
        return False
    pre, dom = email.rsplit("@", 1)
    if not pre or not dom or "." not in dom:
        return False
    if pre in JUNK_PREFIX or dom in JUNK_DOM:
        return False
    if dom.endswith((".png", ".jpg", ".jpeg", ".gif", ".css", ".js", ".svg", ".webp")):
        return False
    return len(pre) <= 64


def _clean_email(value: str) -> str:
    email = unquote(value).strip().lower()
    email = email.strip(" \t\r\n\"'<>[](){}.,;:")
    return email if _ok_email(email) else ""


def _decode_cfemail(hex_value: str) -> str:
    """Decode Cloudflare protected email payload from data-cfemail."""
    try:
        if not hex_value or len(hex_value) < 4 or len(hex_value) % 2:
            return ""
        key = int(hex_value[:2], 16)
        out = []
        for i in range(2, len(hex_value), 2):
            out.append(chr(int(hex_value[i:i + 2], 16) ^ key))
        return "".join(out)
    except Exception:
        return ""


def _extract_emails_from_page(page):
    found = []
    seen = set()

    def add(email: str):
        if not email or email in seen:
            return
        seen.add(email)
        found.append(email)

    try:
        for a in page.css('a[href^="mailto:"]'):
            href = a.attrib.get("href", "")
            email = _clean_email(href.replace("mailto:", "").split("?", 1)[0])
            if email:
                add(email)
    except Exception:
        pass

    # Cloudflare obfuscated email block.
    try:
        for el in page.css("[data-cfemail]"):
            encoded = (el.attrib.get("data-cfemail", "") or "").strip()
            decoded = _clean_email(_decode_cfemail(encoded))
            if decoded:
                add(decoded)
    except Exception:
        pass

    text = ""
    raw_html = ""
    try:
        text = page.get_all_text() or ""
    except Exception:
        pass
    try:
        raw_html = str(page.body) if hasattr(page, "body") else (page.text or "")
    except Exception:
        pass

    blobs = [text, raw_html, html.unescape(raw_html)]
    if raw_html:
        blobs.append(html.unescape(HTML_AT_RE.sub("@", raw_html)))
    if text:
        deobf = OBFUSC_AT.sub("@", OBFUSC_DOT.sub(".", text))
        blobs.append(deobf)

    for blob in blobs:
        if not blob:
            continue
        for match in EMAIL_RE.findall(blob):
            email = _clean_email(match)
            if email:
                add(email)

    return found


def _find_contact_links(page, base_url: str, max_links: int = MAX_DYNAMIC_CONTACT_LINKS):
    """Discover relevant internal contact/about URLs from homepage."""
    links = []
    seen = set()
    base_domain = urlparse(base_url).netloc.lower()

    try:
        for a in page.css("a[href]"):
            href = (a.attrib.get("href", "") or "").strip()
            txt = (getattr(a, "text", "") or "").strip().lower()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            abs_url = href if href.startswith("http") else urljoin(base_url, href)
            parsed = urlparse(abs_url)
            if parsed.netloc.lower() != base_domain:
                continue
            normalized = abs_url.split("#")[0].rstrip("/")
            if normalized in seen:
                continue
            if CONTACT_KW.search(txt) or CONTACT_KW.search(parsed.path):
                seen.add(normalized)
                links.append(normalized)
                if len(links) >= max_links:
                    break
    except Exception:
        pass

    return links


async def _fetch(url: str, retries: int = 1, allow_direct_fallback: bool = False):
    """Fetch URL with fast retries and rotating proxies."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    }

    for _ in range(retries + 1):
        try:
            page = await AsyncFetcher.get(
                url,
                stealthy_headers=False,
                headers=headers,
                follow_redirects=True,
                timeout=FETCH_TIMEOUT,
                proxy=_get_proxy(),
            )
            if page.status in (200, 201, 202):
                return page
            if page.status in (404, 410, 500, 502):
                return None
        except Exception:
            continue

    if allow_direct_fallback:
        try:
            page = await AsyncFetcher.get(
                url,
                stealthy_headers=False,
                headers=headers,
                follow_redirects=True,
                timeout=FETCH_TIMEOUT,
            )
            if page.status in (200, 201, 202):
                return page
        except Exception:
            pass
    return None


async def _fetch_homepage(domain: str):
    """Try minimal homepage variants for speed."""
    candidates = [
        f"https://{domain}/",
        f"http://{domain}/",
    ]
    if not domain.startswith("www."):
        candidates.append(f"https://www.{domain}/")

    for candidate in candidates:
        page = await _fetch(candidate, retries=0, allow_direct_fallback=False)
        if page:
            return candidate, page
    return f"https://{domain}/", None


async def _scrape_one_domain(input_value: str, scrape_id: int):
    domain = _normalize_domain(input_value)
    if not domain:
        db.save_website_result(scrape_id, input_value, "", "", "", "", "")
        return 0

    homepage, page = await _fetch_homepage(domain)
    collected = []
    seen = set()

    def add_batch(batch):
        for email in batch:
            if email in seen:
                continue
            seen.add(email)
            collected.append(email)
            if len(collected) >= MAX_EMAILS_PER_DOMAIN:
                break

    if page:
        add_batch(_extract_emails_from_page(page))

    if len(collected) < MAX_EMAILS_PER_DOMAIN:
        candidates = []
        if page:
            candidates.extend(_find_contact_links(page, homepage))
        candidates.extend([f"https://{domain}/{path}" for path in PATHS_STEP1[:MAX_FALLBACK_CONTACT_PATHS]])

        unique_candidates = []
        seen_urls = set()
        for url in candidates:
            n = url.rstrip("/")
            if n not in seen_urls and n != homepage.rstrip("/"):
                seen_urls.add(n)
                unique_candidates.append(url)

        async def _fetch_and_extract(step_url):
            p = await _fetch(step_url, retries=0, allow_direct_fallback=False)
            if not p:
                return []
            return _extract_emails_from_page(p)

        results = await asyncio.gather(
            *[_fetch_and_extract(u) for u in unique_candidates[:MAX_DYNAMIC_CONTACT_LINKS + MAX_FALLBACK_CONTACT_PATHS]],
            return_exceptions=True
        )
        for result in results:
            if isinstance(result, list):
                add_batch(result)
            if len(collected) >= MAX_EMAILS_PER_DOMAIN:
                break

    db.save_website_result(
        scrape_id,
        homepage,
        ", ".join(collected),
        "",
        "",
        "",
        "",
    )
    log.info(f"  {domain} -> {len(collected)} emails")
    return len(collected)


async def run_website_scrape(scrape_id: int):
    """Main entry point - handles up to 1000 domains."""
    import json

    scrape = db.get_website_scrape(scrape_id)
    if not scrape:
        log.error(f"Website scrape #{scrape_id} not found")
        return

    urls = json.loads(scrape["urls"])
    total = len(urls)
    log.info(f"Website scrape #{scrape_id} starting - {total} URLs, {len(_PROXY_LIST)} proxies")

    try:
        sem = asyncio.Semaphore(CONCURRENCY)
        processed = 0

        async def bounded(value):
            async with sem:
                return await _scrape_one_domain(value, scrape_id)

        batch_size = CONCURRENCY
        for i in range(0, total, batch_size):
            batch = urls[i:i + batch_size]
            await asyncio.gather(*[bounded(u) for u in batch], return_exceptions=True)
            processed += len(batch)
            db.update_website_scrape(scrape_id, processed=processed)

            current = db.get_website_scrape(scrape_id)
            if current and current["status"] != "running":
                break

        db.update_website_scrape(
            scrape_id,
            status="done",
            processed=processed,
            finished_at=datetime.now().isoformat(),
        )
        log.info(f"Website scrape #{scrape_id} DONE - {processed}/{total} processed")
    except Exception as e:
        import traceback

        error_msg = f"{type(e).__name__}: {str(e)}"
        log.error(f"Website scrape #{scrape_id} FAILED: {error_msg}")
        log.error(traceback.format_exc())
        db.update_website_scrape(
            scrape_id,
            status="error",
            error_message=error_msg[:500],
            finished_at=datetime.now().isoformat(),
        )
