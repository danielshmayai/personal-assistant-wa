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
                   │          │ agent (Gemini 2.5 Flash + 25 tools)  │
                   │          │   ↓              ↑                   │
                   │          │ tools ────────────┘ (parallel exec)  │
                   │          │   ↓                                  │
                   │          │ reflection (extract facts/rules)     │
                   │          │   ↓                                  │
                   │          │ END → reply                          │
                   │          └─────────────────────────────────────┘
                   │
                   ├── /ws (WebSocket streaming for Web UI)
                   ├── /auth/google/* (OAuth2 flow)
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
├── graph/
│   ├── graph.py            # LangGraph assembly, run_graph(), stream_graph()
│   ├── distiller.py        # agent_node: system prompt, tool binding, Gemini invoke
│   └── tool_node.py        # Parallel tool executor (asyncio.gather)
├── memory/
│   ├── manager.py          # 10 LangChain tools for vault operations
│   ├── obsidian.py         # Filesystem I/O, file locking, path traversal prevention
│   ├── store.py            # PostgreSQL tables, embeddings, token storage
│   ├── reflection.py       # Post-reply fact/rule extraction
│   ├── episodes.py         # Conversation summarization + embedding
│   ├── embeddings.py       # 768-dim pgvector operations
│   └── self_review.py      # Periodic conversation reflection
├── web/
│   └── tools.py            # web_search, wikipedia, fetch_url, weather, wa_link
├── google/
│   ├── auth.py             # OAuth2 flow, token refresh
│   ├── tools.py            # gmail_read/send, calendar_list/create, drive
│   └── drive_tools.py      # save_photo, save_document, list_files
├── tuya/
│   └── tools.py            # list_devices, get_status, control_device
├── routers/
│   ├── web_chat.py         # WebSocket streaming for web UI
│   ├── leads.py            # Lead watching
│   └── smart_home.py       # Smart-home admin endpoints
├── schedule_tool.py        # schedule_reminder, schedule_tuya, list, cancel
├── tts_tool.py             # Text-to-speech voice configuration
├── scheduled_jobs.py       # APScheduler-based recurring tasks
├── job_queue.py            # DB-backed job persistence
├── crypto.py               # Fernet encrypt/decrypt for OAuth tokens
└── checkpointer.py         # PostgreSQL AsyncPostgresSaver (pool of 10)
```

## Tool Inventory (~25 tools)

| Category | Tools | Source |
|----------|-------|--------|
| **Web** (5) | web_search, wikipedia_search, fetch_url, get_weather, build_whatsapp_link | `web/tools.py` |
| **Google** (6) | google_connect, gmail_read, gmail_send, calendar_list, calendar_create, drive ops | `google/tools.py` + `drive_tools.py` |
| **Tuya** (3) | list_tuya_devices, get_device_status, control_device | `tuya/tools.py` |
| **Memory** (10) | save_fact, update_rule, retrieve_context, list_memory, hide_fact, hide_rule, search_vault, grep_note, read_note, append_to_note | `memory/manager.py` |
| **Schedule** (4) | schedule_tuya_command, schedule_reminder, list_scheduled, cancel_scheduled | `schedule_tool.py` |
| **TTS** (1) | configure_tts | `tts_tool.py` |

Tools are gathered dynamically in `distiller.py` and `tool_node.py` on each agent invocation.

## Data Flow Details

### Message Processing (WhatsApp)
1. WAHA POSTs to `/webhook/{secret}` → `whatsapp.py` validates secret
2. L1 dedup: in-memory `_processed_ids` dict (1hr TTL)
3. L2 dedup: `job_queue.persist_job()` in PostgreSQL
4. Message enqueued to `asyncio.Queue` in `worker.py`
5. Worker calls `run_graph(text, chat_id)` with 4-attempt retry (1s, 5s, 15s delays)
6. Reply sent via WAHA HTTP API

### Message Processing (Web UI)
1. WebSocket connection to `/ws` with session auth
2. `stream_graph()` yields events: `thinking`, `token`, `tool_start`, `tool_end`, `done`
3. No queue — direct async streaming per connection

### Memory System
- **Obsidian Vault** (host-mounted): Human-readable markdown files organized by category (People, Entities, Investments, Projects, Preferences, Misc, System)
- **PostgreSQL pgvector**: 768-dim embeddings for semantic search on vault content, rules, and episodes
- **Reflection**: After each reply, extracts corrections/facts/rules/preferences via LLM
- **Episodes**: Background summarization of conversations for long-term retrieval via Ollama

### Authentication (Google)
1. User triggers `google_connect` tool → generates OAuth URL with PKCE nonce
2. User visits URL → Google redirects to `/auth/google/callback`
3. Tokens Fernet-encrypted with `DB_ENCRYPTION_KEY` → stored in `google_tokens` table
4. Auto-refresh on expiry via `google-auth-oauthlib`

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
| `job_queue` | Persistent job tracking for worker |
| `web_conversations` | Web UI session metadata |
| `watched_leads` | Lead monitoring state |

## Configuration

All env vars in `config.py`. Critical ones (no default, must be set):
- `DATABASE_URL`, `GEMINI_API_KEY`, `WEBHOOK_SECRET`, `TEST_TOKEN`, `DB_ENCRYPTION_KEY`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MY_WHATSAPP_ID`
- `WAHA_API_KEY`, `ALLOWED_ORIGIN`, `TUYA_ACCESS_ID`, `TUYA_ACCESS_KEY`

## Known Architectural Constraints

1. **Single-instance only** — Obsidian vault uses per-file threading.Lock; won't work with replicas
2. **Recursion limit = 25** — Hard cap on agent↔tool loops per invocation
3. **Checkpointer pool = 10 connections** — Can starve under high concurrency
4. **Tool binding is per-invoke** — Tools list rebuilt on every agent call (not cached)
5. **In-memory queue** — Lost on crash; DB recovery fills the gap but in-flight messages drop
6. **Ollama required** — Episode creation and embeddings depend on local Ollama being healthy
