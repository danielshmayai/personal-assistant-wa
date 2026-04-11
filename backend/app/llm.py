from langchain_ollama import ChatOllama
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, LLM_TIMEOUT_SECONDS


def get_llm() -> ChatOllama:
    """Return a ChatOllama instance configured for the local constrained GPU."""
    return ChatOllama(
        base_url=OLLAMA_BASE_URL,
        model=OLLAMA_MODEL,
        temperature=0.3,
        # Keep-alive matches docker OLLAMA_KEEP_ALIVE so model unloads promptly.
        keep_alive="2m",
        # Limit context to avoid OOM on 4GB VRAM.
        num_ctx=2048,
        # Network timeout — first token can be slow on quantized model.
        timeout=LLM_TIMEOUT_SECONDS,
    )
