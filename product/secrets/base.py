# Postponed annotations: the abstract `set` method below shadows builtin
# `set` inside the class body, which would break the `set[str]` annotation.
from __future__ import annotations

from abc import ABC, abstractmethod


class SecretsBackend(ABC):
    """Per-tenant secret storage. Values are encrypted at rest; the backend
    never returns plaintext to callers that only need presence (list_present)."""

    @abstractmethod
    def get(self, tenant_id: str, key: str) -> str | None: ...

    @abstractmethod
    def set(self, tenant_id: str, key: str, value: str) -> None: ...

    @abstractmethod
    def delete(self, tenant_id: str, key: str) -> None: ...

    @abstractmethod
    def list_present(self, tenant_id: str) -> set[str]:
        """Names of keys that have a value — never the values themselves."""

    @abstractmethod
    def delete_all(self, tenant_id: str) -> None:
        """Offboarding: remove every secret (and key material) for a tenant."""
