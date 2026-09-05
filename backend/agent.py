"""
Fraud-Spike Sentinel — Evidence-Grounded Investigation Agent

Generates audit-ready investigation memos using evidence from the active session.
The agent explains decisions; it does not invent evidence.
LLM integration is optional — the core memo works without an API key.
"""

import os
from datetime import datetime

from backend.policy_engine import evaluate_policy, POLICY_VERSION


def generate_investigation_memo(entity_id, entity_type="account", risk_score=None, context=None, session=None):
    """Generate an evidence-grounded risk investigation memo.

    Args:
        entity_id: The entity to investigate.
        entity_type: 'account', 'merchant', or 'cluster'.
        risk_score: Optional override risk score.
        context: Optional dict of additional context.
        session: Active SessionState object (in-memory evidence source).
    """
    entity_type = (entity_type or "account").strip().lower()
    if context is None:
        context = {}

    # Evidence from session (in-memory)
    from backend.session import get_session_entity_evidence
    evidence = get_session_entity_evidence(session, entity_id, entity_type)

    if not evidence.get("found"):
        return (
            "Investigation unavailable: insufficient evidence.\n\n"
            f"No records found for {entity_type} '{entity_id}' in the current session analysis.\n"
            "Upload a CSV containing this entity and run analysis first."
        )

    context = _build_context_from_evidence(evidence, context)

    if risk_score is None:
        decision_row = evidence.get("decision")
        if decision_row and decision_row.get("canonical_risk_score") is not None:
            risk_score = float(decision_row["canonical_risk_score"])
        elif entity_type == "cluster" and evidence.get("cluster"):
            risk_score = float(evidence["cluster"].get("risk_score", 0.5))
        elif entity_type == "merchant" and evidence.get("risk_events"):
            event = evidence["risk_events"][0]
            fold = float(event.get("fold_increase") or 2.0)
            risk_score = min(0.95, max(0.40, 0.30 + fold * 0.1))
        elif evidence.get("recent_transactions"):
            txns = evidence["recent_transactions"]
            failed = sum(1 for t in txns if t.get("status") in ("failed", "declined", "disputed"))
            risk_score = min(0.90, max(0.10, failed / max(1, len(txns))))
        else:
            return (
                "Investigation unavailable: insufficient evidence.\n\n"
                f"Records exist for '{entity_id}' but no scored risk decision was found.\n"
                "Run a full analysis with model scoring enabled, or check that account_id is present."
            )

    policy_res = evaluate_policy(risk_score, context)
    decision = policy_res["decision"]
    reasoning = policy_res["reasoning"]

    llm_key = os.getenv("BURSTGUARD_LLM_API_KEY")
    if llm_key:
        try:
            return _generate_llm_memo(entity_id, entity_type, risk_score, decision, reasoning, context, evidence, llm_key)
        except Exception as e:
            print(f"[Agent] LLM call failed ({str(e)}), falling back to grounded template memo.")

    return _generate_template_memo(entity_id, entity_type, risk_score, decision, reasoning, context, evidence)


def _build_context_from_evidence(evidence, context):
    merged = dict(context)
    customer = evidence.get("customer") or {}
    cluster = evidence.get("cluster") or {}
    decision = evidence.get("decision") or {}
    txns = evidence.get("recent_transactions") or []

    if customer.get("region"):
        merged.setdefault("region", customer["region"])
    if customer.get("primary_device"):
        merged.setdefault("primary_device", customer["primary_device"])

    if txns:
        merged.setdefault("payment_method", txns[0].get("payment_method"))
        merged.setdefault("device_id", txns[0].get("device_id"))
        merged.setdefault("amount", txns[0].get("amount"))
        if "status" in txns[0]:
            failed = sum(1 for t in txns if t.get("status") in ("failed", "declined", "disputed"))
            merged.setdefault("failure_rate", failed / len(txns))

    if cluster:
        merged["is_cluster_member"] = True
        merged.setdefault("cluster_id", cluster.get("cluster_id"))
        merged.setdefault("cluster_risk", cluster.get("risk_score", 0.0))

    if evidence.get("risk_events"):
        merged["is_merchant_spike"] = True

    sub_scores = decision.get("sub_scores")
    if isinstance(sub_scores, dict):
        merged.setdefault("sub_scores", sub_scores)

    return merged


def _generate_template_memo(entity_id, entity_type, risk_score, decision, reasoning, context, evidence):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    sub_scores = context.get("sub_scores", {})

    dev_id = context.get("primary_device") or context.get("device_id")
    pay_method = context.get("payment_method")
    txns = evidence.get("recent_transactions") or []
    amt = context.get("amount")
    region = context.get("region")
    fail_rate = context.get("failure_rate")

    dev_str = dev_id if dev_id else "Not available"
    pay_str = pay_method if pay_method else "Not available"
    txn_count_str = str(len(txns)) if txns else "Not available"
    amt_str = f"₹{amt:,.2f}" if amt is not None else "Not available"
    region_str = region if region else "Not available"
    fail_str = f"{fail_rate * 100:.1f}%" if fail_rate is not None else "Not available"

    memo = f"""================================================================================
FRAUD-SPIKE SENTINEL — RISK INVESTIGATION MEMORANDUM
Generated: {timestamp}
Policy Version: {POLICY_VERSION} | Model Version: v1.2.0-gbm
Data Source: Current Session Analysis
================================================================================

1. EXECUTIVE RISK SUMMARY
--------------------------------------------------------------------------------
Entity ID:         {entity_id}
Entity Type:       {entity_type.upper()}
Canonical Risk:    {risk_score * 100:.1f}% ({risk_score:.4f})
Policy Decision:   [{decision}]
Primary Reason:    {reasoning}

NOTE: The canonical risk score is a normalized composite of multiple signals.
      It is not a calibrated fraud probability unless ground-truth evaluation
      has been performed on this dataset.

2. OBSERVED EVIDENCE (from current session upload)
--------------------------------------------------------------------------------
• Primary Device:         {dev_str}
• Payment Instrument:     {pay_str}
• Recent Transactions:    {txn_count_str}
• Latest Amount:          {amt_str}
• Geographic Region:      {region_str}
• Failure/Decline Rate:   {fail_str}
"""

    cluster = evidence.get("cluster")
    if cluster:
        memo += f"""• Cluster Membership:     {cluster.get('cluster_id', 'N/A')} ({cluster.get('member_count', 'N/A')} members, risk {cluster.get('risk_score', 0.0):.2f})
"""
        shared_devices = cluster.get("shared_devices") or []
        if shared_devices:
            memo += f"""• Cluster Shared Devices: {', '.join(str(d) for d in shared_devices[:3])}
"""
        shared_instr = cluster.get("shared_instruments") or []
        if shared_instr:
            memo += f"""• Cluster Shared Methods: {', '.join(str(i) for i in shared_instr[:3])}
"""

    merchant_events = evidence.get("risk_events") or []
    if merchant_events:
        event = merchant_events[0]
        fold = event.get("fold_increase")
        baseline = event.get("baseline_rate")
        recent = event.get("recent_rate")
        if fold is not None:
            memo += f"""• Merchant Spike Event:   {fold}× fold increase"""
            if baseline is not None and recent is not None:
                memo += f" (baseline {baseline*100:.1f}% → recent {recent*100:.1f}%)"
            memo += "\n"
        sev = event.get("severity")
        if sev:
            memo += f"""• Spike Severity:         {sev}
"""

    if entity_type == "merchant" and evidence.get("transaction_stats"):
        stats = evidence["transaction_stats"]
        memo += f"""• Monitored Transactions: {stats.get('txn_count', 'N/A')}
"""
        if stats.get("total_amount") is not None and stats["total_amount"] > 0:
            memo += f"""• Processed Volume:       ₹{stats.get('total_amount'):,.2f}
"""

    memo += f"""
3. RISK FUSION BREAKDOWN
--------------------------------------------------------------------------------
"""
    if sub_scores:
        memo += f"""• Supervised ML Risk Score:     {(sub_scores.get('ml_probability') or sub_scores.get('supervised_ml_score') or 0.0) * 100:.1f}%
• Isolation Forest Anomaly:     {(sub_scores.get('anomaly_score') or sub_scores.get('unsupervised_anomaly_score') or 0.0) * 100:.1f}%
• Network Cluster Risk:          {(sub_scores.get('cluster_risk') or sub_scores.get('cluster_risk_score') or 0.0) * 100:.1f}%
• Temporal Spike Risk:           {(sub_scores.get('temporal_spike_risk') or 0.0) * 100:.1f}%
"""
    else:
        memo += "• Sub-score breakdown: Not available for this finding type.\n"

    memo += f"""
4. RECOMMENDED ACTION & JUSTIFICATION
--------------------------------------------------------------------------------
Recommended Action: {decision}
"""
    if decision == "BLOCK":
        memo += f"""• Recommend restricting transaction processing for {entity_id} pending manual review.
• Flag associated payment instruments and device IDs in the risk registry.
• Initiate a chargeback risk assessment if transaction volume warrants it.
"""
    elif decision == "REVIEW":
        memo += f"""• Recommend routing {entity_id} to the fraud operations queue for step-up authentication.
• Monitor 24-hour transaction velocity and regional consistency.
• No automated block — this decision requires analyst confirmation before action.
"""
    else:
        memo += f"""• Allow standard transaction processing for {entity_id}.
• Continue standard point-in-time velocity monitoring.
"""

    memo += """
5. IMPORTANT CAVEATS
--------------------------------------------------------------------------------
• Suspicious activity does not confirm fraud. These findings indicate patterns
  that warrant human review, not automated conclusions.
• If no ground-truth labels were provided, precision/recall cannot be estimated.
• Network/cluster membership is supporting context, not standalone evidence of fraud.
================================================================================
"""
    return memo


def _generate_llm_memo(entity_id, entity_type, risk_score, decision, reasoning, context, evidence, api_key):
    import google.generativeai as genai

    # Sanitize evidence before sending to LLM — only include serializable fields
    safe_evidence = {
        "entity_id": evidence.get("entity_id"),
        "entity_type": evidence.get("entity_type"),
        "found": evidence.get("found"),
        "risk_events_count": len(evidence.get("risk_events") or []),
        "recent_txn_count": len(evidence.get("recent_transactions") or []),
        "cluster": {
            "cluster_id": (evidence.get("cluster") or {}).get("cluster_id"),
            "member_count": (evidence.get("cluster") or {}).get("member_count"),
            "risk_score": (evidence.get("cluster") or {}).get("risk_score"),
        } if evidence.get("cluster") else None,
    }

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = (
        f"Generate a concise risk investigation memo for {entity_type} {entity_id}. "
        f"Use ONLY the following verified evidence. Do not invent any facts.\n"
        f"Risk score: {risk_score:.4f}, Decision: {decision}, Reasoning: {reasoning}\n"
        f"Evidence summary: {safe_evidence}\n"
        f"Context: {context}\n"
        f"Note: Describe suspicious activity patterns. Do not claim confirmed fraud unless labels confirm it. "
        f"Use 'Recommended Action' — the system does not execute decisions autonomously."
    )
    res = model.generate_content(prompt)
    return res.text
