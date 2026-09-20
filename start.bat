@echo off
rem 途排智策一键启动：后端(8000) + 前端(5173) + 自动打开浏览器
setlocal
cd /d "%~dp0"

echo [1/4] 检查依赖工具...
where uv >nul 2>nul || (echo [错误] 未找到 uv，请先安装: https://docs.astral.sh/uv/ & pause & exit /b 1)
where pnpm >nul 2>nul || (echo [错误] 未找到 pnpm，请先安装: https://pnpm.io/ & pause & exit /b 1)

echo [2/4] 安装依赖（首次较慢）...
cd backend && uv sync --group dev || (pause & exit /b 1)
cd ..\frontend && pnpm install || (pause & exit /b 1)
cd ..

echo [3/4] 启动后端 (http://127.0.0.1:8000, Swagger: /docs)...
start "tupai-backend" cmd /k "cd /d %~dp0backend && uv run alembic upgrade head && uv run tupai-api"

echo [4/4] 启动前端 (http://127.0.0.1:5173)...
start "tupai-frontend" cmd /k "cd /d %~dp0frontend && pnpm dev"

echo 等待前端就绪后自动打开浏览器...
timeout /t 6 /nobreak >nul
start http://127.0.0.1:5173

echo.
echo 两个窗口分别运行后端与前端，关闭窗口即停止对应服务。
echo 默认管理员: admin / tupai-demo-admin-2026! （首次登录后请在设置页修改密码）
endlocal
