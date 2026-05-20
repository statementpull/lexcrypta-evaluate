from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey, Date, LargeBinary
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
    status = Column(String(30), default="uploading")
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
    library_match = Column(String(200))
    confidence_weight = Column(Float, default=0.0)
    transaction_date = Column(String(20))

    session = relationship("EvalSession", back_populates="signals")


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("evaluate.sessions.id"), nullable=False)
    verdict = Column(String(50))
    las_score = Column(Float)
    report_html = Column(Text)
    deal_summary_html = Column(Text)
    lawyer_summary_html = Column(Text)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    session = relationship("EvalSession", back_populates="reports")


class License(Base):
    __tablename__ = "licenses"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    key = Column(String(100), nullable=False, unique=True)
    activated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DealTransaction(Base):
    __tablename__ = "deal_transactions"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("evaluate.deals.id"), nullable=False)
    source = Column(String(30))
    report_type = Column(String(50))
    transaction_date = Column(Date)
    description = Column(Text)
    description_norm = Column(Text)
    reference = Column(String(100))
    amount = Column(Float)
    direction = Column(String(10))


class TaxDeclaration(Base):
    __tablename__ = "tax_declarations"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("evaluate.deals.id"), nullable=False)
    jurisdiction = Column(String(50))
    tax_year = Column(Integer)
    period_start = Column(Date)
    period_end = Column(Date)
    declared_income = Column(Float)
    declared_expenses = Column(Float)
    declared_net = Column(Float)
    adjusted_gross_income = Column(Float)
    schedule_c_profit = Column(Float)
    taxable_income = Column(Float)
    pdf_filename = Column(String(200))
    pdf_data = Column(LargeBinary)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    k1_partners = relationship("K1Partner", back_populates="tax_declaration", cascade="all, delete-orphan")


class K1Partner(Base):
    __tablename__ = "k1_partners"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    tax_declaration_id = Column(Integer, ForeignKey("evaluate.tax_declarations.id"), nullable=False)
    deal_id = Column(Integer, ForeignKey("evaluate.deals.id"), nullable=False)
    partner_name = Column(String(200))
    distributions = Column(Float)
    income_share = Column(Float)

    tax_declaration = relationship("TaxDeclaration", back_populates="k1_partners")


class ReconciliationBreach(Base):
    __tablename__ = "reconciliation_breaches"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("evaluate.deals.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("evaluate.sessions.id"), nullable=False)
    breach_type = Column(String(50))
    bank_transaction_id = Column(Integer)
    accounting_transaction_id = Column(Integer)
    bank_amount = Column(Float)
    accounting_amount = Column(Float)
    gap_amount = Column(Float)
    transaction_date = Column(Date)
    description = Column(Text)
    library_signal = Column(String(100))
    library_source = Column(String(100))
    severity = Column(String(10))


class RevenueGap(Base):
    __tablename__ = "revenue_gaps"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("evaluate.deals.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("evaluate.sessions.id"), nullable=False)
    tax_year = Column(Integer)
    period_start = Column(Date)
    period_end = Column(Date)
    bank_total_credits = Column(Float)
    declared_income = Column(Float)
    income_gap = Column(Float)
    bank_total_debits = Column(Float)
    declared_expenses = Column(Float)
    expense_gap = Column(Float)
    is_escalating = Column(Boolean, default=False)


class UccFiling(Base):
    __tablename__ = "ucc_filings"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("evaluate.deals.id"), nullable=False)
    filing_number = Column(String(100))
    filing_date = Column(Date)
    expiry_date = Column(Date)
    secured_party = Column(String(300))
    collateral_description = Column(Text)
    status = Column(String(30))
    amount_stated = Column(Float)
    state = Column(String(10))
    notes = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class WebFinding(Base):
    __tablename__ = "web_findings"
    __table_args__ = {"schema": "evaluate"}

    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("evaluate.deals.id"), nullable=False)
    source_name = Column(String(100))
    source_type = Column(String(50))
    severity = Column(String(10))
    title = Column(String(300))
    description = Column(Text)
    confidence = Column(Float)
    business_name_searched = Column(String(300))
    scanned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
