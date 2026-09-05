"""
Fraud-Spike Sentinel — Unsupervised Anomaly Detector

Uses Isolation Forest to compute an unsupervised anomaly score AnomalyScore ∈ [0, 1].
Operates independently from the supervised ML model to capture novel/unseen fraud vectors.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
import pickle
import os

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
ANOMALY_MODEL_PATH = os.path.join(MODEL_DIR, "isolation_forest.pkl")


class AnomalyDetector:
    def __init__(self, contamination=0.05, n_estimators=100, random_state=42):
        self.model = IsolationForest(
            contamination=contamination,
            n_estimators=n_estimators,
            random_state=random_state,
            n_jobs=-1
        )
        self.feature_cols = None
        self.min_raw_score = None
        self.max_raw_score = None

    def fit(self, features_df, feature_cols=None):
        """Fit Isolation Forest on feature matrix."""
        if feature_cols is None:
            feature_cols = [c for c in features_df.columns if c not in ["customer_id", "label_suspicious", "scenario"]]
        self.feature_cols = sorted(feature_cols)

        values = features_df[self.feature_cols]
        if values.isna().any().any():
            raise ValueError("Anomaly model unavailable because required features contain missing values.")
        X = values.to_numpy(dtype=float)
        self.model.fit(X)

        # Compute raw decision scores (negative outlier factor)
        raw_scores = self.model.decision_function(X)
        self.min_raw_score = float(raw_scores.min())
        self.max_raw_score = float(raw_scores.max())

        os.makedirs(MODEL_DIR, exist_ok=True)
        with open(ANOMALY_MODEL_PATH, "wb") as f:
            pickle.dump(self, f)

        print(f"[Anomaly Model] Fit Isolation Forest on {len(features_df)} samples, {len(self.feature_cols)} features.")
        return self

    def predict_anomaly_score(self, features_df):
        """
        Returns normalized AnomalyScore ∈ [0, 1].
        0 = completely normal, 1 = extreme outlier.
        """
        if self.feature_cols is None:
            raise ValueError("Model not fitted.")

        values = features_df[self.feature_cols]
        if values.isna().any().any():
            raise ValueError("Anomaly scoring unavailable because required features contain missing values.")
        X = values.to_numpy(dtype=float)
        raw_scores = self.model.decision_function(X)

        # Invert: raw score is higher for inliers, lower for outliers
        # Normalize so higher = more anomalous
        if self.max_raw_score is not None and self.max_raw_score > self.min_raw_score:
            normalized = (self.max_raw_score - raw_scores) / (self.max_raw_score - self.min_raw_score)
        else:
            normalized = 1.0 / (1.0 + np.exp(raw_scores))

        return np.clip(normalized, 0.0, 1.0)


def load_anomaly_model():
    if os.path.exists(ANOMALY_MODEL_PATH):
        with open(ANOMALY_MODEL_PATH, "rb") as f:
            return pickle.load(f)
    return None
