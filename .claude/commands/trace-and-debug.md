# /trace-and-debug

Diagnose a broken or misbehaving LangGraph run using logs and LangSmith.

## Usage

```
/trace-and-debug "<symptom or error message>"
```

## Steps

### 1. Reproduce locally

```bash
curl -X POST http://localhost:8000/test \
  -H "Content-Type: application/json" \
  -H "X-Test-Token: $TEST_TOKEN" \
  -d '{"text": "<message that triggers the bug>"}'
```

### 2. Read the logs

```bash
docker compose logs backend --since 5m | grep -E "ERROR|WARNING|graph:"
```

Key log fields to look for: `event`, `chat_id`, `request_id`, `duration_ms`.

### 3. Check LangSmith (if enabled)

- Open the LangSmith project (`LANGSMITH_PROJECT` env var, default: `pa-assistant`).
- Find the run by `request_id` (emitted in the `graph_run_start` log line).
- Inspect the node execution chain: `inject_memory → agent → tools → reflection`.
- Look for: unexpected tool calls, empty tool results, recursion limit hits.

### 4. Common failure modes

| Symptom | Likely cause | File |
|---|---|---|
| `[No response generated]` | Agent returned only tool calls, no final text | `graph/graph.py:_last_ai_reply` |
| Infinite tool loop | `should_continue` not routing to `reflection` | `graph/tool_node.py` |
| `RecursionError` | `_RECURSION_LIMIT=25` hit | `graph/graph.py` |
| Memory not injected | `load_memory_context` returned empty | `memory/store.py` |
| Gemini timeout | LLM_TIMEOUT_SECONDS too low, or model overloaded | `llm.py`, `config.py` |
| WhatsApp message lost | Worker queue full or crashed | `worker.py` |

### 5. Fix and verify

After applying the fix, confirm with a clean run:
```bash
docker compose exec backend pytest backend/tests/ -v
```
