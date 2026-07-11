from product.config import SECRETS_BACKEND
from product.secrets.base import SecretsBackend

_backend: SecretsBackend | None = None


def get_secrets_backend() -> SecretsBackend:
    global _backend
    if _backend is None:
        if SECRETS_BACKEND == "aws":
            from product.secrets.aws import AwsSecretsBackend
            _backend = AwsSecretsBackend()
        else:
            from product.secrets.local_pg import LocalPgSecretsBackend
            _backend = LocalPgSecretsBackend()
    return _backend
