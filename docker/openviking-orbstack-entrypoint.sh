#!/bin/sh
# OrbStack 本机部署专用入口。
#
# ov.conf 里 server.host/port 必须保持 127.0.0.1:1934（改了会和转发抢 1933），
# 这里把 0.0.0.0:1933 转发过去（openviking-orbstack-forward，镜像内没有 socat），
# 让宿主机、内网其他机器以及同 openviking-shared 网络的容器都能访问。
set -eu

FORWARD_PORT="${OPENVIKING_FORWARD_PORT:-1933}"
UPSTREAM_HOST="${OPENVIKING_UPSTREAM_HOST:-127.0.0.1}"
UPSTREAM_PORT="${OPENVIKING_UPSTREAM_PORT:-1934}"
WITH_BOT="${OPENVIKING_WITH_BOT:-1}"

python3 /usr/local/bin/openviking-orbstack-forward \
    "${FORWARD_PORT}" "${UPSTREAM_HOST}" "${UPSTREAM_PORT}" &
FORWARD_PID=$!

if [ "${WITH_BOT}" = "1" ]; then
    openviking-server --with-bot &
else
    openviking-server &
fi
SERVER_PID=$!

trap 'kill -TERM "${SERVER_PID}" "${FORWARD_PID}" 2>/dev/null || true' INT TERM

STATUS=0
wait "${SERVER_PID}" || STATUS=$?
kill -TERM "${FORWARD_PID}" 2>/dev/null || true
exit "${STATUS}"
