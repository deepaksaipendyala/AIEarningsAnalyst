"""Tests for period resolution logic."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.verification.period_resolver import resolve_periods


class TestPeriodResolver:
    def test_full_year_no_error(self):
        claim = {
            "period": "FY 2025",
            "claim_type": "absolute",
        }
        result = resolve_periods(claim, transcript_year=2025, transcript_quarter=4)
        assert result["target"] == (2025, 0)
        assert "error" not in result

    def test_full_year_yoy_baseline(self):
        claim = {
            "period": "FY 2025",
            "claim_type": "yoy_growth",
        }
        result = resolve_periods(claim, transcript_year=2025, transcript_quarter=4)
        assert result["target"] == (2025, 0)
        assert result["baseline"] == (2024, 0)

    def test_full_year_qoq_has_no_baseline(self):
        claim = {
            "period": "FY 2025",
            "claim_type": "qoq_growth",
        }
        result = resolve_periods(claim, transcript_year=2025, transcript_quarter=4)
        assert result["target"] == (2025, 0)
        assert "baseline" not in result
