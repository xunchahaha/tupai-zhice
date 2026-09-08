"""重置端到端测试库。

只删库文件是不够的：应用启动只会 bootstrap 管理员，不会灌演示数据，
于是 scheduling-flow 用例里的 T01、S01 这些业务标识全都不存在，
第一个断言就挂在「规则引用了不存在的实体」上。这里补上建表与灌数，
让 e2e 拿到和单元测试同一套种子数据。
"""

from __future__ import annotations

import os
from pathlib import Path

database_url = os.environ.get("DATABASE_URL", "")
prefix = "sqlite:///"
if database_url.startswith(prefix):
    path = Path(database_url.removeprefix(prefix))
    # WAL/SHM 不一起删的话，上一轮的写入会被重放到「全新」的库里。
    for artifact in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        artifact.unlink(missing_ok=True)

from alembic.config import Config  # noqa: E402

from alembic import command  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services.seed import seed_demo_data  # noqa: E402

root = Path(__file__).resolve().parents[1]
config = Config(str(root / "alembic.ini"))
config.set_main_option("script_location", str(root / "alembic"))
command.upgrade(config, "head")
with SessionLocal() as db:
    seed_demo_data(db)
