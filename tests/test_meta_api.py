"""GET 元数据接口测试：验证三个 GET 走统一 ApiResponse 封装（{code,msg,data,trace_id}）。"""
import pytest
from fastapi.testclient import TestClient

from service.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _body(r):
    """断言统一封装结构，返回 data。"""
    assert r.status_code == 200
    b = r.json()
    assert b["code"] == 0
    assert b["trace_id"]            # body 里有 trace_id
    return b["data"]


def test_students_wrapped(client, require_pg):
    data = _body(client.get("/api/v1/students"))
    assert isinstance(data, list)
    assert len(data) > 0   # 防止接口静默返回空列表（真实库有 690 学生）


def test_knowledge_points_wrapped(client):
    data = _body(client.get("/api/v1/knowledge-points"))
    assert isinstance(data, list)
    assert len(data) > 0
    assert {"kp_id", "kp_name", "module"} <= set(data[0])


def test_graph_edges_wrapped(client):
    data = _body(client.get("/api/v1/knowledge-graph/edges"))
    assert isinstance(data, list)
    assert len(data) > 0   # 防止接口静默返回空列表（真实库有 457+ 边）


def test_trace_id_in_header(client):
    """GET 接口的 X-Trace-Id 响应头（与 POST 一致）。"""
    r = client.get("/api/v1/knowledge-points")
    assert r.headers.get("X-Trace-Id")


def test_graph_edges_structure(client):
    """knowledge-graph/edges 返回边列表，每条有 from/to/type，type 为 prereq/cooc。"""
    r = client.get("/api/v1/knowledge-graph/edges")
    assert r.status_code == 200
    edges = r.json()["data"]
    assert isinstance(edges, list)
    valid_types = {"prereq", "cooc"}
    for e in edges[:20]:  # 采样检查
        assert e["from"] and e["to"] and e["type"]
        assert e["type"] in valid_types
