import logging
import re
import shutil
from pathlib import Path

from product.config import VAULT_BASE_DIR
from product.storage.base import VaultStorage

logger = logging.getLogger("product.storage")

_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def _slug(tenant_id: str) -> str:
    slug = _SAFE_ID.sub("", tenant_id)
    if not slug:
        raise ValueError(f"unusable tenant id for vault path: {tenant_id!r}")
    return slug


class LocalFsVaultStorage(VaultStorage):
    def __init__(self):
        self.base = Path(VAULT_BASE_DIR)

    def root_for(self, tenant_id: str) -> Path:
        root = (self.base / "tenants" / _slug(tenant_id)).resolve()
        # Traversal guard: the resolved root must stay under the base dir.
        root.relative_to(self.base.resolve())
        root.mkdir(parents=True, exist_ok=True)
        return root

    def delete_tenant_data(self, tenant_id: str) -> None:
        root = (self.base / "tenants" / _slug(tenant_id)).resolve()
        root.relative_to(self.base.resolve())
        if root.exists():
            shutil.rmtree(root)
            logger.info("Deleted vault for tenant", extra={"tenant_id": tenant_id})
