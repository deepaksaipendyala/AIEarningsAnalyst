"""Pipeline orchestrator: ingest -> extract -> verify -> store.

Coordinates the full pipeline for all companies and quarters.
"""

import json
import time
from datetime import datetime
from pathlib import Path

from backend.config import settings
from backend.services.ingestion.fmp_client import FMPClient, load_fmp_data
from backend.services.ingestion.transcript_client import fetch_transcript
from backend.services.extraction.llm_extractor import ClaimExtractor
from backend.services.verification.verdict_engine import verify_single_claim


def load_companies() -> list[dict]:
    """Load company list from config."""
    path = settings.data_dir / "companies.json"
    with open(path) as f:
        return json.load(f)


def determine_quarters() -> list[tuple[int, int]]:
    """Determine the last 4 reported quarters.

    Uses the most recent quarters where both earnings transcripts
    and financial data are likely available. As of Feb 2026, most
    companies have reported through their most recent fiscal quarter.
    """
    # Most recent 4 fiscal quarters with data available across most companies.
    # FMP free tier returns 5 quarters max, so all 4 should have financial data.
    return [
        (2025, 4),
        (2025, 3),
        (2025, 2),
        (2025, 1),
    ]


def run_ingestion(ticker: str = None):
    """Fetch transcripts and financial data for all (or one) company."""
    settings.ensure_dirs()
    companies = load_companies()
    quarters = determine_quarters()

    if ticker:
        companies = [c for c in companies if c["ticker"] == ticker.upper()]

    fmp = FMPClient()

    for company in companies:
        t = company["ticker"]
        print(f"\n{'='*50}")
        print(f"Ingesting {t} ({company['name']})")
        print(f"{'='*50}")

        # Transcripts (earningscall → FMP fallback)
        print("\n[Transcripts]")
        for year, quarter in quarters:
            fetch_transcript(t, year, quarter)
            time.sleep(0.5)

        # Financials via FMP
        print("\n[Financials - FMP]")
        fmp.fetch_and_cache_financials(t)

        time.sleep(1)

    print("\nIngestion complete.")


def run_extraction(ticker: str = None):
    """Extract claims from all cached transcripts."""
    settings.ensure_dirs()
    extractor = ClaimExtractor()
    quarters = determine_quarters()
    companies = load_companies()

    if ticker:
        companies = [c for c in companies if c["ticker"] == ticker.upper()]

    for company in companies:
        t = company["ticker"]
        print(f"\n[Extraction] {t}")
        for year, quarter in quarters:
            extractor.extract_and_cache(t, year, quarter)


def run_verification(ticker: str = None):
    """Verify all extracted claims against financial data."""
    settings.ensure_dirs()
    verdicts_dir = settings.verdicts_dir
    verdicts_dir.mkdir(parents=True, exist_ok=True)

    quarters = determine_quarters()
    companies = load_companies()

    if ticker:
        companies = [c for c in companies if c["ticker"] == ticker.upper()]

    summary = {"total": 0, "verified": 0, "close_match": 0, "mismatch": 0,
               "misleading": 0, "unverifiable": 0}

    for company in companies:
        t = company["ticker"]
        fmp_data = load_fmp_data(t)

        for year, quarter in quarters:
            key = f"{t}_Q{quarter}_{year}"
            claims_path = settings.claims_dir / f"{key}_claims.json"
            verdict_path = verdicts_dir / f"{key}_verdicts.json"

            if not claims_path.exists():
                continue

            with open(claims_path) as f:
                claim_data = json.load(f)

            claims = claim_data.get("claims", [])
            if not claims:
                continue

            print(f"  [VERIFY] {key}: {len(claims)} claims...", end=" ")

            verdicts = []
            for claim in claims:
                v = verify_single_claim(claim, t, year, quarter, fmp_data)
                verdicts.append({"claim": claim, "verification": v})
                verdict_label = v.get("verdict", "unverifiable")
                summary["total"] += 1
                if verdict_label in summary:
                    summary[verdict_label] += 1

            result = {
                "ticker": t,
                "key": key,
                "year": year,
                "quarter": quarter,
                "verified_at": datetime.now().isoformat(),
                "claims_with_verdicts": verdicts,
                "summary": {
                    "total": len(verdicts),
                    "verified": sum(1 for v in verdicts if v["verification"]["verdict"] == "verified"),
                    "close_match": sum(1 for v in verdicts if v["verification"]["verdict"] == "close_match"),
                    "mismatch": sum(1 for v in verdicts if v["verification"]["verdict"] == "mismatch"),
                    "misleading": sum(1 for v in verdicts if v["verification"]["verdict"] == "misleading"),
                    "unverifiable": sum(1 for v in verdicts if v["verification"]["verdict"] == "unverifiable"),
                },
            }

            with open(verdict_path, "w") as f:
                json.dump(result, f, indent=2)

            s = result["summary"]
            print(f"✓{s['verified']} ≈{s['close_match']} ✗{s['mismatch']} ⚠{s['misleading']} ?{s['unverifiable']}")

    print(f"\n{'='*50}")
    print("OVERALL SUMMARY")
    for k, v in summary.items():
        print(f"  {k}: {v}")


def run_full_pipeline(ticker: str = None):
    """Run the complete pipeline: ingest -> extract -> verify."""
    print("=" * 60)
    print("EARNINGS CALL VERIFICATION PIPELINE")
    print("=" * 60)

    print("\n[Phase 1] Data Ingestion")
    run_ingestion(ticker)

    print("\n[Phase 2] Claim Extraction")
    run_extraction(ticker)

    print("\n[Phase 3] Verification")
    run_verification(ticker)

    print("\n" + "=" * 60)
    print("Pipeline complete!")
