from contextvars import ContextVar

# Correlation ID that flows from webhook enqueue → worker → graph run → reply.
# Set once per message in worker._process_one; read by whatsapp and graph modules.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# Tenant identity for the current request. Empty string = the legacy
# single-user/owner path (env-configured vault, env API keys). The product
# layer sets this at every entry point (web session middleware, WS chat,
# OAuth callbacks); the engine reads it in store/vault/secret lookups.
# asyncio tasks copy the context at creation, so ensure_future/gather
# propagate the tenant into tool execution and background work.
current_tenant_id: ContextVar[str] = ContextVar("tenant_id", default="")
