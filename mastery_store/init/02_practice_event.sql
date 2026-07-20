-- 学生做题历史表（practice_event）：一次答题 = 一行
-- 按 student_id / question_id / kp_ids 过滤；kp_ids 用 GIN 索引支持"按知识点查序列"
-- 【规范】kp_ids 存知识点 id（kp_id，题库唯一 id，接口统一标识），与 mastery_store 通过 KPRegistry 映射

CREATE TABLE IF NOT EXISTS practice_event (
    id           BIGSERIAL PRIMARY KEY,
    student_id   BIGINT   NOT NULL,
    question_id  TEXT     NOT NULL,                 -- 题目标识（来源+题号，如 exam_20251016_3）
    kp_ids       TEXT[]   NOT NULL,                 -- 该题考察的知识点 kp_id 列表（一题可多个）
    ques_type    TEXT,                              -- 单选题/填空题/...
    difficulty   TEXT,                              -- 容易/较易/.../困难（有则填）
    d            NUMERIC(4,2),                      -- DIFF_MAP 映射后的难度数值（有则填）
    score        NUMERIC(5,3) NOT NULL,             -- 得分率 ∈ [0,1]
    is_wrong     BOOLEAN  NOT NULL,                 -- score < 0.5 视为错
    source       TEXT,                              -- exam / homework / classwork
    ts           TIMESTAMPTZ NOT NULL,              -- 答题时间
    client_request_id TEXT                          -- 幂等键（可选）；调用方传入则按 (student_id, client_request_id) 去重
);

CREATE INDEX IF NOT EXISTS pe_student_ts ON practice_event (student_id, ts DESC);
CREATE INDEX IF NOT EXISTS pe_kp_gin     ON practice_event USING GIN (kp_ids);
CREATE INDEX IF NOT EXISTS pe_student_q  ON practice_event (student_id, question_id);
-- 幂等：同一学生同一 client_request_id 只能写一行（仅对传了 client_request_id 的记录生效）
CREATE UNIQUE INDEX IF NOT EXISTS pe_client_req
    ON practice_event (student_id, client_request_id)
    WHERE client_request_id IS NOT NULL;

COMMENT ON TABLE  practice_event IS '学生做题历史：一次答题一行，按 student_id/question_id/kp_ids 过滤';
COMMENT ON COLUMN practice_event.kp_ids IS '该题考察的知识点 kp_id 列表（接口统一标识）；查某知识点序列用 WHERE kp_id = ANY(kp_ids)';
COMMENT ON COLUMN practice_event.client_request_id IS '幂等键（可选）：submit-answer 传入则按 (student_id, client_request_id) 去重，防网络重试重复更新掌握度';

-- 已有库迁移（新库已含上面定义，老库执行以下两句即可）：
-- ALTER TABLE practice_event ADD COLUMN IF NOT EXISTS client_request_id TEXT;
-- CREATE UNIQUE INDEX IF NOT EXISTS pe_client_req ON practice_event (student_id, client_request_id) WHERE client_request_id IS NOT NULL;
