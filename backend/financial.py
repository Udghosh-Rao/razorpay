"""
Fraud-Spike Sentinel — Financial Metrics & Cost-Benefit Model

CRITICAL ASSUMPTIONS (Labeled as illustrative per prompt §20):
  "Illustrative cost model for synthetic evaluation — actual deployment requires
   merchant-specific unit economics."

Formulas:
  1. Per-transaction expected loss:
       ExpectedLoss_i = P(Fraud_i) × TransactionAmount_i
  2. False positive cost:
       Cost(FP_Review) = ReviewCostPerCase (INR 500)
       Cost(FP_Block)  = (LostGMVFraction × Amount) + ChurnPenalty (1% of Amount + INR 1,000)
  3. Estimated avoidable exposure is reported only for transaction-level
     labels and predictions. Account-level evaluation cannot support it.
"""

import numpy as np
import pandas as pd

# Illustrative cost parameters
REVIEW_COST_PER_CASE = 500.0         # INR — analyst review cost
BLOCK_LOST_GMV_FRACTION = 0.02       # 2% lost margin on blocked legit transaction
BLOCK_CHURN_PENALTY = 1000.0         # INR — customer lifetime value impact

DEFAULT_TAU_LOW = 0.30
DEFAULT_TAU_HIGH = 0.70


def compute_transaction_expected_loss(risk_score, amount):
    """
    Per-transaction expected loss:
      ExpectedLoss_i = P(Fraud_i) × TransactionAmount_i
    """
    return float(risk_score * amount)


def compute_financial_metrics(scores, y_true, test_df=None, tau_low=DEFAULT_TAU_LOW,
                              tau_high=DEFAULT_TAU_HIGH, evaluation_unit="transaction"):
    """
    Compute observed suspicious value, potential exposure, and modeled costs.
    """
    scores = np.array(scores)
    y_true = np.array(y_true)

    if test_df is not None and "total_amount" in test_df.columns:
        amounts = test_df["total_amount"].values
    elif test_df is not None and "total_amount_inr" in test_df.columns:
        amounts = test_df["total_amount_inr"].values
    elif test_df is not None and "amount" in test_df.columns:
        amounts = test_df["amount"].values
    elif test_df is not None and "average_amount" in test_df.columns and "txn_count_total" in test_df.columns:
        amounts = (test_df["average_amount"] * test_df["txn_count_total"]).values
    elif test_df is not None and "average_amount" in test_df.columns:
        amounts = test_df["average_amount"].values
    else:
        raise ValueError(
            "Amount data unavailable — cannot compute financial metrics without transaction amounts."
        )

    # Decisions
    decisions = np.where(scores >= tau_high, "BLOCK",
                np.where(scores >= tau_low, "REVIEW", "ALLOW"))

    tp_mask = (y_true == 1) & (decisions != "ALLOW")
    fp_mask = (y_true == 0) & (decisions != "ALLOW")
    fn_mask = (y_true == 1) & (decisions == "ALLOW")
    tn_mask = (y_true == 0) & (decisions == "ALLOW")

    fp_review_mask = (y_true == 0) & (decisions == "REVIEW")
    fp_block_mask = (y_true == 0) & (decisions == "BLOCK")

    observed_suspicious_value = float(np.sum(amounts[y_true == 1]))
    potential_exposure = float(np.sum(amounts[decisions != "ALLOW"]))
    avoidable_exposure = float(np.sum(amounts[tp_mask])) if evaluation_unit == "transaction" else None
    missed_exposure = float(np.sum(amounts[fn_mask])) if evaluation_unit == "transaction" else None

    # FP Costs
    fp_review_cost = float(np.sum(fp_review_mask) * REVIEW_COST_PER_CASE)
    fp_block_cost = float(np.sum(amounts[fp_block_mask] * BLOCK_LOST_GMV_FRACTION + BLOCK_CHURN_PENALTY))
    total_fp_cost = fp_review_cost + fp_block_cost

    net_benefit = avoidable_exposure - total_fp_cost if avoidable_exposure is not None else None
    prevention_rate = (float(avoidable_exposure / max(1.0, observed_suspicious_value))
                       if avoidable_exposure is not None else None)

    return {
        "thresholds": {
            "tau_low": tau_low,
            "tau_high": tau_high,
        },
        "decisions": {
            "allow": int(np.sum(decisions == "ALLOW")),
            "review": int(np.sum(decisions == "REVIEW")),
            "block": int(np.sum(decisions == "BLOCK")),
        },
        "confusion": {
            "true_positives": int(tp_mask.sum()),
            "false_positives": int(fp_mask.sum()),
            "false_negatives": int(fn_mask.sum()),
            "true_negatives": int(tn_mask.sum()),
        },
        "financial": {
            "observed_suspicious_value_inr": round(observed_suspicious_value, 2),
            "potential_exposure_inr": round(potential_exposure, 2),
            "estimated_avoidable_exposure_inr": round(avoidable_exposure, 2) if avoidable_exposure is not None else None,
            "missed_labeled_value_inr": round(missed_exposure, 2) if missed_exposure is not None else None,
            "false_positive_cost_inr": round(total_fp_cost, 2),
            "fp_review_cost_inr": round(fp_review_cost, 2),
            "fp_block_cost_inr": round(fp_block_cost, 2),
            "estimated_net_benefit_inr": round(net_benefit, 2) if net_benefit is not None else None,
            "prevention_rate": round(prevention_rate, 4) if prevention_rate is not None else None,
            "evaluation_unit": evaluation_unit,
            "interpretation": (
                "Avoidable exposure is unavailable for account-level evaluation; "
                "predictions and labels must be transaction-level to estimate it."
                if evaluation_unit != "transaction"
                else "Estimated from provided labels and policy decisions; not a guarantee of prevention."
            ),
        },
        "cost_assumptions": {
            "review_cost_per_case_inr": REVIEW_COST_PER_CASE,
            "block_lost_gmv_fraction": BLOCK_LOST_GMV_FRACTION,
            "block_churn_penalty_inr": BLOCK_CHURN_PENALTY,
            "disclaimer": "Illustrative cost model for synthetic evaluation — actual deployment requires merchant-specific unit economics.",
        },
    }


def calculate_financial_impact(features_df, transactions_df, tau_low=DEFAULT_TAU_LOW, tau_high=DEFAULT_TAU_HIGH):
    """
    Convenience wrapper for evaluation orchestrator pipeline.
    """
    if "predicted_prob" not in features_df.columns or "label_suspicious" not in features_df.columns:
        raise ValueError("Financial impact estimate unavailable without predictions and reliable labels.")
    scores = features_df["predicted_prob"].values
    y_true = features_df["label_suspicious"].values

    return compute_financial_metrics(scores, y_true, features_df, tau_low, tau_high, evaluation_unit="account")


def threshold_sweep_financial(scores, y_true, test_df=None):
    """
    Sweep threshold from 0.05 to 0.95 and calculate financial impact for dashboard.
    """
    sweep = []
    scores = np.array(scores)
    y_true = np.array(y_true)

    if test_df is not None and "total_amount" in test_df.columns:
        amounts = test_df["total_amount"].values
    elif test_df is not None and "total_amount_inr" in test_df.columns:
        amounts = test_df["total_amount_inr"].values
    elif test_df is not None and "amount" in test_df.columns:
        amounts = test_df["amount"].values
    elif test_df is not None and "average_amount" in test_df.columns and "txn_count_total" in test_df.columns:
        amounts = (test_df["average_amount"] * test_df["txn_count_total"]).values
    elif test_df is not None and "average_amount" in test_df.columns:
        amounts = test_df["average_amount"].values
    else:
        raise ValueError(
            "Amount data unavailable — cannot sweep financial thresholds without transaction amounts."
        )

    for thresh in np.arange(0.05, 0.96, 0.05):
        flagged = scores >= thresh
        tp = (y_true == 1) & flagged
        fp = (y_true == 0) & flagged

        prevented = float(np.sum(amounts[tp]))
        fp_cost = float(np.sum(fp) * REVIEW_COST_PER_CASE)
        net = prevented - fp_cost

        sweep.append({
            "threshold": round(float(thresh), 2),
            "estimated_avoidable_exposure_inr": None,
            "false_positive_cost_inr": round(fp_cost, 2),
            "estimated_net_benefit_inr": None,
            "evaluation_unit": "threshold-sweep",
            "interpretation": "Threshold sweep does not establish avoidable exposure without transaction-level validation.",
            "flagged_count": int(flagged.sum()),
        })

    return sweep
