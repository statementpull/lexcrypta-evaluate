import base64
import hashlib
import os
import sqlite3
import tempfile

from cryptography.fernet import Fernet

from app.intelligence.library_loader import LibraryLoader


def _derive_key(secret: str) -> bytes:
    raw = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def _make_test_enc_db(path: str, key: bytes):
    """Build a minimal encrypted SQLite for testing."""
    tmp_path = path + ".tmp.db"
    conn = sqlite3.connect(tmp_path)
    conn.execute("""CREATE TABLE gambling_platform_library (
        id INTEGER PRIMARY KEY,
        brand_name TEXT,
        current_known_bank_tickers TEXT,
        legacy_past_tickers TEXT,
        likely_obfuscations TEXT,
        signal_strength TEXT,
        confidence_weight REAL,
        active_status INTEGER DEFAULT 1
    )""")
    conn.execute("""INSERT INTO gambling_platform_library VALUES
        (1,'Sportsbet','SPORTSBET,SB PAYMENTS','SPORTSBETAU','SPORT BET','Very Strong',0.95,1)""")
    conn.execute("""CREATE TABLE crypto_exchange_library (
        id INTEGER PRIMARY KEY,
        exchange_name TEXT,
        jurisdiction TEXT,
        recovery_difficulty_score REAL,
        signal_strength TEXT,
        confidence_weight REAL,
        active_status INTEGER DEFAULT 1
    )""")
    conn.execute("""INSERT INTO crypto_exchange_library VALUES
        (1,'Coinbase','USA',0.4,'Strong',0.85,1)""")
    conn.commit()
    conn.close()
    raw = open(tmp_path, "rb").read()
    os.unlink(tmp_path)
    f = Fernet(key)
    open(path, "wb").write(f.encrypt(raw))


def test_loader_decrypts_and_queries(tmp_path):
    secret = "test-secret"
    key = _derive_key(secret)
    db_path = str(tmp_path / "lexi_libraries.db.enc")
    _make_test_enc_db(db_path, key)
    loader = LibraryLoader(db_path, secret)
    loader.load()
    result = loader.match_gambling("SPORTSBET AU 123")
    assert result is not None
    assert result["brand_name"] == "Sportsbet"
    assert result["confidence_weight"] == 0.95


def test_match_crypto_exchange(tmp_path):
    secret = "test-secret"
    key = _derive_key(secret)
    db_path = str(tmp_path / "lexi_libraries.db.enc")
    _make_test_enc_db(db_path, key)
    loader = LibraryLoader(db_path, secret)
    loader.load()
    result = loader.match_crypto_exchange("COINBASE INC PAYMENT")
    assert result is not None
    assert result["jurisdiction"] == "USA"


def test_no_match_returns_none(tmp_path):
    secret = "test-secret"
    key = _derive_key(secret)
    db_path = str(tmp_path / "lexi_libraries.db.enc")
    _make_test_enc_db(db_path, key)
    loader = LibraryLoader(db_path, secret)
    loader.load()
    assert loader.match_gambling("WOOLWORTHS METRO 99") is None


def test_missing_enc_file_does_not_crash(tmp_path):
    loader = LibraryLoader(str(tmp_path / "nonexistent.db.enc"), "secret")
    loader.load()
    assert loader.match_gambling("anything") is None
    assert loader.get_library_counts() == {}
