"""PostgreSQL 连接。

从环境变量读 PG 配置（兼容原 mastery_store/.env 的变量名 POSTGRES_* / PG_*）。
load_dotenv 顺序尝试项目根 / mastery_store / deploy 三个位置的 .env，
以便迁移期（.env 仍在 mastery_store/）和归位后（deploy/.env）都能连上。
"""
import os
import psycopg2
from dotenv import load_dotenv

for _p in (".env", "mastery_store/.env", "deploy/.env"):
    if os.path.exists(_p):
        load_dotenv(_p)

# advisory lock 命名空间（按 student_id 串行化写，与原 mastery_store 同源）
LOCK_NS = 20260701


def _db_params() -> dict:
    return dict(
        host=os.environ.get("PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT") or os.environ.get("PG_PORT") or "5433"),
        dbname=os.environ.get("POSTGRES_DB") or os.environ.get("PG_DB") or "mastery",
        user=os.environ.get("POSTGRES_USER") or os.environ.get("PG_USER") or "mastery",
        password=os.environ.get("POSTGRES_PASSWORD") or os.environ.get("PG_PASSWORD") or "",
    )


def conn():
    """新建一个 PG 连接（调用方负责 with/关闭）。"""
    return psycopg2.connect(**_db_params())
