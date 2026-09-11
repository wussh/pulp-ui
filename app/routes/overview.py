import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.endpoints import plugin_api
from app.pulp import PulpClient, PulpError

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


async def check_public_route(settings: Settings) -> tuple[bool, int]:
    if not settings.public_diagnostic_url:
        return False, 0
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"{settings.public_diagnostic_url}/pulp/api/v3/status/"
            )
    except (httpx.HTTPError, httpx.InvalidURL):
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

    public_ok, public_status = await check_public_route(settings)
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
