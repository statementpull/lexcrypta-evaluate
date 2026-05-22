from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from .database import Base


class Matter(Base):
    __tablename__ = "matters"
    __table_args__ = {"schema": "verify"}

    id = Column(Integer, primary_key=True)
    subject = Column(String(200), nullable=False)
    ref = Column(String(50), nullable=False, unique=True)
    type = Column(String(30))
    type_label = Column(String(50))
    matter_date = Column(String(30))
    assigned_to = Column(String(100))
    notes = Column(Text)
    exposure = Column(String(10), default="PENDING")
    att = Column(String(10), default="blue")
    att_flag = Column(String(50), default="")
    last_run = Column(String(50), default="—")
    doc_count = Column(Integer, default=0)
    analysed = Column(Boolean, default=False)
    las_score = Column(Integer)
    las_verdict = Column(String(20))
    las_verdict_cls = Column(String(10))
    las_reason = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    documents = relationship("Document", back_populates="matter", cascade="all, delete-orphan")
    result = relationship("AnalysisResult", back_populates="matter", uselist=False, cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = {"schema": "verify"}

    id = Column(Integer, primary_key=True)
    matter_id = Column(Integer, ForeignKey("verify.matters.id"), nullable=False)
    filename = Column(String(200))
    zone = Column(String(10))
    content = Column(LargeBinary)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    matter = relationship("Matter", back_populates="documents")


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    __table_args__ = {"schema": "verify"}

    id = Column(Integer, primary_key=True)
    matter_id = Column(Integer, ForeignKey("verify.matters.id"), nullable=False, unique=True)
    result_json = Column(Text)
    report_html = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    matter = relationship("Matter", back_populates="result")


class License(Base):
    __tablename__ = "licenses"
    __table_args__ = {"schema": "verify"}

    id = Column(Integer, primary_key=True)
    key_hash = Column(String(128))
    activated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Counterparty(Base):
    __tablename__ = "counterparties"
    __table_args__ = {"schema": "verify"}

    id = Column(Integer, primary_key=True)
    name = Column(String(300), nullable=False, unique=True)   # normalised UPPER merchant name
    matter_count = Column(Integer, default=0)
    transaction_count = Column(Integer, default=0)
    total_volume = Column(Float, default=0.0)                 # abs sum across all matters
    first_seen = Column(String(20), default="")
    last_seen = Column(String(20), default="")
    category = Column(String(50), default="")                 # e.g. mortgage, crypto
    severity = Column(String(10), default="none")             # none / green / amber / red
    tags = Column(Text, default="[]")                         # JSON array of analyst tags
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    matter_links = relationship("CounterpartyMatterLink", back_populates="counterparty", cascade="all, delete-orphan")


class CounterpartyMatterLink(Base):
    __tablename__ = "counterparty_matter_links"
    __table_args__ = (
        UniqueConstraint("counterparty_id", "matter_id", name="uq_cp_matter"),
        {"schema": "verify"},
    )

    id = Column(Integer, primary_key=True)
    counterparty_id = Column(Integer, ForeignKey("verify.counterparties.id"), nullable=False)
    matter_id = Column(Integer, ForeignKey("verify.matters.id"), nullable=False)
    transaction_count = Column(Integer, default=0)
    total_volume = Column(Float, default=0.0)

    counterparty = relationship("Counterparty", back_populates="matter_links")


class SdnEntry(Base):
    """Cached row from the OFAC Specially Designated Nationals list."""
    __tablename__ = "sdn_entries"
    __table_args__ = {"schema": "verify"}

    id = Column(Integer, primary_key=True)
    name = Column(String(300), nullable=False, index=True)   # normalised UPPER, punctuation stripped
    sdn_type = Column(String(20), default="")                # Individual / Entity / Vessel / Aircraft
    programs = Column(Text, default="[]")                    # JSON array of sanctions programs
    is_alias = Column(Boolean, default=False)
    source_uid = Column(Integer, nullable=True)              # OFAC uid of the parent entry
    cached_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DfatEntry(Base):
    """Cached row from the DFAT Consolidated Sanctions List (Australia)."""
    __tablename__ = "dfat_entries"
    __table_args__ = {"schema": "verify"}

    id = Column(Integer, primary_key=True)
    name = Column(String(300), nullable=False, index=True)   # normalised UPPER, punctuation stripped
    entity_type = Column(String(20), default="")             # Individual / Entity / Vessel / Aircraft
    regimes = Column(Text, default="[]")                     # JSON array of DFAT regime names
    is_alias = Column(Boolean, default=False)
    reference_code = Column(String(50), nullable=True)       # DFAT reference code for the parent entry
    cached_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
