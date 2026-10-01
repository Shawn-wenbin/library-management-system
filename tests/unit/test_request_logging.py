import asyncio
import json
import logging
import re

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app


def request_records(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [json.loads(r.message) for r in caplog.records if r.name == "app.requests"]


@pytest.mark.parametrize("status", [200, 204, 401, 403, 404, 409, 422, 500, 503])
async def test_response_and_log_correlation(
    settings: Settings, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    app = create_app(settings)

    @app.get("/probe/{entity_id}", status_code=204)
    async def probe(entity_id: int) -> None:
        if status == 500:
            raise RuntimeError("private-exception")
        if status != 204:
            raise AppError(status, "PROBE", "测试响应")

    caplog.set_level(logging.INFO, logger="app.requests")
    path = "/probe/not-an-int" if status == 422 else "/probe/123"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path, headers={"X-Request-ID": "test-request_123"})
    assert response.status_code == status
    assert response.headers["X-Request-ID"] == "test-request_123"
    [record] = request_records(caplog)
    assert record["request_id"] == "test-request_123"
    assert record["route"] == "/probe/{entity_id}"
    assert record["status"] == status
    assert record["method"] == "GET" and record["duration_ms"] >= 0
    assert record["timestamp"].endswith("+00:00")
    if status == 500:
        assert record["error_type"] == "RuntimeError" and record["outcome"] == "error"
    assert "private-exception" not in caplog.text + response.text
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("value", [None, "", "x" * 65, "bad value", "bad\r\nvalue", "é"])
async def test_invalid_or_missing_request_id(settings: Settings, value: str | None) -> None:
    headers = [] if value is None else [(b"X-Request-ID", value.encode("utf-8"))]
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        response = await client.get("/health", headers=headers)
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


async def test_duplicate_request_id_is_replaced(settings: Settings) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        response = await client.get("/health", headers=[("X-Request-ID", "one")] * 2)
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


async def test_logs_exclude_sensitive_input(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.requests")
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        await client.post(
            "/api/v1/auth/register?password=private-query",
            json={"password": "private-body"},
            headers={"Authorization": "Bearer private-token", "Cookie": "private-cookie"},
        )
        await client.get("/private-path")
    records = request_records(caplog)
    assert [r["status"] for r in records] == [422, 404]
    assert records[1]["route"] == "<unmatched>"
    assert "private-" not in json.dumps(records)
    assert records[0]["request_id"] != records[1]["request_id"]


async def test_concurrent_requests_keep_separate_context(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings)
    barrier = asyncio.Barrier(2)

    @app.get("/concurrent")
    async def concurrent(request: Request) -> dict[str, str]:
        await asyncio.wait_for(barrier.wait(), timeout=5)
        return {"request_id": request.state.request_id}

    caplog.set_level(logging.INFO, logger="app.requests")

    async def call(request_id: str) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/concurrent", headers={"X-Request-ID": request_id})
            assert response.headers["X-Request-ID"] == response.json()["request_id"] == request_id

    await asyncio.gather(call("request-one"), call("request-two"))
    assert {r["request_id"] for r in request_records(caplog)} == {"request-one", "request-two"}


async def test_cancellation_is_not_converted_to_success(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings)

    @app.get("/cancel")
    async def cancel() -> None:
        raise asyncio.CancelledError

    caplog.set_level(logging.INFO, logger="app.requests")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(asyncio.CancelledError):
            await client.get("/cancel")
    [record] = request_records(caplog)
    assert record["status"] is None and record["outcome"] == "interrupted"
