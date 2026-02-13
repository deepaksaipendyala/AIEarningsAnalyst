"""Tests for SEC Company Facts parser."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.ingestion.sec_client import _extract_metrics_index


def _facts(units_by_tag):
    return {
        "facts": {
            "us-gaap": {
                tag: {"units": units}
                for tag, units in units_by_tag.items()
            }
        }
    }


class TestSecParser:
    def test_prefers_quarter_duration_over_ytd(self):
        facts = _facts({
            "Revenues": {
                "USD": [
                    {
                        "fy": 2025,
                        "fp": "Q2",
                        "start": "2025-01-01",
                        "end": "2025-06-30",
                        "val": 210.0,
                        "form": "10-Q",
                        "filed": "2025-07-30",
                    },
                    {
                        "fy": 2025,
                        "fp": "Q2",
                        "start": "2025-04-01",
                        "end": "2025-06-30",
                        "val": 110.0,
                        "form": "10-Q",
                        "filed": "2025-07-30",
                    },
                ]
            }
        })
        indexed = _extract_metrics_index(facts)
        assert indexed[(2025, 2)]["revenue"] == 110.0

    def test_capex_sign_normalization(self):
        facts = _facts({
            "PaymentsToAcquirePropertyPlantAndEquipment": {
                "USD": [
                    {
                        "fy": 2025,
                        "fp": "Q3",
                        "start": "2025-07-01",
                        "end": "2025-09-30",
                        "val": -12.0,
                        "form": "10-Q",
                        "filed": "2025-10-31",
                    }
                ]
            }
        })
        indexed = _extract_metrics_index(facts)
        assert indexed[(2025, 3)]["capital_expenditure"] == 12.0

    def test_derived_cash_and_net_cash(self):
        facts = _facts({
            "CashAndCashEquivalentsAtCarryingValue": {
                "USD": [{"fy": 2025, "fp": "Q4", "end": "2025-12-31", "val": 80.0, "form": "10-K", "filed": "2026-02-01"}]
            },
            "ShortTermInvestments": {
                "USD": [{"fy": 2025, "fp": "Q4", "end": "2025-12-31", "val": 20.0, "form": "10-K", "filed": "2026-02-01"}]
            },
            "DebtLongtermAndShorttermCombinedAmount": {
                "USD": [{"fy": 2025, "fp": "Q4", "end": "2025-12-31", "val": 55.0, "form": "10-K", "filed": "2026-02-01"}]
            },
        })
        indexed = _extract_metrics_index(facts)
        row = indexed[(2025, 4)]
        assert row["cash_and_marketable_securities"] == 100.0
        assert row["total_debt"] == 55.0
        assert row["net_cash"] == 45.0

    def test_prefers_latest_end_date_for_instant_metrics(self):
        facts = _facts({
            "LongTermDebt": {
                "USD": [
                    {
                        "fy": 2025,
                        "fp": "Q2",
                        "end": "2024-12-31",
                        "val": 90.0,
                        "form": "10-Q",
                        "filed": "2025-05-01",
                    },
                    {
                        "fy": 2025,
                        "fp": "Q2",
                        "end": "2025-03-31",
                        "val": 95.0,
                        "form": "10-Q",
                        "filed": "2025-05-01",
                    },
                ]
            },
            "CommercialPaper": {
                "USD": [
                    {
                        "fy": 2025,
                        "fp": "Q2",
                        "end": "2025-03-31",
                        "val": 5.0,
                        "form": "10-Q",
                        "filed": "2025-05-01",
                    }
                ]
            },
        })
        indexed = _extract_metrics_index(facts)
        row = indexed[(2025, 2)]
        assert row["total_debt"] == 100.0

    def test_includes_long_term_marketable_securities(self):
        facts = _facts({
            "CashAndCashEquivalentsAtCarryingValue": {
                "USD": [
                    {"fy": 2025, "fp": "Q3", "end": "2025-09-30", "val": 30.0, "form": "10-Q", "filed": "2025-10-31"}
                ]
            },
            "MarketableSecuritiesCurrent": {
                "USD": [
                    {"fy": 2025, "fp": "Q3", "end": "2025-09-30", "val": 20.0, "form": "10-Q", "filed": "2025-10-31"}
                ]
            },
            "MarketableSecuritiesNoncurrent": {
                "USD": [
                    {"fy": 2025, "fp": "Q3", "end": "2025-09-30", "val": 40.0, "form": "10-Q", "filed": "2025-10-31"}
                ]
            },
            "LongTermDebt": {
                "USD": [
                    {"fy": 2025, "fp": "Q3", "end": "2025-09-30", "val": 70.0, "form": "10-Q", "filed": "2025-10-31"}
                ]
            },
            "CommercialPaper": {
                "USD": [
                    {"fy": 2025, "fp": "Q3", "end": "2025-09-30", "val": 10.0, "form": "10-Q", "filed": "2025-10-31"}
                ]
            },
        })
        indexed = _extract_metrics_index(facts)
        row = indexed[(2025, 3)]
        assert row["cash_and_marketable_securities"] == 90.0
        assert row["total_debt"] == 80.0
        assert row["net_cash"] == 10.0

    def test_derives_q4_from_fy_minus_q1_q2_q3(self):
        facts = _facts({
            "Revenues": {
                "USD": [
                    {"fy": 2025, "fp": "Q1", "start": "2024-02-01", "end": "2024-04-30", "val": 100.0, "form": "10-Q", "filed": "2024-06-01"},
                    {"fy": 2025, "fp": "Q2", "start": "2024-05-01", "end": "2024-07-31", "val": 110.0, "form": "10-Q", "filed": "2024-09-01"},
                    {"fy": 2025, "fp": "Q3", "start": "2024-08-01", "end": "2024-10-31", "val": 120.0, "form": "10-Q", "filed": "2024-12-01"},
                    {"fy": 2025, "fp": "FY", "start": "2024-02-01", "end": "2025-01-31", "val": 460.0, "form": "10-K", "filed": "2025-03-01"},
                ]
            }
        })
        indexed = _extract_metrics_index(facts)
        assert indexed[(2025, 4)]["revenue"] == 130.0
