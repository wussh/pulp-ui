import httpx
import pytest

from app.main import create_app
from tests.helpers import authed_client, csrf_headers, make_client

# LITERAL endpoint segments verified live against tbs-dev Pulp. The per-kind
# segments differ: deb uses `apt`, python distributions use `pypi`, and ansible
# remotes use `collection`. These strings must not be derived from production code.
REPO_PATHS = {
    "rpm": "/pulp/default/api/v3/repositories/rpm/rpm/",
    "deb": "/pulp/default/api/v3/repositories/deb/apt/",
    "python": "/pulp/default/api/v3/repositories/python/python/",
    "ansible": "/pulp/default/api/v3/repositories/ansible/ansible/",
    "container": "/pulp/default/api/v3/repositories/container/container/",
}

REMOTE_PATHS = {
    "rpm": "/pulp/default/api/v3/remotes/rpm/rpm/",
    "deb": "/pulp/default/api/v3/remotes/deb/apt/",
    "python": "/pulp/default/api/v3/remotes/python/python/",
    "ansible": "/pulp/default/api/v3/remotes/ansible/collection/",
    "container": "/pulp/default/api/v3/remotes/container/container/",
}

DISTRIBUTION_PATHS = {
    "rpm": "/pulp/default/api/v3/distributions/rpm/rpm/",
    "deb": "/pulp/default/api/v3/distributions/deb/apt/",
    "python": "/pulp/default/api/v3/distributions/python/pypi/",
    "ansible": "/pulp/default/api/v3/distributions/ansible/ansible/",
    "container": "/pulp/default/api/v3/distributions/container/container/",
}


@pytest.mark.parametrize("plugin,path", list(REPO_PATHS.items()))
def test_create_repository_uses_plugin_scoped_endpoint(settings, plugin, path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(201, json={"pulp_href": path + "abc/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/repository",
            headers=csrf_headers(test_client),
            json={"domain": "default", "plugin": plugin, "name": "demo-repo"},
        )
    assert response.status_code == 200
    assert seen["path"] == path


@pytest.mark.parametrize("plugin,path", list(DISTRIBUTION_PATHS.items()))
def test_create_distribution_uses_plugin_scoped_endpoint(settings, plugin, path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(201, json={"pulp_href": path + "abc/"})

    payload = {
        "domain": "default",
        "plugin": plugin,
        "name": "demo-dist",
        "repository_href": REPO_PATHS[plugin] + "abc/",
    }
    if plugin == "container":
        payload["base_path"] = "demo"
    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/distribution",
            headers=csrf_headers(test_client),
            json=payload,
        )
    assert response.status_code == 200
    assert seen["path"] == path


def test_async_distribution_creation_surfaces_task_href(settings):
    # Pulp distribution creation answers 202 with a task and no pulp_href. Dropping
    # the task href would leave acceptance criterion 4 unmet for this operation.
    task = "/pulp/default/api/v3/tasks/dist-1/"

    def handler(request):
        return httpx.Response(202, json={"task": task})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/content/distribution",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "plugin": "rpm",
                "name": "demo-dist",
                "repository_href": REPO_PATHS["rpm"] + "abc/",
            },
        ).json()
    assert body["task_href"] == task
    assert body["pulp_href"] == ""


@pytest.mark.parametrize("plugin,path", list(REMOTE_PATHS.items()))
def test_sync_uses_plugin_scoped_endpoints(settings, plugin, path):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if "/remotes/" in request.url.path:
            return httpx.Response(201, json={"pulp_href": path + "r1/"})
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/xyz/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "plugin": plugin,
                "repository_href": REPO_PATHS[plugin] + "abc/",
                "remote_url": "https://mirror.example.com/pub/rpm/",
            },
        )
    assert response.status_code == 200
    assert seen[0] == path
    assert seen[1] == REPO_PATHS[plugin] + "abc/sync/"


def test_deb_repository_href_uses_apt_segment(settings):
    from app.routes.content import _require_repository_href

    href = REPO_PATHS["deb"] + "abc/"
    assert _require_repository_href("default", "deb", href) == href


def test_python_distribution_does_not_require_base_path(settings):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(201, json={"pulp_href": DISTRIBUTION_PATHS["python"] + "1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/distribution",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "plugin": "python",
                "name": "demo",
                "repository_href": REPO_PATHS["python"] + "abc/",
            },
        )
    assert response.status_code == 200
    assert seen["path"] == DISTRIBUTION_PATHS["python"]


def test_create_repository_rejects_unknown_plugin(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/repository",
            headers=csrf_headers(test_client),
            json={"domain": "default", "plugin": "shell", "name": "x"},
        )
    assert response.status_code == 400


def test_container_distribution_requires_base_path(settings):
    def handler(request):
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/x/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/distribution",
            headers=csrf_headers(test_client),
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
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            headers=csrf_headers(test_client),
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
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            headers=csrf_headers(test_client),
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
    with authed_client(app) as test_client:
        body = test_client.get("/ui/api/content?domain=default").json()
    assert set(body["plugins"]) == {"rpm", "deb", "python", "ansible", "container"}
    assert all(
        plugin_data.get("error") in (None, "") for plugin_data in body["plugins"].values()
    )


def test_content_listing_isolates_single_plugin_failure(settings):
    # Every deb endpoint 404s. The other four plugins must still load and the response
    # must stay 200 with an error entry naming the failed plugin. Matching on the deb
    # prefix (not one exact path) keeps this a real regression guard: the pre-fix code
    # requested deb/deb, which also 404s here, and had no isolation so it 502'd.
    def handler(request):
        if "/repositories/deb/" in request.url.path:
            return httpx.Response(404, json={"detail": "Not found."})
        return httpx.Response(
            200,
            json={
                "count": 1,
                "results": [{"name": "r1", "pulp_href": request.url.path + "1/"}],
            },
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.get("/ui/api/content?domain=default")
    assert response.status_code == 200
    body = response.json()
    assert body["plugins"]["deb"]["error"]
    assert body["plugins"]["deb"]["repositories"] == []
    assert body["plugins"]["rpm"]["repositories"] == [
        {
            "name": "r1",
            "pulp_href": "/pulp/default/api/v3/repositories/rpm/rpm/1/",
        }
    ]


def test_content_listing_fails_only_when_every_plugin_fails(settings):
    def handler(request):
        return httpx.Response(500, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.get("/ui/api/content?domain=default")
    assert response.status_code == 502
    assert response.json()["correlation_id"]


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
    with authed_client(app) as test_client:
        response = test_client.post("/ui/api/content/distribution", headers=csrf_headers(test_client),
        json=payload)
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
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/distribution",
            headers=csrf_headers(test_client),
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
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/distribution",
            headers=csrf_headers(test_client),
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
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            headers=csrf_headers(test_client),
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
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            headers=csrf_headers(test_client),
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


# --- Feature 1: publish and link ---------------------------------------------

PUBLICATION_PATHS = {
    "rpm": "/pulp/default/api/v3/publications/rpm/rpm/",
    "deb": "/pulp/default/api/v3/publications/deb/apt/",
    "python": "/pulp/default/api/v3/publications/python/pypi/",
}


@pytest.mark.parametrize("plugin,path", list(PUBLICATION_PATHS.items()))
def test_publish_posts_to_plugin_publication_endpoint(settings, plugin, path):
    seen = {}

    def handler(request):
        if request.method == "GET":
            seen["get"] = request.url.path
            return httpx.Response(
                200, json={"latest_version_href": "/pulp/default/api/v3/versions/1/"}
            )
        seen["post"] = request.url.path
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/pub-1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/publish",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "plugin": plugin,
                "repository_href": REPO_PATHS[plugin] + "abc/",
            },
        )
    assert response.status_code == 200
    assert seen["get"] == REPO_PATHS[plugin] + "abc/"
    assert seen["post"] == path
    assert response.json()["task_href"] == "/pulp/default/api/v3/tasks/pub-1/"


@pytest.mark.parametrize("plugin", ["ansible", "container"])
def test_publish_rejects_plugins_without_publication_endpoint(settings, plugin):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"latest_version_href": "x"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/publish",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "plugin": plugin,
                "repository_href": REPO_PATHS[plugin] + "abc/",
            },
        )
    assert response.status_code == 400
    assert "publication" in response.json()["error"]
    assert calls == []


def test_publish_records_activity(settings):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"latest_version_href": "/v/1/"})
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/pub-1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/publish",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "plugin": "rpm",
                "repository_href": REPO_PATHS["rpm"] + "abc/",
            },
        )
    assert response.status_code == 200
    entries = app.state.activity.recent()
    assert [entry["action"] for entry in entries] == ["content.publish"]
    assert entries[0]["target"] == REPO_PATHS["rpm"] + "abc/"


def test_link_patches_distribution_with_publication(settings):
    import json

    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/link-1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    pub = PUBLICATION_PATHS["rpm"] + "p1/"
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/link",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "plugin": "rpm",
                "distribution_href": DISTRIBUTION_PATHS["rpm"] + "d1/",
                "publication_href": pub,
            },
        )
    assert response.status_code == 200
    assert seen["path"] == DISTRIBUTION_PATHS["rpm"] + "d1/"
    assert seen["body"] == {"repository": None, "publication": pub}
    assert response.json()["task_href"] == "/pulp/default/api/v3/tasks/link-1/"


def test_link_rejects_publication_from_other_domain(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(202, json={"task": "t"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/link",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "plugin": "rpm",
                "distribution_href": DISTRIBUTION_PATHS["rpm"] + "d1/",
                "publication_href": "/pulp/other/api/v3/publications/rpm/rpm/p1/",
            },
        )
    assert response.status_code == 400
    assert calls == []


# --- Feature 2: container pull-through ----------------------------------------

PULL_THROUGH_BASE = "/pulp/default/api/v3"


def _pull_through_handler(remotes, repos, dists):
    def handler(request):
        path = request.url.path
        if path == PULL_THROUGH_BASE + "/remotes/container/pull-through/":
            return httpx.Response(200, json={"count": len(remotes), "results": remotes})
        if path == PULL_THROUGH_BASE + "/repositories/container/pull-through/":
            return httpx.Response(200, json={"count": len(repos), "results": repos})
        if path == PULL_THROUGH_BASE + "/distributions/container/pull-through/":
            return httpx.Response(200, json={"count": len(dists), "results": dists})
        return httpx.Response(404, json={"detail": "Not found."})

    return handler


def test_pull_through_status_joins_distribution_to_remote(settings):
    remotes = [
        {
            "name": "ghcr",
            "pulp_href": "/pulp/default/api/v3/remotes/container/pull-through/r1/",
            "url": "https://ghcr.io",
            "upstream_name": "ghcr",
        }
    ]
    repos = [
        {
            "name": "ghcr",
            "pulp_href": "/pulp/default/api/v3/repositories/container/pull-through/p1/",
            "remote": remotes[0]["pulp_href"],
        }
    ]
    dists = [
        {
            "name": "ghcr",
            "pulp_href": "/pulp/default/api/v3/distributions/container/pull-through/d1/",
            "base_path": "ghcr",
            "repository": repos[0]["pulp_href"],
        }
    ]

    app = create_app(
        settings,
        client_factory=lambda: make_client(
            settings, _pull_through_handler(remotes, repos, dists)
        ),
    )
    with authed_client(app) as test_client:
        response = test_client.get("/ui/api/container/status?domain=default")
    assert response.status_code == 200
    assert response.json()["rows"] == [
        {
            "name": "ghcr",
            "base_path": "ghcr",
            "upstream_name": "ghcr",
            "upstream_url": "https://ghcr.io",
            "distribution_href": dists[0]["pulp_href"],
            "note": "",
        }
    ]


def test_pull_through_status_reports_distribution_without_repository(settings):
    dists = [
        {
            "name": "orphan",
            "pulp_href": "/pulp/default/api/v3/distributions/container/pull-through/d1/",
            "base_path": "orphan",
            "repository": None,
        }
    ]
    app = create_app(
        settings,
        client_factory=lambda: make_client(
            settings, _pull_through_handler([], [], dists)
        ),
    )
    with authed_client(app) as test_client:
        rows = test_client.get("/ui/api/container/status?domain=default").json()["rows"]
    assert len(rows) == 1
    assert rows[0]["name"] == "orphan"
    assert rows[0]["note"]


def test_pull_through_status_reports_unresolvable_remote(settings):
    repos = [
        {
            "name": "ghcr",
            "pulp_href": "/pulp/default/api/v3/repositories/container/pull-through/p1/",
            "remote": "/pulp/default/api/v3/remotes/container/pull-through/gone/",
        }
    ]
    dists = [
        {
            "name": "ghcr",
            "pulp_href": "/pulp/default/api/v3/distributions/container/pull-through/d1/",
            "base_path": "ghcr",
            "repository": repos[0]["pulp_href"],
        }
    ]
    app = create_app(
        settings,
        client_factory=lambda: make_client(
            settings, _pull_through_handler([], repos, dists)
        ),
    )
    with authed_client(app) as test_client:
        rows = test_client.get("/ui/api/container/status?domain=default").json()["rows"]
    assert len(rows) == 1
    assert rows[0]["note"]
    assert rows[0]["upstream_url"] == ""


def test_pull_through_add_creates_remote_repo_distribution_in_order(settings):
    import json

    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        if "/distributions/" in request.url.path:
            return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/d1/"})
        return httpx.Response(201, json={"pulp_href": request.url.path + "1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/pull-through",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "name": "ghcr",
                "base_path": "ghcr",
                "upstream_url": "https://mirror.example.com",
            },
        )
    assert response.status_code == 200
    assert [call[1] for call in seen] == [
        PULL_THROUGH_BASE + "/remotes/container/pull-through/",
        PULL_THROUGH_BASE + "/repositories/container/pull-through/",
        PULL_THROUGH_BASE + "/distributions/container/pull-through/",
    ]
    assert seen[2][2]["repository"] == (
        PULL_THROUGH_BASE + "/repositories/container/pull-through/1/"
    )
    assert response.json()["task_href"] == "/pulp/default/api/v3/tasks/d1/"


def test_pull_through_add_stops_on_first_failure(settings):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if "/repositories/" in request.url.path:
            return httpx.Response(400, json={"name": ["This field must be unique."]})
        return httpx.Response(201, json={"pulp_href": request.url.path + "1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/pull-through",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "name": "ghcr",
                "base_path": "ghcr",
                "upstream_url": "https://mirror.example.com",
            },
        )
    assert response.status_code == 400
    body = response.json()
    assert body["failed"] is True
    assert body["completed"][0]["resource"] == "remote"
    assert seen == [
        PULL_THROUGH_BASE + "/remotes/container/pull-through/",
        PULL_THROUGH_BASE + "/repositories/container/pull-through/",
    ]


def test_pull_through_add_rejects_non_allowlisted_upstream(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(201, json={"pulp_href": "x"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/pull-through",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "name": "ghcr",
                "base_path": "ghcr",
                "upstream_url": "https://evil.example.org",
            },
        )
    assert response.status_code == 400
    assert calls == []


def test_pull_through_status_records_no_new_routes_under_container_container(settings):
    # The regular container/container mapping must remain untouched.
    from app.endpoints import resource_segment

    assert resource_segment("container", "repository") == "container/container"
