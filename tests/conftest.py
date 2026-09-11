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
    # S3 credentials (Secret pulp-s3-credentials), read server-side only.
    "PULP_S3_ACCESS_KEY_ID": "test-access-key-id",
    "PULP_S3_SECRET_ACCESS_KEY": "test-secret-access-key",
    "PULP_S3_BUCKET_NAME": "pulp-content",
    "PULP_S3_REGION": "us-east-1",
    "PULP_S3_ENDPOINT": "http://rustfs-svc.rustfs.svc:9000",
    # Kubernetes API access. Tests override the endpoints via secrets_factory; these
    # keep load_settings() able to build a Settings for the app under test.
    "K8S_API_URL": "https://kubernetes.default.svc",
    "K8S_TOKEN_PATH": "/nonexistent/k8s-token",
    "K8S_CA_PATH": "/nonexistent/k8s-ca.crt",
    "K8S_NAMESPACE_PATH": "/nonexistent/k8s-namespace",
    "K8S_NAMESPACE": "pulp",
    "K8S_PROD_NAMESPACE": "prod",
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


@pytest.fixture(autouse=True)
def in_memory_secrets(monkeypatch):
    """Never let a test open a real Kubernetes client.

    Routes build their client via app.state.secrets_factory, so patching the class
    create_app uses is enough. A test that needs to inspect applied Secrets passes
    its own `secrets_factory=<FakeSecretsStore>`.
    """
    from app import main as app_main
    from tests.helpers import FakeConfigMapStore, FakeSecretsStore

    monkeypatch.setattr(app_main, "SecretsStore", lambda settings: FakeSecretsStore())
    monkeypatch.setattr(
        app_main, "ConfigMapStore", lambda settings: FakeConfigMapStore()
    )
