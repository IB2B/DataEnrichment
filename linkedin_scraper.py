"""
LinkedIn People Scraper — Playwright-based async engine
with contact info enrichment, website email crawling, and SerpAPI search.
"""

import re, random, asyncio, json, logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from scrapling.fetchers import AsyncFetcher
import database as db
from config import LINKEDIN_COOKIES_DIR, DEFAULT_PAGE_DELAY_MIN, DEFAULT_PAGE_DELAY_MAX
from enrichment_worker import ProxyPool, parse_proxy_for_playwright, build_proxy_rotator

log = logging.getLogger("enrichment.linkedin")

# Fixed UA shared between manual login and scraper so LinkedIn doesn't invalidate the session
LINKEDIN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# ── SerpAPI blocklists ──

_EMAIL_BLOCKLIST = {
    "example.com", "email.com", "domain.com", "yoursite.com",
    "company.com", "test.com", "sentry.io", "webpack.js.org",
    "wixpress.com", "placeholder.com", "w3.org", "schema.org",
    "json.org", "mozilla.org", "apache.org", "creativecommons.org",
}
_JUNK_EXTENSIONS = {'.png', '.jpg', '.gif', '.js', '.css', '.svg', '.woff'}
_JUNK_DOMAINS = {
    'google.com', 'gstatic.com', 'googleapis.com',
    'bing.com', 'microsoft.com', 'duckduckgo.com',
    'msn.com', 'live.com',
}

# ── JavaScript for bulk extraction of search results ──

JS_EXTRACT_ALL = """() => {
function extractAll() {
    const results = [];
    let cards = document.querySelectorAll('div[data-view-name="people-search-result"]');
    if (!cards.length) cards = document.querySelectorAll('li.reusable-search__result-container');
    if (!cards.length) {
        const list = document.querySelector('div[role="list"]');
        if (list) cards = list.children;
    }
    for (const card of cards) {
        try {
            let name = '', profileUrl = '', title = '', company = '', location = '';
            let nameLink = card.querySelector('a[data-view-name="search-result-lockup-title"]');
            if (!nameLink) nameLink = card.querySelector('a[href*="/in/"]');
            if (nameLink) {
                const visSpan = nameLink.querySelector('span[aria-hidden="true"]');
                name = visSpan ? visSpan.textContent.trim() : nameLink.textContent.trim().split('\\n')[0].trim();
                const href = nameLink.getAttribute('href') || '';
                if (href.includes('/in/')) profileUrl = href.split('?')[0];
            }
            if (!name || name.toLowerCase() === 'linkedin member') {
                if (!name) continue;
                name = '(hidden)';
            }
            const allText = card.innerText || '';
            const lines = allText.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
            const skipPatterns = [
                /^[\\u2022\\u00B7\\s]*\\d*(st|nd|rd|th)\\+?$/i,
                /^[\\u2022\\u00B7]/, /^\\d+(st|nd|rd|th)/i,
                /^(Connect|Message|Follow|Pending|Send|InMail)$/i,
                /^(Connetti|Segui|Messaggio|Invia)$/i,
                /^(Se connecter|Suivre|Envoyer)$/i,
                /^(Vernetzen|Folgen|Nachricht)$/i,
                /^(Summary|Riepilogo|Résumé):?/i, /^\\.\\.\\./,
            ];
            function shouldSkip(line) {
                if (line === name) return true;
                if (line.includes(name) && (line.includes('\\u00B7') || line.includes('\\u2022'))) return true;
                if (line.length < 6 && /\\d/.test(line)) return true;
                for (const p of skipPatterns) { if (p.test(line)) return true; }
                return false;
            }
            const meaningful = [];
            for (const line of lines) {
                if (shouldSkip(line)) continue;
                if (line.length > 300) continue;
                meaningful.push(line);
            }
            let currentLine = '';
            const currentPatterns = [
                /^(?:Current|Attuale|Actuel|Actual|Aktuell)[:\\s]+(.+)/i,
                /^(?:Past|Passato|Passé|Anterior)[:\\s]+(.+)/i,
            ];
            for (const line of meaningful) {
                for (const cp of currentPatterns) {
                    const cm = line.match(cp);
                    if (cm) { currentLine = cm[1].trim(); break; }
                }
                if (currentLine) break;
            }
            if (currentLine) {
                const companySeps = [' at ', ' presso ', ' chez ', ' bei ', ' en '];
                for (const sep of companySeps) {
                    const idx = currentLine.indexOf(sep);
                    if (idx !== -1) { company = currentLine.substring(idx + sep.length).trim(); break; }
                }
                if (!company) company = currentLine;
            }
            const contentLines = meaningful.filter(l => {
                if (/^(?:Current|Attuale|Actuel|Actual|Aktuell|Past|Passato|Passé|Anterior)[:\\s]/i.test(l)) return false;
                if (/^(?:Summary|Riepilogo|Résumé)[:\\s]/i.test(l)) return false;
                return true;
            });
            const locationPattern = /^[A-Z\\u00C0-\\u00DA].*,\\s*[A-Z\\u00C0-\\u00DA]/;
            const locationKeywords = /\\b(Area|Metropolitan|Region|Greater|Province|Provincia)\\b/i;
            const countryOnly = /^(Italy|France|Germany|Spain|United States|United Kingdom|Canada|Australia|Brasil|India|Japan|China|Netherlands|Belgium|Switzerland|Austria|Portugal|Sweden|Norway|Denmark|Finland|Ireland|Poland|Greece|Turkey|Mexico|Argentina|Colombia|Chile|Egypt|Morocco|South Africa|UAE|Saudi Arabia|Singapore|Malaysia|Indonesia|Philippines|Thailand|Vietnam|South Korea|Taiwan|New Zealand|Czech Republic|Romania|Hungary|Croatia|Bulgaria|Serbia|Ukraine|Russia|Israel|Lebanon|Jordan|Tunisia|Algeria|Libya|Nigeria|Kenya|Ghana|Pakistan|Bangladesh|Sri Lanka)$/i;
            function looksLikeLocation(line) {
                if (locationPattern.test(line)) return true;
                if (locationKeywords.test(line)) return true;
                if (countryOnly.test(line.trim())) return true;
                return false;
            }
            for (const line of contentLines) {
                if (!title && !looksLikeLocation(line)) title = line;
                else if (!location && looksLikeLocation(line)) location = line;
                if (title && location) break;
            }
            if (title && !location) {
                let pastTitle = false;
                for (const line of contentLines) {
                    if (line === title) { pastTitle = true; continue; }
                    if (pastTitle && line.length < 80) { location = line; break; }
                }
            }
            if (!title && contentLines.length > 0) title = contentLines[0];
            if (!company && title) {
                const titleSeps = [' at ', ' presso ', ' chez ', ' bei '];
                for (const sep of titleSeps) {
                    const idx = title.indexOf(sep);
                    if (idx !== -1) {
                        company = title.substring(idx + sep.length).trim();
                        title = title.substring(0, idx).trim();
                        break;
                    }
                }
            }
            if (!profileUrl) {
                const anyLink = card.querySelector('a[href*="/in/"]');
                if (anyLink) profileUrl = (anyLink.getAttribute('href') || '').split('?')[0];
            }
            if (profileUrl && !profileUrl.startsWith('http')) {
                profileUrl = 'https://www.linkedin.com' + profileUrl;
            }
            results.push({ name, title, company, location, profile_url: profileUrl });
        } catch(e) {}
    }
    return results;
}
return extractAll();
}"""

# ── JavaScript for contact overlay extraction ──

JS_CONTACT = """() => {
function extractContact() {
    const info = {};
    const body = document.querySelector('div.artdeco-modal__content')
        || document.querySelector('div[data-view-name="profile-card"]')
        || document.body;
    const text = body.innerText || '';

    // Email
    const mailLinks = body.querySelectorAll('a[href^="mailto:"]');
    const emails = [];
    mailLinks.forEach(a => {
        const email = a.getAttribute('href').replace('mailto:', '').trim();
        if (email && !emails.includes(email)) emails.push(email);
    });
    if (!emails.length) {
        const emailRx = /[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g;
        (text.match(emailRx) || []).forEach(e => { if (!emails.includes(e)) emails.push(e); });
    }
    info.email = emails.join('; ');

    // Phone — only from tel: links (never regex on page text to avoid matching URL numbers)
    const telLinks = body.querySelectorAll('a[href^="tel:"]');
    const phones = [];
    telLinks.forEach(a => {
        const phone = a.getAttribute('href').replace('tel:', '').trim();
        if (phone && phone.length >= 7 && !phones.includes(phone)) phones.push(phone);
    });
    info.phone = phones.join('; ');

    // Website
    const allLinks = body.querySelectorAll('a[href]');
    const websites = [];
    allLinks.forEach(a => {
        const href = a.getAttribute('href') || '';
        if (href.startsWith('mailto:') || href.startsWith('tel:')) return;
        if (href.includes('linkedin.com')) return;
        if (href.startsWith('http') && !websites.includes(href)) websites.push(href);
    });
    info.website = websites.join('; ');
    return info;
}
return extractContact();
}"""

# ── JavaScript for extracting emails from a website page ──

JS_WEBSITE_EMAILS = """() => {
const text = document.body.innerText || '';
const html = document.body.innerHTML || '';
const emails = new Set();
document.querySelectorAll('a[href^="mailto:"]').forEach(a => {
    const e = a.getAttribute('href').replace('mailto:', '').split('?')[0].trim();
    if (e) emails.add(e);
});
const rx = /[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g;
(text.match(rx) || []).forEach(e => emails.add(e));
(html.match(rx) || []).forEach(e => {
    if (!e.includes('example.com') && !e.includes('sentry') && !e.includes('webpack')
        && !e.includes('.png') && !e.includes('.jpg') && !e.endsWith('.js'))
        emails.add(e);
});
return [...emails];
}"""


async def _human_delay(low=1.0, high=3.0):
    await asyncio.sleep(random.uniform(low, high))


async def _extract_contact_overlay(page) -> dict:
    """Extract contact info from the currently open /overlay/contact-info/ modal."""
    try:
        return await page.evaluate(JS_CONTACT) or {}
    except Exception:
        return {}


_email_rx = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

_WEBSITE_JUNK_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "wordpress.org", "w3.org",
    "schema.org", "googleapis.com", "google.com", "facebook.com",
    "twitter.com", "cloudflare.com", "gravatar.com", "instagram.com",
}

_WEBSITE_GENERIC_PREFIXES = {
    "info", "contact", "contatti", "admin", "support", "help", "noreply",
    "no-reply", "postmaster", "webmaster", "sales", "marketing", "office",
    "newsletter", "privacy", "abuse", "billing", "jobs", "hr",
}


def _is_good_email(email: str, domain: str = "") -> bool:
    """Filter out junk/generic emails."""
    email = email.lower()
    if "@" not in email:
        return False
    pre, dom = email.split("@", 1)
    if dom in _WEBSITE_JUNK_DOMAINS:
        return False
    if dom.endswith((".png", ".jpg", ".css", ".js", ".gif", ".svg")):
        return False
    if pre in _WEBSITE_GENERIC_PREFIXES:
        return False
    if len(pre) < 2:
        return False
    if domain and dom != domain:
        return False
    return True


async def _scrapling_fetch(url: str, proxy_rotator=None, timeout: int = 10):
    """Fetch a URL with Scrapling. Returns page or None."""
    try:
        proxy = proxy_rotator.get_proxy() if proxy_rotator else None
        page = await AsyncFetcher.get(
            url,
            stealthy_headers=True,
            follow_redirects=True,
            timeout=timeout,
            proxy=proxy,
            verify=False,
        )
        if page.status in (200, 202):
            return page
        else:
            log.debug(f"  _scrapling_fetch {url} — status {page.status}")
    except Exception as e:
        log.debug(f"  _scrapling_fetch {url} — error: {e}")
    return None


async def _extract_emails_from_page(page, domain: str = "") -> set:
    """Extract emails from a Scrapling page response."""
    found = set()
    try:
        for a in page.css('a[href^="mailto:"]'):
            e = a.attrib.get('href', '').replace('mailto:', '').split('?')[0].strip().lower()
            if _is_good_email(e, domain):
                found.add(e)
        for e in _email_rx.findall(page.get_all_text()):
            e = e.lower()
            if _is_good_email(e, domain):
                found.add(e)
        # Also search raw HTML for emails (sometimes in attributes, not visible text)
        try:
            raw_html = str(page.body) if hasattr(page, 'body') else ""
            for e in _email_rx.findall(raw_html):
                e = e.lower()
                if _is_good_email(e, domain):
                    found.add(e)
        except Exception:
            pass
    except Exception:
        pass
    return found


async def _find_email_on_website(page_unused, website_url: str, proxy_rotator=None) -> str:
    """Scrape a website for email addresses using Scrapling (fast, no browser needed)."""
    url = website_url.split(";")[0].strip()
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url

    domain = urlparse(url).netloc.lower().replace("www.", "")
    base = url.rstrip("/")

    # Fetch homepage first (most likely to have emails or links to contact page)
    homepage = await _scrapling_fetch(url, proxy_rotator, timeout=8)
    if homepage:
        emails = await _extract_emails_from_page(homepage, domain)
        if emails:
            return "; ".join(sorted(emails))

    # Fetch contact/about pages in parallel
    subpages = [base + p for p in ["/contact", "/contacts", "/contatti", "/chi-siamo"]]
    tasks = [_scrapling_fetch(u, proxy_rotator, timeout=6) for u in subpages]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception) or result is None:
            continue
        emails = await _extract_emails_from_page(result, domain)
        if emails:
            return "; ".join(sorted(emails))

    return ""


async def _search_email_web(name: str, company: str = "", proxy_rotator=None) -> str:
    """Search Bing (+ Google) for a person's email using Scrapling (free, no API key).
    Also visits top result pages (ContactOut, RocketReach, etc.) to extract emails.
    Tries name+company first, then name-only as fallback."""
    if not name or name == "(hidden)":
        return ""

    from urllib.parse import quote_plus

    _email_sites = {"contactout.com", "rocketreach.co", "lusha.com", "signalhire.com",
                    "apollo.io", "hunter.io", "snov.io", "getprospect.com",
                    "zoominfo.com", "leadiq.com", "clearbit.com"}

    found_emails = set()
    result_urls = []

    # Build queries: name+company first, then name-only fallback
    queries = []
    if company:
        queries.append(f'"{name}" "{company}" email')
    queries.append(f'"{name}" email')

    for query in queries:
        encoded = quote_plus(query)
        log.info(f"    Web search: {query}")

        # Search Bing (reliable) + Google (often 429, but try anyway) in parallel
        search_tasks = [
            _scrapling_fetch(f"https://www.bing.com/search?q={encoded}&count=15", proxy_rotator, timeout=10),
            _scrapling_fetch(f"https://www.google.com/search?q={encoded}&num=10", proxy_rotator, timeout=8),
        ]
        pages = await asyncio.gather(*search_tasks, return_exceptions=True)

        for i, page in enumerate(pages):
            engine = "Bing" if i == 0 else "Google"
            if isinstance(page, Exception) or page is None:
                log.debug(f"    {engine} search failed or returned None")
                continue
            try:
                # Extract emails from visible text
                page_text = page.get_all_text()
                for match in _email_rx.findall(page_text):
                    email = match.lower()
                    if _is_good_email(email):
                        found_emails.add(email)
                        log.info(f"    Found email in {engine} snippet: {email}")

                # Also extract from raw HTML (emails sometimes in href/title but not visible text)
                try:
                    raw_html = str(page.body) if hasattr(page, 'body') else ""
                    for match in _email_rx.findall(raw_html):
                        email = match.lower()
                        if _is_good_email(email):
                            found_emails.add(email)
                except Exception:
                    pass

                # Collect email-rich site URLs from search results
                for a in page.css('a[href]'):
                    href = a.attrib.get('href', '')
                    # Bing wraps URLs, try to extract the real URL
                    if 'bing.com/ck/' in href:
                        continue
                    if href.startswith('http') and any(site in href for site in _email_sites):
                        result_urls.append(href)
                    # Also grab regular result links that aren't search engines
                    elif href.startswith('http') and not any(d in href for d in ('google.', 'bing.', 'microsoft.')):
                        pass  # don't visit all links, only email-rich sites
            except Exception as e:
                log.debug(f"    {engine} parse error: {e}")
                continue

        if found_emails:
            return "; ".join(sorted(found_emails))

    # If no emails in snippets, visit email-rich sites in parallel (max 4)
    if result_urls:
        unique_urls = list(dict.fromkeys(result_urls))[:4]
        log.info(f"    Visiting {len(unique_urls)} email-rich sites: {unique_urls}")
        visit_tasks = [_scrapling_fetch(u, proxy_rotator, timeout=8) for u in unique_urls]
        visit_results = await asyncio.gather(*visit_tasks, return_exceptions=True)

        for page in visit_results:
            if isinstance(page, Exception) or page is None:
                continue
            try:
                emails = await _extract_emails_from_page(page)
                found_emails.update(emails)
            except Exception:
                continue

    return "; ".join(sorted(found_emails)) if found_emails else ""


def _google_dork_email(name: str, company: str = "", serpapi_key: str = "") -> str:
    """Use SerpAPI to search Google for a person's email (synchronous)."""
    if not name or name == "(hidden)" or not serpapi_key:
        return ""
    try:
        from serpapi import GoogleSearch
    except ImportError:
        return ""

    query = f'"{name}" email'
    email_rx = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

    try:
        params = {
            "engine": "google", "q": query, "num": "10",
            "hl": "en", "api_key": serpapi_key,
        }
        search = GoogleSearch(params)
        results = search.get_dict()
        found_emails = set()

        for item in results.get("organic_results", []):
            text = " ".join([
                item.get("title", ""), item.get("snippet", ""), item.get("link", ""),
            ])
            if "rich_snippet" in item:
                rs = item["rich_snippet"]
                if isinstance(rs, dict):
                    text += " " + json.dumps(rs)
            for match in email_rx.findall(text):
                email = match.lower()
                domain = email.split("@")[1] if "@" in email else ""
                if domain in _EMAIL_BLOCKLIST or domain in _JUNK_DOMAINS:
                    continue
                if any(email.endswith(ext) for ext in _JUNK_EXTENSIONS):
                    continue
                found_emails.add(email)

        for key in ("answer_box", "knowledge_graph"):
            blob = results.get(key)
            if blob and isinstance(blob, dict):
                for match in email_rx.findall(json.dumps(blob)):
                    email = match.lower()
                    domain = email.split("@")[1] if "@" in email else ""
                    if domain not in _EMAIL_BLOCKLIST and domain not in _JUNK_DOMAINS:
                        found_emails.add(email)

        if found_emails:
            return "; ".join(sorted(found_emails))
    except Exception:
        pass
    return ""


async def _enrich_one_profile(page, profile: dict, serpapi_key: str, scrape_id: int, idx: int, total: int, proxy_rotator=None):
    """Enrich a single profile: LinkedIn overlay + Scrapling website/search."""
    url = profile.get("profile_url", "")
    name = profile.get("full_name", "?")

    profile.setdefault("email", "")
    profile.setdefault("phone", "")
    profile.setdefault("website", "")
    profile.setdefault("website_email", "")
    profile.setdefault("google_email", "")

    if not url or url == "(hidden)":
        log.info(f"  Scrape #{scrape_id} — [{idx}/{total}] {name} — no profile URL, skipping")
        return

    # Visit contact overlay (must use browser — sequential)
    contact_url = url.rstrip("/") + "/overlay/contact-info/"
    log.info(f"  Scrape #{scrape_id} — [{idx}/{total}] {name} — fetching contact info")

    try:
        await page.goto(contact_url, wait_until="domcontentloaded", timeout=15000)
        await _human_delay(0.8, 1.5)

        info = await _extract_contact_overlay(page)
        profile["email"] = info.get("email", "")
        profile["phone"] = info.get("phone", "")
        profile["website"] = info.get("website", "")

        parts = []
        if info.get("email"):
            parts.append(f"email={info['email']}")
        if info.get("phone"):
            parts.append(f"phone={info['phone']}")
        if info.get("website"):
            parts.append(f"web={info['website']}")
        log.info(f"    Contact: {' | '.join(parts) if parts else '(none)'}")

    except Exception as exc:
        log.warning(f"    Contact overlay error for {name}: {exc}")

    # Run Scrapling enrichment tasks in parallel (doesn't use the browser)
    scrapling_tasks = []
    website = profile.get("website", "")
    company = profile.get("company", "")

    # Always search web for email
    if not profile["email"]:
        if website:
            scrapling_tasks.append(("website", _find_email_on_website(None, website, proxy_rotator)))
        scrapling_tasks.append(("search", _search_email_web(name, company, proxy_rotator)))

    if scrapling_tasks:
        results = await asyncio.gather(
            *[task for _, task in scrapling_tasks],
            return_exceptions=True,
        )
        for (label, _), result in zip(scrapling_tasks, results):
            if isinstance(result, Exception) or not result:
                continue
            if label == "website" and not profile["website_email"]:
                profile["website_email"] = result
                log.info(f"    Website email: {result}")
            elif label == "search" and not profile["google_email"]:
                profile["google_email"] = result
                log.info(f"    Web search email: {result}")

    # SerpAPI as last resort (only if nothing found and key configured)
    has_email = profile["email"] or profile["website_email"] or profile["google_email"]
    if not has_email and serpapi_key:
        try:
            loop = asyncio.get_event_loop()
            google_email = await loop.run_in_executor(
                None, _google_dork_email, name, company, serpapi_key
            )
            if google_email:
                profile["google_email"] = google_email
                log.info(f"    SerpAPI email: {google_email}")
        except Exception:
            pass


async def _enrich_contacts(page, profiles: list, serpapi_key: str, scrape_id: int, proxy_rotator=None):
    """
    Enrich each profile: LinkedIn overlay (browser) + website/search (Scrapling, parallel).
    """
    total = len(profiles)
    for idx, profile in enumerate(profiles, 1):
        try:
            await _enrich_one_profile(page, profile, serpapi_key, scrape_id, idx, total, proxy_rotator)
        except Exception as exc:
            log.warning(f"    Enrichment error for {profile.get('full_name', '?')}: {exc}")


async def run_linkedin_scrape(scrape_id: int):
    """Main LinkedIn scraping function."""
    from playwright.async_api import async_playwright

    scrape = db.get_linkedin_scrape(scrape_id)
    if not scrape:
        log.error(f"LinkedIn scrape #{scrape_id} not found")
        return

    search_url = scrape["search_url"]
    max_pages = scrape["max_pages"] or 100
    li_email = db.get_setting("linkedin_email", "")
    li_password = db.get_setting("linkedin_password", "")
    serpapi_key = db.get_setting("serpapi_key", "")

    delay_min = float(db.get_setting("page_delay_min", str(DEFAULT_PAGE_DELAY_MIN)))
    delay_max = float(db.get_setting("page_delay_max", str(DEFAULT_PAGE_DELAY_MAX)))

    log.info(f"LinkedIn scrape #{scrape_id} starting — max_pages={max_pages}")

    pw = await async_playwright().start()

    try:
        ua = LINKEDIN_UA
        pp = ProxyPool()
        proxy_dict = parse_proxy_for_playwright(pp.get())
        scrapling_proxy_rotator = build_proxy_rotator()

        # Headless mode — works on VPS without a display.
        # Both login and scraping use headless so cookies are compatible.
        launch_kwargs = dict(
            user_data_dir=str(LINKEDIN_COOKIES_DIR),
            headless=True,
            args=[
                "--headless=new",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-gpu",
                "--disable-notifications",
            ],
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        if proxy_dict:
            launch_kwargs["proxy"] = proxy_dict

        context = await pw.chromium.launch_persistent_context(**launch_kwargs)

        page = context.pages[0] if context.pages else await context.new_page()

        # Override navigator.webdriver
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        # Inject li_at cookie from database if available (set via Settings page)
        li_at_cookie = db.get_setting("linkedin_li_at", "")
        if li_at_cookie:
            log.info(f"LinkedIn scrape #{scrape_id} — injecting li_at cookie from Settings")
            await context.add_cookies([
                {
                    "name": "li_at",
                    "value": li_at_cookie,
                    "domain": ".linkedin.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
                {
                    "name": "JSESSIONID",
                    "value": f"ajax:{li_at_cookie[:16]}",
                    "domain": ".linkedin.com",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "None",
                },
            ])

        # Check if already logged in
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2, 4))

        current_url = page.url
        log.info(f"LinkedIn scrape #{scrape_id} — feed redirect URL: {current_url}")
        needs_login = "/login" in current_url or "/uas/" in current_url or "signin" in current_url
        hit_checkpoint = "checkpoint" in current_url or "challenge" in current_url

        if hit_checkpoint:
            # Cookies exist but LinkedIn wants verification — wait and retry
            log.warning(f"LinkedIn scrape #{scrape_id} — checkpoint detected, waiting and retrying...")
            await asyncio.sleep(random.uniform(5, 8))
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(3, 5))
            current_url = page.url
            needs_login = "/login" in current_url or "/uas/" in current_url or "signin" in current_url
            hit_checkpoint = "checkpoint" in current_url or "challenge" in current_url

            if hit_checkpoint:
                db.update_linkedin_scrape(scrape_id, status="error",
                    error_message="LinkedIn requires verification (CAPTCHA/2FA). Go to Settings > LinkedIn Session to re-login, then retry. If this persists, wait 24h.",
                    finished_at=datetime.now().isoformat())
                return

        if needs_login:
            if not li_email or not li_password:
                db.update_linkedin_scrape(scrape_id, status="error",
                    error_message="LinkedIn credentials not configured. Go to Settings.",
                    finished_at=datetime.now().isoformat())
                return

            log.info(f"LinkedIn scrape #{scrape_id} — logging in as {li_email}")
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(1, 2))

            # Type at human speed
            email_field = page.locator("#username")
            await email_field.click()
            for ch in li_email:
                await email_field.press(ch)
                await asyncio.sleep(random.uniform(0.02, 0.08))
            await asyncio.sleep(random.uniform(0.3, 0.6))

            pw_field = page.locator("#password")
            await pw_field.click()
            for ch in li_password:
                await pw_field.press(ch)
                await asyncio.sleep(random.uniform(0.02, 0.08))
            await asyncio.sleep(random.uniform(0.3, 0.8))

            await page.click("button[type='submit']")
            await asyncio.sleep(random.uniform(3, 5))

            post_url = page.url
            if "checkpoint" in post_url or "challenge" in post_url:
                db.update_linkedin_scrape(scrape_id, status="error",
                    error_message="LinkedIn requires verification (CAPTCHA/2FA). Go to Settings > LinkedIn Session to re-login, then retry. If this persists, wait 24h.",
                    finished_at=datetime.now().isoformat())
                return

            if "/login" in post_url:
                db.update_linkedin_scrape(scrape_id, status="error",
                    error_message="LinkedIn login failed. Check your credentials in Settings.",
                    finished_at=datetime.now().isoformat())
                return

            log.info(f"LinkedIn scrape #{scrape_id} — login successful")

        # Navigate to search URL
        log.info(f"LinkedIn scrape #{scrape_id} — navigating to search URL")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2, 3))

        total_scraped = 0

        for page_num in range(1, max_pages + 1):
            # Check if scrape was stopped
            current = db.get_linkedin_scrape(scrape_id)
            if current and current["status"] != "running":
                log.info(f"LinkedIn scrape #{scrape_id} — stopped by user")
                break

            db.update_linkedin_scrape(scrape_id, current_page=page_num)

            # Wait for results to load — try a few selectors
            has_results = False
            for sel in [
                'div[data-view-name="people-search-result"]',
                'li.reusable-search__result-container',
                'div.entity-result',
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=8000)
                    has_results = True
                    break
                except Exception:
                    continue

            if not has_results:
                log.warning(f"LinkedIn scrape #{scrape_id} — no results found on page {page_num}")
                break

            # Scroll to load all results
            for _ in range(3):
                await page.evaluate("(y) => window.scrollBy(0, y)", random.randint(800, 1200))
                await asyncio.sleep(random.uniform(0.2, 0.4))
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(random.uniform(0.3, 0.6))

            # Extract profiles via JS
            try:
                profiles = await page.evaluate(JS_EXTRACT_ALL)
            except Exception as exc:
                log.error(f"LinkedIn scrape #{scrape_id} — JS extraction failed: {exc}")
                profiles = []

            if not profiles:
                log.warning(f"LinkedIn scrape #{scrape_id} — no profiles extracted on page {page_num}")
                break

            log.info(f"LinkedIn scrape #{scrape_id} — page {page_num}: {len(profiles)} people found")

            # Map to DB field names
            page_people = []
            for p in profiles:
                page_people.append({
                    "full_name": p.get("name", ""),
                    "job_title": p.get("title", ""),
                    "company": p.get("company", ""),
                    "location": p.get("location", ""),
                    "profile_url": p.get("profile_url", ""),
                    "email": "",
                    "phone": "",
                    "website": "",
                    "website_email": "",
                    "google_email": "",
                })

            # Enrich contacts
            search_results_url = page.url
            await _enrich_contacts(page, page_people, serpapi_key, scrape_id, proxy_rotator=scrapling_proxy_rotator)

            # Navigate back to search results
            log.info(f"LinkedIn scrape #{scrape_id} — returning to search results")
            await page.goto(search_results_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 3))

            # Save to DB
            if page_people:
                db.save_linkedin_results(scrape_id, page_people)
                total_scraped += len(page_people)
                db.update_linkedin_scrape(scrape_id, total_scraped=total_scraped)
                log.info(f"LinkedIn scrape #{scrape_id} — saved {len(page_people)} enriched profiles (total: {total_scraped})")

            # Try to go to next page
            next_clicked = False

            # Scroll to bottom to reveal pagination
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(random.uniform(0.3, 0.6))

            for sel in [
                'button[aria-label="Next"]',
                'button[aria-label="Avanti"]',
                'button[aria-label="Suivant"]',
                'button[aria-label="Weiter"]',
                'button.artdeco-pagination__button--next',
            ]:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_enabled() and await btn.is_visible():
                        await btn.scroll_into_view_if_needed()
                        await asyncio.sleep(random.uniform(0.3, 0.6))
                        await btn.click()
                        next_clicked = True
                        log.info(f"LinkedIn scrape #{scrape_id} — navigating to next page (button)")
                        break
                except Exception:
                    continue

            if not next_clicked:
                # Fallback: URL-based pagination
                current_url = page.url
                m = re.search(r"[?&]page=(\d+)", current_url)
                if m:
                    current_page_num = int(m.group(1))
                    next_url = re.sub(r"([?&])page=\d+", rf"\g<1>page={current_page_num + 1}", current_url)
                else:
                    sep = "&" if "?" in current_url else "?"
                    next_url = f"{current_url}{sep}page=2"

                log.info(f"LinkedIn scrape #{scrape_id} — trying URL pagination")
                await page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(2, 3))

                # Verify results exist
                found = False
                for sel in [
                    'div[data-view-name="people-search-result"]',
                    'li.reusable-search__result-container',
                ]:
                    try:
                        await page.wait_for_selector(sel, timeout=5000)
                        found = True
                        break
                    except Exception:
                        continue
                if not found:
                    log.info(f"LinkedIn scrape #{scrape_id} — URL pagination yielded no results, stopping")
                    break
                next_clicked = True

            if not next_clicked:
                log.info(f"LinkedIn scrape #{scrape_id} — no next button found, last page reached")
                break

            delay = random.uniform(delay_min, delay_max)
            await asyncio.sleep(delay)

        db.update_linkedin_scrape(scrape_id, status="done", total_scraped=total_scraped,
                                  finished_at=datetime.now().isoformat())
        log.info(f"LinkedIn scrape #{scrape_id} DONE — {total_scraped} total people")

    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}"
        log.error(f"LinkedIn scrape #{scrape_id} FAILED: {error_msg}")
        log.error(traceback.format_exc())
        db.update_linkedin_scrape(scrape_id, status="error", error_message=error_msg[:500],
                                  finished_at=datetime.now().isoformat())
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


async def run_manual_login(status_dict: dict):
    """
    Automatic headless LinkedIn login using saved credentials.
    Works on VPS without a visible browser — logs in with email/password,
    saves session cookies for the scraper to reuse.
    """
    from playwright.async_api import async_playwright

    status_dict["status"] = "opening"
    status_dict["message"] = "Logging in to LinkedIn..."

    li_email = db.get_setting("linkedin_email", "")
    li_password = db.get_setting("linkedin_password", "")

    if not li_email or not li_password:
        status_dict["status"] = "error"
        status_dict["message"] = "LinkedIn email and password must be configured in Settings first."
        return

    pw = await async_playwright().start()

    try:
        ua = LINKEDIN_UA

        # Clear old cookies to start fresh (avoids stale/incompatible sessions)
        import shutil
        cookie_dir = Path(LINKEDIN_COOKIES_DIR)
        if cookie_dir.exists():
            for item in cookie_dir.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except Exception:
                    pass

        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(LINKEDIN_COOKIES_DIR),
            headless=True,
            args=[
                "--headless=new",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-gpu",
                "--disable-notifications",
            ],
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        page = context.pages[0] if context.pages else await context.new_page()

        # Stealth init scripts
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        status_dict["status"] = "waiting"
        status_dict["message"] = "Navigating to LinkedIn login page..."

        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1, 2))

        # Type email at human speed
        status_dict["message"] = "Entering credentials..."
        email_field = page.locator("#username")
        await email_field.click()
        for ch in li_email:
            await email_field.press(ch)
            await asyncio.sleep(random.uniform(0.02, 0.08))
        await asyncio.sleep(random.uniform(0.3, 0.6))

        # Type password at human speed
        pw_field = page.locator("#password")
        await pw_field.click()
        for ch in li_password:
            await pw_field.press(ch)
            await asyncio.sleep(random.uniform(0.02, 0.08))
        await asyncio.sleep(random.uniform(0.3, 0.8))

        # Submit
        status_dict["message"] = "Submitting login..."
        await page.click("button[type='submit']")
        await asyncio.sleep(random.uniform(4, 6))

        current_url = page.url
        log.info(f"Manual login — post-login URL: {current_url}")

        if "/feed" in current_url or ("linkedin.com" in current_url and "/login" not in current_url and "checkpoint" not in current_url and "challenge" not in current_url):
            status_dict["status"] = "done"
            status_dict["message"] = "LinkedIn session saved successfully!"
            log.info("Automatic LinkedIn login successful — cookies saved")
            try:
                await context.close()
            except Exception:
                pass
            return

        if "checkpoint" in current_url or "challenge" in current_url:
            status_dict["status"] = "error"
            status_dict["message"] = "LinkedIn requires verification (CAPTCHA/2FA). Try again later or use a different account. If this keeps happening, wait 24h before retrying."
            log.warning(f"LinkedIn login hit checkpoint: {current_url}")
            try:
                await context.close()
            except Exception:
                pass
            return

        # Still on login page — wrong credentials
        status_dict["status"] = "error"
        status_dict["message"] = "LinkedIn login failed. Check your email and password in Settings."
        log.warning(f"LinkedIn login failed — still on: {current_url}")
        try:
            await context.close()
        except Exception:
            pass

    except Exception as e:
        status_dict["status"] = "error"
        status_dict["message"] = f"Login error: {type(e).__name__}: {str(e)}"
        log.error(f"Manual login error: {e}")
    finally:
        try:
            await pw.stop()
        except Exception:
            pass
