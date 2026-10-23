from product.config import SECRETS_BACKEND
from product.secrets.base import SecretsBackend

_backend: SecretsBackend | None = None


def get_secrets_backend() -> SecretsBackend:
    global _backend
    if _backend is None:
        if SECRETS_BACKEND != "local_pg":
            raise RuntimeError(f"Unsupported SECRETS_BACKEND={SECRETS_BACKEND!r} (only local_pg)")
        from product.secrets.local_pg import LocalPgSecretsBackend
        _backend = LocalPgSecretsBackend()
    return _backend
