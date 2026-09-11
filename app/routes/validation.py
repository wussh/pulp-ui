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
    other_domain = _OTHER_DOMAIN.get(domain, "dummy-beta")

    # Execution order is: create, cross-read, own-domain list. The returned
    # assertions are assembled in reporting order (isolation, creation, listing).
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
        creation = {
            "name": "resource_creation",
            "status": "PASS" if repository_href else "FAIL",
            "evidence": repository_href or "no href returned",
        }
    except PulpError as exc:
        creation = {
            "name": "resource_creation",
            "status": "FAIL",
            "evidence": exc.safe_message,
        }

    if not repository_href:
        # The isolation check cannot run without a resource to look for; a check
        # that did not run must never report PASS.
        isolation = {
            "name": "domain_isolation",
            "status": "FAIL",
            "evidence": "resource was not created, isolation could not be verified",
        }
    else:
        try:
            cross = await client.request(
                "GET", plugin_api(other_domain, "repositories/rpm/rpm/")
            )
            crossed = repository_href in [
                item.get("pulp_href") for item in cross.get("results", [])
            ]
            isolation = {
                "name": "domain_isolation",
                "status": "FAIL" if crossed else "PASS",
                "evidence": f"foreign_domain_leak={crossed}",
            }
        except PulpError as exc:
            isolation = {
                "name": "domain_isolation",
                "status": "FAIL",
                "evidence": exc.safe_message,
            }

    if not repository_href:
        listing_assertion = {
            "name": "resource_listing",
            "status": "FAIL",
            "evidence": "resource was not created, listing could not be verified",
        }
    else:
        try:
            listing = await client.request(
                "GET", plugin_api(domain, "repositories/rpm/rpm/")
            )
            found = repository_href in [
                item.get("pulp_href") for item in listing.get("results", [])
            ]
            listing_assertion = {
                "name": "resource_listing",
                "status": "PASS" if found else "FAIL",
                "evidence": f"listed={found}",
            }
        except PulpError as exc:
            listing_assertion = {
                "name": "resource_listing",
                "status": "FAIL",
                "evidence": exc.safe_message,
            }

    return {
        "run_id": run_id,
        "domain": domain,
        "correlation_id": correlation_id,
        "assertions": [isolation, creation, listing_assertion],
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
            "operator": getattr(request.state, "username", None) or "operator",
            "action": "validation.run",
            "target": body["run_id"],
            "target_type": "run",
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
        app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": getattr(request.state, "username", None) or "operator",
                "action": "validation.cleanup",
                "target": payload.get("run_id", ""),
                "target_type": "run",
                "result": "failed",
            }
        )
        return JSONResponse({"error": str(exc)}, status_code=400)
    await client.aclose()
    app.state.activity.record(
        {
            "correlation_id": correlation_id,
            "operator": getattr(request.state, "username", None) or "operator",
            "action": "validation.cleanup",
            "target": payload.get("run_id", ""),
            "target_type": "run",
            "result": "completed",
        }
    )
    return JSONResponse(body)
