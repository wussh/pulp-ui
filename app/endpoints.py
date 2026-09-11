import re

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

CONTENT_PLUGINS: dict[str, str] = {
    "rpm": "rpm",
    "deb": "deb",
    "python": "python",
    "ansible": "ansible",
    "container": "container",
}


def validate_name(kind: str, value: str) -> str:
    value = (value or "").strip()
    if not _NAME_PATTERN.match(value):
        raise ValueError(
            f"{kind} must be 1-63 characters of lowercase letters, digits, hyphen, "
            "or underscore, and must start with a letter or digit"
        )
    return value


def validate_plugin(value: str) -> str:
    if value not in CONTENT_PLUGINS:
        raise ValueError("unsupported content plugin")
    return value


def plugin_api(domain: str, path: str) -> str:
    domain = validate_name("domain", domain)
    return f"/pulp/{domain}/api/v3/{path.lstrip('/')}"


def global_api(path: str) -> str:
    # Flat paths are permitted only for endpoints verified to stay global.
    # Currently: role assignment under /pulp/api/v3/groups/<id>/roles/.
    # Confirm against live Pulp before adding more.
    return f"/pulp/api/v3/{path.lstrip('/')}"
