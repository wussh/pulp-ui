from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_MAX_LIMIT = 200


@router.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request) -> HTMLResponse:
    entries = request.app.state.activity.recent()
    return templates.TemplateResponse(
        request,
        "activity.html",
        {"entries": entries, "warnings": [], "current_user": "operator"},
    )


@router.get("/api/activity")
async def activity_api(request: Request) -> JSONResponse:
    try:
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        return JSONResponse({"error": "limit must be an integer"}, status_code=400)
    return JSONResponse(
        {"entries": request.app.state.activity.recent(max(1, min(limit, _MAX_LIMIT)))}
    )
