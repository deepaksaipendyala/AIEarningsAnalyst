"""Finnhub API client for fetching earnings call transcripts."""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from backend.config import settings


class FinnhubClient:
    """Client for Finnhub earnings call transcript API."""

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.finnhub_api_key
        self.client = httpx.Client(timeout=30)

    def _get(self, endpoint: str, params: dict = None) -> dict:
        params = params or {}
        params["token"] = self.api_key
        resp = self.client.get(f"{self.BASE_URL}{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    def list_transcripts(self, ticker: str) -> list[dict]:
        """List available earnings call transcripts for a ticker.

        Returns list of {id, title, time, year, quarter}.
        """
        data = self._get("/stock/transcripts/list", {"symbol": ticker})
        return data.get("transcripts", [])

    def get_transcript(self, transcript_id: str) -> dict:
        """Fetch a single transcript by its Finnhub ID.

        Returns {symbol, transcript: [{name, speech: [...], session}], participant: [...]}.
        """
        return self._get("/stock/transcripts", {"id": transcript_id})

    def fetch_and_cache_transcript(self, ticker: str, year: int, quarter: int) -> Optional[dict]:
        """Fetch transcript for a specific quarter, with caching."""
        settings.ensure_dirs()
        key = f"{ticker}_Q{quarter}_{year}"
        cache_path = settings.transcripts_dir / f"{key}.json"

        if cache_path.exists():
            with open(cache_path) as f:
                return json.load(f)

        # Find matching transcript from list
        transcripts = self.list_transcripts(ticker)
        match = None
        for t in transcripts:
            if t.get("year") == year and t.get("quarter") == quarter:
                match = t
                break

        if not match:
            print(f"  [MISS] {key} - no transcript available")
            return None

        time.sleep(0.5)  # Rate limiting

        raw = self.get_transcript(match["id"])
        canonical_text, speaker_sections = build_canonical_text(raw)

        data = {
            "ticker": ticker,
            "year": year,
            "quarter": quarter,
            "finnhub_id": match["id"],
            "title": match.get("title", ""),
            "call_date": match.get("time", ""),
            "text": canonical_text,
            "speaker_sections": speaker_sections,
            "participants": raw.get("participant", []),
            "text_hash": hashlib.sha256(canonical_text.encode()).hexdigest(),
            "fetched_at": datetime.now().isoformat(),
            "source": "finnhub",
        }

        with open(cache_path, "w") as f:
            json.dump(data, f, indent=2)

        print(f"  [OK] {key} ({len(canonical_text)} chars)")
        return data


def build_canonical_text(transcript_data: dict) -> tuple[str, list[dict]]:
    """Build a single text string from Finnhub transcript with character offset tracking.

    Returns (full_text, speaker_sections) where each section tracks
    the speaker, session, character offsets, and text.
    """
    full_text = ""
    sections = []

    for segment in transcript_data.get("transcript", []):
        speaker = segment.get("name", "Unknown")
        session = segment.get("session", "unknown")
        speeches = segment.get("speech", [])

        for speech_block in speeches:
            if not speech_block or not speech_block.strip():
                continue
            start = len(full_text)
            line = f"[{speaker} - {session}]: {speech_block}\n\n"
            full_text += line
            sections.append({
                "name": speaker,
                "session": session,
                "start_char": start,
                "end_char": start + len(line),
                "text": line,
            })

    return full_text, sections


def get_speaker_role(name: str, participants: list[dict]) -> str:
    """Map speaker name to role using participant metadata."""
    name_lower = name.lower()
    for p in participants:
        if p.get("name", "").lower() == name_lower:
            desc = p.get("description", "").lower()
            if "chief executive" in desc or "ceo" in desc:
                return "ceo"
            if "chief financial" in desc or "cfo" in desc:
                return "cfo"
            if any(kw in desc for kw in ["president", "vice president", "svp", "evp", "director", "head"]):
                return "management"
            if "analyst" in desc:
                return "analyst"
            return "management"
    return "unknown"
