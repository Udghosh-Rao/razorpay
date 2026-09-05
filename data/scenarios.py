"""
Fraud-Spike Sentinel — Scenario Definitions

Defines behavioral archetypes for synthetic payment data generation.
Each scenario specifies transaction patterns, device usage, geography,
and payment instrument behavior.

Scenarios:
  A — Normal customer
  B — Compromised account
  C — Coordinated fraud cluster
  D — Fraud spike (sudden short-window increase)
  E — Legitimate high-volume merchant
  F — Legitimate anomaly
"""

# Scenario distribution (fraction of customers)
SCENARIO_DISTRIBUTION = {
    "normal": 0.55,
    "compromised_account": 0.08,
    "coordinated_cluster": 0.05,
    "fraud_spike": 0.07,
    "legit_high_volume": 0.10,
    "legit_anomaly": 0.07,
    "legit_growth": 0.06,
    "noisy": 0.02,
}

# Regions with realistic distribution
REGIONS = [
    "Mumbai", "Delhi", "Bangalore", "Chennai", "Hyderabad",
    "Kolkata", "Pune", "Ahmedabad", "Jaipur", "Lucknow",
    "Chandigarh", "Kochi", "Indore", "Nagpur", "Coimbatore",
]

REGION_WEIGHTS = [
    0.18, 0.15, 0.14, 0.10, 0.08,
    0.07, 0.06, 0.05, 0.04, 0.03,
    0.02, 0.02, 0.02, 0.02, 0.02,
]

# Payment methods
PAYMENT_METHODS = ["upi", "card_visa", "card_mastercard", "card_rupay", "netbanking", "wallet"]
PAYMENT_METHOD_WEIGHTS = [0.35, 0.20, 0.15, 0.10, 0.12, 0.08]

# Banks
BANKS = [
    "HDFC", "SBI", "ICICI", "Axis", "Kotak",
    "PNB", "BOB", "IndusInd", "Yes", "Federal",
]

# ─── Scenario A: Normal Customer ─────────────────────────────────────────
NORMAL = {
    "label": 0,
    "is_suspicious": False,
    "daily_txn_rate": (0.3, 2.0),
    "amount_mean": (500, 5000),
    "amount_std_frac": 0.3,
    "success_rate": (0.92, 0.99),
    "device_change_prob": 0.02,      # rarely changes device
    "region_change_prob": 0.05,      # rarely changes region
    "instrument_count": (1, 3),
    "description": "Stable device, normal frequency, normal amounts, consistent geography, high success rate",
}

# ─── Scenario B: Compromised Account ─────────────────────────────────────
COMPROMISED = {
    "label": 1,
    "is_suspicious": True,
    "trust_phase_days": (15, 40),
    "burst_hours": (4, 36),
    "velocity_multiplier": (3.0, 8.0),
    "amount_multiplier": (3.0, 10.0),
    "new_device_prob": 0.85,         # almost always new device
    "new_region_prob": 0.60,         # frequently unusual geography
    "burst_success_rate": (0.55, 0.75),
    "dispute_rate": 0.30,
    "description": "New device, unusual geography, abnormal amount, sudden velocity increase, behavioral deviation",
}

# ─── Scenario C: Coordinated Fraud Cluster ────────────────────────────────
COORDINATED_CLUSTER = {
    "label": 1,
    "is_suspicious": True,
    "cluster_size": (5, 40),          # accounts per cluster
    "shared_devices": (2, 5),         # devices shared across cluster
    "shared_instruments": (2, 4),     # payment instruments shared
    "shared_merchants": (1, 3),       # merchant relationships
    "individual_suspicion": "moderate",
    "cluster_suspicion": "high",
    "per_account_daily_rate": (0.5, 1.5),
    "per_account_amount": (1000, 8000),
    "coordination_window_hours": (2, 24),
    "description": "Several accounts share device/IP/payment instrument/merchant. Individual = moderate, cluster = high.",
}

# ─── Scenario D: Fraud Spike ─────────────────────────────────────────────
FRAUD_SPIKE = {
    "label": 1,
    "is_suspicious": True,
    "baseline_suspicious_rate": (0.01, 0.03),
    "spike_suspicious_rate": (0.08, 0.20),
    "spike_window_hours": (4, 48),
    "spike_delay_days": (20, 60),     # days before spike starts
    "affected_merchants": (1, 5),
    "description": "Normal baseline suspicious rate, then sudden short-window increase.",
}

# ─── Scenario E: Legitimate High-Volume Merchant ─────────────────────────
LEGIT_HIGH_VOLUME = {
    "label": 0,
    "is_suspicious": False,
    "daily_txn_rate": (5.0, 20.0),    # high volume
    "amount_mean": (200, 3000),
    "amount_std_frac": 0.4,
    "success_rate": (0.94, 0.99),
    "device_change_prob": 0.03,
    "description": "High transaction volume but legitimate behavior. Prevents 'high volume = fraud' bias.",
}

# ─── Scenario F: Legitimate Anomaly ───────────────────────────────────────
LEGIT_ANOMALY = {
    "label": 0,
    "is_suspicious": False,
    "anomaly_count": (1, 3),
    "anomaly_amount_multiplier": (5, 15),
    "anomaly_new_instrument_prob": 0.5,
    "anomaly_new_region_prob": 0.3,
    "base_daily_rate": (0.3, 2.0),
    "base_amount_mean": (500, 5000),
    "description": "Legitimate customer performs unusual transaction. Creates realistic false positives.",
}
