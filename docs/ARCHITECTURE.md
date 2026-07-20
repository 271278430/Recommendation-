# 项目架构地图（人话版）

> 一句话：项目分**三层** + 离线工具 + 文档。
> `mastery_store`（底层数据层）← `service`（你的 FastAPI 服务）→ `data`（题库/图谱）。

---

## 0. 全景图

```
Recommend_question/
│
├── mastery_store/     ① 底层数据层：掌握度的存储 + 算法（独立、共享，不依赖任何上层）
├── service/           ② 应用层：你的 FastAPI 服务（遗忘/推题接口）—— 依赖 ①
├── data/              ③ 数据文件：题库 + 知识图谱（被 ① ② 读）
├── tests/             ② 的自动化测试
│
├── build_prereq.py    离线脚本：建知识图谱（先修关系）
├── build_cooc.py      离线脚本：建知识图谱（共现关系）
├── augment_tags.py    离线脚本：用 LLM 给题补知识点标签
├── annotate_kp_weights.py  离线脚本：标注题目知识点权重
├── conftest.py        pytest 配置（不是业务脚本）
├── pyproject.toml     依赖声明
│
└── *.md               文档（学情建模、推题方案、接口设计等）
```

**依赖方向**：`service` → `mastery_store` → `data`。**反过来不依赖**（底层不知道上层存在）。

---

## ① `mastery_store/` —— 底层数据层

掌握度的"数据库 + 计算公式"。学生每答一题，这里更新掌握度；推荐/遗忘服务来这里读。

| 文件 | 干什么 | 你需要懂吗 |
|---|---|---|
| `mastery_store.py` | **核心**：连 PostgreSQL 读写掌握度（α,β,m_peak,last_ts）+ 7 步更新流水线 + `forget_decay` 衰减公式 + 所有常量（P、τ 等） | 是（算法心脏） |
| `__init__.py` | 包入口：让别人能 `import mastery_store` | 否 |
| `web_app.py` | 一个 Flask 小网页，调试用（看掌握度曲线） | 否（跟服务无关） |
| `init_from_exam.py` | 冷启动：从考试数据初始化掌握度 | 否（一次性工具） |
| `eval_mechanism.py` | 评测机制准不准（AUC 等） | 否（离线分析） |
| `grid_search.py` | 调参：扫超参找最优 | 否（离线分析） |
| `test_*.py` | 它自己的单元测试 | 否 |

> 你日常只需要知道：**`mastery_store.py` 是掌握度的存储和公式来源**，别的文件是它的工具链，可以忽略。

---

## ② `service/` —— 你的 FastAPI 服务（重点）

这是你新搭的、分层的企业级结构。**为什么分 `core/svc/api`？** 因为不同职责要分开：`api` 管 HTTP、`svc` 管业务、`core` 管所有接口共用的基础设施。

```
service/
├── main.py            🚪 门口：组装 App（挂中间件、路由），启动服务
│
├── core/              🧰 基础设施（所有接口共用，跟具体业务无关）
│   ├── config.py         配置：端口、路径、日志级别（从环境变量读）
│   ├── response.py       统一返回格式 {code,msg,data,trace_id} + 错误码 + 业务异常
│   ├── logging.py        日志 + trace_id（每条日志带 [trace_id]）
│   ├── middleware.py     中间件：每请求贴 trace_id、兜底所有异常
│   └── deps.py           启动准备：建 kp_id 映射（服务启动时跑一次）
│
├── svc/               🧠 业务逻辑（算东西，不碰 HTTP）
│   └── forget.py         遗忘核心：衰减公式（decayed_for_kp）、读掌握度、解析 kp_id
│
└── api/               🌐 HTTP 路由（接请求、派活，不做计算）
    └── mastery.py        遗忘接口路由：POST /api/v1/mastery/forget
```

**记忆口诀**：
- `api/` = 前台（接客、派单）
- `svc/` = 后厨（真正做菜=算衰减）
- `core/` = 物业水电（每家店都要用，跟做什么菜无关）
- `main.py` = 大门（把店开起来）

---

## 一个请求是怎么走完这些文件的（让分层活起来）

学生调 `POST /api/v1/mastery/forget`：

```
1. main.py          门开了，请求进来
2. core/middleware   给这单贴个 trace_id 号牌，记一笔"来单了"
3. api/mastery.py    前台接单：校验 student_id/kp_ids 对不对
4. svc/forget.py     后厨做菜：算"经过遗忘后的掌握度 m"
5. mastery_store     去仓库取原始数据（α,β,last_ts）—— 跨到第①层
6. svc/forget.py     拿到原始数据，套衰减公式，算出 m=0.6272
7. api/mastery.py    把结果装进统一格式 {code,msg,data,trace_id}
8. core/middleware   回去时把 trace_id 也塞进响应头
9. main.py           出门，返回给学生
```

**任何一层只干自己的事**：路由不算法、算法不连库、连库不碰 HTTP。这就是分层的意义——改一层不牵连别的。

---

## ③ `data/` —— 数据文件

| 内容 | 说明 |
|---|---|
| `初中_九年级*.jsonl` | 题库（8 万多题） |
| `kg_graph/` | 知识图谱：知识点索引、先修关系、共现关系、kp_id 映射缓存 |
| `九年级数学考试实际数据/` | 真实考试答题数据（冷启动/评测用） |
| `question_kp_weights.jsonl` | 题目-知识点权重 |

---

## 你现在该看哪里

- **想懂掌握度怎么算** → `mastery_store/mastery_store.py` + 《学情建模V1.md》
- **想懂遗忘接口** → `service/svc/forget.py`（算法）+ `service/api/mastery.py`（路由）+ 《遗忘机制接口设计.md》
- **想懂推题整体方案** → 《推荐流程方案V2_新旧结合.md》
- **想加新接口**（如推题 step1）→ 在 `service/api/` 加路由、`service/svc/` 加业务，`core/` 直接复用
