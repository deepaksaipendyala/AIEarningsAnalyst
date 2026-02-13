"""Tolerance matrix for claim verification."""


# Tolerances by metric type
# tight: strict match threshold
# loose: close match threshold
# approx: approximate (for "about", "roughly" qualifiers)
TOLERANCES = {
    "revenue":                  {"tight": 0.005, "loose": 0.02, "approx": 0.05},
    "net_income":               {"tight": 0.01,  "loose": 0.03, "approx": 0.05},
    "eps_basic":                {"tight": 0.01,  "loose": 0.02, "approx": 0.05},
    "eps_diluted":              {"tight": 0.01,  "loose": 0.02, "approx": 0.05},
    "gross_profit":             {"tight": 0.005, "loose": 0.02, "approx": 0.05},
    "gross_margin":             {"tight": 0.003, "loose": 0.01, "approx": 0.02},  # pp as decimals
    "operating_income":         {"tight": 0.01,  "loose": 0.03, "approx": 0.05},
    "operating_margin":         {"tight": 0.003, "loose": 0.01, "approx": 0.02},
    "ebitda":                   {"tight": 0.01,  "loose": 0.03, "approx": 0.05},
    "free_cash_flow":           {"tight": 0.02,  "loose": 0.05, "approx": 0.10},
    "operating_cash_flow":      {"tight": 0.01,  "loose": 0.03, "approx": 0.05},
    "cost_of_revenue":          {"tight": 0.005, "loose": 0.02, "approx": 0.05},
    "capital_expenditures":     {"tight": 0.02,  "loose": 0.05, "approx": 0.10},
    "operating_expenses":       {"tight": 0.01,  "loose": 0.03, "approx": 0.05},
    "research_and_development": {"tight": 0.01,  "loose": 0.03, "approx": 0.05},
    "other":                    {"tight": 0.02,  "loose": 0.05, "approx": 0.10},
}

# EPS absolute tolerance ($0.015 covers rounding)
EPS_ABSOLUTE_TOLERANCE = 0.015

# Growth rate tolerance in percentage points
GROWTH_RATE_TOLERANCE_PP = 1.0
GROWTH_RATE_LOOSE_PP = 2.0

# Margin tolerance in percentage points (already stored as decimals in TOLERANCES)
# The values in TOLERANCES for margins are in decimal form (0.003 = 0.3 pp)

APPROXIMATE_QUALIFIERS = {"approximately", "about", "roughly", "nearly", "around", "close to"}


def is_approximate(qualifiers: list[str]) -> bool:
    """Check if any qualifier indicates an approximate claim."""
    return bool(set(q.lower() for q in qualifiers) & APPROXIMATE_QUALIFIERS)


def get_tolerance(metric: str, is_approx: bool = False) -> dict:
    """Get tolerance thresholds for a given metric.

    Returns dict with 'tight' and 'loose' thresholds.
    """
    tol = TOLERANCES.get(metric, TOLERANCES["other"])

    if is_approx:
        return {"tight": tol["approx"], "loose": tol["approx"] * 1.5}

    return {"tight": tol["tight"], "loose": tol["loose"]}


def get_growth_tolerance(is_approx: bool = False) -> dict:
    """Get tolerance for growth rate claims (in percentage points)."""
    if is_approx:
        return {"tight": GROWTH_RATE_TOLERANCE_PP * 2, "loose": GROWTH_RATE_LOOSE_PP * 2}
    return {"tight": GROWTH_RATE_TOLERANCE_PP, "loose": GROWTH_RATE_LOOSE_PP}
