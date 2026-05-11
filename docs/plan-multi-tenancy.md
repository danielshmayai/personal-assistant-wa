# Plan: Multi-Tenancy for danidin PA (Phases 1+2)

> Status: **Parked** — designed, not yet implemented. Come back when you have a real second user lined up.
> Last updated: 2026-05-11

## Context

Today `pa` is single-user. The user wants to invite additional members who:
- Use ONLY the web app (admin is the only WhatsApp user)
- Are added manually by the admin via a POST endpoint (no SMTP/email)
- Get fully isolated memory (facts/rules/vault), Google OAuth, conversations, scheduled jobs
- Cannot see, search, or affect any other member's data

There is also a **real security bug today** that this work fixes as a side effect:
`backend/app/google/auth.py:46` — `_token_key()` collapses all web sessions to the literal string `"web_user"`, meaning every web user with `TEST_TOKEN` inherits the admin's Google account. Fixing this is non-negotiable once multi-tenancy ships.

**Honest effort & risk:** L (not XL — admin-only WhatsApp removes WAHA-session complexity). 2–4 focused engineering days. Risk: medium-high because Phase 2 includes a one-shot Postgres + Obsidian filesystem migration that is painful to undo.

---

## Architecture

### Tenant model
- `tenant_id` = stable UUID-ish string, one per member.
- `chat_id` (existing) stays as conversation thread key; multiple `chat_id`s may map to one `tenant_id`.
- Admin tenant seeded from `MY_WHATSAPP_ID` on first boot.
- WhatsApp: single sender allowed (admin's `MY_WHATSAPP_ID`). Anything else logged and dropped.
- Web: each member gets a per-tenant bearer token. Web `chat_id` becomes `web_<tenant_id>_<session_uuid>` instead of the literal `"web"`.

### Tenant resolution seam
- `current_tenant_id: ContextVar[str | None]` set at the entry point (webhook handler / web WS handler) and propagated through the worker queue and graph state.
- Tools read `tenant_id` from state/ContextVar — **never** as an LLM argument (prompt-injection prevention).

---

## Phase 1 — Tenancy primitive + admin gating (1–2 days, reversible)

### Files to create
- `backend/app/tenancy/__init__.py`
- `backend/app/tenancy/store.py` — `tenants`, `tenant_chat_ids`, `tenant_tokens` tables + CRUD
- `backend/app/tenancy/resolver.py` — `resolve_tenant(chat_id) -> Tenant | None` with in-memory cache (cleared on bind/unbind)
- `backend/app/tenancy/context.py` — `current_tenant_id` ContextVar + `set_tenant` / `get_tenant` helpers
- `backend/app/routers/admin_members.py` — admin CRUD endpoints (gated by `TEST_TOKEN` for now)

### Files to edit
- `backend/app/main.py` — include new router, call `init_tenancy_tables()` in lifespan, seed admin tenant from `MY_WHATSAPP_ID`
- `backend/app/whatsapp.py` — replace `_is_self_chat()` logic with `resolve_tenant(sender)`; reject anything not bound to a tenant; set ContextVar before `enqueue`
- `backend/app/worker.py` — accept `tenant_id` in `_Msg`, set ContextVar inside `_process_one` before calling the graph
- `backend/app/routers/web_chat.py` — replace global `TEST_TOKEN` check with per-tenant token lookup; derive `chat_id = f"web_{tenant_id}_{session_uuid}"` instead of the literal `"web"`; set ContextVar before `stream_graph`
- `backend/app/google/auth.py` — kill `_token_key()`'s `"web_user"` collapse; key tokens by `(tenant_id, chat_id_or_'web')`
- `backend/app/config.py` — add `ADMIN_EMAIL` (optional, for record-keeping)
- `.env.example` — document tenancy intro; note that `MY_WHATSAPP_ID` is now the admin's seed only

### Admin endpoints (in `admin_members.py`)
```
POST /admin/members                      {display_name, email, whatsapp_id?} -> {tenant_id, web_token}
GET  /admin/members                      -> list tenants
DELETE /admin/members/{id}               -> revoke (soft-delete: status='revoked')
POST /admin/members/{id}/regenerate-token
```
All require `X-Test-Token: $TEST_TOKEN` header (existing pattern).

### Schema additions (Postgres)
```sql
CREATE TABLE tenants (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  email TEXT,
  status TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'revoked'
  is_admin BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE tenant_chat_ids (
  chat_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(id),
  kind TEXT NOT NULL  -- 'whatsapp' | 'web'
);
CREATE TABLE tenant_tokens (
  token_hash TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(id),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_used_at TIMESTAMPTZ
);
```

### Reused primitives (Phase 1)
- ContextVar pattern from `request_id_var` in `backend/app/worker.py`
- `tools_registry._tool_cache` keyed dict — extend cache key from `chat_id` to `(tenant_id, chat_id)` in Phase 2
- `crypto.encrypt/decrypt` for `tenant_tokens.token_hash` (or bcrypt-style hashing — simpler)
- `clear_tools_cache(chat_id)` in `google/auth.py` — extend to also take `tenant_id`
- `oauth_pending_states` nonce table pattern — replicate for invite tokens in Phase 3

### Reversibility
Phase 1 adds tables and refactors entry points but doesn't touch existing memory data. Roll back by dropping the 3 new tables + reverting code. Admin keeps working because `MY_WHATSAPP_ID` still resolves to the admin tenant.

---

## Phase 2 — Real data isolation (2 days, high-risk migration)

### Files to edit
- `backend/app/memory/store.py` — add `tenant_id TEXT NOT NULL` column (via `ALTER TABLE ... ADD COLUMN ... DEFAULT '<ADMIN_TENANT_ID>'`) to:
  `memory_facts`, `memory_rules`, `vault_embeddings`, `rule_embeddings`, `episodes`, `conversation_log`, `web_conversations`, `watched_leads`, `lead_messages`, `scheduled_jobs`, `job_queue`, `oauth_pending_states`
  — and change `google_tokens` primary key from `chat_id` to `(tenant_id, chat_id)`
- `backend/app/memory/obsidian.py` — `VAULT_ROOT` → `_vault_root(tenant_id)` returning `VAULT_ROOT_BASE / slug(tenant_id)`; every public function takes `tenant_id` or reads from ContextVar; traversal guard via `Path.resolve().relative_to(base)`
- `backend/app/memory/manager.py` — `MEMORY_TOOLS` → `get_memory_tools(tenant_id)` factory (same closure pattern as `get_google_tools(chat_id)` in `google/tools.py`)
- `backend/app/memory/embeddings.py` — `semantic_search()` filters by `tenant_id`
- `backend/app/memory/episodes.py` — `create_episode()` / `get_relevant_episodes()` filter by `tenant_id`
- `backend/app/memory/reflection.py` — captures `tenant_id` at enqueue time (not at execution time — ContextVar is gone by then)
- `backend/app/memory/self_review.py` — scopes to single tenant per run
- `backend/app/graph/state.py` — add `tenant_id: str = ""` to `PAState`
- `backend/app/graph/graph.py` — `inject_memory_node` reads tenant from state, threads through `load_memory_context(tenant_id, query)`
- `backend/app/graph/tools_registry.py` — cache key → `(tenant_id, chat_id)`; `_build()` calls `get_memory_tools(tenant_id)`
- `backend/app/scheduled_jobs.py` — `get_due_jobs` filters by `tenant_id`; `_notify` scopes to tenant
- `backend/app/schedule_tool.py` — `insert_job` writes `tenant_id`
- `backend/app/job_queue.py` — persist `tenant_id`
- `backend/app/leads.py` — all queries filter by `tenant_id`

### Data migration steps
1. `docker compose stop backend`
2. `pg_dump > backup.sql` + `tar -czf vault-backup.tgz $HOST_OBSIDIAN_VAULT`
3. Run migration script:
   - `ALTER TABLE` adds `tenant_id` columns defaulting to the admin tenant id
   - `mv $vault/People $vault/<admin_id>/People` etc. for all top-level categories
4. Restart, verify `/admin/members` shows admin tenant with all data intact
5. Keep symlinks at old vault paths for one release as a safety net

### Tool safety invariant (hard rule going forward)
> Tools **never** accept `tenant_id` as an LLM-callable parameter. The factory closes over it. Any tool signature with `tenant_id` as a parameter is a code-review reject.

---

## Risks

1. **Phase 2 DB+filesystem migration is hard to undo.** Take a backup first. This is the dangerous phase.
2. **Fire-and-forget background tasks** (reflection, episodes) must capture `tenant_id` at enqueue time — the ContextVar is gone when they execute. Every `asyncio.create_task(...)` call site must be audited.
3. **pgvector indexes** on `vault_embeddings` / `rule_embeddings` may need recreating after adding `tenant_id` to the indexed columns.
4. **Per-tenant fairness** — global `TOOL_CONCURRENCY` semaphore means a noisy tenant can starve others. Fine for ≤5 tenants; revisit if it grows.
5. **`web_user` Google token bug is live today.** Any web user with `TEST_TOKEN` can access admin's Google account until Phase 1 ships.
6. **OAuth state table** needs `tenant_id` so the callback knows which tenant initiated the auth flow.

---

## Verification

### Phase 1
- `pytest backend/tests/test_sanity.py -v` stays green
- `POST /admin/members` returns a token → web WebSocket authenticates → `chat_id` is `web_<id>_<uuid>`, not `"web"`
- WhatsApp message from non-admin number is logged and dropped
- New tenant connects Google OAuth → a separate `google_tokens` row is created (admin's row untouched)

### Phase 2
- `backend/tests/test_isolation.py` — two tenants save the same fact; each `load_memory_context` returns only its own; vault dirs are separate; `semantic_search` for tenant A never returns tenant B's content
- `backend/tests/test_oauth_isolation.py` — tenant A's Google token does not appear in tenant B's `gmail_read`
- Grep structured logs for `tenant_id=None` outside admin endpoints — should be zero

### Post-merge (each phase)
- Run `/review-auth` — new auth model + Google token fix
- Run `/harden-assistant` — new admin router + per-tenant tokens
- Run `/update-architecture` — 12+ schema additions, new `tenancy/` module

---

## Sequencing recommendation

Ship Phase 1 as its own PR. It's reversible, closes the live `web_user` security bug, and validates the tenant primitive in production. Live with it for a few days. Only start Phase 2 once you have a concrete second user lined up — it's the expensive, hard-to-undo phase.

**Do not combine Phase 1 + Phase 2 in one PR.**

---

## Phase 3 (future, not designed yet)

If you eventually want email-based invite flow:
- `backend/app/email/smtp.py` — SMTP send helper
- `backend/app/tenancy/invites.py` — nonce-keyed tokens with TTL
- `backend/app/routers/invite.py` — claim page + admin approval step
- Needs: SMTP credentials in `.env`, a public `/invite/*` route
