"""Memory manager — LangChain tools the agent calls to manage the Obsidian vault.

All operations are backed by `app.memory.obsidian` (markdown files in a
host-mounted Obsidian vault). Soft-delete semantics: `forget` flips a flag
rather than removing data (`forget`).
"""
import logging
from langchain_core.tools import tool
from app.memory import obsidian

logger = logging.getLogger("pa.memory")


_CATEGORY_LIST = "People, Entities, Investments, Projects, Preferences, Misc"


@tool
def save_fact(category: str, entity: str, content: str) -> str:
    """Save durable information the user explicitly asked you to remember.

    (Things merely mentioned are captured automatically — don't call this for them.)
    category: People, Projects, Entities, Investments, Profile, Misc (others → Misc).
    entity: the person/project/thing the note is about (Profile/Me = the user).
    content: one self-contained sentence of what's NEW; [[wikilinks]] and #tags welcome.
    """
    result = obsidian.save_fact(category, entity, content)
    if result.startswith("Saved to "):
        from app.memory.store import record_memory_event
        record_memory_event("fact", entity, result.removeprefix("Saved to ").strip(), content, source="tool")
    return result


@tool
def update_rule(instruction: str) -> str:
    """Append a behavioral rule to System/Rules.md.

    Use when the user gives a directive about HOW you should behave:
    'always', 'never', 'from now on', 'don't', 'prefer', 'stop doing X'.
    Keep `instruction` to one sentence in imperative voice.

    Example: update_rule(instruction="Always reply in Hebrew.")
    """
    from app.memory.embeddings import find_similar_rule, store_rule_embedding
    similar = find_similar_rule(instruction)
    if similar:
        if similar.strip().lower() == instruction.strip().lower():
            return "Rule already exists (semantically equivalent)."
        # Retire the outdated rule and replace with the refined version
        obsidian.hide_rule(similar)
        logger.info("Replaced similar rule with refined version: %r", instruction)
    result = obsidian.update_rule(instruction)
    store_rule_embedding(instruction)
    if result.startswith("Saved"):
        from app.memory.store import record_memory_event
        record_memory_event("rule", "", "System/Rules.md", instruction, source="tool")
    return result


@tool
def search_memory(query: str, note: str = "") -> str:
    """Search what you've stored: people, projects, notes, saved photo links, lists.

    Use when the user asks "what do you know about X", "remind me of Y", or a
    question that may have stored context the memory block didn't show.

    Without `note`: searches the whole vault by meaning, falling back to
    keywords, and returns the best matching notes.
    With `note` (a vault path, e.g. "Misc/contacts.md"): returns only the lines
    of that file containing `query` — use it to count or filter a stored list
    ("how many contacts have email?" → query="@").
    """
    if note:
        return obsidian.grep_note(note, query)
    from app.config import MEMORY_MIN_SIMILARITY
    from app.memory.embeddings import semantic_search
    hits = semantic_search(query, limit=6, min_similarity=MEMORY_MIN_SIMILARITY)
    if hits:
        text = obsidian.read_facts_by_paths([p for p, _ in hits], byte_budget=4000)
        if text:
            return text
    return obsidian.retrieve_context(query)


@tool
def list_memory() -> str:
    """List all visible (non-hidden) facts grouped by category, plus rule count.

    Call when the user asks 'what do you know about me?', 'what do you
    remember?', 'show my memory'.
    """
    return obsidian.list_visible()


@tool
def forget(target: str, kind: str = "fact") -> str:
    """Stop using something you remembered (soft delete — the vault keeps it).

    kind="fact": target is the note, as "Category/Entity" (e.g. "People/Dana").
    kind="rule": target is the rule's text or part of it.
    Use for "forget X", "that's wrong, drop it", "remove the rule about Y".
    """
    if kind == "rule":
        return obsidian.hide_rule(target)
    category, _, entity = target.removesuffix(".md").partition("/")
    if not entity:
        return "Give the fact as Category/Entity, e.g. People/Dana (see list_memory)."
    return obsidian.hide_fact(category, entity)


@tool
def read_note(filepath: str) -> str:
    """Read a whole vault note, e.g. to summarize it.

    filepath is relative to the vault root ("Projects/Home Renovation.md");
    call list_memory if unsure. To count or filter lines use search_memory(note=…).
    """
    return obsidian.read_note(filepath)


@tool
def append_to_note(filepath: str, content: str, header: str = "") -> str:
    """Append text to a SPECIFIC existing vault file (daily note, expense log…).

    filepath: relative to the vault root, e.g. "Finance/Expenses 2026.md" (must exist).
    header: optional section to append under, e.g. "## Tasks" (created if missing).
    For new structured memory use save_fact instead.
    """
    return obsidian.append_to_note(filepath, content, header)


# Convenience list for graph nodes to import — keep this name stable.
MEMORY_TOOLS = [
    save_fact, update_rule, search_memory, forget,
    list_memory, append_to_note, read_note,
]
