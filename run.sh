#!/bin/bash
set -e

echo "Starting Fraud-Spike Sentinel..."
echo ""

PORT="${PORT:-7860}"

if [ ! -f "models/gbm_calibrated.pkl" ]; then
    echo "ERROR: Model artifacts missing."
    echo "Run this once to generate them:"
    echo "  python3 -m evaluation.run_all"
    exit 1
fi

echo "Starting server on http://0.0.0.0:${PORT}"
echo ""

python3 -m uvicorn backend.app:app --host 0.0.0.0 --port "${PORT}"
