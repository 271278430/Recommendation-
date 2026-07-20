"""学生学情接口 HTTP 测试（GET /students/{id}/learning-status）。

自包含：给临时学生造掌握度（process_answer）+ 做题记录（log_practice_event），测完清理。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import mastery_store as ms
from service.main import app

TEMP_SID = 99000003
KP_NAME = "勾股定理"
KP_ID = "J030004000200080003"
KP_IDX = ms.NAME2IDX[KP_NAME]


@pytest.fixture(scope="module")
def student_with_data(require_pg):
    """临时学生：7 天前答对一题（写掌握度 + 写做题记录）。测完清理两张表。"""
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    ms.process_answer(TEMP_SID, [KP_IDX], [1.0], y=1.0, d=0.5, g=0.0, t=past)
    ms.log_practice_event(TEMP_SID, "test_q_1", [KP_ID], 1.0, False,
                          ques_type="单选题", difficulty="较易", source="test", ts=past)
    ms.log_practice_event(TEMP_SID, "test_q_2", [KP_ID], 0.0, True,
                          ques_type="填空题", difficulty="适中", source="test",
                          ts=past - timedelta(days=1))
    yield TEMP_SID
    with ms.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM practice_event WHERE student_id=%s", (TEMP_SID,))
        cur.execute("DELETE FROM student_mastery WHERE student_id=%s", (TEMP_SID,))
        c.commit()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_learning_status_mastery_and_history(client, student_with_data):
    r = client.get(f"/api/v1/students/{student_with_data}/learning-status",
                   params={"scope_type": "kp_ids", "scope_value": KP_ID, "history_days": 30})
    assert r.status_code == 200
    d = r.json()["data"]
    # mastery
    it = d["mastery"]["items"][0]
    assert it["kp_id"] == KP_ID
    assert it["has_data"] is True
    assert it["level"] in ("补弱", "巩固", "变式", "进阶")
    assert it["practice_count"] == 2
    # history
    assert d["history"]["summary"]["total_answered"] == 2
    assert d["history"]["summary"]["accuracy"] == 0.5
    assert len(d["history"]["events"]) == 2


def test_invalid_scope_returns_404(client, student_with_data):
    r = client.get(f"/api/v1/students/{student_with_data}/learning-status",
                   params={"scope_type": "module", "scope_value": "不存在的模块"})
    assert r.status_code == 404
    assert r.json()["code"] == 40401


def test_mastery_only_aspect(client, student_with_data):
    r = client.get(f"/api/v1/students/{student_with_data}/learning-status",
                   params={"scope_type": "kp_ids", "scope_value": KP_ID, "aspects": "mastery"})
    d = r.json()["data"]
    assert "mastery" in d and "history" not in d


def test_single_kp_id_scope_value(client, student_with_data):
    """scope_type=kp_ids 时传单个 kp_id 也应正常返回。"""
    r = client.get(f"/api/v1/students/{student_with_data}/learning-status",
                   params={"scope_type": "kp_ids", "scope_value": KP_ID, "aspects": "mastery"})
    assert r.status_code == 200
    assert r.json()["data"]["mastery"]["items"][0]["kp_id"] == KP_ID


def test_invalid_scope_type_returns_422(client, student_with_data):
    """非法的 scope_type → Pydantic Literal 校验 422。"""
    r = client.get(f"/api/v1/students/{student_with_data}/learning-status",
                   params={"scope_type": "foo", "scope_value": "bar"})
    assert r.status_code == 422


def test_invalid_aspects_returns_400(client, student_with_data):
    """非法的 aspects → BizError 400。"""
    r = client.get(f"/api/v1/students/{student_with_data}/learning-status",
                   params={"scope_type": "kp_ids", "scope_value": KP_ID, "aspects": "mastrey"})
    assert r.status_code == 400
    assert r.json()["code"] == 40001


def test_module_scope_works(client, student_with_data):
    """scope_type=module 正常返回学情（验证 module 解析通路）。"""
    r = client.get(f"/api/v1/students/{student_with_data}/learning-status",
                   params={"scope_type": "module", "scope_value": "四、函数及其图象",
                           "history_days": 365})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["scope"]["type"] == "module"
    assert d["scope"]["kp_count"] > 0
