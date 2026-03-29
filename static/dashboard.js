const scrapeForm = document.getElementById("scrape-form");
const filterForm = document.getElementById("filter-form");
const jobsBody = document.getElementById("jobs-body");
const summaryGrid = document.getElementById("summary-grid");
const summaryTemplate = document.getElementById("summary-card-template");

const scrapeMessage = document.getElementById("scrape-message");
const scrapePage = document.getElementById("scrape-page");
const scrapeTotal = document.getElementById("scrape-total");
const scrapeWritten = document.getElementById("scrape-written");

let pollTimer = null;

function formToObject(form) {
  const data = new FormData(form);
  return Object.fromEntries(data.entries());
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed.");
  }
  return data;
}

function updateScrapeState(scrape) {
  scrapeMessage.textContent = scrape.message || "Ready to scrape.";
  scrapeMessage.classList.toggle("warning-text", Boolean(scrape.error));
  scrapePage.textContent = scrape.page || 0;
  scrapeTotal.textContent = scrape.total_jobs || 0;
  scrapeWritten.textContent = scrape.rows_written || 0;
}

function renderSummary(summary) {
  summaryGrid.innerHTML = "";
  const cards = [
    ["Total Records", summary.total_records, "All Excel rows currently tracked."],
    ["Filtered Rows", summary.filtered_records, "Current table view after filters."],
    ["Internships", summary.internships, "Roles tagged as internship."],
    ["Awaiting Reply", summary.awaiting_reply, "Rows in Emailed or Followed-up."],
    ["Last Scraped", summary.latest_scraped || "-", "Latest scrape date in workbook."],
  ];

  cards.forEach(([label, value, meta]) => {
    const node = summaryTemplate.content.cloneNode(true);
    node.querySelector(".summary-label").textContent = label;
    node.querySelector(".summary-value").textContent = value;
    node.querySelector(".summary-meta").textContent = meta;
    summaryGrid.appendChild(node);
  });
}

function buildStatusOptions(currentStatus) {
  return window.STATUS_OPTIONS.map((status) => {
    const selected = status === currentStatus ? "selected" : "";
    return `<option value="${status}" ${selected}>${status}</option>`;
  }).join("");
}

function renderRows(rows) {
  if (!rows.length) {
    jobsBody.innerHTML = `<tr><td colspan="9" class="empty-state">No matching rows yet. Start a scrape or widen the table filters.</td></tr>`;
    return;
  }

  jobsBody.innerHTML = rows.map((row) => `
    <tr>
      <td>#${row.id}</td>
      <td>
        <div class="meta-stack">
          <strong>${escapeHTML(row.role || "-")}</strong>
          <span>${escapeHTML(row.job_type || "-")}</span>
          ${row.stipend ? `<span>${escapeHTML(row.stipend)}</span>` : ""}
        </div>
      </td>
      <td>
        <div class="meta-stack">
          <strong>${escapeHTML(row.company || "-")}</strong>
          <span>${escapeHTML(row.recruiter_name || "Recruiter not found")}</span>
          ${row.email ? `<span>${escapeHTML(row.email)}</span>` : ""}
        </div>
      </td>
      <td>${escapeHTML(row.location || "-")}</td>
      <td>
        <div class="action-stack">
          <span class="pill">${escapeHTML(row.status || "Not Contacted")}</span>
          <select data-status-id="${row.id}">
            ${buildStatusOptions(row.status)}
          </select>
          <button class="secondary" data-save-status="${row.id}">Save Status</button>
        </div>
      </td>
      <td>
        <div class="meta-stack">
          <span>Posted: ${escapeHTML(row.date_posted || "-")}</span>
          <span>Scraped: ${escapeHTML(row.date_scraped || "-")}</span>
          <span>Email: ${escapeHTML(row.email_date || "-")}</span>
          <span>Follow-up: ${escapeHTML(row.followup_date || "-")}</span>
        </div>
      </td>
      <td>
        <div class="link-stack">
          ${row.job_url ? `<a href="${row.job_url}" target="_blank" rel="noreferrer">Job post</a>` : ""}
          ${row.linkedin_url ? `<a href="${row.linkedin_url}" target="_blank" rel="noreferrer">Recruiter profile</a>` : ""}
        </div>
      </td>
      <td>
        <div class="action-stack note-box">
          <textarea data-note-input="${row.id}" placeholder="Add note for this row">${escapeHTML(row.notes || "")}</textarea>
          <button class="secondary save-note" data-save-note="${row.id}">Add Note</button>
        </div>
      </td>
      <td>
        <div class="tiny-actions">
          <button data-action="emailed" data-job-id="${row.id}">Emailed</button>
          <button class="secondary" data-action="followup" data-job-id="${row.id}">Follow-up</button>
          <button class="secondary" data-action="replied" data-job-id="${row.id}">Replied</button>
          <button class="secondary" data-action="rejected" data-job-id="${row.id}">Rejected</button>
          <button class="secondary" data-action="hired" data-job-id="${row.id}">Hired</button>
        </div>
      </td>
    </tr>
  `).join("");
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function refreshDashboard() {
  const params = new URLSearchParams(formToObject(filterForm));
  try {
    const data = await fetchJSON(`/api/dashboard?${params.toString()}`);
    updateScrapeState(data.scrape);
    renderSummary(data.summary);
    renderRows(data.rows);
  } catch (error) {
    scrapeMessage.textContent = error.message;
    scrapeMessage.classList.add("warning-text");
  }
}

async function startScrape(event) {
  event.preventDefault();
  const payload = formToObject(scrapeForm);
  payload.cards_only = scrapeForm.cards_only.checked;
  payload.no_cookies = scrapeForm.no_cookies.checked;

  try {
    await fetchJSON("/api/scrape/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await refreshDashboard();
  } catch (error) {
    alert(error.message);
  }
}

async function handleTableClick(event) {
  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    const jobId = actionButton.dataset.jobId;
    const note = document.querySelector(`[data-note-input="${jobId}"]`)?.value || "";
    try {
      await fetchJSON(`/api/jobs/${jobId}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: actionButton.dataset.action, notes: note }),
      });
      await refreshDashboard();
    } catch (error) {
      alert(error.message);
    }
    return;
  }

  const saveNoteButton = event.target.closest("[data-save-note]");
  if (saveNoteButton) {
    const jobId = saveNoteButton.dataset.saveNote;
    const note = document.querySelector(`[data-note-input="${jobId}"]`)?.value || "";
    try {
      await fetchJSON(`/api/jobs/${jobId}/note`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      await refreshDashboard();
    } catch (error) {
      alert(error.message);
    }
    return;
  }

  const saveStatusButton = event.target.closest("[data-save-status]");
  if (saveStatusButton) {
    const jobId = saveStatusButton.dataset.saveStatus;
    const status = document.querySelector(`[data-status-id="${jobId}"]`)?.value || "";
    const note = document.querySelector(`[data-note-input="${jobId}"]`)?.value || "";
    try {
      await fetchJSON(`/api/jobs/${jobId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, notes: note }),
      });
      await refreshDashboard();
    } catch (error) {
      alert(error.message);
    }
  }
}

function startPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
  }
  pollTimer = setInterval(() => {
    refreshDashboard().catch((error) => console.error(error));
  }, 3000);
}

scrapeForm.addEventListener("submit", startScrape);
filterForm.addEventListener("input", () => {
  refreshDashboard().catch((error) => console.error(error));
});
jobsBody.addEventListener("click", handleTableClick);
document.getElementById("refresh-dashboard").addEventListener("click", () => {
  refreshDashboard().catch((error) => console.error(error));
});

refreshDashboard().catch((error) => console.error(error));
startPolling();
