"""Tenant separation & privacy hardening tests (P2).

Covers the leak paths closed in the hardening phase:
  - runtime.get_secret fails closed for tenants
  - Tuya tools resolve per-tenant BYO creds (never the owner env)
  - Tavily key resolves per tenant
  - system prompt carries no owner persona/PII for tenants
  - media_cache blocks cross-scope reads
  - app_settings reads/writes are tenant-scoped
  - Garmin token store / client cache is tenant-keyed
  - offboarding deletes every tenant-scoped engine table

Rules (same as test_sanity.py): no external connections; DB and SDK
boundaries are mocked; module state is isolated between tests.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest


def _wipe_modules():
    # Also drop the package roots: the persistent `app` package object caches
    # submodules as attributes, so `from app import X` would return a stale
    # module holding a different ContextVar instance than freshly imported code.
    for mod in [k for k in sys.modules
                if k in ("app", "product") or k.startswith(("app.", "product."))]:
        del sys.modules[mod]


@pytest.fixture(autouse=True)
def _isolate_modules():
    _wipe_modules()
    yield
    _wipe_modules()


def _set_scope(tenant_id: str):
    """Set the tenant ContextVar; returns a reset callable."""
    from app.context import current_tenant_id
    token = current_tenant_id.set(tenant_id)
    return lambda: current_tenant_id.reset(token)


def _product_like_resolver(tenant_secrets: dict[str, dict[str, str]]):
    """Mimic product/main.py resolve_secret: tenants get their catalog only
    (no env fallback), owner gets the process env."""
    import os
    from app.context import current_tenant_id

    def resolve(key: str):
        tid = current_tenant_id.get()
        if tid:
            return tenant_secrets.get(tid, {}).get(key)
        return os.getenv(key)

    return resolve


# ---------------------------------------------------------------------------
# 1. runtime.get_secret — fail closed for tenants
# ---------------------------------------------------------------------------

class TestGetSecretFailClosed:
    def test_tenant_gets_empty_when_resolver_raises(self, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "owner-env-value")
        from app import runtime

        def boom(key):
            raise RuntimeError("db blip")

        runtime.set_secret_resolver(boom)
        reset = _set_scope("tenant-a")
        try:
            assert runtime.get_secret("SOME_KEY") == ""
        finally:
            reset()

    def test_owner_keeps_env_fallback_when_resolver_raises(self, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "owner-env-value")
        from app import runtime

        def boom(key):
            raise RuntimeError("db blip")

        runtime.set_secret_resolver(boom)
        assert runtime.get_secret("SOME_KEY") == "owner-env-value"


# ---------------------------------------------------------------------------
# 2. Tuya — per-tenant BYO creds, never the owner env
# ---------------------------------------------------------------------------

class TestTuyaTenantIsolation:
    def test_tenant_without_creds_gets_no_tools_even_with_env_set(self, monkeypatch):
        monkeypatch.setenv("TUYA_ACCESS_ID", "owner-id")
        monkeypatch.setenv("TUYA_ACCESS_KEY", "owner-key")
        from app import runtime
        runtime.set_secret_resolver(_product_like_resolver({}))

        reset = _set_scope("tenant-a")
        try:
            from app.tuya.tools import get_tuya_tools
            assert get_tuya_tools() == []
        finally:
            reset()

    def test_owner_keeps_env_creds(self, monkeypatch):
        monkeypatch.setenv("TUYA_ACCESS_ID", "owner-id")
        monkeypatch.setenv("TUYA_ACCESS_KEY", "owner-key")
        from app.tuya.tools import get_tuya_tools
        tools = get_tuya_tools()
        assert {t.name for t in tools} == {
            "list_tuya_devices", "get_device_status", "control_device",
        }

    def test_cloud_client_is_distinct_per_tenant(self):
        from app import runtime
        runtime.set_secret_resolver(_product_like_resolver({
            "tenant-a": {"TUYA_ACCESS_ID": "a-id", "TUYA_ACCESS_KEY": "a-key"},
            "tenant-b": {"TUYA_ACCESS_ID": "b-id", "TUYA_ACCESS_KEY": "b-key"},
        }))
        with patch("tinytuya.Cloud", side_effect=lambda **kw: MagicMock(**kw)) as cloud_cls:
            from app.tuya import tools as tuya_tools
            reset = _set_scope("tenant-a")
            try:
                client_a = tuya_tools._cloud()
                client_a_again = tuya_tools._cloud()
            finally:
                reset()
            reset = _set_scope("tenant-b")
            try:
                client_b = tuya_tools._cloud()
            finally:
                reset()

        assert client_a is client_a_again  # cached within a tenant
        assert client_a is not client_b    # never shared across tenants
        assert cloud_cls.call_count == 2
        assert cloud_cls.call_args_list[0].kwargs["apiKey"] == "a-id"
        assert cloud_cls.call_args_list[1].kwargs["apiKey"] == "b-id"

    def test_cred_rotation_rebuilds_client(self):
        from app import runtime
        secrets = {"tenant-a": {"TUYA_ACCESS_ID": "old-id", "TUYA_ACCESS_KEY": "k"}}
        runtime.set_secret_resolver(_product_like_resolver(secrets))
        with patch("tinytuya.Cloud", side_effect=lambda **kw: MagicMock(**kw)):
            from app.tuya import tools as tuya_tools
            reset = _set_scope("tenant-a")
            try:
                first = tuya_tools._cloud()
                secrets["tenant-a"]["TUYA_ACCESS_ID"] = "new-id"
                second = tuya_tools._cloud()  # fingerprint mismatch → rebuild
            finally:
                reset()
        assert first is not second

    def test_tenant_without_creds_cloud_raises_not_configured(self):
        from app import runtime
        runtime.set_secret_resolver(_product_like_resolver({}))
        from app.tuya import tools as tuya_tools
        reset = _set_scope("tenant-a")
        try:
            with pytest.raises(RuntimeError, match="not configured"):
                tuya_tools._cloud()
        finally:
            reset()

    def test_lan_control_is_owner_only(self, monkeypatch):
        from app.tuya import tools as tuya_tools
        monkeypatch.setattr(tuya_tools, "TUYA_PREFER_LOCAL", True)
        assert tuya_tools._lan_allowed() is True  # owner scope
        reset = _set_scope("tenant-a")
        try:
            assert tuya_tools._lan_allowed() is False
        finally:
            reset()

    def test_clear_tuya_cache_scoped_to_tenant(self):
        from app import runtime
        runtime.set_secret_resolver(_product_like_resolver({
            "tenant-a": {"TUYA_ACCESS_ID": "a", "TUYA_ACCESS_KEY": "a"},
        }))
        with patch("tinytuya.Cloud", side_effect=lambda **kw: MagicMock()):
            from app.tuya import tools as tuya_tools
            reset = _set_scope("tenant-a")
            try:
                first = tuya_tools._cloud()
                tuya_tools.clear_tuya_cache("tenant-a")
                second = tuya_tools._cloud()
            finally:
                reset()
        assert first is not second


# ---------------------------------------------------------------------------
# 3. Tavily — per-tenant key
# ---------------------------------------------------------------------------

class TestTavilyTenantIsolation:
    def test_tenant_key_is_used_not_owner_env(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "owner-key")
        from app import runtime
        runtime.set_secret_resolver(_product_like_resolver({
            "tenant-a": {"TAVILY_API_KEY": "tenant-key"},
        }))
        with patch("app.web.tools._tavily_search", return_value="ok") as mock_t:
            from app.web.tools import web_search
            reset = _set_scope("tenant-a")
            try:
                web_search.invoke({"query": "q"})
            finally:
                reset()
        mock_t.assert_called_once_with("q", "tenant-key")

    def test_tenant_without_key_falls_back_to_ddg(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "owner-key")
        from app import runtime
        runtime.set_secret_resolver(_product_like_resolver({}))
        with patch("app.web.tools._ddg_search", return_value="ddg") as mock_ddg:
            from app.web.tools import web_search
            reset = _set_scope("tenant-a")
            try:
                result = web_search.invoke({"query": "q"})
            finally:
                reset()
        mock_ddg.assert_called_once_with("q")
        assert result == "ddg"


# ---------------------------------------------------------------------------
# 4. System prompt — no owner persona/PII for tenants
# ---------------------------------------------------------------------------

class TestSystemPromptScoping:
    def _build(self, chat_id=""):
        from app.graph.distiller import _build_system_prompt
        return _build_system_prompt("", chat_id=chat_id)

    def test_owner_prompt_keeps_persona_and_targets(self):
        prompt = self._build()
        assert "You are danidin, a personal assistant." in prompt
        assert "goal is high protein (target 110g/day) and low carbs" in prompt
        assert "protein vs the 110g target" in prompt
        assert "(personal medical constraints always apply — see profile)" in prompt

    def test_tenant_prompt_carries_no_owner_pii(self):
        reset = _set_scope("tenant-a")
        try:
            prompt = self._build(chat_id="web_tenant-a_x")
        finally:
            reset()
        assert "danidin" not in prompt
        assert "110g" not in prompt
        assert "medical constraints" not in prompt
        assert "You are the user's personal assistant." in prompt
        assert "*Nutrition tracker:*" in prompt
        assert "*Fitness & training tracker:*" in prompt

    def test_no_unresolved_placeholders(self):
        for scope in ("", "tenant-a"):
            reset = _set_scope(scope)
            try:
                prompt = self._build()
            finally:
                reset()
            for placeholder in (
                "{assistant_identity}", "{nutrition_goal}",
                "{protein_target}", "{fitness_note}", "{datetime_block}",
            ):
                assert placeholder not in prompt


# ---------------------------------------------------------------------------
# 5. media_cache — cross-scope reads blocked
# ---------------------------------------------------------------------------

class TestMediaCacheScoping:
    def test_tenant_upload_not_readable_by_owner_or_other_tenant(self):
        from app import media_cache
        reset = _set_scope("tenant-a")
        try:
            media_cache.store_web_upload("web_abc", b"bytes", "image/png", "a.png")
            assert media_cache.retrieve("web_abc") is not None
        finally:
            reset()
        assert media_cache.retrieve("web_abc") is None  # owner scope
        reset = _set_scope("tenant-b")
        try:
            assert media_cache.retrieve("web_abc") is None
        finally:
            reset()

    def test_owner_whatsapp_media_not_readable_by_tenant(self):
        from app import media_cache
        payload = {"_data": {"body": "A" * 200, "mimetype": "image/jpeg"}}
        assert media_cache.store_from_payload("wa_msg_1", payload) is True
        assert media_cache.retrieve("wa_msg_1") is not None  # owner reads own
        reset = _set_scope("tenant-a")
        try:
            assert media_cache.retrieve("wa_msg_1") is None
        finally:
            reset()

    def test_purge_scope_drops_only_that_tenant(self):
        from app import media_cache
        reset = _set_scope("tenant-a")
        try:
            media_cache.store_web_upload("web_a", b"x", "image/png", "a.png")
        finally:
            reset()
        media_cache.store_web_upload("web_owner", b"y", "image/png", "o.png")
        media_cache.purge_scope("tenant-a")
        reset = _set_scope("tenant-a")
        try:
            assert media_cache.retrieve("web_a") is None
        finally:
            reset()
        assert media_cache.retrieve("web_owner") is not None
        # purge with empty scope must be a no-op (owner data protected)
        media_cache.purge_scope("")
        assert media_cache.retrieve("web_owner") is not None


# ---------------------------------------------------------------------------
# 6. app_settings — tenant-scoped reads/writes
# ---------------------------------------------------------------------------

def _mock_conn(fetchone=None):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    return conn, cur


class TestAppSettingsScoping:
    def test_get_filters_by_tenant(self):
        conn, cur = _mock_conn(fetchone=None)
        with patch("psycopg2.connect", return_value=conn):
            from app.memory.store import get_app_setting
            reset = _set_scope("tenant-a")
            try:
                get_app_setting("tts_config")
            finally:
                reset()
        sql, params = cur.execute.call_args[0]
        assert "tenant_id = %s" in sql
        assert params == ("tenant-a", "tts_config")

    def test_set_writes_tenant_row(self):
        conn, cur = _mock_conn()
        with patch("psycopg2.connect", return_value=conn):
            from app.memory.store import set_app_setting
            reset = _set_scope("tenant-a")
            try:
                set_app_setting("tts_config", {"voice": "x"})
            finally:
                reset()
        sql, params = cur.execute.call_args[0]
        assert "ON CONFLICT (tenant_id, key)" in sql
        assert params[0] == "tenant-a"

    def test_owner_scope_is_empty_string(self):
        conn, cur = _mock_conn(fetchone=None)
        with patch("psycopg2.connect", return_value=conn):
            from app.memory.store import get_app_setting
            get_app_setting("tts_config")
        _, params = cur.execute.call_args[0]
        assert params == ("", "tts_config")


# ---------------------------------------------------------------------------
# 7. Garmin — tenant-keyed token store and client cache
# ---------------------------------------------------------------------------

class TestGarminTenantIsolation:
    def test_token_rows_keyed_by_scope(self):
        conn, cur = _mock_conn()
        with (
            patch("app.garmin.client.crypto.encrypt", side_effect=lambda v: f"ENC:{v}"),
            patch("psycopg2.connect", return_value=conn),
        ):
            from app.garmin.client import save_token_blob
            reset = _set_scope("tenant-a")
            try:
                save_token_blob("blob")
            finally:
                reset()
        _, params = cur.execute.call_args[0]
        assert params == ("tenant-a", "ENC:blob")

    def test_tenant_without_token_raises_even_when_owner_connected(self):
        fake_garmin = MagicMock()
        garmin_mod = MagicMock(Garmin=MagicMock(return_value=fake_garmin))

        conn, cur = _mock_conn(fetchone=("owner-blob",))
        with (
            patch.dict(sys.modules, {"garminconnect": garmin_mod}),
            patch("app.garmin.client.crypto.decrypt", side_effect=lambda v: v),
            patch("psycopg2.connect", return_value=conn),
        ):
            from app.garmin.client import get_client, GarminNotConnected
            owner_client = get_client()  # owner resumes from stored blob
            assert owner_client is fake_garmin

            cur.fetchone.return_value = None  # tenant has no token row
            reset = _set_scope("tenant-a")
            try:
                with pytest.raises(GarminNotConnected):
                    get_client()  # must NOT return the owner's cached client
            finally:
                reset()


# ---------------------------------------------------------------------------
# 8. Offboarding — every tenant-scoped engine table is covered (tripwire)
# ---------------------------------------------------------------------------

class TestOffboardingCoverage:
    def test_deletion_table_lists_are_complete(self):
        offboarding = pytest.importorskip(
            "product.offboarding", reason="product package not on path"
        )
        for table in ("app_settings", "garmin_tokens", "memory_facts",
                      "vault_embeddings", "episodes", "conversation_log"):
            assert table in offboarding._ENGINE_TENANT_TABLES
        for table in ("nutrition_logs", "fitness_workouts", "fitness_body_metrics"):
            assert table in offboarding._ENGINE_CHAT_SCOPED_TABLES
