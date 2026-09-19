# Using phantom-tap from the danidin assistant

phantom-tap runs as its own service. The assistant talks to it through a thin
CLI-wrapping tool, never by joining the race.

## Why the split

The booking race is decided in the first few hundred milliseconds after a
server-published T0. The LangGraph agent waits on Gemini (1–5s, non-deterministic)
and can recurse further still. Anything that waits on the model cannot be on the
T0 path. So:

- **phantom-tap daemon** does the racing — deterministic, clock-synced, no model.
- **danidin** schedules and reports via `integration/danidin_tool.py` — it can tell
  you when the next window opens and whether last week's booking landed, but it
  cannot fire a booking itself.

## Wiring

1. Copy `danidin_tool.py` where the assistant can import it, and set `PT`/`CWD`.
2. Register it in `backend/app/graph/tools_registry.py` alongside the others:

   ```python
   from integration.danidin_tool import get_phantom_tools
   if on("phantom"):
       tools += get_phantom_tools(chat_id)
   ```

3. Reuse the assistant's existing WAHA for booking alerts: set `notify.backend =
   "waha"` in `booking.toml` and point it at the same container. No second bot.

## Note on the shared engine

Per the danidin project's CLAUDE.md, anything under `backend/app/` runs in both the
old and new assistants. This tool is additive and read-only toward the graph, so it
touches neither — it only adds three optional, read-only tools behind a `phantom`
module flag.
