const state = { analysis: null, evaluation: null, file: null };
// Chart instance registry — destroy before re-render to prevent Chart.js reuse errors
const _charts = {};

function destroyChart(id) {
    if (_charts[id]) { try { _charts[id].destroy(); } catch (_) {} delete _charts[id]; }
}

// Session isolation: generate a unique ID per browser tab/session
const SESSION_ID = (() => {
    let sid = sessionStorage.getItem("sentinel_session_id");
    if (!sid) {
        sid = "sess_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);
        sessionStorage.setItem("sentinel_session_id", sid);
    }
    return sid;
})();

const pageMeta = {
    overview: ["Overview", "See what changed in your payment activity and decide what to do next."],
    analyze: ["Analyze Data", "Start with the transaction data you already have."],
    findings: ["Risk Findings", "Patterns worth a closer look, ordered by potential business impact."],
    impact: ["Financial Impact", "Understand how much value could be exposed."],
    investigations: ["Investigations", "Turn a finding into an evidence-grounded review note."],
    model: ["Model & Evaluation", "Technical evidence behind the decisions."],
    audit: ["Audit", "Operational actions and decisions recorded by the system."],
};

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

const num = (value, digits = 0) => (value === null || value === undefined || isNaN(Number(value)))
    ? "Unavailable"
    : Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });

const pct = (value, digits = 1) => (value === null || value === undefined || isNaN(Number(value)))
    ? "Unavailable"
    : `${(Number(value) * 100).toFixed(digits)}%`;

const money = (value) => (value === null || value === undefined || isNaN(Number(value)))
    ? "Unavailable"
    : `₹${Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

const empty = (title, message) => `<div class="empty"><strong>${esc(title)}</strong>${esc(message)}</div>`;

// Chart.js global defaults
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
    document.querySelectorAll("[data-view]").forEach((el) => el.addEventListener("click", (event) => {
        event.preventDefault(); showView(el.dataset.view);
    }));
    document.querySelectorAll("[data-view-link]").forEach((el) => el.addEventListener("click", () => showView(el.dataset.viewLink)));
    document.querySelectorAll("[data-action]").forEach((el) => el.addEventListener("click", () => {
        if (el.dataset.action === "open-upload") showView("analyze");
        if (el.dataset.action === "how-it-works") document.getElementById("how-it-works").scrollIntoView({ behavior: "smooth" });
        if (el.dataset.action === "choose-file") document.getElementById("csv-file-input").click();
    }));
    document.getElementById("refresh-button").addEventListener("click", refreshActive);
    document.getElementById("csv-file-input").addEventListener("change", onFileSelected);
    document.getElementById("run-analysis").addEventListener("click", runAnalysis);
    document.getElementById("investigate-button").addEventListener("click", runInvestigation);
    updateSystemStatus(); loadOverview();
});

function showView(view) {
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
    document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
    document.getElementById("page-title").textContent = pageMeta[view][0];
    document.getElementById("page-subtitle").textContent = pageMeta[view][1];
    if (view === "overview") state.analysis ? renderAnalysisOverview(state.analysis) : loadOverview();
    if (view === "findings") renderFindings();
    if (view === "impact") renderImpact();
    if (view === "model") loadModel();
    if (view === "audit") loadAudit();
}

function refreshActive() {
    const active = document.querySelector(".nav-item.active");
    if (active) showView(active.dataset.view);
}

const sessionHeaders = () => ({ "X-Session-ID": SESSION_ID });

async function getJson(url) {
    const response = await fetch(url, { headers: sessionHeaders() });
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    return response.json();
}

async function updateSystemStatus() {
    try {
        const data = await getJson("/health");
        document.getElementById("system-status-text").textContent = data.status === "healthy" ? "System operational" : "System degraded";
    } catch {
        document.getElementById("system-status-text").textContent = "API unavailable";
    }
}

async function loadOverview() {
    const metrics = document.getElementById("overview-metrics");
    try {
        const data = await getJson("/api/risk/overview");
        if (!data.session_has_analysis && !state.analysis) {
            metrics.innerHTML = `
                <div class="metric"><div class="metric-label">Transactions reviewed</div><div class="metric-value">—</div><div class="metric-note">No dataset uploaded</div></div>
                <div class="metric"><div class="metric-label">Risk findings</div><div class="metric-value">—</div><div class="metric-note">No dataset uploaded</div></div>
                <div class="metric"><div class="metric-label">Connected activity</div><div class="metric-value">—</div><div class="metric-note">No dataset uploaded</div></div>
                <div class="metric"><div class="metric-label">Potential exposure</div><div class="metric-value">—</div><div class="metric-note">No dataset uploaded</div></div>`;
            renderOverviewFindings([]);
            document.getElementById("overview-action").innerHTML = `
                <span class="decision decision-allow">READY</span>
                <h3>Upload payment data to begin.</h3>
                <p>No transaction data has been analyzed yet. Upload a CSV file to inspect payment risk, detect unusual patterns, and review findings.</p>
                <button class="button button-primary" style="margin-top:12px;" data-action="open-upload">Analyze Payment Data →</button>`;
            document.getElementById("analysis-detail-panels").innerHTML = "";
            const visEl = document.getElementById("overview-visuals");
            if (visEl) visEl.innerHTML = "";
            document.querySelectorAll("[data-action]").forEach((el) => el.addEventListener("click", () => {
                if (el.dataset.action === "open-upload") showView("analyze");
            }));
            return;
        }
        const m = data.metrics || {};
        metrics.innerHTML = `
            <div class="metric"><div class="metric-label">Transactions reviewed</div><div class="metric-value">${num(m.transactions_processed)}</div><div class="metric-note">Across the latest analysis</div></div>
            <div class="metric"><div class="metric-label">Risk findings</div><div class="metric-value">${num(m.active_spikes_detected)}</div><div class="metric-note">Emerging activity patterns</div></div>
            <div class="metric"><div class="metric-label">Connected activity</div><div class="metric-value">${num(m.active_clusters_detected)}</div><div class="metric-note">Related groups detected</div></div>
            <div class="metric"><div class="metric-label">Potential exposure</div><div class="metric-value">${money(m.potential_exposure_inr)}</div><div class="metric-note">${m.potential_exposure_inr !== null && m.potential_exposure_inr !== undefined ? "Risk-identified activity" : "Requires labeled evaluation"}</div></div>`;
        renderOverviewFindings(data.recent_spikes || []);
        document.getElementById("overview-action").innerHTML = data.recent_spikes?.length
            ? `<span class="decision decision-review">REVIEW</span><h3>Start with the highest-impact finding.</h3><p>Open Risk Findings to see the change, supporting evidence, and the recommended next action.</p><button class="text-button" data-view-link="findings">Review findings →</button>`
            : `<span class="decision decision-allow">NO SPIKE</span><h3>No emerging merchant spike detected.</h3><p>Review the available fields below and keep monitoring the next data window.</p><button class="text-button" data-action="open-upload">Analyze another file →</button>`;
        document.querySelectorAll("[data-view-link]").forEach((el) => el.addEventListener("click", () => showView(el.dataset.viewLink)));
        document.querySelectorAll("[data-action]").forEach((el) => el.addEventListener("click", () => { if (el.dataset.action === "open-upload") showView("analyze"); }));
    } catch (error) { metrics.innerHTML = empty("Overview unavailable", error.message); }
}

function renderOverviewFindings(spikes) {
    const target = document.getElementById("overview-findings");
    if (!spikes.length) { target.innerHTML = empty("No active findings", "Upload a CSV to look for emerging suspicious activity."); return; }
    target.innerHTML = spikes.slice(0, 4).map((spike) => `<div class="finding-row"><div><div class="finding-title">${esc(spike.merchant_id || "Merchant activity")}</div><div class="finding-description">${esc(spike.description || "A change in suspicious payment activity was detected.")}</div></div><span class="decision decision-${String(spike.severity || "review").toLowerCase() === "critical" ? "block" : "review"}">${esc(spike.severity || "REVIEW")}</span></div>`).join("");
}

function onFileSelected(event) {
    state.file = event.target.files[0];
    document.getElementById("selected-file").textContent = state.file ? `${state.file.name} · ${(state.file.size / 1024).toFixed(1)} KB` : "";
    document.getElementById("run-analysis").hidden = !state.file;
}

async function runAnalysis() {
    if (!state.file) return;
    const status = document.getElementById("upload-status");
    status.innerHTML = `<div class="status-card"><strong>Reading ${esc(state.file.name)}…</strong><p class="finding-description">Checking the fields available before running analysis.</p></div>`;
    const form = new FormData(); form.append("file", state.file);
    try {
        const response = await fetch("/api/upload", { method: "POST", headers: sessionHeaders(), body: form });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Upload failed");
        state.analysis = data;
        renderUploadResult(data);
        document.getElementById("upload-status").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        status.innerHTML = `<div class="status-card error">
            <strong>We couldn't analyze this file.</strong>
            <p style="margin: 8px 0 12px; line-height: 1.5;">${esc(error.message)}</p>
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 8px;">
                <a href="/api/csv-template" class="button button-secondary" style="text-decoration:none; padding: 6px 14px; font-size: 13px;" download="payment_transaction_template.csv">
                    Download CSV Template ↓
                </a>
            </div>
        </div>`;
    }
}

function renderUploadResult(data) {
    const schema = data.schema_report || {}, validation = data.validation_summary || {}, recognized = schema.recognized_columns || validation.recognized_columns || [];
    const known = ["timestamp", "amount", "account_id", "merchant_id", "device_id", "payment_method", "status", "fraud_label"];
    const available = known.filter((field) => recognized.includes(field));
    const missing = known.filter((field) => !recognized.includes(field));
    const analysis = [
        ["Payment behavior", true], ["Amount anomalies", available.includes("amount")], ["Time patterns", available.includes("timestamp")],
        ["Merchant behavior", available.includes("merchant_id")], ["Customer behavior", available.includes("account_id")],
    ];
    document.getElementById("upload-status").innerHTML = `<div class="status-card success">
        <h3>Analysis complete</h3><p class="finding-description">We understand your file and have completed the supported checks below.</p><div class="capability-columns">
        <div><h4>${num(validation.rows_received)} rows detected</h4><ul class="capability-list">${available.map((field) => `<li class="available">✓ ${esc(field.replace("_", " "))}</li>`).join("") || "<li>No recognized fields</li>"}</ul></div>
        <div><h4>What we can analyze</h4><ul class="capability-list">${analysis.map(([label, ok]) => `<li class="${ok ? "available" : "unavailable"}">${ok ? "✓" : "○"} ${label}</li>`).join("")}</ul></div>
        <div><h4>What is unavailable</h4><ul class="capability-list">${missing.map((field) => `<li class="unavailable">○ ${esc(field)} was not provided</li>`).join("") || "<li class=\"available\">✓ All supported fields present</li>"}</ul></div>
        </div><div class="status-meta">${num(validation.valid_rows)} valid rows · ${num(validation.invalid_rows)} excluded · ${schema.model_scoring_available ? "Model risk scoring available" : "Model risk scoring unavailable — required features cannot be calculated from this file"} · ${schema.ground_truth_available ? "Precision and recall available" : "Precision / recall unavailable — fraud labels were not provided"}${validation.warning ? ` · ${esc(validation.warning)}` : ""}</div><div class="status-flow"><span class="status-step">✓ Reading file</span><span class="status-step">✓ Understanding fields</span><span class="status-step">✓ Checking data</span><span class="status-step">✓ Analyzing activity</span><span class="status-step">✓ Building findings</span></div><button class="button button-primary result-button" id="view-analysis-result">View analysis result →</button></div>`;
    document.getElementById("view-analysis-result").addEventListener("click", () => {
        showView("overview");
        renderAnalysisOverview(data);
    });
}

// ─── Chart helpers ────────────────────────────────────────────────────────────

function makeCanvas(id, height = 220) {
    return `<canvas id="${id}" height="${height}" style="width:100%;"></canvas>`;
}

/** Risk score distribution histogram using Chart.js */
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
    const labels = ["0.0–0.1","0.1–0.2","0.2–0.3","0.3–0.4","0.4–0.5","0.5–0.6","0.6–0.7","0.7–0.8","0.8–0.9","0.9–1.0"];
    const bgColors = [
        "#2c7a59","#2c7a59","#2c7a59",
        "#b36b16","#b36b16","#b36b16",
        "#b84942","#b84942","#b84942","#b84942"
    ];
    _charts[canvasId] = new Chart(canvas, {
        type: "bar",
        data: { labels, datasets: [{ label: "Accounts", data: bins, backgroundColor: bgColors, borderRadius: 4, borderSkipped: false }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => `Risk score: ${items[0].label}`,
                        label: (item) => ` ${item.raw} account(s)`,
                    }
                },
                annotation: undefined,
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { family: "'DM Mono', monospace", size: 10 } } },
                y: { beginAtZero: true, grid: { color: "#f0f2f1" }, ticks: { precision: 0 } },
            }
        }
    });
}

/** Decision donut chart */
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
            labels: ["Allow", "Review", "Block"],
            datasets: [{
                data: [counts.ALLOW, counts.REVIEW, counts.BLOCK],
                backgroundColor: ["#2c7a59", "#b36b16", "#b84942"],
                borderWidth: 2, borderColor: "#fff", hoverOffset: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: "62%",
            plugins: {
                legend: { position: "bottom", labels: { color: "#6c7782", padding: 16, font: { size: 12 } } },
                tooltip: { callbacks: { label: (item) => ` ${item.label}: ${item.raw} (${((item.raw / results.length) * 100).toFixed(1)}%)` } }
            }
        }
    });
}

/** Merchant spike comparison horizontal bar chart */
function renderSpikesBarChart(canvasId, spikes) {
    destroyChart(canvasId);
    if (!spikes || !spikes.length) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const top = spikes.slice(0, 6);
    _charts[canvasId] = new Chart(canvas, {
        type: "bar",
        data: {
            labels: top.map((s) => String(s.merchant_id || "Unknown").slice(0, 18)),
            datasets: [
                { label: "Baseline rate", data: top.map((s) => +((Number(s.baseline_rate) || 0) * 100).toFixed(2)), backgroundColor: "#9ca6ad", borderRadius: 3 },
                { label: "Spike rate", data: top.map((s) => +((Number(s.recent_rate) || 0) * 100).toFixed(2)), backgroundColor: "#b84942", borderRadius: 3 },
            ]
        },
        options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: "bottom" },
                tooltip: { callbacks: { label: (item) => ` ${item.dataset.label}: ${item.raw}%` } }
            },
            scales: {
                x: { grid: { color: "#f0f2f1" }, ticks: { callback: (v) => v + "%" }, beginAtZero: true },
                y: { grid: { display: false }, ticks: { font: { family: "'DM Mono', monospace", size: 11 } } }
            }
        }
    });
}

/** Financial impact bar chart */
function renderFinancialBarChart(canvasId, financial) {
    destroyChart(canvasId);
    if (!financial) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const fields = [
        { label: "Suspicious value", key: "observed_suspicious_value_inr", color: "#b84942" },
        { label: "Potential exposure", key: "potential_exposure_inr", color: "#b36b16" },
        { label: "Intervention cost", key: "false_positive_cost_inr", color: "#9ca6ad" },
        { label: "Net benefit", key: "estimated_net_benefit_inr", color: "#2c7a59" },
    ].filter((f) => financial[f.key] !== null && financial[f.key] !== undefined && !isNaN(Number(financial[f.key])));
    if (!fields.length) return;
    _charts[canvasId] = new Chart(canvas, {
        type: "bar",
        data: {
            labels: fields.map((f) => f.label),
            datasets: [{
                label: "Amount (₹)",
                data: fields.map((f) => Number(financial[f.key])),
                backgroundColor: fields.map((f) => f.color),
                borderRadius: 6, borderSkipped: false
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
                y: { beginAtZero: true, grid: { color: "#f0f2f1" }, ticks: { callback: (v) => "₹" + (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "K" : v) } }
            }
        }
    });
}

/** Model sub-score radar chart */
function renderSubScoreRadar(canvasId, results) {
    destroyChart(canvasId);
    if (!results || !results.length) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    // Average sub-scores across all results that have them
    const scored = results.filter((r) => r.sub_scores);
    if (!scored.length) return;
    const avg = (key) => scored.reduce((s, r) => s + (Number(r.sub_scores?.[key]) || 0), 0) / scored.length;
    _charts[canvasId] = new Chart(canvas, {
        type: "radar",
        data: {
            labels: ["ML Risk", "Anomaly", "Spike Risk", "Cluster Risk"],
            datasets: [{
                label: "Avg sub-score",
                data: [
                    +(avg("ml_probability") * 100).toFixed(1),
                    +(avg("anomaly_score") * 100).toFixed(1),
                    +(avg("temporal_spike_risk") * 100).toFixed(1),
                    +(avg("cluster_risk") * 100).toFixed(1),
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
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: (i) => ` ${i.raw.toFixed(1)}%` } } },
            scales: { r: { beginAtZero: true, max: 100, ticks: { callback: (v) => v + "%", stepSize: 25 }, grid: { color: "#e5e9e9" } } }
        }
    });
}

/** Precision / recall bar chart */
function renderPrBarChart(canvasId, perf) {
    destroyChart(canvasId);
    if (!perf || perf.precision === undefined) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    _charts[canvasId] = new Chart(canvas, {
        type: "bar",
        data: {
            labels: ["Precision", "Recall", "F1-Score"],
            datasets: [{
                label: "Score",
                data: [
                    +((perf.precision || 0) * 100).toFixed(2),
                    +((perf.recall || 0) * 100).toFixed(2),
                    +((perf.f1 || 0) * 100).toFixed(2),
                ],
                backgroundColor: ["#0b8176", "#2c7a59", "#b36b16"],
                borderRadius: 6, borderSkipped: false,
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
                y: { beginAtZero: true, max: 100, grid: { color: "#f0f2f1" }, ticks: { callback: (v) => v + "%" } }
            }
        }
    });
}

// ─── Views ────────────────────────────────────────────────────────────────────

function renderAnalysisOverview(data) {
    const summary = data.summary || {};
    document.getElementById("overview-metrics").innerHTML = `
        <div class="metric"><div class="metric-label">Rows analyzed</div><div class="metric-value">${num(summary.transactions)}</div><div class="metric-note">${num(data.validation_summary?.valid_rows)} valid rows</div></div>
        <div class="metric"><div class="metric-label">Risk findings</div><div class="metric-value">${num(data.spikes_count)}</div><div class="metric-note">Emerging activity patterns</div></div>
        <div class="metric"><div class="metric-label">Accounts scored</div><div class="metric-value">${num(data.accounts_count)}</div><div class="metric-note">${data.accounts_count ? "Policy decisions available" : "Account field not provided"}</div></div>
        <div class="metric"><div class="metric-label">Potential exposure</div><div class="metric-value">${money(data.financial_metrics?.financial?.potential_exposure_inr)}</div><div class="metric-note">${data.financial_metrics ? "Risk-identified activity" : "Unavailable without provided labels"}</div></div>`;
    renderOverviewFindings(data.spikes || []);
    document.getElementById("overview-action").innerHTML = data.spikes?.length
        ? `<span class="decision decision-review">REVIEW</span><h3>Investigate the leading activity change.</h3><p>${esc(data.spikes[0].description || "A suspicious activity pattern needs review.")}</p><button class="text-button" data-view-link="findings">Open evidence →</button>`
        : `<span class="decision decision-allow">NO SPIKE</span><h3>No emerging merchant spike detected.</h3><p>Review the available fields below and keep monitoring the next data window.</p><button class="text-button" data-view-link="analyze">Analyze another file →</button>`;
    document.querySelectorAll("[data-view-link]").forEach((el) => el.addEventListener("click", () => showView(el.dataset.viewLink)));

    // Interactive charts
    const hasAccounts = (data.results || []).length > 0;
    const hasSpikes = (data.spikes || []).length > 0;
    const visualsEl = document.getElementById("overview-visuals");
    if (visualsEl) {
        if (hasAccounts || hasSpikes) {
            visualsEl.innerHTML = `
                <div class="two-column" style="margin-top:14px;">
                    <div class="card">
                        <div class="card-header">
                            <div><p class="eyebrow">Risk distribution</p><h3>Risk score histogram</h3></div>
                            <span class="tag" style="font-family:var(--mono);font-size:11px;">${data.results?.length || 0} accounts</span>
                        </div>
                        <div class="card-body" style="padding:20px 22px;">
                            ${hasAccounts
                                ? `<div style="height:200px;">${makeCanvas("ov-hist")}</div>`
                                : empty("Histogram unavailable", "Account identifiers were not present in this file.")}
                        </div>
                    </div>
                    <div class="card">
                        <div class="card-header">
                            <div><p class="eyebrow">${hasSpikes ? "Temporal risk" : "Policy decisions"}</p>
                            <h3>${hasSpikes ? "Merchant spike comparison" : "Decision breakdown"}</h3></div>
                        </div>
                        <div class="card-body" style="padding:20px 22px;">
                            ${hasSpikes
                                ? `<div style="height:200px;">${makeCanvas("ov-spikes")}</div>`
                                : hasAccounts
                                    ? `<div style="height:200px;">${makeCanvas("ov-donut")}</div>`
                                    : empty("Unavailable", "No merchant spikes detected in this dataset.")}
                        </div>
                    </div>
                </div>`;
            // Render charts after DOM update
            requestAnimationFrame(() => {
                if (hasAccounts) renderRiskHistogramChart("ov-hist", data.results);
                if (hasSpikes) renderSpikesBarChart("ov-spikes", data.spikes);
                else if (hasAccounts) renderDecisionDonutChart("ov-donut", data.results);
            });
        } else {
            visualsEl.innerHTML = "";
        }
    }

    const topFinding = (data.spikes || [])[0];
    const financial = data.financial_metrics?.financial;
    const evidence = [];
    if (topFinding?.recent_rate !== undefined && topFinding?.baseline_rate !== undefined) evidence.push(`Suspicious activity moved from ${pct(topFinding.baseline_rate)} to ${pct(topFinding.recent_rate)}.`);
    if (topFinding?.fold_increase !== undefined) evidence.push(`Recent activity is ${esc(topFinding.fold_increase)}× above the historical baseline.`);
    if (data.clusters?.length) evidence.push("Related activity shows overlapping entities.");
    document.getElementById("analysis-detail-panels").innerHTML = `
        <div class="analysis-details">
            <div class="card"><div class="card-header"><div><p class="eyebrow">Evidence</p><h3>Why we flagged this</h3></div></div><div class="card-body">${evidence.length ? `<ul class="business-list">${evidence.map((item) => `<li>${item}</li>`).join("")}</ul>` : empty("No risk finding evidence", "No supported activity change was identified in this analysis.")}</div></div>
            <div class="card"><div class="card-header"><div><p class="eyebrow">Financial impact</p><h3>Potential exposure</h3></div></div><div class="card-body"><div class="metric-value">${money(financial?.potential_exposure_inr)}</div><p class="finding-description">${financial ? "Value associated with activity selected by the account-level policy." : "Unavailable because reliable labels were not provided."}</p></div></div>
            <div class="card wide"><div class="card-header"><div><p class="eyebrow">Technical evidence</p><h3>How the system determined this</h3></div><button class="text-button" data-view-link="model">View evaluation →</button></div><div class="card-body"><p class="finding-description">${data.schema_report?.model_scoring_available ? "Model risk scoring was available because the required input fields and computed features were present." : "Model risk scoring was unavailable because the required features could not be calculated from this file. Supported statistical analysis is shown instead."}</p></div></div>
        </div>`;
    document.querySelectorAll("[data-view-link]").forEach((el) => el.addEventListener("click", () => showView(el.dataset.viewLink)));
}

async function renderFindings() {
    let data = state.analysis;
    if (!data) {
        try {
            const [eventsRes, clustersRes] = await Promise.all([
                getJson("/api/risk/events"),
                getJson("/api/risk/clusters"),
            ]);
            const spikes = eventsRes.events || [];
            const clusters = clustersRes.clusters || [];
            if (spikes.length || clusters.length) {
                data = { spikes, clusters, accounts_count: 0, results: [] };
            }
        } catch {}
    }
    const chartsEl = document.getElementById("findings-charts");
    if (!data || (!data.spikes?.length && !data.clusters?.length && !data.results?.length)) {
        document.getElementById("findings-summary").innerHTML = "";
        if (chartsEl) chartsEl.innerHTML = "";
        document.getElementById("findings-list").innerHTML = empty("No risk findings", "Upload payment data to identify emerging activity patterns.");
        return;
    }

    const spikes = data.spikes || [], clusters = data.clusters || [], accounts = (data.results || []).filter((item) => item.decision !== "ALLOW").slice(0, 10);
    const findings = spikes.concat(clusters.map((c) => ({ ...c, description: `${c.member_count} accounts share related payment signals.`, severity: c.risk_score >= .7 ? "HIGH" : "MEDIUM", recommendedAction: "REVIEW", merchant_id: c.cluster_id, clusterFinding: true }))).concat(accounts.map((account) => ({ ...account, description: account.reasoning || "This account is outside the expected payment behavior.", severity: account.decision, recommendedAction: account.decision, merchant_id: account.account_id, accountFinding: true })));
    document.getElementById("findings-summary").innerHTML = `<span class="summary-pill"><strong>${num(spikes.length)}</strong> activity changes</span><span class="summary-pill"><strong>${num(clusters.length)}</strong> related groups</span><span class="summary-pill"><strong>${num(data.accounts_count)}</strong> accounts scored</span>`;

    // Charts for findings view
    const hasAccounts = (data.results || []).length > 0;
    const hasSpikes = spikes.length > 0;
    if (chartsEl && (hasAccounts || hasSpikes)) {
        chartsEl.innerHTML = `
            <div class="card">
                <div class="card-header"><div><p class="eyebrow">Account risk</p><h3>Decision breakdown</h3></div></div>
                <div class="card-body" style="padding:20px 22px;">
                    ${hasAccounts
                        ? `<div style="height:210px;">${makeCanvas("fi-donut")}</div>`
                        : empty("No accounts scored", "Account IDs were not provided in this dataset.")}
                </div>
            </div>
            <div class="card">
                <div class="card-header"><div><p class="eyebrow">Risk signal</p><h3>${hasSpikes ? "Spike rates by merchant" : "Risk score distribution"}</h3></div></div>
                <div class="card-body" style="padding:20px 22px;">
                    ${hasSpikes
                        ? `<div style="height:210px;">${makeCanvas("fi-spikes")}</div>`
                        : hasAccounts
                            ? `<div style="height:210px;">${makeCanvas("fi-hist")}</div>`
                            : empty("No chart data", "Upload data with account or merchant fields.")}
                </div>
            </div>`;
        requestAnimationFrame(() => {
            if (hasAccounts) renderDecisionDonutChart("fi-donut", data.results);
            if (hasSpikes) renderSpikesBarChart("fi-spikes", spikes);
            else if (hasAccounts) renderRiskHistogramChart("fi-hist", data.results);
        });
    } else if (chartsEl) {
        chartsEl.innerHTML = "";
    }

    document.getElementById("findings-list").innerHTML = findings.length
        ? findings.map((item) => {
            const entityId = item.merchant_id || item.cluster_id || item.account_id;
            const entityType = item.accountFinding ? "account" : (item.clusterFinding ? "cluster" : "merchant");
            const exposureStr = item.accountFinding
                ? money(item.total_amount_inr)
                : item.recent_amount_inr
                    ? `${money(item.recent_amount_inr)} (${num(item.recent_txns)} txns)`
                    : item.recent_txns
                        ? `${num(item.recent_txns)} transactions evaluated`
                        : "Not available for this finding.";
            return `<div class="finding-row"><div><div class="finding-title">${esc(entityId || "Risk pattern")}</div><div class="finding-description"><strong>What changed:</strong> ${esc(item.description || "A pattern changed from the expected baseline.")}</div><div class="finding-description"><strong>Why it matters:</strong> This activity may indicate potential exposure and merits attention.</div><div class="finding-description"><strong>Potential exposure:</strong> ${exposureStr}</div><div class="finding-description"><strong>Recommended action:</strong> ${esc(item.recommendedAction || "REVIEW")}</div><div class="finding-evidence">${item.fold_increase ? `<span class="tag">${esc(item.fold_increase)}× baseline</span><span class="tag">${pct(item.recent_rate)} recent rate</span>` : item.accountFinding ? `<span class="tag">${pct(item.risk_score)} risk score</span><span class="tag">${num(item.transaction_count)} transactions</span>` : `<span class="tag">${num(item.member_count)} connected accounts</span><span class="tag">${pct(item.risk_score)} risk score</span>`}<button class="text-button investigate-btn" data-id="${esc(entityId)}" data-type="${entityType}">Investigate →</button></div>${item.sub_scores ? `<details class="technical-details"><summary>Show technical evidence</summary><div class="finding-evidence"><span class="tag">Model risk ${pct(item.sub_scores.ml_probability)}</span><span class="tag">Anomaly ${pct(item.sub_scores.anomaly_score)}</span><span class="tag">Spike ${pct(item.sub_scores.temporal_spike_risk)}</span><span class="tag">Cluster ${pct(item.sub_scores.cluster_risk)}</span></div></details>` : ""}</div><span class="decision decision-${String(item.severity || "review").toLowerCase() === "block" || String(item.severity || "").toLowerCase() === "critical" ? "block" : "review"}">${esc(item.severity || "REVIEW")}</span></div>`;
        }).join("")
        : empty("No risk findings", "No statistically significant changes were detected in this analysis.");

    document.querySelectorAll(".investigate-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.getElementById("investigate-entity-id").value = btn.dataset.id;
            document.getElementById("investigate-entity-type").value = btn.dataset.type;
            showView("investigations");
            runInvestigation();
        });
    });
}

async function renderImpact() {
    const target = document.getElementById("impact-content");
    const chartWrap = document.getElementById("impact-chart-wrap");
    const financial = state.analysis?.financial_metrics?.financial;
    if (!financial || (financial.observed_suspicious_value_inr === undefined && financial.potential_exposure_inr === undefined)) {
        target.innerHTML = `<div class="card">${empty(
            "Financial exposure is unavailable",
            state.analysis
                ? "Financial estimates require transaction amounts and verified fraud labels in the uploaded CSV. The product never invents financial exposure."
                : "No payment data has been uploaded yet. Upload transaction CSV data with amounts and fraud labels to calculate financial impact."
        )}</div>`;
        if (chartWrap) chartWrap.innerHTML = "";
        return;
    }
    target.innerHTML = `<div class="impact-grid">
        <div class="metric"><div class="metric-label">Suspicious transaction value</div><div class="metric-value">${money(financial.observed_suspicious_value_inr)}</div><div class="metric-note">From provided labels</div></div>
        <div class="metric"><div class="metric-label">Potential exposure</div><div class="metric-value">${money(financial.potential_exposure_inr)}</div><div class="metric-note">Risk-identified activity</div></div>
        <div class="metric"><div class="metric-label">Estimated intervention cost</div><div class="metric-value">${money(financial.false_positive_cost_inr)}</div><div class="metric-note">Illustrative operational cost</div></div>
        <div class="metric"><div class="metric-label">Estimated net benefit</div><div class="metric-value">${money(financial.estimated_net_benefit_inr)}</div><div class="metric-note">Unavailable for account-level evaluation</div></div>
    </div>
    <details class="impact-note"><summary>How this estimate was calculated</summary><p>Observed suspicious value comes from supplied labels. Potential exposure is the value associated with activity selected by policy. Avoidable exposure and net benefit require transaction-level validation and are therefore not estimated here.</p></details>`;

    // Financial bar chart
    if (chartWrap) {
        chartWrap.innerHTML = `<div class="card">
            <div class="card-header"><div><p class="eyebrow">Visual breakdown</p><h3>Financial metrics comparison</h3></div></div>
            <div class="card-body" style="padding:20px 22px;"><div style="height:240px;">${makeCanvas("imp-bar")}</div></div>
        </div>`;
        requestAnimationFrame(() => renderFinancialBarChart("imp-bar", financial));
    }
}

async function loadModel() {
    const target = document.getElementById("model-content");
    const chartsEl = document.getElementById("model-charts");
    const uploadPerf = state.analysis?.model_performance;
    let uploadSection = "";
    if (uploadPerf && uploadPerf.precision !== undefined) {
        uploadSection = `<div class="card methodology-card" style="margin-bottom:15px;"><div class="card-header"><div><p class="eyebrow">Uploaded Data Evaluation</p><h3>Performance on your labeled dataset</h3></div></div><div class="technical-details methodology-body"><div class="technical-grid"><div class="metric"><div class="metric-label">Precision</div><div class="metric-value">${pct(uploadPerf.precision)}</div><div class="metric-note">Account-level evaluation</div></div><div class="metric"><div class="metric-label">Recall</div><div class="metric-value">${pct(uploadPerf.recall)}</div><div class="metric-note">Account-level evaluation</div></div><div class="metric"><div class="metric-label">F1-Score</div><div class="metric-value">${num(uploadPerf.f1, 3)}</div><div class="metric-note">Account-level evaluation</div></div><div class="metric"><div class="metric-label">Ground Truth Field</div><div class="metric-value mono">${esc(uploadPerf.ground_truth_column || "fraud_label")}</div><div class="metric-note">Provided in upload</div></div></div></div></div>`;
    }
    const emptyNotice = (!uploadPerf || uploadPerf.precision === undefined)
        ? `<div class="card" style="margin-bottom:15px;">${empty(
            "Evaluation metrics unavailable",
            "Upload a dataset containing ground-truth labels (e.g. fraud_label, is_fraud) to calculate precision, recall, and evaluation metrics. Sentinel never displays fake performance metrics."
        )}</div>`
        : "";
    const methodologySection = `<div class="card methodology-card"><div class="card-header"><div><p class="eyebrow">Methodology</p><h3>How decisions are assembled</h3></div></div><div class="technical-details methodology-body"><p class="finding-description">The final decision combines calibrated model probability, anomaly score, behavioral deviation, related-activity evidence, and temporal activity changes. The policy then maps the fused risk score to Allow, Review, or Block.</p><div class="finding-evidence"><span class="tag">Model v1.2.0-gbm</span><span class="tag">Features v1.0.0</span><span class="tag">Policy v1.0.0</span><span class="tag">Calibration: sigmoid</span></div></div></div>`;
    target.innerHTML = uploadSection + emptyNotice + methodologySection;

    // Model charts
    if (chartsEl) {
        const results = state.analysis?.results || [];
        const hasPerf = uploadPerf && uploadPerf.precision !== undefined;
        const hasSubScores = results.some((r) => r.sub_scores);
        if (hasPerf || hasSubScores) {
            chartsEl.innerHTML = `<div class="two-column">
                ${hasPerf ? `<div class="card">
                    <div class="card-header"><div><p class="eyebrow">Performance</p><h3>Precision · Recall · F1</h3></div></div>
                    <div class="card-body" style="padding:20px 22px;"><div style="height:220px;">${makeCanvas("md-pr")}</div></div>
                </div>` : ""}
                ${hasSubScores ? `<div class="card">
                    <div class="card-header"><div><p class="eyebrow">Risk signal composition</p><h3>Average sub-scores (radar)</h3></div></div>
                    <div class="card-body" style="padding:20px 22px;"><div style="height:220px;">${makeCanvas("md-radar")}</div></div>
                </div>` : ""}
            </div>`;
            requestAnimationFrame(() => {
                if (hasPerf) renderPrBarChart("md-pr", uploadPerf);
                if (hasSubScores) renderSubScoreRadar("md-radar", results);
            });
        } else {
            chartsEl.innerHTML = "";
        }
    }
}

async function runInvestigation() {
    const id = document.getElementById("investigate-entity-id").value.trim(), output = document.getElementById("investigation-output");
    if (!id) { output.innerHTML = empty("Enter an entity ID", "Choose an account, merchant, or cluster from your analysis."); return; }
    output.innerHTML = `<div class="memo">Gathering verified evidence…</div>`;
    try {
        const response = await fetch("/api/investigations", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...sessionHeaders() },
            body: JSON.stringify({ entity_id: id, entity_type: document.getElementById("investigate-entity-type").value })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Failed to generate investigation memo");
        output.innerHTML = `<div class="memo">${esc(data.investigation_memo || "No memo available.")}</div>`;
    } catch (error) {
        output.innerHTML = `<div class="status-card error">${esc(error.message)}</div>`;
    }
}

async function loadAudit() {
    const target = document.getElementById("audit-content");
    try {
        const data = await getJson("/api/audit");
        const logs = data.audit_trail || [];
        target.innerHTML = logs.length
            ? `<table class="data-table"><thead><tr><th>Time</th><th>Action</th><th>Entity</th><th>Reference</th></tr></thead><tbody>${logs.map((log) => `<tr><td class="mono">${esc(log.timestamp)}</td><td>${esc(log.action)}</td><td>${esc(log.entity_type)}</td><td class="mono">${esc(log.entity_id)}</td></tr>`).join("")}</tbody></table>`
            : empty("No audit activity", "Actions will appear here after an analysis or investigation.");
    } catch (error) {
        target.innerHTML = empty("Audit unavailable", error.message);
    }
}
