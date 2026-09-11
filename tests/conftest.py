import pytest

REQUIRED_ENV = {
    "PULP_INTERNAL_URL": "http://pulp-api-svc:24817",
    "PULP_ADMIN_USER": "admin",
    "PULP_ADMIN_PASSWORD": "test-admin-password",
    "UI_USERNAME": "operator",
    "UI_PASSWORD_HASH": "scrypt$16384$8$1$0123456789abcdef$54f5e57e3cef92c9fa425f9f15accd0efd43f96947978c3fae347fcd6b2ab14a",
    "SESSION_SECRET": "test-session-secret",
    "PUBLIC_DIAGNOSTIC_URL": "https://pulp.dev.tbs.cloudeka.xyz",
    "ALLOWED_SOURCE_HOSTS": "mirror.example.com",
    "REQUEST_TIMEOUT_SECONDS": "10",
    "MAX_RESPONSE_BYTES": "1048576",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def resolvable_dns(monkeypatch):
    # Tests must not depend on real DNS: allowlisted source hosts resolve to a public
    # address. Tests asserting DNS failure override this with their own monkeypatch.
    monkeypatch.setattr(
        "app.safety.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )


@pytest.fixture
def settings(monkeypatch):
    from app.config import load_settings

    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    return load_settings()
