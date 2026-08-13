from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.services.converter_zhengzhou import _read_rows, _row_identity

HEADERS = (
    "标准业务线",
    "标准产品班型",
    "集训营班级标签",
    "教室标签",
    "课表编排来源",
    "编排阶段",
    "计划课次",
    "计划课时",
    "课次序号",
    "课节名称",
    "上课日期",
    "上课时段",
    "课节时长(小时)",
    "授课教师",
)


def _write_workbook(path: Path, rows: list[tuple[object, ...]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "课表数据源"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_exact_deduplication_uses_all_fourteen_official_fields(tmp_path: Path) -> None:
    base = (
        "公职",
        "公职·国省考笔面一体全年班",
        "公职无限学（非考研集训营）",
        "教室-506",
        "郑州27年公职产品课表",
        "基础搭建",
        120,
        360,
        11,
        "面试·结构化面试先导",
        datetime(2026, 10, 1),
        "09:00-12:00",
        3,
        "郑州公职面试教研组",
    )
    another_product = list(base)
    another_product[1] = "公职·结构化面试先导"
    path = tmp_path / "official.xlsx"
    _write_workbook(path, [base, base, tuple(another_product)])

    rows = _read_rows(path)
    deduped = {_row_identity(row): row for row in rows}

    assert len(rows) == 3
    assert len(deduped) == 2
    assert {row["产品班型"] for row in deduped.values()} == {
        "公职·国省考笔面一体全年班",
        "公职·结构化面试先导",
    }
    assert {row["授课教师"] for row in deduped.values()} == {"郑州公职面试教研组"}
    assert {row["开始时间"] for row in deduped.values()} == {"09:00"}


def test_official_zhengzhou_workbook_exact_deduplication_count() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "郑州考研公职专升本课表数据源_教室班级标签版.xlsx"
    )
    if not path.exists():
        pytest.skip("郑州官方数据文件不在工作区")

    rows = _read_rows(path)
    deduped = {_row_identity(row): row for row in rows}

    assert len(rows) == 56344
    assert len(deduped) == 10771
    assert {row["开始时间"] for row in deduped.values()} == {
        "08:30",
        "09:00",
        "14:00",
        "18:30",
    }
