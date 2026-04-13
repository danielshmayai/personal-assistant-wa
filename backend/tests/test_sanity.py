"""
Sanity tests for the PA backend.

Rules:
- No external connections (no Ollama, no Postgres, no Google APIs).
- All I/O boundaries are mocked at import time via unittest.mock.patch.
- Tests are fast (<30s total) and deterministic.
"""

import importlib
import sys
import types
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
    """Return a MagicMock that looks enough like ChatOllama for tests."""
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="mocked"))
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
]


@pytest.mark.parametrize("module_path", MODULES_UNDER_TEST)
def test_module_imports_without_error(module_path):
    """Every listed module must import cleanly with no side-effects."""
    with patch("app.llm.ChatOllama", return_value=_make_llm_mock()):
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
    # intent and reply are optional fields; they are absent from the dict until set.
    assert state.get("intent") is None
    assert state.get("reply", "") == ""


def test_distilled_intent_instantiates():
    """DistilledIntent must accept all required fields and validate category."""
    from app.graph.state import DistilledIntent

    intent = DistilledIntent(
        category="Development_Task",
        is_bug=True,
        summary="Fix login crash",
        raw_text="the login button crashes on iOS",
    )

    assert intent.category == "Development_Task"
    assert intent.is_bug is True
    assert intent.raw_text == "the login button crashes on iOS"


# ---------------------------------------------------------------------------
# 3. build_graph() compiles without errors
# ---------------------------------------------------------------------------

def test_build_graph_returns_compiled_graph():
    """build_graph() must return a compiled LangGraph without DB or LLM calls."""
    with (
        patch("app.llm.get_llm", return_value=_make_llm_mock()),
        patch("app.memory.store.load_memory_context", new_callable=AsyncMock, return_value=""),
        patch("app.memory.store.insert_rule", return_value=None),
        patch("app.llm.ChatOllama", return_value=_make_llm_mock()),
        patch("app.graph.checkpointer.get_checkpointer", return_value=None),
    ):
        from app.graph.graph import build_graph
        graph = build_graph()

    # A compiled LangGraph exposes ainvoke / invoke
    assert hasattr(graph, "ainvoke"), "Compiled graph must expose ainvoke"
    assert hasattr(graph, "invoke"), "Compiled graph must expose invoke"


# ---------------------------------------------------------------------------
# 4. get_google_tools returns 5 tools with correct names
# ---------------------------------------------------------------------------

EXPECTED_TOOL_NAMES = {
    "google_connect",
    "gmail_read",
    "gmail_send",
    "calendar_list",
    "calendar_create",
}


def test_get_google_tools_returns_five_tools():
    """get_google_tools must return exactly 5 tool objects."""
    # Patch the underlying Google API helpers so no credentials are needed.
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

    assert len(tools) == 5, f"Expected 5 tools, got {len(tools)}: {[t.name for t in tools]}"


def test_get_google_tools_has_correct_names():
    """Each of the 5 tools must carry the exact expected name."""
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
    assert actual_names == EXPECTED_TOOL_NAMES, (
        f"Tool name mismatch.\n  Expected: {EXPECTED_TOOL_NAMES}\n  Got:      {actual_names}"
    )


# ---------------------------------------------------------------------------
# 5. _detect_tool keyword routing
# ---------------------------------------------------------------------------

KEYWORD_ROUTING_CASES = [
    # (input_text, expected_tool_name)
    ("connect gmail", "google_connect"),
    ("read my emails", "gmail_read"),
    ("send email to bob", "gmail_send"),
    ("show my calendar", "calendar_list"),
    ("create a meeting", "calendar_create"),
    ("hello how are you", None),
]


@pytest.mark.parametrize("text,expected_tool", KEYWORD_ROUTING_CASES)
def test_detect_tool_keyword_routing(text, expected_tool):
    """_detect_tool must map inputs to the correct tool or None."""
    from app.graph.tool_node import _detect_tool

    result = _detect_tool(text)
    assert result == expected_tool, (
        f"Input '{text}': expected tool={expected_tool!r}, got={result!r}"
    )


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
