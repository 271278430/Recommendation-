#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# 一键全量接口测试
# 用法: ./run_tests.sh [pytest 额外参数]
#   ./run_tests.sh              # 全量
#   ./run_tests.sh -v           # 全量 + 详细输出
#   ./run_tests.sh -x --lf      # 快速重跑上次失败的
#   ./run_tests.sh tests/test_pure_logic.py  # 只跑指定文件
# ============================================================

cd "$(dirname "$0")"

# ── 1. Python 环境检查 ──
echo "====== Python 环境 ======"
echo "Python: $(python3 --version)"
echo "pip:    $(python3 -m pip --version 2>/dev/null | cut -d' ' -f1-2)"

# 关键依赖检查
echo ""
echo "====== 依赖检查 ======"
MISSING=0
check_dep() {
    if python3 -c "import $1" 2>/dev/null; then
        echo "  ✓ $1"
    else
        echo "  ✗ $1 — pip install $2"
        MISSING=1
    fi
}
check_dep "fastapi"      "fastapi[standard]"
check_dep "pydantic"     "pydantic"
check_dep "numpy"        "numpy"
check_dep "psycopg2"     "psycopg2" # 如果是 psycopg2-binary 改这里
check_dep "pytest"       "pytest"
check_dep "mastery_store" ""
check_dep "service"      ""

if [[ $MISSING -eq 1 ]]; then
    echo ""
    echo "请安装缺失依赖后重试。"
    exit 1
fi

# ── 2. 数据库连接检查 ──
echo ""
echo "====== 数据库检查 ======"
DB_OK=0
if python3 -c "
import mastery_store as ms
try:
    with ms.conn() as c, c.cursor() as cur:
        cur.execute('SELECT 1')
    print('ok')
except Exception as e:
    print('no', e)
" 2>/dev/null | grep -q 'ok'; then
    echo "  ✓ PostgreSQL 可用 — 集成测试可跑"
    DB_OK=1
else
    echo "  ⚠ PostgreSQL 不可用 — 集成测试将自动跳过（只跑单元测试）"
fi

# ── 3. 运行测试 ──
echo ""
echo "====== 测试执行 ======"

START=$(date +%s)
python3 -m pytest tests/ "$@" -q
EC=$?
END=$(date +%s)

PASSED=$(python3 -m pytest tests/ --collect-only -q "$@" 2>/dev/null | tail -1 | grep -oE '[0-9]+ selected' | grep -oE '[0-9]+' || echo "?")

echo ""
echo "====== 结果 ======"
if [[ $EC -eq 0 ]]; then
    echo "  ✅ 全部通过（耗时 $((END - START))s）"
else
    echo "  ❌ 有测试失败（退出码 $EC），检查上方输出"
fi
echo ""
echo "  · 单元测试（不依赖外部）:  tests/test_pure_logic.py tests/test_forget_math.py"
echo "  · 接口测试（需要 PostgreSQL）: 其余 test_*.py"
echo "  · 运行方式:  ./run_tests.sh          # 全量"
echo "              ./run_tests.sh -v       # 详细"
echo "              ./run_tests.sh tests/test_submit_answer.py  # 单文件"

exit $EC
