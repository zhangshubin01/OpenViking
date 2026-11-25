#!/bin/sh
# OpenViking 数据备份（~/.openviking）。
#
# 热备份：容器运行时直接打包，服务不中断；恢复前请先停容器。
# 冷备份：COLD=1 ./backup.sh（会临时停容器，备份后拉起；SQLite/RocksDB 一致性更好）。
#
# 可用环境变量：
#   OPENVIKING_HOME       数据与配置目录，默认 ~/.openviking
#   OPENVIKING_BACKUP_DIR 备份目录，默认 ~/.openviking-backups
#   OPENVIKING_CONTAINER  容器名，默认 openviking（仅冷备份用）
#   KEEP                  保留份数，默认 7
#   COLD                  1 表示冷备份，默认 0
set -eu

# 备份里含 ov.conf（带 root key 与模型 key），新文件一律 600。
umask 077

SRC="${OPENVIKING_HOME:-$HOME/.openviking}"
DST="${OPENVIKING_BACKUP_DIR:-$HOME/.openviking-backups}"
CONTAINER="${OPENVIKING_CONTAINER:-openviking}"
KEEP="${KEEP:-7}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$DST/backup.log"

mkdir -p "$DST"

STARTED_CONTAINER=0
cleanup() {
    if [ "$STARTED_CONTAINER" = "1" ]; then
        docker start "$CONTAINER" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM

if [ "${COLD:-0}" = "1" ]; then
    if docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
        echo "[$(date '+%F %T')] cold backup: stopping $CONTAINER" >> "$LOG"
        docker stop "$CONTAINER" >/dev/null
        STARTED_CONTAINER=1
    fi
fi

ARCHIVE="$DST/openviking-$STAMP.tar.gz"
if tar -czf "$ARCHIVE" \
        --exclude='data/bot/logs' \
        --exclude='studio-chrome-profile' \
        --exclude='*.sock' \
        -C "$(dirname "$SRC")" "$(basename "$SRC")" 2>>"$LOG"; then
    SIZE="$(du -h "$ARCHIVE" | cut -f1)"
    echo "[$(date '+%F %T')] ok $ARCHIVE ($SIZE) cold=$STARTED_CONTAINER" >> "$LOG"
else
    echo "[$(date '+%F %T')] FAILED $ARCHIVE" >> "$LOG"
    exit 1
fi

# 轮转：只保留最近 $KEEP 份
ls -1t "$DST"/openviking-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old"
    echo "[$(date '+%F %T')] pruned $old" >> "$LOG"
done

exit 0
