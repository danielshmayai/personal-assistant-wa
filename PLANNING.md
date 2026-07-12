# Product parity plan — bring the old assistant's features to the multi-tenant product

> **Living document.** This is the single source of truth for the product-parity
> effort. It is committed to the repo so it travels to any machine (`git pull`)
> and a remote session can resume from here. **After every step: tick its box,
> add a Decisions-log line if anything non-obvious was decided, and commit this
> file in the same commit as the code.**

## Context

Two assistants share one engine (`backend/app/`): the **old** single-user WhatsApp +
6,237-line PWA dashboard (`backend/app/static/index.html`, `:8000`), and the **new**
multi-tenant React product (`frontend/` + `product/`, `:8443` via Tailscale Funnel).
The new product is an MVP — Chat, Settings, Admin, onboarding work, but the module
pages (`frontend/src/pages/ModulePlaceholder.tsx`) are stubs: no Home dashboard, no
Nutrition/Fitness dashboards, no image upload.

Key finding: the backend data APIs for nutrition/fitness already exist
(`backend/app/routers/nutrition.py`, `fitness.py`) — they're just not wired into the
product and are hardcoded single-user (`_nutrition_key`/`_fitness_key` ignore
`chat_id`, return `"default"`). The foundation makes the data layer tenant-aware,
unblocking every dashboard.

## Full gap map (old → new)

| Feature (old) | New status | Backend data API |
|---|---|---|
| Home dashboard (rings, calendar, jobs, proactive cards) | ❌ shell only (`Dashboard.tsx`) | partial (`dashboard.py`) |
| Nutrition dashboard | ❌ placeholder | ✅ `routers/nutrition.py` |
| Fitness dashboard | ❌ placeholder | ✅ `routers/fitness.py` |
| Image/file upload in Chat | ❌ text-only (`Chat.tsx`) | ✅ old `/api/upload` + media_cache |
| Smart Home | ❌ placeholder | ✅ `routers/smart_home.py` |
| Memory browser | ❌ none | ✅ `routers/memory_api.py` |
| Schedule / Activity / Automations / Proactive cards | ❌ none | ✅ `routers/dashboard.py` |
| Voice (STT/TTS), Push, PWA | ❌ none | ✅ old endpoints exist |
| Agent tools — Google (Gmail/Calendar/Drive/Maps) | ⚠️ tools shared via engine; per-tenant Google connect works | ✅ `google/tools.py`, `drive_tools.py`, `maps_tool.py` |
| Agent tools — web (search/wiki/fetch/weather) | ✅ per-tenant Tavily key (P2) | ✅ `web/tools.py` |
| Agent tools — vault notes / image vision / TTS config | ✅ tenant-scoped (vault P0, app_settings P2) | ✅ `memory/manager.py`, `image_tools.py`, `tts_tool.py` |
| Reminders & scheduled jobs (tools + runtime) | ❌ owner-only (`scheduling` module) | ✅ `schedule_tool.py`, `scheduled_jobs.py` |
| Garmin (connect/MFA/wellness/push-workout) | ⚠️ token store tenant-keyed (P2); connect UI → P4 | ✅ `garmin/` |
| InBody scan · exercise-GIF proxy | ⚠️ scan API ported (P0); GIF proxy → P4 | ✅ `routers/fitness.py` |
| Drive image proxy | ❌ none | ✅ `routers/drive_proxy.py` |
| Proactive flows generator (morning brief, Garmin sync) | ❌ owner-only | ✅ `proactive/flows.py` |
| WhatsApp channel (per-tenant number) | ❌ owner-only → P6+ (WAHA multi-session) | ✅ `whatsapp.py` + WAHA |
| Leads (watched contacts) | ❌ owner-only → P6+ | ✅ `routers/leads.py` |
| Self-review | ❌ owner-only → P6+ | ✅ `/admin/self-review` |

## Progress checklist

- [x] **P0 Foundation** — tenant-scope nutrition/fitness data + expose to product (+ 2 safety fixes)
  - [x] Step 0 — remove destructive init migrations + gate owner PII
  - [x] Step 1 — tenant-scope `_nutrition_key` / `_fitness_key` (+ Garmin wellness gated for tenants)
  - [x] Step 2 — `product/routers/nutrition.py` + `fitness.py` (session-auth, reuse data funcs)
  - [x] Step 3 — `require_module` gate + wire routers/init into `product/main.py`
  - [x] Verify — owner vs tenant isolation, module gating, PII gate ✅ (all passed)
- [x] **P1 Nutrition dashboard** (React) — `frontend/src/pages/Nutrition.tsx`, route wired; verified log/water/delete/history end-to-end
- [x] **P2 Separation & privacy hardening** — full per-tenant BYO creds + close every audited leak path
  - [x] Step 0 — `runtime.get_secret` fails closed for tenants on resolver error
  - [x] Step 1 — Tuya full BYO (per-tenant client cache; LAN control owner-only; `clear_tuya_cache` on secret change)
  - [x] Step 2 — Tavily key resolved per tenant (DDG fallback when absent)
  - [x] Step 3 — tenant-neutral system prompt (owner prompt byte-identical; no danidin/110g/medical framing for tenants)
  - [x] Step 4 — media_cache scope check on retrieve + `purge_scope`
  - [x] Step 5 — `app_settings` tenant-scoped (PK `(tenant_id, key)`; TTS config per tenant)
  - [x] Step 6 — `garmin_tokens` tenant-keyed (PK `tenant_id`; per-tenant client/MFA state; connect UI → P4)
  - [x] Step 7 — offboarding also deletes nutrition/fitness rows, app_settings, garmin_tokens + media purge (savepoint per table)
  - [x] Step 8 — WhatsApp body logging gated behind `LOG_MESSAGE_CONTENT` (default off)
  - [x] Verify — `test_tenant_isolation.py` 23/23 ✅, no regressions vs baseline; live two-cookie smoke ✅
- [x] **P3 Image upload in Chat** — `POST /api/upload` in product (`chat.py`), tenant-scoped via `require_session`; WS accepts `media_id`, builds `[MEDIA...]` tag before `stream_graph`; `Chat.tsx` attach/camera buttons + preview chip. Verified live end-to-end.
- [ ] **P4 Fitness dashboard** (React) — incl. tenant Garmin connect UI + exercise-GIF proxy
- [ ] **P5 Home dashboard** content
- [ ] **P6+** Smart Home UI · Memory browser · Schedule/Activity/Automations · Voice · Push/PWA · Drive proxy · proactive flows per tenant · per-tenant WhatsApp channel (WAHA multi-session) · Leads · self-review

---

## P0 — Foundation (backend only) — BUILD FIRST

Invariant (mirrors memory's `_tid()` but with a fallback, since the owner's historical
rows are under the literal `"default"` while owner `engine_scope=""`):

```python
key = current_tenant_id.get() or "default"
```

Owner → `"default"` (existing data preserved; WhatsApp unchanged). Tenant → their id (isolated).

**Step 0 — two safety fixes FIRST (before any wiring):**
- Remove destructive startup migrations: `nutrition.py` `init_table` `UPDATE ... SET chat_id='default'` and the two equivalents in `fitness.py`. They rewrite ALL rows to `"default"` every boot → would destroy tenant data once `product/main.py` calls `init_table()`. Keep `CREATE TABLE`/`CREATE INDEX`/`ADD COLUMN IF NOT EXISTS`.
- Gate owner PII: `fitness_profile.py` `render_profile_block` returns `""` when `current_tenant_id.get()` is truthy — the owner's `CLINICAL_CONSTRAINTS`/`PHYSIO_BASELINE` (Gilbert's, GERD, HbA1c, age, neck pain) must never enter another tenant's prompt.

**Step 1 — scope the data layer:** rewrite `_nutrition_key` and `_fitness_key` to `return current_tenant_id.get() or "default"`. All reads/writes already funnel through them — no call-site changes.

**Step 2 — product routers (thin, reuse shared funcs):** new `product/routers/nutrition.py` (`prefix="/api/nutrition"`) and `product/routers/fitness.py` (`prefix="/api/fitness"`). Each route `Depends(require_module(...))`; call existing data functions passing `""` for the vestigial `chat_id`. Drop Garmin `push_hydration` from water; short-circuit Garmin wellness overlay when scope non-empty. Model on old `backend/app/routers/*` + session pattern in `product/routers/modules.py`.

**Step 3 — gate + wire:** add `require_module(module_id)` to `product/deps.py` (composes `require_session`; 403 `module_disabled` if not in `get_enabled_modules`). In `product/main.py`: include the two routers before the SPA mount; in lifespan (after `init_memory_tables()`) call `app.nutrition.init_table()` + `app.fitness.init_table()` (now non-destructive, idempotent).

**Step 4 — nginx:** none — `location ~ ^/(api|auth|health)` already proxies `/api/nutrition/*` and `/api/fitness/*`.

**Verify (no UI):** mint a session cookie (`create_session` + `product.auth.sessions.issue_token`; mutating calls need `x-requested-with: pa-app`). (a) owner GET today → existing `"default"` data; (b) tenant GET → empty, POST log-water → row with `chat_id=<uuid>`, owner unaffected; (c) disable nutrition module → 403; (d) `list_today("")` no ContextVar → `"default"`; (e) `init_table()` twice → no collapse; (f) tenant scope → `render_profile_block()` returns no owner PII.

## P1 — Nutrition dashboard (React)
- New `frontend/src/pages/Nutrition.tsx`; route `/nutrition` → it (replace `ModulePlaceholder` in `App.tsx`).
- Rings (protein/carbs/calories); Log-a-meal (text + Gallery/Camera file inputs); water quick-add; today's meals list (delete); micros chips; Today/History tabs. Consumes `/api/nutrition/*`. Reuse `ui.tsx`; lift SVG-ring pattern from old `static/index.html`.

## P2 — Separation & privacy hardening (done)

Origin: a full-code privacy audit (2026-07-12) verified all P0/P1 claims and found 8
leak paths the plan didn't cover. All closed. Shared pattern: per-call
`runtime.get_secret()` + per-tenant client cache keyed by tenant id + cred
fingerprint (mirrors `llm.py`); `clear_*_cache` wired into `runtime.on_secrets_changed`.
Tenants fail closed (no env fallback); owner keeps env. Tests:
`backend/tests/test_tenant_isolation.py`.

## P3 — Image upload in Chat (done)
- Backend: `POST /api/upload` in `product/routers/chat.py`, `Depends(require_session)` (no module gate — chat itself isn't gated). Reuses `media_cache.store_web_upload`, 20MB cap, `web_<uuid4hex>` id. WS handler reads `media_id` from the incoming payload, moves `current_tenant_id.set(scope)` before the media lookup (retrieve() scope-checks), builds the same `[MEDIA id=... type=image|document filename=... mime=...]` tag the old `web_chat.py` uses, prepends it to the text before `stream_graph`.
- Frontend `Chat.tsx`: gallery/camera buttons upload immediately via `api.upload("/api/upload", file)`, show a preview chip (thumbnail + filename + ✕, object-URL revoked on clear/unmount), `send()` includes `media_id` in the WS JSON and clears the chip.

## P4 — Fitness dashboard (React)
- New `frontend/src/pages/Fitness.tsx` (route `/fitness`); mirrors Nutrition. Rings (volume/duration); log text/image/structured; body metrics (manual + InBody scan); progression + body-composition charts; suggest; today's sessions. Consumes `/api/fitness/*`.
- Tenant Garmin connect UI: routes calling `begin_login`/`finish_mfa` with `GARMIN_EMAIL`/`GARMIN_PASSWORD` from the secrets catalog (token store already tenant-keyed in P2); then lift `_garmin_allowed()` to allow tenants with their own connection. Exercise-GIF proxy.

## P5 — Home dashboard content
- Wire tenant-scoped `dashboard.py` routes into product (`/api/today`, `/api/calendar-today`, `/api/jobs`, `/api/proactive-cards`). Scheduled-jobs are owner-only today — gate. `Dashboard.tsx`: real Home view instead of routing `/` straight to Chat.

## P6+ (later, one at a time)
Smart Home UI (`smart_home.py`; per-tenant Tuya creds work end-to-end since P2); Memory browser (`memory_api.py`, tenant vault already isolated); Schedule/Activity/Automations (needs per-tenant scheduler + delivery channel); voice STT/TTS; push/PWA; Drive image proxy; per-tenant proactive flows; **per-tenant WhatsApp channel** (WAHA multi-session — one WA session per tenant, webhook resolves session→tenant scope); **per-tenant Leads**; **per-tenant self-review**.

## Risks
- **Destructive init UPDATEs** (critical) — ✅ removed in P0.
- **fitness_profile PII leak** (high) — ✅ gated in P0.
- **Garmin/Tuya/Tavily owner-global creds** (critical/medium) — ✅ closed in P2: per-tenant BYO via `runtime.get_secret`, fail-closed, LAN owner-only.
- **ContextVar-not-set = owner scope** (by design) — every new route goes through `require_module` → `require_session`; never call data funcs bare. `runtime.get_secret` now also fails closed for tenants if the resolver errors.
- **Remaining shared surfaces** (tracked): `garmin_wellness` table is owner-global (reads gated) until P4; dashboard.py routes are owner-only and must never be co-mounted into the product as-is (P5 re-wraps them).

## Decisions log
- (P0) scope key = `current_tenant_id.get() or "default"` — preserves owner's `"default"` rows, isolates tenants.
- (P0) Garmin wellness overlay gated by `_garmin_allowed()` in `fitness.py` (owner-only single account) — both fetch sites in `generate_daily_recommendation`.
- (P0) `exercise-gif` endpoint deferred to P3 (fitness UI) — it's a static-asset GIF proxy with no tenant scope, not needed for the data foundation.
- (P0) Product routers are thin wrappers over `app.nutrition`/`app.fitness`, passing `""` for the now-vestigial `chat_id`; auth via `require_module()` (composes `require_session`).
- (P0 verified) owner `default` rows unaffected by tenant writes; tenant isolated; module-disabled → 403; tenant fitness prompt carries no owner PII.
- (P1) Added `api.upload()` (multipart) to `frontend/src/lib/api.ts` for meal photos; `Nutrition.tsx` reuses `ui.tsx` primitives + an inline SVG `Ring`. Route `/nutrition` now renders the real page (was `ModulePlaceholder`).
- (P1 verified) tenant log-text "2 eggs and a coffee" → Gemini parsed 12g P / 155 kcal; today/water/history/delete all 200 and update correctly. Image upload path shares the same insert; not driven with a real file.
- (P2 audit, 2026-07-12) read-only verification confirmed every P0/P1 claim in code; privacy audit found 8 leak paths (worst: Tuya tools read owner env creds — a tenant with smart-home enabled on a shared-env box could control the owner's home). Full gap map extended with agent-tool rows + owner-only features.
- (P2) BYO credential pattern = per-call `runtime.get_secret` + per-tenant client cache keyed by `(tenant_id, cred-fingerprint)` — copied from `llm.py`. Never import creds from `app.config` at module load in tenant-reachable code.
- (P2) `runtime.get_secret` exception path now returns `""` for tenants (was: env fallback = owner's keys on a DB blip).
- (P2) Tuya LAN control (`TUYA_PREFER_LOCAL`) gated owner-only — the server's LAN is the owner's network.
- (P2) Garmin connect UI deferred to P4; `garmin_tokens` re-keyed to `tenant_id` (legacy `id=1` row becomes owner row `''`); `_garmin_allowed()` stays as the read gate until P4.
- (P2) Offboarding deletes run in per-table SAVEPOINTs — one missing table previously aborted the whole transaction and Postgres silently rolled back *all* deletions at COMMIT.
- (P2) `LOG_MESSAGE_CONTENT` (default **off**) — the only owner-visible change: WhatsApp log lines show `<N chars>` instead of message text unless enabled in `.env`.
- (P2) Test gotcha: module-isolation fixtures must also wipe the `app` package root from `sys.modules`, otherwise `from app import X` returns a stale module with a *different* ContextVar instance and scope tests silently test nothing.
- (P3) `_download_from_waha` (in `google/drive_tools.py`) is the generic media resolver despite its name — it checks `media_cache.retrieve()` first and only falls back to the WAHA REST API on a cache miss, so it transparently works for web-sourced `web_<hex>` media_ids with no changes needed.
- (P3 verified) real HTTP upload + real WS round trip against the live rebuilt product stack: uploaded a 1×1 red-pixel PNG, sent `media_id` over `/ws/chat`, agent called `analyze_image` with the matching `message_id`, tool succeeded, model replied "אדום" (red) — proves `[MEDIA]` tag construction, tenant-scoped cache retrieval, and vision analysis all work end-to-end.
