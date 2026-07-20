"""粗召回业务层：给定学生 + 知识点范围，召回所有 p∈[0.30,0.80] 的候选题目。

纯召回：只按 ZPD 过滤候选题，不做已做题去重/排除（那是排序/重排层的职责）。

复用：
  - reg.resolve          kp_id → idx
  - forget.get_decayed  查衰减后掌握度
  - learning.level_of    m → 方向
  - question_bank.get_questions_by_kp  KP → 题列表（倒排索引）
  - question_bank.DIFF_MAP / _G_MAP    难度/猜测率映射
不引入新数据源，全是已有能力的编排。
"""
import math

from ..core.response import BizError, ErrorCode
from .forget import get_decayed
from .learning import level_of
from .question_bank import get_questions_by_kp, DIFF_MAP, _G_MAP

K = 5.0        # IRT 区分度（与掌握度标定同源）
P_MIN = 0.30   # ZPD 下限
P_MAX = 0.80   # ZPD 上限


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def recall(student_id: int, kp_ids: list[str], reg, now=None) -> tuple[list, list]:
    """粗召回：返回每个 KP 的候选题目（p∈[0.30,0.80]，不去重、不排除已做题）。

    已做题的去重/排除由下游排序层负责（那里能拿到 m、days_since 等更多上下文）。

    返回 (groups: list, empty_kps: list)；kp_id 无法解析时抛 BizError(KP_NOT_FOUND)。
      groups: [{kp_id, kp_name, m, has_data, direction, question_count, questions[...]}]
      empty_kps: [{kp_id, kp_name, reason}]
    """
    # ① kp_id → idx
    idxs, invalid_kp_ids = reg.resolve(kp_ids)
    if invalid_kp_ids:
        raise BizError(ErrorCode.KP_NOT_FOUND, "部分 kp_id 不存在",
                       http_status=404, data={"invalid_kp_ids": invalid_kp_ids})

    # ② 一次批量查所有 KP 的衰减后掌握度
    decayed_list = get_decayed(student_id, idxs, now)
    # decayed_list 按 idxs 顺序一一对应
    idx_decayed = {idx: d for idx, d in zip(idxs, decayed_list)}

    groups = []
    empty_kps = []
    for kp_id in kp_ids:
        if kp_id in invalid_kp_ids:
            continue
        idx = reg.kp_id_to_idx(kp_id)
        d = idx_decayed[idx]
        m = d["m"]
        has_data = d["has_data"]
        direction = level_of(m, has_data)
        kp_name = d.get("kp_name", "")

        # ③ 倒排索引查该 KP 的所有题
        all_qs = get_questions_by_kp(kp_id)
        candidates = []
        for q in all_qs:
            diff = q.get("difficulty") or "适中"
            dd = DIFF_MAP.get(diff, 0.55)
            g = _G_MAP.get(q.get("ques_type") or "", 0.0)
            p = g + (1.0 - g) * _sigmoid(K * (m - dd))
            if P_MIN <= p <= P_MAX:
                candidates.append({
                    "question_id": q["question_id"],
                    "ques_type": q["ques_type"],
                    "difficulty": diff,
                    "d": dd,
                    "p": round(float(p), 4),
                    "kp_ids": q["kp_ids"],
                })

        # ④ 按 p 降序（最可能做对的在前）
        candidates.sort(key=lambda x: x["p"], reverse=True)

        if candidates:
            groups.append({
                "kp_id": kp_id,
                "kp_name": kp_name,
                "m": m,
                "has_data": has_data,
                "direction": direction,
                "question_count": len(candidates),
                "questions": candidates,
            })
        else:
            empty_kps.append({
                "kp_id": kp_id,
                "kp_name": kp_name,
                "reason": f"题库中该知识点无 p∈[{P_MIN},{P_MAX}] 的题",
            })

    return groups, empty_kps
