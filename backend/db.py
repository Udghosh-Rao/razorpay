"""
Fraud-Spike Sentinel — Database Module (SQLite)

Schema:
  - customers
  - merchants
  - devices
  - transactions
  - risk_events
  - clusters
  - decisions (stores model_version, feature_version, policy_version)
  - audit_log
"""

import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "burstguard.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Customers table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        customer_id TEXT PRIMARY KEY,
        scenario TEXT,
        label_suspicious INTEGER,
        signup_date TEXT,
        region TEXT,
        primary_device TEXT,
        bank TEXT,
        num_instruments INTEGER,
        instruments TEXT
    )
    """)

    # Merchants table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS merchants (
        merchant_id TEXT PRIMARY KEY,
        category TEXT,
        region TEXT,
        avg_daily_txns REAL,
        avg_txn_amount REAL,
        baseline_suspicious_rate REAL
    )
    """)

    # Devices table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        device_type TEXT,
        os_version TEXT,
        first_seen TEXT
    )
    """)

    # Transactions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        txn_id TEXT PRIMARY KEY,
        customer_id TEXT,
        merchant_id TEXT,
        timestamp TEXT,
        amount REAL,
        status TEXT,
        payment_method TEXT,
        device_id TEXT,
        region TEXT,
        bank TEXT,
        is_disputed INTEGER,
        is_suspicious INTEGER
    )
    """)

    # Risk Events / Spikes table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS risk_events (
        event_id TEXT PRIMARY KEY,
        merchant_id TEXT,
        event_type TEXT,
        severity TEXT,
        window_start TEXT,
        window_end TEXT,
        recent_txns INTEGER,
        recent_suspicious INTEGER,
        recent_rate REAL,
        baseline_rate REAL,
        fold_increase REAL,
        wilson_ci_lower REAL,
        wilson_ci_upper REAL,
        p_value REAL,
        description TEXT,
        created_at TEXT
    )
    """)

    # Clusters table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clusters (
        cluster_id TEXT PRIMARY KEY,
        member_count INTEGER,
        risk_score REAL,
        total_transactions INTEGER,
        failure_rate REAL,
        shared_devices TEXT,
        shared_instruments TEXT,
        customer_ids TEXT,
        created_at TEXT
    )
    """)

    # Decisions table (with versioning audit)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS decisions (
        decision_id TEXT PRIMARY KEY,
        entity_id TEXT,
        entity_type TEXT,
        decision TEXT,
        canonical_risk_score REAL,
        model_version TEXT,
        feature_version TEXT,
        policy_version TEXT,
        sub_scores TEXT,
        reasoning TEXT,
        created_at TEXT
    )
    """)

    # Audit log table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        entity_type TEXT,
        entity_id TEXT,
        details TEXT,
        timestamp TEXT
    )
    """)

    conn.commit()
    conn.close()
    print("[DB] Initialized database schema successfully.")


def log_audit_event(action, entity_type, entity_id, details=None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO audit_log (action, entity_type, entity_id, details, timestamp) VALUES (?, ?, ?, ?, ?)",
        (action, entity_type, entity_id, json.dumps(details) if details else None, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def clear_operational_data(conn=None):
    """Clear evaluation-derived rows before reloading from pipeline artifacts."""
    close_conn = conn is None
    if conn is None:
        conn = get_db()
    cursor = conn.cursor()
    for table in (
        "decisions", "risk_events", "clusters", "transactions",
        "customers", "merchants", "devices",
    ):
        cursor.execute(f"DELETE FROM {table}")
    conn.commit()
    if close_conn:
        conn.close()


def populate_evaluation_data(customers_df, merchants_df, transactions_df, devices_df, spikes, clusters, decisions=None):
    """Load synthetic evaluation dataset and detection outputs into SQLite."""
    conn = get_db()
    clear_operational_data(conn)
    cursor = conn.cursor()

    for _, row in customers_df.iterrows():
        cursor.execute(
            """INSERT OR REPLACE INTO customers
               (customer_id, scenario, label_suspicious, signup_date, region, primary_device, bank, num_instruments, instruments)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["customer_id"], row.get("scenario"), int(row.get("label_suspicious", 0)),
                str(row.get("signup_date")), row.get("region"), row.get("primary_device"),
                row.get("bank"), int(row.get("num_instruments", 0)), row.get("instruments"),
            ),
        )

    for _, row in merchants_df.iterrows():
        cursor.execute(
            """INSERT OR REPLACE INTO merchants
               (merchant_id, category, region, avg_daily_txns, avg_txn_amount, baseline_suspicious_rate)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                row["merchant_id"], row.get("category"), row.get("region"),
                float(row.get("avg_daily_txns", 0)), float(row.get("avg_txn_amount", 0)),
                float(row.get("baseline_suspicious_rate", 0)),
            ),
        )

    for _, row in devices_df.iterrows():
        cursor.execute(
            """INSERT OR REPLACE INTO devices (device_id, device_type, os_version, first_seen)
               VALUES (?, ?, ?, ?)""",
            (row["device_id"], row.get("device_type"), row.get("os_version"), str(row.get("first_seen"))),
        )

    for _, row in transactions_df.iterrows():
        cursor.execute(
            """INSERT OR REPLACE INTO transactions
               (txn_id, customer_id, merchant_id, timestamp, amount, status, payment_method,
                device_id, region, bank, is_disputed, is_suspicious)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["txn_id"], row["customer_id"], row.get("merchant_id"), str(row["timestamp"]),
                float(row["amount"]), row.get("status"), row.get("payment_method"),
                row.get("device_id"), row.get("region"), row.get("bank"),
                int(row.get("is_disputed", 0)), int(row.get("is_suspicious", 0)),
            ),
        )

    for i, spike in enumerate(spikes):
        event_id = spike.get("event_id") or f"R-{i + 1:04d}"
        cursor.execute(
            """INSERT OR REPLACE INTO risk_events
               (event_id, merchant_id, event_type, severity, window_start, window_end,
                recent_txns, recent_suspicious, recent_rate, baseline_rate, fold_increase,
                wilson_ci_lower, wilson_ci_upper, p_value, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id, spike.get("merchant_id"), spike.get("event_type", "SUSPICIOUS_ACTIVITY_SPIKE"),
                spike.get("severity"), spike.get("window_start"), spike.get("window_end"),
                spike.get("recent_txns"), spike.get("recent_suspicious"), spike.get("recent_rate"),
                spike.get("baseline_rate"), spike.get("fold_increase"),
                spike.get("wilson_ci_lower"), spike.get("wilson_ci_upper"), spike.get("p_value"),
                spike.get("description"), datetime.now().isoformat(),
            ),
        )

    for cluster in clusters:
        cursor.execute(
            """INSERT OR REPLACE INTO clusters
               (cluster_id, member_count, risk_score, total_transactions, failure_rate,
                shared_devices, shared_instruments, customer_ids, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cluster["cluster_id"], cluster.get("member_count"), cluster.get("risk_score"),
                cluster.get("total_transactions"), cluster.get("failure_rate"),
                json.dumps(cluster.get("shared_devices", [])),
                json.dumps(cluster.get("shared_instruments", [])),
                json.dumps(cluster.get("customer_ids", [])),
                datetime.now().isoformat(),
            ),
        )

    if decisions:
        for decision in decisions:
            cursor.execute(
                """INSERT OR REPLACE INTO decisions
                   (decision_id, entity_id, entity_type, decision, canonical_risk_score,
                    model_version, feature_version, policy_version, sub_scores, reasoning, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision["decision_id"], decision["entity_id"], decision.get("entity_type", "account"),
                    decision["decision"], decision["canonical_risk_score"],
                    decision.get("model_version"), decision.get("feature_version"),
                    decision.get("policy_version"), json.dumps(decision.get("sub_scores", {})),
                    decision.get("reasoning"), decision.get("created_at", datetime.now().isoformat()),
                ),
            )

    conn.commit()
    conn.close()


def get_entity_evidence(entity_id, entity_type="account"):
    """Retrieve stored evidence for an entity from the database."""
    entity_type = (entity_type or "account").strip().lower()
    conn = get_db()
    cursor = conn.cursor()
    evidence = {"entity_id": entity_id, "entity_type": entity_type, "found": False}

    if entity_type == "account":
        cursor.execute("SELECT * FROM customers WHERE customer_id = ?", (entity_id,))
        customer = cursor.fetchone()
        if customer:
            evidence["found"] = True
            evidence["customer"] = dict(customer)

        cursor.execute(
            "SELECT * FROM transactions WHERE customer_id = ? ORDER BY timestamp DESC LIMIT 20",
            (entity_id,),
        )
        recent_txns = [dict(row) for row in cursor.fetchall()]
        evidence["recent_transactions"] = recent_txns
        if recent_txns:
            evidence["found"] = True

        cursor.execute("SELECT * FROM decisions WHERE entity_id = ? ORDER BY created_at DESC LIMIT 1", (entity_id,))
        decision = cursor.fetchone()
        if decision:
            evidence["found"] = True
            row = dict(decision)
            if row.get("sub_scores"):
                try:
                    row["sub_scores"] = json.loads(row["sub_scores"])
                except Exception:
                    pass
            evidence["decision"] = row

        cursor.execute("SELECT * FROM clusters")
        for cluster_row in cursor.fetchall():
            cluster = dict(cluster_row)
            try:
                members = json.loads(cluster.get("customer_ids") or "[]")
            except Exception:
                members = []
            if entity_id in members:
                try:
                    cluster["shared_devices"] = json.loads(cluster.get("shared_devices") or "[]")
                except Exception:
                    pass
                try:
                    cluster["shared_instruments"] = json.loads(cluster.get("shared_instruments") or "[]")
                except Exception:
                    pass
                evidence["cluster"] = cluster
                break

    elif entity_type == "merchant":
        cursor.execute("SELECT * FROM merchants WHERE merchant_id = ?", (entity_id,))
        merchant = cursor.fetchone()
        if merchant:
            evidence["found"] = True
            evidence["merchant"] = dict(merchant)

        cursor.execute("SELECT * FROM risk_events WHERE merchant_id = ? ORDER BY created_at DESC", (entity_id,))
        evidence["risk_events"] = [dict(row) for row in cursor.fetchall()]
        if evidence["risk_events"]:
            evidence["found"] = True

        cursor.execute(
            "SELECT COUNT(*) AS txn_count, SUM(amount) AS total_amount FROM transactions WHERE merchant_id = ?",
            (entity_id,),
        )
        stats = cursor.fetchone()
        if stats and stats["txn_count"]:
            evidence["found"] = True
            evidence["transaction_stats"] = dict(stats)

    elif entity_type == "cluster":
        cursor.execute("SELECT * FROM clusters WHERE cluster_id = ?", (entity_id,))
        cluster = cursor.fetchone()
        if cluster:
            evidence["found"] = True
            row = dict(cluster)
            row["shared_devices"] = json.loads(row.get("shared_devices") or "[]")
            row["shared_instruments"] = json.loads(row.get("shared_instruments") or "[]")
            row["customer_ids"] = json.loads(row.get("customer_ids") or "[]")
            evidence["cluster"] = row

    conn.close()
    return evidence


if __name__ == "__main__":
    init_db()
