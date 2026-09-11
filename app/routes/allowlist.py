"""View and edit the sync source-host allowlist from the UI.

The effective list is the ConfigMap value when present, else the startup env value;
`POST` patches exactly one key of exactly one ConfigMap. Name, namespace, and key
are fixed in app.k8s and are never taken from the request body.

This control is an SSRF guard. Making it editable from the UI weakens it: anyone
holding the UI credential can allowlist an arbitrary host. The accepted trade is
stated on the page and in the activity record.
"""

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.allowlist import canonical_or_raise, effective_allowlist
from app.config import parse_allowed_source_hosts
from app.k8s import (
    CONFIGMAP_ALLOWED_HOSTS_KEY,
    CONFIGMAP_NAME,
    CONFIGMAP_NAMESPACE,
    SecretError,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# The only request-body field accepted. Any other field is rejected, so a request
# cannot try to name a different ConfigMap, namespace, or key.
_REQUEST_FIELDS = frozenset({"hosts"})


async def _read(store, settings):
    """The effective hosts plus where they came from."""
    raw = await store.get_value(CONFIGMAP_ALLOWED_HOSTS_KEY)
    if raw is None:
        return settings.allowed_source_hosts, "settings"
    return parse_allowed_source_hosts(raw), "configmap"


async def _guarded(request: Request, handler) -> JSONResponse:
    app = request.app
    store = app.state.configmap_factory()
    correlation_id = app.state.correlations.new()
    try:
        result = await handler(store, correlation_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except SecretError as exc:
        return JSONResponse(
            {
                "error": exc.safe_message,
                "correlation_id": exc.correlation_id or correlation_id,
            },
            status_code=502,
        )
    finally:
        # Closed on every exit path, including the error returns above.
        await store.aclose()
    return JSONResponse({"correlation_id": correlation_id, **result})


def _record(request: Request, correlation_id: str, count: int, result: str) -> None:
    request.app.state.activity.record(
        {
            "correlation_id": correlation_id,
            "operator": getattr(request.state, "username", None) or "operator",
            "action": "config.allowlist.update",
            "target": str(count),
            "target_type": "config",
            "result": result,
        }
    )


async def allowlist_status(store, settings, correlation_id: str) -> dict:
    hosts, source = await _read(store, settings)
    return {
        "hosts": list(hosts),
        "value": ",".join(hosts),
        "count": len(hosts),
        "source": source,
        "configmap": f"{CONFIGMAP_NAMESPACE}/{CONFIGMAP_NAME}",
        "key": CONFIGMAP_ALLOWED_HOSTS_KEY,
    }


async def allowlist_update(store, settings, payload: dict, correlation_id: str) -> dict:
    unknown = set(payload) - _REQUEST_FIELDS
    if unknown:
        # A request may not name another key even to set it; only `hosts` exists.
        raise ValueError(f"unsupported request field(s): {', '.join(sorted(unknown))}")
    raw = payload.get("hosts")
    if not isinstance(raw, str):
        raise ValueError("hosts must be a string")
    canonical = canonical_or_raise(raw)
    current, _ = await _read(store, settings)
    if canonical == current:
        return {"changed": False, "count": len(canonical), "value": ",".join(canonical)}
    await store.set_value(CONFIGMAP_ALLOWED_HOSTS_KEY, ",".join(canonical))
    return {"changed": True, "count": len(canonical), "value": ",".join(canonical)}


@router.get("/allowlist", response_class=HTMLResponse)
async def allowlist_page(request: Request) -> HTMLResponse:
    store = request.app.state.configmap_factory()
    settings = request.app.state.settings
    try:
        body = await allowlist_status(store, settings, "")
    except SecretError as exc:
        body = {"hosts": [], "value": "", "count": 0, "source": "unavailable",
                "error": exc.safe_message}
    finally:
        await store.aclose()
    return templates.TemplateResponse(
        request,
        "allowlist.html",
        {**body, "warnings": [], "current_user": "operator"},
    )


@router.get("/api/allowlist")
async def allowlist_get(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    return await _guarded(
        request, lambda store, cid: allowlist_status(store, settings, cid)
    )


@router.post("/api/allowlist")
async def allowlist_post(request: Request, payload: dict = Body(...)) -> JSONResponse:
    settings = request.app.state.settings

    async def handler(store, correlation_id):
        result = await allowlist_update(store, settings, payload, correlation_id)
        # A no-op is still an operator action worth recording, with count as target.
        _record(
            request,
            correlation_id,
            result["count"],
            "completed" if result["changed"] else "unchanged",
        )
        return result

    return await _guarded(request, handler)
