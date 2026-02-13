"""Transcript SQLAlchemy model."""

from sqlalchemy import Column, Integer, String, Date, Text, DateTime, ForeignKey, UniqueConstraint, Index, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from backend.database import Base


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    fiscal_year = Column(Integer, nullable=False)
    fiscal_quarter = Column(Integer, nullable=False)
    call_date = Column(Date, nullable=True)
    title = Column(String(500), nullable=True)

    raw_text = Column(Text, nullable=False)
    speaker_sections = Column(JSON, nullable=True)
    text_hash = Column(String(64), nullable=True)

    source = Column(String(50), default="finnhub")
    retrieved_at = Column(DateTime, default=func.now())

    company = relationship("Company", back_populates="transcripts")
    claims = relationship("Claim", back_populates="transcript")

    __table_args__ = (
        UniqueConstraint("company_id", "fiscal_year", "fiscal_quarter"),
        Index("ix_transcript_company_period", "company_id", "fiscal_year", "fiscal_quarter"),
    )
