from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import ClassGroup, CourseSession, Room, ScheduleAssignment, Teacher, TimeSlot
from app.services.converter_zhengzhou import (
    PLACEHOLDER_ROOM,
    SHEET_NAME,
    WorkbookFormatError,
    _read_rows,
    _row_identity,
    _split_placeholder_rows,
    import_schedule_workbook,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_WORKBOOK = PROJECT_ROOT / "data" / "imports" / "sample.xlsx"
OFFICIAL_WORKBOOK = (
    PROJECT_ROOT / "相关文件" / "郑州考研公职专升本课表数据源_教室班级标签版.xlsx"
)

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


@pytest.fixture
def db() -> Iterator[Session]:
    """独立的内存库，避免与 conftest 的会话级共享库互相污染。"""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True)
    with factory() as session:
        yield session
    engine.dispose()


def _write_workbook(path: Path, rows: list[tuple[Any, ...]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _sample_rows() -> list[tuple[Any, ...]]:
    """读取官方模板 sample.xlsx 的真实数据行，作为所有用例的基准数据。"""
    workbook = load_workbook(SAMPLE_WORKBOOK, data_only=True)
    sheet = workbook[SHEET_NAME]
    rows = [tuple(row) for row in sheet.iter_rows(min_row=2, values_only=True)]
    workbook.close()
    return rows


def test_sample_workbook_matches_the_documented_template() -> None:
    """模板即导入契约：列名和列序变了，后面按下标取值的逻辑就全错。"""
    workbook = load_workbook(SAMPLE_WORKBOOK, data_only=True)
    sheet = workbook[SHEET_NAME]
    header = tuple(next(sheet.iter_rows(min_row=1, max_row=1, values_only=True)))
    workbook.close()
    assert header == HEADERS


def test_sample_workbook_carries_one_placeholder_room_row() -> None:
    rows = _sample_rows()
    assert len(rows) == 3
    assert sum(1 for row in rows if row[3] == PLACEHOLDER_ROOM) == 1


def test_placeholder_rows_are_dropped_at_import_not_kept_as_a_disabled_room(
    db: Session,
) -> None:
    """「教室-待校区确认」整行丢弃：不建教室、不建班级、不建课次。"""
    result = import_schedule_workbook(db, SAMPLE_WORKBOOK)
    db.commit()

    dropped = result["warnings"]["dropped_placeholder_room"]
    assert result["rows_total"] == 3
    assert dropped["dropped_rows"] == 1
    assert dropped["dropped_lesson_groups"] == 1
    assert dropped["affected_classes"] == ["公职无限学（非考研集训营）"]
    assert result["rows_kept"] == 2
    assert result["rows_deduped"] == 2
    assert result["course_sessions_created"] == 2

    room_ids = set(db.scalars(select(Room.business_id)))
    assert PLACEHOLDER_ROOM not in room_ids
    assert room_ids == {"教室-510", "教室-305"}

    class_ids = set(db.scalars(select(ClassGroup.business_id)))
    assert "公职无限学（非考研集训营）" not in class_ids
    assert class_ids == {"全年集训营二班", "专升本全年班（非考研集训营）"}

    teacher_ids = set(db.scalars(select(Teacher.business_id)))
    assert "郑州公职申论教研组" not in teacher_ids

    assert db.scalar(select(func.count(CourseSession.id))) == 2
    assert db.scalar(select(func.count(ScheduleAssignment.id))) == 2


def test_placeholder_report_names_the_dimensions_that_disappear_entirely(
    db: Session,
) -> None:
    """丢弃口径必须报到维度级。

    只报「丢了 N 行」看不出一整条业务线连同它的教研组和时钟窗口都没进库；而一旦某条
    业务线整体消失，跨产品线的教室/教师冲突检查在数据层就不成立了。
    """
    result = import_schedule_workbook(db, SAMPLE_WORKBOOK)
    db.commit()

    lost = result["warnings"]["dropped_placeholder_room"]["lost_entirely"]
    assert lost["business_lines"] == ["公职"]
    assert lost["product_types"] == ["公职·国省考笔面一体全年班"]
    assert lost["class_labels"] == ["公职无限学（非考研集训营）"]
    assert lost["teachers"] == ["郑州公职申论教研组"]
    assert lost["clock_windows"] == ["14:00-17:00"]
    assert lost["dates"] == 1
    assert result["warnings"]["dropped_placeholder_room"]["dropped_rows_by_business_line"] == {
        "公职": 1
    }


def test_placeholder_drop_counts_groups_that_only_partly_disappear(
    db: Session, tmp_path: Path
) -> None:
    """占位行与真实教室行挤在同一格时，丢弃统计不能把这一格减没。

    原实现用 len(dropped_groups - kept_groups)，同一格里只要还剩一节真实课，被丢掉的
    那几节就不计数，损耗被自己的统计口径掩盖。
    """
    base = next(row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM)
    # 同班、同日、同时段，但是另一节课：整行丢弃后这一格仍有课，属于混合格。
    mixed = _edit_cell(_edit_cell(_edit_cell(base, 3, PLACEHOLDER_ROOM), 9, "政治·政治基础"), 8, 2)
    path = tmp_path / "mixed-placeholder.xlsx"
    _write_workbook(path, [base, mixed])

    result = import_schedule_workbook(db, path)
    db.commit()

    dropped = result["warnings"]["dropped_placeholder_room"]
    assert dropped["dropped_rows"] == 1
    assert dropped["dropped_lessons"] == 1
    assert dropped["dropped_lesson_groups"] == 1
    assert dropped["partially_dropped_lesson_groups"] == 1
    # 班级/教师/业务线都还在真实行里，不能算作整建制消失。
    assert dropped["lost_entirely"]["business_lines"] == []
    assert dropped["lost_entirely"]["class_labels"] == []
    assert db.scalar(select(func.count(CourseSession.id))) == 1


def test_workbook_with_only_placeholder_rooms_is_rejected(tmp_path: Path) -> None:
    """整表都待定教室时必须报错，而不是静默导入一个空课表。"""
    rows = [row for row in _sample_rows() if row[3] == PLACEHOLDER_ROOM]
    path = tmp_path / "all-placeholder.xlsx"
    _write_workbook(path, rows)
    assert _split_placeholder_rows(_read_rows(path)[0])[0] == []


def test_exact_duplicate_rows_are_deduplicated_at_import(db: Session, tmp_path: Path) -> None:
    """去重发生在导入时，不依赖外部已去重的表格。"""
    rows = [row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM]
    path = tmp_path / "with-duplicates.xlsx"
    _write_workbook(path, [*rows, *rows, rows[0]])

    result = import_schedule_workbook(db, path)
    db.commit()

    assert result["rows_total"] == len(rows) * 2 + 1
    assert result["rows_deduped"] == len(rows)
    assert db.scalar(select(func.count(CourseSession.id))) == len(rows)


def test_reimporting_the_same_workbook_does_not_duplicate_rows(db: Session) -> None:
    first = import_schedule_workbook(db, SAMPLE_WORKBOOK)
    db.commit()
    before = db.scalar(select(func.count(CourseSession.id)))

    second = import_schedule_workbook(db, SAMPLE_WORKBOOK)
    db.commit()
    after = db.scalar(select(func.count(CourseSession.id)))

    assert first["course_sessions_created"] == before
    assert second["course_sessions_created"] == 0
    assert after == before
    assert db.scalar(select(func.count(ScheduleAssignment.id))) == before


def test_import_targets_the_requested_campus_not_a_hardcoded_one(db: Session) -> None:
    """同一份模板换一所学校导入时，校区必须跟着走。"""
    result = import_schedule_workbook(
        db, SAMPLE_WORKBOOK, campus_business_id="CAMPUS-TJ", campus_name="天津校区"
    )
    db.commit()
    assert result["campus"] == "CAMPUS-TJ"


def test_another_school_using_the_same_template_imports_cleanly(
    db: Session, tmp_path: Path
) -> None:
    """通用性回归：同模板、不同学校的数据（不同班级/教师/教室/时段）也要能导入。"""
    rows = [
        (
            "高考",
            "高考·全科冲刺班",
            "天津一班",
            "阶梯教室-A1",
            "天津27年高考产品课表",
            "冲刺阶段",
            60,
            180,
            1,
            "语文·现代文阅读专题",
            datetime(2026, 9, 7),
            "07:50-09:30",
            1.5,
            "天津语文教研组",
        ),
        (
            "高考",
            "高考·全科冲刺班",
            "天津一班",
            "阶梯教室-A1",
            "天津27年高考产品课表",
            "冲刺阶段",
            60,
            180,
            2,
            "数学·导数与圆锥曲线",
            datetime(2026, 9, 8),
            "13:30-15:10",
            1.5,
            "天津数学教研组",
        ),
        (
            "高考",
            "高考·全科冲刺班",
            "天津二班",
            "阶梯教室-A2",
            "天津27年高考产品课表",
            "冲刺阶段",
            60,
            180,
            1,
            "英语·完形与七选五",
            datetime(2026, 9, 9),
            # 与郑州样本重合的时刻，用来暴露「已知时刻排在未知时刻之前」的排序错误
            "08:30-10:10",
            1.5,
            "天津英语教研组",
        ),
    ]
    path = tmp_path / "tianjin.xlsx"
    _write_workbook(path, rows)

    result = import_schedule_workbook(
        db, path, campus_business_id="CAMPUS-TJ", campus_name="天津校区"
    )
    db.commit()

    assert result["rows_total"] == 3
    assert result["course_sessions_created"] == 3
    assert result["warnings"]["dropped_placeholder_room"]["dropped_rows"] == 0
    assert set(db.scalars(select(Teacher.business_id))) == {
        "天津语文教研组",
        "天津数学教研组",
        "天津英语教研组",
    }
    assert set(db.scalars(select(Room.business_id))) == {"阶梯教室-A1", "阶梯教室-A2"}

    slots = list(db.scalars(select(TimeSlot).order_by(TimeSlot.sequence)))
    starts = [slot.start_time for slot in slots if slot.weekday == "周一"]
    assert starts == sorted(starts), f"时段顺序必须按上课时间排列，实际为 {starts}"


def test_official_workbook_import_counts() -> None:
    if not OFFICIAL_WORKBOOK.exists():
        pytest.skip("郑州官方数据文件不在工作区")

    source_rows, skipped = _read_rows(OFFICIAL_WORKBOOK)
    assert skipped == []
    kept, dropped = _split_placeholder_rows(source_rows)
    deduped = {_row_identity(row): row for row in kept}

    assert len(source_rows) == 56344
    assert dropped["dropped_rows"] == 19734
    assert dropped["dropped_lesson_groups"] == 602
    assert dropped["affected_classes"] == [
        "专升本全年班（非考研集训营）",
        "公职无限学（非考研集训营）",
    ]
    assert len(kept) == 36610
    assert len(deduped) == 9340


def test_official_workbook_placeholder_drop_deletes_two_whole_business_lines() -> None:
    """郑州这份表的占位教室行 = 公职 + 专升本两条业务线的**全部**行。

    丢掉之后库里只剩考研，跨产品线的教室/教师冲突根本无从检查。行为不改，但代价必须
    留在回归里：口径一旦被业务方推翻，这个用例就是改动的落点。
    """
    if not OFFICIAL_WORKBOOK.exists():
        pytest.skip("郑州官方数据文件不在工作区")

    source_rows, _ = _read_rows(OFFICIAL_WORKBOOK)
    _, dropped = _split_placeholder_rows(source_rows)

    assert dropped["dropped_rows_by_business_line"] == {"专升本": 487, "公职": 19247}
    assert dropped["dropped_lessons"] == 1395
    assert dropped["partially_dropped_lesson_groups"] == 0
    lost = dropped["lost_entirely"]
    assert lost["business_lines"] == ["专升本", "公职"]
    assert len(lost["product_types"]) == 3
    assert len(lost["teachers"]) == 7
    assert lost["clock_windows"] == ["09:00-12:00"]
    assert lost["dates"] == 97


def test_same_start_time_with_two_durations_does_not_collide(db: Session, tmp_path: Path) -> None:
    """时段业务标识必须带结束时间，否则 08:30-10:00 与 08:30-11:30 会撞同一个标识。"""
    base = next(row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM)
    short = list(base)
    short[2] = "短课班"
    short[11] = "08:30-10:00"
    short[12] = 1.5
    long = list(base)
    long[2] = "长课班"
    long[11] = "08:30-11:30"
    long[12] = 3
    path = tmp_path / "two-durations.xlsx"
    _write_workbook(path, [tuple(short), tuple(long)])

    result = import_schedule_workbook(db, path)
    db.commit()

    assert result["course_sessions_created"] == 2
    monday = {
        slot.business_id: (slot.start_time, slot.end_time)
        for slot in db.scalars(select(TimeSlot).where(TimeSlot.weekday == "周一"))
    }
    assert len(monday) == 2, f"两种时长必须产生两个时段，实际 {monday}"
    assert set(monday.values()) == {("08:30", "10:00"), ("08:30", "11:30")}


def _edit_cell(row: tuple[Any, ...], index: int, value: Any) -> tuple[Any, ...]:
    edited = list(row)
    edited[index] = value
    return tuple(edited)


def test_editing_a_cell_updates_the_lesson_instead_of_duplicating_it(
    db: Session, tmp_path: Path
) -> None:
    """改教室后重新导入必须是更新，不是新增——业务标识不能含可变字段。"""
    base = next(row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM)
    first = tmp_path / "v1.xlsx"
    _write_workbook(first, [base])
    import_schedule_workbook(db, first)
    db.commit()
    before = db.scalar(select(CourseSession.business_id))

    second = tmp_path / "v2.xlsx"
    _write_workbook(second, [_edit_cell(base, 3, "教室-999")])
    result = import_schedule_workbook(db, second)
    db.commit()

    sessions = list(db.scalars(select(CourseSession)))
    assert len(sessions) == 1, [item.business_id for item in sessions]
    assert sessions[0].business_id == before
    assert sessions[0].original_room_business_id == "教室-999"
    assert result["course_sessions_created"] == 0


def test_adding_a_class_does_not_duplicate_existing_lessons(db: Session, tmp_path: Path) -> None:
    """业务标识不能依赖本次导入的班级排序名次。"""
    rows = [row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM]
    later, earlier = rows[0], rows[1]
    first = tmp_path / "one-class.xlsx"
    _write_workbook(first, [later])
    import_schedule_workbook(db, first)
    db.commit()
    kept = db.scalar(select(CourseSession.business_id))

    second = tmp_path / "two-classes.xlsx"
    _write_workbook(second, [earlier, later])
    import_schedule_workbook(db, second)
    db.commit()

    sessions = list(db.scalars(select(CourseSession)))
    assert len(sessions) == 2, [item.business_id for item in sessions]
    assert kept in {item.business_id for item in sessions}


def test_rows_removed_from_the_workbook_are_deleted_on_reimport(
    db: Session, tmp_path: Path
) -> None:
    """导入必须收敛到源表当前状态，不能留下孤儿课次。"""
    rows = [row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM]
    full = tmp_path / "full.xlsx"
    _write_workbook(full, rows)
    import_schedule_workbook(db, full)
    db.commit()
    assert db.scalar(select(func.count(CourseSession.id))) == len(rows)

    trimmed = tmp_path / "trimmed.xlsx"
    _write_workbook(trimmed, rows[:1])
    result = import_schedule_workbook(db, trimmed)
    db.commit()

    assert result["orphans"]["deleted"] == len(rows) - 1
    assert db.scalar(select(func.count(CourseSession.id))) == 1
    assert db.scalar(select(func.count(ScheduleAssignment.id))) == 1


def test_semantic_duplicates_are_collapsed_and_reported(db: Session, tmp_path: Path) -> None:
    """同一节课被登记了两个教师：收敛成一条，并在导入报告里报出差异。"""
    base = next(row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM)
    path = tmp_path / "conflict.xlsx"
    _write_workbook(path, [base, _edit_cell(base, 13, "另一个教研组")])

    result = import_schedule_workbook(db, path)
    db.commit()

    report = result["duplicate_lessons"]
    assert result["rows_deduped"] == 2
    assert result["lessons"] == 1
    assert report["conflicting_lessons"] == 1
    assert report["discarded_rows"] == 1
    assert report["examples"][0]["差异字段"] == ["授课教师"]
    assert db.scalar(select(func.count(CourseSession.id))) == 1


def test_same_class_twice_in_one_slot_is_reported_as_a_data_conflict(
    db: Session, tmp_path: Path
) -> None:
    """同一班级同一时刻两节不同的课是源数据问题，必须报出来而不是留给求解器。"""
    base = next(row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM)
    other = _edit_cell(_edit_cell(base, 9, "数学·另一节课"), 8, 2)
    path = tmp_path / "class-slot.xlsx"
    _write_workbook(path, [base, other])

    result = import_schedule_workbook(db, path)
    db.commit()

    report = result["class_slot_conflicts"]
    assert result["lessons"] == 2
    assert report["conflicting_groups"] == 1
    assert report["extra_lessons"] == 1
    assert len(report["examples"][0]["课节名称"]) == 2


def test_one_slot_conflicts_separate_parallel_subjects_from_same_subject_repeats(
    db: Session, tmp_path: Path
) -> None:
    """走班并行和同科重复必须分开计数。

    客户是走班制：同一个行政班里选数学与不选数学的学生按科目分流，同一格里并排两个
    不同科目是正常业务形态。把它和「同一批学生同一时刻上同一科的两门课」混在一起报，
    教务只会看到一个笼统的冲突数，无从判断该不该改数据。
    """
    base = next(row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM)
    parallel = _edit_cell(_edit_cell(base, 9, "数学·高等数学基础精讲"), 8, 2)
    same_subject = _edit_cell(
        _edit_cell(_edit_cell(base, 2, "另一个班"), 9, "英语·阅读理解方法与真题"), 8, 3
    )
    other_lesson = _edit_cell(_edit_cell(same_subject, 9, "英语·词汇与长难句精讲"), 8, 4)
    path = tmp_path / "walk-in.xlsx"
    _write_workbook(path, [base, parallel, same_subject, other_lesson])

    result = import_schedule_workbook(db, path)
    db.commit()

    report = result["class_slot_conflicts"]
    assert report["conflicting_groups"] == 2
    assert report["cross_subject_groups"] == 1
    assert report["same_subject_groups"] == 1
    parallel_example = next(
        item for item in report["examples"] if item["班级标签"] == base[2]
    )
    assert parallel_example["科目"] == ["数学", "英语"]
    assert parallel_example["科目是否互不相同"] is True


def test_product_type_that_declares_a_subject_absent_but_schedules_it_is_reported(
    db: Session, tmp_path: Path
) -> None:
    """「（无数学）」的产品班型里排了数学课，必须报出来但不能删。

    这些行覆盖整段课次序号、形态与其它课一致，既可能是产品菜单配错、也可能这些班确实
    有少量数学课。删掉就是替业务方裁决，还可能真丢课。
    """
    base = next(row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM)
    no_math = _edit_cell(_edit_cell(base, 1, "考研·暑期强化（无数学）"), 2, "暑假营2班")
    english = _edit_cell(no_math, 9, "英语·词汇与长难句精讲")
    math = _edit_cell(_edit_cell(no_math, 9, "数学·高等数学基础精讲"), 8, 2)
    with_math = _edit_cell(
        _edit_cell(_edit_cell(no_math, 1, "考研·暑期强化（含数学）"), 8, 3),
        9,
        "数学·数学真题与题型突破",
    )
    path = tmp_path / "no-math.xlsx"
    _write_workbook(path, [english, math, with_math])

    result = import_schedule_workbook(db, path)
    db.commit()

    report = result["warnings"]["product_subject_mismatch"]
    assert report["mismatched_lessons"] == 1
    assert report["affected_classes"] == ["暑假营2班"]
    assert report["examples"][0]["产品班型"] == "考研·暑期强化（无数学）"
    assert report["examples"][0]["科目"] == "数学"
    assert report["examples"][0]["课节名称"] == ["数学·高等数学基础精讲"]
    # 只报不删：三节课全部入库。
    assert db.scalar(select(func.count(CourseSession.id))) == 3


def test_header_mismatch_is_rejected_instead_of_silently_misreading(tmp_path: Path) -> None:
    """模板即契约：列顺序变了必须报错，而不是把教室当成班级读进去。"""
    rows = [row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM]
    path = tmp_path / "wrong-order.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    swapped = list(HEADERS)
    swapped[2], swapped[3] = swapped[3], swapped[2]
    sheet.append(swapped)
    for row in rows:
        sheet.append(row)
    workbook.save(path)

    with pytest.raises(WorkbookFormatError) as error:
        _read_rows(path)
    assert "第 3 列" in str(error.value)


def test_unparseable_rows_are_reported_with_line_numbers(tmp_path: Path) -> None:
    """跳过的行必须带行号和原因，否则用户只知道导入了 N 条，不知道少了什么。"""
    base = next(row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM)
    bad_date = _edit_cell(base, 10, "不是日期")
    bad_span = _edit_cell(_edit_cell(base, 8, 2), 11, "上午")
    path = tmp_path / "dirty.xlsx"
    _write_workbook(path, [base, bad_date, bad_span])

    rows, skipped = _read_rows(path)

    assert len(rows) == 1
    assert [item["行号"] for item in skipped] == [3, 4]
    assert "上课日期无法解析" in skipped[0]["原因"]
    assert "08:30-11:30" in skipped[1]["原因"]


def test_version_name_follows_the_campus(db: Session, tmp_path: Path) -> None:
    """版本名此前写死「郑州官方原始课表」，第二所学校会并进同一个版本。"""
    rows = [row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM]
    path = tmp_path / "tj.xlsx"
    _write_workbook(path, rows)

    result = import_schedule_workbook(
        db, path, campus_business_id="CAMPUS-TJ", campus_name="天津校区"
    )
    db.commit()

    assert result["schedule_versions"][0]["name"] == "天津校区官方原始课表"


def test_planned_hours_check_uses_the_actual_lesson_duration(db: Session, tmp_path: Path) -> None:
    """课时校验此前硬编码「每课次 3 小时」，1.5 小时的学校会刷满假告警。"""
    base = next(row for row in _sample_rows() if row[3] != PLACEHOLDER_ROOM)
    consistent = _edit_cell(_edit_cell(_edit_cell(base, 6, 40), 7, 60), 12, 1.5)
    consistent = _edit_cell(consistent, 11, "08:30-10:00")
    path = tmp_path / "hours.xlsx"
    _write_workbook(path, [consistent])

    result = import_schedule_workbook(db, path)
    db.commit()

    assert result["warnings"]["planned_hours_mismatch"] == []
