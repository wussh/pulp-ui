from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.logging_config import configure_logging
from app.middleware import BasicAuthMiddleware, CsrfMiddleware
from app.routes import (
    activity,
    content,
    destroy,
    help as help_routes,
    overview,
    tasks,
    tenants,
    validation,
)
from app.state import ActivityStore, CorrelationStore, RunStore

configure_logging()

STATIC_DIR = "app/static"


def create_app(settings: Settings, client_factory=None) -> FastAPI:
    configure_logging()

    def build_client():
        from app.pulp import PulpClient

        return PulpClient(settings)

    factory = client_factory or build_client
    app = FastAPI(
        title="Pulp Operator UI", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.client_factory = factory
    app.state.correlations = CorrelationStore()
    app.state.activity = ActivityStore()
    app.state.runs = RunStore()

    app.add_middleware(CsrfMiddleware)
    app.add_middleware(BasicAuthMiddleware)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        client = factory()
        try:
            reachable = await client.ping()
        finally:
            await client.aclose()
        if not reachable:
            return JSONResponse({"status": "not-ready"}, status_code=503)
        return JSONResponse({"status": "ready"}, status_code=200)

    app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(overview.router, prefix="/ui")
    app.include_router(tenants.router, prefix="/ui")
    app.include_router(content.router, prefix="/ui")
    app.include_router(tasks.router, prefix="/ui")
    app.include_router(validation.router, prefix="/ui")
    app.include_router(destroy.router, prefix="/ui")
    app.include_router(activity.router, prefix="/ui")
    app.include_router(help_routes.router, prefix="/ui")
    return app
