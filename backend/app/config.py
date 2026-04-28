import os

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

# Web search — set TAVILY_API_KEY for best results; falls back to DuckDuckGo if empty
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ── Security ────────────────────────────────────────────────────────────────

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

# Timezone used for calendar events — defaults to Israel Standard Time
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "Asia/Jerusalem")

# Tuya smart-home
TUYA_ACCESS_ID = os.getenv("TUYA_ACCESS_ID", "")
TUYA_ACCESS_KEY = os.getenv("TUYA_ACCESS_KEY", "")
TUYA_API_ENDPOINT = os.getenv("TUYA_API_ENDPOINT", "https://openapi.tuyaeu.com")
TUYA_PREFER_LOCAL = os.getenv("TUYA_PREFER_LOCAL", "false").lower() == "true"

# ── Obsidian Vault ──────────────────────────────────────────────────────────

# Path INSIDE the container where the vault volume is mounted.
# The docker-compose volume maps OBSIDIAN_VAULT_HOST_PATH → /vault.
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", "/app/obsidian_vault")
