"""Fiscal period resolution for claims verification."""

from typing import Optional

from backend.services.extraction.normalizer import parse_period


def resolve_periods(claim: dict, transcript_year: int, transcript_quarter: int) -> dict:
    """Resolve target and baseline periods for a claim.

    Returns dict with 'target' (year, quarter) and optionally 'baseline' (year, quarter).
    """
    # Parse the claim's period
    period_str = claim.get("period", "")
    parsed = parse_period(period_str)

    if parsed:
        target_year, target_quarter = parsed
    else:
        # Fall back to transcript's period
        target_year = transcript_year
        target_quarter = transcript_quarter

    # Full year claims (quarter=0) are not verifiable in MVP
    if target_quarter == 0:
        return {"target": (target_year, 0), "error": "full_year"}

    result = {"target": (target_year, target_quarter)}

    claim_type = claim.get("claim_type", "")

    if claim_type == "yoy_growth":
        # Check for explicit comparison period
        comp_period = claim.get("comparison_period")
        if comp_period:
            comp_parsed = parse_period(comp_period)
            if comp_parsed:
                result["baseline"] = comp_parsed
                return result
        # Default YoY: same quarter, previous year
        result["baseline"] = (target_year - 1, target_quarter)

    elif claim_type == "qoq_growth":
        # Previous quarter
        if target_quarter == 1:
            result["baseline"] = (target_year - 1, 4)
        else:
            result["baseline"] = (target_year, target_quarter - 1)

    return result
