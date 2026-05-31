import os

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")  # "development" | "production"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b-it-qat")
WAHA_BASE_URL = os.getenv("WAHA_BASE_URL", "http://waha:3000")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "")
WAHA_SESSION = os.getenv("WAHA_SESSION", "default")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Your WhatsApp ID — messages from this ID in self-chat go to LangGraph.
# Format: "972501234567@c.us" (country code + number, no + or spaces)
MY_WHATSAPP_ID = os.getenv("MY_WHATSAPP_ID", "")

# Optional: @lid format of own number in newer WhatsApp multi-device.
# The backend tries to auto-detect this from WAHA at startup.
# Set manually if auto-detection fails: check WAHA logs for your @lid.
MY_WHATSAPP_LID = os.getenv("MY_WHATSAPP_LID", "")

# LLM timeout — generous for 4GB VRAM card, first-token can be slow.
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")

# Google Cloud Text-to-Speech (Neural2). Leave empty to skip and use edge-tts only.
GOOGLE_TTS_API_KEY = os.getenv("GOOGLE_TTS_API_KEY", "")

# Web search — set TAVILY_API_KEY for best results; falls back to DuckDuckGo if empty
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ── Security ────────────────────────────────────────────────────────────────

# Allowed origin for CORS. Set to your Cloudflare tunnel domain in production.
# Example: ALLOWED_ORIGIN=https://pa.yourdomain.com
# Falls back to localhost origins for local dev when unset.
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "")

# Shared secret the backend requires on every incoming WAHA webhook call.
# Include it in the webhook URL: http://backend:8000/webhook/waha?secret=<value>
# Generate: openssl rand -hex 32
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Bearer token required to call the dev-only POST /test endpoint.
# Generate: openssl rand -hex 32
TEST_TOKEN = os.getenv("TEST_TOKEN", "")

# Fernet key for encrypting Google OAuth tokens at rest in PostgreSQL.
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DB_ENCRYPTION_KEY = os.getenv("DB_ENCRYPTION_KEY", "")

# VAPID keys for Web Push Notifications.
# Generate once: docker compose exec backend python -c "from app.push_notifications import print_vapid_keys; print_vapid_keys()"
# VAPID_PRIVATE_KEY: PEM string with literal \n (e.g. -----BEGIN EC PRIVATE KEY-----\nMHQ...\n-----END EC PRIVATE KEY-----)
# VAPID_PUBLIC_KEY: base64url-encoded uncompressed EC point (for browser applicationServerKey)
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com")

# Timezone used for calendar events — defaults to Israel Standard Time
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "Asia/Jerusalem")

# Default city for weather in the Today Strip
USER_CITY = os.getenv("USER_CITY", "Tel Aviv")

# Tuya smart-home
TUYA_ACCESS_ID = os.getenv("TUYA_ACCESS_ID", "")
TUYA_ACCESS_KEY = os.getenv("TUYA_ACCESS_KEY", "")
TUYA_API_ENDPOINT = os.getenv("TUYA_API_ENDPOINT", "https://openapi.tuyaeu.com")
TUYA_PREFER_LOCAL = os.getenv("TUYA_PREFER_LOCAL", "false").lower() == "true"

# ── Obsidian Vault ──────────────────────────────────────────────────────────

# Path INSIDE the container where the vault volume is mounted.
# The docker-compose volume maps OBSIDIAN_VAULT_HOST_PATH → /vault.
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", "/app/obsidian_vault")

# ── Observability ────────────────────────────────────────────────────────────

# When set, enables LangSmith tracing for all LangGraph runs.
# Also set LANGSMITH_PROJECT to route traces to the right project.
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "pa-assistant")

# Set LOG_FORMAT=text to get human-readable logs instead of JSON lines.
LOG_FORMAT = os.getenv("LOG_FORMAT", "json")

# Max concurrent tool executions across asyncio.gather() in tool_node.
# Prevents flooding external APIs (Google, Tuya, web) under high recursion.
TOOL_CONCURRENCY = int(os.getenv("TOOL_CONCURRENCY", "10"))

# ── Fitness ───────────────────────────────────────────────────────────────────────

FITNESS_WEEKLY_SESSION_TARGET = int(os.getenv("FITNESS_WEEKLY_SESSION_TARGET", "4"))
