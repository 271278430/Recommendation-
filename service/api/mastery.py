"""掌握度路由（薄）：
- GET  /students/{id}/mastery — 遗忘衰减后的掌握度
- POST /students/{id}/mastery — 初始化/重置掌握度（从历史数据注入先验）

读操作，过滤条件走 query。KP 标识用 kp_id，通过 KPRegistry 转 idx。
"""
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator

from ..core.kp_registry import N_KP
from ..svc.mastery import init_student
from ..core.deps import get_kp_registry
from ..core.response import ApiResponse, BizError, ErrorCode
from ..svc import forget

router = APIRouter(prefix="/students/{student_id}", tags=["mastery"])


# ---------------------------------------------------------------------------
# GET /mastery
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# POST /mastery
# ---------------------------------------------------------------------------
class InitMasteryRequest(BaseModel):
    """掌握度初始化请求体。三个字段均为可选：
    - 全空 = 冷启动（m 初始化为 0.5，等价于无历史数据的新学生）
    - 填入摸底考试/分班考试的正确/错误次数和答题时间，作为先验注入模型
    - 注意：已有数据的学生会被覆盖（UPSERT），即本接口同时支持初始化和重置
    """
    correct_counts: Optional[dict[str, int]] = Field(
        None, description="正确次数 {kp_id: count}，count >= 0")
    wrong_counts: Optional[dict[str, int]] = Field(
        None, description="错误次数 {kp_id: count}，count >= 0")
    last_ts: Optional[dict[str, str]] = Field(
        None, description="最后答题时间 {kp_id: ISO8601}，如 '2026-07-15T10:00:00Z'")

    @model_validator(mode="after")
    def _validate_fields(self):
        for field_name in ("correct_counts", "wrong_counts"):
            d = getattr(self, field_name)
            if d:
                for kp_id, count in d.items():
                    if count < 0:
                        raise ValueError(
                            f"{field_name}.{kp_id}: count 必须 >= 0，实际 {count}")
        if self.last_ts:
            for kp_id, ts_str in self.last_ts.items():
                try:
                    datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(
                        f"last_ts.{kp_id}: 无效的 ISO8601 时间串: {ts_str}")
        return self


@router.post("/mastery", response_model=ApiResponse, status_code=201,
             summary="初始化/重置学生掌握度（从历史数据注入先验，或冷启动）")
def init_mastery(student_id: int, req: InitMasteryRequest, request: Request):
    tid = getattr(request.state, "trace_id", None)
    reg = get_kp_registry(request)
    now = forget.now_utc_naive()

    # 1. 收集请求中所有 kp_id
    all_kp_ids: set[str] = set()
    for d in (req.correct_counts, req.wrong_counts, req.last_ts):
        if d:
            all_kp_ids.update(d.keys())

    # 2. 校验 kp_id 有效性
    if all_kp_ids:
        _, invalid = reg.resolve(list(all_kp_ids))
        if invalid:
            raise BizError(ErrorCode.KP_NOT_FOUND, "部分 kp_id 不存在",
                           http_status=404, data={"invalid_kp_ids": invalid})

    # 3. 组装向量（长度 N_KP，未提及的知识点填 0 / None）
    correct_arr = np.zeros(N_KP, dtype=np.float32)
    wrong_arr = np.zeros(N_KP, dtype=np.float32)
    last_ts_arr: list = [None] * N_KP

    if req.correct_counts:
        for kp_id, count in req.correct_counts.items():
            correct_arr[reg.kp_id_to_idx(kp_id)] = float(count)
    if req.wrong_counts:
        for kp_id, count in req.wrong_counts.items():
            wrong_arr[reg.kp_id_to_idx(kp_id)] = float(count)
    if req.last_ts:
        for kp_id, ts_str in req.last_ts.items():
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            last_ts_arr[reg.kp_id_to_idx(kp_id)] = ts

    # 4. 写入
    init_student(student_id, correct_arr, wrong_arr, last_ts_arr)

    # 5. 回读并返回初始化后的掌握度状态
    if all_kp_ids:
        return_idxs, _ = reg.resolve(list(all_kp_ids))
    else:
        # 冷启动：返回全部有映射的知识点（与 GET 一致）
        return_idxs = list(reg.idx2id.keys())

    items = forget.get_decayed(student_id, return_idxs, now)
    for it in items:
        it["kp_id"] = reg.idx_to_kp_id(it["kp_idx"])
        del it["kp_idx"]

    return ApiResponse(
        data={"student_id": student_id, "now": now.isoformat(), "items": items},
        trace_id=tid,
    )
