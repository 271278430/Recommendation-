"""掌握度初始化接口 HTTP 测试（POST /students/{id}/mastery）。

自包含：用临时学生测完即删，不依赖真实学生数据。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from service.core.db import conn
from service.main import app

TEMP_SID = 99000002                      # 临时学生（测完即删）
KP_ID_A = "J0300010001000100010006"      # kp_idx=0, 正数和负数
KP_ID_B = "J0300010001000100010007"      # kp_idx=1（如果存在的话）


def _cleanup(sid=TEMP_SID):
    with conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM student_mastery WHERE student_id=%s", (sid,))
        cur.execute("DELETE FROM mastery_event WHERE student_id=%s", (sid,))


@pytest.fixture(autouse=True)
def cleanup(require_pg):
    """每个测试前后自动清理临时学生数据。"""
    _cleanup()
    yield
    _cleanup()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 正常流程
# ---------------------------------------------------------------------------
def test_init_cold_start_returns_all_items(client):
    """空请求体 → 冷启动，返回全量知识点，m=0.5。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/mastery", json={})
    assert r.status_code == 201
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["student_id"] == TEMP_SID
    assert len(body["data"]["items"]) > 0

    # 冷启动：所有知识点 m=0.5, has_data=False, N=2P=4
    for it in body["data"]["items"]:
        assert it["m"] == 0.5
        assert it["has_data"] is False
        assert 3.0 < it["N"] < 5.0  # N = alpha+beta = P+P = 4


def test_init_with_counts_sets_mastery_correctly(client):
    """传入正确/错误次数 → m 反映公式 m = (C_correct+P)/(C_correct+P + C_wrong+P)。"""
    correct = {KP_ID_A: 5}
    wrong = {KP_ID_A: 2}
    # m = (5+2)/(5+2+2+2) ≈ 0.6364
    expected_m = round(7 / 11, 4)

    r = client.post(f"/api/v1/students/{TEMP_SID}/mastery",
                     json={"correct_counts": correct, "wrong_counts": wrong})
    assert r.status_code == 201
    items = r.json()["data"]["items"]
    assert len(items) == 1
    it = items[0]
    assert it["kp_id"] == KP_ID_A
    assert it["m"] == expected_m
    # has_data 必须同时有 last_ts 和 N>2P；只注入先验不给时间戳 → False
    assert it["has_data"] is False
    assert it["N"] == round(5 + 2 + 4, 2)  # N = 5+2+2P = 11

    # 通过 GET 交叉验证：值一致
    r2 = client.get(f"/api/v1/students/{TEMP_SID}/mastery",
                     params={"kp_ids": [KP_ID_A]})
    assert r2.json()["data"]["items"][0]["m"] == expected_m


def test_init_only_correct(client):
    """只传正确次数，无错题 → m 接近 1。"""
    correct = {KP_ID_A: 10}
    # m = (10+2)/(10+2+0+2) = 12/14 ≈ 0.8571
    expected_m = round(12 / 14, 4)

    r = client.post(f"/api/v1/students/{TEMP_SID}/mastery",
                     json={"correct_counts": correct})
    assert r.status_code == 201
    it = r.json()["data"]["items"][0]
    assert it["m"] == expected_m
    assert it["kp_id"] == KP_ID_A


def test_init_with_timestamp_stores_last_ts(client):
    """传入 last_ts → 数据库中存储的是【传入的时间值】本身（不是 now）。"""
    ts = "2026-07-15T10:00:00Z"

    r = client.post(f"/api/v1/students/{TEMP_SID}/mastery",
                     json={"correct_counts": {KP_ID_A: 5}, "last_ts": {KP_ID_A: ts}})
    assert r.status_code == 201
    it = r.json()["data"]["items"][0]
    # 关键：验证存的就是 2026-07-15T10:00:00，不是被 now 覆盖
    assert it["last_ts"].startswith("2026-07-15T10:00:00")
    assert it["has_data"] is True   # 有 last_ts + N>2P
    # 可以用 GET 交叉验证
    r2 = client.get(f"/api/v1/students/{TEMP_SID}/mastery",
                     params={"kp_ids": [KP_ID_A]})
    it2 = r2.json()["data"]["items"][0]
    assert it2["last_ts"].startswith("2026-07-15T10:00:00")
    assert it2["has_data"] is True


def test_init_multiple_kps_no_cross_contamination(client):
    """初始化多个知识点：给不同 KP 注入【不同】的正确次数，验证每个 KP 的 m 等于它
    自己的公式，值不会串到对方的槽位（防止 correct_arr[idx] 写错下标）。"""
    r_all = client.get("/api/v1/knowledge-points")
    kp_ids = [kp["kp_id"] for kp in r_all.json()["data"][:2]]  # 取前两个
    # 故意不同：kp_ids[0]→5, kp_ids[1]→1
    counts = {kp_ids[0]: 5, kp_ids[1]: 1}

    r = client.post(f"/api/v1/students/{TEMP_SID}/mastery",
                     json={"correct_counts": counts})
    assert r.status_code == 201
    items = {it["kp_id"]: it for it in r.json()["data"]["items"]}
    assert len(items) == 2

    # 每个 KP 的 m 必须等于它自己的 (c+P)/(c+P+0+P)，不能因为串槽而相等
    for kid, c in counts.items():
        expected_m = round((c + 2) / (c + 2 + 0 + 2), 4)
        assert items[kid]["m"] == expected_m, f"{kid} 的 m 串了: {items[kid]['m']} != {expected_m}"
    # 两个 KP 的 m 应该不同（因为 count 不同）
    assert items[kp_ids[0]]["m"] != items[kp_ids[1]]["m"]


def test_init_overwrites_existing_data(client):
    """对已有数据的学生再次调用 → 覆盖旧数据。"""
    # 第一次：只有正确 10，m 高
    client.post(f"/api/v1/students/{TEMP_SID}/mastery",
                json={"correct_counts": {KP_ID_A: 10}})
    r1 = client.get(f"/api/v1/students/{TEMP_SID}/mastery",
                     params={"kp_ids": [KP_ID_A]})
    m1 = r1.json()["data"]["items"][0]["m"]
    assert m1 > 0.8  # 高掌握度

    # 第二次：冷启动重置
    client.post(f"/api/v1/students/{TEMP_SID}/mastery", json={})
    r2 = client.get(f"/api/v1/students/{TEMP_SID}/mastery",
                     params={"kp_ids": [KP_ID_A]})
    m2 = r2.json()["data"]["items"][0]["m"]
    assert m2 == 0.5  # 被重置回先验


# ---------------------------------------------------------------------------
# 异常情况
# ---------------------------------------------------------------------------
def test_invalid_kp_id_returns_40403(client):
    """无效 kp_id → 40403。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/mastery",
                     json={"correct_counts": {"FAKE_ID_ZZZ": 5}})
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == 40403
    assert body["data"]["invalid_kp_ids"] == ["FAKE_ID_ZZZ"]


def test_negative_correct_count_returns_422(client):
    """负数 correct_count → Pydantic 校验拒绝 422。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/mastery",
                     json={"correct_counts": {KP_ID_A: -1}})
    assert r.status_code == 422


def test_negative_wrong_count_returns_422(client):
    """负数 wrong_count → 422。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/mastery",
                     json={"wrong_counts": {KP_ID_A: -3}})
    assert r.status_code == 422


def test_invalid_ts_format_returns_422(client):
    """非 ISO8601 时间串 → 422。"""
    r = client.post(f"/api/v1/students/{TEMP_SID}/mastery",
                     json={"last_ts": {KP_ID_A: "not-a-date"}})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 幂等性：两次相同调用 → 结果一致
# ---------------------------------------------------------------------------
def test_init_idempotent(client):
    """两次相同的初始化 → 得到相同的掌握度值。"""
    body = {"correct_counts": {KP_ID_A: 5}, "wrong_counts": {KP_ID_A: 2}}
    r1 = client.post(f"/api/v1/students/{TEMP_SID}/mastery", json=body)
    m1 = r1.json()["data"]["items"][0]["m"]

    r2 = client.post(f"/api/v1/students/{TEMP_SID}/mastery", json=body)
    m2 = r2.json()["data"]["items"][0]["m"]

    assert m1 == m2
