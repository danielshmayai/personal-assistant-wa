"""Obsidian-vault-backed long-term memory.

All filesystem I/O for facts (Markdown files per entity) and rules
(`System/Rules.md`). Designed for a host-mounted Obsidian vault so the
user can browse, edit, and graph-view their memory in Obsidian itself.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("pa.memory.obsidian")

# ── Configuration ──────────────────────────────────────────────────────────

def vault_root() -> Path:
    """Vault directory for the current tenant (via app.runtime; owner base by default)."""
    from app import runtime
    return runtime.get_vault_root()


def rules_file() -> Path:
    return vault_root() / "System" / "Rules.md"


def __getattr__(name):  # PEP 562 — legacy importers (capabilities, self_review, memory_api)
    if name == "VAULT_ROOT":
        return vault_root()
    if name == "RULES_FILE":
        return rules_file()
    raise AttributeError(name)

ALLOWED_CATEGORIES: set[str] = {
    c.strip() for c in os.getenv(
        "OBSIDIAN_CATEGORIES",
        "System,Profile,People,Entities,Investments,Projects,Preferences,Photos,Maps,Misc",
    ).split(",") if c.strip()
} | {"Profile"}  # always allowed: the always-injected user profile lives there

# The always-injected "who is the user" note (maintained by the extractor).
PROFILE_CATEGORY = "Profile"
PROFILE_ENTITY = "Me"

MAX_INJECTED_BYTES = 4096
MAX_RETRIEVE_RESULTS = 8
MAX_SNIPPET_CHARS = 400
# Retrieval snippet per note: the living Summary (or, before the first
# consolidation, the most recent log entries — never the oldest ones).
MAX_NOTE_SNIPPET_CHARS = 700
_MAX_CATEGORY_LEN = 64
_MAX_ENTITY_LEN = 80

_SLUG_RE = re.compile(r"[^\w\- ]+")  # \w is Unicode-aware in Python 3
_STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "what", "about"}

# Per-path locks for in-process write safety
_FILE_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────────────

def _slugify(name: str, max_len: int) -> str:
    if not name:
        raise ValueError("name is empty")
    cleaned = _SLUG_RE.sub("", name).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned or cleaned.startswith(".") or ".." in cleaned:
        raise ValueError(f"invalid name: {name!r}")
    return cleaned[:max_len]


def _safe_path(category: str, entity: str, allow_system: bool = False) -> Path:
    cat = _slugify(category, _MAX_CATEGORY_LEN)
    ent = _slugify(entity, _MAX_ENTITY_LEN)
    if cat not in ALLOWED_CATEGORIES:
        logger.info("Off-list category %r → falling back to Misc", cat)
        cat = "Misc"
    if cat == "System" and not allow_system:
        raise ValueError("category 'System' is reserved for rules")
    target = (vault_root() / cat / f"{ent}.md").resolve()
    root = vault_root().resolve()
    if root not in target.parents:
        raise ValueError("path traversal blocked")
    return target


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(key, threading.Lock())


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _frontmatter_block(category: str, entity: str) -> str:
    return (
        "---\n"
        f"category: {category}\n"
        f"entity: {entity}\n"
        f"created: {datetime.now(timezone.utc).isoformat()}\n"
        "hidden: false\n"
        f"tags: [{category}]\n"
        "---\n\n"
        f"# {entity}\n\n"
        "## Summary\n\n"
        "## Log\n\n"
    )


# ── Note structure: "## Summary" (current truth) + "## Log" (dated entries) ──
#
# Older notes are a flat list of "## <timestamp>" sections. They are converted
# on their next write (structure only, no content change): sections move under
# "## Log" as "### <timestamp>" and an empty Summary is added, which the
# nightly consolidation fills in.

_TS_HEADING = re.compile(r"^#{2,3} (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$", re.MULTILINE)


_SUMMARY_H = re.compile(r"^## Summary[ \t]*$", re.MULTILINE)
_LOG_H = re.compile(r"^## Log[ \t]*$", re.MULTILINE)


def _split_sections(body: str) -> tuple[str, str, str]:
    """Return (preamble incl. '# title', summary_text, log_text) of a note body."""
    lm = _LOG_H.search(body)
    if not lm:
        return body, "", ""
    sm = _SUMMARY_H.search(body, 0, lm.start())
    if not sm:
        return body[: lm.start()], "", body[lm.end():]
    return body[: sm.start()], body[sm.end(): lm.start()].strip(), body[lm.end():]


def _log_entries(log_text: str) -> list[tuple[str, str]]:
    """[(timestamp, content)] oldest first."""
    entries: list[tuple[str, str]] = []
    matches = list(_TS_HEADING.finditer(log_text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(log_text)
        content = log_text[m.end():end].strip()
        if content:
            entries.append((m.group(1), content))
    return entries


def _upgrade_to_log_format(text: str) -> str:
    """Convert a legacy flat note into the Summary/Log shape (idempotent)."""
    meta_part = ""
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            meta_part, body = text[: end + 5], text[end + 5:]
    if _LOG_H.search(body):
        return text
    m = _TS_HEADING.search(body)
    head = body[: m.start()] if m else body
    rest = body[m.start():] if m else ""
    rest = re.sub(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", r"### \1", rest, flags=re.MULTILINE)
    head = head.rstrip() + "\n\n" if head.strip() else ""
    return f"{meta_part}{head}## Summary\n\n## Log\n\n{rest.lstrip()}"


def note_parts(path: Path) -> dict:
    """Parsed view of a note: meta, title, summary, log entries."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)
    pre, summary, log = _split_sections(body)
    if not log and not summary:
        # Legacy flat note — every timestamped section is a log entry.
        entries = _log_entries(body)
    else:
        entries = _log_entries(log)
    title = meta.get("entity") or path.stem
    tm = re.search(r"^# (.+)$", pre, re.MULTILINE)
    if tm:
        title = tm.group(1).strip()
    return {"meta": meta, "title": title, "summary": summary, "entries": entries}


def _normalize(s: str) -> str:
    return re.sub(r"[\W_]+", " ", (s or "").lower()).strip()


_INDEX_POOL = None


def _schedule_index(path: Path) -> None:
    """Re-embed a note right after it changes (background thread, tenant ctx kept).

    Before this, only the external nightly job indexed the vault, so anything
    learned during the day was invisible to semantic recall.
    """
    global _INDEX_POOL
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    if _INDEX_POOL is None:
        _INDEX_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vault-index")
    ctx = contextvars.copy_context()
    _INDEX_POOL.submit(ctx.run, _index_now, path)


def _index_now(path: Path) -> None:
    from app.memory import embeddings
    try:
        rel = path.relative_to(vault_root()).as_posix()
        if _is_hidden(path):
            embeddings.delete_embedding(rel)
            return
        text = index_text(path)
        if text:
            embeddings.upsert_embedding(rel, text)
    except Exception:
        logger.warning("index on write failed for %s", path, exc_info=True)


def index_text(path: Path) -> str:
    """Text that represents a note in the vector index (what retrieval matches)."""
    try:
        p = note_parts(path)
    except OSError:
        return ""
    recent = "\n".join(c for _, c in p["entries"][-6:])
    aliases = p["meta"].get("aliases", "")
    parts = [p["title"]]
    if aliases:
        parts.append(f"Also known as: {aliases}")
    if p["summary"]:
        parts.append(p["summary"])
    if recent:
        parts.append(recent)
    return "\n".join(parts)[:6000]


def note_snippet(path: Path, budget: int = MAX_NOTE_SNIPPET_CHARS) -> str:
    """What gets injected for a note: its Summary, else its NEWEST log entries."""
    p = note_parts(path)
    if p["summary"]:
        return p["summary"][:budget]
    out: list[str] = []
    used = 0
    for ts, content in reversed(p["entries"]):
        line = f"- ({ts[:10]}) {content}"
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(reversed(out))


def unsummarized_entries(path: Path) -> int:
    """How many log entries were added since the Summary was last rewritten.

    Tracked as the entry count at summary time (`summarized_entries`), not a
    timestamp: entry headings have minute resolution, so an entry added in the
    same minute as the summary would otherwise never be consolidated.
    """
    p = note_parts(path)
    try:
        seen = int(p["meta"].get("summarized_entries", "") or 0)
    except ValueError:
        seen = 0
    return max(len(p["entries"]) - seen, 0)


def write_summary(path: Path, summary: str) -> None:
    """Replace a note's Summary section (and stamp `summarized`)."""
    with _lock_for(path):
        text = _upgrade_to_log_format(path.read_text(encoding="utf-8"))
        meta, body = _parse_frontmatter(text)
        pre, _, log = _split_sections(body)
        new_body = f"{pre.strip()}\n\n## Summary\n\n{summary.strip()}\n\n## Log{log}"
        meta["summarized"] = _now_iso()
        meta["summarized_entries"] = str(len(_log_entries(log)))
        new = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n\n" + new_body.lstrip("\n")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    _schedule_index(path)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML-ish parser — only flat key: value pairs."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta: dict = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[end + 5:]


def _set_frontmatter_field(path: Path, key: str, value: str) -> None:
    """Update or insert a single frontmatter field; rewrite atomically."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    meta, body = _parse_frontmatter(text)
    meta[key] = value
    new = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n\n" + body
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _is_hidden(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as f:
            head = f.read(512)
        meta, _ = _parse_frontmatter(head)
        return str(meta.get("hidden", "")).lower() == "true"
    except OSError:
        return False


# ── Public API ─────────────────────────────────────────────────────────────

def _vault_entity_names() -> list[str]:
    """Return all entity names (stem of every .md file) currently in the vault,
    longest first so multi-word names match before their substrings."""
    if not vault_root().exists():
        return []
    names = [p.stem for p in vault_root().rglob("*.md") if not p.stem.startswith(".")]
    # Longest first to avoid partial replacements (e.g. "Tel Aviv" before "Aviv")
    return sorted(set(names), key=len, reverse=True)


def _auto_link_content(content: str, skip_entity: str) -> str:
    """Inject [[wikilinks]] for any vault entity name mentioned in content.

    Skips the note's own entity name (avoid self-referencing link).
    Only replaces bare mentions — already-linked [[...]] text is left untouched.
    Uses whole-word, case-insensitive matching (respects Hebrew words too).
    """
    for name in _vault_entity_names():
        if name == skip_entity or len(name) < 3:
            continue
        # Skip names that are already wikilinked anywhere in the content
        if f"[[{name}]]" in content or f"[[{name}|" in content:
            continue
        # Whole-word boundary — works for both Latin and Hebrew characters
        pattern = rf"(?<!\[)(?<!\w){re.escape(name)}(?!\w)(?!\])"
        replaced = re.sub(pattern, f"[[{name}]]", content, count=1, flags=re.IGNORECASE)
        if replaced != content:
            content = replaced
    return content


def save_fact(category: str, entity: str, content: str) -> str:
    """Append a dated entry to the Log of `{category}/{entity}.md`.

    Creates the note (frontmatter + Summary + Log) on first write, upgrades
    legacy flat notes to that shape, skips content the note already holds,
    and re-indexes the note immediately so it is recallable right away.
    Refuses category='System' (reserved for rules).
    """
    try:
        path = _safe_path(category, entity)
    except ValueError as e:
        return f"Could not save: {e}"

    linked_content = _auto_link_content(content.strip(), skip_entity=path.stem)
    if not linked_content:
        return "Could not save: empty content"
    entry = f"### {_now_iso()}\n{linked_content}\n\n"

    with _lock_for(path):
        _ensure_parent(path)
        if not path.exists():
            with path.open("w", encoding="utf-8", newline="\n") as f:
                f.write(_frontmatter_block(category, entity))
                f.write(entry)
            logger.info("Created vault file: %s", path.relative_to(vault_root()))
        else:
            text = path.read_text(encoding="utf-8")
            norm = _normalize(content)
            if len(norm) >= 12 and norm in _normalize(text):
                return f"Already known — {path.relative_to(vault_root()).as_posix()} unchanged."
            upgraded = _upgrade_to_log_format(text)
            if upgraded != text:
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(upgraded, encoding="utf-8", newline="\n")
                os.replace(tmp, path)
            with path.open("a", encoding="utf-8", newline="\n") as f:
                if not upgraded.endswith("\n\n"):
                    f.write("\n" if upgraded.endswith("\n") else "\n\n")
                f.write(entry)
            logger.info("Appended to vault file: %s", path.relative_to(vault_root()))
    _schedule_index(path)
    return f"Saved to {path.relative_to(vault_root()).as_posix()}"


def profile_path() -> Path:
    return vault_root() / PROFILE_CATEGORY / f"{PROFILE_ENTITY}.md"


def read_profile(budget: int = 1500) -> str:
    """The always-injected user profile (Summary, else newest entries)."""
    path = profile_path()
    if not path.exists() or _is_hidden(path):
        return ""
    try:
        return note_snippet(path, budget)
    except OSError:
        return ""


def update_rule(instruction: str) -> str:
    """Append a rule line to `System/Rules.md` (deduped on exact match)."""
    rule = (instruction or "").strip()
    if not rule:
        return "Could not save: empty instruction"
    line = f"- {rule}  _(added {datetime.now(timezone.utc).date().isoformat()})_"

    with _lock_for(rules_file()):
        _ensure_parent(rules_file())
        if rules_file().exists():
            existing = rules_file().read_text(encoding="utf-8")
            if any(rule == _strip_rule_meta(ln) for ln in existing.splitlines()):
                return "Rule already exists."
            with rules_file().open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        else:
            header = "# Rules\n\nBehavioral rules for the assistant. Edit by hand or use the agent.\n\n"
            rules_file().write_text(header + line + "\n", encoding="utf-8", newline="\n")
    logger.info("Saved rule: %.80s", rule)
    return f"Saved rule: {rule}"


def _strip_rule_meta(line: str) -> str:
    """Extract the rule body from a `- rule  _(added ...)_` line."""
    s = line.strip()
    if s.startswith("~~"):
        s = s.lstrip("~").rstrip("~").strip()
    if s.startswith("- "):
        s = s[2:]
    return re.sub(r"\s*_\(added .*?\)_\s*$", "", s).strip()


def hide_fact(category: str, entity: str) -> str:
    """Soft-delete: flip frontmatter `hidden: true`. File contents preserved."""
    try:
        path = _safe_path(category, entity)
    except ValueError as e:
        return f"Could not hide: {e}"
    if not path.exists():
        return f"No fact found at {category}/{entity}."
    with _lock_for(path):
        _set_frontmatter_field(path, "hidden", "true")
    try:
        from app.memory.embeddings import delete_embedding
        delete_embedding(path.relative_to(vault_root()).as_posix())
    except Exception:
        logger.warning("could not drop embedding for hidden note %s", path, exc_info=True)
    logger.info("Hid vault file: %s", path.relative_to(vault_root()))
    return f"Hidden: {path.relative_to(vault_root()).as_posix()}"


def hide_rule(instruction: str) -> str:
    """Soft-delete: strikethrough the matching rule line in `System/Rules.md`."""
    target = (instruction or "").strip()
    if not target or not rules_file().exists():
        return "No matching rule."
    with _lock_for(rules_file()):
        lines = rules_file().read_text(encoding="utf-8").splitlines()
        changed = False
        removed = ""
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("~~"):
                continue
            body = _strip_rule_meta(ln)
            if body and (body == target or target in body):
                lines[i] = "~~" + ln + "~~"
                changed = True
                removed = body
                break
        if not changed:
            return f"No matching rule for: {target!r}"
        tmp = rules_file().with_suffix(".md.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, rules_file())
    try:
        from app.memory.embeddings import delete_rule_embedding
        delete_rule_embedding(removed)
    except Exception:
        logger.warning("could not drop embedding for hidden rule", exc_info=True)
    logger.info("Hid rule: %.80s", target)
    return f"Hidden rule: {target}"


def active_rules() -> list[str]:
    """Bodies of all rules that are not struck through, oldest first."""
    if not rules_file().exists():
        return []
    out = []
    for ln in rules_file().read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("- ") and not s.startswith("~~"):
            body = _strip_rule_meta(s)
            if body:
                out.append(body)
    return out


def read_rules(budget: int = MAX_INJECTED_BYTES // 2) -> str:
    """Active rules as a bullet list that fits `budget` characters.

    When over budget the NEWEST rules win (they reflect the latest
    corrections). This used to take the first N characters of the file,
    which silently dropped every rule added after the file passed ~2KB.
    """
    rules = active_rules()
    picked: list[str] = []
    used = 0
    for rule in reversed(rules):
        line = f"- {rule}"
        if used + len(line) + 1 > budget:
            break
        picked.append(line)
        used += len(line) + 1
    return "\n".join(reversed(picked))


def _tokenize(query: str) -> list[str]:
    # \w matches Unicode letters/digits — needed for Hebrew/Arabic queries
    tokens = re.findall(r"\w{2,}", (query or "").lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _score_text(text: str, tokens: list[str]) -> int:
    if not tokens:
        return 0
    lowered = text.lower()
    return sum(lowered.count(t) for t in tokens)


def _best_snippet(text: str, tokens: list[str]) -> str:
    """Return a window of ~MAX_SNIPPET_CHARS centered on the densest token cluster."""
    if not text or not tokens:
        return text[:MAX_SNIPPET_CHARS]
    lowered = text.lower()
    best_pos, best_hits = 0, -1
    step = max(MAX_SNIPPET_CHARS // 2, 50)
    for pos in range(0, max(len(text) - MAX_SNIPPET_CHARS + 1, 1), step):
        window = lowered[pos:pos + MAX_SNIPPET_CHARS]
        hits = sum(window.count(t) for t in tokens)
        if hits > best_hits:
            best_hits, best_pos = hits, pos
    snippet = text[best_pos:best_pos + MAX_SNIPPET_CHARS].strip()
    return snippet


def _vault_files(exclude_system: bool = False) -> list[Path]:
    if not vault_root().exists():
        return []
    files = []
    for p in vault_root().rglob("*.md"):
        if exclude_system and p.is_relative_to(vault_root() / "System"):
            continue
        if _is_hidden(p):
            continue
        files.append(p)
    return files


def retrieve_context(query: str, limit: int = MAX_RETRIEVE_RESULTS) -> str:
    """Keyword search across visible vault files. Returns top-N snippets."""
    tokens = _tokenize(query)
    if not tokens:
        return "Provide a more specific query (3+ characters)."

    scored: list[tuple[int, Path, str]] = []
    for path in _vault_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        score = _score_text(text, tokens)
        if score > 0:
            scored.append((score, path, text))

    if not scored:
        return f"No vault entries matched: {query!r}"

    scored.sort(key=lambda t: t[0], reverse=True)
    parts: list[str] = []
    used = 0
    for _, path, text in scored[:limit]:
        rel = path.relative_to(vault_root()).as_posix()
        snippet = _best_snippet(text, tokens)
        block = f"### {rel}\n{snippet}\n"
        if used + len(block) > MAX_INJECTED_BYTES:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def read_relevant_facts(query: str, byte_budget: int = MAX_INJECTED_BYTES,
                        exclude: set[str] | None = None) -> str:
    """Keyword fallback for injection: excludes System/ (and `exclude` paths).

    Injects each hit's Summary when it has one; otherwise the densest window.
    """
    tokens = _tokenize(query)
    if not tokens:
        return ""

    exclude = exclude or set()
    scored: list[tuple[int, Path, str]] = []
    for path in _vault_files(exclude_system=True):
        rel = path.relative_to(vault_root()).as_posix()
        if rel in exclude:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        score = _score_text(text, tokens)
        if score > 0:
            scored.append((score, path, text))

    if not scored:
        return ""

    scored.sort(key=lambda t: t[0], reverse=True)
    parts: list[str] = []
    used = 0
    for _, path, text in scored[:MAX_RETRIEVE_RESULTS]:
        rel = path.relative_to(vault_root()).as_posix()
        try:
            p = note_parts(path)
            snippet = p["summary"][:MAX_NOTE_SNIPPET_CHARS] if p["summary"] else _best_snippet(text, tokens)
        except OSError:
            continue
        block = f"### {rel}\n{snippet}\n"
        if used + len(block) > byte_budget:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def append_to_note(filepath: str, content: str, header: str = "") -> str:
    """Append to an existing note, then re-index it (see _append_to_note)."""
    result = _append_to_note(filepath, content, header)
    if result.startswith(("Appended", "Created")):
        _schedule_index((vault_root() / filepath.lstrip("/").replace("\\", "/")).resolve())
    return result


def _append_to_note(filepath: str, content: str, header: str = "") -> str:
    """Append content to any existing Markdown note by relative file path.

    `filepath` is relative to vault_root(), e.g. "Daily/2024-01-15.md" or
    "Projects/Home Renovation.md".  If `header` is given (e.g. "## Tasks"),
    the content is inserted at the end of that section; if the header does
    not exist it is created.  Atomic write via a .tmp file so Obsidian never
    sees a partial save.
    """
    # Sanitise: strip leading slashes, block traversal
    clean = filepath.lstrip("/").replace("\\", "/")
    path = (vault_root() / clean).resolve()
    root = vault_root().resolve()
    if root not in path.parents and path != root:
        return "Error: path traversal blocked"
    if not clean.endswith(".md"):
        return "Error: only .md files are supported"

    with _lock_for(path):
        if not path.exists():
            return f"Error: file not found: {filepath!r}. Use save_fact() to create new notes."

        if not header:
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(f"\n{content.strip()}\n")
            logger.info("Appended to %s", path.relative_to(vault_root()))
            return f"Appended to {filepath}"

        # Locate the header line (match ignoring leading #s and whitespace)
        target = header.lstrip("#").strip()
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        insert_idx: int | None = None
        section_level = 0
        for i, line in enumerate(lines):
            if not line.startswith("#"):
                continue
            level = len(line) - len(line.lstrip("#"))
            title = line.lstrip("#").strip()
            if title == target:
                section_level = level
                # Scan forward to end of section (next heading of same/higher level)
                for j in range(i + 1, len(lines)):
                    jl = lines[j]
                    if jl.startswith("#"):
                        jlevel = len(jl) - len(jl.lstrip("#"))
                        if jlevel <= section_level:
                            insert_idx = j
                            break
                else:
                    insert_idx = len(lines)
                break

        if insert_idx is None:
            # Header missing — append it + content
            addition = f"\n{header}\n\n{content.strip()}\n"
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(addition)
            logger.info("Created header %r in %s", header, path.relative_to(vault_root()))
            return f"Created section '{header}' and appended to {filepath}"

        # Insert before the next section, keeping a blank separator
        lines.insert(insert_idx, "")
        lines.insert(insert_idx, content.strip())
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        logger.info("Appended under %r in %s", header, path.relative_to(vault_root()))
        return f"Appended under '{header}' in {filepath}"


def grep_note(filepath: str, keyword: str, max_results: int = 100) -> str:
    """Return only the lines of a vault file that contain `keyword`.

    Server-side filtering — only matching lines are returned, keeping
    token usage minimal even for large files.
    """
    clean = filepath.lstrip("/").replace("\\", "/")
    path = (vault_root() / clean).resolve()
    root = vault_root().resolve()
    if root not in path.parents and path != root:
        return "Error: path traversal blocked"
    if not path.exists():
        return f"Error: file not found: {filepath!r}"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        kw_lower = keyword.lower()
        matches = [(i + 1, ln) for i, ln in enumerate(lines) if kw_lower in ln.lower()]
        total = len(matches)
        if total == 0:
            return f"No lines containing {keyword!r} in {filepath}"
        shown = matches[:max_results]
        out = [f"Found {total} line(s) containing {keyword!r} in {filepath}:"]
        out.extend(f"  {no}: {ln}" for no, ln in shown)
        if total > max_results:
            out.append(f"  … and {total - max_results} more (increase max_results if needed)")
        return "\n".join(out)
    except OSError as e:
        return f"Error reading file: {e}"


def read_note(filepath: str, max_chars: int = 32_000) -> str:
    """Return the full content of a vault note for in-context analysis.

    Use when the user asks to analyze, count, filter, or summarize the
    contents of a specific vault file (e.g. a contacts list).
    `filepath` is relative to vault root, e.g. "Misc/רשימת_אנשי_קשר.md".
    """
    clean = filepath.lstrip("/").replace("\\", "/")
    path = (vault_root() / clean).resolve()
    root = vault_root().resolve()
    if root not in path.parents and path != root:
        return "Error: path traversal blocked"
    if not clean.endswith(".md"):
        return "Error: only .md files are supported"
    if not path.exists():
        return f"Error: file not found: {filepath!r}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[…truncated at {max_chars} chars]"
        return text
    except OSError as e:
        return f"Error reading file: {e}"


def read_facts_by_paths(filepaths: list[str], byte_budget: int = MAX_INJECTED_BYTES) -> str:
    """Injectable text for semantic-search hits: each note's Summary, or its
    NEWEST entries when it hasn't been summarized yet (was: the first 400
    chars of the file, i.e. its oldest content)."""
    parts: list[str] = []
    used = 0
    root = vault_root().resolve()
    for rel in filepaths:
        path = (vault_root() / rel).resolve()
        if root not in path.parents or not path.exists() or _is_hidden(path):
            continue
        try:
            snippet = note_snippet(path)
            title = note_parts(path)["title"]
        except OSError:
            continue
        if not snippet:
            continue
        block = f"### {title} ({rel})\n{snippet}\n"
        if used + len(block) > byte_budget:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def indexable_notes() -> list[Path]:
    """Visible notes that belong in the vector index (System/ excluded)."""
    return _vault_files(exclude_system=True)


def list_visible(category: str = "") -> str:
    """Directory listing of visible (non-hidden) fact files, plus rules count."""
    if not vault_root().exists():
        return "Vault is empty."

    by_cat: dict[str, list[str]] = {}
    for path in _vault_files(exclude_system=True):
        rel = path.relative_to(vault_root())
        cat = rel.parts[0] if len(rel.parts) > 1 else "Misc"
        if category and cat != category:
            continue
        by_cat.setdefault(cat, []).append(rel.stem)

    parts: list[str] = []
    if rules_file().exists():
        rules_text = read_rules()
        rule_count = sum(1 for ln in rules_text.splitlines() if ln.strip().startswith("- "))
        parts.append(f"*Rules:* {rule_count} active (see System/Rules.md)")

    if not by_cat:
        parts.append("No facts saved yet." if not category else f"No facts in {category}.")
    else:
        for cat in sorted(by_cat):
            entries = sorted(by_cat[cat])
            parts.append(f"*{cat}:* {', '.join(entries)}")

    return "\n".join(parts)


def remove_log_entry(rel_path: str, content: str) -> bool:
    """Delete the log entry whose text matches `content` ("that's wrong — forget it").

    The Summary is cleared too (and `summarized` reset) because it may already
    repeat the wrong fact; the note then shows its remaining entries until the
    next consolidation rewrites the Summary without it.
    """
    clean = rel_path.lstrip("/").replace("\\", "/")
    path = (vault_root() / clean).resolve()
    root = vault_root().resolve()
    if root not in path.parents or not path.exists():
        return False
    target = _normalize(content)
    if not target:
        return False
    with _lock_for(path):
        text = _upgrade_to_log_format(path.read_text(encoding="utf-8"))
        meta, body = _parse_frontmatter(text)
        pre, summary, log = _split_sections(body)
        matches = list(_TS_HEADING.finditer(log))
        kept: list[str] = []
        removed = False
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(log)
            chunk = log[m.start():end]
            entry = log[m.end():end].strip()
            if not removed and (_normalize(entry) == target or target in _normalize(entry)):
                removed = True
                continue
            kept.append(chunk.rstrip() + "\n\n")
        if not removed:
            return False
        meta["summarized"] = ""
        meta["summarized_entries"] = "0"
        new_body = f"{pre.strip()}\n\n## Summary\n\n## Log\n\n{''.join(kept)}"
        new = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n\n" + new_body.lstrip("\n")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    _schedule_index(path)
    logger.info("Removed log entry from %s", clean)
    return True
