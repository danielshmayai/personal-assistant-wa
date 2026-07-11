"""Product-layer configuration.

Platform-level settings only. Tenant API keys (Gemini, Tavily, Tuya, …)
are NEVER env vars here — they live encrypted per tenant in the secrets
backend and are entered through the settings UI.
"""
import os

# Postgres — same database as the engine (product tables live alongside).
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Signs session tokens (HMAC). Generate: openssl rand -hex 32. Required.
SESSION_SECRET = os.getenv("SESSION_SECRET", "")

# Master KEK that wraps per-tenant DEKs (envelope encryption). Must be a
# Fernet key. Generate:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Required — the secrets backend fails CLOSED without it.
SECRETS_MASTER_KEY = os.getenv("SECRETS_MASTER_KEY", "")

# Platform Google OAuth client — used for BOTH "Sign in with Google" (OIDC)
# and the Gmail/Calendar/Drive consent flow. End users never supply this.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Public base URL of the product (nginx origin). OIDC + OAuth redirects
# derive from it: {PRODUCT_BASE_URL}/auth/callback and /auth/google/callback.
PRODUCT_BASE_URL = os.getenv("PRODUCT_BASE_URL", "http://localhost:8080")

# The Google account that owns this deployment. The first login from this
# email is seeded as the owner tenant (is_owner=true, engine scope '').
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "")

# Where per-tenant Obsidian vaults live (local backend). S3 later.
VAULT_BASE_DIR = os.getenv("VAULT_BASE_DIR", "/data/vaults")
VAULT_BACKEND = os.getenv("VAULT_BACKEND", "local")       # local | s3 (stub)
SECRETS_BACKEND = os.getenv("SECRETS_BACKEND", "local_pg")  # local_pg | aws (stub)

SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "30"))

# Per-tenant rate limits (token bucket, per process)
CHAT_MESSAGES_PER_MINUTE = int(os.getenv("CHAT_MESSAGES_PER_MINUTE", "20"))
API_REQUESTS_PER_MINUTE = int(os.getenv("API_REQUESTS_PER_MINUTE", "60"))

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


def require_platform_config() -> list[str]:
    """Names of required platform settings that are missing."""
    missing = []
    if not DATABASE_URL:
        missing.append("DATABASE_URL")
    if not SESSION_SECRET:
        missing.append("SESSION_SECRET")
    if not SECRETS_MASTER_KEY:
        missing.append("SECRETS_MASTER_KEY")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        missing.append("GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET")
    return missing
