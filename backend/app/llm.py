import logging
from langchain_ollama import ChatOllama
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, LLM_TIMEOUT_SECONDS, GEMINI_MODEL

logger = logging.getLogger("pa.llm")

# One Gemini client per (tenant, key) — never shared across tenants.
# Keyed by tenant_id; the key fingerprint guards against a stale client
# surviving a key rotation that raced the cache bust.
_gemini_cache: dict[str, tuple[str, object]] = {}

# Self-heal cache: tenant_id -> (api_key, resolved_model). Covers a tenant
# whose key was saved before model-pinning existed (or whose pin write
# failed) — probed once per process lifetime, not once per message.
_resolved_model_cache: dict[str, tuple[str, str]] = {}


def _gemini_key() -> str:
    from app import runtime
    return runtime.get_secret("GEMINI_API_KEY")


def _gemini_model_for(tid: str, api_key: str) -> str:
    """Which model to use for this request.

    Owner scope (tid == "") always uses the platform default — unchanged
    behaviour, no probing, zero added latency for the working WhatsApp path.

    Tenant scope: use the pinned GEMINI_MODEL secret if the key was already
    probed (normal path, set at save time in Settings/onboarding). If no pin
    exists — the key was saved before model-pinning shipped — self-heal: probe
    once, cache it in-process, and persist the pin so future requests (and
    process restarts) read it directly instead of re-probing.
    """
    if not tid:
        return GEMINI_MODEL

    from app import runtime
    pinned = runtime.get_secret("GEMINI_MODEL")
    if pinned:
        return pinned

    cached = _resolved_model_cache.get(tid)
    if cached and cached[0] == api_key:
        return cached[1]

    if not api_key:
        return GEMINI_MODEL  # nothing to probe; the real call will fail as before

    from app.gemini_probe import resolve_gemini_access_sync
    access = resolve_gemini_access_sync(api_key)
    model = access["model"] or GEMINI_MODEL
    _resolved_model_cache[tid] = (api_key, model)
    if access["ok"]:
        runtime.persist_resolved_model(model, access["limited"])
    # else: key is broken on every candidate model — fall through and let the
    # real Gemini call fail with the actual provider error below, which
    # agent_node's except-clause classifies via _classify_agent_error.
    return model


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
    model = _gemini_model_for(tid, api_key)
    fingerprint = f"{api_key}:{model}"
    cached = _gemini_cache.get(tid)
    if cached and cached[0] == fingerprint:
        return cached[1]
    kwargs = {}
    if model.startswith("gemini-2.5"):
        # Gemini 2.5-flash bug: with tools bound AND a system prompt, thinking
        # mode swallows the whole turn — it returns empty content and no tool
        # call (finish_reason=STOP). Disabling thinking restores normal tool
        # calling and text replies. (Verified: budget=0 → proper tool_calls.)
        # Non-2.5 fallback models reject the thinking config entirely.
        kwargs["thinking_budget"] = 0
    llm = ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
        temperature=0.3,
        # Fail fast: without this the client retries a 429/quota for ~30s and it
        # surfaces as an opaque timeout. 2 retries lets the real error reach the
        # UI quickly so the user is told what's wrong (e.g. quota exceeded).
        max_retries=2,
        **kwargs,
    )
    _gemini_cache[tid] = (fingerprint, llm)
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
