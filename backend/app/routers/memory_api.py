"""Memory API — browse and manage the Obsidian vault from the web UI."""
from __future__ import annotations

import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from app.config import TEST_TOKEN
from app.memory import obsidian
from app.memory.obsidian import VAULT_ROOT, ALLOWED_CATEGORIES, _is_hidden, _parse_frontmatter

logger = logging.getLogger("pa.memory_api")
router = APIRouter()
_bearer = HTTPBearer(auto_error=False)


def _require_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not TEST_TOKEN or not creds or creds.credentials != TEST_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return creds.credentials


# ── Search ────────────────────────────────────────────────────────────────────

@router.get("/api/memory/search")
async def memory_search(
    q: str = Query(..., min_length=1),
    _: str = Depends(_require_token),
):
    result = obsidian.retrieve_context(q)
    return {"query": q, "result": result}


# ── Categories ────────────────────────────────────────────────────────────────

@router.get("/api/memory/categories")
async def memory_categories(_: str = Depends(_require_token)):
    user_cats = [c for c in ALLOWED_CATEGORIES if c != "System"]
    counts: dict[str, int] = {}
    for cat in user_cats:
        cat_dir = VAULT_ROOT / cat
        if cat_dir.is_dir():
            counts[cat] = sum(
                1 for p in cat_dir.glob("*.md") if not _is_hidden(p)
            )
        else:
            counts[cat] = 0
    return {"categories": [{"name": c, "count": counts.get(c, 0)} for c in sorted(user_cats)]}


@router.get("/api/memory/category/{category}")
async def memory_category_entities(
    category: str,
    _: str = Depends(_require_token),
):
    cat_dir = VAULT_ROOT / category
    if not cat_dir.is_dir() or category not in ALLOWED_CATEGORIES or category == "System":
        raise HTTPException(status_code=404, detail="Category not found")

    entities = []
    for path in sorted(cat_dir.glob("*.md")):
        if _is_hidden(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = _parse_frontmatter(text)
        name = meta.get("entity", path.stem)
        stat = path.stat()
        preview = ""
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("##"):
                preview = line[:120]
                break
        entities.append({
            "entity": name,
            "file": path.relative_to(VAULT_ROOT).as_posix(),
            "updated_at": stat.st_mtime,
            "preview": preview,
        })

    return {"category": category, "entities": entities}


@router.get("/api/memory/entity")
async def memory_entity_detail(
    category: str = Query(...),
    entity: str = Query(...),
    _: str = Depends(_require_token),
):
    if category not in ALLOWED_CATEGORIES or category == "System":
        raise HTTPException(status_code=404, detail="Category not found")
    cat_dir = VAULT_ROOT / category
    # Find the entity file by looking for slugified matches
    from app.memory.obsidian import _slugify, _MAX_ENTITY_LEN
    try:
        slug = _slugify(entity, _MAX_ENTITY_LEN)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid entity name")
    path = cat_dir / f"{slug}.md"
    if not path.exists() or _is_hidden(path):
        raise HTTPException(status_code=404, detail="Entity not found")
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)
    return {"entity": meta.get("entity", slug), "category": category, "content": body.strip(), "meta": meta}


# ── Rules ─────────────────────────────────────────────────────────────────────

@router.get("/api/memory/rules")
async def memory_rules(_: str = Depends(_require_token)):
    from app.memory.obsidian import RULES_FILE, _strip_rule_meta
    import re
    if not RULES_FILE.exists():
        return {"rules": []}
    rules = []
    for line in RULES_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("~~"):
            continue
        if stripped.startswith("- "):
            body = _strip_rule_meta(stripped)
            date_match = re.search(r"added (\d{4}-\d{2}-\d{2})", stripped)
            added = date_match.group(1) if date_match else ""
            rules.append({"text": body, "added": added, "raw": stripped})
    return {"rules": rules}


# ── Write operations ──────────────────────────────────────────────────────────

class FactCreate(BaseModel):
    category: str
    entity: str
    content: str


class RuleCreate(BaseModel):
    instruction: str


class HideFact(BaseModel):
    category: str
    entity: str


class HideRule(BaseModel):
    instruction: str


@router.post("/api/memory/fact")
async def create_fact(body: FactCreate, _: str = Depends(_require_token)):
    result = obsidian.save_fact(body.category, body.entity, body.content)
    return {"result": result}


@router.post("/api/memory/rule")
async def create_rule(body: RuleCreate, _: str = Depends(_require_token)):
    from app.memory.manager import update_rule
    result = update_rule.invoke({"instruction": body.instruction})
    return {"result": result}


@router.post("/api/memory/fact/hide")
async def hide_fact(body: HideFact, _: str = Depends(_require_token)):
    result = obsidian.hide_fact(body.category, body.entity)
    return {"result": result}


@router.post("/api/memory/rule/hide")
async def hide_rule(body: HideRule, _: str = Depends(_require_token)):
    result = obsidian.hide_rule(body.instruction)
    return {"result": result}
