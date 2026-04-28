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

VAULT_ROOT = Path(os.getenv("OBSIDIAN_VAULT_PATH", "/app/obsidian_vault"))
RULES_FILE = VAULT_ROOT / "System" / "Rules.md"

ALLOWED_CATEGORIES: set[str] = {
    c.strip() for c in os.getenv(
        "OBSIDIAN_CATEGORIES",
        "System,People,Entities,Investments,Projects,Preferences,Misc",
    ).split(",") if c.strip()
}

MAX_INJECTED_BYTES = 4096
MAX_RETRIEVE_RESULTS = 8
MAX_SNIPPET_CHARS = 400
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
    target = (VAULT_ROOT / cat / f"{ent}.md").resolve()
    root = VAULT_ROOT.resolve()
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
        "---\n\n"
        f"# {entity}\n\n"
    )


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

def save_fact(category: str, entity: str, content: str) -> str:
    """Append a timestamped fact section to `{category}/{entity}.md`.

    Creates the file with YAML frontmatter on first write.
    Refuses category='System' (reserved for rules).
    """
    try:
        path = _safe_path(category, entity)
    except ValueError as e:
        return f"Could not save: {e}"

    section = f"## {_now_iso()}\n{content.strip()}\n\n"

    with _lock_for(path):
        _ensure_parent(path)
        if not path.exists():
            with path.open("w", encoding="utf-8", newline="\n") as f:
                f.write(_frontmatter_block(category, entity))
                f.write(section)
            logger.info("Created vault file: %s", path.relative_to(VAULT_ROOT))
        else:
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(section)
            logger.info("Appended to vault file: %s", path.relative_to(VAULT_ROOT))
    return f"Saved to {path.relative_to(VAULT_ROOT).as_posix()}"


def update_rule(instruction: str) -> str:
    """Append a rule line to `System/Rules.md` (deduped on exact match)."""
    rule = (instruction or "").strip()
    if not rule:
        return "Could not save: empty instruction"
    line = f"- {rule}  _(added {datetime.now(timezone.utc).date().isoformat()})_"

    with _lock_for(RULES_FILE):
        _ensure_parent(RULES_FILE)
        if RULES_FILE.exists():
            existing = RULES_FILE.read_text(encoding="utf-8")
            if any(rule == _strip_rule_meta(ln) for ln in existing.splitlines()):
                return "Rule already exists."
            with RULES_FILE.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        else:
            header = "# Rules\n\nBehavioral rules for the assistant. Edit by hand or use the agent.\n\n"
            RULES_FILE.write_text(header + line + "\n", encoding="utf-8", newline="\n")
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
    logger.info("Hid vault file: %s", path.relative_to(VAULT_ROOT))
    return f"Hidden: {path.relative_to(VAULT_ROOT).as_posix()}"


def hide_rule(instruction: str) -> str:
    """Soft-delete: strikethrough the matching rule line in `System/Rules.md`."""
    target = (instruction or "").strip()
    if not target or not RULES_FILE.exists():
        return "No matching rule."
    with _lock_for(RULES_FILE):
        lines = RULES_FILE.read_text(encoding="utf-8").splitlines()
        changed = False
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("~~"):
                continue
            body = _strip_rule_meta(ln)
            if body and (body == target or target in body):
                lines[i] = "~~" + ln + "~~"
                changed = True
                break
        if not changed:
            return f"No matching rule for: {target!r}"
        tmp = RULES_FILE.with_suffix(".md.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, RULES_FILE)
    logger.info("Hid rule: %.80s", target)
    return f"Hidden rule: {target}"


def read_rules() -> str:
    """Return the contents of `Rules.md`, stripped of strikethrough lines."""
    if not RULES_FILE.exists():
        return ""
    text = RULES_FILE.read_text(encoding="utf-8")
    visible = [ln for ln in text.splitlines() if not ln.lstrip().startswith("~~")]
    out = "\n".join(visible)
    cap = MAX_INJECTED_BYTES // 2
    return out[:cap]


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
    if not VAULT_ROOT.exists():
        return []
    files = []
    for p in VAULT_ROOT.rglob("*.md"):
        if exclude_system and p.is_relative_to(VAULT_ROOT / "System"):
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
        rel = path.relative_to(VAULT_ROOT).as_posix()
        snippet = _best_snippet(text, tokens)
        block = f"### {rel}\n{snippet}\n"
        if used + len(block) > MAX_INJECTED_BYTES:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def read_relevant_facts(query: str, byte_budget: int = MAX_INJECTED_BYTES) -> str:
    """Same engine as retrieve_context but excludes System/ and uses a smaller budget."""
    tokens = _tokenize(query)
    if not tokens:
        return ""

    scored: list[tuple[int, Path, str]] = []
    for path in _vault_files(exclude_system=True):
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
        rel = path.relative_to(VAULT_ROOT).as_posix()
        snippet = _best_snippet(text, tokens)
        block = f"### {rel}\n{snippet}\n"
        if used + len(block) > byte_budget:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def append_to_note(filepath: str, content: str, header: str = "") -> str:
    """Append content to any existing Markdown note by relative file path.

    `filepath` is relative to VAULT_ROOT, e.g. "Daily/2024-01-15.md" or
    "Projects/Home Renovation.md".  If `header` is given (e.g. "## Tasks"),
    the content is inserted at the end of that section; if the header does
    not exist it is created.  Atomic write via a .tmp file so Obsidian never
    sees a partial save.
    """
    # Sanitise: strip leading slashes, block traversal
    clean = filepath.lstrip("/").replace("\\", "/")
    path = (VAULT_ROOT / clean).resolve()
    root = VAULT_ROOT.resolve()
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
            logger.info("Appended to %s", path.relative_to(VAULT_ROOT))
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
            logger.info("Created header %r in %s", header, path.relative_to(VAULT_ROOT))
            return f"Created section '{header}' and appended to {filepath}"

        # Insert before the next section, keeping a blank separator
        lines.insert(insert_idx, "")
        lines.insert(insert_idx, content.strip())
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        logger.info("Appended under %r in %s", header, path.relative_to(VAULT_ROOT))
        return f"Appended under '{header}' in {filepath}"


def list_visible(category: str | None = None) -> str:
    """Directory listing of visible (non-hidden) fact files, plus rules count."""
    if not VAULT_ROOT.exists():
        return "Vault is empty."

    by_cat: dict[str, list[str]] = {}
    for path in _vault_files(exclude_system=True):
        rel = path.relative_to(VAULT_ROOT)
        cat = rel.parts[0] if len(rel.parts) > 1 else "Misc"
        if category and cat != category:
            continue
        by_cat.setdefault(cat, []).append(rel.stem)

    parts: list[str] = []
    if RULES_FILE.exists():
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
