#!/usr/bin/env python3
"""Scrape raw transcript HTML blocks from mlq.ai pages.

Default behavior:
- Read `data/fool_transcript_urls.json`
- Infer (ticker, quarter, year) targets
- Fetch mlq.ai transcript pages
- Save raw HTML block to `data/transcripts/manual_transcripts/{TICKER}_Q{Q}_{YEAR}.md`

Examples:
    python scripts/scrape_mlq_transcripts.py
    python scripts/scrape_mlq_transcripts.py --ticker NVDA --year 2025 --quarter 1
    python scripts/scrape_mlq_transcripts.py --ticker NVDA
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MLQ_URL_TEMPLATE = "https://mlq.ai/stocks/{ticker}/earnings-call-transcript/Q{quarter}-{year}/"
FOOL_QY_PATTERN = re.compile(r"-q([1-4])-(\d{4})-earnings-call-transcript/?", re.IGNORECASE)


def _extract_transcript_block(html: str) -> str | None:
    """Extract the raw transcript HTML block matching manual transcript format."""
    pattern = re.compile(
        r'(<div class="card-body blog-post-style"[^>]*>.*?'
        r'<div class="transcript-content"[^>]*>.*?</p>\s*</div>\s*</div>)',
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if match:
        return match.group(1).strip()

    # Fallback: capture from card-body to the nearest double close if shape changes slightly.
    start = html.find('<div class="card-body blog-post-style"')
    if start == -1:
        return None

    tail = html[start:]
    end_match = re.search(r"</div>\s*</div>", tail, re.IGNORECASE | re.DOTALL)
    if not end_match:
        return None
    end = start + end_match.end()
    return html[start:end].strip()


def _load_targets_from_fool_urls(path: Path) -> list[tuple[str, int, int]]:
    """Load unique (ticker, quarter, year) targets from fool URL output JSON."""
    if not path.exists():
        return []

    with open(path) as f:
        data = json.load(f)

    targets: set[tuple[str, int, int]] = set()
    for ticker, urls in data.items():
        ticker_u = str(ticker).upper().strip()
        for url in urls or []:
            m = FOOL_QY_PATTERN.search(str(url))
            if not m:
                continue
            quarter = int(m.group(1))
            year = int(m.group(2))
            targets.add((ticker_u, quarter, year))

    return sorted(targets, key=lambda t: (t[0], -t[2], -t[1]))


def _load_tickers(companies_path: Path) -> list[str]:
    with open(companies_path) as f:
        companies = json.load(f)
    return [c["ticker"].upper() for c in companies]


def _build_targets(
    root: Path,
    ticker: str | None,
    year: int | None,
    quarter: int | None,
) -> list[tuple[str, int, int]]:
    if ticker and year and quarter:
        return [(ticker.upper(), quarter, year)]

    if ticker and (year is None and quarter is None):
        fool_targets = _load_targets_from_fool_urls(root / "data" / "fool_transcript_urls.json")
        filtered = [t for t in fool_targets if t[0] == ticker.upper()]
        if filtered:
            return filtered
        return [(ticker.upper(), q, 2025) for q in (1, 2, 3, 4)]

    if (year is None) != (quarter is None):
        raise ValueError("--year and --quarter must be provided together")

    if year is not None and quarter is not None:
        companies = _load_tickers(root / "data" / "companies.json")
        return [(t, quarter, year) for t in companies]

    # Default: all known targets from previously discovered Fool URLs.
    fool_targets = _load_targets_from_fool_urls(root / "data" / "fool_transcript_urls.json")
    if fool_targets:
        return fool_targets

    # Fallback if no URL inventory is available.
    companies = _load_tickers(root / "data" / "companies.json")
    targets: list[tuple[str, int, int]] = []
    for t in companies:
        for q in (1, 2, 3, 4):
            targets.append((t, q, 2025))
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape mlq.ai transcript pages into manual transcript files")
    parser.add_argument("--ticker", type=str, help="Optional ticker, for example NVDA")
    parser.add_argument("--year", type=int, help="Optional fiscal year, for example 2025")
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], help="Optional fiscal quarter (1-4)")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds")
    parser.add_argument("--quiet", action="store_true", help="Print only final summary")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    out_dir = root / "data" / "transcripts" / "manual_transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        targets = _build_targets(root, args.ticker, args.year, args.quarter)
    except ValueError as exc:
        parser.error(str(exc))

    if not targets:
        print("No targets found.")
        return

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
    }

    saved = 0
    misses = 0

    with httpx.Client(timeout=args.timeout, headers=headers, follow_redirects=True) as client:
        for ticker, quarter, year in targets:
            url = MLQ_URL_TEMPLATE.format(ticker=ticker, quarter=quarter, year=year)
            if not args.quiet:
                print(f"[RUN] {ticker} Q{quarter} {year} -> {url}")

            try:
                resp = client.get(url)
            except httpx.HTTPError as exc:
                misses += 1
                if not args.quiet:
                    print(f"  [MISS] request error: {exc}")
                continue

            if resp.status_code != 200:
                misses += 1
                if not args.quiet:
                    print(f"  [MISS] status={resp.status_code}")
                continue

            block = _extract_transcript_block(resp.text)
            if not block:
                misses += 1
                if not args.quiet:
                    print("  [MISS] transcript block not found")
                continue

            out_path = out_dir / f"{ticker}_Q{quarter}_{year}.md"
            with open(out_path, "w") as f:
                f.write(block + "\n")

            saved += 1
            if not args.quiet:
                print(f"  [OK] saved {out_path.name}")

    print(f"Done. saved={saved}, misses={misses}, total={len(targets)}")


if __name__ == "__main__":
    main()
