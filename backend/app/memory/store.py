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


async def load_memory_context(query: str = "") -> str:
    """Build a text block of rules + query-relevant facts from the Obsidian vault.

    Tries semantic (pgvector) search first; falls back to keyword matching.
    """
    from app.memory import obsidian
    from app.memory.embeddings import semantic_search
    from app.runtime import in_thread
    rules = await in_thread(obsidian.read_rules)

    facts = ""
    episodes: list[str] = []
    if query:
        try:
            filepaths = await in_thread(semantic_search, query)
            non_system = [f for f in filepaths if not f.startswith("System/")]
            if non_system:
                facts = await in_thread(obsidian.read_facts_by_paths, non_system)
        except Exception:
            pass
        try:
            from app.memory.episodes import get_relevant_episodes
            episodes = await get_relevant_episodes(query)
        except Exception:
            pass
    if not facts:
        facts = await in_thread(obsidian.read_relevant_facts, query)

    parts: list[str] = []
    if rules:
        parts.append("## Rules\n" + rules)
    if facts:
        parts.append("## Relevant Facts\n" + facts)
    if episodes:
        parts.append("## Past relevant conversations\n" + "\n".join(f"- {e}" for e in episodes))
    if not parts:
        return ""
    return "\n\n".join(parts)[:obsidian.MAX_INJECTED_BYTES]


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


def search_episodes(vec_str: str, limit: int = 3) -> list[str]:
    """Return episode summaries ranked by cosine similarity to vec_str."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT summary FROM episodes
                WHERE tenant_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (_tid(), vec_str, limit),
            )
            return [r[0] for r in cur.fetchall()]
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
    """Return the JSONB value for *key*, or None if not set."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def set_app_setting(key: str, value) -> None:
    """Upsert a JSONB *value* for *key*."""
    import json as _json
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
            """, (key, _json.dumps(value)))
        conn.commit()
    finally:
        conn.close()