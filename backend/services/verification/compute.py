"""Deterministic computation engine for claim verification.

All arithmetic is Python — no LLM involvement.
"""

from typing import Optional


def compute_yoy_growth(current: float, prior: float) -> Optional[float]:
    """Compute year-over-year growth as a percentage.

    Returns: growth percentage (e.g., 15.0 for 15%).
    """
    if prior == 0:
        return None
    return ((current - prior) / abs(prior)) * 100


def compute_qoq_growth(current: float, prior: float) -> Optional[float]:
    """Compute quarter-over-quarter growth as a percentage."""
    if prior == 0:
        return None
    return ((current - prior) / abs(prior)) * 100


def compute_margin(numerator: float, denominator: float) -> Optional[float]:
    """Compute margin as a percentage (e.g., gross margin = gross_profit / revenue * 100)."""
    if denominator == 0:
        return None
    return (numerator / denominator) * 100


def verify_absolute(claimed: float, actual: float) -> dict:
    """Compare absolute values, returning difference metrics."""
    diff = claimed - actual
    if actual != 0:
        pct_diff = abs(diff / actual) * 100
    else:
        pct_diff = float("inf") if claimed != 0 else 0

    return {
        "claimed": claimed,
        "actual": actual,
        "difference": diff,
        "difference_pct": pct_diff,
    }


def verify_growth(claimed_pct: float, current: float, prior: float) -> dict:
    """Compare claimed growth percentage against computed growth."""
    actual_growth = compute_yoy_growth(current, prior)
    if actual_growth is None:
        return {"error": "zero_denominator"}

    diff_pp = claimed_pct - actual_growth  # Difference in percentage points

    return {
        "claimed_pct": claimed_pct,
        "actual_pct": actual_growth,
        "current_value": current,
        "prior_value": prior,
        "difference_pp": diff_pp,
        "abs_difference_pp": abs(diff_pp),
    }


def verify_margin(claimed_margin: float, numerator: float, denominator: float) -> dict:
    """Compare claimed margin against computed margin."""
    actual_margin = compute_margin(numerator, denominator)
    if actual_margin is None:
        return {"error": "zero_denominator"}

    diff_pp = claimed_margin - actual_margin

    return {
        "claimed_margin": claimed_margin,
        "actual_margin": actual_margin,
        "numerator": numerator,
        "denominator": denominator,
        "difference_pp": diff_pp,
        "abs_difference_pp": abs(diff_pp),
    }
