"""Postgres secrets backend with envelope encryption.

- One random DEK (Fernet key) per tenant, generated on first write.
- The DEK is wrapped by the master KEK (SECRETS_MASTER_KEY env → KMS later);
  key_version records which KEK wrapped it, so KEK rotation only re-wraps
  DEKs — stored ciphertexts never need re-encryption.
- Secret values are Fernet-encrypted with the tenant's DEK.
- FAILS CLOSED: any operation without a configured KEK raises. BYO tenant
  keys must never be stored or returned as plaintext (unlike the engine's
  crypto.py, whose fail-open is tolerated only for the owner's own tokens).
"""
import logging

from cryptography.fernet import Fernet

from product.config import SECRETS_MASTER_KEY
from product.db import get_conn
from product.secrets.base import SecretsBackend

logger = logging.getLogger("product.secrets")

_KEK_VERSION = 1


class LocalPgSecretsBackend(SecretsBackend):
    def __init__(self):
        if not SECRETS_MASTER_KEY:
            raise RuntimeError(
                "SECRETS_MASTER_KEY is not set — refusing to handle tenant secrets. "
                'Generate one: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        self._kek = Fernet(SECRETS_MASTER_KEY.encode())
        self._dek_cache: dict[str, Fernet] = {}

    # ── DEK management ──────────────────────────────────────────────────

    def _dek(self, tenant_id: str, create: bool = False) -> Fernet | None:
        cached = self._dek_cache.get(tenant_id)
        if cached:
            return cached
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT dek_wrapped FROM tenant_deks WHERE tenant_id = %s",
                    (tenant_id,),
                )
                row = cur.fetchone()
                if row:
                    dek_bytes = self._kek.decrypt(row[0].encode())
                elif create:
                    dek_bytes = Fernet.generate_key()
                    cur.execute(
                        """INSERT INTO tenant_deks (tenant_id, dek_wrapped, key_version)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (tenant_id) DO NOTHING""",
                        (tenant_id, self._kek.encrypt(dek_bytes).decode(), _KEK_VERSION),
                    )
                    conn.commit()
                    # A concurrent writer may have won the insert — reread.
                    cur.execute(
                        "SELECT dek_wrapped FROM tenant_deks WHERE tenant_id = %s",
                        (tenant_id,),
                    )
                    dek_bytes = self._kek.decrypt(cur.fetchone()[0].encode())
                else:
                    return None
        finally:
            conn.close()
        dek = Fernet(dek_bytes)
        self._dek_cache[tenant_id] = dek
        return dek

    # ── SecretsBackend API ──────────────────────────────────────────────

    def get(self, tenant_id: str, key: str) -> str | None:
        dek = self._dek(tenant_id)
        if not dek:
            return None
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ciphertext FROM tenant_secrets WHERE tenant_id = %s AND key = %s",
                    (tenant_id, key),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return dek.decrypt(row[0].encode()).decode()
        finally:
            conn.close()

    def set(self, tenant_id: str, key: str, value: str) -> None:
        dek = self._dek(tenant_id, create=True)
        ciphertext = dek.encrypt(value.encode()).decode()
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO tenant_secrets (tenant_id, key, ciphertext, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (tenant_id, key) DO UPDATE
                       SET ciphertext = EXCLUDED.ciphertext, updated_at = NOW()""",
                    (tenant_id, key, ciphertext),
                )
            conn.commit()
        finally:
            conn.close()
        logger.info("Secret %s updated", key, extra={"tenant_id": tenant_id, "secret_key": key})

    def delete(self, tenant_id: str, key: str) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tenant_secrets WHERE tenant_id = %s AND key = %s",
                    (tenant_id, key),
                )
            conn.commit()
        finally:
            conn.close()

    def list_present(self, tenant_id: str) -> set[str]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT key FROM tenant_secrets WHERE tenant_id = %s", (tenant_id,)
                )
                return {r[0] for r in cur.fetchall()}
        finally:
            conn.close()

    def delete_all(self, tenant_id: str) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tenant_secrets WHERE tenant_id = %s", (tenant_id,))
                cur.execute("DELETE FROM tenant_deks WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
        finally:
            conn.close()
        self._dek_cache.pop(tenant_id, None)
