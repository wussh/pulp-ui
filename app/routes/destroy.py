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
    # href shape: pulp/<domain>/api/v3/<type>/<plugin>/<plugin>/<id>/
    # Require the full plugin-scoped record shape so a collection-level or
    # global-endpoint path can never reach DELETE.
    parts = [part for part in href.split("/") if part]
    if len(parts) < 8:
        raise ValueError("href must identify a specific resource")
    return href


def _resource_type(href: str) -> str:
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
    # Re-fetch immediately before the destructive call so the displayed preview
    # cannot go stale between confirmation and execution.
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
            "operator": getattr(request.state, "username", None) or "operator",
            "action": "resource.delete",
            "target": body["deleted"],
            "target_type": "href",
            "result": "completed",
        }
    )
    return JSONResponse(body)
