"""
Fraud-Spike Sentinel — FastAPI Application Server

Session-based, stateless runtime architecture.
No SQLite required. Designed for Hugging Face Spaces deployment.
Each session holds its own analysis state in memory.
"""

import io
import os
import json
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np

from backend.policy_engine import evaluate_policy, POLICY_VERSION, DEFAULT_TAU_LOW, DEFAULT_TAU_HIGH
from backend.risk_engine import DEFAULT_RISK_ENGINE
from backend.session import get_session, get_session_entity_evidence
from backend.agent import generate_investigation_memo

MODEL_VERSION = "v1.2.0-gbm"
FEATURE_VERSION = "v1.0.0"
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")

# Upload guard: 20 MB limit
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

app = FastAPI(
    title="Sentinel",
    description="Payment risk intelligence and decision support for merchants.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")

LABEL_COLUMNS = (
    "is_suspicious",
    "label_suspicious",
    "is_fraud",
    "fraud_label",
    "label",
    "target",
    "fraud",
    "is_flagged",
)
CSV_REQUIRED_COLUMNS = ("timestamp", "amount")
MODEL_REQUIRED_INPUTS = (
    "account_id",
    "timestamp",
    "amount",
    "merchant_id",
    "device_id",
    "payment_method",
    "status",
)
CSV_OPTIONAL_COLUMNS = (
    "transaction_id",
    "account_id",
    "merchant_id",
    "device_id",
    "payment_method",
    "status",
    "region",
    "bank",
    "is_disputed",
    "is_suspicious",
    "fraud_label",
)
OPTIONAL_COLUMN_CAPABILITIES = {
    "account_id": ["customer behavior", "account-level risk scoring"],
    "merchant_id": ["merchant spike detection", "merchant volume features"],
    "device_id": ["coordinated cluster detection", "device reuse features"],
    "payment_method": ["instrument sharing analysis"],
    "status": ["failure rate features", "spike proxy detection"],
    "region": ["geographic metadata"],
    "bank": ["bank metadata"],
    "is_disputed": ["dispute rate features"],
    "fraud_label": ["precision/recall evaluation", "decision quality benchmarking"],
}
COLUMN_ALIASES = {
    "account_id": ["account_id", "customer_id", "user_id", "customer", "account", "client_id", "acct_id"],
    "transaction_id": ["transaction_id", "txn_id", "transaction", "payment_id", "id", "txn", "payment_reference"],
    "amount": ["amount", "payment_amount", "transaction_amount", "value", "price", "total", "total_amount"],
    "timestamp": ["timestamp", "time", "date", "created_at", "transaction_time", "payment_time", "event_time", "datetime"],
    "merchant_id": ["merchant_id", "merchant", "merchant_code", "merchant_name", "store_id", "shop_id"],
    "device_id": ["device_id", "device", "device_identifier", "device_token", "phone_id", "imei"],
    "payment_method": ["payment_method", "method", "payment_type", "instrument", "channel", "payment_channel"],
    "status": ["status", "payment_status", "transaction_status", "state", "outcome"],
    "region": ["region", "location", "geo", "country", "city", "state", "area"],
    "bank": ["bank", "bank_name", "issuer_bank", "bank_code", "financial_institution"],
    "is_disputed": ["is_disputed", "disputed", "dispute_flag", "has_dispute"],
    "fraud_label": ["fraud", "is_fraud", "fraud_label", "label", "target", "is_suspicious", "is_flagged",
                    "suspected_fraud"],
}
CSV_COLUMN_DEFINITIONS = {
    "account_id": {"required": False, "description": "Customer or account ID used to group transactions when available.", "example": "ACC1001"},
    "timestamp": {"required": True, "description": "Transaction timestamp in ISO or CSV datetime format.", "example": "2026-08-20 14:32:10"},
    "amount": {"required": True, "description": "Payment amount in the local currency.", "example": "2499.00"},
    "transaction_id": {"required": False, "description": "Unique payment transaction ID. Optional but recommended.", "example": "TXN-1001"},
    "merchant_id": {"required": False, "description": "Merchant identifier used for spike and merchant-volume analysis.", "example": "MERCHANT-45"},
    "device_id": {"required": False, "description": "Device identifier used for device clusters and reuse analysis.", "example": "DEV-8901"},
    "payment_method": {"required": False, "description": "Payment method or instrument used for the transaction.", "example": "UPI"},
    "status": {"required": False, "description": "Transaction business status. Useful for failure-rate features.", "example": "success"},
    "region": {"required": False, "description": "Customer or merchant region used for metadata analysis.", "example": "Bengaluru"},
    "bank": {"required": False, "description": "Bank or issuer identifier.", "example": "HDFC"},
    "is_disputed": {"required": False, "description": "Optional boolean indicator for disputes.", "example": "0"},
    "fraud_label": {"required": False, "description": "Ground-truth label for supervised evaluation; required for precision/recall.", "example": "0"},
}


# ---------------------------------------------------------------------------
# Column resolution helpers
# ---------------------------------------------------------------------------

def normalize_column_name(value):
    if value is None:
        return ""
    return str(value).strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_").replace(".", "_")


def resolve_column_aliases(columns):
    normalized_map = {normalize_column_name(col): col for col in columns if col is not None}
    resolved = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_column_name(alias)
            if normalized_alias in normalized_map:
                resolved[canonical] = normalized_map[normalized_alias]
                break
    return resolved


def apply_column_alias_mapping(df):
    issue_df = df.copy()
    resolved = resolve_column_aliases(issue_df.columns)
    rename_map = {actual: canonical for canonical, actual in resolved.items() if actual != canonical}
    if rename_map:
        issue_df = issue_df.rename(columns=rename_map)
    return issue_df, resolved


def detect_label_column(df):
    for col in LABEL_COLUMNS:
        if col in df.columns:
            return col
    resolved = resolve_column_aliases(df.columns)
    for canonical in LABEL_COLUMNS:
        if canonical in resolved:
            return canonical
    return None


def normalize_ground_truth_value(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value) if int(value) in (0, 1) else None
    if isinstance(value, (float, np.floating)):
        if pd.isna(value):
            return None
        return int(value) if int(value) in (0, 1) else None
    text = str(value).strip().lower()
    if text in {"0", "1"}:
        return int(text)
    if text in {"true", "t", "yes", "y", "fraud", "suspicious"}:
        return 1
    if text in {"false", "f", "no", "n", "legit", "safe", "not_suspicious", "non_fraud", "clean"}:
        return 0
    return None


def validate_ground_truth_labels(df, label_col):
    if label_col is None:
        return False, {"reason": "No label column detected."}
    label_values = df[label_col].map(normalize_ground_truth_value)
    invalid_mask = label_values.isna() & df[label_col].notna()
    if invalid_mask.any():
        return False, {
            "reason": "Ground-truth labels could not be interpreted reliably.",
            "invalid_examples": df.loc[invalid_mask, label_col].astype(str).head(10).tolist(),
        }
    if label_values.empty or label_values.isna().any():
        return False, {"reason": "No valid label values were found."}
    return True, {"valid_rows": int(label_values.notna().sum())}





def model_artifacts_available():
    return os.path.exists(os.path.join(MODEL_DIR, "gbm_calibrated.pkl"))


def _get_session_id(x_session_id: str = "") -> str:
    """Return session identifier from header, default to 'default' session."""
    return (x_session_id or "default").strip() or "default"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/health/", methods=["GET", "HEAD"])
def health_check():
    model_ok = model_artifacts_available()

    checks = {
        "model_artifacts": "loaded" if model_ok else "missing",
        "session_storage": "in-memory",
    }

    return {
        "status": "healthy" if model_ok else "degraded",
        "service": "Fraud-Spike Sentinel",
        "environment": "Production",
        "timestamp": datetime.now().isoformat(),
        "model_version": MODEL_VERSION,
        "policy_version": POLICY_VERSION,
        "checks": checks,
    }


@app.head("/")
def root_head():
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Overview / session state (session-only, zero fake data)
# ---------------------------------------------------------------------------

@app.get("/api/risk/overview")
def get_risk_overview(x_session_id: str = Header(default="default")):
    session = get_session(_get_session_id(x_session_id), create=False)

    metrics = {
        "customers_analyzed": 0,
        "transactions_processed": 0,
        "merchants_monitored": 0,
        "active_spikes_detected": 0,
        "active_clusters_detected": 0,
        "potential_exposure_inr": None,
    }
    recent_spikes = []
    recent_clusters = []

    if session and session.has_analysis:
        ar = session.analysis_result or {}
        summary = ar.get("summary", {})
        metrics["transactions_processed"] = summary.get("transactions", 0) or 0
        metrics["customers_analyzed"] = summary.get("customers", 0) or 0
        metrics["merchants_monitored"] = summary.get("merchants") or 0
        metrics["active_spikes_detected"] = len(session.merchant_spikes)
        metrics["active_clusters_detected"] = len(session.detected_clusters)
        fin = (ar.get("financial_metrics") or {}).get("financial") or {}
        if fin.get("potential_exposure_inr") is not None:
            metrics["potential_exposure_inr"] = fin["potential_exposure_inr"]
        recent_spikes = session.merchant_spikes[:5]
        recent_clusters = [
            {k: v for k, v in c.items() if k != "customer_ids"}
            for c in session.detected_clusters[:5]
        ]

    is_operational = session is not None and session.has_analysis

    return {
        "system_status": "OPERATIONAL" if is_operational else "READY",
        "environment": "Production",
        "model_version": MODEL_VERSION,
        "policy_version": POLICY_VERSION,
        "evaluation_available": False,
        "last_evaluation_timestamp": None,
        "session_has_analysis": is_operational,
        "metrics": metrics,
        "recent_spikes": recent_spikes,
        "recent_clusters": recent_clusters,
    }


# ---------------------------------------------------------------------------
# Risk events / clusters (session-only, zero fake data)
# ---------------------------------------------------------------------------

@app.get("/api/risk/events")
def list_risk_events(x_session_id: str = Header(default="default")):
    session = get_session(_get_session_id(x_session_id), create=False)
    if session and session.has_analysis and session.merchant_spikes:
        return {"events": session.merchant_spikes}
    return {"events": []}


@app.get("/api/risk/events/{event_id}")
def get_risk_event_detail(event_id: str, x_session_id: str = Header(default="default")):
    session = get_session(_get_session_id(x_session_id), create=False)
    if session and session.merchant_spikes:
        for spike in session.merchant_spikes:
            if spike.get("merchant_id") == event_id or spike.get("event_id") == event_id:
                return spike
    raise HTTPException(status_code=404, detail=f"Risk event '{event_id}' not found.")


@app.get("/api/risk/clusters")
def list_clusters(x_session_id: str = Header(default="default")):
    session = get_session(_get_session_id(x_session_id), create=False)
    if session and session.has_analysis and session.detected_clusters:
        clusters = [
            {k: v for k, v in c.items() if k != "customer_ids"}
            for c in session.detected_clusters
        ]
        return {"clusters": clusters}
    return {"clusters": []}


@app.get("/api/risk/clusters/{cluster_id}")
def get_cluster_detail(cluster_id: str, x_session_id: str = Header(default="default")):
    session = get_session(_get_session_id(x_session_id), create=False)
    if session and session.detected_clusters:
        for c in session.detected_clusters:
            if c.get("cluster_id") == cluster_id:
                return {k: v for k, v in c.items() if k != "customer_ids"}
    raise HTTPException(status_code=404, detail=f"Cluster '{cluster_id}' not found.")


# ---------------------------------------------------------------------------
# Evaluation endpoints (derived only from active session, zero fake data)
# ---------------------------------------------------------------------------

@app.get("/api/evaluation/latest")
def get_latest_evaluation(x_session_id: str = Header(default="default")):
    session = get_session(_get_session_id(x_session_id), create=False)
    if session and session.has_analysis and session.analysis_result:
        perf = session.analysis_result.get("model_performance")
        if perf and perf.get("precision") is not None:
            return {
                "evaluation_available": True,
                "status": "available",
                "model": {
                    "evaluation_unit": perf.get("evaluation_unit", "account"),
                    "performance": perf,
                },
                "ground_truth_column": perf.get("ground_truth_column"),
            }
    return {
        "evaluation_available": False,
        "status": "unavailable",
        "message": "Evaluation metrics are unavailable. Upload a dataset containing ground-truth labels (e.g. fraud_label or is_fraud) to view evaluation metrics.",
        "model": {},
        "financials": {},
        "data": {},
    }


@app.get("/api/evaluation/thresholds")
def get_threshold_sweep(x_session_id: str = Header(default="default")):
    session = get_session(_get_session_id(x_session_id), create=False)
    if session and session.has_analysis and session.analysis_result:
        fin = (session.analysis_result.get("financial_metrics") or {}).get("financial")
        if fin and (fin.get("potential_exposure_inr") is not None or fin.get("observed_suspicious_value_inr") is not None):
            return {
                "evaluation_available": True,
                "status": "available",
                "applied_thresholds": {"tau_low": DEFAULT_TAU_LOW, "tau_high": DEFAULT_TAU_HIGH},
                "financial_summary": fin,
            }
    return {
        "evaluation_available": False,
        "status": "unavailable",
        "message": "Financial impact metrics are unavailable. Upload a dataset with transaction amounts and ground-truth labels.",
        "applied_thresholds": {},
        "cost_assumptions": {},
        "financial_summary": {},
    }


# ---------------------------------------------------------------------------
# Audit log (session-scoped)
# ---------------------------------------------------------------------------

@app.get("/api/audit")
def get_audit_trail(x_session_id: str = Header(default="default"), limit: int = Query(default=50)):
    session = get_session(_get_session_id(x_session_id), create=False)
    if not session:
        return {"audit_trail": []}
    logs = list(reversed(session.audit_log))[:limit]
    return {"audit_trail": logs}


# ---------------------------------------------------------------------------
# Entity evidence (session-scoped)
# ---------------------------------------------------------------------------

@app.get("/api/entities/{entity_id}/evidence")
def get_entity_evidence_endpoint(
    entity_id: str,
    entity_type: str = Query(default="account"),
    x_session_id: str = Header(default="default"),
):
    session = get_session(_get_session_id(x_session_id), create=False)
    evidence = get_session_entity_evidence(session, entity_id, entity_type)
    if not evidence.get("found"):
        raise HTTPException(status_code=404, detail=f"No evidence found for {entity_type} '{entity_id}'.")
    return evidence


# ---------------------------------------------------------------------------
# Investigations (session-scoped)
# ---------------------------------------------------------------------------

@app.post("/api/investigations")
def trigger_investigation(payload: dict, x_session_id: str = Header(default="default")):
    entity_id = payload.get("entity_id")
    entity_type = (payload.get("entity_type") or "account").strip().lower()

    if not entity_id:
        raise HTTPException(status_code=400, detail="Missing entity_id in request body.")

    session = get_session(_get_session_id(x_session_id), create=False)
    risk_score = payload.get("risk_score")
    memo = generate_investigation_memo(entity_id, entity_type, risk_score, payload, session=session)

    session_obj = get_session(_get_session_id(x_session_id), create=True)
    session_obj.log_audit("INVESTIGATION_GENERATED", entity_type, entity_id, {"risk_score": risk_score})

    return {
        "status": "COMPLETED" if not memo.startswith("Investigation unavailable") else "UNAVAILABLE",
        "entity_id": entity_id,
        "entity_type": entity_type,
        "investigation_memo": memo,
        "generated_at": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# CSV Template download
# ---------------------------------------------------------------------------

def _build_csv_template_dataframe():
    rows = [
        {
            "account_id": "ACC1001",
            "timestamp": "2026-08-20 14:32:10",
            "amount": "2499.00",
            "transaction_id": "TXN-1001",
            "merchant_id": "MERCHANT-45",
            "device_id": "DEV-8901",
            "payment_method": "UPI",
            "status": "success",
            "region": "Bengaluru",
            "bank": "HDFC",
            "is_disputed": "0",
            "fraud_label": "0",
        },
        {
            "account_id": "ACC1002",
            "timestamp": "2026-08-20 15:08:20",
            "amount": "12850.00",
            "transaction_id": "TXN-1002",
            "merchant_id": "MERCHANT-14",
            "device_id": "DEV-2304",
            "payment_method": "CARD",
            "status": "failed",
            "region": "Mumbai",
            "bank": "ICICI",
            "is_disputed": "0",
            "fraud_label": "1",
        },
        {
            "account_id": "ACC1003",
            "timestamp": "2026-08-20 16:11:45",
            "amount": "5600.00",
            "transaction_id": "TXN-1003",
            "merchant_id": "MERCHANT-21",
            "device_id": "DEV-4418",
            "payment_method": "NETBANKING",
            "status": "success",
            "region": "Delhi",
            "bank": "SBI",
            "is_disputed": "1",
            "fraud_label": "0",
        },
    ]
    return pd.DataFrame(rows, columns=list(CSV_COLUMN_DEFINITIONS.keys()))


@app.get("/api/csv-template")
def get_csv_template():
    """Download a CSV template using the canonical schema for payment transactions."""
    csv_str = _build_csv_template_dataframe().to_csv(index=False)
    return Response(
        content=csv_str,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=payment_transaction_template.csv",
            "X-Template-Mode": "example-only",
        },
    )


@app.get("/api/sample-csv")
def get_sample_csv():
    """Backward-compatible alias for the canonical CSV template."""
    return get_csv_template()


# ---------------------------------------------------------------------------
# CSV upload validation
# ---------------------------------------------------------------------------

def validate_uploaded_transactions(df):
    issue_df, column_mapping = apply_column_alias_mapping(df)
    required_cols = set(CSV_REQUIRED_COLUMNS)
    missing_required = sorted(required_cols - set(issue_df.columns))
    if missing_required:
        return None, {
            "rows_received": len(issue_df),
            "valid_rows": 0,
            "invalid_rows": len(issue_df),
            "missing_required_columns": missing_required,
            "recognized_columns": sorted(issue_df.columns.tolist()),
            "column_mapping": column_mapping,
            "error": (
                "We couldn't process this file. Missing required columns: "
                f"{', '.join(missing_required)}. Please check the file for a timestamp and amount field. "
                f"Required columns: {', '.join(CSV_REQUIRED_COLUMNS)}."
            ),
        }

    issue_df["amount"] = pd.to_numeric(issue_df["amount"], errors="coerce")
    issue_df["timestamp"] = pd.to_datetime(issue_df["timestamp"], errors="coerce", utc=True)

    valid_mask = issue_df["amount"].notna() & issue_df["timestamp"].notna()
    valid_df = issue_df.loc[valid_mask].copy()
    invalid_rows = int((~valid_mask).sum())
    validation_summary = {
        "rows_received": len(issue_df),
        "valid_rows": int(valid_df.shape[0]),
        "invalid_rows": invalid_rows,
        "recognized_columns": sorted(issue_df.columns.tolist()),
        "column_mapping": column_mapping,
        "missing_optional_columns": [col for col in CSV_OPTIONAL_COLUMNS if col not in issue_df.columns],
    }

    if invalid_rows:
        validation_summary["warning"] = (
            f"{invalid_rows} invalid row(s) were excluded from the analysis. "
            "Please check timestamp and amount values before re-uploading."
        )

    return valid_df, validation_summary


# ---------------------------------------------------------------------------
# CSV Upload & Analysis — session-based
# ---------------------------------------------------------------------------

@app.post("/api/upload")
async def analyze_upload_csv(
    file: UploadFile = File(...),
    x_session_id: str = Header(default="default"),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Invalid file format. Please upload a CSV file.")

    # Read bytes with size guard
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large ({len(raw) // (1024*1024)} MB). Maximum allowed size is 20 MB.",
        )

    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV file: {str(e)}")

    df, validation_summary = validate_uploaded_transactions(df)
    if df is None:
        raise HTTPException(status_code=400, detail=validation_summary["error"])

    if validation_summary["invalid_rows"] > 0:
        valid_ratio = validation_summary["valid_rows"] / max(validation_summary["rows_received"], 1)
        if valid_ratio == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"We couldn't process this file. {validation_summary['rows_received']} rows received, "
                    f"0 valid rows. {validation_summary['warning']}"
                ),
            )

    # Capability detection
    missing_optional = [col for col in OPTIONAL_COLUMN_CAPABILITIES if col not in df.columns]
    disabled_analyses = []
    for col in missing_optional:
        disabled_analyses.extend(OPTIONAL_COLUMN_CAPABILITIES[col])

    label_col = detect_label_column(df)
    has_ground_truth = False
    ground_truth_validation = {"reason": "No ground-truth label detected."}
    if label_col is not None:
        has_ground_truth, ground_truth_validation = validate_ground_truth_labels(df, label_col)

    txn_df = df.copy()
    txn_df["timestamp"] = pd.to_datetime(txn_df["timestamp"], errors="coerce", utc=True)
    if txn_df["timestamp"].isna().any():
        raise HTTPException(status_code=400, detail="Invalid timestamp values in CSV.")

    if "txn_id" not in txn_df.columns:
        txn_df["txn_id"] = [f"upload_{i}" for i in range(len(txn_df))]

    has_account_dimension = "account_id" in txn_df.columns
    if has_account_dimension:
        txn_df["customer_id"] = txn_df["account_id"]

    if label_col is not None and has_ground_truth:
        txn_df[label_col] = txn_df[label_col].map(normalize_ground_truth_value)
        if "is_suspicious" not in txn_df.columns:
            txn_df["is_suspicious"] = txn_df[label_col]

    # Build customer records
    cust_records = []
    if has_account_dimension:
        for cust_id, group in txn_df.groupby("customer_id"):
            record = {
                "customer_id": cust_id,
                "scenario": "custom_upload",
                "signup_date": group["timestamp"].min(),
            }
            if label_col and has_ground_truth:
                record["label_suspicious"] = int(pd.to_numeric(group[label_col], errors="coerce").fillna(0).max())
            if "region" in group.columns:
                record["region"] = group["region"].dropna().iloc[0] if group["region"].notna().any() else None
            if "device_id" in group.columns:
                record["primary_device"] = group["device_id"].dropna().iloc[0] if group["device_id"].notna().any() else None
            if "bank" in group.columns:
                record["bank"] = group["bank"].dropna().iloc[0] if group["bank"].notna().any() else None
            if "payment_method" in group.columns:
                record["num_instruments"] = int(group["payment_method"].nunique())
                record["instruments"] = json.dumps(list(group["payment_method"].dropna().unique()))
            cust_records.append(record)

    customers_df = pd.DataFrame(cust_records)

    # Cluster detection
    detected_clusters, customer_cluster_risk = [], {}
    if "device_id" in txn_df.columns and "customer_id" in txn_df.columns and txn_df["customer_id"].notna().any():
        from backend.cluster_detector import detect_clusters
        detected_clusters, customer_cluster_risk = detect_clusters(txn_df)
    else:
        disabled_analyses.append("coordinated cluster detection (device_id or customer/account dimension missing)")

    # Spike detection
    merchant_spikes = []
    if "merchant_id" in txn_df.columns:
        from backend.spike_detector import detect_merchant_spikes
        merchant_spikes = detect_merchant_spikes(txn_df, window_hours=24)
    else:
        disabled_analyses.append("merchant spike detection (merchant_id missing)")

    # Model inputs check
    missing_model_inputs = sorted(set(MODEL_REQUIRED_INPUTS) - set(txn_df.columns))
    invalid_model_inputs = sorted(
        col for col in MODEL_REQUIRED_INPUTS
        if col in txn_df.columns and txn_df[col].isna().any()
    )
    missing_model_inputs = sorted(set(missing_model_inputs + invalid_model_inputs))

    # Load cohort reference stats (frozen from training — do not compute from upload)
    cohort_stats_path = os.path.join(MODEL_DIR, "cohort_reference_stats.json")
    ref_stats = None
    if os.path.exists(cohort_stats_path):
        try:
            with open(cohort_stats_path, "r") as f:
                ref_stats = json.load(f)
        except Exception:
            ref_stats = None

    # Feature computation (only when account dimension + required inputs are present)
    if has_account_dimension and not customers_df.empty and not missing_model_inputs:
        from data.features import compute_features
        features_df = compute_features(
            customers_df, txn_df, reference_stats=ref_stats, scoring_time=txn_df["timestamp"].max()
        )
    else:
        features_df = pd.DataFrame()

    # Build session object
    sid = _get_session_id(x_session_id)
    session = get_session(sid, create=True)

    # --- Early return if model scoring unavailable ---
    if features_df.empty or missing_model_inputs:
        if not has_account_dimension:
            model_reason = "Model risk scoring unavailable for this file because an account or customer identifier was not provided."
        else:
            model_reason = (
                "Model risk scoring unavailable for this file because the required features "
                f"cannot be calculated from the provided fields. Missing inputs: {', '.join(missing_model_inputs)}."
            )

        analysis_result = {
            "results": [],
            "accounts_count": 0,
            "spikes_count": len(merchant_spikes),
            "clusters_count": len(detected_clusters),
            "validation_summary": validation_summary,
            "summary": {
                "customers": len(customers_df),
                "transactions": len(txn_df),
                "merchants": int(txn_df["merchant_id"].nunique()) if "merchant_id" in txn_df.columns else None,
                "devices": int(txn_df["device_id"].nunique()) if "device_id" in txn_df.columns else None,
                "clusters_detected": len(detected_clusters),
                "spikes_detected": len(merchant_spikes),
            },
            "schema_report": {
                "missing_optional_columns": missing_optional,
                "disabled_analyses": sorted(set(disabled_analyses)),
                "ground_truth_available": has_ground_truth,
                "ground_truth_column": label_col if has_ground_truth else None,
                "ground_truth_validation": ground_truth_validation,
                "model_scoring_available": False,
                "model_scoring_reason": model_reason,
                "missing_model_inputs": missing_model_inputs,
                "recognized_columns": sorted(txn_df.columns.tolist()),
                "unavailable_columns": sorted(set(CSV_COLUMN_DEFINITIONS) - set(txn_df.columns)),
            },
            "financial_metrics": None,
            "model_performance": {"message": model_reason},
            "spikes": merchant_spikes[:5],
            "clusters": [{k: v for k, v in c.items() if k != "customer_ids"} for c in detected_clusters[:5]],
        }

        # Persist to session
        session.has_analysis = True
        session.analysis_result = analysis_result
        session.txn_df = txn_df
        session.customers_df = customers_df
        session.merchant_spikes = merchant_spikes
        session.detected_clusters = detected_clusters
        session.decisions = []
        session.log_audit("CSV_UPLOAD_ANALYZED", "file", file.filename,
                          {"rows_received": len(df), "spikes_found": len(merchant_spikes)})

        return analysis_result

    # --- Model scoring path ---
    from backend.anomaly_model import load_anomaly_model
    from data.features import FEATURE_COLS

    anomaly_model = load_anomaly_model()
    if anomaly_model is not None:
        anomaly_scores = anomaly_model.predict_anomaly_score(features_df)
    else:
        anomaly_scores = np.zeros(len(features_df))

    from backend.model import load_model, predict_single

    # Strict check: ALL required model features must be present and finite.
    missing_computed_features = sorted(f for f in FEATURE_COLS if f not in features_df.columns)
    if missing_computed_features:
        # Degrade gracefully — do not error the whole upload.
        fallback_reason = (
            "Model risk scoring unavailable: the feature computation step could not produce "
            f"all required features from this dataset. Missing: {', '.join(missing_computed_features[:5])}."
        )
        fallback_result = {
            "results": [],
            "accounts_count": 0,
            "spikes_count": len(merchant_spikes),
            "clusters_count": len(detected_clusters),
            "validation_summary": validation_summary,
            "summary": {
                "customers": len(customers_df),
                "transactions": len(txn_df),
                "merchants": int(txn_df["merchant_id"].nunique()) if "merchant_id" in txn_df.columns else None,
                "devices": int(txn_df["device_id"].nunique()) if "device_id" in txn_df.columns else None,
                "clusters_detected": len(detected_clusters),
                "spikes_detected": len(merchant_spikes),
            },
            "schema_report": {
                "missing_optional_columns": missing_optional,
                "disabled_analyses": sorted(set(disabled_analyses)),
                "ground_truth_available": has_ground_truth,
                "ground_truth_column": label_col if has_ground_truth else None,
                "ground_truth_validation": ground_truth_validation,
                "model_scoring_available": False,
                "model_scoring_reason": fallback_reason,
                "missing_model_inputs": missing_computed_features,
                "recognized_columns": sorted(txn_df.columns.tolist()),
                "unavailable_columns": sorted(set(CSV_COLUMN_DEFINITIONS) - set(txn_df.columns)),
            },
            "financial_metrics": None,
            "model_performance": {"message": fallback_reason},
            "spikes": merchant_spikes[:5],
            "clusters": [{k: v for k, v in c.items() if k != "customer_ids"} for c in detected_clusters[:5]],
        }
        session.has_analysis = True
        session.analysis_result = fallback_result
        session.txn_df = txn_df
        session.customers_df = customers_df
        session.merchant_spikes = merchant_spikes
        session.detected_clusters = detected_clusters
        session.decisions = []
        session.log_audit("CSV_UPLOAD_ANALYZED", "file", file.filename,
                          {"rows_received": len(df), "model_scoring": "unavailable", "reason": "missing_computed_features"})
        return fallback_result

    if not model_artifacts_available():
        raise HTTPException(
            status_code=503,
            detail="Trained model artifacts unavailable. Run: python -m evaluation.run_all",
        )

    try:
        model_obj, scaler_obj = load_model()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to load model artifacts: {str(e)}")

    results_list = []
    decisions_to_store = []

    spike_windows = [
        (s["merchant_id"], pd.to_datetime(s["window_start"]), pd.to_datetime(s["window_end"]))
        for s in merchant_spikes
    ]

    for idx, row in features_df.iterrows():
        cust_id = row["customer_id"]
        feat_dict = row.to_dict()

        try:
            ml_score = predict_single(feat_dict, model_obj, scaler_obj)
        except (ValueError, KeyError):
            # predict_single raises ValueError if any FEATURE_COL is missing/NaN/non-finite.
            # Skip this account rather than crashing the whole upload.
            continue
        anom_score = float(anomaly_scores[idx]) if idx < len(anomaly_scores) else 0.0
        c_risk = float(customer_cluster_risk.get(cust_id, 0.0))

        # Temporal spike correlation — only flag if transaction is inside the spike window
        cust_txns = txn_df[txn_df["customer_id"] == cust_id]
        has_spike_txn = False
        if "merchant_id" in cust_txns.columns and spike_windows:
            for _, ctxn in cust_txns.iterrows():
                m_id = ctxn.get("merchant_id")
                t_ts = ctxn.get("timestamp")
                if pd.notna(m_id) and pd.notna(t_ts):
                    for sm_id, w_start, w_end in spike_windows:
                        if m_id == sm_id and w_start <= t_ts < w_end:
                            has_spike_txn = True
                            break
                if has_spike_txn:
                    break
        spike_risk = 1.0 if has_spike_txn else 0.0

        canonical_risk = DEFAULT_RISK_ENGINE.fuse_risk_scores(
            ml_score,
            anomaly_score=anom_score,
            dev_score=feat_dict.get("behavioral_deviation_score", 0),
            cluster_risk=c_risk,
            spike_risk=spike_risk,
        )

        policy_ctx = {
            "amount": feat_dict.get("average_amount", 0),
            "is_cluster_member": c_risk > 0.5,
            "is_merchant_spike": has_spike_txn,
        }
        policy_res = evaluate_policy(canonical_risk, context=policy_ctx)

        sub_scores = {
            "ml_probability": round(ml_score, 4),
            "anomaly_score": round(anom_score, 4),
            "cluster_risk": round(c_risk, 4),
            "temporal_spike_risk": round(spike_risk, 4),
        }

        results_list.append({
            "account_id": str(cust_id),
            "risk_score": round(canonical_risk, 4),
            "decision": policy_res["decision"],
            "reasoning": policy_res["reasoning"],
            "transaction_count": int(feat_dict.get("txn_count_total", 0)),
            "total_amount_inr": round(float(feat_dict.get("average_amount", 0) * feat_dict.get("txn_count_total", 0)), 2),
            "average_amount_inr": round(float(feat_dict.get("average_amount", 0)), 2),
            "failure_rate": round(float(feat_dict.get("failure_rate", 0)), 4) if "status" in txn_df.columns else None,
            "sub_scores": sub_scores,
        })

        decisions_to_store.append({
            "decision_id": str(uuid.uuid4()),
            "entity_id": str(cust_id),
            "account_id": str(cust_id),
            "entity_type": "account",
            "decision": policy_res["decision"],
            "canonical_risk_score": round(canonical_risk, 4),
            "model_version": MODEL_VERSION,
            "feature_version": FEATURE_VERSION,
            "policy_version": POLICY_VERSION,
            "sub_scores": sub_scores,
            "reasoning": policy_res["reasoning"],
            "created_at": datetime.now().isoformat(),
        })

    results_list.sort(key=lambda x: -x["risk_score"])

    # Ensure features_df has total_amount for consistent financial metric aggregation
    if not features_df.empty and "average_amount" in features_df.columns and "txn_count_total" in features_df.columns:
        features_df["total_amount"] = features_df["average_amount"] * features_df["txn_count_total"]

    financial_metrics = None
    performance_metrics = None
    if has_ground_truth:
        from backend.financial import compute_financial_metrics
        from sklearn.metrics import precision_score, recall_score, f1_score

        score_by_customer = {str(item["account_id"]): item["risk_score"] for item in results_list}
        # Only include accounts that were actually scored (some may have been skipped due to feature errors)
        scored_customer_ids = [cid for cid in features_df["customer_id"] if str(cid) in score_by_customer]
        label_map = txn_df.groupby("customer_id")[label_col].max()
        y_true = [int(pd.to_numeric(label_map.get(cid, 0), errors="coerce") or 0) for cid in scored_customer_ids]
        scores = [score_by_customer[str(cid)] for cid in scored_customer_ids]
        try:
            financial_metrics = compute_financial_metrics(
                scores, y_true, features_df, tau_low=DEFAULT_TAU_LOW,
                tau_high=DEFAULT_TAU_HIGH, evaluation_unit="account"
            )
        except ValueError as e:
            financial_metrics = {"error": str(e)}

        preds = [1 if s >= DEFAULT_TAU_LOW else 0 for s in scores]
        performance_metrics = {
            "precision": round(float(precision_score(y_true, preds, zero_division=0)), 4),
            "recall": round(float(recall_score(y_true, preds, zero_division=0)), 4),
            "f1": round(float(f1_score(y_true, preds, zero_division=0)), 4),
            "evaluation_unit": "account",
            "ground_truth_column": label_col,
        }
    else:
        performance_metrics = {
            "message": "Ground-truth labels unavailable. Precision/recall cannot be calculated for this dataset.",
        }

    analysis_result = {
        "results": results_list,
        "accounts_count": len(results_list),
        "spikes_count": len(merchant_spikes),
        "clusters_count": len(detected_clusters),
        "validation_summary": validation_summary,
        "summary": {
            "customers": len(customers_df),
            "transactions": len(txn_df),
            "merchants": int(txn_df["merchant_id"].nunique()) if "merchant_id" in txn_df.columns else None,
            "devices": int(txn_df["device_id"].nunique()) if "device_id" in txn_df.columns else None,
            "clusters_detected": len(detected_clusters),
            "spikes_detected": len(merchant_spikes),
        },
        "schema_report": {
            "missing_optional_columns": missing_optional,
            "disabled_analyses": sorted(set(disabled_analyses)),
            "ground_truth_available": has_ground_truth,
            "ground_truth_column": label_col if has_ground_truth else None,
            "ground_truth_validation": ground_truth_validation,
            "model_scoring_available": True,
            "model_scoring_reason": "Model scoring is available because account identifiers and required features were present.",
            "recognized_columns": sorted(txn_df.columns.tolist()),
            "unavailable_columns": sorted(set(CSV_COLUMN_DEFINITIONS) - set(txn_df.columns)),
        },
        "financial_metrics": financial_metrics,
        "model_performance": performance_metrics,
        "spikes": merchant_spikes[:5],
        "clusters": [{k: v for k, v in c.items() if k != "customer_ids"} for c in detected_clusters[:5]],
    }

    # Save to session (replaces SQLite persist)
    session.has_analysis = True
    session.analysis_result = analysis_result
    session.txn_df = txn_df
    session.customers_df = customers_df
    session.merchant_spikes = merchant_spikes
    session.detected_clusters = detected_clusters
    session.decisions = decisions_to_store
    session.log_audit("CSV_UPLOAD_ANALYZED", "file", file.filename,
                      {"accounts_analyzed": len(results_list), "spikes_found": len(merchant_spikes)})

    return analysis_result


# ---------------------------------------------------------------------------
# Serve frontend static files
# ---------------------------------------------------------------------------

if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
