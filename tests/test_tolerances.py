"""Tests for the tolerance matrix."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.verification.tolerances import (
    get_tolerance, get_growth_tolerance, is_approximate, EPS_ABSOLUTE_TOLERANCE
)


class TestGetTolerance:
    def test_revenue_tight(self):
        tol = get_tolerance("revenue", is_approx=False)
        assert tol["tight"] == 0.005  # 0.5%

    def test_revenue_approx(self):
        tol = get_tolerance("revenue", is_approx=True)
        assert tol["tight"] == 0.05  # approx threshold

    def test_eps_tight(self):
        tol = get_tolerance("eps_diluted", is_approx=False)
        assert tol["tight"] == 0.005

    def test_margin_tight(self):
        tol = get_tolerance("gross_margin", is_approx=False)
        assert tol["tight"] == 0.005  # 0.5 pp

    def test_unknown_metric(self):
        tol = get_tolerance("unknown_metric", is_approx=False)
        assert tol["tight"] == 0.02  # Falls back to "other"


class TestGrowthTolerance:
    def test_normal(self):
        tol = get_growth_tolerance(is_approx=False)
        assert tol["tight"] == 1.0  # 1.0 pp

    def test_approximate(self):
        tol = get_growth_tolerance(is_approx=True)
        assert tol["tight"] == 2.0  # Doubled


class TestIsApproximate:
    def test_about(self):
        assert is_approximate(["about"]) is True

    def test_approximately(self):
        assert is_approximate(["approximately"]) is True

    def test_record(self):
        assert is_approximate(["record"]) is False

    def test_empty(self):
        assert is_approximate([]) is False

    def test_mixed(self):
        assert is_approximate(["record", "roughly"]) is True


class TestEPSTolerance:
    def test_value(self):
        assert EPS_ABSOLUTE_TOLERANCE == 0.005
