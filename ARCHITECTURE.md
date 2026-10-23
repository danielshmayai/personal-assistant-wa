# PA (danidin) — System Architecture

> Auto-generated reference. Update with `/update-architecture` when the system changes.

## High-Level Flow

```
WhatsApp ──→ WAHA ──→ engine (backend, :8000) ──→ async worker queue (in-memory + DB dedup)
                                                         │
Web app (React) ──→ product gateway (:8080) ──→ product-api ── /ws/chat (streaming)
                                                         │
                                                         ▼
                              LangGraph graph (PostgreSQL checkpointer, shared engine code)
                              ┌──────────────────────────────────────────────────────┐
                              │ START                                                │
                              │   ↓                                                  │
                              │ inject_memory (Profile + rules + notes + episodes)   │
                              │   ↓                                                  │
                              │ agent (Gemini 2.5 Flash, module-scoped prompt+tools) │
                              │   ↓              ↑                                   │
                              │ tools ────────────┘ (parallel exec)                  │
                              │   ↓                                                  │
                              │ reflection (spawns background memory extractor)      │
                              │   ↓                                                  │
                              │ END → reply                                          │
                              └──────────────────────────────────────────────────────┘
```

Two assistants share `backend/app/` (graph, tools, memory):
- **Engine / "old assistant"** (`docker-compose.yml`, :8000): WhatsApp webhook + worker,
  Google connect for WhatsApp, `/test`, `/admin/*`, `/health`. `GET /` redirects to the web app
  (`PRODUCT_URL`, default same host :8080). It no longer serves a web UI.
- **Product / "new assistant"** (`docker-compose.product.yml`, :8080; or `docker-compose.cloud.yml`):
  multi-tenant web app — login, tenants, secrets, modules, all `/api/*` for the React frontend.

## Services (Docker Compose)

| Compose file | Service | Port | Role |
|---|---|---|---|
| `docker-compose.yml` | **backend** | 0.0.0.0:8000 | Engine: WhatsApp webhook, worker, LangGraph |
| | **postgres** | 127.0.0.1:5432 | pgvector:pg16 — state, memory, checkpoints, embeddings |
| | **ollama** | 127.0.0.1:11434 | Local LLM fallback only (no tools) |
| | **waha** | 127.0.0.1:3000 | WhatsApp HTTP API (prod profile) |
| `docker-compose.product.yml` | **product-api** | internal | Product FastAPI (shares engine Postgres over `pa_pa-net`) |
| | **gateway** | 8080 | nginx: React build + `/api`, `/auth`, `/health`, `/ws/` proxy |
| `docker-compose.cloud.yml` | postgres, product-api, gateway, **tailscale** | Funnel | Standalone new assistant (GHCR images, `update.sh` cron) |

## Backend Module Map

```
backend/app/
├── main.py                 # Engine FastAPI: lifespan, WAHA + google_auth routers, /test, /admin/*, /health, / → web app
├── config.py               # All engine env vars (single source of truth)
├── runtime.py              # Per-tenant secrets / vault root resolution, in_thread()
├── context.py              # ContextVars: correlation/request id, current_tenant_id, user name/email
├── worker.py               # Async message queue (in-memory + DB L2 dedup)
├── whatsapp.py             # WAHA webhook, routing, voice-note transcription, sending
├── voice.py                # STT (Gemini, Whisper fallback) + TTS (Google Neural2 / edge-tts)
├── llm.py                  # Gemini model resolution + Ollama fallback, response_text()
├── background.py           # spawn(): tracked background tasks, failures logged at WARNING
├── metrics.py              # In-process reply latency + retrieval hit-rate (health panel)
├── broadcast.py            # NotificationManager: live WS + WhatsApp broadcast
├── media_cache.py          # In-memory cache for WAHA / web upload media
├── morning_brief.py        # Brief: weather, calendar, gmail, Garmin, fitness, memory learned
├── push_notifications.py   # VAPID keys, push_subscriptions, send_web_push_sync
├── scheduled_jobs.py       # Job loop (owner / tenant split), activity_log, proactive_cards, notifications
├── job_queue.py            # DB-backed worker job persistence + dedup
├── leads.py                # Watched WhatsApp contacts, debounced extraction into memory
├── nutrition.py / nutrition_tool.py   # Meal + water DB layer / tools
├── fitness.py / fitness_tool.py       # Workouts, body metrics, suggestions / tools
├── image_tools.py          # analyze_image, save_photo_tagged
├── schedule_tool.py        # Reminder + scheduled Tuya tools
├── tts_tool.py / tts_config.py        # configure_tts tool / settings
├── crypto.py               # Fernet encrypt/decrypt for OAuth tokens
├── graph/
│   ├── graph.py            # Assembly, run_graph(), stream_graph(), inject_memory_node
│   ├── distiller.py        # agent_node: core prompt + per-group sections, tool binding
│   ├── tools_registry.py   # Module → tool-group mapping, cached per (tenant, chat, modules)
│   ├── tool_node.py        # Parallel tool executor + activity labels
│   ├── streaming.py        # get_history() from checkpoints
│   ├── checkpointer.py     # AsyncPostgresSaver (pool of 10)
│   └── state.py            # PAState
├── memory/
│   ├── manager.py          # 7 memory tools
│   ├── obsidian.py         # Vault I/O: Summary/Log notes, Profile, rules, locking, index-on-write
│   ├── embeddings.py       # gemini-embedding-001 @768 via REST; vault/rule/episode vectors
│   ├── store.py            # Tables, load_memory_context(), memory_events, app_settings
│   ├── reflection.py       # Structured extractor (person/project/decision/preference/fact/commitment/rule)
│   ├── maintenance.py      # In-process loop: index pass at start, nightly consolidation + self-review
│   ├── episodes.py         # Conversation summaries + embeddings
│   ├── self_review.py      # analyse_conversations(): rules + insights from recent chats
│   └── capabilities.py     # System/Capabilities.md (owner) from live tool introspection
├── google/                 # auth, gmail, calendar, drive, drive_tools, maps_tool, tools
├── tuya/tools.py           # list_tuya_devices, get_device_status, control_device
├── garmin/                 # client (per-tenant tokens), sync, workout_push
├── web/tools.py            # web_search, wikipedia_search, fetch_url, get_weather, build_whatsapp_link
├── proactive/flows.py      # Owner WhatsApp flows: 07:00 brief, Garmin workout notices
└── routers/google_auth.py  # /auth/google/* for the WhatsApp owner

product/
├── main.py                 # Lifespan: migrations, tenant scheduler, memory maintenance, morning brief
├── brief.py                # Per-tenant opt-in morning brief loop (feed + push + Home card cache)
├── deps.py                 # require_session / require_owner / require_module (sets tenant scope)
├── offboarding.py          # Delete all tenant data
├── auth/ tenancy/ modules/ secrets/ (local_pg) storage/ (local_fs) migrations/
└── routers/                # see API Surface
```

## Tool Inventory (45 tools)

Bound per turn by enabled modules (`tools_registry._MODULE_GROUPS`); the system prompt only
includes sections for bound groups.

| Group (module) | Tools | Source |
|---|---|---|
| **web** (web-search) (5) | web_search, wikipedia_search, fetch_url, get_weather, build_whatsapp_link | `web/tools.py` |
| **google** (11) | google_connect, gmail_read (optional Gmail `query`), gmail_read_message, gmail_send, calendar_list (days_ahead, query), calendar_create, calendar_update, calendar_delete, drive_save_document, drive_list_files, drive_show_image | `google/tools.py`, `drive_tools.py` |
| **google** — maps (2) | create_google_map, add_places_to_map | `google/maps_tool.py` |
| **tuya** (smart-home) (3) | list_tuya_devices, get_device_status, control_device | `tuya/tools.py` |
| **memory** (7) | save_fact, update_rule, search_memory (semantic + keyword; `note` = filter one file), forget, list_memory, read_note, append_to_note | `memory/manager.py` |
| **schedule** (scheduling) (5) | schedule_reminder, schedule_recurring_reminder, list_scheduled, cancel_scheduled, schedule_tuya_command (only with tuya) | `schedule_tool.py` |
| **nutrition** (3) | log_meal, log_water, nutrition_today | `nutrition_tool.py` |
| **fitness** (6) | log_workout, suggest_workout, fitness_history, log_body_metrics, analyze_body_scan, fitness_morning_brief | `fitness_tool.py` |
| **image** (chat, always on) (2) | analyze_image, save_photo_tagged | `image_tools.py` |
| **tts** (1) | configure_tts | `tts_tool.py` |

## API Surface

Engine (`backend/app`, :8000):

| Path | Purpose |
|---|---|
| `POST /webhook/waha?secret=` | WAHA incoming webhook |
| `/auth/google/start`, `/auth/google/callback` | Google connect for the WhatsApp owner |
| `POST /test` | Run text through the graph (`X-Test-Token`) |
| `POST /admin/self-review` | Run self-review (`X-Test-Token`) |
| `GET /health` | Service status incl. `memory` (embeddings state, counts, last retrieval) |
| `GET /` | 307 → web app (`PRODUCT_URL`) |

Product (`product/routers`, behind gateway :8080; cookie session, tenant-scoped):

| Router | Paths | Purpose |
|---|---|---|
| `auth`, `me`, `onboarding` | `/auth/*`, `/api/me*`, `/api/onboarding` | Login, profile, onboarding |
| `secrets`, `modules` | `/api/secrets`, `/api/modules` | Per-tenant BYO keys, module toggles |
| `google_connect` | `/api/google/connect`, `/auth/google/callback` | Tenant Google OAuth |
| `chat` | `/ws/chat`, `/api/upload`, `/api/conversations[/{id}[/messages]]` | Streaming chat, attachments, history (own `web_<scope>_` threads only) |
| `voice` | `/api/stt`, `/api/tts` | Speech to text (Gemini), text to speech |
| `dashboard` | `/api/today`, `/api/calendar-today`, `/api/jobs`, `/api/activity`, `/api/proactive-cards` | Home + Activity data |
| `brief` | `/api/brief/settings`, `/api/brief/today` | Morning-brief opt-in + today's brief |
| `push` | `/api/push/vapid-public-key`, `subscribe`, `unsubscribe`, `test` | Web Push |
| `memory` | `/api/memory/*` incl. `recent`, `recent/{id}` | Vault browser, learned feed + forget |
| `nutrition`, `fitness`, `garmin` | `/api/nutrition/*`, `/api/fitness/*`, `/api/garmin/*` | Trackers, suggestions, exercise GIFs, Garmin push |
| `smart_home` | `/api/devices*` | Tuya devices |
| `drive_proxy` | `/api/drive/proxy/{file_id}` | Stream Drive images |
| `leads` | `/api/leads` | Watched WhatsApp contacts (local owner only) |
| `admin` | `/api/admin/tenants*`, `/api/admin/health` | Members; Assistant-health numbers (owner) |

## Data Flow Details

### Message Processing (WhatsApp)
1. WAHA POSTs to `/webhook/waha?secret=` → `whatsapp.py` validates the secret; self-chat or `@danidin`/`!danidin` only
2. L1 dedup in memory, L2 dedup via `job_queue` in PostgreSQL
3. Enqueued to the per-chat `asyncio.Queue` in `worker.py`
4. Worker: a leading voice-note `[MEDIA … type=ptt|audio]` is downloaded and transcribed (Gemini) → `🎤 <text>`
5. `run_graph(text, chat_id)` with retries; reply sent via WAHA with the `[ *danidin* ]` prefix

### Message Processing (Web app)
1. Cookie-authenticated WebSocket `/ws/chat`; thread id `web_<scope>_<uuid>` (continue with `chat_id`, new with `new_thread`)
2. `stream_graph()` yields `thinking_token`, `token`, `tool_start`, `tool_end`, `error`
3. Conversation recorded in `web_conversations`; reminders/brief reach `web_<scope>` via live WS, feed and Web Push

### Memory System
- **Vault notes** (Obsidian markdown per scope): frontmatter + `## Summary` (current truth) + `## Log` (dated entries); `Profile/Me.md` always injected; `System/Rules.md`
- **Write path**: reflection node spawns `extract_and_store` on the newest exchange → routed to People/Projects/Entities/Profile/Commitments/Rules, deduped, logged to `memory_events`; every write is indexed immediately
- **Read path** (`load_memory_context`): Profile → active rules (newest within budget) → notes ≥ `MEMORY_MIN_SIMILARITY` (keyword fallback) → related episodes; one query embedding of message + recent turns
- **Maintenance** (`memory/maintenance.py`, in-process per scope): index pass at startup (re-embeds on model change), nightly after 03:00 local — consolidate Summaries, self-review
- **Visibility**: morning brief "learned yesterday" + open commitments; Memory page "Learned" feed with forget; `/health` and Admin → Assistant health

### Nutrition / Fitness scoping
- Data keyed by `current_tenant_id`; the owner (scope `''`) uses the legacy `default` key shared across WhatsApp and web

### Authentication (Google)
1. `google_connect` tool / Settings → OAuth URL with PKCE nonce
2. Callback (`/auth/google/callback` on engine or product) → tokens Fernet-encrypted with `DB_ENCRYPTION_KEY` in `google_tokens` (tenant rows keyed `tenant:<id>`)
3. Auto-refresh on expiry

## PostgreSQL Schema (key tables)

| Table | Purpose |
|---|---|
| `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` | LangGraph state per thread_id |
| `vault_embeddings` | 768-dim vectors per vault note (tenant_id, filepath) |
| `rule_embeddings` | 768-dim vectors for rule dedup |
| `episodes` | Conversation summaries + embeddings |
| `memory_events` | What the memory learned (kind, entity, path, content, source) — brief, feed, health |
| `conversation_log` | Conversation transcripts |
| `web_conversations` | Web chat thread list (title, timestamps) |
| `app_settings` | Per-tenant key/value (embedding model, maintenance last run, morning brief…) |
| `google_tokens`, `oauth_pending_states` | Encrypted OAuth tokens, PKCE nonces |
| `job_queue` | Worker job dedup |
| `scheduled_jobs` | Reminders / scheduled actions (one-shot + recurring, owner vs tenant split) |
| `pending_notifications`, `push_subscriptions` | Notification feed, Web Push endpoints |
| `activity_log`, `proactive_cards` | Tool activity feed, Home cards |
| `nutrition_logs` | Meal + water entries |
| `fitness_workouts`, `fitness_body_metrics` | Workouts, body composition |
| `garmin_tokens`, `garmin_wellness` | Per-tenant Garmin session, daily wellness |
| `watched_leads`, `lead_messages` | Lead monitoring |
| `tenants`, `sessions`, `tenant_modules`, `tenant_secrets`, `tenant_deks`, `tenant_google_identity` | Product tenancy (product migrations) |

Legacy `memory_facts` / `memory_rules` are no longer created or read; existing installs keep them
untouched (`backend/scripts/migrate_memory_to_vault.py` reads them).

## Configuration

Engine env vars in `backend/app/config.py`; product env vars in `product/config.py`.
- Engine critical: `DATABASE_URL`, `GEMINI_API_KEY`, `WEBHOOK_SECRET`, `TEST_TOKEN`, `DB_ENCRYPTION_KEY`, `MY_WHATSAPP_ID`, `WAHA_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- Engine memory: `EMBEDDING_MODEL` (default `gemini-embedding-001`), `MEMORY_MIN_SIMILARITY` (0.55), `OBSIDIAN_VAULT_PATH`
- Engine misc: `PRODUCT_URL` (redirect target for `/`), `ALLOWED_ORIGIN`, `USER_TIMEZONE`, `USER_CITY`, Tuya, VAPID, LangSmith
- Product: `SESSION_SECRET`, `SECRETS_MASTER_KEY`, `PRODUCT_BASE_URL`, `OWNER_EMAIL`, `OWNER_LEGACY_SCOPE`, `VAULT_BASE_DIR`

## Known Architectural Constraints

1. **Single-instance only** — vault uses per-file locks and in-process loops (maintenance, brief, scheduler)
2. **Recursion limit = 25** — hard cap on agent↔tool loops per invocation
3. **Checkpointer pool = 10 connections** — can starve under high concurrency
4. **Tool cache per (tenant, chat, modules)** — call `clear_tools_cache*` after OAuth / secret changes
5. **In-memory queue** — lost on crash; DB recovery fills the gap but in-flight messages drop
6. **Embeddings need a Gemini key** — without one, semantic recall falls back to keyword search
7. **Health metrics are per process** — reply latency / hit-rate count only the process serving the panel (web), since restart
