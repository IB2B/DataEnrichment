"""
Google Maps Business Scraper — Playwright-based async engine
Phase 1: Scout business URLs from search results (no proxy)
Phase 2: Extract business details one by one (no proxy)
Phase 3 (optional): Scrape emails from business websites using concurrent proxy workers
"""

import re, random, asyncio, logging
from datetime import datetime

import database as db
from config import GMAPS_DEFAULT_CHUNK_SIZE, GMAPS_DEFAULT_EXTRACT_DELAY
from enrichment_worker import ProxyPool, parse_proxy_for_playwright

log = logging.getLogger("enrichment.gmaps")

# Number of concurrent proxy workers for email scraping
EMAIL_WORKERS = 5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    window.chrome = { runtime: {} };
"""

# JS to extract emails from a webpage
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

# JS to extract business details from a Google Maps business page
JS_EXTRACT_BUSINESS = """() => {
    const info = {};

    // Name
    const nameEl = document.querySelector('h1.DUwDvf, h1[data-attrid="title"]');
    info.name = nameEl ? nameEl.textContent.trim() : '';

    // Category
    const catEl = document.querySelector('button.DkEaL, span.DkEaL');
    info.category = catEl ? catEl.textContent.trim() : '';

    // Address
    const addrBtn = document.querySelector('button[data-item-id="address"], button[data-tooltip="Copy address"]');
    if (addrBtn) {
        const addrText = addrBtn.querySelector('.Io6YTe, .rogA2c');
        info.address = addrText ? addrText.textContent.trim() : addrBtn.textContent.trim();
    } else {
        info.address = '';
    }

    // Phone
    const phoneBtn = document.querySelector('button[data-item-id^="phone:"], button[data-tooltip="Copy phone number"]');
    if (phoneBtn) {
        const phoneText = phoneBtn.querySelector('.Io6YTe, .rogA2c');
        info.phone = phoneText ? phoneText.textContent.trim() : phoneBtn.textContent.trim();
    } else {
        info.phone = '';
    }

    // Website
    const webLink = document.querySelector('a[data-item-id="authority"], a[data-tooltip="Open website"]');
    if (webLink) {
        info.website = webLink.getAttribute('href') || '';
    } else {
        info.website = '';
    }

    // Rating
    const ratingEl = document.querySelector('div.F7nice span[aria-hidden="true"]');
    info.rating = ratingEl ? ratingEl.textContent.trim() : '';

    // Reviews count
    const reviewEl = document.querySelector('div.F7nice span[aria-label*="review"]');
    if (reviewEl) {
        const label = reviewEl.getAttribute('aria-label') || '';
        const m = label.match(/([\d,]+)/);
        info.reviews_count = m ? m[1].replace(',', '') : '';
    } else {
        info.reviews_count = '';
    }

    return info;
}"""




async def _scout_business_urls(page, search_url, scrape_id):
    """Phase 1: Scroll through Google Maps search results and collect business URLs."""
    log.info(f"GMaps scrape #{scrape_id} — Phase 1: Scouting business URLs")

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            break
        except Exception as e:
            log.warning(f"GMaps scrape #{scrape_id} — page.goto attempt {attempt}/{max_retries} failed: {e}")
            if attempt == max_retries:
                raise
            await asyncio.sleep(3 * attempt)
    await asyncio.sleep(random.uniform(2, 4))

    # Accept cookies if prompted
    try:
        consent_btn = page.locator('button:has-text("Accept all")')
        if await consent_btn.count() > 0:
            await consent_btn.first.click()
            await asyncio.sleep(1)
    except Exception:
        pass

    # Wait for results panel
    try:
        await page.wait_for_selector('div[role="feed"], div.m6QErb', timeout=15000)
    except Exception:
        log.warning(f"GMaps scrape #{scrape_id} — No results feed found")
        return []

    # Scroll the results panel to load all results
    urls = set()
    no_new_count = 0
    max_scrolls = 200

    for i in range(max_scrolls):
        # Check if scrape was stopped
        current = db.get_google_maps_scrape(scrape_id)
        if current and current["status"] != "running":
            log.info(f"GMaps scrape #{scrape_id} — stopped by user during scouting")
            return list(urls)

        # Collect business links
        new_urls = await page.evaluate("""() => {
            const links = document.querySelectorAll('a.hfpxzc');
            return Array.from(links).map(a => a.getAttribute('href')).filter(h => h);
        }""")

        prev_count = len(urls)
        for u in new_urls:
            urls.add(u)

        if len(urls) > prev_count:
            no_new_count = 0
            db.update_google_maps_scrape(scrape_id, total_found=len(urls))
            log.info(f"GMaps scrape #{scrape_id} — found {len(urls)} businesses so far")
        else:
            no_new_count += 1

        # Check for "end of list" indicator
        end_reached = await page.evaluate("""() => {
            const el = document.querySelector('span.HlvSq');
            return el ? true : false;
        }""")
        if end_reached:
            log.info(f"GMaps scrape #{scrape_id} — reached end of results list")
            break

        if no_new_count >= 10:
            log.info(f"GMaps scrape #{scrape_id} — no new results after 10 scrolls, stopping scout")
            break

        # Scroll the results panel
        await page.evaluate("""() => {
            const feed = document.querySelector('div[role="feed"]') || document.querySelector('div.m6QErb[aria-label]');
            if (feed) feed.scrollTop = feed.scrollHeight;
        }""")
        await asyncio.sleep(random.uniform(0.8, 1.5))

    result_urls = list(urls)
    log.info(f"GMaps scrape #{scrape_id} — Phase 1 complete: {len(result_urls)} business URLs found")
    return result_urls


async def _extract_business(page, url):
    """Visit a business page and extract details."""
    for attempt in range(1, 3):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # Wait for business name to appear
            try:
                await page.wait_for_selector('h1.DUwDvf', timeout=8000)
            except Exception:
                pass

            info = await page.evaluate(JS_EXTRACT_BUSINESS)
            info["google_maps_url"] = url
            return info
        except Exception as exc:
            log.warning(f"GMaps extract error (attempt {attempt}) for {url[:60]}: {exc}")
            if attempt < 2:
                await asyncio.sleep(3)
    return None


async def _email_worker(worker_id, pw, job_queue, results_dict, lock, scrape_id, pp=None):
    """
    Concurrent email worker: launches browser,
    visits business websites to find email addresses.
    """
    browser = None
    try:
        launch_kwargs = dict(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-gpu",
                "--disable-notifications",
            ],
        )
        proxy_dict = parse_proxy_for_playwright(pp.get()) if pp else None
        if proxy_dict:
            launch_kwargs["proxy"] = proxy_dict

        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()
        await page.add_init_script(STEALTH_JS)

        while True:
            # Check if scrape was stopped
            current = db.get_google_maps_scrape(scrape_id)
            if current and current["status"] != "running":
                break

            try:
                idx, website = job_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            url = website.strip()
            if not url.startswith("http"):
                url = "https://" + url

            base = url.rstrip("/")
            pages_to_check = [url, base + "/contact", base + "/about"]
            found_email = ""

            for page_url in pages_to_check:
                try:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    emails = await page.evaluate(JS_WEBSITE_EMAILS)
                    if emails:
                        found_email = "; ".join(sorted(set(emails)))
                        break
                except Exception:
                    continue

            if found_email:
                log.info(f"GMaps email W{worker_id} — [{idx}] {website[:40]} => {found_email[:50]}")

            async with lock:
                results_dict[idx] = found_email

            job_queue.task_done()

    except Exception as exc:
        log.warning(f"GMaps email W{worker_id} error: {exc}")
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


async def run_google_maps_scrape(scrape_id: int):
    """Main entry point: scout, extract, then optionally scrape emails with proxy workers."""
    from playwright.async_api import async_playwright

    scrape = db.get_google_maps_scrape(scrape_id)
    if not scrape:
        log.error(f"GMaps scrape #{scrape_id} not found")
        return

    search_url = scrape["search_url"]
    do_emails = scrape["scrape_emails"] == 1

    log.info(f"GMaps scrape #{scrape_id} starting — emails={do_emails}")

    pw = await async_playwright().start()

    try:
        # ── Single browser with optional proxy — for scouting + extracting ──
        pp = ProxyPool()
        main_launch_kwargs = dict(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-gpu",
                "--disable-notifications",
            ],
        )
        main_proxy = parse_proxy_for_playwright(pp.get())
        if main_proxy:
            main_launch_kwargs["proxy"] = main_proxy

        browser = await pw.chromium.launch(**main_launch_kwargs)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()
        await page.add_init_script(STEALTH_JS)

        # ── Phase 1: Scout ──
        business_urls = await _scout_business_urls(page, search_url, scrape_id)

        if not business_urls:
            db.update_google_maps_scrape(scrape_id, status="done", total_found=0, total_scraped=0,
                                         finished_at=datetime.now().isoformat())
            log.info(f"GMaps scrape #{scrape_id} — no businesses found")
            await browser.close()
            return

        db.update_google_maps_scrape(scrape_id, total_found=len(business_urls))

        # ── Phase 2: Extract business details (no proxy, one by one) ──
        log.info(f"GMaps scrape #{scrape_id} — Phase 2: Extracting {len(business_urls)} businesses")
        all_businesses = []
        total_scraped = 0
        chunk_size = GMAPS_DEFAULT_CHUNK_SIZE

        for i in range(0, len(business_urls), chunk_size):
            current = db.get_google_maps_scrape(scrape_id)
            if current and current["status"] != "running":
                log.info(f"GMaps scrape #{scrape_id} — stopped by user during extraction")
                break

            chunk = business_urls[i:i + chunk_size]
            chunk_results = []

            for url in chunk:
                current = db.get_google_maps_scrape(scrape_id)
                if current and current["status"] != "running":
                    break

                info = await _extract_business(page, url)
                if not info:
                    continue

                info["email"] = ""  # will be filled in Phase 3 if enabled
                chunk_results.append(info)
                total_scraped += 1
                db.update_google_maps_scrape(scrape_id, total_scraped=total_scraped)
                log.info(f"GMaps scrape #{scrape_id} — [{total_scraped}/{len(business_urls)}] {info.get('name', '?')}")

                await asyncio.sleep(GMAPS_DEFAULT_EXTRACT_DELAY)

            if chunk_results:
                all_businesses.extend(chunk_results)
                db.save_google_maps_results(scrape_id, chunk_results)

        # Close the main browser — done with Google Maps
        await browser.close()

        # ── Phase 3: Scrape emails from websites using proxies (if enabled) ──
        if do_emails and all_businesses:
            # Collect businesses that have a website
            email_jobs = [(i, b["website"]) for i, b in enumerate(all_businesses) if b.get("website")]

            if email_jobs:
                num_workers = min(EMAIL_WORKERS, len(email_jobs))

                log.info(f"GMaps scrape #{scrape_id} — Phase 3: Scraping emails from {len(email_jobs)} websites with {num_workers} workers")

                job_queue = asyncio.Queue()
                for item in email_jobs:
                    await job_queue.put(item)

                email_results = {}
                lock = asyncio.Lock()

                tasks = []
                for w in range(num_workers):
                    t = asyncio.create_task(
                        _email_worker(w, pw, job_queue, email_results, lock, scrape_id, pp=pp)
                    )
                    tasks.append(t)

                await asyncio.gather(*tasks, return_exceptions=True)

                # Update results in DB with found emails
                if email_results:
                    from database import get_db
                    conn = get_db()
                    # Get result rows in order for this scrape
                    rows = conn.execute(
                        "SELECT id FROM google_maps_results WHERE scrape_id=? ORDER BY id",
                        (scrape_id,)).fetchall()
                    for idx, email_val in email_results.items():
                        if email_val and idx < len(rows):
                            conn.execute("UPDATE google_maps_results SET email=? WHERE id=?",
                                         (email_val, rows[idx]["id"]))
                    conn.commit()
                    conn.close()
                    found_count = sum(1 for v in email_results.values() if v)
                    log.info(f"GMaps scrape #{scrape_id} — found emails for {found_count}/{len(email_jobs)} websites")

        db.update_google_maps_scrape(scrape_id, status="done", total_scraped=total_scraped,
                                     finished_at=datetime.now().isoformat())
        log.info(f"GMaps scrape #{scrape_id} DONE — {total_scraped} businesses extracted")

    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}"
        log.error(f"GMaps scrape #{scrape_id} FAILED: {error_msg}")
        log.error(traceback.format_exc())
        db.update_google_maps_scrape(scrape_id, status="error", error_message=error_msg[:500],
                                     finished_at=datetime.now().isoformat())
    finally:
        try:
            await pw.stop()
        except Exception:
            pass
