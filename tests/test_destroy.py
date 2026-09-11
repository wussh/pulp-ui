import httpx
import pytest

from app.main import create_app
from tests.helpers import authed_client, csrf_headers, make_client

REPO_HREF = "/pulp/default/api/v3/repositories/rpm/rpm/abc/"


def test_preview_returns_current_resource_details(settings):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"name": "demo-repo", "pulp_href": REPO_HREF})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        body = test_client.get(
            f"/ui/delete/preview?domain=default&href={REPO_HREF}"
        ).json()
    assert body["name"] == "demo-repo"
    assert body["type"] == "repositories"
    assert body["domain"] == "default"
    assert body["href"] == REPO_HREF


def test_delete_requires_confirmation(settings):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"name": "demo-repo", "pulp_href": REPO_HREF})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/delete",
            headers=csrf_headers(test_client),
            json={"domain": "default", "href": REPO_HREF, "confirmed": False},
        )
    assert response.status_code == 400
    assert "DELETE" not in calls


def test_delete_refetches_then_deletes_exact_href(settings):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"name": "demo-repo", "pulp_href": REPO_HREF})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/delete",
            headers=csrf_headers(test_client),
            json={"domain": "default", "href": REPO_HREF, "confirmed": True},
        ).json()
    assert seen == [("GET", REPO_HREF), ("GET", REPO_HREF), ("DELETE", REPO_HREF)]
    assert body["deleted"] == REPO_HREF


def test_delete_rejects_href_outside_requested_domain(settings):
    def handler(request):
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/delete",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "href": "/pulp/dummy-beta/api/v3/repositories/rpm/rpm/abc/",
                "confirmed": True,
            },
        )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"domain": "default", "href": REPO_HREF},
        {"domain": "default", "href": REPO_HREF, "confirmed": "false"},
        {"domain": "default", "href": REPO_HREF, "confirmed": 1},
        {"domain": "default", "href": REPO_HREF, "confirmed": None},
    ],
    ids=["absent", "string-false", "int-one", "null"],
)
def test_delete_rejects_non_true_confirmation_shapes(settings, payload):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"name": "demo-repo", "pulp_href": REPO_HREF})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/delete",
            headers=csrf_headers(test_client),
            json=payload,
        )
    assert response.status_code == 400
    assert calls == []


def test_delete_rejects_collection_href(settings):
    def handler(request):
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/delete",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "href": "/pulp/default/api/v3/repositories/rpm/rpm/",
                "confirmed": True,
            },
        )
    assert response.status_code == 400
