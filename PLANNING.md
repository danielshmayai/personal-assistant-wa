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
- [x] **P6.5 Push/PWA** — `frontend/public/manifest.json` + `sw.js` (adapted from the owner's, icons reused) + SW registration in `main.tsx`; `product/routers/push.py` (`vapid-public-key`, `subscribe`, tenant-scoped via a `_dashboard_chat_id`-style key). Delivery has no caller in the product process yet (see P6.6) — subscribing is still correctly scoped and ready for when that lands.
- [~] **P6.6 Schedule/Activity/Automations + real Push delivery** — assessed, not built. Found the real blocker before writing code: `NotificationManager` is a process-local singleton (owner's `backend` and `product-api` are separate containers), the product's per-conversation chat_id shape doesn't match the stable key P5/P6.5 already use for jobs/cards/push, and `_run_job` never sets `current_tenant_id` before executing (would silently use the owner's Tuya creds for a tenant's scheduled command — same bug class as P4's Garmin fix). Full fix path documented below; deliberately not implemented this session — it's a cross-cutting redesign, not additive work, and rushing it risks reproducing the exact leaks this whole effort has been closing.
- [~] **P6.7 Per-tenant WhatsApp / Leads / self-review** — assessed, not built. WhatsApp needs WAHA multi-session provisioning + webhook session→tenant resolution (infrastructure work, touches `docker-compose.yml`); Leads depends on it; self-review is a small follow-up once P6.6's per-tenant scheduler exists. Details below.

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

## P6.5 — Push/PWA (done)
- `frontend/public/manifest.json` + `sw.js`: adapted from the owner's `backend/app/static/` versions (same app-shell caching strategy, same push/notificationclick handlers kept ready but currently unused). Icons copied from the existing owner assets (`icon-180/192/512.png`) rather than generated fresh. Registered via `frontend/src/lib/pwa.ts` → `main.tsx`.
- `product/routers/push.py`: `GET /api/push/vapid-public-key`, `POST /api/push/subscribe`. VAPID keys identify the *server* (one shared identity is correct, standard Web Push architecture — not a tenant-privacy concern). Subscriptions themselves use the same `_dashboard_chat_id`-shaped fix as P5 (`push_subscriptions.chat_id` has no ContextVar awareness, so the router builds a per-tenant key at the call site).
- **Deployment note**: `docker-compose.product.yml`'s `gateway` (nginx) bind-mounts `frontend/dist` directly and proxies `/api|/auth|/health` to `product-api:8000` — the FastAPI `StaticFiles` SPA fallback in `product/main.py` is dead code in this topology (`Dockerfile.product` never copies `frontend/`), only relevant for a hypothetical single-container deployment. Static assets must be verified against `:8080` (the real entry point), not `:8000` directly — tripped over this while smoke-testing and re-verified against the correct port.
- **No delivery path yet**: `send_web_push_sync` is only ever called from `scheduled_jobs.py`'s job loop, which — like the scheduler itself — only runs in the owner's engine entrypoint. Subscribing from the product is harmless and forward-compatible (correctly tenant-scoped, ready the moment P6.6 lands) but does nothing today.

## P6.6 — Schedule/Activity/Automations + real Push delivery (assessed, not built — see below)

Read `scheduled_jobs.py`'s full execution path (`_execute_due_jobs`, `_run_job`, `_notify`, `start`/`stop`) and `broadcast.py`'s `NotificationManager` in full before starting this. This is **not** additive work like P6.1–P6.5 — it needs one real design decision first, or it will reproduce the exact class of bug fixed all through P2–P6.4.

**The blocking problem:** `NotificationManager` (`app/broadcast.py`) is a process-local singleton (`_connections: dict[chat_id, WebSocket]`, a plain class attribute). The owner's engine (`backend`, :8000) and the product (`product-api`, :8080) are **separate Docker containers with separate Python processes** — a WS connection registered in one process is invisible to the other. Today the scheduler (`scheduled_jobs.start()`) only runs inside the owner's `app/main.py`, so `_notify()` can only reach WS sessions the owner's own `web_chat.py` registered. `product/routers/chat.py`'s WS handler never calls `NotificationManager.register()` at all.

**The second problem, underneath the first:** even if the scheduler ran inside `product/main.py`'s own process, the chat_id shapes don't line up. The owner's web chat is one **stable** session (`chat_id="web"`, i.e. same key every time), so `scheduled_jobs` rows, `push_subscriptions` rows, and `NotificationManager` registrations for the owner all naturally use the same key and everything lines up. The product's `chat.py` mints a **fresh chat_id per WebSocket connection** (`f"web_{scope}_{uuid.uuid4().hex[:12]}"`, `chat.py:47`) — so a reminder scheduled from inside one chat session would be stored under that session's ephemeral id, and by the time it fires the WS is almost certainly closed and `get_push_subscriptions(job["chat_id"])` (keyed on the *stable* `_push_chat_id` shape from P6.5) would never find the tenant's push subscription — the keys don't match. `store_notification`/in-app notification-on-reconnect would have the same mismatch.

**Recommended fix, in order:**
1. Redesign product reminder/job/notification/card/push-subscription keys to a single **stable per-tenant identifier** (`f"web_{tenant.engine_scope}"`, matching what P6.5's `_push_chat_id`/P5's `_dashboard_chat_id` already use) instead of the ephemeral per-conversation chat_id — i.e. `schedule_reminder`/`schedule_tuya_command` (chat tools, bound via `tools_registry.py`) need to resolve the *stable* id, not receive the WS's per-connection one. Check whether that requires a small chat.py change (pass the stable id into `stream_graph`'s tool context) or a wrapper in the tool itself.
2. Run the scheduler loop inside `product/main.py`'s lifespan too (`scheduled_jobs.start()`/`stop()`, mirroring the owner's `app/main.py`) — it becomes two independent scheduler instances (owner's in `backend`, tenants' in `product-api`), each polling the same `scheduled_jobs` table but naturally only ever creating/touching rows under their own chat_id shapes, so no cross-process coordination is needed.
3. **Critical**: `_execute_due_jobs`/`_run_job` currently never sets `current_tenant_id` before executing a job. Since P2, `_send_command_local`/`_send_command_cloud` (Tuya) resolve credentials via `runtime.get_secret()`, which reads that ContextVar — a job executed with the ContextVar unset resolves to the *owner's* env credentials. Before `_run_job(job)`, derive the tenant scope from `job["chat_id"]` (`"web_" + scope` per the Step-1 key shape) and wrap execution in `current_tenant_id.set(scope)` / reset. This is the same class of bug as the Garmin-merge and Gilbert's-PII fixes from P4 — latent today only because tenants can't reach `schedule_reminder`/`schedule_tuya_command` at all (`scheduling` module is `owner_only=True`).
4. `product/routers/chat.py`'s WS handler needs its own `NotificationManager.register()`/`unregister()` calls (it doesn't today) so `_notify()`'s WS-broadcast path can actually reach an open tenant tab.
5. Only after 1–4: flip `scheduling`'s `owner_only=True` → `False` in `product/modules/registry.py`, port `/api/jobs`'s create-reminder-from-chat flow, and build the Automations UI.

Proactive flows (`proactive/flows.py`, `start_flows(chat_ids: list[str])`) have the identical shape of problem — explicit chat_id list, single-process, no ContextVar awareness — and should be redesigned together with the scheduler rather than separately, since the fix (stable per-tenant chat_id, `current_tenant_id.set()` around execution, per-tenant task fan-out) is the same for both.

## P6.7 — Per-tenant WhatsApp channel, Leads, self-review (assessed, not built)

**WhatsApp channel**: WAHA (the WhatsApp HTTP API this stack drives) supports multiple named sessions, but `docker-compose.yml`'s `waha` service and `backend/app/whatsapp.py`'s webhook handler are both wired for exactly one session (`WAHA_SESSION` env var, single webhook route, `MY_WHATSAPP_ID`/`MY_WHATSAPP_LID` hardcoded to one number). A per-tenant WhatsApp channel needs: (a) a WAHA session-provisioning flow (create/name/QR-pair a new session per tenant, likely via WAHA's own multi-session REST API), (b) the webhook handler to resolve `session name → tenant scope` instead of assuming the single owner session, (c) `current_tenant_id` set from that resolution before the message reaches the graph, (d) per-tenant phone-number/session lifecycle in the product (connect/disconnect UI, QR code display, session-limit/billing considerations). This is infrastructure-level work (touches `docker-compose.yml`, WAHA's own session API, not just app routes) — comparable in scope to a new deployment topology, not a new page.

**Leads** (`backend/app/routers/leads.py`, watched-WhatsApp-contact silent info-gathering): depends entirely on the WhatsApp channel above — a tenant has no WhatsApp contacts to watch without their own WA session. Deferred until that lands.

**Self-review** (`backend/app/memory/self_review.py`, nightly 3 AM analysis of the last 24h of conversations): the analysis function itself operates on `conversation_log`/`episodes`, which are already tenant-scoped (P0) — the actual blocker is the same as the scheduler: it's only *invoked* from the owner's engine startup/APScheduler-less loop, never per-tenant. Once P6.6's per-tenant scheduler lands, wiring self-review to run once per active tenant (with `current_tenant_id` set per iteration) is comparatively small — do it as a P6.6 follow-up, not standalone.

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
- (P6.5) Deployment topology gotcha: the product's SPA/static assets are served by the `gateway` nginx container (bind-mounts `frontend/dist` from the host, proxies `/api|/auth|/health` to `product-api:8000`) — **not** by `product-api` itself (`Dockerfile.product` never copies `frontend/`, so `product/main.py`'s `StaticFiles` SPA-fallback mount is dead code in this topology, only relevant to a hypothetical single-container deploy). Any future static-asset work must be verified against `:8080`, not `:8000` — tripped over this mid-step (404s on `:8000`, fine on `:8080`) and want the next session to not repeat it.
- (P6.5) Test-suite gotcha (own mistake, fixed same step): `asyncio.run()` inside a test closes the event loop it creates, which breaks *other* tests later in the same pytest session that use the codebase's established bare `asyncio.get_event_loop().run_until_complete()` idiom (`test_sanity.py`'s image/reflection tests, `test_garmin.py`'s `_run()` helper) — caused 8 unrelated failures the first time `TestVoiceTenantIsolation` ran. Fixed by adding a matching `_run()` helper to `test_tenant_isolation.py` instead of `asyncio.run()`. Any new async test in this suite must use `_run()`/`asyncio.get_event_loop().run_until_complete()`, never `asyncio.run()`.
- (P6.5 verified) full offline suite (161 passed, 1 pre-existing unrelated failure) green after the event-loop fix. Live through the real `:8080` gateway: `/manifest.json`/`/sw.js`/`/icon-192.png` all 200 with correct content-types; `/nutrition` (a client-side route) → 200 `text/html` confirming nginx's SPA fallback (`try_files ... /index.html`) works; unauthenticated `/api/push/vapid-public-key` → 401 through the proxy (cookie auth flows correctly end-to-end); owner's `/manifest.json`/`/sw.js` (:8000, untouched code) still 200.
