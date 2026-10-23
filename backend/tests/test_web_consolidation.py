"""Phase 4: features moved from the retired engine web UI into the product."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


def _tenant(tid="t1", owner=False):
    from product.tenancy.models import Tenant
    return Tenant(id=tid, email=f"{tid}@x.com", is_owner=owner)


@pytest.fixture()
def client_as():
    """TestClient for the product app, authenticated as a given tenant."""
    from app.context import current_tenant_id
    from product.deps import require_owner, require_session
    from product.main import app

    def make(tenant):
        async def _session():
            current_tenant_id.set(tenant.engine_scope)
            return tenant
        app.dependency_overrides[require_session] = _session
        app.dependency_overrides[require_owner] = _session
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def test_conversation_messages_of_another_tenant_are_not_readable(client_as):
    client = client_as(_tenant("t1"))
    with patch("app.graph.streaming.get_history", AsyncMock(return_value=[])) as hist:
        assert client.get("/api/conversations/web_t2_abc/messages").status_code == 404
        assert client.get("/api/conversations/web_t1_abc/messages").status_code == 200
    hist.assert_awaited_once_with("web_t1_abc")


def test_deleting_another_tenants_conversation_is_refused(client_as):
    client = client_as(_tenant("t1"))
    with patch("app.memory.store.delete_web_conversation") as delete:
        assert client.delete("/api/conversations/web_t2_abc").status_code == 404
    delete.assert_not_called()


def test_leads_are_owner_only(client_as):
    with patch("app.leads.get_all_leads", return_value=[]):
        assert client_as(_tenant("t1")).get("/api/leads").status_code == 404
        with patch("product.config.OWNER_LEGACY_SCOPE", True):
            assert client_as(_tenant("o", owner=True)).get("/api/leads").json() == {"leads": []}


def test_assistant_health_shape(client_as):
    from app import metrics
    from app.context import current_tenant_id
    token = current_tenant_id.set("o2")
    try:
        metrics.record_reply(1200)
        metrics.record_reply(1800)
        metrics.record_retrieval(injected=True, notes=True)
        metrics.record_retrieval(injected=True, notes=False)
    finally:
        current_tenant_id.reset(token)
    client = client_as(_tenant("o2", owner=True))
    with patch("product.config.OWNER_LEGACY_SCOPE", False), \
         patch("app.memory.store.memory_event_counts", return_value={"person": 3}), \
         patch("app.scheduled_jobs.activity_counts", return_value={"calendar": 2}):
        body = client.get("/api/admin/health").json()
    assert body["memory_writes_7d"] == {"person": 3}
    assert body["tool_calls_7d"] == {"calendar": 2}
    assert body["web"]["median_reply_ms"] == 1500
    assert body["web"]["notes_hit_rate"] == 0.5
    assert "state" in body["embeddings"]


def test_engine_root_redirects_to_the_web_app():
    with patch("app.memory.store._get_conn", side_effect=RuntimeError("no db")):
        from app.main import app
    client = TestClient(app)
    resp = client.get("/", follow_redirects=False, headers={"host": "pa-box:8000"})
    assert resp.status_code == 307
    assert resp.headers["location"] == "http://pa-box:8080/"
