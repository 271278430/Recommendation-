"""遗忘机制核心业务：只读衰减计算（不落库）。

衰减数学直接调 core.mastery_algo.forget_decay（单一权威来源），不再复刻——
读时衰减与"做题写流水线"的衰减用同一份代码，保证完全一致。
不落库：last_ts 的更新归 svc.mastery.process_answer，本模块只读。
"""
from datetime import datetime, timezone

import numpy as np

from ..core.constants import P
from ..core.kp_registry import KP_NAMES, N_KP
from ..core.mastery_algo import forget_decay
from ..core.repository import get_state


def now_utc_naive() -> datetime:
    """naive UTC（与存储层 _ts_list 的转换一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def decayed_for_kp(alpha, beta, m_peak, last_ts, kp_idx, now) -> tuple[float, float, float, bool, float]:
    """单知识点只读衰减。不修改 alpha/beta/m_peak。

    返回 (m_decayed, m_peak_decayed, dt_days, has_data, n_decayed)。
    在长度 1 的副本上调 forget_decay，消除"两处实现同一公式"的复刻。
    """
    a = float(alpha[kp_idx])
    b = float(beta[kp_idx])
    m0 = a / (a + b)        # 衰减前掌握度
    n0 = a + b              # 衰减前证据量
    ts = last_ts[kp_idx]
    has_data = ts is not None and n0 > 2 * P

    # dt 与 step_update 一致：天数（分数），NULL/<=0 不衰减
    if ts is None:
        return m0, float(m_peak[kp_idx]), 0.0, has_data, n0
    dt = (now - ts).total_seconds() / 86400.0
    if dt <= 0:
        return m0, float(m_peak[kp_idx]), 0.0, has_data, n0

    # 在副本上调 forget_decay（单一数学来源），取回衰减后的 a/b/m_peak
    aa = np.array([a], dtype=np.float32)
    bb = np.array([b], dtype=np.float32)
    pp = np.array([float(m_peak[kp_idx])], dtype=np.float32)
    forget_decay(aa, bb, pp, 0, dt)
    m_d = float(aa[0] / (aa[0] + bb[0]))
    mp_d = float(pp[0])
    n_d = float(aa[0] + bb[0])
    return m_d, mp_d, dt, has_data, n_d


def get_decayed(student_id: int, kp_indices=None, now=None) -> list:
    """读 repository.get_state(student_id)，对指定 idx（None=全部）做只读衰减。

    学生无行时 get_state 返回冷启动默认（m=0.5）。不落库。返回 list[dict]。
    """
    if now is None:
        now = now_utc_naive()
    alpha, beta, m_peak, last_ts = get_state(student_id)
    idxs = kp_indices if kp_indices is not None else range(N_KP)
    out = []
    for i in idxs:
        m, mp, dt, hd, n = decayed_for_kp(alpha, beta, m_peak, last_ts, i, now)
        out.append({
            "kp_idx": int(i),
            "kp_name": KP_NAMES[i],
            "m": round(float(m), 4),
            "m_peak": round(float(mp), 4),
            "N": round(float(n), 2),
            "last_ts": last_ts[i].isoformat() if last_ts[i] else None,
            "days_since": round(float(dt), 2),
            "has_data": bool(hd),
        })
    return out
