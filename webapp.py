from __future__ import annotations

import os
import threading
from datetime import date, datetime
from typing import Any

import pandas as pd
from flask import Flask, jsonify, render_template, request, send_file

from config.settings import OUTPUT_FILE, STATUS_OPTIONS
from src.scraper import LinkedInScraper, scrape_jobs_live
from src.storage import ExcelStorage, WorkbookLockedError
from src.tracker import OutreachTracker
from src.utils import get_logger

app = Flask(__name__)
log = get_logger("webapp")

EXCEL_PATH = os.path.abspath(OUTPUT_FILE)
store = ExcelStorage(EXCEL_PATH)
tracker = OutreachTracker(EXCEL_PATH)

SCRAPE_LOCK = threading.Lock()
SCRAPE_STATE: dict[str, Any] = {
    "running": False,
    "event": "idle",
    "message": "Ready to scrape.",
    "page": 0,
    "total_jobs": 0,
    "rows_written": 0,
    "pages_completed": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "last_job": "",
    "config": {},
}


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _set_scrape_state(**updates: Any) -> None:
    with SCRAPE_LOCK:
        SCRAPE_STATE.update(updates)


def _get_scrape_state() -> dict[str, Any]:
    with SCRAPE_LOCK:
        return dict(SCRAPE_STATE)


def _progress_message(event: str, payload: dict[str, Any]) -> str:
    page = payload.get("page", 0)
    total_jobs = payload.get("total_jobs", 0)
    rows_written = payload.get("rows_written", 0)

    if event == "started":
        return "Scrape started."
    if event == "page_fetch_started":
        return f"Fetching page {page}."
    if event == "page_empty":
        return f"Page {page} returned no cards."
    if event == "job_added":
        role = payload.get("role") or "job"
        company = payload.get("company") or "company"
        return f"Captured {role} at {company}."
    if event == "page_completed":
        return f"Page {page} saved. {rows_written} row(s) written to Excel."
    if event == "max_results_reached":
        return f"Reached max results with {total_jobs} job(s)."
    if event == "stopped_after_empty_pages":
        return "Stopped after repeated empty pages."
    if event == "completed":
        return f"Scrape finished. {rows_written} new row(s) written."
    return "Scrape running."


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _load_dataframe() -> pd.DataFrame:
    df = store.load_dataframe()
    return df.fillna("")


def _contains(series: pd.Series, value: str) -> pd.Series:
    return series.astype(str).str.contains(value, case=False, na=False)


def _apply_table_filters(df: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    filtered = df.copy()
    search = str(params.get("search", "")).strip()
    status = str(params.get("status", "")).strip()
    location = str(params.get("location", "")).strip()
    job_type = str(params.get("job_type", "")).strip()
    work_mode = str(params.get("work_mode", "")).strip()

    if search:
        mask = pd.Series(False, index=filtered.index)
        for col in ["Role", "Company", "Location", "Description", "Requirements", "Notes"]:
            if col in filtered.columns:
                mask |= _contains(filtered[col], search)
        filtered = filtered[mask]

    if status:
        filtered = filtered[_contains(filtered["Status"], f"^{status}$")]

    if location:
        filtered = filtered[_contains(filtered["Location"], location)]

    if job_type:
        filtered = filtered[_contains(filtered["Job Type"], job_type)]

    if work_mode:
        filtered = filtered[_contains(filtered["Location"], work_mode) | _contains(filtered["Job Type"], work_mode)]

    return filtered


def _serialize_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "id": int(row["ID"]) if str(row.get("ID", "")).isdigit() else row.get("ID", ""),
                "company": row.get("Company", ""),
                "role": row.get("Role", ""),
                "location": row.get("Location", ""),
                "job_type": row.get("Job Type", ""),
                "status": row.get("Status", ""),
                "email": row.get("Email", ""),
                "recruiter_name": row.get("Recruiter Name", ""),
                "linkedin_url": row.get("LinkedIn URL", ""),
                "job_url": row.get("Job URL", ""),
                "description": row.get("Description", ""),
                "requirements": row.get("Requirements", ""),
                "date_posted": row.get("Date Posted", ""),
                "date_scraped": row.get("Date Scraped", ""),
                "notes": row.get("Notes", ""),
                "cold_email_sent": _normalize_bool(row.get("Cold Email Sent", "")),
                "followup_sent": _normalize_bool(row.get("Follow-up Sent", "")),
                "email_date": row.get("Email Date", ""),
                "followup_date": row.get("Follow-up Date", ""),
                "stipend": row.get("Stipend/Salary", ""),
            }
        )
    return rows


def _build_summary(all_df: pd.DataFrame, filtered_df: pd.DataFrame) -> dict[str, Any]:
    internship_count = 0
    awaiting_reply = 0
    status_counts: dict[str, int] = {}
    latest_scraped = ""

    if not all_df.empty:
        internship_count = int(all_df["Job Type"].astype(str).str.contains("intern", case=False, na=False).sum())
        awaiting_reply = int(all_df["Status"].isin(["Emailed", "Followed-up"]).sum())
        status_counts = {str(k): int(v) for k, v in all_df["Status"].value_counts().to_dict().items()}
        scraped_dates = [value for value in all_df["Date Scraped"].astype(str).tolist() if value]
        latest_scraped = max(scraped_dates) if scraped_dates else ""

    return {
        "total_records": int(len(all_df)),
        "filtered_records": int(len(filtered_df)),
        "internships": internship_count,
        "awaiting_reply": awaiting_reply,
        "latest_scraped": latest_scraped,
        "status_counts": status_counts,
    }


def _dashboard_payload(params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    all_df = _load_dataframe()
    filtered_df = _apply_table_filters(all_df, params)
    filtered_df = filtered_df.sort_values(by=["Date Scraped", "ID"], ascending=[False, False]) if not filtered_df.empty else filtered_df

    return {
        "summary": _build_summary(all_df, filtered_df),
        "rows": _serialize_rows(filtered_df.head(250)),
        "scrape": _get_scrape_state(),
        "excel_path": EXCEL_PATH,
        "download_url": "/download/excel",
    }


def _locked_response(exc: WorkbookLockedError):
    return jsonify({"error": str(exc), "locked": True}), 409


def _update_status(job_id: int, status: str, notes: str = "") -> bool:
    normalized = status.strip()
    if not normalized:
        return False

    if normalized == "Emailed":
        return tracker.mark_emailed(job_id, notes=notes)
    if normalized == "Followed-up":
        return tracker.mark_followed_up(job_id, notes=notes)
    if normalized == "Replied":
        return tracker.mark_replied(job_id, notes=notes)
    if normalized == "Rejected":
        return tracker.mark_rejected(job_id, notes=notes)

    updates: dict[str, Any] = {"Status": normalized}
    if normalized == "Not Contacted":
        updates.update(
            {
                "Cold Email Sent": False,
                "Email Date": "",
                "Follow-up Sent": False,
                "Follow-up Date": "",
            }
        )
    if normalized == "Hired":
        updates["Status"] = "Hired"
    if notes:
        updates["Notes"] = notes
    return store.update_row(job_id, updates)


def _run_scrape(config: dict[str, Any]) -> None:
    try:
        scraper = LinkedInScraper(
            use_cookies=not bool(config.get("no_cookies")),
            fetch_details=not bool(config.get("cards_only")),
        )

        def progress_callback(event: str, **payload: Any) -> None:
            updates = {
                "running": event != "completed",
                "event": event,
                "message": _progress_message(event, payload),
                "page": payload.get("page", SCRAPE_STATE.get("page", 0)),
                "total_jobs": payload.get("total_jobs", SCRAPE_STATE.get("total_jobs", 0)),
                "rows_written": payload.get("rows_written", SCRAPE_STATE.get("rows_written", 0)),
                "pages_completed": payload.get("pages_completed", SCRAPE_STATE.get("pages_completed", 0)),
                "last_job": payload.get("role", SCRAPE_STATE.get("last_job", "")),
                "error": None,
            }
            if event == "completed":
                updates["finished_at"] = _timestamp()
                updates["running"] = False
            _set_scrape_state(**updates)

        result = scrape_jobs_live(
            scraper=scraper,
            storage=store,
            keyword=config["keyword"],
            location=config.get("location", ""),
            job_type=config.get("job_type", ""),
            work_mode=config.get("work_mode", ""),
            max_pages=int(config.get("max_pages", 3)),
            max_results=int(config.get("max_results", 100)),
            date_posted=config.get("date_posted", ""),
            geo_id=config.get("geo_id", ""),
            progress_callback=progress_callback,
        )
        _set_scrape_state(
            running=False,
            event="completed",
            message=f"Scrape finished. {result['rows_written']} new row(s) written.",
            total_jobs=result["total_jobs"],
            rows_written=result["rows_written"],
            pages_completed=result["pages_completed"],
            finished_at=_timestamp(),
            error=None,
        )
    except WorkbookLockedError as exc:
        log.warning("Dashboard scrape blocked by locked workbook: %s", exc)
        _set_scrape_state(
            running=False,
            event="failed",
            message=str(exc),
            error=str(exc),
            finished_at=_timestamp(),
        )
    except Exception as exc:
        log.exception("Dashboard scrape failed: %s", exc)
        _set_scrape_state(
            running=False,
            event="failed",
            message=f"Scrape failed: {exc}",
            error=str(exc),
            finished_at=_timestamp(),
        )


@app.get("/")
def index():
    return render_template("dashboard.html", status_options=STATUS_OPTIONS)


@app.get("/api/dashboard")
def dashboard_data():
    params = request.args.to_dict()
    try:
        return jsonify(_dashboard_payload(params))
    except WorkbookLockedError as exc:
        return _locked_response(exc)


@app.get("/api/scrape/status")
def scrape_status():
    return jsonify(_get_scrape_state())


@app.post("/api/scrape/start")
def start_scrape():
    payload = request.get_json(silent=True) or request.form.to_dict()
    keyword = str(payload.get("keyword", "")).strip()
    if not keyword:
        return jsonify({"error": "Keyword is required."}), 400

    current = _get_scrape_state()
    if current.get("running"):
        return jsonify({"error": "A scrape is already running."}), 409

    config = {
        "keyword": keyword,
        "location": str(payload.get("location", "")).strip(),
        "geo_id": str(payload.get("geo_id", "")).strip(),
        "job_type": str(payload.get("job_type", "")).strip(),
        "work_mode": str(payload.get("work_mode", "")).strip(),
        "date_posted": str(payload.get("date_posted", "")).strip(),
        "max_pages": int(payload.get("max_pages", 3) or 3),
        "max_results": int(payload.get("max_results", 100) or 100),
        "cards_only": bool(payload.get("cards_only")),
        "no_cookies": bool(payload.get("no_cookies")),
    }

    _set_scrape_state(
        running=True,
        event="queued",
        message="Scrape queued.",
        page=0,
        total_jobs=0,
        rows_written=0,
        pages_completed=0,
        started_at=_timestamp(),
        finished_at=None,
        error=None,
        last_job="",
        config=config,
    )

    thread = threading.Thread(target=_run_scrape, args=(config,), daemon=True)
    thread.start()
    return jsonify({"ok": True, "scrape": _get_scrape_state()})


@app.post("/api/jobs/<int:job_id>/action")
def update_job_action(job_id: int):
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action", "")).strip().lower()
    notes = str(payload.get("notes", "")).strip()

    try:
        success = False
        if action == "emailed":
            success = tracker.mark_emailed(job_id, notes=notes)
        elif action in {"followup", "follow-up", "followedup"}:
            success = tracker.mark_followed_up(job_id, notes=notes)
        elif action == "replied":
            success = tracker.mark_replied(job_id, notes=notes)
        elif action == "rejected":
            success = tracker.mark_rejected(job_id, notes=notes)
        elif action == "hired":
            success = store.update_row(job_id, {"Status": "Hired", "Notes": notes} if notes else {"Status": "Hired"})
    except WorkbookLockedError as exc:
        return _locked_response(exc)

    if not success:
        return jsonify({"error": "Unable to update that row."}), 400

    try:
        return jsonify({"ok": True, "dashboard": _dashboard_payload()})
    except WorkbookLockedError as exc:
        return _locked_response(exc)


@app.post("/api/jobs/<int:job_id>/status")
def update_job_status(job_id: int):
    payload = request.get_json(silent=True) or {}
    status = str(payload.get("status", "")).strip()
    notes = str(payload.get("notes", "")).strip()

    try:
        if not _update_status(job_id, status, notes):
            return jsonify({"error": "Unable to save status."}), 400
    except WorkbookLockedError as exc:
        return _locked_response(exc)

    try:
        return jsonify({"ok": True, "dashboard": _dashboard_payload()})
    except WorkbookLockedError as exc:
        return _locked_response(exc)


@app.post("/api/jobs/<int:job_id>/note")
def save_job_note(job_id: int):
    payload = request.get_json(silent=True) or {}
    note = str(payload.get("note", "")).strip()
    if not note:
        return jsonify({"error": "Note cannot be empty."}), 400

    try:
        if not tracker.add_note(job_id, note):
            return jsonify({"error": "Unable to save note."}), 400
    except WorkbookLockedError as exc:
        return _locked_response(exc)

    try:
        return jsonify({"ok": True, "dashboard": _dashboard_payload()})
    except WorkbookLockedError as exc:
        return _locked_response(exc)


@app.get("/download/excel")
def download_excel():
    if not os.path.exists(EXCEL_PATH):
        return jsonify({"error": "Excel file not found yet."}), 404
    return send_file(EXCEL_PATH, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
