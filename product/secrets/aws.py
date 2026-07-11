"""AWS Secrets Manager / KMS backend — stub.

The envelope structure (per-tenant DEK wrapped by a KEK) in local_pg.py maps
1:1 onto KMS: the KEK becomes a KMS key, DEK wrapping becomes kms:Encrypt /
kms:Decrypt, and rows can stay in Postgres (RDS) or move to Secrets Manager.
Selected with SECRETS_BACKEND=aws.
"""
from product.secrets.base import SecretsBackend


class AwsSecretsBackend(SecretsBackend):
    def __init__(self):
        raise NotImplementedError(
            "SECRETS_BACKEND=aws is not implemented yet — use local_pg. "
            "See this module's docstring for the migration path."
        )

    def get(self, tenant_id, key): ...
    def set(self, tenant_id, key, value): ...
    def delete(self, tenant_id, key): ...
    def list_present(self, tenant_id): ...
    def delete_all(self, tenant_id): ...
