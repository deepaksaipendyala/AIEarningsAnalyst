"""Tests for verification-time helpers in pipeline orchestration."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings
from backend.services.pipeline import (
    _claim_with_period_shift,
    _derive_transcript_period_override,
    _downgrade_conflicting_mismatches,
)


class TestTranscriptPeriodOverride:
    def test_reads_fiscal_period_from_fool_url(self, tmp_path):
        original_data_dir = settings.data_dir
        settings.data_dir = tmp_path
        try:
            transcripts_dir = settings.transcripts_dir
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "ticker": "NVDA",
                "year": 2025,
                "quarter": 3,
                "source_url": "https://www.fool.com/earnings/call-transcripts/2025/11/19/nvidia-nvda-q3-2026-earnings-call-transcript/",
            }
            path = transcripts_dir / "NVDA_Q3_2025.json"
            with open(path, "w") as f:
                json.dump(payload, f)

            assert _derive_transcript_period_override("NVDA", 2025, 3) == (2026, 3)
        finally:
            settings.data_dir = original_data_dir

    def test_no_override_when_url_missing(self, tmp_path):
        original_data_dir = settings.data_dir
        settings.data_dir = tmp_path
        try:
            transcripts_dir = settings.transcripts_dir
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            path = transcripts_dir / "AAPL_Q2_2025.json"
            with open(path, "w") as f:
                json.dump({"ticker": "AAPL"}, f)

            assert _derive_transcript_period_override("AAPL", 2025, 2) == (2025, 2)
        finally:
            settings.data_dir = original_data_dir


class TestPeriodShift:
    def test_shifts_period_and_comparison_period(self):
        claim = {
            "claim_id": "claim_1",
            "period": "Q3 2025",
            "comparison_period": "Q3 2024",
        }
        shifted = _claim_with_period_shift(claim, 1)
        assert shifted["period"] == "Q3 2026"
        assert shifted["comparison_period"] == "Q3 2025"


class TestConflictDowngrade:
    def test_conflicting_claim_mismatch_becomes_unverifiable(self):
        verdicts = [
            {
                "claim": {
                    "claim_id": "claim_good",
                    "claim_type": "absolute",
                    "metric_type": "revenue",
                    "metric_context": "Total",
                    "period": "Q1 2025",
                },
                "verification": {"verdict": "verified", "difference_pct": 0.1, "flags": []},
            },
            {
                "claim": {
                    "claim_id": "claim_bad",
                    "claim_type": "absolute",
                    "metric_type": "revenue",
                    "metric_context": "Total",
                    "period": "Q1 2025",
                },
                "verification": {"verdict": "mismatch", "difference_pct": 6.4, "flags": []},
            },
        ]

        _downgrade_conflicting_mismatches(verdicts)

        bad = verdicts[1]["verification"]
        assert bad["verdict"] == "unverifiable"
        assert "conflicting_transcript_claim" in bad["flags"]
