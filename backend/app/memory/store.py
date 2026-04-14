import asyncio
import logging
import psycopg2
from app.config import DATABASE_URL

logger = logging.getLogger("pa.memory")


def _get_conn():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set — cannot connect to PostgreSQL")
    return psycopg2.connect(DATABASE_URL)


def init_memory_tables():
    """Create memory tables if they don't exist (idempotent)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
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
                CREATE TABLE IF NOT EXISTS google_tokens (
                    chat_id TEXT PRIMARY KEY,
                    access_token TEXT,
                    refresh_token TEXT,
                    token_expiry TIMESTAMP,
                    scopes TEXT
                )
            """)
        conn.commit()
    finally:
        conn.close()


def save_google_token(chat_id: str, creds) -> None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
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
                creds.token,
                creds.refresh_token,
                creds.expiry,
                ",".join(creds.scopes) if creds.scopes else "",
            ))
        conn.commit()
        logger.info("Saved Google token for chat_id=%s", chat_id)
    finally:
        conn.close()


def load_google_token(chat_id: str) -> dict | None:
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
                "access_token": row[0],
                "refresh_token": row[1],
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
                INSERT INTO memory_facts (key, value, source, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    source = EXCLUDED.source,
                    updated_at = NOW()
            """, (key, value, source))
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
                INSERT INTO memory_rules (rule, reason, source)
                VALUES (%s, %s, %s)
                ON CONFLICT (rule) DO NOTHING
            """, (rule, reason, source))
        conn.commit()
        logger.info("Inserted rule: %.80s", rule)
    finally:
        conn.close()


def get_all_facts() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value FROM memory_facts ORDER BY updated_at DESC")
            return [{"key": r[0], "value": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def get_all_facts_with_ids() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, key, value FROM memory_facts ORDER BY updated_at DESC")
            return [{"id": r[0], "key": r[1], "value": r[2]} for r in cur.fetchall()]
    finally:
        conn.close()


def get_all_rules() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rule, reason FROM memory_rules ORDER BY created_at DESC")
            return [{"rule": r[0], "reason": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def get_all_rules_with_ids() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, rule, reason FROM memory_rules ORDER BY created_at DESC")
            return [{"id": r[0], "rule": r[1], "reason": r[2]} for r in cur.fetchall()]
    finally:
        conn.close()


def delete_fact(key: str) -> bool:
    """Delete a fact by key. Returns True if a row was deleted."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memory_facts WHERE key = %s", (key,))
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
            cur.execute("DELETE FROM memory_rules WHERE id = %s", (rule_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        if deleted:
            logger.info("Deleted rule id=%s", rule_id)
        return deleted
    finally:
        conn.close()


async def load_memory_context() -> str:
    """Build a text block of all facts + rules for system prompt injection."""
    loop = asyncio.get_running_loop()
    facts = await loop.run_in_executor(None, get_all_facts)
    rules = await loop.run_in_executor(None, get_all_rules)

    if not facts and not rules:
        return ""

    parts = []
    if facts:
        lines = [f"- {f['key']}: {f['value']}" for f in facts[:50]]
        parts.append("## Known Facts\n" + "\n".join(lines))
    if rules:
        lines = [f"- {r['rule']}" + (f" (reason: {r['reason']})" if r['reason'] else "") for r in rules[:30]]
        parts.append("## Rules & Preferences\n" + "\n".join(lines))

    return "\n\n".join(parts)
