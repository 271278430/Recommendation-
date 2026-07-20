#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为 初中_九年级_精简版.jsonl 做 LLM 增量补标:
  - 以"原子单元"(无子题的题 + 每个子题)为粒度,逐个判定"解题必须运用"的知识点
  - 只接受 612 标准清单内的知识点名(逐字校验)
  - 与原标签取并集(只增不删)
  - 父题 kgPoints = 子题并集
用法:
  python3 augment_tags.py process [--limit N]   # 跑 LLM 判定,写 checkpoint
  python3 augment_tags.py assemble              # 组装补标版 jsonl
"""
import json, os, re, sys, time, unicodedata, threading, html, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic

ROOT = '/data/shanghui/Recommend_question'
SRC  = f'{ROOT}/data/初中_九年级.jsonl'
DST  = f'{ROOT}/data/初中_九年级_补标版.jsonl'
KPLIST = f'{ROOT}/data/知识点清单_按学习顺序.md'
CKPT_DIR = '/tmp/augment'
CKPT = f'{CKPT_DIR}/ckpt.jsonl'           # 每行 {uid, kps:[校验后的知识点名]}
MODEL = os.environ.get('AUG_MODEL', 'glm-5.1')
BATCH = 8
WORKERS = int(os.environ.get('AUG_WORKERS', '20'))

# ---------- 知识点清单 ----------
KP = [ln[2:].rstrip('\n') for ln in open(KPLIST, encoding='utf-8') if ln.startswith('- ')]
def norm(s):
    return unicodedata.normalize('NFKC', (s or '').strip())
KP_NORM = {norm(k): k for k in KP}        # 规范化名 -> 原名
SYS = ("你是初中数学考点分析专家。为每道题判定'学生必须实际运用才能正确解出'的知识点。\n"
       "【硬性要求】只能从下面这份612知识点清单里选,名称必须与清单逐字一致(一个字都不能差)。\n"
       "判定标准:学生必须实际运用;单一技能题只给1个;综合题2~4个。不要为凑数多给。\n"
       "严禁把以下当作独立考点:(a)解析里顺带提及的词;(b)图形/题干描述里的'平行''垂直''相似''直角'等(仅描述图形而非考查该知识点);\n"
       "(c)某考点的子步骤/同义(如'整式的化简求值'题里的'去括号''合并同类项')。\n"
       "拿不准某个知识点是否为解题必需时,不要加入(宁可少给,不要凑数)。\n"
       "知识点清单(分号分隔):" + ';'.join(KP))

_client = None
def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()   # 自动读 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN
    return _client

def strip_html(s):
    s = re.sub(r'<[^>]+>', '', s or '')
    return html.unescape(s).strip()

# ---------- 单元抽取 ----------
def extract_units():
    units = []
    with open(SRC, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            pstem = strip_html(d.get('stem'))[:300]
            ch = d.get('children') or []
            if ch:
                for c in ch:
                    units.append({
                        'uid': c.get('_id'), 'parent': d.get('_id'),
                        'stem': strip_html(c.get('stem'))[:400],
                        'options': [strip_html(o)[:150] for o in (c.get('options') or [])],
                        'answer': [strip_html(a)[:150] for a in (c.get('answer') or [])],
                        'ana': strip_html(c.get('quesAnalysis'))[:600],
                        'pstem': pstem,
                        'orig': [k['name'] for k in (c.get('kgPoints') or [])],
                    })
            else:
                units.append({
                    'uid': d.get('_id'), 'parent': None,
                    'stem': strip_html(d.get('stem'))[:400],
                    'options': [strip_html(o)[:150] for o in (d.get('options') or [])],
                    'answer': [strip_html(a)[:150] for a in (d.get('answer') or [])],
                    'ana': strip_html(d.get('quesAnalysis'))[:600],
                    'pstem': '',
                    'orig': [k['name'] for k in (d.get('kgPoints') or [])],
                })
    return units

def validate(names):
    out = []
    seen = set()
    for n in names or []:
        key = norm(n)
        if key in KP_NORM and key not in seen:
            seen.add(key); out.append(KP_NORM[key])
    return out

# ---------- API 调用 ----------
def judge_batch(batch):
    items = []
    for u in batch:
        obj = {'uid': u['uid'], 'stem': u['stem']}
        if u.get('pstem'): obj['parent_stem'] = u['pstem']
        if u['options']: obj['options'] = u['options']
        if u['answer']: obj['answer'] = u['answer']
        if u['ana']: obj['analysis'] = u['ana']
        items.append(obj)
    user = ("题目数组:\n" + json.dumps(items, ensure_ascii=False) +
            "\n\n请输出JSON数组,每个元素 {\"uid\":..., \"kps\":[知识点名,...]}。只输出JSON,不要解释。")
    last = None
    for attempt in range(6):
        try:
            resp = client().messages.create(
                model=MODEL, max_tokens=1500,
                system=SYS,
                messages=[{"role": "user", "content": user}])
            txt = resp.content[0].text
            m = re.search(r'\[.*\]', txt, re.S)
            if not m: raise ValueError("no JSON array: " + txt[:120])
            arr = json.loads(m.group(0))
            res = {}
            for a in arr:
                uid = a.get('uid'); kps = validate(a.get('kps'))
                if uid: res[uid] = kps
            return res
        except Exception as e:
            last = e
            msg = str(e)
            transient = any(s in msg for s in ('429','529','rate','overload','1302','1305','过多','稍后'))
            wait = [5,10,20,40,60,90,120][attempt] if transient else 2*(attempt+1)
            time.sleep(wait)
    print(f'[FAIL] batch uid0={batch[0]["uid"][:8]}: {repr(last)[:140]}', flush=True)
    return {}

# ---------- checkpoint ----------
_lock = threading.Lock()
def load_done():
    done = {}
    if os.path.exists(CKPT):
        for line in open(CKPT, encoding='utf-8'):
            line = line.strip()
            if not line: continue
            try:
                d = json.loads(line); done[d['uid']] = d['kps']
            except: pass
    return done
def append_results(res):
    with _lock:
        with open(CKPT, 'a', encoding='utf-8') as f:
            for uid, kps in res.items():
                f.write(json.dumps({'uid': uid, 'kps': kps}, ensure_ascii=False) + '\n')

# ---------- process ----------
def cmd_process(limit=None):
    os.makedirs(CKPT_DIR, exist_ok=True)
    done = load_done()
    units = extract_units()
    todo = [u for u in units if u['uid'] not in done]
    if limit: todo = todo[:limit]
    print(f'总单元 {len(units)} | 已完成 {len(done)} | 待跑 {len(todo)} | model={MODEL} batch={BATCH} workers={WORKERS}', flush=True)
    batches = [todo[i:i+BATCH] for i in range(0, len(todo), BATCH)]
    t0 = time.time(); done_n = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(judge_batch, b): b for b in batches}
        for fut in as_completed(futs):
            res = fut.result()
            if res: append_results(res)
            done_n += 1
            if done_n % 20 == 0 or done_n == len(batches):
                el = time.time() - t0
                print(f'  {done_n}/{len(batches)} 批, 用时{el:.0f}s, 速率{done_n/el:.2f}批/s, 预计剩{((len(batches)-done_n)/max(done_n,1)*el):.0f}s', flush=True)
    print('process 完成。', flush=True)

# ---------- assemble ----------
def build_canon_id():
    from collections import defaultdict, Counter
    freq = defaultdict(Counter)
    def walk(o):
        for kg in (o.get('kgPoints') or []):
            nm = (kg.get('name') or '').strip(); kid = (kg.get('id') or '').strip()
            if nm and kid: freq[norm(nm)][kid] += 1
        for c in (o.get('children') or []): walk(c)
    with open(SRC, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            walk(json.loads(line))
    return {k: ctr.most_common(1)[0][0] for k, ctr in freq.items()}

def cmd_assemble():
    done = load_done()
    canon = build_canon_id()
    print(f'checkpoint 单元数 {len(done)} | canon_id 数 {len(canon)}', flush=True)
    def to_objs(names):
        out = []; seen = set()
        for n in names:
            k = norm(n)
            if k in seen: continue
            seen.add(k)
            out.append({'name': n, 'id': canon.get(k, '')})
        return out
    n_q = n_ch = 0; added_total = 0
    with open(SRC, encoding='utf-8') as fin, open(DST, 'w', encoding='utf-8') as fout:
        for line in fin:
            line = line.strip()
            if not line: continue
            d = json.loads(line); n_q += 1
            ch = d.get('children') or []
            if ch:
                for c in ch:
                    n_ch += 1
                    orig = [k['name'] for k in (c.get('kgPoints') or [])]
                    judged = done.get(c.get('_id'), [])
                    final = list(dict.fromkeys(orig + judged))
                    added_total += len(final) - len(orig)
                    c['kgPoints'] = to_objs(final)
                # 父题 = 子题并集
                union = []
                seen = set()
                for c in ch:
                    for k in c['kgPoints']:
                        if k['name'] not in seen:
                            seen.add(k['name']); union.append(k)
                d['kgPoints'] = union
            else:
                orig = [k['name'] for k in (d.get('kgPoints') or [])]
                judged = done.get(d.get('_id'), [])
                final = list(dict.fromkeys(orig + judged))
                added_total += len(final) - len(orig)
                d['kgPoints'] = to_objs(final)
            fout.write(json.dumps(d, ensure_ascii=False) + '\n')
    print(f'assemble 完成 -> {DST}', flush=True)
    print(f'题目 {n_q} | 子题 {n_ch} | 净增知识点标签 {added_total}', flush=True)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['process', 'assemble'])
    ap.add_argument('--limit', type=int, default=None)
    a = ap.parse_args()
    if a.cmd == 'process': cmd_process(a.limit)
    else: cmd_assemble()
