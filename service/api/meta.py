"""元数据路由（薄）：知识点列表 + 知识图谱边。

供前端下拉/渲染用的静态元数据查询，统一走 ApiResponse 封装。
（学生列表归 student.py，属于 students 资源）
"""
import json
import os

from fastapi import APIRouter, Depends, Request

from ..core.config import settings
from ..core.deps import get_kp_registry
from ..core.response import ApiResponse
from ..svc.knowledge_graph import get_edges

router = APIRouter(tags=["meta"])


@router.get("/knowledge-points", response_model=ApiResponse, summary="知识点列表 [{kp_id, kp_name, module}]")
def list_knowledge_points(request: Request, reg=Depends(get_kp_registry)):
    tid = getattr(request.state, "trace_id", None)
    nodes_path = os.path.join(settings.project_root, "data", "kg_graph", "nodes.json")
    with open(nodes_path, encoding="utf-8") as f:
        nodes = json.load(f)
    result = [{"kp_id": reg.idx_to_kp_id(n["idx"]), "kp_name": n["name"], "module": n.get("module", "")}
              for n in nodes]
    return ApiResponse(data=result, trace_id=tid)


@router.get("/knowledge-graph/edges", response_model=ApiResponse, summary="全图边（先修+共现）")
def list_graph_edges(request: Request, reg=Depends(get_kp_registry)):
    tid = getattr(request.state, "trace_id", None)
    # 全图：kp_idxs=None，idx2id 只含有效映射，自动过滤无 kp_id 的知识点
    edges = get_edges(None, reg.idx2id)
    return ApiResponse(data=edges, trace_id=tid)
