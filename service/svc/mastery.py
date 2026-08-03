"""掌握度业务编排：答题更新(process_answer)、冷启动(init_student)、读派生指标。

业务逻辑层——调 core.mastery_algo（纯算法）算，调 core.repository 落库/读。
读派生（get_mastery/get_confidence/get_converged_mastery/weakness_score/weakest_kps）
是不带时间衰减的"存储值"派生，主要供 scripts/离线分析用；线上读掌握度走 svc.forget（带衰减）。
"""
from datetime import datetime, timezone

import numpy as np

from ..core.constants import P, K, K_SAT
from ..core.kp_registry import N_KP, KP_NAMES
from ..core.mastery_algo import step_update, _vec, _ts_list
from ..core.repository import get_state, update


# ---------------------------------------------------------------------------
# 读派生（不带衰减，直接 alpha/beta 派生）
# ---------------------------------------------------------------------------
def get_mastery(student_id):
    """读掌握度 m = alpha/(alpha+beta)，0 基 np.float32[N_KP]；不存在返回全 0.5。"""
    alpha, beta, _, _ = get_state(student_id)
    return alpha / (alpha + beta)


def get_confidence(student_id):
    """读置信度 c = (N-2P)/(N-2P+K_SAT)。分母饱和常数是 K_SAT=8（不是 IRT K=5）。"""
    alpha, beta, _, _ = get_state(student_id)
    eff = alpha + beta - 2 * P
    return eff / (eff + K_SAT)


def get_converged_mastery(student_id):
    """收敛掌握度 m* = 0.5 + (m-0.5)·c，用于显示/排序。"""
    m = get_mastery(student_id)
    c = get_confidence(student_id)
    return 0.5 + (m - 0.5) * c


def weakness_score(student_id):
    """薄弱分 = (1-m)·c，用于召回最弱知识点。"""
    m = get_mastery(student_id)
    c = get_confidence(student_id)
    return (1.0 - m) * c


def weakest_kps(student_id, scope_idx=None, k=5):
    """召回限定 scope 内收敛掌握度 m* 最低的 k 个知识点。返回 [(0基kp_idx, m*, name), ...]"""
    m_star = get_converged_mastery(student_id)
    cand = list(scope_idx) if scope_idx is not None else list(range(N_KP))
    cand.sort(key=lambda i: m_star[i])
    return [(i, float(m_star[i]), KP_NAMES[i]) for i in cand[:k]]


# ---------------------------------------------------------------------------
# 写：答题更新（7 步流水线编排）
# ---------------------------------------------------------------------------
def process_answer(student_id, kp_indices, weights, y, d, g, k=None, t=None,
                   source=None, task_id=None):
    """处理一道题的作答结果，对所有关联知识点执行 7 步更新。

    组装 apply_fn（循环调纯函数 step_update）→ 委托 repository.update 落库（事务+advisory lock）。
    """
    if t is None:
        t = datetime.now(timezone.utc).replace(tzinfo=None)
    if k is None:
        k = K

    def apply_fn(alpha, beta, m_peak, last_ts):
        for kp_idx, w in zip(kp_indices, weights):
            if w <= 0:
                continue
            step_update(alpha, beta, m_peak, last_ts, kp_idx, w, y, d, g, k, t)
        return alpha, beta, m_peak, last_ts

    return update(student_id, apply_fn, source=source, task_id=task_id)


def init_student(student_id, correct_counts=None, wrong_counts=None, last_ts=None):
    """初始化/重置一个学生的状态。

    无历史数据：alpha=beta=P=2, m_peak=0.5, last_ts=NULL
    有历史数据：alpha = C_correct + P, beta = C_wrong + P
    """
    if correct_counts is None:
        correct_counts = np.zeros(N_KP, dtype=np.float32)
    if wrong_counts is None:
        wrong_counts = np.zeros(N_KP, dtype=np.float32)

    correct_counts = _vec(correct_counts, default=0.0)
    wrong_counts = _vec(wrong_counts, default=0.0)

    alpha = correct_counts + P
    beta = wrong_counts + P
    m_init = alpha / (alpha + beta)
    m_peak = np.maximum(0.5, m_init).astype(np.float32)

    if last_ts is None:
        last_ts = [None] * N_KP
    else:
        last_ts = _ts_list(last_ts)

    def _write(_, __, ___, ____):
        return alpha, beta, m_peak, last_ts

    return update(student_id, _write, source='init', log_events=False)
