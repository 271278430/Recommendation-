"""中间件与全局异常处理。

- TraceIdMiddleware：每请求生成 trace_id，挂 request.state，响应头回传 X-Trace-Id。
- BizError 处理器：业务异常 → 统一 ApiResponse（带 code/msg/data/trace_id）。
- 兜底 Exception 处理器：未捕获异常 → 500 + 内部错误，日志记录堆栈。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .logging import new_trace_id
from .response import ApiResponse, BizError, ErrorCode

log = logging.getLogger("recommend.http")


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tid = new_trace_id()
        request.state.trace_id = tid
        log.info(f"-> {request.method} {request.url.path}")
        resp = await call_next(request)
        resp.headers["X-Trace-Id"] = tid
        return resp


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def _biz(request: Request, exc: BizError):
        tid = getattr(request.state, "trace_id", None)
        log.warning(f"BizError code={exc.code} msg={exc.msg} path={request.url.path}")
        return JSONResponse(
            status_code=exc.http_status,
            content=ApiResponse(code=exc.code, msg=exc.msg, data=exc.data, trace_id=tid).model_dump(),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        tid = getattr(request.state, "trace_id", None)
        log.exception(f"unhandled error path={request.url.path}: {exc}")
        return JSONResponse(
            status_code=500,
            content=ApiResponse(code=ErrorCode.INTERNAL, msg="internal error", trace_id=tid).model_dump(),
        )
