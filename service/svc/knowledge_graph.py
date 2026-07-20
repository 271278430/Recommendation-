"""知识点图谱边数据服务：给定范围内的知识点，返回它们之间的先修/共现边。

边是**静态知识图谱数据**（学生无关）：A 是 B 的前置、A 和 B 共现——这是知识点本身的属性，
对所有学生一样。所以本函数不连库、不依赖学生，只读 data/kg_graph 静态文件。

供学情接口在 include_graph=true 时调用：学情数据（节点掌握度）已由学情接口查出，
本函数只负责"附加关系边"，前端拿到 节点+边 即可渲染可视化学情图谱。
"""
import json
import os

import numpy as np

from ..core.config import settings

_prereq = None
_cooc = None


def _load():
    """懒加载静态图谱数据（先修边列表 + 共现矩阵）。"""
    global _prereq, _cooc
    if _prereq is None:
        with open(os.path.join(settings.project_root, "data", "kg_graph", "prereq_edges.json"),
                  encoding="utf-8") as f:
            _prereq = json.load(f)
        _cooc = np.load(os.path.join(settings.project_root, "data", "kg_graph", "cooc_adj.npy"))
    return _prereq, _cooc


def get_edges(kp_idxs: list[int] | None, idx2id, edge_types=("prereq", "cooc"), cooc_min_weight: float = 0.25) -> list[dict]:
    """返回知识点关系边（静态图谱数据，学生无关，只读 data/kg_graph）。

    参数：
      kp_idxs: 范围内知识点 idx 集合；传 None 表示全图（所有有 idx2id 映射的知识点）
      idx2id: kp_idx -> kp_id 映射（KPRegistry.idx2id）
      edge_types: 要哪种边，默认都要
      cooc_min_weight: 共现边强度下限（NPMI），低于此丢弃

    返回 [{from, to, type, ...}, ...]，from/to 均为 kp_id。
    kp_idxs 非 None 时只返回两端都在范围内的边。
    """
    prereq, cooc = _load()
    scope = set(kp_idxs) if kp_idxs is not None else None

    def _in_scope(i, j):
        return scope is None or (i in scope and j in scope)

    edges = []

    # 先修边（有向 a→b：a 是 b 的前置）
    if "prereq" in edge_types:
        for e in prereq:
            i, j = e["i"], e["j"]
            if _in_scope(i, j):
                fi, fj = idx2id.get(i), idx2id.get(j)
                if fi and fj:
                    edges.append({"from": fi, "to": fj, "type": "prereq", "subtype": e.get("type")})

    # 共现边（无向，向量化取上三角避免重复）
    if "cooc" in edge_types:
        rows, cols = np.nonzero(cooc >= cooc_min_weight)
        for i, j in zip(rows, cols):
            if i < j and _in_scope(int(i), int(j)):
                fi, fj = idx2id.get(int(i)), idx2id.get(int(j))
                if fi and fj:
                    edges.append({"from": fi, "to": fj, "type": "cooc", "weight": round(float(cooc[i, j]), 4)})

    return edges
