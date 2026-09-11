
from app.logging_config import redact
from app.main import create_app
from app.state import ActivityStore
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


def test_redact_drops_session_secret_and_json_body_keys():
    cleaned = redact(
        {
            "session_secret": "test-session-secret",
            "json_body": {"password": "hunter2"},
            "action": "tenant.setup",
        }
    )
    assert cleaned == {"action": "tenant.setup"}
    assert "test-session-secret" not in str(cleaned)


def test_redact_recurses_into_nested_dicts_and_lists():
    cleaned = redact(
        {
            "action": "tenant.setup",
            "payload": {
                "password": "hunter2",
                "nested": [{"session_secret": "leak"}, {"ok": 1}],
            },
        }
    )
    assert "hunter2" not in str(cleaned)
    assert "leak" not in str(cleaned)
    assert cleaned["payload"]["nested"][1] == {"ok": 1}


def test_record_stamps_rfc3339_utc_timestamp():
    from datetime import datetime

    store = ActivityStore()
    store.record({"action": "x"})
    stamp = store.recent()[0]["timestamp"]
    parsed = datetime.fromisoformat(stamp)
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0


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
