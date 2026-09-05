import io

import pandas as pd
from fastapi.testclient import TestClient

from backend.app import (
    app,
    validate_ground_truth_labels,
    validate_uploaded_transactions,
)
from backend.cluster_detector import detect_clusters
from backend.financial import compute_financial_metrics
from backend.policy_engine import evaluate_policy


client = TestClient(app)


def upload(csv_text):
    response = client.post(
        "/api/upload",
        files={"file": ("transactions.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_minimal_csv_upload_has_only_supported_capabilities():
    result = upload("timestamp,amount\n2026-01-01T00:00:00Z,10\n")
    report = result["schema_report"]
    assert result["validation_summary"]["valid_rows"] == 1
    assert result["accounts_count"] == 0
    assert report["model_scoring_available"] is False
    assert "merchant_id" in report["missing_optional_columns"]
    assert "account_id" not in report["recognized_columns"]


def test_merchant_only_csv_keeps_merchant_analysis_without_accounts():
    result = upload(
        "timestamp,amount,merchant_id\n"
        "2026-01-01T00:00:00Z,10,m1\n"
        "2026-01-01T01:00:00Z,12,m1\n"
    )
    assert result["summary"]["merchants"] == 1
    assert result["accounts_count"] == 0
    assert "merchant spike detection" not in " ".join(result["schema_report"]["disabled_analyses"])


def test_unknown_columns_are_ignored():
    result = upload("timestamp,amount,foo,bar,baz\n2026-01-01T00:00:00Z,10,x,y,z\n")
    assert result["validation_summary"]["valid_rows"] == 1
    assert result["accounts_count"] == 0


def test_invalid_labels_disable_evaluation_instead_of_becoming_zero():
    frame = pd.DataFrame({"label": ["maybe"]})
    valid, details = validate_ground_truth_labels(frame, "label")
    assert valid is False
    assert details["invalid_examples"] == ["maybe"]


def test_cluster_membership_does_not_force_block():
    assert evaluate_policy(0.55, {"is_cluster_member": True})["decision"] == "REVIEW"
    transactions = pd.DataFrame(
        [
            {
                "customer_id": f"acct-{i}",
                "device_id": "shared-device",
                "timestamp": "2026-01-01",
                "amount": 10,
                "status": "success",
                "payment_method": "card",
            }
            for i in range(10)
        ]
    )
    clusters, risks = detect_clusters(transactions)
    assert clusters
    assert max(risks.values()) <= 0.75


def test_account_financial_metrics_do_not_claim_prevention():
    result = compute_financial_metrics(
        [0.9, 0.1], [1, 0],
        pd.DataFrame({"amount": [100, 50]}),
        evaluation_unit="account",
    )["financial"]
    assert result["estimated_avoidable_exposure_inr"] is None
    assert result["prevention_rate"] is None
    assert result["evaluation_unit"] == "account"


def test_session_isolation_and_investigation_memo():
    session_id = "test-session-suite"
    headers = {"X-Session-ID": session_id}
    csv_data = (
        "account_id,timestamp,amount,merchant_id,device_id,payment_method,status\n"
        "cust_1,2026-01-01T10:00:00Z,500,m1,d1,card,success\n"
        "cust_1,2026-01-01T10:05:00Z,1200,m1,d1,card,success\n"
    )
    res = client.post(
        "/api/upload",
        files={"file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv")},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["accounts_count"] == 1

    # Verify overview has data for this session
    ov = client.get("/api/risk/overview", headers=headers)
    assert ov.status_code == 200
    assert ov.json()["session_has_analysis"] is True

    # Verify another session is empty
    ov_other = client.get("/api/risk/overview", headers={"X-Session-ID": "another-session"})
    assert ov_other.status_code == 200
    assert ov_other.json()["session_has_analysis"] is False

    # Verify investigation memo generation
    inv = client.post(
        "/api/investigations",
        json={"entity_id": "cust_1", "entity_type": "account"},
        headers=headers,
    )
    assert inv.status_code == 200
    memo = inv.json()["investigation_memo"]
    assert "INVESTIGATION MEMORANDUM" in memo
    assert "cust_1" in memo


def test_fresh_session_has_zero_dummy_data():
    headers = {"X-Session-ID": "test-empty-session"}
    ov = client.get("/api/risk/overview", headers=headers).json()
    assert ov["session_has_analysis"] is False
    assert ov["recent_spikes"] == []
    assert ov["recent_clusters"] == []
    assert ov["metrics"]["transactions_processed"] == 0
    assert ov["metrics"]["potential_exposure_inr"] is None

    events = client.get("/api/risk/events", headers=headers).json()
    assert events["events"] == []

    clusters = client.get("/api/risk/clusters", headers=headers).json()
    assert clusters["clusters"] == []

    eval_latest = client.get("/api/evaluation/latest", headers=headers).json()
    assert eval_latest["evaluation_available"] is False

    thresh = client.get("/api/evaluation/thresholds", headers=headers).json()
    assert thresh["evaluation_available"] is False



