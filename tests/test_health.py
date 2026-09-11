from fastapi.testclient import TestClient

from app.main import create_app


class StubClient:
    def __init__(self, reachable: bool) -> None:
        self._reachable = reachable

    async def ping(self) -> bool:
        return self._reachable

    async def aclose(self) -> None:
        return None

    async def request(self, method, path, **kwargs):
        return {}


def test_healthz_succeeds_without_pulp(settings):
    client = TestClient(create_app(settings, client_factory=lambda: StubClient(False)))
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_passes_when_pulp_reachable(settings):
    client = TestClient(create_app(settings, client_factory=lambda: StubClient(True)))
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_fails_when_pulp_unreachable(settings):
    client = TestClient(create_app(settings, client_factory=lambda: StubClient(False)))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not-ready"}
