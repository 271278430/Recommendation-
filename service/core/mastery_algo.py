"""掌握度模型纯算法（零 DB、零副作用）。

sigmoid/clamp/_vec/_ts_list/_row_to_state 是数值与向量工具；
forget_decay/step_update 是 7 步更新的核心数学（原地改传入的 numpy 数组）。

与 process_answer、批量模拟（scripts/eval_mechanism、test_ability）共用同一份代码，
保证"模拟结果 == 真实更新"。常量来自 .constants，N_KP 来自 .kp_registry。
"""
import math
from datetime import timezone

import numpy as np

from . import constants
from .constants import (P, TAU_MIN, TAU_MAX, TAU_PEAK, RHO,
                        FIRM_MIN, FIRM_RANGE, FIRM_SAT, M_OBS_MIN, M_OBS_MAX, SAVINGS_CAP)
from .kp_registry import N_KP

# 注意：KAPPA / MU / N_MAX 这三个被 scripts/grid_search 运行时覆盖调参，
# 故通过 constants 模块属性访问（constants.KAPPA），不用 from-import 局部绑定，
# 否则 grid_search 改 constants.KAPPA 不影响本模块已绑定的局部名。


def sigmoid(x):
    """1/(1+e^(-x))，数值稳定版"""
    return 1.0 / (1.0 + math.exp(-max(min(x, 50), -50)))


def clamp(x, lo, hi):
    return max(min(x, hi), lo)


# ---------------------------------------------------------------------------
# 内部：向量读写转换
# ---------------------------------------------------------------------------
def _vec(v, dtype=np.float32, default=0.0, n=N_KP):
    """确保向量长度 = n，不足补 default。"""
    v = np.asarray(v if v is not None else [default] * n, dtype=dtype)
    if v.shape[0] != n:
        pad = np.full(n, default, dtype=dtype)
        pad[:min(n, v.shape[0])] = v[:n]
        v = pad
    return v


def _ts_list(v, n=N_KP):
    """将 DB 返回的 timestamptz[] 转成 list[datetime|None]，长度 = n。"""
    if v is None:
        return [None] * n
    lst = list(v)
    if len(lst) < n:
        lst.extend([None] * (n - len(lst)))
    out = []
    for x in lst[:n]:
        if x is not None:
            if hasattr(x, "tzinfo") and x.tzinfo is not None:
                x = x.astimezone(timezone.utc).replace(tzinfo=None)
        out.append(x)
    return out


def _row_to_state(r, n=N_KP):
    """DB 行 → (alpha, beta, m_peak, last_ts)。无行则返回冷启动默认值。"""
    if r is None:
        return (
            np.full(n, P, dtype=np.float32),
            np.full(n, P, dtype=np.float32),
            np.full(n, 0.5, dtype=np.float32),
            [None] * n,
        )
    return (
        _vec(r[0], default=float(P)),
        _vec(r[1], default=float(P)),
        _vec(r[2], default=0.5),
        _ts_list(r[3]),
    )


# ---------------------------------------------------------------------------
# 核心算法：7 步更新的纯函数（无 DB，process_answer 与批量模拟共用同一份代码）
# ---------------------------------------------------------------------------
def forget_decay(alpha, beta, m_peak, kp_idx, dt):
    """步骤 1-2（纯函数，原地改 alpha/beta/m_peak）：给定间隔 dt（天），
    α/β 对称向先验 P 衰减（思路二）→ N 下降、m→0.5；峰值 m_peak 向当前 m 回落。
    dt<=0 时不衰减。供 process_answer、forget-preview、批量遗忘曲线模拟共用，保证一致。"""
    if dt <= 0:
        return
    m_cur = float(alpha[kp_idx] / (alpha[kp_idx] + beta[kp_idx]))
    n_cur = float(alpha[kp_idx] + beta[kp_idx])
    eff = n_cur - 2 * P
    firm = clamp(FIRM_MIN + FIRM_RANGE * m_cur * eff / (eff + FIRM_SAT), FIRM_MIN, 1.0)
    tau_eff = TAU_MIN + (TAU_MAX - TAU_MIN) * (firm - FIRM_MIN) / FIRM_RANGE
    r = math.exp(-dt / tau_eff)
    # 步骤 1：对称衰减
    alpha[kp_idx] = P + (alpha[kp_idx] - P) * r
    beta[kp_idx] = P + (beta[kp_idx] - P) * r
    # 步骤 2：峰值衰减（用衰减后的 m_cur）
    m_cur = float(alpha[kp_idx] / (alpha[kp_idx] + beta[kp_idx]))
    if m_peak[kp_idx] > m_cur:
        m_peak[kp_idx] = m_cur + (m_peak[kp_idx] - m_cur) * math.exp(-dt / TAU_PEAK)


def step_update(alpha, beta, m_peak, last_ts, kp_idx, w, y, d, g, k, t):
    """完整 7 步更新（单知识点，纯函数，原地改 alpha/beta/m_peak/last_ts）。
    与 process_answer 数学完全一致；返回 IRT 预测 p_pred 供模拟画校准图。
    供 process_answer 与批量更新模拟共用，确保模拟结果 == 真实更新。"""
    prev_ts = last_ts[kp_idx]
    dt = 0.0
    if prev_ts is not None:
        dt = (t - prev_ts).total_seconds() / 86400.0
        if dt < 0:
            dt = 0.0
    # 步骤 1-2：遗忘
    forget_decay(alpha, beta, m_peak, kp_idx, dt)
    # 步骤 3：IRT 预测
    m_cur = float(alpha[kp_idx] / (alpha[kp_idx] + beta[kp_idx]))
    p_pred = g + (1.0 - g) * sigmoid(k * (m_cur - d))
    # 步骤 4：预测误差
    delta = y - p_pred
    # 步骤 5：重学节省 + 有效权重
    if delta > 0:
        savings = min(1.0 + RHO * max(0.0, m_peak[kp_idx] - m_cur), SAVINGS_CAP)
    else:
        savings = 1.0
    W = w * constants.KAPPA * savings
    # 步骤 6：带 N 截断的加权融合
    N_eff = min(float(alpha[kp_idx] + beta[kp_idx]), constants.N_MAX)
    m_obs = clamp(m_cur + constants.MU * delta, M_OBS_MIN, M_OBS_MAX)
    m_target = (N_eff * m_cur + W * m_obs) / (N_eff + W)
    N_new = min(N_eff + W, constants.N_MAX)
    alpha[kp_idx] = N_new * m_target
    beta[kp_idx] = N_new * (1.0 - m_target)
    # 步骤 7：后处理
    m_peak[kp_idx] = max(m_peak[kp_idx], m_target)
    last_ts[kp_idx] = t
    return p_pred
