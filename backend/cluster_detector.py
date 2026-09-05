"""
Fraud-Spike Sentinel — Coordinated Fraud Cluster Detector

Identifies network clusters of accounts sharing hardware devices.
Computes a ClusterRiskScore ∈ [0, 1] for each customer.
Prevents mega-component merging by focusing on shared devices and capping cluster bounds.
"""

import pandas as pd
import numpy as np
from collections import defaultdict


def detect_clusters(transactions_df, customers_df=None):
    """
    Build entity graph across devices to detect coordinated fraud clusters (3 to 50 accounts).

    Returns:
        clusters_list: List of cluster objects with risk scores and members.
        customer_cluster_risk: Dict mapping customer_id -> cluster_risk_score.
    """
    if len(transactions_df) == 0:
        return [], {}

    df = transactions_df.copy()

    # Map devices to customers
    device_to_custs = defaultdict(set)

    for _, row in df.iterrows():
        cust_id = row.get("customer_id")
        dev_id = row.get("device_id")

        if cust_id and dev_id and pd.notna(dev_id):
            device_to_custs[dev_id].add(cust_id)

    # Find devices shared by >= 2 accounts
    shared_devices = {dev: custs for dev, custs in device_to_custs.items() if len(custs) >= 2}

    # Adjacency list for graph components
    adj = defaultdict(set)

    for dev, custs in shared_devices.items():
        cust_list = list(custs)
        for i in range(len(cust_list)):
            for j in range(i + 1, len(cust_list)):
                adj[cust_list[i]].add(cust_list[j])
                adj[cust_list[j]].add(cust_list[i])

    # Connected components
    visited = set()
    clusters = []
    customer_cluster_risk = {}

    cluster_counter = 1
    for cust in list(adj.keys()):
        if cust not in visited:
            component = []
            queue = [cust]
            visited.add(cust)

            while queue:
                node = queue.pop(0)
                component.append(node)
                for neighbor in adj[node]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            # Filter valid fraud cluster size (3 to 50 accounts)
            if 3 <= len(component) <= 50:
                cluster_txns = df[df["customer_id"].isin(component)]
                failed_count = (cluster_txns["status"].isin(["failed", "declined", "disputed"])).sum() if "status" in cluster_txns.columns else 0
                failure_rate = failed_count / max(1, len(cluster_txns))

                shared_dev_ids = [dev for dev, custs in shared_devices.items() if any(c in component for c in custs)]
                # Component size is context, not proof. Keep the network
                # signal bounded so a large connected component cannot force
                # every member into extreme risk.
                network_strength = min(len(shared_dev_ids), 3) / 3.0
                risk_score = min(0.75, 0.15 + (network_strength * 0.2) + (failure_rate * 0.3))
                shared_inst_types = list(cluster_txns["payment_method"].dropna().unique()) if "payment_method" in cluster_txns.columns else []

                cluster_obj = {
                    "cluster_id": f"cluster_{cluster_counter:03d}",
                    "member_count": len(component),
                    "customer_ids": component,
                    "risk_score": round(float(risk_score), 4),
                    "total_transactions": len(cluster_txns),
                    "failure_rate": round(float(failure_rate), 4),
                    "shared_devices": shared_dev_ids[:5],
                    "shared_instruments": shared_inst_types[:4],
                }
                clusters.append(cluster_obj)
                cluster_counter += 1

                for c_id in component:
                    customer_cluster_risk[c_id] = round(float(risk_score), 4)

    return clusters, customer_cluster_risk
