import pytest

REQUIRED_ENV = {
    "PULP_INTERNAL_URL": "http://pulp-api-svc:24817",
    "PULP_ADMIN_USER": "admin",
    "PULP_ADMIN_PASSWORD": "test-admin-password",
    "UI_USERNAME": "operator",
    "UI_PASSWORD_HASH": "placeholder-replaced-by-test-fixture",
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


@pytest.fixture
def settings(monkeypatch):
    from app.config import load_settings

    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    return load_settings()
