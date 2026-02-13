"""Dashboard API routes - aggregate stats for the frontend."""

import json
from pathlib import Path

from fastapi import APIRouter

from backend.config import settings

router = APIRouter()


def _load_all_verdicts() -> dict:
    """Load all verdict files into a structured dict."""
    verdicts_dir = settings.verdicts_dir
    all_data = {}
    if not verdicts_dir.exists():
        return all_data
    for vf in sorted(verdicts_dir.glob("*_verdicts.json")):
        with open(vf) as f:
            data = json.load(f)
        key = data.get("key", vf.stem.replace("_verdicts", ""))
        all_data[key] = data
    return all_data


def _load_companies() -> list[dict]:
    path = settings.data_dir / "companies.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


@router.get("/summary")
def get_dashboard_summary():
    """Get aggregate stats across all companies and quarters."""
    all_verdicts = _load_all_verdicts()
    companies = _load_companies()
    ticker_to_name = {c["ticker"]: c.get("name", c["ticker"]) for c in companies}
    ticker_to_sector = {c["ticker"]: c.get("sector", "") for c in companies}

    total = {"verified": 0, "close_match": 0, "mismatch": 0, "misleading": 0, "unverifiable": 0, "total": 0}

    by_ticker = {}
    for key, data in all_verdicts.items():
        ticker = data.get("ticker", key.split("_")[0])
        s = data.get("summary", {})
        for k in total:
            total[k] += s.get(k, 0)
        if ticker not in by_ticker:
            by_ticker[ticker] = {"quarters": 0, "total": 0, "verified": 0, "close_match": 0,
                                  "mismatch": 0, "misleading": 0, "unverifiable": 0}
        by_ticker[ticker]["quarters"] += 1
        for k in ["total", "verified", "close_match", "mismatch", "misleading", "unverifiable"]:
            by_ticker[ticker][k] += s.get(k, 0)

    company_list = []
    for ticker, stats in sorted(by_ticker.items()):
        company_list.append({
            "ticker": ticker,
            "name": ticker_to_name.get(ticker, ticker),
            "sector": ticker_to_sector.get(ticker, ""),
            "quarters_available": stats["quarters"],
            "total_claims": stats["total"],
            "verified_count": stats["verified"] + stats["close_match"],
            "mismatch_count": stats["mismatch"],
            "misleading_count": stats["misleading"],
            "unverifiable_count": stats["unverifiable"],
        })

    return {
        "total_claims": total["total"],
        "verified": total["verified"],
        "close_match": total["close_match"],
        "mismatch": total["mismatch"],
        "misleading": total["misleading"],
        "unverifiable": total["unverifiable"],
        "companies": company_list,
    }


@router.get("/companies")
def get_companies():
    """List all companies with their metadata."""
    return _load_companies()


@router.get("/companies/{ticker}")
def get_company_detail(ticker: str):
    """Get detailed verdict data for a specific company."""
    all_verdicts = _load_all_verdicts()
    entries = {k: v for k, v in all_verdicts.items() if v.get("ticker") == ticker.upper()}
    return {"ticker": ticker.upper(), "quarters": entries}


@router.get("/transcripts/{ticker}/{year}/{quarter}")
def get_transcript(ticker: str, year: int, quarter: int):
    """Get transcript text for a specific quarter."""
    key = f"{ticker.upper()}_Q{quarter}_{year}"
    path = settings.transcripts_dir / f"{key}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"error": f"Transcript not found: {key}"}


@router.get("/claims/{ticker}/{year}/{quarter}")
def get_claims_for_quarter(ticker: str, year: int, quarter: int):
    """Get all claims with verdicts for a specific quarter."""
    key = f"{ticker.upper()}_Q{quarter}_{year}"
    path = settings.verdicts_dir / f"{key}_verdicts.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"error": f"Verdicts not found: {key}", "claims_with_verdicts": []}
