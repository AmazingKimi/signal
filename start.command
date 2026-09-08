#!/bin/bash
# SIGNAL RADAR 0.8 · Private Discovery Radar · Blue Liquid Glass
# 双击启动：检查/获取 Python -> 建 venv -> 升级 pip -> 装锁定依赖 -> 起服务 -> 开浏览器
# 兼容：系统 Python >=3.10 直接用；<3.10（老 Mac）自动用 uv 获取独立 Python 3.12，无需 sudo / brew / 终端命令
set -e
cd "$(dirname "$0")"

# 强制 UTF-8 locale：C locale 下 bash 对含中文括号的字符串做变量展开会破坏字节
if [ -z "${LC_ALL:-}" ] && [ -z "${LANG:-}" ]; then
  export LC_ALL=en_US.UTF-8
fi
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

echo "== SIGNAL RADAR 0.8 =="
echo "Private Discovery Radar · Blue Liquid Glass"
echo ""

fail(){
  printf "\n[错误] %s\n\n" "$1"
  read -r -p "按回车键退出…" _
  exit 1
}

# 版本比较（纯 awk，兼容 macOS BSD 工具链）：ver_ge A B 当 A >= B 为真
ver_ge(){
  local a b
  a=$(printf '%s\n' "$1" | awk -F. '{printf "%d%02d", $1, $2}')
  b=$(printf '%s\n' "$2" | awk -F. '{printf "%d%02d", $1, $2}')
  [ "${a:-0}" -ge "${b:-0}" ]
}

echo "[1/7] 检查 Python 环境…"
SYS_PY=$(command -v python3 || true)
SYS_VER=""
if [ -n "$SYS_PY" ]; then
  SYS_VER=$("$SYS_PY" -c "import sys; print('%d.%d' % (sys.version_info[0], sys.version_info[1]))" 2>/dev/null)
  [ -z "$SYS_VER" ] && SYS_VER=$("$SYS_PY" --version 2>&1 | awk '{print $2}')
  printf "      系统 Python：%s（%s）\n" "$SYS_VER" "$SYS_PY"
else
  echo "      未检测到 python3"
fi

PYBIN=""
if [ -n "$SYS_PY" ] && [ -n "$SYS_VER" ] && ver_ge "$SYS_VER" "3.10"; then
  PYBIN="$SYS_PY"
  echo "      ✓ 系统 Python 兼容，直接使用"
else
  echo "      → 系统 Python 低于 3.10，自动获取独立 Python 3.12…"
  UV="$HOME/.local/bin/uv"
  if [ ! -x "$UV" ]; then
    echo "      安装 uv（一次性 Python 版本管理器，无需管理员权限）…"
    curl -LsSf https://astral.sh/uv/install.sh | sh || fail "uv 安装失败（请检查网络连接后重新双击本文件）"
    UV="$HOME/.local/bin/uv"
    [ -x "$UV" ] || fail "uv 安装失败：未找到 $UV"
  fi
  echo "      下载 Python 3.12（约 1-2 分钟，仅首次）…"
  "$UV" python install 3.12 > /dev/null 2>&1 || fail "Python 3.12 下载失败（请检查网络连接后重新双击本文件）"
  PYBIN=$("$UV" python find 3.12 2>/dev/null | tail -1) || PYBIN=""
  if [ -z "$PYBIN" ] || [ ! -x "$PYBIN" ]; then
    fail "Python 3.12 获取失败，请稍后重新双击本文件"
  fi
  V_NEW=$("$PYBIN" --version 2>&1 | awk '{print $2}')
  printf "      ✓ 已获取 Python：%s\n" "$V_NEW"
fi

echo ""
echo "[2/7] 检查本地搜索服务…"
if [ -f .env ]; then
  _SEARXNG_URL=$(awk -F= '/^[[:space:]]*SEARXNG_BASE_URL[[:space:]]*=/{sub(/^[^=]*=/,""); print; exit}' .env)
  _SEARXNG_FALLBACK=$(awk -F= '/^[[:space:]]*SEARXNG_FALLBACK_URL[[:space:]]*=/{sub(/^[^=]*=/,""); print; exit}' .env)
  [ -n "$_SEARXNG_URL" ] && export SEARXNG_BASE_URL="$_SEARXNG_URL"
  [ -n "$_SEARXNG_FALLBACK" ] && export SEARXNG_FALLBACK_URL="$_SEARXNG_FALLBACK"
fi
if [ -f "scripts/start_searxng.sh" ]; then
  bash scripts/start_searxng.sh || true
else
  echo "      [SearXNG] 启动脚本缺失，SIGNAL 将继续启动"
fi

echo ""
echo "[3/7] 创建 / 检查虚拟环境…"
if [ -d ".venv" ]; then
  VV=$(".venv/bin/python" --version 2>&1 | awk '{print $2}' || echo "")
  if [ -z "$VV" ] || ! ver_ge "$VV" "3.10"; then
    printf "      现有 .venv 的 Python（%s）不兼容，删除重建…\n" "$VV"
    rm -rf .venv
  fi
fi
if [ ! -d ".venv" ]; then
  "$PYBIN" -m venv .venv || fail "虚拟环境创建失败"
fi

echo "[4/7] 升级 pip / setuptools / wheel…"
./.venv/bin/python -m pip install --upgrade pip setuptools wheel > /dev/null 2>&1 \
  || fail "pip 升级失败（请检查网络连接后重新双击本文件）"

echo "[5/7] 安装锁定依赖（首次约 1-3 分钟）…"
./.venv/bin/python -m pip install -q -r requirements.txt \
  || fail "依赖安装失败（请检查网络连接后重新双击本文件）"

if [ ! -f .env ]; then
  echo "[6/7] 生成 .env（本地搜索服务自动配置）"
  cp .env.example .env
fi

echo "[7/7] 启动 SIGNAL…"
# 幂等：只复用当前构建。旧 SIGNAL 占用 8765 时先退出旧进程，避免一直打开旧前端。
EXPECTED_BUILD='amazing-kimi-0.8.4g-parallels-searxng'
HEALTH_JSON=$(curl -fsS http://127.0.0.1:8765/api/health 2>/dev/null || true)
if printf '%s' "$HEALTH_JSON" | grep -Fq "\"build\":\"$EXPECTED_BUILD\""; then
  echo "      服务已在运行，直接打开浏览器…"
else
  if [ -n "$HEALTH_JSON" ]; then
    OLD_PID=$(lsof -tiTCP:8765 -sTCP:LISTEN 2>/dev/null | head -1 || true)
    if [ -n "$OLD_PID" ]; then
      echo "      检测到旧构建（PID $OLD_PID），正在切换到当前版本…"
      kill "$OLD_PID" 2>/dev/null || true
      for _ in $(seq 1 20); do
        lsof -tiTCP:8765 -sTCP:LISTEN >/dev/null 2>&1 || break
        sleep 0.25
      done
    fi
  fi
  mkdir -p data
  nohup ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765 > server.log 2>&1 &
  echo $! > server.pid

  # 等待服务就绪（最长 30 秒）
  for _ in $(seq 1 30); do
    if curl -s -o /dev/null http://127.0.0.1:8765/api/health; then
      break
    fi
    sleep 1
  done

  if ! curl -s -o /dev/null http://127.0.0.1:8765/api/health; then
    echo ""
    echo "[错误] 服务未能启动，最近日志："
    tail -8 server.log 2>/dev/null || true
    echo ""
    read -r -p "按回车键退出…" _
    exit 1
  fi
fi

echo ""
echo "✔ 已启动：http://127.0.0.1:8765"
open "http://127.0.0.1:8765/?build=$EXPECTED_BUILD"
