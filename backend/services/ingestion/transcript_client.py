"""Unified transcript client with multiple source fallback.

Tries earningscall library first, then mlq.ai local files, then Motley Fool scraper, then FMP API.
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Optional

from backend.config import settings


def fetch_transcript(ticker: str, year: int, quarter: int) -> Optional[dict]:
    """Fetch transcript from best available source, with caching."""
    settings.ensure_dirs()
    key = f"{ticker}_Q{quarter}_{year}"
    cache_path = settings.transcripts_dir / f"{key}.json"

    if cache_path.exists():
        print(f"  [CACHE] {key}")
        with open(cache_path) as f:
            return json.load(f)

    # Try earningscall library first
    data = _try_earningscall(ticker, year, quarter)

    # Fall back to mlq.ai local files
    if data is None:
        data = _try_mlq(ticker, year, quarter)

    # Fall back to Motley Fool scraper (free, no API key)
    if data is None:
        data = _try_fool(ticker, year, quarter)

    # Fall back to FMP
    if data is None:
        data = _try_fmp(ticker, year, quarter)

    if data is None:
        print(f"  [MISS] {key} - no transcript from any source")
        return None

    # Cache result
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"  [OK] {key} ({len(data['text'])} chars, source={data['source']})")
    return data


def _try_earningscall(ticker: str, year: int, quarter: int) -> Optional[dict]:
    """Try fetching from earningscall library."""
    key = f"{ticker}_Q{quarter}_{year}"
    try:
        import earningscall as ec
        # Set API key if available
        ec_key = settings.earningscall_api_key
        if ec_key:
            ec.api_key = ec_key
        from earningscall import get_company
        company = get_company(ticker)
        if company is None:
            return None

        transcript = company.get_transcript(year=year, quarter=quarter, level=2)
        if transcript is None:
            print(f"  [MISS] {key} - earningscall has no data")
            return None

        # Build canonical text with speaker sections
        raw_text, speaker_sections = _build_text_from_earningscall(transcript)
        if not raw_text or len(raw_text) < 100:
            print(f"  [MISS] {key} - earningscall transcript too short")
            return None

        return {
            "ticker": ticker,
            "year": year,
            "quarter": quarter,
            "title": f"{ticker} Q{quarter} {year} Earnings Call",
            "call_date": "",
            "text": raw_text,
            "speaker_sections": speaker_sections,
            "text_hash": hashlib.sha256(raw_text.encode()).hexdigest(),
            "fetched_at": datetime.now().isoformat(),
            "source": "earningscall",
        }

    except ImportError:
        return None
    except Exception as e:
        print(f"  [WARN] {key} - earningscall error: {e}")
        return None


def _build_text_from_earningscall(transcript) -> tuple[str, list[dict]]:
    """Build canonical text from earningscall transcript object (level 2)."""
    full_text = ""
    sections = []

    if not hasattr(transcript, 'speakers') or not transcript.speakers:
        # Level 1 fallback — just plain text
        text = getattr(transcript, 'text', '') or ''
        return text, _parse_speaker_text(text)

    for speaker in transcript.speakers:
        name = "Unknown"
        if hasattr(speaker, 'speaker_info') and speaker.speaker_info:
            info = speaker.speaker_info
            name = getattr(info, 'name', None) or f"Speaker {getattr(speaker, 'speaker', '?')}"
        elif hasattr(speaker, 'speaker'):
            name = f"Speaker {speaker.speaker}"

        speech = getattr(speaker, 'text', '') or ''
        if not speech.strip():
            continue

        start = len(full_text)
        line = f"[{name}]: {speech}\n\n"
        full_text += line
        sections.append({
            "name": name,
            "start_char": start,
            "end_char": start + len(line),
            "text": line,
        })

    return full_text, sections


def _try_mlq(ticker: str, year: int, quarter: int) -> Optional[dict]:
    """Try loading transcript from mlq.ai local files (pre-scraped)."""
    key = f"{ticker}_Q{quarter}_{year}"
    mlq_dir = settings.transcripts_dir / "manual_transcripts"
    mlq_path = mlq_dir / f"{key}.md"

    if not mlq_path.exists():
        return None

    try:
        with open(mlq_path) as f:
            html = f.read()

        if not html or len(html) < 200:
            return None

        raw_text = _html_to_text(html)
        if not raw_text or len(raw_text) < 100:
            print(f"  [MISS] {key} - mlq transcript too short after parsing")
            return None

        print(f"  [MLQ] {key} ({len(raw_text)} chars)")
        return {
            "ticker": ticker,
            "year": year,
            "quarter": quarter,
            "title": f"{ticker} Q{quarter} {year} Earnings Call",
            "call_date": "",
            "text": raw_text,
            "speaker_sections": _parse_speaker_text(raw_text),
            "text_hash": hashlib.sha256(raw_text.encode()).hexdigest(),
            "fetched_at": datetime.now().isoformat(),
            "source": "mlq.ai",
        }

    except Exception as e:
        print(f"  [WARN] {key} - mlq error: {e}")
        return None


def _html_to_text(html: str) -> str:
    """Convert mlq.ai transcript HTML to plain text with speaker labels."""
    # Replace <br> and <br/> with newlines
    text = re.sub(r'<br\s*/?>', '\n', html)
    # Extract speaker names from <strong>Name</strong>: pattern
    text = re.sub(r'<strong>([^<]+)</strong>\s*:', r'\n\1:', text)
    # Remove all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode HTML entities
    text = text.replace('&amp;', '&').replace('&#x27;', "'").replace('&quot;', '"')
    text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
    text = text.replace('&#39;', "'").replace('&mdash;', '\u2014').replace('&ndash;', '\u2013')
    # Clean up whitespace: collapse multiple blank lines, strip leading/trailing
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove "Earnings Call Transcript" header if present
    text = re.sub(r'^Earnings Call Transcript\s*\n*', '', text.strip())
    return text.strip()


def _try_fool(ticker: str, year: int, quarter: int) -> Optional[dict]:
    """Try fetching transcript from Motley Fool (free, no API key)."""
    try:
        from backend.services.ingestion.fool_scraper import fetch_fool_transcript
        return fetch_fool_transcript(ticker, year, quarter)
    except Exception as e:
        key = f"{ticker}_Q{quarter}_{year}"
        print(f"  [WARN] {key} - Motley Fool error: {e}")
        return None


def _try_fmp(ticker: str, year: int, quarter: int) -> Optional[dict]:
    """Try fetching transcript from FMP API."""
    key = f"{ticker}_Q{quarter}_{year}"
    try:
        from backend.services.ingestion.fmp_client import FMPClient
        fmp = FMPClient()
        result = fmp.get_transcript(ticker, year, quarter)

        if not result:
            return None

        transcript_item = result[0] if isinstance(result, list) and result else result
        raw_text = transcript_item.get("content", "")

        if not raw_text:
            return None

        return {
            "ticker": ticker,
            "year": year,
            "quarter": quarter,
            "title": f"{ticker} Q{quarter} {year} Earnings Call",
            "call_date": transcript_item.get("date", ""),
            "text": raw_text,
            "speaker_sections": _parse_speaker_text(raw_text),
            "text_hash": hashlib.sha256(raw_text.encode()).hexdigest(),
            "fetched_at": datetime.now().isoformat(),
            "source": "fmp",
        }

    except Exception as e:
        print(f"  [WARN] {key} - FMP transcript error: {e}")
        return None


def _parse_speaker_text(raw_text: str) -> list[dict]:
    """Parse speaker-labeled text into sections with character offsets."""
    sections = []
    # Match lines like "Name - Title:" or "Name:" at paragraph start
    speaker_pattern = re.compile(
        r'^([A-Z][A-Za-z\'. -]+(?:\s*-\s*[A-Za-z\s,&]+)?)\s*:\s*',
        re.MULTILINE,
    )

    matches = list(speaker_pattern.finditer(raw_text))
    if not matches:
        return [{
            "name": "Unknown",
            "start_char": 0,
            "end_char": len(raw_text),
            "text": raw_text,
        }]

    for i, m in enumerate(matches):
        name_raw = m.group(1).strip()
        name = name_raw.split(" - ")[0].strip() if " - " in name_raw else name_raw
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)

        sections.append({
            "name": name,
            "start_char": start,
            "end_char": end,
            "text": raw_text[start:end],
        })

    return sections
