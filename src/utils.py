"""
utils.py — Shared helpers used across all modules.

Covers:
  - Logger factory
  - Randomised request delays (anti-bot)
  - Rotating User-Agent selection
  - Text sanitisation helpers
  - Cookie loading from JSON file
"""

import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional

# Allow sibling-level imports when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    LOG_FORMAT, LOG_LEVEL, LOG_FILE,
    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX,
    USER_AGENTS, COOKIE_FILE,
)


# ─── Logger ───────────────────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger writing to both stdout and a rotating log file.
    Calling this multiple times with the same name is safe (handlers not duplicated).
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    formatter = logging.Formatter(LOG_FORMAT)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler (creates log dir if needed)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


# ─── Rate-limit helpers ───────────────────────────────────────────────────────

def random_delay(min_s: float = REQUEST_DELAY_MIN,
                 max_s: float = REQUEST_DELAY_MAX) -> None:
    """Sleep for a random duration between min_s and max_s seconds."""
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)


def get_random_user_agent() -> str:
    """Pick a random User-Agent string from the pool."""
    return random.choice(USER_AGENTS)


def build_headers(extra: Optional[dict] = None) -> dict:
    """
    Construct request headers that mimic a real browser session.
    Merges any caller-supplied extras over the defaults.
    """
    headers = {
        "User-Agent": get_random_user_agent(),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "DNT": "1",
    }
    if extra:
        headers.update(extra)
    return headers


# ─── Cookie support ───────────────────────────────────────────────────────────

def load_cookies(path: str = COOKIE_FILE) -> dict:
    """
    Load cookies from a JSON file (key-value pairs).
    Returns an empty dict if the file does not exist.

    To capture your LinkedIn session cookies:
      1. Log in on Chrome/Firefox
      2. Open DevTools → Application → Cookies → linkedin.com
      3. Export as JSON: {"li_at": "...", "JSESSIONID": "...", ...}
      4. Save to config/cookies.json
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        return cookies
    except (json.JSONDecodeError, OSError):
        return {}


# ─── Text sanitisation ────────────────────────────────────────────────────────

def clean_text(text: Optional[str], max_len: int = 5000) -> str:
    """
    Normalise whitespace, strip HTML artefacts, and truncate long strings.
    Returns an empty string for None / falsy inputs.
    """
    if not text:
        return ""
    # Collapse all whitespace (newlines, tabs, multiple spaces)
    text = re.sub(r"\s+", " ", text).strip()
    # Remove zero-width / non-printing characters
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_len]


def extract_email(text: str) -> Optional[str]:
    """
    Attempt to pull an email address out of free text.
    Returns the first match or None.
    """
    if not text:
        return None
    pattern = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    match = re.search(pattern, text)
    return match.group(0) if match else None


def normalize_job_type(raw: str) -> str:
    """
    Map raw LinkedIn job-type strings to canonical labels.
    E.g. 'INTERNSHIP' → 'Internship', 'FULL_TIME' → 'Full-time'
    """
    mapping = {
        "INTERNSHIP": "Internship",
        "FULL_TIME": "Full-time",
        "PART_TIME": "Part-time",
        "CONTRACT": "Contract",
        "TEMPORARY": "Temporary",
        "VOLUNTEER": "Volunteer",
        "OTHER": "Other",
    }
    return mapping.get(raw.upper().replace(" ", "_"), raw.title())
