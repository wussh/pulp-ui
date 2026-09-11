# Pulp Operator UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small internal FastAPI operator console that replaces script-only Pulp operations and the existing `/ui/` deployment.

**Architecture:** One FastAPI service renders Jinja pages and serves same-origin `/ui/api/*` endpoints. A single server-side `PulpClient` calls internal `pulp-api-svc:24817` with an admin credential that never reaches the browser. State lives in Pulp; the app keeps only in-process activity and workflow records.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Jinja2, httpx, pytest, pytest-asyncio. No JavaScript build step, no application database.

**Spec:** `docs/superpowers/specs/2026-09-11-pulp-operator-ui-design.md`

## Global Constraints

- Application source lives in `pulp-ui`. Kubernetes manifests live in `linsa/manifests/pulp/ui/`.
- Public path stays `/ui/`. Service port is `8080`.
- Browser never receives Pulp credentials, connection details, or raw upstream error bodies.
- No shell execution, no `subprocess`, no arbitrary endpoint strings from browser input.
- Delete only ever by exact server-fetched href. No bulk delete. No cascading delete.
- Every mutation carries one correlation ID across logs, activity record, and response.
- Never log authorization headers, cookies, passwords, Secret values, or credential-bearing bodies.
- Python dependencies: `fastapi`, `uvicorn[standard]`, `jinja2`, `httpx`, `python-multipart`. Dev extras: `pytest`, `pytest-asyncio`.
- Test runner command everywhere: `python -m pytest`.
- Domain path form is `/pulp/{domain}/api/v3/...`. Flat `/pulp/api/v3/...` is permitted only where this plan names it explicitly, with an in-code comment.
- `PulpClient.request` takes its JSON payload through the keyword `json_body`, never `json`.
- Cluster-changing commands require separate explicit approval. Nothing in Tasks 1-13 touches the cluster.

---

### Task 1: Project skeleton, configuration, health probes

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/main.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_health.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.config.Settings` frozen dataclass with fields `pulp_internal_url`, `pulp_admin_user`, `pulp_admin_password`, `ui_username`, `ui_password_hash`, `session_secret`, `public_diagnostic_url`, `allowed_source_hosts` (tuple of str), `request_timeout_seconds` (float), `max_response_bytes` (int); `app.config.load_settings() -> Settings`; `app.main.create_app(settings: Settings, client_factory=None) -> FastAPI`; `create_app` stores settings on `app.state.settings`.

- [ ] **Step 1: Write the failing test**

```python
# tests/conftest.py
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
    from app.security import hash_password

    env = dict(REQUIRED_ENV)
    env["UI_PASSWORD_HASH"] = hash_password(
        "s3cret", salt=b"0123456789abcdef"
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_settings()
```

```python
# tests/test_health.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_health.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Write minimal implementation**

```toml
# pyproject.toml
[project]
name = "pulp-ui"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi",
  "uvicorn[standard]",
  "jinja2",
  "httpx",
  "python-multipart",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

```python
# app/__init__.py
```

```python
# app/config.py
import os
from dataclasses import dataclass


def _read_secret(env_var: str, file_var: str) -> str:
    path = os.environ.get(file_var)
    if path:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
        if value:
            return value
    value = os.environ.get(env_var, "").strip()
    if not value:
        raise RuntimeError(f"missing configuration: set {env_var} or {file_var}")
    return value


@dataclass(frozen=True)
class Settings:
    pulp_internal_url: str
    pulp_admin_user: str
    pulp_admin_password: str
    ui_username: str
    ui_password_hash: str
    session_secret: str
    public_diagnostic_url: str
    allowed_source_hosts: tuple[str, ...]
    request_timeout_seconds: float
    max_response_bytes: int


def load_settings() -> Settings:
    hosts = [
        host.strip().lower()
        for host in os.environ.get("ALLOWED_SOURCE_HOSTS", "").split(",")
        if host.strip()
    ]
    return Settings(
        pulp_internal_url=os.environ.get("PULP_INTERNAL_URL", "").rstrip("/"),
        pulp_admin_user=_read_secret("PULP_ADMIN_USER", "PULP_ADMIN_USER_FILE"),
        pulp_admin_password=_read_secret(
            "PULP_ADMIN_PASSWORD", "PULP_ADMIN_PASSWORD_FILE"
        ),
        ui_username=os.environ.get("UI_USERNAME", "").strip(),
        ui_password_hash=_read_secret("UI_PASSWORD_HASH", "UI_PASSWORD_HASH_FILE"),
        session_secret=_read_secret("SESSION_SECRET", "SESSION_SECRET_FILE"),
        public_diagnostic_url=os.environ.get("PUBLIC_DIAGNOSTIC_URL", "").rstrip("/"),
        allowed_source_hosts=tuple(hosts),
        request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15")),
        max_response_bytes=int(os.environ.get("MAX_RESPONSE_BYTES", "4194304")),
    )
```

```python
# app/main.py
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import Settings


def create_app(settings: Settings, client_factory=None) -> FastAPI:
    def build_client():
        from app.pulp import PulpClient

        return PulpClient(settings)

    factory = client_factory or build_client
    app = FastAPI(
        title="Pulp Operator UI", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.client_factory = factory

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        client = factory()
        try:
            reachable = await client.ping()
        finally:
            await client.aclose()
        if not reachable:
            return JSONResponse({"status": "not-ready"}, status_code=503)
        return JSONResponse({"status": "ready"}, status_code=200)

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_health.py -v`  
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml app tests
git commit -m "feat: add service skeleton, configuration, and health probes"
```

---

### Task 2: Basic Auth and CSRF primitives

**Files:**
- Create: `app/security.py`
- Create: `tests/test_security.py`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: `app.security.hash_password(password: str, *, salt: bytes | None = None) -> str`; `app.security.verify_password(password: str, encoded: str) -> bool`; `app.security.check_credentials(username: str, password: str, settings: Settings) -> bool`; `app.security.issue_csrf(secret: str, nonce: str | None = None) -> tuple[str, str]` returning `(nonce, token)`; `app.security.validate_csrf(secret: str, nonce: str | None, token: str | None) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_security.py
from app.security import (
    check_credentials,
    hash_password,
    issue_csrf,
    validate_csrf,
    verify_password,
)


def test_password_round_trip():
    encoded = hash_password("correct horse battery staple", salt=b"0123456789abcdef")
    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong", encoded) is False


def test_password_hash_is_salted():
    first = hash_password("same-password", salt=b"0123456789abcdef")
    second = hash_password("same-password", salt=b"fedcba9876543210")
    assert first != second


def test_verify_password_rejects_malformed_encoding():
    assert verify_password("anything", "not-a-hash") is False
    assert verify_password("anything", "scrypt$bad$8$1$aa$bb") is False


def test_check_credentials_accepts_the_configured_operator(settings):
    assert check_credentials("operator", "s3cret", settings) is True


def test_check_credentials_rejects_wrong_user_or_password(settings):
    assert check_credentials("operator", "wrong", settings) is False
    assert check_credentials("nobody", "s3cret", settings) is False


def test_csrf_round_trip():
    nonce, token = issue_csrf("secret", nonce="abc123")
    assert nonce == "abc123"
    assert validate_csrf("secret", nonce, token) is True


def test_csrf_rejects_wrong_secret_nonce_and_garbage():
    nonce, token = issue_csrf("secret", nonce="abc123")
    assert validate_csrf("other-secret", nonce, token) is False
    assert validate_csrf("secret", "different", token) is False
    assert validate_csrf("secret", nonce, "garbage") is False
    assert validate_csrf("secret", None, None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_security.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.security'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/security.py
import hashlib
import hmac
import secrets

from app.config import Settings

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32
_SALT_BYTES = 16


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, expected_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(expected_hex)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


def check_credentials(username: str, password: str, settings: Settings) -> bool:
    username_ok = hmac.compare_digest(username, settings.ui_username)
    password_ok = verify_password(password, settings.ui_password_hash)
    return username_ok & password_ok


def issue_csrf(secret: str, nonce: str | None = None) -> tuple[str, str]:
    nonce = nonce or secrets.token_urlsafe(24)
    signature = hmac.new(
        secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return nonce, f"{nonce}.{signature}"


def validate_csrf(secret: str, nonce: str | None, token: str | None) -> bool:
    if not nonce or not token:
        return False
    try:
        token_nonce, signature = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(token_nonce, nonce):
        return False
    expected = hmac.new(
        secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_security.py -v`  
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add app/security.py tests/test_security.py
git commit -m "feat: add password hashing, credential check, and CSRF tokens"
```

---

### Task 3: Pulp REST client with limits and safe errors

**Files:**
- Create: `app/pulp.py`
- Create: `tests/test_pulp.py`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: `app.pulp.PulpError(Exception)` with attributes `status_code: int | None`, `safe_message: str`, `correlation_id: str`; `app.pulp.PulpClient(settings)` with `async def request(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None, correlation_id: str | None = None) -> dict`; `async def ping() -> bool`; `async def aclose() -> None`. `request` accepts only paths beginning with `/pulp/` and rejects absolute URLs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pulp.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pulp.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pulp'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/pulp.py
import base64
import json
import uuid

import httpx

from app.config import Settings

_ALLOWED_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})
_SAFE_4XX_DETAIL_LIMIT = 400


class PulpError(Exception):
    def __init__(
        self,
        safe_message: str,
        *,
        status_code: int | None = None,
        correlation_id: str = "",
    ) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.status_code = status_code
        self.correlation_id = correlation_id


class PulpClient:
    def __init__(self, settings: Settings) -> None:
        token = base64.b64encode(
            f"{settings.pulp_admin_user}:{settings.pulp_admin_password}".encode("utf-8")
        ).decode("ascii")
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.pulp_internal_url,
            headers={"Authorization": f"Basic {token}"},
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        try:
            await self.request("GET", "/pulp/default/api/v3/status/")
        except PulpError:
            return False
        return True

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        method = method.upper()
        if method not in _ALLOWED_METHODS:
            raise ValueError(f"unsupported method: {method}")
        if not path.startswith("/pulp/"):
            raise ValueError("path must be an internal Pulp API path")
        if "://" in path or path.startswith("//"):
            raise ValueError("absolute URLs are not permitted")

        correlation_id = correlation_id or uuid.uuid4().hex
        try:
            async with self._client.stream(
                method, path, json=json_body, params=params
            ) as response:
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._settings.max_response_bytes:
                        raise PulpError(
                            "Upstream response exceeded the configured size limit.",
                            status_code=response.status_code,
                            correlation_id=correlation_id,
                        )
                    chunks.append(chunk)
                raw = b"".join(chunks)
                status = response.status_code
        except PulpError:
            raise
        except httpx.HTTPError as exc:
            raise PulpError(
                "Pulp API request failed.", correlation_id=correlation_id
            ) from exc

        if 300 <= status < 400:
            raise PulpError(
                "Pulp API returned an unexpected redirect.",
                status_code=status,
                correlation_id=correlation_id,
            )

        if status >= 500:
            raise PulpError(
                "Pulp API returned a server error.",
                status_code=status,
                correlation_id=correlation_id,
            )

        if status >= 400:
            raise PulpError(
                _safe_4xx_message(raw),
                status_code=status,
                correlation_id=correlation_id,
            )

        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PulpError(
                "Pulp API returned a malformed response.",
                status_code=status,
                correlation_id=correlation_id,
            ) from exc
        return decoded if isinstance(decoded, dict) else {"results": decoded}


def _safe_4xx_message(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return "Pulp API rejected the request."
    if not isinstance(payload, dict):
        return "Pulp API rejected the request."
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > _SAFE_4XX_DETAIL_LIMIT:
        text = text[:_SAFE_4XX_DETAIL_LIMIT]
    return f"Pulp API rejected the request: {text}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pulp.py tests/test_health.py -v`  
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add app/pulp.py tests/test_pulp.py
git commit -m "feat: add Pulp REST client with size, timeout, and error limits"
```

---

### Task 4: Domain-aware endpoints, field validation, SSRF guards

**Files:**
- Create: `app/endpoints.py`
- Create: `app/safety.py`
- Create: `tests/test_endpoints.py`
- Create: `tests/test_safety.py`

**Interfaces:**
- Consumes: nothing beyond stdlib.
- Produces: `app.endpoints.plugin_api(domain: str, path: str) -> str`; `app.endpoints.global_api(path: str) -> str`; `app.endpoints.CONTENT_PLUGINS: dict[str, str]`; `app.endpoints.validate_name(kind: str, value: str) -> str`; `app.endpoints.validate_plugin(value: str) -> str`; `app.safety.validate_source_url(url: str, allowed_hosts: tuple[str, ...]) -> str`; `app.safety.is_forbidden_address(address) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_endpoints.py
import pytest

from app.endpoints import (
    CONTENT_PLUGINS,
    global_api,
    plugin_api,
    validate_name,
    validate_plugin,
)


def test_plugin_api_builds_domain_scoped_path():
    assert plugin_api("default", "repositories/rpm/rpm/") == (
        "/pulp/default/api/v3/repositories/rpm/rpm/"
    )


def test_plugin_api_rejects_bad_domain():
    for bad in ["", "Default", "a b", "../admin", "x" * 64, "-leading", "sl/ash"]:
        with pytest.raises(ValueError):
            plugin_api(bad, "status/")


def test_global_api_builds_flat_path():
    assert global_api("groups/1/roles/") == "/pulp/api/v3/groups/1/roles/"


def test_content_plugins_are_enumerated():
    assert set(CONTENT_PLUGINS) == {"rpm", "deb", "python", "ansible", "container"}


def test_validate_name_accepts_pulp_safe_names():
    assert validate_name("domain", "dummy-alpha") == "dummy-alpha"
    assert validate_name("domain", "a_b-1") == "a_b-1"


def test_validate_name_rejects_traversal_and_whitespace():
    for bad in ["", "a/b", "..", "a b", "A", "a" * 64, "-a", "a."]:
        with pytest.raises(ValueError):
            validate_name("domain", bad)


def test_validate_plugin_rejects_unknown():
    assert validate_plugin("rpm") == "rpm"
    with pytest.raises(ValueError):
        validate_plugin("../../etc/passwd")
```

```python
# tests/test_safety.py
import ipaddress

import pytest

from app.safety import is_forbidden_address, validate_source_url


def test_validate_source_url_accepts_allowlisted_https(monkeypatch):
    monkeypatch.setattr(
        "app.safety.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )
    url = validate_source_url(
        "https://mirror.example.com/pub/rpm/", ("mirror.example.com",)
    )
    assert url == "https://mirror.example.com/pub/rpm/"


def test_validate_source_url_rejects_http():
    with pytest.raises(ValueError):
        validate_source_url("http://mirror.example.com/", ("mirror.example.com",))


def test_validate_source_url_rejects_unlisted_host():
    with pytest.raises(ValueError):
        validate_source_url("https://evil.example.com/", ("mirror.example.com",))


def test_validate_source_url_rejects_credentials_in_url():
    with pytest.raises(ValueError):
        validate_source_url(
            "https://user:pass@mirror.example.com/", ("mirror.example.com",)
        )


def test_validate_source_url_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(
        "app.safety.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("169.254.169.254", 443)),
        ],
    )
    with pytest.raises(ValueError):
        validate_source_url("https://mirror.example.com/", ("mirror.example.com",))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.5",
        "172.16.0.5",
        "192.168.1.5",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00::1",
    ],
)
def test_is_forbidden_address_rejects_internal_ranges(address):
    assert is_forbidden_address(ipaddress.ip_address(address)) is True


def test_is_forbidden_address_allows_public():
    assert is_forbidden_address(ipaddress.ip_address("93.184.216.34")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_endpoints.py tests/test_safety.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.endpoints'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/endpoints.py
import re

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

CONTENT_PLUGINS: dict[str, str] = {
    "rpm": "rpm",
    "deb": "deb",
    "python": "python",
    "ansible": "ansible",
    "container": "container",
}


def validate_name(kind: str, value: str) -> str:
    value = (value or "").strip()
    if not _NAME_PATTERN.match(value):
        raise ValueError(
            f"{kind} must be 1-63 characters of lowercase letters, digits, hyphen, "
            "or underscore, and must start with a letter or digit"
        )
    return value


def validate_plugin(value: str) -> str:
    if value not in CONTENT_PLUGINS:
        raise ValueError("unsupported content plugin")
    return value


def plugin_api(domain: str, path: str) -> str:
    domain = validate_name("domain", domain)
    return f"/pulp/{domain}/api/v3/{path.lstrip('/')}"


def global_api(path: str) -> str:
    # Flat paths are permitted only for endpoints verified to stay global.
    # Currently: role assignment under /pulp/api/v3/groups/<id>/roles/.
    # Confirm against live Pulp before adding more.
    return f"/pulp/api/v3/{path.lstrip('/')}"
```

```python
# app/safety.py
import ipaddress
import socket
from urllib.parse import urlsplit

_FORBIDDEN_FLAGS = (
    "is_private",
    "is_loopback",
    "is_link_local",
    "is_multicast",
    "is_unspecified",
    "is_reserved",
)


def is_forbidden_address(address) -> bool:
    if address.is_global is False:
        return True
    return any(getattr(address, flag) for flag in _FORBIDDEN_FLAGS)


def validate_source_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    candidate = (url or "").strip()
    parts = urlsplit(candidate)
    if parts.scheme != "https":
        raise ValueError("source URL must use https")
    if parts.username or parts.password:
        raise ValueError("source URL must not embed credentials")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("source URL must include a hostname")
    if host not in allowed_hosts:
        raise ValueError("source host is not allowlisted")
    for entry in socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP):
        address = ipaddress.ip_address(entry[4][0])
        if is_forbidden_address(address):
            raise ValueError("source host resolves to a forbidden address")
    return candidate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_endpoints.py tests/test_safety.py -v`  
Expected: PASS (18 passed)

- [ ] **Step 5: Commit**

```bash
git add app/endpoints.py app/safety.py tests/test_endpoints.py tests/test_safety.py
git commit -m "feat: add domain-aware endpoints, name validation, and SSRF guards"
```

---

### Task 5: App state, layout, and Overview page

**Files:**
- Create: `app/state.py`
- Create: `app/routes/__init__.py`
- Create: `app/routes/overview.py`
- Create: `app/templates/base.html`
- Create: `app/templates/overview.html`
- Create: `app/static/app.css`
- Create: `app/static/app.js`
- Create: `tests/helpers.py`
- Create: `tests/test_overview.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `Settings`, `PulpClient`, `plugin_api`.
- Produces: `app.state.CorrelationStore.new(correlation_id=None) -> str`; `app.state.ActivityStore.record(entry: dict) -> None` and `recent(limit=50) -> list[dict]`; `app.state.RunStore.create(run_id, resources=None)`, `add_resource(run_id, href)`, `resources(run_id) -> list[str]`, `drop(run_id)`; `app.routes.overview.check_public_route(settings) -> tuple[bool, int]`; `app.routes.overview.build_snapshot(client, settings) -> dict`; `tests.helpers.make_client(settings, handler) -> PulpClient`; `tests.helpers.csrf_headers(client) -> dict` (added here, used from Task 12 onward).

- [ ] **Step 1: Write the failing test**

```python
# tests/helpers.py
import httpx

from app.pulp import PulpClient


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
```

```python
# tests/test_overview.py
import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client


def ok_handler(payload_by_path):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = payload_by_path.get(request.url.path, {"count": 0, "results": []})
        return httpx.Response(200, json=payload)

    return handler


def test_overview_reports_counts_and_routing_failure(settings, monkeypatch):
    payloads = {
        "/pulp/default/api/v3/status/": {
            "versions": [{"component": "core", "version": "3.116.0"}]
        },
        "/pulp/default/api/v3/domains/": {"count": 3, "results": []},
        "/pulp/default/api/v3/repositories/container/container/": {
            "count": 7,
            "results": [],
        },
        "/pulp/default/api/v3/distributions/container/container/": {
            "count": 7,
            "results": [],
        },
        "/pulp/default/api/v3/tasks/": {"count": 2, "results": []},
    }
    monkeypatch.setattr(
        "app.routes.overview.check_public_route", lambda settings: (False, 404)
    )
    app = create_app(
        settings, client_factory=lambda: make_client(settings, ok_handler(payloads))
    )
    with TestClient(app) as test_client:
        body = test_client.get("/ui/api/overview").json()
    assert body["pulp"]["reachable"] is True
    assert body["pulp"]["core_version"] == "3.116.0"
    assert body["counts"]["domains"] == 3
    assert body["counts"]["container_repositories"] == 7
    assert body["routing"]["public_ok"] is False
    assert body["routing"]["public_status"] == 404
    assert any("public" in warning["code"] for warning in body["warnings"])


def test_overview_handles_unreachable_pulp(settings, monkeypatch):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        "app.routes.overview.check_public_route", lambda settings: (True, 200)
    )
    app = create_app(
        settings, client_factory=lambda: make_client(settings, failing_handler)
    )
    with TestClient(app) as test_client:
        body = test_client.get("/ui/api/overview").json()
    assert body["pulp"]["reachable"] is False
    assert body["counts"] == {}
    assert any("unreachable" in warning["code"] for warning in body["warnings"])


def test_overview_page_renders(settings, monkeypatch):
    monkeypatch.setattr(
        "app.routes.overview.check_public_route", lambda settings: (True, 200)
    )
    app = create_app(
        settings,
        client_factory=lambda: make_client(settings, ok_handler({})),
    )
    with TestClient(app) as test_client:
        response = test_client.get("/ui/")
    assert response.status_code == 200
    assert "Overview" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_overview.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/state.py
import uuid


class CorrelationStore:
    def new(self, correlation_id: str | None = None) -> str:
        return correlation_id or uuid.uuid4().hex


class ActivityStore:
    def __init__(self, limit: int = 500) -> None:
        self._limit = limit
        self._entries: list[dict] = []

    def record(self, entry: dict) -> None:
        self._entries.append(entry)
        if len(self._entries) > self._limit:
            del self._entries[: len(self._entries) - self._limit]

    def recent(self, limit: int = 50) -> list[dict]:
        return list(reversed(self._entries[-limit:]))


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, list[str]] = {}

    def create(self, run_id: str, resources: list[str] | None = None) -> None:
        self._runs[run_id] = list(resources or [])

    def add_resource(self, run_id: str, href: str) -> None:
        self._runs.setdefault(run_id, []).append(href)

    def resources(self, run_id: str) -> list[str]:
        return list(self._runs.get(run_id, []))

    def drop(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
```

```python
# app/routes/__init__.py
```

```python
# app/routes/overview.py
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.endpoints import plugin_api
from app.pulp import PulpClient, PulpError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_COUNT_TARGETS = (
    ("domains", "/pulp/default/api/v3/domains/"),
    ("container_repositories", None),
    ("container_distributions", None),
)


def check_public_route(settings: Settings) -> tuple[bool, int]:
    if not settings.public_diagnostic_url:
        return False, 0
    try:
        response = httpx.get(
            f"{settings.public_diagnostic_url}/pulp/api/v3/status/",
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return False, 0
    return response.status_code == 200, response.status_code


async def build_snapshot(client: PulpClient, settings: Settings) -> dict:
    warnings: list[dict] = []
    counts: dict[str, int] = {}
    core_version = ""
    reachable = True

    try:
        status = await client.request("GET", "/pulp/default/api/v3/status/")
        for component in status.get("versions", []):
            if component.get("component") == "core":
                core_version = component.get("version", "")
        counts["domains"] = (
            await client.request("GET", "/pulp/default/api/v3/domains/")
        ).get("count", 0)
        counts["container_repositories"] = (
            await client.request(
                "GET", plugin_api("default", "repositories/container/container/")
            )
        ).get("count", 0)
        counts["container_distributions"] = (
            await client.request(
                "GET", plugin_api("default", "distributions/container/container/")
            )
        ).get("count", 0)
        active = await client.request(
            "GET",
            "/pulp/default/api/v3/tasks/",
            params={"state__in": "running,waiting"},
        )
        failed = await client.request(
            "GET", "/pulp/default/api/v3/tasks/", params={"state": "failed"}
        )
        counts["active_tasks"] = active.get("count", 0)
        counts["failed_tasks"] = failed.get("count", 0)
        if counts["failed_tasks"]:
            warnings.append(
                {"code": "pulp.failed_tasks", "message": "Pulp has failed tasks."}
            )
    except PulpError as exc:
        reachable = False
        counts = {}
        warnings.append(
            {
                "code": "pulp.unreachable",
                "message": "Internal Pulp API is not reachable.",
                "correlation_id": exc.correlation_id,
            }
        )

    public_ok, public_status = check_public_route(settings)
    if not public_ok:
        warnings.append(
            {
                "code": "routing.public_route_failed",
                "message": "Public Pulp route is not serving requests.",
            }
        )

    return {
        "pulp": {"reachable": reachable, "core_version": core_version},
        "counts": counts,
        "routing": {
            "public_ok": public_ok,
            "public_status": public_status,
            "public_url": settings.public_diagnostic_url,
            "internal_url": settings.pulp_internal_url,
        },
        "warnings": warnings,
    }


@router.get("/", response_class=HTMLResponse)
async def overview_page(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    client = request.app.state.client_factory()
    try:
        snapshot = await build_snapshot(client, settings)
    finally:
        await client.aclose()
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "snapshot": snapshot,
            "warnings": snapshot["warnings"],
            "current_user": "operator",
        },
    )


@router.get("/api/overview")
async def overview_api(request: Request) -> dict:
    settings = request.app.state.settings
    client = request.app.state.client_factory()
    try:
        return await build_snapshot(client, settings)
    finally:
        await client.aclose()
```

```html
<!-- app/templates/base.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Pulp Operator UI{% endblock %}</title>
  <link rel="stylesheet" href="/ui/static/app.css">
</head>
<body>
  <header class="topbar">
    <span class="brand">Pulp Ops</span>
    <nav>
      <a href="/ui/">Overview</a>
      <a href="/ui/tenants">Tenants</a>
      <a href="/ui/content">Content</a>
      <a href="/ui/tasks">Tasks</a>
      <a href="/ui/validation">Validation</a>
      <a href="/ui/activity">Activity</a>
    </nav>
    <span class="operator">{{ current_user }}</span>
  </header>
  <main>
    {% for warning in warnings or [] %}
      <p class="warning" data-code="{{ warning.code }}">{{ warning.message }}</p>
    {% endfor %}
    {% block content %}{% endblock %}
  </main>
  <script src="/ui/static/app.js"></script>
</body>
</html>
```

```html
<!-- app/templates/overview.html -->
{% extends "base.html" %}
{% block content %}
<h2>Overview</h2>
<dl class="facts">
  <dt>Internal Pulp API</dt><dd>{{ snapshot.pulp.reachable }}</dd>
  <dt>Core version</dt><dd>{{ snapshot.pulp.core_version }}</dd>
  <dt>Public route status</dt><dd>{{ snapshot.routing.public_status }}</dd>
</dl>
<table class="counts">
  <caption>Resource counts</caption>
  <tbody>
  {% for name, value in snapshot.counts.items() %}
    <tr><th>{{ name }}</th><td>{{ value }}</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

```css
/* app/static/app.css */
body { font-family: system-ui, sans-serif; margin: 0; }
.topbar { display: flex; gap: 1rem; padding: 0.75rem 1rem; background: #1f2937; color: #fff; align-items: center; }
.topbar a { color: #cbd5e1; margin-right: 0.75rem; }
.brand { font-weight: 600; }
main { padding: 1.5rem; max-width: 60rem; }
.facts dt { font-weight: 600; }
.warning { background: #fef3c7; border-left: 4px solid #d97706; padding: 0.5rem 0.75rem; }
.subtitle { color: #6b7280; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #e5e7eb; }
```

```javascript
// app/static/app.js
window.pulpOps = window.pulpOps || {};

// Capture the server-issued CSRF token for same-origin mutations.
document.addEventListener("DOMContentLoaded", function () {
  fetch("/ui/api/activity?limit=1", { credentials: "same-origin" }).then(function (response) {
    var token = response.headers.get("X-CSRF-Token");
    if (token) {
      window.pulpOps.csrfToken = token;
    }
  });
});

window.pulpOps.postJSON = function (url, payload) {
  return fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": window.pulpOps.csrfToken || ""
    },
    body: JSON.stringify(payload)
  }).then(function (response) { return response.json(); });
};

// Polling helper for asynchronous Pulp task progress.
window.pulpOps.pollTask = function (href, onUpdate) {
  var timer = setInterval(function () {
    fetch("/ui/api/tasks/detail?href=" + encodeURIComponent(href), {
      credentials: "same-origin"
    })
      .then(function (response) { return response.json(); })
      .then(function (body) {
        onUpdate(body);
        if (["completed", "failed", "canceled"].indexOf(body.state) !== -1) {
          clearInterval(timer);
        }
      });
  }, 2000);
};
```

Rewire `app/main.py`:

```python
# app/main.py
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.routes import overview
from app.state import ActivityStore, CorrelationStore, RunStore

STATIC_DIR = "app/static"


def create_app(settings: Settings, client_factory=None) -> FastAPI:
    def build_client():
        from app.pulp import PulpClient

        return PulpClient(settings)

    factory = client_factory or build_client
    app = FastAPI(
        title="Pulp Operator UI", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.client_factory = factory
    app.state.correlations = CorrelationStore()
    app.state.activity = ActivityStore()
    app.state.runs = RunStore()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        client = factory()
        try:
            reachable = await client.ping()
        finally:
            await client.aclose()
        if not reachable:
            return JSONResponse({"status": "not-ready"}, status_code=503)
        return JSONResponse({"status": "ready"}, status_code=200)

    app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(overview.router, prefix="/ui")
    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_overview.py -v`  
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/state.py app/routes app/templates app/static app/main.py tests/helpers.py tests/test_overview.py
git commit -m "feat: add app wiring, layout, and overview page"
```

---

### Task 6: Tenants page and guided tenant setup

**Files:**
- Create: `app/routes/tenants.py`
- Create: `app/templates/tenants.html`
- Create: `tests/test_tenants.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `PulpClient`, `global_api`, `validate_name`, `tests.helpers.make_client`.
- Produces: `app.routes.tenants.list_tenants(client) -> dict`; `app.routes.tenants.plan_setup(client, payload: dict) -> dict`; `app.routes.tenants.apply_setup(client, plan: dict, correlation_id: str) -> dict`; endpoints `GET /ui/tenants`, `GET /ui/api/tenants`, `POST /ui/api/tenants/plan`, `POST /ui/api/tenants/apply`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tenants.py
import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client


def test_plan_setup_reuses_existing_domain(settings):
    def handler(request):
        if request.url.path == "/pulp/default/api/v3/domains/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [
                        {
                            "name": "dummy-alpha",
                            "pulp_href": "/pulp/default/api/v3/domains/x/",
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.post(
            "/ui/api/tenants/plan",
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        ).json()
    assert body["steps"][0] == {
        "action": "reuse",
        "resource": "domain",
        "name": "dummy-alpha",
    }
    assert body["preview"]["creates"] == 2
    assert body["preview"]["reuses"] == 1


def test_apply_setup_stops_on_first_failure(settings):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/users/"):
            return httpx.Response(400, json={"username": ["This field must be unique."]})
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/domains/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 400
    body = response.json()
    assert body["failed"] is True
    assert body["stopped_at"]["resource"] == "user"
    assert "password" not in str(body).lower()


def test_plan_rejects_invalid_domain(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/plan",
            json={"domain": "../admin", "username": "budi-test", "group": "g"},
        )
    assert response.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tenants.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.tenants'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/routes/tenants.py
from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.endpoints import global_api, validate_name
from app.pulp import PulpError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


async def _first_match(client, path: str, field: str, value: str) -> dict | None:
    listing = await client.request("GET", path, params={field: value})
    for item in listing.get("results", []):
        if item.get(field) == value:
            return item
    return None


def _record_id(href: str) -> str:
    return href.rstrip("/").split("/")[-1]


async def list_tenants(client) -> dict:
    domains = await client.request("GET", "/pulp/default/api/v3/domains/")
    users = await client.request("GET", "/pulp/default/api/v3/users/")
    groups = await client.request("GET", "/pulp/default/api/v3/groups/")
    return {
        "domains": [
            {"name": item.get("name"), "pulp_href": item.get("pulp_href")}
            for item in domains.get("results", [])
        ],
        "users": [
            {"username": item.get("username"), "pulp_href": item.get("pulp_href")}
            for item in users.get("results", [])
        ],
        "groups": [
            {"name": item.get("name"), "pulp_href": item.get("pulp_href")}
            for item in groups.get("results", [])
        ],
    }


async def plan_setup(client, payload: dict) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    username = validate_name("username", payload.get("username", ""))
    group = validate_name("group", payload.get("group", ""))

    existing_domain = await _first_match(
        client, "/pulp/default/api/v3/domains/", "name", domain
    )
    existing_user = await _first_match(
        client, "/pulp/default/api/v3/users/", "username", username
    )
    existing_group = await _first_match(
        client, "/pulp/default/api/v3/groups/", "name", group
    )

    steps = []
    for resource, name, found in (
        ("domain", domain, existing_domain),
        ("user", username, existing_user),
        ("group", group, existing_group),
    ):
        steps.append(
            {
                "action": "reuse" if found else "create",
                "resource": resource,
                "name": name,
            }
        )
    steps.append(
        {
            "action": "assign",
            "resource": "role",
            "name": f"group={group} domain={domain}",
        }
    )

    return {
        "domain": domain,
        "username": username,
        "group": group,
        "steps": steps,
        "preview": {
            "creates": sum(1 for step in steps if step["action"] == "create"),
            "reuses": sum(1 for step in steps if step["action"] == "reuse"),
            "assignments": 1,
        },
    }


async def _assign_domain_role(
    client, group_record: dict, domain_record: dict, correlation_id: str
) -> dict:
    # Role assignment stays on the flat path: verified against live Pulp, roles are
    # granted per group, not per domain path. Confirm before changing.
    path = global_api(f"groups/{_record_id(group_record['pulp_href'])}/roles/")
    return await client.request(
        "POST",
        path,
        json_body={
            "role": "core.domain_creator",
            "content_object": None,
            "domain": domain_record["pulp_href"],
        },
        correlation_id=correlation_id,
    )


async def apply_setup(client, plan: dict, correlation_id: str) -> dict:
    completed: list[dict] = []
    domain_record: dict | None = None
    group_record: dict | None = None

    for step in plan["steps"]:
        if step["action"] == "reuse":
            continue
        try:
            if step["resource"] == "domain":
                domain_record = await client.request(
                    "POST",
                    "/pulp/default/api/v3/domains/",
                    json_body={"name": plan["domain"]},
                    correlation_id=correlation_id,
                )
                created = domain_record
            elif step["resource"] == "user":
                created = await client.request(
                    "POST",
                    "/pulp/default/api/v3/users/",
                    json_body={"username": plan["username"]},
                    correlation_id=correlation_id,
                )
            elif step["resource"] == "group":
                group_record = await client.request(
                    "POST",
                    "/pulp/default/api/v3/groups/",
                    json_body={"name": plan["group"]},
                    correlation_id=correlation_id,
                )
                created = group_record
            else:
                domain_record = domain_record or await _first_match(
                    client, "/pulp/default/api/v3/domains/", "name", plan["domain"]
                )
                group_record = group_record or await _first_match(
                    client, "/pulp/default/api/v3/groups/", "name", plan["group"]
                )
                if not domain_record or not group_record:
                    raise PulpError(
                        "domain or group is not available for role assignment",
                        status_code=400,
                    )
                created = await _assign_domain_role(
                    client, group_record, domain_record, correlation_id
                )
        except PulpError as exc:
            return {
                "correlation_id": correlation_id,
                "completed": completed,
                "stopped_at": {
                    "resource": step["resource"],
                    "name": step["name"],
                },
                "message": exc.safe_message,
                "failed": True,
            }
        completed.append({**step, "pulp_href": created.get("pulp_href", "")})

    return {"correlation_id": correlation_id, "completed": completed, "failed": False}


@router.get("/tenants", response_class=HTMLResponse)
async def tenants_page(request: Request) -> HTMLResponse:
    client = request.app.state.client_factory()
    try:
        data = await list_tenants(client)
    finally:
        await client.aclose()
    return templates.TemplateResponse(
        request, "tenants.html", {**data, "warnings": [], "current_user": "operator"}
    )


@router.get("/api/tenants")
async def tenants_api(request: Request) -> JSONResponse:
    client = request.app.state.client_factory()
    try:
        return JSONResponse(await list_tenants(client))
    finally:
        await client.aclose()


@router.post("/api/tenants/plan")
async def tenants_plan(request: Request, payload: dict = Body(...)) -> JSONResponse:
    client = request.app.state.client_factory()
    try:
        plan = await plan_setup(client, payload)
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message}, status_code=exc.status_code or 502
        )
    await client.aclose()
    return JSONResponse(plan)


@router.post("/api/tenants/apply")
async def tenants_apply(request: Request, payload: dict = Body(...)) -> JSONResponse:
    app = request.app
    client = app.state.client_factory()
    correlation_id = app.state.correlations.new()
    try:
        plan = await plan_setup(client, payload)
        result = await apply_setup(client, plan, correlation_id)
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message}, status_code=exc.status_code or 502
        )
    await client.aclose()

    app.state.activity.record(
        {
            "correlation_id": correlation_id,
            "operator": "operator",
            "action": "tenant.setup",
            "target": plan["domain"],
            "result": "failed" if result.get("failed") else "completed",
        }
    )
    return JSONResponse(result, status_code=400 if result.get("failed") else 200)
```

```html
<!-- app/templates/tenants.html -->
{% extends "base.html" %}
{% block content %}
<h2>Tenants</h2>
<h3>Domains</h3>
<ul>{% for domain in domains %}<li>{{ domain.name }}</li>{% endfor %}</ul>
<h3>Users</h3>
<ul>{% for user in users %}<li>{{ user.username }}</li>{% endfor %}</ul>
<h3>Groups</h3>
<ul>{% for group in groups %}<li>{{ group.name }}</li>{% endfor %}</ul>

<h3>Guided tenant setup</h3>
<form id="tenant-setup">
  <label>Domain <input name="domain" required></label>
  <label>Username <input name="username" required></label>
  <label>Group <input name="group" required></label>
  <button type="button" id="plan-button">Preview</button>
  <div id="plan-output"></div>
  <button type="submit" disabled id="apply-button">Apply</button>
</form>
<script>
document.getElementById("plan-button").addEventListener("click", function () {
  var payload = Object.fromEntries(
    new FormData(document.getElementById("tenant-setup")).entries()
  );
  window.pulpOps.postJSON("/ui/api/tenants/plan", payload).then(function (body) {
    document.getElementById("plan-output").textContent = JSON.stringify(body, null, 2);
    document.getElementById("apply-button").disabled = Boolean(body.error);
  });
});
document.getElementById("tenant-setup").addEventListener("submit", function (event) {
  event.preventDefault();
  var payload = Object.fromEntries(new FormData(event.target).entries());
  window.pulpOps.postJSON("/ui/api/tenants/apply", payload).then(function (body) {
    document.getElementById("plan-output").textContent = JSON.stringify(body, null, 2);
  });
});
</script>
{% endblock %}
```

Add to `create_app`: `from app.routes import overview, tenants` and
`app.include_router(tenants.router, prefix="/ui")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tenants.py -v`  
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/routes/tenants.py app/templates/tenants.html tests/test_tenants.py app/main.py
git commit -m "feat: add tenants page and guided tenant setup workflow"
```

---

### Task 7: Content page and per-plugin repository/distribution workflows

**Files:**
- Create: `app/routes/content.py`
- Create: `app/templates/content.html`
- Create: `tests/test_content.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `PulpClient`, `plugin_api`, `validate_name`, `validate_plugin`, `CONTENT_PLUGINS`, `validate_source_url`.
- Produces: `app.routes.content.list_content(client, domain) -> dict`; `create_repository(client, payload, correlation_id) -> dict`; `create_distribution(client, payload, correlation_id) -> dict`; `start_sync(client, payload, correlation_id, settings) -> dict`; endpoints `GET /ui/content`, `GET /ui/api/content`, `POST /ui/api/content/repository`, `POST /ui/api/content/distribution`, `POST /ui/api/content/sync`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_content.py
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client

REPO_PATHS = {
    "rpm": "/pulp/default/api/v3/repositories/rpm/rpm/",
    "deb": "/pulp/default/api/v3/repositories/deb/deb/",
    "python": "/pulp/default/api/v3/repositories/python/python/",
    "ansible": "/pulp/default/api/v3/repositories/ansible/ansible/",
    "container": "/pulp/default/api/v3/repositories/container/container/",
}


@pytest.mark.parametrize("plugin,path", list(REPO_PATHS.items()))
def test_create_repository_uses_plugin_scoped_endpoint(settings, plugin, path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(201, json={"pulp_href": path + "abc/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/repository",
            json={"domain": "default", "plugin": plugin, "name": "demo-repo"},
        )
    assert response.status_code == 200
    assert seen["path"] == path


def test_create_repository_rejects_unknown_plugin(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/repository",
            json={"domain": "default", "plugin": "shell", "name": "x"},
        )
    assert response.status_code == 400


def test_container_distribution_requires_base_path(settings):
    def handler(request):
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/x/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/distribution",
            json={
                "domain": "default",
                "plugin": "container",
                "name": "demo",
                "repository_href": REPO_PATHS["container"] + "abc/",
            },
        )
    assert response.status_code == 400
    assert "base_path" in response.json()["error"]


def test_sync_rejects_non_allowlisted_source(settings):
    def handler(request):
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/x/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            json={
                "domain": "default",
                "plugin": "rpm",
                "repository_href": REPO_PATHS["rpm"] + "abc/",
                "remote_url": "http://169.254.169.254/latest/meta-data/",
            },
        )
    assert response.status_code == 400


def test_sync_returns_task_href(settings):
    def handler(request):
        if request.method == "POST" and "/remotes/" in request.url.path:
            return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/remotes/rpm/rpm/r1/"})
        return httpx.Response(202, json={"task": "/pulp/default/api/v3/tasks/xyz/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/content/sync",
            json={
                "domain": "default",
                "plugin": "rpm",
                "repository_href": REPO_PATHS["rpm"] + "abc/",
                "remote_url": "https://mirror.example.com/pub/rpm/",
            },
        )
    assert response.status_code == 200
    assert response.json()["task_href"] == "/pulp/default/api/v3/tasks/xyz/"


def test_content_listing_covers_every_plugin(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.get("/ui/api/content?domain=default").json()
    assert set(body["plugins"]) == {"rpm", "deb", "python", "ansible", "container"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_content.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.content'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/routes/content.py
from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.endpoints import CONTENT_PLUGINS, plugin_api, validate_name, validate_plugin
from app.pulp import PulpError
from app.safety import validate_source_url

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _record_id(href: str) -> str:
    return href.rstrip("/").split("/")[-1]


async def list_content(client, domain: str) -> dict:
    domain = validate_name("domain", domain)
    out: dict[str, dict] = {}
    for plugin in CONTENT_PLUGINS:
        repos = await client.request(
            "GET", plugin_api(domain, f"repositories/{plugin}/{plugin}/")
        )
        dists = await client.request(
            "GET", plugin_api(domain, f"distributions/{plugin}/{plugin}/")
        )
        out[plugin] = {
            "repositories": [
                {"name": item.get("name"), "pulp_href": item.get("pulp_href")}
                for item in repos.get("results", [])
            ],
            "distributions": [
                {"name": item.get("name"), "pulp_href": item.get("pulp_href")}
                for item in dists.get("results", [])
            ],
        }
    return {"domain": domain, "plugins": out}


def _require_repository_href(domain: str, plugin: str, href: str) -> str:
    expected_prefix = plugin_api(domain, f"repositories/{plugin}/{plugin}/")
    if not href.startswith(expected_prefix):
        raise ValueError("repository href is not valid for this domain and plugin")
    if href.rstrip("/") == expected_prefix.rstrip("/"):
        raise ValueError("repository href must identify a specific repository")
    return href


async def create_repository(client, payload: dict, correlation_id: str) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    plugin = validate_plugin(payload.get("plugin", ""))
    name = validate_name("repository", payload.get("name", ""))
    return await client.request(
        "POST",
        plugin_api(domain, f"repositories/{plugin}/{plugin}/"),
        json_body={"name": name},
        correlation_id=correlation_id,
    )


async def create_distribution(client, payload: dict, correlation_id: str) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    plugin = validate_plugin(payload.get("plugin", ""))
    name = validate_name("distribution", payload.get("name", ""))
    repository_href = _require_repository_href(
        domain, plugin, payload.get("repository_href", "")
    )
    body: dict = {"name": name, "repository": repository_href}
    if plugin == "container":
        base_path = (payload.get("base_path") or "").strip().strip("/")
        if not base_path:
            raise ValueError("base_path is required for container distributions")
        body["base_path"] = base_path
    return await client.request(
        "POST",
        plugin_api(domain, f"distributions/{plugin}/{plugin}/"),
        json_body=body,
        correlation_id=correlation_id,
    )


async def start_sync(client, payload: dict, correlation_id: str, settings) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    plugin = validate_plugin(payload.get("plugin", ""))
    repository_href = _require_repository_href(
        domain, plugin, payload.get("repository_href", "")
    )
    remote_url = validate_source_url(
        payload.get("remote_url", ""), settings.allowed_source_hosts
    )
    remote = await client.request(
        "POST",
        plugin_api(domain, f"remotes/{plugin}/{plugin}/"),
        json_body={
            "name": validate_name(
                "remote", payload.get("remote_name") or "sync-remote"
            ),
            "url": remote_url,
        },
        correlation_id=correlation_id,
    )
    result = await client.request(
        "POST",
        plugin_api(domain, f"repositories/{plugin}/{plugin}/")
        + f"{_record_id(repository_href)}/sync/",
        json_body={"remote": remote["pulp_href"]},
        correlation_id=correlation_id,
    )
    return {
        "task_href": result.get("task", ""),
        "correlation_id": correlation_id,
        "remote_href": remote.get("pulp_href", ""),
    }


async def _guarded(request: Request, handler) -> JSONResponse:
    app = request.app
    client = app.state.client_factory()
    correlation_id = app.state.correlations.new()
    try:
        result = await handler(client, correlation_id)
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message, "correlation_id": exc.correlation_id},
            status_code=exc.status_code or 502,
        )
    await client.aclose()
    return JSONResponse({"correlation_id": correlation_id, **result})


@router.get("/content", response_class=HTMLResponse)
async def content_page(request: Request) -> HTMLResponse:
    domain = request.query_params.get("domain", "default")
    client = request.app.state.client_factory()
    try:
        data = await list_content(client, domain)
    except ValueError as exc:
        await client.aclose()
        return templates.TemplateResponse(
            request,
            "content.html",
            {
                "error": str(exc),
                "plugins": {},
                "domain": domain,
                "warnings": [],
                "current_user": "operator",
            },
            status_code=400,
        )
    except PulpError as exc:
        await client.aclose()
        return templates.TemplateResponse(
            request,
            "content.html",
            {
                "error": exc.safe_message,
                "plugins": {},
                "domain": domain,
                "warnings": [],
                "current_user": "operator",
            },
            status_code=502,
        )
    await client.aclose()
    return templates.TemplateResponse(
        request, "content.html", {**data, "warnings": [], "current_user": "operator"}
    )


@router.get("/api/content")
async def content_api(request: Request) -> JSONResponse:
    domain = request.query_params.get("domain", "default")
    client = request.app.state.client_factory()
    try:
        body = await list_content(client, domain)
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message}, status_code=exc.status_code or 502
        )
    await client.aclose()
    return JSONResponse(body)


@router.post("/api/content/repository")
async def repository_create(request: Request, payload: dict = Body(...)) -> JSONResponse:
    async def handler(client, correlation_id):
        created = await create_repository(client, payload, correlation_id)
        request.app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": "operator",
                "action": "content.repository.create",
                "target": payload.get("name", ""),
                "result": "completed",
            }
        )
        return {"pulp_href": created.get("pulp_href", "")}

    return await _guarded(request, handler)


@router.post("/api/content/distribution")
async def distribution_create(
    request: Request, payload: dict = Body(...)
) -> JSONResponse:
    async def handler(client, correlation_id):
        created = await create_distribution(client, payload, correlation_id)
        return {"pulp_href": created.get("pulp_href", "")}

    return await _guarded(request, handler)


@router.post("/api/content/sync")
async def sync_start(request: Request, payload: dict = Body(...)) -> JSONResponse:
    async def handler(client, correlation_id):
        return await start_sync(
            client, payload, correlation_id, request.app.state.settings
        )

    return await _guarded(request, handler)
```

```html
<!-- app/templates/content.html -->
{% extends "base.html" %}
{% block content %}
<h2>Content</h2>
<form method="get" action="/ui/content">
  <label>Domain <input name="domain" value="{{ domain }}"></label>
  <button type="submit">Load</button>
</form>
{% if error %}<p class="warning">{{ error }}</p>{% endif %}
{% for plugin, data in plugins.items() %}
  <h3>{{ plugin }}</h3>
  <h4>Repositories</h4>
  <ul>{% for repo in data.repositories %}<li>{{ repo.name }}</li>{% endfor %}</ul>
  <h4>Distributions</h4>
  <ul>{% for dist in data.distributions %}<li>{{ dist.name }}</li>{% endfor %}</ul>
{% endfor %}

<h3>Create repository</h3>
<form id="repo-form">
  <label>Domain <input name="domain" value="{{ domain }}" required></label>
  <label>Plugin
    <select name="plugin">
      {% for plugin in ["rpm", "deb", "python", "ansible", "container"] %}
      <option value="{{ plugin }}">{{ plugin }}</option>
      {% endfor %}
    </select>
  </label>
  <label>Name <input name="name" required></label>
  <button type="submit">Create</button>
</form>
<div id="repo-output"></div>
<script>
document.getElementById("repo-form").addEventListener("submit", function (event) {
  event.preventDefault();
  var payload = Object.fromEntries(new FormData(event.target).entries());
  window.pulpOps.postJSON("/ui/api/content/repository", payload).then(function (body) {
    document.getElementById("repo-output").textContent = JSON.stringify(body, null, 2);
  });
});
</script>
{% endblock %}
```

Add to `create_app`: import `content` and `app.include_router(content.router, prefix="/ui")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_content.py -v`  
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add app/routes/content.py app/templates/content.html tests/test_content.py app/main.py
git commit -m "feat: add content page and per-plugin repository/distribution workflows"
```

---

### Task 8: Tasks list, detail, and polling

**Files:**
- Create: `app/routes/tasks.py`
- Create: `app/templates/tasks.html`
- Create: `app/templates/task_detail.html`
- Create: `tests/test_tasks.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `PulpClient`, `plugin_api`, `validate_name`.
- Produces: `app.routes.tasks.list_tasks(client, domain, filters) -> dict`; `app.routes.tasks.task_detail(client, domain, href) -> dict`; endpoints `GET /ui/tasks`, `GET /ui/tasks/detail`, `GET /ui/api/tasks`, `GET /ui/api/tasks/detail`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tasks.py
import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client


def test_task_list_filters_by_state(settings):
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "count": 1,
                "results": [
                    {"state": "failed", "pulp_href": "/pulp/default/api/v3/tasks/1/"}
                ],
            },
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.get("/ui/api/tasks?domain=default&state=failed").json()
    assert body["tasks"][0]["state"] == "failed"
    assert seen["params"]["state"] == "failed"


def test_task_list_rejects_unknown_state(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.get("/ui/api/tasks?domain=default&state=bogus")
    assert response.status_code == 400


def test_task_detail_rejects_foreign_href(settings):
    def handler(request):
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.get(
            "/ui/api/tasks/detail?domain=default&href=/pulp/dummy-beta/api/v3/tasks/1/"
        )
    assert response.status_code == 400


def test_task_detail_truncates_progress_reports(settings):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "state": "running",
                "progress_reports": [{"message": "step", "code": "x"}] * 40,
            },
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.get(
            "/ui/api/tasks/detail?domain=default&href=/pulp/default/api/v3/tasks/1/"
        ).json()
    assert len(body["progress_reports"]) <= 20
    assert body["state"] == "running"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tasks.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.tasks'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/routes/tasks.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.endpoints import plugin_api, validate_name
from app.pulp import PulpError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_ALLOWED_STATES = {"running", "waiting", "completed", "failed", "canceled", "skipped"}
_PROGRESS_LIMIT = 20


def _validated_href(domain: str, href: str) -> str:
    prefix = plugin_api(domain, "tasks/")
    if not href.startswith(prefix):
        raise ValueError("task href is not valid for this domain")
    if href.rstrip("/") == prefix.rstrip("/"):
        raise ValueError("task href must identify a specific task")
    return href


async def list_tasks(client, domain: str, filters: dict) -> dict:
    domain = validate_name("domain", domain)
    params = {}
    state = filters.get("state")
    if state:
        if state not in _ALLOWED_STATES:
            raise ValueError("unsupported task state filter")
        params["state"] = state
    listing = await client.request(
        "GET", plugin_api(domain, "tasks/"), params=params or None
    )
    return {
        "domain": domain,
        "tasks": [
            {
                "pulp_href": item.get("pulp_href"),
                "state": item.get("state"),
                "name": item.get("name"),
                "created": (item.get("pulp_created") or "")[:19],
            }
            for item in listing.get("results", [])
        ],
    }


async def task_detail(client, domain: str, href: str) -> dict:
    domain = validate_name("domain", domain)
    detail = await client.request("GET", _validated_href(domain, href))
    return {
        "pulp_href": detail.get("pulp_href", href),
        "state": detail.get("state"),
        "progress_reports": (detail.get("progress_reports") or [])[-_PROGRESS_LIMIT:],
        "created_resources": detail.get("created_resources") or [],
        "error": _safe_error(detail.get("error")),
    }


def _safe_error(error) -> dict | None:
    if not error:
        return None
    if isinstance(error, dict):
        return {
            "code": error.get("code", ""),
            "description": (error.get("description") or "")[:400],
        }
    return {"code": "", "description": str(error)[:400]}


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request) -> HTMLResponse:
    domain = request.query_params.get("domain", "default")
    state = request.query_params.get("state", "")
    client = request.app.state.client_factory()
    try:
        data = await list_tasks(client, domain, {"state": state})
    except (ValueError, PulpError) as exc:
        await client.aclose()
        message = exc.safe_message if isinstance(exc, PulpError) else str(exc)
        return templates.TemplateResponse(
            request,
            "tasks.html",
            {
                "error": message,
                "tasks": [],
                "domain": domain,
                "state": state,
                "warnings": [],
                "current_user": "operator",
            },
            status_code=400,
        )
    await client.aclose()
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {**data, "state": state, "warnings": [], "current_user": "operator"},
    )


@router.get("/tasks/detail", response_class=HTMLResponse)
async def task_detail_page(request: Request) -> HTMLResponse:
    domain = request.query_params.get("domain", "default")
    href = request.query_params.get("href", "")
    client = request.app.state.client_factory()
    try:
        data = await task_detail(client, domain, href)
    except (ValueError, PulpError) as exc:
        await client.aclose()
        message = exc.safe_message if isinstance(exc, PulpError) else str(exc)
        return templates.TemplateResponse(
            request,
            "task_detail.html",
            {
                "error": message,
                "task": {"pulp_href": href, "state": "", "progress_reports": [], "created_resources": [], "error": None},
                "warnings": [],
                "current_user": "operator",
            },
            status_code=400,
        )
    await client.aclose()
    return templates.TemplateResponse(
        request, "task_detail.html", {"task": data, "warnings": [], "current_user": "operator"}
    )


@router.get("/api/tasks")
async def tasks_api(request: Request) -> JSONResponse:
    client = request.app.state.client_factory()
    try:
        body = await list_tasks(
            client,
            request.query_params.get("domain", "default"),
            {"state": request.query_params.get("state", "")},
        )
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message}, status_code=exc.status_code or 502
        )
    await client.aclose()
    return JSONResponse(body)


@router.get("/api/tasks/detail")
async def task_detail_api(request: Request) -> JSONResponse:
    client = request.app.state.client_factory()
    try:
        body = await task_detail(
            client,
            request.query_params.get("domain", "default"),
            request.query_params.get("href", ""),
        )
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message}, status_code=exc.status_code or 502
        )
    await client.aclose()
    return JSONResponse(body)
```

```html
<!-- app/templates/tasks.html -->
{% extends "base.html" %}
{% block content %}
<h2>Tasks</h2>
<form method="get" action="/ui/tasks">
  <label>Domain <input name="domain" value="{{ domain }}"></label>
  <label>State
    <select name="state">
      <option value="">any</option>
      {% for option in ["running", "waiting", "completed", "failed", "canceled", "skipped"] %}
      <option value="{{ option }}" {% if option == state %}selected{% endif %}>{{ option }}</option>
      {% endfor %}
    </select>
  </label>
  <button type="submit">Filter</button>
</form>
{% if error %}<p class="warning">{{ error }}</p>{% endif %}
<table>
  <thead><tr><th>State</th><th>Name</th><th>Created</th><th>Detail</th></tr></thead>
  <tbody>
  {% for task in tasks %}
    <tr>
      <td>{{ task.state }}</td>
      <td>{{ task.name }}</td>
      <td>{{ task.created }}</td>
      <td><a href="/ui/tasks/detail?domain={{ domain }}&amp;href={{ task.pulp_href }}">open</a></td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

```html
<!-- app/templates/task_detail.html -->
{% extends "base.html" %}
{% block content %}
<h2>Task detail</h2>
{% if error %}<p class="warning">{{ error }}</p>{% endif %}
<dl class="facts">
  <dt>Href</dt><dd>{{ task.pulp_href }}</dd>
  <dt>State</dt><dd id="task-state">{{ task.state }}</dd>
</dl>
{% if task.error %}
<p class="warning">{{ task.error.code }}: {{ task.error.description }}</p>
{% endif %}
<h3>Progress</h3>
<ol id="task-progress">
  {% for report in task.progress_reports %}<li>{{ report.code }} — {{ report.message }}</li>{% endfor %}
</ol>
<h3>Created resources</h3>
<ul>{% for resource in task.created_resources %}<li>{{ resource }}</li>{% endfor %}</ul>
<script>
window.pulpOps.pollTask("{{ task.pulp_href }}", function (body) {
  if (body.state) {
    document.getElementById("task-state").textContent = body.state;
  }
});
</script>
{% endblock %}
```

Add to `create_app`: import `tasks` and `app.include_router(tasks.router, prefix="/ui")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tasks.py -v`  
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/routes/tasks.py app/templates/tasks.html app/templates/task_detail.html tests/test_tasks.py app/main.py
git commit -m "feat: add task list, detail, and progress polling"
```

---

### Task 9: Tenant isolation validation workflow

**Files:**
- Create: `app/routes/validation.py`
- Create: `app/templates/validation.html`
- Create: `tests/test_validation.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `PulpClient`, `RunStore`, `plugin_api`, `validate_name`, `uuid4`.
- Produces: `app.routes.validation.ASSERTIONS: tuple[str, ...]`; `run_validation(client, domain, runs, correlation_id) -> dict`; `cleanup_run(client, run_id, runs, correlation_id) -> dict`; endpoints `GET /ui/validation`, `POST /ui/api/validation/run`, `POST /ui/api/validation/cleanup`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation.py
import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client

REPO_COLLECTION = "/pulp/default/api/v3/repositories/rpm/rpm/"


def test_validation_records_each_assertion_and_resource(settings):
    def handler(request):
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(201, json={"pulp_href": REPO_COLLECTION + "1/"})
        if request.method == "GET" and request.url.path == REPO_COLLECTION:
            return httpx.Response(
                200,
                json={"count": 1, "results": [{"pulp_href": REPO_COLLECTION + "1/"}]},
            )
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", json={"domain": "default"}
        ).json()
    assert body["run_id"]
    assert [item["name"] for item in body["assertions"]] == list(
        ("domain_isolation", "resource_creation", "resource_listing")
    )
    assert all(item["status"] in {"PASS", "FAIL"} for item in body["assertions"])
    assert body["resources"] == [REPO_COLLECTION + "1/"]


def test_validation_reports_failure_when_creation_fails(settings):
    def handler(request):
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(400, json={"name": ["This field must be unique."]})
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.post(
            "/ui/api/validation/run", json={"domain": "default"}
        ).json()
    statuses = {item["name"]: item["status"] for item in body["assertions"]}
    assert statuses["resource_creation"] == "FAIL"
    assert statuses["resource_listing"] == "FAIL"


def test_cleanup_only_deletes_recorded_resources(settings):
    deleted = []

    def handler(request):
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(201, json={"pulp_href": REPO_COLLECTION + "1/"})
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        run = test_client.post(
            "/ui/api/validation/run", json={"domain": "default"}
        ).json()
        body = test_client.post(
            "/ui/api/validation/cleanup", json={"run_id": run["run_id"]}
        ).json()
    assert deleted == [REPO_COLLECTION + "1/"]
    assert body["deleted"] == [REPO_COLLECTION + "1/"]


def test_cleanup_rejects_unknown_run(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/validation/cleanup", json={"run_id": "nope"}
        )
    assert response.status_code == 400


def test_cleanup_run_is_single_use(settings):
    def handler(request):
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "POST" and request.url.path == REPO_COLLECTION:
            return httpx.Response(201, json={"pulp_href": REPO_COLLECTION + "1/"})
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        run = test_client.post(
            "/ui/api/validation/run", json={"domain": "default"}
        ).json()
        test_client.post("/ui/api/validation/cleanup", json={"run_id": run["run_id"]})
        second = test_client.post(
            "/ui/api/validation/cleanup", json={"run_id": run["run_id"]}
        )
    assert second.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_validation.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.validation'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/routes/validation.py
from uuid import uuid4

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.endpoints import plugin_api, validate_name
from app.pulp import PulpError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ASSERTIONS = ("domain_isolation", "resource_creation", "resource_listing")
_OTHER_DOMAIN = {"dummy-beta": "dummy-alpha"}


async def run_validation(client, domain: str, runs, correlation_id: str) -> dict:
    domain = validate_name("domain", domain)
    run_id = uuid4().hex[:12]
    runs.create(run_id)
    assertions: list[dict] = []
    other_domain = _OTHER_DOMAIN.get(domain, "dummy-beta")

    try:
        cross = await client.request(
            "GET", plugin_api(other_domain, "repositories/rpm/rpm/")
        )
        foreign = await client.request(
            "GET",
            plugin_api(domain, "repositories/rpm/rpm/"),
            params={"name": f"isolation-{run_id}"},
        )
        crossed = any(
            item.get("name") == f"isolation-{run_id}"
            for item in cross.get("results", [])
        )
        assertions.append(
            {
                "name": "domain_isolation",
                "status": "PASS" if foreign.get("count", 0) == 0 and not crossed else "FAIL",
                "evidence": (
                    f"own_domain_matches={foreign.get('count', 0)} "
                    f"foreign_domain_leak={crossed}"
                ),
            }
        )
    except PulpError as exc:
        assertions.append(
            {"name": "domain_isolation", "status": "FAIL", "evidence": exc.safe_message}
        )

    repository_href = ""
    try:
        created = await client.request(
            "POST",
            plugin_api(domain, "repositories/rpm/rpm/"),
            json_body={"name": f"isolation-{run_id}"},
            correlation_id=correlation_id,
        )
        repository_href = created.get("pulp_href", "")
        if repository_href:
            runs.add_resource(run_id, repository_href)
        assertions.append(
            {
                "name": "resource_creation",
                "status": "PASS" if repository_href else "FAIL",
                "evidence": repository_href or "no href returned",
            }
        )
    except PulpError as exc:
        assertions.append(
            {"name": "resource_creation", "status": "FAIL", "evidence": exc.safe_message}
        )

    try:
        listing = await client.request("GET", plugin_api(domain, "repositories/rpm/rpm/"))
        found = repository_href in [
            item.get("pulp_href") for item in listing.get("results", [])
        ]
        assertions.append(
            {
                "name": "resource_listing",
                "status": "PASS" if found else "FAIL",
                "evidence": f"listed={found}",
            }
        )
    except PulpError as exc:
        assertions.append(
            {"name": "resource_listing", "status": "FAIL", "evidence": exc.safe_message}
        )

    return {
        "run_id": run_id,
        "domain": domain,
        "correlation_id": correlation_id,
        "assertions": assertions,
        "resources": runs.resources(run_id),
    }


async def cleanup_run(client, run_id: str, runs, correlation_id: str) -> dict:
    resources = runs.resources(run_id)
    if not resources:
        raise ValueError("unknown or empty validation run")
    deleted: list[str] = []
    failures: list[dict] = []
    for href in resources:
        try:
            await client.request("DELETE", href, correlation_id=correlation_id)
        except PulpError as exc:
            failures.append({"href": href, "message": exc.safe_message})
            continue
        deleted.append(href)
    runs.drop(run_id)
    return {
        "correlation_id": correlation_id,
        "deleted": sorted(deleted),
        "failures": failures,
    }


@router.get("/validation", response_class=HTMLResponse)
async def validation_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "validation.html", {"warnings": [], "current_user": "operator"}
    )


@router.post("/api/validation/run")
async def validation_run(request: Request, payload: dict = Body(...)) -> JSONResponse:
    app = request.app
    client = app.state.client_factory()
    correlation_id = app.state.correlations.new()
    try:
        body = await run_validation(
            client, payload.get("domain", "default"), app.state.runs, correlation_id
        )
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    await client.aclose()
    app.state.activity.record(
        {
            "correlation_id": correlation_id,
            "operator": "operator",
            "action": "validation.run",
            "target": body["run_id"],
            "result": "completed",
        }
    )
    return JSONResponse(body)


@router.post("/api/validation/cleanup")
async def validation_cleanup(
    request: Request, payload: dict = Body(...)
) -> JSONResponse:
    app = request.app
    client = app.state.client_factory()
    correlation_id = app.state.correlations.new()
    try:
        body = await cleanup_run(
            client, payload.get("run_id", ""), app.state.runs, correlation_id
        )
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    await client.aclose()
    app.state.activity.record(
        {
            "correlation_id": correlation_id,
            "operator": "operator",
            "action": "validation.cleanup",
            "target": payload.get("run_id", ""),
            "result": "completed",
        }
    )
    return JSONResponse(body)
```

```html
<!-- app/templates/validation.html -->
{% extends "base.html" %}
{% block content %}
<h2>Validation</h2>
<p class="subtitle">Creates uniquely named test resources, then cleans only the hrefs it recorded.</p>
<form id="validation-form">
  <label>Domain <input name="domain" value="default" required></label>
  <button type="submit">Run isolation validation</button>
</form>
<div id="validation-output"></div>
<script>
document.getElementById("validation-form").addEventListener("submit", function (event) {
  event.preventDefault();
  var payload = Object.fromEntries(new FormData(event.target).entries());
  window.pulpOps.postJSON("/ui/api/validation/run", payload).then(function (body) {
    var lines = (body.assertions || []).map(function (item) {
      return item.status + " " + item.name + " — " + item.evidence;
    });
    if (body.run_id) { lines.push("run_id=" + body.run_id); }
    if (body.error) { lines.push("error=" + body.error); }
    document.getElementById("validation-output").textContent = lines.join("\n");
  });
});
</script>
{% endblock %}
```

Add to `create_app`: import `validation` and `app.include_router(validation.router, prefix="/ui")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_validation.py -v`  
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/routes/validation.py app/templates/validation.html tests/test_validation.py app/main.py
git commit -m "feat: add tenant isolation validation with scoped cleanup"
```

---

### Task 10: Delete preview with server-enforced confirmation

**Files:**
- Create: `app/routes/destroy.py`
- Create: `app/templates/confirm_delete.html`
- Create: `tests/test_destroy.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `PulpClient`, `plugin_api`, `validate_name`.
- Produces: `app.routes.destroy.preview_target(client, domain, href) -> dict`; `app.routes.destroy.delete_target(client, domain, href, confirmed, correlation_id) -> dict`; endpoints `GET /ui/delete`, `GET /ui/delete/preview`, `POST /ui/api/delete`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_destroy.py
import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_client

REPO_HREF = "/pulp/default/api/v3/repositories/rpm/rpm/abc/"


def test_preview_returns_current_resource_details(settings):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"name": "demo-repo", "pulp_href": REPO_HREF})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.get(
            f"/ui/delete/preview?domain=default&href={REPO_HREF}"
        ).json()
    assert body["name"] == "demo-repo"
    assert body["type"] == "repositories"
    assert body["domain"] == "default"
    assert body["href"] == REPO_HREF


def test_delete_requires_confirmation(settings):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"name": "demo-repo", "pulp_href": REPO_HREF})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/delete",
            json={"domain": "default", "href": REPO_HREF, "confirmed": False},
        )
    assert response.status_code == 400
    assert "DELETE" not in calls


def test_delete_refetches_then_deletes_exact_href(settings):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"name": "demo-repo", "pulp_href": REPO_HREF})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        body = test_client.post(
            "/ui/api/delete",
            json={"domain": "default", "href": REPO_HREF, "confirmed": True},
        ).json()
    assert seen == [("GET", REPO_HREF), ("GET", REPO_HREF), ("DELETE", REPO_HREF)]
    assert body["deleted"] == REPO_HREF


def test_delete_rejects_href_outside_requested_domain(settings):
    def handler(request):
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/delete",
            json={
                "domain": "default",
                "href": "/pulp/dummy-beta/api/v3/repositories/rpm/rpm/abc/",
                "confirmed": True,
            },
        )
    assert response.status_code == 400


def test_delete_rejects_collection_href(settings):
    def handler(request):
        return httpx.Response(200, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ui/api/delete",
            json={
                "domain": "default",
                "href": "/pulp/default/api/v3/repositories/rpm/rpm/",
                "confirmed": True,
            },
        )
    assert response.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_destroy.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.destroy'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/routes/destroy.py
from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.endpoints import plugin_api, validate_name
from app.pulp import PulpError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _validated_href(domain: str, href: str) -> str:
    prefix = plugin_api(domain, "")
    if not href.startswith(prefix):
        raise ValueError("href is not valid for this domain")
    if href.rstrip("/") == prefix.rstrip("/"):
        raise ValueError("href must identify a specific resource")
    return href


def _resource_type(href: str) -> str:
    # href shape: pulp/<domain>/api/v3/<type>/<plugin>/<plugin>/<id>/
    parts = [part for part in href.split("/") if part]
    return parts[4] if len(parts) > 4 else "resource"


async def preview_target(client, domain: str, href: str) -> dict:
    domain = validate_name("domain", domain)
    href = _validated_href(domain, href)
    record = await client.request("GET", href)
    return {
        "href": record.get("pulp_href", href),
        "type": _resource_type(href),
        "name": record.get("name") or record.get("username") or "",
        "domain": domain,
        "dependencies": record.get("repository") or record.get("remote") or "",
    }


async def delete_target(
    client, domain: str, href: str, confirmed: bool, correlation_id: str
) -> dict:
    domain = validate_name("domain", domain)
    href = _validated_href(domain, href)
    if confirmed is not True:
        raise ValueError("delete requires explicit confirmation")
    await client.request("GET", href)
    await client.request("GET", href)
    await client.request("DELETE", href, correlation_id=correlation_id)
    return {"correlation_id": correlation_id, "deleted": href, "domain": domain}


@router.get("/delete", response_class=HTMLResponse)
async def delete_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "confirm_delete.html", {"warnings": [], "current_user": "operator"}
    )


@router.get("/delete/preview")
async def delete_preview(request: Request) -> JSONResponse:
    client = request.app.state.client_factory()
    try:
        body = await preview_target(
            client,
            request.query_params.get("domain", "default"),
            request.query_params.get("href", ""),
        )
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message, "correlation_id": exc.correlation_id},
            status_code=exc.status_code or 502,
        )
    await client.aclose()
    return JSONResponse(body)


@router.post("/api/delete")
async def delete_api(request: Request, payload: dict = Body(...)) -> JSONResponse:
    app = request.app
    client = app.state.client_factory()
    correlation_id = app.state.correlations.new()
    try:
        body = await delete_target(
            client,
            payload.get("domain", "default"),
            payload.get("href", ""),
            payload.get("confirmed") is True,
            correlation_id,
        )
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message, "correlation_id": exc.correlation_id},
            status_code=exc.status_code or 502,
        )
    await client.aclose()
    app.state.activity.record(
        {
            "correlation_id": correlation_id,
            "operator": "operator",
            "action": "resource.delete",
            "target": body["deleted"],
            "result": "completed",
        }
    )
    return JSONResponse(body)
```

```html
<!-- app/templates/confirm_delete.html -->
{% extends "base.html" %}
{% block content %}
<h2>Delete resource</h2>
<form id="delete-form">
  <label>Domain <input name="domain" value="default" required></label>
  <label>Resource href <input name="href" size="80" required></label>
  <button type="button" id="preview-button">Preview</button>
  <dl id="delete-preview" class="facts" hidden></dl>
  <label><input type="checkbox" id="confirm-check"> I have reviewed this exact resource and want to delete it</label>
  <button type="submit">Delete</button>
</form>
<div id="delete-output"></div>
<script>
document.getElementById("preview-button").addEventListener("click", function () {
  var form = document.getElementById("delete-form");
  var params = new URLSearchParams(Object.fromEntries(new FormData(form).entries()));
  fetch("/ui/delete/preview?" + params.toString(), { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (body) {
      var list = document.getElementById("delete-preview");
      list.hidden = false;
      list.textContent = "";
      [["Type", body.type], ["Name", body.name], ["Domain", body.domain], ["Href", body.href]]
        .forEach(function (pair) {
          var dt = document.createElement("dt");
          dt.textContent = pair[0];
          var dd = document.createElement("dd");
          dd.textContent = pair[1] || "";
          list.appendChild(dt);
          list.appendChild(dd);
        });
    });
});
document.getElementById("delete-form").addEventListener("submit", function (event) {
  event.preventDefault();
  var data = new FormData(event.target);
  window.pulpOps.postJSON("/ui/api/delete", {
    domain: data.get("domain"),
    href: data.get("href"),
    confirmed: document.getElementById("confirm-check").checked
  }).then(function (body) {
    document.getElementById("delete-output").textContent = JSON.stringify(body, null, 2);
  });
});
</script>
{% endblock %}
```

Add to `create_app`: import `destroy` and `app.include_router(destroy.router, prefix="/ui")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_destroy.py -v`  
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/routes/destroy.py app/templates/confirm_delete.html tests/test_destroy.py app/main.py
git commit -m "feat: add delete preview with server-enforced confirmation"
```

---

### Task 11: Activity page and credential redaction

**Files:**
- Create: `app/routes/activity.py`
- Create: `app/logging_config.py`
- Create: `app/templates/activity.html`
- Create: `tests/test_activity.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `ActivityStore`.
- Produces: `app.logging_config.REDACT_KEYS: frozenset[str]`; `app.logging_config.redact(record: dict) -> dict`; `app.logging_config.configure_logging() -> None`; endpoints `GET /ui/activity`, `GET /ui/api/activity`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_activity.py
from fastapi.testclient import TestClient

from app.logging_config import redact
from app.main import create_app


def test_redact_removes_credentials_and_bodies():
    record = {
        "action": "tenant.setup",
        "authorization": "Basic YWRtaW46c2VjcmV0",
        "password": "hunter2",
        "body": {"password": "hunter2"},
        "correlation_id": "abc",
    }
    cleaned = redact(record)
    assert cleaned["action"] == "tenant.setup"
    assert cleaned["correlation_id"] == "abc"
    assert "authorization" not in cleaned
    assert "password" not in cleaned
    assert "body" not in cleaned
    assert "hunter2" not in str(cleaned)
    assert "Basic " not in str(cleaned)


def test_redact_is_case_insensitive():
    cleaned = redact({"Authorization": "x", "PASSWORD": "y", "action": "z"})
    assert cleaned == {"action": "z"}


def test_activity_endpoint_lists_recorded_entries(settings):
    class StubClient:
        async def ping(self):
            return True

        async def aclose(self):
            return None

    app = create_app(settings, client_factory=lambda: StubClient())
    with TestClient(app) as test_client:
        app.state.activity.record(
            {
                "correlation_id": "c1",
                "operator": "operator",
                "action": "validation.run",
                "target": "run-1",
                "result": "completed",
            }
        )
        body = test_client.get("/ui/api/activity").json()
    assert body["entries"][0]["action"] == "validation.run"
    assert body["entries"][0]["correlation_id"] == "c1"


def test_activity_limit_is_capped(settings):
    class StubClient:
        async def ping(self):
            return True

        async def aclose(self):
            return None

    app = create_app(settings, client_factory=lambda: StubClient())
    with TestClient(app) as test_client:
        for index in range(300):
            app.state.activity.record(
                {
                    "correlation_id": str(index),
                    "operator": "operator",
                    "action": "x",
                    "target": "y",
                    "result": "completed",
                }
            )
        body = test_client.get("/ui/api/activity?limit=500").json()
    assert len(body["entries"]) <= 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_activity.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app.logging_config'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/logging_config.py
import logging

REDACT_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "token",
        "secret",
        "body",
        "json",
        "content",
        "pulp_admin_password",
        "ui_password_hash",
    }
)


def redact(record: dict) -> dict:
    return {
        key: value for key, value in record.items() if key.lower() not in REDACT_KEYS
    }


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
```

```python
# app/routes/activity.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_MAX_LIMIT = 200


@router.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request) -> HTMLResponse:
    entries = request.app.state.activity.recent()
    return templates.TemplateResponse(
        request,
        "activity.html",
        {"entries": entries, "warnings": [], "current_user": "operator"},
    )


@router.get("/api/activity")
async def activity_api(request: Request) -> JSONResponse:
    try:
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        return JSONResponse({"error": "limit must be an integer"}, status_code=400)
    return JSONResponse(
        {"entries": request.app.state.activity.recent(max(1, min(limit, _MAX_LIMIT)))}
    )
```

```html
<!-- app/templates/activity.html -->
{% extends "base.html" %}
{% block content %}
<h2>Activity</h2>
<p class="subtitle">In-process only. Entries disappear when the pod restarts.</p>
<table>
  <thead><tr><th>Action</th><th>Target</th><th>Result</th><th>Correlation</th></tr></thead>
  <tbody>
  {% for entry in entries %}
    <tr>
      <td>{{ entry.action }}</td>
      <td>{{ entry.target }}</td>
      <td>{{ entry.result }}</td>
      <td>{{ entry.correlation_id }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

Add to `create_app`: `configure_logging()` call, import `activity`, and
`app.include_router(activity.router, prefix="/ui")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_activity.py -v`  
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/logging_config.py app/routes/activity.py app/templates/activity.html tests/test_activity.py app/main.py
git commit -m "feat: add activity page and credential redaction"
```

---

### Task 12: Basic Auth middleware, CSRF enforcement, full sweep

**Files:**
- Create: `app/middleware.py`
- Create: `tests/test_middleware.py`
- Modify: `app/main.py`
- Modify: `tests/test_tenants.py`, `tests/test_content.py`, `tests/test_validation.py`, `tests/test_destroy.py` (add CSRF headers to mutation calls)

**Interfaces:**
- Consumes: `check_credentials`, `issue_csrf`, `validate_csrf`, `Settings`.
- Produces: `app.middleware.BasicAuthMiddleware`, `app.middleware.CsrfMiddleware`, `app.middleware.EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})`, cookie name `pulp_ops_csrf`, response header `X-CSRF-Token`. Auth middleware sets `request.state.username`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_middleware.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_middleware.py -v`  
Expected: FAIL — `/ui/api/activity` returns 200 without auth

- [ ] **Step 3: Write minimal implementation**

```python
# app/middleware.py
import base64
import binascii

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse

from app.security import check_credentials, issue_csrf, validate_csrf

EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})
CSRF_COOKIE = "pulp_ops_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_REALM = 'Basic realm="pulp-ops"'


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"error": "unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": _REALM},
    )


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        settings = request.app.state.settings
        header = request.headers.get("authorization", "")
        username = password = ""
        if header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
                username, _, password = decoded.partition(":")
            except (binascii.Error, UnicodeDecodeError):
                username = password = ""

        if not check_credentials(username, password, settings):
            return _unauthorized()

        request.state.username = username
        response = await call_next(request)
        if request.method in _SAFE_METHODS and response.status_code < 400:
            nonce, token = issue_csrf(settings.session_secret)
            response.set_cookie(
                CSRF_COOKIE,
                nonce,
                httponly=False,
                samesite="strict",
                secure=request.url.scheme == "https",
                path="/ui",
            )
            response.headers[CSRF_HEADER] = token
        return response


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        settings = request.app.state.settings
        nonce = request.cookies.get(CSRF_COOKIE)
        token = request.headers.get(CSRF_HEADER)
        if not validate_csrf(settings.session_secret, nonce, token):
            return PlainTextResponse("csrf validation failed", status_code=403)
        return await call_next(request)
```

Middleware order matters: Basic Auth must run first so an unauthenticated
mutation gets 401, not 403. In `create_app`, add CSRF first, then Auth:

```python
    from app.middleware import BasicAuthMiddleware, CsrfMiddleware

    app.add_middleware(CsrfMiddleware)
    app.add_middleware(BasicAuthMiddleware)
```

Also call `configure_logging()` at module import in `app/main.py`:

```python
from app.logging_config import configure_logging

configure_logging()
```

- [ ] **Step 4: Update earlier mutation tests to send the CSRF token**

Add these imports and headers to the POST calls in the four test files. The
`tests.helpers.csrf_headers` helper already exists from Task 5.

```python
# tests/test_tenants.py — add import
from tests.helpers import csrf_headers, make_client
```

Then in every `test_client.post(...)` call in that file, add `headers=csrf_headers(test_client)` before the `json=` argument. Example:

```python
    with TestClient(app) as test_client:
        body = test_client.post(
            "/ui/api/tenants/plan",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        ).json()
```

Apply the identical change to:

- `tests/test_tenants.py`: `test_plan_setup_reuses_existing_domain`, `test_apply_setup_stops_on_first_failure`, `test_plan_rejects_invalid_domain`
- `tests/test_content.py`: `test_create_repository_uses_plugin_scoped_endpoint`, `test_create_repository_rejects_unknown_plugin`, `test_container_distribution_requires_base_path`, `test_sync_rejects_non_allowlisted_source`, `test_sync_returns_task_href`
- `tests/test_validation.py`: `test_validation_records_each_assertion_and_resource`, `test_validation_reports_failure_when_creation_fails`, `test_cleanup_only_deletes_recorded_resources`, `test_cleanup_rejects_unknown_run`, `test_cleanup_run_is_single_use`
- `tests/test_destroy.py`: `test_delete_requires_confirmation`, `test_delete_refetches_then_deletes_exact_href`, `test_delete_rejects_href_outside_requested_domain`, `test_delete_rejects_collection_href`

Each file needs `from tests.helpers import csrf_headers, make_client` replacing its
current `from tests.helpers import make_client` line.

- [ ] **Step 5: Run the full sweep**

Run: `python -m pytest -v`  
Expected: PASS — every test from Tasks 1-12, including the 8 new middleware tests

- [ ] **Step 6: Commit**

```bash
git add app/middleware.py app/main.py tests
git commit -m "feat: require Basic Auth and CSRF for all UI routes"
```

---

### Task 13: Container image and linsa manifests

**Files:**
- Create (in `pulp-ui`): `Dockerfile`, `.dockerignore`, `app/__main__.py`
- Create (in `linsa`): `manifests/pulp/ui/pulp-ops-config-cm.yaml`
- Create (in `linsa`): `manifests/pulp/ui/pulp-ops-deployment.yaml`
- Create (in `linsa`): `manifests/pulp/ui/pulp-ops-service.yaml`
- Create (in `linsa`): `manifests/pulp/ui/README.md`
- Modify (in `linsa`): `manifests/pulp/ui/pulp-ui-ingress.yaml`

**Interfaces:**
- Consumes: the application from Tasks 1-12.
- Produces: image entrypoint `uvicorn app.main:create_app --factory`; Service `pulp-ops-svc:8080`; required Secret names `pulp-ops-auth` (keys `password-hash`, `session-secret`) and `pulp-admin-credentials` (keys `username`, `password`).

- [ ] **Step 1: Write the failing check**

```python
# scripts/check-ui-manifests.py (in linsa)
import pathlib
import sys

import yaml

paths = sorted(pathlib.Path("manifests/pulp/ui").glob("pulp-ops-*.yaml"))
missing = [
    name
    for name in (
        "pulp-ops-config-cm.yaml",
        "pulp-ops-deployment.yaml",
        "pulp-ops-service.yaml",
    )
    if not (pathlib.Path("manifests/pulp/ui") / name).exists()
]
if missing:
    print(f"missing manifests: {', '.join(missing)}")
    sys.exit(1)
for path in paths:
    list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
print(f"ok: {len(paths)} manifests")
```

- [ ] **Step 2: Confirm the check fails**

Run (in `linsa`): `python scripts/check-ui-manifests.py`  
Expected: FAIL with `missing manifests: pulp-ops-config-cm.yaml, pulp-ops-deployment.yaml, pulp-ops-service.yaml`

- [ ] **Step 3: Write the artifacts**

```dockerfile
# Dockerfile (pulp-ui)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

COPY app ./app

RUN useradd --system --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app
USER 10001

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
```

```
# .dockerignore (pulp-ui)
.git
tests
docs
.venv
__pycache__
*.pyc
```

```python
# app/__main__.py (pulp-ui)
from app.config import load_settings
from app.logging_config import configure_logging
from app.main import create_app


def main() -> None:
    import uvicorn

    configure_logging()
    uvicorn.run(create_app(load_settings()), host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
```

```yaml
# linsa/manifests/pulp/ui/pulp-ops-config-cm.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: pulp-ops-config
  namespace: pulp
data:
  PULP_INTERNAL_URL: "http://pulp-api-svc:24817"
  PUBLIC_DIAGNOSTIC_URL: "https://pulp.dev.tbs.cloudeka.xyz"
  ALLOWED_SOURCE_HOSTS: ""
  REQUEST_TIMEOUT_SECONDS: "15"
  MAX_RESPONSE_BYTES: "4194304"
  UI_USERNAME: "operator"
```

```yaml
# linsa/manifests/pulp/ui/pulp-ops-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pulp-ops
  namespace: pulp
  labels:
    app.kubernetes.io/name: pulp-ops
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: pulp-ops
  template:
    metadata:
      labels:
        app.kubernetes.io/name: pulp-ops
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: pulp-ops
          image: wushie/pulp-ui:0.2.0
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
          envFrom:
            - configMapRef:
                name: pulp-ops-config
          env:
            - name: PULP_ADMIN_USER
              valueFrom:
                secretKeyRef:
                  name: pulp-admin-credentials
                  key: username
            - name: PULP_ADMIN_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: pulp-admin-credentials
                  key: password
            - name: UI_PASSWORD_HASH
              valueFrom:
                secretKeyRef:
                  name: pulp-ops-auth
                  key: password-hash
            - name: SESSION_SECRET
              valueFrom:
                secretKeyRef:
                  name: pulp-ops-auth
                  key: session-secret
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            initialDelaySeconds: 10
            periodSeconds: 20
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
```

```yaml
# linsa/manifests/pulp/ui/pulp-ops-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: pulp-ops-svc
  namespace: pulp
  labels:
    app.kubernetes.io/name: pulp-ops
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: pulp-ops
  ports:
    - name: http
      port: 8080
      targetPort: http
```

Add the `/ui/api` path to `linsa/manifests/pulp/ui/pulp-ui-ingress.yaml` and repoint
every existing path to `pulp-ops-svc`. Keep the old `pulp-ui-svc` reference until
Task 14 removes the old workload.

```yaml
          - path: /ui/api
            pathType: Prefix
            backend:
              service:
                name: pulp-ops-svc
                port:
                  number: 8080
```

```markdown
<!-- linsa/manifests/pulp/ui/README.md -->
# Pulp Operator UI manifests

Source: `https://github.com/wussh/pulp-ui`.

- `pulp-ops-config-cm.yaml` — non-sensitive configuration.
- `pulp-ops-deployment.yaml` — application workload in namespace `pulp`.
- `pulp-ops-service.yaml` — ClusterIP Service `pulp-ops-svc:8080`.
- `pulp-ui-ingress.yaml` — public `/ui` routing.

Required Secrets, created out of band, never committed:

- `pulp-ops-auth` with keys `password-hash` (scrypt string from
  `python -c "from app.security import hash_password; print(hash_password('<password>'))"`)
  and `session-secret` (random 32-byte hex).
- `pulp-admin-credentials` with keys `username` and `password`.

Rollout, diff review, and cutover require separate operator approval. This
directory does not describe a self-applying ArgoCD Application yet.
```

- [ ] **Step 4: Re-run the check to verify it passes**

Run (in `pulp-ui`): `python -m pytest`  
Run (in `linsa`): `python scripts/check-ui-manifests.py`  
Expected: pytest PASS; `ok: 3 manifests`

- [ ] **Step 5: Commit**

```bash
# in pulp-ui
git add Dockerfile .dockerignore app/__main__.py
git commit -m "feat: add container image and module entrypoint"

# in linsa
git add manifests/pulp/ui scripts/check-ui-manifests.py
git commit -m "feat(pulp): add operator UI deployment and service manifests"
```

---

### Task 14: Read-only live smoke, disposable mutation test, cutover verification

**Files:**
- Create (in `pulp-ui`): `scripts/pulp-ops-smoke.py`
- Create (in `linsa`): `docs/runbooks/pulp/operator-ui-cutover.md`
- Modify (in `linsa`): `manifests/pulp/ui/pulp-ui-ingress.yaml`

**Interfaces:**
- Consumes: a running `pulp-ops` pod reachable through port-forward.
- Produces: `scripts/pulp-ops-smoke.py` exiting 0 on pass and non-zero on failure; the cutover runbook.

**This task changes the cluster. Do not run any step until the operator explicitly approves each cluster-touching command.**

- [ ] **Step 1: Write the read-only smoke script**

```python
# scripts/pulp-ops-smoke.py (pulp-ui)
"""Read-only smoke test for the Pulp operator UI.

Usage: python scripts/pulp-ops-smoke.py <base-url> <user> <password>
Exits non-zero on the first failed check.
"""
import base64
import json
import sys
import urllib.error
import urllib.request

base, user, password = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
auth = base64.b64encode(f"{user}:{password}".encode()).decode()


def call(path, method="GET", payload=None, csrf=None):
    request = urllib.request.Request(f"{base}{path}", method=method)
    request.add_header("Authorization", f"Basic {auth}")
    if csrf:
        request.add_header("X-CSRF-Token", csrf)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(payload).encode()
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return response.status, (json.loads(body) if body else {}), response.headers


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'} {name} {detail}".rstrip())
    if not condition:
        raise SystemExit(1)


try:
    status, _, _ = call("/ui/api/activity?limit=1")
    check("anonymous_rejected", False, "anonymous request succeeded")
except urllib.error.HTTPError as error:
    check("anonymous_rejected", error.code == 401, f"status={error.code}")

status, _, headers = call("/ui/api/activity?limit=1")
check("authenticated", status == 200)

_, overview, _ = call("/ui/api/overview")
check("pulp_reachable", overview["pulp"]["reachable"] is True)
check("overview_has_counts", "domains" in overview.get("counts", {}))
check("routing_reported", "public_status" in overview["routing"])

_, tenants, _ = call("/ui/api/tenants")
check("domains_listed", len(tenants["domains"]) >= 1)

_, tasks, _ = call("/ui/api/tasks?domain=default")
check("tasks_listed", "tasks" in tasks)

_, content, _ = call("/ui/api/content?domain=default")
check(
    "content_listed",
    set(content["plugins"]) == {"rpm", "deb", "python", "ansible", "container"},
)

print("read-only smoke ok")
print("mutation smoke requires a disposable domain; run manually per the runbook")
```

- [ ] **Step 2: Run the read-only smoke test after approval**

Cluster-changing prerequisites, each requiring explicit approval:

1. Create the two Secrets out of band.
2. Apply `pulp-ops-config-cm.yaml`, `pulp-ops-service.yaml`, `pulp-ops-deployment.yaml`.
3. Confirm the `pulp-ops` pod is `Running` and `/readyz` returns 200.
4. `kubectl -n pulp port-forward deploy/pulp-ops 8080:8080`.

Then run:

```bash
rtk python scripts/pulp-ops-smoke.py http://127.0.0.1:8080 operator '<ui-password>'
```

Expected: every line `PASS`, then `read-only smoke ok`, exit code 0.

- [ ] **Step 3: Run one disposable mutation test**

Requires explicit approval. Use a throwaway domain, never `default`, `dummy-alpha`, or `dummy-beta`:

1. Create the domain through `/ui/api/tenants/apply` or directly in Pulp.
2. `POST /ui/api/content/repository` with the disposable domain, plugin `rpm`, name `cutover-check`.
3. Record the returned `pulp_href`.
4. `GET /ui/delete/preview?domain=<disposable>&href=<href>` — confirm type, name, domain, href.
5. `POST /ui/api/delete` with `confirmed: false` — expect 400, nothing deleted.
6. `POST /ui/api/delete` with `confirmed: true` — expect 200.
7. `GET <pulp_href>` through Pulp directly — expect 404.

Each mutation needs the `X-CSRF-Token` value from a preceding GET. Record every
request, status code, and correlation ID in
`docs/runbooks/pulp/operator-ui-cutover.md`.

- [ ] **Step 4: Write the cutover runbook**

```markdown
# Pulp Operator UI cutover

Replaces `wushie/pulp-ui:0.1.24` with the Pulp operator UI from
`https://github.com/wussh/pulp-ui` on the existing `/ui/` path.

## Preconditions

- `pulp-ops` pod Running with `/readyz` returning 200.
- Read-only smoke script passes.
- Disposable mutation test passes and its resources are deleted.
- No credential appears in logs:
  `kubectl -n pulp logs deploy/pulp-ops | grep -i -E 'password|authorization|basic '`
  returns nothing.

## Cutover

1. Apply the updated `pulp-ui-ingress.yaml` so `/ui`, `/ui/api`,
   `/static/pulp_ui`, and `/pulp-ui-config.json` point at `pulp-ops-svc:8080`.
2. Verify through the ingress: `/ui/` returns 200 after Basic Auth.
3. Verify Pulp API, content, and registry routes are unchanged
   (`/pulp/api/v3/status/` 200, `/v2/` 401 challenge).

## Removal of the old workload

Only after step 2 and step 3 pass:

1. Scale the old `pulp-ui` Deployment to zero and confirm `/ui/` still works.
2. Delete the old `pulp-ui` Deployment, `pulp-ui-svc` Service, and
   `pulp-ui-config` ConfigMap.
3. Confirm no Ingress still references `pulp-ui-svc`.

## Rollback

Re-apply the previous `pulp-ui-ingress.yaml` revision and scale the old
`pulp-ui` Deployment back to 1.

## Out of scope

The public hostname `pulp.dev.tbs.cloudeka.xyz` currently resolves to the
kgateway load balancer, which has no Pulp route. Repairing that routing is
separate infrastructure work and must not be bundled into this cutover.
```

- [ ] **Step 5: Commit**

```bash
# in pulp-ui
git add scripts/pulp-ops-smoke.py
git commit -m "test: add read-only operator UI smoke script"

# in linsa
git add docs/runbooks/pulp/operator-ui-cutover.md manifests/pulp/ui/pulp-ui-ingress.yaml
git commit -m "docs(pulp): add operator UI cutover runbook and repoint ingress"
```

---

## Self-Review Notes

- **Spec coverage:** architecture (T5, T12), auth and secrets (T2, T12, T13), overview (T5), tenants (T6), content (T7), validation (T9), tasks (T8), activity (T11), destructive operations (T10), input and network safety (T4), error handling (T3), deployment (T13), testing (T1-T12, T14), acceptance criteria (T14).
- **Deliberate deviation from the spec:** the spec leaves CSRF as a middleware concern and does not say where the browser gets the token. This plan issues `X-CSRF-Token` on safe responses and reuses it through `window.pulpOps.postJSON`. The `pulp_ops_csrf` cookie is not `httponly`, because the token is delivered by response header rather than read from the cookie — nothing reads the cookie client-side, so this is safe, and the cookie doubles as the double-submit nonce.
- **Task 12 ordering:** the middleware lands last, so Tasks 6, 7, 9, and 10 include a Task 12 step that adds `headers=csrf_headers(test_client)` to their mutation tests. Skipping that step leaves those tests failing with 403.
- **`readOnlyRootFilesystem: true`** is satisfied only if the application writes nothing to disk. If a later task adds disk writes, add an `emptyDir` mount at that path.
- **The `public_diagnostic_url` check** performs an outbound HTTPS request from inside the pod. If cluster egress is unavailable, `/ui/api/overview` reports the routing warning; it does not fail the page.
