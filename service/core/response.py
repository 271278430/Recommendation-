"""统一响应封装 + 错误码 + 业务异常。

所有接口返回 ApiResponse{code,msg,data,trace_id}；业务错误抛 BizError，
由全局异常处理器（middleware.py）转成统一结构。
"""
from typing import Any, Optional

from pydantic import BaseModel


class ErrorCode:
    OK = 0
    BAD_REQUEST = 40001
    UNAUTHORIZED = 40101
    FORBIDDEN = 40301
    SCOPE_NOT_FOUND = 40401
    STUDENT_NOT_FOUND = 40402
    KP_NOT_FOUND = 40403
    QUESTION_NOT_FOUND = 40404
    QUESTION_NO_KP = 40002
    RATE_LIMITED = 42901
    INTERNAL = 50001


class BizError(Exception):
    """业务异常：带错误码 + HTTP 状态 + 可选 data（如 invalid_kp_ids）。"""

    def __init__(self, code: int, msg: str, http_status: int = 400, data: Any = None):
        self.code = code
        self.msg = msg
        self.http_status = http_status
        self.data = data
        super().__init__(msg)


class ApiResponse(BaseModel):
    code: int = ErrorCode.OK
    msg: str = "ok"
    data: Optional[Any] = None
    trace_id: Optional[str] = None
