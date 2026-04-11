"""Memory_Manager — a LangChain tool the agent can call to save facts/rules."""

from langchain_core.tools import tool
from app.memory.store import upsert_fact, insert_rule


@tool
def save_fact(key: str, value: str) -> str:
    """Save or update a fact about the user. Use a short key and descriptive value.
    Example: save_fact(key="preferred_language", value="Python")"""
    upsert_fact(key, value, source="agent")
    return f"Saved fact: {key} = {value}"


@tool
def save_rule(rule: str, reason: str = "") -> str:
    """Save a rule or preference the user has expressed. Include a reason if known.
    Example: save_rule(rule="Never mock the database in tests", reason="Prior incident with prod migration")"""
    insert_rule(rule, reason, source="agent")
    return f"Saved rule: {rule}"
