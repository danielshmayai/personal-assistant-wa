import logging
from langchain_ollama import ChatOllama
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, LLM_TIMEOUT_SECONDS, GEMINI_MODEL

logger = logging.getLogger("pa.llm")

# One Gemini client per (tenant, key) — never shared across tenants.
# Keyed by tenant_id; the key fingerprint guards against a stale client
# surviving a key rotation that raced the cache bust.
_gemini_cache: dict[str, tuple[str, object]] = {}

# One Ollama client per tenant — stateless/local, safe to share the same
# client across tenants that fall back to it, but keyed by tenant anyway for
# symmetry with the cache-busting path in clear_llm_cache.
_ollama_cache: dict[str, object] = {}

# Self-heal cache: tenant_id -> ((api_key, allow_premium), (engine, model)).
# Covers a tenant whose key was saved before model-pinning existed (or whose
# pin write failed) — probed once per process lifetime, not once per message.
_resolved_llm_cache: dict[str, tuple[tuple[str, bool], tuple[str, str]]] = {}


def _gemini_key() -> str:
    from app import runtime
    return runtime.get_secret("GEMINI_API_KEY")


def _allow_premium() -> bool:
    from app import runtime
    return runtime.get_secret("GEMINI_ALLOW_PREMIUM") == "1"


def _resolve_engine_and_model(tid: str, api_key: str) -> tuple[str, str]:
    """Which engine+model to use for this request: ("gemini", <model>) or
    ("ollama", <model>) if no Gemini model works for this key at all.

    Owner scope (tid == "") always uses the platform default Gemini model —
    unchanged behaviour, no probing, no premium cap, no Ollama fallback. This
    policy (tier ceiling + fallback) is a product/tenant-facing feature; the
    already-working WhatsApp path is untouched.

    Tenant scope: use the pinned GEMINI_MODEL/LLM_ENGINE secrets if the key
    was already probed (normal path, set at save time in Settings/onboarding,
    or by this self-heal). If no pin exists, probe once, cache in-process,
    and persist so future requests (and process restarts) read it directly.
    """
    if not tid:
        return "gemini", GEMINI_MODEL

    from app import runtime
    pinned_engine = runtime.get_secret("LLM_ENGINE")
    pinned_model = runtime.get_secret("GEMINI_MODEL")
    if pinned_engine and pinned_model:
        return pinned_engine, pinned_model

    allow_premium = _allow_premium()
    cache_key = (api_key, allow_premium)
    cached = _resolved_llm_cache.get(tid)
    if cached and cached[0] == cache_key:
        return cached[1]

    result: tuple[str, str] | None = None
    if api_key:
        from app.gemini_probe import resolve_gemini_access_sync
        access = resolve_gemini_access_sync(api_key, allow_premium=allow_premium)
        if access["ok"]:
            result = ("gemini", access["model"])
            runtime.persist_resolved_model(access["model"], access["limited"], engine="gemini")
        # else: no Gemini model works for this key at all — fall through to
        # the local Ollama fallback below instead of hard-failing every
        # message with the same "key rejected" error forever.

    if result is None:
        result = ("ollama", OLLAMA_MODEL)
        runtime.persist_resolved_model(OLLAMA_MODEL, False, engine="ollama")

    _resolved_llm_cache[tid] = (cache_key, result)
    return result


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
    """Always Gemini — for vision/specialised tools that require it
    specifically (image analysis, structured extraction). Does not fall
    back to Ollama; callers that need graceful degradation should use
    get_chat_llm() instead."""
    from app import runtime
    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = _gemini_key()
    tid = runtime.tenant_id()
    engine, model = _resolve_engine_and_model(tid, api_key)
    if engine != "gemini":
        # No usable Gemini model for this key — nothing sensible to return
        # for a vision-only tool; let the caller's own error handling react
        # to a real (informative) Gemini auth failure.
        model = GEMINI_MODEL
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


def get_chat_llm() -> tuple[object, str, str]:
    """Primary conversational LLM for agent_node.

    Returns (llm, engine, model). engine is "gemini" or "ollama" — the agent
    node uses this to decide whether to bind tools (Ollama's local model
    doesn't reliably support function-calling, so tools are skipped rather
    than attempted and silently mishandled).
    """
    from app import runtime

    api_key = _gemini_key()
    tid = runtime.tenant_id()
    engine, model = _resolve_engine_and_model(tid, api_key)

    if engine == "ollama":
        cached = _ollama_cache.get(tid)
        if cached is not None:
            return cached, "ollama", model
        llm = get_llm()
        _ollama_cache[tid] = llm
        return llm, "ollama", model

    return get_gemini_llm(), "gemini", model


def clear_llm_cache(tenant_id: str | None = None) -> None:
    """Drop cached LLM clients and the self-heal resolution cache (call
    after a tenant's key or model preference changes)."""
    if tenant_id is None:
        _gemini_cache.clear()
        _ollama_cache.clear()
        _resolved_llm_cache.clear()
    else:
        _gemini_cache.pop(tenant_id, None)
        _ollama_cache.pop(tenant_id, None)
        _resolved_llm_cache.pop(tenant_id, None)


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
