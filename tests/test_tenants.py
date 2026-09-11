import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client


def test_plan_setup_reuses_existing_domain(settings):
    def handler(request):
        if request.url.path == "/pulp/default/api/v3/domains/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [
                        {
                            "name": "dummy-alpha",
                            "pulp_href": "/pulp/default/api/v3/domains/x/",
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.post(
            "/ui/api/tenants/plan",
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        ).json()
    assert body["steps"][0] == {
        "action": "reuse",
        "resource": "domain",
        "name": "dummy-alpha",
    }
    assert body["preview"]["creates"] == 2
    assert body["preview"]["reuses"] == 1


def test_apply_setup_stops_on_first_failure(settings):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/users/"):
            return httpx.Response(400, json={"username": ["This field must be unique."]})
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/domains/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 400
    body = response.json()
    assert body["failed"] is True
    assert body["stopped_at"]["resource"] == "user"
    assert "password" not in str(body).lower()
    assert calls[-1] == ("POST", "/pulp/default/api/v3/users/")


def test_plan_reports_probe_warnings_on_4xx(settings):
    def handler(request):
        if request.url.path.endswith("/users/"):
            return httpx.Response(400, json={"username": ["This field must be unique."]})
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/plan",
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["probe_warnings"]) == 1
    warning = body["probe_warnings"][0]
    assert "user" in warning
    assert "400" in warning
    assert "This field must be unique" not in warning
    assert [step["action"] for step in body["steps"]] == [
        "create",
        "create",
        "create",
        "assign",
    ]


def test_plan_propagates_upstream_5xx(settings):
    def handler(request):
        return httpx.Response(500, json={"detail": "internal server error"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/plan",
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 500
    body = response.json()
    assert "steps" not in body


def test_apply_includes_recovery_guidance(settings):
    def handler(request):
        if request.url.path.endswith("/users/"):
            return httpx.Response(400, json={"username": ["This field must be unique."]})
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/domains/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 400
    body = response.json()
    recovery = body["recovery"]
    assert "/pulp/default/api/v3/domains/1/" in recovery
    assert "user" in recovery
    assert "nothing was rolled back" in recovery
    assert "password" not in recovery.lower()


def test_plan_rejects_invalid_domain(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/plan",
            json={"domain": "../admin", "username": "budi-test", "group": "g"},
        )
    assert response.status_code == 400
