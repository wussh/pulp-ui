import base64
import binascii

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse

from app.security import check_credentials, issue_csrf, validate_csrf

EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})
CSRF_COOKIE = "pulp_ops_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_REALM = 'Basic realm="pulp-ops"'


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"error": "unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": _REALM},
    )


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        settings = request.app.state.settings
        header = request.headers.get("authorization", "")
        username = password = ""
        if header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
                username, _, password = decoded.partition(":")
            except (binascii.Error, UnicodeDecodeError):
                username = password = ""

        if not check_credentials(username, password, settings):
            return _unauthorized()

        request.state.username = username
        response = await call_next(request)
        if request.method in _SAFE_METHODS and response.status_code < 400:
            # Reuse the existing nonce so a client that captured the token once keeps
            # working across safe GETs (e.g. the task-detail poll). Rotate only when
            # there is no cookie, or the accompanying token fails validation.
            nonce = request.cookies.get(CSRF_COOKIE)
            token = request.headers.get(CSRF_HEADER)
            if not nonce or (token and not validate_csrf(settings.session_secret, nonce, token)):
                nonce, token = issue_csrf(settings.session_secret)
                response.set_cookie(
                    CSRF_COOKIE,
                    nonce,
                    httponly=False,
                    samesite="strict",
                    secure=request.url.scheme == "https",
                    path="/ui",
                )
            else:
                _, token = issue_csrf(settings.session_secret, nonce)
            response.headers[CSRF_HEADER] = token
        return response


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        settings = request.app.state.settings
        nonce = request.cookies.get(CSRF_COOKIE)
        token = request.headers.get(CSRF_HEADER)
        if not validate_csrf(settings.session_secret, nonce, token):
            return PlainTextResponse("csrf validation failed", status_code=403)
        return await call_next(request)
