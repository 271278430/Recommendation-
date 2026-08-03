"""pytest 根配置：确保仓库根在 sys.path（import service / mastery_store 可用）。"""
import os
import sys

_REPO = os.path.dirname(os.path.abspath(__file__))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest


def _pg_ok() -> bool:
    try:
        from service.core.db import conn
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_AVAILABLE = _pg_ok()


@pytest.fixture(scope="session")
def require_pg():
    if not PG_AVAILABLE:
        pytest.skip("PostgreSQL 不可用，跳过依赖 DB 的测试")
