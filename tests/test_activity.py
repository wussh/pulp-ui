
from app.logging_config import redact
from app.main import create_app
from tests.helpers import authed_client


def test_redact_removes_credentials_and_bodies():
    record = {
        "action": "tenant.setup",
        "authorization": "Basic YWRtaW46c2VjcmV0",
        "password": "hunter2",
        "body": {"password": "hunter2"},
        "correlation_id": "abc",
    }
    cleaned = redact(record)
    assert cleaned["action"] == "tenant.setup"
    assert cleaned["correlation_id"] == "abc"
    assert "authorization" not in cleaned
    assert "password" not in cleaned
    assert "body" not in cleaned
    assert "hunter2" not in str(cleaned)
    assert "Basic " not in str(cleaned)


def test_redact_is_case_insensitive():
    cleaned = redact({"Authorization": "x", "PASSWORD": "y", "action": "z"})
    assert cleaned == {"action": "z"}


def test_activity_endpoint_lists_recorded_entries(settings):
    class StubClient:
        async def ping(self):
            return True

        async def aclose(self):
            return None

    app = create_app(settings, client_factory=lambda: StubClient())
    with authed_client(app) as test_client:
        app.state.activity.record(
            {
                "correlation_id": "c1",
                "operator": "operator",
                "action": "validation.run",
                "target": "run-1",
                "result": "completed",
            }
        )
        body = test_client.get("/ui/api/activity").json()
    assert body["entries"][0]["action"] == "validation.run"
    assert body["entries"][0]["correlation_id"] == "c1"


def test_activity_limit_is_capped(settings):
    class StubClient:
        async def ping(self):
            return True

        async def aclose(self):
            return None

    app = create_app(settings, client_factory=lambda: StubClient())
    with authed_client(app) as test_client:
        for index in range(300):
            app.state.activity.record(
                {
                    "correlation_id": str(index),
                    "operator": "operator",
                    "action": "x",
                    "target": "y",
                    "result": "completed",
                }
            )
        body = test_client.get("/ui/api/activity?limit=500").json()
    assert len(body["entries"]) <= 200
