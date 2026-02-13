#!/bin/bash
# EarningsLens - Full pipeline runner
#
# Usage:
#   ./run.sh                          # Run full pipeline + launch dashboard
#   ./run.sh --ticker AAPL            # Single company
#   ./run.sh --phase ingest           # Only fetch data
#   ./run.sh --phase extract          # Only extract claims
#   ./run.sh --phase verify           # Only verify claims
#   ./run.sh --ui-only                # Just launch the dashboard

set -e

echo "========================================"
echo "  EarningsLens - Earnings Claim Verifier"
echo "========================================"

# Check for required env vars
if [ -z "$FINNHUB_API_KEY" ]; then
    echo "WARNING: FINNHUB_API_KEY not set. Transcript fetching will fail."
fi

if [ -z "$FMP_API_KEY" ]; then
    echo "WARNING: FMP_API_KEY not set. Financial data fetching will fail."
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "WARNING: ANTHROPIC_API_KEY not set. Claim extraction will be skipped."
fi

# Install dependencies
echo ""
echo "[1/2] Installing dependencies..."
pip install -q -e .

# Check for --ui-only flag
if [[ "$*" == *"--ui-only"* ]]; then
    echo ""
    echo "[2/2] Launching dashboard..."
    streamlit run frontend/app.py
    exit 0
fi

# Run pipeline
echo ""
echo "[2/2] Running pipeline..."
python scripts/run_pipeline.py "$@"

# Launch dashboard
echo ""
echo "Launching dashboard..."
streamlit run frontend/app.py
