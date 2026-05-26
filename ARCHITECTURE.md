# PA (danidin) — System Architecture

> Auto-generated reference. Update with `/update-architecture` when the system changes.

## High-Level Flow

```
User (WhatsApp / Web UI)
        │
        ▼
WAHA webhook ──→ FastAPI ──→ async worker queue (in-memory + DB dedup)
                   │                    │
                   │                    ▼
                   │          LangGraph graph (PostgreSQL checkpointer)
                   │          ┌─────────────────────────────────────┐
                   │          │ START                                │
                   │          │   ↓                                  │
                   │          │ inject_memory (Obsidian + pgvector)  │
                   │          │   ↓                                  │
                   │          │ agent (Gemini 2.5 Flash + ~38 tools) │
                   │          │   ↓              ↑                   │
                   │          │ tools ────────────┘ (parallel exec)  │
                   │          │   ↓                                  │
                   │          │ reflection (extract facts/rules)     │
                   │          │   ↓                                  │
                   │          │ END → reply                          │
                   │          └─────────────────────────────────────┘
                   │
                   ├── /ws/chat (WebSocket streaming for Web UI)
                   ├── /auth/google/* (OAuth2 flow)
                   ├── /api/* (REST — dashboard, nutrition, memory, drive)
                   ├── /health (service status)
                   └── /admin/* (self-review, diagnostics)
```

## Services (Docker Compose)

| Service | Image | Port | Role |
|---------|-------|------|------|
| **backend** | custom (Python 3.12) | 0.0.0.0:8000 | FastAPI app, LangGraph, worker |
| **postgres** | pgvector:pg16 | 127.0.0.1:5432 | State, memory, checkpoints, embeddings |
| **ollama** | ollama:latest | 127.0.0.1:11434 | Local LLM fallback, embeddings, episodes |
| **waha** | devlikeapro/waha:latest | 127.0.0.1:3000 | WhatsApp HTTP API (prod profile only) |

Network: single bridge (`pa-net`). Ingress via Cloudflare Tunnel to backend:8000.

## Backend Module Map

```
backend/app/
├── main.py                 # FastAPI app, lifespan, routers, CORS, rate limiting
├── config.py               # All env vars (single source of truth)
├── worker.py               # Async message queue (in-memory + DB L2 dedup)
├── whatsapp.py             # WAHA webhook handler, message sending
├── llm.py                  # Gemini LLM factory
├── logging_config.py       # Structured JSON / text log setup
├── context.py              # Request-scoped correlation_id context var
├── broadcast.py            # Broadcast messages to all active WS connections
├── media_cache.py          # In-memory cache for WAHA media payloads (1hr TTL)
├── graph/
│   ├── graph.py            # LangGraph assembly, run_graph(), stream_graph()
│   ├── distiller.py        # agent_node: system prompt, tool binding, Gemini invoke
│   ├── tool_node.py        # Parallel tool executor (asyncio.gather)
│   ├── state.py            # PAState TypedDict
│   └── tools_registry.py   # Cached per-chat tool list builder
├── memory/
│   ├── manager.py          # 10 LangChain tools for vault operations
│   ├── obsidian.py         # Filesystem I/O, file locking, path traversal prevention
│   ├── store.py            # PostgreSQL tables, embeddings, token storage
│   ├── reflection.py       # Post-reply fact/rule extraction
│   ├── episodes.py         # Conversation summarisation + embedding
│   ├── embeddings.py       # 768-dim pgvector operations
│   ├── self_review.py      # Periodic conversation reflection (nightly 3 AM)
│   └── capabilities.py     # Auto-generates System/Capabilities.md on startup
├── web/
│   └── tools.py            # web_search, wikipedia_search, fetch_url, get_weather, build_whatsapp_link
├── google/
│   ├── auth.py             # OAuth2 flow, token refresh, Fernet encrypt/decrypt
│   ├── calendar.py         # Calendar list/create helpers
│   ├── gmail.py            # Gmail read/send helpers
│   ├── drive.py            # Drive upload/download/list helpers
│   ├── drive_tools.py      # save_photo, save_document, list_files, show_image
│   ├── maps_tool.py        # create_google_map, add_places_to_map (geocode + KML)
│   └── tools.py            # google_connect, gmail_*, calendar_* + drive + maps
├── tuya/
│   └── tools.py            # list_tuya_devices, get_device_status, control_device
├── proactive/
│   ├── cards.py            # trigger_card, notice_card, memory_card helpers
│   └── flows.py            # start_flows/stop_flows, morning briefing scheduler
├── routers/
│   ├── web_chat.py         # /ws/chat WebSocket, /api/conversations, /api/upload, /api/stt, /api/tts
│   ├── google_auth.py      # /auth/google/* OAuth2 redirect + callback
│   ├── dashboard.py        # /api/jobs, /api/activity, /api/proactive-cards, /api/today, /api/calendar-today
│   ├── memory_api.py       # /api/memory/* — search, categories, facts, rules CRUD
│   ├── nutrition.py        # /api/nutrition/* — log-image, log-text, log-water, today, history, delete
│   ├── drive_proxy.py      # /api/drive/proxy/{file_id} — stream Drive image to browser
│   ├── smart_home.py       # /devices/* — list, status, control
│   └── leads.py            # Lead watching endpoints
├── nutrition.py            # Meal + water tracking DB layer (nutrition_logs table)
├── nutrition_tool.py       # log_meal, log_water, nutrition_today LangChain tools
├── schedule_tool.py        # schedule_tuya_command, schedule_reminder, schedule_recurring_reminder, list, cancel
├── tts_tool.py             # configure_tts tool
├── tts_config.py           # TTS voice/rate settings storage
├── scheduled_jobs.py       # APScheduler runner, activity_log, proactive_cards, pending_notifications
├── job_queue.py            # DB-backed job persistence + dedup
├── crypto.py               # Fernet encrypt/decrypt for OAuth tokens
└── checkpointer.py         # PostgreSQL AsyncPostgresSaver (pool of 10)
```

## Tool Inventory (~38 tools)

| Category | Tools | Source |
|----------|-------|--------|
| **Web** (5) | web_search, wikipedia_search, fetch_url, get_weather, build_whatsapp_link | `web/tools.py` |
| **Google** (9) | google_connect, gmail_read, gmail_send, calendar_list, calendar_create, drive_save_photo, drive_save_document, drive_list_files, drive_show_image | `google/tools.py` + `drive_tools.py` |
| **Maps** (2) | create_google_map, add_places_to_map | `google/maps_tool.py` |
| **Tuya** (3) | list_tuya_devices, get_device_status, control_device | `tuya/tools.py` |
| **Memory** (10) | save_fact, update_rule, retrieve_context, list_memory, hide_fact, hide_rule, search_vault, grep_note, read_note, append_to_note | `memory/manager.py` |
| **Schedule** (5) | schedule_tuya_command, schedule_reminder, schedule_recurring_reminder, list_scheduled, cancel_scheduled | `schedule_tool.py` |
| **Nutrition** (3) | log_meal, log_water, nutrition_today | `nutrition_tool.py` |
| **TTS** (1) | configure_tts | `tts_tool.py` |

Tools are gathered dynamically via `tools_registry.py` → `get_all_tools(chat_id)` and cached per chat_id.

## API Surface

| Router | Prefix / Path | Purpose |
|--------|---------------|---------|
| `web_chat` | `/ws/chat`, `/api/conversations`, `/api/upload`, `/api/stt`, `/api/tts` | Web UI: streaming chat, file upload, speech |
| `google_auth` | `/auth/google/*` | OAuth2 authorization flow |
| `dashboard` | `/api/jobs`, `/api/activity`, `/api/proactive-cards`, `/api/today`, `/api/calendar-today` | Dashboard data + job management |
| `memory_api` | `/api/memory/*` | Memory vault CRUD (search, categories, facts, rules) |
| `nutrition` | `/api/nutrition/*` | Meal logging (image/text/water), today summary, history, delete |
| `drive_proxy` | `/api/drive/proxy/{file_id}` | Proxy Drive image bytes to browser (token auth) |
| `smart_home` | `/devices/*` | Tuya device list, status, control |
| `leads` | — | Lead monitoring |
| `whatsapp` | `/webhook/{secret}` | WAHA incoming webhook |

## Data Flow Details

### Message Processing (WhatsApp)
1. WAHA POSTs to `/webhook/{secret}` → `whatsapp.py` validates secret
2. L1 dedup: in-memory `_processed_ids` dict (1hr TTL)
3. L2 dedup: `job_queue.persist_job()` in PostgreSQL
4. Message enqueued to `asyncio.Queue` in `worker.py`
5. Worker calls `run_graph(text, chat_id)` with 4-attempt retry (1s, 5s, 15s delays)
6. Reply sent via WAHA HTTP API

### Message Processing (Web UI)
1. WebSocket connection to `/ws/chat` with token + chat_id query params
2. `stream_graph()` yields events: `thinking`, `token`, `tool_start`, `tool_end`, `done`
3. No queue — direct async streaming per connection

### Memory System
- **Obsidian Vault** (host-mounted): Human-readable markdown files organized by category (People, Entities, Investments, Projects, Preferences, Misc, System)
- **PostgreSQL pgvector**: 768-dim embeddings for semantic search on vault content, rules, and episodes
- **Reflection**: After each reply, extracts corrections/facts/rules/preferences via LLM
- **Episodes**: Background summarisation of conversations for long-term retrieval via Ollama
- **Capabilities doc**: `System/Capabilities.md` is regenerated on every startup from live tool introspection

### Nutrition Tracking
- All entries stored under `chat_id = 'default'` (single-user PA — cross-device sync)
- Water logged as `source='water'` rows with `micros={"water_ml": amount_ml}`
- Targets: protein 110 g/day, water 2000 ml/day
- Meals logged via WhatsApp text, web UI image upload, or `/api/nutrition/log-image`

### Authentication (Google)
1. User triggers `google_connect` tool → generates OAuth URL with PKCE nonce
2. User visits URL → Google redirects to `/auth/google/callback`
3. Tokens Fernet-encrypted with `DB_ENCRYPTION_KEY` → stored in `google_tokens` table
4. Auto-refresh on expiry via `google-auth-oauthlib`

### Drive Image Display
1. `drive_show_image(file_id)` tool returns `![name](/api/drive/proxy/{file_id}?chat_id=&token=)`
2. `marked` renders the markdown as `<img src="...">` in the web UI
3. Browser fetches `/api/drive/proxy/{file_id}` → backend downloads from Drive → streams bytes back
4. Click-to-zoom lightbox overlay on all chat images

## PostgreSQL Schema (key tables)

| Table | Purpose |
|-------|---------|
| `checkpoints` / `checkpoint_writes` | LangGraph state persistence per thread_id |
| `memory_facts` | Structured facts (entity, category, content) |
| `memory_rules` | Behavioral rules with versioning |
| `vault_embeddings` | 768-dim vectors for vault file search |
| `rule_embeddings` | 768-dim vectors for rule deduplication |
| `episodes` | Conversation summaries + embeddings |
| `conversation_log` | Full conversation transcripts |
| `google_tokens` | Encrypted OAuth tokens |
| `oauth_pending_states` | Nonce store (1hr TTL) |
| `job_queue` | Persistent job tracking for worker (dedup) |
| `web_conversations` | Web UI session metadata |
| `scheduled_jobs` | APScheduler jobs (one-shot + recurring); `recurrence` col nullable |
| `pending_notifications` | Queued outbound notifications |
| `activity_log` | Tool activity feed shown in web UI Activity tab |
| `proactive_cards` | Dashboard proactive suggestion cards |
| `nutrition_logs` | Meal + water entries (`source`: text/image/water) |
| `app_settings` | Key-value settings store |
| `watched_leads` | Lead monitoring state |
| `lead_messages` | Individual lead message tracking |

## Configuration

All env vars in `config.py`. Critical ones (no default, must be set):
- `DATABASE_URL`, `GEMINI_API_KEY`, `WEBHOOK_SECRET`, `TEST_TOKEN`, `DB_ENCRYPTION_KEY`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MY_WHATSAPP_ID`
- `WAHA_API_KEY`, `ALLOWED_ORIGIN`, `TUYA_ACCESS_ID`, `TUYA_ACCESS_KEY`

## Known Architectural Constraints

1. **Single-instance only** — Obsidian vault uses per-file threading.Lock; won't work with replicas
2. **Recursion limit = 25** — Hard cap on agent↔tool loops per invocation
3. **Checkpointer pool = 10 connections** — Can starve under high concurrency
4. **Tool cache per chat_id** — Built once on first invocation; call `clear_tools_cache(chat_id)` after OAuth changes
5. **In-memory queue** — Lost on crash; DB recovery fills the gap but in-flight messages drop
6. **Ollama required** — Episode creation and embeddings depend on local Ollama being healthy
7. **Single-user nutrition** — `_nutrition_key()` always returns `"default"`; all devices/sessions share one log
