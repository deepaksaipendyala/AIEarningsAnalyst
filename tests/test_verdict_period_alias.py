"""Tests for calendar alias fallback guards in verdict engine."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.verification.verdict_engine import (
    _should_use_calendar_alias,
    lookup_value,
)


class TestShouldUseCalendarAlias:
    def test_false_when_period_matches_transcript(self):
        claim = {
            "period": "Q1 2025",
            "quote_text": "Revenue for the December quarter was strong.",
        }
        assert _should_use_calendar_alias(claim, 2025, 1) is False

    def test_true_for_explicit_month_quarter_on_different_period(self):
        claim = {
            "period": "Q4 2024",
            "quote_text": "Revenue for the December quarter was strong.",
        }
        assert _should_use_calendar_alias(claim, 2025, 1) is True

    def test_false_without_month_quarter_keyword(self):
        claim = {
            "period": "Q4 2024",
            "quote_text": "Revenue grew strongly year over year.",
        }
        assert _should_use_calendar_alias(claim, 2025, 1) is False


class TestLookupValueWithAlias:
    def test_no_alias_lookup_by_default(self):
        fmp_data = {
            (2025, 1): {"revenue": 100.0},
            "_calendar_aliases": {(2024, 4): (2025, 1)},
        }
        assert lookup_value(fmp_data, "revenue", 2024, 4, use_calendar_alias=False) is None

    def test_alias_lookup_when_enabled(self):
        fmp_data = {
            (2025, 1): {"revenue": 100.0},
            "_calendar_aliases": {(2024, 4): (2025, 1)},
        }
        assert lookup_value(fmp_data, "revenue", 2024, 4, use_calendar_alias=True) == (
            100.0, "fmp_calendar_alias"
        )
