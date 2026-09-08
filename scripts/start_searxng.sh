#!/bin/bash
# SIGNAL 0.8.4F — start and verify an existing local SearXNG installation.
# Never installs, upgrades, activates a venv, or kills an unknown process.
set -u

SEARXNG_HOME="${SEARXNG_HOME:-$HOME/searxng}"
SEARXNG_APP="$SEARXNG_HOME/app"
SEARXNG_PYTHON="$SEARXNG_HOME/.venv/bin/python"
SEARXNG_SETTINGS="$SEARXNG_APP/searx/settings.yml"
SYSTEM_NAME=$(uname -s 2>/dev/null || echo Unknown)
if [ "$SYSTEM_NAME" = "Darwin" ]; then
  SEARXNG_BASE_URL="${SEARXNG_BASE_URL:-http://127.0.0.1:8888}"
else
  SEARXNG_BASE_URL="${SEARXNG_BASE_URL:-}"
fi
SEARXNG_PID_FILE="$SEARXNG_HOME/searxng.pid"
SEARXNG_LOG_FILE="$SEARXNG_HOME/searxng.log"

is_ready(){
  [ -n "$SEARXNG_BASE_URL" ] || return 1
  curl -fsS --max-time 2 "$SEARXNG_BASE_URL/" 2>/dev/null | grep -qi 'SearXNG'
}

if is_ready; then
  echo "      [SearXNG] 已运行"
  exit 0
fi

if [ "$SYSTEM_NAME" != "Darwin" ]; then
  echo "      [SearXNG] 配置的本地搜索服务不可用（非 Mac 环境不执行自动启动）"
  exit 2
fi

if [ -f "$SEARXNG_PID_FILE" ]; then
  OLD_PID=$(cat "$SEARXNG_PID_FILE" 2>/dev/null || true)
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "      [SearXNG] 进程正在启动，等待 READY…"
  else
    rm -f "$SEARXNG_PID_FILE"
    OLD_PID=""
  fi
else
  OLD_PID=""
fi

if [ -z "$OLD_PID" ]; then
  if [ ! -d "$SEARXNG_APP" ] || [ ! -x "$SEARXNG_PYTHON" ] || [ ! -f "$SEARXNG_SETTINGS" ]; then
    echo "      [SearXNG] 未检测到本地搜索服务安装"
    exit 2
  fi
  if lsof -nP -iTCP:8888 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "      [SearXNG] 8888 端口已被其他程序占用"
    exit 3
  fi
  echo "      [SearXNG] 正在启动本地搜索服务…"
  (cd "$SEARXNG_APP" && nohup env SEARXNG_SETTINGS_PATH="$SEARXNG_SETTINGS" \
    "$SEARXNG_PYTHON" -m searx.webapp > "$SEARXNG_LOG_FILE" 2>&1 & echo $! > "$SEARXNG_PID_FILE")
fi

for _ in $(seq 1 20); do
  if is_ready; then
    echo "      [SearXNG] 本地搜索服务已就绪"
    exit 0
  fi
  sleep 1
done

echo "      [SearXNG] 本地搜索服务启动失败"
exit 4
