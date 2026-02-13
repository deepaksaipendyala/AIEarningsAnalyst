"""LLM-based claim extraction from earnings call transcripts via OpenRouter."""

import json
import re
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from backend.config import settings
from backend.schemas.extraction import EXTRACTION_SCHEMA, SYSTEM_PROMPT
from backend.services.ingestion.fmp_client import load_fmp_data
from backend.services.ingestion.sec_client import load_sec_data
from backend.services.extraction.validator import validate_claims

_URL_PERIOD_RE = re.compile(
    r"/[a-z0-9-]+-q([1-4])-(20\d{2})-earnings-call-transcript/?$",
    re.IGNORECASE,
)

_CONTEXT_METRIC_ORDER = [
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_diluted",
    "eps_basic",
    "operating_cash_flow",
    "free_cash_flow",
    "capital_expenditures",
    "cost_of_revenue",
    "operating_expenses",
    "research_and_development",
]


def _period_label(year: int, quarter: int) -> str:
    if quarter == 0:
        return f"FY {year}"
    return f"Q{quarter} {year}"


def _available_metrics(row: dict) -> list[str]:
    if not isinstance(row, dict):
        return []
    metrics = []
    for metric in _CONTEXT_METRIC_ORDER:
        value = row.get(metric)
        if isinstance(value, (int, float)):
            metrics.append(metric)
    if metrics:
        return metrics
    # Fallback for rows containing non-standard numeric fields.
    for key, value in row.items():
        if key.startswith("_"):
            continue
        if isinstance(value, (int, float)):
            metrics.append(key)
    return metrics


def _extract_source_url_fiscal_hint(transcript_meta: dict | None) -> tuple[int, int] | None:
    source_url = str((transcript_meta or {}).get("source_url") or "")
    match = _URL_PERIOD_RE.search(source_url)
    if not match:
        return None
    return (int(match.group(2)), int(match.group(1)))


def _render_financial_context(
    ticker: str,
    transcript_year: int,
    transcript_quarter: int,
    fmp_data: dict,
    sec_data: dict,
    transcript_meta: dict | None = None,
    max_periods: int = 6,
) -> str:
    """Render compact financial context to improve period/comparison extraction."""
    lines: list[str] = []
    lines.append(
        "Financial period map (for period/comparison_period field selection):"
    )
    lines.append(
        f"- Transcript file label: {_period_label(transcript_year, transcript_quarter)}"
    )

    url_hint = _extract_source_url_fiscal_hint(transcript_meta)
    if url_hint:
        lines.append(f"- Source URL fiscal hint: {_period_label(url_hint[0], url_hint[1])}")

    fmp_periods = sorted(
        [k for k in fmp_data.keys() if isinstance(k, tuple)],
        reverse=True,
    )[:max_periods]
    if fmp_periods:
        lines.append("- FMP periods (metric coverage):")
        for year, quarter in fmp_periods:
            row = fmp_data.get((year, quarter), {})
            metrics = _available_metrics(row)
            metric_str = ", ".join(metrics[:8]) if metrics else "no tracked metrics"
            if len(metrics) > 8:
                metric_str += ", ..."
            lines.append(f"  - {_period_label(year, quarter)}: {metric_str}")

    aliases = fmp_data.get("_calendar_aliases", {}) if isinstance(fmp_data, dict) else {}
    alias_items = sorted(aliases.items(), reverse=True)[:max_periods]
    if alias_items:
        lines.append("- Calendar alias mapping from filing dates:")
        for cal_yq, fiscal_yq in alias_items:
            lines.append(
                f"  - {_period_label(cal_yq[0], cal_yq[1])} -> {_period_label(fiscal_yq[0], fiscal_yq[1])}"
            )

    sec_periods = sorted(
        [k for k in sec_data.keys() if isinstance(k, tuple)],
        reverse=True,
    )[:max_periods]
    if sec_periods:
        lines.append("- SEC supplemental periods (if FMP period is missing):")
        for year, quarter in sec_periods:
            row = sec_data.get((year, quarter), {})
            metrics = _available_metrics(row)
            metric_str = ", ".join(metrics[:6]) if metrics else "no tracked metrics"
            if len(metrics) > 6:
                metric_str += ", ..."
            lines.append(f"  - {_period_label(year, quarter)}: {metric_str}")

    lines.extend(
        [
            "Period rules:",
            "- Keep period/comparison_period aligned to the quote wording; do not invent periods.",
            "- For YoY claims, set comparison_period to same quarter prior year unless explicitly different.",
            "- For QoQ claims, set comparison_period to immediate previous quarter.",
        ]
    )

    return "\n".join(lines)


def _build_user_prompt(
    transcript_text: str,
    ticker: str,
    year: int,
    quarter: int,
    chunk_label: str = "",
    financial_context: str = "",
) -> str:
    label = f" ({chunk_label})" if chunk_label else ""
    context_block = ""
    if financial_context:
        context_block = (
            "\nUse this additional structured context to resolve period/comparison_period fields:\n"
            "<financial_context>\n"
            f"{financial_context}\n"
            "</financial_context>\n"
        )

    return f"""Here is the earnings call transcript for {ticker}, Q{quarter} {year}{label}.

The transcript text is provided below. Character offsets start at 0 for the first character.
When providing quote_start_char and quote_end_char, ensure that transcript_text[start:end] exactly matches quote_text.
{context_block}
<transcript>
{transcript_text}
</transcript>

Extract all quantitative financial claims from this transcript. Be thorough and precise.
Remember: quote_text must exactly match the substring at the given character offsets.
Use short, focused quotes — just the sentence containing the numeric claim."""


class ClaimExtractor:
    """Extract quantitative claims from transcripts using OpenRouter (OpenAI-compatible)."""

    def __init__(self, api_key: str = None):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key or settings.openrouter_api_key,
        )
        self.model = settings.extraction_model

    def _build_financial_context(
        self,
        ticker: str,
        year: int,
        quarter: int,
        transcript_meta: dict | None = None,
    ) -> str:
        """Build compact SEC/FMP context to reduce period/comparison extraction errors."""
        try:
            fmp_data = load_fmp_data(ticker)
        except Exception:
            fmp_data = {}
        try:
            sec_data = load_sec_data(ticker, allow_fetch=False)
        except Exception:
            sec_data = {}
        return _render_financial_context(
            ticker=ticker,
            transcript_year=year,
            transcript_quarter=quarter,
            fmp_data=fmp_data,
            sec_data=sec_data,
            transcript_meta=transcript_meta,
        )

    def _call_extraction(
        self,
        transcript_text: str,
        ticker: str,
        year: int,
        quarter: int,
        chunk_label: str = "",
        financial_context: str = "",
    ) -> list[dict]:
        """Make a single extraction API call and return raw claims list."""
        user_prompt = _build_user_prompt(
            transcript_text=transcript_text,
            ticker=ticker,
            year=year,
            quarter=quarter,
            chunk_label=chunk_label,
            financial_context=financial_context,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=settings.extraction_max_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "submit_extracted_claims",
                    "description": "Submit the extracted financial claims from the transcript",
                    "parameters": EXTRACTION_SCHEMA,
                },
            }],
            tool_choice={"type": "function", "function": {"name": "submit_extracted_claims"}},
        )

        choice = response.choices[0]
        if choice.finish_reason == "length":
            return []

        if choice.message.tool_calls:
            args = choice.message.tool_calls[0].function.arguments
            parsed = json.loads(args)
            return parsed.get("claims", [])

        return []

    def extract_from_text(
        self,
        transcript_text: str,
        ticker: str,
        year: int,
        quarter: int,
        transcript_meta: dict | None = None,
    ) -> dict:
        """Extract claims from full transcript, splitting into chunks if needed."""
        financial_context = self._build_financial_context(
            ticker=ticker,
            year=year,
            quarter=quarter,
            transcript_meta=transcript_meta,
        )
        user_prompt = _build_user_prompt(
            transcript_text=transcript_text,
            ticker=ticker,
            year=year,
            quarter=quarter,
            financial_context=financial_context,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=settings.extraction_max_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "submit_extracted_claims",
                    "description": "Submit the extracted financial claims from the transcript",
                    "parameters": EXTRACTION_SCHEMA,
                },
            }],
            tool_choice={"type": "function", "function": {"name": "submit_extracted_claims"}},
        )

        choice = response.choices[0]

        # If the full transcript fit, use the result directly
        if choice.finish_reason != "length":
            if choice.message.tool_calls:
                args = choice.message.tool_calls[0].function.arguments
                result = json.loads(args)
                result["claims"] = validate_claims(result.get("claims", []), transcript_text)
                return result
            return {"claims": [], "transcript_summary": "Extraction failed", "total_claims_found": 0}

        # Output truncated — split transcript into chunks and combine
        chunks = _split_transcript(transcript_text, num_chunks=3)

        all_claims = []
        for i, (chunk_text, offset) in enumerate(chunks):
            chunk_claims = self._call_extraction(
                chunk_text, ticker, year, quarter,
                chunk_label=f"part {i+1}/{len(chunks)}",
                financial_context=financial_context,
            )
            # Adjust character offsets to be relative to the full transcript
            for claim in chunk_claims:
                if "quote_start_char" in claim and claim["quote_start_char"] is not None:
                    claim["quote_start_char"] += offset
                if "quote_end_char" in claim and claim["quote_end_char"] is not None:
                    claim["quote_end_char"] += offset
            all_claims.extend(chunk_claims)

        # Validate against full transcript text and dedup
        validated = validate_claims(all_claims, transcript_text)

        return {
            "claims": validated,
            "transcript_summary": f"{ticker} Q{quarter} {year} earnings call (extracted in {len(chunks)} chunks)",
            "total_claims_found": len(validated),
        }

    def extract_and_cache(self, ticker: str, year: int, quarter: int, force: bool = False) -> dict:
        """Extract claims from cached transcript, with caching."""
        settings.ensure_dirs()
        key = f"{ticker}_Q{quarter}_{year}"
        claims_path = settings.claims_dir / f"{key}_claims.json"

        if claims_path.exists() and not force:
            with open(claims_path) as f:
                cached = json.load(f)
            # Skip cache if it contains an error from a previous failed run
            if "error" not in cached:
                print(f"  [CACHE] {key}")
                return cached
            else:
                print(f"  [RETRY] {key} - previous run had error, re-extracting")
        elif claims_path.exists() and force:
            print(f"  [FORCE] {key} - ignoring cached claims and re-extracting")

        transcript_path = settings.transcripts_dir / f"{key}.json"
        if not transcript_path.exists():
            print(f"  [SKIP] {key} - no transcript")
            return {"claims": [], "error": "No transcript found"}

        with open(transcript_path) as f:
            transcript_data = json.load(f)

        text = transcript_data["text"]
        print(f"  [EXTRACT] {key} ({len(text)} chars)...", end=" ", flush=True)

        try:
            result = self.extract_from_text(
                text,
                ticker,
                year,
                quarter,
                transcript_meta=transcript_data,
            )
            result["ticker"] = ticker
            result["year"] = year
            result["quarter"] = quarter
            result["extracted_at"] = datetime.now().isoformat()

            with open(claims_path, "w") as f:
                json.dump(result, f, indent=2)

            num_claims = len(result.get("claims", []))
            print(f"-> {num_claims} claims")
            return result

        except Exception as e:
            print(f"-> ERROR: {e}")
            # Do NOT cache errors — allow retry on next run
            return {
                "claims": [], "error": str(e),
                "ticker": ticker, "year": year, "quarter": quarter,
            }


def _split_transcript(text: str, num_chunks: int = 3) -> list[tuple[str, int]]:
    """Split transcript into overlapping chunks for extraction.

    Returns list of (chunk_text, start_offset) tuples.
    Each chunk overlaps by ~500 chars to avoid losing claims at boundaries.
    """
    total = len(text)
    overlap = 500
    chunk_size = (total + (num_chunks - 1) * overlap) // num_chunks

    chunks = []
    for i in range(num_chunks):
        start = max(0, i * (chunk_size - overlap))
        end = min(total, start + chunk_size)

        # Snap to sentence boundaries (look for ". " or "\n")
        if start > 0:
            # Find a good break point near start
            search_start = max(0, start - 200)
            for j in range(start, search_start, -1):
                if text[j] in '.!\n' and j + 1 < total and text[j + 1] in ' \n[':
                    start = j + 1
                    break

        if end < total:
            # Find a good break point near end
            search_end = min(total, end + 200)
            for j in range(end, search_end):
                if text[j] in '.!\n' and j + 1 < total and text[j + 1] in ' \n[':
                    end = j + 1
                    break

        chunks.append((text[start:end], start))

    return chunks
