import base64

import httpx
from fastapi.testclient import TestClient

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


def csrf_headers(client) -> dict:
    """Fetch a safe GET so the server issues its CSRF token, then return headers."""
    response = client.get("/ui/api/activity?limit=1")
    token = response.headers.get("X-CSRF-Token", "")
    return {"X-CSRF-Token": token}
