"""Claim and Verdict SQLAlchemy models."""

from uuid import uuid4
from sqlalchemy import Column, Integer, Float, String, Boolean, Text, DateTime, ForeignKey, Index, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from backend.database import Base


class Claim(Base):
    __tablename__ = "claims"

    id = Column(Integer, primary_key=True)
    claim_uid = Column(String(36), unique=True, default=lambda: str(uuid4()))
    transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=False)

    # Quote anchoring
    quote_text = Column(Text, nullable=False)
    quote_start_char = Column(Integer, nullable=True)
    quote_end_char = Column(Integer, nullable=True)

    # Speaker
    speaker_name = Column(String(200), nullable=True)
    speaker_role = Column(String(50), nullable=True)

    # Classification
    metric = Column(String(50), nullable=False, index=True)
    basis = Column(String(20), default="unknown")
    claim_form = Column(String(30), nullable=False)

    # Values
    claimed_value = Column(Float, nullable=True)
    claimed_value_raw = Column(String(100), nullable=True)
    claimed_unit = Column(String(30), nullable=True)
    claimed_scale = Column(String(20), nullable=True)

    # Period
    period_type = Column(String(30), nullable=True)
    period_fiscal_year = Column(Integer, nullable=True)
    period_fiscal_quarter = Column(Integer, nullable=True)
    comparator = Column(String(10), nullable=True)
    comparison_period = Column(String(50), nullable=True)

    # Qualifiers
    qualifiers = Column(JSON, default=list)
    scope = Column(String(200), nullable=True)

    # Metadata
    extraction_confidence = Column(Float, nullable=True)
    raw_llm_output = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now())

    transcript = relationship("Transcript", back_populates="claims")
    verdict = relationship("Verdict", back_populates="claim", uselist=False)

    __table_args__ = (
        Index("ix_claim_metric", "metric"),
        Index("ix_claim_transcript", "transcript_id"),
    )


class Verdict(Base):
    __tablename__ = "verdicts"

    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("claims.id"), unique=True, nullable=False)

    # Verdict
    label = Column(String(30), nullable=False, index=True)

    # Evidence
    actual_value = Column(Float, nullable=True)
    actual_unit = Column(String(30), nullable=True)
    delta_absolute = Column(Float, nullable=True)
    delta_relative_pct = Column(Float, nullable=True)

    # Tolerance
    tolerance_type = Column(String(30), nullable=True)
    tolerance_value = Column(Float, nullable=True)
    tolerance_expanded = Column(Boolean, default=False)

    # Computation trace
    computation_steps = Column(JSON, nullable=True)
    financial_facts_used = Column(JSON, nullable=True)

    # Misleading flags
    misleading_flags = Column(JSON, default=list)
    misleading_reasons = Column(JSON, default=list)

    # Metadata
    evidence_source = Column(String(100), nullable=True)
    overall_confidence = Column(Float, nullable=True)
    explanation = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())

    claim = relationship("Claim", back_populates="verdict")
