from abc import ABC, abstractmethod
from pathlib import Path


class VaultStorage(ABC):
    """Where a tenant's Obsidian-style vault lives.

    The engine works against a filesystem Path; the local backend hands out
    per-tenant directories. An S3 backend can either sync to a local cache
    dir or grow read/write/list methods — the seam is root_for().
    """

    @abstractmethod
    def root_for(self, tenant_id: str) -> Path: ...

    @abstractmethod
    def delete_tenant_data(self, tenant_id: str) -> None:
        """Offboarding: irreversibly remove the tenant's vault."""
