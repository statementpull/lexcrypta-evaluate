import os
import sqlite3
import tempfile
from cryptography.fernet import Fernet
from typing import Optional


class LibraryLoader:
    def __init__(self, enc_path: str, secret: str):
        self._enc_path = enc_path
        self._secret = secret
        self._conn: Optional[sqlite3.Connection] = None

    def load(self):
        if not os.path.exists(self._enc_path):
            self._conn = None
            return
        key = _derive_key(self._secret)
        raw_enc = open(self._enc_path, "rb").read()
        raw_db = Fernet(key).decrypt(raw_enc)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_name = tmp.name
        tmp.write(raw_db)
        tmp.flush()
        tmp.close()
        self._conn = sqlite3.connect(tmp_name)
        self._conn.row_factory = sqlite3.Row
        # On Windows, SQLite holds the file open — skip unlink; tempfile is cleaned by OS on reboot.
        # On Linux/Mac the file is unlinked immediately after open (inode stays until conn closes).

    def _query(self, table: str, merchant_norm: str, ticker_col: str) -> Optional[dict]:
        if self._conn is None:
            return None
        try:
            rows = self._conn.execute(
                f"SELECT * FROM {table} WHERE active_status='active'"
            ).fetchall()
        except sqlite3.OperationalError:
            return None
        mn = merchant_norm.upper()
        best = None
        best_weight = 0.0
        for _row in rows:
            row = dict(_row)
            tickers = [t.strip().upper() for t in (row.get(ticker_col) or "").split(",") if t.strip()]
            obfuscations = [t.strip().upper() for t in (row.get("likely_obfuscations") or "").split(",") if t.strip()]
            legacy = [t.strip().upper() for t in (row.get("legacy_past_tickers") or "").split(",") if t.strip()]
            all_terms = tickers + obfuscations + legacy
            if any(term and term in mn for term in all_terms):
                w = row["confidence_weight"] or 0.0
                if w > best_weight:
                    best_weight = w
                    best = dict(row)
        return best

    def match_gambling(self, merchant_norm: str) -> Optional[dict]:
        return self._query("gambling_platform_library", merchant_norm, "current_known_bank_tickers")

    def match_crypto_exchange(self, merchant_norm: str) -> Optional[dict]:
        if self._conn is None:
            return None
        try:
            rows = self._conn.execute(
                "SELECT * FROM crypto_exchange_library WHERE active_status='active'"
            ).fetchall()
        except sqlite3.OperationalError:
            return None
        mn = merchant_norm.upper()
        for row in rows:
            name = (row["exchange_name"] or "").upper()
            if name and name in mn:
                return dict(row)
        return None

    def match_any(self, merchant_norm: str) -> list[dict]:
        results = []
        g = self.match_gambling(merchant_norm)
        if g:
            g["_source_library"] = "gambling_platform_library"
            results.append(g)
        c = self.match_crypto_exchange(merchant_norm)
        if c:
            c["_source_library"] = "crypto_exchange_library"
            results.append(c)
        return results

    def get_library_counts(self) -> dict:
        if self._conn is None:
            return {}
        counts = {}
        tables = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for t in tables:
            name = t[0]
            try:
                n = self._conn.execute(
                    f"SELECT COUNT(*) FROM {name} WHERE active_status='active'"
                ).fetchone()[0]
                counts[name] = n
            except Exception:
                try:
                    n = self._conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                    counts[name] = n
                except Exception:
                    counts[name] = 0
        return counts


def _derive_key(secret: str) -> bytes:
    import base64
    import hashlib
    raw = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(raw)


# Module-level singleton — loaded at FastAPI startup
_loader: Optional[LibraryLoader] = None


def get_loader() -> Optional[LibraryLoader]:
    return _loader


def init_loader(enc_path: str, secret: str):
    global _loader
    _loader = LibraryLoader(enc_path, secret)
    _loader.load()
