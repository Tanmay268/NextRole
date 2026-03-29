"""
cli.py — Command-line interface for the LinkedIn Tracker tool.

Usage examples
--------------
# Scrape "SDE Intern" jobs in India (5 pages max):
python cli.py scrape --keyword "SDE Intern" --location "India" --max-pages 5

# Scrape only internships and save immediately:
python cli.py scrape --keyword "Frontend Intern" --type internship

# List everything in the tracker:
python cli.py list

# List only pending (not yet contacted):
python cli.py list --status "Not Contacted"

# Search by keyword:
python cli.py list --keyword "React"

# Show only internships:
python cli.py list --internship

# Print summary dashboard:
python cli.py summary

# Mark job ID 3 as emailed:
python cli.py update --id 3 --action emailed --notes "Sent intro email"

# Mark job ID 3 as followed up:
python cli.py update --id 3 --action followup

# Mark job ID 3 as replied:
python cli.py update --id 3 --action replied --notes "Got a response, interview next week!"

# Add a note to job ID 5:
python cli.py note --id 5 --text "Great company culture, check Glassdoor"

# Export filtered results to a new Excel file:
python cli.py export --keyword "Backend" --output filtered_backend.xlsx
"""

import argparse
import sys
import os
from pathlib import Path

# Ensure the project root is on the path regardless of where we call from
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.scraper import LinkedInScraper
from src.storage import ExcelStorage
from src.tracker import OutreachTracker
from src.utils import get_logger

log = get_logger("cli")


# ─── Colour helpers (ANSI — degrades gracefully on Windows) ──────────────────

def _c(text: str, code: str) -> str:
    """Wrap text in an ANSI colour code."""
    if os.name == "nt" and not os.environ.get("FORCE_COLOR"):
        return text   # plain on Windows without colour support
    return f"\033[{code}m{text}\033[0m"

green  = lambda t: _c(t, "92")
yellow = lambda t: _c(t, "93")
cyan   = lambda t: _c(t, "96")
bold   = lambda t: _c(t, "1")
red    = lambda t: _c(t, "91")


# ─── Sub-command handlers ─────────────────────────────────────────────────────

def cmd_scrape(args: argparse.Namespace) -> None:
    """Execute a LinkedIn scrape and save results to Excel."""
    print(bold(cyan(f"\n🔍  Scraping LinkedIn for: {args.keyword!r}\n")))

    scraper = LinkedInScraper(
        use_cookies=not args.no_cookies,
        fetch_details=not args.cards_only,
    )
    jobs = scraper.scrape(
        keyword    = args.keyword,
        location   = args.location,
        job_type   = args.type or "",
        work_mode  = args.work_mode or "",
        max_pages  = args.max_pages,
        max_results= args.max_results,
        date_posted= args.date_posted or "",
        geo_id     = args.geo_id or "",
    )

    if not jobs:
        print(red("  ⚠  No jobs found. Try a different keyword or check your connection."))
        sys.exit(1)

    print(green(f"  ✓  Scraped {len(jobs)} jobs."))

    store = ExcelStorage(args.output)
    written = store.save(jobs)
    print(green(f"  ✓  Saved {written} new rows → {args.output}\n"))


def cmd_list(args: argparse.Namespace) -> None:
    """List jobs from the tracker with optional filters."""
    tracker = OutreachTracker(args.output)

    df = tracker.list_jobs(
        status_filter  = args.status,
        keyword        = args.keyword,
        internship_only= args.internship,
        limit          = args.limit,
    )

    if df.empty:
        print(yellow("  No matching records found."))
        return

    # Pretty-print table
    display_cols = ["ID", "Role", "Company", "Location", "Job Type",
                    "Status", "Date Posted", "Date Scraped"]
    display_cols = [c for c in display_cols if c in df.columns]
    print(bold(f"\n{'─'*100}"))
    print(df[display_cols].to_string(index=False, max_colwidth=30))
    print(bold(f"{'─'*100}"))
    print(cyan(f"  Showing {len(df)} record(s).\n"))


def cmd_summary(args: argparse.Namespace) -> None:
    """Print the outreach dashboard."""
    OutreachTracker(args.output).summary()


def cmd_update(args: argparse.Namespace) -> None:
    """Update the status of a job entry."""
    tracker = OutreachTracker(args.output)
    action  = args.action.lower()
    notes   = args.notes or ""

    success = False
    if action == "emailed":
        success = tracker.mark_emailed(args.id, notes)
    elif action in ("followup", "follow-up", "followedup"):
        success = tracker.mark_followed_up(args.id, notes)
    elif action == "replied":
        success = tracker.mark_replied(args.id, notes)
    elif action == "rejected":
        success = tracker.mark_rejected(args.id, notes)
    else:
        print(red(f"  Unknown action: {action!r}"))
        print(yellow("  Valid actions: emailed | followup | replied | rejected"))
        sys.exit(1)

    if success:
        print(green(f"  ✓  ID={args.id} updated → {action.title()}"))
    else:
        print(red(f"  ✗  Failed to update ID={args.id}. Check the ID."))
        sys.exit(1)


def cmd_note(args: argparse.Namespace) -> None:
    """Append a free-text note to a job record."""
    tracker = OutreachTracker(args.output)
    success = tracker.add_note(args.id, args.text)
    if success:
        print(green(f"  ✓  Note added to ID={args.id}"))
    else:
        print(red(f"  ✗  ID={args.id} not found."))
        sys.exit(1)


def cmd_export(args: argparse.Namespace) -> None:
    """Export a filtered subset to a new Excel file."""
    tracker = OutreachTracker(args.output)

    if args.keyword:
        df = tracker.search(args.keyword)
        label = f"keyword={args.keyword!r}"
    elif args.internship:
        df = tracker.get_internships()
        label = "internship only"
    elif args.status:
        df = tracker.get_by_status(args.status)
        label = f"status={args.status!r}"
    else:
        df = tracker.store.load_dataframe()
        label = "all records"

    if df.empty:
        print(yellow("  No matching records to export."))
        return

    out = args.export_output or "export.xlsx"
    df.to_excel(out, index=False, sheet_name="Export")
    print(green(f"  ✓  Exported {len(df)} rows ({label}) → {out}"))


# ─── Argument parser ──────────────────────────────────────────────────────────

def cmd_web(args: argparse.Namespace) -> None:
    """Launch the web dashboard."""
    from webapp import app

    print(cyan(f"  Dashboard running at http://{args.host}:{args.port}"))
    app.run(host=args.host, port=args.port, debug=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-tracker",
        description=bold("🔗  LinkedIn Job Scraper & Outreach Tracker"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Global option: path to the Excel file
    parser.add_argument(
        "--output", "-o",
        default="data/linkedin_tracker.xlsx",
        metavar="FILE",
        help="Path to the Excel tracker file (default: data/linkedin_tracker.xlsx)",
    )

    sub = parser.add_subparsers(dest="command", title="commands")
    sub.required = True

    # ── scrape ────────────────────────────────────────────────────────────────
    p_scrape = sub.add_parser("scrape", help="Scrape LinkedIn job listings")
    p_scrape.add_argument("--keyword",   "-k", required=True, help="Search keyword (e.g. 'SDE Intern')")
    p_scrape.add_argument("--location",  "-l", default="",    help="Location filter (e.g. 'India', 'Remote')")
    p_scrape.add_argument("--type",      "-t", default="",
                          choices=["internship", "full-time", "part-time", "contract", "remote", ""],
                          help="Job type filter")
    p_scrape.add_argument("--work-mode", default="",
                          choices=["onsite", "remote", "hybrid", ""],
                          help="Work mode filter")
    p_scrape.add_argument("--max-pages", "-p", type=int, default=3,   help="Max result pages (default: 3)")
    p_scrape.add_argument("--max-results", "-n", type=int, default=100, help="Max total results (default: 100)")
    p_scrape.add_argument("--date-posted", "-d", default="",
                          choices=["past-24h", "past-week", "past-month", ""],
                          help="Filter by date posted")
    p_scrape.add_argument("--geo-id", "-g", default="",
                          help="LinkedIn numeric geoId. Auto-resolved for India/Bangalore/US etc. "
                               "Find yours by inspecting a LinkedIn Jobs search URL.")
    p_scrape.add_argument("--no-cookies",  action="store_true", help="Don't use session cookies")
    p_scrape.add_argument("--cards-only",  action="store_true",
                          help="Skip detail pages — faster & safer (recommended for first runs)")
    p_scrape.set_defaults(func=cmd_scrape)

    # ── list ──────────────────────────────────────────────────────────────────
    p_list = sub.add_parser("list", help="List jobs in the tracker")
    p_list.add_argument("--status",    "-s", default=None, help="Filter by status")
    p_list.add_argument("--keyword",   "-k", default=None, help="Search keyword")
    p_list.add_argument("--internship","-i", action="store_true", help="Show only internships")
    p_list.add_argument("--limit",     "-n", type=int, default=50, help="Max rows to show (default: 50)")
    p_list.set_defaults(func=cmd_list)

    # ── summary ───────────────────────────────────────────────────────────────
    p_sum = sub.add_parser("summary", help="Print outreach dashboard")
    p_sum.set_defaults(func=cmd_summary)

    # ── update ────────────────────────────────────────────────────────────────
    p_upd = sub.add_parser("update", help="Update the status of a job entry")
    p_upd.add_argument("--id",     "-i", type=int, required=True, help="Job record ID")
    p_upd.add_argument("--action", "-a", required=True,
                       choices=["emailed", "followup", "replied", "rejected"],
                       help="Status action to apply")
    p_upd.add_argument("--notes",  "-n", default="", help="Optional note to attach")
    p_upd.set_defaults(func=cmd_update)

    # ── note ──────────────────────────────────────────────────────────────────
    p_note = sub.add_parser("note", help="Append a note to a job record")
    p_note.add_argument("--id",   "-i", type=int, required=True, help="Job record ID")
    p_note.add_argument("--text", "-t", required=True, help="Note text")
    p_note.set_defaults(func=cmd_note)

    # ── export ────────────────────────────────────────────────────────────────
    p_exp = sub.add_parser("export", help="Export filtered results to a new file")
    p_exp.add_argument("--keyword",       "-k", default=None)
    p_exp.add_argument("--status",        "-s", default=None)
    p_exp.add_argument("--internship",    "-i", action="store_true")
    p_exp.add_argument("--export-output", "-e", default="export.xlsx", metavar="FILE")
    p_exp.set_defaults(func=cmd_export)

    p_web = sub.add_parser("web", help="Launch the web dashboard")
    p_web.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    p_web.add_argument("--port", type=int, default=5000, help="Port to bind (default: 5000)")
    p_web.set_defaults(func=cmd_web)

    return parser


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args   = parser.parse_args()

    # Pass output path down to every sub-command
    args.output = os.path.abspath(args.output)

    try:
        args.func(args)
    except KeyboardInterrupt:
        print(yellow("\n  Interrupted by user. Partial data may have been saved."))
        sys.exit(130)
    except Exception as exc:
        log.exception("Unhandled error: %s", exc)
        print(red(f"\n  ✗  Error: {exc}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
