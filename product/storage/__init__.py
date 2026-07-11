from product.config import VAULT_BACKEND
from product.storage.base import VaultStorage

_storage: VaultStorage | None = None


def get_vault_storage() -> VaultStorage:
    global _storage
    if _storage is None:
        if VAULT_BACKEND == "s3":
            from product.storage.s3 import S3VaultStorage
            _storage = S3VaultStorage()
        else:
            from product.storage.local_fs import LocalFsVaultStorage
            _storage = LocalFsVaultStorage()
    return _storage
