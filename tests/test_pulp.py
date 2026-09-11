import httpx
import pytest

from app.pulp import PulpClient, PulpError


def build_client(settings, handler):
    client = PulpClient(settings)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=settings.pulp_internal_url,
        follow_redirects=False,
    )
    return client


async def test_request_sends_admin_credentials_and_decodes_json(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"versions": [{"component": "core"}]})

    client = build_client(settings, handler)
    body = await client.request("GET", "/pulp/default/api/v3/status/")
    assert body["versions"][0]["component"] == "core"
    assert seen["url"] == "http://pulp-api-svc:24817/pulp/default/api/v3/status/"
    assert seen["auth"].startswith("Basic ")
    await client.aclose()


async def test_request_sends_json_body(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(201, json={"pulp_href": "/x/"})

    client = build_client(settings, handler)
    await client.request("POST", "/pulp/default/api/v3/domains/", json_body={"name": "d"})
    assert seen["body"] == b'{"name":"d"}'
    await client.aclose()


async def test_request_rejects_absolute_url(settings):
    client = build_client(settings, lambda request: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        await client.request("GET", "http://169.254.169.254/latest/meta-data/")
    await client.aclose()


async def test_request_rejects_path_outside_pulp_prefix(settings):
    client = build_client(settings, lambda request: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        await client.request("GET", "/api/v3/status/")
    await client.aclose()


async def test_4xx_maps_to_pulp_error_without_credential_leak(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"name": ["This field is required."]})

    client = build_client(settings, handler)
    with pytest.raises(PulpError) as excinfo:
        await client.request("POST", "/pulp/default/api/v3/users/", json_body={})
    assert excinfo.value.status_code == 400
    assert "test-admin-password" not in str(excinfo.value)
    assert "Basic " not in str(excinfo.value)
    await client.aclose()


async def test_5xx_maps_to_generic_message(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Traceback: secret internal detail")

    client = build_client(settings, handler)
    with pytest.raises(PulpError) as excinfo:
        await client.request("GET", "/pulp/default/api/v3/status/")
    assert excinfo.value.status_code == 500
    assert "Traceback" not in excinfo.value.safe_message
    await client.aclose()


async def test_oversized_response_is_rejected(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (settings.max_response_bytes + 1))

    client = build_client(settings, handler)
    with pytest.raises(PulpError):
        await client.request("GET", "/pulp/default/api/v3/status/")
    await client.aclose()


async def test_redirect_is_not_followed(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://evil.example.com/"})

    client = build_client(settings, handler)
    with pytest.raises(PulpError) as excinfo:
        await client.request("GET", "/pulp/default/api/v3/status/")
    assert excinfo.value.status_code == 302
    await client.aclose()


async def test_timeout_maps_to_pulp_error(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = build_client(settings, handler)
    with pytest.raises(PulpError):
        await client.request("GET", "/pulp/default/api/v3/status/")
    await client.aclose()
