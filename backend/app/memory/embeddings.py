"""Semantic memory: vector embeddings for vault notes, rules and episodes (pgvector).

Everything here is tenant-scoped through current_tenant_id / the runtime vault
resolver, so the same code serves the owner and product tenants.

Failures are logged at WARNING and recorded in `status()` (surfaced on
/health). They used to be DEBUG-only, which hid the fact that the retired
text-embedding-004 model had silently switched semantic recall off.
"""

import logging
import time
from pathlib import Path

import httpx

from app.config import DATABASE_URL, EMBEDDING_DIMS, EMBEDDING_MODEL
from app.context import current_tenant_id

logger = logging.getLogger("pa.embeddings")

_MAX_CONTENT_CHARS = 8000
_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
_SETTING_KEY = "embedding_model"

# Process-wide health snapshot for /health. Not tenant data — counts and
# timestamps only, never content.
_STATUS: dict = {"model": EMBEDDING_MODEL, "ok": 0, "errors": 0,
                 "last_ok_at": None, "last_error": None, "last_error_at": None}
_last_warn = 0.0


def _tid() -> str:
    """Tenant scope for all embedding rows ('' = owner/legacy data)."""
    return current_tenant_id.get()


def _get_conn():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


def _vault_root() -> Path:
    from app.memory import obsidian
    return obsidian.vault_root()


def _api_key() -> str:
    from app import runtime
    return runtime.get_secret("GEMINI_API_KEY") or ""


def status() -> dict:
    """Counts + last error, plus `state`: ok (last call succeeded), error, or idle."""
    st = dict(_STATUS)
    ok_at, err_at = st["last_ok_at"], st["last_error_at"]
    if ok_at and (not err_at or ok_at >= err_at):
        st["state"] = "ok"
    elif err_at:
        st["state"] = "error"
    else:
        st["state"] = "idle"
    return st


def _record_error(what: str, exc: Exception | str) -> None:
    global _last_warn
    _STATUS["errors"] += 1
    _STATUS["last_error"] = f"{what}: {exc}"[:300]
    _STATUS["last_error_at"] = time.time()
    # One warning per minute is enough to be noticed without flooding logs
    # when the key is bad and every message fails.
    if time.time() - _last_warn > 60:
        _last_warn = time.time()
        logger.warning("embeddings: %s failed: %s", what, exc)


def vec_str(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

# Keep private alias used internally
_vec_str = vec_str


def _embed(text: str, task_type: str) -> list[float] | None:
    """Call the Gemini embedding API. None when unconfigured or on failure."""
    key = _api_key()
    text = (text or "").strip()
    if not key or not text:
        return None
    try:
        r = httpx.post(
            _API_URL.format(model=EMBEDDING_MODEL),
            headers={"x-goog-api-key": key},
            json={
                "model": f"models/{EMBEDDING_MODEL}",
                "content": {"parts": [{"text": text[:_MAX_CONTENT_CHARS]}]},
                "taskType": task_type,
                "outputDimensionality": EMBEDDING_DIMS,
            },
            timeout=20.0,
        )
        if r.status_code != 200:
            _record_error("embed", f"HTTP {r.status_code} {r.text[:200]}")
            return None
        values = (r.json().get("embedding") or {}).get("values") or []
        if len(values) != EMBEDDING_DIMS:
            _record_error("embed", f"unexpected dimension {len(values)}")
            return None
        _STATUS["ok"] += 1
        _STATUS["last_ok_at"] = time.time()
        return values
    except Exception as exc:  # network, JSON, …
        _record_error("embed", exc)
        return None


def embed_text(text: str) -> list[float] | None:
    """Embedding for content that will be stored and searched (notes, rules, episodes)."""
    return _embed(text, "RETRIEVAL_DOCUMENT")


def embed_query(text: str) -> list[float] | None:
    """Embedding for a search query."""
    return _embed(text[:2000], "RETRIEVAL_QUERY")


def upsert_embedding(filepath: str, content: str) -> bool:
    """Embed `content` and store under `filepath` (relative to vault root). Returns True on success."""
    v = embed_text(content)
    if not v:
        return False
    try:
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
                    (_tid(), filepath, _vec_str(v)),
                )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:
        _record_error(f"upsert {filepath}", exc)
        return False


def delete_embedding(filepath: str) -> None:
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM vault_embeddings WHERE tenant_id = %s AND filepath = %s",
                    (_tid(), filepath),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        _record_error(f"delete {filepath}", exc)


def semantic_search(query: str = "", limit: int = 8, *, vec: list[float] | None = None,
                    min_similarity: float = 0.0) -> list[tuple[str, float]]:
    """Return [(filepath, similarity)] best first, filtered by `min_similarity`.

    Pass a precomputed `vec` to avoid a second embedding call per message.
    """
    if vec is None:
        vec = embed_query(query)
    if not vec:
        return []
    try:
        vs = _vec_str(vec)
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT filepath, 1 - (embedding <=> %s::vector) AS sim
                    FROM vault_embeddings
                    WHERE tenant_id = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vs, _tid(), vs, limit),
                )
                return [(r[0], float(r[1])) for r in cur.fetchall() if float(r[1]) >= min_similarity]
        finally:
            conn.close()
    except Exception as exc:
        _record_error("semantic_search", exc)
        return []


def store_rule_embedding(rule_text: str) -> None:
    """Persist the embedding for a rule (upsert by exact rule text)."""
    v = embed_text(rule_text)
    if not v:
        return
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
                    (_tid(), rule_text, vec_str(v)),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        _record_error("store_rule_embedding", exc)


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
    except Exception as exc:
        _record_error("find_similar_rule", exc)
        return None


def delete_rule_embedding(rule_text: str) -> None:
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM rule_embeddings WHERE tenant_id = %s AND rule_text = %s",
                    (_tid(), rule_text),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        _record_error("delete_rule_embedding", exc)


def ensure_embedding_model_current() -> bool:
    """Drop this tenant's vectors if they were made by a different model.

    Vectors from different models live in different spaces — comparing them
    returns garbage — so on a model change the index is cleared and the
    rebuild jobs repopulate it. Returns True when a reset happened.
    """
    from app.memory.store import get_app_setting, set_app_setting
    try:
        if get_app_setting(_SETTING_KEY) == EMBEDDING_MODEL:
            return False
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vault_embeddings WHERE tenant_id = %s", (_tid(),))
                cur.execute("DELETE FROM rule_embeddings WHERE tenant_id = %s", (_tid(),))
                cur.execute("UPDATE episodes SET embedding = NULL WHERE tenant_id = %s", (_tid(),))
            conn.commit()
        finally:
            conn.close()
        set_app_setting(_SETTING_KEY, EMBEDDING_MODEL)
        logger.info("embeddings: index reset for model %s (tenant=%r)", EMBEDDING_MODEL, _tid())
        return True
    except Exception as exc:
        _record_error("ensure_embedding_model_current", exc)
        return False


async def rebuild_rule_embeddings() -> int:
    """Embed all active rules into rule_embeddings. Returns count stored."""
    from app.memory.obsidian import active_rules
    from app.runtime import in_thread
    if not _api_key():
        return 0
    count = 0
    for rule in await in_thread(active_rules):
        await in_thread(store_rule_embedding, rule)
        count += 1
    if count:
        logger.info("Rebuilt rule embeddings for %d rules", count)
    return count


async def rebuild_vault_embeddings() -> int:
    """Embed every vault note newer than its stored embedding (current tenant).

    Indexes the note's retrieval text (title + summary + recent log), the
    same text read_facts_by_paths injects, so what is matched is what is shown.
    """
    from app.memory import obsidian
    from app.runtime import in_thread
    if not _api_key():
        logger.info("Skipping embedding rebuild — no GEMINI_API_KEY")
        return 0
    root = _vault_root()
    if not root.exists():
        return 0

    existing: dict[str, float] = {}
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT filepath, updated_at FROM vault_embeddings WHERE tenant_id = %s",
                    (_tid(),),
                )
                for fp, ts in cur.fetchall():
                    existing[fp] = ts.timestamp() if ts else 0.0
        finally:
            conn.close()
    except Exception as exc:
        _record_error("rebuild: read existing", exc)

    count = 0
    for path in obsidian.indexable_notes():
        rel = path.relative_to(root).as_posix()
        try:
            if path.stat().st_mtime <= existing.get(rel, 0.0):
                continue
        except OSError:
            continue
        text = await in_thread(obsidian.index_text, path)
        if text and await in_thread(upsert_embedding, rel, text):
            count += 1

    if count:
        logger.info("Rebuilt embeddings for %d vault file(s)", count)
    return count


async def backfill_episode_embeddings(limit: int = 200) -> int:
    """Embed episodes stored without a vector (failed embeds, model change)."""
    from app.runtime import in_thread
    if not _api_key():
        return 0
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, summary FROM episodes WHERE tenant_id = %s AND embedding IS NULL "
                    "ORDER BY created_at DESC LIMIT %s",
                    (_tid(), limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        _record_error("backfill: read", exc)
        return 0

    count = 0
    for ep_id, summary in rows:
        v = await in_thread(embed_text, summary)
        if not v:
            break  # API down — try again next maintenance run
        try:
            conn = _get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE episodes SET embedding = %s::vector WHERE id = %s AND tenant_id = %s",
                        (vec_str(v), ep_id, _tid()),
                    )
                conn.commit()
            finally:
                conn.close()
            count += 1
        except Exception as exc:
            _record_error("backfill: write", exc)
    if count:
        logger.info("Backfilled %d episode embedding(s)", count)
    return count
