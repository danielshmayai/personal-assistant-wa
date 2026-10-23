import asyncio
import logging
import psycopg2
from app.config import DATABASE_URL

logger = logging.getLogger("pa.memory")


def _get_conn():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set — cannot connect to PostgreSQL")
    return psycopg2.connect(DATABASE_URL)


def _tid() -> str:
    """Tenant scope for every memory row ('' = owner/legacy data)."""
    from app.context import current_tenant_id
    return current_tenant_id.get()


def _pk_cols(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT a.attname FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = %s::regclass AND i.indisprimary
        """,
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


def _ensure_tenant_scoping(cur) -> None:
    """Idempotent migration: add tenant_id ('' = owner) and composite keys.

    Existing single-user data keeps tenant_id='' so the owner path is
    untouched; product tenants get their own keyspace.
    """
    for table in (
        "vault_embeddings", "memory_facts", "memory_rules", "rule_embeddings",
        "episodes", "conversation_log", "web_conversations",
    ):
        cur.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT ''"
        )

    if _pk_cols(cur, "vault_embeddings") != {"tenant_id", "filepath"}:
        cur.execute("ALTER TABLE vault_embeddings DROP CONSTRAINT IF EXISTS vault_embeddings_pkey")
        cur.execute("ALTER TABLE vault_embeddings ADD PRIMARY KEY (tenant_id, filepath)")
    if _pk_cols(cur, "rule_embeddings") != {"tenant_id", "rule_text"}:
        cur.execute("ALTER TABLE rule_embeddings DROP CONSTRAINT IF EXISTS rule_embeddings_pkey")
        cur.execute("ALTER TABLE rule_embeddings ADD PRIMARY KEY (tenant_id, rule_text)")

    cur.execute("ALTER TABLE memory_facts DROP CONSTRAINT IF EXISTS memory_facts_key_key")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS memory_facts_tenant_key_uq ON memory_facts(tenant_id, key)"
    )
    cur.execute("ALTER TABLE memory_rules DROP CONSTRAINT IF EXISTS memory_rules_rule_key")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS memory_rules_tenant_rule_uq ON memory_rules(tenant_id, rule)"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS episodes_tenant_idx ON episodes(tenant_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS conversation_log_tenant_idx ON conversation_log(tenant_id)")

    cur.execute(
        "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT ''"
    )
    if _pk_cols(cur, "app_settings") != {"tenant_id", "key"}:
        cur.execute("ALTER TABLE app_settings DROP CONSTRAINT IF EXISTS app_settings_pkey")
        cur.execute("ALTER TABLE app_settings ADD PRIMARY KEY (tenant_id, key)")


def init_memory_tables():
    """Create memory tables if they don't exist (idempotent)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vault_embeddings (
                    filepath TEXT PRIMARY KEY,
                    embedding vector(768) NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_facts (
                    id SERIAL PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT NOT NULL,
                    source TEXT DEFAULT 'user',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_rules (
                    id SERIAL PRIMARY KEY,
                    rule TEXT UNIQUE NOT NULL,
                    reason TEXT DEFAULT '',
                    source TEXT DEFAULT 'reflection',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oauth_pending_states (
                    nonce TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS google_tokens (
                    chat_id TEXT PRIMARY KEY,
                    access_token TEXT,
                    refresh_token TEXT,
                    token_expiry TIMESTAMP,
                    scopes TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rule_embeddings (
                    rule_text TEXT PRIMARY KEY,
                    embedding vector(768) NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id SERIAL PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    embedding vector(768),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversation_log (
                    id SERIAL PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS web_conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT 'New conversation',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS watched_leads (
                    id SERIAL PRIMARY KEY,
                    chat_id TEXT UNIQUE NOT NULL,
                    phone TEXT NOT NULL,
                    label TEXT DEFAULT '',
                    obsidian_note TEXT DEFAULT '',
                    extracted_data JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    last_activity TIMESTAMPTZ
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lead_messages (
                    id SERIAL PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    text TEXT NOT NULL,
                    ts TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS lead_messages_chat_id_idx ON lead_messages(chat_id)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            _ensure_tenant_scoping(cur)
            # What the assistant learned, when, and where it was filed. Feeds
            # the "learned yesterday" brief line, the Recently-learned feed and
            # the memory health numbers. Content stays in the vault; this keeps
            # a short copy so the feed works without scanning files.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_events (
                    id SERIAL PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    entity TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'extractor',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS memory_events_tenant_time_idx "
                "ON memory_events(tenant_id, created_at DESC)"
            )
        conn.commit()
    finally:
        conn.close()
    from app.job_queue import init_table as _init_job_queue
    _init_job_queue()
    from app.scheduled_jobs import init_table as _init_scheduled_jobs
    _init_scheduled_jobs()


def save_google_token(chat_id: str, creds) -> None:
    from app.crypto import encrypt
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if creds.refresh_token:
                cur.execute("""
                    INSERT INTO google_tokens (chat_id, access_token, refresh_token, token_expiry, scopes)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (chat_id) DO UPDATE
                    SET access_token = EXCLUDED.access_token,
                        refresh_token = EXCLUDED.refresh_token,
                        token_expiry = EXCLUDED.token_expiry,
                        scopes = EXCLUDED.scopes
                """, (
                    chat_id,
                    encrypt(creds.token or ""),
                    encrypt(creds.refresh_token),
                    creds.expiry,
                    ",".join(creds.scopes) if creds.scopes else "",
                ))
            else:
                # refresh_token absent (normal on token-refresh responses) — preserve existing
                cur.execute("""
                    INSERT INTO google_tokens (chat_id, access_token, refresh_token, token_expiry, scopes)
                    VALUES (%s, %s, '', %s, %s)
                    ON CONFLICT (chat_id) DO UPDATE
                    SET access_token = EXCLUDED.access_token,
                        token_expiry = EXCLUDED.token_expiry,
                        scopes = EXCLUDED.scopes
                """, (
                    chat_id,
                    encrypt(creds.token or ""),
                    creds.expiry,
                    ",".join(creds.scopes) if creds.scopes else "",
                ))
        conn.commit()
        logger.info("Saved Google token for chat_id=%s", chat_id)
    finally:
        conn.close()


def delete_google_token(chat_id: str) -> None:
    """Remove a stored Google token (forces re-auth on next Google tool call)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM google_tokens WHERE chat_id = %s", (chat_id,))
        conn.commit()
        logger.info("Deleted Google token for chat_id=%s", chat_id)
    finally:
        conn.close()


def load_google_token(chat_id: str) -> dict | None:
    from app.crypto import decrypt
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT access_token, refresh_token, token_expiry, scopes FROM google_tokens WHERE chat_id = %s",
                (chat_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "access_token": decrypt(row[0]) if row[0] else "",
                "refresh_token": decrypt(row[1]) if row[1] else "",
                "token_expiry": row[2],
                "scopes": row[3],
            }
    finally:
        conn.close()


def upsert_fact(key: str, value: str, source: str = "user"):
    """Insert or update a fact."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO memory_facts (tenant_id, key, value, source, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (tenant_id, key) DO UPDATE
                SET value = EXCLUDED.value,
                    source = EXCLUDED.source,
                    updated_at = NOW()
            """, (_tid(), key, value, source))
        conn.commit()
        logger.info("Upserted fact: %s", key)
    finally:
        conn.close()


def insert_rule(rule: str, reason: str = "", source: str = "reflection"):
    """Insert a new rule/preference. Ignores duplicates."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO memory_rules (tenant_id, rule, reason, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id, rule) DO NOTHING
            """, (_tid(), rule, reason, source))
        conn.commit()
        logger.info("Inserted rule: %.80s", rule)
    finally:
        conn.close()


def get_all_facts() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value FROM memory_facts WHERE tenant_id = %s ORDER BY updated_at DESC", (_tid(),))
            return [{"key": r[0], "value": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def get_all_facts_with_ids() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, key, value FROM memory_facts WHERE tenant_id = %s ORDER BY updated_at DESC", (_tid(),))
            return [{"id": r[0], "key": r[1], "value": r[2]} for r in cur.fetchall()]
    finally:
        conn.close()


def get_all_rules() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rule, reason FROM memory_rules WHERE tenant_id = %s ORDER BY created_at DESC", (_tid(),))
            return [{"rule": r[0], "reason": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def get_all_rules_with_ids() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, rule, reason FROM memory_rules WHERE tenant_id = %s ORDER BY created_at DESC", (_tid(),))
            return [{"id": r[0], "rule": r[1], "reason": r[2]} for r in cur.fetchall()]
    finally:
        conn.close()


def delete_fact(key: str) -> bool:
    """Delete a fact by key. Returns True if a row was deleted."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memory_facts WHERE tenant_id = %s AND key = %s", (_tid(), key))
            deleted = cur.rowcount > 0
        conn.commit()
        if deleted:
            logger.info("Deleted fact: %s", key)
        return deleted
    finally:
        conn.close()


def delete_rule(rule_id: int) -> bool:
    """Delete a rule by its numeric ID. Returns True if a row was deleted."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memory_rules WHERE tenant_id = %s AND id = %s", (_tid(), rule_id))
            deleted = cur.rowcount > 0
        conn.commit()
        if deleted:
            logger.info("Deleted rule id=%s", rule_id)
        return deleted
    finally:
        conn.close()


# Injection budgets (characters). Profile and rules are small and always
# relevant; notes and past conversations are query-dependent.
_PROFILE_BUDGET = 1500
_RULES_BUDGET = 1500
_FACTS_BUDGET = 2500
_EPISODES_BUDGET = 1000

# Last retrieval outcome per process — counts only, for the health panel.
LAST_RETRIEVAL: dict = {}


async def load_memory_context(query: str = "", recent: str = "") -> str:
    """Build the long-term-memory block injected into the system prompt.

    Order: user profile → active rules → relevant notes → past conversations.
    `recent` is the previous turn(s) so short follow-ups ("and her?") still
    retrieve the right notes. One query embedding serves notes and episodes;
    hits below MEMORY_MIN_SIMILARITY are dropped instead of padding the prompt
    with the 8 nearest-but-unrelated notes. Falls back to keyword search when
    semantic search finds nothing (or embeddings are unavailable).
    """
    from app.config import MEMORY_MIN_SIMILARITY
    from app.memory import obsidian
    from app.memory.embeddings import embed_query, semantic_search, vec_str
    from app.runtime import in_thread

    profile = await in_thread(obsidian.read_profile, _PROFILE_BUDGET)
    rules = await in_thread(obsidian.read_rules, _RULES_BUDGET)
    profile_rel = f"{obsidian.PROFILE_CATEGORY}/{obsidian.PROFILE_ENTITY}.md"

    facts = ""
    episodes: list[str] = []
    semantic_hits = 0
    search_text = f"{recent}\n{query}".strip() if recent else (query or "").strip()
    if search_text:
        vec = await in_thread(embed_query, search_text)
        if vec:
            hits = await in_thread(
                lambda: semantic_search(limit=6, vec=vec, min_similarity=MEMORY_MIN_SIMILARITY)
            )
            paths = [p for p, _ in hits if not p.startswith("System/") and p != profile_rel]
            semantic_hits = len(paths)
            if paths:
                facts = await in_thread(obsidian.read_facts_by_paths, paths, _FACTS_BUDGET)
            try:
                episodes = await in_thread(
                    search_episodes, vec_str(vec), 3, MEMORY_MIN_SIMILARITY
                )
            except Exception:
                logger.warning("episode search failed", exc_info=True)
        if not facts and query:
            facts = await in_thread(
                lambda: obsidian.read_relevant_facts(query, _FACTS_BUDGET, exclude={profile_rel})
            )

    parts: list[str] = []
    if profile:
        parts.append("### About the user\n" + profile)
    if rules:
        parts.append("### Rules the user gave you (always follow)\n" + rules)
    if facts:
        parts.append("### Relevant notes\n" + facts)
    if episodes:
        ep_text = "\n".join(f"- {e}" for e in episodes)[:_EPISODES_BUDGET]
        parts.append("### Related past conversations\n" + ep_text)

    LAST_RETRIEVAL.update({
        "profile": bool(profile), "rules": len(rules.splitlines()) if rules else 0,
        "semantic_hits": semantic_hits, "facts": bool(facts), "episodes": len(episodes),
    })
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# OAuth pending state — persisted in Postgres so restarts don't break flows
# ---------------------------------------------------------------------------

def save_oauth_state(nonce: str, chat_id: str) -> None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO oauth_pending_states (nonce, chat_id) VALUES (%s, %s) ON CONFLICT (nonce) DO NOTHING",
                (nonce, chat_id),
            )
        conn.commit()
    finally:
        conn.close()


def pop_oauth_state(nonce: str) -> str | None:
    """Return and delete the chat_id for the given nonce. Returns None if not found or expired (>1 hour).

    Prefer peek_oauth_state + delete_oauth_state when you need to retry on failure.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM oauth_pending_states WHERE nonce = %s AND created_at > NOW() - INTERVAL '1 hour' RETURNING chat_id",
                (nonce,),
            )
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    finally:
        conn.close()


def peek_oauth_state(nonce: str) -> str | None:
    """Return the chat_id for a nonce without deleting it. Returns None if not found or expired (>1 hour)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT chat_id FROM oauth_pending_states WHERE nonce = %s AND created_at > NOW() - INTERVAL '1 hour'",
                (nonce,),
            )
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def delete_oauth_state(nonce: str) -> None:
    """Delete a nonce after successful token exchange."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM oauth_pending_states WHERE nonce = %s", (nonce,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Web conversation index
# ---------------------------------------------------------------------------

def upsert_web_conversation(chat_id: str, title: str | None = None) -> None:
    """Create or touch a web conversation. Only updates title if still at default."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if title:
                cur.execute("""
                    INSERT INTO web_conversations (id, tenant_id, title, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE
                    SET title = CASE
                            WHEN web_conversations.title = 'New conversation'
                            THEN EXCLUDED.title
                            ELSE web_conversations.title
                        END,
                        updated_at = NOW()
                """, (chat_id, _tid(), title))
            else:
                cur.execute("""
                    INSERT INTO web_conversations (id, tenant_id, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
                """, (chat_id, _tid()))
        conn.commit()
    finally:
        conn.close()


def delete_web_conversation(chat_id: str) -> bool:
    """Delete a web conversation record. Returns True if a row was removed."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM web_conversations WHERE tenant_id = %s AND id = %s", (_tid(), chat_id))
            deleted = cur.rowcount > 0
        conn.commit()
        if deleted:
            logger.info("Deleted conversation: %s", chat_id)
        return deleted
    finally:
        conn.close()


def save_episode(chat_id: str, summary: str, vec_str: str | None = None) -> None:
    """Store an LLM-generated conversation summary, optionally with its embedding."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if vec_str:
                cur.execute(
                    "INSERT INTO episodes (tenant_id, chat_id, summary, embedding) VALUES (%s, %s, %s, %s::vector)",
                    (_tid(), chat_id, summary, vec_str),
                )
            else:
                cur.execute(
                    "INSERT INTO episodes (tenant_id, chat_id, summary) VALUES (%s, %s, %s)",
                    (_tid(), chat_id, summary),
                )
        conn.commit()
    finally:
        conn.close()


def search_episodes(vec_str: str, limit: int = 3, min_similarity: float = 0.0) -> list[str]:
    """Episode summaries ranked by cosine similarity, at or above `min_similarity`."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT summary, 1 - (embedding <=> %s::vector) AS sim, created_at FROM episodes
                WHERE tenant_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vec_str, _tid(), vec_str, limit),
            )
            return [
                f"({r[2]:%Y-%m-%d}) {r[0]}" if r[2] else r[0]
                for r in cur.fetchall() if float(r[1]) >= min_similarity
            ]
    finally:
        conn.close()


def log_conversation(chat_id: str, transcript: str) -> None:
    """Persist a finished conversation transcript for self-review."""
    if not transcript.strip():
        return
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversation_log (tenant_id, chat_id, transcript) VALUES (%s, %s, %s)",
                (_tid(), chat_id, transcript),
            )
        conn.commit()
    finally:
        conn.close()


def get_recent_conversations(hours: int = 24) -> list[dict]:
    """Return transcripts logged in the last N hours, newest first."""
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT chat_id, transcript, created_at
                FROM conversation_log
                WHERE tenant_id = %s AND created_at > %s
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (_tid(), cutoff),
            )
            return [
                {"chat_id": r[0], "transcript": r[1], "created_at": r[2].isoformat()}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def list_web_conversations() -> list[dict]:
    """Return all web conversations ordered by most recently active."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, created_at, updated_at
                FROM web_conversations
                WHERE tenant_id = %s
                ORDER BY updated_at DESC
                LIMIT 50
            """, (_tid(),))
            return [
                {
                    "id": r[0],
                    "title": r[1],
                    "created_at": r[2].isoformat() if r[2] else None,
                    "updated_at": r[3].isoformat() if r[3] else None,
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


# ── app_settings ──────────────────────────────────────────────────────────────

def get_app_setting(key: str):
    """Return the JSONB value for *key* in the current tenant scope, or None."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM app_settings WHERE tenant_id = %s AND key = %s",
                (_tid(), key),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def set_app_setting(key: str, value) -> None:
    """Upsert a JSONB *value* for *key* in the current tenant scope."""
    import json as _json
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO app_settings (tenant_id, key, value, updated_at)
                VALUES (%s, %s, %s::jsonb, NOW())
                ON CONFLICT (tenant_id, key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
            """, (_tid(), key, _json.dumps(value)))
        conn.commit()
    finally:
        conn.close()

# ── memory_events ────────────────────────────────────────────────────────────

def record_memory_event(kind: str, entity: str = "", path: str = "", content: str = "",
                        source: str = "extractor") -> None:
    """Note that something was learned. Never raises — memory must not break chat."""
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO memory_events (tenant_id, kind, entity, path, content, source) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (_tid(), kind, entity[:200], path[:300], content[:500], source),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("record_memory_event failed", exc_info=True)


def recent_memory_events(hours: int = 24, kinds: tuple[str, ...] | None = None,
                         limit: int = 50) -> list[dict]:
    """What was learned in the last `hours`, newest first."""
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    sql = ("SELECT id, kind, entity, path, content, source, created_at FROM memory_events "
           "WHERE tenant_id = %s AND created_at > %s")
    args: list = [_tid(), cutoff]
    if kinds:
        sql += " AND kind = ANY(%s)"
        args.append(list(kinds))
    sql += " ORDER BY created_at DESC LIMIT %s"
    args.append(limit)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return [
                {"id": r[0], "kind": r[1], "entity": r[2], "path": r[3], "content": r[4],
                 "source": r[5], "created_at": r[6].isoformat() if r[6] else None}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def delete_memory_event(event_id: int) -> dict | None:
    """Remove one feed entry (scoped); returns the deleted row for follow-up cleanup."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memory_events WHERE tenant_id = %s AND id = %s "
                "RETURNING kind, entity, path, content",
                (_tid(), event_id),
            )
            row = cur.fetchone()
        conn.commit()
        return {"kind": row[0], "entity": row[1], "path": row[2], "content": row[3]} if row else None
    finally:
        conn.close()


def memory_event_counts(days: int = 7) -> dict:
    """{kind: count} over the last `days` — for the health panel."""
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kind, count(*) FROM memory_events WHERE tenant_id = %s AND created_at > %s "
                "GROUP BY kind",
                (_tid(), cutoff),
            )
            return {r[0]: int(r[1]) for r in cur.fetchall()}
    finally:
        conn.close()
