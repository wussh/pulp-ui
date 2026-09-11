import base64

import httpx
from fastapi.testclient import TestClient

from app.k8s import SecretError
from app.pulp import PulpClient

AUTH_HEADERS = {
    "Authorization": "Basic "
    + base64.b64encode(b"operator:s3cret").decode("ascii")
}


def authed_client(app) -> TestClient:
    """TestClient carrying the configured operator's Basic Auth on every request."""
    return TestClient(app, headers=AUTH_HEADERS)


def make_client(settings, handler) -> PulpClient:
    client = PulpClient(settings)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=settings.pulp_internal_url,
        follow_redirects=False,
    )
    return client


def make_tenant_client(settings, handler, credentials):
    """A PulpClient authenticating as `credentials` over a mock transport.

    Mirrors make_client, but exercises the explicit-credential constructor so the
    Authorization header on the tenant read is the real one for that credential.
    """
    client = PulpClient(settings, credentials=credentials)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=settings.pulp_internal_url,
        follow_redirects=False,
    )
    return client


def credentials_header(credentials) -> str:
    """The Authorization header PulpClient would send for a credential.

    Used to assert the tenant read uses a different credential from the admin one
    without asserting the literal secret in a failure message.
    """
    username, password = credentials
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


class FakeSecretsStore:
    """In-memory stand-in for SecretsStore, used by route tests.

    Records every applied Secret body so a test can assert the exact values the
    UI sends. `tenant_credentials` maps a domain to a (username, password) pair.
    """

    def __init__(self, tenant_credentials=None, everest=None, existing=None):
        self.tenant_credentials = dict(tenant_credentials or {})
        self.everest = everest
        self.existing = dict(existing or {})
        self.applied: list[dict] = []
        self.raised: SecretError | None = None
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def get_secret(self, name, *, namespace=None, correlation_id=""):
        if self.raised:
            raise self.raised
        return self.existing.get(name)

    async def apply_secret(self, name, values, *, namespace=None, correlation_id=""):
        if self.raised:
            raise self.raised
        self.applied.append({"name": name, "values": dict(values)})
        self.existing[name] = {"data": {}}

    async def get_tenant_credential(self, domain, *, correlation_id=""):
        if self.raised:
            raise self.raised
        return self.tenant_credentials.get(domain)

    async def set_tenant_credential(self, domain, username, password, *, correlation_id=""):
        if self.raised:
            raise self.raised
        self.tenant_credentials[domain] = (username, password)

    async def read_everest_db_credentials(self, *, correlation_id=""):
        if self.raised:
            raise self.raised
        return self.everest


def csrf_headers(client) -> dict:
    """Fetch a safe GET so the server issues its CSRF token, then return headers."""
    response = client.get("/ui/api/activity?limit=1")
    token = response.headers.get("X-CSRF-Token", "")
    return {"X-CSRF-Token": token}
