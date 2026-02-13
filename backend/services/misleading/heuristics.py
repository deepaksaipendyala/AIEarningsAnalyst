"""Misleading framing detection heuristics.

Three core heuristics for MVP:
1. Cherry-picking timeframe (positive QoQ when YoY is negative)
2. GAAP/non-GAAP mixing without disclosure
3. Low-base exaggeration (huge percentage on tiny denominator)
"""

from backend.services.verification.compute import compute_yoy_growth, compute_qoq_growth


def check_cherry_picking_timeframe(claim: dict, fmp_data: dict,
                                     target_year: int, target_quarter: int) -> tuple[list, list]:
    """Flag if speaker cites positive QoQ growth when YoY is negative.

    This heuristic detects when management emphasizes a favorable sequential
    comparison while the annual trend is declining.
    """
    flags, reasons = [], []

    claim_type = claim.get("claim_type", "")
    claimed_value = claim.get("claimed_value", 0)
    metric = claim.get("metric_type", "")

    if claim_type != "qoq_growth" or claimed_value <= 0:
        return flags, reasons

    # Look up YoY data for the same metric
    target_yq = (target_year, target_quarter)
    yoy_baseline_yq = (target_year - 1, target_quarter)

    if target_yq not in fmp_data or yoy_baseline_yq not in fmp_data:
        return flags, reasons

    current_val = fmp_data[target_yq].get(metric)
    prior_yoy_val = fmp_data[yoy_baseline_yq].get(metric)

    if current_val is None or prior_yoy_val is None:
        return flags, reasons

    yoy_growth = compute_yoy_growth(current_val, prior_yoy_val)
    if yoy_growth is not None and yoy_growth < -5:
        flags.append("cherry_picking_timeframe")
        reasons.append(
            f"Cited positive QoQ growth ({claimed_value:.1f}%) while YoY {metric} "
            f"declined {yoy_growth:.1f}%. May be selectively highlighting favorable comparison."
        )

    return flags, reasons


def check_gaap_nongaap_mixing(claim: dict, fmp_data: dict,
                                target_year: int, target_quarter: int) -> tuple[list, list]:
    """Flag if claimed value significantly exceeds GAAP without non-GAAP disclosure.

    Detects when EPS or other metrics are cited without specifying "adjusted" or "non-GAAP"
    but the value doesn't match GAAP data, suggesting undisclosed non-GAAP reporting.
    """
    flags, reasons = [], []

    gaap = claim.get("gaap_classification", "unknown")
    metric = claim.get("metric_type", "")
    claim_type = claim.get("claim_type", "")
    claimed_value = claim.get("claimed_value")
    quote_text = claim.get("quote_text", "").lower()

    # Only check for unknown GAAP classification on absolute claims
    if gaap != "unknown" or claim_type != "absolute":
        return flags, reasons

    if metric not in ("eps_basic", "eps_diluted", "ebitda"):
        return flags, reasons

    if claimed_value is None:
        return flags, reasons

    target_yq = (target_year, target_quarter)
    if target_yq not in fmp_data:
        return flags, reasons

    gaap_value = fmp_data[target_yq].get(metric)
    if gaap_value is None or gaap_value == 0:
        return flags, reasons

    # Check if claimed value is significantly higher than GAAP
    pct_diff = (claimed_value - gaap_value) / abs(gaap_value)
    if pct_diff > 0.15:  # >15% higher than GAAP
        nongaap_keywords = ["adjusted", "non-gaap", "non gaap", "excluding", "pro forma"]
        if not any(kw in quote_text for kw in nongaap_keywords):
            flags.append("gaap_nongaap_mixing")
            reasons.append(
                f"Claimed {metric} value ({claimed_value:.2f}) is {pct_diff*100:.0f}% higher "
                f"than GAAP ({gaap_value:.2f}) without non-GAAP disclosure in quote."
            )

    return flags, reasons


def check_low_base_exaggeration(claim: dict, fmp_data: dict,
                                  target_year: int, target_quarter: int) -> tuple[list, list]:
    """Flag percentage claims with tiny denominators.

    Detects when management cites impressive growth percentages (>100%) on a metric
    that represents less than 1% of total revenue — inflating significance.
    """
    flags, reasons = [], []

    claim_type = claim.get("claim_type", "")
    claimed_value = claim.get("claimed_value", 0)
    metric = claim.get("metric_type", "")

    if claim_type not in ("yoy_growth", "qoq_growth"):
        return flags, reasons

    if abs(claimed_value) < 50:  # Only flag extreme percentages
        return flags, reasons

    # Look up baseline value and revenue
    if claim_type == "yoy_growth":
        baseline_yq = (target_year - 1, target_quarter)
    else:
        baseline_yq = (target_year, target_quarter - 1) if target_quarter > 1 else (target_year - 1, 4)

    target_yq = (target_year, target_quarter)

    if baseline_yq not in fmp_data or target_yq not in fmp_data:
        return flags, reasons

    baseline_value = fmp_data[baseline_yq].get(metric)
    revenue = fmp_data[target_yq].get("revenue")

    if baseline_value is None or revenue is None or revenue == 0:
        return flags, reasons

    # Check if the base is tiny relative to total revenue
    base_ratio = abs(baseline_value) / abs(revenue)
    if base_ratio < 0.01:  # Less than 1% of revenue
        flags.append("low_base_exaggeration")
        reasons.append(
            f"{abs(claimed_value):.0f}% growth on base of {baseline_value:,.0f} "
            f"which is <1% of revenue ({revenue:,.0f}). Small denominator exaggerates significance."
        )

    return flags, reasons


def run_all_heuristics(claim: dict, fmp_data: dict,
                        target_year: int, target_quarter: int) -> tuple[list, list]:
    """Run all misleading heuristics and collect flags + reasons."""
    all_flags, all_reasons = [], []

    for heuristic in [check_cherry_picking_timeframe, check_gaap_nongaap_mixing, check_low_base_exaggeration]:
        flags, reasons = heuristic(claim, fmp_data, target_year, target_quarter)
        all_flags.extend(flags)
        all_reasons.extend(reasons)

    return all_flags, all_reasons
