"""掌握度路由（薄）：GET /students/{id}/mastery — 遗忘衰减后的掌握度。

读操作，过滤条件走 query。KP 标识用 kp_id，通过 KPRegistry 转 idx。
"""
from typing import Optional

from fastapi import APIRouter, Query, Request

from ..core.deps import get_kp_registry
from ..core.response import ApiResponse, BizError, ErrorCode
from ..svc import forget

router = APIRouter(prefix="/students/{student_id}", tags=["mastery"])


@router.get("/mastery", response_model=ApiResponse, summary="获取经过时间遗忘后的掌握度")
def get_mastery(
    student_id: int,
    request: Request,
    kp_ids: Optional[list[str]] = Query(default=None, description="知识点 id；不传=该学生全部有映射的知识点"),
):
    tid = getattr(request.state, "trace_id", None)
    now = forget.now_utc_naive()
    reg = get_kp_registry(request)

    if kp_ids:
        idxs, invalid = reg.resolve(kp_ids)
        if invalid:
            raise BizError(ErrorCode.KP_NOT_FOUND, "部分 kp_id 不存在",
                           http_status=404, data={"invalid_kp_ids": invalid})
    else:
        # 全量：只取有规范 kp_id 映射的知识点，避免返回 kp_id:null 的项
        idxs = list(reg.idx2id.keys())

    items = forget.get_decayed(student_id, idxs, now)
    # 回填 kp_id（用 kp_idx 内部查），然后删掉 kp_idx（内部字段不返回）
    for it in items:
        it["kp_id"] = reg.idx_to_kp_id(it["kp_idx"])
        del it["kp_idx"]

    return ApiResponse(
        data={"student_id": student_id, "now": now.isoformat(), "items": items},
        trace_id=tid,
    )
