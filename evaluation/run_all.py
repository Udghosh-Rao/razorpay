"""
Fraud-Spike Sentinel — Pipeline Orchestrator

Runs the full pipeline end-to-end:
1. Synthetic Data Generation (100K+ txns, merchants, devices, scenarios A-F)
2. Point-in-Time Feature Computation (train cohort stats frozen)
3. Unsupervised Anomaly Detection (Isolation Forest)
4. Coordinated Cluster Detection
5. Temporal Fraud Spike Detection (Wilson CI)
6. Supervised ML Training & Validation Calibration
7. Risk Fusion & Central Policy Decision Evaluation
8. Financial Exposure & FP Economics Assessment
9. Export results.json
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime

from data.generator import generate_dataset
from data.features import compute_cohort_reference_stats, compute_features
from backend.anomaly_model import AnomalyDetector
from backend.cluster_detector import detect_clusters
from backend.spike_detector import detect_merchant_spikes
from backend.model import train_all_models
from backend.risk_engine import RiskFusionEngine
from backend.policy_engine import evaluate_policy
from backend.financial import compute_financial_metrics

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(ROOT_DIR, "data", "generated")
os.makedirs(DATA_DIR, exist_ok=True)

def main():
    print("\n" + "="*60)
    print("🚀 Fraud-Spike Sentinel: Pipeline Orchestrator")
    print("="*60)

    # 1. Generate Data (1,000 customers for fast & reliable evaluation)
    print("\n[1/7] Generating synthetic data...")
    customers_df, merchants_df, transactions_df, devices_df, clusters_df = generate_dataset(num_customers=1000)

    customers_df.to_csv(os.path.join(DATA_DIR, "customers.csv"), index=False)
    merchants_df.to_csv(os.path.join(DATA_DIR, "merchants.csv"), index=False)
    transactions_df.to_csv(os.path.join(DATA_DIR, "transactions.csv"), index=False)
    devices_df.to_csv(os.path.join(DATA_DIR, "devices.csv"), index=False)
    if len(clusters_df) > 0:
        clusters_df.to_csv(os.path.join(DATA_DIR, "clusters.csv"), index=False)

    # 2. Compute Point-in-Time Features
    print("\n[2/7] Computing Point-in-Time Features...")
    customers_df["signup_date"] = pd.to_datetime(customers_df["signup_date"])
    customers_df = customers_df.sort_values("signup_date")
    train_end = int(len(customers_df) * 0.5)
    train_customers = customers_df.iloc[:train_end]

    print("  -> Freezing cohort reference statistics from train split...")
    train_features_df = compute_features(train_customers, transactions_df)
    reference_stats = compute_cohort_reference_stats(train_features_df)

    print("  -> Computing full point-in-time feature matrix...")
    features_df = compute_features(customers_df, transactions_df, reference_stats=reference_stats)
    features_df.to_csv(os.path.join(DATA_DIR, "features.csv"), index=False)

    # 3. Unsupervised Anomaly Detection
    print("\n[3/7] Training Isolation Forest Anomaly Model...")
    anomaly_detector = AnomalyDetector(contamination=0.05)
    anomaly_detector.fit(train_features_df)
    anomaly_scores = anomaly_detector.predict_anomaly_score(features_df)
    features_df["unsupervised_anomaly_score"] = anomaly_scores

    # 4. Cluster & Spike Detection
    print("\n[4/7] Detecting Coordinated Clusters & Temporal Spikes...")
    ordered_customers = customers_df.sort_values("signup_date")
    test_start = pd.to_datetime(
        ordered_customers.iloc[int(len(ordered_customers) * 0.7)]["signup_date"]
    )
    historical_transactions = transactions_df[
        pd.to_datetime(transactions_df["timestamp"]) <= test_start
    ].copy()
    detected_clusters, customer_cluster_risk = detect_clusters(historical_transactions)
    merchant_spikes = detect_merchant_spikes(historical_transactions, window_hours=48)
    print(f"  -> Found {len(detected_clusters)} coordinated clusters")
    print(f"  -> Found {len(merchant_spikes)} merchant fraud spikes")

    # 5. Supervised ML Training & Calibration
    print("\n[5/7] Training & Calibrating Supervised Models...")
    model_results, gbm_cal, scaler, val_df, test_df, val_scores = train_all_models(features_df, customers_df)
    gbm_thresh = model_results["models"]["gradient_boosting"]["threshold"]

    # 6. Risk Fusion & Policy Evaluation
    print("\n[6/7] Fusing Risk Signals & Evaluating Central Policy...")
    fusion_engine = RiskFusionEngine()
    
    test_merged = test_df.copy()
    test_scores = model_results["models"]["gradient_boosting"]
    
    # Map sub-scores to test set
    test_anomaly = features_df.loc[test_merged.index, "unsupervised_anomaly_score"].values if "unsupervised_anomaly_score" in features_df.columns else np.zeros(len(test_merged))
    test_dev = test_merged["behavioral_deviation_score"].values if "behavioral_deviation_score" in test_merged.columns else np.zeros(len(test_merged))
    
    test_cluster_risk = np.array([customer_cluster_risk.get(c, 0.0) for c in test_merged["customer_id"]])
    
    # Check if merchant has spike
    spike_windows = [
        (s["merchant_id"], pd.to_datetime(s["window_start"]), pd.to_datetime(s["window_end"]))
        for s in merchant_spikes
    ]
    test_spike_risk = np.array([
        1.0 if any(
            txn["merchant_id"] == merchant_id
            and window_start <= pd.to_datetime(txn["timestamp"]) < window_end
            for merchant_id, window_start, window_end in spike_windows
            for _, txn in transactions_df[
                transactions_df["customer_id"] == cust_id
            ].iterrows()
        ) else 0.0
        for cust_id in test_merged["customer_id"]
    ], dtype=float)

    canonical_scores = []
    policy_decisions = []

    for i in range(len(test_merged)):
        ml_s = model_results["gbm_scores"][i]
        anom_s = test_anomaly[i]
        dev_s = test_dev[i]
        clust_s = test_cluster_risk[i]
        spike_s = test_spike_risk[i]

        canonical = fusion_engine.fuse_risk_scores(ml_s, anom_s, dev_s, clust_s, spike_s)
        policy_res = evaluate_policy(
            canonical,
            context={
                "is_cluster_member": clust_s > 0.5,
                "is_merchant_spike": spike_s > 0.5,
            },
            tau_low=0.30,
            tau_high=gbm_thresh,
        )

        canonical_scores.append(canonical)
        policy_decisions.append(policy_res["decision"])

    test_merged["canonical_risk_score"] = canonical_scores
    test_merged["policy_decision"] = policy_decisions

    # Store policy decisions for flagged test accounts (investigation lookup)
    decisions = []
    for idx in range(len(test_merged)):
        row = test_merged.iloc[idx]
        if policy_decisions[idx] not in ("REVIEW", "BLOCK"):
            continue
        decisions.append({
            "decision_id": f"eval_{row['customer_id']}",
            "entity_id": row["customer_id"],
            "entity_type": "account",
            "decision": policy_decisions[idx],
            "canonical_risk_score": round(canonical_scores[idx], 4),
            "model_version": "v1.2.0-gbm",
            "feature_version": "v1.0.0",
            "policy_version": "v1.0.0",
            "sub_scores": {
                "ml_probability": round(float(model_results["gbm_scores"][idx]), 4),
                "anomaly_score": round(float(test_anomaly[idx]), 4),
                "cluster_risk": round(float(test_cluster_risk[idx]), 4),
            },
            "reasoning": evaluate_policy(
                canonical_scores[idx],
                context={
                    "is_cluster_member": test_cluster_risk[idx] > 0.5,
                    "is_merchant_spike": test_spike_risk[idx] > 0.5,
                },
                tau_low=0.30,
                tau_high=gbm_thresh,
            )["reasoning"],
            "created_at": datetime.now().isoformat(),
        })

    # 7. Financial Exposure Assessment
    print("\n[7/7] Computing Financial Exposure & FP Costs...")
    y_test = model_results["y_test"]
    financial_results = compute_financial_metrics(
        canonical_scores, y_test, test_merged, tau_low=0.30,
        tau_high=gbm_thresh, evaluation_unit="account"
    )

    # Clean & Save Results
    final_results = {
        "timestamp": datetime.now().isoformat(),
        "data": {
            "customers": len(customers_df),
            "transactions": len(transactions_df),
            "merchants": len(merchants_df),
            "devices": len(devices_df),
            "clusters_detected": len(detected_clusters),
            "spikes_detected": len(merchant_spikes),
            "fraud_prevalence": float(customers_df["label_suspicious"].mean()),
        },
        "model": {
            "threshold": gbm_thresh,
            "evaluation_unit": "account",
            "temporal_spike_signal_accounts": int(test_spike_risk.sum()),
            "performance": model_results["models"]["gradient_boosting"],
            "temporal_future_performance": model_results["temporal_test"],
            "drift_test_performance": model_results["drift_test"],
            "feature_importances": model_results.get("feature_importances", {}),
            "comparison_table": model_results.get("comparison_table", {}),
        },
        "financials": financial_results,
        "spikes": merchant_spikes[:5],  # top 5 spikes
        "clusters": [
            {k: v for k, v in c.items() if k != "customer_ids"}
            for c in detected_clusters[:5]
        ],
    }

    results_path = os.path.join(ROOT_DIR, "results.json")
    with open(results_path, "w") as f:
        json.dump(final_results, f, indent=2)


    print("\n" + "="*60)
    print(f"✅ Pipeline Complete! Results exported to {results_path}")
    print("="*60)

if __name__ == "__main__":
    main()
