"""遗忘机制核心业务：只读衰减计算（不落库）。

- 衰减数学复刻 mastery_store.forget_decay（常量从 mastery_store 复用，不重定义），
  保证读时衰减与"做题写流水线"的衰减完全一致。
- 不落库：last_ts 的更新归 process_answer（答题写流水线），本模块只读。
"""
import math
from datetime import datetime, timezone

from mastery_store import (
    get_state,
    P, TAU_MIN, TAU_MAX, TAU_PEAK,
    FIRM_MIN, FIRM_RANGE, FIRM_SAT,
    KP_NAMES, N_KP, clamp,
)


def now_utc_naive() -> datetime:
    """与 mastery_store 一致：naive UTC（其 _ts_list 把 DB 时间转成 naive UTC）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def decayed_for_kp(alpha, beta, m_peak, last_ts, kp_idx, now) -> tuple[float, float, float, bool, float]:
    """单知识点只读衰减。不修改 alpha/beta/m_peak。

    返回 (m_decayed, m_peak_decayed, dt_days, has_data)。
    数学与 mastery_store.forget_decay 逐行一致（单测对拍校验）。
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

    # τ 用衰减前的 m0、N 计算（与 forget_decay 同序）
    eff = n0 - 2 * P
    firm = clamp(FIRM_MIN + FIRM_RANGE * m0 * eff / (eff + FIRM_SAT), FIRM_MIN, 1.0)
    tau = TAU_MIN + (TAU_MAX - TAU_MIN) * (firm - FIRM_MIN) / FIRM_RANGE
    r = math.exp(-dt / tau)

    # 步骤1：α,β 对称向先验 P 衰减 → N 下降、m→0.5
    a_d = P + (a - P) * r
    b_d = P + (b - P) * r
    m_d = a_d / (a_d + b_d)
    n_d = a_d + b_d

    # 步骤2：m_peak 向衰减后的 m 回落（τ_peak 慢衰减）
    mp = float(m_peak[kp_idx])
    mp_d = m_d + (mp - m_d) * math.exp(-dt / TAU_PEAK) if mp > m_d else mp

    return m_d, mp_d, dt, has_data, n_d


def get_decayed(student_id: int, kp_indices=None, now=None) -> list:
    """读 mastery_store.get_state(student_id)，对指定 idx（None=全部）做只读衰减。

    学生无行时 get_state 返回冷启动默认（m=0.5）。不落库。
    返回 list[dict]。
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
