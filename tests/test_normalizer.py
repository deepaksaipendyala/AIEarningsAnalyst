"""Tests for value normalization and period parsing."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.extraction.normalizer import (
    normalize_claimed_value, parse_period, detect_scale_from_text, extract_numeric_from_text
)


class TestNormalizeClaimed:
    def test_dollars_billions(self):
        assert normalize_claimed_value(50.3, "dollars", "billions") == 50_300_000_000

    def test_dollars_millions(self):
        assert normalize_claimed_value(125.5, "dollars", "millions") == 125_500_000

    def test_dollars_no_scale(self):
        assert normalize_claimed_value(100, "dollars", None) == 100

    def test_percent_unchanged(self):
        assert normalize_claimed_value(15.0, "percent", None) == 15.0

    def test_basis_points_to_pct(self):
        assert normalize_claimed_value(200, "basis_points", None) == 2.0

    def test_per_share_unchanged(self):
        assert normalize_claimed_value(1.42, "per_share", None) == 1.42

    def test_dollars_thousands(self):
        assert normalize_claimed_value(500, "dollars", "thousands") == 500_000


class TestParsePeriod:
    def test_q3_2024(self):
        assert parse_period("Q3 2024") == (2024, 3)

    def test_q3_fy2024(self):
        assert parse_period("Q3 FY2024") == (2024, 3)

    def test_3q_2024(self):
        assert parse_period("3Q 2024") == (2024, 3)

    def test_3q24(self):
        assert parse_period("3Q24") == (2024, 3)

    def test_fy_2024(self):
        assert parse_period("FY 2024") == (2024, 0)

    def test_fy2024(self):
        assert parse_period("FY2024") == (2024, 0)

    def test_invalid(self):
        assert parse_period("last quarter") is None

    def test_empty(self):
        assert parse_period("") is None

    def test_none(self):
        assert parse_period(None) is None

    def test_lowercase(self):
        assert parse_period("q1 2025") == (2025, 1)


class TestDetectScale:
    def test_billion(self):
        assert detect_scale_from_text("$50.3 billion") == "billions"

    def test_million(self):
        assert detect_scale_from_text("$125 million") == "millions"

    def test_no_scale(self):
        assert detect_scale_from_text("$1.42") is None


class TestExtractNumeric:
    def test_dollar_amount(self):
        assert extract_numeric_from_text("$50.3 billion") == 50.3

    def test_with_comma(self):
        assert extract_numeric_from_text("$1,500 million") == 1500.0

    def test_plain_number(self):
        assert extract_numeric_from_text("15 percent") == 15.0
