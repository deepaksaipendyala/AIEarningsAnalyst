"""Tests for the deterministic computation engine."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.verification.compute import (
    compute_yoy_growth, compute_qoq_growth, compute_margin,
    verify_absolute, verify_growth, verify_margin
)


class TestComputeGrowth:
    def test_positive_yoy(self):
        result = compute_yoy_growth(115, 100)
        assert abs(result - 15.0) < 0.01

    def test_negative_yoy(self):
        result = compute_yoy_growth(85, 100)
        assert abs(result - (-15.0)) < 0.01

    def test_zero_prior(self):
        assert compute_yoy_growth(100, 0) is None

    def test_large_growth(self):
        result = compute_yoy_growth(500, 100)
        assert abs(result - 400.0) < 0.01

    def test_negative_values(self):
        # Loss narrowing: -50 to -30 = improvement
        result = compute_yoy_growth(-30, -50)
        assert abs(result - 40.0) < 0.01


class TestComputeMargin:
    def test_gross_margin(self):
        result = compute_margin(45_000_000_000, 100_000_000_000)
        assert abs(result - 45.0) < 0.01

    def test_zero_denominator(self):
        assert compute_margin(100, 0) is None

    def test_operating_margin(self):
        result = compute_margin(25_000, 100_000)
        assert abs(result - 25.0) < 0.01


class TestVerifyAbsolute:
    def test_exact_match(self):
        result = verify_absolute(100.0, 100.0)
        assert result["difference"] == 0
        assert result["difference_pct"] == 0

    def test_close_match(self):
        result = verify_absolute(100.5, 100.0)
        assert abs(result["difference_pct"] - 0.5) < 0.01

    def test_large_diff(self):
        result = verify_absolute(120.0, 100.0)
        assert abs(result["difference_pct"] - 20.0) < 0.01


class TestVerifyGrowth:
    def test_accurate_growth(self):
        # 15% growth claimed, actual 115 vs 100 = 15%
        result = verify_growth(15.0, 115, 100)
        assert abs(result["difference_pp"]) < 0.01

    def test_inaccurate_growth(self):
        # 15% claimed but actual is 10%
        result = verify_growth(15.0, 110, 100)
        assert abs(result["difference_pp"] - 5.0) < 0.01

    def test_zero_prior(self):
        result = verify_growth(15.0, 100, 0)
        assert "error" in result


class TestVerifyMargin:
    def test_accurate_margin(self):
        result = verify_margin(45.0, 45_000, 100_000)
        assert abs(result["difference_pp"]) < 0.01

    def test_inaccurate_margin(self):
        result = verify_margin(50.0, 45_000, 100_000)
        assert abs(result["difference_pp"] - 5.0) < 0.01
