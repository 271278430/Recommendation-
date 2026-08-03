#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
原型测试：在预测里加一个"学生全局能力项 θ"，看 AUC 能否提升。
动机：评测发现"冷启动题"（该生首次做该KP）占大多数，此时 m=0.5，
两个强弱不同的学生得到完全相同的预测 → 这是 AUC 上不去的主因。
θ = 该生在此前题目上相对群体基线的平均超/欠表现（用已见答案估计，不偷看未来）。
预测时把掌握度修正为 m_eff = m + β·θ。
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from service.core.mastery_algo import step_update, sigmoid, clamp
from service.core.constants import P
from service.core.kp_registry import N_KP
from scripts.eval_mechanism import auc, brier
from scripts import web_app as W
from datetime import datetime

T0 = datetime(2025, 10, 16, 9, 0, 0)
def irt(m, d, g, k): return g + (1 - g) * sigmoid(k * (m - d))


def replay(beta, K=5.0, use_theta=True):
    p, ys, gs, touched = [], [], [], []
    for sid in W.EXAM_STUDENTS:
        a = np.full(N_KP, P, np.float32); b = a.copy(); pk = np.full(N_KP, 0.5, np.float32); ts = [None]*N_KP
        seen = np.zeros(N_KP, bool)
        sum_res = 0.0; n_res = 0
        for col, ki, y, d, g in W.EXAM_RECORDS[sid]:
            m = float(a[ki] / (a[ki] + b[ki]))
            p_base = irt(0.5, d, g, K)
            theta = (sum_res / n_res) if (use_theta and n_res > 0) else 0.0
            m_eff = clamp(m + beta * theta, 0.001, 0.999)
            p.append(irt(m_eff, d, g, K)); ys.append(float(y)); gs.append(g); touched.append(bool(seen[ki]))
            step_update(a, b, pk, ts, ki, 1.0, float(y), float(d), float(g), float(K), T0)
            seen[ki] = True
            sum_res += (float(y) - p_base); n_res += 1
    bin_idx = [i for i in range(len(gs)) if gs[i] > 0]
    yb = [1 if ys[i] >= 0.999 else 0 for i in bin_idx]
    pa = [p[i] for i in bin_idx]
    pers = [i for i in bin_idx if touched[i]]
    ybp = [1 if ys[i] >= 0.999 else 0 for i in pers]; pap = [p[i] for i in pers]
    return auc(yb, pa), (auc(ybp, pap) if pers else float('nan')), brier(pa, yb)


if __name__ == '__main__':
    a0, ap0, br0 = replay(beta=0.0, use_theta=False)
    print(f'基线(无θ): 全AUC={a0:.4f}  个性化AUC={ap0:.4f}  Brier={br0:.4f}\n')
    print('加 θ 后（扫 β）：')
    best = (a0, 0)
    for beta in [0.2, 0.4, 0.6, 0.8, 1.0]:
        a, ap, br = replay(beta=beta)
        mark = '  ← 最优' if a >= best[0] else ''
        print(f'  β={beta}: 全AUC={a:.4f}(Δ{a-a0:+.4f})  个性化AUC={ap:.4f}(Δ{ap-ap0:+.4f})  Brier={br:.4f}{mark}')
        if a > best[0]: best = (a, beta)
    print(f'\n最优 β={best[1]} → 全AUC {a0:.4f}→{best[0]:.4f} ({"+" if best[0]-a0>=0 else ""}{(best[0]-a0)*100:.2f}%)')
