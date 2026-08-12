"""清空示范数据，重建主数据表，并只导入官方课表数据源。

用法：python scripts/reset_to_official.py <官方课表 xlsx 路径>
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

from app.db import Base, SessionLocal, engine
from app.models import (
    AuditLog,
    Campus,
    ClassGroup,
    CourseSession,
    DataSnapshot,
    IntegrationSync,
    RescheduleEvent,
    Room,
    Rule,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.services.converter_zhengzhou import import_schedule_workbook

BUSINESS_TABLES = [
    ScheduleAssignment.__table__,
    RescheduleEvent.__table__,
    ScheduleVersion.__table__,
    SolverRun.__table__,
    DataSnapshot.__table__,
    Rule.__table__,
    CourseSession.__table__,
    ClassGroup.__table__,
    Teacher.__table__,
    Room.__table__,
    TimeSlot.__table__,
    Campus.__table__,
    AuditLog.__table__,
    IntegrationSync.__table__,
]


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python scripts/reset_to_official.py <官方课表 xlsx 路径>")
        raise SystemExit(2)
    workbook_path = Path(sys.argv[1])
    if not workbook_path.exists():
        raise SystemExit(f"文件不存在：{workbook_path}")

    Base.metadata.drop_all(bind=engine, tables=BUSINESS_TABLES)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        result = import_schedule_workbook(db, workbook_path)
        db.commit()
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = 'c0d7e2f4a1b6'")
            )

    print("已清空示范数据并仅导入官方数据：")
    for key, value in result.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
