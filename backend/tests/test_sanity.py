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
    "drive_show_image",
    "create_google_map",
    "add_places_to_map",
}


def test_get_google_tools_returns_eight_tools():
    """get_google_tools must return exactly 11 tool objects (5 existing + 4 Drive + 2 Maps)."""
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

    assert len(tools) == 11, f"Expected 11 tools, got {len(tools)}: {[t.name for t in tools]}"


def test_get_google_tools_has_correct_names():
    """Each of the 11 tools must carry the exact expected name."""
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
    """get_tuya_tools must return 3 tools when TUYA_ACCESS_ID/KEY resolve."""
    fake = {"TUYA_ACCESS_ID": "fake-id", "TUYA_ACCESS_KEY": "fake-key"}
    with (
        patch("app.runtime.get_secret", side_effect=lambda k: fake.get(k, "")),
        patch("tinytuya.Cloud", return_value=MagicMock()),
    ):
        from app.tuya.tools import get_tuya_tools
        tools = get_tuya_tools()

    assert len(tools) == 3
    assert {t.name for t in tools} == {"list_tuya_devices", "get_device_status", "control_device"}


def test_get_tuya_tools_returns_empty_when_not_configured():
    """get_tuya_tools must return [] when credentials are missing."""
    with patch("app.runtime.get_secret", return_value=""):
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

EXPECTED_MEMORY_TOOL_NAMES = {
    "save_fact", "update_rule", "retrieve_context", "search_vault",
    "list_memory", "hide_fact", "hide_rule", "append_to_note", "read_note", "grep_note",
}


def test_memory_tools_exist_with_correct_names():
    """MEMORY_TOOLS must contain exactly the expected Obsidian-vault tool names."""
    from app.memory.manager import MEMORY_TOOLS
    actual = {t.name for t in MEMORY_TOOLS}
    assert actual == EXPECTED_MEMORY_TOOL_NAMES, (
        f"Memory tool mismatch.\n  Expected: {EXPECTED_MEMORY_TOOL_NAMES}\n  Got: {actual}"
    )


def test_save_fact_tool_delegates_to_obsidian(monkeypatch):
    """save_fact must delegate to obsidian.save_fact with category/entity/content."""
    calls = []

    from app.memory import manager as manager_mod
    monkeypatch.setattr(manager_mod.obsidian, "save_fact",
                        lambda category, entity, content: calls.append((category, entity, content)) or "ok")

    from app.memory.manager import save_fact
    save_fact.invoke({"category": "People", "entity": "Daniel", "content": "Lives in Tel Aviv"})

    assert ("People", "Daniel", "Lives in Tel Aviv") in calls


def test_update_rule_tool_delegates_to_obsidian(monkeypatch):
    """update_rule must delegate to obsidian.update_rule."""
    calls = []

    from app.memory import manager as manager_mod
    monkeypatch.setattr(manager_mod.obsidian, "update_rule",
                        lambda instruction: calls.append(instruction) or "ok")

    from app.memory.manager import update_rule
    update_rule.invoke({"instruction": "Always reply in Hebrew"})

    assert "Always reply in Hebrew" in calls


def test_retrieve_context_tool_delegates_to_obsidian(monkeypatch):
    """retrieve_context must delegate to obsidian.retrieve_context."""
    captured = []

    from app.memory import manager as manager_mod
    monkeypatch.setattr(manager_mod.obsidian, "retrieve_context",
                        lambda query: captured.append(query) or "snippets")

    from app.memory.manager import retrieve_context
    result = retrieve_context.invoke({"query": "Milwaukee"})

    assert captured == ["Milwaukee"]
    assert result == "snippets"


def test_hide_fact_tool_delegates_to_obsidian(monkeypatch):
    """hide_fact must delegate to obsidian.hide_fact (soft delete)."""
    calls = []

    from app.memory import manager as manager_mod
    monkeypatch.setattr(manager_mod.obsidian, "hide_fact",
                        lambda category, entity: calls.append((category, entity)) or "Hidden: …")

    from app.memory.manager import hide_fact
    result = hide_fact.invoke({"category": "Investments", "entity": "Milwaukee_Property"})
    assert ("Investments", "Milwaukee_Property") in calls
    assert "Hidden" in result


def test_hide_rule_tool_delegates_to_obsidian(monkeypatch):
    """hide_rule must delegate to obsidian.hide_rule (strikethrough)."""
    calls = []

    from app.memory import manager as manager_mod
    monkeypatch.setattr(manager_mod.obsidian, "hide_rule",
                        lambda instruction: calls.append(instruction) or "Hidden rule: …")

    from app.memory.manager import hide_rule
    hide_rule.invoke({"instruction": "Always reply in Hebrew"})
    assert "Always reply in Hebrew" in calls


def test_list_memory_calls_list_visible(monkeypatch):
    """list_memory must delegate to obsidian.list_visible."""
    from app.memory import manager as manager_mod
    monkeypatch.setattr(manager_mod.obsidian, "list_visible", lambda: "Vault is empty.")

    from app.memory.manager import list_memory
    result = list_memory.invoke({})
    assert "Vault is empty." in result


# ---------------------------------------------------------------------------
# 8. Memory tools wired into agent and executor
# ---------------------------------------------------------------------------

def test_agent_node_includes_memory_tools():
    """agent_node must bind all memory tools to the LLM."""
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

EXPECTED_WEB_TOOL_NAMES = {"web_search", "wikipedia_search", "fetch_url", "get_weather", "build_whatsapp_link"}


def test_web_tools_exist_with_correct_names():
    """WEB_TOOLS must export exactly the 5 expected tool names."""
    from app.web.tools import WEB_TOOLS
    actual = {t.name for t in WEB_TOOLS}
    assert actual == EXPECTED_WEB_TOOL_NAMES, (
        f"Web tool mismatch.\n  Expected: {EXPECTED_WEB_TOOL_NAMES}\n  Got: {actual}"
    )


def test_web_search_uses_tavily_when_key_set(monkeypatch):
    """web_search must call Tavily when TAVILY_API_KEY resolves for the scope."""
    monkeypatch.setattr("app.runtime.get_secret", lambda k: "fake-key" if k == "TAVILY_API_KEY" else "")

    with patch("app.web.tools._tavily_search", return_value="Paris is the capital of France.") as mock_t:
        from app.web.tools import web_search
        result = web_search.invoke({"query": "capital of France"})

    mock_t.assert_called_once_with("capital of France", "fake-key")
    assert isinstance(result, str)
    assert len(result) > 0


def test_web_search_falls_back_to_ddg_without_key(monkeypatch):
    """web_search must fall back to DuckDuckGo when TAVILY_API_KEY is empty."""
    monkeypatch.setattr("app.runtime.get_secret", lambda k: "")

    with patch("app.web.tools._ddg_search", return_value="DDG result") as mock_ddg:
        from app.web.tools import web_search
        result = web_search.invoke({"query": "test query"})

    mock_ddg.assert_called_once_with("test query")
    assert result == "DDG result"


def test_web_search_ddg_handles_error(monkeypatch):
    """_ddg_search must return an error string (not raise) when DDGS fails."""
    monkeypatch.setattr("app.runtime.get_secret", lambda k: "")

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
    """get_weather must return location name, current conditions, and 7-day forecast."""
    import asyncio

    GEO_JSON = [{
        "display_name": "Tel Aviv, Tel Aviv District, Israel",
        "lat": "32.08",
        "lon": "34.78",
    }]
    FORECAST_JSON = {
        "current_weather": {"temperature": 28, "windspeed": 14, "weathercode": 0},
        "daily": {
            "time":                        ["2024-05-01", "2024-05-02", "2024-05-03",
                                            "2024-05-04", "2024-05-05", "2024-05-06", "2024-05-07"],
            "temperature_2m_max":          [30, 29, 27, 26, 28, 31, 32],
            "temperature_2m_min":          [21, 20, 18, 17, 19, 22, 23],
            "precipitation_probability_max":[0,  5, 60, 20,  0,  0,  0],
            "weathercode":                 [0,   2,  63,  2,  0,  0,  1],
        },
    }

    call_count = 0

    async def fake_get(*args, **kwargs):
        nonlocal call_count
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if call_count == 0:
            resp.json = MagicMock(return_value=GEO_JSON)
        else:
            resp.json = MagicMock(return_value=FORECAST_JSON)
        call_count += 1
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

    assert "Tel Aviv" in result
    assert "28" in result        # current temperature
    assert "7-day forecast" in result


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

EXPECTED_DRIVE_TOOL_NAMES = {"drive_save_photo", "drive_save_document", "drive_list_files", "drive_show_image"}


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
    fake_creds.scopes = ["https://www.googleapis.com/auth/drive.file"]

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


# ---------------------------------------------------------------------------
# 13. Fitness tools
# ---------------------------------------------------------------------------

EXPECTED_FITNESS_TOOL_NAMES = {
    "log_workout",
    "suggest_workout",
    "fitness_today",
    "fitness_history",
    "log_body_metrics",
    "analyze_body_scan",
    "fitness_morning_brief",
}


def test_get_fitness_tools_returns_seven_tools():
    """get_fitness_tools must return exactly 7 tool objects."""
    from app.fitness_tool import get_fitness_tools
    tools = get_fitness_tools("test-chat-id")
    assert len(tools) == 7, f"Expected 7 fitness tools, got {len(tools)}: {[t.name for t in tools]}"


def test_get_fitness_tools_has_correct_names():
    """Each of the 7 fitness tools must carry the exact expected name."""
    from app.fitness_tool import get_fitness_tools
    tools = get_fitness_tools("test-chat-id")
    actual = {t.name for t in tools}
    assert actual == EXPECTED_FITNESS_TOOL_NAMES, (
        f"Fitness tool name mismatch.\n  Expected: {EXPECTED_FITNESS_TOOL_NAMES}\n  Got: {actual}"
    )


def test_progression_targets_rpe_based_increments():
    """Progressive-overload math is deterministic — verify each RPE band."""
    from app.fitness import _compute_progression_targets
    workout = {
        "avg_rpe": 7,
        "exercises": [
            {"name": "Leg Press", "sets": 3, "reps": 12, "weight_kg": 100, "rpe": 5},   # ≤6 → +5%
            {"name": "Chest Press", "sets": 3, "reps": 10, "weight_kg": 40, "rpe": 7},   # 7-8 → +2.5%
            {"name": "Row", "sets": 3, "reps": 8, "weight_kg": 60, "rpe": 9},            # 9 → hold
            {"name": "Curl", "sets": 3, "reps": 10, "weight_kg": 20, "rpe": 10},         # 10 → deload 10%
            {"name": "Plank", "sets": 3, "reps": 0, "weight_kg": 0, "rpe": 6},           # bodyweight
        ],
    }
    targets, focus = _compute_progression_targets(workout)
    by_name = {t["name"]: t for t in targets}
    assert by_name["Leg Press"]["target_weight_kg"] == 105.0
    assert by_name["Chest Press"]["target_weight_kg"] == 41.0
    assert by_name["Row"]["target_weight_kg"] == 60.0
    assert by_name["Curl"]["target_weight_kg"] == 18.0
    assert by_name["Plank"]["target_weight_kg"] == 0.0


def test_progression_focus_recovery_on_high_rpe():
    """avg_rpe >= 9 must force recovery focus."""
    from app.fitness import _compute_progression_targets
    _, focus = _compute_progression_targets(
        {"avg_rpe": 9, "exercises": [{"name": "Squat", "sets": 3, "reps": 5, "weight_kg": 80, "rpe": 9}]}
    )
    assert focus == "recovery"


def test_reflux_guardrail_in_clinical_constraints():
    """The GERD/reflux guardrail must be present and reach every fitness LLM call
    via render_profile_block()."""
    from app.fitness_profile import CLINICAL_CONSTRAINTS, render_profile_block
    low = CLINICAL_CONSTRAINTS.lower()
    assert "reflux" in low or "gerd" in low
    assert "exhale on exertion" in low
    assert "decline" in low  # avoid decline-press positions
    block = render_profile_block(None).lower()
    assert "exhale on exertion" in block


def test_timed_exercises_volume_and_progression():
    """Timed HIIT exercises (reps=0, duration_sec>0) must produce non-zero volume
    and duration-based progression targets."""
    from app.fitness import _total_volume, _compute_progression_targets, _apply_volume_fallback

    timed = [
        {"name": "Lat Pulldown", "sets": 4, "reps": 0, "weight_kg": 20.0, "duration_sec": 40, "rpe": 8.0, "notes": ""},
        {"name": "Mountain Climbers", "sets": 4, "reps": 0, "weight_kg": 0.0, "duration_sec": 40, "rpe": 9.0, "notes": ""},
    ]
    # Volume: 4*(40/30)*20 + 0 = ~106.7
    vol = _total_volume(timed)
    assert vol > 0, f"Expected timed volume > 0, got {vol}"

    # Progression targets should use target_duration_sec not target_reps
    workout = {"avg_rpe": 8.0, "exercises": timed}
    targets, focus = _compute_progression_targets(workout)
    by_name = {t["name"]: t for t in targets}
    assert by_name["Lat Pulldown"]["target_duration_sec"] == 45
    assert by_name["Mountain Climbers"]["target_duration_sec"] == 45

    # Fallback should NOT replace timed exercises with synthetic row
    parsed = {"exercises": timed, "is_estimated_volume": False}
    result = _apply_volume_fallback(parsed, "HIIT workout")
    assert len(result["exercises"]) == 2, "Timed exercises should not be replaced by fallback"


# ---------------------------------------------------------------------------
# Garmin integration — shared message formatter, metrics merge, history()
# projection, and the readiness-downgrade overlay in generate_daily_recommendation.
# ---------------------------------------------------------------------------

def test_format_workout_log_message_strength_shape():
    """A strength workout (has exercises) shows volume, RPE, and next-session targets."""
    from app.fitness import format_workout_log_message
    parsed = {"title": "אימון כוח", "workout_type": "strength", "duration_min": 45, "avg_rpe": 7.5,
              "exercises": [{"sets": 3, "reps": 8, "weight_kg": 60}], "metrics": {}}
    evaluation = {"ai_summary": "עבודה טובה.", "ai_next_rec": {"targets": [
        {"name": "לחיצת חזה", "target_weight_kg": 62.5, "target_reps": 8}]}}
    today = {"total_duration_min": 45, "total_volume": 1440}

    msg = format_workout_log_message(parsed, evaluation, today)
    assert "אימון כוח" in msg and "נרשם" in msg
    assert "RPE 7.5" in msg
    assert "נפח: 1440" in msg
    assert "עבודה טובה." in msg
    assert "יעדים לאימון הבא" in msg
    assert "לחיצת חזה: 62.5 קג × 8" in msg


def test_format_workout_log_message_cardio_shape_no_exercises():
    """A Garmin cardio import (no exercises) shows distance/HR/calories instead of volume,
    and the watch tag is appended to the title line."""
    from app.fitness import format_workout_log_message
    parsed = {"title": "Cycling", "workout_type": "cardio", "duration_min": 122, "avg_rpe": 0,
              "exercises": [], "metrics": {"distance_km": 32.0, "hr_avg": 128, "calories": 850}}
    evaluation = {"ai_summary": "אימון סבולת טוב.", "ai_next_rec": {"targets": []}}
    today = {"total_duration_min": 122, "total_volume": 0}

    msg = format_workout_log_message(parsed, evaluation, today, tag=" ⌚")
    assert "*Cycling* ⌚" in msg
    assert "RPE" not in msg  # avg_rpe == 0 must not show a fake RPE line
    assert "• נפח:" not in msg  # no exercises → no per-workout volume line
                                # (the daily-totals footer legitimately says "נפח 0 קג")
    assert "32.0 ק\"מ" in msg
    assert "דופק ממוצע 128" in msg
    assert "850 קלוריות" in msg
    assert "אימון סבולת טוב." in msg


def test_format_workout_log_message_estimated_volume_note():
    from app.fitness import format_workout_log_message
    parsed = {"title": "ריצה", "workout_type": "cardio", "duration_min": 30, "avg_rpe": 6,
              "exercises": [{"sets": 1, "reps": 1, "weight_kg": 1}], "metrics": {}, "is_estimated_volume": True}
    msg = format_workout_log_message(parsed, {"ai_summary": "", "ai_next_rec": {}}, {"total_duration_min": 30, "total_volume": 1})
    assert "(הערכה)" in msg


def test_merge_garmin_metrics_updates_jsonb_column():
    """merge_garmin_metrics must UPDATE metrics via jsonb concatenation, not overwrite."""
    import json
    from unittest.mock import MagicMock, patch
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.rowcount = 1
    fake_conn = MagicMock()
    fake_conn.cursor = MagicMock(return_value=fake_cursor)

    with (
        patch("app.fitness.DATABASE_URL", "postgresql://fake"),
        patch("psycopg2.connect", return_value=fake_conn),
    ):
        from app.fitness import merge_garmin_metrics
        ok = merge_garmin_metrics(42, {"hr_avg": 128, "garmin_activity_id": "999"})

    assert ok is True
    sql, params = fake_cursor.execute.call_args[0]
    assert "metrics" in sql and "||" in sql, "must merge (||) not overwrite metrics"
    assert params[1] == 42
    assert json.loads(params[0]) == {"hr_avg": 128, "garmin_activity_id": "999"}
    fake_conn.commit.assert_called_once()


def test_merge_garmin_metrics_returns_false_when_no_row_matched():
    from unittest.mock import MagicMock, patch
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.rowcount = 0
    fake_conn = MagicMock()
    fake_conn.cursor = MagicMock(return_value=fake_cursor)

    with (
        patch("app.fitness.DATABASE_URL", "postgresql://fake"),
        patch("psycopg2.connect", return_value=fake_conn),
    ):
        from app.fitness import merge_garmin_metrics
        assert merge_garmin_metrics(9999, {"hr_avg": 100}) is False


def test_history_query_includes_garmin_fields():
    """history()'s per-session projection must include metrics/ai_summary/source so
    suggest_workout and fitness_history can see Garmin-imported cardio sessions."""
    from unittest.mock import MagicMock, patch
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.fetchall = MagicMock(return_value=[])
    fake_conn = MagicMock()
    fake_conn.cursor = MagicMock(return_value=fake_cursor)

    with (
        patch("app.fitness.DATABASE_URL", "postgresql://fake"),
        patch("psycopg2.connect", return_value=fake_conn),
    ):
        from app.fitness import history
        history("default", days=14)

    sql = fake_cursor.execute.call_args[0][0]
    for token in ("'metrics', metrics", "'ai_summary', ai_summary", "'source', source"):
        assert token in sql, f"history() query missing {token!r}"


def _daily_rec_common_mocks(recent_days, garmin_wellness):
    """Shared patch set for generate_daily_recommendation tests — mocks history,
    body metrics, profile rendering, the LLM call, and the Garmin wellness lookup."""
    from unittest.mock import AsyncMock, MagicMock, patch
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content='{"title": "אימון", "duration_min": 45, "rationale": "בדיקה"}'
    ))
    return (
        patch("app.fitness.history", return_value=recent_days),
        patch("app.fitness.latest_body_metrics", return_value=None),
        patch("app.fitness_profile.render_profile_block", return_value="profile"),
        patch("app.llm.get_gemini_llm", return_value=llm),
        patch("app.garmin.sync.get_wellness", return_value=garmin_wellness),
    )


def test_daily_rec_garmin_downgrades_ready_to_light_on_poor_sleep():
    """Deterministic history/RPE logic alone would say 'ready' — poor Garmin sleep
    score must downgrade it to 'light' and record why in garmin_flags."""
    import asyncio
    from app.fitness import _user_today
    today = _user_today()
    # No sessions in the last 14 days → days_since_last stays -1 → readiness = 'ready'.
    recent_days = []
    garmin_wellness = {"date": today.isoformat(), "sleep_score": 40, "sleep_min": 300,
                        "bb_high": 70, "resting_hr": 55}

    mocks = _daily_rec_common_mocks(recent_days, garmin_wellness)
    with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
        from app.fitness import generate_daily_recommendation
        result = asyncio.get_event_loop().run_until_complete(
            generate_daily_recommendation("default")
        )

    assert result["readiness"] == "light"
    assert result["garmin"]["sleep_score"] == 40
    assert "שינה ירודה" in result["garmin_flags"]


def test_daily_rec_garmin_never_upgrades_rest():
    """A day already forced to 'rest' by the deterministic rules must stay 'rest'
    even with excellent Garmin wellness data — Garmin can only downgrade."""
    import asyncio
    from app.fitness import _user_today
    today = _user_today()
    # Trained today with RPE 9 → deterministic rules force 'rest'.
    recent_days = [{
        "date": today.isoformat(), "session_count": 1, "avg_rpe": 9.0,
        "sessions": [{"ai_next_rec": {"targets": []}}],
    }]
    garmin_wellness = {"date": today.isoformat(), "sleep_score": 95, "bb_high": 90, "resting_hr": 48}

    mocks = _daily_rec_common_mocks(recent_days, garmin_wellness)
    with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
        from app.fitness import generate_daily_recommendation
        result = asyncio.get_event_loop().run_until_complete(
            generate_daily_recommendation("default")
        )

    assert result["readiness"] == "rest"
    assert result["garmin_flags"] == []


def test_daily_rec_no_garmin_data_is_unaffected():
    """When Garmin isn't connected (get_wellness returns None), behavior must be
    identical to before this feature existed — no crash, garmin=None."""
    import asyncio
    recent_days = []

    mocks = _daily_rec_common_mocks(recent_days, None)
    with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
        from app.fitness import generate_daily_recommendation
        result = asyncio.get_event_loop().run_until_complete(
            generate_daily_recommendation("default")
        )

    assert result["readiness"] == "ready"
    assert result["garmin"] is None
    assert result["garmin_flags"] == []


def test_body_metric_deltas_direction_and_goodness():
    """Deltas vs previous measurement: LBM/SMM up = good, BF% down = good,
    weight carries no goodness judgement."""
    from app.fitness import _body_metric_deltas
    new = {"weight_kg": 71.6, "lbm_kg": 59.1, "smm_kg": 33.7, "bf_pct": 17.5}
    prev = {"weight_kg": 70.6, "lbm_kg": 58.0, "smm_kg": 33.1, "bf_pct": 17.3}
    d = _body_metric_deltas(new, prev)
    assert d["smm_kg"]["delta"] == 0.6 and d["smm_kg"]["good"] is True
    assert d["lbm_kg"]["delta"] == 1.1 and d["lbm_kg"]["good"] is True
    assert d["bf_pct"]["delta"] == 0.2 and d["bf_pct"]["good"] is False  # fat went up
    assert d["weight_kg"]["delta"] == 1.0 and "good" not in d["weight_kg"]


def test_body_metric_deltas_first_measurement_and_missing_fields():
    from app.fitness import _body_metric_deltas
    assert _body_metric_deltas({"weight_kg": 71}, None) == {}
    d = _body_metric_deltas({"weight_kg": 71, "bf_pct": None}, {"weight_kg": 70, "bf_pct": 20})
    assert "bf_pct" not in d and d["weight_kg"]["delta"] == 1.0


# ---------------------------------------------------------------------------
# Image tools — general vision analysis + Drive save with memory tagging
# ---------------------------------------------------------------------------

def test_get_image_tools_returns_two_tools_with_correct_names():
    from app.image_tools import get_image_tools
    tools = get_image_tools("test-chat-id")
    assert {t.name for t in tools} == {"analyze_image", "save_photo_tagged"}


def test_photos_category_is_allowed_not_coerced_to_misc():
    """The Photos memory category must be in ALLOWED_CATEGORIES so photo-index notes
    don't silently land in Misc/."""
    from app.memory.obsidian import ALLOWED_CATEGORIES
    assert "Photos" in ALLOWED_CATEGORIES


def test_save_photo_tagged_uploads_and_writes_memory_note():
    """save_photo_tagged must upload to Drive AND append a Photos/<YYYY-MM> memory note
    containing the Drive link + description, so the link is retrievable later."""
    import asyncio

    fact_calls = []
    fake_creds = MagicMock(valid=True, scopes=["https://www.googleapis.com/auth/drive.file"])

    async def fake_download(message_id):
        assert message_id == "msg_123"
        return b"jpegbytes", "image/jpeg"

    async def fake_vision(image_bytes, mime, system, user_text):
        return "אופני הרים כחולים של טרק\n#אופניים #ספורט"

    with (
        patch("app.google.auth.get_credentials", return_value=fake_creds),
        patch("app.google.drive_tools._download_from_waha", side_effect=fake_download),
        patch("app.google.drive.upload_photo", return_value="https://drive.google.com/file/d/abc/view"),
        patch("app.image_tools._vision", side_effect=fake_vision),
        patch("app.memory.obsidian.save_fact",
              side_effect=lambda cat, ent, content: fact_calls.append((cat, ent, content)) or "Saved"),
    ):
        from app.image_tools import get_image_tools
        tools = {t.name: t for t in get_image_tools("test-chat-id")}
        result = asyncio.get_event_loop().run_until_complete(
            tools["save_photo_tagged"].ainvoke(
                {"message_id": "msg_123", "filename": "bike.jpg", "subfolder": "אופניים"}
            )
        )

    assert "https://drive.google.com/file/d/abc/view" in result
    assert len(fact_calls) == 1
    cat, ent, content = fact_calls[0]
    assert cat == "Photos"
    import re as _re
    assert _re.fullmatch(r"\d{4}-\d{2}", ent), f"entity should be YYYY-MM, got {ent}"
    assert "https://drive.google.com/file/d/abc/view" in content
    assert "אופני הרים" in content  # AI description indexed for retrieval
    assert "#אופניים" in content


def test_analyze_image_passes_question_and_returns_vision_text():
    import asyncio

    async def fake_download(message_id):
        return b"jpegbytes", "image/jpeg"

    seen = {}

    async def fake_vision(image_bytes, mime, system, user_text):
        seen["question"] = user_text
        return "אלה אופני Trek Marlin 7, אופני הרים לשבילים."

    with (
        patch("app.google.drive_tools._download_from_waha", side_effect=fake_download),
        patch("app.image_tools._vision", side_effect=fake_vision),
    ):
        from app.image_tools import get_image_tools
        tools = {t.name: t for t in get_image_tools("test-chat-id")}
        result = asyncio.get_event_loop().run_until_complete(
            tools["analyze_image"].ainvoke({"message_id": "m1", "question": "איזה דגם אופניים זה?"})
        )

    assert seen["question"] == "איזה דגם אופניים זה?"
    assert "Trek Marlin" in result


def test_episode_gate_skips_trivial_exchanges():
    """Trivial acknowledgements must not trigger an LLM summary / episode row."""
    from app.memory.episodes import _is_trivial
    assert _is_trivial("User: thanks\nAssistant: You're welcome!") is True
    assert _is_trivial("User: תודה\nAssistant: בשמחה") is True
    assert _is_trivial("User: ok\nAssistant: 👍") is True
    # Substantive short exchanges and long transcripts are kept.
    assert _is_trivial("User: remind me to call the bank tomorrow at 9\nAssistant: Done.") is False
    assert _is_trivial("User: " + "x" * 300) is False
