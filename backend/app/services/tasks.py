from __future__ import annotations

import logging
from concurrent.futures import Future, ProcessPoolExecutor
from datetime import date
from typing import Any

from sqlalchemy import func, select, update

from ..config import get_settings
from ..db import SessionLocal
from ..models import (
    DEFAULT_SCHEDULE_SET_ID,
    CourseSession,
    DataSnapshot,
    RescheduleEvent,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolveGoal,
    SolverRun,
    Teacher,
    TimeSlot,
)

# 偏好记忆已在创建任务时冻结进快照，执行路径只读 snapshot.payload["memory"]。
# (见 _merge_frozen_extras；本模块不再现场编译偏好。)
from ..timezone import shanghai_now
from .solver import solve_problem

settings = get_settings()
logger = logging.getLogger("tupai.memory")
_executor: ProcessPoolExecutor | None = None


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


def _get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=settings.solver_workers)
    return _executor


def _clock_minutes(value: str) -> int:
    hours, minutes = (int(part) for part in value.split(":", 1))
    return hours * 60 + minutes


def _occupancy_window(course: Any, item: dict[str, Any], slots: dict[str, Any]) -> tuple[Any, ...]:
    """课次实际占用的时间区间。

    日期感知课表用「日期 + 固定起止时刻」，经典时段课表退化为时段标识本身。
    """
    lesson_date = item.get("lesson_date")
    slot = slots.get(str(item.get("slot_business_id") or ""))
    start = str(
        (slot.start_time if slot else None)
        or item.get("start_time")
        or getattr(course, "fixed_start_time", "")
        or ""
    )
    end = str(
        (slot.end_time if slot else None)
        or item.get("end_time")
        or getattr(course, "fixed_end_time", "")
        or ""
    )
    if lesson_date and start and end:
        return ("clock", str(lesson_date), _clock_minutes(start), _clock_minutes(end))
    return ("slot", str(lesson_date or ""), str(item["slot_business_id"]))


def _windows_overlap(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    if left[0] != right[0] or left[1] != right[1]:
        return False
    if left[0] == "slot":
        return left[2] == right[2]
    return left[2] < right[3] and right[2] < left[3]


def count_hard_conflicts(
    db: Any,
    assignments: list[dict[str, Any]],
    schedule_set_id: str = DEFAULT_SCHEDULE_SET_ID,
    *,
    course_overrides: dict[str, CourseSession] | None = None,
) -> dict[str, int]:
    """按维度实算硬冲突记录数。

    此前该指标是写死的 0，等于用一个恒真值去证明正确性。这里独立于求解器重算一遍，
    既是发布门禁，也是求解器建模出错时的兜底。
    """
    courses = {
        item.id: item
        for item in db.scalars(
            select(CourseSession).where(CourseSession.schedule_set_id == schedule_set_id)
        ).all()
    }
    if course_overrides is not None:
        courses = {key: course_overrides.get(key, value) for key, value in courses.items()}
    teachers = {
        item.business_id: item
        for item in db.scalars(
            select(Teacher).where(Teacher.schedule_set_id == schedule_set_id)
        ).all()
    }
    slots = {
        item.business_id: item
        for item in db.scalars(
            select(TimeSlot).where(TimeSlot.schedule_set_id == schedule_set_id)
        ).all()
    }
    entries: list[tuple[dict[str, Any], Any, tuple[Any, ...]]] = []
    rooms = set(db.scalars(select(Room.business_id).where(
        Room.schedule_set_id == schedule_set_id
    )))
    integrity_errors = 0
    for item in assignments:
        course = courses.get(item.get("course_session_id"))
        if (course is None or item.get("room_business_id") not in rooms
                or item.get("slot_business_id") not in slots):
            integrity_errors += 1
            continue
        entries.append((item, course, _occupancy_window(course, item, slots)))

    def dimension_keys(item: dict[str, Any], course: Any, name: str) -> list[str]:
        if name == "room":
            value = str(item.get("room_business_id") or "")
            return [value] if value else []
        if name == "class":
            product_types = list(getattr(course, "product_types", None) or [])
            primary = str(getattr(course, "product_type", "") or "")
            if primary and primary not in product_types:
                product_types.append(primary)
            return [
                "\x00".join(
                    (
                        str(getattr(course, "business_line", "") or ""),
                        str(product_type),
                        str(item.get("class_business_id") or ""),
                    )
                )
                for product_type in sorted(set(product_types or [""]))
            ]
        teacher_ids = list(getattr(course, "teacher_business_ids", None) or [])
        primary_teacher = str(item.get("teacher_business_id") or "")
        if primary_teacher and primary_teacher not in teacher_ids:
            teacher_ids.append(primary_teacher)
        if name == "teacher":
            return [
                teacher_id
                for teacher_id in teacher_ids
                if teacher_id
                and not bool(teachers.get(teacher_id) and teachers[teacher_id].is_group)
            ]
        explicit = str(getattr(course, "calendar_user_id", None) or "").strip()
        if explicit:
            return [explicit]
        return sorted(
            {
                str(teachers[teacher_id].calendar_user_id or "").strip()
                for teacher_id in teacher_ids
                if teacher_id in teachers and teachers[teacher_id].calendar_user_id
            }
        )

    breakdown: dict[str, int] = {}
    for name in ("room", "class", "teacher", "calendar"):
        grouped: dict[str, list[tuple[dict[str, Any], tuple[Any, ...]]]] = {}
        for item, course, window in entries:
            for key in dimension_keys(item, course, name):
                grouped.setdefault(key, []).append((item, window))
        involved: set[str] = set()
        for rows in grouped.values():
            for index, (left_item, left_window) in enumerate(rows):
                for right_item, right_window in rows[index + 1 :]:
                    if _windows_overlap(left_window, right_window):
                        involved.add(str(left_item["course_session_id"]))
                        involved.add(str(right_item["course_session_id"]))
        breakdown[name] = len(involved)
    breakdown["integrity"] = integrity_errors
    breakdown["total"] = sum(breakdown.values())
    return breakdown


def _room_slot_capacity(
    rooms: dict[str, Any], open_slots: list[Any], occupied: set[tuple[str, str | None, str]]
) -> int:
    """可用的「教室 × 时间格」总数。

    分母必须和分子用同一种格子定义，否则占用率会算出 200% 这种没有意义的数。
    经典时段课表的一个格子就是一个时段标识；日期感知课表的一个格子是
    「日期 + 固定起止时刻」，同一时刻落在不同星期上共用一个时钟窗口，
    所以只有后者才按起止时刻去重。两种课次混在一份课表里时分别算再相加。
    """
    active_rooms = sum(1 for room in rooms.values() if room.is_active)
    if not active_rooms:
        return 0
    scheduled_dates = {lesson_date for _room, lesson_date, _slot in occupied if lesson_date}
    capacity = 0
    if any(lesson_date is None for _room, lesson_date, _slot in occupied):
        capacity += active_rooms * len({slot.business_id for slot in open_slots})
    if scheduled_dates:
        capacity += (
            active_rooms
            * len(scheduled_dates)
            * len({(slot.start_time, slot.end_time) for slot in open_slots})
        )
    return capacity


def calculate_metrics(
    db: Any,
    assignments: list[dict[str, Any]],
    schedule_set_id: str = DEFAULT_SCHEDULE_SET_ID,
) -> dict[str, Any]:
    rooms = {
        item.business_id: item
        for item in db.scalars(
            select(Room).where(Room.schedule_set_id == schedule_set_id)
        ).all()
    }
    open_slots = list(
        db.scalars(
            select(TimeSlot).where(
                TimeSlot.schedule_set_id == schedule_set_id,
                TimeSlot.is_open.is_(True),
            )
        )
    )
    room_slots: set[tuple[str, str | None, str]] = set()
    unknown_rooms: set[str] = set()
    for item in assignments:
        room = rooms.get(item["room_business_id"])
        if room is None:
            unknown_rooms.add(str(item["room_business_id"]))
            continue
        room_slots.add((room.business_id, item.get("lesson_date"), item["slot_business_id"]))
    available_room_slots = _room_slot_capacity(rooms, open_slots, room_slots)
    conflicts = count_hard_conflicts(db, assignments, schedule_set_id)
    return {
        "assignment_count": len(assignments),
        "hard_conflicts": conflicts["total"],
        "hard_conflicts_by_dimension": {
            key: value for key, value in conflicts.items() if key != "total"
        },
        "unassigned_rooms": len(unknown_rooms),
        "room_slot_occupancy": round(len(room_slots) / available_room_slots, 4)
        if available_room_slots
        else 0,
    }


def _persist_result(run_id: str, result: dict[str, Any]) -> None:
    run_goal_id: str | None = None
    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        if run is None:
            return
        run_goal_id = run.goal_id
        # MEM-D2/D4a 取数口径：solved_course_business_ids 必须在把父版本保留行
        # 合并进 assignments **之前**计算——它是「本次求解课次」与「合并交付课表」
        # 的分界，goal 验收器的三集合判定依赖这个字段。
        result["solved_course_business_ids"] = sorted(
            {
                str(item.get("course_business_id") or "")
                for item in result.get("assignments", [])
                if str(item.get("course_business_id") or "").strip()
            }
        )
        run.model_status = result["model_status"]
        run.objective_value = result["objective_value"]
        run.best_bound = result["best_bound"]
        run.wall_time_seconds = result["wall_time_seconds"]
        run.conflict_rule_ids = result["conflict_rule_ids"]
        run.priority_rule_ids = result["priority_rule_ids"]
        run.priority_explanations = result["priority_explanations"]
        run.result_payload = result
        run.status = "completed"
        if result["model_status"] in {"OPTIMAL", "FEASIBLE"}:
            version_no = (
                int(
                    db.scalar(
                        select(func.coalesce(func.max(ScheduleVersion.version_no), 0)).where(
                            ScheduleVersion.schedule_set_id == run.schedule_set_id
                        )
                    )
                    or 0
                )
                + 1
            )
            parent_id = run.request_payload.get("parent_schedule_id")
            assignments = list(result["assignments"])
            parent_previous: dict[str, tuple[str | None, str, str]] = {}
            if parent_id:
                solved_course_ids = {str(item["course_session_id"]) for item in assignments}
                parent_rows = db.scalars(
                    select(ScheduleAssignment).where(
                        ScheduleAssignment.schedule_version_id == parent_id
                    )
                ).all()
                parent_courses = {
                    item.id: item
                    for item in db.scalars(
                        select(CourseSession).where(
                            CourseSession.id.in_([item.course_session_id for item in parent_rows])
                        )
                    )
                }
                excluded_ids = set(run.request_payload.get("excluded_course_session_ids", []))
                for parent_item in parent_rows:
                    if parent_item.course_session_id in excluded_ids:
                        continue
                    parent_course = parent_courses.get(parent_item.course_session_id)
                    course_business_id = (
                        parent_course.business_id
                        if parent_course
                        else parent_item.course_session_id
                    )
                    parent_previous[course_business_id] = (
                        parent_item.lesson_date.isoformat() if parent_item.lesson_date else None,
                        parent_item.room_business_id,
                        parent_item.slot_business_id,
                    )
                    if parent_item.course_session_id in solved_course_ids:
                        continue
                    assignments.append(
                        {
                            "course_session_id": parent_item.course_session_id,
                            "course_business_id": course_business_id,
                            "class_business_id": (
                                parent_course.class_business_id if parent_course else ""
                            ),
                            "teacher_business_id": (
                                parent_course.teacher_business_id if parent_course else ""
                            ),
                            "lesson_date": (
                                parent_item.lesson_date.isoformat()
                                if parent_item.lesson_date
                                else None
                            ),
                            "room_business_id": parent_item.room_business_id,
                            "slot_business_id": parent_item.slot_business_id,
                            "change_kind": "unchanged",
                        }
                    )
            result["assignments"] = assignments
            run.result_payload = result
            previous = parent_previous | {
                item["course_business_id"]: (
                    item.get("lesson_date"),
                    item["room_business_id"],
                    item["slot_business_id"],
                )
                for item in run.request_payload.get("previous_assignments", [])
            }
            metrics = calculate_metrics(db, assignments, run.schedule_set_id)
            metrics["changed_assignments"] = sum(
                1
                for item in assignments
                if item["course_business_id"] in previous
                and previous[item["course_business_id"]]
                != (item.get("lesson_date"), item["room_business_id"], item["slot_business_id"])
            )
            schedule = ScheduleVersion(
                schedule_set_id=run.schedule_set_id,
                version_no=version_no,
                name=f"课表版本 V{version_no}",
                status="draft",
                parent_id=parent_id,
                solver_run_id=run.id,
                metrics=metrics,
                created_by=run.created_by,
            )
            db.add(schedule)
            db.flush()
            for item in assignments:
                old = previous.get(item["course_business_id"])
                change_kind = (
                    "unchanged"
                    if old
                    == (item.get("lesson_date"), item["room_business_id"], item["slot_business_id"])
                    else ("changed" if old else "assigned")
                )
                db.add(
                    ScheduleAssignment(
                        schedule_version_id=schedule.id,
                        course_session_id=item["course_session_id"],
                        lesson_date=_parse_date(item.get("lesson_date")),
                        slot_business_id=item["slot_business_id"],
                        room_business_id=item["room_business_id"],
                        change_kind=change_kind,
                    )
                )
            event = db.scalar(
                select(RescheduleEvent).where(
                    RescheduleEvent.schedule_set_id == run.schedule_set_id,
                    RescheduleEvent.solver_run_id == run.id,
                )
            )
            if event:
                event.status = "candidate_ready"
                event.candidate_schedule_id = schedule.id
        if run_goal_id:
            # MEM-D2/D6：run completed 时先把验收状态置 pending（报告在下面
            # 的独立事务里异步生成）。前端据此显示「验收中…」而不是干等。
            goal = db.get(SolveGoal, run_goal_id)
            if goal is not None and goal.status != "abandoned":
                goal.acceptance_status = "pending"
                goal.acceptance_detail = None
        db.commit()
    # 目标验收闭环（MEM-C3）：run 到达 completed 后对关联目标自动出验收报告。
    # 放在求解结果事务之外单独提交——验收层的任何异常都不得影响求解落库，
    # 也不得让报告与课表版本处于同一失败域。
    if run_goal_id:
        _evaluate_goal_for_run(run_id, str(run_goal_id))


def _goal_version_anchor(request_payload: dict[str, Any] | None) -> int | None:
    """目标写回的版本锚点：任务创建时冻结的 goal_checklist_version。

    MEM-E2/E2a 在创建任务时把目标当时的清单版本冻进 request_payload
    （api.create_solver_run）；验收异常/求解失败的写回一律以它为乐观锁——
    即便写回前另一会话已修订清单并完成新版验收，条件 UPDATE 的
    `checklist_revision = 锚点` 也不匹配，旧失败不会改写新结论。
    """
    raw = (request_payload or {}).get("goal_checklist_version")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _mark_goal_acceptance_failed(db: Any, goal_id: str, *, version: int, detail: str) -> bool:
    """异常/失败路径的目标写回：唯一入口是带版本守卫的条件 UPDATE。

    与 goal.apply_goal_evaluation 的结论写回同构（MEM-F/F2 第五轮复审模式）：
    WHERE id + checklist_revision = 版本锚点 + status <> 'abandoned'（abandoned
    是人工终态，验收异常与求解失败同样不得越权改写），rowcount 判定。行数=0
    = 版本已前移或目标已放弃——**目标当前结论一字不改**，失败只留在该 run
    自己的 goal_report 上（由调用方记录 run 与清单版本）。
    """
    result = db.execute(
        update(SolveGoal)
        .where(
            SolveGoal.id == goal_id,
            SolveGoal.checklist_revision == version,
            SolveGoal.status != "abandoned",
        )
        .values(acceptance_status="failed", acceptance_detail=detail)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0) == 1


def _evaluate_goal_for_run(run_id: str, goal_id: str) -> None:
    # 延迟导入：goal.py 验收器复用本模块的 count_hard_conflicts，顶层互相引用成环。
    from .goal import apply_goal_evaluation

    evaluated_version: int | None = None
    try:
        with SessionLocal() as db:
            run = db.get(SolverRun, run_id)
            goal = db.get(SolveGoal, goal_id)
            if run is None or goal is None:
                return
            # 评估时点版本：优先创建时冻结的 goal_checklist_version；旧任务没有
            # 该字段时退回此刻读到的持久化 checklist_revision。评估失败写回时
            # 以它做条件 UPDATE 的版本守卫。
            evaluated_version = _goal_version_anchor(run.request_payload)
            if evaluated_version is None:
                evaluated_version = int(goal.checklist_revision)
            # TC-4 写入点②（docs/roadmap/07-task-context.md §4.2）：run completed
            # 产出草稿 → 按 ScheduleVersion.solver_run_id（unique）反查本次产物，
            # 版本仍为 draft 时写 goal.context.work_draft_schedule_id。与验收
            # 同一独立事务；发布/回滚不改这个指针——「正在调整」语义由
            # api.create_solver_run 的三级基准选择按「仍为 draft」惰性校验保证。
            if goal.status != "abandoned":
                draft_version = db.scalar(
                    select(ScheduleVersion).where(ScheduleVersion.solver_run_id == run.id)
                )
                if draft_version is not None and draft_version.status == "draft":
                    context = dict(goal.context or {})
                    context.setdefault("schema_version", 1)
                    context["work_draft_schedule_id"] = draft_version.id
                    goal.context = context
                    # 立即 flush：apply_goal_evaluation 的事务内重读（db.refresh）
                    # 会丢弃未 flush 的 ORM 赋值——先落库（事务内可见）再验收。
                    db.flush()
            apply_goal_evaluation(db, run)
            db.commit()
    except Exception as exc:  # noqa: BLE001 - 验收失败不影响求解结果落库
        # MEM-D2/D6：验收异常不再只打日志——acceptance_status 置 failed，原因
        # 同时落 goal.acceptance_detail 与 run.goal_report 失败标记，API 可见、
        # 前端可停轮询并显示「验收失败：原因」。
        # 第七轮复审收口：写回不再用 ORM 对象直接赋值（那会绕过版本保护、
        # 且漏掉 abandoned 守卫），统一走 _mark_goal_acceptance_failed 的条件
        # UPDATE；rowcount=0（评估到写回之间清单已修订且新版本已完成验收/
        # 目标已放弃）时目标当前结论一字不改，失败只留在旧任务自己的报告上。
        logger.exception("目标验收执行失败：求解结果不受影响，目标验收状态置 failed")
        try:
            with SessionLocal() as db:
                run = db.get(SolverRun, run_id)
                detail = f"{type(exc).__name__}: {exc}"[:1000]
                anchor = None
                if run is not None:
                    anchor = _goal_version_anchor(run.request_payload)
                    if anchor is None:
                        anchor = evaluated_version
                    if anchor is None:
                        # 连评估时点版本都没有（异常发生在读取之前）：退回此刻
                        # 持久化版本做兜底锚点。
                        current = db.scalar(
                            select(SolveGoal.checklist_revision).where(SolveGoal.id == goal_id)
                        )
                        anchor = int(current) if current is not None else None
                landed = False
                if anchor is not None:
                    landed = _mark_goal_acceptance_failed(
                        db, goal_id, version=anchor, detail=detail
                    )
                if run is not None:
                    run.goal_report = {
                        "goal_id": goal_id,
                        "run_id": run_id,
                        "acceptance_status": "failed",
                        "acceptance_error": detail,
                        "all_passed": False,
                        "items": [],
                        "gaps": [],
                        "decision": None,
                        "evaluated_at": shanghai_now().isoformat(),
                        # 审计：本次失败写回对应的清单版本与目标写回结果。
                        "checklist_version": anchor,
                        "goal_writeback": "applied" if landed else "skipped",
                    }
                db.commit()
        except Exception:  # noqa: BLE001 - 兜底落库也失败时只能记日志
            logger.exception("目标验收失败状态落库失败：验收结果不可见，请检查数据库")


def _persist_failure(run_id: str, message: str) -> None:
    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        if run:
            run.status = "failed"
            run.error_message = message
            # MEM-D2/D6：run 失败 = 不会有验收报告。若挂着目标，同步把验收
            # 状态置 failed 并落失败标记，避免目标永远停在 pending、前端永远
            # 轮询不到报告。
            # 第七轮复审收口：写回走带版本守卫的条件 UPDATE（锚点 = 创建时
            # 冻结的 goal_checklist_version，旧任务退回当前持久化版本）；
            # rowcount=0（求解期间清单已修订至新版本/目标已放弃）时不动目标
            # 当前结论，失败只留在 run.goal_report 上。
            if run.goal_id:
                detail = f"求解失败，未进入验收：{message}"[:1000]
                anchor = _goal_version_anchor(run.request_payload)
                if anchor is None:
                    current = db.scalar(
                        select(SolveGoal.checklist_revision).where(SolveGoal.id == run.goal_id)
                    )
                    anchor = int(current) if current is not None else None
                landed = False
                if anchor is not None:
                    landed = _mark_goal_acceptance_failed(
                        db, run.goal_id, version=anchor, detail=detail
                    )
                run.goal_report = {
                    "goal_id": run.goal_id,
                    "run_id": run.id,
                    "acceptance_status": "failed",
                    "acceptance_error": detail,
                    "all_passed": False,
                    "items": [],
                    "gaps": [],
                    "decision": None,
                    "evaluated_at": shanghai_now().isoformat(),
                    # 审计：本次失败写回对应的清单版本与目标写回结果。
                    "checklist_version": anchor,
                    "goal_writeback": "applied" if landed else "skipped",
                }
            db.commit()


def _merge_frozen_extras(payload: dict[str, Any], schedule_set_id: str, db: Any) -> None:
    """求解前把快照里冻结的「额外规则」并入同一条规则管线（MEM-C1 + TC-3）。

    两类冻结产物，都只在执行侧并入、永不替换或覆盖显式规则：
    1. 偏好记忆（snapshot.payload["memory"].compiled_rules）：编译产物是
       hardness=soft 的规则对象，与快照里的规则一起走 _normalized_rules 的现有
       路径。编译产物在创建任务时就冻结进快照——这里只读快照，不再现场读库，
       改记忆不影响在途求解的可复现性。status=compile_failed 或旧快照没有
       memory 节时无偏好求解：记忆是加分项，不能变成排课主链路的故障点，但
       编译失败必须由解释层显式提示（explain.py / 前端），不得无声出课表。
    2. 任务级约束（payload["task_constraint_rules"]，api.create_solver_run 冻进
       request_payload 的独立键，docs/roadmap/07-task-context.md §2.4）：goal
       清单/软约束/请求直调约束编译成的规则对象。**不要**把它们写进
       request_payload["rules"]——execute_solver_run 与 enqueue_solver_run 的
       ``payload.update(run.request_payload)``（本模块两处）会用请求键整体覆盖
       快照冻结的规则全集，独立键正是为躲开这个覆盖陷阱而存在。
    """
    memory = payload.get("memory") or {}
    if memory.get("status") == "ok":
        compiled = memory.get("compiled_rules") or []
    else:
        compiled = []
        if memory.get("status") == "compile_failed":
            logger.warning(
                "偏好记忆编译失败，本次求解将在无偏好状态下进行：%s", memory.get("detail")
            )
    payload["rules"] = [
        *(payload.get("rules") or []),
        *compiled,
        *(payload.get("task_constraint_rules") or []),
    ]


def execute_solver_run(run_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        if run is None:
            raise ValueError(f"Unknown solver run: {run_id}")
        snapshot = db.get(DataSnapshot, run.snapshot_id)
        if snapshot is None:
            raise ValueError(f"Missing data snapshot: {run.snapshot_id}")
        if snapshot.schedule_set_id != run.schedule_set_id:
            raise ValueError("Solver run and data snapshot belong to different schedule sets")
        run.status = "running"
        db.commit()
        payload = dict(snapshot.payload)
        payload.update(run.request_payload)
        _merge_frozen_extras(payload, run.schedule_set_id, db)
    try:
        result = solve_problem(payload)
        _persist_result(run_id, result)
        return result
    except Exception as exc:
        _persist_failure(run_id, str(exc))
        raise


def enqueue_solver_run(run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(SolverRun, run_id)
        if run is None:
            raise ValueError(f"Unknown solver run: {run_id}")
        snapshot = db.get(DataSnapshot, run.snapshot_id)
        if snapshot is None:
            raise ValueError(f"Missing data snapshot: {run.snapshot_id}")
        if snapshot.schedule_set_id != run.schedule_set_id:
            raise ValueError("Solver run and data snapshot belong to different schedule sets")
        run.status = "running"
        db.commit()
        payload = dict(snapshot.payload)
        payload.update(run.request_payload)
        _merge_frozen_extras(payload, run.schedule_set_id, db)

    future: Future[dict[str, Any]] = _get_executor().submit(solve_problem, payload)

    def done_callback(completed: Future[dict[str, Any]]) -> None:
        try:
            _persist_result(run_id, completed.result())
        except Exception as exc:
            _persist_failure(run_id, str(exc))

    future.add_done_callback(done_callback)
