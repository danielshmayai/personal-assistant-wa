# PA Product — Multi-Tenant Personal Assistant

The productized, multi-tenant edition of the `pa` assistant. It wraps the
existing engine (`backend/app` — LangGraph agent, ~40 tools, Obsidian-style
memory) in a SaaS-ready shell: Google sign-in, per-user encrypted API keys,
per-user memory isolation, module toggles, and a React web app.

The original single-user owner stack (`docker-compose.yml`, WhatsApp via
WAHA) keeps working unchanged; the product is a second entrypoint over the
same engine.

## Architecture (two layers, one seam)

```
frontend/  (React + Vite + TS + Tailwind SPA)
    │  same-origin via nginx (cookie flows to WebSocket)
    ▼
product/   (FastAPI shell: tenancy, OIDC auth, secrets, modules, admin)
    │  injection seam: current_tenant_id ContextVar +
    │  runtime.set_secret_resolver / set_vault_root_resolver
    ▼
backend/app  (engine, unchanged behaviour for the owner:
              LangGraph graph, tools, memory, WhatsApp)
```

- **Tenant isolation**: every memory table carries `tenant_id` (owner = `''`),
  every pgvector search filters by it, each tenant gets a private vault
  directory, Google tokens are keyed per tenant, and conversation threads are
  `web_<tenant>_<session>`.
- **BYO keys**: tenants enter their own Gemini/Tavily/Tuya/… keys in the UI.
  Keys are envelope-encrypted (per-tenant DEK wrapped by `SECRETS_MASTER_KEY`)
  and fail closed. The platform provides only the Google OAuth client.
- **Modules**: chat (core), memory, web-search, google, nutrition, fitness,
  smart-home, tts — toggled per tenant; disabled modules remove tools from
  the agent and tabs from the UI. `scheduling` is owner-only for now.
- **Cloud-portable**: storage/secrets/DB sit behind backends selected by env
  (`VAULT_BACKEND=local|s3`, `SECRETS_BACKEND=local_pg|aws`, `DATABASE_URL`).
  Moving to AWS is a config swap (S3 + RDS + KMS), not a rewrite.

## Running locally

1. Create a Google OAuth client (Cloud Console → Credentials) with redirect
   URIs `http://localhost:8080/auth/callback` and
   `http://localhost:8080/auth/google/callback`.
2. `cp .env.product.example .env` and fill it in (see comments inside).
3. Build the SPA, then start the stack:

```bash
cd frontend && npm ci && npm run build && cd ..
docker compose -f docker-compose.product.yml up --build
```

Open http://localhost:8080 → Sign in with Google → onboarding asks for your
Gemini key (free at aistudio.google.com/apikey) → chat.

The first sign-in from `OWNER_EMAIL` (or the first ever sign-in when unset)
becomes the owner/admin, sees the Admin tab, and maps onto the legacy
single-user data scope — so the owner keeps their existing memory.

## CI/CD

`.github/workflows/deploy.yml` redeploys both assistants on every push to
`main`: the old assistant's `deploy` job runs first (sanity tests → rebuild →
health check), then `deploy-product` rebuilds the frontend (in a throwaway
Node container, no host Node.js required) and runs
`docker compose -f docker-compose.product.yml -p pa-product up --build -d`,
verified via `product-api`'s health check and an end-to-end `/health` request
through the gateway. It's a no-op until the product stack has been started at
least once on that host (detects `.env` + the `pa-product-api` container) —
safe to merge before you've opted in on a given machine.

## Dev mode

```bash
# API (needs DATABASE_URL etc. in the environment)
PYTHONPATH=backend uvicorn product.main:app --reload --port 8000
# SPA with hot reload (proxies /api,/auth,/ws to :8000)
cd frontend && npm run dev
```

## Key paths

| Area | Path |
|---|---|
| Product entrypoint | `product/main.py` |
| Engine seam | `backend/app/runtime.py`, `backend/app/context.py` |
| Tenancy + sessions | `product/tenancy/`, `product/auth/` |
| Secrets vault | `product/secrets/` (envelope encryption, catalog, validation) |
| Module registry | `product/modules/registry.py` ↔ `backend/app/graph/tools_registry.py` |
| Offboarding | `product/offboarding.py` (full per-tenant data deletion) |
| SPA | `frontend/src/` |
| Migrations | `product/migrations/NNNN_*.sql` (tracked in `schema_version`) |

## Security model

- Sessions: HMAC-signed cookie (HttpOnly, SameSite=Lax) + DB session row;
  revocation and tenant status are enforced on every request.
- CSRF: mutating requests require the `X-Requested-With: pa-app` header.
- Rate limits: per-tenant token buckets (chat + API).
- Tenant secrets: Fernet, envelope-encrypted, fail-closed, never returned
  in plaintext after save; `tenant_id` never passes through the LLM.
- Logs carry `tenant_id` on every line for auditability.
