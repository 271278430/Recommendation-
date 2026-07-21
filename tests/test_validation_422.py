"""422 校验失败统一走 ApiResponse 的测试。

验证所有 Pydantic 请求校验失败都返回统一结构 {code, msg, data, trace_id}，
而不是 FastAPI 默认的 {"detail":[...]}。这些用例在路由函数执行前就被框架拒绝，不触达 DB，
因此不需要 require_pg。
"""
import pytest
from fastapi.testclient import TestClient

from service.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


SID = 99000422


def _assert_unified_422(r):
    """断言响应是统一 ApiResponse 的 422 形态，且 trace_id 与响应头一致。"""
    assert r.status_code == 422
    body = r.json()
    assert set(body) >= {"code", "msg", "data", "trace_id"}, body
    assert body["code"] == 40001
    assert body["msg"] == "请求参数校验失败"
    assert isinstance(body["data"], dict)
    assert isinstance(body["data"]["errors"], list) and body["data"]["errors"]
    assert isinstance(body["trace_id"], str) and body["trace_id"]
    assert r.headers.get("X-Trace-Id") == body["trace_id"]


# ---------------------------------------------------------------------------
# POST /students/{id}/mastery —— model_validator 抛 ValueError → 422
# ---------------------------------------------------------------------------
def test_mastery_negative_count_422_unified(client):
    r = client.post(f"/api/v1/students/{SID}/mastery",
                    json={"correct_counts": {"J0300010001000100010006": -1}})
    _assert_unified_422(r)


def test_mastery_bad_ts_422_unified(client):
    r = client.post(f"/api/v1/students/{SID}/mastery",
                    json={"last_ts": {"J0300010001000100010006": "not-a-date"}})
    _assert_unified_422(r)


# ---------------------------------------------------------------------------
# POST /students/{id}/practice-events —— Field(ge/le) / Literal → 422
# ---------------------------------------------------------------------------
def test_submit_score_out_of_range_422_unified(client):
    r = client.post(f"/api/v1/students/{SID}/practice-events",
                    json={"question_id": "q1", "score": 1.5})
    _assert_unified_422(r)


def test_submit_bad_source_enum_422_unified(client):
    r = client.post(f"/api/v1/students/{SID}/practice-events",
                    json={"question_id": "q1", "score": 0.0, "source": "garbage"})
    _assert_unified_422(r)


# ---------------------------------------------------------------------------
# POST /students/{id}/recommendations —— min_length=1 / 必填 → 422
# ---------------------------------------------------------------------------
def test_recommend_empty_kp_ids_422_unified(client):
    r = client.post(f"/api/v1/students/{SID}/recommendations", json={"kp_ids": []})
    _assert_unified_422(r)


def test_recommend_missing_kp_ids_422_unified(client):
    r = client.post(f"/api/v1/students/{SID}/recommendations", json={})
    _assert_unified_422(r)
