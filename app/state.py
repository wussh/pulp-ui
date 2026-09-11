import uuid


class CorrelationStore:
    def new(self, correlation_id: str | None = None) -> str:
        return correlation_id or uuid.uuid4().hex


class ActivityStore:
    def __init__(self, limit: int = 500) -> None:
        self._limit = limit
        self._entries: list[dict] = []

    def record(self, entry: dict) -> None:
        self._entries.append(entry)
        if len(self._entries) > self._limit:
            del self._entries[: len(self._entries) - self._limit]

    def recent(self, limit: int = 50) -> list[dict]:
        if limit <= 0:
            return []
        return list(reversed(self._entries[-limit:]))


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, list[str]] = {}

    def create(self, run_id: str, resources: list[str] | None = None) -> None:
        self._runs[run_id] = list(resources or [])

    def add_resource(self, run_id: str, href: str) -> None:
        self._runs.setdefault(run_id, []).append(href)

    def resources(self, run_id: str) -> list[str]:
        return list(self._runs.get(run_id, []))

    def drop(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
