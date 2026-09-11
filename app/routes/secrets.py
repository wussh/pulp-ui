"""Cluster Secret management — the UI port of manifests/pulp/scripts/setup-secrets.sh.

Every route works through SecretsStore, which enforces a strict Secret-name
allowlist and fixed namespaces. A Secret VALUE is never returned, logged, or put
in an activity record; the status view exposes names and key names only.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.k8s import (
    ALLOWED_SECRET_NAMES,
    EVEREST_DB_SECRET,
    POSTGRES_CREDENTIALS_SECRET,
    S3_CREDENTIALS_SECRET,
    SecretError,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Key names the two managed Secrets carry, matching setup-secrets.sh exactly.
S3_KEYS = (
    "s3-access-key-id",
    "s3-secret-access-key",
    "s3-bucket-name",
    "s3-region",
    "s3-endpoint",
)
POSTGRES_KEYS = (
    "POSTGRES_USERNAME",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB_NAME",
    "POSTGRES_SSLMODE",
)


async def apply_s3(secrets, settings, correlation_id: str) -> dict:
    await secrets.apply_secret(
        S3_CREDENTIALS_SECRET,
        {
            "s3-access-key-id": settings.pulp_s3_access_key_id,
            "s3-secret-access-key": settings.pulp_s3_secret_access_key,
            "s3-bucket-name": settings.pulp_s3_bucket_name,
            "s3-region": settings.pulp_s3_region,
            "s3-endpoint": settings.pulp_s3_endpoint,
        },
        correlation_id=correlation_id,
    )
    return {"applied": True, "secret": S3_CREDENTIALS_SECRET}


async def apply_postgres(secrets, settings, correlation_id: str) -> dict:
    # Username/password come from the single permitted prod Secret; the rest from
    # settings. A missing Everest Secret is reported, not fabricated.
    credential = await secrets.read_everest_db_credentials(
        correlation_id=correlation_id
    )
    if credential is None:
        raise SecretError(
            f"{EVEREST_DB_SECRET} was not found in the "
            f"{settings.k8s_prod_namespace} namespace; wait for the DatabaseCluster "
            "to become Ready and retry.",
            correlation_id=correlation_id,
        )
    username, password = credential
    await secrets.apply_secret(
        POSTGRES_CREDENTIALS_SECRET,
        {
            "POSTGRES_USERNAME": username,
            "POSTGRES_PASSWORD": password,
            "POSTGRES_HOST": settings.pulp_postgres_host,
            "POSTGRES_PORT": settings.pulp_postgres_port,
            "POSTGRES_DB_NAME": settings.pulp_postgres_db_name,
            "POSTGRES_SSLMODE": settings.pulp_postgres_sslmode,
        },
        correlation_id=correlation_id,
    )
    # No value from the source Secret is returned — not even the username.
    return {"applied": True, "secret": POSTGRES_CREDENTIALS_SECRET}


async def secrets_status(secrets, correlation_id: str) -> dict:
    """Names and key names of the managed Secrets. Values are never read out."""
    out: list[dict] = []
    for name, expected_keys in (
        (S3_CREDENTIALS_SECRET, S3_KEYS),
        (POSTGRES_CREDENTIALS_SECRET, POSTGRES_KEYS),
    ):
        payload = await secrets.get_secret(name, correlation_id=correlation_id)
        keys = sorted((payload or {}).get("data", {}).keys())
        out.append(
            {
                "name": name,
                "exists": payload is not None,
                "keys": keys,
                "expected_keys": list(expected_keys),
            }
        )
    return {
        "correlation_id": correlation_id,
        "secrets": out,
        # Surfacing the allowlist is itself a read-only fact; it names no value.
        "allowed": sorted(ALLOWED_SECRET_NAMES),
    }


async def _guarded(request: Request, action: str, target: str, handler) -> JSONResponse:
    """Run `handler(secrets, correlation_id)`, closing the client on every path."""
    app = request.app
    secrets = app.state.secrets_factory()
    correlation_id = app.state.correlations.new()
    try:
        result = await handler(secrets, correlation_id)
    except SecretError as exc:
        await secrets.aclose()
        if action:
            _record(request, correlation_id, action, target, failed=True)
        return JSONResponse(
            {
                "error": exc.safe_message,
                "correlation_id": exc.correlation_id or correlation_id,
            },
            status_code=502,
        )
    await secrets.aclose()
    if action:
        _record(request, correlation_id, action, target)
    return JSONResponse({"correlation_id": correlation_id, **result})


def _record(request: Request, correlation_id: str, action: str, target: str, failed=False) -> None:
    request.app.state.activity.record(
        {
            "correlation_id": correlation_id,
            "operator": getattr(request.state, "username", None) or "operator",
            "action": action,
            "target": target,
            "target_type": "secret",
            "result": "failed" if failed else "completed",
        }
    )


@router.get("/secrets", response_class=HTMLResponse)
async def secrets_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "secrets.html", {"warnings": [], "current_user": "operator"}
    )


@router.get("/api/secrets/status")
async def secrets_status_route(request: Request) -> JSONResponse:
    return await _guarded(request, "", "", lambda secrets, cid: secrets_status(secrets, cid))


@router.post("/api/secrets/s3")
async def secrets_s3_apply(request: Request) -> JSONResponse:
    async def handler(secrets, correlation_id):
        return await apply_s3(secrets, request.app.state.settings, correlation_id)

    return await _guarded(request, "secrets.s3.apply", S3_CREDENTIALS_SECRET, handler)


@router.post("/api/secrets/postgres")
async def secrets_postgres_apply(request: Request) -> JSONResponse:
    async def handler(secrets, correlation_id):
        return await apply_postgres(
            secrets, request.app.state.settings, correlation_id
        )

    return await _guarded(
        request, "secrets.postgres.apply", POSTGRES_CREDENTIALS_SECRET, handler
    )
