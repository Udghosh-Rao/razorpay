"""
Fraud-Spike Sentinel — ML Model Pipeline

Three models trained in sequence:
  1. Rule-based baseline
  2. Logistic regression (interpretable coefficients)
  3. Gradient boosting (HistGradientBoostingClassifier) — final model

Temporal train/test split. Calibration on validation set using sigmoid.
Full evaluation metrics including temporal future period and drift test.
"""

import numpy as np
import pandas as pd
import pickle
import os
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.metrics import (
    precision_score, recall_score, f1_score,
    precision_recall_curve, average_precision_score,
    roc_auc_score, confusion_matrix, roc_curve
)
from sklearn.inspection import permutation_importance
from data.features import FEATURE_COLS

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")

def temporal_split(features_df, customers_df, train_ratio=0.5, val_ratio=0.2):
    """
    Temporal 3-way split by signup date: 
    First ~50% -> train
    Next ~20% -> validation (used for calibration & threshold tuning)
    Last ~30% -> held-out test (includes future period)
    """
    merged = features_df.merge(
        customers_df[["customer_id", "signup_date", "label_suspicious", "scenario"]],
        on="customer_id"
    )
    merged["signup_date"] = pd.to_datetime(merged["signup_date"])
    merged = merged.sort_values("signup_date")

    n = len(merged)
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))

    train = merged.iloc[:train_end]
    val = merged.iloc[train_end:val_end]
    test = merged.iloc[val_end:]
    
    print(f"Temporal split: {len(train)} train, {len(val)} val, {len(test)} test")
    for name, split in [("Train", train), ("Val", val), ("Test", test)]:
        print(f"  {name} labels: {split['label_suspicious'].value_counts().to_dict()}")
        
    return train, val, test

def prepare_xy(df):
    """Extract feature matrix X and label vector y."""
    missing_cols = [c for c in FEATURE_COLS if c not in df.columns]
    if missing_cols:
        raise ValueError("Model training unavailable because required features are missing: " + ", ".join(missing_cols))
    available_cols = list(FEATURE_COLS)
    values = df[available_cols]
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("Model training unavailable because required features contain missing or non-finite values.")
    X = values.to_numpy(dtype=float)
    y = df["label_suspicious"].values
    return X, y, available_cols

def best_f1_threshold(scores, y_true, candidates=None):
    """Threshold that maximizes F1. Must only ever be called with validation data."""
    if candidates is None:
        candidates = np.round(np.arange(0.05, 0.96, 0.05), 2)
    best_f1, best_t = -1, 0.5
    for t in candidates:
        f1 = f1_score(y_true, (scores >= t).astype(int), zero_division=0)
        if f1 > best_f1:
            best_f1, best_t = f1, t
    return best_t

class RuleBaseline:
    """Simple rule-based baseline: high velocity AND high escalation."""
    def __init__(self, velocity_thresh=3.0, disc_thresh=2.0):
        self.velocity_thresh = velocity_thresh
        self.disc_thresh = disc_thresh

    def predict_proba(self, df):
        scores = np.zeros(len(df))
        vel = df["velocity_ratio"].values if "velocity_ratio" in df.columns else np.zeros(len(df))
        disc = df["escalation_discontinuity"].values if "escalation_discontinuity" in df.columns else np.ones(len(df))
        scores += (vel > self.velocity_thresh).astype(float) * 0.4
        scores += (disc > self.disc_thresh).astype(float) * 0.6
        return scores

    def predict(self, df, threshold=0.5):
        return (self.predict_proba(df) >= threshold).astype(int)

def train_all_models(features_df, customers_df):
    """Train models and return evaluation results."""
    os.makedirs(MODEL_DIR, exist_ok=True)

    train_df, val_df, test_df = temporal_split(features_df, customers_df)
    X_train, y_train, feature_names = prepare_xy(train_df)
    X_val, y_val, _ = prepare_xy(val_df)
    X_test, y_test, _ = prepare_xy(test_df)

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    X_test_scaled = scaler.transform(X_test)

    results = {
        "feature_names": feature_names,
        "train_size": len(train_df),
        "val_size": len(val_df),
        "test_size": len(test_df),
        "train_positive_rate": float(y_train.mean()) if len(y_train) > 0 else 0,
        "val_positive_rate": float(y_val.mean()) if len(y_val) > 0 else 0,
        "test_positive_rate": float(y_test.mean()) if len(y_test) > 0 else 0,
        "models": {},
    }

    # ─── Model 1: Rule-based baseline ──────────────────────────────────────
    print("\n--- Training Rule Baseline ---")
    rule_model = RuleBaseline()
    rule_val_scores = rule_model.predict_proba(val_df)
    rule_thresh = best_f1_threshold(rule_val_scores, y_val)
    rule_test_scores = rule_model.predict_proba(test_df)
    rule_metrics = evaluate_model("rule_baseline", rule_test_scores, y_test, threshold=rule_thresh)
    results["models"]["rule_baseline"] = rule_metrics

    # ─── Model 2: Logistic Regression ──────────────────────────────────────
    print("\n--- Training Logistic Regression ---")
    lr = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)
    lr.fit(X_train_scaled, y_train)
    lr_val_scores = lr.predict_proba(X_val_scaled)[:, 1]
    lr_thresh = best_f1_threshold(lr_val_scores, y_val)
    lr_test_scores = lr.predict_proba(X_test_scaled)[:, 1]
    lr_metrics = evaluate_model("logistic_regression", lr_test_scores, y_test, threshold=lr_thresh)
    lr_metrics["coefficients"] = {
        name: round(float(coef), 4)
        for name, coef in zip(feature_names, lr.coef_[0])
    }
    results["models"]["logistic_regression"] = lr_metrics

    # ─── Model 3: Gradient Boosting (final model) ─────────────────────────
    print("\n--- Training Gradient Boosting ---")
    sample_weights = compute_sample_weight('balanced', y_train)
    gbm = HistGradientBoostingClassifier(
        max_iter=200, max_depth=6, learning_rate=0.1, min_samples_leaf=20, random_state=42,
    )
    gbm.fit(X_train, y_train, sample_weight=sample_weights)

    # Calibration on VALIDATION set using sigmoid (Platt scaling) per prompt §11
    print("Calibrating model on validation set (Platt scaling)...")
    gbm_val_raw = gbm.predict_proba(X_val)[:, 1]
    
    # Fit Platt scaling (Logistic Regression on raw validation probabilities)
    calibrator = LogisticRegression(C=1.0, solver="lbfgs")
    calibrator.fit(gbm_val_raw.reshape(-1, 1), y_val)

    # Function to get calibrated probabilities
    def predict_calibrated_proba(model, X):
        raw_p = model.predict_proba(X)[:, 1]
        return calibrator.predict_proba(raw_p.reshape(-1, 1))[:, 1]

    gbm_val_scores = predict_calibrated_proba(gbm, X_val)
    gbm_thresh = best_f1_threshold(gbm_val_scores, y_val)
    gbm_test_scores = predict_calibrated_proba(gbm, X_test)
    gbm_metrics = evaluate_model("gradient_boosting", gbm_test_scores, y_test, threshold=gbm_thresh)

    # Calibration curve
    try:
        cal_prob_true, cal_prob_pred = calibration_curve(y_test, gbm_test_scores, n_bins=10)
        gbm_metrics["calibration"] = {
            "prob_true": [round(float(x), 4) for x in cal_prob_true],
            "prob_pred": [round(float(x), 4) for x in cal_prob_pred],
            "is_calibrated": True,
            "method": "sigmoid",
        }
    except Exception:
        gbm_metrics["calibration"] = {"is_calibrated": False}

    results["models"]["gradient_boosting"] = gbm_metrics

    # Feature importances
    print("Computing permutation importances...")
    perm = permutation_importance(
        gbm, X_test, y_test, n_repeats=5, random_state=42, scoring="average_precision"
    )
    importance_dict = {
        name: round(float(imp), 4)
        for name, imp in sorted(zip(feature_names, perm.importances_mean), key=lambda x: -x[1])
    }
    results["feature_importances"] = importance_dict
    
    # ─── Temporal & Drift Tests ───────────────────────────────────────────
    # Split test set into early test (normal) and late test (future period)
    mid_test = len(test_df) // 2
    future_test_df = test_df.iloc[mid_test:]
    future_scores = gbm_test_scores[mid_test:]
    future_y = y_test[mid_test:]
    results["temporal_test"] = evaluate_model("temporal_future", future_scores, future_y, threshold=gbm_thresh)

    # Drift test: artificially shift behavior of a subset to simulate drift
    drift_df = future_test_df.copy()
    if "average_amount" in drift_df.columns:
        drift_df["average_amount"] = drift_df["average_amount"] * 2.0  # shift amounts
    if "velocity_ratio" in drift_df.columns:
        drift_df["velocity_ratio"] = drift_df["velocity_ratio"] * 0.5  # shift velocity
    
    X_drift, y_drift, _ = prepare_xy(drift_df)
    drift_scores = predict_calibrated_proba(gbm, X_drift)
    results["drift_test"] = evaluate_model("drift_scenario", drift_scores, y_drift, threshold=gbm_thresh)

    # ─── Model comparison table ───────────────────────────────────────────
    results["comparison_table"] = {
        "rule_baseline": {
            "precision": rule_metrics["precision"],
            "recall": rule_metrics["recall"],
            "pr_auc": rule_metrics["pr_auc"],
        },
        "logistic_regression": {
            "precision": lr_metrics["precision"],
            "recall": lr_metrics["recall"],
            "pr_auc": lr_metrics["pr_auc"],
        },
        "gradient_boosting": {
            "precision": gbm_metrics["precision"],
            "recall": gbm_metrics["recall"],
            "pr_auc": gbm_metrics["pr_auc"],
        },
    }

    # Save models
    with open(os.path.join(MODEL_DIR, "scaler.pkl"), "wb") as f: pickle.dump(scaler, f)
    with open(os.path.join(MODEL_DIR, "logistic.pkl"), "wb") as f: pickle.dump(lr, f)
    with open(os.path.join(MODEL_DIR, "gbm.pkl"), "wb") as f: pickle.dump(gbm, f)
    with open(os.path.join(MODEL_DIR, "gbm_calibrated.pkl"), "wb") as f:
        pickle.dump({"gbm": gbm, "calibrator": calibrator}, f)

    results["gbm_scores"] = gbm_test_scores.tolist()
    results["y_test"] = y_test.tolist()

    precision_curve, recall_curve, _ = precision_recall_curve(y_test, gbm_test_scores)
    results["pr_curve"] = {
        "precision": [round(float(x), 4) for x in precision_curve[::max(1, len(precision_curve)//100)]],
        "recall": [round(float(x), 4) for x in recall_curve[::max(1, len(recall_curve)//100)]],
    }
    fpr, tpr, _ = roc_curve(y_test, gbm_test_scores)
    results["roc_curve"] = {
        "fpr": [round(float(x), 4) for x in fpr[::max(1, len(fpr)//100)]],
        "tpr": [round(float(x), 4) for x in tpr[::max(1, len(tpr)//100)]],
    }

    return results, gbm, scaler, val_df, test_df, gbm_val_scores

def evaluate_model(name, scores, y_true, threshold=0.5):
    preds = (scores >= threshold).astype(int)
    precision = float(precision_score(y_true, preds, zero_division=0))
    recall = float(recall_score(y_true, preds, zero_division=0))
    f1 = float(f1_score(y_true, preds, zero_division=0))
    pr_auc = float(average_precision_score(y_true, scores)) if len(np.unique(y_true)) > 1 else 0.0
    try: roc = float(roc_auc_score(y_true, scores))
    except ValueError: roc = 0.0

    cm = confusion_matrix(y_true, preds)
    tn, fp, fn, tp = cm.ravel() if cm.size == 4 else (0, 0, 0, 0)

    metrics = {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "pr_auc": round(pr_auc, 4),
        "roc_auc": round(roc, 4),
        "threshold": threshold,
        "confusion_matrix": {
            "true_negatives": int(tn),
            "false_positives": int(fp),
            "false_negatives": int(fn),
            "true_positives": int(tp),
        },
    }
    print(f"  {name}: P={precision:.3f} R={recall:.3f} F1={f1:.3f} PR-AUC={pr_auc:.3f} ROC-AUC={roc:.3f}")
    return metrics

def load_model():
    with open(os.path.join(MODEL_DIR, "gbm_calibrated.pkl"), "rb") as f:
        cal_dict = pickle.load(f)
    with open(os.path.join(MODEL_DIR, "scaler.pkl"), "rb") as f:
        scaler = pickle.load(f)
    return cal_dict, scaler

def predict_single(features_dict, model=None, scaler=None):
    if model is None or scaler is None:
        model, scaler = load_model()
    
    if isinstance(model, dict):
        gbm = model["gbm"]
        calibrator = model["calibrator"]
    else:
        gbm = model
        calibrator = None

    missing_features = [feature for feature in FEATURE_COLS
                        if feature not in features_dict or features_dict[feature] is None]
    invalid_features = [feature for feature in FEATURE_COLS
                        if feature in features_dict and not np.isfinite(float(features_dict[feature]))]
    if missing_features or invalid_features:
        raise ValueError(
            "Model scoring unavailable because required computed features are missing: "
            + ", ".join(missing_features + invalid_features)
        )
    X = np.array([[float(features_dict[c]) for c in FEATURE_COLS]])
    raw_p = gbm.predict_proba(X)[:, 1]
    
    if calibrator is not None:
        score = calibrator.predict_proba(raw_p.reshape(-1, 1))[:, 1][0]
    else:
        score = raw_p[0]

    return float(score)
