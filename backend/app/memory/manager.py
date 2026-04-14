"""Memory_Manager — LangChain tools the agent calls to manage long-term memory."""

from langchain_core.tools import tool
from app.memory.store import (
    upsert_fact,
    insert_rule,
    delete_fact as _delete_fact,
    delete_rule as _delete_rule,
    get_all_facts_with_ids,
    get_all_rules_with_ids,
)


@tool
def save_fact(key: str, value: str) -> str:
    """Save or update a long-term fact about the user.
    Use a short, stable key and a descriptive value.
    Call this whenever the user shares personal information worth remembering across sessions:
    name, job, family, location, preferences, important dates, etc.
    Example: save_fact(key="user_name", value="Daniel")"""
    upsert_fact(key, value, source="agent")
    return f"Saved fact: {key} = {value}"


@tool
def save_rule(rule: str, reason: str = "") -> str:
    """Save a behavioral rule or preference the user has expressed.
    Call this when the user gives an instruction about how the bot should behave:
    'always', 'never', 'from now on', 'prefer', 'stop doing', 'don't', etc.
    Example: save_rule(rule="Always reply in Hebrew", reason="User prefers Hebrew")"""
    insert_rule(rule, reason, source="agent")
    return f"Saved rule: {rule}"


@tool
def list_memory() -> str:
    """List everything the bot currently remembers: all facts and all rules with their IDs.
    Call this when the user asks 'what do you know about me?', 'what do you remember?',
    'show my rules', etc."""
    facts = get_all_facts_with_ids()
    rules = get_all_rules_with_ids()

    if not facts and not rules:
        return "No memories saved yet."

    parts = []
    if facts:
        lines = [f"[{f['id']}] *{f['key']}*: {f['value']}" for f in facts]
        parts.append("*Saved Facts:*\n" + "\n".join(lines))
    if rules:
        lines = [
            f"[{r['id']}] {r['rule']}" + (f" _(reason: {r['reason']})_" if r['reason'] else "")
            for r in rules
        ]
        parts.append("*Saved Rules:*\n" + "\n".join(lines))

    return "\n\n".join(parts)


@tool
def delete_fact(key: str) -> str:
    """Delete a saved fact by its key.
    Use list_memory first to find the exact key.
    Call this when the user says 'forget X', 'remove that fact', 'delete my X', etc.
    Example: delete_fact(key="user_name")"""
    if _delete_fact(key):
        return f"Deleted fact: {key}"
    return f"No fact found with key '{key}'. Use list_memory to see all keys."


@tool
def delete_rule(rule_id: int) -> str:
    """Delete a saved rule by its numeric ID.
    Use list_memory first to find the ID shown in brackets, e.g. [3].
    Call this when the user says 'remove rule 3', 'forget that rule', 'delete rule about X', etc.
    Example: delete_rule(rule_id=3)"""
    if _delete_rule(rule_id):
        return f"Deleted rule #{rule_id}"
    return f"No rule found with ID {rule_id}. Use list_memory to see all IDs."


# Convenience list for graph nodes to import
MEMORY_TOOLS = [save_fact, save_rule, list_memory, delete_fact, delete_rule]
