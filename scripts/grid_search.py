#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
参数网格搜索：在真实考试数据上，扫描 k / κ / N_max / μ，看能否提升 AUC。
更新路径的参数（k/κ/N_max/μ）能被这次评测检验；遗忘参数（τ/ρ 等）无时间间隔，不在此范围。
复用 eval_mechanism 的 replay/auc/brier。
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import service.core.constants as ms
from scripts.eval_mechanism import replay, auc, brier


def metrics(K):
    r = replay(K)
    p_our, ys, gs, touched = r['p_our'], r['y'], r['g'], r['touched']
    bin_idx = [i for i in range(len(gs)) if gs[i] > 0]
    yb = [1 if ys[i] >= 0.999 else 0 for i in bin_idx]
    pa = [p_our[i] for i in bin_idx]
    pers = [i for i in bin_idx if touched[i]]
    ybp = [1 if ys[i] >= 0.999 else 0 for i in pers]
    pap = [p_our[i] for i in pers]
    return (auc(yb, pa),                                 # 全部二值题 AUC
            auc(ybp, pap) if pers else float('nan'),     # 已个性化子集 AUC
            brier(pa, yb))                               # Brier


if __name__ == '__main__':
    # 基线（当前默认）
    ms.KAPPA, ms.N_MAX, ms.MU = 1.0, 100, 1.0
    a0, ap0, br0 = metrics(5.0)
    print(f'基线 (k=5,κ=1,N_max=100,μ=1): 全AUC={a0:.4f}  个性化AUC={ap0:.4f}  Brier={br0:.4f}\n')

    rows = []
    for k in [3, 5, 8]:
        for kap in [1, 3, 6]:
            for nmax in [20, 50, 100]:
                for mu in [0.5, 1, 2]:
                    ms.KAPPA, ms.N_MAX, ms.MU = kap, nmax, mu
                    a, ap, br = metrics(float(k))
                    rows.append((a, ap, br, k, kap, nmax, mu))
    ms.KAPPA, ms.N_MAX, ms.MU = 1.0, 100, 1.0  # 复位

    print('=== Top 5 按「全部二值题 AUC」===')
    for a, ap, br, k, kap, nmax, mu in sorted(rows, key=lambda x: -x[0])[:5]:
        print(f'  全AUC={a:.4f}(Δ{a-a0:+.4f}) 个性化AUC={ap:.4f}(Δ{ap-ap0:+.4f}) Brier={br:.4f}  '
              f'<- k={k} κ={kap} N_max={nmax} μ={mu}')

    print('\n=== Top 5 按「已个性化子集 AUC」(参数在这里最能发力) ===')
    for a, ap, br, k, kap, nmax, mu in sorted(rows, key=lambda x: -x[1])[:5]:
        print(f'  个性化AUC={ap:.4f}(Δ{ap-ap0:+.4f}) 全AUC={a:.4f}(Δ{a-a0:+.4f}) Brier={br:.4f}  '
              f'<- k={k} κ={kap} N_max={nmax} μ={mu}')

    best_overall = max(rows, key=lambda x: x[0])
    best_pers = max(rows, key=lambda x: x[1])
    print(f'\n结论：最优全部AUC={best_overall[0]:.4f}(比基线{best_overall[0]-a0:+.4f})；'
          f'最优个性化AUC={best_pers[1]:.4f}(比基线{best_pers[1]-ap0:+.4f})')
