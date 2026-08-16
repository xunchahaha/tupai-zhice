#!/bin/sh
set -eu

# Fresh deployments start with an empty database. Apply the same Alembic path
# documented for local installs before FastAPI's schema guard and admin
# bootstrap run.
uv run --no-sync alembic upgrade head

exec uv run --no-sync uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}"
