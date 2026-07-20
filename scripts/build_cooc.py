#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建知识点共现关系图谱(邻接矩阵)。
参数统一从 data/kg_graph/cooc_config.json 读取,改配置后重跑即可重建。
产物全部输出到 data/kg_graph/。
"""
import json, os, math
from collections import defaultdict
import numpy as np

ROOT = '/data/shanghui/Recommend_question'
SRC  = f'{ROOT}/data/初中_九年级.jsonl'
def _find(name):
    for p in (f'{ROOT}/{name}', f'{ROOT}/data/{name}'):
        if os.path.exists(p): return p
    raise FileNotFoundError(name)
KPLIST = _find('知识点清单_按学习顺序.md')
OUT  = f'{ROOT}/data/kg_graph'
CFG  = f'{OUT}/cooc_config.json'

DEFAULT_CFG = {
    "cooc_unit": "parent_union",
    "weight_metric": "npmi",
    "k_min": 3,
    "npmi_min": 0.25,
    "top_k": 10,
    "save_jaccard": True,
}
DIFF = {'容易':1, '较易':2, '适中':3, '较难':4, '困难':5}

# ---------- 配置 ----------
def load_config():
    os.makedirs(OUT, exist_ok=True)
    if os.path.exists(CFG):
        cfg = {**DEFAULT_CFG, **{k:v for k,v in json.load(open(CFG, encoding='utf-8')).items() if not k.startswith('_')}}
    else:
        cfg = DEFAULT_CFG
    return cfg

# ---------- 索引:按 学习顺序清单 建索引 0..611 ----------
def build_index():
    names, module_of, order_in_module = [], {}, {}
    cur, cnt = '', 0
    for ln in open(KPLIST, encoding='utf-8'):
        ln = ln.rstrip('\n')
        if ln.startswith('## '):
            cur = ln[3:].strip(); cnt = 0
        elif ln.startswith('- '):
            nm = ln[2:]
            names.append(nm); module_of[nm] = cur; order_in_module[nm] = cnt; cnt += 1
    idx2 = {i:n for i,n in enumerate(names)}
    n2i = {n:i for i,n in enumerate(names)}
    return names, idx2, n2i, module_of, order_in_module

# ---------- 共现单元 ----------
def units_of(d, unit):
    """返回这道题的若干个 tag_set 列表(每个 set 是一组共现的知识点名)"""
    parent_tags = [k['name'] for k in (d.get('kgPoints') or [])]
    children = d.get('children') or []
    if unit == 'parent_union':
        s = set(parent_tags)
        for c in children:
            s |= set(k['name'] for k in (c.get('kgPoints') or []))
        return [s]
    elif unit == 'child_only':
        out = []
        if children:
            for c in children:
                out.append(set(k['name'] for k in (c.get('kgPoints') or [])))
        else:
            out.append(set(parent_tags))
        return [s for s in out if s]
    else:
        raise ValueError(unit)

def main():
    cfg = load_config()
    unit = cfg['cooc_unit']; k_min = cfg['k_min']
    npm_t = cfg['npmi_min']; top_k = cfg['top_k']
    metric = cfg['weight_metric']; save_jac = cfg['save_jaccard']
    print(f'配置: unit={unit} metric={metric} k_min={k_min} npmi_min={npm_t} top_k={top_k} jaccard={save_jac}')

    names, idx2, n2i, module_of, order_in_module = build_index()
    n = len(names)
    print(f'知识点数: {n}')

    # 计数
    C = np.zeros((n, n), dtype=np.int64)
    freq = np.zeros(n, dtype=np.int64)
    diff_sum = np.zeros(n, dtype=np.float64); diff_cnt = np.zeros(n, dtype=np.int64)
    N_units = 0
    with open(SRC, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            dv = DIFF.get(d.get('difficulty'), 0)
            for s in units_of(d, unit):
                idx = sorted(n2i[x] for x in s if x in n2i)
                if not idx: continue
                N_units += 1
                for i in idx:
                    freq[i] += 1
                    if dv: diff_sum[i] += dv; diff_cnt[i] += 1
                for a in range(len(idx)):
                    for b in range(a+1, len(idx)):
                        C[idx[a], idx[b]] += 1; C[idx[b], idx[a]] += 1
    N = N_units
    print(f'共现单元数 N={N} | 有共现的对数(C>0)={int((C>0).sum()//2)}')

    # ---- 权重矩阵 ----
    Pi = freq / N
    Pij = C / N
    # NPMI
    with np.errstate(divide='ignore', invalid='ignore'):
        denom = np.outer(Pi, Pi)
        pmi = np.where(Pij > 0, np.log(Pij / denom), 0.0)
        neg = -np.log(Pij)
        npmi = np.where(Pij > 0, pmi / neg, 0.0)
    npmi = np.nan_to_num(npmi, nan=0.0, posinf=1.0, neginf=0.0)
    npmi = np.clip(npmi, -1.0, 1.0)
    # Jaccard
    if save_jac or metric == 'jaccard':
        union = freq[:, None] + freq[None, :] - C
        with np.errstate(divide='ignore', invalid='ignore'):
            jac = np.where(union > 0, C / union, 0.0)

    # 主权重矩阵
    if metric == 'npmi':       Wmain = npmi
    elif metric == 'jaccard':  Wmain = jac
    else:                      Wmain = C.astype(np.float64)

    # ---- 阈值化 -> 邻接矩阵 A ----
    pass_mask = (C >= k_min)
    if metric == 'npmi':
        pass_mask &= (Wmain >= npm_t)
    topk = np.zeros((n, n), dtype=bool)
    for i in range(n):
        cand = np.where(pass_mask[i], Wmain[i], -1e9)
        npass = int(pass_mask[i].sum())
        if npass == 0: continue
        k = min(top_k, npass)
        idx = np.argpartition(cand, -k)[-k:]
        topk[i, idx] = True
    keep = pass_mask & (topk | topk.T)
    A = np.where(keep, Wmain, 0.0).astype(np.float64)

    # ---- 节点属性 ----
    nodes = []
    for i, nm in enumerate(names):
        nodes.append({
            'idx': i, 'name': nm,
            'module': module_of.get(nm, ''),
            'order_in_module': order_in_module.get(nm, 0),
            'freq': int(freq[i]),
            'avg_difficulty': round(float(diff_sum[i]/diff_cnt[i]), 2) if diff_cnt[i] else None,
        })

    # ---- 输出(精简:仅 4 个文件) ----
    json.dump(names, open(f'{OUT}/kg_index.json','w',encoding='utf-8'), ensure_ascii=False)  # list, 位置即索引
    json.dump(nodes, open(f'{OUT}/nodes.json','w',encoding='utf-8'), ensure_ascii=False)
    np.save(f'{OUT}/cooc_adj.npy', A)
    # 注:cooc_config.json 为用户维护的参数文件,不在此覆盖;count/npmi/jaccard 等中间量不落盘,改参数重跑即可

    # ---- 校验/报告(仅控制台打印,不落盘) ----
    assert np.allclose(A, A.T), 'A 不对称!'
    deg = (A > 0).sum(axis=1)
    n_edges = int((A > 0).sum() // 2)
    # 临时算 top 边用于打印
    top = []
    for i in range(n):
        for j in range(i+1, n):
            if A[i, j] != 0:
                top.append((A[i, j], int(C[i, j]), names[i], names[j]))
    top.sort(reverse=True)
    print(f'\n==== 构建完成 ====')
    print(f'边数(A 中非零/2): {n_edges}')
    print(f'节点度: min={deg.min()} max={deg.max()} 中位={int(np.median(deg))} 均值={deg.mean():.2f} | 孤立节点(度0): {int((deg==0).sum())}')
    print(f'冷门点(freq<{k_min}): {int((freq<k_min).sum())}')
    print(f'\nTop 15 共现边(按主权重 {metric}):')
    for w, c, a, b in top[:15]:
        print(f'  {w:.3f} (cnt={c:<4})  {a}  +  {b}')
    print(f'\n产物目录: {OUT}/  (kg_index.json, nodes.json, cooc_adj.npy, cooc_config.json)')

if __name__ == '__main__':
    main()
