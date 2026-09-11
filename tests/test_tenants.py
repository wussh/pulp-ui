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
