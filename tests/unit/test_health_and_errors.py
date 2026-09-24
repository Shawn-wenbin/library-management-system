import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


async def test_liveness_without_database(settings: Settings) -> None:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health")).json() == {"status": "ok"}
            response = await client.get("/api/v1/users/me")
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            response = await client.post("/api/v1/auth/register", json={"password": "secret"})
            assert response.status_code == 422
            assert "secret" not in response.text
            assert set(response.json()) == {"code", "message", "details"}
            assert (await client.get("/missing")).status_code == 404


async def test_database_not_ready(settings: Settings) -> None:
    settings = Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "database_url": "mysql+asyncmy://unused:unused@127.0.0.1:1/library_test",
        },
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/ready")
    assert response.status_code == 503
    assert "unused" not in response.text


async def test_unexpected_error_does_not_leak(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings)

    @app.get("/test-error")
    async def error() -> None:
        raise RuntimeError("private-password-and-sql")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test-error")
    assert response.status_code == 500
    assert "private-password-and-sql" not in response.text + caplog.text
