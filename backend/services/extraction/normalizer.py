"""Value normalization for extracted claims."""

import re
from typing import Optional


SCALE_MULTIPLIERS = {
    "ones": 1,
    "thousands": 1_000,
    "millions": 1_000_000,
    "billions": 1_000_000_000,
    "trillions": 1_000_000_000_000,
    None: 1,
}


def normalize_claimed_value(claimed_value: float, unit: str, scale: str = None) -> float:
    """Convert claimed value to raw units for comparison.

    Examples:
        normalize_claimed_value(50.3, "dollars", "billions") -> 50_300_000_000
        normalize_claimed_value(15.0, "percent", None) -> 15.0
        normalize_claimed_value(1.42, "per_share", None) -> 1.42
        normalize_claimed_value(200, "basis_points", None) -> 2.0
    """
    if unit == "percent":
        return claimed_value
    if unit == "basis_points":
        return claimed_value / 100  # Convert bps to percentage points
    if unit == "per_share":
        return claimed_value  # EPS compared directly
    if unit == "ratio":
        return claimed_value

    multiplier = SCALE_MULTIPLIERS.get(scale, 1)
    return claimed_value * multiplier


def parse_period(period_str: str) -> Optional[tuple[int, int]]:
    """Parse period string like 'Q3 2024' into (year, quarter).

    Returns None if unparseable. quarter=0 means full year.
    """
    if not period_str:
        return None

    period_str = period_str.strip().upper()

    # "Q3 2024", "Q3 FY2024"
    m = re.match(r"Q(\d)\s*(?:FY)?(\d{4})", period_str)
    if m:
        return (int(m.group(2)), int(m.group(1)))

    # "3Q 2024", "3Q2024"
    m = re.match(r"(\d)Q\s*(?:FY)?(\d{4})", period_str)
    if m:
        return (int(m.group(2)), int(m.group(1)))

    # "3Q24"
    m = re.match(r"(\d)Q(\d{2})$", period_str)
    if m:
        return (2000 + int(m.group(2)), int(m.group(1)))

    # "FY 2024", "FY2024"
    m = re.match(r"FY\s*(\d{4})", period_str)
    if m:
        return (int(m.group(1)), 0)

    # "FISCAL 2024"
    m = re.match(r"FISCAL\s*(?:YEAR\s*)?(\d{4})", period_str)
    if m:
        return (int(m.group(1)), 0)

    return None


def detect_scale_from_text(text: str) -> Optional[str]:
    """Detect scale from raw text like '$50.3 billion'."""
    text_lower = text.lower()
    if "trillion" in text_lower:
        return "trillions"
    if "billion" in text_lower:
        return "billions"
    if "million" in text_lower:
        return "millions"
    if "thousand" in text_lower:
        return "thousands"
    return None


def extract_numeric_from_text(text: str) -> Optional[float]:
    """Extract the first numeric value from text like '$50.3 billion'."""
    m = re.search(r"\$?\s*([\d,]+\.?\d*)", text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None
