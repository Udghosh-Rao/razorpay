const state = { analysis: null, evaluation: null, file: null };

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

document.addEventListener("DOMContentLoaded", () => {
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
    } catch (error) { status.innerHTML = `<div class="status-card error"><strong>We couldn't analyze this file.</strong><p>${esc(error.message)}</p></div>`; }
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
    if (!data || (!data.spikes?.length && !data.clusters?.length && !data.results?.length)) {
        document.getElementById("findings-summary").innerHTML = "";
        document.getElementById("findings-list").innerHTML = empty("No risk findings", "Upload payment data to identify emerging activity patterns.");
        return;
    }
    const spikes = data.spikes || [], clusters = data.clusters || [], accounts = (data.results || []).filter((item) => item.decision !== "ALLOW").slice(0, 10);
    const findings = spikes.concat(clusters.map((c) => ({ ...c, description: `${c.member_count} accounts share related payment signals.`, severity: c.risk_score >= .7 ? "HIGH" : "MEDIUM", recommendedAction: "REVIEW", merchant_id: c.cluster_id, clusterFinding: true }))).concat(accounts.map((account) => ({ ...account, description: account.reasoning || "This account is outside the expected payment behavior.", severity: account.decision, recommendedAction: account.decision, merchant_id: account.account_id, accountFinding: true })));
    document.getElementById("findings-summary").innerHTML = `<span class="summary-pill"><strong>${num(spikes.length)}</strong> activity changes</span><span class="summary-pill"><strong>${num(clusters.length)}</strong> related groups</span><span class="summary-pill"><strong>${num(data.accounts_count)}</strong> accounts scored</span>`;
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
    const financial = state.analysis?.financial_metrics?.financial;
    if (!financial || (financial.observed_suspicious_value_inr === undefined && financial.potential_exposure_inr === undefined)) {
        target.innerHTML = `<div class="card">${empty(
            "Financial exposure is unavailable",
            state.analysis
                ? "Financial estimates require transaction amounts and verified fraud labels in the uploaded CSV. The product never invents financial exposure."
                : "No payment data has been uploaded yet. Upload transaction CSV data with amounts and fraud labels to calculate financial impact."
        )}</div>`;
        return;
    }
    target.innerHTML = `<div class="impact-grid">
        <div class="metric"><div class="metric-label">Suspicious transaction value</div><div class="metric-value">${money(financial.observed_suspicious_value_inr)}</div><div class="metric-note">From provided labels</div></div>
        <div class="metric"><div class="metric-label">Potential exposure</div><div class="metric-value">${money(financial.potential_exposure_inr)}</div><div class="metric-note">Risk-identified activity</div></div>
        <div class="metric"><div class="metric-label">Estimated intervention cost</div><div class="metric-value">${money(financial.false_positive_cost_inr)}</div><div class="metric-note">Illustrative operational cost</div></div>
        <div class="metric"><div class="metric-label">Estimated net benefit</div><div class="metric-value">${money(financial.estimated_net_benefit_inr)}</div><div class="metric-note">Unavailable for account-level evaluation</div></div>
    </div>
    <details class="impact-note"><summary>How this estimate was calculated</summary><p>Observed suspicious value comes from supplied labels. Potential exposure is the value associated with activity selected by policy. Avoidable exposure and net benefit require transaction-level validation and are therefore not estimated here.</p></details>`;
}

async function loadModel() {
    const target = document.getElementById("model-content");
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
