"""Injection seam between the engine (this package) and the product layer.

The engine is a library: it must keep working standalone with env-var
configuration (the owner / WhatsApp deployment), while the product layer
wraps it for multi-tenant web use. The product injects resolvers here at
startup; every default preserves today's single-user behaviour, so this
module is a no-op unless a product layer configures it.

Resolvers are expected to consult `app.context.current_tenant_id` — the
engine never passes tenant_id explicitly to them (and tools never accept
tenant_id as an LLM-callable argument).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from app.context import current_tenant_id

# ── Secrets ─────────────────────────────────────────────────────────────────

# Default: process environment — exactly what config.py reads today.
_secret_resolver: Callable[[str], str | None] = os.getenv


def set_secret_resolver(fn: Callable[[str], str | None]) -> None:
    global _secret_resolver
    _secret_resolver = fn


def get_secret(key: str) -> str:
    """Resolve a secret for the current tenant.

    Fail-closed for tenants: if the resolver errors, a tenant gets ""
    (never the platform env, which holds the owner's credentials).
    Owner scope keeps the env fallback.
    """
    try:
        return _secret_resolver(key) or ""
    except Exception:
        if current_tenant_id.get():
            return ""
        return os.getenv(key, "")


# ── Vault root ──────────────────────────────────────────────────────────────

def _default_vault_root() -> Path:
    return Path(os.getenv("OBSIDIAN_VAULT_PATH", "/app/obsidian_vault"))


_vault_root_resolver: Callable[[], Path] = _default_vault_root


def set_vault_root_resolver(fn: Callable[[], Path]) -> None:
    global _vault_root_resolver
    _vault_root_resolver = fn


def get_vault_root() -> Path:
    """Vault directory for the current tenant (owner base dir by default)."""
    try:
        return _vault_root_resolver()
    except Exception:
        return _default_vault_root()


# ── Model pinning ───────────────────────────────────────────────────────────

# Lets llm.py's self-heal probe (for a tenant whose key was saved before
# model-pinning existed, or whose pin never landed) persist the result so
# future requests read the pin directly instead of re-probing every time.
_model_pin_writer: Callable[[str, bool, str], None] | None = None


def set_model_pin_writer(fn: Callable[[str, bool, str], None]) -> None:
    global _model_pin_writer
    _model_pin_writer = fn


def persist_resolved_model(model: str, limited: bool, engine: str = "gemini") -> None:
    if _model_pin_writer is None:
        return
    try:
        _model_pin_writer(model, limited, engine)
    except Exception:
        pass


# ── Cache invalidation ──────────────────────────────────────────────────────

def on_secrets_changed(tenant_id: str) -> None:
    """Bust per-tenant caches after a secret is added/rotated/removed."""
    from app.llm import clear_llm_cache
    from app.graph.tools_registry import clear_tools_cache_for_tenant
    from app.tuya.tools import clear_tuya_cache

    clear_llm_cache(tenant_id)
    clear_tools_cache_for_tenant(tenant_id)
    clear_tuya_cache(tenant_id)


def tenant_id() -> str:
    """Convenience accessor for the current tenant ('' = owner/legacy)."""
    return current_tenant_id.get()


async def in_thread(fn, *args):
    """run_in_executor with ContextVar propagation.

    loop.run_in_executor does NOT copy contextvars into the worker thread,
    so tenant-scoped code (store/_tid, vault root, secret lookups) executed
    via a bare executor would silently fall back to the owner scope. Always
    use this for blocking calls that touch tenant data.
    """
    import asyncio
    import contextvars
    import functools

    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(None, functools.partial(ctx.run, fn, *args))
