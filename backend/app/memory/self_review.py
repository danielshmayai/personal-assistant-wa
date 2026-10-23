"""Nightly self-review: analyze recent conversations and write insights to the vault."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from langchain_core.messages import HumanMessage
from app.llm import get_smart_llm, response_text
from app.memory import obsidian
from app.memory.store import get_recent_conversations

logger = logging.getLogger("pa.self_review")

_REVIEW_PROMPT = """\
You are reviewing recent conversations between a user and their personal assistant.

Analyze the conversations below and identify:
1. **Behavioral patterns** — things the assistant consistently does well or poorly.
2. **New rules** — behavioral directives that would measurably improve future interactions.
3. **Knowledge gaps** — topics the user cares about that aren't captured in memory yet.
4. **Style signals** — how the user prefers to receive information (length, format, tone).

For each insight, output exactly this format:
INSIGHT
CATEGORY: pattern | rule | knowledge_gap | style
OBSERVATION: <one sentence — what you noticed>
ACTION: <concrete change the assistant should make, or NONE>
END_INSIGHT

Rules with a clear ACTION will be auto-saved. Be specific and actionable.

If there is nothing significant to note, respond with:
NOTHING_TO_REVIEW

---
{conversations}"""


async def run_self_review(hours: int = 24) -> str:
    """Bring the memory index up to date, then analyse recent conversations.

    Works for any data scope: the embedding rebuilds now go through the
    tenant-aware vault resolver (they used to be owner-only because they read
    a path frozen at import time).
    """
    from app.context import current_tenant_id
    from app.memory.maintenance import index_pass
    if not current_tenant_id.get():
        # Capabilities.md is generated from the owner deployment's features.
        from app.memory.capabilities import sync_capabilities
        sync_capabilities()
    try:
        logger.info("Self-review index pass: %s", await index_pass())
    except Exception:
        logger.warning("Self-review index pass failed", exc_info=True)
    return await analyse_conversations(hours)


async def analyse_conversations(hours: int) -> str:
    """Analyze conversations from the last `hours` hours and write insights to the vault."""
    from app.runtime import in_thread
    convs = await in_thread(get_recent_conversations, hours)
    if not convs:
        logger.info("Self-review: no conversations in last %dh", hours)
        return "No conversations to review."

    blocks = [
        f"--- Conversation {i} (chat: {c['chat_id']}, at: {c['created_at']}) ---\n{c['transcript']}"
        for i, c in enumerate(convs, 1)
    ]
    combined = "\n\n".join(blocks)

    llm = get_smart_llm()
    prompt = _REVIEW_PROMPT.format(conversations=combined)

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response_text(response).strip()

        if "NOTHING_TO_REVIEW" in content:
            logger.info("Self-review: nothing to note for last %dh", hours)
            return "Nothing significant to note."

        rules_saved: list[str] = []
        for block in re.findall(r"INSIGHT\s*(.*?)\s*END_INSIGHT", content, re.DOTALL):
            cat_match = re.search(r"CATEGORY:\s*(.+)", block)
            act_match = re.search(r"ACTION:\s*(.+)", block)
            category = cat_match.group(1).strip().lower() if cat_match else ""
            action = act_match.group(1).strip() if act_match else "NONE"

            if category == "rule" and action and action.upper() != "NONE":
                # Same semantic dedup as live extraction, so the nightly review
                # can't pile near-identical rules into Rules.md.
                from app.memory.reflection import _save_rule
                from app.memory.store import record_memory_event
                if await _save_rule(action):
                    record_memory_event("rule", "", "System/Rules.md", action, source="self_review")
                    rules_saved.append(action)
                    logger.info("Self-review saved rule: %s", action)

        # Append full analysis to System/DailyInsights.md
        insights_path: Path = obsidian.vault_root() / "System" / "DailyInsights.md"
        insights_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header = f"## Review {now} ({len(convs)} conversations)\n\n"
        with insights_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(header + content + "\n\n---\n\n")

        summary = f"Self-review complete: {len(convs)} conversations analysed."
        if rules_saved:
            summary += f" {len(rules_saved)} new rule(s) saved."
        logger.info(summary)
        return summary

    except Exception:
        logger.exception("Self-review failed")
        return "Self-review failed."
