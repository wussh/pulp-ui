"""Tests for the sync source-host allowlist editor.

Covers the validation rules, the fixed ConfigMap path/key, the no-write path for an
unchanged value, auth/CSRF, and the end-to-end effect on validate_source_url.
"""

import asyncio

import httpx
import pytest

from app.config import (
    canonicalize_allowed_source_hosts,
    parse_allowed_source_hosts,
)
from app.k8s import (
    CONFIGMAP_ALLOWED_HOSTS_KEY,
    CONFIGMAP_NAME,
    CONFIGMAP_NAMESPACE,
    ConfigMapStore,
    KubernetesClient,
)
from app.main import create_app
from tests.helpers import (
    FakeConfigMapStore,
    authed_client,
    csrf_headers,
    make_client,
)

FIXED_PATH = (
    f"/api/v1/namespaces/{CONFIGMAP_NAMESPACE}/configmaps/{CONFIGMAP_NAME}"
)

VALID_HOSTS = ("alpha.example.com", "beta.example.com")


def build_app(settings, fake_configmap, client_factory=None):
    kwargs = {"configmap_factory": lambda: fake_configmap}
    if client_factory is not None:
        kwargs["client_factory"] = client_factory
    return create_app(settings, **kwargs)


def test_update_issues_one_request_to_the_fixed_configmap_path(settings):
    """The write goes to the hardcoded path, one PATCH, canonicalized value."""
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(200, json={"kind": "ConfigMap"})

    client = KubernetesClient(settings, transport=httpx.MockTransport(handler))
    store = ConfigMapStore(settings, client=client)

    asyncio.run(
        store.set_value(CONFIGMAP_ALLOWED_HOSTS_KEY, "beta.example.com,alpha.example.com")
    )
    asyncio.run(store.aclose())

    assert len(seen) == 1
    method, path, body = seen[0]
    assert method == "PATCH"
    assert path == FIXED_PATH
    assert b'"beta.example.com,alpha.example.com"' in body


def test_update_route_writes_canonical_sorted_value_exactly_once(settings):
    fake = FakeConfigMapStore()
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/allowlist",
            headers=csrf_headers(test_client),
            json={"hosts": "Zeta.Example.COM, alpha.example.com , beta.example.com"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert body["value"] == "alpha.example.com,beta.example.com,zeta.example.com"
    assert body["count"] == 3
    assert len(fake.writes) == 1
    assert fake.writes[0]["key"] == CONFIGMAP_ALLOWED_HOSTS_KEY
    assert fake.writes[0]["value"] == "alpha.example.com,beta.example.com,zeta.example.com"


@pytest.mark.parametrize(
    "bad_value",
    [
        "https://example.com",  # scheme
        "example.com:8443",  # port
        "*.example.com",  # wildcard
        "example.com/path",  # path
        "user@example.com",  # credentials
        "192.168.1.10",  # IPv4 literal
        "2001:db8::1",  # IPv6 literal
        "example.com,,other.example.com",  # blank entry from ",,"
        "example.com,",  # trailing comma -> blank entry
        ".example.com",  # leading dot
    ],
)
def test_invalid_values_are_rejected_with_400_and_no_write(settings, bad_value):
    fake = FakeConfigMapStore()
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/allowlist",
            headers=csrf_headers(test_client),
            json={"hosts": bad_value},
        )
    assert response.status_code == 400
    assert fake.writes == []


def test_hostname_longer_than_253_characters_is_rejected(settings):
    fake = FakeConfigMapStore()
    app = build_app(settings, fake)
    long_host = "a" * 254 + ".example.com"
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/allowlist",
            headers=csrf_headers(test_client),
            json={"hosts": long_host},
        )
    assert response.status_code == 400
    assert fake.writes == []


def test_store_refuses_any_key_other_than_the_allowlist():
    """The write key is fixed; a caller cannot patch a sibling key."""
    from app.k8s import SecretError

    client = KubernetesClient.__new__(KubernetesClient)
    store = ConfigMapStore.__new__(ConfigMapStore)
    store._settings = None
    store._client = client
    store._owns_client = False
    store._namespace = CONFIGMAP_NAMESPACE

    async def attempt():
        try:
            await store.set_value("OTHER_KEY", "x")
        except SecretError as exc:
            return exc.safe_message
        return None

    message = asyncio.run(attempt())
    assert message == "config key is not on the allowlist."
    # Path itself is fixed to the one ConfigMap.
    assert store._path == FIXED_PATH


def test_more_than_fifty_entries_is_rejected(settings):
    fake = FakeConfigMapStore()
    app = build_app(settings, fake)
    hosts = ",".join(f"h{i}.example.com" for i in range(51))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/allowlist",
            headers=csrf_headers(test_client),
            json={"hosts": hosts},
        )
    assert response.status_code == 400
    assert fake.writes == []


def test_unchanged_value_returns_changed_false_and_writes_nothing(settings):
    fake = FakeConfigMapStore(
        {CONFIGMAP_ALLOWED_HOSTS_KEY: "alpha.example.com,beta.example.com"}
    )
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        # Same set, different order/case: canonicalization makes it a no-op.
        response = test_client.post(
            "/ui/api/allowlist",
            headers=csrf_headers(test_client),
            json={"hosts": "beta.example.com, ALPHA.example.com"},
        )
    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert fake.writes == []


def test_unknown_request_field_is_rejected(settings):
    fake = FakeConfigMapStore()
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/allowlist",
            headers=csrf_headers(test_client),
            json={"hosts": "alpha.example.com", "key": "OTHER_KEY"},
        )
    assert response.status_code == 400
    assert fake.writes == []


def test_route_requires_auth(settings):
    from fastapi.testclient import TestClient

    fake = FakeConfigMapStore()
    app = build_app(settings, fake)
    with TestClient(app) as test_client:
        response = test_client.post("/ui/api/allowlist", json={"hosts": "x.example.com"})
    assert response.status_code == 401
    assert fake.writes == []


def test_route_requires_csrf(settings):
    fake = FakeConfigMapStore()
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/allowlist", json={"hosts": "alpha.example.com"}
        )
    assert response.status_code == 403
    assert fake.writes == []


def test_status_reports_effective_and_source(settings):
    fake = FakeConfigMapStore({CONFIGMAP_ALLOWED_HOSTS_KEY: "mirror.example.org"})
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.get("/ui/api/allowlist")
    assert response.status_code == 200
    body = response.json()
    assert body["hosts"] == ["mirror.example.org"]
    assert body["source"] == "configmap"
    assert body["configmap"] == "pulp/pulp-ops-config"
    assert body["key"] == "ALLOWED_SOURCE_HOSTS"


def test_status_falls_back_to_settings_when_configmap_absent(settings):
    fake = FakeConfigMapStore()  # no data -> get_value returns None
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        response = test_client.get("/ui/api/allowlist")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "settings"
    assert body["hosts"] == list(settings.allowed_source_hosts)


def test_update_records_activity_with_action_and_count(settings):
    fake = FakeConfigMapStore()
    app = build_app(settings, fake)
    with authed_client(app) as test_client:
        test_client.post(
            "/ui/api/allowlist",
            headers=csrf_headers(test_client),
            json={"hosts": "alpha.example.com,beta.example.com"},
        )
    entries = app.state.activity.recent()
    entry = next(e for e in entries if e["action"] == "config.allowlist.update")
    assert entry["target"] == "2"
    assert entry["result"] == "completed"


def test_new_allowlist_is_used_by_validate_source_url_end_to_end(settings):
    """The proof the feature works: after an update, sync accepts the new host.

    The ConfigMap value is re-read per request, so no restart is needed. The same
    request against the previously-stored host is rejected.
    """
    fake = FakeConfigMapStore(
        {CONFIGMAP_ALLOWED_HOSTS_KEY: "old.example.com"}
    )

    def handler(request):
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/remotes/rpm/rpm/r1/"})

    app = build_app(
        settings,
        fake,
        client_factory=lambda: make_client(settings, handler),
    )
    with authed_client(app) as test_client:
        # Pre-update: the old host is allowed, the new one is not.
        before_new = test_client.post(
            "/ui/api/content/pull-through",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "name": "newreg",
                "base_path": "newreg",
                "upstream_url": "https://new.example.com",
            },
        )
        assert before_new.status_code == 400

        update = test_client.post(
            "/ui/api/allowlist",
            headers=csrf_headers(test_client),
            json={"hosts": "new.example.com"},
        )
        assert update.status_code == 200
        assert update.json()["changed"] is True

        # Post-update: same URL now passes the SSRF check with no restart.
        after_new = test_client.post(
            "/ui/api/content/pull-through",
            headers=csrf_headers(test_client),
            json={
                "domain": "default",
                "name": "newreg",
                "base_path": "newreg",
                "upstream_url": "https://new.example.com",
            },
        )
        assert after_new.status_code == 200


def test_canonicalization_dedupes_sorts_and_lowercases():
    assert canonicalize_allowed_source_hosts("B.example.com,a.example.com,B.example.com") == (
        "a.example.com",
        "b.example.com",
    )
    assert canonicalize_allowed_source_hosts("") == ()
    with pytest.raises(ValueError):
        canonicalize_allowed_source_hosts("a.example.com,")


def test_startup_parse_matches_previous_lenient_behaviour():
    assert parse_allowed_source_hosts(" Mirror.Example.com , ") == ("mirror.example.com",)
    assert parse_allowed_source_hosts("") == ()
