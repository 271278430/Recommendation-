"""结构化日志 + trace_id 上下文。

trace_id 用 contextvars（async 安全），每条日志带 [trace_id]，
便于按请求串日志。中间件（middleware.py）在请求入口生成 trace_id。
"""
import contextvars
import logging
import sys
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


class _TraceFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(trace_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(_TraceFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:12]
    _trace_id.set(tid)
    return tid


def current_trace_id() -> str:
    return _trace_id.get()
