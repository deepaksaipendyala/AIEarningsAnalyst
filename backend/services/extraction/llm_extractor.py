"""LLM-based claim extraction from earnings call transcripts via OpenRouter."""

import json
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from backend.config import settings
from backend.schemas.extraction import EXTRACTION_SCHEMA, SYSTEM_PROMPT
from backend.services.extraction.validator import validate_claims


class ClaimExtractor:
    """Extract quantitative claims from transcripts using OpenRouter (OpenAI-compatible)."""

    def __init__(self, api_key: str = None):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key or settings.openrouter_api_key,
        )
        self.model = settings.extraction_model

    def _call_extraction(self, transcript_text: str, ticker: str, year: int,
                         quarter: int, chunk_label: str = "") -> list[dict]:
        """Make a single extraction API call and return raw claims list."""
        label = f" ({chunk_label})" if chunk_label else ""
        user_prompt = f"""Here is the earnings call transcript for {ticker}, Q{quarter} {year}{label}.

The FULL transcript text is provided below. Character offsets start at 0 for the first character of this text.
When providing quote_start_char and quote_end_char, ensure that text[start:end] exactly matches quote_text.

<transcript>
{transcript_text}
</transcript>

Extract all quantitative financial claims from this transcript. Be thorough and precise.
Remember: quote_text must exactly match the substring at the given character offsets.
Use short, focused quotes — just the sentence containing the numeric claim."""

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

    def extract_from_text(self, transcript_text: str, ticker: str, year: int, quarter: int) -> dict:
        """Extract claims from full transcript, splitting into chunks if needed."""
        user_prompt = f"""Here is the earnings call transcript for {ticker}, Q{quarter} {year}.

The transcript text is provided below. Character offsets start at 0 for the first character.
When providing quote_start_char and quote_end_char, ensure that transcript_text[start:end] exactly matches quote_text.

<transcript>
{transcript_text}
</transcript>

Extract all quantitative financial claims from this transcript. Be thorough and precise.
Remember: quote_text must exactly match the substring at the given character offsets.
Use short, focused quotes — just the sentence containing the numeric claim."""

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
                chunk_label=f"part {i+1}/{len(chunks)}"
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

    def extract_and_cache(self, ticker: str, year: int, quarter: int) -> dict:
        """Extract claims from cached transcript, with caching."""
        settings.ensure_dirs()
        key = f"{ticker}_Q{quarter}_{year}"
        claims_path = settings.claims_dir / f"{key}_claims.json"

        if claims_path.exists():
            with open(claims_path) as f:
                cached = json.load(f)
            # Skip cache if it contains an error from a previous failed run
            if "error" not in cached:
                print(f"  [CACHE] {key}")
                return cached
            else:
                print(f"  [RETRY] {key} - previous run had error, re-extracting")

        transcript_path = settings.transcripts_dir / f"{key}.json"
        if not transcript_path.exists():
            print(f"  [SKIP] {key} - no transcript")
            return {"claims": [], "error": "No transcript found"}

        with open(transcript_path) as f:
            transcript_data = json.load(f)

        text = transcript_data["text"]
        print(f"  [EXTRACT] {key} ({len(text)} chars)...", end=" ", flush=True)

        try:
            result = self.extract_from_text(text, ticker, year, quarter)
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
