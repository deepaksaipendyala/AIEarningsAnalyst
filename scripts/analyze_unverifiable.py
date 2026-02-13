#!/usr/bin/env python3
"""Analyze unverifiable claims from cached verdict files.

Usage:
    python scripts/analyze_unverifiable.py
"""

import json
from collections import Counter, defaultdict
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    verdicts_dir = root / "data" / "verdicts"

    verdict_counts = Counter()
    flags = Counter()
    explanations = Counter()
    claim_types = Counter()
    metrics = Counter()
    by_ticker = Counter()
    top_examples: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)

    for path in sorted(verdicts_dir.glob("*_verdicts.json")):
        with open(path) as f:
            data = json.load(f)
        ticker = data.get("ticker", path.stem.split("_")[0])

        for item in data.get("claims_with_verdicts", []):
            claim = item.get("claim", {})
            verification = item.get("verification", {})
            verdict = verification.get("verdict", "unverifiable")
            verdict_counts[verdict] += 1

            if verdict != "unverifiable":
                continue

            by_ticker[ticker] += 1
            claim_type = claim.get("claim_type", "unknown")
            metric = claim.get("metric_type", "unknown")
            claim_types[claim_type] += 1
            metrics[metric] += 1

            exp = verification.get("explanation", "").strip() or "NO_EXPLANATION"
            explanations[exp] += 1
            if len(top_examples[exp]) < 2:
                top_examples[exp].append(
                    (
                        ticker,
                        claim.get("claim_id", ""),
                        claim.get("period", ""),
                        claim.get("quote_text", "")[:160],
                    )
                )

            for flag in verification.get("flags", []):
                flags[flag] += 1

    total = sum(verdict_counts.values())
    unverifiable = verdict_counts["unverifiable"]
    unverif_pct = (unverifiable / total * 100) if total else 0.0

    print("=== Verdict Summary ===")
    print(f"total_claims: {total}")
    for k in ["verified", "close_match", "mismatch", "misleading", "unverifiable"]:
        print(f"{k}: {verdict_counts[k]}")
    print(f"unverifiable_rate: {unverif_pct:.1f}%")

    print("\n=== Unverifiable by Ticker ===")
    for ticker, count in by_ticker.most_common():
        print(f"{ticker}: {count}")

    print("\n=== Unverifiable by Claim Type ===")
    for claim_type, count in claim_types.most_common():
        print(f"{claim_type}: {count}")

    print("\n=== Unverifiable by Metric ===")
    for metric, count in metrics.most_common(20):
        print(f"{metric}: {count}")

    print("\n=== Top Flags ===")
    for flag, count in flags.most_common(20):
        print(f"{flag}: {count}")

    print("\n=== Top Explanations ===")
    for explanation, count in explanations.most_common(25):
        print(f"{count}: {explanation}")
        for ticker, claim_id, period, quote in top_examples[explanation]:
            print(f"  - {ticker} {claim_id} ({period}): {quote}")


if __name__ == "__main__":
    main()
