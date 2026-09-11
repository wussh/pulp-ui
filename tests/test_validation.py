import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client

REPO_COLLECTION = "/pulp/default/api/v3/repositories/rpm/rpm/"


def test_validation_records_each_assertion_and_resource(settings):
    def handler(request):
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(201, json={"pulp_href": REPO_COLLECTION + "1/"})
        if request.method == "GET" and request.url.path == REPO_COLLECTION:
            return httpx.Response(
                200,
                json={"count": 1, "results": [{"pulp_href": REPO_COLLECTION + "1/"}]},
            )
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", json={"domain": "default"}
        ).json()
    assert body["run_id"]
    assert [item["name"] for item in body["assertions"]] == list(
        ("domain_isolation", "resource_creation", "resource_listing")
    )
    assert all(item["status"] in {"PASS", "FAIL"} for item in body["assertions"])
    assert body["resources"] == [REPO_COLLECTION + "1/"]


def test_validation_reports_failure_when_creation_fails(settings):
    def handler(request):
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(400, json={"name": ["This field must be unique."]})
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", json={"domain": "default"}
        ).json()
    statuses = {item["name"]: item["status"] for item in body["assertions"]}
    assert statuses["resource_creation"] == "FAIL"
    assert statuses["resource_listing"] == "FAIL"


def test_cleanup_only_deletes_recorded_resources(settings):
    deleted = []

    def handler(request):
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(201, json={"pulp_href": REPO_COLLECTION + "1/"})
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        run = test_client.post(
            "/ui/api/validation/run", json={"domain": "default"}
        ).json()
        body = test_client.post(
            "/ui/api/validation/cleanup", json={"run_id": run["run_id"]}
        ).json()
    assert deleted == [REPO_COLLECTION + "1/"]
    assert body["deleted"] == [REPO_COLLECTION + "1/"]


def test_cleanup_rejects_unknown_run(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/validation/cleanup", json={"run_id": "nope"}
        )
    assert response.status_code == 400


def test_cleanup_run_is_single_use(settings):
    def handler(request):
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(201, json={"pulp_href": REPO_COLLECTION + "1/"})
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        run = test_client.post(
            "/ui/api/validation/run", json={"domain": "default"}
        ).json()
        test_client.post("/ui/api/validation/cleanup", json={"run_id": run["run_id"]})
        second = test_client.post(
            "/ui/api/validation/cleanup", json={"run_id": run["run_id"]}
        )
    assert second.status_code == 400
