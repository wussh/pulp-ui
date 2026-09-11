import httpx

from app.main import create_app
from tests.helpers import authed_client, csrf_headers, make_client


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
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/tenants/plan",
            headers=csrf_headers(test_client),
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


def test_tenant_listing_page_failure_returns_mapped_error(settings):
    # tenants_page was try/finally with no except: a PulpError became a bare 500
    # with no correlation id. It must return the mapped error instead.
    def handler(request):
        return httpx.Response(500, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.get("/ui/tenants")
    assert response.status_code == 500
    assert "correlation id:" in response.text


def test_apply_step_carries_async_task_href(settings):
    # Role assignment answers 202 with a task. The step record must surface it so
    # the operator can follow live progress.
    task = "/pulp/api/v3/tasks/role-1/"

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"count": 0, "results": []})
        if request.url.path.endswith("/roles/"):
            return httpx.Response(202, json={"task": task})
        return httpx.Response(
            201,
            json={
                "name": "x",
                "username": "x",
                "pulp_href": request.url.path + "1/",
            },
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 200
    steps = response.json()["completed"]
    assert steps[-1]["resource"] == "role"
    assert steps[-1]["task_href"] == task


def test_apply_setup_stops_on_first_failure(settings):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/users/"):
            return httpx.Response(400, json={"username": ["This field must be unique."]})
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/domains/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
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
    assert calls[-1] == ("POST", "/pulp/default/api/v3/users/")


def test_plan_reports_probe_warnings_on_4xx(settings):
    def handler(request):
        if request.url.path.endswith("/users/"):
            return httpx.Response(400, json={"username": ["This field must be unique."]})
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/plan",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["probe_warnings"]) == 1
    warning = body["probe_warnings"][0]
    assert "user" in warning
    assert "400" in warning
    assert "This field must be unique" not in warning
    assert [step["action"] for step in body["steps"]] == [
        "create",
        "create",
        "create",
        "assign",
    ]


def test_plan_propagates_upstream_5xx(settings):
    def handler(request):
        return httpx.Response(500, json={"detail": "internal server error"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/plan",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 500
    body = response.json()
    assert "steps" not in body
    assert body["correlation_id"]


def test_tenant_listing_failure_includes_correlation_id(settings):
    def handler(request):
        return httpx.Response(500, json={})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.get("/ui/api/tenants")
    assert response.status_code == 500
    assert response.json()["correlation_id"]


def test_apply_includes_recovery_guidance(settings):
    def handler(request):
        if request.url.path.endswith("/users/"):
            return httpx.Response(400, json={"username": ["This field must be unique."]})
        return httpx.Response(201, json={"pulp_href": "/pulp/default/api/v3/domains/1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 400
    body = response.json()
    recovery = body["recovery"]
    assert "/pulp/default/api/v3/domains/1/" in recovery
    assert "user" in recovery
    assert "nothing was rolled back" in recovery
    # The recovery text must not promise a delete path the UI rejects: domains,
    # users, and groups have no route through the delete page.
    assert "cannot be deleted here" in recovery
    assert "repositories and distributions" in recovery
    assert "pulpcore-manager" in recovery
    assert "Pulp REST API" in recovery
    assert "delete the completed resources listed above through the delete page" not in recovery
    assert "password" not in recovery.lower()


def test_plan_rejects_invalid_domain(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/plan",
            headers=csrf_headers(test_client),
            json={"domain": "../admin", "username": "budi-test", "group": "g"},
        )
    assert response.status_code == 400


# --- Feature 4: complete tenant content role assignments ----------------------

EXPECTED_ROLES = [
    # core.domain_owner is assigned OBJECT-LEVEL (content_object = domain href,
    # domain = null). core.domain_creator is intentionally absent: Pulp rejects it
    # for a domain-scoped assignment, verified live.
    "core.domain_owner",
    "rpm.rpmrepository_creator",
    "rpm.rpmrepository_owner",
    "deb.aptrepository_creator",
    "deb.aptrepository_owner",
    "python.pythonrepository_creator",
    "python.pythonrepository_owner",
    "ansible.ansiblerepository_creator",
    "ansible.ansiblerepository_owner",
    "container.containerrepository_creator",
    "container.containerrepository_owner",
]


def test_apply_assigns_owner_creator_and_content_roles(settings):
    import json

    role_posts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"count": 0, "results": []})
        if request.url.path.endswith("/roles/"):
            role_posts.append(json.loads(request.content))
            return httpx.Response(202, json={"task": "/pulp/api/v3/tasks/role-1/"})
        return httpx.Response(
            201,
            json={"name": "x", "username": "x", "pulp_href": request.url.path + "1/"},
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 200
    assert [post["role"] for post in role_posts] == EXPECTED_ROLES
    # The metadata role is object-level, the content roles model-level. See the
    # longer note in test_apply_creates_all_roles_and_returns_every_task_href.
    domain_href = "/pulp/default/api/v3/domains/1/"
    for post in role_posts:
        if post["role"] == "core.domain_owner":
            assert post["content_object"] == domain_href
            assert post["domain"] is None
        else:
            assert post["content_object"] is None
            assert post["domain"] == domain_href


def test_plan_preview_reports_role_assignment_count(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        body = test_client.post(
            "/ui/api/tenants/plan",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        ).json()
    assert body["preview"]["assignments"] == len(EXPECTED_ROLES)
    role_step = body["steps"][-1]
    assert role_step["resource"] == "role"
    assert role_step["roles"] == len(EXPECTED_ROLES)


def test_roles_listing_uses_domain_scoped_path(settings):
    # Live tbs-dev: /pulp/api/v3/groups/3/roles/ 404s; only the domain-scoped
    # /pulp/default/api/v3/groups/3/roles/ returns the assignments.
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/pulp/default/api/v3/groups/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [
                        {
                            "name": "dummy-alpha-users",
                            "pulp_href": "/pulp/default/api/v3/groups/7/",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "count": 1,
                "results": [
                    {
                        "role": "rpm.rpmrepository_creator",
                        "content_object": None,
                        "domain": "/pulp/default/api/v3/domains/1/",
                    }
                ],
            },
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.get(
            "/ui/api/tenants/roles?domain=default&group=dummy-alpha-users"
        )
    assert response.status_code == 200
    assert seen == [
        "/pulp/default/api/v3/groups/",
        "/pulp/default/api/v3/groups/7/roles/",
    ]
    assert response.json()["assignments"] == [
        {
            "role": "rpm.rpmrepository_creator",
            "content_object": None,
            "domain": "/pulp/default/api/v3/domains/1/",
        }
    ]


def test_roles_listing_rejects_unknown_group(settings):
    def handler(request):
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.get(
            "/ui/api/tenants/roles?domain=default&group=missing"
        )
    assert response.status_code == 400


def test_roles_listing_rejects_foreign_domain(settings):
    # A traversal-shaped domain must be a clean 400, never a 500.
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.get(
            "/ui/api/tenants/roles?domain=../admin&group=dummy-alpha-users"
        )
    assert response.status_code == 400
    assert calls == []


def test_apply_reassigns_nothing_when_all_roles_present(settings):
    # Regression: a re-apply re-POSTed all 12 assignments. The source script matches
    # on role + content_object + domain first; so must the UI.
    import json

    domain_href = "/pulp/default/api/v3/domains/1/"
    existing = [
        {"role": role, "content_object": None, "domain": domain_href}
        for role in EXPECTED_ROLES
    ]
    # The metadata role is already present too — object-level, so it carries the
    # domain as its content_object and no `domain` field.
    existing.append(
        {"role": "core.domain_owner", "content_object": domain_href, "domain": None}
    )
    posts = []

    def handler(request):
        if request.method == "GET":
            if "/roles/" in request.url.path:
                return httpx.Response(
                    200, json={"count": len(existing), "results": existing}
                )
            return httpx.Response(200, json={"count": 0, "results": []})
        if request.url.path.endswith("/roles/"):
            posts.append(json.loads(request.content))
            return httpx.Response(202, json={"task": "/pulp/api/v3/tasks/r/"})
        return httpx.Response(
            201,
            json={"name": "x", "username": "x", "pulp_href": request.url.path + "1/"},
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 200
    assert posts == []
    step = response.json()["completed"][-1]
    assert step["created_count"] == 0
    assert step["skipped_count"] == len(EXPECTED_ROLES)
    assert step["task_hrefs"] == []


def test_apply_creates_all_roles_and_returns_every_task_href(settings):
    import json

    posts = []
    counter = {"n": 0}

    def handler(request):
        if request.method == "GET":
            if "/roles/" in request.url.path:
                return httpx.Response(200, json={"count": 0, "results": []})
            return httpx.Response(200, json={"count": 0, "results": []})
        if request.url.path.endswith("/roles/"):
            posts.append(json.loads(request.content))
            counter["n"] += 1
            return httpx.Response(
                202, json={"task": f"/pulp/api/v3/tasks/r{counter['n']}/"}
            )
        return httpx.Response(
            201,
            json={"name": "x", "username": "x", "pulp_href": request.url.path + "1/"},
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert response.status_code == 200
    step = response.json()["completed"][-1]
    assert step["created_count"] == len(EXPECTED_ROLES)
    assert step["skipped_count"] == 0
    # Every sibling assignment's task is surfaced, not just the last one's.
    assert len(step["task_hrefs"]) == len(EXPECTED_ROLES)
    assert step["task_hrefs"][0] == "/pulp/api/v3/tasks/r1/"
    assert step["task_hrefs"][-1] == f"/pulp/api/v3/tasks/r{len(EXPECTED_ROLES)}/"
    # Two body shapes, and Pulp treats them differently:
    #   - the domain metadata role is OBJECT-LEVEL: content_object=domain, domain=None
    #     (`domain` together with `content_object` is rejected as "mutually exclusive")
    #   - the content roles are MODEL-LEVEL: content_object=None, domain=domain href
    domain_href = "/pulp/default/api/v3/domains/1/"
    metadata = [p for p in posts if p["role"] == "core.domain_owner"]
    content = [p for p in posts if p["role"] != "core.domain_owner"]
    assert len(metadata) == 1
    assert metadata[0]["content_object"] == domain_href
    assert metadata[0]["domain"] is None
    assert content, "expected content-role assignments"
    for post in content:
        assert post["content_object"] is None
        assert post["domain"] == domain_href


# --- Feature A: create a domain with custom storage --------------------------


def test_apply_domain_body_carries_storage_class_and_settings(settings):
    import json

    domain_posts = []

    def handler(request):
        if request.method == "POST" and request.url.path == "/pulp/default/api/v3/domains/":
            domain_posts.append(json.loads(request.content))
            return httpx.Response(
                201, json={"pulp_href": "/pulp/default/api/v3/domains/1/"}
            )
        if request.method == "GET":
            return httpx.Response(200, json={"count": 0, "results": []})
        return httpx.Response(201, json={"pulp_href": request.url.path + "1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
                "description": "Tenant alpha",
                "bucket_name": "alpha-content",
            },
        )
    assert response.status_code == 200
    assert len(domain_posts) == 1
    body = domain_posts[0]
    assert body["name"] == "dummy-alpha"
    assert body["description"] == "Tenant alpha"
    assert body["storage_class"] == "storages.backends.s3boto3.S3Boto3Storage"
    settings_body = body["storage_settings"]
    assert settings_body["bucket_name"] == "alpha-content"
    assert settings_body["endpoint_url"] == settings.pulp_s3_endpoint
    assert settings_body["access_key"] == settings.pulp_s3_access_key_id
    assert settings_body["secret_key"] == settings.pulp_s3_secret_access_key
    assert settings_body["addressing_style"] == "path"


def test_domain_without_override_uses_the_settings_default_bucket(settings):
    import json

    domain_posts = []

    def handler(request):
        if request.method == "POST" and request.url.path == "/pulp/default/api/v3/domains/":
            domain_posts.append(json.loads(request.content))
            return httpx.Response(
                201, json={"pulp_href": "/pulp/default/api/v3/domains/1/"}
            )
        if request.method == "GET":
            return httpx.Response(200, json={"count": 0, "results": []})
        return httpx.Response(201, json={"pulp_href": request.url.path + "1/"})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert domain_posts[0]["storage_settings"]["bucket_name"] == settings.pulp_s3_bucket_name


def test_domain_response_contains_no_s3_secret(settings):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"count": 0, "results": []})
        return httpx.Response(
            201, json={"name": "dummy-alpha", "pulp_href": "/pulp/default/api/v3/domains/1/"}
        )

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
            },
        )
    assert settings.pulp_s3_secret_access_key not in response.text
    assert settings.pulp_s3_access_key_id not in response.text


def test_apply_rejects_invalid_bucket_name(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"count": 0, "results": []})

    app = create_app(settings, client_factory=lambda: make_client(settings, handler))
    with authed_client(app) as test_client:
        response = test_client.post(
            "/ui/api/tenants/apply",
            headers=csrf_headers(test_client),
            json={
                "domain": "dummy-alpha",
                "username": "budi-test",
                "group": "dummy-alpha-users",
                "bucket_name": "Bad_Bucket",
            },
        )
    assert response.status_code == 400
    assert calls == []


# --- Feature B: tenant credential stored via the UI --------------------------


def test_tenant_credential_set_stores_and_never_echoes(settings):
    from tests.helpers import FakeSecretsStore

    fake = FakeSecretsStore()
    app = create_app(settings, secrets_factory=lambda: fake)
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
    assert fake.tenant_credentials["dummy-alpha"] == (
        "tenant-user",
        "tenant-password-value",
    )
    entries = app.state.activity.recent()
    assert entries[0]["action"] == "tenant.credential.set"
    assert "tenant-password-value" not in str(entries)
