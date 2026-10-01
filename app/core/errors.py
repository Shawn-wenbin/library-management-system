from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(
            status_code=exc.status,
            content={"code": exc.code, "message": exc.message, "details": None},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 不回显输入值或校验上下文，避免泄露密码等敏感字段。
        details = [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={"code": "VALIDATION_ERROR", "message": "请求参数不合法", "details": details},
        )

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": "HTTP_ERROR", "message": "请求无法处理", "details": None},
            headers=exc.headers,
        )
