import re

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

CONTENT_PLUGINS: dict[str, str] = {
    "rpm": "rpm",
    "deb": "deb",
    "python": "python",
    "ansible": "ansible",
    "container": "container",
}

# Verified live against tbs-dev Pulp. The endpoint segment is NOT always
# `{plugin}/{plugin}`: deb uses `apt`, python distributions use `pypi`, and
# ansible remotes use `collection`. Keyed by (plugin, kind) where kind is one of
# repository / remote / distribution.
RESOURCE_SEGMENTS: dict[tuple[str, str], str] = {
    ("rpm", "repository"): "rpm/rpm",
    ("rpm", "remote"): "rpm/rpm",
    ("rpm", "distribution"): "rpm/rpm",
    ("deb", "repository"): "deb/apt",
    ("deb", "remote"): "deb/apt",
    ("deb", "distribution"): "deb/apt",
    ("python", "repository"): "python/python",
    ("python", "remote"): "python/python",
    ("python", "distribution"): "python/pypi",
    ("ansible", "repository"): "ansible/ansible",
    ("ansible", "remote"): "ansible/collection",
    ("ansible", "distribution"): "ansible/ansible",
    ("container", "repository"): "container/container",
    ("container", "remote"): "container/container",
    ("container", "distribution"): "container/container",
}

# Publication endpoints verified live against tbs-dev Pulp. Only these three
# plugins expose one: ansible and container have no publication endpoint (the
# ansible distribution serves straight from the repository; container is
# pull-through with nothing to publish). Segment shape mirrors the resource
# table: deb uses `apt`, python uses `pypi`.
PUBLICATION_SEGMENTS: dict[str, str] = {
    "rpm": "rpm/rpm",
    "deb": "deb/apt",
    "python": "python/pypi",
}

# Container pull-through lives at its own segment, distinct from the regular
# container/container resources; the latter must not be repurposed.
PULL_THROUGH_SEGMENT = "container/pull-through"

# Only container distributions take the `base_path` field.
_BASE_PATH_PLUGINS = frozenset({"container"})

_KIND_COLLECTION = {
    "repository": "repositories",
    "remote": "remotes",
    "distribution": "distributions",
}


def resource_segment(plugin: str, kind: str) -> str:
    try:
        return RESOURCE_SEGMENTS[(plugin, kind)]
    except KeyError:
        raise ValueError("unsupported content plugin") from None


def plugin_resource_path(plugin: str, kind: str) -> str:
    """Relative collection path for a plugin resource kind, e.g. `repositories/deb/apt/`."""
    collection = _KIND_COLLECTION[kind]
    return f"{collection}/{resource_segment(plugin, kind)}/"


def requires_base_path(plugin: str) -> bool:
    return plugin in _BASE_PATH_PLUGINS


def publication_path(plugin: str) -> str:
    """Relative collection path for a plugin's publication endpoint.

    Raises ValueError for plugins with no publication endpoint (ansible,
    container, or anything unknown).
    """
    try:
        segment = PUBLICATION_SEGMENTS[plugin]
    except KeyError:
        raise ValueError("plugin has no publication endpoint") from None
    return f"publications/{segment}/"


def pull_through_path(kind: str) -> str:
    """Relative collection path for a container pull-through resource kind."""
    try:
        collection = _KIND_COLLECTION[kind]
    except KeyError:
        raise ValueError("unsupported pull-through resource kind") from None
    return f"{collection}/{PULL_THROUGH_SEGMENT}/"


def validate_name(kind: str, value: str) -> str:
    value = value.strip() if isinstance(value, str) else ""
    if not _NAME_PATTERN.match(value):
        raise ValueError(
            f"{kind} must be 1-63 characters of lowercase letters, digits, hyphen, "
            "or underscore, and must start with a letter or digit"
        )
    return value


def validate_plugin(value: str) -> str:
    if not isinstance(value, str) or value not in CONTENT_PLUGINS:
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
