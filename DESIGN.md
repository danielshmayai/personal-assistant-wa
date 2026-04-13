# Personal Assistant — Design Document

---

## 1. What It Does (Functional Overview)

The PA is a personal assistant you interact with through **WhatsApp**. Send it a message, it understands what you need, responds intelligently, and remembers things about you over time.

### Core capabilities

| What you send | What happens |
|---|---|
| A reminder or todo | Logged and acknowledged naturally |
| A financial note ("paid 350 for AWS") | Logged and confirmed |
| A bug report | Formatted into a structured Octane bug template |
| A general dev task or question | Answered with context |
| A correction ("no, I meant...") | The PA learns the rule and applies it going forward |

### How you interact

**Self-chat** — Open "Message Yourself" in WhatsApp and send anything. Every message goes to the PA.

**Group chat** — Prefix your message with `@bot` or `!bot`. The PA responds in the group. Messages without the prefix are ignored.

**DMs from others** — Ignored. The PA only responds to you.

### Memory

The PA builds a persistent profile of you over time:

- **Facts** — things you tell it (`preferred_language: Python`, `aws_bill: 350 NIS`)
- **Rules** — behaviors it learns from corrections (`never mock the database in tests`)

These are loaded into context on every message, so the PA always knows who it's talking to.

---

## 2. Technical Overview

### Stack at a glance

```
WhatsApp ──► WAHA ──► FastAPI Backend ──► LangGraph Pipeline ──► Ollama (local LLM)
                                    │                        │
                                    └──── PostgreSQL ◄───────┘
                                          (memory)

Cloudflare Tunnel ──► FastAPI Backend  (public HTTPS access)
```

### Services

| Service | Technology | Role |
|---|---|---|
| **Backend** | Python, FastAPI, LangGraph | Pipeline orchestration, webhook handler |
| **LLM** | Ollama + `gemma3:4b-it-qat` | Local inference, runs on GTX 1660 (4GB VRAM) |
| **Memory** | PostgreSQL 16 | Persistent storage for facts and rules |
| **WhatsApp bridge** | WAHA (WhatsApp Web JS) | Sends and receives WhatsApp messages |
| **Tunnel** | Cloudflare Zero Trust | Exposes the backend to the internet without open ports |

Everything runs locally via **Docker Compose**. No cloud AI API is used — the LLM runs on your GPU.

---

## 3. Detailed Design

### 3.1 Message Routing (whatsapp.py)

When a WhatsApp message arrives, WAHA sends a webhook POST to `/webhook/waha`. The router applies these rules in order:

```
Incoming event
    │
    ├── Not "message" or "message.any" ──► ignore
    │
    ├── Self-chat (fromMe=True, to=MY_WHATSAPP_ID) ──► send to pipeline
    │
    ├── Group chat (to ends with @g.us)
    │       ├── starts with @bot or !bot ──► strip prefix, send to pipeline
    │       └── no trigger ──► ignore
    │
    └── DM from another person ──► ignore
```

This ensures the bot never replies to strangers and never causes infinite loops by replying to its own outgoing messages.

---

### 3.2 LangGraph Pipeline (graph.py)

Every message that passes routing runs through a 4-node state machine:

```
START
  │
  ▼
inject_memory     ← loads facts + rules from PostgreSQL into state
  │
  ▼
distiller         ← classifies the message into a structured intent
  │
  ▼
formatter         ← generates the reply based on intent + memory
  │
  ▼
reflection        ← checks if the message is a correction; saves new rules
  │
  ▼
END
```

**State** (`PAState`) — passed between every node:

| Field | Type | Description |
|---|---|---|
| `user_input` | str | Original message text |
| `chat_id` | str | WhatsApp chat ID for routing the reply |
| `intent` | DistilledIntent | Structured classification (category, is_bug, summary) |
| `memory_context` | str | Formatted block of facts + rules injected into the system prompt |
| `reply` | str | Final reply text sent back to WhatsApp |

---

### 3.3 Distiller Node (distiller.py)

Classifies the raw message into one of three categories using structured LLM output:

| Category | When |
|---|---|
| `Development_Task` | Coding, bugs, PRs, deployments, tech questions |
| `Financial_Log` | Expenses, payments, invoices, budgets |
| `General_Reminder` | Reminders, todos, anything else |

Also sets `is_bug: true` when a `Development_Task` is specifically a bug report.

**Structured output** — the LLM is forced to return a `DistilledIntent` JSON schema via `llm.with_structured_output()`. If parsing fails, falls back to `General_Reminder`.

---

### 3.4 Formatter Node (distiller.py)

Generates the reply based on the distilled intent:

**Bug report path** (`Development_Task` + `is_bug=True`)
- Runs a separate prompt that extracts and formats the bug into the Octane template:
  ```
  ACTUAL BEHAVIOR:
  EXPECTED BEHAVIOR:
  HOW-TO-REPRODUCE:
  Env URL:
  Octane / OPB / Sync builds:
  ```

**All other intents**
- Builds a system prompt with `"You are a helpful personal assistant."` + the memory context block
- Invokes the LLM with the original user message
- Returns the natural language response

---

### 3.5 Memory System (store.py, manager.py)

**Storage** — PostgreSQL with two tables:

```sql
memory_facts
  id, key (unique), value, source, created_at, updated_at

memory_rules
  id, rule (unique), reason, source, created_at
```

**Read path** — `load_memory_context()` is called at the start of every pipeline run. It fetches the 50 most recent facts and 30 most recent rules, formats them as a text block, and injects into the system prompt:

```
## Known Facts
- preferred_language: Python
- aws_bill: 350 NIS

## Rules & Preferences
- Never mock the database in tests (reason: prior migration incident)
```

**Write path — two sources:**

1. **Agent tools** (`manager.py`) — `save_fact` and `save_rule` are LangChain tools that the LLM can call explicitly when the user states something worth remembering.

2. **Reflection node** — runs after every reply and automatically detects corrections (see 3.6).

---

### 3.6 Reflection Node (reflection.py)

Runs after every formatter response. Lightweight — only fires when the user's message contains correction signals (`no,`, `wrong`, `actually`, `don't`, `i meant`, etc.).

If a correction signal is found, the LLM analyzes the exchange and responds in one of two ways:

```
CORRECTION_DETECTED
RULE: Never use mocks for database tests
REASON: Prior incident where mocked tests passed but prod migration failed

— or —

NO_CORRECTION
```

If `CORRECTION_DETECTED`, the rule is saved to `memory_rules` with `source="reflection"`.

This is non-fatal — any exception in the reflection node is logged and ignored so it never blocks the reply.

---

### 3.7 LLM Configuration (llm.py)

Model: `gemma3:4b-it-qat` (4-bit quantized, ~2.5GB VRAM)

| Setting | Value | Why |
|---|---|---|
| `temperature` | 0.3 | Consistent, low-creativity output |
| `num_ctx` | 2048 | Fits in 4GB VRAM without OOM |
| `keep_alive` | 2 min | Stays hot in VRAM between requests |
| `timeout` | 120s | First request is slow while model loads |

The LLM is accessed via `langchain_ollama.ChatOllama`. All inference is local — no external API calls.

---

### 3.8 Infrastructure (docker-compose.yml)

Two Docker Compose profiles:

**Dev** (`docker compose up -d --build`) — 3 services:
- `pa-postgres`, `pa-ollama`, `pa-backend`
- No WhatsApp, no public access
- Test the pipeline at `POST /test`

**Prod** (`docker compose --profile prod up -d --build`) — 5 services:
- All dev services + `pa-waha` + `pa-cloudflared`
- Full WhatsApp integration with public tunnel

Startup order enforced by `depends_on`:
```
pa-postgres (healthy)
    └── pa-ollama (healthy)
            └── pa-backend (healthy)
                    └── pa-cloudflared
```

All containers have `restart: always` — they recover automatically after reboots.

---

## 4. Data Flow (End to End)

```
1. You send a WhatsApp message to yourself
2. WhatsApp → WAHA receives it (WebJS session)
3. WAHA POSTs to http://backend:8000/webhook/waha
4. Routing: self-chat check → passes
5. LangGraph starts:
   a. inject_memory: loads your facts + rules from PostgreSQL
   b. distiller: LLM classifies the message (e.g., Development_Task, is_bug=true)
   c. formatter: LLM formats the Octane bug template with your input
   d. reflection: checks for corrections → none found → skips
6. Reply text → WAHA API → sendText → WhatsApp → delivered to you
```

---

## 5. Key Design Decisions

| Decision | Rationale |
|---|---|
| Local LLM (Ollama) instead of OpenAI API | Privacy, no per-token cost, works offline |
| Quantized model (`q4` + `qat`) | Fits in 4GB VRAM with acceptable quality |
| LangGraph for orchestration | Clean node/state model, easy to add new pipeline steps |
| PostgreSQL for memory | Persistent, queryable, survives container restarts |
| WAHA instead of official WhatsApp API | No business account or Meta approval required |
| Cloudflare Tunnel instead of open ports | No firewall changes, no exposed IP, encrypted by default |
| Reflection node for passive learning | User doesn't need to explicitly teach the PA — it learns from corrections automatically |
