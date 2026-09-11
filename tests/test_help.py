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
    # Every consumable content type has a client command.
    for client in ("dnf", "apt", "pip", "ansible-galaxy", "docker pull"):
        assert client in body
    assert "/v2/default/" in body


def test_help_page_absorbs_the_admin_guide(settings):
    with TestClient(build_app(settings)) as client:
        response = client.get("/ui/help", headers=AUTH_HEADERS)
    body = response.text
    # The guide's operational facts survive the merge, in English.
    for section in (
        "Configuring Pulp from this UI",
        "What still needs a script",
        "Sync policy",
        "Maintenance",
    ):
        assert section in body
    # Concrete facts a reader relies on.
    assert "on_demand" in body and "immediate" in body
    assert "25 minutes" in body
    assert "requirements_file" in body
    assert "RustFS" in body
    # Still-needed scripts are named with their paths.
    assert "scripts/pulp/pulp-domains-setup.py" in body
    assert "manifests/pulp/scripts/setup-secrets.sh" in body
    assert "task/pulp-domains-multitenancy-simulation/" in body
    # The isolation-check caveat is stated, not glossed over.
    assert "admin" in body
    assert "non-superuser" in body


def test_help_page_contains_no_plaintext_credential(settings):
    with TestClient(build_app(settings)) as client:
        body = client.get("/ui/help", headers=AUTH_HEADERS).text
    assert "Pulp@D3k4cloud!" not in body
    assert "&lt;admin-password&gt;" in body


def test_help_link_is_in_the_navigation(settings):
    with TestClient(build_app(settings)) as client:
        body = client.get("/ui/help", headers=AUTH_HEADERS).text
    assert '<a href="/ui/help">Help</a>' in body
    # The separate Admin page is gone.
    assert "/ui/admin-guide" not in body


def test_admin_guide_route_is_removed(settings):
    with TestClient(build_app(settings)) as client:
        response = client.get("/ui/admin-guide", headers=AUTH_HEADERS)
    assert response.status_code == 404
