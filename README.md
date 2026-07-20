# 学情推荐接口服务

个性化推题 HTTP 接口服务（FastAPI）。基于 Beta-Binomial 掌握度模型 + IRT，提供学情查询、推题召回、答题更新掌握度等接口。

## 快速开始

```bash
cd /data/shanghui/Recommend_question
python3 -m service.main          # 起 http://0.0.0.0:8800
./run_tests.sh                   # 一键全量测试（80 用例）
```

依赖 PostgreSQL，见 `mastery_store/docker-compose.yml`（端口 5433，首次启动自动建表）。

## 目录结构

```
service/          接口核心（api/ 路由 + svc/ 业务 + core/ 基建 + main.py）
tests/            pytest 套件
mastery_store/    数据层（掌握度模型 mastery_store.py + 做题事件 practice.py + 建表 SQL）
data/             题库 + 知识图谱(kg_graph/) + 考试实际数据
scripts/          离线工具（建图/标注/DB初始化/评估/调参，用 python -m scripts.xxx 运行）
docs/             设计与说明文档
conftest.py       pytest 根配置
pyproject.toml
```

分层依赖方向：`api → svc → core → mastery_store → data/`，严禁反向。

## 接口（/api/v1 前缀，统一返回 {code,msg,data,trace_id}）

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/recommend/recall` | 粗召回：ZPD 范围候选题 |
| POST | `/mastery/forget` | 遗忘衰减后的掌握度 |
| POST | `/practice/kp-sequence` | 某知识点做题序列 |
| POST | `/practice/submit-answer` | 答题更新掌握度 + 写记录 |
| POST | `/student/learning-status` | 学情（掌握度+历史+图谱） |
| GET | `/students` `/knowledge-points` `/graph/edges` | 元数据列表 |
| GET | `/health` | 健康检查 |

**接口全貌、请求/响应字段、错误码、业务逻辑详见 [docs/接口现状文档.md](docs/接口现状文档.md)（活文档，随改动维护）。**

## 文档索引（docs/）

- [接口现状文档](docs/接口现状文档.md) — **程序员首查**：接口全貌 + 统一约定 + 错误码
- [ARCHITECTURE](docs/ARCHITECTURE.md) — 项目架构地图
- [推荐服务接口文档](docs/推荐服务接口文档.md) — 5 个核心接口 API 详解
- [接口数据规范](docs/接口数据规范.md) — 统一数据格式约定
- [数据库表设计文档](docs/数据库表设计文档.md) — PG 三张表设计
- [学情建模与学情状态更新机制建模V1](docs/学情建模与学情状态更新机制建模V1.md) — 掌握度算法（Beta-Binomial + 7步更新）
- [mastery_store/README.md](mastery_store/README.md) — 数据层说明
