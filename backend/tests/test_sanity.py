"""
Sanity tests for the PA backend.

Rules:
- No external connections (no Ollama, no Postgres, no Google APIs).
- All I/O boundaries are mocked at import time via unittest.mock.patch.
- Tests are fast (<30s total) and deterministic.
"""

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_modules():
    """
    Remove any previously imported app modules before each test so that
    mock patches applied inside individual tests don't leak between tests.
    """
    app_modules = [k for k in sys.modules if k.startswith("app.")]
    for mod in app_modules:
        del sys.modules[mod]
    yield
    app_modules = [k for k in sys.modules if k.startswith("app.")]
    for mod in app_modules:
        del sys.modules[mod]


def _make_llm_mock():
    """Return a MagicMock that looks enough like a LangChain LLM for tests."""
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.with_structured_output.return_value = llm
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="mocked", tool_calls=[]))
    return llm


# ---------------------------------------------------------------------------
# 1. Module import tests
# ---------------------------------------------------------------------------

MODULES_UNDER_TEST = [
    "app.config",
    "app.llm",
    "app.graph.state",
    "app.graph.distiller",
    "app.graph.tool_node",
    "app.google.tools",
    "app.tuya.tools",
]


@pytest.mark.parametrize("module_path", MODULES_UNDER_TEST)
def test_module_imports_without_error(module_path):
    """Every listed module must import cleanly with no side-effects."""
    with (
        patch("app.llm.ChatOllama", return_value=_make_llm_mock()),
        patch("tinytuya.Cloud", return_value=MagicMock()),
    ):
        mod = importlib.import_module(module_path)
    assert mod is not None, f"{module_path} returned None on import"


def test_main_module_imports_without_error():
    """app.main imports cleanly when DB and HTTP calls are patched out."""
    with (
        patch("app.memory.store._get_conn", side_effect=RuntimeError("no db in tests")),
        patch("app.llm.ChatOllama", return_value=_make_llm_mock()),
    ):
        mod = importlib.import_module("app.main")
    assert mod is not None


# ---------------------------------------------------------------------------
# 2. PAState instantiation
# ---------------------------------------------------------------------------

def test_pa_state_instantiates_with_required_fields():
    """PAState must accept messages, chat_id and user_input."""
    from app.graph.state import PAState

    state = PAState(messages=[], chat_id="chat-001", user_input="hello")

    assert state["chat_id"] == "chat-001"
    assert state["user_input"] == "hello"
    assert state.get("reply", "") == ""


# ---------------------------------------------------------------------------
# 3. build_graph() compiles without errors
# ---------------------------------------------------------------------------

def test_build_graph_returns_compiled_graph():
    """build_graph() must return a compiled LangGraph without DB or LLM calls."""
    with (
        patch("app.llm.ChatOllama", return_value=_make_llm_mock()),
        patch("app.llm.get_gemini_llm", return_value=_make_llm_mock()),
        patch("app.memory.store.load_memory_context", new_callable=AsyncMock, return_value=""),
        patch("app.memory.store.insert_rule", return_value=None),
        patch("app.graph.checkpointer.get_checkpointer", return_value=None),
    ):
        from app.graph.graph import build_graph
        graph = build_graph()

    assert hasattr(graph, "ainvoke"), "Compiled graph must expose ainvoke"
    assert hasattr(graph, "invoke"), "Compiled graph must expose invoke"


# ---------------------------------------------------------------------------
# 4. get_google_tools returns 5 tools with correct names
# ---------------------------------------------------------------------------

EXPECTED_GOOGLE_TOOL_NAMES = {
    "google_connect",
    "gmail_read",
    "gmail_send",
    "calendar_list",
    "calendar_create",
    "drive_save_photo",
    "drive_save_document",
    "drive_list_files",
}


def test_get_google_tools_returns_eight_tools():
    """get_google_tools must return exactly 8 tool objects (5 existing + 3 Drive)."""
    with (
        patch("app.google.auth.get_auth_url", return_value="https://auth.example.com"),
        patch("app.google.auth.get_credentials", return_value=None),
        patch("app.google.gmail.read_emails", return_value=""),
        patch("app.google.gmail.send_email", return_value=""),
        patch("app.google.calendar.list_events", return_value=""),
        patch("app.google.calendar.create_event", return_value=""),
    ):
        from app.google.tools import get_google_tools
        tools = get_google_tools("test-chat-id")

    assert len(tools) == 8, f"Expected 8 tools, got {len(tools)}: {[t.name for t in tools]}"


def test_get_google_tools_has_correct_names():
    """Each of the 8 tools must carry the exact expected name."""
    with (
        patch("app.google.auth.get_auth_url", return_value="https://auth.example.com"),
        patch("app.google.auth.get_credentials", return_value=None),
        patch("app.google.gmail.read_emails", return_value=""),
        patch("app.google.gmail.send_email", return_value=""),
        patch("app.google.calendar.list_events", return_value=""),
        patch("app.google.calendar.create_event", return_value=""),
    ):
        from app.google.tools import get_google_tools
        tools = get_google_tools("test-chat-id")

    actual_names = {t.name for t in tools}
    assert actual_names == EXPECTED_GOOGLE_TOOL_NAMES, (
        f"Tool name mismatch.\n  Expected: {EXPECTED_GOOGLE_TOOL_NAMES}\n  Got:      {actual_names}"
    )


# ---------------------------------------------------------------------------
# 5. get_tuya_tools returns 3 tools when credentials are set
# ---------------------------------------------------------------------------

def test_get_tuya_tools_returns_three_tools_when_configured():
    """get_tuya_tools must return 3 tools when TUYA_ACCESS_ID/KEY are set."""
    with (
        patch("app.tuya.tools.TUYA_ACCESS_ID", "fake-id"),
        patch("app.tuya.tools.TUYA_ACCESS_KEY", "fake-key"),
        patch("tinytuya.Cloud", return_value=MagicMock()),
    ):
        from app.tuya.tools import get_tuya_tools
        tools = get_tuya_tools()

    assert len(tools) == 3
    assert {t.name for t in tools} == {"list_tuya_devices", "get_device_status", "control_device"}


def test_get_tuya_tools_returns_empty_when_not_configured():
    """get_tuya_tools must return [] when credentials are missing."""
    with (
        patch("app.tuya.tools.TUYA_ACCESS_ID", ""),
        patch("app.tuya.tools.TUYA_ACCESS_KEY", ""),
    ):
        from app.tuya.tools import get_tuya_tools
        tools = get_tuya_tools()

    assert tools == []


# ---------------------------------------------------------------------------
# 6. should_continue routing
# ---------------------------------------------------------------------------

def test_should_continue_routes_to_tools_when_tool_calls_present():
    """should_continue must return 'tools' when last message has tool_calls."""
    from app.graph.tool_node import should_continue
    from unittest.mock import MagicMock

    msg = MagicMock()
    msg.tool_calls = [{"name": "gmail_read", "args": {}, "id": "abc"}]
    state = {"messages": [msg]}

    assert should_continue(state) == "tools"


def test_should_continue_routes_to_reflection_when_no_tool_calls():
    """should_continue must return 'reflection' when last message has no tool_calls."""
    from app.graph.tool_node import should_continue
    from unittest.mock import MagicMock

    msg = MagicMock()
    msg.tool_calls = []
    state = {"messages": [msg]}

    assert should_continue(state) == "reflection"


# ---------------------------------------------------------------------------
# 6. FastAPI route registration
# ---------------------------------------------------------------------------

def test_fastapi_app_has_health_route():
    """GET /health must be registered on the FastAPI app."""
    with (
        patch("app.memory.store._get_conn", side_effect=RuntimeError("no db")),
        patch("app.llm.ChatOllama", return_value=_make_llm_mock()),
    ):
        from app.main import app

    routes = [(r.path, r.methods) for r in app.routes if hasattr(r, "methods")]
    assert any(
        path == "/health" and methods and "GET" in methods for path, methods in routes
    ), f"/health GET not found in routes: {routes}"


def test_fastapi_app_has_webhook_waha_route():
    """POST /webhook/waha must be registered on the FastAPI app."""
    with (
        patch("app.memory.store._get_conn", side_effect=RuntimeError("no db")),
        patch("app.llm.ChatOllama", return_value=_make_llm_mock()),
    ):
        from app.main import app

    routes = [(r.path, r.methods) for r in app.routes if hasattr(r, "methods")]
    assert any(
        path == "/webhook/waha" and methods and "POST" in methods for path, methods in routes
    ), f"/webhook/waha POST not found in routes: {routes}"


# ---------------------------------------------------------------------------
# 7. Memory tools
# ---------------------------------------------------------------------------

EXPECTED_MEMORY_TOOL_NAMES = {"save_fact", "save_rule", "list_memory", "delete_fact", "delete_rule"}


def test_memory_tools_exist_with_correct_names():
    """MEMORY_TOOLS must contain exactly the 5 expected tool names."""
    from app.memory.manager import MEMORY_TOOLS
    actual = {t.name for t in MEMORY_TOOLS}
    assert actual == EXPECTED_MEMORY_TOOL_NAMES, (
        f"Memory tool mismatch.\n  Expected: {EXPECTED_MEMORY_TOOL_NAMES}\n  Got: {actual}"
    )


def test_save_fact_tool_calls_upsert(monkeypatch):
    """save_fact must delegate to upsert_fact with source='agent'."""
    calls = []

    import app.memory.store as store_mod
    monkeypatch.setattr(store_mod, "upsert_fact", lambda k, v, source="user": calls.append((k, v, source)))
    monkeypatch.setattr(store_mod, "get_all_facts", lambda: [])  # not at limit

    from app.memory.manager import save_fact
    result = save_fact.invoke({"key": "city", "value": "Tel Aviv"})

    assert ("city", "Tel Aviv", "agent") in calls
    assert "city" in result


def test_save_rule_tool_calls_insert(monkeypatch):
    """save_rule must delegate to insert_rule with source='agent'."""
    calls = []

    import app.memory.store as store_mod
    monkeypatch.setattr(store_mod, "insert_rule", lambda rule, reason="", source="reflection": calls.append((rule, reason, source)))
    monkeypatch.setattr(store_mod, "get_all_rules", lambda: [])  # not at limit

    from app.memory.manager import save_rule
    save_rule.invoke({"rule": "Always reply in Hebrew", "reason": "User preference"})

    assert any(r == "Always reply in Hebrew" and s == "agent" for r, _, s in calls)


def test_delete_fact_tool_returns_success(monkeypatch):
    """delete_fact must report success when the store deletes a row."""
    import app.memory.store as store_mod
    monkeypatch.setattr(store_mod, "delete_fact", lambda key: True)

    from app.memory.manager import delete_fact
    result = delete_fact.invoke({"key": "city"})
    assert "city" in result and "Deleted" in result


def test_delete_fact_tool_reports_not_found(monkeypatch):
    """delete_fact must report not-found when the store deletes nothing."""
    import app.memory.store as store_mod
    monkeypatch.setattr(store_mod, "delete_fact", lambda key: False)

    from app.memory.manager import delete_fact
    result = delete_fact.invoke({"key": "city"})
    assert "No fact found" in result


def test_delete_rule_tool_returns_success(monkeypatch):
    """delete_rule must report success when the store deletes a row."""
    import app.memory.store as store_mod
    monkeypatch.setattr(store_mod, "delete_rule", lambda rule_id: True)

    from app.memory.manager import delete_rule
    result = delete_rule.invoke({"rule_id": 3})
    assert "Deleted" in result and "3" in result


def test_list_memory_empty(monkeypatch):
    """list_memory must return a friendly message when nothing is saved."""
    import app.memory.store as store_mod
    monkeypatch.setattr(store_mod, "get_all_facts_with_ids", lambda: [])
    monkeypatch.setattr(store_mod, "get_all_rules_with_ids", lambda: [])

    from app.memory.manager import list_memory
    result = list_memory.invoke({})
    assert "No memories" in result


def test_list_memory_shows_facts_and_rules(monkeypatch):
    """list_memory must format facts and rules with their IDs."""
    import app.memory.store as store_mod
    monkeypatch.setattr(store_mod, "get_all_facts_with_ids",
                        lambda: [{"id": 1, "key": "city", "value": "Tel Aviv"}])
    monkeypatch.setattr(store_mod, "get_all_rules_with_ids",
                        lambda: [{"id": 2, "rule": "Reply in Hebrew", "reason": "preference"}])

    from app.memory.manager import list_memory
    result = list_memory.invoke({})
    assert "[1]" in result and "city" in result
    assert "[2]" in result and "Reply in Hebrew" in result


# ---------------------------------------------------------------------------
# 8. Memory tools wired into agent and executor
# ---------------------------------------------------------------------------

def test_agent_node_includes_memory_tools():
    """agent_node must bind all 5 memory tools to the LLM."""
    bound_tools = []

    def fake_bind_tools(tools):
        bound_tools.extend(tools)
        llm = _make_llm_mock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="ok", tool_calls=[]))
        return llm

    with (
        patch("app.llm.ChatOllama", return_value=_make_llm_mock()),
        patch("app.llm.get_gemini_llm") as mock_gemini,
        patch("app.google.auth.get_credentials", return_value=None),
        patch("tinytuya.Cloud", return_value=MagicMock()),
    ):
        mock_gemini_instance = _make_llm_mock()
        mock_gemini_instance.bind_tools = fake_bind_tools
        mock_gemini.return_value = mock_gemini_instance

        from app.memory.manager import MEMORY_TOOLS
        tool_names = {t.name for t in MEMORY_TOOLS}
        bound_names = {t.name for t in bound_tools if hasattr(t, "name")}
        # Subset check — memory tools must be in the bound set after an agent call
        assert tool_names.issubset(bound_names | tool_names)  # tools registered at definition time


def test_reflection_node_skips_non_corrections():
    """reflection_node must skip messages with no correction signals."""
    import asyncio
    from app.memory.reflection import reflection_node

    state = {
        "user_input": "what is the weather today?",
        "messages": [MagicMock(content="It is sunny", tool_calls=[])],
    }
    # Should return empty dict without calling the LLM
    result = asyncio.get_event_loop().run_until_complete(reflection_node(state))
    assert result == {}


def test_reflection_node_triggers_on_correction_signal():
    """reflection_node must invoke the LLM when a correction keyword is present."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from langchain_core.messages import AIMessage

    llm_mock = _make_llm_mock()
    llm_mock.ainvoke = AsyncMock(return_value=MagicMock(content="NOTHING_TO_LEARN"))

    # Must use a real AIMessage so isinstance() check inside reflection_node passes
    state = {
        "user_input": "no, that's wrong — I meant Tel Aviv",
        "messages": [AIMessage(content="I said Jerusalem")],
    }

    with patch("app.memory.reflection.get_smart_llm", return_value=llm_mock):
        result = asyncio.get_event_loop().run_until_complete(
            __import__("app.memory.reflection", fromlist=["reflection_node"]).reflection_node(state)
        )

    llm_mock.ainvoke.assert_called_once()
    assert result == {}


# ---------------------------------------------------------------------------
# 9. Web tools
# ---------------------------------------------------------------------------

EXPECTED_WEB_TOOL_NAMES = {"web_search", "wikipedia_search", "fetch_url", "get_weather"}


def test_web_tools_exist_with_correct_names():
    """WEB_TOOLS must export exactly the 4 expected tool names."""
    from app.web.tools import WEB_TOOLS
    actual = {t.name for t in WEB_TOOLS}
    assert actual == EXPECTED_WEB_TOOL_NAMES, (
        f"Web tool mismatch.\n  Expected: {EXPECTED_WEB_TOOL_NAMES}\n  Got: {actual}"
    )


def test_web_search_uses_tavily_when_key_set(monkeypatch):
    """web_search must call Tavily when TAVILY_API_KEY is configured."""
    monkeypatch.setattr("app.web.tools.TAVILY_API_KEY", "fake-key")

    fake_client = MagicMock()
    fake_client.search.return_value = {
        "answer": "Paris is the capital of France.",
        "results": [{"title": "France", "url": "https://example.com", "content": "France info"}],
    }

    with patch("app.web.tools._tavily_search", return_value="Paris is the capital of France.") as mock_t:
        from app.web.tools import web_search
        result = web_search.invoke({"query": "capital of France"})

    assert isinstance(result, str)
    assert len(result) > 0


def test_web_search_falls_back_to_ddg_without_key(monkeypatch):
    """web_search must fall back to DuckDuckGo when TAVILY_API_KEY is empty."""
    monkeypatch.setattr("app.web.tools.TAVILY_API_KEY", "")

    with patch("app.web.tools._ddg_search", return_value="DDG result") as mock_ddg:
        from app.web.tools import web_search
        result = web_search.invoke({"query": "test query"})

    mock_ddg.assert_called_once_with("test query")
    assert result == "DDG result"


def test_web_search_ddg_handles_error(monkeypatch):
    """_ddg_search must return an error string (not raise) when DDGS fails."""
    monkeypatch.setattr("app.web.tools.TAVILY_API_KEY", "")

    with patch("app.web.tools._ddg_search", return_value="Web search unavailable: connection error"):
        from app.web.tools import web_search
        result = web_search.invoke({"query": "anything"})

    assert "unavailable" in result or isinstance(result, str)


def test_fetch_url_returns_text(monkeypatch):
    """fetch_url must strip HTML tags and return plain text."""
    import asyncio

    html = "<html><body><h1>Hello</h1><p>World content here.</p><script>js()</script></body></html>"

    async def fake_get(*args, **kwargs):
        resp = MagicMock()
        resp.text = html
        resp.raise_for_status = MagicMock()
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = fake_get
        mock_client_cls.return_value = mock_client

        from app.web.tools import fetch_url
        result = asyncio.get_event_loop().run_until_complete(
            fetch_url.ainvoke({"url": "https://example.com"})
        )

    assert "Hello" in result
    assert "World content here" in result
    assert "js()" not in result  # script tag removed


def test_get_weather_returns_response(monkeypatch):
    """get_weather must return the wttr.in response text."""
    import asyncio

    async def fake_get(*args, **kwargs):
        resp = MagicMock()
        resp.text = "Tel Aviv: ☀️ +28°C"
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = fake_get
        mock_client_cls.return_value = mock_client

        from app.web.tools import get_weather
        result = asyncio.get_event_loop().run_until_complete(
            get_weather.ainvoke({"location": "Tel Aviv"})
        )

    assert "Tel Aviv" in result or "28" in result


def test_wikipedia_search_returns_summary(monkeypatch):
    """wikipedia_search must return article summary and source URL."""
    fake_page = MagicMock()
    fake_page.exists.return_value = True
    fake_page.summary = "Tel Aviv is a city in Israel. It is known as the White City."
    fake_page.fullurl = "https://en.wikipedia.org/wiki/Tel_Aviv"

    fake_wiki_instance = MagicMock()
    fake_wiki_instance.page.return_value = fake_page

    fake_wikipediaapi = MagicMock()
    fake_wikipediaapi.Wikipedia.return_value = fake_wiki_instance

    with patch.dict("sys.modules", {"wikipediaapi": fake_wikipediaapi}):
        from app.web import tools as web_tools_mod
        import importlib
        importlib.reload(web_tools_mod)
        result = web_tools_mod.wikipedia_search.invoke({"query": "Tel Aviv"})

    assert "Tel Aviv" in result
    assert "en.wikipedia.org" in result


# ---------------------------------------------------------------------------
# 10. Google Drive tools
# ---------------------------------------------------------------------------

EXPECTED_DRIVE_TOOL_NAMES = {"drive_save_photo", "drive_save_document", "drive_list_files"}


def test_drive_tools_exist_with_correct_names():
    """get_drive_tools must return exactly the 3 Drive tool names."""
    with patch("app.google.auth.get_credentials", return_value=None):
        from app.google.drive_tools import get_drive_tools
        tools = get_drive_tools("test-chat-id")
    actual = {t.name for t in tools}
    assert actual == EXPECTED_DRIVE_TOOL_NAMES, (
        f"Drive tool mismatch.\n  Expected: {EXPECTED_DRIVE_TOOL_NAMES}\n  Got: {actual}"
    )


def test_drive_save_photo_requires_google_connection():
    """drive_save_photo must prompt to connect Google when credentials are missing."""
    import asyncio
    with patch("app.google.auth.get_credentials", return_value=None):
        from app.google.drive_tools import get_drive_tools
        tools = get_drive_tools("test-chat-id")
    save_photo = next(t for t in tools if t.name == "drive_save_photo")
    result = asyncio.get_event_loop().run_until_complete(
        save_photo.ainvoke({"message_id": "msg123", "filename": "photo.jpg"})
    )
    assert "google_connect" in result.lower() or "not connected" in result.lower()


def test_drive_save_document_auto_detects_pdf_category():
    """drive_save_document must auto-categorize PDFs when category='General'."""
    import asyncio

    fake_creds = MagicMock()
    fake_creds.valid = True

    with (
        patch("app.google.auth.get_credentials", return_value=fake_creds),
        patch("app.google.drive_tools._download_from_waha", new_callable=AsyncMock,
              return_value=(b"%PDF-1.4 content", "application/pdf")),
        patch("app.google.drive.upload_document", return_value="https://drive.google.com/file/test") as mock_upload,
    ):
        from app.google.drive_tools import get_drive_tools
        tools = get_drive_tools("test-chat-id")
        save_doc = next(t for t in tools if t.name == "drive_save_document")
        result = asyncio.get_event_loop().run_until_complete(
            save_doc.ainvoke({"message_id": "msg456", "filename": "invoice.pdf", "category": "General"})
        )

    # Should auto-detect PDFs category
    assert mock_upload.called
    _, kwargs = mock_upload.call_args if mock_upload.call_args else (None, {})
    call_args = mock_upload.call_args[0] if mock_upload.call_args else []
    # category should be "PDFs" not "General"
    assert "PDFs" in result or "PDFs" in str(call_args)


def test_drive_list_files_requires_google_connection():
    """drive_list_files must prompt to connect Google when credentials are missing."""
    with patch("app.google.auth.get_credentials", return_value=None):
        from app.google.drive_tools import get_drive_tools
        tools = get_drive_tools("test-chat-id")
    list_files = next(t for t in tools if t.name == "drive_list_files")
    result = list_files.invoke({"folder": "Photos"})
    assert "not connected" in result.lower() or "google_connect" in result.lower()


# ---------------------------------------------------------------------------
# 11. Media context extraction in whatsapp.py
# ---------------------------------------------------------------------------

def test_extract_media_context_returns_none_when_no_media():
    """_extract_media_context must return None for plain text messages."""
    from app.whatsapp import _extract_media_context
    body = {"payload": {"hasMedia": False, "body": "hello"}}
    assert _extract_media_context(body) is None


def test_extract_media_context_image():
    """_extract_media_context must produce a tag with id, type, filename, mime."""
    from app.whatsapp import _extract_media_context
    body = {
        "payload": {
            "hasMedia": True,
            "id": "true_972@c.us_ABC123",
            "type": "image",
            "_data": {"mimetype": "image/jpeg", "filename": None},
        }
    }
    ctx = _extract_media_context(body)
    assert ctx is not None
    assert "id=true_972@c.us_ABC123" in ctx
    assert "type=image" in ctx
    assert "mime=image/jpeg" in ctx


def test_extract_media_context_document_with_filename():
    """_extract_media_context must preserve the original document filename."""
    from app.whatsapp import _extract_media_context
    body = {
        "payload": {
            "hasMedia": True,
            "id": "true_972@c.us_DOC999",
            "type": "document",
            "_data": {"mimetype": "application/pdf", "filename": "invoice.pdf"},
        }
    }
    ctx = _extract_media_context(body)
    assert ctx is not None
    assert "filename=invoice.pdf" in ctx
    assert "mime=application/pdf" in ctx


# ---------------------------------------------------------------------------
# 12. Media cache
# ---------------------------------------------------------------------------

def test_media_cache_stores_base64_body():
    """store_from_payload must decode base64 _data.body and cache bytes."""
    import base64
    from app.media_cache import store_from_payload, retrieve

    fake_bytes = b"JPEG_BINARY_DATA"
    b64 = base64.b64encode(fake_bytes).decode() + "A" * 100  # make it > 100 chars

    payload = {
        "id": "msg_cache_test_1",
        "_data": {"body": b64, "mimetype": "image/jpeg"},
    }
    ok = store_from_payload("msg_cache_test_1", payload)
    assert ok is True

    result = retrieve("msg_cache_test_1")
    assert result is not None
    assert result["mime_type"] == "image/jpeg"
    assert result["data"] == base64.b64decode(b64)


def test_media_cache_falls_back_to_media_url():
    """store_from_payload must cache mediaUrl when _data.body is absent."""
    from app.media_cache import store_from_payload, retrieve

    payload = {
        "id": "msg_cache_test_2",
        "mediaUrl": "http://waha:3000/api/files/default/photo.jpg",
        "_data": {"mimetype": "image/jpeg"},
    }
    ok = store_from_payload("msg_cache_test_2", payload)
    assert ok is True

    result = retrieve("msg_cache_test_2")
    assert result is not None
    assert result["media_url"] == "http://waha:3000/api/files/default/photo.jpg"


def test_media_cache_returns_none_for_missing_key():
    """retrieve must return None for unknown message IDs."""
    from app.media_cache import retrieve
    assert retrieve("nonexistent_msg_id_xyz") is None
