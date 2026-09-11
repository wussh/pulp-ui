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
        "base_path": "demo",
        "repository_href": REPO_PATHS[plugin] + "abc/",
    }
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
                "base_path": "demo",
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


def test_every_plugin_distribution_requires_base_path(settings):
    """Pulp requires base_path on EVERY plugin's distribution.

    Verified live against tbs-dev: rpm, python, and ansible each answer
    `{"base_path": ["This field is required."]}` when it is omitted. An earlier
    version of this feature sent base_path only for container, so creating any
    other distribution always failed with a Pulp 400.
    """

    def handler(request):
        return httpx.Response(201, json={"pulp_href": request.url.path + "1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    for plugin in ("rpm", "deb", "python", "ansible", "container"):
        with authed_client(app) as test_client:
            response = test_client.post(
                "/ui/api/content/distribution",
                headers=csrf_headers(test_client),
                json={
                    "domain": "default",
                    "plugin": plugin,
                    "name": "demo",
                    "base_path": "demo",
                    "repository_href": REPO_PATHS[plugin] + "abc/",
                },
            )
        assert response.status_code == 200, plugin

    # And omitting it is refused before any upstream call.
    calls = []

    def recording_handler(request):
        calls.append(request.url.path)
        return httpx.Response(201, json={"pulp_href": "/x/"})

    app = create_app(
        settings, client_factory=lambda: make_client(settings, recording_handler)
    )
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
    assert response.status_code == 400
    assert calls == []


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
                "base_path": "demo",
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
                "base_path": "demo",
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


def _pull_through_handler(remotes, dists, seen=None):
    def handler(request):
        path = request.url.path
        if seen is not None:
            seen.append(path)
        if path == PULL_THROUGH_BASE + "/remotes/container/pull-through/":
            return httpx.Response(200, json={"count": len(remotes), "results": remotes})
        if path == PULL_THROUGH_BASE + "/distributions/container/pull-through/":
            return httpx.Response(200, json={"count": len(dists), "results": dists})
        return httpx.Response(404, json={"detail": "Not found."})

    return handler


def test_pull_through_status_uses_only_two_collections(settings):
    # Live tbs-dev: /repositories/container/pull-through/ 404s. A pull-through
    # distribution binds the remote directly, so the UI must never call it.
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        test_client.get("/ui/api/container/status?domain=default")
    assert seen == [
        PULL_THROUGH_BASE + "/remotes/container/pull-through/",
        PULL_THROUGH_BASE + "/distributions/container/pull-through/",
    ]


def test_pull_through_status_joins_distribution_to_remote(settings):
    # Fixture mirrors the real Pulp records: a pull-through remote has no
    # `upstream_name` field, and the distribution name is `<registry>-proxy` with a
    # bare (unprefixed) base_path. Verified live against tbs-dev.
    remotes = [
        {
            "name": "ghcr",
            "pulp_href": "/pulp/default/api/v3/remotes/container/pull-through/r1/",
            "url": "https://ghcr.io",
        }
    ]
    dists = [
        {
            "name": "ghcr-proxy",
            "pulp_href": "/pulp/default/api/v3/distributions/container/pull-through/d1/",
            "base_path": "ghcr",
            "remote": remotes[0]["pulp_href"],
        }
    ]

    app = create_app(
        settings,
        client_factory=lambda: make_client(
            settings, _pull_through_handler(remotes, dists)
        ),
    )
    with authed_client(app) as test_client:
        response = test_client.get("/ui/api/container/status?domain=default")
    assert response.status_code == 200
    assert response.json()["rows"] == [
        {
            "name": "ghcr-proxy",
            "base_path": "ghcr",
            "upstream_name": "ghcr",
            "upstream_url": "https://ghcr.io",
            "distribution_href": dists[0]["pulp_href"],
            "note": "",
        }
    ]


def test_pull_through_status_reports_distribution_without_remote(settings):
    dists = [
        {
            "name": "orphan",
            "pulp_href": "/pulp/default/api/v3/distributions/container/pull-through/d1/",
            "base_path": "orphan",
            "remote": None,
        }
    ]
    app = create_app(
        settings,
        client_factory=lambda: make_client(settings, _pull_through_handler([], dists)),
    )
    with authed_client(app) as test_client:
        rows = test_client.get("/ui/api/container/status?domain=default").json()["rows"]
    assert len(rows) == 1
    assert rows[0]["name"] == "orphan"
    assert rows[0]["note"]


def test_pull_through_status_reports_unresolvable_remote(settings):
    dists = [
        {
            "name": "ghcr",
            "pulp_href": "/pulp/default/api/v3/distributions/container/pull-through/d1/",
            "base_path": "ghcr",
            "remote": "/pulp/default/api/v3/remotes/container/pull-through/gone/",
        }
    ]
    app = create_app(
        settings,
        client_factory=lambda: make_client(settings, _pull_through_handler([], dists)),
    )
    with authed_client(app) as test_client:
        rows = test_client.get("/ui/api/container/status?domain=default").json()["rows"]
    assert len(rows) == 1
    assert rows[0]["note"]
    assert rows[0]["upstream_url"] == ""


def test_pull_through_add_creates_remote_then_distribution(settings):
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
        PULL_THROUGH_BASE + "/distributions/container/pull-through/",
    ]
    # The distribution carries the remote href directly; no repository field.
    dist_body = seen[1][2]
    assert dist_body["remote"] == (
        PULL_THROUGH_BASE + "/remotes/container/pull-through/1/"
    )
    assert dist_body["base_path"] == "ghcr"
    assert response.json()["task_href"] == "/pulp/default/api/v3/tasks/d1/"


def test_pull_through_add_stops_on_first_failure(settings):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if "/distributions/" in request.url.path:
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
    assert body["completed"] == [
        {"resource": "remote", "pulp_href": PULL_THROUGH_BASE + "/remotes/container/pull-through/1/"}
    ]
    assert seen == [
        PULL_THROUGH_BASE + "/remotes/container/pull-through/",
        PULL_THROUGH_BASE + "/distributions/container/pull-through/",
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


# --- Feature 3: python and ansible include-list editors -----------------------

PYTHON_REMOTE = "/pulp/default/api/v3/remotes/python/python/r1/"
PYTHON_REPO = "/pulp/default/api/v3/repositories/python/python/abc/"
ANSIBLE_REMOTE = "/pulp/default/api/v3/remotes/ansible/collection/r1/"
ANSIBLE_REPO = "/pulp/default/api/v3/repositories/ansible/ansible/abc/"


def test_python_includes_no_change_skips_sync(settings):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "pulp_href": PYTHON_REMOTE,
                    "includes": [{"name": "requests"}],
                },
            )
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/t/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/python/includes",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "remote_href": PYTHON_REMOTE,
                "repository_href": PYTHON_REPO,
                "names": ["requests"],
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is False
    assert body["task_href"] == ""
    assert calls == [("GET", PYTHON_REMOTE)]


def test_python_includes_merge_patches_and_syncs(settings):
    import json

    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        if request.method == "GET":
            # Pulp returns `includes` as plain strings, not dicts. Verified live:
            # the real remote answers ["pyyaml"], and a dict-shaped PATCH is rejected
            # with {"0": ["Not a valid string."]}. The old fixture invented dicts,
            # which is why this shipped broken.
            return httpx.Response(
                200, json={"pulp_href": PYTHON_REMOTE, "includes": ["requests"]}
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={"pulp_href": PYTHON_REMOTE})
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/sync-1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/python/includes",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "remote_href": PYTHON_REMOTE,
                "repository_href": PYTHON_REPO,
                "names": ["boto3"],
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert body["includes"] == ["boto3", "requests"]
    assert body["task_href"] == "/pulp/default/api/v3/tasks/sync-1/"
    patch = [call for call in seen if call[0] == "PATCH"][0]
    assert patch[1] == PYTHON_REMOTE
    # Plain strings — this is the only shape Pulp accepts for `includes`.
    assert patch[2] == {"includes": ["boto3", "requests"]}
    assert seen[-1][1] == PYTHON_REPO + "sync/"


def test_python_includes_rejects_bad_package_name(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/python/includes",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "remote_href": PYTHON_REMOTE,
                "repository_href": PYTHON_REPO,
                "names": ["../../etc/passwd"],
            },
        )
    assert response.status_code == 400
    assert calls == []


def test_ansible_collections_merge_never_sends_empty_requirements(settings):
    import json

    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "pulp_href": ANSIBLE_REMOTE,
                    "requirements_file": "collections:\n  - name: community.general\n",
                },
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={"pulp_href": ANSIBLE_REMOTE})
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/sync-2/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/ansible/collections",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "remote_href": ANSIBLE_REMOTE,
                "repository_href": ANSIBLE_REPO,
                "names": ["ansible.posix"],
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert body["collections"] == ["ansible.posix", "community.general"]
    patch = [call for call in seen if call[0] == "PATCH"][0]
    sent = patch[2]["requirements_file"]
    assert sent.strip() != ""
    assert "- name: ansible.posix" in sent
    assert "- name: community.general" in sent
    assert seen[-1][1] == ANSIBLE_REPO + "sync/"


def test_ansible_collections_no_change_skips_sync(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "pulp_href": ANSIBLE_REMOTE,
                "requirements_file": "collections:\n  - name: community.general\n",
            },
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/ansible/collections",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "remote_href": ANSIBLE_REMOTE,
                "repository_href": ANSIBLE_REPO,
                "names": ["community.general"],
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is False
    assert body["task_href"] == ""
    assert calls == [ANSIBLE_REMOTE]


def test_ansible_collections_rejects_malformed_name(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/ansible/collections",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "remote_href": ANSIBLE_REMOTE,
                "repository_href": ANSIBLE_REPO,
                "names": ["NoDotName"],
            },
        )
    assert response.status_code == 400
    assert calls == []


def test_ansible_requirements_guard_rejects_empty_file(settings):
    # The endpoint cannot reach this state (names are validated non-empty), but the
    # guard is the documented protection against an OOM-inducing full-galaxy sync.
    import pytest as _pytest

    from app.routes.content import _guard_ansible_requirements

    for bad in ["", "collections:\n", "  - name: x"]:
        with _pytest.raises(ValueError):
            _guard_ansible_requirements(bad)
    assert _guard_ansible_requirements("collections:\n  - name: a.b\n")


def test_ansible_merge_preserves_roles_section_and_comments(settings):
    # Regression: the merge used to rebuild the file from parsed collection names,
    # silently deleting a `roles:` section and any comments the operator added.
    import json

    original = (
        "# managed by platform team\n"
        "collections:\n"
        "  - name: community.general\n"
        "\n"
        "roles:\n"
        "  - name: geerlingguy.docker\n"
    )
    seen = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"pulp_href": ANSIBLE_REMOTE, "requirements_file": original}
            )
        if request.method == "PATCH":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"pulp_href": ANSIBLE_REMOTE})
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/s1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/ansible/collections",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "remote_href": ANSIBLE_REMOTE,
                "repository_href": ANSIBLE_REPO,
                "names": ["ansible.posix"],
            },
        )
    assert response.status_code == 200
    sent = seen["body"]["requirements_file"]
    # Operator content survives verbatim.
    assert "# managed by platform team" in sent
    assert "roles:\n  - name: geerlingguy.docker" in sent
    # The new collection was added, sorted among the entries.
    assert "  - name: ansible.posix\n" in sent
    assert "  - name: community.general\n" in sent
    assert sent.index("ansible.posix") < sent.index("community.general")


def test_ansible_merge_refuses_unrecognised_shape(settings):
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "pulp_href": ANSIBLE_REMOTE,
                    # No top-level `collections:` key at all.
                    "requirements_file": "roles:\n  - name: geerlingguy.docker\n",
                },
            )
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/ansible/collections",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "remote_href": ANSIBLE_REMOTE,
                "repository_href": ANSIBLE_REPO,
                "names": ["ansible.posix"],
            },
        )
    assert response.status_code == 400
    assert "directly" in response.json()["error"]
    # Refused before any PATCH or sync.
    assert calls == ["GET"]


def test_ansible_merge_refuses_unparsable_entry(settings):
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "pulp_href": ANSIBLE_REMOTE,
                    "requirements_file": "collections:\n  - src: some/thing\n",
                },
            )
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/ansible/collections",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "remote_href": ANSIBLE_REMOTE,
                "repository_href": ANSIBLE_REPO,
                "names": ["ansible.posix"],
            },
        )
    assert response.status_code == 400
    assert calls == ["GET"]


def test_pull_through_add_uses_bare_base_path_and_proxy_distribution_name(settings):
    # Live convention on tbs-dev: remote `quay` with distribution `quay-proxy` and
    # base_path `quay` (no `container/` prefix). The client URL is /v2/<domain>/quay/.
    import json

    seen = {}

    def handler(request):
        body = json.loads(request.content or b"{}")
        if "/distributions/" in request.url.path:
            seen["dist"] = body
            return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/d1/"})
        seen["remote"] = body
        return httpx.Response(201, json={"pulp_href": request.url.path + "1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/content/pull-through",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "name": "quay",
                "base_path": "quay",
                "upstream_url": "https://mirror.example.com",
            },
        )
    assert response.status_code == 200
    assert seen["remote"]["name"] == "quay"
    assert seen["dist"]["name"] == "quay-proxy"
    assert seen["dist"]["base_path"] == "quay"
    assert not seen["dist"]["base_path"].startswith("container/")
