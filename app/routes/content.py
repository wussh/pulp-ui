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
