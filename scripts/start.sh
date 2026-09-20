#!/usr/bin/env bash
# 途排智策一键启动：后端(8000) + 前端(5173) + 自动打开浏览器
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "[错误] 未找到 uv: https://docs.astral.sh/uv/"; exit 1; }
command -v pnpm >/dev/null || { echo "[错误] 未找到 pnpm: https://pnpm.io/"; exit 1; }

echo "[1/3] 安装依赖（首次较慢）..."
(cd backend && uv sync --group dev)
(cd frontend && pnpm install)

echo "[2/3] 启动后端 (http://127.0.0.1:8000, Swagger: /docs)..."
(cd backend && uv run alembic upgrade head && uv run tupai-api) &
BACK_PID=$!
trap 'kill $BACK_PID $FRONT_PID 2>/dev/null || true' EXIT

echo "[3/3] 启动前端 (http://127.0.0.1:5173)..."
(cd frontend && pnpm dev) &
FRONT_PID=$!

sleep 6
( command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:5173 ) \
  || ( command -v open >/dev/null && open http://127.0.0.1:5173 ) \
  || echo "请手动打开 http://127.0.0.1:5173"

echo "默认管理员: admin / tupai-demo-admin-2026! （首次登录后请在设置页修改密码）"
echo "Ctrl+C 退出（会同时停止前后端）"
wait
