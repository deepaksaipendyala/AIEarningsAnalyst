"""Tests for FMP statement period key extraction."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.ingestion.fmp_client import (
    _extract_calendar_year_quarter_from_date,
    _extract_statement_period_keys,
)


class TestCalendarKeyFromDate:
    def test_extract_q1(self):
        stmt = {"date": "2025-01-31"}
        assert _extract_calendar_year_quarter_from_date(stmt) == (2025, 1)

    def test_extract_q2(self):
        stmt = {"date": "2025-04-30"}
        assert _extract_calendar_year_quarter_from_date(stmt) == (2025, 2)

    def test_extract_q4(self):
        stmt = {"date": "2024-12-28"}
        assert _extract_calendar_year_quarter_from_date(stmt) == (2024, 4)

    def test_invalid_date(self):
        stmt = {"date": "not-a-date"}
        assert _extract_calendar_year_quarter_from_date(stmt) is None

    def test_missing_date(self):
        stmt = {}
        assert _extract_calendar_year_quarter_from_date(stmt) is None


class TestStatementPeriodKeys:
    def test_returns_fiscal_and_calendar_alias_for_non_calendar_fy(self):
        # Walmart fiscal Q1 FY2026 ends in calendar Q2 2025.
        stmt = {
            "fiscalYear": 2026,
            "period": "Q1",
            "date": "2025-04-30",
        }
        assert _extract_statement_period_keys(stmt) == [(2026, 1), (2025, 2)]

    def test_returns_fiscal_and_calendar_alias_for_apple(self):
        # Apple fiscal Q1 FY2025 ends in calendar Q4 2024.
        stmt = {
            "fiscalYear": 2025,
            "period": "Q1",
            "date": "2024-12-28",
        }
        assert _extract_statement_period_keys(stmt) == [(2025, 1), (2024, 4)]

    def test_calendar_only_payload(self):
        stmt = {
            "calendarYear": 2025,
            "period": "Q3",
        }
        assert _extract_statement_period_keys(stmt) == [(2025, 3)]

    def test_invalid_period(self):
        stmt = {
            "fiscalYear": 2025,
            "period": "FY",
            "date": "2025-09-30",
        }
        assert _extract_statement_period_keys(stmt) == [(2025, 3)]
