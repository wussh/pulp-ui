from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.endpoints import global_api, plugin_api, validate_name
from app.pulp import PulpError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Model-level content roles copied verbatim from the script's CONTENT_ROLES
# (/home/wush/linsa/scripts/pulp/pulp-domain-rbac-setup.py). Both the *_creator
# and *_owner role per plugin are required: without *_creator a non-superuser
# group member gets 403 on every create inside the domain. These are MODEL-LEVEL
# grants, assigned with content_object: null and domain: <href>.
CONTENT_ROLES: tuple[str, ...] = (
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
)

# Groups (and users) live in the `default` domain, not the tenant's domain: the
# group-resource API paths are /pulp/default/api/v3/groups/. A role assignment is
# still scoped to a tenant domain via the `domain` field in its body.
_GLOBAL_DOMAIN = "default"

# core.domain_owner manages the Domain object itself; core.domain_creator allows
# creating objects within it. Both sit alongside the content roles above.
ASSIGNED_ROLES: tuple[str, ...] = ("core.domain_owner", "core.domain_creator") + CONTENT_ROLES


async def _first_match(client, path: str, field: str, value: str) -> dict | None:
    listing = await client.request("GET", path, params={field: value})
    for item in listing.get("results", []):
        if item.get(field) == value:
            return item
    return None


def _record_id(href: str) -> str:
    return href.rstrip("/").split("/")[-1]


def _recovery_note(completed: list[dict], stopped: dict) -> str:
    done = ", ".join(
        f"{item['resource']} {item.get('pulp_href') or item['name']}"
        for item in completed
    )
    return (
        f"The operation stopped at the {stopped['resource']} step "
        f"({stopped['name']}) and nothing was rolled back. "
        f"Completed resources: {done or 'none'}. "
        "To recover, fix the cause of the failure and re-run setup (the plan "
        "re-fetches existing resources and creates only what is missing). This UI's "
        "delete page only removes plugin-scoped content resources such as "
        "repositories and distributions; domains, users, and groups cannot be "
        "deleted here. Remove those through Pulp directly, using the "
        "pulpcore-manager shell inside the api pod or the Pulp REST API."
    )


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

    probe_warnings: list[str] = []

    async def probe(resource: str, path: str, field: str, value: str) -> dict | None:
        # A 4xx means the lookup could not answer definitively; treat it as absent
        # (apply will surface the real error at that step) but record the uncertainty
        # so the preview is not read as authoritative. Network/timeout (no status) and
        # 5xx are NOT "absent" and must propagate to the 502 handler.
        try:
            return await _first_match(client, path, field, value)
        except PulpError as exc:
            if exc.status_code is None or exc.status_code >= 500:
                raise
            probe_warnings.append(
                f"lookup of {resource} returned status {exc.status_code}; "
                "treating it as absent"
            )
            return None

    existing_domain = await probe(
        "domain", "/pulp/default/api/v3/domains/", "name", domain
    )
    existing_user = await probe(
        "user", "/pulp/default/api/v3/users/", "username", username
    )
    existing_group = await probe(
        "group", "/pulp/default/api/v3/groups/", "name", group
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
            # Surface the real scope of the step: it creates one assignment per
            # role in ASSIGNED_ROLES, not a single one.
            "roles": len(ASSIGNED_ROLES),
        }
    )

    return {
        "domain": domain,
        "username": username,
        "group": group,
        "steps": steps,
        "probe_warnings": probe_warnings,
        "preview": {
            "creates": sum(1 for step in steps if step["action"] == "create"),
            "reuses": sum(1 for step in steps if step["action"] == "reuse"),
            "assignments": len(ASSIGNED_ROLES),
        },
    }


async def _list_group_roles(client, api_domain: str, group_href: str) -> list[dict]:
    """List a group's role assignments.

    The listing is domain-scoped: the flat /pulp/api/v3 shape 404s. The POST that
    creates an assignment stays flat (see _assign_domain_role).
    """
    listing = await client.request(
        "GET", plugin_api(api_domain, f"groups/{_record_id(group_href)}/roles/")
    )
    return listing.get("results", [])


async def _assign_domain_role(
    client, group_record: dict, domain_record: dict, correlation_id: str
) -> dict:
    # Role assignment stays on the flat path: verified against live Pulp, roles are
    # granted per group, not per domain path. Confirm before changing.
    path = global_api(f"groups/{_record_id(group_record['pulp_href'])}/roles/")
    domain_href = domain_record["pulp_href"]
    # Match on role + content_object + domain, exactly as the source script does,
    # so a re-run is a no-op instead of re-POSTing every assignment.
    existing = await _list_group_roles(
        client, _GLOBAL_DOMAIN, group_record["pulp_href"]
    )
    assigned = {
        (item.get("role"), item.get("content_object"), item.get("domain"))
        for item in existing
    }
    created: list[str] = []
    skipped: list[str] = []
    task_hrefs: list[str] = []
    for role in ASSIGNED_ROLES:
        if (role, None, domain_href) in assigned:
            skipped.append(role)
            continue
        result = await client.request(
            "POST",
            path,
            json_body={
                "role": role,
                "content_object": None,
                "domain": domain_href,
            },
            correlation_id=correlation_id,
        )
        created.append(role)
        task_href = result.get("task", "")
        if task_href:
            task_hrefs.append(task_href)
    # Every sibling assignment's task is surfaced, not just the last one's, so the
    # operator can follow all of them.
    return {
        "created": created,
        "skipped": skipped,
        "created_count": len(created),
        "skipped_count": len(skipped),
        "task_hrefs": task_hrefs,
        "task_href": task_hrefs[-1] if task_hrefs else "",
    }


async def list_role_assignments(client, domain: str, group: str) -> dict:
    domain = validate_name("domain", domain)
    group = validate_name("group", group)
    record = await _first_match(
        client, plugin_api(domain, "groups/"), "name", group
    )
    if not record:
        raise ValueError("group not found")
    assignments = await _list_group_roles(client, domain, record["pulp_href"])
    return {
        "group": group,
        "assignments": [
            {
                "role": item.get("role"),
                "content_object": item.get("content_object"),
                "domain": item.get("domain"),
            }
            for item in assignments
        ],
    }


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
            stopped = {
                "resource": step["resource"],
                "name": step["name"],
            }
            return {
                "correlation_id": correlation_id,
                "completed": completed,
                "stopped_at": stopped,
                "message": exc.safe_message,
                "recovery": _recovery_note(completed, stopped),
                "failed": True,
            }
        # Role assignment and several other steps answer 202 with a task; a step that
        # dropped the task href would leave the operator unable to follow progress.
        if step["resource"] == "role":
            # Surface the real outcome of the assignment step: how many were created
            # vs already present, and every task href so all are followable.
            completed.append({**step, **created})
        else:
            completed.append(
                {
                    **step,
                    "pulp_href": created.get("pulp_href", ""),
                    "task_href": created.get("task", ""),
                }
            )

    return {"correlation_id": correlation_id, "completed": completed, "failed": False}


@router.get("/tenants", response_class=HTMLResponse)
async def tenants_page(request: Request) -> HTMLResponse:
    client = request.app.state.client_factory()
    try:
        data = await list_tenants(client)
    except PulpError as exc:
        await client.aclose()
        return templates.TemplateResponse(
            request,
            "tenants.html",
            {
                "domains": [],
                "users": [],
                "groups": [],
                "warnings": [
                    {
                        "code": "pulp.error",
                        "message": f"{exc.safe_message} (correlation id: {exc.correlation_id})",
                    }
                ],
                "current_user": "operator",
            },
            status_code=exc.status_code or 502,
        )
    await client.aclose()
    return templates.TemplateResponse(
        request, "tenants.html", {**data, "warnings": [], "current_user": "operator"}
    )


@router.get("/api/tenants")
async def tenants_api(request: Request) -> JSONResponse:
    client = request.app.state.client_factory()
    try:
        body = await list_tenants(client)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message, "correlation_id": exc.correlation_id},
            status_code=exc.status_code or 502,
        )
    await client.aclose()
    return JSONResponse(body)


@router.get("/api/tenants/roles")
async def tenant_roles(request: Request) -> JSONResponse:
    client = request.app.state.client_factory()
    try:
        body = await list_role_assignments(
            client,
            request.query_params.get("domain", "default"),
            request.query_params.get("group", ""),
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
            {"error": exc.safe_message, "correlation_id": exc.correlation_id},
            status_code=exc.status_code or 502,
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
            {"error": exc.safe_message, "correlation_id": exc.correlation_id},
            status_code=exc.status_code or 502,
        )
    await client.aclose()

    app.state.activity.record(
        {
            "correlation_id": correlation_id,
            "operator": getattr(request.state, "username", None) or "operator",
            "action": "tenant.setup",
            "target": plan["domain"],
            "target_type": "domain",
            "result": "failed" if result.get("failed") else "completed",
        }
    )
    return JSONResponse(result, status_code=400 if result.get("failed") else 200)
