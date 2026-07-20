"""答题更新接口测试（POST /students/{id}/practice-events）。

自包含：临时学生 + 临时题目 → 答对/答错 → 验证 before/after 变化 → 清理。
"""
import json

import pytest
from fastapi.testclient import TestClient

import mastery_store as ms
from service.main import app

TEMP_SID = 99000004


@pytest.fixture(scope="module")
def real_question(require_pg):
    """从题库取一道有标签的真实题。"""
    with open("data/初中_九年级.jsonl", encoding="utf-8") as f:
        for line in f:
            q = json.loads(line)
            if q.get("kgPoints") and q.get("difficulty") and q.get("quesType"):
                return q["_id"]
    pytest.skip("题库里没有合适的题")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with ms.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM practice_event WHERE student_id=%s", (TEMP_SID,))
        cur.execute("DELETE FROM student_mastery WHERE student_id=%s", (TEMP_SID,))
        c.commit()


def test_correct_answer_raises_mastery(client, real_question):
    """答对 → 掌握度上升（delta > 0）。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 1.0, "source": "homework",
    })
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["is_wrong"] is False
    for kp in d["updated_kps"]:
        assert kp["delta"] > 0
        assert kp["m_after"] > kp["m_before"]


def test_wrong_answer_lowers_mastery(client, real_question):
    """答错 → 掌握度下降（delta < 0）。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 0.0, "source": "homework",
    })
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["is_wrong"] is True
    for kp in d["updated_kps"]:
        assert kp["delta"] < 0


def test_question_not_found(client):
    """不存在的题 → 404 / 40404。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": "FAKE_QID", "score": 1.0,
    })
    assert r.status_code == 404
    assert r.json()["code"] == 40404


def test_practice_event_written(client, real_question):
    """答题后 practice_event 里有记录。"""
    client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 1.0, "source": "homework",
    })
    events = ms.get_student_events(TEMP_SID, days=1, limit=5)
    assert len(events) >= 1
    assert events[0]["question_id"] == real_question
    assert events[0]["score"] == 1.0


def test_explicit_is_wrong_for_partial_score(client, real_question):
    """主观题部分得分（score=0.6）显式传 is_wrong=True → 应记为错题，不走 score<0.5 默认。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 0.6,
        "is_wrong": True, "source": "homework",
    })
    assert r.status_code == 201
    with ms.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT is_wrong FROM practice_event WHERE student_id=%s AND question_id=%s "
            "ORDER BY ts DESC LIMIT 1",
            (TEMP_SID, real_question),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is True   # 显式传的 is_wrong 生效，而非 score<0.5 兜底的 False


def test_client_request_id_is_idempotent(client, real_question):
    """相同 client_request_id 重复提交 → 第二次返回 idempotent_replay，只写一行、不重复更新掌握度。"""
    rid = "test-req-id-001"
    body = {"question_id": real_question, "score": 1.0,
            "source": "homework", "client_request_id": rid}
    url = f"/api/v1/students/{TEMP_SID}/practice-events"

    r1 = client.post(url, json=body)
    assert r1.status_code == 201
    d1 = r1.json()["data"]
    assert "idempotent_replay" not in d1      # 第一次正常处理
    assert "updated_kps" in d1

    r2 = client.post(url, json=body)
    assert r2.status_code == 201
    d2 = r2.json()["data"]
    assert d2.get("idempotent_replay") is True   # 第二次命中幂等
    assert "updated_kps" not in d2               # 没有重复走更新流程

    # 该 client_request_id 只写了一行（第二次没追加）
    with ms.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM practice_event WHERE student_id=%s AND client_request_id=%s",
            (TEMP_SID, rid),
        )
        assert cur.fetchone()[0] == 1


def test_invalid_source_returns_422(client, real_question):
    """非法的 source → Pydantic Literal 校验 422。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 1.0, "source": "invalid_source",
    })
    assert r.status_code == 422


def test_ts_too_old_returns_400(client, real_question):
    """ts 超过 30 天前 → BizError 400。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 1.0, "source": "homework",
        "ts": "2020-01-01T00:00:00",
    })
    assert r.status_code == 400
    assert r.json()["code"] == 40001


def test_ts_in_future_returns_400(client, real_question):
    """ts 在未来 → BizError 400。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 1.0, "source": "homework",
        "ts": "2099-01-01T00:00:00",
    })
    assert r.status_code == 400


def test_default_ts_and_equal_weights(client, real_question):
    """不传 ts（默认 now）+ equal_weights=True 应正常工作。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 1.0, "source": "homework",
        "equal_weights": True,
    })
    assert r.status_code == 201
    assert len(r.json()["data"]["updated_kps"]) > 0


def test_minimal_request_works(client, real_question):
    """只传必填字段（question_id + score）应正常工作。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 0.8,
    })
    assert r.status_code == 201
    assert r.json()["data"]["is_wrong"] is False   # 0.8 ≥ 0.5


def test_score_point_five_is_not_wrong_by_default(client, real_question):
    """score=0.5 边界：is_wrong 默认用 score<0.5 → 0.5 不算错。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 0.5, "source": "homework",
    })
    assert r.status_code == 201
    assert r.json()["data"]["is_wrong"] is False   # score<0.5 → False（0.5 刚好过线）


def test_score_point_fourty_nine_is_wrong_by_default(client, real_question):
    """score=0.49 边界：刚好低于 0.5，默认判错。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "score": 0.49, "source": "homework",
    })
    assert r.status_code == 201
    assert r.json()["data"]["is_wrong"] is True    # score<0.5 → True


def test_missing_score_returns_422(client, real_question):
    """不传必填字段 score → Pydantic 校验 422。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/practice-events", json={
        "question_id": real_question, "source": "homework",
    })
    assert r.status_code == 422
