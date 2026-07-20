"""遗忘机制 HTTP 端到端测试（GET /students/{id}/mastery）。

自包含：用临时学生造一条 7 天前的练习记录，测完清理，不依赖任何真实学生数据。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import mastery_store as ms
from service.main import app

TEMP_SID = 99000001                      # 临时学生（测完即删）
KP_IDX = 0                              # 正数和负数
KP_ID = "J0300010001000100010006"


@pytest.fixture(scope="module")
def student_with_history(require_pg):
    """造一个临时学生：7 天前在 KP0 上答对一题，产生练习历史。测完删除。"""
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    ms.process_answer(TEMP_SID, [KP_IDX], [1.0], y=1.0, d=0.5, g=0.0, t=past)
    yield TEMP_SID
    with ms.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM student_mastery WHERE student_id=%s", (TEMP_SID,))


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_forget_returns_decayed_mastery(client, student_with_history):
    r = client.get(f"/api/v1/students/{student_with_history}/mastery",
                   params={"kp_ids": [KP_ID]})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["trace_id"]
    it = body["data"]["items"][0]
    assert it["kp_id"] == KP_ID
    assert it["has_data"] is True
    assert 0 < it["m"] < 1
    assert 6.5 < it["days_since"] < 7.5      # 约 7 天


def test_invalid_kp_id_returns_40403(client, student_with_history):
    r = client.get(f"/api/v1/students/{student_with_history}/mastery",
                   params={"kp_ids": ["FAKE_ID"]})
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == 40403
    assert body["data"]["invalid_kp_ids"] == ["FAKE_ID"]


def test_no_kp_ids_returns_all(client, student_with_history):
    r = client.get(f"/api/v1/students/{student_with_history}/mastery")
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    # 全量返回所有有规范 kp_id 映射的知识点；不再出现 kp_id:null（无题库映射的 idx 已过滤）
    assert len(items) > 0
    assert all(it["kp_id"] is not None for it in items)


def test_trace_id_in_response_header(client, student_with_history):
    r = client.get(f"/api/v1/students/{student_with_history}/mastery",
                   params={"kp_ids": [KP_ID]})
    assert r.headers.get("X-Trace-Id")


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_cold_start_student_returns_prior_m(client):
    """新学生（无任何做题记录）→ 返回 m=0.5, has_data=false（冷启动默认）。"""
    r = client.get("/api/v1/students/99999998/mastery")
    assert r.status_code == 200
    it = r.json()["data"]["items"][0]
    assert it["m"] == 0.5
    assert it["has_data"] is False
    assert 3.0 < it["N"] < 5.0   # 冷启动 N = alpha+beta = P+P = 4
