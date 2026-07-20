"""推荐路由（薄）：POST /students/{id}/recommendations — 粗召回。

按学生 + 知识点范围生成推荐候选（计算型资源，用 POST）。
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..core.deps import get_kp_registry
from ..core.response import ApiResponse
from ..svc import recommend

router = APIRouter(prefix="/students/{student_id}", tags=["recommend"])


class RecommendationRequest(BaseModel):
    kp_ids: list[str] = Field(..., min_length=1, description="要推题的知识点 id 列表")


@router.post("/recommendations", response_model=ApiResponse,
             summary="粗召回：按 p∈[0.30,0.80] 召回候选题库")
def create_recommendations(student_id: int, req: RecommendationRequest, request: Request):
    tid = getattr(request.state, "trace_id", None)
    reg = get_kp_registry(request)
    groups, empty_kps = recommend.recall(student_id, req.kp_ids, reg)
    return ApiResponse(
        data={"student_id": student_id, "groups": groups, "empty_kps": empty_kps},
        trace_id=tid,
    )
