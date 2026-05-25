from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text
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
    is_demo = Column(Boolean, default=False)
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
