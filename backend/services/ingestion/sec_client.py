"""SEC Company Facts client for supplemental historical financial metrics.

Used as a fallback when FMP coverage is missing prior quarters.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from backend.config import settings


_SEC_BASE_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SEC_USER_AGENT = "EarningsLens/1.0 (research; contact@example.com)"
_SEC_CACHE_SCHEMA_VERSION = 4


# Primary metrics used by the verifier.
_SEC_TAGS = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "eps_basic": ["EarningsPerShareBasic"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "cost_of_revenue": ["CostOfGoodsAndServicesSold"],
    "operating_expenses": ["OperatingExpenses"],
    "sga_expenses": ["SellingGeneralAndAdministrativeExpense"],
    "research_and_development": ["ResearchAndDevelopmentExpense"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditure": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    # Balance-sheet style fallbacks for "other" remapped claims.
    "_cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "_short_term_investments": [
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesCurrent",
    ],
    "_long_term_investments": [
        "MarketableSecuritiesNoncurrent",
        "AvailableForSaleSecuritiesNoncurrent",
    ],
    "_cash_and_short_term": ["CashCashEquivalentsAndShortTermInvestments"],
    "_debt_total": ["DebtLongtermAndShorttermCombinedAmount", "DebtInstrumentCarryingAmount"],
    "_debt_current": ["DebtCurrent", "ShortTermBorrowings", "CommercialPaper"],
    "_debt_noncurrent": ["LongTermDebt", "LongTermDebtNoncurrent"],
}

_USD_METRICS = {
    "revenue",
    "net_income",
    "gross_profit",
    "operating_income",
    "cost_of_revenue",
    "operating_expenses",
    "sga_expenses",
    "research_and_development",
    "operating_cash_flow",
    "capital_expenditure",
    "_cash",
    "_short_term_investments",
    "_long_term_investments",
    "_cash_and_short_term",
    "_debt_total",
    "_debt_current",
    "_debt_noncurrent",
}

_EPS_METRICS = {"eps_basic", "eps_diluted"}
_DURATION_METRICS = {
    "revenue",
    "net_income",
    "gross_profit",
    "operating_income",
    "eps_basic",
    "eps_diluted",
    "cost_of_revenue",
    "operating_expenses",
    "sga_expenses",
    "research_and_development",
    "operating_cash_flow",
    "capital_expenditure",
}


def _load_companies() -> list[dict]:
    path = settings.data_dir / "companies.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _ticker_to_cik(ticker: str) -> Optional[str]:
    ticker = ticker.upper()
    for company in _load_companies():
        if company.get("ticker", "").upper() == ticker:
            cik = str(company.get("cik", "")).strip()
            if cik:
                return cik.zfill(10)
    return None


def _parse_fp_quarter(fp: str) -> Optional[int]:
    if not isinstance(fp, str):
        return None
    fp = fp.upper()
    if fp in ("Q1", "Q2", "Q3", "Q4"):
        return int(fp[1])
    return None


def _duration_days(entry: dict) -> Optional[int]:
    start = entry.get("start")
    end = entry.get("end")
    if not start or not end:
        return None
    try:
        ds = datetime.fromisoformat(start)
        de = datetime.fromisoformat(end)
    except Exception:
        return None
    return max(1, (de - ds).days)


def _entry_score(entry: dict, metric: str) -> tuple[int, str, str]:
    """Higher score is better; second key breaks ties by filing date."""
    form = str(entry.get("form", "")).upper()
    end = str(entry.get("end", ""))
    filed = str(entry.get("filed", ""))
    score = 0

    if form in ("10-Q", "10-Q/A"):
        score += 10
    if form in ("10-K", "10-K/A"):
        score += 8

    if metric in _DURATION_METRICS:
        days = _duration_days(entry)
        if days is not None:
            # Prefer quarter-length durations over YTD/full-year durations.
            if 70 <= days <= 120:
                score += 40
            elif 140 <= days <= 210:
                score -= 15
            elif days >= 250:
                score -= 25

    # Prefer the most recent period end when a filing includes both current and
    # prior-year comparative values under the same (fy, fp).
    return (score, end, filed)


def _select_best_entry(entries: list[dict], metric: str) -> Optional[dict]:
    if not entries:
        return None
    return max(entries, key=lambda e: _entry_score(e, metric))


def _extract_metric_series(facts: dict, metric: str) -> dict[tuple[int, int], float]:
    """Extract fiscal-quarter values for one metric."""
    result: dict[tuple[int, int], float] = {}
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        return result

    tag_candidates = _SEC_TAGS.get(metric, [])
    unit_candidates = ["USD"] if metric in _USD_METRICS else ["USD/shares"]
    if metric in _EPS_METRICS:
        unit_candidates = ["USD/shares", "USDPerShare"]

    # Use first tag that yields data.
    chosen_items = None
    for tag in tag_candidates:
        node = us_gaap.get(tag)
        if not node:
            continue
        units = node.get("units", {})
        items = []
        for unit_key in unit_candidates:
            items.extend(units.get(unit_key, []))
        if items:
            chosen_items = items
            break

    if not chosen_items:
        return result

    grouped: dict[tuple[int, int], list[dict]] = {}
    grouped_fy: dict[int, list[dict]] = {}
    for item in chosen_items:
        fy = item.get("fy")
        fp = item.get("fp")
        if fy is None:
            continue
        try:
            y = int(fy)
            val = float(item.get("val"))
        except Exception:
            continue

        if isinstance(fp, str) and fp.upper() == "FY":
            grouped_fy.setdefault(y, []).append({**item, "val": val})
            continue

        q = _parse_fp_quarter(fp)
        if q is None:
            continue
        grouped.setdefault((y, q), []).append({**item, "val": val})

    for yq, entries in grouped.items():
        best = _select_best_entry(entries, metric)
        if best is None:
            continue
        val = float(best["val"])
        if metric == "capital_expenditure":
            val = abs(val)
        result[yq] = val

    # Derive fiscal Q4 for duration metrics from FY totals when Q1-Q3 exist.
    if metric in _DURATION_METRICS:
        years = {y for (y, _q) in result.keys()}
        for year in years:
            if (year, 4) in result:
                continue
            if not all((year, q) in result for q in (1, 2, 3)):
                continue
            fy_entries = grouped_fy.get(year)
            if not fy_entries:
                continue
            fy_best = _select_best_entry(fy_entries, metric)
            if fy_best is None:
                continue
            fy_val = float(fy_best["val"])
            q1_q3_sum = result[(year, 1)] + result[(year, 2)] + result[(year, 3)]
            q4_val = fy_val - q1_q3_sum
            if metric == "capital_expenditure":
                q4_val = abs(q4_val)
            result[(year, 4)] = q4_val

    return result


def _extract_metrics_index(facts: dict) -> dict[tuple[int, int], dict]:
    """Extract and normalize SEC metrics to the verifier index format."""
    by_period: dict[tuple[int, int], dict] = {}

    metric_series = {metric: _extract_metric_series(facts, metric) for metric in _SEC_TAGS}

    # Assemble direct metrics.
    for metric, series in metric_series.items():
        for yq, val in series.items():
            by_period.setdefault(yq, {})
            by_period[yq][metric] = val

    # Derived helpers.
    for yq, row in by_period.items():
        cash_st = row.get("_cash_and_short_term")
        cash = row.get("_cash")
        st_inv = row.get("_short_term_investments")
        lt_inv = row.get("_long_term_investments")
        if cash_st is not None:
            row["cash_and_marketable_securities"] = cash_st + (lt_inv or 0.0)
        elif cash is not None or st_inv is not None or lt_inv is not None:
            row["cash_and_marketable_securities"] = (
                (cash or 0.0) + (st_inv or 0.0) + (lt_inv or 0.0)
            )

        debt_total = row.get("_debt_total")
        if debt_total is None:
            debt_cur = row.get("_debt_current")
            debt_noncur = row.get("_debt_noncurrent")
            if debt_cur is not None or debt_noncur is not None:
                debt_total = (debt_cur or 0.0) + (debt_noncur or 0.0)
        if debt_total is not None:
            row["total_debt"] = debt_total

        if "cash_and_marketable_securities" in row and "total_debt" in row:
            row["net_cash"] = row["cash_and_marketable_securities"] - row["total_debt"]

        # Free cash flow as derived OCF - CapEx.
        if "operating_cash_flow" in row and "capital_expenditure" in row:
            row["free_cash_flow"] = row["operating_cash_flow"] - row["capital_expenditure"]

    # Remove internal helper metrics.
    helper_prefix = "_"
    for row in by_period.values():
        for key in list(row.keys()):
            if key.startswith(helper_prefix):
                del row[key]

    return by_period


def fetch_and_cache_sec_metrics(ticker: str, sec_dir: Path | None = None) -> dict:
    """Fetch SEC company facts and cache parsed quarter metrics."""
    settings.ensure_dirs()
    ticker = ticker.upper()
    target_sec_dir = Path(sec_dir) if sec_dir else settings.sec_dir
    target_sec_dir.mkdir(parents=True, exist_ok=True)
    cache_path = target_sec_dir / f"{ticker}_metrics.json"
    stale_payload = None
    if cache_path.exists():
        with open(cache_path) as f:
            cached = json.load(f)
        if cached.get("schema_version") == _SEC_CACHE_SCHEMA_VERSION:
            return cached
        stale_payload = cached

    cik = _ticker_to_cik(ticker)
    if not cik:
        return {}

    url = _SEC_BASE_URL.format(cik=cik)
    headers = {"User-Agent": _SEC_USER_AGENT, "Accept": "application/json"}
    try:
        resp = httpx.get(url, headers=headers, timeout=45.0, follow_redirects=True)
        resp.raise_for_status()
        facts = resp.json()
    except Exception:
        return stale_payload or {}

    indexed = _extract_metrics_index(facts)
    serializable = {
        "ticker": ticker,
        "cik": cik,
        "source": "sec_companyfacts",
        "schema_version": _SEC_CACHE_SCHEMA_VERSION,
        "fetched_at": datetime.now().isoformat(),
        "periods": {f"{y}_Q{q}": vals for (y, q), vals in indexed.items()},
    }

    with open(cache_path, "w") as f:
        json.dump(serializable, f, indent=2)

    return serializable


def load_sec_data(
    ticker: str,
    sec_dir: Path | None = None,
    allow_fetch: bool = True,
) -> dict[tuple[int, int], dict]:
    """Load SEC cached metrics indexed by (year, quarter)."""
    ticker = ticker.upper()
    target_sec_dir = Path(sec_dir) if sec_dir else settings.sec_dir
    path = target_sec_dir / f"{ticker}_metrics.json"
    if not path.exists():
        if not allow_fetch:
            return {}
        payload = fetch_and_cache_sec_metrics(ticker, sec_dir=target_sec_dir)
        if not payload:
            return {}

    with open(path) as f:
        payload = json.load(f)
    if payload.get("schema_version") != _SEC_CACHE_SCHEMA_VERSION:
        if not allow_fetch:
            return {}
        payload = fetch_and_cache_sec_metrics(ticker, sec_dir=target_sec_dir)
        if not payload:
            return {}

    periods = payload.get("periods", {})
    indexed: dict[tuple[int, int], dict] = {}
    for key, vals in periods.items():
        try:
            year_str, q_str = key.split("_Q")
            y = int(year_str)
            q = int(q_str)
        except Exception:
            continue
        if not isinstance(vals, dict):
            continue
        indexed[(y, q)] = vals

    return indexed
