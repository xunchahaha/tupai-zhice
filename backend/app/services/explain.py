"""求解结果的解释层。

分工是刻意的：**事实由代码算，措辞由模型写**。

- 本模块把一次求解的结论整理成确定性的事实包（`build_explanation_facts`），
  并给出一份不依赖任何模型的兜底解释（`deterministic_summary`）。
- AI 只负责把事实包翻译成教务读得懂的话，以及做一次意图核对。
  它不判断「排得对不对」——硬冲突由 `tasks.count_hard_conflicts` 确定性重算，
  再让模型复核一个确定性组件的输出，只会把可靠性往下拉。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import ColumnElement, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from ..models import CourseSession, DataSnapshot, Rule, ScheduleVersion, SolveGoal, SolverRun
from ..timezone import shanghai_now
from .solver import _session_matches_rule

# 求解器内建约束的业务口径。这些 SYSTEM-* 标识不对应教务录入的规则，
# 直接展示等于把内部符号丢给用户，所以先在代码里翻译一次。
SYSTEM_RULE_GLOSSARY: dict[str, str] = {
    "SYSTEM-FIXED-TIME": "固定上课时段：课次的上课时刻保持原样，只允许改日期和教室。",
    "SYSTEM-CLASS-NO-OVERLAP": "同一班级不能在同一时间上两节课。",
    "SYSTEM-ROOM-NO-OVERLAP": "同一教室不能在同一时间安排两节课。",
    "SYSTEM-TEACHER-NO-OVERLAP": "同一位教师不能在同一时间上两节课（教研组不受此限）。",
    "SYSTEM-CALENDAR-NO-OVERLAP": "同一个飞书日程账号不能在同一时间被占用两次。",
}

MODEL_STATUS_MEANING: dict[str, str] = {
    "OPTIMAL": "在给定时限内找到并证明了最优解。",
    "FEASIBLE": "找到了可行解，但没能在时限内证明它最优。",
    "INFEASIBLE": "在当前约束下不存在任何可行解。",
    "UNKNOWN": "在时限内既没找到可行解，也没能证明无解。",
}

# 「一句话排课」把这些标签交给模型做候选值，`api._solver_rules_from_labels`
# 再按字面把它们匹配回 solver_rules。建议指令要想被解析成同一组硬约束，就必须原样
# 引用这里的措辞，所以标签定义放在解释层，api 直接引用，避免两处各写一份文案。
SOLVER_RULE_LABELS: dict[str, str] = {
    "fixed_time": "固定时段不可调整",
    "room_no_overlap": "同一教室真实时间区间不可重叠",
    "teacher_no_overlap": "同一教师不可同时上两节课",
    "calendar_no_overlap": "同一具体日程账号不可重叠",
    "minimize_changes": "优先最小化日期和教室变更",
}

# SolveRequest.date_window_days 的上界。建议指令不能给出会被入参校验直接拒掉的数字。
MAX_DATE_WINDOW_DAYS = 31

# 建议指令挑的重跑批次至少要有这么多课次：只排一节课当然排得出来，但什么也证明不了。
MIN_RETRY_GROUP_SESSIONS = 3


def describe_conflict_rule(
    db: Session,
    business_id: str,
    *,
    schedule_set_id: str | None = None,
    frozen_rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把一个冲突规则标识翻译成业务口径。

    教务录入的规则读它自己的原文；SYSTEM-* 走内建词表。

    第七轮复审收口（方案隔离 + 历史快照）：解释的是「求解当时」的规则，所以
    用户规则**优先读 run 对应 DataSnapshot.payload["rules"] 里的冻结原文**
    （frozen_rules，创建任务时定格的 active 规则全集）；冻结里没有时才回退
    现库，且回退查询强制携带 schedule_set_id——Rule 的唯一约束是
    (schedule_set_id, business_id)，只按 business_id 查会把方案乙的同名规则
    混进来。没有方案上下文的调用不做现库查询（无法安全定位，宁缺毋错）。
    """
    if business_id in SYSTEM_RULE_GLOSSARY:
        return {
            "business_id": business_id,
            "origin": "solver_builtin",
            "meaning": SYSTEM_RULE_GLOSSARY[business_id],
        }
    for frozen in frozen_rules or []:
        if str(frozen.get("business_id") or "") != business_id:
            continue
        return {
            "business_id": business_id,
            "origin": "user_rule",
            "meaning": frozen.get("source_text"),
            "constraint_type": frozen.get("constraint_type"),
            "hardness": frozen.get("hardness"),
            "actor_type": frozen.get("actor_type"),
            "actor_ids": list(frozen.get("actor_ids") or []),
            "scope": dict(frozen.get("scope") or {}),
            # 任务级约束（TC-3 §2.4）：TASK- 规则永不建 Rule 行，来源只在
            # source_doc——goal:<id> 前缀表示「本次任务要求（goal 来源）」，
            # request:task_constraint 表示请求直调来源；前端据此转述。
            "source_doc": frozen.get("source_doc"),
            # 冻结原文来自求解时点的快照，改规则不影响旧解释。
            "source": "snapshot",
        }
    if schedule_set_id is None:
        return {
            "business_id": business_id,
            "origin": "unknown",
            "meaning": "求解器返回了这个标识，但没有方案上下文，无法安全定位对应规则。",
        }
    rule = db.scalar(
        select(Rule).where(
            Rule.business_id == business_id,
            Rule.schedule_set_id == schedule_set_id,
        )
    )
    if rule is None:
        return {
            "business_id": business_id,
            "origin": "unknown",
            "meaning": "求解器返回了这个标识，但库里找不到对应规则。",
        }
    return {
        "business_id": business_id,
        "origin": "user_rule",
        "meaning": rule.source_text,
        "constraint_type": rule.constraint_type,
        "hardness": rule.hardness,
        "actor_type": rule.actor_type,
        "actor_ids": list(rule.actor_ids or []),
        "scope": dict(rule.scope or {}),
        "source": "current_db",
    }


def _assignment_digest(assignments: list[dict[str, Any]], limit: int = 40) -> dict[str, Any]:
    """给模型看的课表摘要。

    整表可能上千条，全量塞进 prompt 既贵又没必要；这里只给统计量加少量样本，
    意图核对靠的是「范围对不对、动了什么」，不是逐条复核。
    """
    changed = [item for item in assignments if item.get("change_kind") == "changed"]
    dates = sorted(
        {str(item.get("lesson_date")) for item in assignments if item.get("lesson_date")}
    )
    return {
        "total": len(assignments),
        "date_span": [dates[0], dates[-1]] if dates else None,
        "distinct_dates": len(dates),
        "distinct_classes": len({str(item.get("class_business_id") or "") for item in assignments}),
        "distinct_rooms": len({str(item.get("room_business_id") or "") for item in assignments}),
        "changed_count": len(changed),
        "changed_samples": [
            {
                "course_business_id": item.get("course_business_id"),
                "class_business_id": item.get("class_business_id"),
                "lesson_date": item.get("lesson_date"),
                "room_business_id": item.get("room_business_id"),
            }
            for item in changed[:limit]
        ],
    }


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed else None


def _json_array_contains(values: Any, target: str) -> ColumnElement[bool]:
    elements = func.json_each(values).table_valued("key", "value")
    return exists(select(literal(1)).select_from(elements).where(elements.c.value == target))


def _frozen_sessions_in_scope(
    sessions: list[dict[str, Any]], scope: dict[str, Any]
) -> list[dict[str, Any]]:
    """按求解范围过滤快照冻结的课次（dict 形态，日期是 ISO 字符串）。

    条件语义与现库 SQL 一致：日期边界存在时无日期课次不算命中（SQL 里
    NULL 比较为假）；产品班型按主值 + product_types 列表任一命中。
    """
    date_from = _as_date(scope.get("date_from"))
    date_to = _as_date(scope.get("date_to"))
    business_lines = {str(item) for item in scope.get("business_lines") or []}
    product_types = {str(item) for item in scope.get("product_types") or []}
    class_ids = {str(item) for item in scope.get("class_business_ids") or []}
    course_ids = {str(item) for item in scope.get("course_business_ids") or []}
    matched: list[dict[str, Any]] = []
    for session in sessions:
        if business_lines and str(session.get("business_line") or "") not in business_lines:
            continue
        if product_types:
            owned = {str(session.get("product_type") or "")}
            owned.update(str(item) for item in session.get("product_types") or [])
            if not owned & product_types:
                continue
        if class_ids and str(session.get("class_business_id") or "") not in class_ids:
            continue
        if course_ids and str(session.get("business_id") or "") not in course_ids:
            continue
        lesson_date = _as_date(session.get("lesson_date"))
        if date_from and (lesson_date is None or lesson_date < date_from):
            continue
        if date_to and (lesson_date is None or lesson_date > date_to):
            continue
        matched.append(session)
    return matched


# 现库回退路径构造课次 dict 所需的字段：范围聚合用前四个，记忆匹配用其余。
_SESSION_FIELDS = (
    "business_id",
    "business_line",
    "product_type",
    "class_business_id",
    "product_types",
    "teacher_business_id",
    "teacher_business_ids",
    "lesson_date",
    "original_room_business_id",
    "candidate_room_business_ids",
)


def _current_sessions_in_scope(
    db: Session, scope: dict[str, Any], schedule_set_id: str
) -> list[dict[str, Any]]:
    """现库回退：按范围 + 方案过滤课次并转成 dict。

    快照不可用时的兜底路径；schedule_set_id 是强制条件——解释方案甲绝不读
    方案乙的课次（第七轮复审：原实现缺这个条件，多方案下把其它方案的班级
    混进重试候选）。
    """
    conditions: list[ColumnElement[bool]] = [CourseSession.schedule_set_id == schedule_set_id]
    if scope.get("business_lines"):
        conditions.append(CourseSession.business_line.in_(scope["business_lines"]))
    if scope.get("product_types"):
        product_conditions = [
            or_(
                CourseSession.product_type == product_type,
                _json_array_contains(CourseSession.product_types, product_type),
            )
            for product_type in scope["product_types"]
        ]
        conditions.append(or_(*product_conditions))
    if scope.get("class_business_ids"):
        conditions.append(CourseSession.class_business_id.in_(scope["class_business_ids"]))
    if scope.get("course_business_ids"):
        conditions.append(CourseSession.business_id.in_(scope["course_business_ids"]))
    date_from = _as_date(scope.get("date_from"))
    if date_from:
        conditions.append(CourseSession.lesson_date >= date_from)
    date_to = _as_date(scope.get("date_to"))
    if date_to:
        conditions.append(CourseSession.lesson_date <= date_to)
    rows = db.scalars(select(CourseSession).where(*conditions)).all()
    return [{field: getattr(item, field) for field in _SESSION_FIELDS} for item in rows]


def _scope_candidates(
    db: Session,
    scope: dict[str, Any],
    *,
    schedule_set_id: str,
    frozen_sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """本次求解范围内真实存在的业务线 / 产品班型 / 班级，以及最小的一组课次。

    建议指令要能被 `/assistant/interpret` 原样解析，而那个接口只接受库里存在的业务
    实体（`_validated_assistant_scope` 对未知实体直接 422）。所以候选值一律从课次表
    取，拼出来的指令里的班级、产品班型必然落在合法集合内，而不是模型编出来的名字。

    第七轮复审收口（方案隔离 + 历史快照）：候选**优先取 run 对应快照冻结的
    course_sessions**——本次求解面对的就是那份课次清单，重试建议不能引入
    快照之外的实体；快照不可用时回退现库，且查询强制按 schedule_set_id 过滤。
    origin 字段标注数据来源（snapshot=求解时点 / current_db=解释时点）。
    """
    if frozen_sessions is not None:
        in_scope = _frozen_sessions_in_scope(frozen_sessions, scope)
        origin = "snapshot"
        as_of = None
    else:
        in_scope = _current_sessions_in_scope(db, scope, schedule_set_id)
        origin = "current_db"
        as_of = shanghai_now().isoformat()
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for session in in_scope:
        key = (
            str(session.get("business_line") or ""),
            str(session.get("product_type") or ""),
            str(session.get("class_business_id") or ""),
        )
        group = groups.setdefault(
            key,
            {
                "business_line": key[0],
                "product_type": key[1],
                "class_business_id": key[2],
                "session_count": 0,
                "date_from": None,
                "date_to": None,
            },
        )
        group["session_count"] += 1
        lesson_date = _iso(session.get("lesson_date"))
        if lesson_date:
            low, high = group["date_from"], group["date_to"]
            group["date_from"] = min(low, lesson_date) if low else lesson_date
            group["date_to"] = max(high, lesson_date) if high else lesson_date
    ordered_groups = list(groups.values())
    dates = sorted(
        value
        for group in ordered_groups
        for value in (group["date_from"], group["date_to"])
        if value
    )
    # 重跑最容易排得出来的是课次最少的那一组；同数按班级、班型定序，保证同一份数据
    # 每次给出同一条建议。全是零星单节课时退回最大的一组，免得建议一个只有一节课的批次。
    ordered = sorted(
        ordered_groups,
        key=lambda item: (
            item["session_count"],
            item["class_business_id"],
            item["product_type"],
        ),
    )
    qualified = [item for item in ordered if item["session_count"] >= MIN_RETRY_GROUP_SESSIONS]
    retry_group = next(iter(qualified or ordered[-1:]), None)
    candidates = {
        "session_count": sum(group["session_count"] for group in ordered_groups),
        "group_count": len(ordered_groups),
        "business_lines": sorted(
            {item["business_line"] for item in ordered_groups if item["business_line"]}
        )[:12],
        "product_types": sorted(
            {item["product_type"] for item in ordered_groups if item["product_type"]}
        )[:12],
        "class_business_ids": sorted(
            {item["class_business_id"] for item in ordered_groups if item["class_business_id"]}
        )[:12],
        "lesson_date_range": [dates[0], dates[-1]] if dates else None,
        "retry_group": retry_group,
        "origin": origin,
    }
    if as_of:
        candidates["as_of"] = as_of
    return candidates


def _memory_rule_matches_session(rule: dict[str, Any], session: dict[str, Any]) -> bool:
    """编译后的记忆规则是否作用于一节课程（第二段「实际匹配」的判定）。

    教师/班级/课程主体沿用求解器同一套 actor 对齐口径
    （solver._session_matches_rule，room_id 传空）；教室主体特殊：求解器里
    教室约束是「按安置结果生效」的（看 assignment 的 room），课程层面没有
    确定归属，这里按课次的原始教室 / 候选教室归属判定，都没有时不算匹配。
    """
    actor_type = str(rule.get("actor_type") or "").lower()
    if actor_type in {"room", "classroom", "教室"}:
        actor_ids = {str(item) for item in rule.get("actor_ids") or [] if item}
        if not actor_ids:
            return False
        pool = {str(session.get("original_room_business_id") or "")}
        pool.update(str(item) for item in session.get("candidate_room_business_ids") or [])
        return bool(actor_ids & {value for value in pool if value})
    return _session_matches_rule(session, "", rule)


def _memory_result_evidence(
    rules: list[dict[str, Any]], assignments: list[dict[str, Any]]
) -> dict[str, Any]:
    """第三段「有结果证据的满足情况」：逐条核对求解结果里的软偏好证据。

    只数证据，不下验收结论：prefer_* 数「命中偏好目标的安置数」，
    avoid_* 数「触碰回避目标的安置数」，consecutive_sessions 等没有逐条
    证据的谓词保持 0 并由 constraint_type 说明口径。规则的日期窗口
    （scope.date_from/date_to）存在时只统计窗口内的安置。
    """
    rows: list[dict[str, Any]] = []
    total_hits = 0
    total_violations = 0
    for rule in rules:
        constraint_type = str(rule.get("constraint_type") or "")
        rule_scope = dict(rule.get("scope") or {})
        date_from = _as_date(rule_scope.get("date_from"))
        date_to = _as_date(rule_scope.get("date_to"))
        slots = {str(item) for item in rule_scope.get("slot_ids") or []}
        rooms = {str(item) for item in rule_scope.get("room_ids") or []}
        actor_ids = {str(item) for item in rule.get("actor_ids") or [] if item}
        actor_type = str(rule.get("actor_type") or "").lower()
        hits = 0
        violations = 0
        matched_assignments = 0
        for item in assignments:
            if actor_type in {"room", "classroom", "教室"}:
                # 教室主体按安置结果归属：放在该教室的安置才算这条规则的作用对象。
                if str(item.get("room_business_id") or "") not in actor_ids:
                    continue
            else:
                teacher = str(item.get("teacher_business_id") or "")
                klass = str(item.get("class_business_id") or "")
                course = str(item.get("course_business_id") or "")
                if actor_ids and not (
                    teacher in actor_ids or klass in actor_ids or course in actor_ids
                ):
                    continue
            matched_assignments += 1
            lesson_date = _as_date(item.get("lesson_date"))
            if date_from and (lesson_date is None or lesson_date < date_from):
                continue
            if date_to and (lesson_date is None or lesson_date > date_to):
                continue
            slot = str(item.get("slot_business_id") or "")
            room = str(item.get("room_business_id") or "")
            if constraint_type == "preferred_slot" and slot in slots:
                hits += 1
            elif constraint_type == "forbidden_slot" and slot in slots:
                violations += 1
            elif constraint_type == "preferred_room" and room in rooms:
                hits += 1
            elif constraint_type == "forbidden_room" and room in rooms:
                violations += 1
        total_hits += hits
        total_violations += violations
        rows.append(
            {
                "business_id": rule.get("business_id"),
                "constraint_type": constraint_type,
                "matched_assignments": matched_assignments,
                "prefer_hits": hits,
                "avoid_violations": violations,
            }
        )
    return {
        "evaluated_assignments": len(assignments),
        "prefer_hits": total_hits,
        "avoid_violations": total_violations,
        "rules": rows,
    }


def _memory_usage_facts(
    memory: dict[str, Any],
    *,
    scope: dict[str, Any],
    sessions: list[dict[str, Any]],
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """偏好记忆使用情况的事实化转述（MEM-C1 修正 6 + 第七轮复审口径收口）。

    三段区分（编译资格 ≠ 对本次课程的实际匹配 ≠ 结果满足）：

    - ``compilation``：已获准编译——创建任务时点、方案级的编译资格统计
      （summary 原值），不代表这些偏好作用于本次求解的课次；
    - ``match``：对本次课程实际匹配——按本次任务的课程范围逐条核对编译
      出的软规则，只有匹配到的才真正参与了本次求解的打分；
    - ``satisfaction``：有结果证据的满足情况——**仅当求解结果存在时**计算；
      没有结果时保持 None，headline 显式说明「无使用证据」，绝不把编译
      状态当成最终使用结论。

    status=compile_failed 时必须显式说「本次未使用偏好记忆」——求解照常成功
    不代表偏好参与了，静默降级正是这轮修正要消灭的行为。
    """
    summary = dict(memory.get("summary") or {})
    considered = int(summary.get("considered") or 0)
    admitted = int(summary.get("applied") or 0)
    excluded = int(summary.get("unused") or 0)
    compiled_rules = [rule for rule in memory.get("compiled_rules") or [] if isinstance(rule, dict)]
    match_rows = [
        {
            "business_id": str(rule.get("business_id") or ""),
            "matched_sessions": sum(
                1 for session in sessions if _memory_rule_matches_session(rule, session)
            ),
        }
        for rule in compiled_rules
    ]
    matched = sum(1 for row in match_rows if row["matched_sessions"])
    assignments = [
        item for item in (result or {}).get("assignments") or [] if isinstance(item, dict)
    ]
    satisfaction = _memory_result_evidence(compiled_rules, assignments) if assignments else None
    headline = memory_headline(
        memory,
        match=matched if compiled_rules else None,
        sessions=sessions,
        result=result,
    )
    return {
        "status": str(memory.get("status") or "not_recorded"),
        "headline": headline,
        "detail": memory.get("detail"),
        # 第一段：已获准编译（创建时点、方案级，summary 原值）。
        "compilation": {"considered": considered, "admitted": admitted, "excluded": excluded},
        # 第二段：对本次课程实际匹配（按本次任务课程范围计算）。
        "match": {
            "scope_sessions": len(sessions),
            "admitted": len(compiled_rules),
            "matched": matched,
            "unmatched": len(compiled_rules) - matched,
            "rules": match_rows,
        },
        # 第三段：有结果证据的满足情况；无结果时为 None，不得编造结论。
        "satisfaction": satisfaction,
        "outcomes": list(memory.get("outcomes") or []),
    }


def memory_headline(
    memory: dict[str, Any],
    *,
    match: int | None = None,
    sessions: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
) -> str:
    """一句话说明偏好记忆的使用口径。措辞由代码拼，模型只许复述。

    第七轮复审收口：summary 是「创建时点、方案级」的编译资格统计，原措辞
    「本次参考 N 条（已应用 X / 未使用 Y）」把方案级数字说成了本次结论。
    现按三段拼：编译资格（创建时点）→ 本次范围匹配（有编译结果时）→
    结果证据（仅当求解结果存在；没有就明说，不下最终使用结论）。
    """
    if memory.get("status") == "compile_failed":
        return "本次未使用偏好记忆：编译失败"
    if memory.get("status") == "not_recorded":
        # 早于「记忆冻结进快照」版本的历史任务：当时确实现读现用，只是没留痕，
        # 不能反过来断言它没有偏好记忆。
        return "该任务创建时尚未记录偏好记忆使用情况"
    summary = memory.get("summary") or {}
    considered = int(summary.get("considered") or 0)
    if considered == 0:
        return "创建任务时没有可用的偏好记忆"
    parts = [
        f"创建任务时 {considered} 条偏好记忆获准编译"
        f"（其中 {int(summary.get('applied') or 0)} 条编译进求解输入，"
        f"未编译 {int(summary.get('unused') or 0)} 条，原因见逐条明细）"
    ]
    if match is not None:
        scope_note = f"（范围内 {len(sessions or [])} 节课）" if sessions is not None else ""
        parts.append(f"按本次任务课程范围核对{scope_note}：{match} 条与本次课程实际匹配")
    if result is None or not [
        item for item in (result or {}).get("assignments") or [] if isinstance(item, dict)
    ]:
        parts.append("本次求解暂无结果，尚无使用证据，不据此下最终使用结论")
    return "；".join(parts)


def _goal_acceptance_facts(db: Session, run: SolverRun) -> dict[str, Any] | None:
    """目标验收摘要（MEM-C3）：带 goal 的 run 在事实包里附上次验收结论。

    只引用已落库的 run.goal_report 与目标原话，不再重算——解释层的职责是
    转述，验收口径以 services/goal.py 为准。
    """
    if not run.goal_id:
        return None
    goal = db.get(SolveGoal, run.goal_id)
    report = dict(run.goal_report or {})
    if not report:
        # 尚未验收（run 未到达 completed）：不预填空结论。
        return None
    items = [entry for entry in report.get("items") or [] if isinstance(entry, dict)]
    return {
        "goal_id": run.goal_id,
        "instruction": goal.instruction if goal is not None else None,
        "goal_status": goal.status if goal is not None else None,
        "all_passed": report.get("all_passed"),
        "passed_count": report.get("passed_count"),
        "failed_count": report.get("failed_count"),
        "decision": report.get("decision"),
        "items": [
            {
                "key": entry.get("key"),
                "kind": entry.get("kind"),
                "requirement": entry.get("requirement"),
                "passed": entry.get("passed"),
                # MEM-D2/D4b：unverifiable（缺日期/缺参数/无课表）与普通失败分开转述，
                # 解释层不得把「无法验证」说成「已达标」。
                "verdict": entry.get("verdict"),
                "detail": entry.get("detail"),
            }
            for entry in items
        ],
        "gaps": [entry for entry in report.get("gaps") or [] if isinstance(entry, dict)],
    }


def build_explanation_facts(db: Session, run: SolverRun) -> dict[str, Any]:
    """整理一次求解的确定性事实包。这里出现的每个数字都来自代码，不来自模型。

    第七轮复审收口（方案隔离 + 历史快照）：事实优先取 run 对应 DataSnapshot
    的冻结数据——冲突规则读 payload["rules"] 的冻结原文，范围候选与记忆匹配
    读 payload["course_sessions"] 的冻结课次；快照不可用时回退现库，且所有
    现库查询都强制携带 run.schedule_set_id，方案甲的解释绝不混入方案乙的
    规则与课程。
    """
    result = dict(run.result_payload or {})
    request = dict(run.request_payload or {})
    assignments = list(result.get("assignments") or [])
    schedule = db.scalar(select(ScheduleVersion).where(ScheduleVersion.solver_run_id == run.id))
    snapshot = db.get(DataSnapshot, run.snapshot_id) if run.snapshot_id else None
    snapshot_payload = dict(snapshot.payload or {}) if snapshot is not None else None
    # 冻结数据按「键真的在快照里」判断：键存在但为空列表也是如实的求解时点
    # 状态（当时的方案里就是没有 active 规则/课次），不能拿现库数据冒充；
    # 键缺失（旧格式快照）说明快照没记录这一段，只能回退现库并强制方案过滤。
    # 任务级约束（TC-3 §2.4）：TASK- 规则不进快照（不参与 checksum），冻结在
    # run.request_payload["task_constraint_rules"]——仍是求解时点的冻结值，
    # 追加进 frozen_rules 让 describe_conflict_rule 能转述其原文与来源。
    frozen_rules = (
        [
            item
            for item in [
                *(snapshot_payload.get("rules") or []),
                *dict(run.request_payload or {}).get("task_constraint_rules", []),
            ]
            if isinstance(item, dict)
        ]
        if snapshot_payload is not None and "rules" in snapshot_payload
        else None
    )
    frozen_sessions = (
        [item for item in snapshot_payload.get("course_sessions") or [] if isinstance(item, dict)]
        if snapshot_payload is not None and "course_sessions" in snapshot_payload
        else None
    )
    presolved = bool(result.get("presolve_infeasible"))
    model_status = run.model_status or "UNKNOWN"
    meaning = MODEL_STATUS_MEANING.get(model_status, "求解器没有返回可识别的状态。")
    if presolved and model_status == "INFEASIBLE":
        # 预检无解是在建模之前按鸽笼原理判定的，CP-SAT 根本没跑，措辞不能说「已证明」。
        meaning = "求解前的数据预检就判定无解，CP-SAT 没有运行，因此不能说「已证明无解」。"
    request_scope = {
        "instruction": request.get("instruction"),
        "business_lines": request.get("business_lines") or [],
        "product_types": request.get("product_types") or [],
        "class_business_ids": request.get("class_business_ids") or [],
        "course_business_ids": request.get("course_business_ids") or [],
        "date_from": request.get("date_from"),
        "date_to": request.get("date_to"),
        "date_window_days": request.get("date_window_days"),
        "change_weight": request.get("change_weight"),
        "solver_rules": request.get("solver_rules") or [],
    }
    # 记忆匹配用的课次清单与范围候选同一来源（快照优先，现库回退强制方案过滤）。
    if frozen_sessions is not None:
        scope_sessions = _frozen_sessions_in_scope(frozen_sessions, request_scope)
    else:
        scope_sessions = _current_sessions_in_scope(db, request_scope, run.schedule_set_id)
    return {
        "run": {
            "id": run.id,
            "run_type": run.run_type,
            "status": run.status,
            "model_status": model_status,
            "model_status_meaning": meaning,
            "presolve_infeasible": presolved,
            "objective_value": run.objective_value,
            "best_bound": run.best_bound,
            "wall_time_seconds": run.wall_time_seconds,
            "error_message": run.error_message,
        },
        "request_scope": request_scope,
        # 本次范围内实际有哪些班级、产品班型和日期，既给模型当事实依据，
        # 也给 build_retry_instruction 拼「下一条指令」时提供合法实体。
        "scope_candidates": _scope_candidates(
            db,
            request_scope,
            schedule_set_id=run.schedule_set_id,
            frozen_sessions=frozen_sessions,
        ),
        # 偏好记忆使用情况：编译失败必须原样传达，不许把「无声无偏好」说成正常；
        # 匹配与满足情况按本次任务范围/结果另行计算，不拿编译资格冒充使用结论。
        "memory_usage": _memory_usage_facts(
            dict(run.memory_usage or {}),
            scope=request_scope,
            sessions=scope_sessions,
            result=result,
        ),
        # 目标验收闭环（MEM-C3）：带 goal 的 run 附验收摘要（改动仅限附加事实字段）。
        "goal_acceptance": _goal_acceptance_facts(db, run),
        "conflicts": {
            "rule_ids": list(run.conflict_rule_ids or []),
            "rules": [
                describe_conflict_rule(
                    db,
                    business_id,
                    schedule_set_id=run.schedule_set_id,
                    frozen_rules=frozen_rules,
                )
                for business_id in (run.conflict_rule_ids or [])
            ],
            "priority_rule_ids": list(run.priority_rule_ids or []),
            "solver_diagnostics": list(run.priority_explanations or []),
        },
        "schedule": {
            "version_no": schedule.version_no if schedule else None,
            "name": schedule.name if schedule else None,
            # 这些指标由 tasks.calculate_metrics / count_hard_conflicts 独立重算，是权威值。
            "metrics": dict(schedule.metrics or {}) if schedule else {},
        },
        "assignments": _assignment_digest(assignments),
        # 参与本次求解的规则全集：快照里冻结的 active 规则就是求解时点的输入；
        # 快照不可用时回退现库（强制方案过滤）。
        "active_rules": (
            sorted(
                {str(item.get("business_id") or "") for item in frozen_rules} - {""},
            )
            if frozen_rules is not None
            else list(
                db.scalars(
                    select(Rule.business_id)
                    .where(
                        Rule.schedule_set_id == run.schedule_set_id,
                        Rule.status == "active",
                    )
                    .order_by(Rule.business_id)
                )
            )
        ),
    }


def deterministic_summary(facts: dict[str, Any]) -> dict[str, Any]:
    """不依赖任何模型的兜底解释。

    AI 未配置或调用失败时仍要给教务一份能读的结论——SYSTEM-* 的翻译本来就是
    代码能做完的部分，没有理由因为模型不可用就退回展示裸标识。
    """
    run = facts["run"]
    conflicts = facts["conflicts"]
    metrics = facts["schedule"].get("metrics") or {}
    memory_usage = facts.get("memory_usage") or {}
    headline = f"{run['model_status']}：{run['model_status_meaning']}"
    explanation: list[str] = []
    if run["model_status"] in {"OPTIMAL", "FEASIBLE"}:
        digest = facts["assignments"]
        span = digest["date_span"]
        explanation.append(
            f"共排定 {digest['total']} 个课次，覆盖 {digest['distinct_classes']} 个班级、"
            f"{digest['distinct_rooms']} 间教室"
            + (f"，日期从 {span[0]} 到 {span[1]}。" if span else "。")
        )
        if metrics:
            by_dimension = metrics.get("hard_conflicts_by_dimension") or {}
            explanation.append(
                f"独立重算的硬冲突为 {metrics.get('hard_conflicts', 0)} 条"
                f"（教室 {by_dimension.get('room', 0)}、班级 {by_dimension.get('class', 0)}、"
                f"教师 {by_dimension.get('teacher', 0)}、"
                f"日程账号 {by_dimension.get('calendar', 0)}）。"
            )
        if metrics.get("changed_assignments"):
            explanation.append(f"与上一版课表相比改动了 {metrics['changed_assignments']} 个课次。")
    # 偏好记忆的使用情况永远要有一条：编译资格、本次范围匹配、结果证据三段
    # 分开说（headline 已按口径拼好），或者编译失败显式提示。
    if memory_usage.get("status") == "compile_failed":
        line = "本次未使用偏好记忆：编译失败"
        detail = memory_usage.get("detail")
        if detail:
            line += f"（{detail}）"
        explanation.append(line + "。求解本身未受影响，修复记忆后重新求解即可带上偏好。")
    elif (memory_usage.get("compilation") or {}).get("considered"):
        explanation.append((memory_usage.get("headline") or memory_headline(memory_usage)) + "。")
        for item in memory_usage.get("outcomes") or []:
            if item.get("outcome") != "applied":
                explanation.append(
                    f"未编译：{item.get('subject_type')} {item.get('subject_id')} "
                    f"{item.get('predicate')}——{item.get('detail')}"
                )
    for rule in conflicts["rules"]:
        prefix = "内建约束" if rule["origin"] == "solver_builtin" else "教务规则"
        explanation.append(f"{prefix} {rule['business_id']}：{rule['meaning']}")
    return {
        "headline": headline,
        "explanation": explanation,
        "next_actions": _next_actions(facts),
        "suggested_instruction": build_retry_instruction(facts),
        "intent_review": None,
        "source": "deterministic",
    }


def _retry_date_range(
    scope: dict[str, Any], group: dict[str, Any], candidates: dict[str, Any], span_days: int
) -> tuple[str | None, str | None]:
    """建议指令里的日期区间：从重跑批次的首日起，最多截取 span_days 天。

    重跑要想比这次更容易排出来，唯一可靠的办法是让课次变少；日期区间是教务最好理解、
    也最容易在指令里表达的那把尺子。
    """
    fallback_range = candidates.get("lesson_date_range") or []
    start = (
        _as_date(group.get("date_from"))
        or _as_date(scope.get("date_from"))
        or _as_date(fallback_range[0] if fallback_range else None)
    )
    if start is None:
        return None, None
    end = (
        _as_date(group.get("date_to"))
        or _as_date(scope.get("date_to"))
        or _as_date(fallback_range[-1] if fallback_range else None)
        or start
    )
    if end < start:
        end = start
    return start.isoformat(), min(end, start + timedelta(days=span_days - 1)).isoformat()


def _retry_scope_clause(scope: dict[str, Any], candidates: dict[str, Any], span_days: int) -> str:
    group = candidates.get("retry_group") or {}
    named = [
        f"{label}「{group[key]}」"
        for key, label in (
            ("business_line", "业务线"),
            ("product_type", "产品班型"),
            ("class_business_id", "班级"),
        )
        if group.get(key)
    ]
    head = "只排" + "、".join(named) if named else "先把排课范围缩小到单个班级"
    date_from, date_to = _retry_date_range(scope, group, candidates, span_days)
    if date_from and date_to:
        return f"{head}，日期范围 {date_from} 到 {date_to}"
    return head


def build_retry_instruction(facts: dict[str, Any]) -> str | None:
    """排不出来时，给「一句话排课」输入框的下一条指令草稿。

    教务真正需要的不是「请缩小范围」这种泛泛建议，而是一句能直接粘回输入框重跑的话。
    这里只用事实包里已有的实体和数字拼，措辞对齐 `SOLVER_RULE_LABELS`，
    保证 `/assistant/interpret` 能把它解析回同一组硬约束；求解成功时返回 None。

    只认 INFEASIBLE / UNKNOWN 两种状态。`schedule.metrics.hard_conflicts` 统计的是整版
    课表，按班级或按周分批求解时，剩下那些没参与本次求解的课次照样计进去——拿它当触发条件，
    会给一次明明排成功的求解挂上「建议改指令」，属于误报。
    """
    status = facts["run"]["model_status"]
    if status not in {"INFEASIBLE", "UNKNOWN"}:
        return None
    scope = facts["request_scope"]
    candidates = facts.get("scope_candidates") or {}
    current_window = int(scope.get("date_window_days") or 0)
    if status == "UNKNOWN":
        # 超时不是约束太紧，是搜索空间太大：范围要更小，窗口反而不该再放大。
        span_days, window = 7, min(MAX_DATE_WINDOW_DAYS, current_window or 7)
    else:
        span_days = 14
        window = min(MAX_DATE_WINDOW_DAYS, max(current_window * 2, current_window + 7))
    clauses = [
        _retry_scope_clause(scope, candidates, span_days),
        f"日期调整窗口设为 {window} 天",
    ]
    labels = [
        SOLVER_RULE_LABELS[key]
        for key in scope.get("solver_rules") or []
        if key in SOLVER_RULE_LABELS
    ]
    if labels:
        clauses.append("继续遵守" + "、".join(labels))
    return "；".join(clauses) + "。"


def _next_actions(facts: dict[str, Any]) -> list[str]:
    run = facts["run"]
    scope = facts["request_scope"]
    actions: list[str] = []
    if run["model_status"] == "INFEASIBLE":
        if run["presolve_infeasible"]:
            actions.append(
                "先缩小求解范围：真实数据整表求解会在鸽笼预检阶段直接判无解，"
                "按班级或按日期范围分批求解才有意义。"
            )
        if not scope.get("date_window_days"):
            actions.append("把「日期调整窗口」调大到 1 天以上，让求解器有腾挪空间。")
        if facts["conflicts"]["rule_ids"]:
            actions.append("逐条检查上面列出的冲突规则，把其中不必要的硬约束改成软约束或停用。")
    elif run["model_status"] == "UNKNOWN":
        actions.append("调大求解时限，或按班级、按周把范围拆小后重跑。")
    return actions
