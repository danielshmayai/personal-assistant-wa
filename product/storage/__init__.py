from product.config import VAULT_BACKEND
from product.storage.base import VaultStorage

_storage: VaultStorage | None = None


def get_vault_storage() -> VaultStorage:
    global _storage
    if _storage is None:
        if VAULT_BACKEND != "local":
            raise RuntimeError(f"Unsupported VAULT_BACKEND={VAULT_BACKEND!r} (only local)")
        from product.storage.local_fs import LocalFsVaultStorage
        _storage = LocalFsVaultStorage()
    return _storage
