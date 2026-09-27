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

        Co-located with the WhatsApp engine (OWNER_LEGACY_SCOPE=1, default),
        the owner maps to '' — the legacy single-user scope — so the owner
        sees the same memory/vault through WhatsApp and the product web UI.
        Standalone (OWNER_LEGACY_SCOPE=0) there is no engine process to own
        that scope, so the owner gets their own keyspace like everyone else.
        """
        from product.config import OWNER_LEGACY_SCOPE
        return "" if (self.is_owner and OWNER_LEGACY_SCOPE) else self.id
