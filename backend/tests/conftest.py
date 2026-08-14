from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DB = Path(__file__).resolve().parents[2] / "data" / "test.db"
# 连 WAL/SHM 一起删：只删主库文件时，上一次跑子集测试留下的 WAL 会被重放，
# 下一次全量跑就会出现与改动无关的假失败。
for _artifact in (TEST_DB, Path(f"{TEST_DB}-wal"), Path(f"{TEST_DB}-shm")):
    if _artifact.exists():
        _artifact.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-with-at-least-32-characters"
# 示例密钥在生产会被拒绝，测试也必须用真实值走同一条鉴权路径。
os.environ["AILY_SKILL_API_KEY"] = "test-aily-key-not-the-repo-default"

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.services.seed import seed_demo_data  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as test_client:
        with SessionLocal() as db:
            seed_demo_data(db)
        yield test_client


@pytest.fixture(scope="session")
def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/token", data={"username": "admin", "password": "tupai-demo"}
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
