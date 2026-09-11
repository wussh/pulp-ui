import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client


def test_task_list_filters_by_state(settings):
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "count": 1,
                "results": [
                    {"state": "failed", "pulp_href": "/pulp/default/api/v3/tasks/1/"}
                ],
            },
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.get("/ui/api/tasks?domain=default&state=failed").json()
    assert body["tasks"][0]["state"] == "failed"
    assert seen["params"]["state"] == "failed"


def test_task_list_rejects_unknown_state(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.get("/ui/api/tasks?domain=default&state=bogus")
    assert response.status_code == 400


def test_task_detail_rejects_foreign_href(settings):
    def handler(request):
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.get(
            "/ui/api/tasks/detail?domain=default&href=/pulp/dummy-beta/api/v3/tasks/1/"
        )
    assert response.status_code == 400


def test_task_detail_truncates_progress_reports(settings):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "state": "running",
                "progress_reports": [{"message": "step", "code": "x"}] * 40,
            },
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.get(
            "/ui/api/tasks/detail?domain=default&href=/pulp/default/api/v3/tasks/1/"
        ).json()
    assert len(body["progress_reports"]) <= 20
    assert body["state"] == "running"


def test_task_detail_accepts_non_default_domain_href(settings):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"state": "completed"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.get(
            "/ui/api/tasks/detail?domain=dummy-beta&href=/pulp/dummy-beta/api/v3/tasks/1/"
        )
    assert response.status_code == 200
    assert response.json()["state"] == "completed"
    assert seen["path"] == "/pulp/dummy-beta/api/v3/tasks/1/"


def test_task_detail_omitted_domain_rejects_non_default_href(settings):
    def handler(request):
        return httpx.Response(200, json={"state": "completed"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.get(
            "/ui/api/tasks/detail?href=/pulp/dummy-beta/api/v3/tasks/1/"
        )
    assert response.status_code == 400


def test_task_detail_rejects_mismatched_domain(settings):
    def handler(request):
        return httpx.Response(200, json={"state": "completed"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.get(
            "/ui/api/tasks/detail?domain=dummy-alpha&href=/pulp/dummy-beta/api/v3/tasks/1/"
        )
    assert response.status_code == 400


def test_task_detail_error_shape_is_string_coerced(settings):
    def handler(request):
        return httpx.Response(
            200,
            json={"state": "failed", "error": {"code": ["bad"], "description": ["boom"]}},
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.get(
            "/ui/api/tasks/detail?domain=default&href=/pulp/default/api/v3/tasks/1/"
        ).json()
    assert isinstance(body["error"]["code"], str)
    assert isinstance(body["error"]["description"], str)
    assert body["error"]["description"] == "['boom']"


def test_task_detail_page_passes_domain_to_poller(settings):
    def handler(request):
        return httpx.Response(200, json={"state": "running"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.get(
            "/ui/tasks/detail?domain=dummy-beta&href=/pulp/dummy-beta/api/v3/tasks/1/"
        )
    assert response.status_code == 200
    assert 'pollTask("dummy-beta", "/pulp/dummy-beta/api/v3/tasks/1/"' in response.text
