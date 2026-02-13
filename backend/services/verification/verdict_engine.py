"""Verdict engine: produces final verdicts for claims by combining
computation results with tolerance checks and misleading heuristics."""

import re
from typing import Optional

from backend.services.extraction.normalizer import normalize_claimed_value, parse_period
from backend.services.verification.metric_catalog import get_catalog_entry
from backend.services.verification.period_resolver import resolve_periods
from backend.services.verification.compute import (
    verify_absolute,
    verify_growth,
    verify_margin,
    compute_qoq_growth,
)
from backend.services.verification.tolerances import (
    get_tolerance, get_growth_tolerance, is_approximate,
    EPS_ABSOLUTE_TOLERANCE,
)
from backend.services.ingestion.fmp_client import load_fmp_data
from backend.services.misleading.heuristics import run_all_heuristics

# --- TTM / Multi-period detection ---
_TTM_KEYWORDS = re.compile(
    r"(trailing\s+(twelve|12)[- ]month|ttm|last\s+12\s+months|past\s+year"
    r"|first\s+half|first\s+nine\s+months|year[- ]to[- ]date|ytd|through\s+the\s+first\s+half)",
    re.IGNORECASE,
)

# --- CapEx "including finance leases" detection ---
_CAPEX_LEASE_KEYWORDS = re.compile(
    r"(including.*?financ(e|ed)\s+leases?|plus.*?financ(e|ed)\s+leases?|financ(e|ed)\s+lease)",
    re.IGNORECASE,
)

# --- "Total expenses" / "Total costs and expenses" detection ---
_TOTAL_EXPENSES_KEYWORDS = re.compile(
    r"(total\s+(costs?\s+and\s+)?expenses|costs?\s+and\s+expenses)",
    re.IGNORECASE,
)

# --- Dollar-amount growth detection (e.g., "$500 million" misread as 500% growth) ---
_DOLLAR_GROWTH_KEYWORDS = re.compile(
    r"\$[\d,.]+\s*(billion|million|thousand|B|M|K)\b",
    re.IGNORECASE,
)

# --- Basis-point margin change detection ---
_BPS_CHANGE_KEYWORDS = re.compile(
    r"(expand(ed)?|contract(ed)?|improv(ed)?|declin(ed)?|increas(ed)?|decreas(ed)?|deleverag(e|ed|ing))"
    r"\s+\d+\s*basis\s*points?",
    re.IGNORECASE,
)
_BPS_CHANGE_KEYWORDS2 = re.compile(
    r"\d+\s*basis\s*points?\s*(of\s+)?(expansion|contraction|improvement|decline|deleverag(e|ed|ing))",
    re.IGNORECASE,
)


SEGMENT_KEYWORDS = [
    # Apple
    "iphone", "in mac", "mac,", "mac revenue", "ipad", "wearable",
    "services,", "services business", "from services", "to services",
    "products revenue", "apple intelligence",
    # Microsoft
    "cloud", "azure", "office", "linkedin", "gaming", "windows", "xbox",
    "intelligent cloud", "productivity and business", "more personal computing",
    # Google/Alphabet
    "advertising", "youtube", "google search", "pixel", "google cloud",
    # Amazon
    "aws", "north america", "third-party", "first-party",
    # General segments / geographies
    "international", "subscriptions", "device", "greater china",
    "europe", "japan", "rest of asia", "americas",
    # JPM
    "consumer banking", "investment banking", "asset management",
    "commercial banking",
    # JNJ
    "pharmaceutical", "medtech", "innovative medicine",
    # Walmart
    "sam's club", "walmart u.s.", "walmart international",
    # Tesla
    "automotive", "energy generation", "energy storage",
    # NVIDIA
    "data center", "professional visualization", "compute & networking",
    # Meta
    "reality labs", "family of apps",
]

_MONTH_QUARTER_KEYWORDS = (
    "january quarter", "february quarter", "march quarter",
    "april quarter", "may quarter", "june quarter",
    "july quarter", "august quarter", "september quarter",
    "october quarter", "november quarter", "december quarter",
)

_ANNUAL_SUM_METRICS = {
    "revenue",
    "net_income",
    "gross_profit",
    "operating_income",
    "ebitda",
    "free_cash_flow",
    "operating_cash_flow",
    "cost_of_revenue",
    "capital_expenditures",
    "operating_expenses",
    "research_and_development",
}

_BPS_NEGATIVE_WORDS = ("down", "decline", "decrease", "decreased", "contraction", "contracted")
_BPS_POSITIVE_WORDS = ("up", "increase", "increased", "expansion", "expanded", "improvement", "improved")
_SEQUENTIAL_WORDS = ("sequential", "sequentially", "qoq", "quarter over quarter", "versus the prior quarter")
_YOY_WORDS = ("year over year", "yoy", "versus last year", "from a year ago")


def _keyword_to_regex(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


_SEGMENT_KEYWORD_PATTERNS = [_keyword_to_regex(kw) for kw in SEGMENT_KEYWORDS]


def _is_segment_claim(claim: dict) -> bool:
    """Detect if a claim refers to a product/segment/geography rather than total.

    Uses metric_context (from LLM extraction) as the primary signal,
    falling back to keyword matching on quote_text.
    """
    # Primary: use metric_context from extraction
    ctx = (claim.get("metric_context") or "").strip().lower()
    if ctx and ctx not in ("total", "company", "overall", "consolidated", ""):
        return True

    # Fallback: keyword matching on quote text
    quote_lower = claim.get("quote_text", "").lower()
    return any(p.search(quote_lower) for p in _SEGMENT_KEYWORD_PATTERNS)


def _should_use_calendar_alias(claim: dict, transcript_year: int, transcript_quarter: int) -> bool:
    """Only allow calendar-period fallback for explicit month-quarter claims.

    This protects fiscal-quarter claims (the common case) from being incorrectly
    matched to same-numbered calendar quarters on non-calendar fiscal-year companies.
    """
    period_str = claim.get("period", "")
    parsed = parse_period(period_str)
    if not parsed:
        return False

    # If claim period equals transcript period, treat it as fiscal by default.
    if parsed == (transcript_year, transcript_quarter):
        return False

    quote_lower = claim.get("quote_text", "").lower()
    return any(kw in quote_lower for kw in _MONTH_QUARTER_KEYWORDS)


def lookup_value(fmp_data: dict, metric: str, year: int, quarter: int,
                 use_calendar_alias: bool = False) -> Optional[tuple[float, str]]:
    """Look up a metric value for a given period. Returns (value, source) or None."""
    yq = (year, quarter)
    source_map = fmp_data.get("_metric_sources", {})
    if yq in fmp_data and metric in fmp_data[yq]:
        source = source_map.get((year, quarter, metric), "fmp")
        return (fmp_data[yq][metric], source)

    if use_calendar_alias:
        aliases = fmp_data.get("_calendar_aliases", {})
        fyq = aliases.get(yq)
        if fyq in fmp_data and metric in fmp_data[fyq]:
            source = source_map.get((fyq[0], fyq[1], metric), "fmp")
            return (fmp_data[fyq][metric], f"{source}_calendar_alias")

    return None


def _previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    if quarter == 1:
        return (year - 1, 4)
    return (year, quarter - 1)


def _sum_metric_for_periods(
    fmp_data: dict,
    metric: str,
    periods: list[tuple[int, int]],
    use_calendar_alias: bool = False,
) -> tuple[Optional[float], list[str], list[dict], list[str]]:
    """Sum a metric across explicit periods.

    Returns (sum_or_none, sources, financial_facts_used, missing_period_labels).
    """
    total = 0.0
    sources: list[str] = []
    facts: list[dict] = []
    missing: list[str] = []

    for year, quarter in periods:
        actual = lookup_value(
            fmp_data, metric, year, quarter, use_calendar_alias=use_calendar_alias
        )
        if actual is None:
            missing.append(f"Q{quarter} {year}")
            continue

        value, source = actual
        total += value
        if source not in sources:
            sources.append(source)
        facts.append({"field": metric, "fy": year, "fq": quarter, "value": value})

    if missing:
        return (None, sources, facts, missing)
    return (total, sources, facts, [])


def _full_year_periods(year: int) -> list[tuple[int, int]]:
    return [(year, 1), (year, 2), (year, 3), (year, 4)]


def _ttm_periods(target_year: int, target_quarter: int) -> list[tuple[int, int]]:
    periods = [(target_year, target_quarter)]
    y, q = target_year, target_quarter
    for _ in range(3):
        y, q = _previous_quarter(y, q)
        periods.append((y, q))
    return periods


def _determine_multiperiod_periods(
    quote_lower: str, target_year: int, target_quarter: int
) -> tuple[list[tuple[int, int]], str] | None:
    """Resolve quarter list for multiperiod claims."""
    if any(k in quote_lower for k in ("trailing twelve", "trailing 12", "ttm", "last 12 months", "past year")):
        return (_ttm_periods(target_year, target_quarter), "TTM")

    if "first half" in quote_lower:
        return ([(target_year, 1), (target_year, 2)], "first_half")

    if "first nine months" in quote_lower:
        return ([(target_year, 1), (target_year, 2), (target_year, 3)], "first_nine_months")

    if "year-to-date" in quote_lower or re.search(r"\bytd\b", quote_lower):
        if target_quarter in (1, 2, 3, 4):
            return ([(target_year, q) for q in range(1, target_quarter + 1)], "ytd")

    return None


def _sum_full_year_metric(
    fmp_data: dict, metric: str, year: int, use_calendar_alias: bool = False
) -> tuple[Optional[float], list[str], list[dict], list[str]]:
    periods = _full_year_periods(year)
    return _sum_metric_for_periods(
        fmp_data, metric, periods, use_calendar_alias=use_calendar_alias
    )


def _compute_full_year_margin(
    fmp_data: dict,
    numerator_metric: str,
    denominator_metric: str,
    year: int,
    use_calendar_alias: bool = False,
) -> tuple[Optional[float], list[str], list[dict], list[str], Optional[float], Optional[float]]:
    num_sum, num_sources, num_facts, num_missing = _sum_full_year_metric(
        fmp_data, numerator_metric, year, use_calendar_alias=use_calendar_alias
    )
    den_sum, den_sources, den_facts, den_missing = _sum_full_year_metric(
        fmp_data, denominator_metric, year, use_calendar_alias=use_calendar_alias
    )

    missing = list(dict.fromkeys(num_missing + den_missing))
    sources = list(dict.fromkeys(num_sources + den_sources))
    facts = num_facts + den_facts

    if num_sum is None or den_sum is None:
        return (None, sources, facts, missing, num_sum, den_sum)

    margin = (num_sum / den_sum) * 100 if den_sum != 0 else None
    if margin is None:
        return (None, sources, facts, missing, num_sum, den_sum)
    return (margin, sources, facts, [], num_sum, den_sum)


def _signed_bps_value(claimed_bps: float, quote_lower: str) -> float:
    """Infer direction for basis-point claims from language."""
    base = abs(claimed_bps)
    if any(w in quote_lower for w in _BPS_NEGATIVE_WORDS):
        return -base
    if any(w in quote_lower for w in _BPS_POSITIVE_WORDS):
        return base
    # If direction is not explicit, preserve original sign.
    return claimed_bps


def _resolve_margin_change_baseline(
    claim: dict, target_year: int, target_quarter: int, quote_lower: str
) -> tuple[int, int] | None:
    comp_period = claim.get("comparison_period")
    if comp_period:
        comp_parsed = parse_period(comp_period)
        if comp_parsed and comp_parsed[1] in (1, 2, 3, 4):
            return comp_parsed

    if target_quarter not in (1, 2, 3, 4):
        return None

    if any(w in quote_lower for w in _YOY_WORDS):
        return (target_year - 1, target_quarter)
    if any(w in quote_lower for w in _SEQUENTIAL_WORDS):
        return _previous_quarter(target_year, target_quarter)

    # Most bps commentary is sequential unless stated otherwise.
    return _previous_quarter(target_year, target_quarter)


def _definition_gap_check(metric: str, normalized: float, actual_value: float, quote_lower: str) -> tuple[str, str] | None:
    """Detect known definition/period gaps that should stay unverifiable."""
    if actual_value == 0:
        return None

    ratio = normalized / actual_value

    if metric in ("cash_and_marketable_securities", "total_debt", "net_cash"):
        pct_diff = abs(normalized - actual_value) / abs(actual_value)
        if pct_diff > 0.05:
            return (
                "balance_sheet_definition_gap",
                (
                    f"Claimed {metric.replace('_', ' ')} ({normalized:,.0f}) differs from available data "
                    f"({actual_value:,.0f}) by {pct_diff*100:.1f}%. Companies may use definitions that include "
                    "or exclude items (for example, marketable securities classes, short-term borrowings, "
                    "or lease-related obligations) not aligned with this dataset."
                ),
            )

    if metric == "revenue":
        if 0.50 < ratio < 0.80 and any(
            kw in quote_lower for kw in ("net revenue", "managed revenue", "net interest")
        ):
            return (
                "bank_net_vs_gross_revenue",
                (
                    f"This claim references net revenue (${normalized/1e9:.1f}B) which excludes provisions "
                    f"and interest expense. Our data source reports gross revenue (${actual_value/1e9:.1f}B). "
                    "Net and gross revenue are different measures for financial institutions."
                ),
            )
        if 0.50 < ratio < 0.80:
            return (
                "revenue_definition_mismatch",
                (
                    f"Claimed revenue (${normalized/1e9:.1f}B) is {ratio*100:.0f}% of "
                    f"data source revenue (${actual_value/1e9:.1f}B). This likely reflects a difference in "
                    "revenue definition (e.g., net revenue vs gross revenue for financial institutions)."
                ),
            )

    if ratio > 1.30:
        return (
            "value_exceeds_actual",
            (
                f"Claimed value ({normalized:,.0f}) is {ratio:.1f}x the reported "
                f"{metric.replace('_', ' ')} ({actual_value:,.0f}). This likely reflects a different "
                "time period (TTM, annual, or fiscal year offset) or includes items beyond this metric."
            ),
        )

    if metric in ("capital_expenditures", "capital_expenditure"):
        if 1.05 < ratio < 1.50:
            return (
                "capex_definition_gap",
                (
                    f"Claimed CapEx (${normalized/1e9:.1f}B) exceeds reported cash CapEx "
                    f"(${actual_value/1e9:.1f}B) by {(ratio-1)*100:.0f}%. Companies often report CapEx "
                    "including finance leases on calls, while data sources report cash CapEx only."
                ),
            )
        if 0.70 < ratio < 0.95:
            return (
                "capex_definition_gap",
                (
                    f"Claimed CapEx (${normalized/1e9:.1f}B) is {(1-ratio)*100:.0f}% below reported CapEx "
                    f"(${actual_value/1e9:.1f}B). This likely reflects a definition difference "
                    "(for example, net vs gross presentation or inclusion/exclusion of specific asset classes)."
                ),
            )

    if metric == "free_cash_flow" and 0.80 < ratio < 0.96:
        return (
            "fcf_definition_gap",
            (
                f"Claimed FCF (${normalized/1e9:.1f}B) is {(1-ratio)*100:.0f}% below reported FCF "
                f"(${actual_value/1e9:.1f}B). Companies sometimes report FCF net of finance lease "
                "principal payments, resulting in a lower figure than the standard definition."
            ),
        )

    return None


def _remap_other_metric(metric: str, quote_lower: str) -> str:
    """Remap extractor 'other' metric when quote text is unambiguous."""
    if metric != "other":
        return metric

    if "net cash" in quote_lower:
        return "net_cash"
    if "cash and marketable securities" in quote_lower:
        return "cash_and_marketable_securities"
    if "cash and investments" in quote_lower:
        return "cash_and_marketable_securities"
    if "cash and cash equivalents" in quote_lower:
        return "cash_and_marketable_securities"
    if "total debt" in quote_lower or " in debt" in quote_lower:
        return "total_debt"
    if "free cash flow" in quote_lower:
        return "free_cash_flow"
    if "operating cash flow" in quote_lower or "cash flow from operations" in quote_lower:
        return "operating_cash_flow"
    if "capital expenditures" in quote_lower or "capital expenditure" in quote_lower or "capex" in quote_lower:
        return "capital_expenditures"
    if "research and development" in quote_lower or "r&d" in quote_lower:
        return "research_and_development"

    return metric


def verify_single_claim(claim: dict, ticker: str, transcript_year: int,
                         transcript_quarter: int, fmp_data: dict) -> dict:
    """Verify a single extracted claim against financial data.

    Returns a verdict dict with: verdict, actual_value, difference, difference_pct,
    tolerance_used, computation_detail, evidence_source, flags, explanation.
    """
    metric = claim.get("metric_type", "other")
    claim_type = claim.get("claim_type", "")
    claimed_value = claim.get("claimed_value")
    unit = claim.get("unit", "dollars")

    # Auto-correct: "operating cash flow" misclassified as free_cash_flow
    quote_lower = claim.get("quote_text", "").lower()
    metric = _remap_other_metric(metric, quote_lower)
    if metric == "free_cash_flow" and "operating cash flow" in quote_lower:
        metric = "operating_cash_flow"
    scale = claim.get("scale")
    gaap = claim.get("gaap_classification", "unknown")
    qualifiers = claim.get("qualifiers", [])
    approx = claim.get("is_approximate", False) or is_approximate(qualifiers)

    result = {
        "claim_id": claim.get("claim_id", ""),
        "claimed_value": claimed_value,
        "actual_value": None,
        "difference": None,
        "difference_pct": None,
        "tolerance_used": None,
        "computation_detail": None,
        "computation_steps": [],
        "financial_facts_used": [],
        "evidence_source": None,
        "flags": [],
        "misleading_flags": [],
        "misleading_reasons": [],
        "explanation": "",
    }

    # Non-GAAP claims: unverifiable against GAAP data
    if gaap == "non_gaap":
        result["flags"].append("non_gaap_claim")
        result["verdict"] = "unverifiable"
        result["explanation"] = (
            "This claim references a non-GAAP metric. Our data sources contain GAAP figures only. "
            "Non-GAAP metrics exclude items like stock-based compensation or restructuring charges."
        )
        return result

    # Guidance claims: unverifiable
    if claim_type == "guidance":
        result["verdict"] = "unverifiable"
        result["explanation"] = "Forward-looking guidance claims cannot be verified against historical data."
        result["flags"].append("guidance_claim")
        return result

    is_multiperiod_claim = bool(_TTM_KEYWORDS.search(quote_lower))

    # CapEx "including finance leases": unverifiable (FMP reports cash CapEx only)
    if metric in ("capital_expenditures", "capital_expenditure") and _CAPEX_LEASE_KEYWORDS.search(quote_lower):
        result["verdict"] = "unverifiable"
        result["explanation"] = (
            "This CapEx claim includes finance leases. Our financial data reports cash capital "
            "expenditures only, excluding finance lease obligations."
        )
        result["flags"].append("capex_includes_leases")
        return result

    is_bps_margin_change = bool(
        claim_type == "margin" and
        (_BPS_CHANGE_KEYWORDS.search(quote_lower) or _BPS_CHANGE_KEYWORDS2.search(quote_lower))
    )

    # Dollar-amount growth misclassified as percentage growth
    if claim_type in ("yoy_growth", "qoq_growth") and unit == "dollars":
        result["verdict"] = "unverifiable"
        result["explanation"] = (
            "This growth claim appears to use a dollar amount rather than a percentage. "
            "Dollar-amount changes cannot be compared against percentage growth computations."
        )
        result["flags"].append("dollar_amount_growth")
        return result

    # Resolve periods
    periods = resolve_periods(claim, transcript_year, transcript_quarter)
    if "error" in periods:
        result["verdict"] = "unverifiable"
        result["explanation"] = f"Cannot verify: {periods['error']}"
        return result

    target_year, target_quarter = periods["target"]
    use_calendar_alias = _should_use_calendar_alias(claim, transcript_year, transcript_quarter)

    catalog = get_catalog_entry(metric)
    if not catalog:
        result["verdict"] = "unverifiable"
        result["explanation"] = f"Metric '{metric}' not in verification catalog."
        return result

    # === ABSOLUTE CLAIMS ===
    if claim_type == "absolute":
        normalized = normalize_claimed_value(claimed_value, unit, scale)

        # Detect segment-level claims (e.g., "iPhone revenue was $44.6B")
        # We only have total figures, not segment breakdowns
        _segment_metrics = {"revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
                            "operating_expenses", "research_and_development"}
        if metric in _segment_metrics and _is_segment_claim(claim):
            ctx = (claim.get("metric_context") or "segment").strip()
            result["verdict"] = "unverifiable"
            result["explanation"] = (
                f"This is a {ctx} {metric.replace('_', ' ')} claim. "
                "Our data only includes total company-level figures."
            )
            result["flags"].append("segment_claim")
            return result

        # TTM / YTD / first-half / first-nine-months claims
        if is_multiperiod_claim:
            if metric not in _ANNUAL_SUM_METRICS:
                result["verdict"] = "unverifiable"
                result["explanation"] = (
                    "This multi-period claim references a metric that is not supported for "
                    "deterministic aggregation."
                )
                result["flags"].append("ttm_or_multiperiod")
                return result

            anchor_quarter = target_quarter if target_quarter in (1, 2, 3, 4) else transcript_quarter
            period_info = _determine_multiperiod_periods(quote_lower, target_year, anchor_quarter)
            if not period_info:
                result["verdict"] = "unverifiable"
                result["explanation"] = (
                    "This claim references a trailing twelve-month (TTM), year-to-date, or multi-period figure. "
                    "Could not determine exact aggregation window."
                )
                result["flags"].append("ttm_or_multiperiod")
                return result

            periods_to_sum, label = period_info
            fmp_field = catalog.get("fmp_field", metric)
            actual_sum, sources, facts, missing = _sum_metric_for_periods(
                fmp_data, fmp_field, periods_to_sum, use_calendar_alias=use_calendar_alias
            )
            if actual_sum is None:
                result["verdict"] = "unverifiable"
                result["explanation"] = f"Missing data for period(s): {', '.join(missing)}"
                result["flags"].append("ttm_or_multiperiod")
                return result

            result["actual_value"] = actual_sum
            result["evidence_source"] = ", ".join(sources) if sources else "fmp"
            result["financial_facts_used"].extend(facts)

            def_gap = _definition_gap_check(metric, normalized, actual_sum, quote_lower)
            if def_gap:
                flag, explanation = def_gap
                result["verdict"] = "unverifiable"
                result["explanation"] = explanation
                result["flags"].append(flag)
                return result

            comp = verify_absolute(normalized, actual_sum)
            tol = get_tolerance(metric, approx)
            pct_diff = abs(comp["difference"]) / abs(actual_sum) if actual_sum != 0 else float("inf")

            if pct_diff <= tol["tight"]:
                result["verdict"] = "verified"
            elif pct_diff <= tol["loose"]:
                result["verdict"] = "close_match"
            else:
                result["verdict"] = "mismatch"

            label_text = label.replace("_", " ").upper()
            result["difference"] = comp["difference"]
            result["difference_pct"] = comp["difference_pct"]
            result["tolerance_used"] = tol["tight"]
            result["computation_detail"] = (
                f"{label_text} {metric.replace('_', ' ')}: claimed {normalized:,.2f} vs "
                f"actual {actual_sum:,.2f}. Diff: {comp['difference_pct']:.2f}% "
                f"(threshold: {tol['tight']*100:.1f}%)."
            )
            result["computation_steps"].append({
                "step": f"{label_text} aggregation",
                "formula": " + ".join(f"Q{q} {y}" for y, q in periods_to_sum),
                "result": actual_sum,
            })
            result["explanation"] = (
                f"{label_text} claim verified by summing quarterly {metric.replace('_', ' ')} over "
                f"{len(periods_to_sum)} quarters."
            )
            return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

        # Full-year absolute claims (FY YYYY): aggregate Q1-Q4 for supported metrics
        if target_quarter == 0:
            if metric not in _ANNUAL_SUM_METRICS:
                result["verdict"] = "unverifiable"
                result["explanation"] = (
                    f"Full-year verification is not supported for metric '{metric}'."
                )
                return result

            fmp_field = catalog.get("fmp_field", metric)
            annual_total, sources, facts, missing = _sum_full_year_metric(
                fmp_data, fmp_field, target_year, use_calendar_alias=use_calendar_alias
            )
            if annual_total is None:
                result["verdict"] = "unverifiable"
                result["explanation"] = f"Missing data for period(s): {', '.join(missing)}"
                return result

            result["actual_value"] = annual_total
            result["evidence_source"] = ", ".join(sources) if sources else "fmp"
            result["financial_facts_used"].extend(facts)

            def_gap = _definition_gap_check(metric, normalized, annual_total, quote_lower)
            if def_gap:
                flag, explanation = def_gap
                result["verdict"] = "unverifiable"
                result["explanation"] = explanation
                result["flags"].append(flag)
                return result

            comp = verify_absolute(normalized, annual_total)
            tol = get_tolerance(metric, approx)
            pct_diff = abs(comp["difference"]) / abs(annual_total) if annual_total != 0 else float("inf")

            if pct_diff <= tol["tight"]:
                result["verdict"] = "verified"
            elif pct_diff <= tol["loose"]:
                result["verdict"] = "close_match"
            else:
                result["verdict"] = "mismatch"

            result["difference"] = comp["difference"]
            result["difference_pct"] = comp["difference_pct"]
            result["tolerance_used"] = tol["tight"]
            result["computation_detail"] = (
                f"Full-year {metric.replace('_', ' ')}: claimed {normalized:,.2f} vs "
                f"actual {annual_total:,.2f}. Diff: {comp['difference_pct']:.2f}% "
                f"(threshold: {tol['tight']*100:.1f}%)."
            )
            result["computation_steps"].append({
                "step": "Full-year aggregation",
                "formula": f"Q1 {target_year} + Q2 {target_year} + Q3 {target_year} + Q4 {target_year}",
                "result": annual_total,
            })
            result["explanation"] = (
                f"Full-year claim verified against sum of Q1-Q4 {target_year} {metric.replace('_', ' ')}."
            )
            return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

        # "Total expenses" = COGS + OpEx: use summed value for comparison
        # Detect via keyword in quote OR by value ratio (claimed >> FMP OpEx but ≈ COGS+OpEx)
        if metric == "operating_expenses":
            cogs = lookup_value(
                fmp_data, "cost_of_revenue", target_year, target_quarter,
                use_calendar_alias=use_calendar_alias,
            )
            opex = lookup_value(
                fmp_data, "operating_expenses", target_year, target_quarter,
                use_calendar_alias=use_calendar_alias,
            )
            is_total_expenses_by_keyword = _TOTAL_EXPENSES_KEYWORDS.search(quote_lower)
            is_total_expenses_by_value = False
            if cogs is not None and opex is not None:
                total_exp = cogs[0] + opex[0]
                if total_exp > 0 and normalized > opex[0] * 1.2:
                    ratio_to_total = normalized / total_exp
                    if 0.90 < ratio_to_total < 1.10:
                        is_total_expenses_by_value = True

        if metric == "operating_expenses" and (is_total_expenses_by_keyword or is_total_expenses_by_value):
            if cogs is not None and opex is not None:
                total_expenses = cogs[0] + opex[0]
                comp = verify_absolute(normalized, total_expenses)
                tol = get_tolerance(metric, approx)
                pct_diff = abs(comp["difference"]) / abs(total_expenses) if total_expenses != 0 else float("inf")
                result["actual_value"] = total_expenses
                result["evidence_source"] = "fmp (cost_of_revenue + operating_expenses)"
                result["financial_facts_used"].extend([
                    {"field": "cost_of_revenue", "fy": target_year, "fq": target_quarter, "value": cogs[0]},
                    {"field": "operating_expenses", "fy": target_year, "fq": target_quarter, "value": opex[0]},
                ])
                if pct_diff <= tol["tight"]:
                    result["verdict"] = "verified"
                elif pct_diff <= tol["loose"]:
                    result["verdict"] = "close_match"
                else:
                    result["verdict"] = "mismatch"
                result["difference"] = comp["difference"]
                result["difference_pct"] = comp["difference_pct"]
                result["tolerance_used"] = tol["tight"]
                result["computation_detail"] = (
                    f"Total expenses (COGS + OpEx): claimed {normalized:,.2f} vs "
                    f"actual {total_expenses:,.2f} ({cogs[0]:,.0f} + {opex[0]:,.0f}). "
                    f"Diff: {comp['difference_pct']:.2f}% (threshold: {tol['tight']*100:.1f}%)"
                )
                result["computation_steps"].append({
                    "step": "Total expenses = COGS + OpEx",
                    "formula": f"{cogs[0]:,.0f} + {opex[0]:,.0f} = {total_expenses:,.0f}",
                    "result": total_expenses,
                })
                result["explanation"] = (
                    f"Total expenses claim verified against sum of cost of revenue + operating expenses: "
                    f"claimed {normalized:,.2f} vs actual {total_expenses:,.2f}."
                )
                return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

        fmp_field = catalog.get("fmp_field", metric)
        actual = lookup_value(
            fmp_data, fmp_field, target_year, target_quarter,
            use_calendar_alias=use_calendar_alias,
        )

        if actual is None:
            result["verdict"] = "unverifiable"
            result["explanation"] = f"No {metric} data found for {ticker} Q{target_quarter} {target_year}."
            return result

        actual_value, source = actual

        def_gap = _definition_gap_check(metric, normalized, actual_value, quote_lower)
        if def_gap:
            flag, explanation = def_gap
            result["verdict"] = "unverifiable"
            result["explanation"] = explanation
            result["flags"].append(flag)
            result["actual_value"] = actual_value
            result["evidence_source"] = source
            result["financial_facts_used"].append({
                "field": metric, "fy": target_year, "fq": target_quarter, "value": actual_value
            })
            return result

        # Bank "net revenue" vs FMP gross revenue detection
        # Financial companies (JPM, etc.) report "managed net revenue" on calls
        # which excludes provisions/interest expense. FMP reports gross revenue.
        # Ratio is typically 0.55-0.75x. Mark as unverifiable rather than mismatch.
        if (metric == "revenue" and actual_value > 0
                and 0.50 < normalized / actual_value < 0.80
                and any(kw in quote_lower for kw in ("net revenue", "managed revenue", "net interest"))):
            result["verdict"] = "unverifiable"
            result["explanation"] = (
                f"This claim references net revenue (${normalized/1e9:.1f}B) which excludes provisions "
                f"and interest expense. Our data source reports gross revenue (${actual_value/1e9:.1f}B). "
                "Net and gross revenue are different measures for financial institutions."
            )
            result["flags"].append("bank_net_vs_gross_revenue")
            return result

        # Value-based segment/component detection: if claimed value < 50% of actual total,
        # almost certainly a segment or line-item claim rather than the full metric
        _value_check_metrics = {"revenue", "cost_of_revenue", "gross_profit", "operating_income",
                                "operating_expenses", "capital_expenditures", "net_income",
                                "free_cash_flow", "operating_cash_flow"}
        if metric in _value_check_metrics and actual_value > 0 and normalized < actual_value * 0.50:
            result["verdict"] = "unverifiable"
            result["explanation"] = (
                f"Claimed {normalized:,.0f} is much less than total {metric.replace('_', ' ')} "
                f"{actual_value:,.0f} — likely a segment, subset, or incremental amount."
            )
            result["flags"].append("segment_claim_by_value")
            return result

        # Revenue: if claimed value is 50-80% of FMP value, likely bank net vs gross revenue
        # (financial companies like JPM where FMP revenue != earnings call "revenue")
        if (metric == "revenue" and actual_value > 0
                and 0.50 < normalized / actual_value < 0.80):
            result["verdict"] = "unverifiable"
            result["explanation"] = (
                f"Claimed revenue (${normalized/1e9:.1f}B) is {normalized/actual_value*100:.0f}% of "
                f"data source revenue (${actual_value/1e9:.1f}B). This likely reflects a difference in "
                "revenue definition (e.g., net revenue vs gross revenue for financial institutions)."
            )
            result["flags"].append("revenue_definition_mismatch")
            return result

        # Value significantly ABOVE actual (>1.3x): likely different period (TTM, guidance, or fiscal offset)
        if actual_value > 0 and normalized > actual_value * 1.30:
            result["verdict"] = "unverifiable"
            result["explanation"] = (
                f"Claimed value ({normalized:,.0f}) is {normalized/actual_value:.1f}x the reported "
                f"{metric.replace('_', ' ')} ({actual_value:,.0f}). This likely reflects a different "
                "time period (TTM, annual, or fiscal year offset) or includes items beyond this metric."
            )
            result["flags"].append("value_exceeds_actual")
            return result

        # CapEx: if claimed is 5-30% above actual, likely includes finance leases
        if metric in ("capital_expenditures", "capital_expenditure") and actual_value > 0:
            ratio = normalized / actual_value
            if 1.05 < ratio < 1.50:
                result["verdict"] = "unverifiable"
                result["explanation"] = (
                    f"Claimed CapEx (${normalized/1e9:.1f}B) exceeds reported cash CapEx "
                    f"(${actual_value/1e9:.1f}B) by {(ratio-1)*100:.0f}%. Companies often report CapEx "
                    "including finance leases on calls, while data sources report cash CapEx only."
                )
                result["flags"].append("capex_definition_gap")
                return result

        # FCF: if claimed is 5-15% below actual, likely a FCF definition difference
        # (some companies subtract finance lease principal payments from FCF)
        if metric == "free_cash_flow" and actual_value > 0:
            ratio = normalized / actual_value
            if 0.80 < ratio < 0.96:
                result["verdict"] = "unverifiable"
                result["explanation"] = (
                    f"Claimed FCF (${normalized/1e9:.1f}B) is {(1-ratio)*100:.0f}% below reported FCF "
                    f"(${actual_value/1e9:.1f}B). Companies sometimes report FCF net of finance lease "
                    "principal payments, resulting in a lower figure than the standard definition."
                )
                result["flags"].append("fcf_definition_gap")
                return result

        result["actual_value"] = actual_value
        result["evidence_source"] = source
        result["financial_facts_used"].append({
            "field": metric, "fy": target_year, "fq": target_quarter, "value": actual_value
        })

        # EPS: check absolute tolerance first
        if metric in ("eps_basic", "eps_diluted"):
            abs_diff = abs(normalized - actual_value)
            if abs_diff <= EPS_ABSOLUTE_TOLERANCE:
                result["verdict"] = "verified"
                result["difference"] = normalized - actual_value
                result["difference_pct"] = abs_diff / abs(actual_value) * 100 if actual_value != 0 else 0
                result["tolerance_used"] = EPS_ABSOLUTE_TOLERANCE
                result["computation_detail"] = (
                    f"EPS: claimed ${normalized:.2f} vs actual ${actual_value:.2f}. "
                    f"Diff ${abs_diff:.3f} within ${EPS_ABSOLUTE_TOLERANCE} tolerance."
                )
                result["computation_steps"].append({
                    "step": "EPS absolute comparison",
                    "formula": f"|{normalized:.2f} - {actual_value:.2f}| = {abs_diff:.3f}",
                    "result": abs_diff,
                    "threshold": EPS_ABSOLUTE_TOLERANCE,
                })
                result["explanation"] = f"Verified: claimed ${normalized:.2f} matches actual ${actual_value:.2f}."
                return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

        # General absolute comparison
        comp = verify_absolute(normalized, actual_value)
        tol = get_tolerance(metric, approx)

        if actual_value != 0:
            pct_diff = abs(comp["difference"]) / abs(actual_value)
        else:
            pct_diff = float("inf")

        if pct_diff <= tol["tight"]:
            result["verdict"] = "verified"
        elif pct_diff <= tol["loose"]:
            result["verdict"] = "close_match"
        else:
            result["verdict"] = "mismatch"

        result["difference"] = comp["difference"]
        result["difference_pct"] = comp["difference_pct"]
        result["tolerance_used"] = tol["tight"]
        result["computation_detail"] = (
            f"Claimed {normalized:,.2f} vs Actual {actual_value:,.2f}. "
            f"Diff: {comp['difference_pct']:.2f}% (threshold: {tol['tight']*100:.1f}%)"
        )
        result["computation_steps"].append({
            "step": "Absolute comparison",
            "formula": f"|{normalized:,.2f} - {actual_value:,.2f}| / |{actual_value:,.2f}|",
            "result": pct_diff,
            "threshold": tol["tight"],
        })
        result["explanation"] = (
            f"{result['verdict'].replace('_', ' ').title()}: "
            f"claimed {normalized:,.2f} vs actual {actual_value:,.2f} from {source}."
        )

        # Check for possible undisclosed non-GAAP
        # Only flag when claimed value EXCEEDS GAAP (non-GAAP typically excludes
        # expenses, making income metrics higher). If claimed << actual, it's likely
        # a segment/product claim, not a non-GAAP issue.
        if (gaap == "unknown" and result["verdict"] == "mismatch"
                and normalized > actual_value
                and metric in ("net_income", "eps_basic", "eps_diluted", "operating_income", "ebitda")):
            result["flags"].append("possible_non_gaap_without_disclosure")
            result["verdict"] = "misleading"
            result["explanation"] = (
                f"Claimed value ({normalized:,.2f}) exceeds GAAP data ({actual_value:,.2f}) by "
                f"{comp['difference_pct']:.1f}%. Speaker did not specify GAAP or non-GAAP basis."
            )

        return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

    # === GROWTH CLAIMS ===
    if claim_type in ("yoy_growth", "qoq_growth"):
        # Segment growth claims can't be verified with total-level data
        _segment_metrics_growth = {"revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
                                    "operating_expenses", "research_and_development"}
        if metric in _segment_metrics_growth and _is_segment_claim(claim):
            ctx = (claim.get("metric_context") or "segment").strip()
            result["verdict"] = "unverifiable"
            result["explanation"] = (
                f"This is a {ctx} {metric.replace('_', ' ')} growth claim. "
                "Our data only includes total company-level figures."
            )
            result["flags"].append("segment_claim")
            return result

        # Full-year YoY growth (FY YYYY vs FY YYYY-1)
        if target_quarter == 0:
            if metric not in _ANNUAL_SUM_METRICS:
                result["verdict"] = "unverifiable"
                result["explanation"] = (
                    f"Full-year growth verification is not supported for metric '{metric}'."
                )
                return result

            baseline_year = target_year - 1
            baseline_quarter = 0
            if "baseline" in periods:
                baseline_year, baseline_quarter = periods["baseline"]
            if baseline_quarter != 0:
                result["verdict"] = "unverifiable"
                result["explanation"] = "Full-year growth requires a full-year baseline period."
                return result

            fmp_field = catalog.get("fmp_field", metric)
            current_sum, src_current, facts_current, miss_current = _sum_full_year_metric(
                fmp_data, fmp_field, target_year, use_calendar_alias=use_calendar_alias
            )
            prior_sum, src_prior, facts_prior, miss_prior = _sum_full_year_metric(
                fmp_data, fmp_field, baseline_year, use_calendar_alias=use_calendar_alias
            )

            if current_sum is None or prior_sum is None:
                missing = list(dict.fromkeys(miss_current + miss_prior))
                result["verdict"] = "unverifiable"
                result["explanation"] = f"Missing data for period(s): {', '.join(missing)}"
                return result

            result["actual_value"] = current_sum
            sources = list(dict.fromkeys(src_current + src_prior))
            result["evidence_source"] = ", ".join(sources) if sources else "fmp"
            result["financial_facts_used"].extend(facts_current + facts_prior)

            comp = verify_growth(claimed_value, current_sum, prior_sum)
            if "error" in comp:
                result["verdict"] = "unverifiable"
                result["explanation"] = "Prior full-year value is zero, cannot compute growth."
                return result

            tol = get_growth_tolerance(approx)
            abs_diff_pp = comp["abs_difference_pp"]
            if abs_diff_pp <= tol["tight"]:
                result["verdict"] = "verified"
            elif abs_diff_pp <= tol["loose"]:
                result["verdict"] = "close_match"
            else:
                result["verdict"] = "mismatch"

            result["difference"] = comp["difference_pp"]
            result["difference_pct"] = abs_diff_pp
            result["tolerance_used"] = tol["tight"]
            result["computation_detail"] = (
                f"Claimed {claimed_value:.1f}% full-year YoY growth. "
                f"Actual: (FY {target_year} {current_sum:,.0f} - FY {baseline_year} {prior_sum:,.0f}) "
                f"/ |{prior_sum:,.0f}| = {comp['actual_pct']:.2f}%. "
                f"Diff: {abs_diff_pp:.2f} pp (threshold: {tol['tight']:.1f} pp)."
            )
            result["computation_steps"].append({
                "step": "Full-year YoY growth",
                "formula": (
                    f"(SUM(Q1-Q4 {target_year}) - SUM(Q1-Q4 {baseline_year})) / "
                    f"|SUM(Q1-Q4 {baseline_year})| * 100"
                ),
                "result": comp["actual_pct"],
                "claimed": claimed_value,
                "difference_pp": comp["difference_pp"],
            })
            result["explanation"] = (
                f"Full-year growth claim: {claimed_value:.1f}% claimed vs {comp['actual_pct']:.2f}% computed "
                f"from FY {target_year} and FY {baseline_year} totals."
            )
            return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

        if "baseline" not in periods:
            result["verdict"] = "unverifiable"
            result["explanation"] = "Cannot determine baseline period for growth comparison."
            return result

        baseline_year, baseline_quarter = periods["baseline"]
        fmp_field = catalog.get("fmp_field", metric)

        actual_current = lookup_value(
            fmp_data, fmp_field, target_year, target_quarter,
            use_calendar_alias=use_calendar_alias,
        )
        actual_prior = lookup_value(
            fmp_data, fmp_field, baseline_year, baseline_quarter,
            use_calendar_alias=use_calendar_alias,
        )

        if actual_current is None or actual_prior is None:
            missing = []
            if actual_current is None:
                missing.append(f"Q{target_quarter} {target_year}")
            if actual_prior is None:
                missing.append(f"Q{baseline_quarter} {baseline_year}")
            result["verdict"] = "unverifiable"
            result["explanation"] = f"Missing data for period(s): {', '.join(missing)}"
            return result

        current_val, src1 = actual_current
        prior_val, src2 = actual_prior
        result["actual_value"] = current_val
        result["evidence_source"] = f"{src1} (current), {src2} (prior)"
        result["financial_facts_used"].extend([
            {"field": metric, "fy": target_year, "fq": target_quarter, "value": current_val},
            {"field": metric, "fy": baseline_year, "fq": baseline_quarter, "value": prior_val},
        ])

        comp = verify_growth(claimed_value, current_val, prior_val)
        if "error" in comp:
            result["verdict"] = "unverifiable"
            result["explanation"] = f"Prior period value is zero, cannot compute growth."
            return result

        # If growth mismatch is large (>3pp), check if it's a revenue definition issue
        # (e.g., JPM net revenue growth vs FMP gross revenue growth)
        if (metric == "revenue" and abs(comp["difference_pp"]) > 3.0
                and "revenue_definition_mismatch" not in result["flags"]):
            # Check if there are revenue definition mismatches for this ticker's absolute claims
            result["verdict"] = "unverifiable"
            result["explanation"] = (
                f"Revenue growth mismatch ({claimed_value:.1f}% claimed vs {comp['actual_pct']:.1f}% computed). "
                "This likely reflects a difference in revenue definition (e.g., net vs gross revenue "
                "for financial institutions) which affects the growth rate calculation."
            )
            result["flags"].append("revenue_growth_definition_mismatch")
            return result

        tol = get_growth_tolerance(approx)
        abs_diff_pp = comp["abs_difference_pp"]

        if abs_diff_pp <= tol["tight"]:
            result["verdict"] = "verified"
        elif abs_diff_pp <= tol["loose"]:
            result["verdict"] = "close_match"
        else:
            result["verdict"] = "mismatch"

        result["difference"] = comp["difference_pp"]
        result["difference_pct"] = abs_diff_pp
        result["tolerance_used"] = tol["tight"]
        growth_label = "YoY" if claim_type == "yoy_growth" else "QoQ"
        result["computation_detail"] = (
            f"Claimed {claimed_value:.1f}% {growth_label} growth. "
            f"Actual: ({current_val:,.0f} - {prior_val:,.0f}) / {abs(prior_val):,.0f} = {comp['actual_pct']:.2f}%. "
            f"Diff: {abs_diff_pp:.2f} pp (threshold: {tol['tight']:.1f} pp)"
        )
        result["computation_steps"].append({
            "step": f"{growth_label} growth",
            "formula": f"({current_val:,.0f} - {prior_val:,.0f}) / |{prior_val:,.0f}| * 100",
            "result": comp["actual_pct"],
            "claimed": claimed_value,
            "difference_pp": comp["difference_pp"],
        })
        result["explanation"] = (
            f"Growth claim: {claimed_value:.1f}% claimed. "
            f"Computed from Q{target_quarter} {target_year} ({current_val:,.0f}) vs "
            f"Q{baseline_quarter} {baseline_year} ({prior_val:,.0f}) = {comp['actual_pct']:.2f}% actual."
        )

        # Supplemental quarter-to-quarter discrepancy for YoY growth claims.
        if claim_type == "yoy_growth":
            prev_year, prev_quarter = _previous_quarter(target_year, target_quarter)
            prev_q_actual = lookup_value(
                fmp_data,
                fmp_field,
                prev_year,
                prev_quarter,
                use_calendar_alias=use_calendar_alias,
            )
            if prev_q_actual is not None:
                prev_q_val, _ = prev_q_actual
                qoq_growth = compute_qoq_growth(current_val, prev_q_val)
                if qoq_growth is not None:
                    qoq_discrepancy_pp = abs(claimed_value - qoq_growth)
                    result["computation_steps"].append({
                        "step": "Supplemental QoQ discrepancy",
                        "formula": f"({current_val:,.0f} - {prev_q_val:,.0f}) / |{prev_q_val:,.0f}| * 100",
                        "result": qoq_growth,
                        "claimed": claimed_value,
                        "difference_pp": claimed_value - qoq_growth,
                    })
                    result["explanation"] += (
                        f" Quarter-over-quarter reference: Q{target_quarter} {target_year} vs "
                        f"Q{prev_quarter} {prev_year} = {qoq_growth:.2f}% "
                        f"({qoq_discrepancy_pp:.2f} pp from claimed growth)."
                    )

        return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

    # === MARGIN CLAIMS ===
    if claim_type == "margin":
        # Segment margin claims can't be verified (e.g., "Products gross margin")
        if _is_segment_claim(claim):
            ctx = (claim.get("metric_context") or "segment").strip()
            result["verdict"] = "unverifiable"
            result["explanation"] = (
                f"This is a {ctx} {metric.replace('_', ' ')} claim. "
                "Our data only includes total company-level figures."
            )
            result["flags"].append("segment_claim")
            return result

        if metric == "operating_expenses":
            # SG&A / operating expenses margin claims are typically the
            # operating-expense ratio to revenue.
            num_field = "sga_expenses" if "sg&a" in quote_lower else "operating_expenses"
            den_field = "revenue"
            tolerance_metric = "operating_margin"
        else:
            if catalog.get("computation") != "ratio":
                result["verdict"] = "unverifiable"
                result["explanation"] = f"Cannot compute margin for metric: {metric}"
                return result
            num_field = catalog["fmp_fields"]["numerator"]
            den_field = catalog["fmp_fields"]["denominator"]
            tolerance_metric = metric

        def _lookup_num_with_fallback(year: int, quarter: int):
            num_val = lookup_value(
                fmp_data, num_field, year, quarter, use_calendar_alias=use_calendar_alias
            )
            if num_val is None and num_field == "sga_expenses":
                num_val = lookup_value(
                    fmp_data, "operating_expenses", year, quarter, use_calendar_alias=use_calendar_alias
                )
            return num_val

        # Basis-point margin change claims (e.g., "margin expanded 70 bps")
        if is_bps_margin_change:
            baseline = _resolve_margin_change_baseline(claim, target_year, target_quarter, quote_lower)
            if baseline is None:
                result["verdict"] = "unverifiable"
                result["explanation"] = (
                    "This claim describes a margin change in basis points, but baseline period "
                    "could not be determined."
                )
                result["flags"].append("margin_change_bps")
                return result

            baseline_year, baseline_quarter = baseline
            num_current = _lookup_num_with_fallback(target_year, target_quarter)
            den_current = lookup_value(
                fmp_data, den_field, target_year, target_quarter, use_calendar_alias=use_calendar_alias
            )
            num_prior = _lookup_num_with_fallback(baseline_year, baseline_quarter)
            den_prior = lookup_value(
                fmp_data, den_field, baseline_year, baseline_quarter, use_calendar_alias=use_calendar_alias
            )

            if any(x is None for x in (num_current, den_current, num_prior, den_prior)):
                missing = []
                if num_current is None or den_current is None:
                    missing.append(f"Q{target_quarter} {target_year}")
                if num_prior is None or den_prior is None:
                    missing.append(f"Q{baseline_quarter} {baseline_year}")
                result["verdict"] = "unverifiable"
                result["explanation"] = f"Missing data for period(s): {', '.join(missing)}"
                result["flags"].append("margin_change_bps")
                return result

            cur_num, src1 = num_current
            cur_den, src2 = den_current
            prev_num, src3 = num_prior
            prev_den, src4 = den_prior
            current_margin = (cur_num / cur_den) * 100 if cur_den != 0 else None
            prior_margin = (prev_num / prev_den) * 100 if prev_den != 0 else None
            if current_margin is None or prior_margin is None:
                result["verdict"] = "unverifiable"
                result["explanation"] = "Denominator is zero."
                result["flags"].append("margin_change_bps")
                return result

            actual_change_bps = (current_margin - prior_margin) * 100
            claimed_change_bps = _signed_bps_value(claimed_value, quote_lower)
            diff_bps = claimed_change_bps - actual_change_bps
            abs_diff_bps = abs(diff_bps)

            tight_bps = 25.0 if not approx else 50.0
            loose_bps = 75.0 if not approx else 100.0

            if abs_diff_bps <= tight_bps:
                result["verdict"] = "verified"
            elif abs_diff_bps <= loose_bps:
                result["verdict"] = "close_match"
            else:
                result["verdict"] = "mismatch"

            result["actual_value"] = actual_change_bps
            result["difference"] = diff_bps
            result["difference_pct"] = abs_diff_bps
            result["tolerance_used"] = tight_bps
            result["evidence_source"] = ", ".join(
                list(dict.fromkeys([src1, src2, src3, src4]))
            )
            result["financial_facts_used"].extend([
                {"field": num_field, "fy": target_year, "fq": target_quarter, "value": cur_num},
                {"field": den_field, "fy": target_year, "fq": target_quarter, "value": cur_den},
                {"field": num_field, "fy": baseline_year, "fq": baseline_quarter, "value": prev_num},
                {"field": den_field, "fy": baseline_year, "fq": baseline_quarter, "value": prev_den},
            ])
            result["computation_detail"] = (
                f"Claimed {claimed_change_bps:.1f} bps change. Actual: "
                f"({current_margin:.2f}% - {prior_margin:.2f}%) * 100 = {actual_change_bps:.1f} bps. "
                f"Diff: {abs_diff_bps:.1f} bps (threshold: {tight_bps:.0f} bps)."
            )
            result["computation_steps"].append({
                "step": "Margin change in bps",
                "formula": f"({current_margin:.2f} - {prior_margin:.2f}) * 100",
                "result": actual_change_bps,
                "claimed": claimed_change_bps,
                "difference_bps": diff_bps,
            })
            result["explanation"] = (
                f"Margin change claim: {claimed_change_bps:.1f} bps claimed vs "
                f"{actual_change_bps:.1f} bps computed from baseline quarter."
            )
            return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

        # Full-year absolute margin (FY YYYY)
        if target_quarter == 0:
            margin_actual, sources, facts, missing, num_sum, den_sum = _compute_full_year_margin(
                fmp_data, num_field, den_field, target_year, use_calendar_alias=use_calendar_alias
            )
            if margin_actual is None and num_field == "sga_expenses":
                margin_actual, sources, facts, missing, num_sum, den_sum = _compute_full_year_margin(
                    fmp_data, "operating_expenses", den_field, target_year, use_calendar_alias=use_calendar_alias
                )
            if margin_actual is None:
                if missing:
                    result["verdict"] = "unverifiable"
                    result["explanation"] = f"Missing data for period(s): {', '.join(missing)}"
                else:
                    result["verdict"] = "unverifiable"
                    result["explanation"] = "Denominator is zero."
                return result

            tol = get_tolerance(tolerance_metric, approx)
            threshold_pp = tol["tight"] * 100
            loose_pp = tol["loose"] * 100
            diff_pp = claimed_value - margin_actual
            abs_diff_pp = abs(diff_pp)

            if abs_diff_pp <= threshold_pp:
                result["verdict"] = "verified"
            elif abs_diff_pp <= loose_pp:
                result["verdict"] = "close_match"
            else:
                result["verdict"] = "mismatch"

            result["actual_value"] = margin_actual
            result["difference"] = diff_pp
            result["difference_pct"] = abs_diff_pp
            result["tolerance_used"] = threshold_pp
            result["evidence_source"] = ", ".join(sources) if sources else "fmp"
            result["financial_facts_used"].extend(facts)
            result["computation_detail"] = (
                f"Claimed {claimed_value:.1f}% full-year margin. Actual: "
                f"SUM({num_field}) / SUM({den_field}) = {num_sum:,.0f} / {den_sum:,.0f} "
                f"= {margin_actual:.2f}%. Diff: {abs_diff_pp:.2f} pp "
                f"(threshold: {threshold_pp:.1f} pp)."
            )
            result["computation_steps"].append({
                "step": "Full-year margin",
                "formula": f"SUM(Q1-Q4 {num_field}) / SUM(Q1-Q4 {den_field}) * 100",
                "result": margin_actual,
            })
            result["explanation"] = (
                f"Full-year margin claim: {claimed_value:.1f}% claimed vs {margin_actual:.2f}% computed."
            )
            return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

        num_actual = _lookup_num_with_fallback(target_year, target_quarter)
        den_actual = lookup_value(
            fmp_data, den_field, target_year, target_quarter,
            use_calendar_alias=use_calendar_alias,
        )

        if num_actual is None or den_actual is None:
            result["verdict"] = "unverifiable"
            result["explanation"] = f"Missing {num_field} or {den_field} data for Q{target_quarter} {target_year}."
            return result

        num_val, num_src = num_actual
        den_val, den_src = den_actual
        result["evidence_source"] = f"{num_src} ({num_field}), {den_src} ({den_field})"
        result["financial_facts_used"].extend([
            {"field": num_field, "fy": target_year, "fq": target_quarter, "value": num_val},
            {"field": den_field, "fy": target_year, "fq": target_quarter, "value": den_val},
        ])

        comp = verify_margin(claimed_value, num_val, den_val)
        if "error" in comp:
            result["verdict"] = "unverifiable"
            result["explanation"] = "Denominator is zero."
            return result

        tol = get_tolerance(tolerance_metric, approx)
        # Margin tolerances are in decimal form (0.003), need to compare in pp
        threshold_pp = tol["tight"] * 100
        loose_pp = tol["loose"] * 100
        abs_diff_pp = comp["abs_difference_pp"]

        if abs_diff_pp <= threshold_pp:
            result["verdict"] = "verified"
        elif abs_diff_pp <= loose_pp:
            result["verdict"] = "close_match"
        else:
            result["verdict"] = "mismatch"

        result["actual_value"] = comp["actual_margin"]
        result["difference"] = comp["difference_pp"]
        result["difference_pct"] = abs_diff_pp
        result["tolerance_used"] = threshold_pp
        result["computation_detail"] = (
            f"Claimed {claimed_value:.1f}%. "
            f"Actual: {num_val:,.0f} / {den_val:,.0f} = {comp['actual_margin']:.2f}%. "
            f"Diff: {abs_diff_pp:.2f} pp (threshold: {threshold_pp:.1f} pp)"
        )
        result["computation_steps"].append({
            "step": f"Compute {metric}",
            "formula": f"{num_val:,.0f} / {den_val:,.0f} * 100",
            "result": comp["actual_margin"],
        })
        result["explanation"] = (
            f"Margin claim: {claimed_value:.1f}% claimed. "
            f"Computed: {num_val:,.0f} / {den_val:,.0f} = {comp['actual_margin']:.2f}%."
        )

        return _apply_misleading_checks(result, claim, fmp_data, target_year, target_quarter)

    # === COMPARISON / OTHER ===
    result["verdict"] = "unverifiable"
    result["explanation"] = f"Claim type '{claim_type}' is not verifiable in the current system."
    result["flags"].append(f"unsupported_claim_type_{claim_type}")
    return result


def _apply_misleading_checks(result: dict, claim: dict, fmp_data: dict,
                              target_year: int, target_quarter: int) -> dict:
    """Apply misleading heuristics and update result accordingly."""
    if result["verdict"] in ("unverifiable",):
        return result

    flags, reasons = run_all_heuristics(claim, fmp_data, target_year, target_quarter)
    if flags:
        result["misleading_flags"] = flags
        result["misleading_reasons"] = reasons
        # If numerically verified but misleading, upgrade to "misleading"
        if result["verdict"] in ("verified", "close_match"):
            result["verdict"] = "misleading"
            result["explanation"] += f" HOWEVER: {'; '.join(reasons)}"

    return result
