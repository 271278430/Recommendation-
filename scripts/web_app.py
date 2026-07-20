#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学情状态更新机制 — 可视化测试界面。
启动: python web_app.py  →  浏览器打开 http://127.0.0.1:5000
"""
import os
import sys
import json
import math
import random
import openpyxl
import numpy as np
from datetime import datetime, timezone
from collections import defaultdict, Counter
from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(__file__))
from mastery_store.mastery_store import (
    NAME2IDX, N_KP, KP_NAMES,
    get_mastery, get_converged_mastery, get_confidence,
    get_state, process_answer, init_student, conn,
    step_update, forget_decay,
    P, K, TAU_MIN, TAU_MAX, TAU_PEAK, KAPPA, MU, RHO, N_MAX, K_SAT,
    clamp, sigmoid,
    FIRM_MIN, FIRM_RANGE, FIRM_SAT, SAVINGS_CAP, M_OBS_MIN, M_OBS_MAX,
)

app = Flask(__name__)
ROOT = os.environ.get('PROJECT_ROOT', '/data/shanghui/Recommend_question')

# ---------------------------------------------------------------------------
# 启动时加载数据
# ---------------------------------------------------------------------------
# 知识点图
with open(f'{ROOT}/data/kg_graph/nodes.json') as f:
    KG_NODES = json.load(f)
with open(f'{ROOT}/data/kg_graph/prereq_edges.json') as f:
    KG_EDGES = json.load(f)

# 模块列表（按首次出现顺序）
_seen = set()
MODULES = []
for n in KG_NODES:
    if n['module'] not in _seen:
        _seen.add(n['module'])
        MODULES.append(n['module'])

# 题库索引：KP名 → [(qid, difficulty_label, quesType, stem_preview), ...]
print('构建题库索引...')
DIFF_MAP = {'容易': 0.15, '较易': 0.35, '适中': 0.55, '较难': 0.75, '困难': 0.90}
kp_questions = defaultdict(list)
with open(f'{ROOT}/data/初中_九年级.jsonl') as f:
    for line in f:
        q = json.loads(line)
        diff = DIFF_MAP.get(q.get('difficulty', '适中'), 0.55)
        for kp in q.get('kgPoints', []):
            kp_questions[kp['name']].append({
                'qid': q['_id'],
                'stem': q['stem'][:120],
                'difficulty_label': q.get('difficulty', '适中'),
                'difficulty': diff,
                'quesType': q.get('quesType', ''),
                'options': q.get('options', []),
                'answer': q.get('answer', [''])[0][:200] if q.get('answer') else '',
            })
# 只保留在 kg_index 中的 KP
KP_QUESTIONS = {}
for name in NAME2IDX:
    if name in kp_questions:
        KP_QUESTIONS[name] = kp_questions[name]
print(f'  题库索引: {len(KP_QUESTIONS)} 个知识点有题目, 共 {sum(len(v) for v in KP_QUESTIONS.values())} 题')

# ---------------------------------------------------------------------------
# 真实考试数据（每生每题真实得分 + 经验难度 d），用于批量机制测试
# ---------------------------------------------------------------------------
EXAM_DIR = f'{ROOT}/data/九年级数学考试实际数据/20251016-0453-58eb-b45c-67523a043649'
# 考试知识点名 → 标准 612 知识点名（与 init_from_exam.NORM 一致；init_from_exam 无 __main__ 守卫，不能 import）
_EXAM_NORM = {
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
_G_MAP = {'单选题': 0.25, '多选题': 0.10, '判断题': 0.50}


def load_exam_records():
    """解析真实考试数据。返回 (records, students)：
      records[sid] = [(col_key, kp_idx, y, d, g), ...] 按题号顺序
      d = 1 − 全体平均得分率（文档 V1 的经验难度，天然校准）。
    11~14 题合并为一列（解答题分小问），按知识点拆成多条事件。"""
    # 1) 试题数据：题号 → (题型, 标准kp_idx, 满分)
    wb = openpyxl.load_workbook(f'{EXAM_DIR}/试题数据.xlsx', read_only=True, data_only=True)
    qmeta = {}
    for r in wb['题库数据'].iter_rows(values_only=True):
        if r[0] is None or not str(r[0]).isdigit():
            continue
        qno = int(r[0]); qt = str(r[1] or ''); raw_kp = r[2]; full = float(r[3] or 0)
        kp_name = _EXAM_NORM.get(raw_kp, raw_kp)
        ki = NAME2IDX.get(kp_name)
        if ki is None or full <= 0:
            continue
        qmeta[qno] = (qt, ki, full)
    wb.close()

    # 2) 小题列 → {kps, full, qts}；11~14 合并为 '11-14'
    col_map = {}
    for qno, (qt, ki, full) in qmeta.items():
        key = '11-14' if 11 <= qno <= 14 else str(qno)
        m = col_map.setdefault(key, {'kps': [], 'full': 0.0, 'qts': set()})
        m['kps'].append(ki); m['full'] += full; m['qts'].add(qt)

    # 3) 读全部学生行
    wb = openpyxl.load_workbook(f'{EXAM_DIR}/小题得分表.xlsx', read_only=True, data_only=True)
    rows = list(wb['数学成绩表'].iter_rows(values_only=True))
    wb.close()
    hdr = [str(h) for h in rows[2][9:]]
    data_rows = [r for r in rows[4:] if r[2] is not None and str(r[2]).isdigit()]

    # 4) 每列经验 d = 1 − 平均得分率
    col_d = {}
    for key, m in col_map.items():
        if key not in hdr:
            continue
        j = hdr.index(key); full = m['full']; rates = []
        for r in data_rows:
            raw = r[9 + j] if 9 + j < len(r) else None
            try:
                sc = float(raw) if raw not in (None, '') else 0.0
            except Exception:
                sc = 0.0
            rates.append(sc / full if full > 0 else 0.0)
        mean_rate = sum(rates) / len(rates) if rates else 0.5
        col_d[key] = 1.0 - mean_rate

    # 5) 每生记录（按列顺序；多 KP 列按知识点展开为多条事件）
    records = {}
    for r in data_rows:
        sid = int(r[2]); recs = []
        for key in [k for k in col_map if k in hdr]:
            m = col_map[key]; j = hdr.index(key)
            raw = r[9 + j] if 9 + j < len(r) else None
            try:
                sc = float(raw) if raw not in (None, '') else 0.0
            except Exception:
                sc = 0.0
            y = sc / m['full'] if m['full'] > 0 else 0.0
            d = col_d.get(key, 0.5)
            qts = m['qts']
            g = _G_MAP.get(next(iter(qts)), 0.0) if len(qts) == 1 else 0.0
            for ki in m['kps']:
                recs.append((key, ki, y, d, g))
        if recs:
            records[sid] = recs
    return records, sorted(records.keys())


print('加载真实考试数据...')
try:
    EXAM_RECORDS, EXAM_STUDENTS = load_exam_records()
    _exam_kps = sorted({ki for recs in EXAM_RECORDS.values() for _, ki, _, _, _ in recs})
    print(f'  考试数据: {len(EXAM_STUDENTS)} 学生, 覆盖 {len(_exam_kps)} 个知识点')
except Exception as e:
    print(f'  [警告] 考试数据加载失败: {e}')
    EXAM_RECORDS, EXAM_STUDENTS = {}, []

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return HTML


@app.route('/api/ping')
def ping():
    return jsonify({'ok': True})


@app.route('/api/students')
def list_students():
    """列出所有学生"""
    with conn() as c, c.cursor() as cur:
        cur.execute('SELECT student_id FROM student_mastery ORDER BY student_id')
        ids = [r[0] for r in cur.fetchall()]
    return jsonify(ids)


@app.route('/api/student/<int:sid>')
def get_student(sid):
    """获取学生完整状态"""
    alpha, beta, m_peak, last_ts = get_state(sid)
    m = alpha / (alpha + beta)
    m_star = get_converged_mastery(sid)
    c = get_confidence(sid)

    # 每个 KP 的状态
    kps = []
    for i in range(N_KP):
        has_data = alpha[i] > P + 0.01
        kps.append({
            'idx': i,
            'name': KP_NAMES[i],
            'm': round(float(m[i]), 4),
            'm_star': round(float(m_star[i]), 4),
            'c': round(float(c[i]), 4),
            'alpha': round(float(alpha[i]), 2),
            'beta': round(float(beta[i]), 2),
            'N': round(float(alpha[i] + beta[i]), 1),
            'm_peak': round(float(m_peak[i]), 4),
            'has_data': bool(has_data),
            'last_ts': last_ts[i].isoformat() if last_ts[i] else None,
        })
    return jsonify(kps)


@app.route('/api/graph')
def get_graph():
    """返回知识图谱数据（节点 + 边）"""
    nodes = []
    for n in KG_NODES:
        nodes.append({
            'idx': n['idx'],
            'name': n['name'],
            'module': n['module'],
            'freq': n.get('freq', 0),
            'avg_difficulty': n.get('avg_difficulty', 3),
        })
    edges = []
    for e in KG_EDGES:
        edges.append({
            'source': e['i'],
            'target': e['j'],
            'type': e['type'],  # '间'=跨模块, '内'=模块内
        })
    return jsonify({'nodes': nodes, 'edges': edges, 'modules': MODULES})


@app.route('/api/recommend')
def recommend():
    """为指定学生+知识点推荐一道难度匹配的题目"""
    sid = int(request.args.get('student_id'))
    kp_idx = int(request.args.get('kp_idx'))
    kp_name = request.args.get('kp_name', '')

    # 获取学生当前掌握度
    m = get_mastery(sid)[kp_idx]

    # 难度匹配策略
    if m < 0.4:
        target_diffs = [0.15, 0.35]
    elif m < 0.6:
        target_diffs = [0.35, 0.55, 0.15]
    elif m < 0.8:
        target_diffs = [0.55, 0.75, 0.35]
    else:
        target_diffs = [0.75, 0.90, 0.55]

    # 找题
    questions = KP_QUESTIONS.get(kp_name, [])
    if not questions:
        return jsonify({'error': f'知识点 "{kp_name}" 暂无题目', 'm': round(float(m), 4)})

    # 按难度匹配度排序
    scored = []
    for q in questions:
        diff_dist = min(abs(q['difficulty'] - td) for td in target_diffs)
        scored.append((diff_dist, q))
    scored.sort(key=lambda x: x[0])

    # 取前 10 道最佳匹配，随机选一道
    top = scored[:min(10, len(scored))]
    q = random.choice(top)[1]

    # 题型 → g
    g_map = {'单选题': 0.25, '多选题': 0.10, '判断题': 0.50}
    g = g_map.get(q['quesType'], 0.0)

    return jsonify({
        'qid': q['qid'],
        'stem': q['stem'],
        'quesType': q['quesType'],
        'difficulty_label': q['difficulty_label'],
        'd': q['difficulty'],
        'g': g,
        'k': 5.0,
        'kp_name': kp_name,
        'kp_idx': kp_idx,
        'm_before': round(float(m), 4),
        'options': q['options'],
        'answer_preview': q['answer'][:200] if q['answer'] else '',
    })


@app.route('/api/answer', methods=['POST'])
def answer():
    """提交作答，执行 7 步更新，返回变化"""
    data = request.json
    sid = int(data['student_id'])
    kp_idx = int(data['kp_idx'])
    y = float(data['y'])          # 得分率 0~1
    d = float(data['d'])
    g = float(data['g'])
    k = float(data.get('k', 5.0))
    source = data.get('source', 'manual_test')

    # 更新前
    m_before = float(get_mastery(sid)[kp_idx])
    alpha_before, beta_before, pk_before, _ = get_state(sid)
    a_before = float(alpha_before[kp_idx])
    b_before = float(beta_before[kp_idx])
    N_before = float(a_before + b_before)
    pk_before_val = float(pk_before[kp_idx])

    # 执行更新
    process_answer(sid, [kp_idx], [1.0], y=y, d=d, g=g, k=k,
                   source=source, task_id=f'web_{datetime.now().isoformat()}')

    # 更新后
    m_after = float(get_mastery(sid)[kp_idx])
    alpha_after, beta_after, pk_after, ts_after = get_state(sid)
    a_after = float(alpha_after[kp_idx])
    b_after = float(beta_after[kp_idx])
    N_after = float(a_after + b_after)
    pk_after_val = float(pk_after[kp_idx])

    # 展示前后对比
    change_sign = '+' if m_after >= m_before else ''
    delta_class = 'delta-positive' if m_after >= m_before else 'delta-negative'

    return jsonify({
        'm_before': round(m_before, 4),
        'm_after': round(m_after, 4),
        'delta_m': round(m_after - m_before, 4),
        'N_before': round(N_before, 1),
        'N_after': round(N_after, 1),
        'alpha_before': round(a_before, 2),
        'alpha_after': round(a_after, 2),
        'beta_before': round(b_before, 2),
        'beta_after': round(b_after, 2),
        'm_peak_before': round(pk_before_val, 4),
        'm_peak_after': round(pk_after_val, 4),
        'y': float(y), 'd': float(d), 'g': float(g), 'k': float(k),
    })


@app.route('/api/reset', methods=['POST'])
def reset():
    """重置学生的某个知识点为冷启动"""
    data = request.json
    sid = int(data['student_id'])
    kp_idx = int(data.get('kp_idx', -1))

    alpha, beta, m_peak, last_ts = get_state(sid)
    if kp_idx >= 0:
        alpha[kp_idx] = P
        beta[kp_idx] = P
        m_peak[kp_idx] = 0.5
        last_ts[kp_idx] = None
    else:
        alpha[:] = P
        beta[:] = P
        m_peak[:] = 0.5
        last_ts[:] = None

    from mastery_store import update as ms_update
    def _write(_, __, ___, ____):
        return alpha, beta, m_peak, last_ts
    ms_update(sid, _write, source='reset', log_events=False)
    return jsonify({'ok': True})


@app.route('/api/forget', methods=['POST'])
def apply_forget():
    """模拟遗忘：将指定 KP 的 last_ts 回退 days 天，直接写 DB"""
    data = request.json
    sid = int(data['student_id'])
    kp_idx = int(data['kp_idx'])
    days = float(data.get('days', 30))

    from datetime import timedelta
    alpha, beta, m_peak, last_ts = get_state(sid)
    cur_ts = last_ts[kp_idx]
    if cur_ts is None:
        return jsonify({'error': '该知识点无练习记录，无法模拟遗忘。请先作答一次。'})

    old_m = float(alpha[kp_idx] / (alpha[kp_idx] + beta[kp_idx]))
    new_ts = cur_ts - timedelta(days=days)
    last_ts[kp_idx] = new_ts

    # 直接写 DB（绕过 update 的事务抽象，确保遗忘模拟一定持久化）
    from mastery_store import conn as db_conn
    with db_conn() as c, c.cursor() as cur:
        cur.execute(
            'INSERT INTO student_mastery(student_id, alpha, beta, m_peak, last_ts) '
            'VALUES (%s,%s,%s,%s,%s) ON CONFLICT (student_id) DO UPDATE '
            'SET last_ts=EXCLUDED.last_ts, updated_at=now()',
            (sid, alpha.tolist(), beta.tolist(), m_peak.tolist(), last_ts))
        c.commit()

    # 验证
    _, _, _, verify_ts = get_state(sid)
    verify_days = (cur_ts - verify_ts[kp_idx]).total_seconds() / 86400 if verify_ts[kp_idx] else 0

    return jsonify({
        'ok': True,
        'days': days,
        'verified_days_back': round(verify_days, 1),
        'old_last_ts': cur_ts.isoformat(),
        'new_last_ts': new_ts.isoformat(),
        'm_before': round(old_m, 4),
        'hint': f'last_ts 已回退 {days} 天（验证通过）。下次作答将触发遗忘衰减。'
    })


@app.route('/api/forget-preview')
def preview_forget():
    """预览遗忘效果：计算若过去 days 天后 m/m_peak/置信度的变化（不修改 DB）"""
    sid = int(request.args.get('student_id'))
    kp_idx = int(request.args.get('kp_idx'))
    days = float(request.args.get('days', 30))

    from datetime import timedelta
    import math as _math

    alpha, beta, m_peak, last_ts = get_state(sid)

    a = float(alpha[kp_idx])
    b = float(beta[kp_idx])
    pk = float(m_peak[kp_idx])
    cur_ts = last_ts[kp_idx]

    if cur_ts is None:
        return jsonify({
            'error': '该知识点无练习记录',
            'm_now': round(a/(a+b), 4),
            'has_data': False,
        })

    # firm / τ / r（仅用于展示读数）
    dt = days
    m_cur = a / (a + b)
    n_cur = a + b
    eff = n_cur - 2 * P
    firm = clamp(FIRM_MIN + FIRM_RANGE * m_cur * eff / (eff + FIRM_SAT), FIRM_MIN, 1.0)
    tau_eff = TAU_MIN + (TAU_MAX - TAU_MIN) * (firm - FIRM_MIN) / FIRM_RANGE
    r = _math.exp(-dt / tau_eff)

    # 整体衰减快照（复用 forget_decay，与机制同源）
    ca = alpha.copy(); cb = beta.copy(); cp = m_peak.copy()
    forget_decay(ca, cb, cp, kp_idx, dt)
    m_decay = float(ca[kp_idx] / (ca[kp_idx] + cb[kp_idx]))
    N_decay = float(ca[kp_idx] + cb[kp_idx])
    pk_decay = float(cp[kp_idx])

    # 逐日曲线（每个采样日从原始状态独立计算 = 解析解）
    curve = []
    for day in range(int(days) + 1):
        if day % max(1, int(days / 30)) == 0 or day == int(days):  # 最多30个采样点
            da = alpha.copy(); db = beta.copy(); dp = m_peak.copy()
            forget_decay(da, db, dp, kp_idx, float(day))
            curve.append({
                'day': day,
                'm': round(float(da[kp_idx] / (da[kp_idx] + db[kp_idx])), 4),
                'N': round(float(da[kp_idx] + db[kp_idx]), 1),
                'm_peak': round(float(dp[kp_idx]), 4),
            })

    return jsonify({
        'has_data': True,
        'm_now': round(m_cur, 4),
        'N_now': round(n_cur, 1),
        'm_peak_now': round(pk, 4),
        'firm': round(firm, 4),
        'tau_eff': round(tau_eff, 1),
        'r': round(r, 4),
        'm_after': round(m_decay, 4),
        'N_after': round(N_decay, 1),
        'm_peak_after': round(pk_decay, 4),
        'delta_m': round(m_decay - m_cur, 4),
        'delta_N': round(N_decay - n_cur, 1),
        'curve': curve,
    })


# ---------------------------------------------------------------------------
# 批量机制测试（用真实数据画更新/遗忘变化曲线，判断机制是否合理）
# ---------------------------------------------------------------------------
@app.route('/api/sim/exam')
def sim_exam_meta():
    """真实考试数据：不传 student_id 返回学生列表；传了返回该生涉及的 KP 列表。"""
    sid = request.args.get('student_id', type=int)
    if sid is None:
        return jsonify({'students': EXAM_STUDENTS})
    recs = EXAM_RECORDS.get(sid, [])
    c = Counter(ki for _, ki, _, _, _ in recs)
    kps = [{'idx': ki, 'name': KP_NAMES[ki], 'n': n} for ki, n in c.most_common()]
    return jsonify({'student_id': sid, 'kps': kps, 'n_events': len(recs)})


@app.route('/api/sim/update', methods=['POST'])
def sim_update():
    """批量更新机制测试：真实考试回放 / 受控实验。
    返回 m / N / m_peak / IRT预测p / 真实y / 难度d 随题序的曲线。全程内存、不写库。"""
    data = request.json or {}
    mode = data.get('mode', 'real')
    t0 = datetime(2025, 10, 16, 9, 0, 0)  # 统一时间戳 → 题间 dt=0，隔离更新机制（不触发遗忘）

    alpha = np.full(N_KP, P, dtype=np.float32)
    beta = np.full(N_KP, P, dtype=np.float32)
    m_peak = np.full(N_KP, 0.5, dtype=np.float32)
    last_ts = [None] * N_KP

    events = []  # (y, d, g)
    kp_idx, kp_name = -1, ''

    if mode == 'real':
        sid = int(data['student_id'])
        kp_idx = int(data.get('kp_idx', -1))
        recs = EXAM_RECORDS.get(sid, [])
        if kp_idx < 0:
            c = Counter(ki for _, ki, _, _, _ in recs)
            if not c:
                return jsonify({'error': '该学生无考试数据'})
            kp_idx = c.most_common(1)[0][0]
        events = [(y, d, g) for _, ki, y, d, g in recs if ki == kp_idx]
        kp_name = KP_NAMES[kp_idx]
    else:
        kp_idx = int(data['kp_idx'])
        kp_name = KP_NAMES[kp_idx]
        scenario = data.get('scenario', 'all_correct')
        n = int(data.get('n', 15))
        qs = KP_QUESTIONS.get(kp_name, [])
        if not qs:
            return jsonify({'error': f'知识点 "{kp_name}" 题库无题'})
        if scenario == 'easy_to_hard':
            # 跨难度均匀取样、按易→难顺序：演示"难题做对 δ 更大"
            srt = sorted(qs, key=lambda q: q['difficulty'])
            step = max(1, len(srt) // n)
            picked = srt[::step][:n]
        else:
            picked = random.sample(qs, min(n, len(qs)))
        for q in picked:
            d = q['difficulty']; g = _G_MAP.get(q.get('quesType', ''), 0.0)
            if scenario == 'all_wrong':
                y = 0.0
            elif scenario == 'irt':
                m_now = float(alpha[kp_idx] / (alpha[kp_idx] + beta[kp_idx]))
                p = g + (1 - g) * sigmoid(5.0 * (m_now - d))
                y = 1.0 if random.random() < p else 0.0
            else:  # all_correct / easy_to_hard
                y = 1.0
            events.append((y, d, g))

    curve = []
    for (y, d, g) in events:
        p_pred = step_update(alpha, beta, m_peak, last_ts, kp_idx, 1.0, y, d, g, 5.0, t0)
        m = float(alpha[kp_idx] / (alpha[kp_idx] + beta[kp_idx]))
        N = float(alpha[kp_idx] + beta[kp_idx])
        curve.append({'step': len(curve) + 1, 'm': round(m, 4), 'N': round(N, 2),
                      'm_peak': round(float(m_peak[kp_idx]), 4), 'p': round(float(p_pred), 4),
                      'y': round(float(y), 3), 'd': round(float(d), 3)})

    return jsonify({'mode': mode, 'kp_idx': kp_idx, 'kp_name': kp_name,
                    'n_events': len(events), 'curve': curve})


@app.route('/api/sim/forget')
def sim_forget():
    """批量遗忘机制测试：从该生状态（DB 或考试回放）出发，算 0..days 天衰减曲线。
    forget_decay 为闭式衰减，每个采样日从原始状态独立计算 → 曲线即解析解。纯计算，不写库。"""
    sid = request.args.get('student_id', type=int)
    days = request.args.get('days', 180, type=int)
    kp_param = request.args.get('kp_idxs', 'practiced')
    seed = request.args.get('seed', 'exam')

    if seed == 'exam':
        # 内存回放真实考试 → 得到"学习后"状态，再衰减（自包含，不依赖 DB）
        alpha0 = np.full(N_KP, P, dtype=np.float32)
        beta0 = np.full(N_KP, P, dtype=np.float32)
        m_peak0 = np.full(N_KP, 0.5, dtype=np.float32)
        last_ts0 = [None] * N_KP
        t0 = datetime(2025, 10, 16, 9, 0, 0)
        for _, ki, y, d, g in EXAM_RECORDS.get(sid, []):
            step_update(alpha0, beta0, m_peak0, last_ts0, ki, 1.0, y, d, g, 5.0, t0)
    else:
        alpha0, beta0, m_peak0, _ = get_state(sid)

    if kp_param == 'practiced':
        kp_list = [i for i in range(N_KP) if float(alpha0[i]) > P + 0.01]
    else:
        kp_list = [int(x) for x in kp_param.split(',') if x.strip() != '']
    if not kp_list:
        return jsonify({'error': '该生无已练习知识点'})

    samples = list(range(0, days + 1, max(1, days // 60)))
    kp_info, per_kp = [], {}
    for ki in kp_list:
        a0 = float(alpha0[ki]); b0 = float(beta0[ki])
        m0 = a0 / (a0 + b0); n0 = a0 + b0
        eff = n0 - 2 * P
        firm = clamp(FIRM_MIN + FIRM_RANGE * m0 * eff / (eff + FIRM_SAT), FIRM_MIN, 1.0)
        tau = TAU_MIN + (TAU_MAX - TAU_MIN) * (firm - FIRM_MIN) / FIRM_RANGE
        kp_info.append({'idx': ki, 'name': KP_NAMES[ki], 'm0': round(m0, 4),
                        'firm': round(float(firm), 3), 'tau': round(float(tau), 1)})
        series = []
        for day in samples:
            a = alpha0.copy(); b = beta0.copy(); pk = m_peak0.copy()
            forget_decay(a, b, pk, ki, float(day))
            series.append({'day': day,
                           'm': round(float(a[ki] / (a[ki] + b[ki])), 4),
                           'N': round(float(a[ki] + b[ki]), 2),
                           'm_peak': round(float(pk[ki]), 4)})
        per_kp[ki] = series

    avg_curve = []
    for di, day in enumerate(samples):
        ms = [per_kp[ki][di]['m'] for ki in kp_list]
        Ns = [per_kp[ki][di]['N'] for ki in kp_list]
        mps = [per_kp[ki][di]['m_peak'] for ki in kp_list]
        avg_curve.append({'day': day, 'm': round(sum(ms) / len(ms), 4),
                          'N': round(sum(Ns) / len(Ns), 2),
                          'm_peak': round(sum(mps) / len(mps), 4)})

    order = sorted(kp_list, key=lambda ki: per_kp[ki][0]['m'])
    reps = [order[-1]] + ([order[0]] if len(order) > 1 else []) + \
           ([order[len(order) // 2]] if len(order) > 2 else [])
    reps = [ki for ki in reps if ki is not None]
    return jsonify({'n_kps': len(kp_list), 'kp_info': kp_info, 'avg_curve': avg_curve,
                    'rep_kps': reps,
                    'per_kp': {str(ki): per_kp[ki] for ki in reps}})


# ---------------------------------------------------------------------------
# HTML 界面
# ---------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>学情状态更新机制 — 可视化测试</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#1a1a2e;color:#e0e0e0;height:100vh;display:flex}
#graph-panel{flex:1;min-width:0;position:relative}
#graph{width:100%;height:100%}
#side-panel{width:420px;background:#16213e;display:flex;flex-direction:column;overflow-y:auto;border-left:2px solid #0f3460}
.panel-section{padding:16px;border-bottom:1px solid #0f3460}
.panel-title{font-size:14px;font-weight:600;color:#e94560;margin-bottom:10px}
select,button,input{padding:8px 12px;border:1px solid #0f3460;border-radius:6px;background:#1a1a2e;color:#e0e0e0;font-size:13px;cursor:pointer}
button{background:#e94560;border:none;font-weight:600;transition:background .2s}
button:hover{background:#c23152}
button.secondary{background:#0f3460}
button.success{background:#2ecc71}
button.danger{background:#e74c3c}
.btn-row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.btn-row button{flex:1;min-width:80px}
#kp-info{font-size:13px;line-height:1.8}
#kp-info span{color:#e94560;font-weight:600}
.stat-row{display:flex;justify-content:space-between;padding:4px 0;font-size:13px;border-bottom:1px solid rgba(255,255,255,0.05)}
.stat-label{color:#999}
.stat-value{font-weight:600;font-family:monospace}
.mastery-bar{height:8px;border-radius:4px;background:#0f3460;margin:6px 0;overflow:hidden}
.mastery-bar-fill{height:100%;border-radius:4px;transition:width .5s}
.delta-positive{color:#2ecc71}
.delta-negative{color:#e74c3c}
#question-stem{font-size:14px;line-height:1.6;background:#1a1a2e;padding:12px;border-radius:8px;margin:8px 0;max-height:200px;overflow-y:auto}
#history{max-height:200px;overflow-y:auto;font-size:12px}
.history-item{padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.03);font-family:monospace}
.module-toggle{cursor:pointer;padding:2px 6px;border-radius:4px;font-size:11px;margin:2px;display:inline-block;background:#0f3460}
.module-toggle.active{background:#e94560}
</style>
</head>
<body>

<div id="graph-panel"><div id="graph"></div></div>

<div id="side-panel">
  <div class="panel-section">
    <div class="panel-title">👤 学生</div>
    <select id="student-select" onchange="selectStudent()"><option>加载中...</option></select>
    <button class="secondary" onclick="addTestStudent()" style="margin-left:8px">+ 新增测试学生</button>
  </div>

  <div class="panel-section" id="kp-detail" style="display:none">
    <div class="panel-title">📌 知识点详情</div>
    <div id="kp-info"></div>
    <div id="mastery-gauge"></div>
    <div style="margin-top:10px">
      <div class="panel-title">⏳ 遗忘模拟</div>
      <div style="display:flex;gap:6px;align-items:center;margin:8px 0">
        <input id="forget-days" type="number" value="30" min="1" max="365" style="width:70px" title="距上次练习的天数">
        <span style="font-size:12px;color:#999">天不练</span>
        <button onclick="previewForget()" style="font-size:12px;padding:6px 10px">🔍 预览</button>
        <button onclick="applyForget()" style="font-size:12px;padding:6px 10px;background:#e67e22">⚡ 应用</button>
        <button onclick="resetKP()" style="font-size:12px;padding:6px 10px;background:#7f8c8d">🔄 重置KP</button>
      </div>
      <div id="forget-preview" style="font-size:12px;color:#999"></div>
      <div id="forget-chart" style="width:100%;height:150px;display:none"></div>
    </div>
  </div>

  <div class="panel-section" id="question-panel" style="display:none">
    <div class="panel-title">📋 推荐题目</div>
    <div id="question-meta"></div>
    <div id="question-stem"></div>
    <div class="panel-title" style="margin-top:12px">✏️ 作答（模拟）</div>
    <div class="btn-row">
      <button class="success" onclick="submitAnswer(1.0)">✅ 做对 (100%)</button>
      <button class="danger" onclick="submitAnswer(0.0)">❌ 做错 (0%)</button>
    </div>
    <div class="btn-row">
      <button onclick="submitAnswer(0.75)">📊 75%</button>
      <button onclick="submitAnswer(0.5)">📊 50%</button>
      <button onclick="submitAnswer(0.25)">📊 25%</button>
    </div>
  </div>

  <div class="panel-section" id="result-panel" style="display:none">
    <div class="panel-title">📊 掌握度变化</div>
    <div id="result-content"></div>
  </div>

  <div class="panel-section">
    <div class="panel-title">🧪 机制批量测试</div>
    <div style="font-size:11px;color:#999;margin-bottom:8px">用真实数据画更新/遗忘曲线，判断机制是否合理、是否符合学情</div>

    <div class="panel-title" style="font-size:12px;color:#f39c12">① 更新机制曲线（随题序）</div>
    <select id="sim-mode" onchange="onSimModeChange()" style="width:100%">
      <option value="real">真实考试学生回放（真实得分）</option>
      <option value="controlled">受控实验（题库取样）</option>
    </select>
    <select id="sim-student" onchange="loadExamKPs()" style="width:100%;margin-top:6px"></select>
    <select id="sim-kp" style="width:100%;margin-top:6px"></select>
    <div id="sim-controlled-ctl" style="display:none">
      <select id="sim-scenario" style="width:100%;margin-top:6px">
        <option value="all_correct">情景：全部做对</option>
        <option value="all_wrong">情景：全部做错</option>
        <option value="easy_to_hard">情景：由易到难全对</option>
        <option value="irt">情景：按 IRT 概率作答</option>
      </select>
      <input id="sim-n" type="number" value="15" min="1" max="50" style="width:100%;margin-top:6px">
    </div>
    <button onclick="runSimUpdate()" style="width:100%;margin-top:6px">▶ 生成更新曲线</button>
    <div id="sim-update-chart" style="width:100%;height:230px;margin-top:6px"></div>
    <div id="sim-update-info" style="font-size:11px;color:#999;margin-top:4px"></div>

    <div class="panel-title" style="font-size:12px;color:#f39c12;margin-top:12px">② 遗忘机制曲线（随天数）</div>
    <button onclick="runSimForget()" style="width:100%;margin-top:6px">▶ 生成遗忘曲线（该生全部已练 KP，0~180 天）</button>
    <div id="sim-forget-chart" style="width:100%;height:230px;margin-top:6px"></div>
    <div id="sim-forget-info" style="font-size:11px;color:#999;margin-top:4px"></div>

    <div style="font-size:10px;color:#666;margin-top:8px;line-height:1.6">
      合理性锚点：①全对应 m 单调升、N 单调增；难题做对（d 高）比易题 δ 更大。②遗忘 m→0.5、N 下降；扎实(firm 高→τ 大)的 KP 衰减明显慢。
    </div>
  </div>

  <div class="panel-section">
    <div class="panel-title">📜 操作历史</div>
    <div id="history"><span style="color:#999">等待操作...</span></div>
  </div>
</div>

<script>
// ---- 全局状态 ----
let studentId = null;
let currentKP = null;       // {idx, name}
let currentQuestion = null; // 推荐题目
let graphData = null;
let chart = null;
let studentMastery = {};    // {kp_idx: m}
let _simUpdChart = null, _simForgChart = null;

// ---- 初始化 ----
async function init() {
  chart = echarts.init(document.getElementById('graph'));
  await loadStudents();
  await loadGraph();
  await loadExamStudents();
  window.addEventListener('resize', ()=>{
    chart.resize();
    if (_simUpdChart) _simUpdChart.resize();
    if (_simForgChart) _simForgChart.resize();
  });
}

async function loadStudents() {
  const res = await fetch('/api/students');
  const ids = await res.json();
  const sel = document.getElementById('student-select');
  sel.innerHTML = ids.map(id => `<option value="${id}">学生 #${id}</option>`).join('');
  if (ids.length > 0) { studentId = ids[0]; await selectStudent(); }
}

async function selectStudent() {
  studentId = parseInt(document.getElementById('student-select').value);
  const res = await fetch(`/api/student/${studentId}`);
  const kps = await res.json();
  kps.forEach(kp => { studentMastery[kp.idx] = kp.m; });
  updateGraphColors();
  if (currentKP) await loadKPDetail(currentKP.idx);
}

async function addTestStudent() {
  const sid = Math.floor(Math.random() * 900000) + 100000;
  await fetch('/api/reset', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({student_id: sid, kp_idx: -1})});
  await loadStudents();
  document.getElementById('student-select').value = sid;
  studentId = sid;
  await selectStudent();
}

// ---- 图谱 ----
async function loadGraph() {
  const res = await fetch('/api/graph');
  graphData = await res.json();
  renderGraph();
}

function renderGraph() {
  const nodes = graphData.nodes.map(n => ({
    id: n.idx, name: n.name, module: n.module,
    symbolSize: Math.max(8, Math.min(30, (n.freq || 1) * 2.2)),
    category: graphData.modules.indexOf(n.module),
    itemStyle: {color: '#0f3460', borderColor: '#1a1a2e', borderWidth: 1},
    label: {show: true},
  }));
  const edges = graphData.edges.map(e => ({
    source: e.source, target: e.target,
    lineStyle: {color: e.type==='间'?'#e94560':'#334155', opacity: e.type==='间'?0.45:0.22, width: e.type==='间'?1.4:0.7},
  }));
  const categories = graphData.modules.map(m => ({name: m}));

  chart.setOption({
    tooltip: {
      formatter: p => {
        if (p.dataType !== 'node') return '';
        const mv = studentMastery[p.data.id];
        return `<b>${p.name}</b><br/>模块: ${p.data.module}<br/>掌握度 m: ${mv==null?'—':mv.toFixed(3)}<br/><span style="color:#999">点击查看详情/做题</span>`;
      }
    },
    legend: {type:'scroll', data: graphData.modules.map(m=>({name:m,icon:'circle'})),
             bottom: 4, textStyle:{color:'#cfd8e3',fontSize:11}, itemWidth:11, itemHeight:11, itemGap:10},
    series: [{
      type: 'graph', layout: 'force', roam: 'move', draggable: true,
      categories, nodes, edges,
      force: {repulsion: 200, edgeLength: [35, 170], gravity: 0.06, friction: 0.6},
      scaleLimit: {min: 0.15, max: 8},
      zoom: 0.65,
      label: {show: true, fontSize: 9, color: '#cfd8e3',
              formatter: p => p.name.length > 6 ? p.name.slice(0,6)+'…' : p.name},
      labelLayout: {hideOverlap: true},
      lineStyle: {curveness: 0.08},
      emphasis: {focus: 'adjacency',
                 label: {show: true, fontSize: 13, fontWeight: 'bold', color: '#fff'},
                 lineStyle: {width: 3, color: '#e94560'}},
    }],
  });

  chart.on('click', params => {
    if (params.dataType === 'node') {
      currentKP = {idx: params.data.id, name: params.data.name};
      loadKPDetail(params.data.id);
    }
  });
}

function updateGraphColors() {
  if (!graphData || !studentMastery) return;
  const colors = graphData.nodes.map(n => {
    const m = studentMastery[n.idx] ?? 0.5;
    // 绿(m>0.7) → 黄(0.5) → 红(m<0.3)
    let r, g;
    if (m >= 0.5) { r = Math.round(255*(1-m)*2); g = Math.round(180 + 75*(m-0.5)*2); }
    else { r = 255; g = Math.round(180*m*2); }
    return `rgb(${r},${g},80)`;
  });
  chart.setOption({series:[{data: graphData.nodes.map((n,i)=>({...chart.getOption().series[0].data[i], itemStyle:{color:colors[i]}}))}]});
}

// ---- 知识点 & 题目 ----
async function loadKPDetail(kpIdx) {
  currentKP = {idx: kpIdx, name: graphData.nodes[kpIdx].name};
  const res = await fetch(`/api/student/${studentId}`);
  const kps = await res.json();
  const kp = kps[kpIdx];

  document.getElementById('kp-detail').style.display = 'block';
  document.getElementById('kp-info').innerHTML = `
    <b>${kp.name}</b><br>
    掌握度 m: <span>${kp.m.toFixed(4)}</span> &nbsp; m*: ${kp.m_star.toFixed(4)}<br>
    α=${kp.alpha} β=${kp.beta} N=${kp.N}<br>
    m_peak=${kp.m_peak.toFixed(4)} &nbsp; 置信度 c=${kp.c.toFixed(4)}<br>
    ${kp.has_data?`上次练习: ${kp.last_ts||'—'}`:'🆕 冷启动'}
  `;

  // 推荐题目
  const qRes = await fetch(`/api/recommend?student_id=${studentId}&kp_idx=${kpIdx}&kp_name=${encodeURIComponent(kp.name)}`);
  const q = await qRes.json();
  if (q.error) {
    document.getElementById('question-panel').style.display = 'block';
    document.getElementById('question-meta').innerHTML = `<span style="color:#e74c3c">${q.error}</span>`;
    document.getElementById('question-stem').innerHTML = '';
    return;
  }
  currentQuestion = q;
  document.getElementById('question-panel').style.display = 'block';
  document.getElementById('question-meta').innerHTML = `
    难度: <span>${q.difficulty_label}</span> (d=${q.d}) &nbsp; 题型: ${q.quesType} (g=${q.g})<br>
    当前 m: <span>${q.m_before.toFixed(4)}</span> &nbsp; 题目ID: ${q.qid?.slice(0,8)}...
  `;
  document.getElementById('question-stem').innerHTML = q.stem;
  document.getElementById('result-panel').style.display = 'none';
}

// ---- 作答 ----
async function submitAnswer(y) {
  if (!currentQuestion) return;
  const q = currentQuestion;
  const res = await fetch('/api/answer', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      student_id: studentId, kp_idx: q.kp_idx,
      y: y, d: q.d, g: q.g, k: q.k,
      source: 'manual_test'
    })
  });
  const r = await res.json();

  // 更新本地缓存
  studentMastery[q.kp_idx] = r.m_after;

  // 显示结果
  document.getElementById('result-panel').style.display = 'block';
  const deltaClass = r.delta_m >= 0 ? 'delta-positive' : 'delta-negative';
  const deltaSign = r.delta_m >= 0 ? '+' : '';
  document.getElementById('result-content').innerHTML = `
    <div class="stat-row"><span class="stat-label">掌握度</span><span class="stat-value">${r.m_before.toFixed(4)} → <b class="${deltaClass}">${r.m_after.toFixed(4)} (${deltaSign}${r.delta_m.toFixed(4)})</b></span></div>
    <div class="mastery-bar"><div class="mastery-bar-fill" style="width:${r.m_before*100}%;background:#e94560"></div></div>
    <div class="mastery-bar"><div class="mastery-bar-fill" style="width:${r.m_after*100}%;background:#2ecc71"></div></div>
    <div class="stat-row"><span class="stat-label">α</span><span class="stat-value">${r.alpha_before} → ${r.alpha_after}</span></div>
    <div class="stat-row"><span class="stat-label">β</span><span class="stat-value">${r.beta_before} → ${r.beta_after}</span></div>
    <div class="stat-row"><span class="stat-label">N</span><span class="stat-value">${r.N_before} → ${r.N_after}</span></div>
    <div class="stat-row"><span class="stat-label">m_peak</span><span class="stat-value">${r.m_peak_before.toFixed(4)} → ${r.m_peak_after.toFixed(4)}</span></div>
    ${r.N_after < r.N_before - 0.5 ? '<div class="stat-row"><span class="stat-label">⏳ 遗忘衰减</span><span class="stat-value" style="color:#e67e22">触发（N减少）</span></div>' : ''}
  `;

  // 更新图谱颜色 & 详情
  updateGraphColors();
  await loadKPDetail(q.kp_idx);

  // 历史记录
  const hist = document.getElementById('history');
  const item = document.createElement('div');
  item.className = 'history-item';
  item.innerHTML = `<span style="color:#999">${new Date().toLocaleTimeString()}</span> ${graphData.nodes[q.kp_idx].name}: y=${y} | m ${r.m_before.toFixed(3)}→<span class="${deltaClass}">${r.m_after.toFixed(3)}</span> | N ${r.N_before}→${r.N_after}`;
  hist.insertBefore(item, hist.firstChild);
}

// ---- 遗忘模拟 ----
async function previewForget() {
  if (!currentKP) return;
  const days = parseInt(document.getElementById('forget-days').value) || 30;
  const res = await fetch(`/api/forget-preview?student_id=${studentId}&kp_idx=${currentKP.idx}&days=${days}`);
  const r = await res.json();
  const div = document.getElementById('forget-preview');
  const chartDiv = document.getElementById('forget-chart');

  if (r.error) { div.innerHTML = `<span style="color:#e74c3c">⚠️ ${r.error}</span>`; chartDiv.style.display='none'; return; }

  const sign = r.delta_m >= 0 ? '+' : '';
  const color = r.delta_m >= 0 ? '#2ecc71' : '#e74c3c';
  div.innerHTML = `
    <div class="stat-row"><span class="stat-label">m (遗忘前→后)</span><span class="stat-value">${r.m_now.toFixed(4)} → <b style="color:${color}">${r.m_after.toFixed(4)} (${sign}${r.delta_m.toFixed(4)})</b></span></div>
    <div class="stat-row"><span class="stat-label">N (置信度)</span><span class="stat-value">${r.N_now.toFixed(1)} → ${r.N_after.toFixed(1)} (${r.delta_N>=0?'+':''}${r.delta_N.toFixed(1)})</span></div>
    <div class="stat-row"><span class="stat-label">m_peak</span><span class="stat-value">${r.m_peak_now.toFixed(4)} → ${r.m_peak_after.toFixed(4)}</span></div>
    <div class="stat-row"><span class="stat-label">firm / τ_eff / r</span><span class="stat-value">${r.firm.toFixed(3)} / ${r.tau_eff.toFixed(1)}天 / ${r.r.toFixed(4)}</span></div>
  `;

  // 遗忘曲线
  chartDiv.style.display = 'block';
  if (!window._forgetChart) {
    window._forgetChart = echarts.init(chartDiv);
  }
  window._forgetChart.setOption({
    grid: {top:10,right:10,bottom:25,left:45},
    tooltip: {trigger:'axis'},
    xAxis: {type:'category', data: r.curve.map(c=>c.day+'d'), axisLabel:{fontSize:9,color:'#999'}},
    yAxis: {min:0.3, max:Math.max(1, r.m_now*1.1), axisLabel:{fontSize:9,color:'#999'}},
    series: [
      {name:'m', type:'line', data: r.curve.map(c=>c.m), smooth:true, lineStyle:{color:'#e94560'}, symbol:'none'},
      {name:'m_peak', type:'line', data: r.curve.map(c=>c.m_peak), smooth:true, lineStyle:{color:'#f39c12',type:'dashed'}, symbol:'none'},
    ],
    legend: {data:['m','m_peak'], textStyle:{color:'#999',fontSize:9}, bottom:0},
  });
}

async function applyForget() {
  if (!currentKP) return;
  if (!confirm(`确定要将 "${currentKP.name}" 的 last_ts 回退 ${document.getElementById('forget-days').value} 天？\n下次作答时将触发遗忘衰减。`)) return;

  const days = parseInt(document.getElementById('forget-days').value) || 30;
  const res = await fetch('/api/forget', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({student_id:studentId, kp_idx:currentKP.idx, days})
  });
  const r = await res.json();
  if (r.error) { alert(r.error); return; }

  await loadKPDetail(currentKP.idx);
  const hist = document.getElementById('history');
  const item = document.createElement('div');
  item.className = 'history-item';
  item.innerHTML = `<span style="color:#e67e22">⏳ 遗忘${days}天</span> ${currentKP.name}: m=${r.m_before.toFixed(3)} | last_ts→${new Date(r.new_last_ts).toLocaleDateString()}`;
  hist.insertBefore(item, hist.firstChild);
}

async function resetKP() {
  if (!currentKP) return;
  if (!confirm(`确定重置 "${currentKP.name}" 为冷启动状态？`)) return;
  await fetch('/api/reset', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({student_id:studentId, kp_idx:currentKP.idx})});
  studentMastery[currentKP.idx] = 0.5;
  updateGraphColors();
  await loadKPDetail(currentKP.idx);
  const hist = document.getElementById('history');
  const item = document.createElement('div');
  item.className = 'history-item';
  item.innerHTML = `<span style="color:#7f8c8d">🔄 重置</span> ${currentKP.name} → 冷启动`;
  hist.insertBefore(item, hist.firstChild);
}

// ---- 批量机制测试 ----
async function loadExamStudents() {
  const res = await fetch('/api/sim/exam');
  const r = await res.json();
  const sel = document.getElementById('sim-student');
  sel.innerHTML = (r.students || []).slice(0, 300)
    .map(s => `<option value="${s}">学生 #${s}</option>`).join('');
  if ((r.students || []).length) await loadExamKPs();
}

async function loadExamKPs() {
  const sid = document.getElementById('sim-student').value;
  if (!sid) return;
  const res = await fetch(`/api/sim/exam?student_id=${sid}`);
  const r = await res.json();
  document.getElementById('sim-kp').innerHTML = (r.kps || [])
    .map(k => `<option value="${k.idx}">${k.name} (${k.n}题)</option>`).join('');
}

function onSimModeChange() {
  const m = document.getElementById('sim-mode').value;
  document.getElementById('sim-controlled-ctl').style.display = m === 'controlled' ? 'block' : 'none';
}

async function runSimUpdate() {
  const mode = document.getElementById('sim-mode').value;
  let body;
  if (mode === 'real') {
    body = { mode: 'real', student_id: parseInt(document.getElementById('sim-student').value),
             kp_idx: parseInt(document.getElementById('sim-kp').value) };
  } else {
    body = { mode: 'controlled', kp_idx: parseInt(document.getElementById('sim-kp').value),
             scenario: document.getElementById('sim-scenario').value,
             n: parseInt(document.getElementById('sim-n').value || 15) };
  }
  const res = await fetch('/api/sim/update', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const r = await res.json();
  const info = document.getElementById('sim-update-info');
  if (r.error) { info.innerHTML = `<span style="color:#e74c3c">${r.error}</span>`; return; }
  const c = r.curve;
  info.innerHTML = `${r.kp_name} · ${c.length}题 · m: <b style="color:#e94560">${c[0].m}→${c[c.length-1].m}</b> · N: ${c[0].N}→${c[c.length-1].N}`;
  if (!_simUpdChart) _simUpdChart = echarts.init(document.getElementById('sim-update-chart'));
  _simUpdChart.setOption({
    grid: { top: 30, right: 58, bottom: 52, left: 44 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['掌握度 m', '证据量 N', '预测 p', '真实得分 y', '难度 d'],
              bottom: 2, textStyle: { color: '#cfd8e3', fontSize: 11 }, itemWidth: 12, itemHeight: 12, itemGap: 12 },
    xAxis: { type: 'category', data: c.map(x => '#' + x.step), axisLabel: { fontSize: 9, color: '#999' }, name: '题序', nameTextStyle:{color:'#666',fontSize:9} },
    yAxis: [
      { type: 'value', min: 0, max: 1, name: 'm/p/y/d', nameTextStyle: { color: '#999', fontSize: 10 }, axisLabel: { fontSize: 9, color: '#999' } },
      { type: 'value', min: 0, max: 100, name: 'N', position: 'right', nameTextStyle: { color: '#9b59b6', fontSize: 10 }, axisLabel: { fontSize: 9, color: '#9b59b6' }, splitLine: { show: false } },
    ],
    series: [
      { name: '掌握度 m', type: 'line', data: c.map(x => x.m), smooth: true, lineStyle: { color: '#e94560', width: 2.5 }, symbol: 'circle', symbolSize: 5, itemStyle: { color: '#e94560' } },
      { name: '证据量 N', type: 'line', yAxisIndex: 1, data: c.map(x => x.N), smooth: true, lineStyle: { color: '#9b59b6', width: 2 }, symbol: 'none' },
      { name: '预测 p', type: 'line', data: c.map(x => x.p), lineStyle: { color: '#3498db', type: 'dashed', width: 1.5 }, symbol: 'none' },
      { name: '真实得分 y', type: 'scatter', data: c.map(x => x.y), itemStyle: { color: '#2ecc71' }, symbolSize: 7 },
      { name: '难度 d', type: 'bar', data: c.map(x => x.d), itemStyle: { color: 'rgba(243,156,18,0.32)' }, barWidth: '60%' },
    ],
  });
}

async function runSimForget() {
  const sid = parseInt(document.getElementById('sim-student').value);
  if (!sid) { alert('请先在 ① 选真实考试学生'); return; }
  const res = await fetch(`/api/sim/forget?student_id=${sid}&days=180&seed=exam`);
  const r = await res.json();
  const info = document.getElementById('sim-forget-info');
  if (r.error) { info.innerHTML = `<span style="color:#e74c3c">${r.error}</span>`; return; }
  const ac = r.avg_curve, ki = r.kp_info;
  const byTau = [...ki].sort((a, b) => b.tau - a.tau);
  info.innerHTML = `${r.n_kps} 个 KP 均值 · m: <b style="color:#e94560">${ac[0].m}→${ac[ac.length-1].m}</b> · N: ${ac[0].N}→${ac[ac.length-1].N}<br>最扎实 τ=${byTau[0].tau}天 / 最不扎实 τ=${byTau[byTau.length-1].tau}天`;
  if (!_simForgChart) _simForgChart = echarts.init(document.getElementById('sim-forget-chart'));
  const series = [
    { name: 'm 均值', type: 'line', data: ac.map(x => x.m), smooth: true, lineStyle: { color: '#e94560', width: 2.5 }, symbol: 'none' },
    { name: 'N 均值', type: 'line', yAxisIndex: 1, data: ac.map(x => x.N), smooth: true, lineStyle: { color: '#9b59b6', width: 2 }, symbol: 'none' },
    { name: 'm_peak 均值', type: 'line', data: ac.map(x => x.m_peak), smooth: true, lineStyle: { color: '#f39c12', type: 'dashed' }, symbol: 'none' },
  ];
  (r.rep_kps || []).forEach(idx => {
    const s = r.per_kp[String(idx)]; if (!s) return;
    const nm = (ki.find(x => x.idx === idx) || {}).name || ('KP' + idx);
    series.push({ name: nm.slice(0, 6) + '(m)', type: 'line', data: s.map(x => x.m), smooth: true, lineStyle: { width: 1, opacity: 0.6 }, symbol: 'none' });
  });
  _simForgChart.setOption({
    grid: { top: 30, right: 58, bottom: 52, left: 44 },
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#cfd8e3', fontSize: 10 }, bottom: 2, type: 'scroll', itemWidth: 11, itemHeight: 11 },
    xAxis: { type: 'category', data: ac.map(x => x.day + 'd'), axisLabel: { fontSize: 9, color: '#999' }, name: '天数', nameTextStyle: { color: '#666', fontSize: 9 } },
    yAxis: [
      { type: 'value', min: 0, max: 1, name: 'm / m_peak', nameTextStyle: { color: '#999', fontSize: 10 }, axisLabel: { fontSize: 9, color: '#999' } },
      { type: 'value', min: 0, max: 100, name: 'N', position: 'right', nameTextStyle: { color: '#9b59b6', fontSize: 10 }, axisLabel: { fontSize: 9, color: '#9b59b6' }, splitLine: { show: false } },
    ],
    series,
  });
}

// ---- 启动 ----
init();
</script>
</body>
</html>"""


if __name__ == '__main__':
    print(f'知识点: {N_KP}  模块: {len(MODULES)}  先修边: {len(KG_EDGES)}')
    print(f'题库覆盖: {len(KP_QUESTIONS)} 个知识点')
    print(f'启动: http://127.0.0.1:5000')
    app.run(host='0.0.0.0', port=5000, debug=True)
