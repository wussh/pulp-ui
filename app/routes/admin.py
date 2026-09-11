from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.markdown_render import render

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

GUIDE_PATH = Path(__file__).resolve().parent.parent / "content" / "admin-guide.md"


@router.get("/admin-guide", response_class=HTMLResponse)
async def admin_guide_page(request: Request) -> HTMLResponse:
    markdown_text = GUIDE_PATH.read_text(encoding="utf-8")
    return templates.TemplateResponse(
        request,
        "admin_guide.html",
        {
            "guide": render(markdown_text),
            "warnings": [],
            "current_user": "operator",
        },
    )
