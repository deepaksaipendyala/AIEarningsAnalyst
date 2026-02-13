#!/usr/bin/env python3
"""Scrape latest Motley Fool transcript URLs from quote pages.

Usage:
    python scripts/scrape_fool_transcript_urls.py
    python scripts/scrape_fool_transcript_urls.py --ticker NVDA
    python scripts/scrape_fool_transcript_urls.py --limit 4
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.ingestion.fool_scraper import get_latest_transcript_urls


def load_tickers(companies_path: Path) -> list[str]:
    with open(companies_path) as f:
        companies = json.load(f)
    return [c["ticker"].upper() for c in companies]


def main():
    parser = argparse.ArgumentParser(
        description="Scrape latest Motley Fool earnings transcript URLs"
    )
    parser.add_argument(
        "--ticker",
        type=str,
        help="Optional single ticker to scrape (for example: NVDA)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=4,
        help="How many transcript URLs per ticker to return",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Hide scraper diagnostics and only print final URLs",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        help="Optional anchor year for websearch prompts (for example: 2025)",
    )
    parser.add_argument(
        "--start-quarter",
        type=int,
        choices=[1, 2, 3, 4],
        help="Optional anchor quarter for websearch prompts (1-4)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    companies_path = root / "data" / "companies.json"

    if (args.start_year is None) ^ (args.start_quarter is None):
        parser.error("--start-year and --start-quarter must be provided together")

    tickers = [args.ticker.upper()] if args.ticker else load_tickers(companies_path)
    results = {}
    debug = not args.quiet

    for ticker in tickers:
        if debug:
            print(f"\n[RUN] Processing {ticker}...")
        urls = get_latest_transcript_urls(
            ticker,
            limit=max(1, args.limit),
            debug=debug,
            start_year=args.start_year,
            start_quarter=args.start_quarter,
        )
        results[ticker] = urls
        print(f"\n{ticker} ({len(urls)}):")
        for idx, url in enumerate(urls, start=1):
            print(f"  {idx}. {url}")

    output_path = root / "data" / "fool_transcript_urls.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved results to {output_path}")


if __name__ == "__main__":
    main()
