import logging
from langchain_ollama import ChatOllama
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, LLM_TIMEOUT_SECONDS, GEMINI_MODEL

logger = logging.getLogger("pa.llm")

# One Gemini client per (tenant, key) — never shared across tenants.
# Keyed by tenant_id; the key fingerprint guards against a stale client
# surviving a key rotation that raced the cache bust.
_gemini_cache: dict[str, tuple[str, object]] = {}


def _gemini_key() -> str:
    from app import runtime
    return runtime.get_secret("GEMINI_API_KEY")


def get_llm() -> ChatOllama:
    return ChatOllama(
        base_url=OLLAMA_BASE_URL,
        model=OLLAMA_MODEL,
        temperature=0.3,
        keep_alive="2m",
        num_ctx=2048,
        timeout=LLM_TIMEOUT_SECONDS,
    )


def get_gemini_llm():
    from app import runtime
    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = _gemini_key()
    tid = runtime.tenant_id()
    cached = _gemini_cache.get(tid)
    if cached and cached[0] == api_key:
        return cached[1]
    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=api_key,
        temperature=0.3,
        # Fail fast: without this the client retries a 429/quota for ~30s and it
        # surfaces as an opaque timeout. 2 retries lets the real error reach the
        # UI quickly so the user is told what's wrong (e.g. quota exceeded).
        max_retries=2,
    )
    _gemini_cache[tid] = (api_key, llm)
    return llm


def clear_llm_cache(tenant_id: str | None = None) -> None:
    """Drop cached Gemini clients (call after a tenant's key changes)."""
    if tenant_id is None:
        _gemini_cache.clear()
    else:
        _gemini_cache.pop(tenant_id, None)


def get_smart_llm():
    if _gemini_key():
        return get_gemini_llm()
    return get_llm()


def llm_with_fallback():
    ollama = get_llm()
    if not _gemini_key():
        return ollama
    gemini = get_gemini_llm()
    return ollama.with_fallbacks([gemini])
