"""中间件与全局异常处理。

- TraceIdMiddleware：每请求生成 trace_id，挂 request.state，响应头回传 X-Trace-Id。
- 四类异常（BizError / RequestValidationError / HTTPException / 未捕获 Exception）
  全部经 _api_json 收敛成统一 ApiResponse{code,msg,data,trace_id}，杜绝框架默认的 {"detail"}。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException  # 必须用 starlette 的：Router 抛的是它的基类，注册 fastapi.HTTPException(子类) 会被默认 handler 抢先命中
from starlette.middleware.base import BaseHTTPMiddleware

from .logging import new_trace_id
from .response import ApiResponse, BizError, ErrorCode

log = logging.getLogger("recommend.http")


# HTTPException 状态码 → (业务码, msg)；未命中状态码 fallback 用 (status_code, detail)。
_HTTP_CODE_MAP = {
    401: (ErrorCode.UNAUTHORIZED, "未授权"),
    403: (ErrorCode.FORBIDDEN, "禁止访问"),
    404: (ErrorCode.NOT_FOUND, "接口不存在"),
    405: (ErrorCode.METHOD_NOT_ALLOWED, "方法不允许"),
    429: (ErrorCode.RATE_LIMITED, "请求过于频繁"),
}


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tid = new_trace_id()
        request.state.trace_id = tid
        log.info(f"-> {request.method} {request.url.path}")
        resp = await call_next(request)
        resp.headers["X-Trace-Id"] = tid
        return resp


def _api_json(request: Request, status_code: int, code: int, msg: str, data=None) -> JSONResponse:
    """统一构造 ApiResponse JSON 响应（自动注入 trace_id）。

    所有异常 handler 共用此函数，保证成功 / 业务错误 / 422 / 404 / 405 / 500 响应结构完全一致。
    """
    tid = getattr(request.state, "trace_id", None)
    return JSONResponse(
        status_code=status_code,
        content=ApiResponse(code=code, msg=msg, data=data, trace_id=tid).model_dump(),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def _biz(request: Request, exc: BizError):
        log.warning(f"BizError code={exc.code} msg={exc.msg} path={request.url.path}")
        return _api_json(request, exc.http_status, exc.code, exc.msg, exc.data)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        """请求体/参数 Pydantic 校验失败（422）→ code=40001，data.errors 透传字段级报错。"""
        log.warning(f"ValidationError path={request.url.path} errors={exc.errors()}")
        return _api_json(request, 422, ErrorCode.BAD_REQUEST, "请求参数校验失败",
                         {"errors": jsonable_encoder(exc.errors())})

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):
        """路由不存在(404)/方法不允许(405)等 → 按状态码映射业务码（见 _HTTP_CODE_MAP）。"""
        code, msg = _HTTP_CODE_MAP.get(
            exc.status_code, (exc.status_code, str(exc.detail or "http error")))
        log.warning(f"HTTPException path={request.url.path} status={exc.status_code}")
        return _api_json(request, exc.status_code, code, msg)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception(f"unhandled error path={request.url.path}: {exc}")
        return _api_json(request, 500, ErrorCode.INTERNAL, "internal error")
