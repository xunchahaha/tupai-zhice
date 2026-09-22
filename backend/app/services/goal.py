"""持久目标验收闭环（MEM-C3，docs/roadmap/02 §6「目标闭环要点」）。

核心思想：求解任务结束 ≠ 目标完成；找到可行解 ≠ 用户全部要求都完成。
把用户的一句话目标拆成可逐项验收的 checklist，每次关联的 SolverRun 到达
completed 后由**代码验收器**出报告（缺口 + 证据 + 允许的下一步），回灌同一
目标。本模块零求解逻辑：全部复用既有 count_hard_conflicts / 快照范围选择 /
审计日志查询，solver.py 不做任何改动。

两条红线写死在实现里：
1. 「尽量少改」是优化目标——验收只设上限（max_changes），并在报告里标注
   优化类属性，绝不把「尽量」升级为「绝不」；
2. 「不能上」「不发布」不得降级为尽量——draft_only 核对审计日志里该 run
   之后有无 publish/calendar 动作，不信任任何自报状态。

停止规则：验收器只出报告，绝不自动重跑；不存在允许补救动作的缺口保持
open 并附终态报告。状态永远反映最近一次验收（abandoned 人工终态除外）。
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import func, select

from ..models import (
    AuditLog,
    ClassGroup,
    CourseSession,
    DataSnapshot,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolveGoal,
    SolverRun,
    Teacher,
    TimeSlot,
)
from ..timezone import shanghai_now
from .solver import _selected_sessions
from .tasks import count_hard_conflicts

logger = logging.getLogger("tupai.memory")

# checklist kind 与验收器一一对应：coverage=_check_coverage、
# forbidden_slot_free=_check_forbidden_slots、no_hard_conflicts=_check_no_hard_conflicts、
# max_changes=_check_max_changes、draft_only=_check_draft_only、date_range_match=_check_date_range、
# deliverable_exists=_check_deliverable_exists。
GOAL_CHECKLIST_KINDS = (
    "deliverable_exists",
    "coverage",
    "forbidden_slot_free",
    "no_hard_conflicts",
    "max_changes",
    "draft_only",
    "date_range_match",
)
GOAL_STATUSES = ("open", "awaiting_decision", "achieved", "abandoned")

# 验收状态三态（MEM-D2/D6）：这是「最近一次验收执行本身」的状态，与目标状态机
# （GOAL_STATUSES）正交。pending=run 已完成但报告未生成；completed=报告已落库；
# failed=验收执行本身异常（原因在 SolveGoal.acceptance_detail）。
GOAL_ACCEPTANCE_STATUSES = ("pending", "completed", "failed")

# 底线验收（MEM-D2/D4c）：无论清单来自自动生成还是用户自定义，这三类都必须在——
# 有合格交付物、目标课次完整且不重复（有明确目标集合时）、硬冲突重算。前端给这些
# 项打「底线」徽标，用户附加清单与底线并列验收。
BOTTOM_LINE_KINDS = ("deliverable_exists", "coverage", "no_hard_conflicts")

# draft_only 验收口径：审计日志里这些动作发生在该 run 创建之后即视为违规。
PUBLISH_AUDIT_ACTIONS = ("publish", "calendar_publish")

# 一句话指令里出现这些 cues 时，interpret 草稿会预填一条禁排占位项——
# 参数未量化前验收器不给它通过（宁可卡住也不虚报完成）。
_FORBIDDEN_CUES = re.compile(r"禁排|不排|不要排|不安排|不要安排|避开|不占")

_SUBJECT_TYPE_LABELS = {
    "teacher": "教师",
    "classroom": "教室",
    "cohort": "班级",
    "course": "课程",
}


def _item(key: str, requirement: str, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"key": key, "requirement": requirement, "kind": kind, "params": params}


def _bottom_line_item(
    kind: str, requirement: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """底线验收项：params.bottom_line=True 是前端「底线」徽标的依据。"""
    return _item(
        kind,
        requirement,
        kind,
        {"bottom_line": True, **(params or {})},
    )


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _join(values: list[str] | set[str]) -> str:
    ordered = sorted(values)
    return "、".join(ordered[:8]) + ("…" if len(ordered) > 8 else "")


# ---------------------------------------------------------------- 清单生成


def build_checklist(
    instruction: str,
    *,
    business_lines: list[str] | None = None,
    product_types: list[str] | None = None,
    class_business_ids: list[str] | None = None,
    course_business_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    forbidden_slots: list[dict[str, Any]] | None = None,
    max_changes: int | None = None,
    baseline_schedule_version_id: str | None = None,
    forbid_publish: bool = True,
) -> list[dict[str, Any]]:
    """由结构化范围（通常是 /assistant/interpret 的产物）确定性生成验收清单。

    纯代码、零 LLM 依赖：每一项都对应一个可执行的验收器。指令原文另存
    SolveGoal.instruction，清单只保存可核对的口径。
    """
    del instruction  # 生成口径只来自结构化字段；原文在 Goal.instruction 里留档。
    lines = [v for v in (business_lines or []) if str(v).strip()]
    types = [v for v in (product_types or []) if str(v).strip()]
    classes = [v for v in (class_business_ids or []) if str(v).strip()]
    courses = [v for v in (course_business_ids or []) if str(v).strip()]
    scope_bits = [
        label
        for label, values in (
            ("业务线", lines),
            ("产品班型", types),
            ("班级", classes),
            ("课次", courses),
        )
        if values
    ]
    coverage_requirement = (
        "求解结果覆盖全部目标课次（逐项比对，不允许漏排）"
        + (
            f"——范围：{'／'.join(scope_bits)}"
            if scope_bits
            else "——未给范围时按本次求解已选课次核对"
        )
    )
    # 底线在前（MEM-D2/D4c）：没有非空课表产物，其余一切核对无从谈起。
    items = [
        _bottom_line_item(
            "deliverable_exists",
            "存在非空课表产物（本次求解至少安置 1 个课次）",
        ),
        _bottom_line_item(
            "coverage",
            coverage_requirement,
            {
                "business_lines": lines,
                "product_types": types,
                "class_business_ids": classes,
                "course_business_ids": courses,
                "date_from": date_from,
                "date_to": date_to,
            },
        ),
    ]
    if date_from or date_to:
        items.append(
            _item(
                "date_range_match",
                f"结果日期全部落在请求范围内（{date_from or '不限'} ~ {date_to or '不限'}）",
                "date_range_match",
                {"date_from": date_from, "date_to": date_to},
            )
        )
    for index, entry in enumerate(forbidden_slots or [], start=1):
        subject_type = str(entry.get("subject_type") or "teacher")
        subject_ids = [str(v) for v in entry.get("subject_ids") or [] if str(v).strip()]
        slots = [str(v) for v in entry.get("slot_business_ids") or [] if str(v).strip()]
        items.append(
            _item(
                f"forbidden_slot_free-{index}",
                (
                    f"{_SUBJECT_TYPE_LABELS.get(subject_type, subject_type)}"
                    f" {'、'.join(subject_ids) or '（未指定主体）'} 不占用指定时段"
                    f"（{'、'.join(slots) or '（未指定时段）'}）——独立复核，不信任求解器自报"
                ),
                "forbidden_slot_free",
                {
                    "subject_type": subject_type,
                    "subject_ids": subject_ids,
                    "slot_business_ids": slots,
                },
            )
        )
    items.append(
        _bottom_line_item(
            "no_hard_conflicts",
            "结果课表无硬冲突（按教室/班级/教师/日程独立重算，不信任求解器自报）",
        )
    )
    if max_changes is not None:
        items.append(
            _item(
                "max_changes",
                (
                    f"相对基准版本的变更数 ≤ {max_changes}"
                    "（优化类目标：只设验收上限，「尽量少改」不升级为「绝不改」）"
                ),
                "max_changes",
                {
                    "max_changes": max_changes,
                    "baseline_schedule_version_id": baseline_schedule_version_id,
                },
            )
        )
    if forbid_publish:
        items.append(
            _item(
                "draft_only",
                "只交付草稿：本目标期间不发生发布或日历下发（审计日志核对）",
                "draft_only",
                {},
            )
        )
    return items


def draft_checklist_from_interpretation(
    instruction: str, parsed: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """/assistant/interpret 的响应预填清单草稿（前端可增删项后再创建 goal）。

    业务范围→coverage、日期→date_range_match、禁排语→forbidden_slot_free 占位。
    占位项带 needs_params=True：参数未量化前验收器判不通过——解析通道给不出
    具体时段，与其虚放一个恒真检查，不如明示「待教务补充」。
    """
    items = build_checklist(
        instruction,
        business_lines=parsed.get("business_lines") or [],
        product_types=parsed.get("product_types") or [],
        class_business_ids=parsed.get("class_business_ids") or [],
        course_business_ids=parsed.get("course_business_ids") or [],
        date_from=parsed.get("date_from"),
        date_to=parsed.get("date_to"),
    )
    warnings: list[str] = []
    if _FORBIDDEN_CUES.search(instruction):
        items.append(
            _item(
                "forbidden_slot_free-draft",
                "禁排要求待量化：补充主体与具体时段后才能独立复核",
                "forbidden_slot_free",
                {
                    "subject_type": "teacher",
                    "subject_ids": [],
                    "slot_business_ids": [],
                    "needs_params": True,
                },
            )
        )
        warnings.append(
            "识别到禁排类要求，但解析通道给不出具体时段；清单里已放一条待量化项，"
            "请在创建目标前补充主体与时段，否则该项验收不会通过。"
        )
    return items, warnings


def ensure_bottom_line_items(
    checklist: list[dict[str, Any]], *, has_target_set: bool
) -> list[dict[str, Any]]:
    """底线验收与自定义清单强制并列（MEM-D2/D4c），创建目标时由 API 层调用。

    底线项不可删除：用户传了自定义清单也必须并入——
    - `deliverable_exists`：run 必须存在非空课表产物，杜绝「仅 draft_only 的
      清单在零课表上也达成」；
    - `no_hard_conflicts`：交付课表硬冲突独立重算；
    - `coverage`：仅当请求带明确目标集合（课次/班级/业务线/班型范围）时并入。
    清单里已有同 kind 的项不重复追加（用户自己的口径优先），底线由
    「缺失即补齐」保证，不由用户自觉保证。
    """
    merged = [dict(item) for item in checklist]
    present = {str(item.get("kind") or "") for item in merged}
    if "deliverable_exists" not in present:
        merged.append(
            _bottom_line_item(
                "deliverable_exists",
                "存在非空课表产物（本次求解至少安置 1 个课次）",
            )
        )
    if "no_hard_conflicts" not in present:
        merged.append(
            _bottom_line_item(
                "no_hard_conflicts",
                "结果课表无硬冲突（按教室/班级/教师/日程独立重算，不信任求解器自报）",
            )
        )
    if has_target_set and "coverage" not in present:
        merged.append(
            _bottom_line_item(
                "coverage",
                "目标课次完整且不重复（逐项比对，不允许漏排或重复交付）",
            )
        )
    return merged


# ---------------------------------------------------------------- 验收器


def _course_subjects(course: CourseSession, subject_type: str) -> set[str]:
    if subject_type == "teacher":
        values = {str(course.teacher_business_id or "")}
        values.update(str(v) for v in course.teacher_business_ids or [])
        values.discard("")
        return values
    if subject_type == "cohort":
        return {str(course.class_business_id or "")} - {""}
    if subject_type == "course":
        return {str(course.business_id or "")} - {""}
    return set()


def _unverifiable(key: str, requirement: str, kind: str, detail: str) -> dict[str, Any]:
    """没有可核对对象（无课表/缺参数/缺日期）≠ 通过：verdict=unverifiable。

    MEM-D2/D4b：不能因为「没检测到越界」就判通过——该项 passed=False 并带
    verdict="unverifiable"，detail 说明缺什么；整体 all_passed 要求所有项
    passed=True 且无 unverifiable。
    """
    return {
        "key": key,
        "requirement": requirement,
        "kind": kind,
        "passed": False,
        "verdict": "unverifiable",
        "detail": detail,
        "evidence": None,
    }


# ------------------------------------------------- 三集合口径（MEM-D2/D4a）
#
# 一次带 goal 的局部重排会产生三个不同的课次集合，验收前必须分清：
# 1. 目标课次（期望集合）：goal 清单 coverage 参数（显式课次 id 或范围过滤）
#    从当前方案解析出的集合——验收的「应然」；
# 2. 本次求解课次：本次 run 实际排的课次——验收的「本次实然」；
# 3. 合并交付课表：_persist_result 会把父版本里未参与本次重排的旧课次
#    （change_kind="unchanged"）合并回最终课表——它只用于 no_hard_conflicts
#    这类「交付物全局自洽」检查。
# date_range_match / coverage / forbidden_slot_free 只看 1∪2；把合并回填的
# 旧课次混进来，就会把「保留上周原样」误判成「日期越界/禁排违规」。


def _deliverable_assignments(run: SolverRun) -> list[dict[str, Any]]:
    """合并交付课表：result_payload.assignments（含父版本合并回填的保留行）。"""
    return [
        dict(a)
        for a in (run.result_payload or {}).get("assignments") or []
        if isinstance(a, dict)
    ]


def _solved_course_business_ids(run: SolverRun) -> set[str]:
    """本次求解课次（业务 id 集合）。

    取数口径：`_persist_result` 在把父版本保留行合并进 assignments **之前**，
    先从求解产物计算出 `solved_course_business_ids` 落入 result_payload——
    这是「求解产物」与「合并回填」最稳的分界，不依赖行内字段约定。旧载荷
    缺该字段时退回 `change_kind != "unchanged"` 口径（求解器产物不带
    change_kind，合并回填行恒为 "unchanged"）。
    """
    solved = {
        str(v)
        for v in (run.result_payload or {}).get("solved_course_business_ids") or []
        if str(v).strip()
    }
    if solved:
        return solved
    return {
        str(a.get("course_business_id"))
        for a in _deliverable_assignments(run)
        if a.get("course_business_id") and a.get("change_kind") != "unchanged"
    }


def _goal_expected_course_ids(db: Any, goal: SolveGoal) -> set[str]:
    """目标课次（期望集合）：取 goal 清单里第一条 coverage 项的口径解析。"""
    for entry in goal.checklist or []:
        if str(entry.get("kind")) == "coverage":
            return _expected_course_ids(db, goal.schedule_set_id, dict(entry.get("params") or {}))
    return set()


def _acceptance_scope_assignments(
    db: Any, goal: SolveGoal, run: SolverRun
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    """明细核对对象：目标课次 ∪ 本次求解课次 对应的交付行（D4a）。

    合并回填的旧课次既不在求解产物里、也不属于本目标的承诺范围，必须排除；
    反过来，求解可能安置目标集合之外的课次（范围提取副作用），这些行同样要
    受日期/禁排约束。返回 (核对行, 求解集合, 期望集合)。
    """
    solved = _solved_course_business_ids(run)
    expected = _goal_expected_course_ids(db, goal)
    target_ids = solved | expected
    if not target_ids:
        return [], solved, expected
    scoped = [
        a
        for a in _deliverable_assignments(run)
        if str(a.get("course_business_id") or "") in target_ids
    ]
    return scoped, solved, expected


def _check_coverage(
    db: Any,
    goal: SolveGoal,
    run: SolverRun,
    params: dict[str, Any],
    has_schedule: bool,
) -> dict[str, Any]:
    """目标课次集合与结果课次集合逐项比对（MEM-D2/D4b+D4c 口径）。

    期望集合按 coverage 参数（显式课次 id 或范围过滤条件）从当前方案解析；
    同时对照本次求解的**输入选择**（快照 + 请求范围），把缺口拆成两类：
    未进求解范围（范围提取漏课次，可修正范围重跑）与求解未安置（等待教务）。
    另含两道新增防线：
    - 重复检测：同一目标课次在交付课表出现 ≥2 次直接 failed（D4c）；
    - 无法验证≠通过：参数无法解析、课次缺日期/缺时段时判 unverifiable，
      绝不因「没检测到缺口」洗成达标（D4b）。
    """
    requirement = str(params.get("requirement") or "覆盖目标课次")
    item = {"key": "coverage", "kind": "coverage", "requirement": requirement}
    date_from_raw = params.get("date_from")
    date_to_raw = params.get("date_to")
    date_from = _parse_date(date_from_raw)
    date_to = _parse_date(date_to_raw)
    if (date_from_raw and date_from is None) or (date_to_raw and date_to is None):
        return _unverifiable(
            "coverage",
            requirement,
            "coverage",
            "coverage 日期参数无法解析（date_from/date_to 不是合法日期），"
            "无法确定目标课次集合——请修订目标清单",
        )
    expected = _expected_course_ids(db, goal.schedule_set_id, params)
    assignments = _deliverable_assignments(run)
    solved = _solved_course_business_ids(run)
    if not expected:
        # 没有可核对对象 ≠ 通过：空口径的 coverage 会把「漏课次」洗成达标。
        return _unverifiable(
            "coverage",
            requirement,
            "coverage",
            "求解未产出课表，无法核对覆盖"
            if not has_schedule
            else "coverage 项未解析出任何目标课次（范围参数为空或无匹配课次），请修订目标清单",
        )
    # D4c 重复检测：目标课次在交付课表（合并后全集）出现 ≥2 次 = 重复交付。
    occurrence = Counter(
        str(a.get("course_business_id"))
        for a in assignments
        if a.get("course_business_id")
    )
    duplicates = sorted(
        course_id
        for course_id in expected
        if occurrence.get(course_id, 0) >= 2
    )
    input_ids = _run_input_course_ids(db, run, params)
    missing_from_scope = sorted(expected - input_ids)
    missing_from_result = sorted((expected & input_ids) - solved)
    if duplicates:
        return {
            **item,
            "passed": False,
            "detail": (
                f"{len(duplicates)} 个目标课次在交付课表中重复出现（≥2 次）："
                f"{_join(duplicates)}——请核对课次主数据是否重复导入"
            ),
            "evidence": {
                "duplicates": [
                    {"course_business_id": course_id, "count": occurrence[course_id]}
                    for course_id in duplicates
                ],
                "expected_count": len(expected),
            },
        }
    passed = not missing_from_scope and not missing_from_result and has_schedule
    detail_bits = [f"目标课次 {len(expected)} 个，结果命中 {len(expected & solved)} 个"]
    if missing_from_scope:
        detail_bits.append(f"未进入本次求解范围：{_join(missing_from_scope)}")
    if missing_from_result:
        detail_bits.append(f"进入范围但未出现在结果：{_join(missing_from_result)}")
    if not has_schedule and not (missing_from_scope or missing_from_result):
        detail_bits.append("本次求解未产出课表，覆盖无从谈起")
    # D4b 无法验证 ≠ 通过：目标课次排上了但没有日期/时段，覆盖结论不可信。
    placed = expected & solved
    undated = sorted(
        str(a.get("course_business_id"))
        for a in assignments
        if str(a.get("course_business_id") or "") in placed
        and _parse_date(a.get("lesson_date")) is None
    )
    missing_slot = sorted(
        str(a.get("course_business_id"))
        for a in assignments
        if str(a.get("course_business_id") or "") in placed
        and not str(a.get("slot_business_id") or "").strip()
    )
    if (date_from or date_to) and undated:
        return _unverifiable(
            "coverage",
            requirement,
            "coverage",
            f"{len(undated)} 个目标课次缺少上课日期，无法按日期范围核对覆盖：{_join(undated)}",
        )
    if missing_slot:
        return _unverifiable(
            "coverage",
            requirement,
            "coverage",
            f"{len(missing_slot)} 个目标课次没有时段信息，无法确认真实落位：{_join(missing_slot)}",
        )
    return {
        **item,
        "passed": passed,
        "detail": "；".join(detail_bits),
        "evidence": {
            "expected_count": len(expected),
            "missing_from_scope": missing_from_scope,
            "missing_from_result": missing_from_result,
            "duplicates": [],
        },
    }


def _expected_course_ids(
    db: Any, schedule_set_id: str, params: dict[str, Any]
) -> set[str]:
    explicit = {
        str(v)
        for v in params.get("course_business_ids") or []
        if str(v).strip()
    }
    lines = {str(v) for v in params.get("business_lines") or [] if str(v).strip()}
    types = {str(v) for v in params.get("product_types") or [] if str(v).strip()}
    classes = {str(v) for v in params.get("class_business_ids") or [] if str(v).strip()}
    if explicit or not (lines or types or classes):
        return explicit
    rows = db.scalars(
        select(CourseSession).where(
            CourseSession.schedule_set_id == schedule_set_id,
            CourseSession.is_active.is_(True),
        )
    ).all()
    date_from = _parse_date(params.get("date_from"))
    date_to = _parse_date(params.get("date_to"))
    expected: set[str] = set()
    for row in rows:
        row_types = {str(v) for v in row.product_types or []}
        if str(row.product_type or ""):
            row_types.add(str(row.product_type))
        if lines and str(row.business_line or "") not in lines:
            continue
        if types and not row_types & types:
            continue
        if classes and str(row.class_business_id or "") not in classes:
            continue
        # 日期窗按课次自身日期过滤（与求解范围提取同口径）；日期不明的课次保留，
        # 宁可多验不可漏验。
        if (date_from or date_to) and row.lesson_date:
            if date_from and row.lesson_date < date_from:
                continue
            if date_to and row.lesson_date > date_to:
                continue
        expected.add(str(row.business_id))
    return expected


def _run_input_course_ids(db: Any, run: SolverRun, params: dict[str, Any]) -> set[str]:
    """本次求解实际选入的课次（快照 + 请求范围），用于定位「范围提取漏课次」。"""
    del params  # 输入侧只看 run 的真实范围；goal 与 run 的范围差异落在 missing_from_scope。
    snapshot = db.get(DataSnapshot, run.snapshot_id) if run.snapshot_id else None
    snapshot_payload = dict(snapshot.payload or {}) if snapshot is not None else {}
    merged = {**snapshot_payload, **dict(run.request_payload or {})}
    # coverage 参数比求解请求更宽时（例如 goal 圈了两个业务线、run 只排了一个），
    # 输入侧仍以 run 的真实范围为准——这里不二次过滤，差异会落在 missing_from_scope。
    return {
        str(item.get("business_id"))
        for item in _selected_sessions(merged)
        if item.get("business_id")
    }


def _missing_subject_ids(
    db: Any, schedule_set_id: str, subject_type: str, subject_ids: set[str]
) -> set[str]:
    """按主体类型核对主数据存在性（MEM-D2/D4d），返回不存在的主体 id。"""
    if not subject_ids:
        return set()
    model_by_type: dict[str, Any] = {
        "teacher": Teacher,
        "classroom": Room,
        "cohort": ClassGroup,
    }
    model = model_by_type.get(subject_type)
    if model is not None:
        known = set(
            db.scalars(
                select(model.business_id).where(
                    model.schedule_set_id == schedule_set_id,
                    model.business_id.in_(subject_ids),
                )
            )
        )
        return subject_ids - known
    if subject_type == "course":
        known = set(
            db.scalars(
                select(CourseSession.business_id).where(
                    CourseSession.schedule_set_id == schedule_set_id,
                    CourseSession.business_id.in_(subject_ids),
                )
            )
        )
        return subject_ids - known
    return subject_ids  # 未知主体类型 = 全部无法核对


def _missing_slot_ids(
    db: Any, schedule_set_id: str, slot_ids: set[str]
) -> set[str]:
    """核对时段存在性（MEM-D2/D4d）：方案主数据里没有的时段无法复核。"""
    if not slot_ids:
        return set()
    known = set(
        db.scalars(
            select(TimeSlot.business_id).where(
                TimeSlot.schedule_set_id == schedule_set_id,
                TimeSlot.business_id.in_(slot_ids),
            )
        )
    )
    return slot_ids - known


def _check_forbidden_slots(
    db: Any,
    goal: SolveGoal,
    run: SolverRun,
    params: dict[str, Any],
    has_schedule: bool,
) -> dict[str, Any]:
    """独立检查禁排时段是否仍被占用——不信任求解器的「已满足」。

    MEM-D2/D4d：执行前先验证 subject/slot 在当前方案主数据中存在；指向不存在
    的对象时该项 unverifiable（「禁排对象不存在，请补齐参数」），绝不当成
    「禁排已满足」。MEM-D2/D4a：只核对 目标课次 ∪ 本次求解课次——合并回填
    的旧课次不是本目标的验收对象（保留上周原样不算违规）。
    """
    requirement = str(params.get("requirement") or "禁排时段不被占用")
    item = {"key": "forbidden_slot_free", "kind": "forbidden_slot_free", "requirement": requirement}
    slots = {str(v) for v in params.get("slot_business_ids") or [] if str(v).strip()}
    subject_ids = {str(v) for v in params.get("subject_ids") or [] if str(v).strip()}
    if params.get("needs_params") or not slots or not subject_ids:
        return _unverifiable(
            "forbidden_slot_free",
            requirement,
            "forbidden_slot_free",
            "禁排参数未量化（缺主体或时段），无法独立复核——请修订目标清单",
        )
    subject_type = str(params.get("subject_type") or "teacher")
    unknown_subjects = sorted(
        _missing_subject_ids(db, goal.schedule_set_id, subject_type, subject_ids)
    )
    unknown_slots = sorted(_missing_slot_ids(db, goal.schedule_set_id, slots))
    if unknown_subjects or unknown_slots:
        missing_bits = []
        if unknown_subjects:
            label = _SUBJECT_TYPE_LABELS.get(subject_type, subject_type)
            missing_bits.append(f"{label} {_join(unknown_subjects)}")
        if unknown_slots:
            missing_bits.append(f"时段 {_join(unknown_slots)}")
        return _unverifiable(
            "forbidden_slot_free",
            requirement,
            "forbidden_slot_free",
            "禁排对象不存在，请补齐参数：当前方案主数据中找不到 " + "；".join(missing_bits),
        )
    if not has_schedule:
        return _unverifiable(
            "forbidden_slot_free",
            requirement,
            "forbidden_slot_free",
            "本次求解未产出课表，无法复核禁排时段",
        )
    assignments, _solved, _expected = _acceptance_scope_assignments(db, goal, run)
    course_ids = {
        str(a.get("course_session_id")) for a in assignments if a.get("course_session_id")
    }
    courses: dict[str, CourseSession] = {}
    if course_ids:
        courses = {
            row.id: row
            for row in db.scalars(
                select(CourseSession).where(CourseSession.id.in_(course_ids))
            ).all()
        }
    violations: list[dict[str, Any]] = []
    for assignment in assignments:
        course = courses.get(str(assignment.get("course_session_id") or ""))
        if str(assignment.get("slot_business_id")) not in slots:
            continue
        subjects = _course_subjects(course, subject_type) if course is not None else set()
        if subject_type == "classroom":
            subjects = {str(assignment.get("room_business_id") or "")} - {""}
        if subjects & subject_ids:
            violations.append(
                {
                    "course_business_id": assignment.get("course_business_id"),
                    "lesson_date": str(assignment.get("lesson_date") or ""),
                    "slot_business_id": assignment.get("slot_business_id"),
                    "room_business_id": assignment.get("room_business_id"),
                }
            )
    violation_courses = sorted({str(v["course_business_id"]) for v in violations})
    return {
        **item,
        "passed": not violations,
        "detail": (
            f"禁排时段仍被占用 {len(violations)} 处：{_join(violation_courses)}"
            if violations
            else "指定时段未发现对应课程（独立复核通过）"
        ),
        "evidence": {"violations": violations[:20]},
    }


def _check_no_hard_conflicts(
    db: Any, goal: SolveGoal, run: SolverRun, params: dict[str, Any], has_schedule: bool
) -> dict[str, Any]:
    """硬冲突独立重算（底线项）。口径 = 合并交付课表（D4a）：全局资源冲突
    本来就该看全集——教室/教师/日程冲突不分「本次重排」还是「保留原样」。"""
    requirement = str(params.get("requirement") or "无硬冲突")
    item = {"key": "no_hard_conflicts", "kind": "no_hard_conflicts", "requirement": requirement}
    if not has_schedule:
        return _unverifiable(
            "no_hard_conflicts",
            requirement,
            "no_hard_conflicts",
            "本次求解未产出课表，无法复核硬冲突",
        )
    assignments = _deliverable_assignments(run)
    conflicts = count_hard_conflicts(db, assignments, goal.schedule_set_id)
    nonzero = "、".join(
        f"{key} {value}" for key, value in conflicts.items() if key != "total" and value
    )
    return {
        **item,
        "passed": conflicts["total"] == 0,
        "detail": (
            "独立重算硬冲突 0 条"
            if conflicts["total"] == 0
            else f"独立重算硬冲突 {conflicts['total']} 条（{nonzero}）"
        ),
        "evidence": dict(conflicts),
    }


def _check_max_changes(
    db: Any, goal: SolveGoal, run: SolverRun, params: dict[str, Any], has_schedule: bool
) -> dict[str, Any]:
    requirement = str(params.get("requirement") or "变更数不超过上限")
    item = {"key": "max_changes", "kind": "max_changes", "requirement": requirement}
    limit = params.get("max_changes")
    baseline_id = str(params.get("baseline_schedule_version_id") or "")
    if limit is None or not baseline_id:
        return _unverifiable(
            "max_changes",
            requirement,
            "max_changes",
            "未提供上限或基准版本，无法核算变更数——请修订目标清单",
        )
    if not has_schedule:
        return _unverifiable(
            "max_changes",
            requirement,
            "max_changes",
            "本次求解未产出课表，无法核算变更数",
        )
    baseline = db.get(ScheduleVersion, baseline_id)
    if baseline is None or baseline.schedule_set_id != goal.schedule_set_id:
        return {
            **item,
            "passed": False,
            "detail": "基准版本不存在或不属于当前方案",
            "evidence": None,
        }
    baseline_rows = list(
        db.scalars(
            select(ScheduleAssignment).where(
                ScheduleAssignment.schedule_version_id == baseline_id
            )
        )
    )
    baseline_map = {
        row.course_session_id: (
            row.lesson_date.isoformat() if row.lesson_date else None,
            row.slot_business_id,
            row.room_business_id,
        )
        for row in baseline_rows
    }
    assignments = [
        dict(a)
        for a in (run.result_payload or {}).get("assignments") or []
        if isinstance(a, dict)
    ]

    def _assignment_key(a: dict[str, Any]) -> tuple[str | None, str, str]:
        lesson_date = _parse_date(a.get("lesson_date"))
        return (
            lesson_date.isoformat() if lesson_date else None,
            str(a.get("slot_business_id") or ""),
            str(a.get("room_business_id") or ""),
        )

    result_ids = {str(a.get("course_session_id")) for a in assignments}
    moved = [
        str(a.get("course_business_id"))
        for a in assignments
        if a.get("course_session_id") in baseline_map
        and baseline_map[str(a.get("course_session_id"))] != _assignment_key(a)
    ]
    added = [
        str(a.get("course_business_id"))
        for a in assignments
        if a.get("course_session_id") not in baseline_map
    ]
    removed = [
        str(row.course_session_id)
        for row in baseline_rows
        if row.course_session_id not in result_ids
    ]
    changed_count = len(moved) + len(added) + len(removed)
    return {
        **item,
        "passed": changed_count <= int(limit),
        "detail": (
            f"相对基准 v{baseline.version_no} 变更 {changed_count} 处（上限 {int(limit)}）"
            + ("" if changed_count <= int(limit) else f"——超出 {changed_count - int(limit)} 处")
            + "。优化类目标：仅设验收上限，不改变硬约束语义"
        ),
        "evidence": {
            "baseline_schedule_version_id": baseline_id,
            "moved": sorted(moved)[:20],
            "added": sorted(added)[:20],
            "removed": sorted(removed)[:20],
            "changed_count": changed_count,
            "max_changes": int(limit),
        },
    }


def _check_draft_only(
    db: Any, goal: SolveGoal, run: SolverRun, params: dict[str, Any], has_schedule: bool
) -> dict[str, Any]:
    """验证未发生发布/日历下发/外部分发：查审计日志里目标期间的相关动作。

    口径是 goal.created_at 之后（覆盖「该 run 之后」）：一旦目标期间发生过
    发布/下发，后补一次干净的求解不能把违规洗掉——「不发布」不得降级为尽量。
    """
    del params, has_schedule
    requirement = "不发生发布或日历下发"
    schedule_ids = set(
        db.scalars(
            select(ScheduleVersion.id).where(
                ScheduleVersion.schedule_set_id == goal.schedule_set_id
            )
        )
    )
    leaks: list[AuditLog] = []
    if schedule_ids:
        leaks = list(
            db.scalars(
                select(AuditLog)
                .where(
                    AuditLog.action.in_(PUBLISH_AUDIT_ACTIONS),
                    AuditLog.resource_type == "schedule",
                    AuditLog.resource_id.in_(schedule_ids),
                    AuditLog.created_at >= goal.created_at,
                )
                .order_by(AuditLog.created_at)
            )
        )
    leak_actions = sorted({row.action for row in leaks})
    return {
        "key": "draft_only",
        "kind": "draft_only",
        "requirement": requirement,
        "passed": not leaks,
        "detail": (
            f"目标期间发生了 {len(leaks)} 次发布/下发动作（{'、'.join(leak_actions)}），"
            "已不符合「只出草稿」——发布必须由有权限的人显式执行，目标不能算自动完成"
            if leaks
            else "目标期间无发布/日历下发动作，草稿交付合规"
        ),
        "evidence": {
            "actions": [
                {
                    "action": row.action,
                    "resource_id": row.resource_id,
                    "created_at": row.created_at.isoformat(),
                }
                for row in leaks[:10]
            ]
        },
    }


def _check_deliverable_exists(
    db: Any, goal: SolveGoal, run: SolverRun, params: dict[str, Any], has_schedule: bool
) -> dict[str, Any]:
    """底线验收（MEM-D2/D4c）：run 必须存在非空课表产物（assignment 数 > 0）。

    仅 draft_only 之类的自定义清单在「零课表 + 无违规」上也能全过——没有交付物
    的目标不得判 achieved，这条底线把「求解跑完」和「真的排出了课」分开。
    """
    del db, goal, params
    requirement = "存在非空课表产物"
    assignments = _deliverable_assignments(run)
    passed = has_schedule and bool(assignments)
    return {
        "key": "deliverable_exists",
        "kind": "deliverable_exists",
        "requirement": requirement,
        "passed": passed,
        "detail": (
            f"课表产物 {len(assignments)} 条（独立核对 result 载荷，不信任自报）"
            if passed
            else "本次求解未产出任何课表产物（assignment 数为 0），目标不能算达成"
        ),
        "evidence": {"assignment_count": len(assignments)},
    }


def _check_date_range(
    db: Any, goal: SolveGoal, run: SolverRun, params: dict[str, Any], has_schedule: bool
) -> dict[str, Any]:
    """范围端点核对：结果日期必须落在请求范围内（防范围提取漏课次的另一道网）。

    MEM-D2/D4a：只核对 目标课次 ∪ 本次求解课次。合并交付课表里保留了旧课次
    （局部重排一周时，其余三周的课次原样并入），它们不是本次请求的承诺对象，
    混进来会把「保留原样」误判成越界。MEM-D2/D4b：日期参数无法解析、核对对象
    缺日期时判 unverifiable——不能因「没检测到越界」放行。
    """
    requirement = str(params.get("requirement") or "结果日期在请求范围内")
    item = {"key": "date_range_match", "kind": "date_range_match", "requirement": requirement}
    date_from_raw = params.get("date_from")
    date_to_raw = params.get("date_to")
    date_from = _parse_date(date_from_raw)
    date_to = _parse_date(date_to_raw)
    if (date_from_raw and date_from is None) or (date_to_raw and date_to is None):
        return _unverifiable(
            "date_range_match",
            requirement,
            "date_range_match",
            "日期范围参数无法解析（date_from/date_to 不是合法日期），无法核对——请修订目标清单",
        )
    if not date_from and not date_to:
        return _unverifiable(
            "date_range_match",
            requirement,
            "date_range_match",
            "未提供日期范围参数，无法核对——请修订目标清单",
        )
    if not has_schedule:
        return _unverifiable(
            "date_range_match",
            requirement,
            "date_range_match",
            "本次求解未产出课表，无法核对日期范围",
        )
    assignments, _solved, _expected = _acceptance_scope_assignments(db, goal, run)
    if not assignments:
        return _unverifiable(
            "date_range_match",
            requirement,
            "date_range_match",
            "交付课表中定位不到目标课次或本次求解课次，无法核对日期范围",
        )
    undated: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    for assignment in assignments:
        lesson_date = _parse_date(assignment.get("lesson_date"))
        if lesson_date is None:
            undated.append(
                {
                    "course_business_id": assignment.get("course_business_id"),
                }
            )
            continue
        if (date_from and lesson_date < date_from) or (date_to and lesson_date > date_to):
            outside.append(
                {
                    "course_business_id": assignment.get("course_business_id"),
                    "lesson_date": lesson_date.isoformat(),
                }
            )
    if undated:
        undated_courses = sorted({str(v["course_business_id"]) for v in undated})
        return _unverifiable(
            "date_range_match",
            requirement,
            "date_range_match",
            f"{len(undated)} 个核对课次缺少上课日期，无法核对日期范围：{_join(undated_courses)}",
        )
    low_bound = date_from.isoformat() if date_from else "不限"
    high_bound = date_to.isoformat() if date_to else "不限"
    bounds = f"{low_bound} ~ {high_bound}"
    outside_courses = sorted({str(v["course_business_id"]) for v in outside})
    return {
        **item,
        "passed": not outside,
        "detail": (
            f"有 {len(outside)} 个课次落在请求范围（{bounds}）之外：{_join(outside_courses)}"
            if outside
            else f"全部核对课次的日期都在请求范围（{bounds}）内"
        ),
        "evidence": {
            "outside": outside[:20],
            "undated_count": 0,
            "checked_count": len(assignments),
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
        },
    }


_CHECKERS = {
    "deliverable_exists": _check_deliverable_exists,
    "coverage": _check_coverage,
    "forbidden_slot_free": _check_forbidden_slots,
    "no_hard_conflicts": _check_no_hard_conflicts,
    "max_changes": _check_max_changes,
    "draft_only": _check_draft_only,
    "date_range_match": _check_date_range,
}


# ---------------------------------------------------------------- 决策与回灌

# 允许的补救动作分类 → 状态机映射。await_admin 类缺口必须由教务放宽或裁决，
# 对应 awaiting_decision；其余（改范围重跑 / 加大预算 / 修订清单）保持 open。
_AWAIT_ADMIN_KINDS = {"no_hard_conflicts"}
_REMEDIABLE_KINDS = {"coverage", "date_range_match", "max_changes"}


def _decide(
    items: list[dict[str, Any]],
    has_schedule: bool,
    run: SolverRun | None = None,
) -> dict[str, Any]:
    """按缺口给出决策建议（MEM-D2/D6）：解释文案按求解状态三态分开。

    - UNKNOWN（含超时）：不是「约束放不下」——尚未找到可行解也未证明无解，
      是否继续由求解预算与诊断决定，目标保持 open；
    - INFEASIBLE：当前模型已证明无解（presolve 预检不算——CP-SAT 未运行），
      进 awaiting_decision 的规则/数据调整流程；
    - 有可行解但有缺口：按缺口逐项处理（原有 await_admin / 补救动作逻辑）。
    """
    model_status = str(getattr(run, "model_status", "") or "") if run is not None else ""
    presolve = bool((getattr(run, "result_payload", None) or {}).get("presolve_infeasible"))
    unknown = model_status == "UNKNOWN"
    infeasible = model_status == "INFEASIBLE"
    failed = [entry for entry in items if not entry["passed"]]
    if not failed:
        return {
            "status": "achieved",
            "reason": "全部验收项通过：目标在合格交付物上达成（发布永远不在目标自动动作里）",
            "gaps": [],
        }
    gaps: list[dict[str, Any]] = []
    awaiting = False
    for entry in failed:
        kind = entry["kind"]
        evidence = entry.get("evidence") or {}
        unverifiable = entry.get("verdict") == "unverifiable"
        if kind == "coverage":
            missing_scope = list(evidence.get("missing_from_scope") or [])
            missing_result = list(evidence.get("missing_from_result") or [])
            duplicates = list(evidence.get("duplicates") or [])
            if duplicates:
                gaps.append(
                    {
                        "key": entry["key"],
                        "kind": kind,
                        "summary": f"目标课次在交付课表中重复出现：{len(duplicates)} 个",
                        "next_step": "核对课次主数据是否重复导入（同一课次出现 ≥2 次），修正后重跑",
                        "remedy": "await_admin",
                    }
                )
            elif missing_scope:
                gaps.append(
                    {
                        "key": entry["key"],
                        "kind": kind,
                        "summary": f"范围提取漏课次：{'、'.join(missing_scope[:8])}",
                        "next_step": "修正课程范围后再次求解（缺失课次未进入本次求解范围）",
                        "remedy": "resolve_scope",
                    }
                )
            elif missing_result:
                gaps.append(
                    {
                        "key": entry["key"],
                        "kind": kind,
                        "summary": f"求解未能安置：{'、'.join(missing_result[:8])}",
                        "next_step": (
                            "求解未得出结论（UNKNOWN）：先看诊断，由预算与诊断决定是否继续"
                            if unknown
                            else "等待教务放宽规则或调整（当前约束放不下这些课次）"
                        ),
                        "remedy": "raise_budget" if unknown else "await_admin",
                    }
                )
            else:
                gaps.append(
                    {
                        "key": entry["key"],
                        "kind": kind,
                        "summary": entry["detail"],
                        "next_step": "本次求解未产出课表：先看诊断，再决定放宽规则或修正范围",
                        "remedy": "await_admin" if not has_schedule else "resolve_scope",
                    }
                )
            if missing_result and not unknown:
                awaiting = True
            if duplicates:
                awaiting = True
        elif kind == "deliverable_exists":
            gaps.append(
                {
                    "key": entry["key"],
                    "kind": kind,
                    "summary": entry["detail"],
                    "next_step": (
                        "求解未得出结论（UNKNOWN）：先看诊断，由预算与诊断决定是否继续"
                        if unknown
                        else "本次求解未产出课表：先看求解诊断，再决定加预算、修范围或调整规则"
                    ),
                    "remedy": "raise_budget" if unknown else "resolve_scope",
                }
            )
        elif kind in _AWAIT_ADMIN_KINDS:
            gaps.append(
                {
                    "key": entry["key"],
                    "kind": kind,
                    "summary": entry["detail"],
                    "next_step": "等待教务放宽或调整（硬冲突必须消除后才能交付）",
                    "remedy": "await_admin",
                }
            )
            awaiting = True
        elif kind == "draft_only":
            gaps.append(
                {
                    "key": entry["key"],
                    "kind": kind,
                    "summary": entry["detail"],
                    "next_step": "目标期间发生了发布/下发：请人工确认目标是否仍然有效",
                    "remedy": "await_admin",
                }
            )
            awaiting = True
        elif kind == "forbidden_slot_free":
            unquantified = "未量化" in entry["detail"] or "不存在" in entry["detail"]
            gaps.append(
                {
                    "key": entry["key"],
                    "kind": kind,
                    "summary": entry["detail"],
                    "next_step": (
                        "修订目标清单，补充禁排主体与具体时段"
                        if unquantified
                        else "禁排时段仍被占用：等待教务放宽或调整排课"
                    ),
                    "remedy": "fix_checklist" if unquantified else "await_admin",
                }
            )
            if not unquantified:
                awaiting = True
        else:
            # max_changes / date_range_match：存在允许的补救动作（加预算 / 修范围重跑）。
            next_step = {
                "max_changes": (
                    "优化类目标未达上限：可加大时间预算或调高变更权重后重跑"
                    "（「尽量」不升级为「绝不」）"
                ),
                "date_range_match": "结果日期越界：核对解析出的日期范围后重跑",
            }.get(kind, entry["detail"])
            remedy = "raise_budget" if kind == "max_changes" else "resolve_scope"
            gaps.append(
                {
                    "key": entry["key"],
                    "kind": kind,
                    "summary": entry["detail"],
                    "next_step": next_step,
                    "remedy": remedy,
                }
            )
        # 无法验证 ≠ 通过：这类缺口允许的下一步是补参数/补数据，不是放行。
        if unverifiable and kind not in {"coverage", "forbidden_slot_free", "deliverable_exists"}:
            gaps[-1]["next_step"] = f"{gaps[-1]['next_step']}（该项当前无法验证，需先补齐核对条件）"
    if unknown:
        # D6：UNKNOWN 不进「约束放不下」的 awaiting_decision 叙事——目标保持 open，
        # 由预算与诊断决定是否继续。
        return {
            "status": "open",
            "reason": (
                f"求解未得出结论（UNKNOWN，含超时）：{len(failed)} 项无法核对。"
                "是否继续由求解预算与诊断决定——可加大时限或缩小范围后重跑，目标保持 open"
            ),
            "gaps": gaps,
        }
    if infeasible:
        if presolve:
            return {
                "status": "open",
                "reason": (
                    f"求解前预检判定不可行（CP-SAT 未运行，不能表述为「已证明无解」）："
                    f"{len(failed)} 项无法核对。请核对课次范围与输入数据后重跑"
                ),
                "gaps": gaps,
            }
        return {
            "status": "awaiting_decision",
            "reason": (
                f"当前模型已证明无解：{len(failed)} 项未通过。"
                "需要教务调整规则或数据后重排（进入规则/数据调整流程）"
            ),
            "gaps": gaps,
        }
    return {
        "status": "awaiting_decision" if awaiting else "open",
        "reason": (
            f"{len(failed)} 项未通过；有可行解但未达标，按缺口逐项处理"
            if awaiting
            else f"{len(failed)} 项未通过；存在允许的补救动作，按缺口逐项处理后可再次求解"
        ),
        "gaps": gaps,
    }


def evaluate_goal(db: Any, goal: SolveGoal, run: SolverRun) -> dict[str, Any]:
    """对一次 completed 的 run 出具验收报告。纯代码，绝不触发新的求解。"""
    result = dict(run.result_payload or {})
    assignments = [a for a in result.get("assignments") or [] if isinstance(a, dict)]
    has_schedule = run.model_status in {"OPTIMAL", "FEASIBLE"} and bool(assignments)
    items: list[dict[str, Any]] = []
    for entry in goal.checklist or []:
        kind = str(entry.get("kind") or "")
        checker = _CHECKERS.get(kind)
        if checker is None:
            items.append(
                _unverifiable(
                    str(entry.get("key") or kind),
                    str(entry.get("requirement") or ""),
                    kind or "unknown",
                    "未知验收类型，无法执行（清单可能与当前版本不兼容）",
                )
            )
            continue
        item = checker(db, goal, run, dict(entry.get("params") or {}), has_schedule)
        item["key"] = str(entry.get("key") or kind)
        item["requirement"] = str(entry.get("requirement") or "")
        # verdict 归一（MEM-D2/D4b）：unverifiable 由验收器显式标注，其余按
        # passed 归为 passed/failed；底线徽标随清单 params 透传给前端。
        item["verdict"] = str(item.get("verdict") or ("passed" if item["passed"] else "failed"))
        item["bottom_line"] = bool((entry.get("params") or {}).get("bottom_line"))
        items.append(item)
    decision = _decide(items, has_schedule, run)
    passed_count = sum(1 for entry in items if entry["passed"])
    unverifiable_count = sum(1 for entry in items if entry["verdict"] == "unverifiable")
    return {
        "goal_id": goal.id,
        "instruction": goal.instruction,
        "run_id": run.id,
        "run_status": run.status,
        "model_status": run.model_status,
        "has_schedule": has_schedule,
        # 无法验证 ≠ 通过：all_passed 要求所有项 passed=True 且无 unverifiable。
        "all_passed": passed_count == len(items) and bool(items) and unverifiable_count == 0,
        "passed_count": passed_count,
        "failed_count": len(items) - passed_count,
        "unverifiable_count": unverifiable_count,
        "items": items,
        "gaps": decision["gaps"],
        "decision": {"status": decision["status"], "reason": decision["reason"]},
        "evaluated_at": shanghai_now().isoformat(),
    }


def apply_goal_evaluation(db: Any, run: SolverRun) -> dict[str, Any] | None:
    """run 到达 completed 后回灌：报告落 run.goal_report，目标状态随最新验收走。

    abandoned 是人工终态，验收器不越权改动；其余状态一律反映最近一次验收
    （含 achieved 回退——后一次跑砸了就该让人看见）。评估异常由调用方兜底
    （tasks._evaluate_goal_for_run：置 acceptance_status=failed 并落 detail），
    绝不影响求解结果本身的落库。验收成功 → acceptance_status=completed。
    """
    if not run.goal_id:
        return None
    goal = db.get(SolveGoal, run.goal_id)
    if goal is None or goal.status == "abandoned":
        return None
    report = evaluate_goal(db, goal, run)
    run.goal_report = report
    goal.latest_run_id = run.id
    goal.status = str(report["decision"]["status"])
    goal.acceptance_status = "completed"
    goal.acceptance_detail = None
    return report


def goal_run_counts(db: Any, schedule_set_id: str) -> dict[str, int]:
    """目标列表用：一次分组查询拿到每个目标的关联 run 数。"""
    rows = db.execute(
        select(SolverRun.goal_id, func.count(SolverRun.id))
        .where(
            SolverRun.schedule_set_id == schedule_set_id,
            SolverRun.goal_id.is_not(None),
        )
        .group_by(SolverRun.goal_id)
    ).all()
    return {str(goal_id): int(count) for goal_id, count in rows}


__all__ = [
    "BOTTOM_LINE_KINDS",
    "GOAL_ACCEPTANCE_STATUSES",
    "GOAL_CHECKLIST_KINDS",
    "GOAL_STATUSES",
    "PUBLISH_AUDIT_ACTIONS",
    "apply_goal_evaluation",
    "build_checklist",
    "draft_checklist_from_interpretation",
    "ensure_bottom_line_items",
    "evaluate_goal",
    "goal_run_counts",
]
