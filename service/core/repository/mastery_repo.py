"""student_mastery / mastery_event 的数据访问。

update() 是事务安全的 RMW 骨架（advisory lock → SELECT FOR UPDATE → apply_fn → UPSERT →
写 event），整体搬迁自原 mastery_store.update，签名不变——并发安全靠这套，不能拆。
"""
import psycopg2.extras

from ..db import conn, LOCK_NS
from ..mastery_algo import _row_to_state, _vec
from ..constants import P
from ..kp_registry import N_KP


def get_state(student_id):
    """读完整状态：(alpha, beta, m_peak, last_ts)。不存在返回冷启动默认。"""
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT alpha, beta, m_peak, last_ts FROM student_mastery WHERE student_id=%s",
                    (student_id,))
        r = cur.fetchone()
    return _row_to_state(r)


def update(student_id, apply_fn, source=None, task_id=None, log_events=True):
    """
    在事务内锁定一行 → 读旧状态 → apply_fn 计算新状态 → 写回 → 记事件。

    apply_fn(alpha, beta, m_peak, last_ts) -> (new_alpha, new_beta, new_m_peak, new_last_ts)
    其中 alpha/beta/m_peak 是 np.float32[N_KP]，last_ts 是 list[datetime|None]。
    apply_fn 应原地修改并返回（或返回新对象均可）。
    """
    with conn() as c, c.cursor() as cur:
        # 1. 按 student_id 加 advisory lock，串行化同一学生的并发更新
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_NS, int(student_id) % 2147483647))

        # 2. SELECT ... FOR UPDATE，读当前状态
        cur.execute(
            "SELECT alpha, beta, m_peak, last_ts FROM student_mastery WHERE student_id=%s FOR UPDATE",
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
        changed = []
        if log_events:
            old_m = old_a / (old_a + old_b)
            new_m_val = new_a / (new_a + new_b)
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

    return {"changed_cells": len(changed)}


def prune_events(days=90):
    """删除 days 天以前的变更日志。返回删除行数。"""
    with conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM mastery_event WHERE created_at < now() - make_interval(days => %s)",
                    (int(days),))
        return cur.rowcount


def list_students():
    """所有有掌握度记录的 student_id（原 api/student.py 的裸 SQL 收敛到此）。"""
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT student_id FROM student_mastery ORDER BY student_id")
        return [r[0] for r in cur.fetchall()]
