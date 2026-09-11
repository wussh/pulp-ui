import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client


def ok_handler(payload_by_path):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = payload_by_path.get(request.url.path, {"count": 0, "results": []})
        return httpx.Response(200, json=payload)

    return handler


def test_overview_reports_counts_and_routing_failure(settings, monkeypatch):
    payloads = {
        "/pulp/default/api/v3/status/": {
            "versions": [{"component": "core", "version": "3.116.0"}]
        },
        "/pulp/default/api/v3/domains/": {"count": 3, "results": []},
        "/pulp/default/api/v3/repositories/container/container/": {
            "count": 7,
            "results": [],
        },
        "/pulp/default/api/v3/distributions/container/container/": {
            "count": 7,
            "results": [],
        },
        "/pulp/default/api/v3/tasks/": {"count": 2, "results": []},
    }
    monkeypatch.setattr(
        "app.routes.overview.check_public_route", lambda settings: (False, 404)
    )
    app = create_app(
        settings, client_factory=lambda: make_client(settings, ok_handler(payloads))
    )
    with TestClient(app) as test_client:
        body = test_client.get("/ui/api/overview").json()
    assert body["pulp"]["reachable"] is True
    assert body["pulp"]["core_version"] == "3.116.0"
    assert body["counts"]["domains"] == 3
    assert body["counts"]["container_repositories"] == 7
    assert body["routing"]["public_ok"] is False
    assert body["routing"]["public_status"] == 404
    assert any("public" in warning["code"] for warning in body["warnings"])


def test_overview_handles_unreachable_pulp(settings, monkeypatch):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        "app.routes.overview.check_public_route", lambda settings: (True, 200)
    )
    app = create_app(
        settings, client_factory=lambda: make_client(settings, failing_handler)
    )
    with TestClient(app) as test_client:
        body = test_client.get("/ui/api/overview").json()
    assert body["pulp"]["reachable"] is False
    assert body["counts"] == {}
    assert any("unreachable" in warning["code"] for warning in body["warnings"])


def test_overview_page_renders(settings, monkeypatch):
    monkeypatch.setattr(
        "app.routes.overview.check_public_route", lambda settings: (True, 200)
    )
    app = create_app(
        settings,
        client_factory=lambda: make_client(settings, ok_handler({})),
    )
    with TestClient(app) as test_client:
        response = test_client.get("/ui/")
    assert response.status_code == 200
    assert "Overview" in response.text
