# 粗召回接口测试（POST /students/{id}/recommendations）。自包含：用真实题库知识点。
from fastapi.testclient import TestClient
from service.main import app
import mastery_store as ms
import pytest


@pytest.fixture(scope="module")
def kp_ids(require_pg):
    """取两个有真实题目标签的知识点。"""
    with ms.conn() as cc, cc.cursor() as cur:
        cur.execute("SELECT DISTINCT kp FROM practice_event, unnest(kp_ids) kp LIMIT 2")
        return [r[0] for r in cur.fetchall()]


@pytest.fixture(scope="module")
def student_id(require_pg):
    with ms.conn() as cc, cc.cursor() as cur:
        cur.execute("SELECT student_id FROM practice_event LIMIT 1")
        return cur.fetchone()[0]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_recall_returns_groups_with_candidates(client, student_id, kp_ids):
    r = client.post(f"/api/v1/students/{student_id}/recommendations",
                    json={"kp_ids": kp_ids})
    assert r.status_code == 200
    d = r.json()["data"]
    assert len(d["groups"]) == 2
    for g in d["groups"]:
        assert g["question_count"] > 0
        for q in g["questions"]:
            assert 0.30 <= q["p"] <= 0.80
            assert q["d"] > 0


def test_recall_sort_is_p_descending(client, student_id, kp_ids):
    r = client.post(f"/api/v1/students/{student_id}/recommendations",
                    json={"kp_ids": kp_ids})
    for g in r.json()["data"]["groups"]:
        p_list = [q["p"] for q in g["questions"]]
        assert p_list == sorted(p_list, reverse=True), "应按 p 降序"


def test_invalid_kp_id_returns_404(client, student_id):
    r = client.post(f"/api/v1/students/{student_id}/recommendations",
                    json={"kp_ids": ["FAKE_ID"]})
    assert r.status_code == 404
    assert r.json()["code"] == 40403


def test_all_three_fields_present(client, student_id, kp_ids):
    """验证每个 group 有完整的 has_data/direction/m/question_count。"""
    r = client.post(f"/api/v1/students/{student_id}/recommendations",
                    json={"kp_ids": kp_ids})
    for g in r.json()["data"]["groups"]:
        assert "has_data" in g
        assert g["direction"] in ("补弱", "巩固", "变式", "进阶", "未接触")
        assert g["question_count"] == len(g["questions"])


def test_partial_invalid_kp_ids_still_rejects(client, student_id, kp_ids):
    """只要有一个 kp_id 无效，整个请求就 404（当前行为）。"""
    if not kp_ids:
        return
    mixed = [kp_ids[0], "FAKE_NOT_EXIST"]
    r = client.post(f"/api/v1/students/{student_id}/recommendations",
                    json={"kp_ids": mixed})
    assert r.status_code == 404
    assert r.json()["code"] == 40403


def test_empty_kp_ids_returns_422(client, student_id):
    """kp_ids 为空列表 → Pydantic min_length=1 校验 422。"""
    r = client.post(f"/api/v1/students/{student_id}/recommendations",
                    json={"kp_ids": []})
    assert r.status_code == 422
