"""Post-extraction validation and cleanup for claims."""

import re

# Metrics that are standard GAAP income statement items
_GAAP_DEFAULT_METRICS = {
    "revenue", "gross_profit", "gross_margin", "operating_income",
    "operating_margin", "net_income", "eps_basic", "eps_diluted",
    "cost_of_revenue", "operating_expenses", "research_and_development",
}

# Keywords that indicate non-GAAP
_NON_GAAP_KEYWORDS = re.compile(
    r"(adjusted|non[- ]?gaap|excluding|pro\s*forma|constant\s+currency)",
    re.IGNORECASE,
)

# Keywords that indicate GAAP
_GAAP_KEYWORDS = re.compile(
    r"\b(gaap|as\s+reported)\b",
    re.IGNORECASE,
)


def validate_claims(claims: list[dict], transcript_text: str) -> list[dict]:
    """Validate extracted claims and fix/filter issues.

    1. Span validation: check quote_text matches transcript[start:end]
    2. Speaker filter: drop analyst claims
    3. GAAP classification fix: default to gaap for standard metrics
    4. Comparison period fix: infer missing comparison periods
    5. Dedup: overlapping spans + same metric -> keep higher confidence
    6. Confidence threshold: drop claims below 0.3
    """
    valid_claims = []

    for claim in claims:
        # Skip analyst claims
        if claim.get("speaker_role") == "analyst":
            continue

        # Confidence threshold
        if claim.get("confidence", 0) < 0.3:
            continue

        # Span validation - try to fix if misaligned
        _fix_span(claim, transcript_text)

        # Fix GAAP classification
        _fix_gaap(claim)

        # Fix comparison period
        _fix_comparison_period(claim)

        # Fix metric_context
        if not claim.get("metric_context") or claim["metric_context"] in ("null", ""):
            claim["metric_context"] = "Total"

        valid_claims.append(claim)

    # Dedup: overlapping spans + same metric -> keep higher confidence
    deduped = _dedup_claims(valid_claims)

    return deduped


def _fix_span(claim: dict, transcript_text: str) -> None:
    """Fix quote character offsets if they don't match."""
    start = claim.get("quote_start_char", 0)
    end = claim.get("quote_end_char", 0)
    quote = claim.get("quote_text", "")

    if start >= 0 and end > start and end <= len(transcript_text):
        actual = transcript_text[start:end]
        if actual == quote:
            return  # Already correct

    # Try exact match
    fixed_start = transcript_text.find(quote)
    if fixed_start >= 0:
        claim["quote_start_char"] = fixed_start
        claim["quote_end_char"] = fixed_start + len(quote)
        return

    # Try fuzzy match with first 50 chars
    if len(quote) > 20:
        substr = quote[:50]
        fuzzy_start = transcript_text.find(substr)
        if fuzzy_start >= 0:
            claim["quote_start_char"] = fuzzy_start
            claim["quote_end_char"] = fuzzy_start + len(quote)
            return

    # Can't validate span
    claim["quote_start_char"] = None
    claim["quote_end_char"] = None


def _fix_gaap(claim: dict) -> None:
    """Fix GAAP classification based on metric type and quote context."""
    metric = claim.get("metric_type", "")
    current = claim.get("gaap_classification", "unknown")
    quote = claim.get("quote_text", "")

    # If already explicitly set to non_gaap, trust it
    if current == "non_gaap":
        return

    # Check quote for explicit non-GAAP keywords
    if _NON_GAAP_KEYWORDS.search(quote):
        claim["gaap_classification"] = "non_gaap"
        return

    # Check quote for explicit GAAP keywords
    if _GAAP_KEYWORDS.search(quote):
        claim["gaap_classification"] = "gaap"
        return

    # For standard income statement metrics, default to GAAP
    if metric in _GAAP_DEFAULT_METRICS and current == "unknown":
        claim["gaap_classification"] = "gaap"
        return

    # EBITDA and FCF remain unknown if not explicitly stated
    # capital_expenditures is straightforward (always as-reported)
    if metric == "capital_expenditures" and current == "unknown":
        claim["gaap_classification"] = "gaap"


def _fix_comparison_period(claim: dict) -> None:
    """Infer missing comparison_period for growth claims."""
    claim_type = claim.get("claim_type", "")
    period = claim.get("period", "")
    comparison = claim.get("comparison_period")

    if claim_type not in ("yoy_growth", "qoq_growth"):
        return
    if comparison:
        return  # Already set

    # Try to infer from period
    match = re.match(r"Q(\d)\s+(\d{4})", period)
    if not match:
        return

    q = int(match.group(1))
    y = int(match.group(2))

    if claim_type == "yoy_growth":
        claim["comparison_period"] = f"Q{q} {y - 1}"
    elif claim_type == "qoq_growth":
        if q == 1:
            claim["comparison_period"] = f"Q4 {y - 1}"
        else:
            claim["comparison_period"] = f"Q{q - 1} {y}"


def _dedup_claims(claims: list[dict]) -> list[dict]:
    """Remove duplicate claims with overlapping spans and same metric."""
    deduped = []
    seen_spans = set()

    for claim in sorted(claims, key=lambda c: c.get("confidence", 0), reverse=True):
        start = claim.get("quote_start_char")
        end = claim.get("quote_end_char")
        metric = claim.get("metric_type")

        if start is not None and end is not None:
            is_dup = False
            for s_start, s_end, s_metric in seen_spans:
                if s_metric == metric and start < s_end and end > s_start:
                    is_dup = True
                    break

            if is_dup:
                continue
            seen_spans.add((start, end, metric))

        deduped.append(claim)

    return deduped
