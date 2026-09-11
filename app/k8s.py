"""Dependency-free Kubernetes API client for Secret management.

Uses httpx against the in-cluster API server with the ServiceAccount token and CA
mounts. Only the operations the UI needs exist here: read one Secret, and
create-or-update a Secret. There is deliberately no list/watch, which would expose
values, and no generic path builder — every namespace and name is fixed by the
caller or hardcoded in SecretsStore.
"""

import json
import logging

import httpx

from app.logging_config import redact

logger = logging.getLogger(__name__)

# The only Secrets the namespace-scoped writer may touch. Anything else is refused
# before an HTTP request is made. A `create` verb cannot be constrained by
# `resourceNames` in RBAC, so the app enforces the allowlist itself.
ALLOWED_SECRET_NAMES = frozenset(
    {
        "pulp-s3-credentials",
        "pulp-postgres-credentials",
        "pulp-tenant-credentials",
    }
)

TENANT_CREDENTIALS_SECRET = "pulp-tenant-credentials"
S3_CREDENTIALS_SECRET = "pulp-s3-credentials"
POSTGRES_CREDENTIALS_SECRET = "pulp-postgres-credentials"
# Read from the prod namespace. Exactly one name; see the RBAC file.
EVEREST_DB_SECRET = "everest-secrets-db-pulp"


class SecretError(Exception):
    """A Kubernetes API failure safe to surface to the operator."""

    def __init__(self, safe_message: str, *, correlation_id: str = "") -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.correlation_id = correlation_id


class KubernetesClient:
    def __init__(self, settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        # Token/CA are read lazily by the API server-issued Mounted files; the token
        # path is configurable so tests can point at a fixture.
        self._token_path = settings.k8s_token_path
        verify: bool | str = True
        try:
            with open(settings.k8s_ca_path, "rb"):
                verify = settings.k8s_ca_path
        except OSError:
            # No CA mount (tests): fall back to the default trust store.
            verify = True
        self._client = httpx.AsyncClient(
            base_url=settings.k8s_api_url,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            verify=verify,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict:
        try:
            with open(self._token_path, encoding="utf-8") as handle:
                token = handle.read().strip()
        except OSError:
            token = ""
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
    ) -> httpx.Response:
        # Never log the token or a Secret body. Path only.
        logger.info(
            "k8s.request %s",
            redact({"method": method, "path": path}),
        )
        try:
            return await self._client.request(
                method, path, headers=self._headers(), json=json_body
            )
        except httpx.HTTPError as exc:
            raise SecretError("Kubernetes API request failed.") from exc


class SecretsStore:
    """Policy layer: fixed namespaces and an allowlist over KubernetesClient."""

    def __init__(self, settings, client: KubernetesClient | None = None) -> None:
        self._settings = settings
        self._client = client or KubernetesClient(settings)
        self._owns_client = client is None

    @property
    def client(self) -> KubernetesClient:
        return self._client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_secret(
        self, name: str, *, namespace: str | None = None, correlation_id: str = ""
    ) -> dict | None:
        self._require_allowed(name)
        target = namespace or self._settings.k8s_namespace
        response = await self._client.request(
            "GET", f"/api/v1/namespaces/{target}/secrets/{name}"
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SecretError(
                "Kubernetes API rejected the request.", correlation_id=correlation_id
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SecretError(
                "Kubernetes API returned a malformed response.",
                correlation_id=correlation_id,
            ) from exc

    async def apply_secret(
        self,
        name: str,
        values: dict[str, str],
        *,
        namespace: str | None = None,
        correlation_id: str = "",
    ) -> None:
        """Create or update one Secret. Upsert, like `kubectl apply`."""
        self._require_allowed(name)
        target = namespace or self._settings.k8s_namespace
        existing = await self.get_secret(
            name, namespace=target, correlation_id=correlation_id
        )
        body = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": target},
            "type": "Opaque",
            # stringData lets the API server base64-encode; the app never handles an
            # encoded value and never logs this body.
            "stringData": values,
        }
        if existing is None:
            path = f"/api/v1/namespaces/{target}/secrets"
            method = "POST"
        else:
            path = f"/api/v1/namespaces/{target}/secrets/{name}"
            method = "PUT"
        response = await self._client.request(method, path, json_body=body)
        if response.status_code >= 400:
            raise SecretError(
                "Kubernetes API rejected the request.", correlation_id=correlation_id
            )

    # -- tenant credentials -------------------------------------------------

    @staticmethod
    def _require_allowed(name: str) -> None:
        if name not in ALLOWED_SECRET_NAMES:
            raise SecretError("secret name is not on the allowlist.")

    @staticmethod
    def _require_prod_name(name: str) -> None:
        """The prod read is for exactly one Secret. Anything else is refused."""
        if name != EVEREST_DB_SECRET:
            raise SecretError("only the named Everest Postgres Secret may be read.")

    async def get_tenant_credential(
        self, domain: str, *, correlation_id: str = ""
    ) -> tuple[str, str] | None:
        payload = await self.get_secret(
            TENANT_CREDENTIALS_SECRET, correlation_id=correlation_id
        )
        raw = _decoded_values(payload).get(domain)
        if not raw:
            return None
        try:
            record = json.loads(raw)
            username = str(record["username"])
            password = str(record["password"])
        except (ValueError, KeyError, TypeError):
            raise SecretError(
                "stored tenant credential is malformed.",
                correlation_id=correlation_id,
            ) from None
        if not username or not password:
            return None
        return username, password

    async def set_tenant_credential(
        self, domain: str, username: str, password: str, *, correlation_id: str = ""
    ) -> None:
        payload = await self.get_secret(
            TENANT_CREDENTIALS_SECRET, correlation_id=correlation_id
        )
        values = _decoded_values(payload)
        values[domain] = json.dumps({"username": username, "password": password})
        await self.apply_secret(
            TENANT_CREDENTIALS_SECRET, values, correlation_id=correlation_id
        )

    # -- everest postgres read ---------------------------------------------

    async def read_prod_secret(
        self, name: str, *, correlation_id: str = ""
    ) -> dict | None:
        """Read exactly the named Everest Secret from the prod namespace.

        The name is checked against a single-value allowlist before any request;
        the prod Role grants `get` on that one resourceName only.
        """
        self._require_prod_name(name)
        namespace = self._settings.k8s_prod_namespace
        response = await self._client.request(
            "GET", f"/api/v1/namespaces/{namespace}/secrets/{name}"
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SecretError(
                "Kubernetes API rejected the request.", correlation_id=correlation_id
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SecretError(
                "Kubernetes API returned a malformed response.",
                correlation_id=correlation_id,
            ) from exc

    async def read_everest_db_credentials(
        self, *, correlation_id: str = ""
    ) -> tuple[str, str] | None:
        """The username/password from the one permitted prod Secret."""
        payload = await self.read_prod_secret(
            EVEREST_DB_SECRET, correlation_id=correlation_id
        )
        if payload is None:
            return None
        values = _decoded_values(payload)
        username = values.get("user", "")
        password = values.get("password", "")
        if not username or not password:
            return None
        return username, password

def _decoded_values(payload: dict | None) -> dict[str, str]:
    """base64-decode a Secret's data map. Values never leave this module's callers."""
    import base64
    import binascii

    if not payload:
        return {}
    data = payload.get("data") or {}
    decoded: dict[str, str] = {}
    for key, value in data.items():
        try:
            decoded[str(key)] = base64.b64decode(str(value)).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
    return decoded
