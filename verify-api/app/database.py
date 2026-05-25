from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import DATABASE_URL

_db_url = DATABASE_URL.replace("postgres://", "postgresql://", 1) if DATABASE_URL.startswith("postgres://") else DATABASE_URL
engine = create_engine(_db_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_verify_schema():
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS verify"))
        # Add is_demo column if it doesn't exist (safe to run on existing DBs)
        conn.execute(text(
            "ALTER TABLE IF EXISTS verify.matters "
            "ADD COLUMN IF NOT EXISTS is_demo BOOLEAN DEFAULT FALSE"
        ))
        conn.commit()
