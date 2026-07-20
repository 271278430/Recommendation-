"""学情图谱边（include_graph）测试。

边的正确性：范围过滤、类型过滤、共现权重过滤、端点都是范围内 kp_id。
"""
import pytest
from fastapi.testclient import TestClient

from service.main import app

SID = 9060601
MODULE = "四、函数及其图象"   # 81 个 KP，有先修+共现边


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _graph(client, **extra):
    params = {"scope_type": "module", "scope_value": MODULE,
              "history_days": 1, "include_graph": True}
    params.update(extra)
    return client.get(f"/api/v1/students/{SID}/learning-status", params=params).json()["data"]


def test_edges_endpoints_within_scope(client):
    """边两端必须都是范围内的 kp_id。"""
    d = _graph(client)
    node_ids = {it["kp_id"] for it in d["mastery"]["items"] if it.get("kp_id")}
    edges = d["graph"]["edges"]
    assert len(edges) > 0
    for e in edges:
        assert e["from"] in node_ids and e["to"] in node_ids


def test_edge_type_filter(client):
    """edge_types 只取 prereq → 全是 prereq。"""
    d = _graph(client, edge_types=["prereq"])
    assert all(e["type"] == "prereq" for e in d["graph"]["edges"])


def test_cooc_weight_filter(client):
    """cooc_min_weight 提高 → 共现边变少，且权重都 ≥ 阈值。"""
    low = _graph(client, edge_types=["cooc"], cooc_min_weight=0.25)["graph"]["edges"]
    high = _graph(client, edge_types=["cooc"], cooc_min_weight=0.5)["graph"]["edges"]
    assert len(high) <= len(low)
    assert all(e["weight"] >= 0.5 for e in high)


def test_no_graph_by_default(client):
    """include_graph 默认 false → 响应里没有 graph 字段。"""
    r = client.get(f"/api/v1/students/{SID}/learning-status",
                   params={"scope_type": "module", "scope_value": MODULE, "history_days": 1})
    assert "graph" not in r.json()["data"]
