from __future__ import annotations

import os
from pathlib import Path

database_url = os.environ.get("DATABASE_URL", "")
prefix = "sqlite:///"
if database_url.startswith(prefix):
    Path(database_url.removeprefix(prefix)).unlink(missing_ok=True)
