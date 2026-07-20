#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给"沾到考试13个知识点"的题库原子题,用 LLM 标注知识点考察权重(Σ=1)。
- 单知识点题: 权重 1.0(不调 LLM)
- 多知识点题: LLM 分配权重,允许把无关知识点置0(软修正多标),存活者归一化到 Σ=1
用法:
  python3 annotate_kp_weights.py process [--limit N]   # 跑 LLM,写 checkpoint(只多知识点)
  python3 annotate_kp_weights.py assemble              # 合并单/多,输出 data/question_kp_weights.jsonl
"""
import json, os, re, sys, time, threading, html, argparse, unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic

ROOT = '/data/shanghui/Recommend_question'
SRC  = f'{ROOT}/data/初中_九年级.jsonl'
DST  = f'{ROOT}/data/question_kp_weights.jsonl'
CKPT_DIR = '/tmp/kp_weights'
CKPT = f'{CKPT_DIR}/ckpt.jsonl'           # {qid, weights:[{name,weight}]}
MODEL = os.environ.get('AW_MODEL', 'glm-5.1')
BATCH = 8
WORKERS = int(os.environ.get('AW_WORKERS', '10'))

# 考试涉及的标准13知识点(决定范围)
EXAM_KP = {'二次函数的定义','二次函数的图象与性质','二次函数的最值','反比例函数的定义',
           '反比例函数的图象与性质','二次函数图象的平移','二次函数的实际应用',
           '二次函数的综合应用','二次函数图象与系数a、b、c的关系',
           '二次函数图象与一元二次方程的关系','反比例函数与一次函数的综合应用',
           '同一坐标系中函数图象的判断','用待定系数法确定二次函数表达式'}

SYS = ("你是初中数学命题专家。给每道题的知识点分配【考察权重】。"
       "权重含义:该题对这个知识点的考察比重(主考点权重大,辅助考点小)。"
       "规则:(1)只能用题目给出的知识点名,逐字一致;(2)若某知识点与该题解题实际无关,权重设为0;"
       "(3)其余按考察比重分配,要求 所有权重之和 = 1;(4)权重∈[0,1]。"
       "只输出JSON数组,不要解释。")

def strip_html(s):
    return html.unescape(re.sub(r'<[^>]+>', '', s or '')).strip()

_client = None
def client():
    global _client
    if _client is None: _client = anthropic.Anthropic()
    return _client

# ---------- 原子单元抽取(只保留沾到考试13知识点的) ----------
def atomic_units(d):
    out=[]
    pstem = strip_html(d.get('stem'))[:300]
    def mk(o, pstem):
        tags=[k['name'] for k in (o.get('kgPoints') or [])]
        return {'qid': o.get('_id'),
                'stem': strip_html(o.get('stem'))[:400],
                'options':[strip_html(x)[:150] for x in (o.get('options') or [])],
                'answer':[strip_html(x)[:150] for x in (o.get('answer') or [])],
                'ana': strip_html(o.get('quesAnalysis'))[:600],
                'pstem': pstem, 'tags': tags}
    ch = d.get('children') or []
    if ch:
        for c in ch: out.append(mk(c, pstem))
    else:
        out.append(mk(d, ''))
    return [u for u in out if u['tags'] and (set(u['tags']) & EXAM_KP)]

def collect_scope():
    """返回 (multi_units, single_units) —— 多知识点/单知识点(沾13考点)"""
    multi, single = [], []
    seen = set()
    with open(SRC, encoding='utf-8') as f:
        for line in f:
            line=line.strip()
            if not line: continue
            d=json.loads(line)
            for u in atomic_units(d):
                if u['qid'] in seen: continue
                seen.add(u['qid'])
                uniq = list(dict.fromkeys(u['tags']))
                u['tags'] = uniq
                if len(uniq) > 1: multi.append(u)
                else: single.append(u)
    return multi, single

# ---------- 权重校验/归一 ----------
def normalize(ws, tags):
    """ws: list[{name,weight}]; 只保留 name∈tags 且 weight>0 的;归一化到Σ=1。全0则均分。"""
    tagset=set(tags)
    kept=[(w['name'], float(w.get('weight',0))) for w in ws if w['name'] in tagset and float(w.get('weight',0))>0]
    if not kept:                                 # 全0/非法 -> 均分兜底
        kept=[(t, 1.0) for t in tags]
    s=sum(w for _,w in kept)
    return [{'name':n,'weight':round(w/s,4)} for n,w in kept]

def judge_batch(batch):
    items=[]
    for u in batch:
        o={'qid':u['qid'],'stem':u['stem'],'知识点':u['tags']}
        if u['pstem']: o['父题背景']=u['pstem']
        if u['options']: o['options']=u['options']
        if u['answer']: o['answer']=u['answer']
        if u['ana']: o['analysis']=u['ana']
        items.append(o)
    user=("题目数组:\n"+json.dumps(items,ensure_ascii=False)+
          "\n\n请输出JSON数组,每个元素 {\"qid\":..., \"weights\":[{\"name\":知识点名,\"weight\":数值}]}。")
    last=None
    for attempt in range(6):
        try:
            resp=client().messages.create(model=MODEL,max_tokens=2000,system=SYS,
                messages=[{"role":"user","content":user}])
            txt=resp.content[0].text
            m=re.search(r'\[.*\]', txt, re.S)
            if not m: raise ValueError("no JSON")
            arr=json.loads(m.group(0))
            res={}
            tagmap={u['qid']:u['tags'] for u in batch}
            for a in arr:
                qid=a.get('qid'); ws=a.get('weights',[])
                if qid in tagmap: res[qid]=normalize(ws, tagmap[qid])
            return res
        except Exception as e:
            last=e; msg=str(e)
            wait=[5,10,20,40,60,90][attempt] if any(s in msg for s in ('429','529','rate','overload','1302','1305','稍后','过多')) else 2*(attempt+1)
            time.sleep(wait)
    print(f'[FAIL] qid0={batch[0]["qid"][:8]}: {repr(last)[:140]}', flush=True)
    return {}

_lock=threading.Lock()
def load_done():
    done={}
    if os.path.exists(CKPT):
        for line in open(CKPT,encoding='utf-8'):
            line=line.strip()
            if not line: continue
            try:
                d=json.loads(line); done[d['qid']]=d['weights']
            except: pass
    return done
def append(res):
    with _lock, open(CKPT,'a',encoding='utf-8') as f:
        for qid,ws in res.items():
            f.write(json.dumps({'qid':qid,'weights':ws},ensure_ascii=False)+'\n')

def cmd_process(limit=None):
    os.makedirs(CKPT_DIR, exist_ok=True)
    done=load_done()
    multi,_=collect_scope()
    todo=[u for u in multi if u['qid'] not in done]
    if limit: todo=todo[:limit]
    print(f'多知识点原子题 {len(multi)} | 已完成 {len(done)} | 待跑 {len(todo)} | model={MODEL} batch={BATCH} workers={WORKERS}', flush=True)
    batches=[todo[i:i+BATCH] for i in range(0,len(todo),BATCH)]
    t0=time.time(); n=0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs={ex.submit(judge_batch,b):b for b in batches}
        for fut in as_completed(futs):
            res=fut.result()
            if res: append(res)
            n+=1
            if n%20==0 or n==len(batches):
                el=time.time()-t0
                print(f'  {n}/{len(batches)} 批, {el:.0f}s, {n/el:.2f}批/s', flush=True)
    print('process 完成。', flush=True)

def cmd_assemble():
    done=load_done()
    multi,single=collect_scope()
    n1=n2=nfb=0
    with open(DST,'w',encoding='utf-8') as f:
        for u in single:                              # 单知识点 -> 1.0
            f.write(json.dumps({'qid':u['qid'],'weights':[{'name':u['tags'][0],'weight':1.0}],'fallback':False},ensure_ascii=False)+'\n'); n1+=1
        for u in multi:                               # 多知识点: 有LLM用LLM, 没有则均分回退
            if u['qid'] in done:
                ws=done[u['qid']]; fb=False
            else:
                ws=[{'name':t,'weight':round(1/len(u['tags']),4)} for t in u['tags']]; fb=True; nfb+=1
            f.write(json.dumps({'qid':u['qid'],'weights':ws,'fallback':fb},ensure_ascii=False)+'\n'); n2+=1
    print(f'assemble -> {DST}')
    print(f'  单知识点(1.0): {n1} | 多知识点: {n2}(LLM成功 {n2-nfb}, 回退均分 {nfb}) | 合计 {n1+n2}')
    import random; random.seed(0)
    done_multi=[u for u in multi if u['qid'] in done]
    sample=random.sample(done_multi, min(5,len(done_multi)))
    print('\n抽样(多知识点权重):')
    for u in sample:
        print('  ', u['qid'][:8], [(w['name'][:14],w['weight']) for w in done[u['qid']]])

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('cmd',choices=['process','assemble'])
    ap.add_argument('--limit',type=int,default=None)
    a=ap.parse_args()
    if a.cmd=='process': cmd_process(a.limit)
    else: cmd_assemble()
