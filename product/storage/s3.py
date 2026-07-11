"""S3 vault storage — stub. Selected with VAULT_BACKEND=s3.

Planned shape: mirror s3://{bucket}/vaults/{tenant_id}/ into a local cache
directory returned by root_for(), syncing writes back (or replace the
filesystem calls in app/memory/obsidian.py with read/write/list methods).
"""
from pathlib import Path

from product.storage.base import VaultStorage


class S3VaultStorage(VaultStorage):
    def __init__(self):
        raise NotImplementedError("VAULT_BACKEND=s3 is not implemented yet — use local.")

    def root_for(self, tenant_id: str) -> Path: ...
    def delete_tenant_data(self, tenant_id: str) -> None: ...
