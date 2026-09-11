import base64

from fastapi.testclient import TestClient

from app.main import create_app


class StubClient:
    async def ping(self):
        return True

    async def aclose(self):
        return None

    async def request(self, method, path, **kwargs):
        return {"count": 0, "results": [], "domains": [], "users": [], "groups": []}


def creds(username="operator", password="s3cret"):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def build_app(settings):
    return create_app(settings, client_factory=lambda: StubClient())


def test_healthz_is_exempt_from_auth(settings):
    with TestClient(build_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200


def test_ui_requires_basic_auth(settings):
    with TestClient(build_app(settings)) as client:
        response = client.get("/ui/api/activity")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="pulp-ops"'


def test_ui_rejects_wrong_password_with_same_response(settings):
    with TestClient(build_app(settings)) as client:
        wrong = client.get("/ui/api/activity", headers=creds(password="nope"))
        unknown = client.get("/ui/api/activity", headers=creds(username="nobody"))
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_safe_get_issues_csrf_token_and_cookie(settings):
    with TestClient(build_app(settings)) as client:
        response = client.get("/ui/api/activity", headers=creds())
    assert response.status_code == 200
    assert response.headers.get("X-CSRF-Token")
    assert client.cookies.get("pulp_ops_csrf")


def test_mutation_without_csrf_token_is_rejected(settings):
    with TestClient(build_app(settings)) as client:
        client.get("/ui/api/activity", headers=creds())
        response = client.post(
            "/ui/api/delete",
            headers=creds(),
            json={"domain": "default", "href": "/x", "confirmed": True},
        )
    assert response.status_code == 403


def test_mutation_with_csrf_token_is_allowed(settings):
    with TestClient(build_app(settings)) as client:
        token = client.get("/ui/api/activity", headers=creds()).headers["X-CSRF-Token"]
        response = client.post(
            "/ui/api/delete",
            headers={**creds(), "X-CSRF-Token": token},
            json={
                "domain": "default",
                "href": "/pulp/default/api/v3/repositories/rpm/rpm/abc/",
                "confirmed": False,
            },
        )
    assert response.status_code == 400


def test_unauthenticated_mutation_is_rejected_before_csrf(settings):
    with TestClient(build_app(settings)) as client:
        response = client.post(
            "/ui/api/delete", json={"domain": "default", "href": "/x", "confirmed": True}
        )
    assert response.status_code == 401


def test_password_never_appears_in_response(settings):
    with TestClient(build_app(settings)) as client:
        body = client.get("/ui/api/activity", headers=creds()).text
    assert "s3cret" not in body
    assert "Basic " not in body


def test_csrf_token_survives_intervening_safe_gets(settings):
    # task_detail.html polls a safe GET every 2s; the browser captures the token once
    # at load, so the nonce must not rotate under it.
    with TestClient(build_app(settings)) as client:
        first = client.get("/ui/api/activity", headers=creds()).headers["X-CSRF-Token"]
        client.get("/ui/api/activity", headers=creds())
        response = client.post(
            "/ui/api/delete",
            headers={**creds(), "X-CSRF-Token": first},
            json={
                "domain": "default",
                "href": "/pulp/default/api/v3/repositories/rpm/rpm/abc/",
                "confirmed": False,
            },
        )
    assert response.status_code != 403


def test_tampered_csrf_signature_is_rejected(settings):
    with TestClient(build_app(settings)) as client:
        token = client.get("/ui/api/activity", headers=creds()).headers["X-CSRF-Token"]
        nonce = token.split(".", 1)[0]
        response = client.post(
            "/ui/api/delete",
            headers={**creds(), "X-CSRF-Token": f"{nonce}.deadbeef"},
            json={"domain": "default", "href": "/x", "confirmed": True},
        )
    assert response.status_code == 403
