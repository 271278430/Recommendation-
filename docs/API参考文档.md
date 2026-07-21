# 学情推荐服务 API 参考文档

> 版本：v2.1 · 最后更新：2026-07-21 · 基础路径：`http://{host}/api/v1`

---

## 一、约定

### 1.1 请求方式

- **GET**：读取。参数通过 query string 传递（`?key=value`）
- **POST**：创建或计算。参数通过 JSON body 传递，`Content-Type: application/json`

### 1.2 响应格式

所有接口统一返回：

```json
{
  "code": 0,
  "msg": "ok",
  "data": { ... },
  "trace_id": "a1b2c3d4e5f6"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | int | 0 = 成功；非 0 见错误码表 |
| `msg` | string | 成功为 `"ok"`；失败为错误描述 |
| `data` | any | 具体业务数据，见各接口 |
| `trace_id` | string | 12 位请求追踪 ID，响应头 `X-Trace-Id` 同步返回 |

### 1.3 错误码

| 码 | HTTP | 含义 |
|----|------|------|
| 0 | 200 | 成功 |
| 40001 | 400 | 参数错误 |
| 40002 | 400 | 题目无知识点标签 |
| 40401 | 404 | 范围无效 |
| 40403 | 404 | 知识点 ID 不存在 |
| 40404 | 404 | 题目不存在 |
| 50001 | 500 | 内部错误 |

### 1.4 公共概念

| 概念 | 格式 | 示例 |
|------|------|------|
| `student_id` | int | `9060601` |
| `kp_id` | string | `"J030004000200080003"` |
| `question_id` | string | `"exam_20251016_3"` |
| `score` | float | `0.0`（全错）~ `1.0`（全对） |
| `m`（掌握度） | float | `0.0`（完全不会）~ `1.0`（完全掌握） |

---

## 二、接口清单

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/students` | 学生列表 |
| GET | `/students/{student_id}/mastery` | 掌握度 |
| POST | `/students/{student_id}/mastery` | 初始化/重置掌握度 |
| GET | `/students/{student_id}/learning-status` | 学情总览 |
| GET | `/students/{student_id}/practice-events` | 做题记录 |
| POST | `/students/{student_id}/practice-events` | 提交答案 |
| POST | `/students/{student_id}/recommendations` | 推题召回 |
| GET | `/knowledge-points` | 知识点列表 |
| GET | `/knowledge-graph/edges` | 图谱关系边 |

---

## 三、接口详情

### 3.1 GET /students

> 返回所有学生 ID，供下拉框或批量操作。

**请求**

```
GET /students
```

**响应**

```json
{
  "code": 0,
  "data": [9060601, 9060602, ...]
}
```

---

### 3.2 GET /students/{student_id}/mastery

> 查询某个学生在指定知识点上的掌握度（已考虑遗忘衰减）。
> 不传 `kp_ids` 则返回该学生在**所有知识点**上的掌握度。

**请求**

```
GET /api/v1/students/{student_id}/mastery?kp_ids=J030004000200080003&kp_ids=J030005000200010002
```

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `student_id` | path | int | ✅ | 学生 ID |
| `kp_ids` | query | string[] | 否 | 知识点 ID；可传多个；不传返回全部 |

**成功响应**

```json
{
  "code": 0,
  "data": {
    "student_id": 9060601,
    "now": "2026-07-20T12:00:00",
    "items": [
      {
        "kp_id": "J030004000200080003",
        "kp_name": "勾股定理",
        "m": 0.6234,
        "m_peak": 0.7800,
        "N": 8.00,
        "last_ts": "2026-07-17T10:30:00",
        "days_since": 3.06,
        "has_data": true
      }
    ]
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `items[].m` | float | 衰减后掌握度，∈ [0, 1] |
| `items[].m_peak` | float | 历史峰值掌握度 |
| `items[].N` | float | 总证据量（相当于"做过的题数"） |
| `items[].days_since` | float | 距上次做题天数 |
| `items[].has_data` | bool | false 表示冷启动（无记录），此时 m = 0.5 |

**错误**

```
404  { "code": 40403, "msg": "部分 kp_id 不存在", "data": { "invalid_kp_ids": ["BAD_ID"] } }
```

---

### 3.3 POST /students/{student_id}/mastery

> 初始化或重置某学生的掌握度：把历史做题数据（如摸底/分班考试）注入为先验，或冷启动一个新学生。
> 已有数据的学生会被**覆盖**（UPSERT），因此同时支持"初始化"和"重置"两种用途。

**请求**

```
POST /api/v1/students/9060601/mastery
Content-Type: application/json

{
  "correct_counts": { "J030004000200080003": 5 },
  "wrong_counts":   { "J030004000200080003": 2 },
  "last_ts":        { "J030004000200080003": "2026-07-15T10:00:00Z" }
}
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `student_id` | int | ✅ | — | 路径参数 |
| `correct_counts` | object | 否 | — | 各知识点正确次数 `{kp_id: count}`，count ≥ 0 |
| `wrong_counts` | object | 否 | — | 各知识点错误次数 `{kp_id: count}`，count ≥ 0 |
| `last_ts` | object | 否 | — | 各知识点最后答题时间 `{kp_id: ISO8601}` |

> 三个字段全空 = **冷启动**（所有知识点 m = 0.5）。注入公式：`m = (correct + P) / (correct + P + wrong + P)`，P = 2。
> `last_ts` 影响后续遗忘衰减的起算点；只注入先验不给时间戳时，该知识点 `has_data` 仍为 false。

**成功响应**（201 Created）——格式与 GET /mastery 一致，返回初始化后的掌握度：

```json
{
  "code": 0,
  "data": {
    "student_id": 9060601,
    "now": "2026-07-21T10:00:00",
    "items": [
      {
        "kp_id": "J030004000200080003",
        "kp_name": "勾股定理",
        "m": 0.6364,
        "m_peak": 0.6364,
        "N": 11.00,
        "last_ts": "2026-07-15T10:00:00",
        "days_since": 6.0,
        "has_data": true
      }
    ]
  }
}
```

**错误**

```
404  { "code": 40403, "msg": "部分 kp_id 不存在", "data": { "invalid_kp_ids": ["BAD_ID"] } }
422  { "detail": [...] }  —— correct_counts/wrong_counts 为负，或 last_ts 非 ISO8601
```

---

### 3.4 GET /students/{student_id}/learning-status

> 查询某个学生的学情总览——包含掌握度概要、做题历史、可选的知识图谱边。
> 这是一个**聚合视图**，前端一次性拿到学情页需要的所有数据。

**请求**

```
GET /api/v1/students/9060601/learning-status?scope_type=kp_ids&scope_value=J030004000200080003&aspects=mastery&aspects=history&history_days=30&history_limit=20
```

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|------|
| `student_id` | path | int | ✅ | — | 学生 ID |
| `scope_type` | query | enum | ✅ | — | `module`（模块） 或 `kp_ids`（知识点 ID 列表） |
| `scope_value` | query | string[] | ✅ | — | module 时传模块名（单个）；kp_ids 时传 ID（可多个） |
| `aspects` | query | string[] | 否 | `mastery,history` | 子集：`mastery`/`history` |
| `history_days` | query | int | 否 | 30 | 历史时间窗（天），范围 1–365 |
| `history_limit` | query | int | 否 | 20 | 历史最大返回条数，范围 1–200 |
| `mastery_detail` | query | bool | 否 | true | 是否返回逐知识点的掌握度明细 |
| `include_graph` | query | bool | 否 | false | 是否附加知识点关系边 |
| `edge_types` | query | string[] | 否 | `prereq,cooc` | 边类型 |
| `cooc_min_weight` | query | float | 否 | 0.25 | 共现边强度下限（NPMI），范围 -1~1 |

**成功响应**

```json
{
  "code": 0,
  "data": {
    "student_id": 9060601,
    "now": "2026-07-20T12:00:00",
    "scope": { "type": "kp_ids", "value": ["J030004000200080003"], "kp_count": 1 },
    "mastery": {
      "summary": {
        "total_kp": 1,
        "has_data_kp": 1,
        "avg_m": 0.6234,
        "level_counts": { "未接触": 0, "补弱": 0, "巩固": 1, "变式": 0, "进阶": 0 },
        "weakest": [{ "kp_id": "J030004000200080003", "kp_name": "勾股定理", "m": 0.6234 }],
        "stalest": [{ "kp_id": "J030004000200080003", "kp_name": "勾股定理", "days_since": 3.06 }]
      },
      "items": [
        {
          "kp_id": "J030004000200080003",
          "kp_name": "勾股定理",
          "m": 0.6234,
          "level": "巩固",
          "N": 8.00,
          "has_data": true,
          "days_since": 3.06,
          "practice_count": 5,
          "recent_wrong_count": 1
        }
      ]
    },
    "history": {
      "summary": { "total_answered": 5, "accuracy": 0.80, "last_active_ts": "2026-07-17T10:30:00" },
      "events": [
        {
          "question_id": "exam_20251016_3",
          "ts": "2026-07-17T10:30:00",
          "ques_type": "单选题",
          "difficulty": "较易",
          "score": 1.0,
          "is_wrong": false,
          "kp_ids": ["J030004000200080003"]
        }
      ]
    },
    "graph": {
      "edges": [
        { "from": "J03000300010003", "to": "J030004000200080003", "type": "prereq", "subtype": "solid" }
      ]
    }
  }
}
```

**错误**

```
404  { "code": 40401, "msg": "范围无效", "data": { "invalid_scope": ["不存在的模块"] } }
400  { "code": 40001, "msg": "aspects 含非法值 ..." }
```

---

### 3.5 GET /students/{student_id}/practice-events

> 查询某个学生在指定知识点上的做题记录，按时间倒序排列。
> 可用于错题本（`wrong_only=true`）或做题历史展示。

**请求**

```
GET /api/v1/students/9060601/practice-events?kp_id=J030004000200080003&wrong_only=true&limit=20
```

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|------|
| `student_id` | path | int | ✅ | — | 学生 ID |
| `kp_id` | query | string | ✅ | — | 知识点 ID |
| `wrong_only` | query | bool | 否 | false | true=只返回错题 |
| `limit` | query | int | 否 | 50 | 最大条数，范围 1–500 |

**成功响应**

```json
{
  "code": 0,
  "data": {
    "student_id": 9060601,
    "kp_id": "J030004000200080003",
    "count": 2,
    "events": [
      {
        "question_id": "exam_20251016_3",
        "ts": "2026-07-17T10:30:00",
        "ques_type": "单选题",
        "difficulty": "较易",
        "score": 0.0,
        "is_wrong": true,
        "kp_ids": ["J030004000200080003"]
      },
      {
        "question_id": "exam_20251016_5",
        "ts": "2026-07-16T14:00:00",
        "ques_type": "填空题",
        "difficulty": "适中",
        "score": 1.0,
        "is_wrong": false,
        "kp_ids": ["J030004000200080003"]
      }
    ]
  }
}
```

**错误**

```
404  { "code": 40403, "msg": "kp_id 不存在", "data": { "invalid_kp_ids": ["FAKE"] } }
```

---

### 3.6 POST /students/{student_id}/practice-events

> 提交一次答题结果。
>
> 服务端自动完成：题目知识点识别 → 权重计算 → 掌握度更新（7 步流水线）→ 做题记录入库。
> 返回每个关联知识点的掌握度变化（before → after）。

**请求**

```
POST /api/v1/students/9060601/practice-events
Content-Type: application/json

{
  "question_id": "exam_20251016_3",
  "score": 1.0,
  "is_wrong": false,
  "source": "homework",
  "ts": "2026-07-17T10:30:00",
  "equal_weights": false,
  "client_request_id": "req-uuid-001"
}
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `student_id` | int | ✅ | — | 路径参数 |
| `question_id` | string | ✅ | — | 题目标识 |
| `score` | float | ✅ | — | 得分率，∈ [0, 1] |
| `is_wrong` | bool | 否 | score < 0.5 | 是否判错。主观题建议显式传 |
| `source` | string | 否 | `homework` | 枚举：`exam` / `homework` / `classwork` |
| `ts` | string | 否 | 当前时间 | ISO 8601，范围 now-30天 ~ now+1分钟 |
| `equal_weights` | bool | 否 | false | true=多知识点等权；false=自动加权 |
| `client_request_id` | string | 否 | — | 幂等键；重复提交直接返回已处理结果 |

**成功响应**（201 Created）

```json
{
  "code": 0,
  "data": {
    "student_id": 9060601,
    "question_id": "exam_20251016_3",
    "score": 1.0,
    "is_wrong": false,
    "question": {
      "ques_type": "单选题",
      "difficulty": "较易",
      "kp_ids": ["J030004000200080003"]
    },
    "updated_kps": [
      {
        "kp_id": "J030004000200080003",
        "kp_name": "勾股定理",
        "m_before": 0.5000,
        "m_after": 0.6234,
        "delta": 0.1234,
        "level_before": "巩固",
        "level_after": "巩固"
      }
    ]
  }
}
```

| 字段 | 说明 |
|------|------|
| `updated_kps[].m_before` | 答题前掌握度 |
| `updated_kps[].m_after` | 答题后掌握度 |
| `updated_kps[].delta` | 变化量（正=进步，负=退步） |
| `question.kp_ids` | 题目考察的知识点（从题库自动识别） |

**幂等命中**（相同 `client_request_id` 重复提交）

```json
{
  "code": 0,
  "data": {
    "student_id": 9060601,
    "question_id": "exam_20251016_3",
    "idempotent_replay": true,
    "message": "该 client_request_id 已处理过，未重复更新掌握度"
  }
}
```

**错误**

```
404  { "code": 40404, "msg": "题目不存在" }
400  { "code": 40002, "msg": "题目没有知识点标签，无法更新掌握度" }
400  { "code": 40001, "msg": "题目的知识点无法解析" }
400  { "code": 40001, "msg": "ts 超出允许范围（now-30天 ~ now+1分钟）" }
422  { "detail": [...] }  —— source 非法值
```

---

### 3.7 POST /students/{student_id}/recommendations

> 根据学生当前掌握度，在指定知识点范围内召回 ZPD（最近发展区）候选题目。
>
> 只做召回（p ∈ [0.30, 0.80]），不做去重和排除已做题（由下游排序层负责）。

**请求**

```
POST /api/v1/students/9060601/recommendations
Content-Type: application/json

{
  "kp_ids": ["J030004000200080003", "J030005000200010002"]
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `student_id` | int | ✅ | 路径参数 |
| `kp_ids` | string[] | ✅ | 知识点 ID 列表，至少 1 个 |

**成功响应**

```json
{
  "code": 0,
  "data": {
    "student_id": 9060601,
    "groups": [
      {
        "kp_id": "J030004000200080003",
        "kp_name": "勾股定理",
        "m": 0.6234,
        "has_data": true,
        "direction": "巩固",
        "question_count": 15,
        "questions": [
          {
            "question_id": "Q_001",
            "ques_type": "单选题",
            "difficulty": "适中",
            "d": 0.55,
            "p": 0.6234,
            "kp_ids": ["J030004000200080003"]
          }
        ]
      }
    ],
    "empty_kps": [
      {
        "kp_id": "J030005000200010002",
        "kp_name": "二次函数",
        "reason": "题库中该知识点无 p∈[0.30,0.80] 的题"
      }
    ]
  }
}
```

| 字段 | 说明 |
|------|------|
| `groups[].m` | 学生在该知识点的当前掌握度 |
| `groups[].direction` | 学情方向（补弱/巩固/变式/进阶/未接触） |
| `groups[].questions[].p` | IRT 预测正确概率（越高越适合该学生） |
| `groups[].questions[].d` | 题目难度值（0~1，越大越难） |
| `empty_kps` | 没能召回题目的知识点及原因 |

**错误**

```
404  { "code": 40403, "msg": "部分 kp_id 不存在", "data": { "invalid_kp_ids": ["BAD"] } }
422   —— kp_ids 为空列表
```

---

### 3.8 GET /knowledge-points

> 全量知识点列表，供前端筛选/搜索。

```
GET /knowledge-points
```

**响应**

```json
{
  "code": 0,
  "data": [
    { "kp_id": "J0300010001000100010006", "kp_name": "正数和负数", "module": "一、有理数" }
  ]
}
```

---

### 3.9 GET /knowledge-graph/edges

> 全量知识图谱关系边，供前端渲染知识图谱。

```
GET /knowledge-graph/edges
```

**响应**

```json
{
  "code": 0,
  "data": [
    { "from": "J03000300010003", "to": "J030004000200080003", "type": "prereq", "subtype": "solid" },
    { "from": "J03000300040005", "to": "J030004000200080003", "type": "cooc", "weight": 0.4521 }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `type` | `prereq`（先修关系）或 `cooc`（共现关系） |
| `subtype` | 先修边的子类型（`solid`/`thin`） |
| `weight` | 共现边的 NPMI 强度 |

---

## 四、快速开始

```bash
BASE="http://localhost/api/v1"

# 查学生列表
curl $BASE/students

# 查掌握度
curl "$BASE/students/9060601/mastery?kp_ids=J030004000200080003"

# 初始化/重置掌握度（注入摸底考试先验）
curl -X POST $BASE/students/9060601/mastery \
  -H "Content-Type: application/json" \
  -d '{"correct_counts":{"J030004000200080003":5},"wrong_counts":{"J030004000200080003":2}}'

# 查学情
curl "$BASE/students/9060601/learning-status?scope_type=kp_ids&scope_value=J030004000200080003"

# 提交答案
curl -X POST $BASE/students/9060601/practice-events \
  -H "Content-Type: application/json" \
  -d '{"question_id":"exam_20251016_3","score":1.0,"source":"homework"}'

# 推题召回
curl -X POST $BASE/students/9060601/recommendations \
  -H "Content-Type: application/json" \
  -d '{"kp_ids":["J030004000200080003"]}'
```
