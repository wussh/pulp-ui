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

    async def probe(path: str, field: str, value: str) -> dict | None:
        # A probe that cannot complete must not abort planning: record a create and
        # let apply_setup surface the real upstream error at that step, where it is
        # reported as stopped_at. Keeps apply's stop-on-first-failure authoritative.
        try:
            return await _first_match(client, path, field, value)
        except PulpError:
            return None

    existing_domain = await probe("/pulp/default/api/v3/domains/", "name", domain)
    existing_user = await probe("/pulp/default/api/v3/users/", "username", username)
    existing_group = await probe("/pulp/default/api/v3/groups/", "name", group)

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
