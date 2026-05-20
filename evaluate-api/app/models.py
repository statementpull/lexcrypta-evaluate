from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from .database import Base


class Deal(Base):
    __tablename__ = "deals"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    ref = Column(String(50), nullable=False, unique=True)
    deal_value = Column(Float)
    industry = Column(String(100))
    analysis_period_months = Column(Integer, default=24)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    purged = Column(Boolean, default=False)

    sessions = relationship("EvalSession", back_populates="deal", cascade="all, delete-orphan")


class EvalSession(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("evaluate.deals.id"), nullable=False)
    status = Column(String(30), default="uploading")  # uploading | running | complete | purged
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime)

    deal = relationship("Deal", back_populates="sessions")
    signals = relationship("Signal", back_populates="session", cascade="all, delete-orphan")
    reports = relationship("Report", back_populates="session", cascade="all, delete-orphan")


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("evaluate.sessions.id"), nullable=False)
    signal_type = Column(String(50))
    severity = Column(String(10))
    merchant = Column(String(200))
    amount = Column(Float)
    description = Column(Text)
    library_match = Column(String(100))
    confidence_weight = Column(Float, default=0.0)
    transaction_date = Column(String(20))

    session = relationship("EvalSession", back_populates="signals")


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("evaluate.sessions.id"), nullable=False)
    verdict = Column(String(10))
    las_score = Column(Float)
    report_html = Column(Text)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    session = relationship("EvalSession", back_populates="reports")
