"""Tests for forced re-extraction bypassing cached claim files."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings
from backend.services.extraction.llm_extractor import ClaimExtractor


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f)


class TestClaimExtractorForceCache:
    def test_extract_uses_cache_when_force_false(self, tmp_path):
        original_data_dir = settings.data_dir
        settings.data_dir = tmp_path / "data"
        try:
            settings.ensure_dirs()
            key = "AAPL_Q1_2025"
            transcript_path = settings.transcripts_dir / f"{key}.json"
            claims_path = settings.claims_dir / f"{key}_claims.json"

            _write_json(
                transcript_path,
                {
                    "ticker": "AAPL",
                    "year": 2025,
                    "quarter": 1,
                    "text": "Revenue was $100 billion.",
                },
            )
            _write_json(claims_path, {"claims": [{"claim_id": "cached_claim"}]})

            extractor = ClaimExtractor(api_key="test_key")
            calls = {"n": 0}

            def fake_extract(*args, **kwargs):
                calls["n"] += 1
                return {"claims": [{"claim_id": "fresh_claim"}]}

            extractor.extract_from_text = fake_extract  # type: ignore[assignment]

            result = extractor.extract_and_cache("AAPL", 2025, 1, force=False)
            assert result["claims"][0]["claim_id"] == "cached_claim"
            assert calls["n"] == 0
        finally:
            settings.data_dir = original_data_dir

    def test_extract_bypasses_cache_when_force_true(self, tmp_path):
        original_data_dir = settings.data_dir
        settings.data_dir = tmp_path / "data"
        try:
            settings.ensure_dirs()
            key = "AAPL_Q1_2025"
            transcript_path = settings.transcripts_dir / f"{key}.json"
            claims_path = settings.claims_dir / f"{key}_claims.json"

            _write_json(
                transcript_path,
                {
                    "ticker": "AAPL",
                    "year": 2025,
                    "quarter": 1,
                    "text": "Revenue was $100 billion.",
                    "source_url": "https://example.com/transcript",
                },
            )
            _write_json(claims_path, {"claims": [{"claim_id": "cached_claim"}]})

            extractor = ClaimExtractor(api_key="test_key")
            calls = {"n": 0}

            def fake_extract(*args, **kwargs):
                calls["n"] += 1
                return {
                    "claims": [{"claim_id": "fresh_claim"}],
                    "transcript_summary": "summary",
                    "total_claims_found": 1,
                }

            extractor.extract_from_text = fake_extract  # type: ignore[assignment]

            result = extractor.extract_and_cache("AAPL", 2025, 1, force=True)
            assert result["claims"][0]["claim_id"] == "fresh_claim"
            assert calls["n"] == 1

            with open(claims_path) as f:
                saved = json.load(f)
            assert saved["claims"][0]["claim_id"] == "fresh_claim"
            assert saved["ticker"] == "AAPL"
            assert saved["year"] == 2025
            assert saved["quarter"] == 1
        finally:
            settings.data_dir = original_data_dir
