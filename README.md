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

## 接口（REST 风格，/api/v1 前缀，统一返回 {code,msg,data,trace_id}）

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/students` | 学生列表 |
| GET | `/students/{id}/mastery` | 遗忘衰减后的掌握度 |
| GET | `/students/{id}/learning-status` | 学情总览（掌握度+历史+图谱） |
| GET | `/students/{id}/practice-events` | 做题序列 |
| POST | `/students/{id}/practice-events` | 提交答案、更新掌握度 |
| POST | `/students/{id}/recommendations` | 粗召回：ZPD 候选题 |
| GET | `/knowledge-points` | 知识点列表 |
| GET | `/knowledge-graph/edges` | 知识图谱边 |
| GET | `/health` | 健康检查 |

**完整 API 文档见 [docs/API参考文档.md](docs/API参考文档.md)（调用方手册）；架构与逻辑见 [docs/接口现状文档.md](docs/接口现状文档.md)（程序员文档）。**

## 文档索引（docs/）

- [API参考文档](docs/API参考文档.md) — **调用方手册**：请求参数表 + 响应示例 + curl 命令
- [接口现状文档](docs/接口现状文档.md) — **程序员文档**：分层架构 + 统一约定 + 业务逻辑
- [架构文档](docs/架构文档.md) — 项目架构地图
- [接口数据规范](docs/接口数据规范.md) — 统一数据格式约定
- [数据库表设计文档](docs/数据库表设计文档.md) — PG 三张表设计
- [学情建模V1](docs/学情建模与学情状态更新机制建模V1.md) — 掌握度算法（Beta-Binomial + 7步更新）
- [mastery_store/README.md](mastery_store/README.md) — 数据层说明
