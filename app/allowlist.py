"""Runtime source-host allowlist, re-read from the ConfigMap at the point of use.

`Settings.allowed_source_hosts` is loaded once at startup. Editing the ConfigMap
from the UI would otherwise not take effect until a pod restart, and the page
would have to admit that. Instead the effective list is resolved per request: the
ConfigMap value wins when present, the startup value is the fallback when the
ConfigMap or key is absent. `Settings.allowed_source_hosts` therefore still means
what it always did (the env/startup value) and existing tests keep working.

The read is one extra ConfigMap GET per sync/pull-through request. Those are
operator-initiated and already make several Pulp calls, so the cost is negligible;
no cache is introduced because a stale cache would reintroduce the very restart
problem this module removes.
"""

import logging

from app.config import canonicalize_allowed_source_hosts, parse_allowed_source_hosts
from app.k8s import CONFIGMAP_ALLOWED_HOSTS_KEY, SecretError

logger = logging.getLogger(__name__)


async def effective_allowlist(store, settings) -> tuple[str, ...]:
    """Resolve the allowlist from the fixed ConfigMap, falling back to settings.

    `store` is a caller-owned ConfigMapStore; the caller closes it, matching the
    per-request factory pattern used for the Secrets store. A Kubernetes API failure
    falls back to the startup value rather than denying every host: the ConfigMap
    read must not be able to break sync outright, and that is the pre-existing
    behaviour for a cluster whose ConfigMap was never edited.
    """
    try:
        raw = await store.get_value(CONFIGMAP_ALLOWED_HOSTS_KEY)
    except SecretError as exc:
        logger.warning("allowlist.read_failed %s", exc.safe_message)
        return settings.allowed_source_hosts
    if raw is None:
        return settings.allowed_source_hosts
    # Stored values are canonical, but parse leniently: a hand-edited ConfigMap must
    # not crash sync. Invalid entries are dropped, not fatal.
    return parse_allowed_source_hosts(raw)


def canonical_or_raise(raw_hosts: str) -> tuple[str, ...]:
    """Canonicalize a submitted allowlist, surfacing the validation message."""
    return canonicalize_allowed_source_hosts(raw_hosts)
