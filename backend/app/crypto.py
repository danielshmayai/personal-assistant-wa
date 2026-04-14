"""Symmetric encryption for sensitive values stored in the database.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` package.

Set DB_ENCRYPTION_KEY in the environment to a Fernet key generated with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

If DB_ENCRYPTION_KEY is not set the module operates in plaintext-passthrough mode
and logs a warning at import time — safe for local dev, not acceptable for production.
"""

import logging
from app.config import DB_ENCRYPTION_KEY

logger = logging.getLogger("pa.crypto")

_fernet = None

if not DB_ENCRYPTION_KEY:
    logger.warning(
        "DB_ENCRYPTION_KEY is not set — Google tokens will be stored in plaintext. "
        "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )
else:
    try:
        from cryptography.fernet import Fernet, InvalidToken
        _fernet = Fernet(DB_ENCRYPTION_KEY.encode())
        logger.info("Token encryption enabled (Fernet/AES-128-CBC)")
    except Exception as exc:
        logger.error("Invalid DB_ENCRYPTION_KEY — tokens will be stored in plaintext: %s", exc)


def encrypt(value: str) -> str:
    """Encrypt a string. Returns ciphertext or the original value if encryption is disabled."""
    if _fernet is None:
        return value
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a string. Falls back to plaintext for legacy unencrypted rows."""
    if _fernet is None:
        return value
    try:
        from cryptography.fernet import InvalidToken
        return _fernet.decrypt(value.encode()).decode()
    except (InvalidToken, Exception):
        # Value is likely a legacy plaintext row — return as-is so existing tokens
        # keep working; they will be re-encrypted on the next save.
        logger.debug("decrypt: value appears to be unencrypted (legacy row), returning as-is")
        return value
