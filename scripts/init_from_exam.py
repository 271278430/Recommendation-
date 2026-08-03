#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用考试实际数据初始化学情状态表（Beta-Binomial 模型）。

数据源：data/九年级数学考试实际数据/20251016-.../ 下的 试题数据.xlsx + 小题得分表.xlsx

做法：
  1) 考试知识点 → 标准 612 知识点（归一化映射）
  2) 每学生按知识点聚合：
       α[kp] = Σ得分 + P      （有效正确证据）
       β[kp] = Σ(满分-得分) + P （有效错误证据）
     m_peak = max(0.5, α/(α+β))
     last_ts = 考试时间
  3) 批量 upsert 进 student_mastery
"""
import os
import sys
import json
import numpy as np
import openpyxl
import psycopg2
import psycopg2.extras
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from service.core.kp_registry import NAME2IDX, N_KP
from service.core.constants import P
from service.core.db import conn

EXAM = '/data/shanghui/Recommend_question/data/九年级数学考试实际数据/20251016-0453-58eb-b45c-67523a043649'

# 考试知识点名 → 标准 612 知识点名（归一化映射）
NORM = {
    '二次函数的定义': '二次函数的定义',
    '二次函数y=ax²+bx+c的图象和性质': '二次函数的图象与性质',
    '二次函数的图象和性质': '二次函数的图象与性质',
    'y=a（x-h）²+k的图象和性质': '二次函数的图象与性质',
    '二次函数的最值': '二次函数的最值',
    '二次函数图象与各项系数符号': '二次函数图象与系数a、b、c的关系',
    '二次函数图象的平移': '二次函数图象的平移',
    '二次函数与一元二次方程': '二次函数图象与一元二次方程的关系',
    '待定系数法求二次函数解析式': '用待定系数法确定二次函数表达式',
    '实际问题与二次函数': '二次函数的实际应用',
    '二次函数综合': '二次函数的综合应用',
    '反比例函数的定义': '反比例函数的定义',
    '反比例函数的性质': '反比例函数的图象与性质',
    '反比例函数与一次函数的综合': '反比例函数与一次函数的综合应用',
    '反比例函数、二次函数图象综合判断': '同一坐标系中函数图象的判断',
}

# ---- 1) 试题数据：题号 → (标准KP名, 满分) ----
wb = openpyxl.load_workbook(f'{EXAM}/试题数据.xlsx', read_only=True, data_only=True)
qs = {}
for r in wb['题库数据'].iter_rows(values_only=True):
    if r[0] is None or not str(r[0]).isdigit():
        continue
    qno = int(r[0])
    raw_kp = r[2]
    if raw_kp in NORM:
        qs[qno] = (NORM[raw_kp], float(r[3]))
wb.close()

# ---- 2) 小题列 → (KP列表, 该列满分)。题号11-14合并为"11-14"列 ----
col_map = {}
for qno, (kp_name, full_score) in qs.items():
    key = '11-14' if 11 <= qno <= 14 else str(qno)
    col_map.setdefault(key, [[], 0.0])
    col_map[key][0].append(kp_name)
    col_map[key][1] += full_score

# ---- 3) 解析学生得分，按 KP 聚合 ----
wb = openpyxl.load_workbook(f'{EXAM}/小题得分表.xlsx', read_only=True, data_only=True)
rows = list(wb['数学成绩表'].iter_rows(values_only=True))
wb.close()
hdr = [str(h) for h in rows[2][9:]]  # 小题列名

# 考试时间（用数据目录名推断或用固定值）
exam_ts = datetime(2025, 10, 16, 9, 0, 0)

students = []
for r in rows[4:]:
    sid = r[2]
    if sid is None or not str(sid).isdigit():
        continue
    sid = int(sid)

    # 按 KP 累加：total_score[kp_idx], total_full[kp_idx]
    acc_score = {}
    acc_full = {}
    subs = r[9:]

    for j, name in enumerate(hdr):
        if name not in col_map:
            continue
        raw = subs[j] if j < len(subs) else None
        try:
            sc = float(raw) if raw not in (None, '') else 0.0
        except Exception:
            sc = 0.0

        kp_names, full = col_map[name]
        # 该列的分数按 KP 数均分（同一列多个 KP 时均摊）
        share = full / len(kp_names) if len(kp_names) > 0 else full
        score_per_kp = sc * (share / full) if full > 0 else 0.0

        for kp_name in kp_names:
            ki = NAME2IDX.get(kp_name)
            if ki is None:
                continue
            acc_score[ki] = acc_score.get(ki, 0.0) + score_per_kp
            acc_full[ki] = acc_full.get(ki, 0.0) + share

    # 冷启动公式：α = score + P, β = (full - score) + P
    alpha = np.full(N_KP, P, dtype=np.float32)
    beta = np.full(N_KP, P, dtype=np.float32)
    m_peak = np.full(N_KP, 0.5, dtype=np.float32)
    last_ts = [None] * N_KP

    for ki in acc_score:
        s = acc_score[ki]
        f = acc_full[ki]
        alpha[ki] = s + P
        beta[ki] = (f - s) + P
        m_init = alpha[ki] / (alpha[ki] + beta[ki])
        m_peak[ki] = max(0.5, m_init)
        last_ts[ki] = exam_ts

    students.append((sid, alpha.tolist(), beta.tolist(), m_peak.tolist(), last_ts))

print(f'解析学生: {len(students)}')

# ---- 4) 写库 ----
with conn() as cc, cc.cursor() as cur:
    cur.execute('SELECT count(*) FROM student_mastery')
    before = cur.fetchone()[0]
    # 清空旧数据（初始化脚本，可接受）
    cur.execute('DELETE FROM student_mastery')
    psycopg2.extras.execute_values(cur,
        """INSERT INTO student_mastery(student_id, alpha, beta, m_peak, last_ts)
           VALUES %s
           ON CONFLICT (student_id) DO UPDATE
           SET alpha=EXCLUDED.alpha, beta=EXCLUDED.beta,
               m_peak=EXCLUDED.m_peak, last_ts=EXCLUDED.last_ts,
               updated_at=now()""",
        students)
    cc.commit()
print(f'库原 {before} 行 → 已写入 {len(students)} 行')

# ---- 5) 抽查 + 统计 ----
with conn() as cc, cc.cursor() as cur:
    cur.execute("SELECT count(*) FROM student_mastery")
    n = cur.fetchone()[0]
    print(f'库行数: {n}')

    # 抽查第一个学生：显示非冷启动 KP 的数量和平均 m
    cur.execute("SELECT alpha, beta FROM student_mastery LIMIT 1")
    r = cur.fetchone()
    if r:
        a = np.asarray(r[0], dtype=np.float32)
        b = np.asarray(r[1], dtype=np.float32)
        m = a / (a + b)
        active = np.sum(a > P + 0.01)  # alpha > P 表示有得分数据
        print(f'首个学生: 有数据 KP={active}, 平均 m={m.mean():.3f}')

    # 统计覆盖的知识点数
    cur.execute("""
        SELECT count(*) FROM (
            SELECT unnest(alpha) as a FROM student_mastery
        ) t WHERE a > 3
    """)
    print(f'(抽查) 总 alpha>3 的格子数:', cur.fetchone()[0])

print('完成。')
