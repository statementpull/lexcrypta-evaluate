"""
PII encryption layer using Fernet symmetric encryption.
All client deal names and sensitive identifiers are encrypted at rest.

Key is loaded from ENCRYPTION_KEY env var (base64-encoded 32-byte key).
Generate a key once: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import os
import base64
from cryptography.fernet import Fernet, InvalidToken

_key_env = os.getenv("ENCRYPTION_KEY", "")

def _get_fernet() -> Fernet | None:
    if not _key_env:
        return None
    try:
        return Fernet(_key_env.encode() if isinstance(_key_env, str) else _key_env)
    except Exception:
        return None

_fernet = _get_fernet()


def encrypt_field(value: str) -> str:
    """Encrypt a string field. Returns ciphertext prefixed with 'enc:' so
    it can be distinguished from plaintext values stored before encryption
    was introduced. Falls back to plaintext if no key is configured."""
    if not _fernet or not value:
        return value
    token = _fernet.encrypt(value.encode("utf-8"))
    return "enc:" + base64.urlsafe_b64encode(token).decode("ascii")


def decrypt_field(value: str) -> str:
    """Decrypt a field encrypted by encrypt_field. Passthrough for values
    that aren't prefixed with 'enc:' (backwards-compatible with existing rows)."""
    if not _fernet or not value or not value.startswith("enc:"):
        return value
    try:
        token = base64.urlsafe_b64decode(value[4:].encode("ascii"))
        return _fernet.decrypt(token).decode("utf-8")
    except (InvalidToken, Exception):
        return value


def is_encryption_active() -> bool:
    return _fernet is not None
