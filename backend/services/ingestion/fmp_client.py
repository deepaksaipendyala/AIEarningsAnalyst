"""Financial Modeling Prep (FMP) API client for structured financial data and transcripts."""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from backend.config import settings


class FMPClient:
    """Client for FMP financial data and transcript API."""

    BASE_URL = "https://financialmodelingprep.com/api/v3"
    STABLE_URL = "https://financialmodelingprep.com/stable"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.fmp_api_key
        self.client = httpx.Client(timeout=60)

    def _get(self, endpoint: str, params: dict = None, base_url: str = None) -> list | dict:
        params = params or {}
        params["apikey"] = self.api_key
        url = f"{base_url or self.BASE_URL}{endpoint}"
        resp = self.client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # --- Transcript Fetching ---

    def get_transcript(self, ticker: str, year: int, quarter: int) -> list[dict]:
        """Fetch earnings call transcript from FMP.

        Returns list of transcript segments (usually one item with full content).
        """
        return self._get(f"/earning_call_transcript/{ticker}", {
            "year": year, "quarter": quarter,
        })

    def fetch_and_cache_transcript(self, ticker: str, year: int, quarter: int) -> Optional[dict]:
        """Fetch transcript for a specific quarter via FMP, with caching."""
        settings.ensure_dirs()
        key = f"{ticker}_Q{quarter}_{year}"
        cache_path = settings.transcripts_dir / f"{key}.json"

        if cache_path.exists():
            print(f"  [CACHE] {key}")
            with open(cache_path) as f:
                return json.load(f)

        try:
            result = self.get_transcript(ticker, year, quarter)

            if not result:
                print(f"  [MISS] {key} - no transcript on FMP")
                return None

            # FMP returns a list; each item has 'content' with the full transcript text
            # and optionally 'date', 'symbol', 'quarter', 'year'
            transcript_item = result[0] if isinstance(result, list) and result else result
            raw_text = transcript_item.get("content", "")

            if not raw_text:
                print(f"  [MISS] {key} - empty transcript content")
                return None

            data = {
                "ticker": ticker,
                "year": year,
                "quarter": quarter,
                "title": f"{ticker} Q{quarter} {year} Earnings Call",
                "call_date": transcript_item.get("date", ""),
                "text": raw_text,
                "speaker_sections": parse_fmp_speakers(raw_text),
                "text_hash": hashlib.sha256(raw_text.encode()).hexdigest(),
                "fetched_at": datetime.now().isoformat(),
                "source": "fmp",
            }

            with open(cache_path, "w") as f:
                json.dump(data, f, indent=2)

            print(f"  [OK] {key} ({len(raw_text)} chars)")
            return data

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                print(f"  [SKIP] {key} - FMP transcript requires higher plan")
            else:
                print(f"  [ERR] {key} - HTTP {e.response.status_code}")
            return None
        except Exception as e:
            print(f"  [ERR] {key} - {e}")
            return None

    # --- Financial Data ---

    def get_income_statements(self, ticker: str, limit: int = 5) -> list[dict]:
        """Fetch quarterly income statements via stable endpoint."""
        return self._get("/income-statement", {
            "symbol": ticker, "period": "quarter", "limit": limit,
        }, base_url=self.STABLE_URL)

    def get_cash_flow_statements(self, ticker: str, limit: int = 5) -> list[dict]:
        """Fetch quarterly cash flow statements via stable endpoint."""
        return self._get("/cash-flow-statement", {
            "symbol": ticker, "period": "quarter", "limit": limit,
        }, base_url=self.STABLE_URL)

    def fetch_and_cache_financials(self, ticker: str) -> Optional[dict]:
        """Fetch income statement + cash flow for a ticker, with caching."""
        settings.ensure_dirs()
        cache_path = settings.financials_dir / f"{ticker}_fmp.json"

        if cache_path.exists():
            print(f"  [CACHE] {ticker} FMP")
            with open(cache_path) as f:
                return json.load(f)

        data = {
            "ticker": ticker,
            "fetched_at": datetime.now().isoformat(),
            "source": "fmp",
        }

        try:
            data["income_statement"] = self.get_income_statements(ticker)
            print(f"  [OK] {ticker} FMP income_statement ({len(data['income_statement'])} quarters)")
            time.sleep(0.3)

            data["cash_flow"] = self.get_cash_flow_statements(ticker)
            print(f"  [OK] {ticker} FMP cash_flow ({len(data['cash_flow'])} quarters)")
            time.sleep(0.3)

        except Exception as e:
            print(f"  [ERR] {ticker} FMP - {e}")
            data["income_statement"] = data.get("income_statement", [])
            data["cash_flow"] = data.get("cash_flow", [])

        with open(cache_path, "w") as f:
            json.dump(data, f, indent=2)

        return data


# --- FMP Field Mapping ---

FMP_INCOME_MAP = {
    "revenue": "revenue",
    "net_income": "netIncome",
    "gross_profit": "grossProfit",
    "operating_income": "operatingIncome",
    "ebitda": "ebitda",
    "eps_basic": "eps",
    "eps_diluted": "epsDiluted",
    "cost_of_revenue": "costOfRevenue",
    "operating_expenses": "operatingExpenses",
    "research_and_development": "researchAndDevelopmentExpenses",
    "depreciation_and_amortization": "depreciationAndAmortization",
}

FMP_CASHFLOW_MAP = {
    "free_cash_flow": "freeCashFlow",
    "operating_cash_flow": "operatingCashFlow",
    "capital_expenditure": "capitalExpenditure",
}

# Fields that FMP reports as negative but claims reference as positive
_SIGN_FLIP_FIELDS = {"capital_expenditure"}


def parse_fmp_speakers(raw_text: str) -> list[dict]:
    """Parse FMP transcript text into speaker sections with character offsets.

    FMP transcripts typically use the format:
        Speaker Name - Title: speech text...
    or:
        Operator: speech text...
    """
    import re

    sections = []
    # Match lines like "Name - Title:" or "Name:" at the start of a paragraph
    speaker_pattern = re.compile(
        r'^([A-Z][A-Za-z\'. -]+(?:\s*-\s*[A-Za-z\s,&]+)?)\s*:\s*',
        re.MULTILINE,
    )

    matches = list(speaker_pattern.finditer(raw_text))
    if not matches:
        # No speaker markers found — treat entire text as one block
        return [{
            "name": "Unknown",
            "start_char": 0,
            "end_char": len(raw_text),
            "text": raw_text,
        }]

    for i, m in enumerate(matches):
        name_raw = m.group(1).strip()
        # Split "Tim Cook - Chief Executive Officer" into name and role
        if " - " in name_raw:
            name = name_raw.split(" - ")[0].strip()
        else:
            name = name_raw

        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        text = raw_text[start:end]

        sections.append({
            "name": name,
            "start_char": start,
            "end_char": end,
            "text": text,
        })

    return sections


def _extract_fiscal_year_quarter(stmt: dict) -> tuple[int, int] | None:
    """Extract the fiscal (year, quarter) key from an FMP statement."""
    period = stmt.get("period", "")
    if not period.startswith("Q"):
        return None
    try:
        q = int(period[1])
    except (TypeError, ValueError):
        return None

    # Stable endpoint provides fiscalYear for quarterly statements.
    fiscal_year = stmt.get("fiscalYear")
    if fiscal_year:
        return (int(fiscal_year), q)

    # Fallback for v3-style payloads that expose calendarYear only.
    cal_year = stmt.get("calendarYear")
    if cal_year:
        return (int(cal_year), q)

    return None


def _extract_calendar_year_quarter_from_date(stmt: dict) -> tuple[int, int] | None:
    """Extract calendar (year, quarter) from statement end date.

    This acts as an alias key so claims tagged with calendar quarters can
    still map to the same statement for non-calendar fiscal-year companies.
    """
    raw_date = stmt.get("date")
    if not raw_date or not isinstance(raw_date, str):
        return None

    # FMP dates are expected in YYYY-MM-DD format.
    try:
        year = int(raw_date[0:4])
        month = int(raw_date[5:7])
    except (TypeError, ValueError):
        return None

    if not (1 <= month <= 12):
        return None

    quarter = ((month - 1) // 3) + 1
    return (year, quarter)


def _extract_statement_period_keys(stmt: dict) -> list[tuple[int, int]]:
    """Return all useful period keys for a statement.

    Key order is intentional:
    1) fiscal key (primary for transcript/claim alignment)
    2) calendar key derived from the statement end date (alias)
    """
    keys: list[tuple[int, int]] = []
    fiscal_key = _extract_fiscal_year_quarter(stmt)
    if fiscal_key is not None:
        keys.append(fiscal_key)

    calendar_key = _extract_calendar_year_quarter_from_date(stmt)
    if calendar_key is not None and calendar_key not in keys:
        keys.append(calendar_key)

    return keys


def load_fmp_data(
    ticker: str,
    financials_dir: Path | None = None,
    sec_dir: Path | None = None,
    enable_sec_fallback: bool = True,
) -> dict:
    """Load and index FMP data by (year, quarter) -> {metric: value}."""
    base_financials_dir = Path(financials_dir) if financials_dir else settings.financials_dir
    path = base_financials_dir / f"{ticker}_fmp.json"
    raw = {}
    if path.exists():
        with open(path) as f:
            raw = json.load(f)

    indexed = {}
    calendar_aliases: dict[tuple[int, int], tuple[int, int]] = {}
    metric_sources: dict[tuple[int, int, str], str] = {}

    for stmt in raw.get("income_statement", []):
        period_keys = _extract_statement_period_keys(stmt)
        if not period_keys:
            continue

        # Fiscal key is always primary for verification.
        yq = period_keys[0]
        if yq not in indexed:
            indexed[yq] = {}

        for metric, field in FMP_INCOME_MAP.items():
            if field in stmt and stmt[field] is not None:
                indexed[yq][metric] = stmt[field]
                metric_sources[(yq[0], yq[1], metric)] = "fmp"

        indexed[yq]["_raw_income"] = stmt

        # Keep calendar key as an explicit alias to fiscal key.
        if len(period_keys) > 1:
            cal_yq = period_keys[1]
            if cal_yq != yq and cal_yq not in calendar_aliases:
                calendar_aliases[cal_yq] = yq

    for stmt in raw.get("cash_flow", []):
        period_keys = _extract_statement_period_keys(stmt)
        if not period_keys:
            continue

        yq = period_keys[0]
        if yq not in indexed:
            indexed[yq] = {}

        for metric, field in FMP_CASHFLOW_MAP.items():
            if field in stmt and stmt[field] is not None:
                val = stmt[field]
                # Flip sign for fields FMP reports as negative (e.g. CapEx)
                if metric in _SIGN_FLIP_FIELDS and val < 0:
                    val = abs(val)
                indexed[yq][metric] = val
                metric_sources[(yq[0], yq[1], metric)] = "fmp"

        # Alias: capital_expenditures -> capital_expenditure (extraction uses plural)
        if "capital_expenditure" in indexed[yq]:
            indexed[yq]["capital_expenditures"] = indexed[yq]["capital_expenditure"]
            metric_sources[(yq[0], yq[1], "capital_expenditures")] = metric_sources.get(
                (yq[0], yq[1], "capital_expenditure"), "fmp"
            )

        indexed[yq]["_raw_cashflow"] = stmt

        if len(period_keys) > 1:
            cal_yq = period_keys[1]
            if cal_yq != yq and cal_yq not in calendar_aliases:
                calendar_aliases[cal_yq] = yq

    if calendar_aliases:
        indexed["_calendar_aliases"] = calendar_aliases

    # Fill missing periods/metrics from SEC Company Facts when available.
    # Keep FMP as primary source and only backfill missing metric-period points.
    sec_fallback_metrics = {
        # Income statement metrics
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
        # Cash flow metrics
        "free_cash_flow",
        "operating_cash_flow",
        "capital_expenditure",
        "capital_expenditures",
        # Balance sheet metrics
        "cash_and_marketable_securities",
        "total_debt",
        "net_cash",
    }
    if enable_sec_fallback:
        try:
            from backend.services.ingestion.sec_client import load_sec_data

            sec_indexed = load_sec_data(ticker, sec_dir=sec_dir)
            for yq, sec_vals in sec_indexed.items():
                if yq not in indexed:
                    indexed[yq] = {}
                for metric, val in sec_vals.items():
                    if val is None:
                        continue
                    if metric not in sec_fallback_metrics:
                        continue
                    if metric not in indexed[yq]:
                        indexed[yq][metric] = val
                        metric_sources[(yq[0], yq[1], metric)] = "sec_companyfacts"
                    # Keep alias parity with FMP cash-flow normalization.
                    if metric == "capital_expenditure" and "capital_expenditures" not in indexed[yq]:
                        indexed[yq]["capital_expenditures"] = indexed[yq][metric]
                        metric_sources[(yq[0], yq[1], "capital_expenditures")] = "sec_companyfacts"
        except Exception:
            pass

    if metric_sources:
        indexed["_metric_sources"] = metric_sources

    return indexed
