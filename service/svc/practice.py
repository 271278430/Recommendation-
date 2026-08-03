"""做题历史 + 答题更新业务层。

- kp_sequence: 查做题序列（只读）
- submit_answer: 答题后更新掌握度 + 写做题记录 + 返回 before/after 变化
"""
from ..core.kp_registry import KP_NAMES
from ..core.repository import get_kp_sequence, find_event_by_request_id, log_practice_event
from ..core.response import BizError, ErrorCode
from .forget import get_decayed, now_utc_naive
from .learning import level_of
from .mastery import process_answer
from .question_bank import get_question_meta, get_question_weights, DIFF_MAP, _G_MAP


def kp_sequence(student_id: int, kp_id: str, limit: int = 50, wrong_only: bool = False) -> list:
    """某学生在某知识点上的做题序列（按时间倒序）。"""
    events = get_kp_sequence(student_id, kp_id, limit, wrong_only)
    return [{
        "question_id": e["question_id"],
        "ts": e["ts"].isoformat() if e["ts"] is not None else None,
        "ques_type": e["ques_type"],
        "difficulty": e["difficulty"],
        "score": float(e["score"]),
        "is_wrong": e["is_wrong"],
        "kp_ids": e["kp_ids"],
    } for e in events]


def submit_answer(student_id: int, question_id: str, score: float, reg, source: str | None = None, ts=None,
                  equal_weights: bool = False, is_wrong: bool | None = None, client_request_id: str | None = None) -> dict:
    """答题后更新掌握度 + 写做题记录，返回受影响知识点的 before→after + 等级变化。

    - is_wrong：是否判错。None（默认）→ 按 score<0.5 兜底；主观题建议调用方显式传。
    - client_request_id：幂等键。传入且已处理过 → 直接返回，不重复更新掌握度/写记录。
    权重三级兼容：equal_weights=True 强制等权 → 否则用 question_kp_weights 文件 → 兜底等权。
    """
    now = ts or now_utc_naive()
    is_wrong = (score < 0.5) if is_wrong is None else is_wrong

    # ⓪ 幂等：相同 client_request_id 已处理过则直接返回，不重复更新/写记录
    if client_request_id:
        existed = find_event_by_request_id(student_id, client_request_id)
        if existed is not None:
            return {
                "student_id": student_id,
                "question_id": existed["question_id"],
                "idempotent_replay": True,
                "message": "该 client_request_id 已处理过，未重复更新掌握度",
            }

    # ① 查题目属性
    qmeta = get_question_meta(question_id)
    if not qmeta:
        raise BizError(ErrorCode.QUESTION_NOT_FOUND, "题目不存在",
                       http_status=404, data={"question_id": question_id})
    kp_ids = qmeta["kp_ids"]
    if not kp_ids:
        raise BizError(ErrorCode.QUESTION_NO_KP, "题目没有知识点标签，无法更新掌握度",
                       http_status=400, data={"question_id": question_id})

    ques_type = qmeta.get("ques_type")
    difficulty = qmeta.get("difficulty")
    d = DIFF_MAP.get(difficulty, 0.55)
    g = _G_MAP.get(ques_type, 0.0)

    # ② kp_id → idx
    idxs, invalid = reg.resolve(kp_ids)
    if not idxs:
        raise BizError(ErrorCode.BAD_REQUEST, "题目的知识点无法解析",
                       http_status=400, data={"question_id": question_id})
    # 权重三级兼容：手动等权 → 题库权重文件 → 等权兜底
    if equal_weights:
        weights = [1.0 / len(idxs)] * len(idxs)
    else:
        qp_w = get_question_weights(question_id)
        if qp_w:
            kp_names = [KP_NAMES[idx] for idx in idxs]
            w_raw = [qp_w.get(nm, 0) for nm in kp_names]
            s = sum(w_raw)
            weights = [w / s for w in w_raw] if s > 0 else [1.0 / len(idxs)] * len(idxs)
        else:
            weights = [1.0 / len(idxs)] * len(idxs)

    # ③ m_before（衰减后，= 学生当前看到的掌握度）
    before = get_decayed(student_id, idxs, now)

    # ④ process_answer（7步掌握度更新流水线）
    process_answer(student_id, idxs, weights, y=score, d=d, g=g, k=5, t=now)

    # ⑤ 写做题记录
    log_practice_event(student_id, question_id, kp_ids, score, is_wrong,
                       ques_type=ques_type, difficulty=difficulty, d=d,
                       source=source, ts=now, client_request_id=client_request_id)

    # ⑥ m_after（last_ts 已更新到 now，Δt=0 不衰减，= 存储值）
    after = get_decayed(student_id, idxs, now)

    # ⑦ 组装变化
    updated = []
    for i, idx in enumerate(idxs):
        m_b = before[i]["m"]
        m_a = after[i]["m"]
        hd_b = before[i]["has_data"]
        hd_a = after[i]["has_data"]
        updated.append({
            "kp_id": reg.idx_to_kp_id(idx),
            "kp_name": KP_NAMES[idx],
            "m_before": m_b,
            "m_after": m_a,
            "delta": round(m_a - m_b, 4),
            "level_before": level_of(m_b, hd_b),
            "level_after": level_of(m_a, hd_a),
        })

    return {
        "student_id": student_id,
        "question_id": question_id,
        "score": score,
        "is_wrong": is_wrong,
        "question": {"ques_type": ques_type, "difficulty": difficulty, "kp_ids": kp_ids},
        "updated_kps": updated,
    }
