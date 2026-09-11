from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.routes import overview
from app.state import ActivityStore, CorrelationStore, RunStore

STATIC_DIR = "app/static"


def create_app(settings: Settings, client_factory=None) -> FastAPI:
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
    return app
