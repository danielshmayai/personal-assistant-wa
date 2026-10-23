"""System prompt and tool binding are scoped to the modules a tenant has on."""
from unittest.mock import patch


def _prompt(groups, chat_id="web_x"):
    from app.graph.distiller import _build_system_prompt
    return _build_system_prompt("", chat_id, groups=groups)


def test_full_prompt_stays_small():
    """Was ~12.6K chars of prompt on every call; everything-on is now ≤ 5K."""
    assert len(_prompt(None)) <= 5000


def test_disabled_modules_contribute_no_sections():
    prompt = _prompt({"image", "memory"})
    assert "*Images" in prompt and "Memory is captured automatically" in prompt
    for absent in ("*Google:*", "*Smart home:*", "*Nutrition tracker", "*Fitness", "*Reminders:*", "*Web:*"):
        assert absent not in prompt
    assert "You have tools for long-term memory, image analysis." in prompt


def test_ollama_fallback_gets_no_tool_sections():
    from app.graph.distiller import _build_system_prompt
    prompt = _build_system_prompt("", "web_x", ollama_fallback=True)
    assert "*Images" not in prompt and "NO TOOLS available" in prompt


def test_schedule_tuya_command_only_bound_with_smart_home():
    from app.graph import tools_registry
    tools_registry.clear_tools_cache()
    with patch("app.google.auth.get_credentials", return_value=None):
        off = {t.name for t in tools_registry.get_all_tools("c1", "t", ["scheduling"])}
        with patch("app.tuya.tools.get_tuya_tools", return_value=[]):
            on = {t.name for t in tools_registry.get_all_tools("c2", "t", ["scheduling", "smart-home"])}
    assert "schedule_reminder" in off and "schedule_tuya_command" not in off
    assert "schedule_tuya_command" in on
    tools_registry.clear_tools_cache()
