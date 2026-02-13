#!/usr/bin/env python3
"""Fetch and cache SEC Company Facts metrics for project tickers.

Usage:
    python scripts/fetch_sec_financials.py
    python scripts/fetch_sec_financials.py --ticker AAPL
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings
from backend.services.ingestion.sec_client import fetch_and_cache_sec_metrics


def _load_tickers() -> list[str]:
    path = settings.data_dir / "companies.json"
    with open(path) as f:
        companies = json.load(f)
    return [c["ticker"].upper() for c in companies]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SEC Company Facts metrics")
    parser.add_argument("--ticker", type=str, help="Optional single ticker")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else _load_tickers()
    ok = 0
    for ticker in tickers:
        payload = fetch_and_cache_sec_metrics(ticker)
        if payload:
            periods = len(payload.get("periods", {}))
            print(f"[OK] {ticker}: {periods} periods")
            ok += 1
        else:
            print(f"[WARN] {ticker}: no SEC data")

    print(f"\nDone. success={ok}/{len(tickers)}")


if __name__ == "__main__":
    main()
