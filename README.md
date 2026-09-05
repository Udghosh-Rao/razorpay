---
title: Sentinel - Payment Risk Intelligence
emoji: 🛡️
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Sentinel

### Sentinel — Payment risk intelligence
**Find suspicious payment activity before it becomes a costly problem.**

Analyze transaction data to identify unusual behavior, emerging fraud risk, potential financial exposure, and the evidence behind each alert.

This product helps merchants identify suspicious payment activity early, understand why it is risky, estimate potential financial exposure, and decide what action to take.

## Product goals

- Detect emerging fraud spikes early
- Surface the merchants and customers contributing to the risk
- Estimate financial exposure and policy impact
- Explain the decision in plain language for technical and non-technical users
- Keep the app honest: when data is missing, show an empty state instead of fabricated values

## Running locally

1. Install dependencies:
   ```bash
   python3 -m pip install -r requirements.txt
   ```
2. Start the app:
   ```bash
   ./run.sh
   ```
3. Open http://localhost:7860 (or port specified in `$PORT`)

## Uploading payment data

The app accepts transaction CSVs in a range of common formats. It automatically maps common aliases for identifiers, amounts, timestamps, merchant IDs, device IDs, payment methods, and ground-truth labels.

Model scoring is capability-gated. A file must contain the raw dimensions needed to compute the trained feature set (account, timestamp, amount, merchant, device, payment method, and status). Smaller files still receive supported statistical analysis, but the app explicitly reports that model risk scoring is unavailable rather than substituting zeros for missing features.

Examples of supported aliases include:
- account_id / customer_id / user_id
- amount / payment_amount / value
- timestamp / date / created_at
- transaction_id / txn_id / payment_id
- merchant_id / merchant
- device_id / device
- payment_method / method
- fraud_label / is_fraud / label

Use the CSV template endpoint for a starting point:

```bash
curl -sS http://localhost:8000/api/csv-template -o payment_transaction_template.csv
```

The app only uses columns that are actually present in the uploaded file. Missing information stays missing instead of being invented.

The runtime does not train models on startup. Model artifacts are produced by the evaluation workflow and shipped with the application image or deployment bundle.

## Important rule

This project does not fabricate model metrics or dashboard data. If results are not available, the app shows an empty state or a clear explanation instead of fake numbers.
