import base64
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core import auth
from app.core.auth import DashboardAuthMiddleware


def authorization_header(username: str = "admin", password: str = "secret") -> str:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {encoded}"


def protected_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    settings = SimpleNamespace(
        dashboard_auth_enabled=True,
        dashboard_auth_username="admin",
        dashboard_auth_password="secret",
        cors_origin_list=["https://dashboard.example.com"],
        cors_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    app = FastAPI()
    app.add_middleware(DashboardAuthMiddleware)

    @app.api_route("/resource", methods=["GET", "POST"])
    async def resource(request: Request) -> dict[str, str | None]:
        return {"actor": getattr(request.state, "audit_actor", None)}

    return app


@pytest.mark.asyncio
async def test_authenticated_cross_origin_mutation_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = protected_app(monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://dashboard.example.com",
    ) as client:
        response = await client.post(
            "/resource",
            headers={
                "Authorization": authorization_header(),
                "Origin": "https://evil.example",
            },
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin mutation request rejected."}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "https://dashboard.example.com",
        "https://dashboard.example.com/",
        "http://localhost:3000",
    ],
)
async def test_authenticated_allowed_origin_mutation_sets_audit_actor(
    monkeypatch: pytest.MonkeyPatch,
    origin: str,
) -> None:
    app = protected_app(monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://dashboard.example.com",
    ) as client:
        response = await client.post(
            "/resource",
            headers={
                "Authorization": authorization_header(),
                "Origin": origin,
            },
        )

    assert response.status_code == 200
    assert response.json() == {"actor": "admin"}


@pytest.mark.asyncio
async def test_origin_guard_applies_only_to_mutating_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = protected_app(monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://dashboard.example.com",
    ) as client:
        response = await client.get(
            "/resource",
            headers={
                "Authorization": authorization_header(),
                "Origin": "https://evil.example",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"actor": "admin"}


@pytest.mark.asyncio
async def test_origin_guard_runs_only_after_successful_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = protected_app(monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://dashboard.example.com",
    ) as client:
        response = await client.post(
            "/resource",
            headers={
                "Authorization": authorization_header(password="wrong"),
                "Origin": "https://evil.example",
            },
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="Hyperliquid Copy Agent"'
