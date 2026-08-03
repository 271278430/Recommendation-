-- 学情状态库建表脚本（容器首次启动自动执行）
-- 模型：Beta-Binomial 共轭推断，每个(学生, 知识点)存 alpha/beta/m_peak/last_ts
-- 数组下标 1..612 对应知识点 0 基索引 0..611（复用 data/kg_graph/kg_index.json）

-- 初始化辅助函数
CREATE OR REPLACE FUNCTION init_alpha() RETURNS real[] AS $$
  SELECT array_agg(2.0::real) FROM generate_series(1,612);
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION init_beta() RETURNS real[] AS $$
  SELECT array_agg(2.0::real) FROM generate_series(1,612);
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION init_m_peak() RETURNS real[] AS $$
  SELECT array_agg(0.5::real) FROM generate_series(1,612);
$$ LANGUAGE sql IMMUTABLE;

-- 1) 当前状态表（每学生一行）
CREATE TABLE IF NOT EXISTS student_mastery (
    student_id   BIGINT PRIMARY KEY,
    alpha        REAL[]    NOT NULL DEFAULT init_alpha()
                           CHECK (coalesce(array_length(alpha,1),0) = 612),
    beta         REAL[]    NOT NULL DEFAULT init_beta()
                           CHECK (coalesce(array_length(beta,1),0) = 612),
    m_peak       REAL[]    NOT NULL DEFAULT init_m_peak()
                           CHECK (coalesce(array_length(m_peak,1),0) = 612),
    last_ts      TIMESTAMPTZ[] DEFAULT NULL
                           CHECK (last_ts IS NULL OR coalesce(array_length(last_ts,1),0) = 612),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE  student_mastery IS '学生知识点掌握度状态：Beta 分布参数 alpha/beta + 历史峰值 + 上次练习时间';
COMMENT ON COLUMN student_mastery.alpha   IS 'real[612], 有效正确证据, 初始值 2(=P), 下标 1..612 = 知识点索引 0..611';
COMMENT ON COLUMN student_mastery.beta    IS 'real[612], 有效错误证据, 初始值 2(=P)';
COMMENT ON COLUMN student_mastery.m_peak  IS 'real[612], 历史峰值掌握度, 初始值 0.5, 用于重学节省加速';
COMMENT ON COLUMN student_mastery.last_ts IS 'timestamptz[612], 各知识点上次练习时间, 初始 NULL, 用于遗忘衰减';

-- 2) 变更事件日志（可选，用于回溯/分析掌握度如何变化）
CREATE TABLE IF NOT EXISTS mastery_event (
    id           BIGSERIAL PRIMARY KEY,
    student_id   BIGINT NOT NULL,
    kp_idx       INTEGER NOT NULL CHECK (kp_idx BETWEEN 1 AND 612),  -- 1..612, 与数组下标一致
    old_m        REAL,       -- 更新前掌握度 m = alpha/(alpha+beta)
    new_m        REAL,       -- 更新后掌握度
    delta        REAL,       -- new_m - old_m
    source       TEXT,       -- exam / homework / classwork
    task_id      TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_event_student_time ON mastery_event(student_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_kp           ON mastery_event(kp_idx);
