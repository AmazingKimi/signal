#!/bin/bash
# AMAZING KIMI — 停止服务（含残留清理）
cd "$(dirname "$0")"
if [ -f server.pid ]; then
  PID=$(cat server.pid)
  if kill "$PID" 2>/dev/null; then
    echo "已停止 (pid $PID)"
  else
    echo "进程 $PID 未在运行，尝试清理残留 uvicorn…"
    pkill -f "uvicorn app.main:app" 2>/dev/null && echo "已清理残留进程" || echo "无残留进程"
  fi
  rm -f server.pid
else
  pkill -f "uvicorn app.main:app" 2>/dev/null && echo "已清理残留 uvicorn 进程" || echo "未找到 server.pid，服务未在运行"
fi
