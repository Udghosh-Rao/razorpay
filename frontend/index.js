const state = { analysis: null, evaluation: null, file: null };
// Chart instance registry — destroy before re-render to prevent canvas reuse errors
const _charts = {};

function destroyChart(id) {
    if (_charts[id]) { try { _charts[id].destroy(); } catch (_) {} delete _charts[id]; }
}

// Session isolation: unique ID per browser tab
const SESSION_ID = (() => {
    let sid = sessionStorage.getItem("sentinel_session_id");
    if (!sid) {
        sid = "sess_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);
        sessionStorage.setItem("sentinel_session_id", sid);
    }
    return sid;
})();

const pageMeta = {
    overview:       ["Summary",           "Your current fraud position at a glance."],
    analyze:        ["Upload Data",        "Import your payment transaction export to begin the fraud check."],
    findings:       ["Alerts & Flags",     "Suspicious merchants and accounts flagged from your data, by financial impact."],
    impact:         ["Financial Exposure", "How much money is tied to flagged transactions — only shown when your data supports it."],
    investigations: ["Case Review",        "Look up any merchant or account and get a plain-English case summary."],
    model:          ["Detection Report",   "How Sentinel spots suspicious activity and how accurate it was on your data."],
    audit:          ["Activity Log",       "Every upload, analysis, and case review recorded in order."],
};

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

const num = (value, digits = 0) => (value === null || value === undefined || isNaN(Number(value)))
    ? "—"
    : Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });

const pct = (value, digits = 1) => (value === null || value === undefined || isNaN(Number(value)))
    ? "—"
    : `${(Number(value) * 100).toFixed(digits)}%`;

const money = (value) => (value === null || value === undefined || isNaN(Number(value)))
    ? "—"
    : `₹${Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

const empty = (title, message) =>
    `<div class="empty"><strong>${esc(title)}</strong>${esc(message)}</div>`;

// Chart.js global style
function applyChartDefaults() {
    if (!window.Chart) return;
    Chart.defaults.font.family = "'DM Sans', -apple-system, sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.color = "#6c7782";
    Chart.defaults.plugins.legend.labels.boxWidth = 10;
    Chart.defaults.plugins.legend.labels.padding = 14;
    Chart.defaults.plugins.tooltip.backgroundColor = "#16232d";
    Chart.defaults.plugins.tooltip.titleColor = "#fff";
    Chart.defaults.plugins.tooltip.bodyColor = "#a9ded7";
    Chart.defaults.plugins.tooltip.cornerRadius = 7;
    Chart.defaults.plugins.tooltip.padding = 10;
}

document.addEventListener("DOMContentLoaded", () => {
    applyChartDefaults();
    document.querySelectorAll("[data-view]").forEach((el) =>
        el.addEventListener("click", (e) => { e.preventDefault(); showView(el.dataset.view); }));
    document.querySelectorAll("[data-view-link]").forEach((el) =>
        el.addEventListener("click", () => showView(el.dataset.viewLink)));
    document.querySelectorAll("[data-action]").forEach((el) =>
        el.addEventListener("click", () => {
            if (el.dataset.action === "open-upload") showView("analyze");
            if (el.dataset.action === "how-it-works") document.getElementById("how-it-works").scrollIntoView({ behavior: "smooth" });
            if (el.dataset.action === "choose-file") document.getElementById("csv-file-input").click();
        }));
    document.getElementById("refresh-button").addEventListener("click", refreshActive);
    document.getElementById("csv-file-input").addEventListener("change", onFileSelected);
    document.getElementById("run-analysis").addEventListener("click", runAnalysis);
    document.getElementById("investigate-button").addEventListener("click", runInvestigation);
    updateSystemStatus();
    loadOverview();
});

function showView(view) {
    document.querySelectorAll(".nav-item").forEach((item) =>
        item.classList.toggle("active", item.dataset.view === view));
    document.querySelectorAll(".view").forEach((section) =>
        section.classList.toggle("active", section.id === `view-${view}`));
    document.getElementById("page-title").textContent = pageMeta[view][0];
    document.getElementById("page-subtitle").textContent = pageMeta[view][1];
    if (view === "overview")       state.analysis ? renderAnalysisOverview(state.analysis) : loadOverview();
    if (view === "findings")       renderFindings();
    if (view === "impact")         renderImpact();
    if (view === "model")          loadModel();
    if (view === "audit")          loadAudit();
}

function refreshActive() {
    const active = document.querySelector(".nav-item.active");
    if (active) showView(active.dataset.view);
}

const sessionHeaders = () => ({ "X-Session-ID": SESSION_ID });

async function getJson(url) {
    const res = await fetch(url, { headers: sessionHeaders() });
    if (!res.ok) throw new Error(`Request failed (${res.status})`);
    return res.json();
}

async function updateSystemStatus() {
    try {
        const data = await getJson("/health");
        document.getElementById("system-status-text").textContent =
            data.status === "healthy" ? "System online" : "System degraded";
    } catch {
        document.getElementById("system-status-text").textContent = "Offline";
    }
}

// ─── Overview ──────────────────────────────────────────────────────────────

async function loadOverview() {
    const metrics = document.getElementById("overview-metrics");
    try {
        const data = await getJson("/api/risk/overview");
        if (!data.session_has_analysis && !state.analysis) {
            metrics.innerHTML = `
                <div class="metric"><div class="metric-label">Transactions checked</div><div class="metric-value">—</div><div class="metric-note">No file uploaded yet</div></div>
                <div class="metric"><div class="metric-label">Merchants flagged</div><div class="metric-value">—</div><div class="metric-note">No file uploaded yet</div></div>
                <div class="metric"><div class="metric-label">Linked account groups</div><div class="metric-value">—</div><div class="metric-note">No file uploaded yet</div></div>
                <div class="metric"><div class="metric-label">Estimated exposure</div><div class="metric-value">—</div><div class="metric-note">No file uploaded yet</div></div>`;
            renderOverviewFindings([]);
            document.getElementById("overview-action").innerHTML = `
                <span class="decision decision-allow">READY</span>
                <h3>Upload a transaction file to begin.</h3>
                <p>Once you upload your payment data, Sentinel will flag suspicious merchants, score accounts, and estimate your financial exposure.</p>
                <button class="button button-primary" style="margin-top:12px;" data-action="open-upload">Upload Transactions →</button>`;
            document.getElementById("analysis-detail-panels").innerHTML = "";
            const visEl = document.getElementById("overview-visuals");
            if (visEl) visEl.innerHTML = "";
            document.querySelectorAll("[data-action]").forEach((el) =>
                el.addEventListener("click", () => { if (el.dataset.action === "open-upload") showView("analyze"); }));
            return;
        }
        const m = data.metrics || {};
        metrics.innerHTML = `
            <div class="metric"><div class="metric-label">Transactions checked</div><div class="metric-value">${num(m.transactions_processed)}</div><div class="metric-note">From the latest upload</div></div>
            <div class="metric"><div class="metric-label">Merchants flagged</div><div class="metric-value">${num(m.active_spikes_detected)}</div><div class="metric-note">Unusual activity detected</div></div>
            <div class="metric"><div class="metric-label">Linked account groups</div><div class="metric-value">${num(m.active_clusters_detected)}</div><div class="metric-note">Accounts sharing behaviour</div></div>
            <div class="metric"><div class="metric-label">Estimated exposure</div><div class="metric-value">${money(m.potential_exposure_inr)}</div><div class="metric-note">${m.potential_exposure_inr != null ? "Value in flagged transactions" : "Needs fraud-label column"}</div></div>`;
        renderOverviewFindings(data.recent_spikes || []);
        document.getElementById("overview-action").innerHTML = data.recent_spikes?.length
            ? `<span class="decision decision-review">ACTION NEEDED</span><h3>Review the top flagged merchant.</h3><p>Suspicious activity has been detected. Open Alerts & Flags to see what changed, the supporting evidence, and the recommended action.</p><button class="text-button" data-view-link="findings">Go to alerts →</button>`
            : `<span class="decision decision-allow">ALL CLEAR</span><h3>No unusual merchant activity detected.</h3><p>Your transaction data looks normal against the baseline. Upload a newer file to keep monitoring.</p><button class="text-button" data-action="open-upload">Upload another file →</button>`;
        document.querySelectorAll("[data-view-link]").forEach((el) => el.addEventListener("click", () => showView(el.dataset.viewLink)));
        document.querySelectorAll("[data-action]").forEach((el) => el.addEventListener("click", () => { if (el.dataset.action === "open-upload") showView("analyze"); }));
    } catch (error) {
        metrics.innerHTML = empty("Dashboard unavailable", error.message);
    }
}

function renderOverviewFindings(spikes) {
    const target = document.getElementById("overview-findings");
    if (!spikes.length) {
        target.innerHTML = empty("No active alerts", "Upload a transaction CSV to scan for suspicious activity.");
        return;
    }
    target.innerHTML = spikes.slice(0, 4).map((spike) => `
        <div class="finding-row">
            <div>
                <div class="finding-title">${esc(spike.merchant_id || "Merchant")}</div>
                <div class="finding-description">${esc(spike.description || "Suspicious payment activity detected above the normal baseline.")}</div>
            </div>
            <span class="decision decision-${String(spike.severity || "review").toLowerCase() === "critical" ? "block" : "review"}">${esc(spike.severity || "REVIEW")}</span>
        </div>`).join("");
}

// ─── Upload & Analysis ─────────────────────────────────────────────────────

function onFileSelected(event) {
    state.file = event.target.files[0];
    document.getElementById("selected-file").textContent =
        state.file ? `${state.file.name} · ${(state.file.size / 1024).toFixed(1)} KB` : "";
    document.getElementById("run-analysis").hidden = !state.file;
}

async function runAnalysis() {
    if (!state.file) return;
    const status = document.getElementById("upload-status");
    status.innerHTML = `<div class="status-card"><strong>Reading ${esc(state.file.name)}…</strong><p class="finding-description">Identifying available columns and running the fraud check.</p></div>`;
    const form = new FormData();
    form.append("file", state.file);
    try {
        const response = await fetch("/api/upload", { method: "POST", headers: sessionHeaders(), body: form });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Upload failed");
        state.analysis = data;
        renderUploadResult(data);
        document.getElementById("upload-status").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        status.innerHTML = `<div class="status-card error">
            <strong>We couldn't process this file.</strong>
            <p style="margin: 8px 0 12px; line-height: 1.5;">${esc(error.message)}</p>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px;">
                <a href="/api/csv-template" class="button button-secondary"
                   style="text-decoration:none;padding:6px 14px;font-size:13px;" download="sample_transactions.csv">
                    Download sample file ↓
                </a>
            </div>
        </div>`;
    }
}

function renderUploadResult(data) {
    const schema = data.schema_report || {};
    const validation = data.validation_summary || {};
    const recognized = schema.recognized_columns || validation.recognized_columns || [];
    const known = ["timestamp", "amount", "account_id", "merchant_id", "device_id", "payment_method", "status", "fraud_label"];

    // Human-readable column names
    const friendlyName = {
        timestamp: "Transaction date/time", amount: "Transaction amount",
        account_id: "Customer account ID", merchant_id: "Merchant ID",
        device_id: "Device ID", payment_method: "Payment method",
        status: "Transaction status", fraud_label: "Fraud label (ground truth)",
    };

    const available = known.filter((f) => recognized.includes(f));
    const missing = known.filter((f) => !recognized.includes(f));
    const checks = [
        ["Unusual merchant activity",   true],
        ["Transaction amount spikes",   available.includes("amount")],
        ["Time-of-day patterns",        available.includes("timestamp")],
        ["Merchant-level behaviour",    available.includes("merchant_id")],
        ["Customer account behaviour",  available.includes("account_id")],
    ];

    document.getElementById("upload-status").innerHTML = `<div class="status-card success">
        <h3>Fraud check complete</h3>
        <p class="finding-description">We've processed your file and completed all supported checks.</p>
        <div class="capability-columns">
            <div>
                <h4>${num(validation.rows_received)} rows found</h4>
                <ul class="capability-list">
                    ${available.map((f) => `<li class="available">✓ ${esc(friendlyName[f] || f)}</li>`).join("") || "<li>No recognised columns</li>"}
                </ul>
            </div>
            <div>
                <h4>Checks performed</h4>
                <ul class="capability-list">
                    ${checks.map(([label, ok]) => `<li class="${ok ? "available" : "unavailable"}">${ok ? "✓" : "○"} ${label}</li>`).join("")}
                </ul>
            </div>
            <div>
                <h4>Not available</h4>
                <ul class="capability-list">
                    ${missing.map((f) => `<li class="unavailable">○ ${esc(friendlyName[f] || f)} not found</li>`).join("")
                      || `<li class="available">✓ All supported columns present</li>`}
                </ul>
            </div>
        </div>
        <div class="status-meta">
            ${num(validation.valid_rows)} valid rows · ${num(validation.invalid_rows)} excluded
            · ${schema.model_scoring_available ? "Account risk ratings calculated" : "Account risk rating unavailable — needs account ID and amount columns"}
            · ${schema.ground_truth_available ? "Accuracy metrics available (fraud labels found)" : "Accuracy metrics unavailable — no fraud-label column"}
            ${validation.warning ? ` · ${esc(validation.warning)}` : ""}
        </div>
        <div class="status-flow">
            <span class="status-step">✓ File read</span>
            <span class="status-step">✓ Columns identified</span>
            <span class="status-step">✓ Data validated</span>
            <span class="status-step">✓ Activity scanned</span>
            <span class="status-step">✓ Alerts generated</span>
        </div>
        <button class="button button-primary result-button" id="view-analysis-result">View results →</button>
    </div>`;
    document.getElementById("view-analysis-result").addEventListener("click", () => {
        showView("overview");
        renderAnalysisOverview(data);
    });
}

// ─── Chart helpers ─────────────────────────────────────────────────────────

function makeCanvas(id, height = 220) {
    return `<canvas id="${id}" height="${height}" style="width:100%;"></canvas>`;
}

/** Risk rating histogram — labelled in plain language */
function renderRiskHistogramChart(canvasId, results) {
    destroyChart(canvasId);
    if (!results || !results.length) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const bins = new Array(10).fill(0);
    results.forEach((r) => {
        const score = Math.min(0.9999, Math.max(0, Number(r.risk_score) || 0));
        bins[Math.floor(score * 10)]++;
    });
    // Business-friendly x-axis labels
    const labels = ["Very Low","Low","Low-Med","Medium","Med-High","Elevated","High","High","Very High","Critical"];
    const bgColors = [
        "#2c7a59","#2c7a59","#2c7a59",
        "#b36b16","#b36b16","#b36b16",
        "#b84942","#b84942","#b84942","#b84942"
    ];
    _charts[canvasId] = new Chart(canvas, {
        type: "bar",
        data: {
            labels,
            datasets: [{
                label: "Accounts",
                data: bins,
                backgroundColor: bgColors,
                borderRadius: 4,
                borderSkipped: false,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => `Risk level: ${items[0].label}`,
                        label: (item) => ` ${item.raw} account(s)`,
                    }
                }
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 10 } } },
                y: { beginAtZero: true, grid: { color: "#f0f2f1" }, ticks: { precision: 0, stepSize: 1 } },
            }
        }
    });
}

/** Decision breakdown donut — ALLOW/REVIEW/BLOCK in business terms */
function renderDecisionDonutChart(canvasId, results) {
    destroyChart(canvasId);
    if (!results || !results.length) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const counts = { ALLOW: 0, REVIEW: 0, BLOCK: 0 };
    results.forEach((r) => { const d = r.decision || "ALLOW"; counts[d] = (counts[d] || 0) + 1; });
    _charts[canvasId] = new Chart(canvas, {
        type: "doughnut",
        data: {
            labels: ["Cleared", "Needs Review", "Blocked"],
            datasets: [{
                data: [counts.ALLOW, counts.REVIEW, counts.BLOCK],
                backgroundColor: ["#2c7a59", "#b36b16", "#b84942"],
                borderWidth: 2,
                borderColor: "#fff",
                hoverOffset: 6,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: "62%",
            plugins: {
                legend: { position: "bottom", labels: { color: "#6c7782", padding: 16 } },
                tooltip: {
                    callbacks: {
                        label: (item) => ` ${item.label}: ${item.raw} account(s) (${((item.raw / results.length) * 100).toFixed(1)}%)`
                    }
                }
            }
        }
    });
}

/** Merchant spike horizontal bar — business-friendly axes */
function renderSpikesBarChart(canvasId, spikes) {
    destroyChart(canvasId);
    if (!spikes || !spikes.length) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const top = spikes.slice(0, 6);
    _charts[canvasId] = new Chart(canvas, {
        type: "bar",
        data: {
            labels: top.map((s) => String(s.merchant_id || "Unknown").slice(0, 20)),
            datasets: [
                {
                    label: "Normal rate",
                    data: top.map((s) => +((Number(s.baseline_rate) || 0) * 100).toFixed(2)),
                    backgroundColor: "#9ca6ad",
                    borderRadius: 3,
                },
                {
                    label: "Flagged rate",
                    data: top.map((s) => +((Number(s.recent_rate) || 0) * 100).toFixed(2)),
                    backgroundColor: "#b84942",
                    borderRadius: 3,
                },
            ]
        },
        options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: "bottom" },
                tooltip: { callbacks: { label: (item) => ` ${item.dataset.label}: ${item.raw}% of transactions` } }
            },
            scales: {
                x: {
                    grid: { color: "#f0f2f1" },
                    ticks: { callback: (v) => v + "%" },
                    beginAtZero: true,
                    title: { display: true, text: "% of transactions flagged as suspicious" }
                },
                y: { grid: { display: false } }
            }
        }
    });
}

/** Financial exposure comparison bar */
function renderFinancialBarChart(canvasId, financial) {
    destroyChart(canvasId);
    if (!financial) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const fields = [
        { label: "Confirmed suspicious",  key: "observed_suspicious_value_inr", color: "#b84942" },
        { label: "Total exposure at risk", key: "potential_exposure_inr",         color: "#b36b16" },
        { label: "Est. operational cost",  key: "false_positive_cost_inr",        color: "#9ca6ad" },
        { label: "Est. net saving",        key: "estimated_net_benefit_inr",      color: "#2c7a59" },
    ].filter((f) => financial[f.key] != null && !isNaN(Number(financial[f.key])));
    if (!fields.length) return;
    _charts[canvasId] = new Chart(canvas, {
        type: "bar",
        data: {
            labels: fields.map((f) => f.label),
            datasets: [{
                label: "Amount (₹)",
                data: fields.map((f) => Number(financial[f.key])),
                backgroundColor: fields.map((f) => f.color),
                borderRadius: 6,
                borderSkipped: false,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (item) => ` ₹${Number(item.raw).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                    }
                }
            },
            scales: {
                x: { grid: { display: false } },
                y: {
                    beginAtZero: true,
                    grid: { color: "#f0f2f1" },
                    ticks: {
                        callback: (v) => "₹" + (v >= 1e6 ? (v / 1e6).toFixed(1) + "M"
                            : v >= 1e3 ? (v / 1e3).toFixed(0) + "K" : v)
                    }
                }
            }
        }
    });
}

/** Detection signal breakdown — radar with business labels */
function renderSubScoreRadar(canvasId, results) {
    destroyChart(canvasId);
    if (!results || !results.length) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const scored = results.filter((r) => r.sub_scores);
    if (!scored.length) return;
    const avg = (key) => scored.reduce((s, r) => s + (Number(r.sub_scores?.[key]) || 0), 0) / scored.length;
    _charts[canvasId] = new Chart(canvas, {
        type: "radar",
        data: {
            labels: ["Fraud probability", "Unusual behaviour", "Activity surge", "Linked accounts"],
            datasets: [{
                label: "Avg signal strength",
                data: [
                    +(avg("ml_probability")       * 100).toFixed(1),
                    +(avg("anomaly_score")         * 100).toFixed(1),
                    +(avg("temporal_spike_risk")   * 100).toFixed(1),
                    +(avg("cluster_risk")          * 100).toFixed(1),
                ],
                backgroundColor: "rgba(11,129,118,0.15)",
                borderColor: "#0b8176",
                pointBackgroundColor: "#0b8176",
                pointRadius: 4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: (i) => ` ${i.raw.toFixed(1)}%` } }
            },
            scales: {
                r: {
                    beginAtZero: true,
                    max: 100,
                    ticks: { callback: (v) => v + "%", stepSize: 25 },
                    grid: { color: "#e5e9e9" }
                }
            }
        }
    });
}

/** Accuracy chart — precision, recall, F1 */
function renderPrBarChart(canvasId, perf) {
    destroyChart(canvasId);
    if (!perf || perf.precision === undefined) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    _charts[canvasId] = new Chart(canvas, {
        type: "bar",
        data: {
            labels: ["Precision\n(Correct flags)", "Recall\n(Fraud caught)", "F1-Score\n(Overall balance)"],
            datasets: [{
                label: "Score",
                data: [
                    +((perf.precision || 0) * 100).toFixed(2),
                    +((perf.recall    || 0) * 100).toFixed(2),
                    +((perf.f1        || 0) * 100).toFixed(2),
                ],
                backgroundColor: ["#0b8176", "#2c7a59", "#b36b16"],
                borderRadius: 6,
                borderSkipped: false,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: (i) => ` ${i.raw.toFixed(1)}%` } }
            },
            scales: {
                x: { grid: { display: false } },
                y: {
                    beginAtZero: true,
                    max: 100,
                    grid: { color: "#f0f2f1" },
                    ticks: { callback: (v) => v + "%" }
                }
            }
        }
    });
}

// ─── Overview render ────────────────────────────────────────────────────────

function renderAnalysisOverview(data) {
    const summary = data.summary || {};
    document.getElementById("overview-metrics").innerHTML = `
        <div class="metric"><div class="metric-label">Transactions checked</div><div class="metric-value">${num(summary.transactions)}</div><div class="metric-note">${num(data.validation_summary?.valid_rows)} valid rows processed</div></div>
        <div class="metric"><div class="metric-label">Merchants flagged</div><div class="metric-value">${num(data.spikes_count)}</div><div class="metric-note">Unusual activity detected</div></div>
        <div class="metric"><div class="metric-label">Accounts rated</div><div class="metric-value">${num(data.accounts_count)}</div><div class="metric-note">${data.accounts_count ? "Clear / Review / Block assigned" : "No account ID column provided"}</div></div>
        <div class="metric"><div class="metric-label">Estimated exposure</div><div class="metric-value">${money(data.financial_metrics?.financial?.potential_exposure_inr)}</div><div class="metric-note">${data.financial_metrics ? "Value in flagged transactions" : "Needs fraud-label column"}</div></div>`;
    renderOverviewFindings(data.spikes || []);
    document.getElementById("overview-action").innerHTML = data.spikes?.length
        ? `<span class="decision decision-review">ACTION NEEDED</span><h3>Start with the top flagged merchant.</h3><p>${esc(data.spikes[0].description || "A suspicious activity spike needs your review.")}</p><button class="text-button" data-view-link="findings">Open alerts →</button>`
        : `<span class="decision decision-allow">ALL CLEAR</span><h3>No unusual merchant activity detected.</h3><p>Your transactions look normal against the baseline. Upload a newer file to keep monitoring.</p><button class="text-button" data-view-link="analyze">Upload another file →</button>`;
    document.querySelectorAll("[data-view-link]").forEach((el) => el.addEventListener("click", () => showView(el.dataset.viewLink)));

    // Visuals
    const hasAccounts = (data.results || []).length > 0;
    const hasSpikes   = (data.spikes   || []).length > 0;
    const visEl = document.getElementById("overview-visuals");
    if (visEl) {
        if (hasAccounts || hasSpikes) {
            visEl.innerHTML = `
                <div class="two-column" style="margin-top:14px;">
                    <div class="card">
                        <div class="card-header">
                            <div><p class="eyebrow">Account risk levels</p><h3>How accounts are distributed by risk</h3></div>
                            <span class="tag" style="font-family:var(--mono);font-size:11px;">${data.results?.length || 0} accounts</span>
                        </div>
                        <div class="card-body">
                            ${hasAccounts
                                ? `<div style="height:200px;">${makeCanvas("ov-hist")}</div>
                                   <p class="finding-description" style="margin-top:10px;font-size:11px;">
                                     Green = cleared &nbsp;·&nbsp; Amber = needs review &nbsp;·&nbsp; Red = blocked
                                   </p>`
                                : empty("Not available", "No account ID column was found in this file.")}
                        </div>
                    </div>
                    <div class="card">
                        <div class="card-header">
                            <div><p class="eyebrow">${hasSpikes ? "Flagged merchants" : "Account decisions"}</p>
                            <h3>${hasSpikes ? "Suspicious rate vs. normal baseline" : "Clear · Review · Block breakdown"}</h3></div>
                        </div>
                        <div class="card-body">
                            ${hasSpikes
                                ? `<div style="height:200px;">${makeCanvas("ov-spikes")}</div>`
                                : hasAccounts
                                    ? `<div style="height:200px;">${makeCanvas("ov-donut")}</div>`
                                    : empty("Not available", "No merchant or account data in this file.")}
                        </div>
                    </div>
                </div>`;
            requestAnimationFrame(() => {
                if (hasAccounts) renderRiskHistogramChart("ov-hist", data.results);
                if (hasSpikes)   renderSpikesBarChart("ov-spikes", data.spikes);
                else if (hasAccounts) renderDecisionDonutChart("ov-donut", data.results);
            });
        } else {
            visEl.innerHTML = "";
        }
    }

    // Detail panels
    const topFinding = (data.spikes || [])[0];
    const financial  = data.financial_metrics?.financial;
    const evidence   = [];
    if (topFinding?.recent_rate !== undefined && topFinding?.baseline_rate !== undefined)
        evidence.push(`Suspicious transaction rate moved from ${pct(topFinding.baseline_rate)} to ${pct(topFinding.recent_rate)}.`);
    if (topFinding?.fold_increase !== undefined)
        evidence.push(`Current rate is ${esc(topFinding.fold_increase)}× above the historical baseline.`);
    if (data.clusters?.length)
        evidence.push("Multiple accounts share overlapping payment signals — possible coordinated activity.");
    document.getElementById("analysis-detail-panels").innerHTML = `
        <div class="analysis-details">
            <div class="card">
                <div class="card-header"><div><p class="eyebrow">Why we flagged it</p><h3>Supporting evidence</h3></div></div>
                <div class="card-body">
                    ${evidence.length
                        ? `<ul class="business-list">${evidence.map((e) => `<li>${e}</li>`).join("")}</ul>`
                        : empty("No flags raised", "No statistically significant activity change was found in this file.")}
                </div>
            </div>
            <div class="card">
                <div class="card-header"><div><p class="eyebrow">Money at risk</p><h3>Estimated exposure</h3></div></div>
                <div class="card-body">
                    <div class="metric-value">${money(financial?.potential_exposure_inr)}</div>
                    <p class="finding-description">${financial
                        ? "Total value of transactions tied to flagged accounts and merchants."
                        : "Not available — a fraud-label column is needed to calculate exposure."}</p>
                </div>
            </div>
            <div class="card wide">
                <div class="card-header">
                    <div><p class="eyebrow">How we decide</p><h3>What's behind the recommendations</h3></div>
                    <button class="text-button" data-view-link="model">See full report →</button>
                </div>
                <div class="card-body">
                    <p class="finding-description">
                        ${data.schema_report?.model_scoring_available
                            ? "Risk ratings were calculated using transaction behaviour patterns — amount, frequency, timing, and merchant relationships. Each account is rated Clear, Review, or Block."
                            : "Account-level risk ratings were unavailable because the required columns (account ID, amount, timestamp) were not all present in the uploaded file."}
                    </p>
                </div>
            </div>
        </div>`;
    document.querySelectorAll("[data-view-link]").forEach((el) => el.addEventListener("click", () => showView(el.dataset.viewLink)));
}

// ─── Findings / Alerts ──────────────────────────────────────────────────────

async function renderFindings() {
    let data = state.analysis;
    if (!data) {
        try {
            const [eventsRes, clustersRes] = await Promise.all([
                getJson("/api/risk/events"),
                getJson("/api/risk/clusters"),
            ]);
            const spikes = eventsRes.events || [], clusters = clustersRes.clusters || [];
            if (spikes.length || clusters.length) data = { spikes, clusters, accounts_count: 0, results: [] };
        } catch {}
    }
    const chartsEl = document.getElementById("findings-charts");
    if (!data || (!data.spikes?.length && !data.clusters?.length && !data.results?.length)) {
        document.getElementById("findings-summary").innerHTML = "";
        if (chartsEl) chartsEl.innerHTML = "";
        document.getElementById("findings-list").innerHTML =
            empty("No alerts yet", "Upload a transaction file to scan for suspicious merchants and accounts.");
        return;
    }

    const spikes   = data.spikes || [];
    const clusters = data.clusters || [];
    const accounts = (data.results || []).filter((r) => r.decision !== "ALLOW").slice(0, 10);
    const findings = [
        ...spikes,
        ...clusters.map((c) => ({
            ...c,
            description: `${c.member_count} accounts are sharing payment behaviour — possible coordinated activity.`,
            severity: c.risk_score >= 0.7 ? "HIGH" : "MEDIUM",
            recommendedAction: "REVIEW",
            merchant_id: c.cluster_id,
            clusterFinding: true,
        })),
        ...accounts.map((a) => ({
            ...a,
            description: a.reasoning || "This account is outside the expected payment pattern.",
            severity: a.decision,
            recommendedAction: a.decision,
            merchant_id: a.account_id,
            accountFinding: true,
        })),
    ];

    document.getElementById("findings-summary").innerHTML = `
        <span class="summary-pill"><strong>${num(spikes.length)}</strong> merchants flagged</span>
        <span class="summary-pill"><strong>${num(clusters.length)}</strong> linked groups</span>
        <span class="summary-pill"><strong>${num(data.accounts_count)}</strong> accounts rated</span>`;

    // Charts
    const hasAccounts = (data.results || []).length > 0;
    if (chartsEl && (hasAccounts || spikes.length)) {
        chartsEl.innerHTML = `
            <div class="card">
                <div class="card-header"><div><p class="eyebrow">Account decisions</p><h3>Clear · Review · Block</h3></div></div>
                <div class="card-body">
                    ${hasAccounts
                        ? `<div style="height:210px;">${makeCanvas("fi-donut")}</div>`
                        : empty("Not available", "No account ID column was found in this file.")}
                </div>
            </div>
            <div class="card">
                <div class="card-header"><div><p class="eyebrow">${spikes.length ? "Flagged merchants" : "Risk level spread"}</p>
                    <h3>${spikes.length ? "Suspicious vs. normal transaction rate" : "How accounts are spread by risk"}</h3></div>
                </div>
                <div class="card-body">
                    ${spikes.length
                        ? `<div style="height:210px;">${makeCanvas("fi-spikes")}</div>`
                        : hasAccounts
                            ? `<div style="height:210px;">${makeCanvas("fi-hist")}</div>`
                            : empty("Not available", "Upload data with account or merchant columns.")}
                </div>
            </div>`;
        requestAnimationFrame(() => {
            if (hasAccounts) renderDecisionDonutChart("fi-donut", data.results);
            if (spikes.length) renderSpikesBarChart("fi-spikes", spikes);
            else if (hasAccounts) renderRiskHistogramChart("fi-hist", data.results);
        });
    } else if (chartsEl) {
        chartsEl.innerHTML = "";
    }

    document.getElementById("findings-list").innerHTML = findings.length
        ? findings.map((item) => {
            const entityId   = item.merchant_id || item.cluster_id || item.account_id;
            const entityType = item.accountFinding ? "account" : item.clusterFinding ? "cluster" : "merchant";
            const exposure   = item.accountFinding
                ? money(item.total_amount_inr)
                : item.recent_amount_inr
                    ? `${money(item.recent_amount_inr)} across ${num(item.recent_txns)} transactions`
                    : item.recent_txns
                        ? `${num(item.recent_txns)} transactions reviewed`
                        : "Amount data not available.";
            const ratingClass = ["block","critical"].includes(String(item.severity || "").toLowerCase()) ? "block" : "review";

            // Sub-score tags in business language
            const subTags = item.sub_scores ? `
                <details class="technical-details">
                    <summary>See detection signals</summary>
                    <div class="finding-evidence">
                        <span class="tag">Fraud probability: ${pct(item.sub_scores.ml_probability)}</span>
                        <span class="tag">Unusual behaviour: ${pct(item.sub_scores.anomaly_score)}</span>
                        <span class="tag">Activity surge: ${pct(item.sub_scores.temporal_spike_risk)}</span>
                        <span class="tag">Linked accounts: ${pct(item.sub_scores.cluster_risk)}</span>
                    </div>
                </details>` : "";

            const evidenceTags = item.fold_increase
                ? `<span class="tag">${esc(item.fold_increase)}× above normal</span><span class="tag">Current rate: ${pct(item.recent_rate)}</span>`
                : item.accountFinding
                    ? `<span class="tag">Risk rating: ${pct(item.risk_score)}</span><span class="tag">${num(item.transaction_count)} transactions</span>`
                    : `<span class="tag">${num(item.member_count)} linked accounts</span><span class="tag">Group risk: ${pct(item.risk_score)}</span>`;

            return `
                <div class="finding-row">
                    <div>
                        <div class="finding-title">${esc(entityId || "Flagged entity")}</div>
                        <div class="finding-description"><strong>What happened:</strong> ${esc(item.description || "Behaviour moved outside the expected baseline.")}</div>
                        <div class="finding-description"><strong>Why it matters:</strong> This may represent financial exposure that warrants a closer look.</div>
                        <div class="finding-description"><strong>Money at risk:</strong> ${exposure}</div>
                        <div class="finding-description"><strong>Recommended action:</strong> ${esc(item.recommendedAction || "REVIEW")}</div>
                        <div class="finding-evidence">
                            ${evidenceTags}
                            <button class="text-button investigate-btn" data-id="${esc(entityId)}" data-type="${entityType}">Open case →</button>
                        </div>
                        ${subTags}
                    </div>
                    <span class="decision decision-${ratingClass}">${esc(item.severity || "REVIEW")}</span>
                </div>`;
        }).join("")
        : empty("No alerts to show", "No statistically significant changes were detected in this file.");

    document.querySelectorAll(".investigate-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.getElementById("investigate-entity-id").value = btn.dataset.id;
            document.getElementById("investigate-entity-type").value = btn.dataset.type;
            showView("investigations");
            runInvestigation();
        });
    });
}

// ─── Financial Exposure ─────────────────────────────────────────────────────

async function renderImpact() {
    const target    = document.getElementById("impact-content");
    const chartWrap = document.getElementById("impact-chart-wrap");
    const financial = state.analysis?.financial_metrics?.financial;

    if (!financial || (financial.observed_suspicious_value_inr === undefined && financial.potential_exposure_inr === undefined)) {
        target.innerHTML = `<div class="card">${empty(
            "Exposure data not available",
            state.analysis
                ? "To calculate financial exposure, your CSV needs a transaction amount column and a fraud-label column marking which transactions are fraudulent. Sentinel never invents exposure figures."
                : "No transaction data uploaded yet. Upload a CSV file with amounts and fraud labels to see financial exposure."
        )}</div>`;
        if (chartWrap) chartWrap.innerHTML = "";
        return;
    }

    target.innerHTML = `
        <div class="impact-grid">
            <div class="metric">
                <div class="metric-label">Confirmed suspicious value</div>
                <div class="metric-value">${money(financial.observed_suspicious_value_inr)}</div>
                <div class="metric-note">From fraud-labelled transactions in your file</div>
            </div>
            <div class="metric">
                <div class="metric-label">Total exposure at risk</div>
                <div class="metric-value">${money(financial.potential_exposure_inr)}</div>
                <div class="metric-note">Value tied to flagged accounts and merchants</div>
            </div>
            <div class="metric">
                <div class="metric-label">Est. review cost</div>
                <div class="metric-value">${money(financial.false_positive_cost_inr)}</div>
                <div class="metric-note">Illustrative cost of acting on every alert</div>
            </div>
            <div class="metric">
                <div class="metric-label">Est. net saving</div>
                <div class="metric-value">${money(financial.estimated_net_benefit_inr)}</div>
                <div class="metric-note">Exposure prevented minus review cost</div>
            </div>
        </div>
        <details class="impact-note">
            <summary>How these numbers are calculated</summary>
            <p><strong>Confirmed suspicious value</strong> is the sum of amounts on transactions your file marks as fraudulent. <strong>Total exposure</strong> is the value on all transactions assigned a Review or Block decision. <strong>Review cost</strong> and <strong>Net saving</strong> are illustrative — they depend on your team's actual operational costs and are shown as a planning guide only.</p>
        </details>`;

    if (chartWrap) {
        chartWrap.innerHTML = `<div class="card">
            <div class="card-header"><div><p class="eyebrow">Side-by-side view</p><h3>Exposure breakdown at a glance</h3></div></div>
            <div class="card-body"><div style="height:240px;">${makeCanvas("imp-bar")}</div></div>
        </div>`;
        requestAnimationFrame(() => renderFinancialBarChart("imp-bar", financial));
    }
}

// ─── Detection Report (was "Model & Evaluation") ────────────────────────────

async function loadModel() {
    const target   = document.getElementById("model-content");
    const chartsEl = document.getElementById("model-charts");
    const perf     = state.analysis?.model_performance;

    let perfSection = "";
    if (perf && perf.precision !== undefined) {
        perfSection = `
        <div class="card methodology-card" style="margin-bottom:15px;">
            <div class="card-header"><div><p class="eyebrow">Accuracy on your data</p><h3>How accurate were the flags on this file?</h3></div></div>
            <div class="technical-details methodology-body">
                <div class="technical-grid">
                    <div class="metric">
                        <div class="metric-label">Precision</div>
                        <div class="metric-value">${pct(perf.precision)}</div>
                        <div class="metric-note">Of all flags raised, this % were real fraud</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Recall</div>
                        <div class="metric-value">${pct(perf.recall)}</div>
                        <div class="metric-note">Of all known fraud, this % was caught</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">F1-Score</div>
                        <div class="metric-value">${num(perf.f1, 3)}</div>
                        <div class="metric-note">Balance between precision and recall (0–1)</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Fraud column used</div>
                        <div class="metric-value mono">${esc(perf.ground_truth_column || "fraud_label")}</div>
                        <div class="metric-note">Found in your uploaded file</div>
                    </div>
                </div>
            </div>
        </div>`;
    }

    const noDataNotice = (!perf || perf.precision === undefined)
        ? `<div class="card" style="margin-bottom:15px;">${empty(
            "Accuracy data not available",
            "To see how accurate the flags are on your data, include a fraud-label column (e.g. 'is_fraud' or 'fraud_label') with 1 for fraud and 0 for clean. Sentinel will calculate precision and recall automatically. No fake numbers are shown."
        )}</div>` : "";

    const howItWorks = `
        <div class="card methodology-card">
            <div class="card-header"><div><p class="eyebrow">How it works</p><h3>How Sentinel decides to flag an account or merchant</h3></div></div>
            <div class="technical-details methodology-body">
                <p class="finding-description">
                    Sentinel combines four signals to produce a single risk rating for each account and merchant:
                </p>
                <div style="margin-top:14px;display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                    <div style="background:#f7f9f8;border-radius:10px;padding:14px;">
                        <strong style="font-size:13px;">Fraud probability</strong>
                        <p class="finding-description" style="margin-top:4px;">How likely is this account to be committing fraud, based on its transaction pattern?</p>
                    </div>
                    <div style="background:#f7f9f8;border-radius:10px;padding:14px;">
                        <strong style="font-size:13px;">Unusual behaviour</strong>
                        <p class="finding-description" style="margin-top:4px;">How different is this account's activity compared to others at the same volume level?</p>
                    </div>
                    <div style="background:#f7f9f8;border-radius:10px;padding:14px;">
                        <strong style="font-size:13px;">Activity surge</strong>
                        <p class="finding-description" style="margin-top:4px;">Has this merchant's suspicious rate jumped sharply compared to its own historical baseline?</p>
                    </div>
                    <div style="background:#f7f9f8;border-radius:10px;padding:14px;">
                        <strong style="font-size:13px;">Linked accounts</strong>
                        <p class="finding-description" style="margin-top:4px;">Is this account connected to other flagged accounts through shared devices, merchants, or patterns?</p>
                    </div>
                </div>
                <p class="finding-description" style="margin-top:14px;">
                    The combined score maps to one of three decisions: <strong style="color:#2c7a59;">Clear</strong> (no action needed), <strong style="color:#b36b16;">Review</strong> (investigate before processing), or <strong style="color:#b84942;">Block</strong> (halt immediately).
                </p>
                <div class="finding-evidence" style="margin-top:10px;">
                    <span class="tag">Detection v1.2.0</span>
                    <span class="tag">Fraud policy v1.0</span>
                    <span class="tag">Probability calibrated</span>
                </div>
            </div>
        </div>`;

    target.innerHTML = perfSection + noDataNotice + howItWorks;

    // Charts
    if (chartsEl) {
        const results     = state.analysis?.results || [];
        const hasPerf     = perf && perf.precision !== undefined;
        const hasSubScores = results.some((r) => r.sub_scores);
        if (hasPerf || hasSubScores) {
            chartsEl.innerHTML = `<div class="two-column">
                ${hasPerf ? `<div class="card">
                    <div class="card-header"><div><p class="eyebrow">Detection accuracy</p><h3>Precision · Recall · F1 on your data</h3></div></div>
                    <div class="card-body"><div style="height:220px;">${makeCanvas("md-pr")}</div>
                    <p class="finding-description" style="margin-top:8px;font-size:11px;">Higher is better. Precision = fewer false alarms. Recall = more fraud caught.</p>
                    </div></div>` : ""}
                ${hasSubScores ? `<div class="card">
                    <div class="card-header"><div><p class="eyebrow">Signal strength</p><h3>Average detection signal across all accounts</h3></div></div>
                    <div class="card-body"><div style="height:220px;">${makeCanvas("md-radar")}</div></div>
                </div>` : ""}
            </div>`;
            requestAnimationFrame(() => {
                if (hasPerf)      renderPrBarChart("md-pr", perf);
                if (hasSubScores) renderSubScoreRadar("md-radar", results);
            });
        } else {
            chartsEl.innerHTML = "";
        }
    }
}

// ─── Case Review (Investigations) ──────────────────────────────────────────

async function runInvestigation() {
    const id     = document.getElementById("investigate-entity-id").value.trim();
    const output = document.getElementById("investigation-output");
    if (!id) {
        output.innerHTML = empty("Enter an ID to look up", "Type an account or merchant ID from your analysis above.");
        return;
    }
    output.innerHTML = `<div class="memo">Compiling case summary for "${esc(id)}"…</div>`;
    try {
        const response = await fetch("/api/investigations", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...sessionHeaders() },
            body: JSON.stringify({
                entity_id: id,
                entity_type: document.getElementById("investigate-entity-type").value,
            })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not generate case summary");
        output.innerHTML = `<div class="memo">${esc(data.investigation_memo || "No case summary available.")}</div>`;
    } catch (error) {
        output.innerHTML = `<div class="status-card error">${esc(error.message)}</div>`;
    }
}

// ─── Activity Log (Audit) ──────────────────────────────────────────────────

async function loadAudit() {
    const target = document.getElementById("audit-content");
    try {
        const data = await getJson("/api/audit");
        const logs = data.audit_trail || [];
        target.innerHTML = logs.length
            ? `<table class="data-table">
                <thead><tr><th>Time</th><th>Action</th><th>Type</th><th>ID / Reference</th></tr></thead>
                <tbody>${logs.map((log) => `
                    <tr>
                        <td class="mono">${esc(log.timestamp)}</td>
                        <td>${esc(log.action)}</td>
                        <td>${esc(log.entity_type)}</td>
                        <td class="mono">${esc(log.entity_id)}</td>
                    </tr>`).join("")}
                </tbody>
               </table>`
            : empty("No activity recorded yet", "Uploads, analyses, and case reviews will appear here in order.");
    } catch (error) {
        target.innerHTML = empty("Activity log unavailable", error.message);
    }
}
