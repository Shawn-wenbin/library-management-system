"""记录脱敏请求摘要；请求上下文仅保存在当前 ASGI 调用中。"""

import json
import logging
import re
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.requests")
REQUEST_ID = re.compile(rb"[A-Za-z0-9._-]{1,64}")


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        values = [value for key, value in scope["headers"] if key.lower() == b"x-request-id"]
        request_id = (
            values[0].decode("ascii")
            if len(values) == 1 and REQUEST_ID.fullmatch(values[0])
            else uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        status: int | None = None
        error_type: str | None = None
        outcome = "interrupted"

        async def send_response(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_response)
            outcome = "complete"
        except Exception as exc:
            # 不记录异常消息或堆栈，其中可能包含 SQL、口令及请求输入。
            error_type = type(exc).__name__
            outcome = "error"
            if status is not None:
                # 响应已开始时不能再次发送响应头，向服务器报告脱敏异常。
                raise RuntimeError("响应发送失败") from None
            response = JSONResponse(
                status_code=500,
                content={"code": "INTERNAL_ERROR", "message": "服务暂时不可用", "details": None},
            )
            await response(scope, receive, send_response)
        finally:
            # 使用匹配后的路由模板；未知路径和路径参数可能包含敏感信息。
            route = getattr(scope.get("route"), "path", "<unmatched>")
            record = {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": "http_request",
                "request_id": request_id,
                "method": scope["method"],
                "route": route,
                "status": status,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "outcome": outcome,
            }
            if error_type is not None:
                record["error_type"] = error_type
            level = logging.ERROR if error_type or (status and status >= 500) else logging.INFO
            logger.log(level, json.dumps(record, ensure_ascii=False))
