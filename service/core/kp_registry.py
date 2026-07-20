"""统一知识点标识注册表：kp_id ↔ kp_idx ↔ kp_name 的唯一权威映射。

【规范】API 边界一律用 kp_id（题库唯一 id）。本注册表负责把 kp_id 翻译成内部用的
kp_idx（mastery_store 索引）和 kp_name（人读）。所有接口都通过这里做映射，不各写一份。

数据来源：
  kp_id  → kp_name：题库 kgPoints 的 {id, name}（缓存到 data/kg_graph/kp_id_map.json）
  kp_name → kp_idx：mastery_store.NAME2IDX（来自 kg_index.json 的 612 标准名）

重名处理：题库有 24 组"同名不同 id"，本注册表里每个 kp_idx 取第一个遇到的 kp_id
作为规范 id（kp_id 自身永远唯一，不会错连）。
"""
import json
import logging
import os
from dataclasses import dataclass

import mastery_store as ms

from .config import settings

log = logging.getLogger("recommend.kp_registry")


@dataclass
class KPRegistry:
    id2idx: dict    # kp_id -> kp_idx
    idx2id: dict    # kp_idx -> kp_id（规范 id）
    id2name: dict   # kp_id -> kp_name

    def kp_id_to_idx(self, kp_id: str):
        return self.id2idx.get(kp_id)

    def idx_to_kp_id(self, idx: int):
        return self.idx2id.get(idx)

    def kp_id_to_name(self, kp_id: str):
        return self.id2name.get(kp_id)

    def resolve(self, kp_ids: list):
        """kp_id 列表 → (kp_idx 列表, 无效 kp_id 列表)。"""
        idxs, invalid = [], []
        for kid in kp_ids:
            i = self.id2idx.get(kid)
            if i is None:
                invalid.append(kid)
            else:
                idxs.append(i)
        return idxs, invalid


def _load_id2name() -> dict:
    """kp_id -> 规范 kp_name。有缓存读缓存，否则扫题库并选最佳名字。

    同一个 kp_id 可能在题库里出现多次、每次用不同的名字（别名/简写）。
    本函数先收集每个 kp_id 的所有名字，再从中挑选跟 NAME2IDX 匹配的那个
    作为规范名（匹配不上则取第一个名字作为兜底）。
    """
    path = settings.kp_id_map_path
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    # 收集: kp_id -> [name1, name2, ...]
    id_to_names: dict = {}
    with open(settings.qbank_path, encoding="utf-8") as f:
        for line in f:
            for kp in json.loads(line).get("kgPoints", []):
                kid, nm = kp.get("id"), kp.get("name")
                if kid and nm:
                    id_to_names.setdefault(kid, []).append(nm)
    # 选最佳名字: 优先匹配 NAME2IDX
    import mastery_store as ms
    name2idx = ms.NAME2IDX
    m: dict = {}
    for kid, names in id_to_names.items():
        for nm in names:
            if nm in name2idx:
                m[kid] = nm
                break
        else:
            m[kid] = names[0]  # 兜底：都不匹配就取第一个
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    log.info(f"kp_id_map built from qbank: {len(m)} unique kp_id -> {path}")
    return m


def build_registry() -> KPRegistry:
    """启动时构建唯一权威映射。"""
    id2name = _load_id2name()
    name2idx = ms.NAME2IDX
    id2idx = {kid: name2idx[nm] for kid, nm in id2name.items() if nm in name2idx}
    # idx -> 规范 kp_id（同 idx 多 id 时取第一个）
    idx2id = {}
    for kid, idx in id2idx.items():
        idx2id.setdefault(idx, kid)
    log.info(f"kp_registry ready: {len(id2idx)} kp_id 映射到 {len(idx2id)} kp_idx")
    return KPRegistry(id2idx=id2idx, idx2id=idx2id, id2name=id2name)
