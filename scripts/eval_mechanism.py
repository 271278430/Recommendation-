#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线评估：用真实考试数据重放掌握度更新机制，逐题预测，回答"机制预测得准不准"。

做法：
  对每个学生，从冷启动开始，按题序逐题：
    1) 用当前 m 经 IRT 预测该题正确率 p_our；
    2) 记录 (p_our, p_base, y, 该KP是否已练过) —— p_base = m=0.5 的纯难度预测（个性化前的基线）；
    3) 用真实 y 走 step_update 更新 m。
  汇总所有 (p, y)，算 AUC / Brier / 相关系数；并扫描 k 找最优。

注意（诚实边界）：
  - 考试是单次横截面（所有题同一时间戳），所以本评估只验证【更新机制】，
    不验证【遗忘机制】（无时间间隔，dt=0，遗忘不触发）。
  - 难度 d 由全体得分率估计（含被预测学生），属标准项目参数估计，轻微泄漏可接受。
依赖：numpy；复用 mastery_store.step_update 与 web_app 的考试数据。
"""
import os
import sys
import numpy as np
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from service.core.mastery_algo import step_update, sigmoid
from service.core.constants import P
from service.core.kp_registry import N_KP

print('加载考试数据...')
from scripts import web_app as W
print(f'  学生 {len(W.EXAM_STUDENTS)} 名')

T0 = datetime(2025, 10, 16, 9, 0, 0)


def irt(m, d, g, k):
    return g + (1 - g) * sigmoid(k * (m - d))


def auc(y_true, y_score):
    """Mann-Whitney AUC（处理并列），y_true ∈ {0,1}。"""
    yt = np.asarray(y_true, float)
    ys = np.asarray(y_score, float)
    n1 = yt.sum(); n0 = len(yt) - n1
    if n1 == 0 or n0 == 0:
        return float('nan')
    order = np.argsort(ys, kind='mergesort')
    ranks = np.empty(len(ys), float)
    ys_s = ys[order]
    i = 0
    while i < len(ys_s):
        j = i
        while j + 1 < len(ys_s) and ys_s[j + 1] == ys_s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1
        i = j + 1
    return float((ranks[yt == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def logloss(p, y):
    p = np.clip(np.asarray(p), 1e-6, 1 - 1e-6)
    y = np.asarray(y)
    return float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))


def replay(K=5.0):
    """逐生逐题重放。返回 dict: keys=p_our,p_base,y,touched,g。"""
    p_our, p_base, ys, touched, gs = [], [], [], [], []
    for sid in W.EXAM_STUDENTS:
        recs = W.EXAM_RECORDS[sid]
        a = np.full(N_KP, P, np.float32)
        b = np.full(N_KP, P, np.float32)
        pk = np.full(N_KP, 0.5, np.float32)
        ts = [None] * N_KP
        seen = np.zeros(N_KP, dtype=bool)
        for col, ki, y, d, g in recs:
            m = float(a[ki] / (a[ki] + b[ki]))
            p_our.append(irt(m, d, g, K))
            p_base.append(irt(0.5, d, g, K))
            ys.append(float(y)); touched.append(bool(seen[ki])); gs.append(float(g))
            step_update(a, b, pk, ts, ki, 1.0, float(y), float(d), float(g), float(K), T0)
            seen[ki] = True
    return dict(p_our=p_our, p_base=p_base, y=ys, touched=touched, g=gs)


def report(r, label):
    p_our, p_base, ys, touched, gs = r['p_our'], r['p_base'], r['y'], r['touched'], r['g']
    # 选择/判断题（g>0，y 天然 0/1）→ AUC 主指标
    bin_idx = [i for i in range(len(gs)) if gs[i] > 0]
    yb = [1 if ys[i] >= 0.999 else 0 for i in bin_idx]
    pa = [p_our[i] for i in bin_idx]
    pba = [p_base[i] for i in bin_idx]
    # 已个性化子集（该KP此前练过）
    pers_idx = [i for i in bin_idx if touched[i]]
    yb_p = [1 if ys[i] >= 0.999 else 0 for i in pers_idx]
    pa_p = [p_our[i] for i in pers_idx]
    pba_p = [p_base[i] for i in pers_idx]

    print(f'\n=== {label} (k={r["K"]}) ===')
    print(f'  总记录 {len(ys)} | 选择/判断题(二值) {len(bin_idx)} | 其中已个性化 {len(pers_idx)}')
    if len(bin_idx):
        print(f'  [全部二值题] AUC  我们={auc(yb,pa):.4f}   难度基线={auc(yb,pba):.4f}   '
              f'提升={auc(yb,pa)-auc(yb,pba):+.4f}')
        print(f'               Brier 我们={brier(pa,yb):.4f}  基线={brier(pba,yb):.4f}   '
              f'logloss 我们={logloss(pa,yb):.4f}')
    if len(pers_idx):
        print(f'  [已个性化子集] AUC 我们={auc(yb_p,pa_p):.4f}  基线={auc(yb_p,pba_p):.4f}  '
              f'提升={auc(yb_p,pa_p)-auc(yb_p,pba_p):+.4f}  ← 个性化是否有效看这里')
    corr_all = np.corrcoef(p_our, ys)[0, 1] if len(ys) > 2 else float('nan')
    print(f'  [全部记录]   相关系数 r(p_our, y)={corr_all:.4f}   Brier={brier(p_our,ys):.4f}')
    return auc(yb, pa) if len(bin_idx) else float('nan')


if __name__ == '__main__':
    print('\n逐生逐题重放中...')
    r = replay(K=5.0); r['K'] = 5.0
    a5 = report(r, '当前默认 k=5')

    print('\n' + '=' * 60)
    print('扫描 k（在二值题上找最优 AUC，顺带看是否稳定）')
    print('=' * 60)
    best = (None, -1)
    for k in [0.5, 1, 2, 3, 5, 8, 12, 20]:
        rr = replay(K=k)
        bin_idx = [i for i in range(len(rr['g'])) if rr['g'][i] > 0]
        yb = [1 if rr['y'][i] >= 0.999 else 0 for i in bin_idx]
        pa = [rr['p_our'][i] for i in bin_idx]
        a = auc(yb, pa)
        bl = brier(pa, yb)
        mark = '  ← 当前默认' if abs(k - 5.0) < 1e-9 else ('  ← 最优' if a == best[1] else '')
        print(f'  k={k:<5} AUC={a:.4f}  Brier={bl:.4f}{mark if a<=best[1] else ""}')
        if a > best[1]:
            best = (k, a)
    print(f'\n  最优 k = {best[0]} (AUC={best[1]:.4f})')
    print('\n完成。')
