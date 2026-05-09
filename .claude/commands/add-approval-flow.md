# /add-approval-flow

Add a human-in-the-loop approval step to a LangGraph tool or node.

## Usage

```
/add-approval-flow <tool-name-or-node>
```

## Context

The PA graph is in `backend/app/graph/`. The tool node is `tool_executor_node` in
`backend/app/graph/tool_node.py`. LangGraph supports interrupt-before patterns via
`interrupt_before=[<node>]` in `builder.compile(...)`.

## Steps

1. Identify which tool or node needs approval.
2. Read `backend/app/graph/graph.py` and `backend/app/graph/tool_node.py`.
3. Read `backend/app/routers/web_chat.py` — this is where the WebSocket streams
   events back to the UI; approval responses need a return path.
4. Implement the approval flow:
   - Add an `approval_node` that emits a `{"type": "approval_request", ...}` event.
   - Wire the WebSocket handler to pause and wait for a `{"type": "approval_response"}` message.
   - Resume or abort the graph based on the response.
5. Add a test in `backend/tests/` that mocks the approval response.
6. Update `backend/app/static/index.html` to render the approval UI prompt.

## Constraints

- Approval state must survive a page refresh (store in PostgreSQL checkpointer, not in-memory).
- WhatsApp path (`backend/app/whatsapp.py`) should auto-approve or reject based on config flag `APPROVAL_REQUIRED=true/false`.
- Do not break the existing streaming token flow.
