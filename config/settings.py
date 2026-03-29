"""
settings.py — Central configuration for the LinkedIn Tracker tool.
Adjust these values to tune scraping behavior, file paths, and defaults.
"""

import os

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR    = os.path.join(BASE_DIR, "data")
LOG_DIR     = os.path.join(BASE_DIR, "logs")
OUTPUT_FILE = os.path.join(DATA_DIR, "linkedin_tracker.xlsx")
COOKIE_FILE = os.path.join(BASE_DIR, "config", "cookies.json")

# ─── Rate Limiting ────────────────────────────────────────────────────────────
# Seconds to wait between individual requests (randomized between MIN and MAX)
REQUEST_DELAY_MIN = 3.0
REQUEST_DELAY_MAX = 7.0

# Extra pause after every N pages (anti-bot cooldown)
PAGINATION_COOLDOWN_EVERY = 5     # pages
PAGINATION_COOLDOWN_SEC   = 15.0  # seconds

# Max retries per failed request
MAX_RETRIES = 3
RETRY_BACKOFF = 5  # seconds (multiplied by attempt number)

# ─── LinkedIn Scraping ────────────────────────────────────────────────────────
LINKEDIN_BASE_URL   = "https://www.linkedin.com"
JOBS_SEARCH_URL     = "https://www.linkedin.com/jobs/search/"
PUBLIC_JOBS_URL     = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# Default results per page (LinkedIn usually returns 25)
RESULTS_PER_PAGE = 25

# ─── Excel Sheet ─────────────────────────────────────────────────────────────
SHEET_NAME = "Jobs & Outreach"

# Column header order (must match ExcelStorage column map)
COLUMNS = [
    "ID", "Recruiter Name", "Company", "Role", "Stipend/Salary",
    "Location", "Job Type", "Email", "LinkedIn URL", "Job URL",
    "Description", "Requirements", "Date Posted", "Date Scraped",
    "Cold Email Sent", "Email Date", "Follow-up Sent", "Follow-up Date",
    "Notes", "Status",
]

# Column widths (characters) for formatting
COLUMN_WIDTHS = {
    "ID": 6,
    "Recruiter Name": 22,
    "Company": 22,
    "Role": 28,
    "Stipend/Salary": 18,
    "Location": 18,
    "Job Type": 14,
    "Email": 28,
    "LinkedIn URL": 36,
    "Job URL": 36,
    "Description": 60,
    "Requirements": 50,
    "Date Posted": 14,
    "Date Scraped": 14,
    "Cold Email Sent": 16,
    "Email Date": 14,
    "Follow-up Sent": 16,
    "Follow-up Date": 14,
    "Notes": 40,
    "Status": 16,
}

# Allowed status values
STATUS_OPTIONS = [
    "Not Contacted",
    "Emailed",
    "Followed-up",
    "Replied",
    "Rejected",
    "Hired",
]

# ─── User-Agent Pool ──────────────────────────────────────────────────────────
# Rotated per request to reduce fingerprinting
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",

    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",

    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",

    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
    "Gecko/20100101 Firefox/124.0",

    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL  = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
LOG_FILE   = os.path.join(LOG_DIR, "tracker.log")
