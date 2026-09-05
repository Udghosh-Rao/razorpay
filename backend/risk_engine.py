"""
Fraud-Spike Sentinel — Risk Fusion Engine

Combines multiple risk signals into one canonical risk score CanonicalRiskScore ∈ [0, 1].

Fused Signals:
  1. Supervised ML Score P(Fraud)
  2. Unsupervised Anomaly Score (Isolation Forest)
  3. Behavioral Deviation Score (Point-in-time)
  4. Coordinated Cluster Risk Score
  5. Temporal Fraud Spike Risk Score
"""

import numpy as np
import pandas as pd

from backend.policy_engine import evaluate_policy


class RiskFusionEngine:
    def __init__(self, weights=None):
        if weights is None:
            self.weights = {
                "supervised_ml": 0.45,
                "unsupervised_anomaly": 0.15,
                "behavioral_deviation": 0.15,
                "cluster_risk": 0.15,
                "temporal_spike_risk": 0.10,
            }
        else:
            self.weights = weights

        # Ensure weights sum to 1.0
        total_w = sum(self.weights.values())
        self.weights = {k: v / total_w for k, v in self.weights.items()}

    def fuse_risk_scores(self, ml_score, anomaly_score=0.0, dev_score=0.0,
                        cluster_risk=0.0, spike_risk=0.0):
        """
        Compute fused canonical risk score.
        All input scores expected in [0, 1] except dev_score (normalized internally).
        """
        ml_score = float(np.clip(ml_score, 0.0, 1.0))
        anomaly_score = float(np.clip(anomaly_score, 0.0, 1.0))
        cluster_risk = float(np.clip(cluster_risk, 0.0, 1.0))
        spike_risk = float(np.clip(spike_risk, 0.0, 1.0))

        # Normalize behavioral deviation score (typically 0-20, maps to [0, 1])
        norm_dev_score = float(np.clip(dev_score / 15.0, 0.0, 1.0))

        canonical_score = (
            self.weights["supervised_ml"] * ml_score +
            self.weights["unsupervised_anomaly"] * anomaly_score +
            self.weights["behavioral_deviation"] * norm_dev_score +
            self.weights["cluster_risk"] * cluster_risk +
            self.weights["temporal_spike_risk"] * spike_risk
        )

        return float(np.clip(canonical_score, 0.0, 1.0))

    def evaluate_account(self, features_dict, ml_score, anomaly_score=0.0,
                         cluster_risk=0.0, spike_risk=0.0, context=None):
        """
        Fuse risk signals and evaluate operational policy decision for a single account/transaction.
        """
        dev_score = features_dict.get("behavioral_deviation_score", 0.0)
        canonical_risk = self.fuse_risk_scores(
            ml_score, anomaly_score, dev_score, cluster_risk, spike_risk
        )

        policy_ctx = context or {}
        policy_ctx["is_cluster_member"] = cluster_risk > 0.5
        policy_ctx["is_merchant_spike"] = spike_risk > 0.5
        policy_ctx["amount"] = features_dict.get("average_amount", features_dict.get("amount", 0.0))

        policy_result = evaluate_policy(canonical_risk, policy_ctx)

        return {
            "canonical_risk_score": round(canonical_risk, 4),
            "decision": policy_result["decision"],
            "policy_version": policy_result["policy_version"],
            "reasoning": policy_result["reasoning"],
            "sub_scores": {
                "supervised_ml_score": round(ml_score, 4),
                "unsupervised_anomaly_score": round(anomaly_score, 4),
                "behavioral_deviation_score": round(dev_score, 4),
                "cluster_risk_score": round(cluster_risk, 4),
                "temporal_spike_risk_score": round(spike_risk, 4),
            },
            "weights": self.weights,
        }


# Global engine instance
DEFAULT_RISK_ENGINE = RiskFusionEngine()
