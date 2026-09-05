"""
Fraud-Spike Sentinel — In-Memory Session Management

Replaces SQLite for production runtime and deployment (e.g. Hugging Face Spaces).
Maintains isolated in-memory analysis state for active user sessions.
No database, no disk persistence, no credentials required.
"""

import time
from datetime import datetime
from typing import Dict, List, Optional, Any
import pandas as pd

MAX_SESSIONS = 50
SESSION_TIMEOUT_SECONDS = 24 * 3600  # 24 hours


class SessionState:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.created_at = datetime.now().isoformat()
        self.last_accessed = time.time()
        self.has_analysis = False
        self.analysis_result: Optional[Dict[str, Any]] = None
        self.txn_df: Optional[pd.DataFrame] = None
        self.customers_df: Optional[pd.DataFrame] = None
        self.merchant_spikes: List[Dict[str, Any]] = []
        self.detected_clusters: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []
        self.audit_log: List[Dict[str, Any]] = []

    def touch(self):
        self.last_accessed = time.time()

    def log_audit(self, action: str, entity_type: str, entity_id: str, details: Optional[Dict[str, Any]] = None):
        self.touch()
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "details": details or {},
        }
        self.audit_log.append(entry)


_SESSIONS: Dict[str, SessionState] = {}


def cleanup_expired_sessions():
    now = time.time()
    expired = [
        sid for sid, s in _SESSIONS.items()
        if now - s.last_accessed > SESSION_TIMEOUT_SECONDS
    ]
    for sid in expired:
        _SESSIONS.pop(sid, None)

    if len(_SESSIONS) > MAX_SESSIONS:
        sorted_sessions = sorted(_SESSIONS.items(), key=lambda item: item[1].last_accessed)
        to_evict = len(_SESSIONS) - MAX_SESSIONS
        for sid, _ in sorted_sessions[:to_evict]:
            _SESSIONS.pop(sid, None)


def get_session(session_id: Optional[str] = None, create: bool = True) -> Optional[SessionState]:
    cleanup_expired_sessions()
    sid = (session_id or "default").strip()
    if sid not in _SESSIONS:
        if create:
            _SESSIONS[sid] = SessionState(sid)
        else:
            return None
    session = _SESSIONS[sid]
    session.touch()
    return session


def clear_all_sessions():
    _SESSIONS.clear()


def get_session_entity_evidence(session: Optional[SessionState], entity_id: str, entity_type: str = "account") -> dict:
    entity_type = (entity_type or "account").strip().lower()
    entity_id_str = str(entity_id).strip()
    evidence = {"entity_id": entity_id_str, "entity_type": entity_type, "found": False}

    if not session or not session.has_analysis:
        return evidence

    if entity_type == "account":
        # 1. Customer record
        if session.customers_df is not None and not session.customers_df.empty:
            cust_rows = session.customers_df[session.customers_df["customer_id"].astype(str) == entity_id_str]
            if not cust_rows.empty:
                evidence["found"] = True
                evidence["customer"] = cust_rows.iloc[0].to_dict()

        # 2. Recent transactions
        if session.txn_df is not None and not session.txn_df.empty:
            col = None
            if "customer_id" in session.txn_df.columns:
                col = "customer_id"
            elif "account_id" in session.txn_df.columns:
                col = "account_id"

            if col:
                txns = session.txn_df[session.txn_df[col].astype(str) == entity_id_str]
                if not txns.empty:
                    evidence["found"] = True
                    if "timestamp" in txns.columns:
                        txns = txns.sort_values("timestamp", ascending=False)
                    evidence["recent_transactions"] = txns.head(20).to_dict(orient="records")

        # 3. Decision
        if session.decisions:
            for d in reversed(session.decisions):
                if str(d.get("entity_id")) == entity_id_str or str(d.get("account_id")) == entity_id_str:
                    evidence["found"] = True
                    evidence["decision"] = d
                    break
        elif session.analysis_result and session.analysis_result.get("results"):
            for res in session.analysis_result["results"]:
                if str(res.get("account_id")) == entity_id_str:
                    evidence["found"] = True
                    evidence["decision"] = {
                        "canonical_risk_score": res.get("risk_score"),
                        "decision": res.get("decision"),
                        "reasoning": res.get("reasoning"),
                        "sub_scores": res.get("sub_scores"),
                    }
                    break

        # 4. Cluster membership
        if session.detected_clusters:
            for cluster in session.detected_clusters:
                members = [str(m) for m in cluster.get("customer_ids", [])]
                if entity_id_str in members:
                    evidence["cluster"] = cluster
                    evidence["found"] = True
                    break

    elif entity_type == "merchant":
        # 1. Risk events / spikes
        if session.merchant_spikes:
            m_spikes = [
                s for s in session.merchant_spikes
                if str(s.get("merchant_id")) == entity_id_str or str(s.get("event_id")) == entity_id_str
            ]
            if m_spikes:
                evidence["found"] = True
                evidence["risk_events"] = m_spikes

        # 2. Transaction stats and recent transactions
        if session.txn_df is not None and not session.txn_df.empty and "merchant_id" in session.txn_df.columns:
            m_txns = session.txn_df[session.txn_df["merchant_id"].astype(str) == entity_id_str]
            if not m_txns.empty:
                evidence["found"] = True
                if "timestamp" in m_txns.columns:
                    m_txns = m_txns.sort_values("timestamp", ascending=False)
                evidence["recent_transactions"] = m_txns.head(20).to_dict(orient="records")
                evidence["transaction_stats"] = {
                    "txn_count": int(len(m_txns)),
                    "total_amount": float(m_txns["amount"].sum()) if "amount" in m_txns.columns else 0.0,
                    "avg_amount": float(m_txns["amount"].mean()) if "amount" in m_txns.columns else 0.0,
                }

    elif entity_type == "cluster":
        # 1. Cluster detail
        if session.detected_clusters:
            for cluster in session.detected_clusters:
                if str(cluster.get("cluster_id")) == entity_id_str:
                    evidence["found"] = True
                    evidence["cluster"] = cluster
                    members = [str(m) for m in cluster.get("customer_ids", [])]
                    if session.txn_df is not None and not session.txn_df.empty and members:
                        col = None
                        if "customer_id" in session.txn_df.columns:
                            col = "customer_id"
                        elif "account_id" in session.txn_df.columns:
                            col = "account_id"
                        if col:
                            c_txns = session.txn_df[session.txn_df[col].astype(str).isin(members)]
                            if not c_txns.empty:
                                if "timestamp" in c_txns.columns:
                                    c_txns = c_txns.sort_values("timestamp", ascending=False)
                                evidence["recent_transactions"] = c_txns.head(20).to_dict(orient="records")
                    break

    return evidence
