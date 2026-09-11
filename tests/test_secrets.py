import base64

from app.k8s import ALLOWED_SECRET_NAMES, POSTGRES_CREDENTIALS_SECRET, S3_CREDENTIALS_SECRET
from app.main import create_app
from tests.helpers import FakeSecretsStore, authed_client, csrf_headers


def build_app(settings, fake):
    return create_app(settings, secrets_factory=lambda: fake)


def test_s3_apply_writes_the_five_expected_keys(settings):
    fake = FakeSecretsStore()
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/secrets/s3", headers=csrf_headers(test_client)
        )
    assert response.status_code == 200
    assert response.json()["secret"] == S3_CREDENTIALS_SECRET
    assert len(fake.applied) == 1
    applied = fake.applied[0]
    assert applied["name"] == S3_CREDENTIALS_SECRET
    assert set(applied["values"]) == {
        "s3-access-key-id",
        "s3-secret-access-key",
        "s3-bucket-name",
        "s3-region",
        "s3-endpoint",
    }
    assert applied["values"]["s3-access-key-id"] == settings.pulp_s3_access_key_id


def test_postgres_apply_reads_everest_and_writes_expected_keys(settings):
    fake = FakeSecretsStore(everest=("db-user", "db-pass"))
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/secrets/postgres", headers=csrf_headers(test_client)
        )
    assert response.status_code == 200
    assert response.json()["secret"] == POSTGRES_CREDENTIALS_SECRET
    applied = fake.applied[0]
    assert applied["name"] == POSTGRES_CREDENTIALS_SECRET
    assert applied["values"]["POSTGRES_USERNAME"] == "db-user"
    assert applied["values"]["POSTGRES_PASSWORD"] == "db-pass"
    assert applied["values"]["POSTGRES_HOST"] == settings.pulp_postgres_host
    # The source Secret's values never reach the response.
    assert "db-pass" not in response.text
    assert "db-user" not in response.text


def test_postgres_apply_reports_missing_everest_secret(settings):
    fake = FakeSecretsStore(everest=None)
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/secrets/postgres", headers=csrf_headers(test_client)
        )
    assert response.status_code == 502
    body = response.json()
    assert "everest-secrets-db-pulp" in body["error"]
    assert body["correlation_id"]
    assert fake.applied == []


def test_status_view_never_contains_a_secret_value(settings):
    known_secret = "known-test-secret-value"
    fake = FakeSecretsStore(
        existing={
            S3_CREDENTIALS_SECRET: {
                "data": {
                    "s3-access-key-id": base64.b64encode(b"admin").decode(),
                    "s3-secret-access-key": base64.b64encode(
                        known_secret.encode()
                    ).decode(),
                }
            }
        }
    )
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.get("/ui/api/secrets/status")
    assert response.status_code == 200
    assert known_secret not in response.text
    body = response.json()
    by_name = {item["name"]: item for item in body["secrets"]}
    assert by_name[S3_CREDENTIALS_SECRET]["exists"] is True
    assert by_name[S3_CREDENTIALS_SECRET]["keys"] == [
        "s3-access-key-id",
        "s3-secret-access-key",
    ]
    assert by_name[POSTGRES_CREDENTIALS_SECRET]["exists"] is False


def test_kubernetes_error_maps_to_safe_message_with_correlation_id(settings):
    from app.k8s import SecretError

    fake = FakeSecretsStore()
    fake.raised = SecretError("Kubernetes API rejected the request.")
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/secrets/s3", headers=csrf_headers(test_client)
        )
    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "Kubernetes API rejected the request."
    assert body["correlation_id"]
    # A failed apply records a failed activity, never a value.
    entries = app.state.activity.recent()
    assert entries[0]["result"] == "failed"
    assert entries[0]["target"] == S3_CREDENTIALS_SECRET


def test_secret_allowlist_refuses_an_unlisted_name_before_any_request():
    # The allowlist is the defense against RBAC `create` being namespace-bounded.
    assert ALLOWED_SECRET_NAMES == {
        "pulp-s3-credentials",
        "pulp-postgres-credentials",
        "pulp-tenant-credentials",
    }
    from app.k8s import SecretError, SecretsStore

    class NoRequests:
        calls = 0

        async def request(self, *args, **kwargs):
            NoRequests.calls += 1
            raise AssertionError("a request must not be issued")

    store = SecretsStore.__new__(SecretsStore)
    store._settings = None
    store._client = NoRequests()
    store._owns_client = False

    import asyncio

    async def attempt():
        try:
            await store.apply_secret("pulp-other", {"a": "b"})
        except SecretError as exc:
            return exc.safe_message
        return None

    message = asyncio.run(attempt())
    assert message == "secret name is not on the allowlist."
    assert NoRequests.calls == 0


def test_tenant_credential_set_stores_and_never_echoes(settings):
    fake = FakeSecretsStore()
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/credential",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "tenant-user",
                "password": "tenant-password-value",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["stored"] is True
    assert body["domain"] == "dummy-alpha"
    assert "tenant-password-value" not in response.text
    assert fake.tenant_credentials["dummy-alpha"] == ("tenant-user", "tenant-password-value")
    entries = app.state.activity.recent()
    assert entries[0]["action"] == "tenant.credential.set"
    assert "tenant-password-value" not in str(entries)


def test_prod_read_refuses_any_other_name_before_a_request():
    from app.k8s import SecretError, SecretsStore

    class NoRequests:
        calls = 0

        async def request(self, *args, **kwargs):
            NoRequests.calls += 1
            raise AssertionError("a request must not be issued")

    store = SecretsStore.__new__(SecretsStore)
    store._settings = None
    store._client = NoRequests()
    store._owns_client = False

    import asyncio

    async def attempt():
        try:
            await store.read_prod_secret("some-other-secret")
        except SecretError as exc:
            return exc.safe_message
        return None

    message = asyncio.run(attempt())
    assert message is not None
    assert NoRequests.calls == 0
