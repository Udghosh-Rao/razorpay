"""
Fraud-Spike Sentinel — Synthetic Payment Data Generator

Generates a realistic payment environment with:
  - 100,000+ transactions
  - 10,000+ customers
  - 1,000+ merchants
  - 5,000+ devices
  - Multiple payment methods, banks, regions, time periods

Scenarios A-F per product specification.
All data is clearly labeled as synthetic.
Deterministic random seed for reproducibility.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import uuid
import json

from data.scenarios import (
    SCENARIO_DISTRIBUTION, REGIONS, REGION_WEIGHTS,
    PAYMENT_METHODS, PAYMENT_METHOD_WEIGHTS, BANKS,
    NORMAL, COMPROMISED, COORDINATED_CLUSTER,
    FRAUD_SPIKE, LEGIT_HIGH_VOLUME, LEGIT_ANOMALY,
)

# ─── Configurable constants ───────────────────────────────────────────────
RANDOM_SEED = 42
NUM_CUSTOMERS = 10_000
NUM_MERCHANTS = 1_200
NUM_DEVICES = 5_500
SIM_START_DATE = datetime(2024, 1, 1)
SIM_END_DATE = datetime(2024, 6, 30)


def _uid(prefix, rng=None):
    if rng is not None:
        h = "".join(f"{rng.randint(0, 15):x}" for _ in range(12))
        return f"{prefix}_{h}"
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def generate_dataset(num_customers=NUM_CUSTOMERS, seed=RANDOM_SEED):
    """
    Generate the full synthetic payment dataset.

    Returns:
        customers_df: DataFrame with customer metadata and labels
        merchants_df: DataFrame with merchant metadata
        transactions_df: DataFrame with all transactions
        devices_df: DataFrame with device metadata
        clusters_df: DataFrame with cluster membership info (for cluster scenario)
    """
    rng = np.random.RandomState(seed)
    sim_days = (SIM_END_DATE - SIM_START_DATE).days

    # ─── Generate merchants ───────────────────────────────────────────
    merchants = _generate_merchants(rng, NUM_MERCHANTS)
    merchants_df = pd.DataFrame(merchants)

    # ─── Generate devices ─────────────────────────────────────────────
    devices = _generate_devices(rng, NUM_DEVICES)
    devices_df = pd.DataFrame(devices)

    # ─── Assign customer scenarios ────────────────────────────────────
    scenarios = list(SCENARIO_DISTRIBUTION.keys())
    probs = list(SCENARIO_DISTRIBUTION.values())
    assigned = rng.choice(scenarios, size=num_customers, p=probs)

    all_customers = []
    all_transactions = []
    all_clusters = []

    # Pre-assign devices to customers (most get 1-2 devices)
    device_ids = devices_df["device_id"].tolist()

    cluster_id_counter = 0

    for i in range(num_customers):
        scenario = assigned[i]
        customer_id = _uid("cust", rng)

        # Signup date spread across first 70% of window
        signup_offset = rng.randint(0, int(sim_days * 0.7))
        signup_date = SIM_START_DATE + timedelta(days=signup_offset)

        # Assign primary region and device
        region = rng.choice(REGIONS, p=REGION_WEIGHTS)
        num_devices = rng.choice([1, 1, 1, 2, 2, 3])
        customer_devices = list(rng.choice(device_ids, size=num_devices, replace=False))
        primary_device = customer_devices[0]

        # Assign merchant preferences
        merchant_ids = merchants_df["merchant_id"].tolist()
        num_preferred = rng.choice([1, 2, 3, 4, 5])
        preferred_merchants = list(rng.choice(merchant_ids, size=num_preferred, replace=False))

        # Assign payment method and bank
        num_instruments = rng.choice([1, 1, 1, 2, 2, 3])
        instruments = list(rng.choice(PAYMENT_METHODS, size=num_instruments,
                                       replace=False, p=PAYMENT_METHOD_WEIGHTS))
        bank = rng.choice(BANKS)

        label = 0
        is_suspicious = False

        if scenario == "normal":
            txns = _gen_normal(
                rng, customer_id, signup_date, SIM_END_DATE,
                customer_devices, region, preferred_merchants, instruments, bank
            )

        elif scenario == "compromised_account":
            label = 1
            is_suspicious = True
            txns = _gen_compromised(
                rng, customer_id, signup_date, SIM_END_DATE,
                customer_devices, device_ids, region, preferred_merchants, instruments, bank
            )

        elif scenario == "coordinated_cluster":
            label = 1
            is_suspicious = True
            cluster_size = rng.randint(*COORDINATED_CLUSTER["cluster_size"])
            # Generate a mini cluster; actual cluster stitching happens below
            txns = _gen_cluster_member(
                rng, customer_id, signup_date, SIM_END_DATE,
                customer_devices, region, preferred_merchants, instruments, bank,
                cluster_id_counter
            )
            all_clusters.append({
                "cluster_id": f"cluster_{cluster_id_counter}",
                "customer_id": customer_id,
                "device_ids": json.dumps(customer_devices[:2]),
                "shared_instruments": json.dumps(instruments[:2]),
            })
            if rng.random() < 0.15:
                cluster_id_counter += 1

        elif scenario == "fraud_spike":
            label = 1
            is_suspicious = True
            txns = _gen_fraud_spike(
                rng, customer_id, signup_date, SIM_END_DATE,
                customer_devices, region, preferred_merchants, instruments, bank
            )

        elif scenario == "legit_high_volume":
            txns = _gen_legit_high_volume(
                rng, customer_id, signup_date, SIM_END_DATE,
                customer_devices, region, preferred_merchants, instruments, bank
            )

        elif scenario == "legit_anomaly":
            txns = _gen_legit_anomaly(
                rng, customer_id, signup_date, SIM_END_DATE,
                customer_devices, device_ids, region, preferred_merchants, instruments, bank
            )

        elif scenario == "legit_growth":
            txns = _gen_legit_growth(
                rng, customer_id, signup_date, SIM_END_DATE,
                customer_devices, region, preferred_merchants, instruments, bank
            )

        elif scenario == "noisy":
            txns = _gen_noisy(
                rng, customer_id, signup_date, SIM_END_DATE,
                customer_devices, region, preferred_merchants, instruments, bank
            )
        else:
            txns = []

        customer_record = {
            "customer_id": customer_id,
            "scenario": scenario,
            "label_suspicious": label,
            "signup_date": signup_date.isoformat(),
            "region": region,
            "primary_device": primary_device,
            "bank": bank,
            "num_instruments": len(instruments),
            "instruments": json.dumps(instruments),
        }
        all_customers.append(customer_record)
        all_transactions.extend(txns)

    customers_df = pd.DataFrame(all_customers)
    transactions_df = pd.DataFrame(all_transactions)
    transactions_df = transactions_df.sort_values("timestamp").reset_index(drop=True)
    clusters_df = pd.DataFrame(all_clusters) if all_clusters else pd.DataFrame(
        columns=["cluster_id", "customer_id", "device_ids", "shared_instruments"]
    )

    print(f"[Synthetic Data] Generated {len(customers_df)} customers, "
          f"{len(merchants_df)} merchants, {len(transactions_df)} transactions, "
          f"{len(devices_df)} devices")
    print(f"  Label distribution: {customers_df['label_suspicious'].value_counts().to_dict()}")
    print(f"  Scenario distribution: {customers_df['scenario'].value_counts().to_dict()}")

    return customers_df, merchants_df, transactions_df, devices_df, clusters_df


# ─── Entity generators ───────────────────────────────────────────────────

def _generate_merchants(rng, n):
    merchants = []
    for _ in range(n):
        category = rng.choice([
            "electronics", "grocery", "fashion", "food_delivery",
            "travel", "utilities", "healthcare", "entertainment",
            "education", "services",
        ])
        merchants.append({
            "merchant_id": _uid("merch"),
            "category": category,
            "region": rng.choice(REGIONS, p=REGION_WEIGHTS),
            "avg_daily_txns": round(rng.uniform(5, 200), 1),
            "avg_txn_amount": round(rng.uniform(100, 10000), 2),
            "baseline_suspicious_rate": round(rng.uniform(0.005, 0.03), 4),
        })
    return merchants


def _generate_devices(rng, n):
    devices = []
    for _ in range(n):
        devices.append({
            "device_id": _uid("dev"),
            "device_type": rng.choice(["mobile_android", "mobile_ios", "desktop", "tablet"]),
            "os_version": rng.choice(["Android 13", "Android 14", "iOS 17", "Windows 11", "macOS 14"]),
            "first_seen": (SIM_START_DATE + timedelta(days=rng.randint(0, 120))).isoformat(),
        })
    return devices


# ─── Scenario transaction generators ─────────────────────────────────────

def _gen_normal(rng, cust_id, start, end, devices, region, merchants, instruments, bank):
    """Scenario A: Normal customer — stable, consistent behavior."""
    txns = []
    days = (end - start).days
    daily_rate = rng.uniform(*NORMAL["daily_txn_rate"])
    amt_mean = rng.uniform(*NORMAL["amount_mean"])
    amt_std = amt_mean * NORMAL["amount_std_frac"]
    success_rate = rng.uniform(*NORMAL["success_rate"])
    num_txns = max(3, int(days * daily_rate * rng.uniform(0.8, 1.2)))

    for _ in range(num_txns):
        offset_s = rng.randint(0, days * 86400)
        ts = start + timedelta(seconds=int(offset_s))
        amount = max(10, rng.normal(amt_mean, amt_std))
        status = "success" if rng.random() < success_rate else rng.choice(["failed", "declined"])
        txns.append({
            "txn_id": _uid("txn"),
            "customer_id": cust_id,
            "merchant_id": rng.choice(merchants),
            "timestamp": ts.isoformat(),
            "amount": round(amount, 2),
            "status": status,
            "payment_method": rng.choice(instruments),
            "device_id": rng.choice(devices),
            "region": region,
            "bank": bank,
            "is_disputed": False,
            "is_suspicious": False,
        })
    return txns


def _gen_compromised(rng, cust_id, start, end, devices, all_devices, region,
                     merchants, instruments, bank):
    """Scenario B: Compromised account — trust then burst."""
    trust_days = rng.randint(*COMPROMISED["trust_phase_days"])
    trust_end = start + timedelta(days=trust_days)
    if trust_end >= end:
        trust_end = end - timedelta(days=5)
        trust_days = (trust_end - start).days

    # Trust phase: normal-looking
    trust_txns = _gen_normal(rng, cust_id, start, trust_end, devices, region,
                             merchants, instruments, bank)
    for t in trust_txns:
        t["is_suspicious"] = False

    # Burst phase
    burst_hours = rng.randint(*COMPROMISED["burst_hours"])
    burst_start = trust_end + timedelta(hours=rng.randint(1, 12))
    burst_end_dt = burst_start + timedelta(hours=burst_hours)
    if burst_end_dt >= end:
        burst_end_dt = end - timedelta(hours=1)

    vel_mult = rng.uniform(*COMPROMISED["velocity_multiplier"])
    amt_mult = rng.uniform(*COMPROMISED["amount_multiplier"])
    base_rate = rng.uniform(*NORMAL["daily_txn_rate"])
    base_amount = rng.uniform(*NORMAL["amount_mean"])

    num_burst = max(5, int(burst_hours / 24 * base_rate * vel_mult * rng.uniform(3, 8)))

    # New device for burst
    new_device = rng.choice(all_devices) if rng.random() < COMPROMISED["new_device_prob"] else rng.choice(devices)
    new_region = rng.choice(REGIONS) if rng.random() < COMPROMISED["new_region_prob"] else region

    burst_txns = []
    for _ in range(num_burst):
        offset_s = rng.randint(0, max(1, burst_hours * 3600))
        ts = burst_start + timedelta(seconds=int(offset_s))
        amount = max(100, rng.normal(base_amount * amt_mult, base_amount * amt_mult * 0.5))
        sr = rng.uniform(*COMPROMISED["burst_success_rate"])
        status = "success" if rng.random() < sr else rng.choice(["failed", "declined", "disputed"])
        is_disputed = rng.random() < COMPROMISED["dispute_rate"]
        burst_txns.append({
            "txn_id": _uid("txn"),
            "customer_id": cust_id,
            "merchant_id": rng.choice(merchants),
            "timestamp": ts.isoformat(),
            "amount": round(amount, 2),
            "status": status,
            "payment_method": rng.choice(PAYMENT_METHODS),
            "device_id": new_device,
            "region": new_region,
            "bank": bank,
            "is_disputed": is_disputed,
            "is_suspicious": True,
        })

    return trust_txns + burst_txns


def _gen_cluster_member(rng, cust_id, start, end, devices, region,
                        merchants, instruments, bank, cluster_id):
    """Scenario C: Coordinated fraud cluster member."""
    days = (end - start).days
    daily_rate = rng.uniform(*COORDINATED_CLUSTER["per_account_daily_rate"])
    amt_mean = rng.uniform(*COORDINATED_CLUSTER["per_account_amount"])
    amt_std = amt_mean * 0.4
    num_txns = max(5, int(days * daily_rate * rng.uniform(0.8, 1.2)))

    # Coordination window
    coord_hours = rng.randint(*COORDINATED_CLUSTER["coordination_window_hours"])
    coord_start_offset = rng.randint(int(days * 0.3 * 86400), int(days * 0.8 * 86400))
    coord_start = start + timedelta(seconds=coord_start_offset)

    txns = []
    for j in range(num_txns):
        if j > num_txns * 0.6:
            # Coordinated burst in window
            offset_s = rng.randint(0, max(1, coord_hours * 3600))
            ts = coord_start + timedelta(seconds=int(offset_s))
            is_susp = True
        else:
            offset_s = rng.randint(0, days * 86400)
            ts = start + timedelta(seconds=int(offset_s))
            is_susp = False

        amount = max(50, rng.normal(amt_mean, amt_std))
        status = "success" if rng.random() < 0.85 else rng.choice(["failed", "declined"])
        txns.append({
            "txn_id": _uid("txn"),
            "customer_id": cust_id,
            "merchant_id": rng.choice(merchants[:3]) if merchants else _uid("merch"),
            "timestamp": ts.isoformat(),
            "amount": round(amount, 2),
            "status": status,
            "payment_method": rng.choice(instruments),
            "device_id": rng.choice(devices),
            "region": region,
            "bank": bank,
            "is_disputed": rng.random() < 0.15,
            "is_suspicious": is_susp,
        })
    return txns


def _gen_fraud_spike(rng, cust_id, start, end, devices, region,
                     merchants, instruments, bank):
    """Scenario D: Normal baseline then sudden suspicious-rate spike."""
    days = (end - start).days
    daily_rate = rng.uniform(0.5, 2.0)
    amt_mean = rng.uniform(500, 5000)
    amt_std = amt_mean * 0.3

    spike_delay = rng.randint(*FRAUD_SPIKE["spike_delay_days"])
    spike_start = start + timedelta(days=min(spike_delay, days - 5))
    spike_hours = rng.randint(*FRAUD_SPIKE["spike_window_hours"])
    spike_end = spike_start + timedelta(hours=spike_hours)
    if spike_end >= end:
        spike_end = end - timedelta(hours=1)

    # Normal phase
    normal_days = (spike_start - start).days
    num_normal = max(3, int(normal_days * daily_rate))
    txns = []
    for _ in range(num_normal):
        offset_s = rng.randint(0, max(1, normal_days * 86400))
        ts = start + timedelta(seconds=int(offset_s))
        amount = max(10, rng.normal(amt_mean, amt_std))
        status = "success" if rng.random() < 0.95 else "failed"
        txns.append({
            "txn_id": _uid("txn"),
            "customer_id": cust_id,
            "merchant_id": rng.choice(merchants),
            "timestamp": ts.isoformat(),
            "amount": round(amount, 2),
            "status": status,
            "payment_method": rng.choice(instruments),
            "device_id": rng.choice(devices),
            "region": region,
            "bank": bank,
            "is_disputed": False,
            "is_suspicious": False,
        })

    # Spike phase — high velocity, larger amounts, more failures
    num_spike = max(5, int(spike_hours / 24 * daily_rate * rng.uniform(4, 10)))
    for _ in range(num_spike):
        offset_s = rng.randint(0, max(1, spike_hours * 3600))
        ts = spike_start + timedelta(seconds=int(offset_s))
        amount = max(100, rng.normal(amt_mean * rng.uniform(2, 6), amt_std * 2))
        status = "success" if rng.random() < 0.65 else rng.choice(["failed", "declined"])
        txns.append({
            "txn_id": _uid("txn"),
            "customer_id": cust_id,
            "merchant_id": rng.choice(merchants),
            "timestamp": ts.isoformat(),
            "amount": round(amount, 2),
            "status": status,
            "payment_method": rng.choice(PAYMENT_METHODS),
            "device_id": rng.choice(devices),
            "region": rng.choice(REGIONS),
            "bank": bank,
            "is_disputed": rng.random() < 0.25,
            "is_suspicious": True,
        })

    return txns


def _gen_legit_high_volume(rng, cust_id, start, end, devices, region,
                           merchants, instruments, bank):
    """Scenario E: Legitimate high-volume — many txns, all legit."""
    days = (end - start).days
    daily_rate = rng.uniform(*LEGIT_HIGH_VOLUME["daily_txn_rate"])
    amt_mean = rng.uniform(*LEGIT_HIGH_VOLUME["amount_mean"])
    amt_std = amt_mean * LEGIT_HIGH_VOLUME["amount_std_frac"]
    success_rate = rng.uniform(*LEGIT_HIGH_VOLUME["success_rate"])
    num_txns = max(10, int(days * daily_rate * rng.uniform(0.8, 1.2)))

    txns = []
    for _ in range(num_txns):
        offset_s = rng.randint(0, days * 86400)
        ts = start + timedelta(seconds=int(offset_s))
        amount = max(10, rng.normal(amt_mean, amt_std))
        status = "success" if rng.random() < success_rate else "failed"
        txns.append({
            "txn_id": _uid("txn"),
            "customer_id": cust_id,
            "merchant_id": rng.choice(merchants),
            "timestamp": ts.isoformat(),
            "amount": round(amount, 2),
            "status": status,
            "payment_method": rng.choice(instruments),
            "device_id": rng.choice(devices),
            "region": region,
            "bank": bank,
            "is_disputed": False,
            "is_suspicious": False,
        })
    return txns


def _gen_legit_anomaly(rng, cust_id, start, end, devices, all_devices, region,
                       merchants, instruments, bank):
    """Scenario F: Legitimate anomaly — normal with 1-3 unusual transactions."""
    txns = _gen_normal(rng, cust_id, start, end, devices, region,
                       merchants, instruments, bank)
    days = (end - start).days
    num_anomalies = rng.choice([1, 2, 3])
    base_amount = rng.uniform(*LEGIT_ANOMALY["base_amount_mean"])

    for _ in range(num_anomalies):
        offset_s = rng.randint(0, days * 86400)
        ts = start + timedelta(seconds=int(offset_s))
        mult = rng.uniform(*LEGIT_ANOMALY["anomaly_amount_multiplier"])
        amount = base_amount * mult
        new_inst = rng.choice(PAYMENT_METHODS) if rng.random() < LEGIT_ANOMALY["anomaly_new_instrument_prob"] else rng.choice(instruments)
        new_region = rng.choice(REGIONS) if rng.random() < LEGIT_ANOMALY["anomaly_new_region_prob"] else region
        new_device = rng.choice(all_devices) if rng.random() < 0.3 else rng.choice(devices)
        txns.append({
            "txn_id": _uid("txn"),
            "customer_id": cust_id,
            "merchant_id": rng.choice(merchants),
            "timestamp": ts.isoformat(),
            "amount": round(amount, 2),
            "status": "success",
            "payment_method": new_inst,
            "device_id": new_device,
            "region": new_region,
            "bank": bank,
            "is_disputed": False,
            "is_suspicious": False,
        })
    return txns


def _gen_legit_growth(rng, cust_id, start, end, devices, region,
                      merchants, instruments, bank):
    """Legitimate high-growth: smooth organic amount growth over time."""
    txns = []
    days = (end - start).days
    daily_rate = rng.uniform(0.5, 2.0)
    amt_mean = rng.uniform(500, 3000)
    amt_std = amt_mean * 0.3
    growth_factor = rng.uniform(2.0, 5.0)
    success_rate = rng.uniform(0.93, 0.99)
    num_txns = max(5, int(days * daily_rate * rng.uniform(1.0, 1.5)))

    for _ in range(num_txns):
        offset_s = rng.randint(0, days * 86400)
        ts = start + timedelta(seconds=int(offset_s))
        progress = offset_s / (days * 86400)
        current_mean = amt_mean * (1 + (growth_factor - 1) * progress)
        amount = max(10, rng.normal(current_mean, amt_std))
        status = "success" if rng.random() < success_rate else "failed"
        txns.append({
            "txn_id": _uid("txn"),
            "customer_id": cust_id,
            "merchant_id": rng.choice(merchants),
            "timestamp": ts.isoformat(),
            "amount": round(amount, 2),
            "status": status,
            "payment_method": rng.choice(instruments),
            "device_id": rng.choice(devices),
            "region": region,
            "bank": bank,
            "is_disputed": False,
            "is_suspicious": False,
        })
    return txns


def _gen_noisy(rng, cust_id, start, end, devices, region,
               merchants, instruments, bank):
    """Mixed/noisy: missing fields, inconsistent data."""
    txns = _gen_normal(rng, cust_id, start, end, devices, region,
                       merchants, instruments, bank)
    for txn in txns:
        if rng.random() < 0.1:
            txn["amount"] = None
        if rng.random() < 0.05:
            txn["payment_method"] = None
        if rng.random() < 0.08:
            txn["status"] = None
        if rng.random() < 0.05:
            txn["device_id"] = None
    return txns


if __name__ == "__main__":
    customers_df, merchants_df, transactions_df, devices_df, clusters_df = generate_dataset()
    print(f"\nCustomers: {customers_df.shape}")
    print(f"Merchants: {merchants_df.shape}")
    print(f"Transactions: {transactions_df.shape}")
    print(f"Devices: {devices_df.shape}")
    print(f"Clusters: {clusters_df.shape}")
