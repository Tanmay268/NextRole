"""
tracker.py — Cold-outreach tracking layer.

Wraps ExcelStorage to provide domain-friendly methods:

  - mark_emailed(job_id)         → sets Cold Email Sent=True, Status=Emailed
  - mark_followed_up(job_id)     → sets Follow-up Sent=True, Status=Followed-up
  - mark_replied(job_id, notes)  → Status=Replied + appends note
  - mark_rejected(job_id, notes) → Status=Rejected + appends note
  - get_pending()                → rows that are still 'Not Contacted'
  - get_awaiting_reply()         → rows that are 'Emailed' or 'Followed-up'
  - summary()                    → print a quick dashboard to stdout
"""

from datetime import date
from typing import Optional

import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage import ExcelStorage
from src.utils import get_logger

log = get_logger(__name__)


class OutreachTracker:
    """
    High-level outreach tracking interface.

    Usage::

        tracker = OutreachTracker()

        # After sending a cold email to job_id=7:
        tracker.mark_emailed(7, notes="Sent via Gmail at 10am")

        # After a follow-up 1 week later:
        tracker.mark_followed_up(7)

        # They replied!
        tracker.mark_replied(7, notes="Positive — scheduled interview")

        # See everything pending:
        df = tracker.get_pending()
        print(df[["ID", "Role", "Company", "Status"]])

        # Print summary dashboard:
        tracker.summary()
    """

    def __init__(self, filepath: Optional[str] = None):
        self.store = ExcelStorage(filepath) if filepath else ExcelStorage()

    # ─── Status updaters ──────────────────────────────────────────────────────

    def mark_emailed(self, job_id: int, notes: str = "") -> bool:
        """Record that a cold email was sent today for the given job ID."""
        today = date.today().isoformat()
        updates = {
            "Cold Email Sent": True,
            "Email Date":      today,
            "Status":          "Emailed",
        }
        if notes:
            updates["Notes"] = notes
        success = self.store.update_row(job_id, updates)
        if success:
            log.info("Marked ID=%d as Emailed on %s.", job_id, today)
        return success

    def mark_followed_up(self, job_id: int, notes: str = "") -> bool:
        """Record that a follow-up email was sent today."""
        today = date.today().isoformat()
        updates = {
            "Follow-up Sent": True,
            "Follow-up Date": today,
            "Status":         "Followed-up",
        }
        if notes:
            updates["Notes"] = notes
        success = self.store.update_row(job_id, updates)
        if success:
            log.info("Marked ID=%d as Followed-up on %s.", job_id, today)
        return success

    def mark_replied(self, job_id: int, notes: str = "") -> bool:
        """Record that the recruiter replied."""
        updates = {"Status": "Replied"}
        if notes:
            updates["Notes"] = notes
        success = self.store.update_row(job_id, updates)
        if success:
            log.info("Marked ID=%d as Replied.", job_id)
        return success

    def mark_rejected(self, job_id: int, notes: str = "") -> bool:
        """Record a rejection."""
        updates = {"Status": "Rejected"}
        if notes:
            updates["Notes"] = notes
        success = self.store.update_row(job_id, updates)
        if success:
            log.info("Marked ID=%d as Rejected.", job_id)
        return success

    def add_note(self, job_id: int, note: str) -> bool:
        """Append a free-text note to a job record."""
        df = self.store.load_dataframe()
        row = df[df["ID"].astype(str) == str(job_id)]
        if row.empty:
            log.warning("ID=%d not found.", job_id)
            return False

        existing_note = row.iloc[0]["Notes"]
        separator     = "\n" if existing_note else ""
        new_note      = f"{existing_note}{separator}[{date.today().isoformat()}] {note}"
        return self.store.update_row(job_id, {"Notes": new_note})

    # ─── Query helpers ────────────────────────────────────────────────────────

    def get_pending(self) -> pd.DataFrame:
        """Return all rows with Status='Not Contacted'."""
        df = self.store.load_dataframe()
        return df[df["Status"] == "Not Contacted"].copy()

    def get_awaiting_reply(self) -> pd.DataFrame:
        """Return rows with Status in {Emailed, Followed-up}."""
        df   = self.store.load_dataframe()
        mask = df["Status"].isin(["Emailed", "Followed-up"])
        return df[mask].copy()

    def get_by_status(self, status: str) -> pd.DataFrame:
        """Filter by any status string."""
        df = self.store.load_dataframe()
        return df[df["Status"].str.lower() == status.lower()].copy()

    def get_internships(self) -> pd.DataFrame:
        """Return only internship listings."""
        return self.store.filter_internships()

    def search(self, keyword: str) -> pd.DataFrame:
        """Full-text search across key fields."""
        return self.store.search(keyword)

    # ─── Dashboard ────────────────────────────────────────────────────────────

    def summary(self) -> None:
        """Print a concise outreach dashboard to the terminal."""
        df = self.store.load_dataframe()
        if df.empty:
            print("No data found. Run `scrape` first.")
            return

        total      = len(df)
        status_cnt = df["Status"].value_counts()
        internship = df["Job Type"].str.contains("internship", case=False, na=False).sum()

        line = "─" * 50
        print(f"\n{'═'*50}")
        print(f"  📊  LinkedIn Outreach Tracker — Summary")
        print(f"{'═'*50}")
        print(f"  Total records     : {total}")
        print(f"  Internships       : {internship}")
        print(f"{line}")
        print(f"  Status breakdown:")
        for status, count in status_cnt.items():
            bar = "█" * min(count, 30)
            print(f"    {status:<18} {count:>4}  {bar}")
        print(f"{'═'*50}\n")

    def list_jobs(
        self,
        status_filter: Optional[str] = None,
        keyword: Optional[str] = None,
        internship_only: bool = False,
        limit: int = 50,
    ) -> pd.DataFrame:
        """
        Flexible listing method used by the CLI.
        Applies status, keyword, and type filters in sequence.
        """
        if keyword:
            df = self.search(keyword)
        elif internship_only:
            df = self.get_internships()
        else:
            df = self.store.load_dataframe()

        if status_filter:
            df = df[df["Status"].str.lower() == status_filter.lower()]

        return df.head(limit)
