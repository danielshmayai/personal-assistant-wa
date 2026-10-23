"""One-shot migration: copy Postgres memory_facts/memory_rules into the Obsidian vault.

Run once after the vault is mounted:
    docker compose exec backend python -m scripts.migrate_memory_to_vault

Idempotent — append-with-dedupe means repeated runs are safe but may add
duplicate timestamped sections to fact files. Run once, then archive.
"""
from __future__ import annotations

import logging

from app.memory import obsidian
from app.memory.store import _get_conn, _tid


def _rows(sql: str) -> list[tuple]:
    """Read the legacy table, or nothing if it was never created."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (sql.split(" FROM ")[1].split()[0],))
            if cur.fetchone()[0] is None:
                return []
            cur.execute(sql, (_tid(),))
            return cur.fetchall()
    finally:
        conn.close()


def get_all_rules() -> list[dict]:
    return [{"rule": r[0], "reason": r[1]} for r in
            _rows("SELECT rule, reason FROM memory_rules WHERE tenant_id = %s ORDER BY created_at DESC")]


def get_all_facts() -> list[dict]:
    return [{"key": r[0], "value": r[1]} for r in
            _rows("SELECT key, value FROM memory_facts WHERE tenant_id = %s ORDER BY updated_at DESC")]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("migrate")

    rules = get_all_rules()
    log.info("Migrating %d rules → %s", len(rules), obsidian.RULES_FILE)
    for r in rules:
        line = r["rule"]
        if r.get("reason"):
            line = f"{line} (because: {r['reason']})"
        obsidian.update_rule(line)

    facts = get_all_facts()
    log.info("Migrating %d facts → Preferences/", len(facts))
    for f in facts:
        # Best-effort categorization; user can reorganize in Obsidian afterwards.
        obsidian.save_fact("Preferences", f["key"], f["value"])

    log.info("Done. Inspect the vault and reorganize folders/wikilinks in Obsidian.")


if __name__ == "__main__":
    main()
