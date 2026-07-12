"""Tenant-scoped memory/vault browser API for the product web app.

Deliberately does NOT reuse backend/app/routers/memory_api.py's import
pattern (`from app.memory.obsidian import VAULT_ROOT`) — that captures
obsidian.vault_root() ONCE at module-import time (outside any request's
tenant context) and freezes it as a plain constant for the process
lifetime. Every handler here calls obsidian.vault_root() itself, inside
the request, so it always resolves the calling tenant's own vault via
runtime.get_vault_root() (current_tenant_id, set by require_module).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.memory import obsidian
from product.deps import _rate_limit, require_module
from product.tenancy.models import Tenant

logger = logging.getLogger("product.memory")
router = APIRouter(prefix="/api/memory")
_gate = require_module("memory")


# ── Search ────────────────────────────────────────────────────────────────────

@router.get("/search")
async def memory_search(q: str = Query(..., min_length=1), _: Tenant = Depends(_gate)):
    result = obsidian.retrieve_context(q)
    return {"query": q, "result": result}


# ── Categories ────────────────────────────────────────────────────────────────

@router.get("/categories")
async def memory_categories(_: Tenant = Depends(_gate)):
    root = obsidian.vault_root()
    user_cats = [c for c in obsidian.ALLOWED_CATEGORIES if c != "System"]
    counts: dict[str, int] = {}
    for cat in user_cats:
        cat_dir = root / cat
        counts[cat] = (
            sum(1 for p in cat_dir.glob("*.md") if not obsidian._is_hidden(p))
            if cat_dir.is_dir() else 0
        )
    return {"categories": [{"name": c, "count": counts.get(c, 0)} for c in sorted(user_cats)]}


@router.get("/category/{category}")
async def memory_category_entities(category: str, _: Tenant = Depends(_gate)):
    root = obsidian.vault_root()
    cat_dir = root / category
    if not cat_dir.is_dir() or category not in obsidian.ALLOWED_CATEGORIES or category == "System":
        raise HTTPException(status_code=404, detail="category_not_found")

    entities = []
    for path in sorted(cat_dir.glob("*.md")):
        if obsidian._is_hidden(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = obsidian._parse_frontmatter(text)
        name = meta.get("entity", path.stem)
        stat = path.stat()
        preview = ""
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                preview = line[:120]
                break
        entities.append({
            "entity": name,
            "file": path.relative_to(root).as_posix(),
            "updated_at": stat.st_mtime,
            "preview": preview,
        })

    return {"category": category, "entities": entities}


@router.get("/entity")
async def memory_entity_detail(
    category: str = Query(...), entity: str = Query(...), _: Tenant = Depends(_gate),
):
    if category not in obsidian.ALLOWED_CATEGORIES or category == "System":
        raise HTTPException(status_code=404, detail="category_not_found")
    root = obsidian.vault_root()
    cat_dir = root / category
    try:
        slug = obsidian._slugify(entity, obsidian._MAX_ENTITY_LEN)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_entity_name")
    path = cat_dir / f"{slug}.md"
    if not path.exists() or obsidian._is_hidden(path):
        raise HTTPException(status_code=404, detail="entity_not_found")
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = obsidian._parse_frontmatter(text)
    return {"entity": meta.get("entity", slug), "category": category, "content": body.strip(), "meta": meta}


# ── Rules ─────────────────────────────────────────────────────────────────────

@router.get("/rules")
async def memory_rules(_: Tenant = Depends(_gate)):
    import re
    rules_file = obsidian.rules_file()
    if not rules_file.exists():
        return {"rules": []}
    rules = []
    for line in rules_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("~~"):
            continue
        if stripped.startswith("- "):
            body = obsidian._strip_rule_meta(stripped)
            date_match = re.search(r"added (\d{4}-\d{2}-\d{2})", stripped)
            added = date_match.group(1) if date_match else ""
            rules.append({"text": body, "added": added, "raw": stripped})
    return {"rules": rules}


# ── Write operations ────────────────────────────────────────────────────────

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


@router.post("/fact")
async def create_fact(body: FactCreate, _: Tenant = Depends(_gate)):
    return {"result": obsidian.save_fact(body.category, body.entity, body.content)}


@router.post("/rule")
async def create_rule(body: RuleCreate, _: Tenant = Depends(_gate)):
    from app.memory.manager import update_rule
    return {"result": update_rule.invoke({"instruction": body.instruction})}


@router.post("/fact/hide")
async def hide_fact(body: HideFact, _: Tenant = Depends(_gate)):
    return {"result": obsidian.hide_fact(body.category, body.entity)}


@router.post("/rule/hide")
async def hide_rule(body: HideRule, _: Tenant = Depends(_gate)):
    return {"result": obsidian.hide_rule(body.instruction)}


# ── Self-review ──────────────────────────────────────────────────────────────
#
# The owner's engine has no automatic nightly trigger either (despite the
# assistant's own prompt text claiming "at 3 AM" — that's aspirational
# copy, not real code) — it's manually invoked via POST /admin/self-review
# (TEST_TOKEN-gated). This is the same thing, tenant-scoped: analyses the
# calling tenant's own conversation_log (already tenant-scoped, P0) with
# their own Gemini key, writes rules/insights into their own vault.
# get_recent_conversations/obsidian.update_rule/vault_root() are all
# already ContextVar-safe — only capabilities.py's frozen VAULT_ROOT import
# needed guarding (done in self_review.py itself).

@router.post("/self-review")
async def trigger_self_review(hours: int = 24, tenant: Tenant = Depends(_gate)):
    _rate_limit(tenant.id, "self_review", per_minute=2)
    from app.memory.self_review import run_self_review
    result = await run_self_review(hours=min(max(hours, 1), 168))
    return {"result": result}
