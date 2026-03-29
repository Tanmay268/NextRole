"""
scraper.py — LinkedIn Jobs + Recruiter scraper.

Strategy
--------
LinkedIn blocks most unauthenticated scrapers. This module uses two
complementary approaches:

1. **Public guest API** (no login required):
   GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
   Returns HTML fragments with job cards. Requires a numeric geoId for
   reliable location filtering — plain text location alone returns very
   few results.  Works for basic job card data (title, company, location,
   URL, date posted, job type).

2. **Job detail page scraping** (fetches individual job pages):
   After collecting card-level data we visit each job URL to extract the
   full description, requirements, and any visible recruiter info.
   Use --cards-only to skip this and run 10× faster.

Common reasons for low card counts
------------------------------------
  ✗ No geoId — pass --geo-id or use a recognised location name (see GEO_IDS)
  ✗ Detail page fetching triggers rate-limiting — use --cards-only
  ✗ No session cookie — add li_at to config/cookies.json (see README)
  ✗ Too-fast requests — increase REQUEST_DELAY_MIN in config/settings.py

Rate-limit mitigations
-----------------------
- Numeric geoId for precise location (avoids server-side no-results)
- Referer header on every guest-API call
- Random delay between every request (3–7 s default)
- Cooldown pause every N pages
- Rotating User-Agent pool
- Optional li_at session-cookie injection
- Exponential back-off on 429 / 5xx responses
- Duplicate detection before returning results
"""

import re
import time
from datetime import date, timedelta
from typing import Any, Callable, Optional

import requests
from bs4 import BeautifulSoup

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (
    PUBLIC_JOBS_URL,
    MAX_RETRIES, PAGINATION_COOLDOWN_EVERY, PAGINATION_COOLDOWN_SEC,
    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, RESULTS_PER_PAGE,
)
from src.utils import (
    build_headers, clean_text, extract_email,
    get_logger, load_cookies, normalize_job_type, random_delay,
)

log = get_logger(__name__)


# ─── LinkedIn geoId lookup table ──────────────────────────────────────────────
# LinkedIn ignores plain-text location strings on the guest API.
# Pass the numeric geoId for reliable country / city filtering.
# Find your city's geoId: inspect a LinkedIn Jobs URL after filtering by location.
GEO_IDS: dict[str, str] = {
    # Countries
    "india":          "102713980",
    "united states":  "103644278",
    "us":             "103644278",
    "usa":            "103644278",
    "united kingdom": "101165590",
    "uk":             "101165590",
    "canada":         "101174742",
    "australia":      "101452733",
    "germany":        "101282230",
    "singapore":      "102454443",
    "uae":            "104305776",
    "france":         "105015875",
    "netherlands":    "102890719",
    # Indian cities
    "bangalore":      "105214831",
    "bengaluru":      "105214831",
    "mumbai":         "102717819",
    "delhi":          "102713836",
    "hyderabad":      "102571160",
    "pune":           "106680522",
    "chennai":        "102650290",
    "kolkata":        "102635488",
    "remote":         "",           # remote has no geoId; use f_WT=2 instead
}


# ─── Data model ───────────────────────────────────────────────────────────────

def empty_job() -> dict:
    """Return a job record pre-filled with default/empty values."""
    return {
        "recruiter_name":  "",
        "company":         "",
        "role":            "",
        "stipend":         "",
        "location":        "",
        "job_type":        "",
        "email":           "",
        "linkedin_url":    "",
        "job_url":         "",
        "description":     "",
        "requirements":    "",
        "date_posted":     "",
        "date_scraped":    date.today().isoformat(),
        # Outreach fields — populated later by the tracker
        "cold_email_sent": False,
        "email_date":      "",
        "followup_sent":   False,
        "followup_date":   "",
        "notes":           "",
        "status":          "Not Contacted",
    }


# ─── HTTP session ─────────────────────────────────────────────────────────────

class LinkedInSession:
    """
    Wraps requests.Session with:
      - automatic header rotation
      - cookie injection (li_at session token)
      - retry + exponential back-off logic
    """

    def __init__(self, use_cookies: bool = True):
        self.session = requests.Session()
        if use_cookies:
            cookies = load_cookies()
            if cookies:
                self.session.cookies.update(cookies)
                log.info("Loaded %d cookie(s) from config/cookies.json.", len(cookies))
            else:
                log.warning(
                    "No cookie file found → running unauthenticated. "
                    "Result counts will be limited. "
                    "See README §Cookie Setup to add your li_at session token."
                )

    def get(self, url: str, params: Optional[dict] = None,
            extra_headers: Optional[dict] = None) -> Optional[requests.Response]:
        """
        GET with retry + back-off.
        Returns the Response on success, None after exhausted retries.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            headers = build_headers(extra_headers)
            try:
                resp = self.session.get(
                    url, params=params, headers=headers,
                    timeout=20, allow_redirects=True,
                )

                if resp.status_code == 200:
                    return resp

                if resp.status_code == 429:
                    wait = 45 * attempt
                    log.warning("Rate-limited (429) — waiting %ds before retry …", wait)
                    time.sleep(wait)
                    continue

                if resp.status_code in (403, 401):
                    log.error(
                        "Access denied (%d). Add your li_at cookie to "
                        "config/cookies.json for authenticated scraping.",
                        resp.status_code,
                    )
                    return None

                # 5xx or other non-OK
                wait = 8 * attempt
                log.warning(
                    "HTTP %d on attempt %d/%d — retrying in %ds …",
                    resp.status_code, attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)

            except requests.exceptions.ConnectionError as exc:
                log.error("Connection error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
                time.sleep(8 * attempt)
            except requests.exceptions.Timeout:
                log.warning("Request timed out (attempt %d/%d).", attempt, MAX_RETRIES)
                time.sleep(5 * attempt)
            except requests.exceptions.RequestException as exc:
                log.error("Request error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
                time.sleep(5 * attempt)

        log.error("All %d retries exhausted for %s", MAX_RETRIES, url)
        return None


# ─── Date helpers ─────────────────────────────────────────────────────────────

def _parse_date_posted(raw: str) -> str:
    """
    Convert LinkedIn relative strings ('2 days ago', '1 week ago') or
    ISO datetimes to a YYYY-MM-DD date string.
    """
    if not raw:
        return ""
    raw = raw.strip()

    # Already ISO date or datetime
    iso_m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if iso_m:
        return iso_m.group(1)

    lower = raw.lower()
    today = date.today()

    if "just now" in lower or "today" in lower or "moment" in lower:
        return today.isoformat()
    if "yesterday" in lower:
        return (today - timedelta(days=1)).isoformat()

    m = re.search(r"(\d+)\s*(minute|hour|day|week|month)", lower)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta_map = {
            "minute": timedelta(minutes=n),
            "hour":   timedelta(hours=n),
            "day":    timedelta(days=n),
            "week":   timedelta(weeks=n),
            "month":  timedelta(days=n * 30),
        }
        return (today - delta_map[unit]).isoformat()

    return raw  # Return raw if unparseable


# ─── Card parsing (multi-fallback) ────────────────────────────────────────────

# LinkedIn's HTML class names change frequently.
# Each field has a list of selector strategies tried in order.

def _find_text(card: BeautifulSoup, tag: str, patterns: list[str]) -> str:
    """
    Try each CSS-class regex in `patterns` against `tag`.
    Return cleaned text of the first match, or empty string.
    """
    for pat in patterns:
        el = card.find(tag, class_=re.compile(pat, re.I))
        if el:
            return clean_text(el.get_text())
    return ""


def _find_href(card: BeautifulSoup, patterns: list[str]) -> str:
    """Try each class pattern to find an <a href=...>. Strip tracking params."""
    for pat in patterns:
        el = card.find("a", class_=re.compile(pat, re.I))
        if el and el.get("href"):
            return el["href"].split("?")[0]
    # Fallback: any <a> whose href contains /jobs/view/
    el = card.find("a", href=re.compile(r"/jobs/view/"))
    if el:
        return el["href"].split("?")[0]
    return ""


def _parse_job_card(card: BeautifulSoup) -> dict:
    """
    Extract structured data from one job-card <li> element.
    Uses multi-layered fallbacks to handle LinkedIn's changing HTML.
    """
    job = empty_job()

    try:
        # ── Role / Title ─────────────────────────────────────────────────────
        job["role"] = _find_text(card, "h3", [
            r"base-search-card__title",
            r"job-result-card__title",
            r"job-card-list__title",
            r"job-card-container__link",
        ]) or _find_text(card, "a", [r"job-card-list__title"])

        # ── Company ───────────────────────────────────────────────────────────
        job["company"] = _find_text(card, "h4", [
            r"base-search-card__subtitle",
            r"job-result-card__company-name",
            r"job-card-container__company-name",
        ]) or _find_text(card, "a", [r"job-card-container__company-name"])

        # ── Location ──────────────────────────────────────────────────────────
        job["location"] = _find_text(card, "span", [
            r"job-search-card__location",
            r"job-result-card__location",
            r"job-card-container__metadata-item",
        ])

        # ── Job URL ───────────────────────────────────────────────────────────
        job["job_url"] = _find_href(card, [
            r"base-card__full-link",
            r"job-card-list__title",
            r"job-card-container__link",
        ])
        # Normalise to absolute URL
        if job["job_url"] and job["job_url"].startswith("/"):
            job["job_url"] = "https://www.linkedin.com" + job["job_url"]

        # ── Date posted ───────────────────────────────────────────────────────
        time_tag = card.find("time")
        if time_tag:
            raw_date = time_tag.get("datetime") or time_tag.get_text()
            job["date_posted"] = _parse_date_posted(raw_date)

        # ── Job type ──────────────────────────────────────────────────────────
        badge = card.find("span", class_=re.compile(
            r"job-search-card__job-insight|job-result-card__benefit|"
            r"job-card-container__job-insight-text", re.I
        ))
        if badge:
            job["job_type"] = normalize_job_type(clean_text(badge.get_text()))

        # ── Salary / Stipend (if visible on card) ─────────────────────────────
        salary_tag = card.find("span", class_=re.compile(r"salary|compensation", re.I))
        if salary_tag:
            job["stipend"] = clean_text(salary_tag.get_text())

    except Exception as exc:
        log.debug("Card parse error: %s", exc)

    return job


# ─── Detail-page enrichment ───────────────────────────────────────────────────

def _parse_job_detail(soup: BeautifulSoup, job: dict) -> dict:
    """
    Enrich a job dict with data from the full job detail page:
      description, requirements, recruiter name/URL, salary, job type.
    """
    try:
        # ── Description ──────────────────────────────────────────────────────
        desc_div = (
            soup.find("div", class_=re.compile(
                r"description__text|jobs-description__content|"
                r"show-more-less-html|jobs-box__html-content", re.I
            ))
            or soup.find("section", class_=re.compile(r"description", re.I))
        )
        if desc_div:
            job["description"] = clean_text(desc_div.get_text())
            job["requirements"] = _extract_requirements(job["description"])
            found_email = extract_email(job["description"])
            if found_email:
                job["email"] = found_email

        # ── Job criteria sidebar ──────────────────────────────────────────────
        for item in soup.find_all("li", class_=re.compile(r"description__job-criteria-item", re.I)):
            label_tag = item.find("h3")
            value_tag = item.find("span")
            if not (label_tag and value_tag):
                continue
            label = clean_text(label_tag.get_text()).lower()
            value = clean_text(value_tag.get_text())
            if "employment type" in label or "job type" in label:
                job["job_type"] = normalize_job_type(value)

        # ── Salary / Stipend ──────────────────────────────────────────────────
        salary_tag = soup.find(class_=re.compile(r"salary|compensation", re.I))
        if salary_tag and not job["stipend"]:
            job["stipend"] = clean_text(salary_tag.get_text())
        if not job["stipend"] and job["description"]:
            sal_m = re.search(
                r"(?:salary|stipend|compensation|pay|ctc|₹|inr|usd|\$|£)"
                r"[\s:]*[\d,\.\-\s]+(?:per|/|lpa|k|l|month|year|annum)?",
                job["description"], re.I,
            )
            if sal_m:
                job["stipend"] = sal_m.group(0).strip()

        # ── Recruiter / hirer card ────────────────────────────────────────────
        poster = soup.find("div", class_=re.compile(
            r"hirer-card|message-the-recruiter|jobs-poster|"
            r"job-details-jobs-unified-top-card__job-insight--highlight", re.I
        ))
        if poster:
            name_tag = poster.find("span", class_=re.compile(r"name|actor", re.I))
            if name_tag:
                job["recruiter_name"] = clean_text(name_tag.get_text())
            profile_link = poster.find("a", href=re.compile(r"linkedin\.com/in/|^/in/"))
            if profile_link:
                href = profile_link["href"].split("?")[0]
                job["linkedin_url"] = (
                    "https://www.linkedin.com" + href
                    if href.startswith("/") else href
                )

    except Exception as exc:
        log.debug("Detail page parse error: %s", exc)

    return job


def _extract_requirements(description: str) -> str:
    """
    Heuristically pull a Requirements / Qualifications block from the
    full description text. Returns empty string if not found.
    """
    pattern = (
        r"(?:requirements?|qualifications?|what you(?:'ll)? need|"
        r"skills required|you(?:'ll)? have|what we(?:'re)? looking for)"
        r"[:\s]+([\s\S]{80,2000}?)"
        r"(?=\n\n|\Z|responsibilities|about us|who we are|benefits|perks|what we offer)"
    )
    m = re.search(pattern, description, re.IGNORECASE)
    return clean_text(m.group(1)) if m else ""


# ─── Main Scraper class ───────────────────────────────────────────────────────

class LinkedInScraper:
    """
    High-level scraper.  Typical usage::

        scraper = LinkedInScraper()
        jobs = scraper.scrape(
            keyword="SDE Intern",
            location="India",           # resolved to geoId automatically
            job_type="internship",
            max_pages=5,
        )

    Tips for more results
    ----------------------
    1. Add your li_at cookie to config/cookies.json
    2. Use --cards-only (skip detail pages) for faster/safer bulk scraping
    3. Use a specific city name (e.g. "Bangalore") — better than "India"
    4. Try --date-posted past-week for fresher listings
    """

    def __init__(self, use_cookies: bool = True, fetch_details: bool = True):
        """
        Args:
            use_cookies:   Inject cookies from config/cookies.json if present.
            fetch_details: Visit each job URL for full description + recruiter
                           info. Set False (--cards-only) for faster scraping.
        """
        self.http          = LinkedInSession(use_cookies=use_cookies)
        self.fetch_details = fetch_details
        self._seen_urls: set = set()

    # ── Public API ────────────────────────────────────────────────────────────

    def scrape(
        self,
        keyword: str,
        location: str = "",
        job_type: str = "",
        work_mode: str = "",
        max_pages: int = 5,
        max_results: int = 200,
        date_posted: str = "",
        geo_id: str = "",          # override auto-lookup
        progress_callback: Optional[Callable[..., None]] = None,
        page_callback: Optional[Callable[..., None]] = None,
    ) -> list[dict]:
        """
        Scrape LinkedIn job listings matching the given parameters.
        Returns a list of job dicts (see empty_job() for schema).
        """
        # Resolve geoId: explicit override → lookup table → empty (global)
        resolved_geo = geo_id or GEO_IDS.get(location.lower().strip(), "")
        if resolved_geo:
            log.info("Using geoId=%s for location=%r", resolved_geo, location)
        else:
            log.warning(
                "No geoId resolved for location=%r — results may be sparse. "
                "Pass --geo-id explicitly or use a city/country from the GEO_IDS table.",
                location,
            )

        log.info(
            "Scrape started — keyword=%r  location=%r  geoId=%s  "
            "job_type=%r  max_pages=%d  max_results=%d  fetch_details=%s",
            keyword, location, resolved_geo or "none",
            job_type, max_pages, max_results, self.fetch_details,
        )

        all_jobs: list[dict] = []
        start_offset = 0
        consecutive_empty = 0  # stop early if API keeps returning nothing

        def emit_progress(event: str, **payload: Any) -> None:
            if not progress_callback:
                return
            try:
                progress_callback(event=event, **payload)
            except Exception as exc:
                log.debug("Progress callback failed: %s", exc)

        def emit_page(jobs: list[dict], **payload: Any) -> None:
            if not page_callback:
                return
            try:
                page_callback(jobs, **payload)
            except Exception as exc:
                log.debug("Page callback failed: %s", exc)

        emit_progress(
            "started",
            keyword=keyword,
            location=location,
            job_type=job_type,
            max_pages=max_pages,
            max_results=max_results,
            date_posted=date_posted,
            geo_id=resolved_geo,
        )

        for page_num in range(1, max_pages + 1):
            if len(all_jobs) >= max_results:
                log.info("Reached max_results=%d — stopping.", max_results)
                break

            log.info("Fetching page %d  (offset=%d) …", page_num, start_offset)

            cards = self._fetch_job_cards(
                keyword=keyword,
                location=location,
                geo_id=resolved_geo,
                job_type=job_type,
                work_mode=work_mode,
                date_posted=date_posted,
                start=start_offset,
            )

            if not cards:
                consecutive_empty += 1
                log.info(
                    "No cards at offset=%d  (empty run %d/2).",
                    start_offset, consecutive_empty,
                )
                if consecutive_empty >= 2:
                    log.info("Two consecutive empty pages — ending pagination.")
                    break
                start_offset += RESULTS_PER_PAGE
                random_delay()
                continue

            consecutive_empty = 0
            log.info("  Parsed %d card(s) on page %d", len(cards), page_num)

            new_this_page = 0
            for card in cards:
                if len(all_jobs) >= max_results:
                    break

                job = _parse_job_card(card)

                # Skip empty / malformed cards
                if not job["role"] and not job["job_url"]:
                    continue

                # Dedup by URL
                url_key = job["job_url"] or f"__nourl_{job['role']}_{job['company']}"
                if url_key in self._seen_urls:
                    continue
                self._seen_urls.add(url_key)

                # Enrich from detail page (slower but richer)
                if self.fetch_details and job["job_url"]:
                    job = self._enrich_from_detail_page(job)
                    random_delay(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)

                all_jobs.append(job)
                new_this_page += 1

            log.info("  Added %d new job(s) this page. Total so far: %d",
                     new_this_page, len(all_jobs))

            start_offset += RESULTS_PER_PAGE

            # Cooldown every N pages
            if page_num % PAGINATION_COOLDOWN_EVERY == 0:
                log.info(
                    "Cooldown — sleeping %.0fs after %d pages …",
                    PAGINATION_COOLDOWN_SEC, page_num,
                )
                time.sleep(PAGINATION_COOLDOWN_SEC)
            else:
                random_delay()

        log.info("Scrape complete — %d unique job(s) collected.", len(all_jobs))
        return all_jobs

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _fetch_job_cards(
        self,
        keyword: str,
        location: str,
        geo_id: str,
        job_type: str,
        work_mode: str,
        date_posted: str,
        start: int,
    ) -> list:
        """
        Call LinkedIn's guest jobs API and return parsed <li> elements.

        Key parameters:
          f_JT  — job type (I=Internship, F=Full-time, P=Part-time, C=Contract)
          f_E   — experience level (1=Internship, 2=Entry, 3=Associate, …)
          f_WT  — work type (1=On-site, 2=Remote, 3=Hybrid)
          f_TPR — time posted range (r86400=24h, r604800=week, r2592000=month)
          geoId — numeric LinkedIn location ID (critical for non-US locations)
        """
        jt_map = {
            "internship": "I",
            "full-time":  "F",
            "part-time":  "P",
            "contract":   "C",
            "temporary":  "T",
            "volunteer":  "V",
        }
        wt_map = {
            "onsite": "1",
            "on-site": "1",
            "remote": "2",
            "hybrid": "3",
        }
        tpr_map = {
            "past-24h":   "r86400",
            "past-week":  "r604800",
            "past-month": "r2592000",
        }

        params: dict[str, Any] = {
            "keywords": keyword,
            "start":    start,
        }

        # Always pass location text AND geoId for best results
        if location:
            params["location"] = location
        if geo_id:
            params["geoId"] = geo_id

        jt_lower = job_type.lower()
        if jt_lower in jt_map:
            params["f_JT"] = jt_map[jt_lower]
            # Also set experience level filter for internships
            if jt_lower == "internship":
                params["f_E"] = "1"

        work_mode_lower = work_mode.lower().strip() or ("remote" if jt_lower == "remote" else "")
        if work_mode_lower in wt_map:
            params["f_WT"] = wt_map[work_mode_lower]

        if date_posted in tpr_map:
            params["f_TPR"] = tpr_map[date_posted]

        # Referer header is checked by LinkedIn's bot-detection
        resp = self.http.get(
            PUBLIC_JOBS_URL,
            params=params,
            extra_headers={
                "Referer": "https://www.linkedin.com/jobs/search/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if resp is None:
            return []

        # Guard: if we got a redirect to login, HTML will be very short
        if len(resp.text) < 200:
            log.warning(
                "Response suspiciously short (%d chars) — "
                "likely a redirect or empty page. Try adding cookies.",
                len(resp.text),
            )
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("li")

        # Filter out <li> elements that are clearly not job cards
        # (LinkedIn sometimes returns navigation / footer <li>s)
        real_cards = [
            li for li in cards
            if li.find("a", href=re.compile(r"/jobs/view/"))
            or li.find("h3")
            or li.find(class_=re.compile(r"base-card|job-card|base-search-card", re.I))
        ]
        return real_cards if real_cards else cards  # fall back to all if filter too strict

    def _enrich_from_detail_page(self, job: dict) -> dict:
        """Fetch the individual job page and extract richer fields."""
        log.debug("  → Detail page: %s", job["job_url"])
        resp = self.http.get(
            job["job_url"],
            extra_headers={
                "Referer": "https://www.linkedin.com/jobs/search/",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        if resp is None:
            return job
        soup = BeautifulSoup(resp.text, "html.parser")
        return _parse_job_detail(soup, job)


def scrape_jobs_live(
    scraper: LinkedInScraper,
    storage,
    keyword: str,
    location: str = "",
    job_type: str = "",
    work_mode: str = "",
    max_pages: int = 5,
    max_results: int = 200,
    date_posted: str = "",
    geo_id: str = "",
    progress_callback: Optional[Callable[..., None]] = None,
) -> dict[str, Any]:
    """
    Run a scrape page-by-page and persist each page immediately.
    This is designed for the dashboard so table data and Excel stay synchronized.
    """
    resolved_geo = geo_id or GEO_IDS.get(location.lower().strip(), "")
    all_jobs: list[dict] = []
    start_offset = 0
    consecutive_empty = 0
    pages_completed = 0
    rows_written = 0

    def emit(event: str, **payload: Any) -> None:
        if not progress_callback:
            return
        try:
            progress_callback(event=event, **payload)
        except Exception as exc:
            log.debug("Progress callback failed: %s", exc)

    emit(
        "started",
        keyword=keyword,
        location=location,
        job_type=job_type,
        work_mode=work_mode,
        max_pages=max_pages,
        max_results=max_results,
        date_posted=date_posted,
        geo_id=resolved_geo,
    )

    for page_num in range(1, max_pages + 1):
        if len(all_jobs) >= max_results:
            emit("max_results_reached", page=page_num, total_jobs=len(all_jobs), rows_written=rows_written)
            break

        emit("page_fetch_started", page=page_num, offset=start_offset, total_jobs=len(all_jobs), rows_written=rows_written)
        cards = scraper._fetch_job_cards(
            keyword=keyword,
            location=location,
            geo_id=resolved_geo,
            job_type=job_type,
            work_mode=work_mode,
            date_posted=date_posted,
            start=start_offset,
        )

        if not cards:
            consecutive_empty += 1
            emit(
                "page_empty",
                page=page_num,
                offset=start_offset,
                empty_runs=consecutive_empty,
                total_jobs=len(all_jobs),
                rows_written=rows_written,
            )
            if consecutive_empty >= 2:
                emit("stopped_after_empty_pages", page=page_num, total_jobs=len(all_jobs), rows_written=rows_written)
                break
            start_offset += RESULTS_PER_PAGE
            random_delay()
            continue

        consecutive_empty = 0
        page_jobs: list[dict] = []
        for card in cards:
            if len(all_jobs) >= max_results:
                break

            job = _parse_job_card(card)
            if not job["role"] and not job["job_url"]:
                continue

            url_key = job["job_url"] or f"__nourl_{job['role']}_{job['company']}"
            if url_key in scraper._seen_urls:
                continue
            scraper._seen_urls.add(url_key)

            if scraper.fetch_details and job["job_url"]:
                job = scraper._enrich_from_detail_page(job)
                random_delay(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)

            all_jobs.append(job)
            page_jobs.append(job)
            emit(
                "job_added",
                page=page_num,
                total_jobs=len(all_jobs),
                rows_written=rows_written,
                role=job.get("role", ""),
                company=job.get("company", ""),
            )

        if page_jobs:
            rows_written += storage.save(page_jobs)
        pages_completed += 1
        emit(
            "page_completed",
            page=page_num,
            offset=start_offset,
            new_jobs=len(page_jobs),
            total_jobs=len(all_jobs),
            rows_written=rows_written,
        )

        start_offset += RESULTS_PER_PAGE
        if page_num % PAGINATION_COOLDOWN_EVERY == 0:
            time.sleep(PAGINATION_COOLDOWN_SEC)
        else:
            random_delay()

    emit("completed", total_jobs=len(all_jobs), rows_written=rows_written, pages_completed=pages_completed)
    return {
        "jobs": all_jobs,
        "total_jobs": len(all_jobs),
        "rows_written": rows_written,
        "pages_completed": pages_completed,
    }
