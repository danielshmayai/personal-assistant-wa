# PA (danidin) — Claude Code Project Instructions

## What this repo is

`pa` is a self-hosted personal AI assistant named **danidin**.
It runs as a Docker Compose stack: FastAPI backend, Ollama (local LLM fallback),
WAHA (WhatsApp HTTP API), and PostgreSQL. The primary LLM is Google Gemini 2.5.

```
User (WhatsApp / Web UI)
        │
        ▼
WAHA webhook → FastAPI → async worker queue
                              │
                              ▼
                    LangGraph graph
                    START → inject_memory → agent ⟵──┐
                                            │         │
                                    tools / reflection │
                                            └──────────┘
                                                │
                                            reflection → END
```

## The two assistants — shared vocabulary

This repo ships **two** assistants that share one engine. When the user
says where they want a change, map it as follows:

| Term the user uses | Means | Touch |
|---|---|---|
| "old assistant" / "the WhatsApp one" / "8000" | Original single-user WhatsApp engine | `docker-compose.yml` (project `pa`), `backend/app/` (routers, worker, WAHA) |
| "new assistant" / "product" / "multi-tenant" / "8080" | Multi-tenant web platform that *wraps* the engine | `docker-compose.product.yml` (project `pa-product`), `product/`, `frontend/` |
| "both" / "the shared engine" | Code under `backend/app/` used by both | graph, tools, memory |

Key fact: the **new** assistant wraps the **old** one. Everything in
`backend/app/` (graph, tools, memory) runs in BOTH. So a change to a
tool / the graph / memory affects both assistants — flag this to the
user and confirm scope. WhatsApp/worker/WAHA changes are old-only;
tenants/login/secrets/settings-UI changes are new-only.

Run them isolated (product must start after the engine — it shares the
engine's Postgres over the external `pa_pa-net` network):
```bash
docker compose up -d                                          # old, :8000
docker compose -f docker-compose.product.yml -p pa-product up -d  # new, :8080
```

## Tech stack

| Layer | Tech |
|---|---|
| Backend | FastAPI 0.11+, Python 3.12 |
| Orchestration | LangGraph 0.3+, LangChain core |
| LLM | Gemini 2.5 Flash via `langchain-google-genai` |
| Memory | PostgreSQL (pgvector + checkpointing) + Obsidian vault |
| Messaging | WAHA (WhatsApp HTTP API) |
| Tracing | LangSmith (optional) |
| Auth | Google OAuth2 (Calendar, Gmail, Drive) |
| Smart-home | Tuya OpenAPI |
| Deploy | Docker Compose + Cloudflare Tunnel |

## Key files

- `backend/app/main.py` — FastAPI app, startup, routers
- `backend/app/graph/graph.py` — LangGraph graph assembly + `run_graph` / `stream_graph`
- `backend/app/graph/distiller.py` — `agent_node`, system prompt, tool binding
- `backend/app/graph/tool_node.py` — parallel tool executor
- `backend/app/worker.py` — async queue that serialises WhatsApp messages per chat_id
- `backend/app/memory/` — Obsidian vault, pgvector, episodes, reflections
- `backend/app/config.py` — all env-var configuration (source of truth)
- `docker-compose.yml` — service definitions and volumes
- `scripts/` — deploy, setup, security check

## Dev conventions

- All env vars live in `.env` (never committed). See `.env.example`.
- Run locally with `docker compose up --build`.
- Test the graph without WhatsApp: `POST /test` with `X-Test-Token`.
- Structured JSON logging by default (`LOG_FORMAT=json`); set `LOG_FORMAT=text` for dev.
- `correlation_id` / `request_id` is threaded through all log lines.
- LangSmith tracing is opt-in via `LANGSMITH_API_KEY`.

## Security invariants — never break these

1. `WEBHOOK_SECRET` must be in every WAHA webhook URL.
2. CORS is locked to `ALLOWED_ORIGIN` — no wildcard in production.
3. Google tokens are Fernet-encrypted at rest (`DB_ENCRYPTION_KEY`).
4. `/test` and `/admin/*` require `X-Test-Token` header.
5. The graph's `recursion_limit` is 25 — never raise it without benchmarking.

## How to run tests

```bash
docker compose exec backend pytest backend/tests/ -v
```

`test_sanity.py` — import/startup checks (fast, always run)
`test_worker.py` — queue serialisation tests
`test_live.py` — end-to-end graph tests (requires `.env` with real keys)

## Architecture reference

See `ARCHITECTURE.md` in the repo root for full system documentation:
module map, data flows, tool inventory, DB schema, and known constraints.
Run `/update-architecture` after structural changes to keep it current.

## Agents & commands

- Use `/repo-assess` before large refactors.
- Use `/update-architecture` after adding tools, routes, services, or schema changes.
- Use `/harden-assistant` when touching auth, CORS, or WEBHOOK_SECRET handling.
- Use `/review-auth` before merging any Google OAuth or token-storage changes.
- Use `/trace-and-debug` when diagnosing LangGraph or LangSmith trace issues.
- Spawn `architecture-reviewer` agent for structural design questions.
- Spawn `security-reviewer` agent before any security-sensitive PR.
