"""题库索引：question_id → 题目属性（知识点/题型/难度）。

submit-answer 接口用这个查"这道题考了哪些知识点、什么难度"，调用方不用传。
懒加载：首次调用时扫一遍题库建索引（82k 题，~2s），之后内存命中。
"""
import json
import logging
import os

from ..core.config import settings

log = logging.getLogger("recommend.question_bank")

_qindex = None
_kp_questions = None

# 标定常量（与 web_app.DIFF_MAP / mastery 机制同源，不可改）
DIFF_MAP = {"容易": 0.15, "较易": 0.35, "适中": 0.55, "较难": 0.75, "困难": 0.90}
_G_MAP = {"单选题": 0.25, "多选题": 0.10, "判断题": 0.50}


def _build():
    """扫题库建两个索引：
    - _qindex: question_id → {kp_ids, ques_type, difficulty}
    - _kp_questions: kp_id → [question meta, ...]（倒排索引，供粗召回用）
    """
    global _qindex, _kp_questions
    idx = {}
    kp_idx = {}
    with open(settings.qbank_path, encoding="utf-8") as f:
        for line in f:
            q = json.loads(line)
            qid = q.get("_id")
            if not qid:
                continue
            kp_ids = [kp["id"] for kp in q.get("kgPoints", []) if kp.get("id")]
            ques_type = q.get("quesType")
            difficulty = q.get("difficulty")
            idx[qid] = {"kp_ids": kp_ids, "ques_type": ques_type, "difficulty": difficulty}
            meta = {"question_id": qid, "ques_type": ques_type, "difficulty": difficulty, "kp_ids": kp_ids}
            for kid in kp_ids:
                kp_idx.setdefault(kid, []).append(meta)
    log.info(f"question_index built: {len(idx)} questions, {len(kp_idx)} kp_id in inverted index")
    _qindex = idx
    _kp_questions = kp_idx


def get_question_meta(question_id: str) -> dict | None:
    """查题目属性。首次调用懒加载索引。返回 None = 题库里没这道题。"""
    if _qindex is None:
        _build()
    return _qindex.get(question_id)


def get_questions_by_kp(kp_id: str) -> list[dict]:
    """查某个知识点下的所有题目（倒排索引）。返回 list 或空 list。"""
    if _kp_questions is None:
        _build()
    return _kp_questions.get(kp_id, [])


_qp_weights = None


def _build_qp_weights():
    """加载 question_kp_weights.jsonl → {qid: {kp_name: weight}}。"""
    global _qp_weights
    _qp_weights = {}
    path = os.path.join(settings.project_root, "data", "question_kp_weights.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                qid = d.get("qid")
                if qid:
                    _qp_weights[qid] = {w["name"]: w["weight"] for w in d.get("weights", [])}
    log.info(f"question_kp_weights loaded: {len(_qp_weights)} questions")


def get_question_weights(question_id: str) -> dict | None:
    """查题目知识点权重。返回 {kp_name: weight} 或 None。"""
    if _qp_weights is None:
        _build_qp_weights()
    return _qp_weights.get(question_id)
