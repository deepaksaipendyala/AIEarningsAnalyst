"""Company SQLAlchemy model."""

from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from backend.database import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    cik = Column(String(10), nullable=True)
    sector = Column(String(100), nullable=True)
    fiscal_year_end_month = Column(Integer, default=12)
    created_at = Column(DateTime, default=func.now())

    transcripts = relationship("Transcript", back_populates="company")
    financial_periods = relationship("FinancialPeriod", back_populates="company")
