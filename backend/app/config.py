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

# LLM timeout — generous for 4GB VRAM card, first-token can be slow.
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
