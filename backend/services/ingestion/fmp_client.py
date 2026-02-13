"""Financial Modeling Prep (FMP) API client for structured financial data and transcripts."""

import hashlib
import json
import time
from datetime import datetime
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
    """Extract fiscal year and quarter from an FMP statement.

    Uses fiscalYear + period (Q1-Q4) to create the key that matches
    how transcripts and claims reference periods. For companies with
    non-calendar fiscal years (AAPL ends Sep, MSFT ends Jun, etc.),
    this correctly maps to the period the earnings call refers to.
    """
    period = stmt.get("period", "")
    if not period.startswith("Q"):
        return None
    q = int(period[1])

    # Try calendarYear first (v3 endpoint)
    cal_year = stmt.get("calendarYear")
    if cal_year:
        return (int(cal_year), q)

    # Use fiscalYear (stable endpoint)
    fiscal_year = stmt.get("fiscalYear")
    if fiscal_year:
        return (int(fiscal_year), q)

    return None


def load_fmp_data(ticker: str) -> dict:
    """Load and index FMP data by (year, quarter) -> {metric: value}."""
    path = settings.financials_dir / f"{ticker}_fmp.json"
    if not path.exists():
        return {}

    with open(path) as f:
        raw = json.load(f)

    indexed = {}

    for stmt in raw.get("income_statement", []):
        result = _extract_fiscal_year_quarter(stmt)
        if result is None:
            continue
        yq = result

        if yq not in indexed:
            indexed[yq] = {}

        for metric, field in FMP_INCOME_MAP.items():
            if field in stmt and stmt[field] is not None:
                indexed[yq][metric] = stmt[field]

        indexed[yq]["_raw_income"] = stmt

    for stmt in raw.get("cash_flow", []):
        result = _extract_fiscal_year_quarter(stmt)
        if result is None:
            continue
        yq = result

        if yq not in indexed:
            indexed[yq] = {}

        for metric, field in FMP_CASHFLOW_MAP.items():
            if field in stmt and stmt[field] is not None:
                val = stmt[field]
                # Flip sign for fields FMP reports as negative (e.g. CapEx)
                if metric in _SIGN_FLIP_FIELDS and val < 0:
                    val = abs(val)
                indexed[yq][metric] = val

        # Alias: capital_expenditures -> capital_expenditure (extraction uses plural)
        if "capital_expenditure" in indexed[yq]:
            indexed[yq]["capital_expenditures"] = indexed[yq]["capital_expenditure"]

        indexed[yq]["_raw_cashflow"] = stmt

    return indexed
