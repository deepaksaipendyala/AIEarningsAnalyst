"""Financial period data SQLAlchemy model."""

from sqlalchemy import Column, Integer, BigInteger, Float, String, Date, DateTime, ForeignKey, UniqueConstraint, Index, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from backend.database import Base


class FinancialPeriod(Base):
    __tablename__ = "financial_periods"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    fiscal_year = Column(Integer, nullable=False)
    fiscal_quarter = Column(Integer, nullable=False)
    period_end_date = Column(Date, nullable=True)
    filing_date = Column(Date, nullable=True)

    # Income Statement
    revenue = Column(BigInteger, nullable=True)
    cost_of_revenue = Column(BigInteger, nullable=True)
    gross_profit = Column(BigInteger, nullable=True)
    gross_profit_ratio = Column(Float, nullable=True)
    operating_income = Column(BigInteger, nullable=True)
    operating_income_ratio = Column(Float, nullable=True)
    net_income = Column(BigInteger, nullable=True)
    net_income_ratio = Column(Float, nullable=True)
    eps = Column(Float, nullable=True)
    eps_diluted = Column(Float, nullable=True)
    ebitda = Column(BigInteger, nullable=True)
    weighted_avg_shares_out = Column(BigInteger, nullable=True)
    weighted_avg_shares_out_dil = Column(BigInteger, nullable=True)
    depreciation_and_amortization = Column(BigInteger, nullable=True)

    # Cash Flow
    operating_cash_flow = Column(BigInteger, nullable=True)
    capital_expenditure = Column(BigInteger, nullable=True)
    free_cash_flow = Column(BigInteger, nullable=True)

    # Metadata
    source = Column(String(50), default="fmp")
    raw_income_statement = Column(JSON, nullable=True)
    raw_cash_flow = Column(JSON, nullable=True)
    retrieved_at = Column(DateTime, default=func.now())

    company = relationship("Company", back_populates="financial_periods")

    __table_args__ = (
        UniqueConstraint("company_id", "fiscal_year", "fiscal_quarter"),
        Index("ix_fin_company_period", "company_id", "fiscal_year", "fiscal_quarter"),
    )
