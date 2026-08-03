"""遗忘机制数学层测试：对拍 + §2.2 性质 + 全边界（不依赖 HTTP）。

对照《学情建模与学情状态更新机制建模V1.md》§2.2.1（掌握度遗忘）与 §2.2.2（峰值遗忘）。
"""
import math
import random
from datetime import datetime, timedelta

import numpy as np
import pytest

from service.core.mastery_algo import forget_decay
from service.core.constants import TAU_PEAK
from service.svc.forget import decayed_for_kp, get_decayed

NOW = datetime(2026, 7, 15)


def _write_ref(a, b, pk, ts):
    """写式参考：拷贝数组后走 forget_decay（mastery_store 的权威实现）。"""
    A = np.array([a], dtype=np.float32)
    B = np.array([b], dtype=np.float32)
    PK = np.array([pk], dtype=np.float32)
    if ts is not None:
        dt = (NOW - ts).total_seconds() / 86400
        if dt > 0:
            forget_decay(A, B, PK, 0, dt)
    return float(A[0] / (A[0] + B[0])), float(PK[0])


# ---- §2.2 一致性：只读实现 == 写式权威实现 ----
def test_pairwise_vs_forget_decay_2000_cases():
    """对拍 2000 例，m 与 m_peak 都必须与 forget_decay 一致。"""
    random.seed(42)
    for _ in range(2000):
        a, b = random.uniform(2, 60), random.uniform(2, 60)
        pk = random.uniform(0.4, 0.99)
        ts = NOW - timedelta(days=random.uniform(0, 400)) if random.random() > 0.1 else None
        mr, mpr, _, _, _ = decayed_for_kp(np.array([a]), np.array([b]), np.array([pk]), [ts], 0, NOW)
        mw, mpw = _write_ref(a, b, pk, ts)
        assert abs(mr - mw) < 1e-5, f"m 不一致: {mr} vs {mw}"
        assert abs(mpr - mpw) < 1e-5, f"m_peak 不一致: {mpr} vs {mpw}"


# ---- §2.2.1 对称衰减：dt→∞ 时 m→0.5、N→2P（不是→0）----
def test_long_gap_decays_to_prior_not_zero():
    m, _, _, _, _ = decayed_for_kp(np.array([40.]), np.array([10.]), np.array([0.9]),
                                [NOW - timedelta(days=10000)], 0, NOW)
    assert abs(m - 0.5) < 1e-3


# ---- §2.2.2 m_peak 永不跌破 m（用合法输入 m_peak ≥ max(0.5, m0)）----
def test_m_peak_never_below_m():
    random.seed(7)
    for _ in range(2000):
        a, b = random.uniform(2, 50), random.uniform(2, 50)
        m0 = a / (a + b)
        pk = random.uniform(max(0.5, m0), 0.99)   # 合法：历史峰值 ≥ max(0.5, 当前m)
        ts = NOW - timedelta(days=random.uniform(1, 300))
        m, mp, _, _, _ = decayed_for_kp(np.array([a]), np.array([b]), np.array([pk]), [ts], 0, NOW)
        assert mp >= m - 1e-6


# ---- §2.2.2 gap 保留率 = exp(-dt/τ_peak)（相对衰减后 m）----
def test_gap_retention_equals_tau_peak():
    a, b, pk, dt = 40, 10, 0.95, 60
    m, mp, _, _, _ = decayed_for_kp(np.array([a]), np.array([b]), np.array([pk]),
                                 [NOW - timedelta(days=dt)], 0, NOW)
    gap_after = mp - m
    expected = math.exp(-dt / TAU_PEAK)
    assert abs(gap_after / (pk - m) - expected) < 1e-3


# ---- firm 单调：扎实者衰减慢，不扎实者衰减快（同 dt）----
def test_solid_decays_slower_than_weak():
    def delta(a, b, m0, dt):
        m, _, _, _, _ = decayed_for_kp(np.array([a]), np.array([b]), np.array([0.9]),
                                    [NOW - timedelta(days=dt)], 0, NOW)
        return abs(m - m0)
    solid = delta(40, 10, 0.8, 30)     # m0=0.8, N=50 → firm 高 → 衰减慢
    weak = delta(3, 5, 0.375, 30)      # m0=0.375, N=8 → firm 低 → 衰减快
    assert solid < weak


# ---- 边界 ----
def test_boundary_null_last_ts():
    m, _, dt, hd, _ = decayed_for_kp(np.array([40.]), np.array([10.]), np.array([0.9]), [None], 0, NOW)
    assert m == 0.8 and dt == 0 and hd is False


def test_boundary_zero_and_negative_dt():
    # dt=0：刚练过，不衰减
    assert decayed_for_kp(np.array([40.]), np.array([10.]), np.array([0.9]), [NOW], 0, NOW)[0] == 0.8
    # dt<0：时钟回拨，不衰减
    assert decayed_for_kp(np.array([40.]), np.array([10.]), np.array([0.9]),
                          [NOW + timedelta(days=5)], 0, NOW)[0] == 0.8


def test_cold_start_new_student_all_prior(require_pg):
    """新学生（库里无行）所有知识点 m=0.5、has_data=False。"""
    items = get_decayed(99999999, [0, 1, 2], NOW)
    assert all(it["m"] == 0.5 and it["has_data"] is False for it in items)
