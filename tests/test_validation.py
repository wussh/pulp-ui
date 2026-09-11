import httpx

from app.main import create_app
from tests.helpers import authed_client, csrf_headers, make_client

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
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", headers=csrf_headers(test_client),
            json={"domain": "default"}
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
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", headers=csrf_headers(test_client),
            json={"domain": "default"}
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
    with authed_client(app) as test_client:
        run = test_client.post(
            "/ui/api/validation/run", headers=csrf_headers(test_client),
            json={"domain": "default"}
        ).json()
        body = test_client.post(
            "/ui/api/validation/cleanup", headers=csrf_headers(test_client),
            json={"run_id": run["run_id"]}
        ).json()
    assert deleted == [REPO_COLLECTION + "1/"]
    assert body["deleted"] == [REPO_COLLECTION + "1/"]


def test_cleanup_rejects_unknown_run(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/validation/cleanup", headers=csrf_headers(test_client),
            json={"run_id": "nope"}
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
    with authed_client(app) as test_client:
        run = test_client.post(
            "/ui/api/validation/run", headers=csrf_headers(test_client),
            json={"domain": "default"}
        ).json()
        test_client.post("/ui/api/validation/cleanup", headers=csrf_headers(test_client),
        json={"run_id": run["run_id"]})
        second = test_client.post(
            "/ui/api/validation/cleanup", headers=csrf_headers(test_client),
            json={"run_id": run["run_id"]}
        )
    assert second.status_code == 400


def test_isolation_fails_when_foreign_domain_leaks_created_href(settings):
    # The foreign domain returns the created resource's exact href while the run's
    # own domain does not list it. The pre-fix assertion cross-read before creation
    # and matched on name, so it reported PASS here; this is the regression guard.
    def handler(request):
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(201, json={"pulp_href": REPO_COLLECTION + "1/"})
        if request.url.path == "/pulp/dummy-beta/api/v3/repositories/rpm/rpm/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [{"pulp_href": REPO_COLLECTION + "1/"}],
                },
            )
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", headers=csrf_headers(test_client),
            json={"domain": "default"}
        ).json()
    statuses = {item["name"]: item["status"] for item in body["assertions"]}
    assert statuses["domain_isolation"] == "FAIL"
    evidence = {
        item["name"]: item["evidence"] for item in body["assertions"]
    }["domain_isolation"]
    assert "foreign_domain_leak=True" in evidence


def test_isolation_follows_pagination_on_foreign_listing(settings):
    # The leaked href is only on page 2. A single-page membership test reports PASS
    # here; following `next` is the regression guard for that false PASS.
    foreign = "/pulp/dummy-beta/api/v3/repositories/rpm/rpm/"

    def handler(request):
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(201, json={"pulp_href": REPO_COLLECTION + "1/"})
        if request.url.path == foreign:
            if request.url.params.get("offset") == "1":
                return httpx.Response(
                    200,
                    json={
                        "count": 2,
                        "next": None,
                        "results": [{"pulp_href": REPO_COLLECTION + "1/"}],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "next": f"{foreign}?offset=1",
                    "results": [{"pulp_href": foreign + "other/"}],
                },
            )
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", headers=csrf_headers(test_client),
            json={"domain": "default"}
        ).json()
    statuses = {item["name"]: item["status"] for item in body["assertions"]}
    assert statuses["domain_isolation"] == "FAIL"
    evidence = {item["name"]: item["evidence"] for item in body["assertions"]}[
        "domain_isolation"
    ]
    assert "foreign_domain_leak=True" in evidence


def test_isolation_reports_fail_when_pagination_is_truncated(settings):
    # Upstream always advertises a further page. The bounded walk stops; a search
    # that did not finish must never be reported as PASS.
    foreign = "/pulp/dummy-beta/api/v3/repositories/rpm/rpm/"

    def handler(request):
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(201, json={"pulp_href": REPO_COLLECTION + "1/"})
        if request.url.path == foreign:
            return httpx.Response(
                200,
                json={
                    "count": 999,
                    "next": f"{foreign}?offset=next",
                    "results": [],
                },
            )
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", headers=csrf_headers(test_client),
            json={"domain": "default"}
        ).json()
    statuses = {item["name"]: item["status"] for item in body["assertions"]}
    assert statuses["domain_isolation"] == "FAIL"
    evidence = {item["name"]: item["evidence"] for item in body["assertions"]}[
        "domain_isolation"
    ]
    assert "truncated" in evidence


def test_isolation_fails_when_creation_fails(settings):
    def handler(request):
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(400, json={"name": ["This field must be unique."]})
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", headers=csrf_headers(test_client),
            json={"domain": "default"}
        ).json()
    statuses = {item["name"]: item["status"] for item in body["assertions"]}
    assert statuses["domain_isolation"] == "FAIL"
    evidence = {
        item["name"]: item["evidence"] for item in body["assertions"]
    }["domain_isolation"]
    assert "could not be verified" in evidence


def test_cleanup_rejects_unknown_run_records_failed_activity(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/validation/cleanup", headers=csrf_headers(test_client),
            json={"run_id": "nope"}
        )
    assert response.status_code == 400
    entries = app.state.activity.recent()
    assert [entry["result"] for entry in entries] == ["failed"]
