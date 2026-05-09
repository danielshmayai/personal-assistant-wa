# Claude Code Workflow for `pa`

This document explains how Claude Code agents, commands, and worktrees are used in this project.

## Overview

The `pa` repo uses Claude Code with a multi-agent workflow:

```
Developer intent
      |
      v
Slash command (/harden-assistant, /repo-assess, ...)
      |
      v
Claude orchestrator
      |
      +--> architecture-reviewer agent   (design questions)
      +--> backend-implementer agent     (implementation)
      +--> workflow-guardian agent       (graph/worker integrity)
      +--> security-reviewer agent       (security sign-off)
      +--> qa-validator agent            (final validation)
```

Each Claude worktree session operates on an isolated branch, so parallel work does not conflict.

## Slash commands

| Command | When to use |
|---|---|
| `/repo-assess` | Before a large refactor or unfamiliar task |
| `/safe-refactor <file>` | Improving code quality without changing behaviour |
| `/harden-assistant` | After touching auth, CORS, or secret handling |
| `/add-approval-flow` | Adding human-in-the-loop to a graph node |
| `/add-worker-queue` | Adding a new async job type |
| `/trace-and-debug "<symptom>"` | Diagnosing broken LangGraph runs |
| `/review-auth` | Before merging any OAuth or token-storage change |
| `/test-changed-scope` | Running targeted tests for the current diff |

## Agents

| Agent | Spawn when |
|---|---|
| `architecture-reviewer` | Proposing a structural change to the graph or routers |
| `backend-implementer` | Implementing a new tool, endpoint, or memory feature |
| `workflow-guardian` | Modifying graph nodes, routing, or the worker loop |
| `security-reviewer` | Any change touching auth, CORS, tokens, or dependencies |
| `qa-validator` | Before merging any branch to main |

## Worktree workflow

1. Claude Code creates a new worktree for each session: `.claude/worktrees/<name>/`.
2. Changes are made on an isolated branch: `claude/<name>`.
3. When complete, the branch is pushed and a PR is opened (or merged directly to main for housekeeping tasks).
4. The worktree is cleaned up after the branch lands.

## Key invariants Claude must never break

1. `WEBHOOK_SECRET` validation on every incoming WAHA request.
2. Fernet encryption of Google tokens before PostgreSQL writes.
3. CORS `ALLOWED_ORIGIN` — no wildcard in production.
4. Per-`chat_id` message serialisation in the worker queue.
5. LangGraph recursion limit at 25.

## Adding a new LLM tool — checklist

- [ ] Tool function in `backend/app/<domain>/tools.py`
- [ ] Docstring that tells the LLM when and how to use it
- [ ] Registered in `backend/app/graph/distiller.py`
- [ ] Unit test in `backend/tests/test_sanity.py` (import) and `test_live.py` (integration)
- [ ] `backend/app/memory/capabilities.py` regenerated on next startup (automatic)

## Debugging a bad LangGraph run

1. Get `request_id` from the `graph_run_start` log line.
2. Open LangSmith project (`LANGSMITH_PROJECT`, default: `pa-assistant`).
3. Search by `request_id` metadata.
4. Inspect node-by-node: `inject_memory → agent → tools → reflection`.
5. Use `/trace-and-debug` for a guided diagnosis.
