# /update-architecture

Update `ARCHITECTURE.md` to reflect the current state of the codebase.

## Steps

1. Read the current `ARCHITECTURE.md` to understand its structure.
2. Scan the codebase for changes since the doc was last accurate:
   - `backend/app/config.py` — env vars
   - `backend/app/graph/` — graph nodes, flow
   - `backend/app/main.py` — routers, middleware, startup
   - `backend/app/*/tools.py` + `backend/app/schedule_tool.py` + `backend/app/tts_tool.py` — tool inventory
   - `backend/app/memory/` — memory system
   - `docker-compose.yml` — services, ports, volumes
   - `backend/app/routers/` — API surface
   - `backend/app/memory/store.py` — DB schema
3. Compare current code against what ARCHITECTURE.md documents.
4. Update ARCHITECTURE.md sections that are stale:
   - Add new modules/tools/routes/tables
   - Remove references to deleted code
   - Update flow diagrams if node structure changed
   - Update constraints section if limits changed
5. Keep the same structure and style — concise tables, ASCII diagrams, no prose fluff.
6. Do NOT add speculative content — only document what exists in code right now.

## When to use

- After adding or removing tools, routers, graph nodes, or services
- After changing the database schema
- After modifying the message flow or adding new integrations
- Before a major refactor (to baseline the "before" state)
- When `/repo-assess` is run (as a companion update)
