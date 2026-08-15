from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    CalendarEventBinding,
    Campus,
    ClassGroup,
    CourseSession,
    DataSnapshot,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
)

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
PLACEHOLDER_ROOM = "教室-待校区确认"
CAMPUS_BUSINESS_ID = "CAMPUS-ZZ"
CAMPUS_NAME = "郑州校区"
SHEET_NAME = "课表数据源"
# 模板即导入契约：列名和列序变了，按下标取值的逻辑就会静默错位。
TEMPLATE_HEADERS = (
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
OFFICIAL_VERSION_SUFFIX = "官方原始课表"


class WorkbookFormatError(RuntimeError):
    """工作簿不符合官方模板。调用方应当转成 4xx 而不是 500。"""


def official_version_name(campus_name: str) -> str:
    return f"{campus_name}{OFFICIAL_VERSION_SUFFIX}"


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize_clock(text: str) -> str:
    hours, minutes = (part.strip() for part in text.split(":", 1))
    return f"{int(hours):02d}:{int(minutes):02d}"


def _clock_minutes(clock: str) -> int:
    hours, minutes = (int(part) for part in clock.split(":", 1))
    return hours * 60 + minutes


def _add_minutes(clock: str, minutes: int) -> str:
    hours, mins = (int(part) for part in clock.split(":"))
    total = hours * 60 + mins + minutes
    return f"{total // 60:02d}:{total % 60:02d}"


def _lesson_subject(lesson_name: str) -> str:
    if "·" in lesson_name:
        return lesson_name.split("·", 1)[0].strip()
    return ""


def _validate_headers(sheet: Any) -> None:
    header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
    header = tuple(
        (str(value).strip() if value is not None else "") for value in (header_row or ())
    )
    if header[: len(TEMPLATE_HEADERS)] == TEMPLATE_HEADERS:
        return
    mismatches: list[str] = []
    for index, expected in enumerate(TEMPLATE_HEADERS):
        actual = header[index] if index < len(header) else ""
        if actual != expected:
            mismatches.append(f"第 {index + 1} 列应为「{expected}」，实际为「{actual}」")
    raise WorkbookFormatError(
        "工作簿列名与官方模板不一致，请下载模板后按列填写：" + "；".join(mismatches[:5])
    )


def _read_rows(workbook_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    workbook = load_workbook(workbook_path, data_only=True, read_only=True)
    if SHEET_NAME not in workbook.sheetnames:
        raise WorkbookFormatError(
            f"工作簿缺少「{SHEET_NAME}」工作表，实际为：{workbook.sheetnames}"
        )
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    sheet = workbook[SHEET_NAME]
    _validate_headers(sheet)

    def skip(number: int, reason: str) -> None:
        # 跳过的行必须带行号报出来，否则用户只会看到「导入了 N 条」而不知道少了什么。
        if len(skipped) < 50:
            skipped.append({"行号": number, "原因": reason})

    for number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if not values or all(value is None for value in values):
            continue
        if values[2] is None:
            skip(number, "缺少班级标签")
            continue
        if values[10] is None or values[11] is None:
            skip(number, "缺少上课日期或上课时段")
            continue
        lesson_date = _parse_date(values[10])
        time_text = str(values[11]).strip()
        if lesson_date is None:
            skip(number, f"上课日期无法解析：{values[10]}")
            continue
        if "-" not in time_text:
            skip(number, f"上课时段格式应为 08:30-11:30，实际为：{time_text}")
            continue
        start_text, end_text = (part.strip() for part in time_text.split("-", 1))
        try:
            start = _normalize_clock(start_text)
            end = _normalize_clock(end_text)
        except (ValueError, TypeError):
            skip(number, f"上课时段无法解析：{time_text}")
            continue
        try:
            planned_sessions = int(float(str(values[6]))) if values[6] is not None else 0
            planned_hours = float(str(values[7])) if values[7] is not None else 0
            session_no = int(float(str(values[8]))) if values[8] is not None else 0
            duration_hours = float(str(values[12])) if values[12] is not None else 0
        except (TypeError, ValueError):
            skip(number, "计划课次/计划课时/课次序号/课节时长必须是数字")
            continue
        rows.append(
            {
                "业务线": str(values[0] or "").strip(),
                "产品班型": str(values[1] or "").strip(),
                "班级标签": str(values[2]).strip(),
                "教室标签": str(values[3] or "").strip(),
                "编排来源": str(values[4] or "").strip(),
                "编排阶段": str(values[5] or "").strip(),
                "计划课次": planned_sessions,
                "计划课时": planned_hours,
                "课次序号": session_no,
                "课节名称": str(values[9] or "").strip(),
                "上课日期": lesson_date,
                "上课时段": f"{start}-{end}",
                "开始时间": start,
                "结束时间": end,
                "星期": WEEKDAYS[lesson_date.weekday()],
                "课节时长小时": duration_hours,
                "授课教师": str(values[13] or "").strip(),
            }
        )
    workbook.close()
    return rows, skipped


def _lesson_group(row: dict[str, Any]) -> tuple[str, str, str]:
    """课次组：同一班级、同一天、同一时段的一格。

    注意这一格里出现多节课**不必然**是数据错误：客户是走班制，同一个行政班里选数学
    与不选数学的学生按科目分流，同一格里并排两节不同科目的课是正常业务形态。因此这
    个键只用于统计和报告，不用来删数据。
    """
    return (row["班级标签"], row["上课日期"].isoformat(), row["上课时段"])


def _entirely_lost_values(
    kept: list[dict[str, Any]], dropped: list[dict[str, Any]], field: str
) -> list[str]:
    """只出现在被丢弃行里的取值——即这次导入整建制消失的那一批。"""
    kept_values = {str(row[field]) for row in kept if str(row[field])}
    dropped_values = {str(row[field]) for row in dropped if str(row[field])}
    return sorted(dropped_values - kept_values)


def _split_placeholder_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """丢弃教室待确认的行。

    现行口径：「教室-待校区确认」被当作「该课次不占用校区教室」，整行丢弃。这个口径
    只有代码注释支撑、没有书面依据，而列名字面是「待确认」，两种读法结论相反，必须由
    业务方拍板——所以这里不改行为，只把丢弃的**完整代价**报出来。

    代价必须报到维度级：当某条业务线的行 100% 是占位教室时，整条业务线连同它的教研组、
    时钟窗口和上课日期会从库里消失，此时「跨产品线检查教室/教师冲突」在数据层就不成立
    了，而按行数统计完全看不出这一点。
    """
    kept = [row for row in rows if row["教室标签"] != PLACEHOLDER_ROOM]
    dropped = [row for row in rows if row["教室标签"] == PLACEHOLDER_ROOM]
    kept_groups = {_lesson_group(row) for row in kept}
    dropped_groups = {_lesson_group(row) for row in dropped}
    kept_lessons = {_lesson_identity(row) for row in kept}
    dropped_lessons = {_lesson_identity(row) for row in dropped}
    dropped_dates = {row["上课日期"] for row in dropped} - {row["上课日期"] for row in kept}
    return kept, {
        "placeholder_room": PLACEHOLDER_ROOM,
        "dropped_rows": len(dropped),
        # 按导入器自己的身份键算：这才是「库里少了多少条课次」，行数会被完全重复行放大。
        "dropped_lessons": len(dropped_lessons - kept_lessons),
        # 原实现用 len(dropped_groups - kept_groups)：某一格同时有占位行和真实教室行时，
        # 这一格会被减掉、报成 0，而占位的那几节课照样消失。改为报「受影响的格数」，
        # 并单独报出这种混合格，否则损耗会被自己的统计口径掩盖。
        "dropped_lesson_groups": len(dropped_groups),
        "partially_dropped_lesson_groups": len(dropped_groups & kept_groups),
        "affected_classes": sorted({row["班级标签"] for row in dropped}),
        "dropped_rows_by_business_line": dict(
            sorted(Counter(row["业务线"] for row in dropped).items())
        ),
        "lost_entirely": {
            "business_lines": _entirely_lost_values(kept, dropped, "业务线"),
            "product_types": _entirely_lost_values(kept, dropped, "产品班型"),
            "class_labels": _entirely_lost_values(kept, dropped, "班级标签"),
            "teachers": _entirely_lost_values(kept, dropped, "授课教师"),
            "clock_windows": _entirely_lost_values(kept, dropped, "上课时段"),
            "dates": len(dropped_dates),
        },
    }


def _row_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    """官方确认只删除完全相同的重复行，因此身份键覆盖源表全部 14 个字段。"""
    return (
        row["业务线"],
        row["产品班型"],
        row["班级标签"],
        row["教室标签"],
        row["编排来源"],
        row["编排阶段"],
        row["计划课次"],
        row["计划课时"],
        row["课次序号"],
        row["课节名称"],
        row["上课日期"].isoformat(),
        row["上课时段"],
        row["课节时长小时"],
        row["授课教师"],
    )


LESSON_IDENTITY_FIELDS = ("业务线", "班级标签", "课次序号", "上课日期", "科目")


def _lesson_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    """预处理后的教学需求身份。

    原表四层信息不能平铺成一行一课：产品班型是归属、班级标签是行政班、编排阶段是
    课次阶段，课节名称/上课时段是该学科需求的内容与候选时段。同一个行政班、课次、
    日期和学科只建一条需求；产品、阶段、具体课节名称、时段和教室完整保存在多值字段。
    """
    return (
        row["业务线"],
        row["班级标签"],
        row["课次序号"],
        row["上课日期"].isoformat(),
        _lesson_subject(str(row["课节名称"])),
    )


def _business_id(row: dict[str, Any], campus_business_id: str) -> str:
    digest = _digest(_lesson_identity(row))
    return f"{campus_business_id}-{digest[:16]}"


def _digest(value: tuple[Any, ...]) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().upper()


def _source_row_id(row: dict[str, Any]) -> str:
    return _digest(_row_identity(row))


def _source_group_id(rows: list[dict[str, Any]]) -> str:
    """所有来源变体共同决定追溯 ID，行序变化不会导致新 ID。"""
    return _digest(tuple(sorted(_source_row_id(row) for row in rows)))


def _product_contexts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["产品班型"])].append(row)
    contexts: list[dict[str, Any]] = []
    for product_type, items in sorted(grouped.items()):
        contexts.append(
            {
                "product_type": product_type,
                "stages": sorted({str(item["编排阶段"]) for item in items}),
                "lesson_names": sorted({str(item["课节名称"]) for item in items}),
                "planned_sessions": sorted({int(item["计划课次"]) for item in items}),
                "planned_hours": sorted({float(item["计划课时"]) for item in items}),
                "schedule_sources": sorted({str(item["编排来源"]) for item in items}),
            }
        )
    return contexts


def _collect_lesson_rows(
    rows: list[dict[str, Any]], campus_business_id: str
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """把平铺源表预处理成可求解的「行政班 × 课次 × 学科」需求。

    不丢任何语义：多产品班型、多阶段、多个具体课节名称、候选时段与候选教室全部
    保留。单值列只作为兼容主值，求解和页面展示读取对应多值字段。
    """
    grouped: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {}
    for row in rows:
        business_id = _business_id(row, campus_business_id)
        grouped.setdefault(business_id, {})[_row_identity(row)] = row

    session_rows: dict[str, dict[str, Any]] = {}
    examples: list[dict[str, Any]] = []
    ambiguous_teachers = 0
    ambiguous_rooms = 0
    ambiguous_demands = 0
    multi_product_demands = 0
    multi_lesson_name_demands = 0
    multi_slot_demands = 0
    for business_id, variants in grouped.items():
        ordered = [variants[key] for key in sorted(variants)]
        merged = dict(ordered[0])
        product_types = sorted({str(item["产品班型"]) for item in ordered})
        stages = sorted({str(item["编排阶段"]) for item in ordered})
        lesson_names = sorted({str(item["课节名称"]) for item in ordered})
        teachers = sorted({str(item["授课教师"]) for item in ordered})
        rooms = sorted({str(item["教室标签"]) for item in ordered})
        clock_windows = sorted(
            {(str(item["开始时间"]), str(item["结束时间"])) for item in ordered},
            key=lambda item: (_clock_minutes(item[0]), _clock_minutes(item[1])),
        )
        contexts = _product_contexts(ordered)
        subject = _lesson_subject(str(ordered[0]["课节名称"]))
        merged.update(
            {
                "科目": subject,
                "产品班型": product_types[0],
                "产品班型列表": product_types,
                "产品上下文": contexts,
                "编排阶段": " / ".join(stages),
                "编排阶段列表": stages,
                "课节名称": " / ".join(lesson_names),
                "课节名称列表": lesson_names,
                "授课教师": teachers[0],
                "授课教师列表": teachers,
                "教室标签": rooms[0],
                "候选教室列表": rooms,
                "上课时段": f"{clock_windows[0][0]}-{clock_windows[0][1]}",
                "开始时间": clock_windows[0][0],
                "结束时间": clock_windows[0][1],
                "候选时钟窗口": [
                    {"start_time": start, "end_time": end} for start, end in clock_windows
                ],
                "来源变体数": len(ordered),
                "来源组标识": _source_group_id(ordered),
            }
        )
        session_rows[business_id] = merged
        multi_product_demands += int(len(product_types) > 1)
        multi_lesson_name_demands += int(len(lesson_names) > 1)
        multi_slot_demands += int(len(clock_windows) > 1)
        ambiguous_teachers += int(len(teachers) > 1)
        ambiguous_rooms += int(len(rooms) > 1)
        ambiguous_demands += int(len(teachers) > 1 or len(rooms) > 1)
        if len(ordered) > 1 and len(examples) < 20:
            examples.append(
                {
                    "业务标识": business_id,
                    "班级标签": merged["班级标签"],
                    "课次序号": int(merged["课次序号"]),
                    "上课日期": merged["上课日期"].isoformat(),
                    "科目": subject,
                    "产品班型": product_types,
                    "编排阶段": stages,
                    "课节名称": lesson_names,
                    "候选时段": [f"{start}-{end}" for start, end in clock_windows],
                    "候选教室": rooms,
                    "教师": teachers,
                    "来源变体数": len(ordered),
                }
            )
    return session_rows, {
        # 兼容旧接口名：这里只统计预处理后仍有多教师/多教室的真正歧义，合并产品、
        # 内容名称和候选时段不再叫“冲突”，因为这些信息已完整保留。
        "conflicting_lessons": ambiguous_demands,
        "discarded_rows": 0,
        "source_rows": len(rows),
        "preprocessed_demands": len(session_rows),
        "collapsed_source_variants": len(rows) - len(session_rows),
        "multi_product_demands": multi_product_demands,
        "multi_lesson_name_demands": multi_lesson_name_demands,
        "multi_slot_demands": multi_slot_demands,
        "ambiguous_teacher_demands": ambiguous_teachers,
        "ambiguous_room_demands": ambiguous_rooms,
        "examples": examples,
    }


def _upsert(db: Session, model: type[Any], match: dict[str, Any], values: dict[str, Any]) -> Any:
    statement = select(model)
    for key, value in match.items():
        statement = statement.where(getattr(model, key) == value)
    instance = db.scalar(statement)
    if instance is None:
        instance = model(**match, **values)
        db.add(instance)
    else:
        for key, value in values.items():
            setattr(instance, key, value)
    return instance


def _class_slot_conflicts(session_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """报告「同一班级同一天同一时段有多节课」，并按科目分成两类。

    这一格里有多节课，在走班制下有两种完全不同的含义，混在一起报会把教务引向错误的
    修数据方向：

    - 科目互不相同：可能是**并行走班**（选数学的去数学教室，其余人上公共课），也可能
      是同一批学生被排了两节课。只看这张表分辨不了，必须由业务方裁决。
    - 同一科目出现多次：同一批学生同一时刻上同一科的两门课，教学上不成立，更像是源表
      把科目菜单展开到了每个课次上。

    两类都不在导入阶段删数据——删错了就是真丢课。
    """
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in session_rows.values():
        for window in row.get("候选时钟窗口") or []:
            key = (
                str(row["班级标签"]),
                row["上课日期"].isoformat(),
                f"{window['start_time']}-{window['end_time']}",
            )
            grouped.setdefault(key, []).append(row)
    conflicts: list[dict[str, Any]] = []
    for key, items in grouped.items():
        if len(items) <= 1:
            continue
        subjects = [str(item.get("科目") or "") for item in items]
        conflicts.append(
            {
                "班级标签": key[0],
                "上课日期": key[1],
                "上课时段": key[2],
                "课节数": len(items),
                "课节名称": sorted(
                    {
                        str(name)
                        for item in items
                        for name in item.get("课节名称列表") or [item["课节名称"]]
                    }
                ),
                "科目": sorted(set(subjects)),
                "科目是否互不相同": len(set(subjects)) == len(subjects),
            }
        )
    conflicts.sort(key=lambda item: (str(item["班级标签"]), str(item["上课日期"])))
    cross_subject = [item for item in conflicts if item["科目是否互不相同"]]
    return {
        "lesson_groups": len(grouped),
        "conflicting_groups": len(conflicts),
        "extra_lessons": sum(int(str(item["课节数"])) - 1 for item in conflicts),
        # 走班制下这一类未必是错，单独计数，避免把并行走班当成必须清理的脏数据。
        "cross_subject_groups": len(cross_subject),
        "same_subject_groups": len(conflicts) - len(cross_subject),
        "examples": conflicts[:20],
    }


def _product_subject_mismatches(session_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """报告「产品班型声明不含某科目，却排了该科目的课」。

    例如「考研·暑期强化（无数学）」下出现「数学·高等数学基础精讲」。这批行不是随机噪声：
    它们覆盖整段课次序号、形态与其它课一致，既可能是产品班型菜单配错，也可能是这些班
    确实有少量该科目的课。两种读法对「能不能按产品班型名判定走班轨道」的结论完全相反，
    所以这里只报不删——删掉就是替业务方做了裁决。
    """
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in session_rows.values():
        subject = str(row.get("科目") or "")
        if not subject:
            continue
        for product_type in row.get("产品班型列表") or [str(row["产品班型"])]:
            product_type = str(product_type)
            # 「无{科目}」是产品班型名里对科目的显式否定声明。
            if f"无{subject}" not in product_type:
                continue
            key = (str(row["业务线"]), product_type, str(row["班级标签"]), subject)
            grouped.setdefault(key, []).append(row)
    examples = [
        {
            "业务线": key[0],
            "产品班型": key[1],
            "班级标签": key[2],
            "科目": key[3],
            "课次数": len(items),
            "课节名称": sorted(
                {
                    str(name)
                    for item in items
                    for name in item.get("课节名称列表") or [item["课节名称"]]
                }
            ),
            "日期范围": [
                min(item["上课日期"] for item in items).isoformat(),
                max(item["上课日期"] for item in items).isoformat(),
            ],
        }
        for key, items in grouped.items()
    ]
    # 排序必须完全确定，否则同一份表两次导入的报告会抖动。
    examples.sort(
        key=lambda item: (
            -int(str(item["课次数"])),
            str(item["产品班型"]),
            str(item["班级标签"]),
            str(item["科目"]),
        )
    )
    return {
        "rule": "产品班型名称含「无{科目}」，但该课次的课节名称属于该科目",
        "mismatched_lessons": sum(len(items) for items in grouped.values()),
        "affected_classes": sorted({str(key[2]) for key in grouped}),
        "examples": examples[:20],
    }


def _remove_orphan_sessions(
    db: Session,
    campus_id: str,
    keep_business_ids: set[str],
    official_version_name: str,
    schedule_set_id: str = "default",
) -> dict[str, Any]:
    """删除源表里已经不存在的课次，让导入收敛到工作簿的当前状态。

    唯一的例外是被求解产出的课表版本引用过的课次：直接删会破坏历史版本与回滚链，
    因此保留并报出来，由教务决定怎么处理。
    """
    orphans = [
        item
        for item in db.scalars(select(CourseSession).where(CourseSession.campus_id == campus_id))
        if item.business_id not in keep_business_ids
    ]
    if not orphans:
        return {"deleted": 0, "retained_by_schedule": 0, "retained_examples": []}

    official_version_id = db.scalar(
        select(ScheduleVersion.id).where(
            ScheduleVersion.schedule_set_id == schedule_set_id,
            ScheduleVersion.name == official_version_name,
        )
    )
    orphan_ids = {item.id for item in orphans}
    referenced_elsewhere = set(
        db.scalars(
            select(ScheduleAssignment.course_session_id).where(
                ScheduleAssignment.course_session_id.in_(orphan_ids),
                ScheduleAssignment.schedule_version_id != official_version_id
                if official_version_id
                else ScheduleAssignment.course_session_id.is_not(None),
            )
        )
    )
    removable = [item for item in orphans if item.id not in referenced_elsewhere]
    retained = [item for item in orphans if item.id in referenced_elsewhere]
    if removable:
        removable_ids = [item.id for item in removable]
        db.execute(
            delete(CalendarEventBinding).where(
                CalendarEventBinding.course_session_id.in_(removable_ids)
            )
        )
        db.execute(
            delete(ScheduleAssignment).where(
                ScheduleAssignment.course_session_id.in_(removable_ids)
            )
        )
        db.execute(delete(CourseSession).where(CourseSession.id.in_(removable_ids)))
        db.flush()
    return {
        "deleted": len(removable),
        "retained_by_schedule": len(retained),
        "retained_examples": sorted(item.business_id for item in retained)[:20],
    }


def import_schedule_workbook(
    db: Session,
    workbook_path: Path,
    campus_business_id: str = CAMPUS_BUSINESS_ID,
    campus_name: str = CAMPUS_NAME,
    schedule_set_id: str = "default",
) -> dict[str, Any]:
    source_rows, skipped_rows = _read_rows(workbook_path)
    if not source_rows:
        raise RuntimeError(f"未从 {workbook_path.name} 解析到任何数据行")
    rows, placeholder_report = _split_placeholder_rows(source_rows)
    if not rows:
        raise RuntimeError(
            f"{workbook_path.name} 的全部 {len(source_rows)} 行教室标签均为"
            f"「{PLACEHOLDER_ROOM}」，没有可排课的课次"
        )

    campus = _upsert(db, Campus, {"business_id": campus_business_id}, {"name": campus_name})
    db.flush()

    teachers = sorted({row["授课教师"] for row in rows if row["授课教师"]})
    for name in teachers:
        subjects = Counter(
            _lesson_subject(row["课节名称"])
            for row in rows
            if row["授课教师"] == name and _lesson_subject(row["课节名称"])
        )
        _upsert(
            db,
            Teacher,
            {"campus_id": campus.id, "business_id": name},
            {
                "name": name,
                "subject": subjects.most_common(1)[0][0] if subjects else "",
                # 源表「授课教师」列填的是教研组，不是自然人。
                "is_group": True,
            },
        )

    class_labels = sorted({row["班级标签"] for row in rows})
    for label in class_labels:
        # 班级只写身份。班型/业务线/教师以前是各自取众数猜出来的，三个众数彼此不保证
        # 来自同一批行，能猜出「班型=无数学 + 教师=数学教研组」这种自相矛盾的组合。
        # 现在由接口从 course_sessions 实时聚合，导入侧不再猜。
        _upsert(
            db,
            ClassGroup,
            {"campus_id": campus.id, "business_id": label},
            {"name": label},
        )

    room_labels = sorted({row["教室标签"] for row in rows if row["教室标签"]})
    for label in room_labels:
        _upsert(
            db,
            Room,
            {"campus_id": campus.id, "business_id": label},
            {"name": label, "is_active": True},
        )

    # 时段顺序按真实上课时间排序，不依赖任何校区的固定作息表。
    clock_ranges = sorted(
        {(row["开始时间"], row["结束时间"]) for row in rows},
        key=lambda item: (_clock_minutes(item[0]), _clock_minutes(item[1])),
    )
    slot_keys = sorted(
        {(weekday, start, end) for weekday in WEEKDAYS for start, end in clock_ranges},
        key=lambda item: (
            WEEKDAYS.index(item[0]),
            _clock_minutes(item[1]),
            _clock_minutes(item[2]),
        ),
    )
    # 时段标识必须带结束时间：同一开始时间可以对应不同时长（例如 08:30-10:00 与
    # 08:30-11:30），只用开始时间会让两个时段撞同一个业务标识并触发唯一约束冲突。
    slot_business_ids: dict[tuple[str, str, str], str] = {}
    for sequence, (weekday, start, end) in enumerate(slot_keys, 1):
        business_id = f"SLOT-{weekday}-{start.replace(':', '')}-{end.replace(':', '')}"
        slot_business_ids[(weekday, start, end)] = business_id
        _upsert(
            db,
            TimeSlot,
            {"campus_id": campus.id, "business_id": business_id},
            {
                "weekday": weekday,
                "start_time": start,
                "end_time": end,
                "kind": f"{start}-{end}",
                "sequence": sequence,
            },
        )

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        deduped[_row_identity(row)] = row

    session_rows, duplicate_report = _collect_lesson_rows(
        list(deduped.values()), campus_business_id
    )

    existing_sessions = {
        item.business_id: item
        for item in db.scalars(select(CourseSession).where(CourseSession.campus_id == campus.id))
    }
    new_rows = []
    for business_id, row in session_rows.items():
        candidate_clock_windows = list(row["候选时钟窗口"])
        candidate_slot_ids = [
            slot_business_ids[(row["星期"], item["start_time"], item["end_time"])]
            for item in candidate_clock_windows
        ]
        values = {
            "source_row_id": row["来源组标识"],
            "business_line": row["业务线"],
            "product_type": row["产品班型"],
            "product_types": row["产品班型列表"],
            "product_contexts": row["产品上下文"],
            "class_business_id": row["班级标签"],
            "teacher_business_id": row["授课教师"],
            "teacher_business_ids": row["授课教师列表"],
            "subject": row["科目"],
            "lesson_name": row["课节名称"],
            "lesson_names": row["课节名称列表"],
            "schedule_source": row["编排来源"],
            "stage": row["编排阶段"],
            "stages": row["编排阶段列表"],
            "planned_sessions": row["计划课次"],
            "planned_hours": row["计划课时"],
            "session_no": row["课次序号"],
            "lesson_date": row["上课日期"],
            "duration_minutes": int(row["课节时长小时"] * 60),
            "suggested_slot_id": candidate_slot_ids[0],
            "candidate_slot_ids": candidate_slot_ids,
            "candidate_clock_windows": candidate_clock_windows,
            "fixed_start_time": row["开始时间"],
            "fixed_end_time": row["结束时间"],
            "original_room_business_id": row["教室标签"],
            "candidate_room_business_ids": row["候选教室列表"],
            "source_variant_count": row["来源变体数"],
        }
        existing = existing_sessions.get(business_id)
        if existing is None:
            new_rows.append((business_id, values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
    for start_index in range(0, len(new_rows), 500):
        chunk = new_rows[start_index : start_index + 500]
        db.add_all(
            [
                CourseSession(
                    campus_id=campus.id,
                    business_id=business_id,
                    **values,
                )
                for business_id, values in chunk
            ]
        )
        db.flush()

    orphan_report = _remove_orphan_sessions(
        db,
        campus.id,
        set(session_rows),
        official_version_name(campus_name),
        schedule_set_id,
    )

    session_ids: dict[str, str] = {
        business_id: session_id
        for business_id, session_id in db.execute(
            select(CourseSession.business_id, CourseSession.id).where(
                CourseSession.campus_id == campus.id
            )
        )
    }

    checksum = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    revision = (
        db.scalar(
            select(func.max(DataSnapshot.revision)).where(
                DataSnapshot.schedule_set_id == schedule_set_id
            )
        )
        or 0
    ) + 1
    snapshot = DataSnapshot(
        schedule_set_id=schedule_set_id,
        revision=revision,
        checksum=checksum,
        payload={
            "source": workbook_path.name,
            "rows_total": len(source_rows),
            "rows_skipped": len(skipped_rows),
            "rows_dropped_placeholder_room": placeholder_report["dropped_rows"],
            "rows_kept": len(rows),
            "rows_deduped": len(deduped),
            "preprocessing": duplicate_report,
        },
    )
    db.add(snapshot)
    db.flush()

    version_stats: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    version_name = official_version_name(campus_name)
    version = db.scalar(
        select(ScheduleVersion).where(
            ScheduleVersion.schedule_set_id == schedule_set_id,
            ScheduleVersion.name == version_name,
        )
    )
    if version is None:
        run = SolverRun(
            schedule_set_id=schedule_set_id,
            snapshot_id=snapshot.id,
            run_type="import",
            status="completed",
            model_status="IMPORTED",
            request_payload={
                "source": workbook_path.name,
                "deduplication": "exact_row",
                "preprocessing": "class_session_subject",
            },
        )
        db.add(run)
        db.flush()
        version_no = (
            db.scalar(
                select(func.max(ScheduleVersion.version_no)).where(
                    ScheduleVersion.schedule_set_id == schedule_set_id
                )
            )
            or 0
        ) + 1
        version = ScheduleVersion(
            schedule_set_id=schedule_set_id,
            version_no=version_no,
            name=version_name,
            status="published",
            solver_run_id=run.id,
            published_at=now,
            metrics={"assignment_count": len(session_rows), "source_rows": len(rows)},
        )
        db.add(version)
        db.flush()
    else:
        db.execute(
            delete(ScheduleAssignment).where(ScheduleAssignment.schedule_version_id == version.id)
        )
        version.status = "published"
        version.published_at = now
        version.metrics = {"assignment_count": len(session_rows), "source_rows": len(rows)}
    for business_id, row in session_rows.items():
        db.add(
            ScheduleAssignment(
                schedule_version_id=version.id,
                course_session_id=session_ids[business_id],
                lesson_date=row["上课日期"],
                slot_business_id=slot_business_ids[(row["星期"], row["开始时间"], row["结束时间"])],
                room_business_id=row["教室标签"],
            )
        )
    db.flush()
    version_stats.append({"name": version_name, "rows": len(session_rows)})

    # 计划课时是否自洽，按该班型自己的实际课节时长核对，不假定每课次固定 3 小时。
    hour_warnings: list[dict[str, Any]] = []
    for product_type in sorted({row["产品班型"] for row in rows}):
        product_rows = [row for row in rows if row["产品班型"] == product_type]
        durations = {row["课节时长小时"] for row in product_rows if row["课节时长小时"]}
        if len(durations) != 1:
            continue
        duration = next(iter(durations))
        for planned_sessions, planned_hours in {
            (row["计划课次"], row["计划课时"]) for row in product_rows
        }:
            if planned_sessions and abs(planned_sessions * duration - planned_hours) > 0.01:
                hour_warnings.append(
                    {
                        "产品班型": product_type,
                        "计划课次": planned_sessions,
                        "计划课时": planned_hours,
                        "课节时长小时": duration,
                        "按课次×课节时长": round(planned_sessions * duration, 2),
                    }
                )

    return {
        "campus": campus_business_id,
        "rows_total": len(source_rows),
        "rows_skipped": len(skipped_rows),
        "skipped_examples": skipped_rows[:20],
        "rows_dropped_placeholder_room": placeholder_report["dropped_rows"],
        "rows_kept": len(rows),
        "rows_deduped": len(deduped),
        "teachers": len(teachers),
        "class_groups": len(class_labels),
        "rooms": len(room_labels),
        "time_slots": len(slot_keys),
        "course_sessions_created": len(new_rows),
        "schedule_versions": version_stats,
        "lessons": len(session_rows),
        "duplicate_lessons": duplicate_report,
        "class_slot_conflicts": _class_slot_conflicts(session_rows),
        "orphans": orphan_report,
        "warnings": {
            "dropped_placeholder_room": placeholder_report,
            "teachers_are_groups": teachers,
            "planned_hours_mismatch": hour_warnings,
            "product_subject_mismatch": _product_subject_mismatches(session_rows),
        },
    }


def import_zhengzhou(db: Session, workbook_path: Path) -> dict[str, Any]:
    return import_schedule_workbook(db, workbook_path)


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python -m app.services.converter_zhengzhou <xlsx 路径>")
        raise SystemExit(2)
    workbook_path = Path(sys.argv[1])
    with SessionLocal() as db:
        result = import_schedule_workbook(db, workbook_path)
        db.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
