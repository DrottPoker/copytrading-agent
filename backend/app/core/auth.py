import base64
import binascii
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import get_settings

AUTH_EXEMPT_PATHS = {"/health", "/ready"}


class DashboardAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        if (
            not settings.dashboard_auth_enabled
            or request.method == "OPTIONS"
            or request.url.path in AUTH_EXEMPT_PATHS
        ):
            return await call_next(request)

        if dashboard_basic_auth_is_valid(
            authorization=request.headers.get("authorization"),
            expected_username=settings.dashboard_auth_username,
            expected_password=settings.dashboard_auth_password,
        ):
            return await call_next(request)

        return JSONResponse(
            {"detail": "Authentication required."},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Hyperliquid Copy Agent"'},
        )


def dashboard_basic_auth_is_valid(
    *,
    authorization: str | None,
    expected_username: str,
    expected_password: str,
) -> bool:
    if not authorization:
        return False

    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "basic" or not credentials:
        return False

    try:
        decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False

    username, separator, password = decoded.partition(":")
    if not separator:
        return False

    return secrets.compare_digest(username, expected_username) and secrets.compare_digest(
        password,
        expected_password,
    )
