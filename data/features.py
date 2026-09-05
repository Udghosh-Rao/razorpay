"""
Fraud-Spike Sentinel — Point-in-Time Feature Computation

CRITICAL: All features computed using ONLY data available at scoring_time.
No future information leaks. No label-based cutoffs.
Same logic for training, validation, testing, and production scoring.

Feature Groups:
  Velocity:    transactions_last_5m, transactions_last_30m, transactions_last_24h, amount_last_30m
  Customer:    transaction_count, average_amount, amount_std, success_rate, failure_rate,
               days_since_signup, amount_deviation
  Device:      device_customer_count, device_transaction_count, device_merchant_count,
               device_age_days, device_reuse_score
  Instrument:  instrument_customer_count, instrument_failure_rate, instrument_velocity,
               instrument_reuse_score
  Merchant:    merchant_baseline_volume, merchant_current_volume, merchant_baseline_suspicious_rate,
               merchant_current_suspicious_rate, merchant_volume_change, merchant_suspicious_rate_change
  Temporal:    hour_of_day, day_of_week, rolling_failure_rate, rolling_suspicious_rate
  Behavioral:  behavioral_deviation_score, velocity_escalation_interaction,
               amount_trend_slope, amount_acceleration, escalation_discontinuity
"""

import numpy as np
import pandas as pd
from datetime import timedelta
import hashlib


# Feature metadata registry
FEATURE_REGISTRY = {
    "transactions_last_5m":    {"type": "velocity", "version": "v1"},
    "transactions_last_30m":   {"type": "velocity", "version": "v1"},
    "transactions_last_24h":   {"type": "velocity", "version": "v1"},
    "amount_last_30m":         {"type": "velocity", "version": "v1"},
    "txn_count_total":         {"type": "customer", "version": "v1"},
    "average_amount":          {"type": "customer", "version": "v1"},
    "amount_std":              {"type": "customer", "version": "v1"},
    "success_rate":            {"type": "customer", "version": "v1"},
    "failure_rate":            {"type": "customer", "version": "v1"},
    "days_since_signup":       {"type": "customer", "version": "v1"},
    "amount_deviation":        {"type": "customer", "version": "v1"},
    "device_customer_count":   {"type": "device", "version": "v1"},
    "device_transaction_count":{"type": "device", "version": "v1"},
    "device_merchant_count":   {"type": "device", "version": "v1"},
    "device_age_days":         {"type": "device", "version": "v1"},
    "device_reuse_score":      {"type": "device", "version": "v1"},
    "instrument_customer_count":{"type": "instrument", "version": "v1"},
    "instrument_failure_rate": {"type": "instrument", "version": "v1"},
    "instrument_velocity":     {"type": "instrument", "version": "v1"},
    "instrument_reuse_score":  {"type": "instrument", "version": "v1"},
    "merchant_baseline_volume":{"type": "merchant", "version": "v1"},
    "merchant_current_volume": {"type": "merchant", "version": "v1"},
    "merchant_volume_change":  {"type": "merchant", "version": "v1"},
    "hour_of_day":             {"type": "temporal", "version": "v1"},
    "day_of_week":             {"type": "temporal", "version": "v1"},
    "rolling_failure_rate":    {"type": "temporal", "version": "v1"},
    "velocity_ratio":          {"type": "velocity", "version": "v1"},
    "amount_trend_slope":      {"type": "behavioral", "version": "v1"},
    "amount_acceleration":     {"type": "behavioral", "version": "v1"},
    "escalation_discontinuity":{"type": "behavioral", "version": "v1"},
    "velocity_escalation_interaction": {"type": "behavioral", "version": "v1"},
    "behavioral_deviation_score": {"type": "behavioral", "version": "v1"},
    "amount_percentile_vs_cohort": {"type": "customer", "version": "v1"},
    "new_payment_instrument_flag": {"type": "instrument", "version": "v1"},
    "instrument_count":        {"type": "instrument", "version": "v1"},
}

FEATURE_COLS = sorted(FEATURE_REGISTRY.keys())


def compute_cohort_reference_stats(train_features_df):
    """
    Compute population-level statistics from TRAINING data only.
    These are frozen and reused for all future scoring to prevent
    training/serving skew.
    """
    bins = [0, 30, 60, 90, 120, float('inf')]
    labels = ["0-30d", "30-60d", "60-90d", "90-120d", "120d+"]
    df = train_features_df.copy()
    df["age_bucket"] = pd.cut(df["days_since_signup"], bins=bins, labels=labels, right=False)

    cohort_amounts = {
        str(bucket): sorted(group["average_amount"].dropna().tolist())
        for bucket, group in df.groupby("age_bucket", observed=True)
    }

    deviation_features = [
        "velocity_ratio", "average_amount", "amount_std", "amount_trend_slope",
        "amount_acceleration", "escalation_discontinuity", "success_rate",
        "transactions_last_24h", "velocity_escalation_interaction",
    ]
    deviation_stats = {}
    for feat in deviation_features:
        if feat in df.columns:
            std_value = float(df[feat].std(ddof=0)) if len(df[feat]) > 0 else 0.0
            deviation_stats[feat] = {
                "mean": float(df[feat].mean()),
                "std": float(max(std_value, 1e-6)),
            }

    return {"cohort_amounts_by_age_bucket": cohort_amounts, "deviation_stats": deviation_stats}


def compute_features(customers_df, transactions_df, reference_stats=None, scoring_time=None):
    """
    Compute point-in-time features for all customers.

    CRITICAL: scoring_time determines the information cutoff.
    For each customer, only transactions at or before scoring_time are used.
    If scoring_time is None, a deterministic per-customer cutoff is used.

    Args:
        customers_df: Customer metadata
        transactions_df: All transactions
        reference_stats: Frozen cohort statistics from training (prevents skew)
        scoring_time: Global scoring cutoff timestamp (optional)

    Returns:
        DataFrame with one row per customer, all features computed point-in-time.
    """
    features_list = []

    txn_df = transactions_df.copy()
    if "txn_id" not in txn_df.columns:
        txn_df["txn_id"] = [f"txn_{i}" for i in range(len(txn_df))]
    txn_df["timestamp"] = pd.to_datetime(txn_df["timestamp"])
    cust_df = customers_df.copy()
    cust_df["signup_date"] = pd.to_datetime(cust_df["signup_date"])

    for _, customer in cust_df.iterrows():
        cust_id = customer["customer_id"]
        signup = customer["signup_date"]

        # Get this customer's transactions
        cust_txns = txn_df[txn_df["customer_id"] == cust_id].sort_values("timestamp")

        if len(cust_txns) == 0:
            features_list.append(_empty_features(cust_id))
            continue

        # Determine scoring cutoff — NO LABEL LEAKAGE
        if scoring_time is not None:
            cutoff_ts = pd.to_datetime(scoring_time)
        else:
            # Deterministic, label-independent cutoff based on customer_id hash
            cutoff_days = _customer_scoring_cutoff_days(cust_id)
            cutoff_ts = signup + pd.Timedelta(days=cutoff_days)

        # Only use transactions at or before cutoff
        scoring_txns = cust_txns[cust_txns["timestamp"] <= cutoff_ts]

        if len(scoring_txns) == 0:
            features_list.append(_empty_features(cust_id))
            continue

        # Every aggregate must use the same information cutoff as the
        # evaluated customer. Using the full dataset here leaks future
        # device, instrument, and merchant activity into historical features.
        eligible_txns = txn_df[txn_df["timestamp"] <= cutoff_ts]
        device_stats = _compute_device_stats(eligible_txns)
        instrument_stats = _compute_instrument_stats(eligible_txns)
        merchant_stats = _compute_merchant_stats(eligible_txns)

        features = _compute_customer_features(
            cust_id, signup, scoring_txns, cutoff_ts,
            device_stats, instrument_stats, merchant_stats
        )
        features_list.append(features)

    features_df = pd.DataFrame(features_list)

    # Add cohort-relative features
    features_df = _add_cohort_features(features_df, reference_stats)
    features_df = _add_deviation_score(features_df, reference_stats)

    # Guard against sparse or single-row uploads producing NaN/inf values.
    numeric_cols = features_df.select_dtypes(include=[np.number]).columns
    features_df[numeric_cols] = features_df[numeric_cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    return features_df


def _customer_scoring_cutoff_days(customer_id):
    """
    Deterministic, label-independent scoring cutoff (10-50 days).
    Uses hash of customer_id for reproducibility.
    """
    h = int(hashlib.md5(customer_id.encode()).hexdigest()[:8], 16)
    return 10 + (h % 41)


def _compute_device_stats(txn_df):
    """Global device-level statistics from eligible transactions."""
    if len(txn_df) == 0 or "device_id" not in txn_df.columns:
        return {}
    valid = txn_df.dropna(subset=["device_id"])
    if len(valid) == 0:
        return {}
    agg_kwargs = {
        "txn_count": ("txn_id", "count"),
        "first_seen": ("timestamp", "min"),
    }
    if "customer_id" in valid.columns:
        agg_kwargs["customer_count"] = ("customer_id", "nunique")
    if "merchant_id" in valid.columns:
        agg_kwargs["merchant_count"] = ("merchant_id", "nunique")

    stats = valid.groupby("device_id").agg(**agg_kwargs).to_dict("index")
    for s in stats.values():
        if "customer_count" not in s:
            s["customer_count"] = 1
        if "merchant_count" not in s:
            s["merchant_count"] = 0
    return stats


def _compute_instrument_stats(txn_df):
    """Global payment instrument statistics from eligible transactions."""
    if len(txn_df) == 0 or "payment_method" not in txn_df.columns:
        return {}
    valid = txn_df.dropna(subset=["payment_method"])
    if len(valid) == 0:
        return {}

    grouped = valid.groupby("payment_method")
    stats = {}
    for inst, group in grouped:
        total = len(group)
        failures = (group["status"].isin(["failed", "declined"])).sum() if "status" in group.columns else 0
        stats[inst] = {
            "customer_count": group["customer_id"].nunique(),
            "failure_rate": float(failures / max(1, total)),
            "velocity": float(total),
        }
    return stats


def _compute_merchant_stats(txn_df):
    """Global merchant-level statistics."""
    if len(txn_df) == 0 or "merchant_id" not in txn_df.columns:
        return {}
    valid = txn_df.dropna(subset=["merchant_id"])
    if len(valid) == 0:
        return {}

    grouped = valid.groupby("merchant_id")
    stats = {}
    for merch, group in grouped:
        stats[merch] = {
            "total_volume": len(group),
            "avg_amount": float(group["amount"].mean()) if "amount" in group.columns else 0,
        }
    return stats


def _compute_customer_features(cust_id, signup_date, txns, scoring_time,
                                device_stats, instrument_stats, merchant_stats):
    """Compute all features for a single customer using only point-in-time data."""
    now = scoring_time
    age_days = max(0, (now - signup_date).total_seconds() / 86400)

    valid_txns = txns.dropna(subset=["amount"])
    amounts = valid_txns["amount"].values if len(valid_txns) > 0 else np.array([0.0])

    txn_count = len(txns)

    # ─── Velocity features ────────────────────────────────────────────
    last_5m = txns[txns["timestamp"] >= (now - timedelta(minutes=5))]
    last_30m = txns[txns["timestamp"] >= (now - timedelta(minutes=30))]
    last_24h = txns[txns["timestamp"] >= (now - timedelta(hours=24))]
    last_7d = txns[txns["timestamp"] >= (now - timedelta(days=7))]

    transactions_last_5m = len(last_5m)
    transactions_last_30m = len(last_30m)
    transactions_last_24h = len(last_24h)
    amount_last_30m = float(last_30m["amount"].sum()) if len(last_30m) > 0 else 0.0

    # Velocity ratio
    total_days = max(1, age_days)
    baseline_rate = txn_count / total_days
    recent_rate = len(last_7d) / 7.0 if total_days > 7 else len(last_24h) / max(1, min(total_days, 1))
    velocity_ratio = recent_rate / max(0.01, baseline_rate)

    # ─── Customer features ────────────────────────────────────────────
    average_amount = float(np.mean(amounts))
    amount_std = float(np.std(amounts)) if len(amounts) > 1 else 0.0

    if "status" in txns.columns:
        status_txns = txns.dropna(subset=["status"])
        if len(status_txns) > 0:
            success_count = (status_txns["status"].astype(str).str.lower().isin({"success", "succeeded", "settled", "completed"})).sum()
            failure_count = len(status_txns) - success_count
            success_rate = float(success_count / len(status_txns))
            failure_rate = float(failure_count / len(status_txns))
        else:
            success_rate = 1.0
            failure_rate = 0.0
    else:
        success_rate = 1.0
        failure_rate = 0.0

    # ─── Device features ─────────────────────────────────────────────
    primary_device = txns["device_id"].mode().iloc[0] if "device_id" in txns.columns and len(txns["device_id"].dropna()) > 0 else None
    if primary_device and primary_device in device_stats:
        ds = device_stats[primary_device]
        device_customer_count = ds["customer_count"]
        device_transaction_count = ds["txn_count"]
        device_merchant_count = ds["merchant_count"]
        device_first_seen = ds["first_seen"]
        device_age_days = max(0, (now - device_first_seen).total_seconds() / 86400)
        device_reuse_score = min(1.0, (device_customer_count - 1) / 5.0)
    else:
        device_customer_count = 1
        device_transaction_count = 0
        device_merchant_count = 0
        device_age_days = age_days
        device_reuse_score = 0.0

    # ─── Instrument features ─────────────────────────────────────────
    instruments = txns["payment_method"].dropna().unique() if "payment_method" in txns.columns else []
    instrument_count = len(instruments)
    primary_instrument = txns["payment_method"].mode().iloc[0] if len(instruments) > 0 else None

    if primary_instrument and primary_instrument in instrument_stats:
        ist = instrument_stats[primary_instrument]
        instrument_customer_count = ist["customer_count"]
        instrument_failure_rate = ist["failure_rate"]
        instrument_velocity = ist["velocity"]
        instrument_reuse_score = min(1.0, (instrument_customer_count - 1) / 10.0)
    else:
        instrument_customer_count = 1
        instrument_failure_rate = 0.0
        instrument_velocity = 0.0
        instrument_reuse_score = 0.0

    # New instrument in last 7 days
    early_txns = txns[txns["timestamp"] < (now - timedelta(days=7))]
    early_instruments = set(early_txns["payment_method"].dropna().unique()) if "payment_method" in early_txns.columns else set()
    recent_instruments = set(last_7d["payment_method"].dropna().unique()) if "payment_method" in last_7d.columns else set()
    new_payment_instrument_flag = 1 if recent_instruments - early_instruments else 0

    # ─── Merchant features ───────────────────────────────────────────
    if "merchant_id" in txns.columns:
        primary_merchant = txns["merchant_id"].mode().iloc[0] if len(txns["merchant_id"].dropna()) > 0 else None
        if primary_merchant and primary_merchant in merchant_stats:
            ms = merchant_stats[primary_merchant]
            merchant_baseline_volume = ms["total_volume"]
            merchant_current_volume = len(txns[txns["merchant_id"] == primary_merchant])
            merchant_volume_change = merchant_current_volume / max(1, merchant_baseline_volume)
        else:
            merchant_baseline_volume = 0
            merchant_current_volume = 0
            merchant_volume_change = 1.0
    else:
        merchant_baseline_volume = 0
        merchant_current_volume = 0
        merchant_volume_change = 1.0

    # ─── Temporal features ───────────────────────────────────────────
    last_txn = txns.iloc[-1]
    last_ts = last_txn["timestamp"]
    hour_of_day = last_ts.hour
    day_of_week = last_ts.dayofweek

    # Rolling failure rate (last 7 days)
    if len(last_7d) > 0 and "status" in last_7d.columns:
        rolling_failures = (last_7d["status"].isin(["failed", "declined"])).sum()
        rolling_failure_rate = float(rolling_failures / len(last_7d))
    else:
        rolling_failure_rate = 0.0

    # ─── Behavioral features ─────────────────────────────────────────
    amount_trend_slope = _compute_trend_slope(valid_txns)
    amount_acceleration = _compute_amount_acceleration(valid_txns)
    escalation_disc = _compute_escalation_discontinuity(valid_txns)
    velocity_escalation_interaction = round(velocity_ratio * escalation_disc, 4)
    amount_deviation = abs(average_amount - float(np.median(amounts))) if len(amounts) > 1 else 0.0

    return {
        "customer_id": cust_id,
        # Velocity
        "transactions_last_5m": transactions_last_5m,
        "transactions_last_30m": transactions_last_30m,
        "transactions_last_24h": transactions_last_24h,
        "amount_last_30m": round(amount_last_30m, 2),
        "velocity_ratio": round(velocity_ratio, 4),
        # Customer
        "txn_count_total": txn_count,
        "average_amount": round(average_amount, 2),
        "amount_std": round(amount_std, 2),
        "success_rate": round(success_rate, 4),
        "failure_rate": round(failure_rate, 4),
        "days_since_signup": round(age_days, 2),
        "amount_deviation": round(amount_deviation, 2),
        # Device
        "device_customer_count": device_customer_count,
        "device_transaction_count": device_transaction_count,
        "device_merchant_count": device_merchant_count,
        "device_age_days": round(device_age_days, 2),
        "device_reuse_score": round(device_reuse_score, 4),
        # Instrument
        "instrument_customer_count": instrument_customer_count,
        "instrument_failure_rate": round(instrument_failure_rate, 4),
        "instrument_velocity": round(instrument_velocity, 2),
        "instrument_reuse_score": round(instrument_reuse_score, 4),
        "new_payment_instrument_flag": new_payment_instrument_flag,
        "instrument_count": instrument_count,
        # Merchant
        "merchant_baseline_volume": merchant_baseline_volume,
        "merchant_current_volume": merchant_current_volume,
        "merchant_volume_change": round(merchant_volume_change, 4),
        # Temporal
        "hour_of_day": hour_of_day,
        "day_of_week": day_of_week,
        "rolling_failure_rate": round(rolling_failure_rate, 4),
        # Behavioral
        "amount_trend_slope": round(amount_trend_slope, 4),
        "amount_acceleration": round(amount_acceleration, 4),
        "escalation_discontinuity": round(escalation_disc, 4),
        "velocity_escalation_interaction": velocity_escalation_interaction,
    }


def _compute_trend_slope(txns):
    if len(txns) < 3:
        return 0.0
    valid = txns.dropna(subset=["amount"]).copy()
    if len(valid) < 3:
        return 0.0
    t0 = valid["timestamp"].min()
    days = (valid["timestamp"] - t0).dt.total_seconds() / 86400
    if days.std() == 0:
        return 0.0
    try:
        slope, _ = np.polyfit(days.values, valid["amount"].values, 1)
        return float(slope)
    except (np.linalg.LinAlgError, ValueError):
        return 0.0


def _compute_amount_acceleration(txns):
    if len(txns) < 6:
        return 0.0
    valid = txns.dropna(subset=["amount"]).sort_values("timestamp")
    if len(valid) < 6:
        return 0.0
    mid = len(valid) // 2
    slope1 = _compute_trend_slope(valid.iloc[:mid])
    slope2 = _compute_trend_slope(valid.iloc[mid:])
    return float(slope2 - slope1)


def _compute_escalation_discontinuity(txns):
    if len(txns) < 5:
        return 1.0
    valid = txns.dropna(subset=["amount"]).sort_values("timestamp")
    if len(valid) < 5:
        return 1.0

    t0 = valid["timestamp"].min()
    valid = valid.copy()
    valid["day_offset"] = (valid["timestamp"] - t0).dt.total_seconds() / 86400
    total_days = valid["day_offset"].max()

    if total_days < 14:
        n = len(valid)
        split = max(1, 2 * n // 3)
        recent = valid.tail(n - split)["amount"]
        early = valid.head(split)["amount"]
        if len(recent) == 0 or len(early) == 0:
            return 1.0
        early_std = early.std()
        if early_std > 0:
            return float((recent.mean() - early.mean()) / early_std)
        else:
            return float(recent.mean() / max(1.0, early.mean()))

    window_size = 7
    window_means = []
    for start in np.arange(0, total_days - window_size + 1, window_size):
        window = valid[(valid["day_offset"] >= start) & (valid["day_offset"] < start + window_size)]
        if len(window) > 0:
            window_means.append(window["amount"].mean())

    if len(window_means) < 2:
        return 1.0
    recent = window_means[-1]
    prior = window_means[:-1]
    p90 = np.percentile(prior, 90)
    return float(recent / max(1.0, p90))


def _add_cohort_features(features_df, reference_stats=None):
    merged = features_df.copy()
    bins = [0, 30, 60, 90, 120, float('inf')]
    labels = ["0-30d", "30-60d", "60-90d", "90-120d", "120d+"]

    if reference_stats is None:
        merged["age_bucket"] = pd.cut(merged["days_since_signup"], bins=bins, labels=labels, right=False)

        def cohort_percentile(group):
            if len(group) < 2:
                group["amount_percentile_vs_cohort"] = 0.5
            else:
                group["amount_percentile_vs_cohort"] = group["average_amount"].rank(pct=True)
            return group

        merged = merged.groupby("age_bucket", group_keys=False, observed=True).apply(cohort_percentile, include_groups=False)
        merged = merged.drop(columns=["age_bucket"], errors="ignore")
    else:
        cohort_amounts = reference_stats["cohort_amounts_by_age_bucket"]

        def _age_bucket(age):
            if age < 30: return "0-30d"
            elif age < 60: return "30-60d"
            elif age < 90: return "60-90d"
            elif age < 120: return "90-120d"
            else: return "120d+"

        def pct(row):
            bucket = _age_bucket(row["days_since_signup"])
            ref = cohort_amounts.get(bucket, [])
            return float(np.searchsorted(ref, row["average_amount"]) / len(ref)) if ref else 0.5

        merged["amount_percentile_vs_cohort"] = merged.apply(pct, axis=1)

    return merged


def _add_deviation_score(features_df, reference_stats=None):
    deviation_features = [
        "velocity_ratio", "average_amount", "amount_std",
        "amount_trend_slope", "amount_acceleration", "escalation_discontinuity",
        "success_rate", "transactions_last_24h", "velocity_escalation_interaction",
    ]
    weights = {
        "velocity_ratio": 1.5,
        "average_amount": 1.0,
        "amount_std": 0.8,
        "amount_trend_slope": 1.2,
        "amount_acceleration": 1.3,
        "escalation_discontinuity": 2.0,
        "success_rate": 1.0,
        "transactions_last_24h": 0.8,
        "velocity_escalation_interaction": 1.8,
    }

    eps = 1e-6
    df = features_df.copy()
    scores = np.zeros(len(df))

    for feat in deviation_features:
        if feat in df.columns:
            if reference_stats is None:
                mu = df[feat].mean()
                sigma = max(float(df[feat].std(ddof=0)) if len(df[feat]) > 0 else 0.0, eps)
            else:
                stats = reference_stats.get("deviation_stats", {}).get(feat, {})
                if not stats:
                    continue
                mu = float(stats["mean"])
                sigma = max(float(stats["std"]), eps)
            w = weights.get(feat, 1.0)
            scores += w * np.abs(df[feat].fillna(0) - mu) / (sigma + eps)

    df["behavioral_deviation_score"] = np.round(scores, 4)
    return df


def _empty_features(customer_id):
    return {
        "customer_id": customer_id,
        "transactions_last_5m": 0, "transactions_last_30m": 0,
        "transactions_last_24h": 0, "amount_last_30m": 0,
        "velocity_ratio": 0, "txn_count_total": 0,
        "average_amount": 0, "amount_std": 0,
        "success_rate": 1.0, "failure_rate": 0.0,
        "days_since_signup": 0, "amount_deviation": 0,
        "device_customer_count": 0, "device_transaction_count": 0,
        "device_merchant_count": 0, "device_age_days": 0,
        "device_reuse_score": 0, "instrument_customer_count": 0,
        "instrument_failure_rate": 0, "instrument_velocity": 0,
        "instrument_reuse_score": 0, "new_payment_instrument_flag": 0,
        "instrument_count": 0, "merchant_baseline_volume": 0,
        "merchant_current_volume": 0, "merchant_volume_change": 1.0,
        "hour_of_day": 0, "day_of_week": 0, "rolling_failure_rate": 0,
        "amount_trend_slope": 0, "amount_acceleration": 0,
        "escalation_discontinuity": 1.0,
        "velocity_escalation_interaction": 0,
        "amount_percentile_vs_cohort": 0.5,
        "behavioral_deviation_score": 0,
    }
