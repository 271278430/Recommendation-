"""practice_event 表的数据访问（CRUD）。

一次答题 = 一行；按 student_id / question_id / kp_ids 过滤。
【规范】kp_ids 存知识点 id（kp_id，与题库/接口统一标识），不存 name。
查某知识点序列用 WHERE kp_id = ANY(kp_ids)。

注意：这里只管"存/查做题事件"，不更新掌握度。掌握度更新归 svc.mastery.process_answer；
答题入口应在调 process_answer 之后，紧接着调 log_practice_event。
"""
from datetime import datetime, timezone

import psycopg2.extras

from ..db import conn


def _utcnow():
    return datetime.now(timezone.utc)


def log_practice_event(student_id, question_id, kp_ids, score, is_wrong,
                       ques_type=None, difficulty=None, d=None, source=None, ts=None,
                       client_request_id=None):
    """插一行答题事件。"""
    if ts is None:
        ts = _utcnow()
    with conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO practice_event
               (student_id, question_id, kp_ids, ques_type, difficulty, d, score, is_wrong, source, ts, client_request_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (int(student_id), str(question_id), list(kp_ids),
             ques_type, difficulty, d, float(score), bool(is_wrong), source, ts, client_request_id),
        )
        c.commit()


def _fetch(cur, sql, params):
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def get_kp_sequence(student_id, kp_id, limit=50, wrong_only=False):
    """某学生在某知识点（kp_id）上的做题序列（按时间倒序）。wrong_only=True 只返回错题。"""
    sql = ("SELECT question_id, ts, ques_type, difficulty, score, is_wrong, kp_ids "
           "FROM practice_event WHERE student_id = %s AND %s = ANY(kp_ids)")
    params = [int(student_id), kp_id]
    if wrong_only:
        sql += " AND is_wrong"
    sql += " ORDER BY ts DESC LIMIT %s"
    params.append(int(limit))
    with conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        return _fetch(cur, sql, params)


def get_student_events(student_id, days=None, wrong_only=False, kp_ids=None, limit=100):
    """某学生最近的做题事件。"""
    sql = "SELECT question_id, ts, ques_type, difficulty, score, is_wrong, kp_ids FROM practice_event WHERE student_id = %s"
    params = [int(student_id)]
    if kp_ids:
        sql += " AND kp_ids && %s"
        params.append(list(kp_ids))
    if days is not None:
        sql += " AND ts >= now() - make_interval(days => %s)"
        params.append(int(days))
    if wrong_only:
        sql += " AND is_wrong"
    sql += " ORDER BY ts DESC LIMIT %s"
    params.append(int(limit))
    with conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        return _fetch(cur, sql, params)


def get_kp_stats(student_id, kp_ids, recent_days=30):
    """每个 kp_id 的做题统计（用于学情 per-KP 增强）。

    返回 {kp_id: {practice_count, recent_wrong_count, last_ts}}。
    """
    if not kp_ids:
        return {}
    with conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT kp,
                      count(*) AS practice_count,
                      count(*) FILTER (WHERE is_wrong AND ts >= now() - make_interval(days => %s)) AS recent_wrong_count,
                      max(ts) AS last_ts
               FROM practice_event, unnest(kp_ids) kp
               WHERE student_id = %s AND kp = ANY(%s)
               GROUP BY kp""",
            (int(recent_days), int(student_id), list(kp_ids)),
        )
        return {
            r["kp"]: {
                "practice_count": int(r["practice_count"]),
                "recent_wrong_count": int(r["recent_wrong_count"]),
                "last_ts": r["last_ts"],
            }
            for r in cur.fetchall()
        }


def get_history_summary(student_id, kp_ids, days=30):
    """指定范围 + 时间窗内的做题汇总（不受 limit 限制）。

    返回 {total_answered, wrong_count, accuracy, last_active_ts}。
    """
    if not kp_ids:
        return {"total_answered": 0, "wrong_count": 0, "accuracy": None, "last_active_ts": None}
    with conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE is_wrong) AS wrong,
                      max(ts) AS last_ts
               FROM practice_event
               WHERE student_id = %s AND kp_ids && %s
                 AND ts >= now() - make_interval(days => %s)""",
            (int(student_id), list(kp_ids), int(days)),
        )
        total, wrong, last_ts = cur.fetchone()
        total, wrong = int(total), int(wrong)
        acc = round((total - wrong) / total, 4) if total else None
        return {
            "total_answered": total,
            "wrong_count": wrong,
            "accuracy": acc,
            "last_active_ts": last_ts.isoformat() if last_ts else None,
        }


def seen_question_ids(student_id, limit=None):
    """该学生做过的题 id 集合（用于推荐去重）。"""
    sql = "SELECT DISTINCT question_id FROM practice_event WHERE student_id = %s"
    params = [int(student_id)]
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


def get_latest_results(student_id):
    """该学生每道题最近一次作答是否错误（DISTINCT ON，推荐去重用）。"""
    sql = ("SELECT DISTINCT ON (question_id) question_id, is_wrong "
           "FROM practice_event WHERE student_id = %s "
           "ORDER BY question_id, ts DESC")
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, (int(student_id),))
        return {r[0]: r[1] for r in cur.fetchall()}


def find_event_by_request_id(student_id, client_request_id):
    """按幂等键查是否已处理过。返回 dict(question_id, ts) 或 None。"""
    if not client_request_id:
        return None
    sql = ("SELECT question_id, ts FROM practice_event "
           "WHERE student_id = %s AND client_request_id = %s LIMIT 1")
    with conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (int(student_id), client_request_id))
        row = cur.fetchone()
        return dict(row) if row else None
