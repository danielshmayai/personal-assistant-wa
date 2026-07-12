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

## Progress checklist

- [x] **P0 Foundation** — tenant-scope nutrition/fitness data + expose to product (+ 2 safety fixes)
  - [x] Step 0 — remove destructive init migrations + gate owner PII
  - [x] Step 1 — tenant-scope `_nutrition_key` / `_fitness_key` (+ Garmin wellness gated for tenants)
  - [x] Step 2 — `product/routers/nutrition.py` + `fitness.py` (session-auth, reuse data funcs)
  - [x] Step 3 — `require_module` gate + wire routers/init into `product/main.py`
  - [x] Verify — owner vs tenant isolation, module gating, PII gate ✅ (all passed)
- [x] **P1 Nutrition dashboard** (React) — `frontend/src/pages/Nutrition.tsx`, route wired; verified log/water/delete/history end-to-end
- [ ] **P2 Image upload in Chat**
- [ ] **P3 Fitness dashboard** (React)
- [ ] **P4 Home dashboard** content
- [ ] **P5+** Smart Home · Memory browser · Schedule/Activity/Automations · Voice · Push/PWA

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

## P2 — Image upload in Chat
- Backend: tenant-scoped `POST /api/upload` in product (media_cache `web_<uuid>`, reuse old `web_chat.py` flow); extend `chat.py` WS message to accept `media_id` and prepend `[MEDIA …]` tag.
- Frontend `Chat.tsx`: attach + camera buttons, preview chip, send `media_id`.

## P3 — Fitness dashboard (React)
- New `frontend/src/pages/Fitness.tsx` (route `/fitness`); mirrors Nutrition. Rings (volume/duration); log text/image/structured; body metrics (manual + InBody scan); progression + body-composition charts; suggest; today's sessions. Consumes `/api/fitness/*`. Garmin stays owner-only for now.

## P4 — Home dashboard content
- Wire tenant-scoped `dashboard.py` routes into product (`/api/today`, `/api/calendar-today`, `/api/jobs`, `/api/proactive-cards`). Scheduled-jobs are owner-only today — gate. `Dashboard.tsx`: real Home view instead of routing `/` straight to Chat.

## P5+ (later, one at a time)
Smart Home (`smart_home.py`, per-tenant Tuya creds already in secrets catalog); Memory browser (`memory_api.py`, tenant vault already isolated); Schedule/Activity/Automations; voice STT/TTS; push/PWA.

## Risks
- **Destructive init UPDATEs** (critical) — remove before product lifespan calls `init_table`.
- **fitness_profile PII leak** (high) — gate by scope in P0 Step 0.
- **Garmin/Tuya owner-global creds** (medium) — drop `push_hydration`, short-circuit wellness; smart-home per-tenant creds in P5.
- **ContextVar-not-set = owner scope** (by design) — every new route goes through `require_module` → `require_session`; never call data funcs bare.

## Decisions log
- (P0) scope key = `current_tenant_id.get() or "default"` — preserves owner's `"default"` rows, isolates tenants.
- (P0) Garmin wellness overlay gated by `_garmin_allowed()` in `fitness.py` (owner-only single account) — both fetch sites in `generate_daily_recommendation`.
- (P0) `exercise-gif` endpoint deferred to P3 (fitness UI) — it's a static-asset GIF proxy with no tenant scope, not needed for the data foundation.
- (P0) Product routers are thin wrappers over `app.nutrition`/`app.fitness`, passing `""` for the now-vestigial `chat_id`; auth via `require_module()` (composes `require_session`).
- (P0 verified) owner `default` rows unaffected by tenant writes; tenant isolated; module-disabled → 403; tenant fitness prompt carries no owner PII.
- (P1) Added `api.upload()` (multipart) to `frontend/src/lib/api.ts` for meal photos; `Nutrition.tsx` reuses `ui.tsx` primitives + an inline SVG `Ring`. Route `/nutrition` now renders the real page (was `ModulePlaceholder`).
- (P1 verified) tenant log-text "2 eggs and a coffee" → Gemini parsed 12g P / 155 kcal; today/water/history/delete all 200 and update correctly. Image upload path shares the same insert; not driven with a real file.
