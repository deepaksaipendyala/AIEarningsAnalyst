"""Metric catalog: maps canonical metric names to FMP fields and computation templates."""

METRIC_CATALOG = {
    "revenue": {
        "fmp_field": "revenue",
        "statement": "income_statement",
        "unit": "dollars",
        "computation": "direct",
    },
    "eps_basic": {
        "fmp_field": "eps_basic",
        "statement": "income_statement",
        "unit": "per_share",
        "computation": "direct",
    },
    "eps_diluted": {
        "fmp_field": "eps_diluted",
        "statement": "income_statement",
        "unit": "per_share",
        "computation": "direct",
    },
    "gross_profit": {
        "fmp_field": "gross_profit",
        "statement": "income_statement",
        "unit": "dollars",
        "computation": "direct",
    },
    "gross_margin": {
        "fmp_fields": {"numerator": "gross_profit", "denominator": "revenue"},
        "statement": "income_statement",
        "unit": "percent",
        "computation": "ratio",
    },
    "operating_income": {
        "fmp_field": "operating_income",
        "statement": "income_statement",
        "unit": "dollars",
        "computation": "direct",
    },
    "operating_margin": {
        "fmp_fields": {"numerator": "operating_income", "denominator": "revenue"},
        "statement": "income_statement",
        "unit": "percent",
        "computation": "ratio",
    },
    "net_income": {
        "fmp_field": "net_income",
        "statement": "income_statement",
        "unit": "dollars",
        "computation": "direct",
    },
    "ebitda": {
        "fmp_field": "ebitda",
        "statement": "income_statement",
        "unit": "dollars",
        "computation": "direct",
        "note": "Treat as non-GAAP by default",
    },
    "free_cash_flow": {
        "fmp_field": "free_cash_flow",
        "statement": "cash_flow",
        "unit": "dollars",
        "computation": "direct",
    },
    "operating_cash_flow": {
        "fmp_field": "operating_cash_flow",
        "statement": "cash_flow",
        "unit": "dollars",
        "computation": "direct",
    },
    "cost_of_revenue": {
        "fmp_field": "cost_of_revenue",
        "statement": "income_statement",
        "unit": "dollars",
        "computation": "direct",
    },
    "capital_expenditures": {
        "fmp_field": "capital_expenditures",
        "statement": "cash_flow",
        "unit": "dollars",
        "computation": "direct",
    },
    "operating_expenses": {
        "fmp_field": "operating_expenses",
        "statement": "income_statement",
        "unit": "dollars",
        "computation": "direct",
    },
    "research_and_development": {
        "fmp_field": "research_and_development",
        "statement": "income_statement",
        "unit": "dollars",
        "computation": "direct",
    },
    "cash_and_marketable_securities": {
        "fmp_field": "cash_and_marketable_securities",
        "statement": "balance_sheet",
        "unit": "dollars",
        "computation": "direct",
    },
    "total_debt": {
        "fmp_field": "total_debt",
        "statement": "balance_sheet",
        "unit": "dollars",
        "computation": "direct",
    },
    "net_cash": {
        "fmp_field": "net_cash",
        "statement": "balance_sheet",
        "unit": "dollars",
        "computation": "direct",
    },
}


def get_catalog_entry(metric: str) -> dict | None:
    """Get the catalog entry for a metric, with fallback."""
    return METRIC_CATALOG.get(metric)
