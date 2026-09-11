import re

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.endpoints import (
    CONTENT_PLUGINS,
    PUBLICATION_SEGMENTS,
    plugin_api,
    plugin_resource_path,
    publication_path,
    pull_through_path,
    requires_base_path,
    validate_ansible_collection,
    validate_name,
    validate_plugin,
    validate_python_package,
)
from app.pulp import PulpError
from app.safety import validate_source_url

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _record_id(href: str) -> str:
    return href.rstrip("/").split("/")[-1]


def _listing(response: dict) -> list[dict]:
    return [
        {"name": item.get("name"), "pulp_href": item.get("pulp_href")}
        for item in response.get("results", [])
    ]


async def list_content(client, domain: str) -> dict:
    domain = validate_name("domain", domain)
    out: dict[str, dict] = {}
    loaded = 0
    last_correlation = ""
    for plugin in CONTENT_PLUGINS:
        entry: dict = {"repositories": [], "distributions": [], "error": ""}
        errors: list[str] = []
        for kind, key in (("repository", "repositories"), ("distribution", "distributions")):
            try:
                response = await client.request(
                    "GET", plugin_api(domain, plugin_resource_path(plugin, kind))
                )
            except PulpError as exc:
                # A single broken plugin endpoint must not fail the whole page.
                errors.append(f"{key}: {exc.safe_message}")
                last_correlation = exc.correlation_id
                continue
            entry[key] = _listing(response)
        if errors:
            entry["error"] = "; ".join(errors)
        else:
            loaded += 1
        out[plugin] = entry
    if loaded == 0:
        raise PulpError(
            "every content plugin endpoint failed", correlation_id=last_correlation
        )
    return {"domain": domain, "plugins": out}


def _require_resource_href(
    domain: str, plugin: str, kind: str, href, label: str
) -> str:
    href = str(href or "")
    expected_prefix = plugin_api(domain, plugin_resource_path(plugin, kind))
    if not href.startswith(expected_prefix):
        raise ValueError(f"{label} href is not valid for this domain and plugin")
    if href.rstrip("/") == expected_prefix.rstrip("/"):
        raise ValueError(f"{label} href must identify a specific {label}")
    return href


def _require_repository_href(domain: str, plugin: str, href: str) -> str:
    return _require_resource_href(domain, plugin, "repository", href, "repository")


def _require_distribution_href(domain: str, plugin: str, href) -> str:
    return _require_resource_href(domain, plugin, "distribution", href, "distribution")


def _require_remote_href(domain: str, plugin: str, href) -> str:
    return _require_resource_href(domain, plugin, "remote", href, "remote")


async def _sync_repository(client, domain, plugin, repository_href, correlation_id):
    return await client.request(
        "POST",
        plugin_api(domain, plugin_resource_path(plugin, "repository"))
        + f"{_record_id(repository_href)}/sync/",
        json_body={},
        correlation_id=correlation_id,
    )


def _new_names(raw, validator) -> set[str]:
    if not isinstance(raw, list):
        raise ValueError("names must be a list")
    names: set[str] = set()
    for item in raw:
        names.add(validator(item))
    return names


async def update_python_includes(client, payload: dict, correlation_id: str) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    remote_href = _require_remote_href(domain, "python", payload.get("remote_href", ""))
    repository_href = _require_repository_href(
        domain, "python", payload.get("repository_href", "")
    )
    names = _new_names(payload.get("names"), validate_python_package)
    if not names:
        raise ValueError("at least one package name is required")
    remote = await client.request("GET", remote_href, correlation_id=correlation_id)
    current = {
        str(item.get("name"))
        for item in remote.get("includes") or []
        if isinstance(item, dict) and item.get("name")
    }
    merged = sorted(current | names)
    if merged == sorted(current):
        # Nothing to add: do not issue an empty sync (it would re-walk the upstream).
        return {"changed": False, "includes": merged, "task_href": ""}
    await client.request(
        "PATCH",
        remote_href,
        json_body={"includes": [{"name": name} for name in merged]},
        correlation_id=correlation_id,
    )
    result = await _sync_repository(
        client, domain, "python", repository_href, correlation_id
    )
    return {
        "changed": True,
        "includes": merged,
        "task_href": result.get("task", ""),
    }


def _parse_ansible_requirements(raw) -> list[str]:
    """Collection names under the top-level `collections:` block only.

    Stops at the next top-level key, so a `roles:` entry is never mistaken for a
    collection (which would make an unrelated role look like an existing package).
    """
    collections: list[str] = []
    in_collections = False
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if stripped == "collections:":
            in_collections = True
            continue
        if in_collections and stripped and not stripped.startswith("#") and not line[:1].isspace():
            break
        if in_collections and stripped.startswith("- name:"):
            collections.append(stripped.split("- name:", 1)[1].strip())
    return collections


_ANSIBLE_ENTRY = re.compile(r"^(\s*)-\s+name:\s*(?P<name>\S.*?)\s*$")


def _merge_ansible_requirements(raw: str, names: set[str]) -> str:
    """Add `names` to the collections block, preserving every other line.

    Treats requirements_file as the operator's document: lines that are not
    collections entries (comments, blank lines, other top-level keys such as
    `roles:`) are copied verbatim. The collections entries are rebuilt as the
    sorted union. A shape we cannot confidently reason about is refused rather
    than rewritten, so operator content is never silently deleted.
    """
    text = str(raw or "")
    lines = text.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == "collections:" and not line[:1].isspace()
        ),
        None,
    )
    if header_index is None:
        if text.strip():
            raise ValueError(
                "requirements_file uses a shape this UI does not recognise "
                "(no top-level `collections:` block); edit the remote's "
                "requirements_file directly instead"
            )
        # An empty file has no operator content to destroy; treat it as a fresh
        # collections document.
        lines = ["collections:"]
        header_index = 0

    # The collections block ends at the next top-level (unindented, non-blank,
    # non-comment) key.
    block_end = len(lines)
    for index in range(header_index + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[:1].isspace():
            block_end = index
            break

    existing: list[str] = []
    for index in range(header_index + 1, block_end):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ANSIBLE_ENTRY.match(line)
        if not match:
            raise ValueError(
                "requirements_file contains a collections entry this UI cannot "
                "safely rewrite; edit the remote's requirements_file directly "
                "instead"
            )
        existing.append(match.group("name"))

    merged = sorted(set(existing) | names)
    new_entries = [f"  - name: {name}" for name in merged]

    result = lines[: header_index + 1]
    inserted = False
    for index in range(header_index + 1, block_end):
        line = lines[index]
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            # Replace the old entry lines with the rebuilt, sorted entries.
            if not inserted:
                result.extend(new_entries)
                inserted = True
            continue
        result.append(line)
    if not inserted:
        result.extend(new_entries)
    result.extend(lines[block_end:])
    return "\n".join(result) + "\n"


def _guard_ansible_requirements(requirements_file: str) -> str:
    # A sync with no requirements downloads all of galaxy.ansible.com and OOMs the
    # worker. The merge always contains at least the submitted names, but this stays
    # a standalone guard so no future refactor can send an empty file.
    if not _parse_ansible_requirements(requirements_file):
        raise ValueError("refusing to sync ansible with an empty requirements_file")
    return requirements_file


async def update_ansible_collections(client, payload: dict, correlation_id: str) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    remote_href = _require_remote_href(
        domain, "ansible", payload.get("remote_href", "")
    )
    repository_href = _require_repository_href(
        domain, "ansible", payload.get("repository_href", "")
    )
    names = _new_names(payload.get("names"), validate_ansible_collection)
    if not names:
        raise ValueError("at least one collection name is required")
    remote = await client.request("GET", remote_href, correlation_id=correlation_id)
    current_file = str(remote.get("requirements_file") or "")
    current = set(_parse_ansible_requirements(current_file))
    # Merge into the operator's document rather than rebuilding it: anything besides
    # collection entries must survive the PATCH untouched.
    requirements_file = _merge_ansible_requirements(current_file, names)
    merged = _parse_ansible_requirements(requirements_file)
    if set(merged) == current:
        return {
            "changed": False,
            "collections": merged,
            "requirements_file": current_file,
            "task_href": "",
        }
    # A sync with no requirements downloads all of galaxy.ansible.com and OOMs the
    # worker. The merge always adds at least the submitted names, but this stays a
    # standalone guard so no future refactor can send a requirements-less file.
    _guard_ansible_requirements(requirements_file)
    await client.request(
        "PATCH",
        remote_href,
        json_body={"requirements_file": requirements_file},
        correlation_id=correlation_id,
    )
    result = await _sync_repository(
        client, domain, "ansible", repository_href, correlation_id
    )
    return {
        "changed": True,
        "collections": merged,
        "requirements_file": requirements_file,
        "task_href": result.get("task", ""),
    }


def _require_publication_href(domain: str, plugin: str, href) -> str:
    href = str(href or "")
    # publication_path raises for plugins without a publication endpoint.
    expected_prefix = plugin_api(domain, publication_path(plugin))
    if not href.startswith(expected_prefix):
        raise ValueError("publication href is not valid for this domain and plugin")
    if href.rstrip("/") == expected_prefix.rstrip("/"):
        raise ValueError("publication href must identify a specific publication")
    return href


async def publish_repository(client, payload: dict, correlation_id: str) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    plugin = validate_plugin(payload.get("plugin", ""))
    # Reject plugins with no publication endpoint before any upstream call.
    path = plugin_api(domain, publication_path(plugin))
    repository_href = _require_repository_href(
        domain, plugin, payload.get("repository_href", "")
    )
    repository = await client.request(
        "GET", repository_href, correlation_id=correlation_id
    )
    latest_version_href = str(repository.get("latest_version_href") or "")
    if not latest_version_href:
        raise ValueError("repository has no version to publish")
    # Fire the publish and return the task; the operator follows it on /ui/tasks.
    result = await client.request(
        "POST",
        path,
        json_body={"repository_version": latest_version_href},
        correlation_id=correlation_id,
    )
    return {"task_href": result.get("task", "")}


async def link_publication(client, payload: dict, correlation_id: str) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    plugin = validate_plugin(payload.get("plugin", ""))
    distribution_href = _require_distribution_href(
        domain, plugin, payload.get("distribution_href", "")
    )
    publication_href = _require_publication_href(
        domain, plugin, payload.get("publication_href", "")
    )
    # rpm/deb bind through `publication` with `repository` null; the UI previously
    # bound `repository`, so synced content was never served on those plugins.
    result = await client.request(
        "PATCH",
        distribution_href,
        json_body={"repository": None, "publication": publication_href},
        correlation_id=correlation_id,
    )
    return {"task_href": result.get("task", "")}


def _pull_through_rows(remotes: list, dists: list) -> list[dict]:
    remote_by_href = {item.get("pulp_href"): item for item in remotes}
    rows: list[dict] = []
    for dist in dists:
        # A pull-through distribution binds the remote directly; there is no
        # repository for pull-through. Report a null/unresolvable link instead of
        # dropping the distribution: a silent omission hides a live registry.
        remote = remote_by_href.get(dist.get("remote"))
        if remote is None:
            rows.append(
                {
                    "name": dist.get("name"),
                    "base_path": dist.get("base_path"),
                    "upstream_name": "",
                    "upstream_url": "",
                    "distribution_href": dist.get("pulp_href"),
                    "note": "distribution has no resolvable pull-through remote",
                }
            )
            continue
        rows.append(
            {
                "name": dist.get("name"),
                "base_path": dist.get("base_path"),
                # Pulp's pull-through remote has no `upstream_name` field; the
                # registry name is the remote's `name` (verified live).
                "upstream_name": remote.get("name"),
                "upstream_url": remote.get("url"),
                "distribution_href": dist.get("pulp_href"),
                "note": "",
            }
        )
    return rows


async def list_pull_through(client, domain: str) -> dict:
    domain = validate_name("domain", domain)
    fetched: list[list] = []
    for kind in ("remote", "distribution"):
        response = await client.request(
            "GET", plugin_api(domain, pull_through_path(kind))
        )
        fetched.append(response.get("results", []))
    remotes, dists = fetched
    return {"domain": domain, "rows": _pull_through_rows(remotes, dists)}


async def add_pull_through_registry(
    client, payload: dict, correlation_id: str, settings
) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    name = validate_name("registry", payload.get("name", ""))
    raw_base_path = payload.get("base_path")
    if not isinstance(raw_base_path, str):
        raise ValueError("base_path must be a string")
    base_path = raw_base_path.strip().strip("/")
    if not base_path:
        raise ValueError("base_path is required")
    # Reuse the shared SSRF guard rather than re-implementing host checks.
    upstream_url = validate_source_url(
        str(payload.get("upstream_url") or ""), settings.allowed_source_hosts
    )
    completed: list[dict] = []
    try:
        remote = await client.request(
            "POST",
            plugin_api(domain, pull_through_path("remote")),
            json_body={"name": name, "url": upstream_url},
            correlation_id=correlation_id,
        )
        completed.append({"resource": "remote", "pulp_href": remote.get("pulp_href", "")})
        # Two-step create: pull-through has no repository, so the distribution
        # carries both base_path and the remote href directly. The operator names
        # the remote; the distribution follows this cluster's `<name>-proxy`
        # convention. base_path is the bare registry name — no `container/` prefix;
        # the client URL is `/v2/<domain>/<base_path>/...`.
        dist = await client.request(
            "POST",
            plugin_api(domain, pull_through_path("distribution")),
            json_body={
                "name": f"{name}-proxy",
                "base_path": base_path,
                "remote": remote.get("pulp_href", ""),
            },
            correlation_id=correlation_id,
        )
        completed.append(
            {
                "resource": "distribution",
                "pulp_href": dist.get("pulp_href", ""),
                "task_href": dist.get("task", ""),
            }
        )
    except PulpError as exc:
        return {
            "correlation_id": correlation_id,
            "completed": completed,
            "stopped_at": {"resource": "pull-through registry", "name": name},
            "message": exc.safe_message,
            "failed": True,
        }
    return {
        "correlation_id": correlation_id,
        "completed": completed,
        "task_href": completed[-1].get("task_href", ""),
        "failed": False,
    }


async def create_repository(client, payload: dict, correlation_id: str) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    plugin = validate_plugin(payload.get("plugin", ""))
    name = validate_name("repository", payload.get("name", ""))
    return await client.request(
        "POST",
        plugin_api(domain, plugin_resource_path(plugin, "repository")),
        json_body={"name": name},
        correlation_id=correlation_id,
    )


async def create_distribution(client, payload: dict, correlation_id: str) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    plugin = validate_plugin(payload.get("plugin", ""))
    name = validate_name("distribution", payload.get("name", ""))
    repository_href = _require_repository_href(
        domain, plugin, payload.get("repository_href", "")
    )
    body: dict = {"name": name, "repository": repository_href}
    if requires_base_path(plugin):
        raw_base_path = payload.get("base_path")
        if raw_base_path is not None and not isinstance(raw_base_path, str):
            raise ValueError("base_path must be a string")
        base_path = (raw_base_path or "").strip().strip("/")
        if not base_path:
            raise ValueError("base_path is required for container distributions")
        body["base_path"] = base_path
    return await client.request(
        "POST",
        plugin_api(domain, plugin_resource_path(plugin, "distribution")),
        json_body=body,
        correlation_id=correlation_id,
    )


async def start_sync(client, payload: dict, correlation_id: str, settings) -> dict:
    domain = validate_name("domain", payload.get("domain", ""))
    plugin = validate_plugin(payload.get("plugin", ""))
    repository_href = _require_repository_href(
        domain, plugin, payload.get("repository_href", "")
    )
    remote_url = validate_source_url(
        str(payload.get("remote_url") or ""), settings.allowed_source_hosts
    )
    # The correlation id is unique per attempt, so a retry after a failed sync never
    # collides with Pulp's per-plugin remote-name uniqueness constraint.
    remote_name = validate_name("remote", f"sync-{correlation_id}")
    remote = await client.request(
        "POST",
        plugin_api(domain, plugin_resource_path(plugin, "remote")),
        json_body={"name": remote_name, "url": remote_url},
        correlation_id=correlation_id,
    )
    remote_href = remote.get("pulp_href", "")
    try:
        result = await client.request(
            "POST",
            plugin_api(domain, plugin_resource_path(plugin, "repository"))
            + f"{_record_id(repository_href)}/sync/",
            json_body={"remote": remote_href},
            correlation_id=correlation_id,
        )
    except PulpError as exc:
        # The sync failed but the remote was created: report it so the operator can
        # retry without orphaning the remote.
        return {
            "task_href": "",
            "correlation_id": correlation_id,
            "remote_href": remote_href,
            "error": exc.safe_message,
            "failed": True,
        }
    return {
        "task_href": result.get("task", ""),
        "correlation_id": correlation_id,
        "remote_href": remote_href,
    }


async def _guarded(request: Request, handler) -> JSONResponse:
    app = request.app
    client = app.state.client_factory()
    correlation_id = app.state.correlations.new()
    try:
        result = await handler(client, correlation_id)
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message, "correlation_id": exc.correlation_id},
            status_code=exc.status_code or 502,
        )
    await client.aclose()
    body = {"correlation_id": correlation_id, **result}
    return JSONResponse(body, status_code=400 if result.get("failed") else 200)


@router.get("/content", response_class=HTMLResponse)
async def content_page(request: Request) -> HTMLResponse:
    domain = request.query_params.get("domain", "default")
    choices = list(CONTENT_PLUGINS)
    publish_choices = list(PUBLICATION_SEGMENTS)
    client = request.app.state.client_factory()
    try:
        data = await list_content(client, domain)
    except ValueError as exc:
        await client.aclose()
        return templates.TemplateResponse(
            request,
            "content.html",
            {
                "error": str(exc),
                "plugins": {},
                "plugin_choices": choices,
                "publish_plugin_choices": publish_choices,
                "domain": domain,
                "warnings": [],
                "current_user": "operator",
            },
            status_code=400,
        )
    except PulpError as exc:
        await client.aclose()
        return templates.TemplateResponse(
            request,
            "content.html",
            {
                "error": exc.safe_message,
                "plugins": {},
                "plugin_choices": choices,
                "publish_plugin_choices": publish_choices,
                "domain": domain,
                "warnings": [],
                "current_user": "operator",
            },
            status_code=502,
        )
    # The pull-through view is a separate read; its failure must not take down the
    # content page, matching the per-plugin isolation in list_content.
    pull_through: list[dict] = []
    pull_through_error = ""
    try:
        pull_through = (await list_pull_through(client, domain))["rows"]
    except (ValueError, PulpError) as exc:
        pull_through_error = exc.safe_message if isinstance(exc, PulpError) else str(exc)
    await client.aclose()
    return templates.TemplateResponse(
        request,
        "content.html",
        {
            **data,
            "plugin_choices": choices,
            "publish_plugin_choices": publish_choices,
            "pull_through": pull_through,
            "pull_through_error": pull_through_error,
            "warnings": [],
            "current_user": "operator",
        },
    )


@router.get("/api/content")
async def content_api(request: Request) -> JSONResponse:
    domain = request.query_params.get("domain", "default")
    client = request.app.state.client_factory()
    try:
        body = await list_content(client, domain)
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message, "correlation_id": exc.correlation_id},
            status_code=exc.status_code or 502,
        )
    await client.aclose()
    return JSONResponse(body)


@router.post("/api/content/repository")
async def repository_create(request: Request, payload: dict = Body(...)) -> JSONResponse:
    async def handler(client, correlation_id):
        created = await create_repository(client, payload, correlation_id)
        request.app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": getattr(request.state, "username", None) or "operator",
                "action": "content.repository.create",
                "target": payload.get("name", ""),
                "target_type": "repository",
                "result": "completed",
            }
        )
        return {"pulp_href": created.get("pulp_href", "")}

    return await _guarded(request, handler)


@router.post("/api/content/distribution")
async def distribution_create(
    request: Request, payload: dict = Body(...)
) -> JSONResponse:
    async def handler(client, correlation_id):
        created = await create_distribution(client, payload, correlation_id)
        request.app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": getattr(request.state, "username", None) or "operator",
                "action": "content.distribution.create",
                "target": payload.get("name", ""),
                "target_type": "distribution",
                "result": "completed",
            }
        )
        # Distribution creation is asynchronous: Pulp answers 202 with a task and no
        # pulp_href. Surface the task so the UI can link to live progress.
        return {
            "pulp_href": created.get("pulp_href", ""),
            "task_href": created.get("task", ""),
        }

    return await _guarded(request, handler)


@router.get("/api/container/status")
@router.get("/api/content/pull-through")
async def pull_through_status(request: Request) -> JSONResponse:
    client = request.app.state.client_factory()
    try:
        body = await list_pull_through(
            client, request.query_params.get("domain", "default")
        )
    except ValueError as exc:
        await client.aclose()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PulpError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": exc.safe_message, "correlation_id": exc.correlation_id},
            status_code=exc.status_code or 502,
        )
    await client.aclose()
    return JSONResponse(body)


@router.post("/api/content/python/includes")
async def python_includes(request: Request, payload: dict = Body(...)) -> JSONResponse:
    async def handler(client, correlation_id):
        result = await update_python_includes(client, payload, correlation_id)
        request.app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": getattr(request.state, "username", None) or "operator",
                "action": "content.python.includes",
                "target": payload.get("remote_href", ""),
                "target_type": "remote",
                "result": "completed",
            }
        )
        return result

    return await _guarded(request, handler)


@router.post("/api/content/ansible/collections")
async def ansible_collections(
    request: Request, payload: dict = Body(...)
) -> JSONResponse:
    async def handler(client, correlation_id):
        result = await update_ansible_collections(client, payload, correlation_id)
        request.app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": getattr(request.state, "username", None) or "operator",
                "action": "content.ansible.collections",
                "target": payload.get("remote_href", ""),
                "target_type": "remote",
                "result": "completed",
            }
        )
        return result

    return await _guarded(request, handler)


@router.post("/api/content/pull-through")
async def pull_through_add(request: Request, payload: dict = Body(...)) -> JSONResponse:
    async def handler(client, correlation_id):
        result = await add_pull_through_registry(
            client, payload, correlation_id, request.app.state.settings
        )
        request.app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": getattr(request.state, "username", None) or "operator",
                "action": "content.pull_through.add",
                "target": payload.get("name", ""),
                "target_type": "distribution",
                "result": "failed" if result.get("failed") else "completed",
            }
        )
        return result

    return await _guarded(request, handler)


@router.post("/api/content/publish")
async def publish(request: Request, payload: dict = Body(...)) -> JSONResponse:
    async def handler(client, correlation_id):
        result = await publish_repository(client, payload, correlation_id)
        request.app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": getattr(request.state, "username", None) or "operator",
                "action": "content.publish",
                "target": payload.get("repository_href", ""),
                "target_type": "repository",
                "result": "completed",
            }
        )
        return result

    return await _guarded(request, handler)


@router.post("/api/content/link")
async def link(request: Request, payload: dict = Body(...)) -> JSONResponse:
    async def handler(client, correlation_id):
        result = await link_publication(client, payload, correlation_id)
        request.app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": getattr(request.state, "username", None) or "operator",
                "action": "content.link",
                "target": payload.get("distribution_href", ""),
                "target_type": "distribution",
                "result": "completed",
            }
        )
        return result

    return await _guarded(request, handler)


@router.post("/api/content/sync")
async def sync_start(request: Request, payload: dict = Body(...)) -> JSONResponse:
    async def handler(client, correlation_id):
        result = await start_sync(
            client, payload, correlation_id, request.app.state.settings
        )
        request.app.state.activity.record(
            {
                "correlation_id": correlation_id,
                "operator": getattr(request.state, "username", None) or "operator",
                "action": "content.sync",
                "target": payload.get("repository_href", ""),
                "target_type": "repository",
                "result": "failed" if result.get("failed") else "completed",
            }
        )
        return result

    return await _guarded(request, handler)
