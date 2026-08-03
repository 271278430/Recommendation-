# 学情推荐服务 API 参考文档

> 版本：v2.3 · 最后更新：2026-07-29 · 基础路径：`http://{host}/api/v1`

---

## 一、约定

### 1.1 请求方式

- **GET**：读取。参数通过 query string 传递（`?key=value`，数组用同名参数重复传递，如 `?kp_ids=A&kp_ids=B`）
- **POST**：创建或计算。参数通过 JSON body 传递，`Content-Type: application/json`

### 1.2 类型记法

本文档输入参数统一用以下类型记法：

| 记法 | 含义 | 示例 |
|------|------|------|
| `int` | 整数 | `9060601` |
| `float` | 浮点数 | `0.6234` |
| `string` | 字符串 | `"exam_20251016_3"` |
| `bool` | 布尔 | `true` / `false` |
| `enum<a\|b\|c>` | 枚举，取其一 | `enum<exam\|homework\|classwork>` |
| `array<T>` | 数组；**query 中用同名参数重复传递** | `?kp_ids=A&kp_ids=B` |
| `object<K:V>` | 对象/字典 | `{"J03...": 5}`（key 为 kp_id，value 为 int） |

### 1.3 响应格式

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

> 所有响应——成功、业务错误、422 参数校验失败、**以及 404/405 等框架级错误**——都遵循此结构；错误时 `data` 通常携带定位信息（如 `invalid_kp_ids`、`errors`）。

### 1.4 错误码

| 码 | HTTP | 含义 |
|----|------|------|
| 0 | 200 | 成功 |
| 40001 | 400 / 422 | 参数错误（见下方注） |
| 40002 | 400 | 题目无知识点标签 |
| 40400 | 404 | 接口/路由不存在 |
| 40401 | 404 | 范围无效（scope 解析失败） |
| 40403 | 404 | 知识点 ID 不存在 |
| 40404 | 404 | 题目不存在 |
| 40501 | 405 | 请求方法不允许 |
| 50001 | 500 | 内部错误 |

> 注：`40001` 出现在两种场景——业务参数错误（**HTTP 400**，如 `ts` 越界、`aspects` 非法）与请求体校验失败（**HTTP 422**，Pydantic 拒绝非法字段，字段级报错在 `data.errors`）。`40400`/`40501` 为框架级错误（路由不匹配 / 方法不允许），同样走统一 ApiResponse。

### 1.5 公共概念

| 概念 | 格式 | 示例 |
|------|------|------|
| `student_id` | int | `9060601` |
| `kp_id` | string | `"J030004000200080003"` |
| `question_id` | string | `"exam_20251016_3"` |
| `score` | float ∈ [0, 1] | `0.0`（全错）~ `1.0`（全对） |
| `m`（掌握度） | float ∈ [0, 1] | `0.0`（完全不会）~ `1.0`（完全掌握） |

---

## 二、接口清单

| 方法 | 路径 | 用途 | 输入位置 |
|------|------|------|----------|
| GET | `/students` | 学生列表 | 无 |
| GET | `/students/{student_id}/mastery` | 掌握度 | path + query |
| POST | `/students/{student_id}/mastery` | 初始化/重置掌握度 | path + body |
| GET | `/students/{student_id}/learning-status` | 学情总览 | path + query |
| GET | `/students/{student_id}/practice-events` | 做题记录 | path + query |
| POST | `/students/{student_id}/practice-events` | 提交答案 | path + body |
| POST | `/students/{student_id}/recommendations` | 推题召回 | path + body |
| GET | `/knowledge-points` | 知识点列表 | 无 |
| GET | `/knowledge-graph/edges` | 图谱关系边 | 无 |

> **输入位置速记**：GET 接口的过滤条件走 query；POST 接口的业务数据走 JSON body；`student_id` 一律是 path 参数。

---

## 三、接口详情

> 每个接口给出：**输入参数表**（字段 / 位置 / 类型 / 必填 / 默认 / 取值约束 / 说明）→ 请求示例 → 响应示例 → 错误。
> 下方 curl 示例的主机地址为示例值 `10.50.243.143`，请按实际环境替换。

### 3.1 GET /students

> 返回所有学生 ID，供下拉框或批量操作。

**输入参数**

| 字段 | 位置 | 类型 | 必填 | 默认 | 取值约束 | 说明 |
|------|------|------|------|------|----------|------|
| — | — | — | — | — | — | 本接口无输入参数 |

**请求示例**

```bash
curl "http://10.50.243.143/api/v1/students"
```

**响应**

```json
{
  "code": 0,
  "msg": "ok",
  "data": [9060601, 9060602],
  "trace_id": "a1b2c3d4e5f6"
}
```

---

### 3.2 GET /students/{student_id}/mastery

> 查询某个学生在指定知识点上的掌握度（已考虑遗忘衰减）。
> 不传 `kp_ids` 则返回该学生在**所有知识点**上的掌握度。

**输入参数**

| 字段 | 位置 | 类型 | 必填 | 默认 | 取值约束 | 说明 |
|------|------|------|------|------|----------|------|
| `student_id` | path | `int` | ✅ | — | — | 学生 ID |
| `kp_ids` | query | `array<string>` | ❌ | 全部知识点 | kp_id 必须存在 | 知识点 ID；query 中同名参数重复传递可传多个；不传返回全部 |

**请求示例**

```bash
# 单个知识点
curl "http://10.50.243.143/api/v1/students/9060601/mastery?kp_ids=J030004000200080003"

# 多个知识点（同名参数重复）
curl "http://10.50.243.143/api/v1/students/9060601/mastery?kp_ids=J030004000200080003&kp_ids=J030005000200010002"

# 不传 kp_ids → 返回全部知识点
curl "http://10.50.243.143/api/v1/students/9060601/mastery"
```

**成功响应**

```json
{
  "code": 0,
  "msg": "ok",
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
  },
  "trace_id": "a1b2c3d4e5f6"
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
404  { "code": 40403, "msg": "部分 kp_id 不存在", "data": { "invalid_kp_ids": ["BAD_ID"] }, "trace_id": "a1b2c3d4e5f6" }
```

---

### 3.3 POST /students/{student_id}/mastery

> 初始化或重置某学生的掌握度：把历史做题数据（如摸底/分班考试）注入为先验，或冷启动一个新学生。
> 已有数据的学生会被**覆盖**（UPSERT），因此同时支持"初始化"和"重置"两种用途。

**输入参数**

| 字段 | 位置 | 类型 | 必填 | 默认 | 取值约束 | 说明 |
|------|------|------|------|------|----------|------|
| `student_id` | path | `int` | ✅ | — | — | 学生 ID |
| `correct_counts` | body | `object<string,int>` | ❌ | — | value ≥ 0 | 各知识点正确次数 `{kp_id: count}` |
| `wrong_counts` | body | `object<string,int>` | ❌ | — | value ≥ 0 | 各知识点错误次数 `{kp_id: count}` |
| `last_ts` | body | `object<string,string>` | ❌ | — | value 为合法 ISO8601 | 各知识点最后答题时间 `{kp_id: ISO8601}` |

> 三个 body 字段全空 = **冷启动**（所有知识点 m = 0.5）。注入公式：`m = (correct + P) / (correct + P + wrong + P)`，P = 2。
> `last_ts` 影响后续遗忘衰减的起算点；只注入先验不给时间戳时，该知识点 `has_data` 仍为 false。

**请求示例**

```bash
# 注入摸底考试先验
curl -X POST "http://10.50.243.143/api/v1/students/9060601/mastery" \
  -H "Content-Type: application/json" \
  -d '{
    "correct_counts": {"J030004000200080003": 5},
    "wrong_counts":   {"J030004000200080003": 2},
    "last_ts":        {"J030004000200080003": "2026-07-15T10:00:00Z"}
  }'

# 冷启动（三个字段全空 → 所有知识点 m = 0.5）
curl -X POST "http://10.50.243.143/api/v1/students/9060601/mastery" \
  -H "Content-Type: application/json" -d '{}'
```

**成功响应**（201 Created）——格式与 GET /mastery 一致，返回初始化后的掌握度：

```json
{
  "code": 0,
  "msg": "ok",
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
  },
  "trace_id": "a1b2c3d4e5f6"
}
```

**错误**

```
404  { "code": 40403, "msg": "部分 kp_id 不存在", "data": { "invalid_kp_ids": ["BAD_ID"] }, "trace_id": "a1b2c3d4e5f6" }
422  { "code": 40001, "msg": "请求参数校验失败", "data": { "errors": [{ "type": "value_error", "loc": ["body", "correct_counts", "J03"], "msg": "count 必须 >= 0" }] }, "trace_id": "a1b2c3d4e5f6" }  —— correct_counts/wrong_counts 为负，或 last_ts 非 ISO8601
```

---

### 3.4 GET /students/{student_id}/learning-status

> 查询某个学生的学情总览——包含掌握度概要、做题历史、可选的知识图谱边。
> 这是一个**聚合视图**，前端一次性拿到学情页需要的所有数据。

**输入参数**

| 字段 | 位置 | 类型 | 必填 | 默认 | 取值约束 | 说明 |
|------|------|------|------|------|----------|------|
| `student_id` | path | `int` | ✅ | — | — | 学生 ID |
| `scope_type` | query | `enum<module\|kp_ids>` | ✅ | — | `module` 或 `kp_ids` | 范围类型 |
| `scope_value` | query | `array<string>` | ✅ | — | module 时传模块名；kp_ids 时传知识点 ID | query 同名参数重复；module 取首个，kp_ids 可多值 |
| `aspects` | query | `array<enum<mastery\|history>>` | ❌ | `[mastery, history]` | 只允许 `mastery`/`history` | 要返回哪些子集 |
| `history_days` | query | `int` | ❌ | `30` | 1 ~ 365 | 历史时间窗（天） |
| `history_limit` | query | `int` | ❌ | `20` | 1 ~ 200 | 历史最大返回条数 |
| `mastery_detail` | query | `bool` | ❌ | `true` | — | 是否返回逐知识点的掌握度明细 |
| `include_graph` | query | `bool` | ❌ | `false` | — | 是否附加范围内的知识点关系边 |
| `edge_types` | query | `array<enum<prereq\|cooc>>` | ❌ | `[prereq, cooc]` | — | 要哪些边类型 |
| `cooc_min_weight` | query | `float` | ❌ | `0.25` | -1 ~ 1 | 共现边强度下限（NPMI） |

**请求示例**

```bash
# 知识点范围 + 掌握度与历史
curl "http://10.50.243.143/api/v1/students/9060601/learning-status?scope_type=kp_ids&scope_value=J030004000200080003&aspects=mastery&aspects=history&history_days=30&history_limit=20"

# 带知识图谱边
curl "http://10.50.243.143/api/v1/students/9060601/learning-status?scope_type=kp_ids&scope_value=J030004000200080003&include_graph=true&edge_types=prereq&edge_types=cooc&cooc_min_weight=0.25"

# 模块范围
curl "http://10.50.243.143/api/v1/students/9060601/learning-status?scope_type=module&scope_value=一、有理数"
```

**成功响应**

```json
{
  "code": 0,
  "msg": "ok",
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
      "summary": { "total_answered": 5, "wrong_count": 1, "accuracy": 0.80, "last_active_ts": "2026-07-17T10:30:00" },
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
  },
  "trace_id": "a1b2c3d4e5f6"
}
```

> `mastery` / `history` / `graph` 三个块是否出现取决于 `aspects` 与 `include_graph`。掌握度等级阈值：`未接触`（无数据）／`补弱` m<0.45 ／`巩固` m<0.65 ／`变式` m<0.80 ／`进阶` m≥0.80。

**错误**

```
404  { "code": 40401, "msg": "范围无效", "data": { "invalid_scope": ["不存在的模块"] }, "trace_id": "a1b2c3d4e5f6" }
400  { "code": 40001, "msg": "aspects 含非法值 ...", "trace_id": "a1b2c3d4e5f6" }
```

---

### 3.5 GET /students/{student_id}/practice-events

> 查询某个学生在指定知识点上的做题记录，按时间倒序排列。
> 可用于错题本（`wrong_only=true`）或做题历史展示。

**输入参数**

| 字段 | 位置 | 类型 | 必填 | 默认 | 取值约束 | 说明 |
|------|------|------|------|------|----------|------|
| `student_id` | path | `int` | ✅ | — | — | 学生 ID |
| `kp_id` | query | `string` | ✅ | — | kp_id 必须存在 | 知识点 ID（**单数**，只传一个） |
| `wrong_only` | query | `bool` | ❌ | `false` | — | true = 只返回错题 |
| `limit` | query | `int` | ❌ | `50` | 1 ~ 500 | 最大返回条数 |

> ⚠️ 注意：本接口的知识点参数是 **`kp_id`（单数）**，只接受一个值，与 `/mastery` 的 `kp_ids`（复数，可多值）不同。

**请求示例**

```bash
curl "http://10.50.243.143/api/v1/students/9060601/practice-events?kp_id=J030004000200080003&wrong_only=true&limit=20"
```

**成功响应**

```json
{
  "code": 0,
  "msg": "ok",
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
  },
  "trace_id": "a1b2c3d4e5f6"
}
```

**错误**

```
404  { "code": 40403, "msg": "kp_id 不存在", "data": { "invalid_kp_ids": ["FAKE"] }, "trace_id": "a1b2c3d4e5f6" }
```

---

### 3.6 POST /students/{student_id}/practice-events

> 提交一次答题结果。
>
> 服务端自动完成：题目知识点识别 → 权重计算 → 掌握度更新（7 步流水线）→ 做题记录入库。
> 返回每个关联知识点的掌握度变化（before → after）。

**输入参数**

| 字段 | 位置 | 类型 | 必填 | 默认 | 取值约束 | 说明 |
|------|------|------|------|------|----------|------|
| `student_id` | path | `int` | ✅ | — | — | 学生 ID |
| `question_id` | body | `string` | ✅ | — | 必须存在于题库 | 题目标识 |
| `score` | body | `float` | ✅ | — | 0 ~ 1 | 得分率；选择题一般 0 或 1 |
| `is_wrong` | body | `bool` | ❌ | 按 `score<0.5` 兜底 | — | 是否判错；主观题（部分得分）建议显式传，否则错题统计失真 |
| `source` | body | `enum<exam\|homework\|classwork>` | ❌ | `homework` | 三选一 | 来源 |
| `ts` | body | `string`（ISO8601） | ❌ | 当前时间 | now-30天 ~ now+1分钟 | 答题时间，如 `2026-07-17T10:30:00` |
| `equal_weights` | body | `bool` | ❌ | `false` | — | true=多知识点强制等权；false=自动用题库权重，兜底等权 |
| `client_request_id` | body | `string` | ❌ | — | — | 幂等键；传入则相同键的重复请求直接返回已处理结果 |

**请求示例**

```bash
curl -X POST "http://10.50.243.143/api/v1/students/9060601/practice-events" \
  -H "Content-Type: application/json" \
  -d '{
    "question_id": "exam_20251016_3",
    "score": 1.0,
    "is_wrong": false,
    "source": "homework",
    "ts": "2026-07-17T10:30:00",
    "equal_weights": false,
    "client_request_id": "req-uuid-001"
  }'
```

**成功响应**（201 Created）

```json
{
  "code": 0,
  "msg": "ok",
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
  },
  "trace_id": "a1b2c3d4e5f6"
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
  "msg": "ok",
  "data": {
    "student_id": 9060601,
    "question_id": "exam_20251016_3",
    "idempotent_replay": true,
    "message": "该 client_request_id 已处理过，未重复更新掌握度"
  },
  "trace_id": "a1b2c3d4e5f6"
}
```

**错误**

```
404  { "code": 40404, "msg": "题目不存在", "trace_id": "a1b2c3d4e5f6" }
400  { "code": 40002, "msg": "题目没有知识点标签，无法更新掌握度", "trace_id": "a1b2c3d4e5f6" }
400  { "code": 40001, "msg": "题目的知识点无法解析", "trace_id": "a1b2c3d4e5f6" }
400  { "code": 40001, "msg": "ts 超出允许范围（now-30天 ~ now+1分钟）", "trace_id": "a1b2c3d4e5f6" }
422  { "code": 40001, "msg": "请求参数校验失败", "data": { "errors": [{ "type": "literal_error", "loc": ["body", "source"], "msg": "..." }] }, "trace_id": "a1b2c3d4e5f6" }  —— source 非法值 / score 越界 / 字段缺失
```

---

### 3.7 POST /students/{student_id}/recommendations

> 根据学生当前掌握度，在指定知识点范围内召回 ZPD（最近发展区）候选题目。
>
> 只做召回（p ∈ [0.30, 0.80]），不做去重和排除已做题（由下游排序层负责）。

**输入参数**

| 字段 | 位置 | 类型 | 必填 | 默认 | 取值约束 | 说明 |
|------|------|------|------|------|----------|------|
| `student_id` | path | `int` | ✅ | — | — | 学生 ID |
| `kp_ids` | body | `array<string>` | ✅ | — | 至少 1 个；kp_id 必须存在 | 要推题的知识点 ID 列表 |

**请求示例**

```bash
curl -X POST "http://10.50.243.143/api/v1/students/9060601/recommendations" \
  -H "Content-Type: application/json" \
  -d '{"kp_ids": ["J030004000200080003", "J030005000200010002"]}'
```

**成功响应**

```json
{
  "code": 0,
  "msg": "ok",
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
  },
  "trace_id": "a1b2c3d4e5f6"
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
404  { "code": 40403, "msg": "部分 kp_id 不存在", "data": { "invalid_kp_ids": ["BAD"] }, "trace_id": "a1b2c3d4e5f6" }
422  { "code": 40001, "msg": "请求参数校验失败", "data": { "errors": [{ "type": "too_short", "loc": ["body", "kp_ids"], "msg": "..." }] }, "trace_id": "a1b2c3d4e5f6" }  —— kp_ids 为空列表或缺字段
```

---

### 3.8 GET /knowledge-points

> 全量知识点列表，供前端筛选/搜索。

**输入参数**

| 字段 | 位置 | 类型 | 必填 | 默认 | 取值约束 | 说明 |
|------|------|------|------|------|----------|------|
| — | — | — | — | — | — | 本接口无输入参数 |

**请求示例**

```bash
curl "http://10.50.243.143/api/v1/knowledge-points"
```

**响应**

```json
{
  "code": 0,
  "msg": "ok",
  "data": [
    { "kp_id": "J0300010001000100010006", "kp_name": "正数和负数", "module": "一、有理数" }
  ],
  "trace_id": "a1b2c3d4e5f6"
}
```

---

### 3.9 GET /knowledge-graph/edges

> 全量知识图谱关系边，供前端渲染知识图谱。

**输入参数**

| 字段 | 位置 | 类型 | 必填 | 默认 | 取值约束 | 说明 |
|------|------|------|------|------|----------|------|
| — | — | — | — | — | — | 本接口无输入参数 |

**请求示例**

```bash
curl "http://10.50.243.143/api/v1/knowledge-graph/edges"
```

**响应**

```json
{
  "code": 0,
  "msg": "ok",
  "data": [
    { "from": "J03000300010003", "to": "J030004000200080003", "type": "prereq", "subtype": "solid" },
    { "from": "J03000300040005", "to": "J030004000200080003", "type": "cooc", "weight": 0.4521 }
  ],
  "trace_id": "a1b2c3d4e5f6"
}
```

| 字段 | 说明 |
|------|------|
| `type` | `prereq`（先修关系）或 `cooc`（共现关系） |
| `subtype` | 先修边的子类型（`solid`/`thin`） |
| `weight` | 共现边的 NPMI 强度 |

---

## 四、快速开始

> 以下命令可直接复制到终端运行；`10.50.243.143` 请按实际主机替换。

```bash
# ─── 1. 学生列表 (GET /students) ───
curl "http://10.50.243.143/api/v1/students"

# ─── 2. 查掌握度 (GET /students/{id}/mastery) ───
# 单个知识点
curl "http://10.50.243.143/api/v1/students/9060601/mastery?kp_ids=J030004000200080003"
# 多个知识点（query 传多次）
curl "http://10.50.243.143/api/v1/students/9060601/mastery?kp_ids=J030004000200080003&kp_ids=J030005000200010002"
# 不传 kp_ids → 返回全部知识点
curl "http://10.50.243.143/api/v1/students/9060601/mastery"

# ─── 3. 初始化/重置掌握度 (POST /students/{id}/mastery) ───
curl -X POST "http://10.50.243.143/api/v1/students/9060601/mastery" \
  -H "Content-Type: application/json" \
  -d '{
    "correct_counts": {"J030004000200080003": 5},
    "wrong_counts":   {"J030004000200080003": 2},
    "last_ts":        {"J030004000200080003": "2026-07-15T10:00:00Z"}
  }'
# 冷启动（三个字段全空 → 所有知识点 m=0.5）
curl -X POST "http://10.50.243.143/api/v1/students/9060601/mastery" \
  -H "Content-Type: application/json" -d '{}'

# ─── 4. 学情总览 (GET /students/{id}/learning-status) ───
# 知识点范围 + 全部子集
curl "http://10.50.243.143/api/v1/students/9060601/learning-status?scope_type=kp_ids&scope_value=J030004000200080003&aspects=mastery&aspects=history&history_days=30&history_limit=20"
# 带知识图谱边
curl "http://10.50.243.143/api/v1/students/9060601/learning-status?scope_type=kp_ids&scope_value=J030004000200080003&include_graph=true&edge_types=prereq&edge_types=cooc&cooc_min_weight=0.25"
# 模块范围
curl "http://10.50.243.143/api/v1/students/9060601/learning-status?scope_type=module&scope_value=一、有理数"

# ─── 5. 查做题记录 (GET /students/{id}/practice-events) ───
curl "http://10.50.243.143/api/v1/students/9060601/practice-events?kp_id=J030004000200080003&wrong_only=true&limit=20"

# ─── 6. 提交答案 (POST /students/{id}/practice-events) ───
curl -X POST "http://10.50.243.143/api/v1/students/9060601/practice-events" \
  -H "Content-Type: application/json" \
  -d '{
    "question_id": "exam_20251016_3",
    "score": 1.0,
    "is_wrong": false,
    "source": "homework",
    "ts": "2026-07-17T10:30:00",
    "equal_weights": false,
    "client_request_id": "req-uuid-001"
  }'

# ─── 7. 推题召回 (POST /students/{id}/recommendations) ───
curl -X POST "http://10.50.243.143/api/v1/students/9060601/recommendations" \
  -H "Content-Type: application/json" \
  -d '{"kp_ids": ["J030004000200080003", "J030005000200010002"]}'

# ─── 8. 知识点列表 (GET /knowledge-points) ───
curl "http://10.50.243.143/api/v1/knowledge-points"

# ─── 9. 知识图谱关系边 (GET /knowledge-graph/edges) ───
curl "http://10.50.243.143/api/v1/knowledge-graph/edges"
```
