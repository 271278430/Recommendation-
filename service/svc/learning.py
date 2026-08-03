"""学情查询业务层：组装学生学情 = 掌握度概览 + 做题历史 + 指标。

复用：forget.get_decayed（掌握度衰减）、repository（做题统计/事件）。
KP 标识统一用 kp_id；scope（module/kp_ids）经 KPRegistry + nodes.json 解析。
"""
import json
import os

from ..core import repository
from ..core.config import settings
from ..core.response import BizError, ErrorCode
from .forget import get_decayed, now_utc_naive

LEVELS = ["未接触", "补弱", "巩固", "变式", "进阶"]

_nodes = None


def _load_nodes():
    """nodes.json：[{idx, name, module, ...}]，用于 module→kp 解析。"""
    global _nodes
    if _nodes is None:
        with open(os.path.join(settings.project_root, "data", "kg_graph", "nodes.json"), encoding="utf-8") as f:
            _nodes = json.load(f)
    return _nodes


def level_of(m: float, has_data: bool) -> str:
    """掌握度等级（与《接口数据规范》阈值一致）。"""
    if not has_data:
        return "未接触"
    if m < 0.45:
        return "补弱"
    if m < 0.65:
        return "巩固"
    if m < 0.80:
        return "变式"
    return "进阶"


def resolve_scope(scope_type: str, scope_value: str | list[str], reg) -> list[dict]:
    """scope → [{kp_id, kp_idx, kp_name}, ...]。范围无效时抛 BizError(SCOPE_NOT_FOUND)。

    - module：从 nodes.json 取该模块所有知识点
    - kp_ids：经注册表解析，无效的抛异常
    """
    if scope_type == "module":
        matched = [n for n in _load_nodes() if n.get("module") == scope_value]
        if not matched:
            raise BizError(ErrorCode.SCOPE_NOT_FOUND, "范围无效",
                           http_status=404, data={"invalid_scope": [scope_value]})
        return [{"kp_id": reg.idx_to_kp_id(n["idx"]), "kp_idx": n["idx"], "kp_name": n["name"]}
                for n in matched]
    if scope_type == "kp_ids":
        # 兼容单个 kp_id 传字符串：API 层 scope_value 允许 str|list，这里统一成 list
        if isinstance(scope_value, str):
            scope_value = [scope_value]
        idxs, invalid = reg.resolve(scope_value)
        if invalid:
            raise BizError(ErrorCode.SCOPE_NOT_FOUND, "范围无效",
                           http_status=404, data={"invalid_scope": invalid})
        return [{"kp_id": kid, "kp_idx": reg.kp_id_to_idx(kid), "kp_name": reg.kp_id_to_name(kid)}
                for kid in scope_value]
    raise BizError(ErrorCode.SCOPE_NOT_FOUND, "范围无效",
                   http_status=404, data={"invalid_scope": [scope_type]})


def learning_status(student_id: int, scope_type: str, scope_value: str | list[str],
                    aspects: list[str], history_days: int, history_limit: int, mastery_detail: bool, reg, now=None,
                    include_graph: bool = False, edge_types=("prereq", "cooc"), cooc_min_weight: float = 0.25) -> dict:
    """组装学情。返回 result_dict；范围无效时由 resolve_scope 抛 BizError。

    include_graph=True 时，额外附加范围内的知识点关系边（先修/共现），
    供前端渲染可视化学情图谱。边是静态数据（学生无关），不重复查掌握度。
    """
    if now is None:
        now = now_utc_naive()
    kps = resolve_scope(scope_type, scope_value, reg)
    scope_kp_ids = [k["kp_id"] for k in kps if k["kp_id"]]

    result = {
        "student_id": student_id,
        "now": now.isoformat(),
        "scope": {"type": scope_type, "value": scope_value, "kp_count": len(kps)},
    }
    if "mastery" in aspects:
        result["mastery"] = _mastery_part(student_id, kps, now, mastery_detail, history_days)
    if "history" in aspects:
        result["history"] = _history_part(student_id, scope_kp_ids, history_days, history_limit)
    if include_graph:
        from .knowledge_graph import get_edges
        edges = get_edges([k["kp_idx"] for k in kps], reg.idx2id, edge_types, cooc_min_weight)
        result["graph"] = {"edges": edges}
    return result


def _mastery_part(student_id, kps, now, detail, recent_days):
    if not kps:
        return {"summary": _empty_summary(len(kps)), "items": [] if detail else None}

    decayed = get_decayed(student_id, [k["kp_idx"] for k in kps], now)
    kp_ids = [k["kp_id"] for k in kps if k["kp_id"]]
    stats = repository.get_kp_stats(student_id, kp_ids, recent_days) if kp_ids else {}

    items = []
    for k, d in zip(kps, decayed):
        st = stats.get(k["kp_id"], {})
        items.append({
            "kp_id": k["kp_id"],
            "kp_name": k["kp_name"],
            "m": d["m"],
            "level": level_of(d["m"], d["has_data"]),
            "N": d["N"],
            "has_data": d["has_data"],
            "days_since": d["days_since"],
            "practice_count": st.get("practice_count", 0),
            "recent_wrong_count": st.get("recent_wrong_count", 0),
        })

    has_data_items = [it for it in items if it["has_data"]]
    avg_m = round(sum(it["m"] for it in has_data_items) / len(has_data_items), 4) if has_data_items else None
    level_counts = {lv: 0 for lv in LEVELS}
    for it in items:
        level_counts[it["level"]] += 1
    weakest = sorted(has_data_items, key=lambda x: x["m"])[:5]
    stalest = sorted(has_data_items, key=lambda x: -x["days_since"])[:5]

    return {
        "summary": {
            "total_kp": len(items),
            "has_data_kp": len(has_data_items),
            "avg_m": avg_m,
            "level_counts": level_counts,
            "weakest": [{"kp_id": w["kp_id"], "kp_name": w["kp_name"], "m": w["m"]} for w in weakest],
            "stalest": [{"kp_id": w["kp_id"], "kp_name": w["kp_name"], "days_since": w["days_since"]} for w in stalest],
        },
        "items": items if detail else None,
    }


def _empty_summary(total_kp=0):
    return {
        "total_kp": total_kp,
        "has_data_kp": 0,
        "avg_m": None,
        "level_counts": {lv: 0 for lv in LEVELS},
        "weakest": [],
        "stalest": [],
    }


def _history_part(student_id, scope_kp_ids, days, limit):
    summary = repository.get_history_summary(student_id, scope_kp_ids, days)
    raw = repository.get_student_events(student_id, days=days, kp_ids=scope_kp_ids, limit=limit) if scope_kp_ids else []
    events = [{
        "question_id": e["question_id"],
        "ts": e["ts"].isoformat() if e["ts"] is not None else None,
        "ques_type": e["ques_type"],
        "difficulty": e["difficulty"],
        "score": float(e["score"]),
        "is_wrong": e["is_wrong"],
        "kp_ids": e["kp_ids"],
    } for e in raw]
    return {"summary": summary, "events": events}
