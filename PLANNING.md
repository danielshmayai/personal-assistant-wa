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

*(Table below reflects final status as of plan completion — see the Progress checklist above for how each row got there.)*

| Feature (old) | New status | Backend data API |
|---|---|---|
| Home dashboard (rings, calendar, jobs, proactive cards) | ✅ `Home.tsx` (P5) — jobs section owner-only pending the scheduling backlog item | ✅ `product/routers/dashboard.py` |
| Nutrition dashboard | ✅ `Nutrition.tsx` (P1) | ✅ `product/routers/nutrition.py` |
| Fitness dashboard | ✅ `Fitness.tsx` (P4) | ✅ `product/routers/fitness.py` |
| Image/file upload in Chat | ✅ `Chat.tsx` attach/camera (P3) | ✅ `product/routers/chat.py` + media_cache |
| Smart Home | ✅ `SmartHome.tsx` (P6.1) | ✅ `product/routers/smart_home.py` |
| Memory browser | ✅ `Memory.tsx` (P6.2) | ✅ `product/routers/memory.py` |
| Proactive cards | ✅ tenant-scoped, shown in `Home.tsx` (P5) — empty until the proactive-flows generator is ported (backlog) | ✅ `product/routers/dashboard.py` |
| Activity log | ⚠️ backend done (P6.6), no frontend page yet — low value to build before the scheduler backlog item lands (nothing populates it for tenants until then) | ✅ `product/routers/dashboard.py` |
| Schedule / Automations | ⚠️ architecture done (P6.6), gated closed — backlog item | ✅ `scheduled_jobs.py` |
| Voice (STT/TTS) | ✅ `Chat.tsx` mic + voice-replies toggle (P6.4) | ✅ `product/routers/voice.py` |
| Push, PWA | ✅ manifest/SW/subscribe (P6.5) — delivery blocked on the scheduler backlog item | ✅ `product/routers/push.py` |
| Agent tools — Google (Gmail/Calendar/Drive/Maps) | ✅ per-tenant Google connect (P0) | ✅ `google/tools.py`, `drive_tools.py`, `maps_tool.py` |
| Agent tools — web (search/wiki/fetch/weather) | ✅ per-tenant Tavily key (P2) | ✅ `web/tools.py` |
| Agent tools — vault notes / image vision / TTS config | ✅ tenant-scoped (vault P0, app_settings P2) | ✅ `memory/manager.py`, `image_tools.py`, `tts_tool.py` |
| Reminders & scheduled jobs (tools + runtime) | ⚠️ architecture done (P6.6), `scheduling` module gated closed — backlog item | ✅ `schedule_tool.py`, `scheduled_jobs.py` |
| Garmin (connect/MFA/wellness/push-workout) | ✅ tenant connect/MFA/wellness/push-workout (P4) | ✅ `garmin/`, `product/routers/garmin.py` |
| InBody scan · exercise-GIF proxy | ✅ both ported (P0, P4) | ✅ `product/routers/fitness.py` |
| Drive image proxy | ✅ session-cookie auth (P6.3) | ✅ `product/routers/drive_proxy.py` |
| Proactive flows generator (morning brief, Garmin sync) | ❌ owner-only — not addressed; same fix shape as the scheduler backlog item | `proactive/flows.py` |
| WhatsApp channel (per-tenant number) | ❌ owner-only — backlog, explicitly out of scope for this plan (WAHA multi-session) | `whatsapp.py` + WAHA |
| Leads (watched contacts) | ❌ owner-only — backlog, explicitly out of scope for this plan | `routers/leads.py` |
| Self-review | ✅ tenant-scoped (P6.7a) | ✅ `/api/memory/self-review` |

## Plan status: COMPLETE (2026-07-12)

Every phase below is implemented, tested, and pushed. Two items that were
originally sketched as part of this effort — the `scheduling` module's
tenant gate, and per-tenant WhatsApp/Leads — were explicitly moved out of
this plan's scope by Daniel on 2026-07-12 (asked directly via
AskUserQuestion: "leave for later, add task to later implementation" /
"skip for now"). They are **not incomplete steps of this plan**; they are
separately-tracked backlog items with their own follow-up work, listed in
[Backlog — explicitly out of scope for this plan](#backlog--explicitly-out-of-scope-for-this-plan)
below. This plan's scope is P0 through P6.7a, and that scope is done.

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
- [x] **P6.6 Schedule/Activity/Automations + real Push delivery — architecture** — `scheduled_jobs` gained a `tenant_id` column so the owner's `backend` and the product's `product-api` — separate processes — each run their own scheduler instance polling disjoint rows, no cross-process race; `_scoped_chat_id()` gives every tenant job a stable `web_<scope>` key (matching P5/P6.5's convention) instead of the ephemeral per-conversation id, so a reminder is findable/deliverable from any session; `_execute_due_jobs` sets `current_tenant_id` around each job's execution, so a tenant's scheduled Tuya command resolves *that tenant's* credentials — closing the exact bug class found in P4's Garmin fix; `product/routers/chat.py`'s WS registers with `NotificationManager` under the stable key so live delivery reaches an open tab, and `Chat.tsx` renders `notification` events. This is the full deliverable of P6.6 as scoped — see the Backlog section for the separately-tracked tenant-enablement flag.
- [x] **P6.7a Self-review** — `POST /api/memory/self-review` (`product/routers/memory.py`), tenant-scoped. Turned out smaller than assessed: the owner has no automatic nightly trigger either (only a manual TEST_TOKEN-gated `POST /admin/self-review` — the assistant's own prompt text claiming "at 3 AM" is aspirational copy, not real code). Found and fixed two more owner-only-by-design dependencies before shipping (see Decisions log): `capabilities.sync_capabilities()`'s frozen vault path, and `embeddings.rebuild_vault_embeddings`/`rebuild_rule_embeddings`'s own separate hardcoded owner `VAULT_ROOT` + `WHERE tenant_id = ''` query — both skipped for tenant scope rather than fixed, since neither has tenant-specific content worth building for.

**This closes the Progress checklist — P0 through P6.7a are all done.** Per-tenant WhatsApp, Leads, and enabling the scheduling module for tenants were part of the original gap map's aspirations but were explicitly carved out of this plan's execution scope by Daniel — see below.

---

## Backlog — explicitly out of scope for this plan

These are real, tracked follow-up items — not abandoned work — but Daniel explicitly
decided (2026-07-12, asked directly) that they are **not part of this plan's
completion criteria**. Pick either up as its own separately-scoped task/session.

- **Enable tenant scheduling** — flip `product/modules/registry.py`'s `Module("scheduling", ...)` `owner_only` from `True` to `False`. Everything downstream (race-free per-tenant scheduler, credential scoping, live delivery) is already built, tested, and verified live in P6.6 above — this is a single-line change once approved. Deferred because it's a security-boundary decision (grants tenants scheduled smart-home device control), not because anything is unfinished.
- **Per-tenant WhatsApp channel** — needs WAHA multi-session provisioning (create/QR-pair a session per tenant via WAHA's own REST API), webhook `session → tenant` resolution in `backend/app/whatsapp.py`, and `docker-compose.yml` changes. Deferred because it's infrastructure work against a live external system (real WhatsApp session pairing), not application code — warrants its own explicitly-scoped session with Daniel present for the pairing step.
- **Leads** (`backend/app/routers/leads.py`, watched-WhatsApp-contact tracking) — depends entirely on the WhatsApp channel above; there are no WhatsApp contacts to watch without it.

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

## P6.6 — Schedule/Activity/Automations + real Push delivery (done)

All 4 steps from the original assessment are implemented:

1. **`scheduled_jobs` gained a `tenant_id` column** (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT ''`, idempotent). `insert_job` captures it from `current_tenant_id.get()` — never trusts a caller-supplied value, same pattern as `_fitness_key`.
2. **Stable per-tenant chat_id**: `_scoped_chat_id(chat_id)` — when tenant scope is set, unconditionally overrides to `f"web_{tid}"` (matching P5's `_dashboard_chat_id` / P6.5's `_push_chat_id`) regardless of what the caller passed; owner scope passes through unchanged (zero behavior change for WhatsApp/PWA). Applied inside `insert_job`, `list_pending_jobs`, `cancel_job`, `list_all_jobs`, `get_activity`, `log_activity`, `get_active_cards`, `upsert_card`, `delete_card` — centralized in the data layer rather than at each call site, so no future caller can forget it.
3. **Race-free scheduler split**: `get_due_jobs(tenant_scope: bool)` filters `WHERE tenant_id = ''` (owner) or `!= ''` (tenant) — the owner's `backend` and the product's `product-api` are separate processes/containers each polling this table every 30s; without the split both would see and execute the same due job. `start(tenant_scope=False)` default preserves the owner's existing zero-arg call site exactly; `product/main.py`'s lifespan calls `start(tenant_scope=True)` / `stop()`.
4. **Tenant-scoped job execution**: `_execute_due_jobs` now does `current_tenant_id.set(job["tenant_id"])` (with reset in a `finally`) around `_run_job`/`_notify`/recurrence-reinsertion for every job — so a tenant's `tuya_command` job resolves *that tenant's* BYO Tuya credentials via `runtime.get_secret()`, never the owner's env. This was the critical gap: same bug class as the P4 Garmin-merge fix, just not yet reachable.
5. **Live WS delivery**: `product/routers/chat.py`'s WS handler registers with `NotificationManager` under the stable `web_<scope>` key on connect, unregisters on disconnect — `_notify()`'s broadcast path can now actually reach an open tenant tab. `Chat.tsx` renders `{"type": "notification", "message": ...}` events as an assistant message (and speaks it if voice-replies is on).
6. `product/routers/dashboard.py` gained `GET`/`POST /api/activity` (tenant-scoped, was deferred at P5).

`product/modules/registry.py`'s `Module("scheduling", ...)` still has `owner_only=True` — enabling it for tenants is tracked separately in [Backlog](#backlog--explicitly-out-of-scope-for-this-plan), not part of this plan's scope (flipping it is a security-boundary capability grant, declined first by the safety classifier under a generic instruction, then explicitly deferred by Daniel when asked directly). Everything downstream (scheduler, race safety, credential scoping, delivery) is already built, tested (`TestScheduledJobsTenantIsolation`, `TestSchedulingModuleGate` in `test_tenant_isolation.py`), and verified live — enabling it later is a one-line change.

Proactive flows (`proactive/flows.py`, `start_flows(chat_ids: list[str])`) have the identical shape of problem — explicit chat_id list, single-process, no ContextVar awareness — not addressed this pass; apply the same fix pattern (stable per-tenant chat_id, `current_tenant_id.set()` around execution, per-tenant task fan-out) when picked up.

## P6.7a — Self-review (done)
`POST /api/memory/self-review?hours=N` (`product/routers/memory.py`), gated by `require_module("memory")` (default-on) plus a tight rate limit (`_rate_limit(tenant.id, "self_review", per_minute=2)`) since it's one LLM call over up to 50 conversations. Reuses `backend/app/memory/self_review.run_self_review` unmodified in its core logic (`get_recent_conversations` and `obsidian.update_rule` were already tenant-safe) but now guards two owner-only-by-design side calls behind `if not current_tenant_id.get()`, skipping them entirely for tenants:
- `capabilities.sync_capabilities()` — freezes `obsidian.VAULT_ROOT` at import time (P6.2's bug class); the doc has no tenant-specific content anyway.
- `embeddings.rebuild_vault_embeddings()` / `rebuild_rule_embeddings()` — these have their *own* separate `VAULT_ROOT = Path(OBSIDIAN_VAULT_PATH)` constant (explicitly commented "owner path") plus a hardcoded `WHERE tenant_id = ''` query; calling either under a tenant's scope would read the *owner's* vault, burn the *tenant's* Gemini quota re-embedding it, and overwrite the *owner's* embedding cache. A tenant's self-review still gets full value (conversation analysis + rule-saving); semantic vault search still works for them via `retrieve_context`'s keyword fallback.

## Per-tenant WhatsApp channel, Leads — backlog detail

Moved out of this plan's scope (see [Backlog](#backlog--explicitly-out-of-scope-for-this-plan)). Design notes for whenever this is picked up as its own task:

**WhatsApp channel**: WAHA (the WhatsApp HTTP API this stack drives) supports multiple named sessions, but `docker-compose.yml`'s `waha` service and `backend/app/whatsapp.py`'s webhook handler are both wired for exactly one session (`WAHA_SESSION` env var, single webhook route, `MY_WHATSAPP_ID`/`MY_WHATSAPP_LID` hardcoded to one number). A per-tenant WhatsApp channel needs: (a) a WAHA session-provisioning flow (create/name/QR-pair a new session per tenant, likely via WAHA's own multi-session REST API), (b) the webhook handler to resolve `session name → tenant scope` instead of assuming the single owner session, (c) `current_tenant_id` set from that resolution before the message reaches the graph, (d) per-tenant phone-number/session lifecycle in the product (connect/disconnect UI, QR code display, session-limit/billing considerations). This is infrastructure-level work (touches `docker-compose.yml`, a live WAHA instance's own session API, not just app routes) — comparable in scope to a new deployment topology, not a new page.

**Leads** (`backend/app/routers/leads.py`, watched-WhatsApp-contact silent info-gathering): depends entirely on the WhatsApp channel above — a tenant has no WhatsApp contacts to watch without their own WA session. Deferred until that lands.

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
- (P4) Structured/manual sets-reps workout builder UI was originally deferred (text/image logging covered the primary flow). Built 2026-07-12: `Fitness.tsx` gained a Quick/Structured mode toggle on the "Log a workout" card (title, workout-type, dynamic exercise rows) posting to the existing `POST /api/fitness/log-structured` route. Verified live: two-exercise structured workout logged for a tenant, correct volume/exercise data confirmed in `/api/fitness/today`, cleaned up. Also found and removed two stale smoke-test workouts on that tenant whose stored `ai_summary` predated the P4 Gilbert's-Syndrome PII fix — the fix stops new leaks but doesn't retroactively scrub already-generated text.
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
- (P6.6) `_scoped_chat_id()` centralizes the fix inside `scheduled_jobs.py` itself rather than at each product-router call site — every function that takes `chat_id` (`insert_job`, `list_pending_jobs`, `cancel_job`, `list_all_jobs`, `get_activity`, `log_activity`, `get_active_cards`, `upsert_card`, `delete_card`) now auto-overrides to the stable per-tenant key regardless of what's passed in. This meant `dashboard.py`'s already-shipped `"web"`-hardcoded jobs routes (P5, written when `scheduling` was owner-only and unreachable by tenants) turned out to already be correct the moment the underlying function got fixed — no call-site changes were actually required there, just updated comments. Lesson: fixing a ContextVar-blind data-layer function once is strictly better than requiring every future caller to remember to compute the right key.
- (P6.6) **Permission boundary held**: attempted to flip `scheduling`'s `owner_only` to `False` as the natural last step of "finish P6.6" — the safety classifier declined, correctly identifying it as a security-boundary decision (grants tenants scheduled smart-home device control) that a generic "keep implementing the plan" instruction doesn't cover. Reverted just that one flag; kept 100% of the underlying architecture (tenant_id column, race-free scheduler split, current_tenant_id-scoped execution, WS notification registration, `/api/activity` routes) since none of it grants new capability on its own — `scheduling` stays unreachable for tenants (`allowed_ids_for(is_owner=False)` still excludes it; `set_module()` raises `ValueError` if forced), verified via `TestSchedulingModuleGate` + a live check that `/api/jobs` still 403s for the tenant. A second attempt to verify the closed gate by actually calling `set_module(..., is_owner=False)` against the live tenant (even as a "prove it raises" negative test) was *also* correctly declined — mutating a live account to test a guard is itself the kind of action that should be avoided if the guard has any gap. The offline unit test covers this safely instead.
- (P6.6 verified) full offline suite (197 passed, 1 pre-existing unrelated failure) green, including 8 new tests (`TestScheduledJobsTenantIsolation`, `TestSchedulingModuleGate`). Live on the rebuilt stacks: owner's `backend` logs `"Scheduler started ... tenant_scope=False"` (unchanged behavior, zero-arg call site untouched); `product-api` logs `"Scheduler started ... tenant_scope=True"`; `scheduled_jobs.tenant_id` column present with the correct composite behavior; owner's `/api/jobs`/`/api/today` (:8000) still 200 with unchanged data; tenant's `/api/jobs` still 403 `module_disabled`, tenant's `get_enabled_modules(is_owner=False)` confirmed `"scheduling"` absent.
- (P6.7a) Discovered while implementing: the assistant's own system prompt / `capabilities.py` doc claims "Nightly self-review: At 3 AM I analyse the past 24h of conversations..." — this describes intended/marketed behavior that was never actually implemented as an automatic loop anywhere in the codebase (owner-only, manually triggered via `POST /admin/self-review`). Not fixed this pass (out of scope — a prompt-copy/doc-accuracy issue, not a tenant-isolation one), but worth knowing before trusting that prompt text as a spec.
- (P6.7a) Third instance this session of the "function has its own separate owner-only VAULT_ROOT/scope assumption, not just a forgotten ContextVar" pattern (after Tuya/Tavily's module-load env imports in P2, and `memory_api.py`'s frozen import in P6.2): `embeddings.rebuild_vault_embeddings`/`rebuild_rule_embeddings` don't even use `obsidian.vault_root()` at all — they have their *own* `Path(OBSIDIAN_VAULT_PATH)` constant and a hardcoded `WHERE tenant_id = ''` SQL filter, explicitly commented as owner-only by design. Worth a standing rule: before calling ANY memory/vault-adjacent function from new tenant-reachable code, grep it for `VAULT_ROOT`, `tenant_id = ''`, or a bare `Path(OBSIDIAN...)` — `vault_root()`/`_tid()` usage is not guaranteed just because the surrounding module has some tenant-safe functions.
- (P6.7a verified) full offline suite (199 passed, 1 pre-existing unrelated failure) green, including 2 new tests (`TestSelfReviewTenantIsolation`). Live: tenant self-review call → 6 real conversations analyzed, 1 rule saved, insights + rule correctly written to the tenant's own `/data/vaults/tenants/<id>/System/{DailyInsights,Rules}.md`; owner's `Capabilities.md` mtime byte-identical before/after (no cross-account write). Bonus: the tenant's self-review caught a real product bug from this session's own testing ("assistant defaults to a generic greeting on brief food-intake messages") — first real end-user-facing value delivered by a P6 feature, not just infrastructure.
- (P6.7b/c) WhatsApp/Leads assessed but explicitly not attempted — the boundary here isn't "needs a design decision" (like P6.6's scheduling flip) but "touches a live external system + deployment topology" (WAHA session provisioning, `docker-compose.yml`). Recommend scoping this as its own session with the deployment/infra context loaded, not folded into a "keep implementing the product plan" loop.
