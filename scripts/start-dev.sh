#!/usr/bin/env bash
# 一键同时启动：FastAPI (8000) + Next.js (3000)
# 用法：在项目根目录执行  ./scripts/start-dev.sh
# 停止：Ctrl+C（会同时结束两个子进程）
#
# Windows 原生 PowerShell 请用同目录下的 start-dev.ps1（.venv 路径与 bash 不同）。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f "$ROOT/.venv/bin/activate" ]]; then
  echo "未找到 .venv，请先执行: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# shellcheck source=/dev/null
source "$ROOT/.venv/bin/activate"

API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

API_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  echo "正在停止服务..."
  [[ -n "$API_PID" ]]      && kill "$API_PID"      2>/dev/null || true
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 同时启动两个服务
echo "► 启动 API       → http://127.0.0.1:${API_PORT}"
uvicorn api:app --host 0.0.0.0 --port "$API_PORT" &
API_PID=$!

echo "► 启动前端       → http://localhost:${FRONTEND_PORT}"
cd "$ROOT/frontend"
npm run dev -- --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

echo "按 Ctrl+C 停止所有服务"

# 等待任意子进程退出，若一个崩溃则停止另一个
wait -n 2>/dev/null || wait
