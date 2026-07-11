"""Product DB access + numbered SQL migrations (schema_version table)."""
import logging
from pathlib import Path

import psycopg2

from product.config import DATABASE_URL

logger = logging.getLogger("product.db")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_conn():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set")
    return psycopg2.connect(DATABASE_URL)


def run_migrations() -> None:
    """Apply product/migrations/NNNN_*.sql in order, tracked in schema_version."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    applied_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
            current = cur.fetchone()[0]

            for path in sorted(_MIGRATIONS_DIR.glob("[0-9]*.sql")):
                version = int(path.name.split("_", 1)[0])
                if version <= current:
                    continue
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO schema_version (version, filename) VALUES (%s, %s)",
                    (version, path.name),
                )
                logger.info("Applied migration %s", path.name)
        conn.commit()
    finally:
        conn.close()
