from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import AUTH_HEADERS


class StubClient:
    async def ping(self):
        return True

    async def aclose(self):
        return None

    async def request(self, method, path, **kwargs):
        return {"count": 0, "results": []}


def build_app(settings):
    return create_app(settings, client_factory=lambda: StubClient())


def test_help_page_requires_auth(settings):
    with TestClient(build_app(settings)) as client:
        response = client.get("/ui/help")
    assert response.status_code == 401


def test_help_page_renders_the_usage_guide(settings):
    with TestClient(build_app(settings)) as client:
        response = client.get("/ui/help", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.text
    # Each page in the workflow order is documented.
    for page in ("Overview", "Tenants", "Content", "Tasks", "Validation", "Activity"):
        assert page in body
    # The destructive path is documented with its three dependency states.
    assert "/ui/delete" in body
    assert "No dependencies recorded" in body
    assert "Dependency check could not complete" in body


def test_help_link_is_in_the_navigation(settings):
    with TestClient(build_app(settings)) as client:
        body = client.get("/ui/help", headers=AUTH_HEADERS).text
    assert '<a href="/ui/help">Help</a>' in body
