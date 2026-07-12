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
- [x] **P4 Fitness dashboard** (React) — `Fitness.tsx` (rings, daily-rec, log text/image, body metrics + InBody scan, progression sparkline, Garmin card), tenant Garmin connect/MFA/disconnect/wellness/push-workout (`product/routers/garmin.py`), exercise-GIF proxy. + **critical fix**: tenant-scoped `garmin_wellness` (was a shared global table) and fixed 2 unscoped SQL queries in `garmin/sync.py` that could merge one tenant's Garmin activity into another's workout row. + **critical PII fix**: 5 hardcoded owner-medical prompt fragments in `fitness.py` (independent of `render_profile_block`'s P0 gate) leaked "Gilbert's Syndrome"/neck-injury text into a tenant's AI workout evaluation — caught live, all 5 sites gated, 5 regression tests added.
- [x] **P5 Home dashboard** content — `Home.tsx` at `/` (weather, calendar, proactive cards, owner-only jobs section); `product/routers/dashboard.py` tenant-scoped (`/api/today`, `/api/calendar-today` open to all tenants; `/api/jobs*` gated by the owner-only `scheduling` module; `/api/proactive-cards*` open but per-tenant scoped). Chat moved to `/chat`.
- [x] **P6.1 Smart Home UI** — `SmartHome.tsx` (device list, expand for status + on/off + raw JSON); `product/routers/smart_home.py` wraps the already-tenant-scoped `app.tuya.tools` (P2) with `require_module("smart-home")`.
- [x] **P6.2 Memory browser** — `Memory.tsx` (browse/rules/search tabs, add/hide fact & rule); `product/routers/memory.py`. **Found + fixed a latent leak**: the old `memory_api.py` did `from app.memory.obsidian import VAULT_ROOT` — a PEP-562 dynamic attribute captured ONCE at process-import time (outside any tenant's request context) and frozen forever; porting that pattern naively would have pointed every tenant at the owner's vault. New router calls `obsidian.vault_root()` as a function inside every handler instead.
- [x] **P6.3 Drive image proxy** — `product/routers/drive_proxy.py`, session-cookie auth (no query-string token needed — the browser sends the cookie automatically on same-origin `<img>` loads); `get_credentials("web")` resolves per-tenant via the ContextVar, and Drive's own API naturally scopes file access to whichever account's token is used.
- [x] **P6.4 Voice (STT/TTS)** — `product/routers/voice.py`; STT (local Whisper, no creds) is core, TTS is gated by the `tts` module. **Found + fixed another critical leak**: `web_chat.py` imported `GOOGLE_TTS_API_KEY` from `app.config` at module load — the same anti-pattern as the pre-P2 Tuya/Tavily bugs. Extracted STT/TTS into a shared `backend/app/voice.py` (used by both the owner router and the new product router) and fixed the key resolution to `runtime.get_secret()` per call. `Chat.tsx` gained a mic button (record → `/api/stt` → fills the input) and a voice-replies toggle (`/api/tts` playback on `done`, only shown when the `tts` module is enabled).
- [ ] **P6.5+** Push/PWA · Schedule/Activity/Automations · proactive flows per tenant · per-tenant WhatsApp channel (WAHA multi-session) · Leads · self-review

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

## P4 — Fitness dashboard (React) (done)
- `frontend/src/pages/Fitness.tsx` (route `/fitness`); mirrors Nutrition's structure (Ring, `withBusy`, gallery/camera refs). Today tab: volume/duration rings, daily-rec card (readiness badge, key exercises), log workout (text/image), Garmin card (connect/MFA/disconnect/status), today's sessions with delete. History tab: day list + exercise-progression sparkline (hand-rolled SVG — no chart lib in this project). Body tab: latest snapshot, manual entry (weight/lbm/smm/bf) + InBody scan image upload, history with delete. Structured (manual sets/reps builder) UI deferred — text/image logging covers the primary flow, same simplification P1 made for Nutrition.
- `product/routers/garmin.py` (new): tenant-scoped connect/mfa/disconnect/status/wellness/push-workout, mirroring the old owner router but reading `GARMIN_EMAIL`/`GARMIN_PASSWORD` from the tenant's own secrets catalog entry instead of a request body. `fitness._garmin_allowed()` now checks `gclient.is_connected()` for tenant scope (owner always `True`) instead of a hard `not current_tenant_id.get()`.
- Exercise-GIF proxy ported into `product/routers/fitness.py` (`GET /api/fitness/exercise-gif`) — pure static-asset lookup, no tenant scope needed.
- **Critical fix found live**: `garmin/sync.py`'s `_existing_garmin_ids`/`_find_merge_candidate` queried `fitness_workouts` with no `chat_id` filter — a tenant's Garmin activity sync could merge into another tenant's or the owner's unrelated workout row sharing the same date. `merge_garmin_metrics` (fitness.py) had the same gap. `garmin_wellness` had no tenant column at all (`log_date` was the sole PK) — two scopes syncing the same day would silently overwrite each other's sleep/HR/steps. Fixed: `garmin_wellness` re-keyed to `(tenant_id, log_date)` (same idempotent migration pattern as `garmin_tokens`); all three queries scoped.
- **Critical PII fix found live**: logged a tenant workout via the rebuilt product stack and its AI evaluation surfaced "תסמונת גילברט" (Gilbert's Syndrome — the owner's medical condition, gated in `fitness_profile.py` since P0). Root cause: `fitness.py` has 5 *separate* hardcoded prompt fragments (`_EVALUATE_INSTRUCTIONS`, `_EVALUATE_BODY_INSTRUCTIONS`, `suggest_workout`'s MANDATORY CONSTRAINTS, `generate_morning_brief`'s reminders, `generate_daily_recommendation`'s key-exercise rule) that instruct the LLM to reference Gilbert's-Syndrome hydration and neck-injury exercise restrictions — all independent of and bypassing `render_profile_block`'s gate. All 5 converted to scope-conditional (owner text unchanged; tenants get generic hydration/form cues, no owner injury constraints — which would also just be wrong advice for someone without that injury). 5 regression tests added to `test_tenant_isolation.py`; live-reverified with the exact same call that leaked — clean.

## P5 — Home dashboard content (done)
- `product/routers/dashboard.py` (new): reimplemented from the old TEST_TOKEN-gated `dashboard.py`, not reused as-is. `/api/today` + `/api/calendar-today` are `require_session`-only (core, every tenant); `/api/jobs` + `DELETE /api/jobs/{id}` are `require_module("scheduling")` — the registry already marks `scheduling` `owner_only=True`, so tenants get a clean 403 with no extra gating logic needed. `/api/proactive-cards*` is `require_session`-only but empty for tenants today since the flows that populate cards (`proactive/flows.py`) only run in the owner entrypoint (→ P6+).
- **Scoping subtlety, not a schema change**: `scheduled_jobs.py`'s job/card/activity helpers have no ContextVar awareness — they trust the `chat_id` argument verbatim as the DB key (unlike `_fitness_key`, which ignores its argument). Passing `""`/`"web"` for every tenant (the nutrition/fitness vestigial pattern) would have collided every tenant's proactive cards into one shared row. Fixed by constructing a per-tenant key at the call site instead: `_dashboard_chat_id(tenant)` = `f"web_{tenant.engine_scope}"` for tenants, literal `"web"` for the owner (preserves the old PWA's existing `"web"`-keyed rows exactly). Calendar's `get_credentials("web")` didn't need this — `_token_key` only checks the `"web"` prefix and resolves the real key from the ContextVar (P2), so the literal suffix is irrelevant there.
- `Home.tsx` (`/`): weather + owner-only jobs-count strip, proactive cards with dismiss, today's calendar (or a "connect Google" prompt), owner-only jobs list with cancel, a Chat CTA. `Dashboard.tsx` nav: `/` is now Home, Chat moved to `/chat`.

## P6.1-P6.4 — Smart Home UI, Memory browser, Drive proxy, Voice (done)
- **Smart Home** (`SmartHome.tsx` + `product/routers/smart_home.py`): thin wrapper over `app.tuya.tools`, already fully tenant-scoped since P2 — no new scoping work needed, only the UI + `require_module` gate.
- **Memory browser** (`Memory.tsx` + `product/routers/memory.py`): see Decisions log — found the `VAULT_ROOT` frozen-import bug before it could ship.
- **Drive image proxy** (`product/routers/drive_proxy.py`): session-cookie auth instead of the old query-string TEST_TOKEN; wrapped the sync `drive_api.download_file` in `asyncio.to_thread` since blocking the event loop matters more with concurrent tenants than it did for the single-user owner.
- **Voice** (`backend/app/voice.py` + `product/routers/voice.py` + `Chat.tsx` mic/speaker UI): see Decisions log for the `GOOGLE_TTS_API_KEY` fix — same class of bug as pre-P2 Tuya/Tavily.

## P6.5+ (later, one at a time)
Schedule/Activity/Automations (needs per-tenant scheduler + delivery channel — the APScheduler-less poll loop in `scheduled_jobs.py` only runs in the owner entrypoint); push/PWA; per-tenant proactive flows (the flow engine in `proactive/flows.py` is owner-only, same constraint as scheduling); **per-tenant WhatsApp channel** (WAHA multi-session — one WA session per tenant, webhook resolves session→tenant scope); **per-tenant Leads**; **per-tenant self-review**.

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
- (P4) `garmin_tokens`/`garmin_wellness` use `''` for the owner scope (matches `runtime.tenant_id()` directly), which is a **different convention** from `nutrition_logs`/`fitness_workouts` (`chat_id` column, owner = `'default'` via `_fitness_key`'s `or "default"` fallback). Any new Garmin-adjacent code must resolve scope via `runtime.tenant_id()`/`gclient._scope()`, not `_fitness_key()` — mixing the two silently mismatches owner rows.
- (P4) Structured/manual sets-reps workout builder UI deferred — text/image logging (AI-parsed) is the primary flow and matches P1's Nutrition simplification; the backend `POST /log-structured` route already exists for a future pass.
- (P4 process note) this pattern of hardcoded-instruction PII leaks living outside the profile-gate function is the kind of bug `render_profile_block`-only auditing misses — worth a targeted grep sweep (`Gilbert|scapular|neck|GERD`) across `fitness.py`/`nutrition.py`/`distiller.py` before any future prompt-touching change ships.
- (P4 verified) full offline suite (28 tenant-isolation + garmin + sanity, 1 pre-existing unrelated failure) green; live: tenant fitness/body-metrics/exercise-gif routes 200; tenant Garmin connect without stored creds → 400 with a clear message; `gclient.is_connected()` False for tenant / True for owner in the same process; `garmin_wellness` migration preserved the owner's row (`tenant_id=''`) and `GET /api/garmin/status` (owner, :8000) still `connected:true` after migration; re-ran the exact leaking call post-fix — zero leak terms, tenant's daily-rec now suggests squats (no longer blocked by the owner's neck constraint), owner's daily-rec unchanged (still scapular-safe + liver-hydration phrasing).
- (P5) `_dashboard_chat_id(tenant)` pattern — when a data-layer function takes `chat_id` as a literal, ContextVar-blind scope key (true of `scheduled_jobs.py`, unlike `_fitness_key`/`_token_key`), the product router must construct a per-tenant key at the call site rather than passing a shared literal; check which pattern applies (`grep current_tenant_id` in the target module) before wiring any future data layer into the product.
- (P5 verified) grep sweep of `nutrition.py`/`distiller.py`/`fitness_tool.py`/`image_tools.py`/`tools_registry.py` for the same hardcoded-instruction PII pattern found in P4 — clean, only the already-gated P2 `_OWNER_FRAGMENTS` hits. Live: tenant `/api/jobs` → 403 `module_disabled`; tenant `/api/today`/`/api/calendar-today`/`/api/proactive-cards` → 200, `jobs_count:0`; tenant created/read/deleted its own proactive card with no bleed to/from the owner's `"web"`-keyed rows (owner's old :8000 routes independently verified unaffected, both empty as expected — no pre-existing jobs/cards on this dev box).
- (P6.2) `obsidian.py` exposes `VAULT_ROOT`/`RULES_FILE` as PEP-562 module `__getattr__` shims for legacy importers (`memory_api.py`, `capabilities.py`, `self_review.py`) that predate the tenant-aware `vault_root()` function. `from app.memory.obsidian import VAULT_ROOT` evaluates `__getattr__` **once** at import time (outside any request's tenant context) and binds the result as a plain local constant forever — every subsequent call inside that module uses that frozen path regardless of which tenant is asking. Not a live bug for `memory_api.py`/`capabilities.py` today (both owner-only, :8000-only, so the frozen value always equals the owner's own path) — but it is exactly the shape of bug that would silently point every tenant at the owner's vault the moment either file got reused for the product without this fix. Rule going forward: never `from app.memory.obsidian import VAULT_ROOT` in tenant-reachable code — always call `obsidian.vault_root()`.
- (P6.3) `drive_show_image` (chat tool, `google/drive_tools.py`) still builds `f"/api/drive/proxy/{file_id}?chat_id={chat_id}&token={TEST_TOKEN}"` — stale-looking but harmless for the product: the new route only declares `file_id` + a cookie dependency, so FastAPI ignores the extra undeclared query params and the session cookie (sent automatically on same-origin `<img>` loads) does the real auth. Left as-is rather than editing shared owner-facing tool code for a cosmetic URL cleanup — the owner's :8000 route still uses `token` for real auth and must keep working unchanged.
- (P6.4) **Critical fix found live**: `web_chat.py`'s TTS route did `from app.config import GOOGLE_TTS_API_KEY` — a module-level env import, same anti-pattern as the pre-P2 Tuya/Tavily bugs. Since TTS wasn't ported to the product until this step, it was latent (owner-only, :8000-only) rather than actively exploited — but wiring it into `product/routers/voice.py` without fixing it would have silently billed/used the owner's Google TTS key for every tenant with the `tts` module enabled. Fixed by extracting STT+TTS into `backend/app/voice.py` (shared by both the owner router and the new product route) and resolving the key via `runtime.get_secret("GOOGLE_TTS_API_KEY")` per call — tenants without their own key fall back to the free edge-tts engine, never the owner's paid key. 3 regression tests added (`TestVoiceTenantIsolation`) mirroring the P2 Tavily test shape.
- (P6.4 verified) full offline suite (31 tenant-isolation + garmin + sanity, 1 pre-existing unrelated failure) green. Live on the rebuilt stacks: tenant Smart Home `/api/devices` → 503 "not configured" (no owner-device leak); tenant Memory fact created/searched/hidden — confirmed on disk at `/data/vaults/tenants/<id>/Misc/...`, never touching the owner's vault; tenant Drive proxy → 403 `google_not_connected`; tenant `/api/tts` → 200, 7632 bytes via edge-tts fallback (no Google key configured for this tenant); owner's `/api/tts` (:8000, env key) still 200 after the `voice.py` extraction — byte-for-byte same code path, just relocated.
