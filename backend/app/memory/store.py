import logging
import psycopg2
from app.config import DATABASE_URL

logger = logging.getLogger("pa.memory")


def _get_conn():
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
        conn.commit()
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


def get_all_rules() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rule, reason FROM memory_rules ORDER BY created_at DESC")
            return [{"rule": r[0], "reason": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


async def load_memory_context() -> str:
    """Build a text block of all facts + rules for system prompt injection."""
    facts = get_all_facts()
    rules = get_all_rules()

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
