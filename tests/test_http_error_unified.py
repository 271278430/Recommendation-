"""HTTPException 统一走 ApiResponse 的测试。

验证 404 路由不存在、405 方法不允许等框架级 HTTP 异常也返回统一结构
{code, msg, data, trace_id}，而不是 Starlette 默认的 {"detail":...}。
这些用例在路由匹配 / 方法校验阶段就被拒绝，不触达 DB，因此不需要 require_pg。
"""
import pytest
from fastapi.testclient import TestClient

from service.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _assert_unified(r, status, code, msg):
    """断言响应是统一 ApiResponse，且 trace_id 与响应头一致。"""
    assert r.status_code == status
    body = r.json()
    assert set(body) >= {"code", "msg", "data", "trace_id"}, body
    assert body["code"] == code
    assert body["msg"] == msg
    assert isinstance(body["trace_id"], str) and body["trace_id"]
    assert r.headers.get("X-Trace-Id") == body["trace_id"]


def test_unknown_route_returns_unified_404(client):
    """不存在的路由 → 404 + ApiResponse（code=40400），而非 {"detail":"Not Found"}。"""
    r = client.get("/api/v1/this-route-does-not-exist")
    _assert_unified(r, 404, 40400, "接口不存在")


def test_method_not_allowed_returns_unified_405(client):
    """/students 仅注册 GET，用 DELETE → 405 + ApiResponse（code=40501）。"""
    r = client.delete("/api/v1/students")
    _assert_unified(r, 405, 40501, "方法不允许")
