import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client

REPO_PATHS = {
    "rpm": "/pulp/default/api/v3/repositories/rpm/rpm/",
    "deb": "/pulp/default/api/v3/repositories/deb/deb/",
    "python": "/pulp/default/api/v3/repositories/python/python/",
    "ansible": "/pulp/default/api/v3/repositories/ansible/ansible/",
    "container": "/pulp/default/api/v3/repositories/container/container/",
}


@pytest.mark.parametrize("plugin,path", list(REPO_PATHS.items()))
def test_create_repository_uses_plugin_scoped_endpoint(settings, plugin, path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(201, json={"pulp_href": path + "abc/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/repository",
            json={"domain": "default", "plugin": plugin, "name": "demo-repo"},
        )
    assert response.status_code == 200
    assert seen["path"] == path


def test_create_repository_rejects_unknown_plugin(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/repository",
            json={"domain": "default", "plugin": "shell", "name": "x"},
        )
    assert response.status_code == 400


def test_container_distribution_requires_base_path(settings):
    def handler(request):
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/x/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/distribution",
            json={
                "domain": "default",
                "plugin": "container",
                "name": "demo",
                "repository_href": REPO_PATHS["container"] + "abc/",
            },
        )
    assert response.status_code == 400
    assert "base_path" in response.json()["error"]


def test_sync_rejects_non_allowlisted_source(settings):
    def handler(request):
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/x/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            json={
                "domain": "default",
                "plugin": "rpm",
                "repository_href": REPO_PATHS["rpm"] + "abc/",
                "remote_url": "http://169.254.169.254/latest/meta-data/",
            },
        )
    assert response.status_code == 400


def test_sync_returns_task_href(settings):
    def handler(request):
        if request.method == "POST" and "/remotes/" in request.url.path:
            return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/remotes/rpm/rpm/r1/"})
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/xyz/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            json={
                "domain": "default",
                "plugin": "rpm",
                "repository_href": REPO_PATHS["rpm"] + "abc/",
                "remote_url": "https://mirror.example.com/pub/rpm/",
            },
        )
    assert response.status_code == 200
    assert response.json()["task_href"] == "/pulp/default/api/v3/tasks/xyz/"


def test_content_listing_covers_every_plugin(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.get("/ui/api/content?domain=default").json()
    assert set(body["plugins"]) == {"rpm", "deb", "python", "ansible", "container"}


@pytest.mark.parametrize(
    "payload",
    [
        {
            "domain": "default",
            "plugin": "rpm",
            "name": "demo",
            "repository_href": 123,
        },
        {
            "domain": "default",
            "plugin": "container",
            "name": "demo",
            "repository_href": REPO_PATHS["container"] + "abc/",
            "base_path": 5,
        },
    ],
)
def test_non_string_browser_input_is_rejected(settings, payload):
    def handler(request):
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/x/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post("/ui/api/content/distribution", json=payload)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "href",
    [
        "/pulp/other/api/v3/repositories/rpm/rpm/abc/",
        REPO_PATHS["rpm"],
    ],
)
def test_distribution_rejects_foreign_or_collection_href(settings, href):
    def handler(request):
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/x/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/distribution",
            json={
                "domain": "default",
                "plugin": "rpm",
                "name": "demo",
                "repository_href": href,
            },
        )
    assert response.status_code == 400


def test_distribution_records_activity(settings):
    def handler(request):
        return httpx.Response(201, json={"pulp_href": REPO_PATHS["rpm"] + "abc/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/distribution",
            json={
                "domain": "default",
                "plugin": "rpm",
                "name": "demo-dist",
                "repository_href": REPO_PATHS["rpm"] + "abc/",
            },
        )
    assert response.status_code == 200
    entries = app.state.activity.recent()
    assert [entry["action"] for entry in entries] == ["content.distribution.create"]
    assert entries[0]["target"] == "demo-dist"


def test_sync_records_activity(settings):
    def handler(request):
        if "/remotes/" in request.url.path:
            return httpx.Response(201, json={"pulp_href": REPO_PATHS["rpm"] + "r1/"})
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/xyz/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            json={
                "domain": "default",
                "plugin": "rpm",
                "repository_href": REPO_PATHS["rpm"] + "abc/",
                "remote_url": "https://mirror.example.com/pub/rpm/",
            },
        )
    assert response.status_code == 200
    entries = app.state.activity.recent()
    assert [entry["action"] for entry in entries] == ["content.sync"]
    assert entries[0]["result"] == "completed"


def test_sync_failure_still_reports_created_remote(settings):
    import json

    seen = {}

    def handler(request):
        if "/remotes/" in request.url.path:
            seen["remote_name"] = json.loads(request.content)["name"]
            return httpx.Response(201, json={"pulp_href": REPO_PATHS["rpm"] + "r1/"})
        return httpx.Response(500, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            json={
                "domain": "default",
                "plugin": "rpm",
                "repository_href": REPO_PATHS["rpm"] + "abc/",
                "remote_url": "https://mirror.example.com/pub/rpm/",
            },
        )
    assert response.status_code == 400
    body = response.json()
    assert body["failed"] is True
    assert body["remote_href"] == REPO_PATHS["rpm"] + "r1/"
    assert body["error"]
    # A retry must not reuse the constant name that collides on Pulp's uniqueness rule.
    assert seen["remote_name"].startswith("sync-")
    assert seen["remote_name"] != "sync-remote"
    assert body["correlation_id"] in seen["remote_name"]
