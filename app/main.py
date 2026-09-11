from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import Settings


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

    return app
