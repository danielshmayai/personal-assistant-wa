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


# ---------------------------------------------------------------------------
# 10. Voice (TTS) — GOOGLE_TTS_API_KEY resolved per tenant (P6 regression)
#
# backend/app/routers/web_chat.py used to import GOOGLE_TTS_API_KEY directly
# from app.config (the same anti-pattern as the pre-P2 Tuya/Tavily bugs) —
# any tenant with the "tts" module enabled would have silently used the
# owner's Google TTS key. Fixed by extracting into app/voice.py and
# resolving the key via runtime.get_secret() per call, like llm.py.
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock  # noqa: E402


def _run(coro):
    """Drive a coroutine to completion without asyncio.run() — this codebase's
    test suite doesn't use pytest-asyncio, and asyncio.run() closes the loop
    it creates, which breaks other tests' bare asyncio.get_event_loop() calls
    later in the same pytest session (test_sanity.py's established idiom)."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)


class TestVoiceTenantIsolation:
    def test_tts_uses_tenant_key_not_owner_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_TTS_API_KEY", "owner-key")
        from app import runtime
        runtime.set_secret_resolver(_product_like_resolver({
            "tenant-a": {"GOOGLE_TTS_API_KEY": "tenant-key"},
        }))

        fake_resp = MagicMock(status_code=200)
        fake_resp.json.return_value = {"audioContent": "aGVsbG8="}  # base64 "hello"
        fake_client = AsyncMock()
        fake_client.post = AsyncMock(return_value=fake_resp)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        from app import voice
        reset = _set_scope("tenant-a")
        try:
            with patch("httpx.AsyncClient", return_value=fake_client):
                _run(voice.synthesize_speech("hello"))
        finally:
            reset()

        _, kwargs = fake_client.post.call_args
        assert kwargs["params"] == {"key": "tenant-key"}

    def test_tts_falls_back_to_edge_tts_without_tenant_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_TTS_API_KEY", "owner-key")
        from app import runtime
        runtime.set_secret_resolver(_product_like_resolver({}))  # tenant has no key

        async def fake_stream():
            yield {"type": "audio", "data": b"chunk"}

        fake_communicate = MagicMock()
        fake_communicate.stream = fake_stream
        edge_tts_mod = MagicMock()
        edge_tts_mod.Communicate = MagicMock(return_value=fake_communicate)

        from app import voice
        reset = _set_scope("tenant-a")
        try:
            with (
                patch.dict(sys.modules, {"edge_tts": edge_tts_mod}),
                patch("httpx.AsyncClient") as httpx_client,
            ):
                result = _run(voice.synthesize_speech("hello"))
        finally:
            reset()

        httpx_client.assert_not_called()  # never touched Google TTS at all
        assert result == b"chunk"

    def test_owner_keeps_env_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_TTS_API_KEY", "owner-key")
        from app import runtime
        runtime.set_secret_resolver(_product_like_resolver({}))

        fake_resp = MagicMock(status_code=200)
        fake_resp.json.return_value = {"audioContent": "aGVsbG8="}
        fake_client = AsyncMock()
        fake_client.post = AsyncMock(return_value=fake_resp)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        from app import voice
        with patch("httpx.AsyncClient", return_value=fake_client):
            _run(voice.synthesize_speech("hello"))  # owner scope — no _set_scope

        _, kwargs = fake_client.post.call_args
        assert kwargs["params"] == {"key": "owner-key"}


# ---------------------------------------------------------------------------
# 9. Fitness LLM prompts — owner medical PII (P4 regression)
#
# fitness_profile.render_profile_block was correctly gated since P0, but
# several separate hardcoded prompt fragments in fitness.py (evaluate_workout,
# evaluate_body_metrics, suggest_workout) instructed the LLM to reference the
# owner's Gilbert's-Syndrome/neck-injury constraints regardless of scope —
# caught live when a tenant's workout evaluation surfaced "תסמונת גילברט".
# These tests pin the fix: no owner medical terms leak into tenant prompts,
# and the owner's prompt text is unchanged.
# ---------------------------------------------------------------------------

_OWNER_MEDICAL_TERMS = ("gilbert", "scapular", "neck")


class TestFitnessPromptScoping:
    def test_evaluate_body_instructions_tenant_vs_owner(self):
        from app.fitness import _evaluate_body_instructions
        owner_text = _evaluate_body_instructions()
        assert "gilbert" in owner_text.lower()

        reset = _set_scope("tenant-a")
        try:
            tenant_text = _evaluate_body_instructions()
        finally:
            reset()
        assert "gilbert" not in tenant_text.lower()

    def test_evaluate_workout_instructions_tenant_vs_owner(self):
        from app.fitness import _evaluate_workout_instructions
        owner_text = _evaluate_workout_instructions()
        for term in _OWNER_MEDICAL_TERMS:
            assert term in owner_text.lower()

        reset = _set_scope("tenant-a")
        try:
            tenant_text = _evaluate_workout_instructions()
        finally:
            reset()
        for term in _OWNER_MEDICAL_TERMS:
            assert term not in tenant_text.lower()

    def test_suggest_workout_constraints_tenant_vs_owner(self):
        from app.fitness import _suggest_workout_constraints
        owner_text = _suggest_workout_constraints()
        assert "scapular" in owner_text.lower()
        assert "back squat" in owner_text.lower()

        reset = _set_scope("tenant-a")
        try:
            tenant_text = _suggest_workout_constraints()
        finally:
            reset()
        assert "scapular" not in tenant_text.lower()
        assert "back squat" not in tenant_text.lower()

    def test_garmin_allowed_owner_true_tenant_checks_own_connection(self):
        from app.fitness import _garmin_allowed
        assert _garmin_allowed() is True  # owner scope: always allowed

        reset = _set_scope("tenant-a")
        try:
            with patch("app.garmin.client.is_connected", return_value=False):
                assert _garmin_allowed() is False
            with patch("app.garmin.client.is_connected", return_value=True):
                assert _garmin_allowed() is True
        finally:
            reset()

    def test_garmin_wellness_scoped_by_tenant(self):
        """garmin.sync.get_wellness/_upsert_wellness must filter by scope,
        not just log_date — otherwise two tenants syncing the same day would
        silently overwrite each other's sleep/HR/steps in one shared row."""
        conn, cur = _mock_conn(fetchone=None)
        with patch("psycopg2.connect", return_value=conn):
            from app.garmin.sync import get_wellness
            import datetime
            reset = _set_scope("tenant-a")
            try:
                get_wellness(datetime.date(2026, 1, 1))
            finally:
                reset()
        sql, params = cur.execute.call_args[0]
        assert "tenant_id = %s" in sql


# ---------------------------------------------------------------------------
# 11. scheduled_jobs.py — tenant_id scoping, cross-process race fix (P6.6)
#
# Two problems fixed together: (a) chat tools only have the ephemeral
# per-conversation chat_id, so a reminder created in one session would be
# unfindable/undeliverable from any other — insert_job now overrides to a
# stable per-tenant key (_scoped_chat_id). (b) the owner's engine and
# product-api are separate processes each polling this table — without a
# tenant_id column and a WHERE split, both would see and execute the same
# due job. (c) _execute_due_jobs must set current_tenant_id per job or a
# tenant's scheduled Tuya command would resolve the owner's credentials
# (same bug class as the P4 Garmin-merge fix).
# ---------------------------------------------------------------------------

class TestScheduledJobsTenantIsolation:
    def test_scoped_chat_id_stable_for_tenant_passthrough_for_owner(self):
        from app.scheduled_jobs import _scoped_chat_id
        reset = _set_scope("tenant-a")
        try:
            assert _scoped_chat_id("web_tenant-a_ephemeral123") == "web_tenant-a"
            assert _scoped_chat_id("anything-else") == "web_tenant-a"  # always overrides
        finally:
            reset()
        assert _scoped_chat_id("web_someephemeralid") == "web_someephemeralid"  # owner passthrough
        assert _scoped_chat_id("972501234567@c.us") == "972501234567@c.us"  # WhatsApp passthrough

    def test_insert_job_stores_tenant_id_and_stable_chat_id(self):
        conn, cur = _mock_conn(fetchone=(42,))
        with patch("psycopg2.connect", return_value=conn):
            from app.scheduled_jobs import insert_job
            from datetime import datetime, timezone
            reset = _set_scope("tenant-a")
            try:
                insert_job("web_tenant-a_ephemeral999", "tuya_command", {}, datetime.now(timezone.utc))
            finally:
                reset()
        sql, params = cur.execute.call_args[0]
        assert "tenant_id" in sql
        assert params[0] == "web_tenant-a"  # chat_id overridden, not the ephemeral one
        assert params[1] == "tenant-a"      # tenant_id column

    def test_insert_job_owner_scope_unchanged(self):
        conn, cur = _mock_conn(fetchone=(1,))
        with patch("psycopg2.connect", return_value=conn):
            from app.scheduled_jobs import insert_job
            from datetime import datetime, timezone
            insert_job("972501234567@c.us", "send_message", {"text": "hi"}, datetime.now(timezone.utc))
        _, params = cur.execute.call_args[0]
        assert params[0] == "972501234567@c.us"  # unchanged real WhatsApp chat_id
        assert params[1] == ""                    # owner tenant_id

    def test_get_due_jobs_splits_owner_vs_tenant_queries(self):
        conn, cur = _mock_conn()
        cur.fetchall.return_value = []
        with patch("psycopg2.connect", return_value=conn):
            from app.scheduled_jobs import get_due_jobs
            get_due_jobs(tenant_scope=False)
            owner_sql = cur.execute.call_args[0][0]
            get_due_jobs(tenant_scope=True)
            tenant_sql = cur.execute.call_args[0][0]
        assert "tenant_id = ''" in owner_sql
        assert "tenant_id != ''" in tenant_sql
        assert owner_sql != tenant_sql  # disjoint queries — no cross-process race

    def test_execute_due_jobs_scopes_tuya_command_to_owning_tenant(self):
        """The critical fix: a tenant's scheduled Tuya command must resolve
        THAT tenant's own credentials, never the owner's env — proven by
        checking current_tenant_id is set correctly while _run_job executes."""
        from app.context import current_tenant_id
        import app.scheduled_jobs as sj

        job = {
            "id": 1, "chat_id": "web_tenant-a", "tenant_id": "tenant-a",
            "action_type": "tuya_command",
            "payload": {"device_id": "dev1", "commands": {"switch_1": True}, "description": "test"},
            "run_at": None, "recurrence": None,
        }
        seen_scope_during_run = {}

        async def fake_run_job(j):
            seen_scope_during_run["tid"] = current_tenant_id.get()
            return "ok"

        with (
            patch.object(sj, "get_due_jobs", return_value=[job]),
            patch.object(sj, "_run_job", side_effect=fake_run_job),
            patch.object(sj, "_set_status"),
            patch.object(sj, "mark_job_done"),
            patch.object(sj, "log_activity"),
            patch.object(sj, "_notify", new=AsyncMock()),
        ):
            _run(sj._execute_due_jobs(tenant_scope=True))

        assert seen_scope_during_run["tid"] == "tenant-a"
        assert current_tenant_id.get() == ""  # reset after the job — doesn't leak to the next tick

    def test_execute_due_jobs_owner_job_keeps_empty_scope(self):
        from app.context import current_tenant_id
        import app.scheduled_jobs as sj

        job = {
            "id": 2, "chat_id": "972501234567@c.us", "tenant_id": "",
            "action_type": "send_message", "payload": {"text": "hi"},
            "run_at": None, "recurrence": None,
        }
        seen = {}

        async def fake_run_job(j):
            seen["tid"] = current_tenant_id.get()
            return "ok"

        with (
            patch.object(sj, "get_due_jobs", return_value=[job]),
            patch.object(sj, "_run_job", side_effect=fake_run_job),
            patch.object(sj, "_set_status"),
            patch.object(sj, "mark_job_done"),
            patch.object(sj, "log_activity"),
            patch.object(sj, "_notify", new=AsyncMock()),
        ):
            _run(sj._execute_due_jobs(tenant_scope=False))

        assert seen["tid"] == ""

    def test_cancel_job_and_list_all_jobs_apply_scoped_chat_id(self):
        conn, cur = _mock_conn()
        cur.rowcount = 1
        with patch("psycopg2.connect", return_value=conn):
            from app.scheduled_jobs import cancel_job
            reset = _set_scope("tenant-a")
            try:
                cancel_job(5, "web_tenant-a_someephemeralthread")
            finally:
                reset()
        _, params = cur.execute.call_args[0]
        assert params == (5, "web_tenant-a")  # not the ephemeral thread id passed in


class TestSchedulingModuleGate:
    """Regression guard: the scheduling module must stay owner_only until
    explicit sign-off — the P6.6 architecture work (tenant_id column,
    race-free scheduler, current_tenant_id scoping) is ready, but exposing
    it is a separate, deliberate decision. See PLANNING.md P6.6."""

    def test_scheduling_module_is_owner_only(self):
        registry = pytest.importorskip(
            "product.modules.registry", reason="product package not on path"
        )
        assert registry.MODULES_BY_ID["scheduling"].owner_only is True

    def test_tenant_cannot_enable_scheduling(self):
        registry = pytest.importorskip(
            "product.modules.registry", reason="product package not on path"
        )
        assert "scheduling" not in registry.allowed_ids_for(is_owner=False)
        assert "scheduling" in registry.allowed_ids_for(is_owner=True)


# ---------------------------------------------------------------------------
# 12. self_review — skips owner-only vault-embedding rebuild for tenants (P6.7)
#
# capabilities.sync_capabilities() and embeddings.rebuild_vault_embeddings/
# rebuild_rule_embeddings were never designed to be tenant-aware (their own
# separate VAULT_ROOT constant + a hardcoded `WHERE tenant_id = ''` query) —
# calling them under a tenant's scope would read the OWNER's vault, burn the
# TENANT's Gemini quota, and overwrite the OWNER's embedding cache.
# ---------------------------------------------------------------------------

class TestSelfReviewTenantIsolation:
    def test_tenant_scope_skips_owner_only_rebuild_calls(self):
        import app.memory.self_review as sr
        import app.memory.capabilities as caps
        import app.memory.embeddings as emb

        with (
            patch.object(sr, "get_recent_conversations", return_value=[]),
            patch.object(caps, "sync_capabilities") as fake_sync,
            patch.object(emb, "rebuild_vault_embeddings") as fake_rebuild_vault,
            patch.object(emb, "rebuild_rule_embeddings") as fake_rebuild_rules,
        ):
            reset = _set_scope("tenant-a")
            try:
                result = _run(sr.run_self_review(hours=24))
            finally:
                reset()

        fake_sync.assert_not_called()
        fake_rebuild_vault.assert_not_called()
        fake_rebuild_rules.assert_not_called()
        assert result == "No conversations to review."

    def test_owner_scope_still_calls_rebuild(self):
        import app.memory.self_review as sr
        import app.memory.capabilities as caps
        import app.memory.embeddings as emb

        async def fake_rebuild():
            return 0

        with (
            patch.object(sr, "get_recent_conversations", return_value=[]),
            patch.object(caps, "sync_capabilities") as fake_sync,
            patch.object(emb, "rebuild_vault_embeddings", side_effect=fake_rebuild),
            patch.object(emb, "rebuild_rule_embeddings", side_effect=fake_rebuild),
        ):
            _run(sr.run_self_review(hours=24))  # owner scope — no _set_scope

        fake_sync.assert_called_once()
