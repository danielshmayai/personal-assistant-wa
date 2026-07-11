"""Semantic memory: vector embeddings for Obsidian vault files via pgvector."""

import logging
from pathlib import Path
from app.config import DATABASE_URL, OBSIDIAN_VAULT_PATH
from app.context import current_tenant_id

logger = logging.getLogger("pa.embeddings")

# Owner base vault — used only by the startup rebuild jobs (owner path).
VAULT_ROOT = Path(OBSIDIAN_VAULT_PATH)


def _tid() -> str:
    """Tenant scope for all embedding rows ('' = owner/legacy data)."""
    return current_tenant_id.get()
_EMBEDDING_MODEL = "models/text-embedding-004"
_MAX_CONTENT_CHARS = 8000


def _get_conn():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


def _model():
    from app import runtime
    api_key = runtime.get_secret("GEMINI_API_KEY")
    if not api_key:
        return None
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    return GoogleGenerativeAIEmbeddings(model=_EMBEDDING_MODEL, google_api_key=api_key)


def vec_str(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

# Keep private alias used internally
_vec_str = vec_str


def embed_text(text: str) -> list[float] | None:
    """Return a 768-dim embedding or None if no model is configured."""
    m = _model()
    if not m:
        return None
    try:
        return m.embed_query(text[:_MAX_CONTENT_CHARS])
    except Exception:
        logger.debug("embed_text failed", exc_info=True)
        return None


def upsert_embedding(filepath: str, content: str) -> bool:
    """Embed `content` and store under `filepath` (relative to vault root). Returns True on success."""
    m = _model()
    if not m:
        return False
    try:
        vec = _vec_str(m.embed_query(content[:_MAX_CONTENT_CHARS]))
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vault_embeddings (tenant_id, filepath, embedding, updated_at)
                    VALUES (%s, %s, %s::vector, NOW())
                    ON CONFLICT (tenant_id, filepath) DO UPDATE
                    SET embedding = EXCLUDED.embedding, updated_at = NOW()
                    """,
                    (_tid(), filepath, vec),
                )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        logger.debug("upsert_embedding failed for %s", filepath, exc_info=True)
        return False


def semantic_search(query: str, limit: int = 8) -> list[str]:
    """Return vault filepaths ranked by cosine similarity to `query`. Empty list on any failure."""
    m = _model()
    if not m:
        return []
    try:
        vec = _vec_str(m.embed_query(query[:2000]))
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT filepath
                    FROM vault_embeddings
                    WHERE tenant_id = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (_tid(), vec, limit),
                )
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        logger.debug("semantic_search failed", exc_info=True)
        return []


def store_rule_embedding(rule_text: str) -> None:
    """Persist the embedding for a rule (upsert by exact rule text)."""
    v = embed_text(rule_text)
    if not v:
        return
    vs = vec_str(v)
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rule_embeddings (tenant_id, rule_text, embedding)
                    VALUES (%s, %s, %s::vector)
                    ON CONFLICT (tenant_id, rule_text) DO UPDATE SET embedding = EXCLUDED.embedding
                    """,
                    (_tid(), rule_text, vs),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("store_rule_embedding failed for %.80s", rule_text, exc_info=True)


def find_similar_rule(rule_text: str, threshold: float = 0.85) -> str | None:
    """Return the stored rule most similar to rule_text if similarity >= threshold, else None."""
    v = embed_text(rule_text)
    if not v:
        return None
    vs = vec_str(v)
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rule_text, 1 - (embedding <=> %s::vector) AS sim
                    FROM rule_embeddings
                    WHERE tenant_id = %s AND 1 - (embedding <=> %s::vector) >= %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT 1
                    """,
                    (vs, _tid(), vs, threshold, vs),
                )
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        logger.debug("find_similar_rule failed", exc_info=True)
        return None


async def rebuild_rule_embeddings() -> int:
    """Embed all active rules from Rules.md into rule_embeddings. Returns count updated."""
    import asyncio
    m = _model()
    if not m:
        return 0
    from app.memory.obsidian import read_rules, _strip_rule_meta, RULES_FILE
    if not RULES_FILE.exists():
        return 0

    rules_text = read_rules()
    rule_lines = [
        _strip_rule_meta(ln)
        for ln in rules_text.splitlines()
        if ln.strip().startswith("- ")
    ]
    rule_lines = [r for r in rule_lines if r]

    if not rule_lines:
        return 0

    loop = asyncio.get_running_loop()
    count = 0
    for rule in rule_lines:
        ok = await loop.run_in_executor(None, store_rule_embedding, rule)
        count += 1
    logger.info("Rebuilt rule embeddings for %d rules", count)
    return count


async def rebuild_vault_embeddings() -> int:
    """Embed every vault .md file that is newer than its stored embedding. Returns count updated."""
    import asyncio
    m = _model()
    if not m:
        logger.info("Skipping embedding rebuild — no GEMINI_API_KEY")
        return 0
    if not VAULT_ROOT.exists():
        return 0

    existing: dict[str, float] = {}
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT filepath, updated_at FROM vault_embeddings WHERE tenant_id = ''")
                for fp, ts in cur.fetchall():
                    existing[fp] = ts.timestamp() if ts else 0.0
        finally:
            conn.close()
    except Exception:
        logger.debug("Could not read existing embeddings", exc_info=True)

    to_embed: list[Path] = []
    for path in VAULT_ROOT.rglob("*.md"):
        rel = path.relative_to(VAULT_ROOT).as_posix()
        try:
            if path.stat().st_mtime > existing.get(rel, 0.0):
                to_embed.append(path)
        except OSError:
            pass

    if not to_embed:
        logger.info("All vault embeddings are up to date")
        return 0

    loop = asyncio.get_running_loop()
    count = 0
    for path in to_embed:
        rel = path.relative_to(VAULT_ROOT).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            ok = await loop.run_in_executor(None, upsert_embedding, rel, content)
            if ok:
                count += 1
        except Exception:
            logger.debug("Failed to embed %s", rel, exc_info=True)

    logger.info("Rebuilt embeddings for %d vault file(s)", count)
    return count
