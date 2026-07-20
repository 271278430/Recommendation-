#!/usr/bin/env bash
# 学情库备份(pg_dump 自定义格式 + gzip)。建议 crontab: 0 3 * * * /绝对路径/backup.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then echo "缺少 .env"; exit 1; fi
# shellcheck disable=SC1091
set -a; . ./.env; set +u

TS=$(date +%Y%m%d_%H%M%S)
OUT="./backups/mastery_${TS}.dump.gz"
mkdir -p ./backups

# 走容器内的 pg_dump,无需在宿主机装客户端
docker exec mastery-db pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc \
    | gzip > "$OUT"
echo "已备份到 $OUT  ($(du -h "$OUT" | cut -f1))"

# 保留最近 14 份
ls -1t ./backups/mastery_*.dump.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "当前备份份数: $(ls -1 ./backups/mastery_*.dump.gz 2>/dev/null | wc -l)"
echo
echo "恢复示例: gunzip -c $OUT | docker exec -i mastery-db pg_restore -U ${POSTGRES_USER} -d ${POSTGRES_DB} --clean"
