"""Memory manager — LangChain tools the agent calls to manage the Obsidian vault.

All operations are backed by `app.memory.obsidian` (markdown files in a
host-mounted Obsidian vault). Soft-delete semantics: `hide_*` flips a flag
rather than removing data.
"""
from langchain_core.tools import tool
from app.memory import obsidian


_CATEGORY_LIST = "People, Entities, Investments, Projects, Preferences, Misc"


@tool
def save_fact(category: str, entity: str, content: str) -> str:
    """Persist a fact to the user's Obsidian vault.

    Use whenever the user shares durable info: people, properties, projects,
    preferences, important dates, recurring events.

    Pick `category` from: People, Entities, Investments, Projects,
    Preferences, Misc. (Off-list values fall into Misc.)

    Embed Obsidian tags (`#real-estate`, `#family`) and wikilinks
    (`[[Daniel]]`, `[[Milwaukee_Property]]`) directly inside `content` so
    the graph view stays connected.

    Multiple calls to the same (category, entity) APPEND timestamped
    sections — write only what is NEW.

    Example:
      save_fact(
        category="Investments",
        entity="Milwaukee_Property",
        content="Grosses $2,400/mo. #real-estate Linked: [[Daniel]]"
      )
    """
    return obsidian.save_fact(category, entity, content)


@tool
def update_rule(instruction: str) -> str:
    """Append a behavioral rule to System/Rules.md.

    Use when the user gives a directive about HOW you should behave:
    'always', 'never', 'from now on', 'don't', 'prefer', 'stop doing X'.
    Keep `instruction` to one sentence in imperative voice.

    Example: update_rule(instruction="Always reply in Hebrew.")
    """
    return obsidian.update_rule(instruction)


@tool
def retrieve_context(query: str) -> str:
    """Search the Obsidian vault for snippets relevant to a topic.

    Use when the user asks 'what do you know about X', 'remind me of Y',
    or whenever answering a question that may have stored context.

    Returns up to 8 matching snippets across visible (non-hidden) vault
    files. Hidden facts are excluded.
    """
    return obsidian.retrieve_context(query)


@tool
def list_memory() -> str:
    """List all visible (non-hidden) facts grouped by category, plus rule count.

    Call when the user asks 'what do you know about me?', 'what do you
    remember?', 'show my memory'.
    """
    return obsidian.list_visible()


@tool
def hide_fact(category: str, entity: str) -> str:
    """Soft-delete a fact: marks it hidden so it won't surface in
    retrieve_context or list_memory. The file itself remains in the vault
    (information is preserved for future reference).

    Use when the user says 'forget X', 'hide my X', 'don't show that anymore'.

    Example: hide_fact(category="Investments", entity="Milwaukee_Property")
    """
    return obsidian.hide_fact(category, entity)


@tool
def hide_rule(instruction: str) -> str:
    """Soft-delete a behavioral rule by matching its text. The line is
    struck through (`~~...~~`) in System/Rules.md so it remains visible
    when the user opens the vault, but is no longer applied.

    Pass the rule text (or a substring of it). Use when the user says
    'remove that rule', 'forget the rule about X', 'undo the rule'.
    """
    return obsidian.hide_rule(instruction)


@tool
def search_vault(query: str) -> str:
    """Search the entire Obsidian vault for notes relevant to a topic.

    Use before answering questions that may benefit from stored context:
    tasks, expenses, project notes, contacts, daily logs, etc.

    Searches across ALL vault folders (not just the structured memory
    categories), so it finds hand-written notes, daily journals, imported
    documents, etc.

    Returns up to 8 ranked snippets with their file paths.

    Example: search_vault(query="home renovation budget")
    """
    return obsidian.retrieve_context(query)


@tool
def read_note(filepath: str) -> str:
    """Read the full content of a vault note into context for analysis.

    Use when the user asks to analyze, count, search, filter, or summarize
    the contents of a specific vault file — e.g. a contacts list, expense
    log, or project file.

    `filepath` is relative to vault root, e.g.:
        "Misc/רשימת_אנשי_קשר.md"
        "Projects/Home Renovation.md"

    After calling this you can directly answer questions like:
    "how many people have email?", "list everyone from Tel Aviv",
    "find all entries with a phone number", etc.

    Example:
        read_note(filepath="Misc/רשימת_אנשי_קשר.md")
    """
    return obsidian.read_note(filepath)


@tool
def append_to_note(filepath: str, content: str, header: str = "") -> str:
    """Append content to any existing Markdown note in the Obsidian vault.

    Use when you need to add information to a SPECIFIC existing file
    (e.g. a daily note, a project file, an expense log) rather than
    creating a structured memory fact.

    `filepath` — path relative to vault root, e.g.:
        "Daily/2024-01-15.md"
        "Projects/Home Renovation.md"
        "Finance/Expenses 2024.md"

    `header` (optional) — section heading to append under, e.g. "## Tasks".
        If the header doesn't exist it will be created at the end of the file.
        If omitted, content is appended at the very end of the file.

    The file must already exist. To create new structured memory notes use
    save_fact() instead.

    Example:
        append_to_note(
            filepath="Daily/2024-01-15.md",
            content="- Buy paint for living room",
            header="## Tasks"
        )
    """
    return obsidian.append_to_note(filepath, content, header)


# Convenience list for graph nodes to import — keep this name stable.
MEMORY_TOOLS = [
    save_fact, update_rule, retrieve_context, search_vault,
    list_memory, hide_fact, hide_rule, append_to_note, read_note,
]
