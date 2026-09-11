import base64
import json
import logging
import posixpath
import uuid

import httpx

from app.config import Settings
from app.logging_config import redact

logger = logging.getLogger(__name__)

_ALLOWED_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})
_SAFE_4XX_DETAIL_LIMIT = 400
_SAFE_4XX_FIELDS = frozenset(
    {
        "name",
        "username",
        "group",
        "domain",
        "base_path",
        "url",
        "repository",
        "remote",
        "role",
    }
)
_CONTROL_CHARS = frozenset(chr(code) for code in range(0x20)) | {"\x7f"}


def _reject_unsafe_path(path: str) -> None:
    if not path.startswith("/pulp/"):
        raise ValueError("path must be an internal Pulp API path")
    if "://" in path or path.startswith("//"):
        raise ValueError("absolute URLs are not permitted")
    if any(char in _CONTROL_CHARS for char in path):
        raise ValueError("path must not contain control characters")
    lowered = path.lower()
    if "%2e" in lowered or "%2f" in lowered:
        raise ValueError("path must not contain encoded traversal characters")
    if any(segment in (".", "..") for segment in path.split("/")):
        raise ValueError("path must not contain traversal segments")


def _reject_unsafe_resolved_path(resolved: httpx.URL) -> None:
    decoded = resolved.path
    normalized = posixpath.normpath(decoded)
    if not normalized.startswith("/pulp/"):
        raise ValueError("path must resolve inside the /pulp/ prefix")
    if any(segment == ".." for segment in normalized.split("/")):
        raise ValueError("path must not resolve to a traversal segment")


class PulpError(Exception):
    def __init__(
        self,
        safe_message: str,
        *,
        status_code: int | None = None,
        correlation_id: str = "",
    ) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.status_code = status_code
        self.correlation_id = correlation_id


class PulpClient:
    def __init__(self, settings: Settings) -> None:
        token = base64.b64encode(
            f"{settings.pulp_admin_user}:{settings.pulp_admin_password}".encode("utf-8")
        ).decode("ascii")
        self._settings = settings
        self._headers = {"Authorization": f"Basic {token}"}
        self._client = httpx.AsyncClient(
            base_url=settings.pulp_internal_url,
            headers=self._headers,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        try:
            await self.request("GET", "/pulp/default/api/v3/status/")
        except PulpError:
            return False
        return True

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        method = method.upper()
        if method not in _ALLOWED_METHODS:
            raise ValueError(f"unsupported method: {method}")
        _reject_unsafe_path(path)

        correlation_id = correlation_id or uuid.uuid4().hex
        # Never log headers or the Authorization value: only method, path,
        # correlation id, and the redacted json_body.
        logger.info(
            "pulp.request %s",
            redact(
                {
                    "method": method,
                    "path": path,
                    "correlation_id": correlation_id,
                    "json_body": json_body,
                }
            ),
        )
        try:
            resolved = self._client.base_url.join(path)
            _reject_unsafe_resolved_path(resolved)
            async with self._client.stream(
                method,
                path,
                json=json_body,
                params=params,
                headers=self._headers,
            ) as response:
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._settings.max_response_bytes:
                        raise PulpError(
                            "Upstream response exceeded the configured size limit.",
                            status_code=response.status_code,
                            correlation_id=correlation_id,
                        )
                    chunks.append(chunk)
                raw = b"".join(chunks)
                status = response.status_code
        except (PulpError, ValueError):
            raise
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise PulpError(
                "Pulp API request failed.", correlation_id=correlation_id
            ) from exc

        if 300 <= status < 400:
            raise PulpError(
                "Pulp API returned an unexpected redirect.",
                status_code=status,
                correlation_id=correlation_id,
            )

        if status >= 500:
            raise PulpError(
                "Pulp API returned a server error.",
                status_code=status,
                correlation_id=correlation_id,
            )

        if status >= 400:
            raise PulpError(
                _safe_4xx_message(raw),
                status_code=status,
                correlation_id=correlation_id,
            )

        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PulpError(
                "Pulp API returned a malformed response.",
                status_code=status,
                correlation_id=correlation_id,
            ) from exc
        return decoded if isinstance(decoded, dict) else {"results": decoded}


def _safe_4xx_message(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return "Pulp API rejected the request."
    if not isinstance(payload, dict):
        return "Pulp API rejected the request."
    safe = {key: value for key, value in payload.items() if key in _SAFE_4XX_FIELDS}
    if not safe:
        return "Pulp API rejected the request."
    text = json.dumps(safe, ensure_ascii=False)
    if len(text) > _SAFE_4XX_DETAIL_LIMIT:
        text = text[:_SAFE_4XX_DETAIL_LIMIT]
    return f"Pulp API rejected the request: {text}"
