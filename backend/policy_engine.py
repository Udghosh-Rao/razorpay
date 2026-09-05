"""
Fraud-Spike Sentinel — Central Policy Engine

SINGLE SOURCE OF TRUTH for operational risk decisions across the product.
Replaces all hardcoded decision thresholds in frontend, API endpoints, or agents.

Inputs:
  - Canonical Risk Score ∈ [0, 1]
  - Decision Context (amount, merchant, cluster flag, etc.)

Outputs:
  - Decision: ALLOW | REVIEW | BLOCK
  - Applied Thresholds: (tau_low, tau_high)
  - Policy Version: "v1.0.0"
  - Decision Reason / Rule Breakdown
"""

POLICY_VERSION = "v1.0.0"

# Operational Thresholds (Configurable per environment)
DEFAULT_TAU_LOW = 0.30     # Risk < 0.30 -> ALLOW
DEFAULT_TAU_HIGH = 0.70    # Risk >= 0.70 -> BLOCK


class PolicyEngine:
    def __init__(self, tau_low=DEFAULT_TAU_LOW, tau_high=DEFAULT_TAU_HIGH, version=POLICY_VERSION):
        self.tau_low = tau_low
        self.tau_high = tau_high
        self.version = version

    def evaluate(self, risk_score, context=None):
        """
        Evaluate a canonical risk score and optional context against policy rules.

        Args:
            risk_score: float ∈ [0, 1]
            context: dict with transaction/account details

        Returns:
            dict containing decision, reasoning, policy_version, applied thresholds.
        """
        if context is None:
            context = {}

        score = float(risk_score)
        amount = context.get("amount", 0.0)
        is_cluster_member = context.get("is_cluster_member", False)
        is_merchant_spike = context.get("is_merchant_spike", False)

        reasons = []

        # Network and spike signals are already part of the fused score. They
        # provide context, but neither is proof of harmful activity on its own.
        if score >= self.tau_high:
            decision = "BLOCK"
            reasons.append(f"Canonical risk score {score:.3f} >= block threshold {self.tau_high:.2f}")
        elif score >= self.tau_low:
            decision = "REVIEW"
            reasons.append(f"Canonical risk score {score:.3f} in review band [{self.tau_low:.2f}, {self.tau_high:.2f})")
        else:
            decision = "ALLOW"
            reasons.append(f"Canonical risk score {score:.3f} < allow threshold {self.tau_low:.2f}")

        if is_cluster_member:
            reasons.append("Related activity was considered as contextual network evidence")
        if is_merchant_spike:
            reasons.append("A merchant activity change was considered as contextual temporal evidence")

        return {
            "decision": decision,
            "canonical_risk_score": round(score, 4),
            "policy_version": self.version,
            "thresholds": {
                "tau_low": self.tau_low,
                "tau_high": self.tau_high,
            },
            "reasoning": "; ".join(reasons),
            "override_applied": False,
        }

    def evaluate_batch(self, risk_scores, contexts=None):
        """Batch evaluation for lists of scores."""
        if contexts is None:
            contexts = [{}] * len(risk_scores)
        return [self.evaluate(score, ctx) for score, ctx in zip(risk_scores, contexts)]


# Global default engine instance
DEFAULT_POLICY_ENGINE = PolicyEngine()


def evaluate_policy(risk_score, context=None, tau_low=DEFAULT_TAU_LOW, tau_high=DEFAULT_TAU_HIGH):
    """Convenience function for direct policy calls."""
    engine = PolicyEngine(tau_low=tau_low, tau_high=tau_high)
    return engine.evaluate(risk_score, context)
