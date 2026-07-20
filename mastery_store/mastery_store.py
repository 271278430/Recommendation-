#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学情状态库 Python 客户端。
模型：Beta 参数化 + IRT + 误差驱动加权更新（ELO 族）。每个(学生, 知识点)存 alpha/beta/m_peak/last_ts。
核心算法：7 步更新（遗忘→峰值衰减→IRT→误差→savings→加权融合→持久化）。
遗忘采用 α、β 对称向先验 P 衰减（思路二）：N 随之下降、m→0.5，为重学腾出空间。

并发安全：update() 走 "读旧→apply_fn→写" 单事务 + advisory lock 按 student_id 串行化。

依赖：pip install psycopg2-binary numpy
"""

import os
import json
import math
import numpy as np
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# ---------------------------------------------------------------------------
# 路径 & 知识点索引
# ---------------------------------------------------------------------------
ROOT = os.environ.get('PROJECT_ROOT', '/data/shanghui/Recommend_question')
KP_NAMES = json.load(open(f'{ROOT}/data/kg_graph/kg_index.json', encoding='utf-8'))
NAME2IDX = {n: i for i, n in enumerate(KP_NAMES)}
N_KP = len(KP_NAMES)  # 612

# ---------------------------------------------------------------------------
# 超参数（来自 mastery_update_mechanism.md §超参数总览，可运行时覆盖）
# ---------------------------------------------------------------------------
P = 2.0                # 先验伪计数
K = 5.0                # IRT 区分度 默认值（每题可独立传入）
TAU_MIN = 7.0          # firm=0.3（最不扎实）时的遗忘时间常数（天）
TAU_MAX = 90.0         # firm=1.0（最扎实）时的遗忘时间常数（天，可调 60~180）
TAU_PEAK = 180.0       # 峰值衰减常数（天，须 >> TAU_MAX，否则 savings 缺口打不开）
KAPPA = 1.0            # 基础伪观测权重
MU = 1.0               # evidence → 掌握度位移系数（已并入 m_obs，校准可调）
RHO = 1.0              # 重学节省系数
N_MAX = 100            # 总证据量上限
K_SAT = 8              # 置信度饱和常数（仅读时派生展示用，不进更新回路）

# 固定常量（不调）
FIRM_MIN = 0.3         # firm 下限 / τ 插值起点
FIRM_RANGE = 0.7       # firm 动态幅度 / τ 插值分母（=1−FIRM_MIN）
FIRM_SAT = 5.0         # firm 证据饱和常数
M_OBS_MIN = 0.001      # m_obs 下限（防 Beta 退化）
M_OBS_MAX = 0.999      # m_obs 上限
SAVINGS_CAP = 3.0      # savings 封顶倍数

# ---------------------------------------------------------------------------
# DB 连接
# ---------------------------------------------------------------------------
DB = dict(
    host=os.environ.get('PG_HOST', '127.0.0.1'),
    port=int(os.environ.get('POSTGRES_PORT') or os.environ.get('PG_PORT') or '5433'),
    dbname=os.environ.get('POSTGRES_DB') or os.environ.get('PG_DB') or 'mastery',
    user=os.environ.get('POSTGRES_USER') or os.environ.get('PG_USER') or 'mastery',
    password=os.environ.get('POSTGRES_PASSWORD') or os.environ.get('PG_PASSWORD') or '',
)
_LOCK_NS = 20260701


def conn():
    return psycopg2.connect(**DB)


# ---------------------------------------------------------------------------
# 数学工具
# ---------------------------------------------------------------------------
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
    # psycopg2 返回的 timestamptz 可能是带时区的，统一转 UTC
    out = []
    for x in lst[:n]:
        if x is not None:
            if hasattr(x, 'tzinfo') and x.tzinfo is not None:
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
# 读（无需锁）
# ---------------------------------------------------------------------------
def get_state(student_id):
    """读完整状态：(alpha, beta, m_peak, last_ts)。不存在返回冷启动默认。"""
    with conn() as c, c.cursor() as cur:
        cur.execute('SELECT alpha, beta, m_peak, last_ts FROM student_mastery WHERE student_id=%s',
                    (student_id,))
        r = cur.fetchone()
    return _row_to_state(r)


def get_mastery(student_id):
    """读掌握度 m = alpha/(alpha+beta)，0 基 np.float32[612]；不存在返回全 0.5。"""
    alpha, beta, _, _ = get_state(student_id)
    return alpha / (alpha + beta)


def get_confidence(student_id):
    """读置信度 c = (N-2P)/(N-2P+K)，0 基 np.float32[612]；不存在返回全 0。"""
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
# 写（事务内 RMW + advisory lock，并发安全）
# ---------------------------------------------------------------------------
def update(student_id, apply_fn, source=None, task_id=None, log_events=True):
    """
    在事务内锁定一行 → 读旧状态 → apply_fn 计算新状态 → 写回 → 记事件。

    apply_fn(alpha, beta, m_peak, last_ts) -> (new_alpha, new_beta, new_m_peak, new_last_ts)
    其中 alpha/beta/m_peak 是 np.float32[612]，last_ts 是 list[datetime|None]。
    apply_fn 应原地修改并返回（或返回新对象均可）。
    """
    with conn() as c, c.cursor() as cur:
        # 1. 按 student_id 加 advisory lock，串行化同一学生的并发更新
        cur.execute('SELECT pg_advisory_xact_lock(%s, %s)',
                    (_LOCK_NS, int(student_id) % 2147483647))

        # 2. SELECT ... FOR UPDATE，读当前状态
        cur.execute(
            'SELECT alpha, beta, m_peak, last_ts FROM student_mastery WHERE student_id=%s FOR UPDATE',
            (student_id,))
        r = cur.fetchone()
        old_a, old_b, old_pk, old_ts = _row_to_state(r)

        # 3. apply_fn 计算新状态
        new_a, new_b, new_pk, new_ts = apply_fn(
            old_a.copy(), old_b.copy(), old_pk.copy(), list(old_ts))

        new_a = _vec(new_a, default=float(P))
        new_b = _vec(new_b, default=float(P))
        new_pk = _vec(new_pk, default=0.5)

        # 4. Upsert
        cur.execute(
            """INSERT INTO student_mastery(student_id, alpha, beta, m_peak, last_ts)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (student_id) DO UPDATE
               SET alpha=EXCLUDED.alpha, beta=EXCLUDED.beta,
                   m_peak=EXCLUDED.m_peak, last_ts=EXCLUDED.last_ts,
                   updated_at=now()""",
            (student_id, new_a.tolist(), new_b.tolist(), new_pk.tolist(), new_ts))

        # 5. 记事件（追踪掌握度变化）
        if log_events:
            old_m = old_a / (old_a + old_b)
            new_m_val = new_a / (new_a + new_b)
            changed = []
            for i in range(N_KP):
                delta = float(new_m_val[i] - old_m[i])
                if abs(delta) > 1e-6:
                    changed.append((student_id, i + 1, float(old_m[i]), float(new_m_val[i]), delta))
            if changed:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO mastery_event(student_id, kp_idx, old_m, new_m, delta, source, task_id)
                       VALUES %s""",
                    [(sid, kp, om, nm, d, source, task_id) for sid, kp, om, nm, d in changed])

    return {'changed_cells': sum(1 for _ in (changed if log_events else [])) if log_events else 0}


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
    W = w * KAPPA * savings
    # 步骤 6：带 N 截断的加权融合
    N_eff = min(float(alpha[kp_idx] + beta[kp_idx]), N_MAX)
    m_obs = clamp(m_cur + MU * delta, M_OBS_MIN, M_OBS_MAX)
    m_target = (N_eff * m_cur + W * m_obs) / (N_eff + W)
    N_new = min(N_eff + W, N_MAX)
    alpha[kp_idx] = N_new * m_target
    beta[kp_idx] = N_new * (1.0 - m_target)
    # 步骤 7：后处理
    m_peak[kp_idx] = max(m_peak[kp_idx], m_target)
    last_ts[kp_idx] = t
    return p_pred


def process_answer(student_id, kp_indices, weights, y, d, g, k=None, t=None,
                   source=None, task_id=None):
    """
    处理一道题的作答结果，对所有关联知识点执行 7 步更新。

    参数
    ----
    student_id : int
    kp_indices : list[int]  知识点 0 基索引列表
    weights    : list[float]  对应权重（题内 sum=1）
    y          : float      实际得分率 [0,1]
    d          : float      题目难度 [0,1]
    g          : float      猜对底 [0,1]（非选择题=0）
    k          : float      题目区分度（默认取模块级 K=5）
    t          : datetime   答题时间（默认 now()）
    source     : str        来源标签（exam/homework/classwork）
    task_id    : str        任务 ID
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


# ---------------------------------------------------------------------------
# 冷启动初始化
# ---------------------------------------------------------------------------
def init_student(student_id, correct_counts=None, wrong_counts=None, last_ts=None):
    """
    初始化/重置一个学生的状态。

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


# ---------------------------------------------------------------------------
# 运维
# ---------------------------------------------------------------------------
def prune_events(days=90):
    """删除 days 天以前的变更日志。返回删除行数。"""
    with conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM mastery_event WHERE created_at < now() - make_interval(days => %s)",
                    (int(days),))
        return cur.rowcount


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    sid = 100001
    print(f'超参数: P={P}, τ∈[{TAU_MIN},{TAU_MAX}]d, τ_peak={TAU_PEAK}d, κ={KAPPA}, N_max={N_MAX}')
    print(f'冷启动 mastery 前 5: {get_mastery(sid)[:5]}')
    print(f'冷启动 收敛掌握度 前 5: {get_converged_mastery(sid)[:5]}')
    print(f'冷启动 薄弱分 前 5: {weakness_score(sid)[:5]}')

    # ---- 测试 7 步更新 ----
    # 场景：知识点"勾股定理"（找到其索引），做对一道中等难度非选择题
    idx = NAME2IDX.get('勾股定理')
    if idx is None:
        # fallback：用第一个知识点
        idx = 0
        print(f'注意："勾股定理"不在 kg_index 中，用索引 0 ({KP_NAMES[0]}) 代替')
    else:
        print(f'知识点: {KP_NAMES[idx]} (索引 {idx})')

    from datetime import timedelta
    t_now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 测试 1：首次做对一道中等难度非选择题
    print('\n--- 测试 1：首次做对中等题 ---')
    r = process_answer(sid, [idx], [1.0],
                       y=1.0, d=0.55, g=0.0, k=5.0, t=t_now,
                       source='test', task_id='t1')
    m = get_mastery(sid)
    alpha, beta, m_peak, last_ts = get_state(sid)
    print(f'  mastery[{idx}] = {m[idx]:.4f} (预期 ≈0.60)')
    print(f'  alpha={alpha[idx]:.2f}, beta={beta[idx]:.2f}, N={alpha[idx]+beta[idx]:.1f}')
    print(f'  m_peak={m_peak[idx]:.4f}, last_ts={last_ts[idx]}')
    print(f'  changed: {r}')

    # 测试 2：再做对 4 道中等题
    print('\n--- 测试 2：连续再做对 4 道中等题 ---')
    for i in range(4):
        process_answer(sid, [idx], [1.0],
                       y=1.0, d=0.55, g=0.0, k=5.0, t=t_now,
                       source='test', task_id=f't{i+2}')
    m = get_mastery(sid)
    alpha, beta, m_peak, _ = get_state(sid)
    print(f'  mastery[{idx}] = {m[idx]:.4f} (5题全对应 ≈0.78+)')
    print(f'  alpha={alpha[idx]:.2f}, beta={beta[idx]:.2f}, N={alpha[idx]+beta[idx]:.1f}')
    print(f'  m_peak={m_peak[idx]:.4f}')

    # 测试 3：遗忘模拟（把 last_ts 改到 30 天前，再做一题）
    print('\n--- 测试 3：遗忘 30 天后再做对 ---')
    alpha, beta, m_peak, last_ts = get_state(sid)
    old_ts = t_now - timedelta(days=30)
    last_ts[idx] = old_ts
    # 直接把旧时间写回（绕过 update，直接写库）
    with conn() as c, c.cursor() as cur:
        cur.execute(
            'INSERT INTO student_mastery(student_id, alpha, beta, m_peak, last_ts) '
            'VALUES (%s,%s,%s,%s,%s) ON CONFLICT (student_id) DO UPDATE '
            'SET alpha=EXCLUDED.alpha, beta=EXCLUDED.beta, '
            'm_peak=EXCLUDED.m_peak, last_ts=EXCLUDED.last_ts, updated_at=now()',
            (sid, alpha.tolist(), beta.tolist(), m_peak.tolist(), last_ts))
        c.commit()
    # 读回确认
    m_before = get_mastery(sid)[idx]
    print(f'  遗忘前 mastery={m_before:.4f}')
    # 再做一题
    r = process_answer(sid, [idx], [1.0],
                       y=1.0, d=0.55, g=0.0, k=5.0, t=t_now,
                       source='test', task_id='t_forget')
    m_after = get_mastery(sid)[idx]
    _, _, m_peak_after, _ = get_state(sid)
    print(f'  再做题后 mastery={m_after:.4f} (应因遗忘先降再升)')
    print(f'  m_peak={m_peak_after[idx]:.4f} (应 > mastery，触发 savings)')

    # 测试 4：薄弱知识点召回
    print('\n--- 测试 4：最弱知识点 ---')
    weak = weakest_kps(sid, k=3)
    for i, val, name in weak:
        print(f'  {name}: m*={val:.4f}')

    # 清理测试数据
    print('\n清理测试数据...')
    with conn() as c, c.cursor() as cur:
        cur.execute('DELETE FROM student_mastery WHERE student_id=%s', (sid,))
        cur.execute('DELETE FROM mastery_event WHERE student_id=%s', (sid,))
    print('自检完成。')
