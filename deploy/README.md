# 学情状态库（单容器 PostgreSQL）

Beta-Binomial 共轭推断模型。每个学生对 612 个知识点存储 `alpha/beta/m_peak/last_ts`。

## 数据模型

`student_mastery`（一行一学生）：

| 列 | 类型 | 含义 |
|----|------|------|
| `alpha` | `REAL[612]` | 有效正确证据，初始 = P = 2 |
| `beta` | `REAL[612]` | 有效错误证据，初始 = P = 2 |
| `m_peak` | `REAL[612]` | 历史峰值掌握度，初始 = 0.5 |
| `last_ts` | `TIMESTAMPTZ[612]` | 各知识点上次练习时间，初始 NULL |

- 掌握度 `m = alpha/(alpha+beta)`，$\in[0,1]$
- 总证据量 `N = alpha+beta`
- 下标 `1..612` ↔ 知识点 0 基索引 `0..611`（与 `data/kg_graph/kg_index.json` 一致）
- 均带 `CHECK (长度=612)` 约束

`mastery_event`（变更日志）：每次掌握度变化记一条；`kp_idx` 为 `1..612`。

> **索引约定**：应用层 / 共现·先修矩阵 / `kg_index.json` 都是 **0 基**；DB 数组下标和 `mastery_event.kp_idx` 是 **1 基**。换算 `kp_idx = 0基索引 + 1`。

## 安全与并发

- **端口只绑 `127.0.0.1:5433`**，不暴露到网络。
- **密码**走 `.env`（`POSTGRES_PASSWORD`），`.env` 已 gitignore。
- **并发安全**：更新统一走 `update(sid, apply_fn)` —— 事务内 `pg_advisory_xact_lock` 按 student_id 串行化。

## 启动

```bash
cd mastery_store
cp .env.example .env
# 编辑 .env，设置 POSTGRES_PASSWORD
docker compose up -d
docker compose ps            # 等 healthy
```

## 自检

```bash
pip install psycopg2-binary numpy
python mastery_store.py
```

预期输出：冷启动 m=0.5 → 7 步更新后 m≈0.60 → 遗忘模拟 → savings 触发 → 薄弱知识点召回。

## API 用法

```python
from mastery_store import (
    get_mastery, get_converged_mastery, get_confidence,
    weakness_score, weakest_kps,
    process_answer, init_student, update,
    NAME2IDX, N_KP
)

sid = 100001

# ---- 初始化 ----
init_student(sid)  # 冷启动，alpha=beta=2, m_peak=0.5

# ---- 题目作答更新（7 步算法） ----
idx = NAME2IDX['二次函数的定义']  # 知识点 0 基索引
process_answer(
    student_id=sid,
    kp_indices=[idx],          # 该题考察的知识点
    weights=[1.0],              # 对应权重（单知识点=1.0）
    y=1.0,                      # 得分率（1=全对, 0=全错, 0.5=半对）
    d=0.55,                     # 题目难度（0~1，越大越难）
    g=0.0,                      # 猜对底（非选择题=0，单选=0.25）
    k=5.0,                      # 区分度（默认5，每道题可不同）
    source='homework',
    task_id='h123',
)

# ---- 读取 ----
m = get_mastery(sid)            # 掌握度 m = alpha/(alpha+beta)
m_star = get_converged_mastery(sid)  # 收敛掌握度（推荐排序用）
conf = get_confidence(sid)      # 置信度 c
weak = weakness_score(sid)      # 薄弱分 (1-m)*c

# ---- 召回 ----
# 指定范围内掌握度最低的 k 个知识点
weak_list = weakest_kps(sid, scope_idx={3, 7, 12}, k=3)
# → [(kp_idx, m*, name), ...]

# ---- 通用更新（自定义逻辑） ----
def my_apply(alpha, beta, m_peak, last_ts):
    # 自定义修改 alpha/beta/m_peak/last_ts
    return alpha, beta, m_peak, last_ts
update(sid, my_apply, source='custom')
```

## 超参数

见 `mastery_store.py` 顶部，可运行时覆盖：

```python
import mastery_store as ms
ms.P = 3.0        # 更保守的先验
ms.TAU_MAX = 120  # 遗忘更慢（最扎实时 τ 上限）
ms.N_MAX = 150    # 更大证据量上限
```

完整定义见 [学情建模与学情状态更新机制建模V1.md](../docs/学情建模与学情状态更新机制建模V1.md)。

## 运维

| 操作 | 命令 |
|------|------|
| 停（留数据） | `docker compose down` |
| 彻底清空 | `docker compose down -v` |
| 进库看 | `docker exec -it mastery-db psql -U mastery -d mastery` |
| 清旧日志 | `mastery_store.prune_events(days=90)`（建议定时跑） |

## 改表说明

init 脚本只在**数据目录为空**时跑。改表结构：
- 有数据要保留：`docker exec ... psql -c "ALTER TABLE ..."`
- 可丢弃：`docker compose down -v && up -d`，靠 init 脚本重建

## 连接参数

`host=127.0.0.1 port=5433 db=mastery user=mastery`
（环境变量 `PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD` 可覆盖）
