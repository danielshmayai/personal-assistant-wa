"""Long-term memory pipeline: vault note shape, indexing, extraction, retrieval.

Regression tests for the reasons the "second brain" stopped working:
retired embedding model with DEBUG-only errors, no index on write, the first
400 chars (oldest content) injected, rules cut at 2KB, every fact dumped into
Preferences/ as one-liners, and silent fire-and-forget background work.

No network and no database: the vault is a tmp dir wired through
runtime.set_vault_root_resolver, embeddings/LLM/DB calls are patched.
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage


def _run(coro):
    # A private loop, leaving a fresh current loop behind: asyncio.run() would
    # unset it and break later tests that use get_event_loop().
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def _fresh_app_modules():
    """Other test files purge app.* from sys.modules, leaving stale submodule
    attributes on the app package; start every test from a clean import."""
    for key in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
        del sys.modules[key]
    yield


@pytest.fixture()
def vault(tmp_path):
    """Point the vault resolver at a fresh tmp dir; index-on-write disabled."""
    from app import runtime
    from app.memory import obsidian
    runtime.set_vault_root_resolver(lambda: tmp_path)
    with patch.object(obsidian, "_schedule_index"):
        yield tmp_path
    runtime.set_vault_root_resolver(runtime._default_vault_root)


# ── Note shape ───────────────────────────────────────────────────────────────

def test_new_note_has_summary_and_log_sections(vault):
    from app.memory import obsidian
    assert obsidian.save_fact("People", "Dana", "Dana is my sister, lives in Haifa.").startswith("Saved to ")
    text = (vault / "People" / "Dana.md").read_text(encoding="utf-8")
    assert "## Summary" in text and "## Log" in text
    assert text.index("## Summary") < text.index("## Log") < text.index("Dana is my sister")
    assert "### " in text  # dated log entry


def test_legacy_flat_note_is_upgraded_and_keeps_entries(vault):
    from app.memory import obsidian
    legacy = vault / "People" / "Avi.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "---\ncategory: People\nentity: Avi\nhidden: false\n---\n\n# Avi\n\n"
        "## 2026-01-01 10:00\nAvi works at Intel.\n\n", encoding="utf-8",
    )
    obsidian.save_fact("People", "Avi", "Avi moved to Google in March 2026.")
    parts = obsidian.note_parts(legacy)
    contents = [c for _, c in parts["entries"]]
    assert contents == ["Avi works at Intel.", "Avi moved to Google in March 2026."]
    assert "## Log" in legacy.read_text(encoding="utf-8")


def test_duplicate_content_is_not_appended_again(vault):
    from app.memory import obsidian
    obsidian.save_fact("Projects", "Kitchen", "Budget is 40k shekels, contractor Moshe.")
    again = obsidian.save_fact("Projects", "Kitchen", "Budget is 40k shekels, contractor Moshe.")
    assert again.startswith("Already known")
    assert len(obsidian.note_parts(vault / "Projects" / "Kitchen.md")["entries"]) == 1


def test_snippet_shows_newest_entries_not_oldest(vault):
    """The old reader injected the first 400 chars — i.e. the OLDEST content."""
    from app.memory import obsidian
    obsidian.save_fact("People", "Noa", "OLD: Noa studies law. " + "x" * 300)
    obsidian.save_fact("People", "Noa", "NEW: Noa started at Herzog Fox Neeman.")
    snippet = obsidian.note_snippet(vault / "People" / "Noa.md", budget=120)
    assert "NEW:" in snippet and "OLD:" not in snippet


def test_snippet_prefers_summary_once_written(vault):
    from app.memory import obsidian
    obsidian.save_fact("People", "Noa", "Noa studies law.")
    path = vault / "People" / "Noa.md"
    obsidian.write_summary(path, "- Lawyer at Herzog Fox Neeman")
    assert obsidian.note_snippet(path) == "- Lawyer at Herzog Fox Neeman"
    assert obsidian.unsummarized_entries(path) == 0
    obsidian.save_fact("People", "Noa", "Noa got engaged to Ido.")
    assert obsidian.unsummarized_entries(path) == 1


def test_save_fact_reindexes_immediately(vault):
    from app.memory import obsidian
    obsidian.save_fact("People", "Dana", "Dana's birthday is 1990-05-04.")
    obsidian._schedule_index.assert_called_once()
    with patch("app.memory.embeddings.upsert_embedding", return_value=True) as up:
        obsidian._index_now(vault / "People" / "Dana.md")
    rel, text = up.call_args.args
    assert rel == "People/Dana.md" and "1990-05-04" in text


def test_remove_log_entry_forgets_and_clears_summary(vault):
    from app.memory import obsidian
    obsidian.save_fact("People", "Dana", "Dana lives in Haifa.")
    obsidian.save_fact("People", "Dana", "Dana is allergic to peanuts.")
    path = vault / "People" / "Dana.md"
    obsidian.write_summary(path, "- Lives in Haifa\n- Allergic to peanuts")
    assert obsidian.remove_log_entry("People/Dana.md", "Dana is allergic to peanuts.")
    parts = obsidian.note_parts(path)
    assert [c for _, c in parts["entries"]] == ["Dana lives in Haifa."]
    assert parts["summary"] == ""  # the wrong fact can't linger in the summary


# ── Rules ────────────────────────────────────────────────────────────────────

def test_rules_keep_newest_when_over_budget(vault):
    """Was: first 2048 chars of the file → every rule after ~20 silently dropped."""
    from app.memory import obsidian
    for i in range(30):
        obsidian.update_rule(f"Rule number {i:02d} — " + "detail " * 8)
    obsidian.hide_rule("Rule number 29")
    text = obsidian.read_rules(budget=600)
    assert "Rule number 28" in text           # newest active rule is injected
    assert "Rule number 29" not in text       # struck-through rule is not
    assert "Rule number 00" not in text       # oldest dropped when over budget
    assert len(text) <= 600


# ── Extraction ───────────────────────────────────────────────────────────────

def test_parse_items_handles_fences_and_confidence():
    from app.memory.reflection import _parse_items
    raw = '```json\n[{"kind":"person","entity":"Dana","content":"Dana is my sister.","confidence":0.9},' \
          '{"kind":"fact","entity":"me","content":"maybe","confidence":0.3}]\n```'
    items = _parse_items(raw)
    assert items == [{"kind": "person", "entity": "Dana", "content": "Dana is my sister."}]
    assert _parse_items("nothing here") == []


@pytest.mark.parametrize("kind,entity,expected", [
    ("person", "Dana", ("People", "Dana")),
    ("project", "Kitchen", ("Projects", "Kitchen")),
    ("decision", "Kitchen", ("Projects", "Kitchen")),
    ("preference", "me", ("Profile", "Me")),
    ("fact", "me", ("Profile", "Me")),
    ("fact", "Intel", ("Entities", "Intel")),
    ("commitment", "lawyer", ("Profile", "Commitments")),
    ("rule", "", None),
])
def test_routing(kind, entity, expected):
    from app.memory.reflection import route
    assert route({"kind": kind, "entity": entity, "content": "x"}) == expected


def test_only_the_new_exchange_is_extracted_from():
    """Was: the last 6 turns re-extracted on every message → duplicates."""
    from app.memory.reflection import split_new_exchange
    msgs = [HumanMessage(content="My sister Dana lives in Haifa"), AIMessage(content="Noted!"),
            HumanMessage(content="I'm renovating the kitchen"), AIMessage(content="Nice")]
    new, earlier = split_new_exchange(msgs)
    assert "kitchen" in new and "Dana" not in new
    assert "Dana" in earlier


def test_extract_and_store_files_items_where_a_person_would_look(vault):
    from app.memory import reflection
    reply = json.dumps([
        {"kind": "person", "entity": "Dana", "content": "Dana (sister) moved to Haifa.", "confidence": 0.9},
        {"kind": "project", "entity": "Kitchen", "content": "Kitchen renovation budget is 40k.", "confidence": 0.8},
        {"kind": "preference", "entity": "me", "content": "Prefers short answers in Hebrew.", "confidence": 0.9},
        {"kind": "rule", "entity": "", "content": "Never use emojis.", "confidence": 0.95},
    ])
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=reply))
    msgs = [HumanMessage(content="Dana moved to Haifa; kitchen budget 40k; short answers please, no emojis"),
            AIMessage(content="Got it")]
    with patch.object(reflection, "get_smart_llm", return_value=llm), \
         patch("app.memory.store.record_memory_event") as rec, \
         patch("app.memory.embeddings.find_similar_rule", return_value=None), \
         patch("app.memory.embeddings.store_rule_embedding"):
        stored = _run(reflection.extract_and_store(msgs, known_memory=""))

    paths = sorted(s["path"] for s in stored)
    assert paths == ["People/Dana.md", "Profile/Me.md", "Projects/Kitchen.md", "System/Rules.md"]
    assert not (vault / "Preferences").exists()  # no more one-liner dumps
    assert "Never use emojis." in (vault / "System" / "Rules.md").read_text(encoding="utf-8")
    assert rec.call_count == 4
    prompt = llm.ainvoke.call_args.args[0][0].content
    assert "KNOWN MEMORY" in prompt


def test_reflection_node_respects_memory_module_toggle():
    from app.memory import reflection
    with patch("app.background.spawn") as spawn:
        _run(reflection.reflection_node({"messages": [], "enabled_modules": ["chat"]}))
        spawn.assert_not_called()
        _run(reflection.reflection_node({"messages": [], "enabled_modules": None}))
        spawn.assert_called_once()
        spawn.call_args.args[0].close()  # never ran — don't leak the coroutine


# ── Retrieval ────────────────────────────────────────────────────────────────

def test_load_memory_context_profile_rules_notes_order(vault):
    from app.memory import obsidian, store
    obsidian.save_fact("Profile", "Me", "Works as a product manager at Wix.")
    obsidian.update_rule("Answer in Hebrew.")
    obsidian.save_fact("People", "Dana", "Dana is my sister, lives in Haifa.")
    with patch("app.memory.embeddings.embed_query", return_value=[0.1] * 768), \
         patch("app.memory.embeddings.semantic_search",
               return_value=[("People/Dana.md", 0.81), ("Profile/Me.md", 0.9)]) as sem, \
         patch.object(store, "search_episodes", return_value=["(2026-10-01) Planned Dana's birthday"]):
        ctx = _run(store.load_memory_context("what about my sister?", recent="we talked about Dana"))

    assert ctx.index("About the user") < ctx.index("Rules") < ctx.index("Relevant notes") \
        < ctx.index("Related past conversations")
    assert "product manager" in ctx and "Haifa" in ctx and "Answer in Hebrew." in ctx
    assert ctx.count("product manager") == 1  # profile not duplicated as a "note" hit
    assert sem.call_args.kwargs["min_similarity"] > 0  # relevance threshold applied


def test_keyword_fallback_when_embeddings_unavailable(vault):
    from app.memory import obsidian, store
    obsidian.save_fact("Projects", "Kitchen", "Kitchen renovation contractor is Moshe.")
    with patch("app.memory.embeddings.embed_query", return_value=None):
        ctx = _run(store.load_memory_context("kitchen contractor"))
    assert "Moshe" in ctx


def test_memory_block_is_placed_before_tool_instructions():
    from app.graph.distiller import _build_system_prompt
    prompt = _build_system_prompt("### About the user\n- Lives in Tel Aviv", "web_x")
    assert prompt.index("Long-term memory") < prompt.index("You have tools for")


# ── Embeddings ───────────────────────────────────────────────────────────────

def test_embed_uses_new_model_and_reports_errors():
    from app.memory import embeddings
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"embedding": {"values": [0.0] * 768}}
    with patch.object(embeddings, "_api_key", return_value="k"), \
         patch.object(embeddings.httpx, "post", return_value=ok) as post:
        assert len(embeddings.embed_query("hello")) == 768
    body = post.call_args.kwargs["json"]
    assert "gemini-embedding-001" in post.call_args.args[0]
    assert body["outputDimensionality"] == 768 and body["taskType"] == "RETRIEVAL_QUERY"

    bad = MagicMock(status_code=404, text="model retired")
    with patch.object(embeddings, "_api_key", return_value="k"), \
         patch.object(embeddings.httpx, "post", return_value=bad):
        assert embeddings.embed_text("hello") is None
    st = embeddings.status()
    assert st["state"] == "error" and "404" in st["last_error"]


def test_model_change_resets_tenant_index():
    from app.memory import embeddings
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    with patch("app.memory.store.get_app_setting", return_value="text-embedding-004"), \
         patch("app.memory.store.set_app_setting") as setter, \
         patch.object(embeddings, "_get_conn", return_value=conn):
        assert embeddings.ensure_embedding_model_current() is True
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list)
    assert "DELETE FROM vault_embeddings" in sqls and "UPDATE episodes SET embedding = NULL" in sqls
    setter.assert_called_once_with("embedding_model", embeddings.EMBEDDING_MODEL)
    with patch("app.memory.store.get_app_setting", return_value=embeddings.EMBEDDING_MODEL):
        assert embeddings.ensure_embedding_model_current() is False


# ── Maintenance ──────────────────────────────────────────────────────────────

def test_consolidation_rewrites_summary_only_where_needed(vault):
    from app.memory import maintenance, obsidian
    obsidian.save_fact("People", "Single", "Only one entry here.")
    obsidian.save_fact("People", "Avi", "Avi works at Intel.")
    obsidian.save_fact("People", "Avi", "Avi moved to Google.")
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="- Works at Google (moved from Intel)"))
    with patch("app.llm.get_smart_llm", return_value=llm):
        done = _run(maintenance.consolidate_vault())
    assert done == 1
    assert obsidian.note_parts(vault / "People" / "Avi.md")["summary"] == "- Works at Google (moved from Intel)"
    assert obsidian.note_parts(vault / "People" / "Single.md")["summary"] == ""
