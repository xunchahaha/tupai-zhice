#!/usr/bin/env bash
# 本地一键校验：与 .github/workflows/ci.yml 的步骤一一对应。
# GitHub Actions 不可用时（账户欠费/额度用尽，job 会 0 步骤直接失败）用它代替 CI。
#
#   bash scripts/verify.sh            后端 ruff/mypy/pytest + 前端 tsc/orval 零 diff/vitest/build
#   bash scripts/verify.sh --e2e      再加 Playwright 端到端（需要已安装 chromium：pnpm exec playwright install chromium）
#   bash scripts/verify.sh --backend  只跑后端     --frontend 只跑前端
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_backend=1
run_frontend=1
run_e2e=0
for arg in "$@"; do
  case "$arg" in
    --e2e) run_e2e=1 ;;
    --backend) run_frontend=0 ;;
    --frontend) run_backend=0 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# 目录内容指纹：工作区有未提交改动时 `git diff` 会把「本来就没提交」误报成漂移，所以比的是再生成前后的指纹。
fingerprint() { find "$@" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1; }
assert_unchanged() { # 用法：assert_unchanged 说明 前指纹 后指纹
  if [ "$2" != "$3" ]; then
    echo "✗ $1：再生成后内容变了，说明没有同步提交（重新生成并提交它）" >&2
    exit 1
  fi
}

if [ "$run_backend" = 1 ]; then
  cd "$root/backend"
  step "后端：uv sync"
  uv sync --group dev
  step "后端：ruff"
  uv run ruff check app tests
  step "后端：mypy"
  uv run mypy app
  step "后端：pytest"
  uv run pytest -q
  step "后端：openapi.json 与代码一致（前端 orval 的输入）"
  before="$(fingerprint "$root/backend/openapi.json")"
  uv run python scripts/export_openapi.py >/dev/null
  assert_unchanged "backend/openapi.json" "$before" "$(fingerprint "$root/backend/openapi.json")"
fi

if [ "$run_frontend" = 1 ]; then
  cd "$root/frontend"
  step "前端：安装依赖"
  pnpm install --frozen-lockfile
  step "前端：tsc"
  pnpm exec tsc -b
  step "前端：eslint"
  pnpm exec eslint .
  step "前端：orval 再生成零 diff"
  before="$(fingerprint "$root/frontend/src/api/generated")"
  pnpm generate:api
  assert_unchanged "frontend/src/api/generated" "$before" "$(fingerprint "$root/frontend/src/api/generated")"
  step "前端：vitest"
  pnpm exec vitest run
  step "前端：build"
  pnpm build
fi

if [ "$run_e2e" = 1 ]; then
  cd "$root/frontend"
  step "端到端：Playwright（自起后端 8001 + 前端 5174）"
  pnpm test:e2e
fi

printf '\n\033[1;32m全部通过\033[0m\n'
