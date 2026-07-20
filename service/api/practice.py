"""做题事件路由（薄）：
- GET  /students/{id}/practice-events — 做题序列（原 kp-sequence，读，过滤条件走 query）
- POST /students/{id}/practice-events — 答题（原 submit-answer，创建事件，数据走 body）
"""
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..core.deps import get_kp_registry
from ..core.response import ApiResponse, BizError, ErrorCode
from ..svc import practice

router = APIRouter(prefix="/students/{student_id}", tags=["practice"])


@router.get("/practice-events", response_model=ApiResponse, summary="某学生在某知识点的做题序列")
def list_practice_events(
    student_id: int,
    request: Request,
    kp_id: str = Query(..., description="知识点 id"),
    limit: int = Query(50, ge=1, le=500, description="最多返回条数"),
    wrong_only: bool = Query(False, description="True=只要错题（错题本）"),
):
    tid = getattr(request.state, "trace_id", None)
    reg = get_kp_registry(request)

    if reg.kp_id_to_idx(kp_id) is None:
        raise BizError(ErrorCode.KP_NOT_FOUND, "kp_id 不存在",
                       http_status=404, data={"invalid_kp_ids": [kp_id]})

    events = practice.kp_sequence(student_id, kp_id, limit, wrong_only)
    return ApiResponse(
        data={"student_id": student_id, "kp_id": kp_id, "count": len(events), "events": events},
        trace_id=tid,
    )


class SubmitAnswerRequest(BaseModel):
    question_id: str
    score: float = Field(..., ge=0, le=1, description="得分率 [0,1]；选择题 0 或 1")
    is_wrong: Optional[bool] = Field(None, description="是否判错；不传则按 score<0.5 兜底。主观题（部分得分）建议显式传，否则错题统计失真")
    source: Literal["exam", "homework", "classwork"] = Field("homework", description="来源：exam/homework/classwork")
    ts: Optional[str] = Field(None, description="答题时间（ISO 8601），默认 now；范围 now-30天~now+1分钟")
    equal_weights: bool = Field(False, description="True=强制多知识点等权；False=自动用题库权重文件，兜底等权")
    client_request_id: Optional[str] = Field(None, description="幂等键；传入则相同键的重复请求不重复更新掌握度（防网络重试）")


@router.post("/practice-events", response_model=ApiResponse, status_code=201,
             summary="答题后更新掌握度 + 写做题记录，返回 before→after 变化")
def create_practice_event(student_id: int, req: SubmitAnswerRequest, request: Request):
    tid = getattr(request.state, "trace_id", None)
    reg = get_kp_registry(request)

    ts = None
    if req.ts:
        ts = datetime.fromisoformat(req.ts.replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)  # 统一 naive UTC
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if ts < now - timedelta(days=30) or ts > now + timedelta(minutes=1):
            raise BizError(ErrorCode.BAD_REQUEST, "ts 超出允许范围（now-30天 ~ now+1分钟）",
                           http_status=400, data={"ts": req.ts})

    result = practice.submit_answer(
        student_id, req.question_id, req.score, reg,
        source=req.source, ts=ts, equal_weights=req.equal_weights,
        is_wrong=req.is_wrong, client_request_id=req.client_request_id,
    )
    return ApiResponse(data=result, trace_id=tid)
