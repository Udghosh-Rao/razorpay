"""
Fraud-Spike Sentinel — Temporal Fraud Spike Detector

Detects sudden short-window increases in suspicious activity per merchant/segment
using Wilson score confidence intervals and an optional Benjamini-Hochberg
false-discovery-rate adjustment.
Scans per-merchant temporal activity windows across the simulation timeline.
"""

import numpy as np
import pandas as pd
from scipy import stats
from datetime import datetime, timedelta


def wilson_score_interval(k, n, confidence=0.95):
    """Compute Wilson score interval for k successes in n trials."""
    if n == 0:
        return 0.0, 0.0, 0.0

    z = stats.norm.ppf(1 - (1 - confidence) / 2)
    p_hat = k / n

    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    spread = z * np.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * n)) / n) / denom

    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)

    return lower, upper, center


def detect_merchant_spikes(transactions_df, merchants_df=None,
                          window_hours=24,
                          min_sample_size=10,
                          min_suspicious_count=3,
                          confidence=0.95,
                          baseline_multiplier=2.0,
                          fdr_q=0.05):
    """
    Detect fraud spikes across all merchants by scanning temporal windows.

    Returns:
        List of detected risk event dicts sorted by fold_increase.
    """
    if len(transactions_df) == 0 or "merchant_id" not in transactions_df.columns:
        return []
    label_col = None
    for candidate in ("is_suspicious", "fraud_label", "label", "target"):
        if candidate in transactions_df.columns:
            label_col = candidate
            break

    if label_col is None and "status" not in transactions_df.columns:
        return []

    df = transactions_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    spikes = []

    for merchant_id, group in df.groupby("merchant_id"):
        if len(group) < min_sample_size:
            continue

        # Find peak suspicious 24h window for this merchant
        group = group.sort_values("timestamp")
        min_ts = group["timestamp"].min()
        max_ts = group["timestamp"].max()

        # Step through timeline in window_hours increments
        current = min_ts
        best_spike = None
        max_fold = 0.0

        while current + pd.Timedelta(hours=window_hours) <= max_ts:
            w_end = current + pd.Timedelta(hours=window_hours)
            w_txns = group[(group["timestamp"] >= current) & (group["timestamp"] < w_end)]
            historical_txns = group[group["timestamp"] < current]

            n_window = len(w_txns)
            if n_window >= min_sample_size and len(historical_txns) >= min_sample_size:
                if label_col is not None:
                    k_total = int(historical_txns[label_col].fillna(0).sum())
                else:
                    k_total = int(historical_txns["status"].astype(str).str.lower().isin(["failed", "declined", "disputed"]).sum())
                p_baseline = max(0.01, k_total / len(historical_txns))
                if label_col is not None:
                    k_window = int(w_txns[label_col].fillna(0).sum())
                else:
                    k_window = int(w_txns["status"].astype(str).str.lower().isin(["failed", "declined", "disputed"]).sum())

                if k_window >= min_suspicious_count:
                    recent_rate = k_window / n_window
                    fold = recent_rate / p_baseline

                    if fold >= baseline_multiplier and fold > max_fold:
                        lower_ci, upper_ci, _ = wilson_score_interval(k_window, n_window, confidence)

                        # Test statistic
                        se = np.sqrt(p_baseline * (1 - p_baseline) / n_window)
                        z_stat = (recent_rate - p_baseline) / max(se, 1e-5)
                        p_val = float(1 - stats.norm.cdf(z_stat))

                        best_spike = {
                            "merchant_id": merchant_id,
                            "window_start": current.isoformat(),
                            "window_end": w_end.isoformat(),
                            "recent_txns": n_window,
                            "recent_suspicious": k_window,
                            "recent_rate": round(recent_rate, 4),
                            "baseline_rate": round(p_baseline, 4),
                            "wilson_ci_lower": round(lower_ci, 4),
                            "wilson_ci_upper": round(upper_ci, 4),
                            "fold_increase": round(fold, 2),
                            "p_value": max(1e-10, p_val),
                            "severity": "CRITICAL" if fold >= 5.0 else "HIGH",
                            "event_type": "SUSPICIOUS_ACTIVITY_SPIKE",
                            "description": f"Merchant {merchant_id} experienced a {fold:.1f}x surge in suspicious transactions ({recent_rate*100:.1f}% vs baseline {p_baseline*100:.1f}%)."
                        }
                        if "amount" in w_txns.columns:
                            best_spike["recent_amount_inr"] = round(float(w_txns["amount"].sum()), 2)
                        max_fold = fold

            current += pd.Timedelta(hours=window_hours // 2)

        if best_spike:
            spikes.append(best_spike)

    # Apply Benjamini-Hochberg to the one selected candidate per merchant.
    # Do not claim FDR control unless the adjusted q-values are computed.
    if spikes:
        ordered = sorted(spikes, key=lambda x: x["p_value"])
        total = len(ordered)
        running_min = 1.0
        for rank in range(total, 0, -1):
            candidate = ordered[rank - 1]
            running_min = min(running_min, candidate["p_value"] * total / rank)
            candidate["fdr_q_value"] = round(float(min(1.0, running_min)), 6)
        spikes = [spike for spike in ordered if spike["fdr_q_value"] <= fdr_q]

    return sorted(spikes, key=lambda x: -x["fold_increase"])
