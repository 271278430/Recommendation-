#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证更新机制的"难度感知对称性"：
  做对：难题增更多、易题增更少
  做错：易题减更多、难题减更少
在不同起始掌握度 m0 下扫描难度 d，用真实 step_update 单步更新，看 Δm。
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from mastery_store import step_update, P, N_KP
from datetime import datetime

T0 = datetime(2025, 10, 16, 9, 0, 0)
N0 = 4.0  # 冷启动证据量


def delta_m(m0, d, y, g=0.0, k=5.0):
    a = np.full(N_KP, P, np.float32); b = np.full(N_KP, P, np.float32)
    ki = 0
    a[ki] = N0 * m0; b[ki] = N0 * (1 - m0)          # 构造 m=m0, N=4
    pk = np.full(N_KP, 0.5, np.float32); pk[ki] = max(0.5, m0)   # m_peak ≥ m0
    ts = [None] * N_KP
    m_before = float(a[ki] / (a[ki] + b[ki]))
    step_update(a, b, pk, ts, ki, 1.0, float(y), float(d), float(g), float(k), T0)
    return float(a[ki] / (a[ki] + b[ki])) - m_before


ds = [0.1, 0.3, 0.5, 0.7, 0.9]
labels = ['很易', '较易', '适中', '较难', '很难']
print(f"{'难度d':<14}" + ''.join(f'{l}({d})'.ljust(13) for l, d in zip(labels, ds)))
all_ok = True
for m0 in [0.3, 0.5, 0.8]:
    corr = [delta_m(m0, d, 1.0) for d in ds]   # 做对 Δm
    wrong = [delta_m(m0, d, 0.0) for d in ds]  # 做错 Δm
    print(f"\n--- 起始 m0={m0} (N=4, g=0, k=5) ---")
    print(f"  做对Δm: " + ''.join(f'{x:+.4f}'.ljust(13) for x in corr))
    print(f"  做错Δm: " + ''.join(f'{x:+.4f}'.ljust(13) for x in wrong))
    p1 = corr[-1] > corr[0]          # 难题做对 > 易题做对
    p2 = wrong[0] < wrong[-1]        # 易题做错(更负) < 难题做错
    p3 = corr[-1] > 0                # 难题做对仍增
    p4 = wrong[0] < 0                # 易题做错仍减
    ok = p1 and p2 and p3 and p4
    all_ok = all_ok and ok
    print(f"  ✓ 难题做对增更多({corr[0]:+.4f}→{corr[-1]:+.4f}): {'是' if p1 else '否'}")
    print(f"  ✓ 易题做错减更多({wrong[0]:+.4f} vs 难题{wrong[-1]:+.4f}): {'是' if p2 else '否'}")

print(f"\n=== 结论：四条难度感知性质在 m0=0.3/0.5/0.8 下{'全部满足 ✅' if all_ok else '存在违反 ❌'} ===")
