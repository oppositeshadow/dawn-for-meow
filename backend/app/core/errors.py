"""统一业务异常与错误码（代码结构稿 §4.1）。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

CODE_OK = 200
CODE_BAD_REQUEST = 400
CODE_CONFLICT = 409
CODE_NOT_FOUND = 404
CODE_INTERNAL_ERROR = 500


class GameError(Exception):
    """业务异常：HTTP 状态码与响应体 code 保持一致。"""

    def __init__(self, code: int, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class BadRequest(GameError):
    def __init__(self, message: str = "BAD_REQUEST", detail: str | None = None) -> None:
        super().__init__(CODE_BAD_REQUEST, message, detail)


class WorkstationLimitExceeded(BadRequest):
    def __init__(self, detail: str | None = None) -> None:
        super().__init__("WORKSTATION_LIMIT_EXCEEDED", detail)


class InsufficientResource(BadRequest):
    def __init__(self, detail: str | None = None) -> None:
        super().__init__("INSUFFICIENT_RESOURCE", detail)


class Conflict(GameError):
    def __init__(self, message: str = "CONFLICT", detail: str | None = None) -> None:
        super().__init__(CODE_CONFLICT, message, detail)


class NotFound(GameError):
    def __init__(self, message: str = "NOT_FOUND", detail: str | None = None) -> None:
        super().__init__(CODE_NOT_FOUND, message, detail)


def error_payload(code: int, message: str, detail: str | None = None) -> dict:
    payload = {"code": code, "message": message}
    if detail:
        payload["detail"] = detail
    return payload


def register_exception_handlers(app: FastAPI) -> None:
    """把业务异常与校验异常统一收敛成 {code, message} 结构。"""

    @app.exception_handler(GameError)
    async def _game_error_handler(_: Request, exc: GameError) -> JSONResponse:
        return JSONResponse(status_code=exc.code, content=error_payload(exc.code, exc.message, exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=CODE_BAD_REQUEST,
            content=error_payload(CODE_BAD_REQUEST, "BAD_REQUEST", str(exc.errors()[:3])),
        )
