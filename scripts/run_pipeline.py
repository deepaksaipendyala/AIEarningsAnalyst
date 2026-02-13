#!/usr/bin/env python3
"""Run the full earnings verification pipeline.

Usage:
    python scripts/run_pipeline.py                    # All companies
    python scripts/run_pipeline.py --ticker AAPL       # Single company
    python scripts/run_pipeline.py --phase ingest      # Only ingestion
    python scripts/run_pipeline.py --phase extract     # Only extraction
    python scripts/run_pipeline.py --phase verify      # Only verification
"""

import sys
import os
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.pipeline import run_full_pipeline, run_ingestion, run_extraction, run_verification


def main():
    parser = argparse.ArgumentParser(description="Run earnings verification pipeline")
    parser.add_argument("--ticker", type=str, help="Process single ticker only")
    parser.add_argument("--phase", type=str, choices=["ingest", "extract", "verify", "all"],
                        default="all", help="Run specific phase")
    args = parser.parse_args()

    if args.phase == "ingest":
        run_ingestion(args.ticker)
    elif args.phase == "extract":
        run_extraction(args.ticker)
    elif args.phase == "verify":
        run_verification(args.ticker)
    else:
        run_full_pipeline(args.ticker)


if __name__ == "__main__":
    main()
