import os
from dataclasses import dataclass


def _read_secret(env_var: str, file_var: str) -> str:
    path = os.environ.get(file_var)
    if path:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
        if value:
            return value
    value = os.environ.get(env_var, "").strip()
    if not value:
        raise RuntimeError(f"missing configuration: set {env_var} or {file_var}")
    return value


@dataclass(frozen=True)
class Settings:
    pulp_internal_url: str
    pulp_admin_user: str
    pulp_admin_password: str
    ui_username: str
    ui_password_hash: str
    session_secret: str
    public_diagnostic_url: str
    allowed_source_hosts: tuple[str, ...]
    request_timeout_seconds: float
    max_response_bytes: int
    # S3 credential mounted from Secret pulp-s3-credentials. Read server-side only;
    # never returned to the browser.
    pulp_s3_access_key_id: str
    pulp_s3_secret_access_key: str
    pulp_s3_bucket_name: str
    pulp_s3_region: str
    pulp_s3_endpoint: str


def load_settings() -> Settings:
    hosts = [
        host.strip().lower()
        for host in os.environ.get("ALLOWED_SOURCE_HOSTS", "").split(",")
        if host.strip()
    ]
    return Settings(
        pulp_internal_url=os.environ.get("PULP_INTERNAL_URL", "").rstrip("/"),
        pulp_admin_user=_read_secret("PULP_ADMIN_USER", "PULP_ADMIN_USER_FILE"),
        pulp_admin_password=_read_secret(
            "PULP_ADMIN_PASSWORD", "PULP_ADMIN_PASSWORD_FILE"
        ),
        ui_username=os.environ.get("UI_USERNAME", "").strip(),
        ui_password_hash=_read_secret("UI_PASSWORD_HASH", "UI_PASSWORD_HASH_FILE"),
        session_secret=_read_secret("SESSION_SECRET", "SESSION_SECRET_FILE"),
        public_diagnostic_url=os.environ.get("PUBLIC_DIAGNOSTIC_URL", "").rstrip("/"),
        allowed_source_hosts=tuple(hosts),
        request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15")),
        max_response_bytes=int(os.environ.get("MAX_RESPONSE_BYTES", "4194304")),
        pulp_s3_access_key_id=_read_secret(
            "PULP_S3_ACCESS_KEY_ID", "PULP_S3_ACCESS_KEY_ID_FILE"
        ),
        pulp_s3_secret_access_key=_read_secret(
            "PULP_S3_SECRET_ACCESS_KEY", "PULP_S3_SECRET_ACCESS_KEY_FILE"
        ),
        pulp_s3_bucket_name=_read_secret(
            "PULP_S3_BUCKET_NAME", "PULP_S3_BUCKET_NAME_FILE"
        ),
        pulp_s3_region=_read_secret("PULP_S3_REGION", "PULP_S3_REGION_FILE"),
        pulp_s3_endpoint=_read_secret("PULP_S3_ENDPOINT", "PULP_S3_ENDPOINT_FILE"),
    )
