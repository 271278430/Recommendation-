"""学生路由（薄）：
- GET /students — 学生 ID 列表
- GET /students/{id}/learning-status — 学情查询（掌握度+做题历史+图谱边）

学情是聚合读视图，参数走 query。
"""
from typing import Literal, Optional

from fastapi import APIRouter, Query, Request

from ..core.repository import list_students as all_student_ids

from ..core.deps import get_kp_registry
from ..core.response import ApiResponse, BizError, ErrorCode
from ..svc import learning

router = APIRouter(prefix="/students", tags=["student"])

_VALID_ASPECTS = {"mastery", "history"}


@router.get("", response_model=ApiResponse, summary="学生 ID 列表")
def list_students(request: Request):
    tid = getattr(request.state, "trace_id", None)
    ids = all_student_ids()
    return ApiResponse(data=ids, trace_id=tid)


@router.get("/{student_id}/learning-status", response_model=ApiResponse,
            summary="学生学情（掌握度+做题历史+图谱边）")
def get_learning_status(
    student_id: int,
    request: Request,
    scope_type: Literal["module", "kp_ids"] = Query(..., description="module / kp_ids"),
    scope_value: list[str] = Query(..., description="module 时传 1 个模块名；kp_ids 时传知识点 id（可多值）"),
    aspects: Optional[list[str]] = Query(default=None, description="要哪些部分，默认 mastery+history"),
    history_days: int = Query(30, ge=1, le=365),
    history_limit: int = Query(20, ge=1, le=200),
    mastery_detail: bool = Query(True),
    include_graph: bool = Query(False, description="true=附加范围内的知识点关系边"),
    edge_types: Optional[list[str]] = Query(default=None, description="要哪些边，默认 prereq+cooc"),
    cooc_min_weight: float = Query(0.25, ge=-1, le=1, description="共现边强度下限（NPMI）"),
):
    tid = getattr(request.state, "trace_id", None)
    reg = get_kp_registry(request)

    # aspects 合法值校验（Query 不支持 field_validator，运行时检查）
    if aspects is not None:
        bad = set(aspects) - _VALID_ASPECTS
        if bad:
            raise BizError(ErrorCode.BAD_REQUEST, f"aspects 含非法值 {sorted(bad)}，允许：mastery / history",
                           http_status=400, data={"invalid_aspects": sorted(bad)})

    # scope_value 是 query 收的 list；module 取首元素（模块名），kp_ids 用整个 list
    scope_value_arg = scope_value[0] if scope_type == "module" else scope_value

    result = learning.learning_status(
        student_id, scope_type, scope_value_arg,
        aspects or ["mastery", "history"],
        history_days, history_limit, mastery_detail, reg,
        include_graph=include_graph,
        edge_types=tuple(edge_types) if edge_types else ("prereq", "cooc"),
        cooc_min_weight=cooc_min_weight,
    )
    return ApiResponse(data=result, trace_id=tid)
