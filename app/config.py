import ipaddress
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


def _read_optional_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _env(env_var: str, default: str = "") -> str:
    return os.environ.get(env_var, default).strip()


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
    # Non-secret Postgres coordinates from the setup-secrets.sh script; the username
    # and password are read from the Everest Secret in the prod namespace.
    pulp_postgres_host: str
    pulp_postgres_port: str
    pulp_postgres_db_name: str
    pulp_postgres_sslmode: str
    # Kubernetes API access for the cluster-secret and tenant-isolation features.
    k8s_api_url: str
    k8s_token_path: str
    k8s_ca_path: str
    k8s_namespace_path: str
    k8s_namespace: str
    k8s_prod_namespace: str


MAX_ALLOWED_SOURCE_HOSTS = 50
MAX_HOSTNAME_LENGTH = 253


def parse_allowed_source_hosts(value: str) -> tuple[str, ...]:
    """Lenient startup parse: strip, lowercase, drop blanks. Matches env behaviour."""
    return tuple(
        host.strip().lower() for host in (value or "").split(",") if host.strip()
    )


def canonicalize_allowed_source_hosts(value: str) -> tuple[str, ...]:
    """Validate and canonicalize a comma-separated host allowlist.

    Raises ValueError with an operator-safe message. The canonical form is
    lowercase, deduplicated, and sorted, so an unchanged submission is detected by
    string comparison and never triggers a write.
    """
    raw = value if isinstance(value, str) else ""
    if raw.strip() == "":
        return ()
    entries = raw.split(",")
    cleaned: list[str] = []
    for entry in entries:
        host = entry.strip().lower()
        if not host:
            # A ",," or a trailing comma is rejected rather than silently dropped.
            raise ValueError("allowlist contains an empty entry")
        if len(host) > MAX_HOSTNAME_LENGTH:
            raise ValueError(f"hostname exceeds {MAX_HOSTNAME_LENGTH} characters")
        if any(char in host for char in "/:*@") or any(
            char.isspace() for char in host
        ):
            raise ValueError(
                f"hostname {host!r} must be a bare hostname "
                "(no scheme, port, path, wildcard, or credentials)"
            )
        if host.startswith("."):
            raise ValueError(f"hostname {host!r} must not start with a dot")
        if host in (".", ".."):
            raise ValueError(f"hostname {host!r} is not a hostname")
        if ".." in host or host.startswith("-") or host.endswith("-"):
            raise ValueError(f"hostname {host!r} is not a valid hostname")
        # Reject IP literals: hosts must be names. validate_source_url already
        # resolves the name and vets every returned address, so an IP literal adds
        # no reachable capability while removing the DNS-name intent of the list.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError(f"hostname {host!r} must not be an IP literal")
        cleaned.append(host)
    if len(cleaned) > MAX_ALLOWED_SOURCE_HOSTS:
        raise ValueError(
            f"allowlist holds at most {MAX_ALLOWED_SOURCE_HOSTS} entries"
        )
    return tuple(sorted(set(cleaned)))


def load_settings() -> Settings:
    hosts = parse_allowed_source_hosts(os.environ.get("ALLOWED_SOURCE_HOSTS", ""))
    token_path = _env(
        "K8S_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token"
    )
    namespace_path = _env(
        "K8S_NAMESPACE_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
    )
    # The in-cluster ServiceAccount namespace wins; the env value is the default.
    k8s_namespace = _read_optional_file(namespace_path) or _env("K8S_NAMESPACE", "pulp")
    service_host = _env("KUBERNETES_SERVICE_HOST")
    service_port = _env("KUBERNETES_SERVICE_PORT", "443")
    default_api_url = (
        f"https://{service_host}:{service_port}"
        if service_host
        else "https://kubernetes.default.svc"
    )
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
        pulp_postgres_host=_env("PULP_POSTGRES_HOST", "db-pulp-pgbouncer.prod.svc"),
        pulp_postgres_port=_env("PULP_POSTGRES_PORT", "5432"),
        pulp_postgres_db_name=_env("PULP_POSTGRES_DB_NAME", "postgres"),
        pulp_postgres_sslmode=_env("PULP_POSTGRES_SSLMODE", "prefer"),
        k8s_api_url=_env("K8S_API_URL", default_api_url).rstrip("/"),
        k8s_token_path=token_path,
        k8s_ca_path=_env(
            "K8S_CA_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        ),
        k8s_namespace_path=namespace_path,
        k8s_namespace=k8s_namespace,
        k8s_prod_namespace=_env("K8S_PROD_NAMESPACE", "prod"),
    )
