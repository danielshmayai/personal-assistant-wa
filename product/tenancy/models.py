from dataclasses import dataclass
from datetime import datetime


@dataclass
class Tenant:
    id: str
    email: str
    display_name: str = ""
    avatar_url: str = ""
    is_owner: bool = False
    status: str = "active"
    onboarded_at: datetime | None = None

    @property
    def engine_scope(self) -> str:
        """Tenant id as seen by the engine's data layer.

        The owner maps to '' — the legacy single-user scope — so the owner
        sees the same memory/vault through WhatsApp and the product web UI.
        Every other tenant gets their own keyspace.
        """
        return "" if self.is_owner else self.id
