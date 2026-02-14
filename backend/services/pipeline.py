"""Pipeline orchestrator: ingest -> extract -> verify -> store.

Coordinates the full pipeline for all companies and quarters.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

from backend.config import settings
from backend.services.extraction.normalizer import parse_period
from backend.services.ingestion.fmp_client import FMPClient, load_fmp_data
from backend.services.ingestion.sec_client import fetch_and_cache_sec_metrics
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

        # Transcripts (earningscall -> fool -> FMP -> mlq.ai final hack)
        print("\n[Transcripts]")
        for year, quarter in quarters:
            fetch_transcript(t, year, quarter)
            time.sleep(0.5)

        # Financials via FMP
        print("\n[Financials - FMP]")
        fmp.fetch_and_cache_financials(t)

        # Supplemental SEC facts (historical fallback)
        print("\n[Financials - SEC]")
        sec_payload = fetch_and_cache_sec_metrics(t)
        if sec_payload:
            num_periods = len(sec_payload.get("periods", {}))
            print(f"  [OK] {t} SEC companyfacts ({num_periods} periods)")
        else:
            print(f"  [WARN] {t} SEC companyfacts unavailable")

        time.sleep(1)

    print("\nIngestion complete.")


def run_extraction(ticker: str = None, force: bool = False):
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
            extractor.extract_and_cache(t, year, quarter, force=force)


_FOOL_URL_PERIOD_RE = re.compile(
    r"/[a-z0-9-]+-q([1-4])-(20\d{2})-earnings-call-transcript/?$",
    re.IGNORECASE,
)
_TOTAL_CONTEXTS = {"", "total", "company", "overall", "consolidated"}


def _derive_transcript_period_override(ticker: str, year: int, quarter: int) -> tuple[int, int]:
    """Use transcript metadata to recover fiscal period when file key is mislabeled."""
    path = settings.transcripts_dir / f"{ticker}_Q{quarter}_{year}.json"
    if not path.exists():
        return (year, quarter)

    try:
        with open(path) as f:
            transcript = json.load(f)
    except Exception:
        return (year, quarter)

    source_url = str(transcript.get("source_url") or "")
    match = _FOOL_URL_PERIOD_RE.search(source_url)
    if not match:
        return (year, quarter)

    url_quarter = int(match.group(1))
    url_year = int(match.group(2))
    return (url_year, url_quarter)


def _shift_period_string(period_str: str, year_delta: int) -> str:
    if not isinstance(period_str, str):
        return period_str
    parsed = parse_period(period_str)
    if not parsed:
        return period_str
    year, quarter = parsed
    shifted_year = year + year_delta
    if quarter == 0:
        return f"FY {shifted_year}"
    return f"Q{quarter} {shifted_year}"


def _claim_with_period_shift(claim: dict, year_delta: int) -> dict:
    if year_delta == 0:
        return claim
    shifted = dict(claim)
    for key in ("period", "comparison_period"):
        if key in shifted:
            shifted[key] = _shift_period_string(shifted.get(key), year_delta)
    return shifted


def _downgrade_conflicting_mismatches(verdicts: list[dict]) -> None:
    """Downgrade obvious in-transcript contradictions to unverifiable."""
    grouped: dict[tuple, list[int]] = {}
    for idx, item in enumerate(verdicts):
        claim = item.get("claim", {})
        verification = item.get("verification", {})
        if claim.get("claim_type") != "absolute":
            continue
        if verification.get("verdict") not in {"verified", "close_match", "mismatch"}:
            continue

        ctx = (claim.get("metric_context") or "").strip().lower()
        if ctx not in _TOTAL_CONTEXTS:
            continue

        parsed_period = parse_period(claim.get("period", ""))
        if not parsed_period:
            continue

        key = (claim.get("metric_type"), parsed_period, "total")
        grouped.setdefault(key, []).append(idx)

    for indices in grouped.values():
        reference_idx = next(
            (
                i for i in indices
                if verdicts[i]["verification"].get("verdict") in {"verified", "close_match"}
            ),
            None,
        )
        if reference_idx is None:
            continue

        reference_claim_id = verdicts[reference_idx]["claim"].get("claim_id")
        for i in indices:
            verification = verdicts[i]["verification"]
            if verification.get("verdict") != "mismatch":
                continue

            diff_pct = verification.get("difference_pct")
            if diff_pct is None or abs(diff_pct) < 3.0:
                continue

            flags = verification.get("flags", [])
            if "conflicting_transcript_claim" not in flags:
                flags.append("conflicting_transcript_claim")
            verification["flags"] = flags
            verification["verdict"] = "unverifiable"
            verification["explanation"] = (
                "Transcript contains conflicting values for this same metric and period. "
                f"Another claim ({reference_claim_id}) aligns with financial data, so this value "
                "is likely a transcript/source artifact."
            )


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
            effective_year, effective_quarter = _derive_transcript_period_override(t, year, quarter)
            period_shift = effective_year - year
            if (effective_year, effective_quarter) != (year, quarter):
                print(
                    f" using transcript fiscal period Q{effective_quarter} {effective_year}...",
                    end=" ",
                )

            verdicts = []
            for claim in claims:
                claim_for_verification = _claim_with_period_shift(claim, period_shift)
                v = verify_single_claim(
                    claim_for_verification,
                    t,
                    effective_year,
                    effective_quarter,
                    fmp_data,
                )
                verdicts.append({"claim": claim, "verification": v})

            _downgrade_conflicting_mismatches(verdicts)

            file_summary = {
                "total": len(verdicts),
                "verified": sum(1 for v in verdicts if v["verification"]["verdict"] == "verified"),
                "close_match": sum(1 for v in verdicts if v["verification"]["verdict"] == "close_match"),
                "mismatch": sum(1 for v in verdicts if v["verification"]["verdict"] == "mismatch"),
                "misleading": sum(1 for v in verdicts if v["verification"]["verdict"] == "misleading"),
                "unverifiable": sum(1 for v in verdicts if v["verification"]["verdict"] == "unverifiable"),
            }
            summary["total"] += file_summary["total"]
            for label in ("verified", "close_match", "mismatch", "misleading", "unverifiable"):
                summary[label] += file_summary[label]

            result = {
                "ticker": t,
                "key": key,
                "year": year,
                "quarter": quarter,
                "verified_at": datetime.now().isoformat(),
                "claims_with_verdicts": verdicts,
                "summary": file_summary,
            }

            with open(verdict_path, "w") as f:
                json.dump(result, f, indent=2)

            s = result["summary"]
            print(f"✓{s['verified']} ≈{s['close_match']} ✗{s['mismatch']} ⚠{s['misleading']} ?{s['unverifiable']}")

    print(f"\n{'='*50}")
    print("OVERALL SUMMARY")
    for k, v in summary.items():
        print(f"  {k}: {v}")


def run_full_pipeline(ticker: str = None, force_extract: bool = False):
    """Run the complete pipeline: ingest -> extract -> verify."""
    print("=" * 60)
    print("EARNINGS CALL VERIFICATION PIPELINE")
    print("=" * 60)

    print("\n[Phase 1] Data Ingestion")
    run_ingestion(ticker)

    print("\n[Phase 2] Claim Extraction")
    run_extraction(ticker, force=force_extract)

    print("\n[Phase 3] Verification")
    run_verification(ticker)

    print("\n" + "=" * 60)
    print("Pipeline complete!")
