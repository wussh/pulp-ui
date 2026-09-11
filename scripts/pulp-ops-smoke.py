#!/usr/bin/env python3
"""Read-only smoke test for the Pulp operator UI.

Usage: python3 scripts/pulp-ops-smoke.py <base-url> <user> <password>

Issues GET requests only. Exits non-zero on the first failed check.

Mutation smoke (create/sync/delete) is deliberately not performed here: it
requires a disposable domain and must be run manually per the cutover runbook.
"""

import base64
import json
import sys
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 30
EXPECTED_PLUGINS = {"rpm", "deb", "python", "ansible", "container"}


def _decode(raw):
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def get(url, auth=None):
    """Return (status, body, detail). status is None when no HTTP response arrived."""
    request = urllib.request.Request(url, method="GET")
    if auth:
        request.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, _decode(response.read()), ""
    except urllib.error.HTTPError as error:
        return error.code, _decode(error.read()), ""
    except (urllib.error.URLError, OSError, ValueError) as error:
        return None, {}, str(getattr(error, "reason", error) or error)


def check(name, passed, detail=""):
    print(f"{'PASS' if passed else 'FAIL'} {name} {detail}".rstrip())
    if not passed:
        raise SystemExit(1)


def main(argv):
    if len(argv) != 4:
        print(f"usage: {argv[0]} <base-url> <user> <password>", file=sys.stderr)
        return 2

    base = argv[1].rstrip("/")
    # The credential travels only in the Authorization header, never in the URL.
    auth = base64.b64encode(f"{argv[2]}:{argv[3]}".encode("utf-8")).decode("ascii")

    status, _, detail = get(f"{base}/ui/api/activity?limit=1")
    check("anonymous_rejected", status == 401, detail or f"status={status}")

    status, _, detail = get(f"{base}/ui/api/activity?limit=1", auth)
    check("authenticated", status == 200, detail or f"status={status}")

    _, overview, detail = get(f"{base}/ui/api/overview", auth)
    check("overview_reached", not detail, detail)
    pulp = overview.get("pulp") or {}
    check("pulp_reachable", pulp.get("reachable") is True, f"reachable={pulp.get('reachable')}")
    counts = overview.get("counts") or {}
    check("overview_has_counts", "domains" in counts, f"counts={sorted(counts)}")
    routing = overview.get("routing") or {}
    check("routing_reported", "public_status" in routing, f"public_status={routing.get('public_status')}")

    _, tenants, detail = get(f"{base}/ui/api/tenants", auth)
    check("tenants_reached", not detail, detail)
    domains = tenants.get("domains") or []
    check("domains_listed", len(domains) >= 1, f"domains={len(domains)}")

    _, tasks, detail = get(f"{base}/ui/api/tasks?domain=default", auth)
    check("tasks_reached", not detail, detail)
    check("tasks_listed", "tasks" in tasks, f"keys={sorted(tasks)}")

    _, content, detail = get(f"{base}/ui/api/content?domain=default", auth)
    check("content_reached", not detail, detail)
    plugins = set((content.get("plugins") or {}).keys())
    check("content_plugins", plugins == EXPECTED_PLUGINS, f"plugins={sorted(plugins)}")

    print("read-only smoke ok")
    print(
        "mutation smoke not run: it requires a disposable domain and must be "
        "run manually per the operator UI cutover runbook."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
