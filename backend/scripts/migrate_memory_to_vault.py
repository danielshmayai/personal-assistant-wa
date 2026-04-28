"""One-shot migration: copy Postgres memory_facts/memory_rules into the Obsidian vault.

Run once after the vault is mounted:
    docker compose exec backend python -m scripts.migrate_memory_to_vault

Idempotent — append-with-dedupe means repeated runs are safe but may add
duplicate timestamped sections to fact files. Run once, then archive.
"""
from __future__ import annotations

import logging

from app.memory.store import get_all_facts, get_all_rules
from app.memory import obsidian


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
