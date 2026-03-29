A production-ready CLI tool to scrape LinkedIn job listings, store them in a
formatted Excel file, and track your cold-outreach pipeline end-to-end.

---

## 📁 Project Structure

```
linkedin_tracker/
├── cli.py                        ← Entry point  (run all commands from here)
├── requirements.txt
├── README.md
├── config/
│   ├── settings.py               ← Tunable constants (delays, paths, columns)
│   └── cookies.json              ← ⬅ YOU CREATE THIS (see §Cookie Setup)
├── src/
│   ├── scraper.py                ← LinkedIn scraper (guest API + detail pages)
│   ├── storage.py                ← Excel engine  (formatted, append-safe)
│   ├── tracker.py                ← Outreach tracking layer
│   └── utils.py                  ← Logger, headers, delays, text helpers
└── data/
    └── linkedin_tracker.xlsx     ← Auto-created on first save
```

---

## ⚙️ Setup

```bash
# 1. Unzip & enter folder
unzip linkedin_tracker_tool.zip && cd linkedin_tracker

# 2. Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## 🍪 Cookie Setup (CRITICAL for more results)

Without a session cookie LinkedIn returns **only 3-5 cards** per search.
Adding your `li_at` cookie typically raises this to **25+ per page**.

### Step-by-step (Chrome)

1. Log in at **linkedin.com**
2. Open **DevTools** → **Application** tab → **Cookies** → `linkedin.com`
3. Find the cookie named **`li_at`**  (and optionally `JSESSIONID`)
4. Create the file `config/cookies.json`:

```json
{
  "li_at": "AQEDATxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "JSESSIONID": "\"ajax:0123456789012345678\""
}
```

5. Re-run the scraper — you should see 25+ cards per page immediately.

> **Security note:** `li_at` is your LinkedIn session token.
> Never share it. The file is local and never uploaded anywhere.

---

## 🚀 CLI Commands

### `scrape` — Collect LinkedIn job listings

```bash
# Basic usage
python cli.py scrape --keyword "SDE Intern" --location "India"

# ✅ RECOMMENDED: use --cards-only for first runs (10x faster, avoids 429s)
python cli.py scrape --keyword "SDE Intern" --location "India" --cards-only

# Specify geo ID explicitly for more results (see GeoID table below)
python cli.py scrape -k "SDE Intern" -l "India" --geo-id 102713980 --cards-only

# Internship filter + Bangalore + past week
python cli.py scrape -k "Frontend Intern" -l "Bangalore" -t internship -d past-week

# Full-time jobs in US, up to 200 results
python cli.py scrape -k "Backend Engineer" -l "United States" -t full-time -n 200 -p 8

# Remote jobs globally
python cli.py scrape -k "React Developer" -l "" -t remote
```

**Scrape flags:**

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--keyword` | `-k` | required | Search term, e.g. `"SDE Intern"` |
| `--location` | `-l` | `""` | Location name (auto-resolved to geoId) |
| `--geo-id` | `-g` | `""` | Override numeric LinkedIn geoId |
| `--type` | `-t` | `""` | `internship` / `full-time` / `part-time` / `contract` / `remote` |
| `--max-pages` | `-p` | `3` | Number of result pages to fetch |
| `--max-results` | `-n` | `100` | Max total jobs to collect |
| `--date-posted` | `-d` | `""` | `past-24h` / `past-week` / `past-month` |
| `--cards-only` | — | off | Skip detail pages (10× faster) |
| `--no-cookies` | — | off | Ignore `config/cookies.json` |
| `--output` / `-o` | — | `data/linkedin_tracker.xlsx` | Target Excel file |

---

### `list` — View tracked jobs

```bash
python cli.py list                              # all records
python cli.py list --status "Not Contacted"     # pending outreach
python cli.py list --keyword "React"            # search across fields
python cli.py list --internship                 # internships only
python cli.py list --limit 100                  # show more rows
```

### `summary` — Dashboard

```bash
python cli.py summary
```

```
══════════════════════════════════════════════════
  📊  LinkedIn Outreach Tracker — Summary
══════════════════════════════════════════════════
  Total records     : 47
  Internships       : 23
──────────────────────────────────────────────────
  Status breakdown:
    Not Contacted       31  ███████████████████████████████
    Emailed              9  █████████
    Followed-up          4  ████
    Replied              2  ██
    Rejected             1  █
══════════════════════════════════════════════════
```

### `update` — Track outreach actions

```bash
# After sending a cold email:
python cli.py update --id 5 --action emailed --notes "Sent intro email via Gmail"

# After sending a follow-up:
python cli.py update --id 5 --action followup

# They replied!
python cli.py update --id 5 --action replied --notes "Interview scheduled for Friday"

# Rejection:
python cli.py update --id 5 --action rejected --notes "Role filled internally"
```

### `note` — Append notes

```bash
python cli.py note --id 5 --text "Company has great Glassdoor reviews"
python cli.py note --id 5 --text "HR name is Priya — mention referral from Rahul"
```

### `export` — Save filtered subset

```bash
python cli.py export --internship -e internships_only.xlsx
python cli.py export --keyword "Backend" -e backend_jobs.xlsx
python cli.py export --status "Replied" -e warm_leads.xlsx
```

---

## 🌍 geoId Quick Reference

LinkedIn uses numeric IDs for locations. Pass `--geo-id` for precise filtering.

| Location | geoId |
|----------|-------|
| India (country) | `102713980` |
| Bangalore / Bengaluru | `105214831` |
| Mumbai | `102717819` |
| Delhi / NCR | `102713836` |
| Hyderabad | `102571160` |
| Pune | `106680522` |
| Chennai | `102650290` |
| United States | `103644278` |
| United Kingdom | `101165590` |
| Canada | `101174742` |
| Singapore | `102454443` |
| Germany | `101282230` |
| UAE | `104305776` |

> **How to find any city's geoId:**
> 1. Go to `linkedin.com/jobs/search/`
> 2. Type your city in the Location box and select it
> 3. Look at the URL — find `geoId=XXXXXXXXX`

---

## 🛡️ Anti-Block Strategy

| Layer | What it does |
|-------|-------------|
| **geoId** | Correct location filtering → server returns proper results |
| **Referer header** | `Referer: linkedin.com/jobs/search/` — mimics browser navigation |
| **Random delays** | 3–7s between requests; 15s cooldown every 5 pages |
| **User-Agent rotation** | Pool of 5 real Chrome/Firefox/Safari UA strings |
| **Full browser headers** | `Sec-Fetch-*`, `Accept-Language`, `DNT`, `Cache-Control` |
| **Session cookies** | `li_at` token gives authenticated-like rate limits |
| **Retry + back-off** | 3 retries × 45s on 429, exponential on 5xx |
| **--cards-only mode** | Halves request count; avoids detail-page fingerprinting |
| **Deduplication** | Never re-fetches already-stored URLs |

---

## 🔧 Troubleshooting

### "Only 3-5 results per page"
→ **Add your `li_at` cookie** to `config/cookies.json` (see §Cookie Setup)
→ Use `--geo-id 102713980` explicitly for India

### "No more results at offset 25"
→ LinkedIn has paginated you out. Add cookies and try `--date-posted past-week`
→ Break the search into smaller queries: `"SDE Intern Bangalore"`, `"SDE Intern Mumbai"`, etc.

### "HTTP 429 — rate limited"
→ The tool already waits 45s+ on 429s. If persistent:
  - Increase `REQUEST_DELAY_MIN = 8` in `config/settings.py`
  - Use `--cards-only` to cut requests in half
  - Run in smaller batches: `--max-pages 2` then wait 10 minutes

### "Access denied (403)"
→ Your session has expired. Re-extract `li_at` from the browser and update `cookies.json`

### "Connection error"
→ Check your internet. LinkedIn may have temporarily blocked your IP.
  Wait 30 minutes, then retry with `--cards-only`.

---

## 📊 Excel Columns Reference

| Column | Type | Description |
|--------|------|-------------|
| ID | Auto-int | Auto-generated sequential ID |
| Recruiter Name | Text | Name of hiring manager / recruiter |
| Company | Text | Company name |
| Role | Text | Job title |
| Stipend/Salary | Text | Compensation if stated |
| Location | Text | City / Country / Remote |
| Job Type | Text | Internship / Full-time / etc. |
| Email | Text | Recruiter email if found |
| LinkedIn URL | URL | Recruiter's LinkedIn profile |
| Job URL | URL | LinkedIn job posting URL |
| Description | Text | Full job description |
| Requirements | Text | Extracted skills / qualifications |
| Date Posted | Date | When the job was posted |
| Date Scraped | Date | When you scraped it |
| Cold Email Sent | Bool | ✓ after sending first email |
| Email Date | Date | Date of cold email |
| Follow-up Sent | Bool | ✓ after follow-up email |
| Follow-up Date | Date | Date of follow-up |
| Notes | Text | Your free-text notes |
| Status | Dropdown | Not Contacted → Emailed → Followed-up → Replied → Rejected |
