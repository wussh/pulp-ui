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
            "code": str(error.get("code") or ""),
            "description": str(error.get("description") or "")[:400],
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
                "domain": domain,
                "warnings": [],
                "current_user": "operator",
            },
            status_code=400,
        )
    await client.aclose()
    return templates.TemplateResponse(
        request,
        "task_detail.html",
        {"task": data, "domain": domain, "warnings": [], "current_user": "operator"},
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
