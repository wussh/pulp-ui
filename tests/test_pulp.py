import logging

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


async def test_traversal_path_is_rejected_without_dispatch(settings):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={})

    client = build_client(settings, handler)
    with pytest.raises(ValueError):
        await client.request("GET", "/pulp/../admin/api/v3/users/")
    assert called == []
    await client.aclose()


async def test_percent_encoded_dot_traversal_is_rejected(settings):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={})

    client = build_client(settings, handler)
    bad_paths = (
        "/pulp/%2e%2e/admin/api/v3/users/",
        "/pulp/%2E%2E/admin/api/v3/users/",
        "/pulp/default/api/v3/%2e%2e/%2e%2e/admin/",
    )
    for bad in bad_paths:
        with pytest.raises(ValueError):
            await client.request("GET", bad)
    assert called == []
    await client.aclose()


async def test_percent_encoded_slash_traversal_is_rejected(settings):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={})

    client = build_client(settings, handler)
    bad_paths = (
        "/pulp/..%2fadmin/api/v3/users/",
        "/pulp/%2e%2e%2fadmin/api/v3/users/",
        "/pulp/%2E%2Fadmin/api/v3/users/",
    )
    for bad in bad_paths:
        with pytest.raises(ValueError):
            await client.request("GET", bad)
    assert called == []
    await client.aclose()


async def test_mixed_encoded_and_literal_traversal_is_rejected(settings):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={})

    client = build_client(settings, handler)
    bad_paths = (
        "/pulp/../%2e%2e/admin/",
        "/pulp/%2e./admin/",
        "/pulp/.%2e/admin/",
    )
    for bad in bad_paths:
        with pytest.raises(ValueError):
            await client.request("GET", bad)
    assert called == []
    await client.aclose()


async def test_control_characters_are_rejected(settings):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={})

    client = build_client(settings, handler)
    for bad in ("/pulp/default/api/v3/status/\n", "/pulp/default/\r\nadmin/", "/pulp/a\x00b/"):
        with pytest.raises(ValueError):
            await client.request("GET", bad)
    assert called == []
    await client.aclose()


async def test_4xx_does_not_echo_unknown_keys(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"password": ["hunter2-secret"], "name": ["This field is required."]},
        )

    client = build_client(settings, handler)
    with pytest.raises(PulpError) as excinfo:
        await client.request("POST", "/pulp/default/api/v3/users/", json_body={})
    assert "hunter2-secret" not in excinfo.value.safe_message
    assert "password" not in excinfo.value.safe_message
    assert "This field is required." in excinfo.value.safe_message
    await client.aclose()


async def test_4xx_with_only_unknown_keys_is_generic(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"password": ["hunter2-secret"]})

    client = build_client(settings, handler)
    with pytest.raises(PulpError) as excinfo:
        await client.request("POST", "/pulp/default/api/v3/users/", json_body={})
    assert excinfo.value.safe_message == "Pulp API rejected the request."
    await client.aclose()


async def test_outgoing_request_is_logged_without_credentials(settings, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"pulp_href": "/x/"})

    client = build_client(settings, handler)
    with caplog.at_level(logging.INFO, logger="app.pulp"):
        await client.request(
            "POST",
            "/pulp/default/api/v3/users/",
            json_body={"username": "operator", "password": "hunter2"},
            correlation_id="corr-1",
        )
    await client.aclose()
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "hunter2" not in logged
    assert "json_body" not in logged
    assert "corr-1" in logged
    assert "POST" in logged
    assert "Basic " not in logged
    assert "authorization" not in logged.lower()


async def test_invalid_url_maps_to_pulp_error(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("bad url")

    client = build_client(settings, handler)
    with pytest.raises(PulpError):
        await client.request("GET", "/pulp/default/api/v3/status/")
    await client.aclose()
