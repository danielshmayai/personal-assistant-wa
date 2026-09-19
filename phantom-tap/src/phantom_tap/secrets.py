"""Credentials at rest.

Same shape as the existing assistant's `app/crypto.py`: Fernet (AES-128-CBC +
HMAC-SHA256) with the key in a 0600 file outside the encrypted blob. The point is
narrow and worth stating plainly - it protects a stolen backup or a synced folder,
not a process running as you. Anything that can read the key file can read the
password, so the key file's mode is enforced rather than assumed.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretsError(RuntimeError):
    pass


class SecretStore:
    def __init__(self, key_path: str | Path, store_path: str | Path) -> None:
        self.key_path = Path(key_path)
        self.store_path = Path(store_path)

    # ------------------------------------------------------------------ key --

    def ensure_key(self) -> bytes:
        if self.key_path.exists():
            return self._read_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        # Create with 0600 from the start: writing then chmod leaves a window
        # where the key is world-readable.
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        return key

    def _read_key(self) -> bytes:
        mode = stat.S_IMODE(self.key_path.stat().st_mode)
        if mode & 0o077:
            raise SecretsError(
                f"{self.key_path} is mode {mode:o}; it must not be group- or "
                f"world-readable. Fix with: chmod 600 {self.key_path}"
            )
        return self.key_path.read_bytes().strip()

    # --------------------------------------------------------------- values --

    def _load(self) -> dict[str, str]:
        if not self.store_path.exists():
            return {}
        try:
            plain = Fernet(self._read_key()).decrypt(self.store_path.read_bytes())
        except InvalidToken as exc:
            raise SecretsError(
                f"{self.store_path} cannot be decrypted with {self.key_path}. "
                f"If the key was regenerated, the old secrets are unrecoverable - "
                f"delete the store and re-run `pt login`."
            ) from exc
        return json.loads(plain)

    def _save(self, values: dict[str, str]) -> None:
        blob = Fernet(self.ensure_key()).encrypt(json.dumps(values).encode())
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        tmp.replace(self.store_path)  # atomic: never a half-written secret store

    def set(self, name: str, value: str) -> None:
        values = self._load()
        values[name] = value
        self._save(values)

    def get(self, name: str, *, env_fallback: str | None = None) -> str:
        """Read a secret, preferring the environment so containers can inject one."""
        if env_fallback and os.getenv(env_fallback):
            return os.environ[env_fallback]
        values = self._load()
        if name not in values:
            hint = f" or set ${env_fallback}" if env_fallback else ""
            raise SecretsError(f"secret {name!r} is not stored. Run `pt login`{hint}.")
        return values[name]

    def names(self) -> list[str]:
        return sorted(self._load())
