from __future__ import annotations

import json
from datetime import date, time
from decimal import Decimal

from app.services.snapshot import _json_default


def test_snapshot_json_default_serializes_database_scalars() -> None:
    payload = {
        "lesson_date": date(2026, 8, 14),
        "start_time": time(9, 0),
        "weight": Decimal("1.25"),
    }
    serialized = json.dumps(payload, default=_json_default, sort_keys=True)
    assert '"lesson_date": "2026-08-14"' in serialized
    assert '"start_time": "09:00:00"' in serialized
    assert '"weight": "1.25"' in serialized
