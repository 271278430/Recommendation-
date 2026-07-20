"""做题序列接口 HTTP 测试（GET /students/{id}/practice-events）。

自包含：给临时学生造一条做题事件，测完清理。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import mastery_store as ms
from service.main import app

TEMP_SID = 99000002
KP_ID = "J030003000400010006"   # 二次函数的定义（题库真实 kp_id）


@pytest.fixture(scope="module")
def student_with_event(require_pg):
    """给临时学生造两条做题事件（一对一错），测完删除。"""
    now = datetime.now(timezone.utc)
    ms.log_practice_event(TEMP_SID, "test_q_right", [KP_ID], 1.0, False,
                          ques_type="单选题", difficulty="较易", source="test",
                          ts=now - timedelta(days=2))
    ms.log_practice_event(TEMP_SID, "test_q_wrong", [KP_ID], 0.0, True,
                          ques_type="填空题", difficulty="适中", source="test",
                          ts=now - timedelta(days=1))
    yield TEMP_SID
    with ms.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM practice_event WHERE student_id=%s", (TEMP_SID,))


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_kp_sequence_returns_events(client, student_with_event):
    r = client.get(f"/api/v1/students/{student_with_event}/practice-events",
                   params={"kp_id": KP_ID})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["count"] == 2
    assert d["events"][0]["kp_ids"] == [KP_ID]          # 回填了 kp_ids
    assert d["events"][0]["ts"] >= d["events"][1]["ts"]  # 倒序（最近在前）


def test_wrong_only_filters(client, student_with_event):
    r = client.get(f"/api/v1/students/{student_with_event}/practice-events",
                   params={"kp_id": KP_ID, "wrong_only": True})
    d = r.json()["data"]
    assert d["count"] == 1
    assert all(e["is_wrong"] for e in d["events"])


def test_invalid_kp_id_returns_404(client, student_with_event):
    r = client.get(f"/api/v1/students/{student_with_event}/practice-events",
                   params={"kp_id": "FAKE_ID"})
    assert r.status_code == 404
    assert r.json()["data"]["invalid_kp_ids"] == ["FAKE_ID"]


def test_empty_sequence(client):
    """没做过题的学生返回空序列（不报错）。"""
    r = client.get("/api/v1/students/99999999/practice-events",
                   params={"kp_id": KP_ID})
    assert r.status_code == 200
    assert r.json()["data"]["count"] == 0
