"""Pydantic schemas for claims and verdicts (API responses)."""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class MetricType(str, Enum):
    REVENUE = "revenue"
    NET_INCOME = "net_income"
    EPS_BASIC = "eps_basic"
    EPS_DILUTED = "eps_diluted"
    GROSS_PROFIT = "gross_profit"
    GROSS_MARGIN = "gross_margin"
    OPERATING_INCOME = "operating_income"
    OPERATING_MARGIN = "operating_margin"
    EBITDA = "ebitda"
    FREE_CASH_FLOW = "free_cash_flow"
    OTHER = "other"


class ClaimForm(str, Enum):
    ABSOLUTE = "absolute"
    YOY_GROWTH = "yoy_growth"
    QOQ_GROWTH = "qoq_growth"
    MARGIN = "margin"
    COMPARISON = "comparison"
    GUIDANCE = "guidance"
    OTHER = "other"


class GaapBasis(str, Enum):
    GAAP = "gaap"
    NON_GAAP = "non_gaap"
    UNKNOWN = "unknown"


class VerdictLabel(str, Enum):
    VERIFIED = "verified"
    CLOSE_MATCH = "close_match"
    CONTRADICTED = "mismatch"
    MISLEADING = "misleading"
    UNVERIFIABLE = "unverifiable"


class ClaimResponse(BaseModel):
    claim_uid: str
    quote_text: str
    quote_start_char: Optional[int] = None
    quote_end_char: Optional[int] = None
    speaker_name: Optional[str] = None
    speaker_role: Optional[str] = None
    metric: str
    basis: str = "unknown"
    claim_form: str
    claimed_value: Optional[float] = None
    claimed_value_raw: Optional[str] = None
    claimed_unit: Optional[str] = None
    claimed_scale: Optional[str] = None
    period_fiscal_year: Optional[int] = None
    period_fiscal_quarter: Optional[int] = None
    comparator: Optional[str] = None
    qualifiers: list[str] = Field(default_factory=list)
    extraction_confidence: Optional[float] = None

    class Config:
        from_attributes = True


class VerdictResponse(BaseModel):
    label: str
    actual_value: Optional[float] = None
    delta_absolute: Optional[float] = None
    delta_relative_pct: Optional[float] = None
    tolerance_value: Optional[float] = None
    tolerance_type: Optional[str] = None
    computation_steps: Optional[list] = None
    financial_facts_used: Optional[list] = None
    misleading_flags: list[str] = Field(default_factory=list)
    misleading_reasons: list[str] = Field(default_factory=list)
    evidence_source: Optional[str] = None
    explanation: Optional[str] = None

    class Config:
        from_attributes = True


class ClaimWithVerdict(BaseModel):
    claim: ClaimResponse
    verdict: Optional[VerdictResponse] = None


class CompanyResponse(BaseModel):
    ticker: str
    name: str
    sector: Optional[str] = None
    quarters_available: int = 0
    total_claims: int = 0
    verified_count: int = 0
    mismatch_count: int = 0
    misleading_count: int = 0
    unverifiable_count: int = 0


class DashboardSummary(BaseModel):
    total_claims: int = 0
    verified: int = 0
    close_match: int = 0
    mismatch: int = 0
    misleading: int = 0
    unverifiable: int = 0
    companies: list[CompanyResponse] = Field(default_factory=list)
