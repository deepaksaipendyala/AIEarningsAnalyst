"""Tests for verification gap fixes in the verdict engine."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.verification.verdict_engine import verify_single_claim


def _base_claim(**overrides):
    claim = {
        "claim_id": "claim_test",
        "metric_type": "revenue",
        "claim_type": "absolute",
        "claimed_value": 100.0,
        "claimed_value_raw": "100",
        "unit": "dollars",
        "scale": "ones",
        "period": "Q1 2025",
        "comparison_period": None,
        "gaap_classification": "gaap",
        "is_approximate": False,
        "qualifiers": [],
        "metric_context": "Total",
        "quote_text": "Revenue was 100",
    }
    claim.update(overrides)
    return claim


class TestFullYearSupport:
    def test_full_year_absolute_revenue(self):
        claim = _base_claim(
            metric_type="revenue",
            claim_type="absolute",
            claimed_value=400.0,
            period="FY 2025",
            quote_text="Full-year revenue was 400",
        )
        fmp_data = {
            (2025, 1): {"revenue": 100.0},
            (2025, 2): {"revenue": 100.0},
            (2025, 3): {"revenue": 100.0},
            (2025, 4): {"revenue": 100.0},
        }
        result = verify_single_claim(claim, "TEST", 2025, 4, fmp_data)
        assert result["verdict"] == "verified"
        assert result["actual_value"] == 400.0

    def test_full_year_yoy_growth(self):
        claim = _base_claim(
            metric_type="revenue",
            claim_type="yoy_growth",
            claimed_value=25.0,
            period="FY 2025",
            comparison_period="FY 2024",
            unit="percent",
            scale="ones",
            quote_text="Revenue grew 25% year over year for FY 2025",
        )
        fmp_data = {
            (2025, 1): {"revenue": 125.0},
            (2025, 2): {"revenue": 125.0},
            (2025, 3): {"revenue": 125.0},
            (2025, 4): {"revenue": 125.0},
            (2024, 1): {"revenue": 100.0},
            (2024, 2): {"revenue": 100.0},
            (2024, 3): {"revenue": 100.0},
            (2024, 4): {"revenue": 100.0},
        }
        result = verify_single_claim(claim, "TEST", 2025, 4, fmp_data)
        assert result["verdict"] == "verified"


class TestMultiPeriodSupport:
    def test_ttm_free_cash_flow(self):
        claim = _base_claim(
            metric_type="free_cash_flow",
            claim_type="absolute",
            claimed_value=100.0,
            period="Q3 2025",
            quote_text="Trailing 12-month free cash flow was 100",
        )
        fmp_data = {
            (2025, 3): {"free_cash_flow": 40.0},
            (2025, 2): {"free_cash_flow": 30.0},
            (2025, 1): {"free_cash_flow": 20.0},
            (2024, 4): {"free_cash_flow": 10.0},
        }
        result = verify_single_claim(claim, "TEST", 2025, 3, fmp_data)
        assert result["verdict"] == "verified"
        assert result["actual_value"] == 100.0


class TestSupplementalQoqDiscrepancy:
    def test_yoy_growth_includes_qoq_discrepancy_step(self):
        claim = _base_claim(
            metric_type="revenue",
            claim_type="yoy_growth",
            claimed_value=10.0,
            unit="percent",
            period="Q3 2025",
            comparison_period="Q3 2024",
            quote_text="Revenue grew 10% year over year.",
        )
        fmp_data = {
            (2025, 3): {"revenue": 110.0},
            (2025, 2): {"revenue": 100.0},  # for supplemental QoQ reference
            (2024, 3): {"revenue": 100.0},
        }
        result = verify_single_claim(claim, "TEST", 2025, 3, fmp_data)
        assert result["verdict"] == "verified"
        assert any(
            step.get("step") == "Supplemental QoQ discrepancy"
            for step in result.get("computation_steps", [])
        )


class TestMarginBpsSupport:
    def test_margin_change_bps_sequential(self):
        claim = _base_claim(
            metric_type="gross_margin",
            claim_type="margin",
            claimed_value=70.0,
            unit="basis_points",
            period="Q2 2025",
            quote_text="Gross margin expanded 70 basis points sequentially",
        )
        fmp_data = {
            (2025, 2): {"gross_profit": 53.0, "revenue": 100.0},
            (2025, 1): {"gross_profit": 52.3, "revenue": 100.0},
        }
        result = verify_single_claim(claim, "TEST", 2025, 2, fmp_data)
        assert result["verdict"] == "verified"
        assert abs(result["actual_value"] - 70.0) < 0.01


class TestSegmentKeywordFalsePositives:
    def test_research_not_treated_as_google_search_segment(self):
        claim = _base_claim(
            metric_type="research_and_development",
            claim_type="absolute",
            claimed_value=3.5,
            scale="billions",
            period="Q2 2025",
            quote_text="We invested 3.5 billion in research and development.",
            metric_context="Total",
        )
        fmp_data = {
            (2025, 2): {"research_and_development": 3_500_000_000.0},
        }
        result = verify_single_claim(claim, "TEST", 2025, 2, fmp_data)
        assert result["verdict"] == "verified"


class TestOtherMetricRemap:
    def test_remap_cash_and_marketable(self):
        claim = _base_claim(
            metric_type="other",
            claim_type="absolute",
            claimed_value=100.0,
            scale="billions",
            period="Q4 2025",
            quote_text="We ended the quarter with $100 billion in cash and marketable securities.",
        )
        fmp_data = {
            (2025, 4): {"cash_and_marketable_securities": 100_000_000_000.0},
        }
        result = verify_single_claim(claim, "TEST", 2025, 4, fmp_data)
        assert result["verdict"] == "verified"


class TestCapexDefinitionGap:
    def test_capex_claim_below_actual_marked_unverifiable(self):
        claim = _base_claim(
            metric_type="capital_expenditures",
            claim_type="absolute",
            claimed_value=84.0,
            scale="ones",
            period="Q4 2025",
            quote_text="CapEx totaled 84",
        )
        fmp_data = {
            (2025, 4): {"capital_expenditures": 100.0},
        }
        result = verify_single_claim(claim, "TEST", 2025, 4, fmp_data)
        assert result["verdict"] == "unverifiable"
        assert "capex_definition_gap" in result["flags"]


class TestOperatingExpensesBpsMargin:
    def test_deleveraged_bps_detected_for_opex_ratio(self):
        claim = _base_claim(
            metric_type="operating_expenses",
            claim_type="margin",
            claimed_value=46.0,
            unit="basis_points",
            period="Q4 2025",
            comparison_period="Q4 2024",
            quote_text="SG&A expenses deleveraged 46 basis points in the quarter.",
        )
        fmp_data = {
            (2025, 4): {"operating_expenses": 204.6, "revenue": 1000.0},
            (2024, 4): {"operating_expenses": 200.0, "revenue": 1000.0},
        }
        result = verify_single_claim(claim, "TEST", 2025, 4, fmp_data)
        assert result["verdict"] == "verified"
        assert abs(result["actual_value"] - 46.0) < 0.01
