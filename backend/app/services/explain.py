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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CourseSession, Rule, ScheduleVersion, SolverRun

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


def describe_conflict_rule(db: Session, business_id: str) -> dict[str, Any]:
    """把一个冲突规则标识翻译成业务口径。

    教务录入的规则读它自己的原文；SYSTEM-* 走内建词表。
    """
    if business_id in SYSTEM_RULE_GLOSSARY:
        return {
            "business_id": business_id,
            "origin": "solver_builtin",
            "meaning": SYSTEM_RULE_GLOSSARY[business_id],
        }
    rule = db.scalar(select(Rule).where(Rule.business_id == business_id))
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


def _scope_candidates(db: Session, scope: dict[str, Any]) -> dict[str, Any]:
    """本次求解范围内真实存在的业务线 / 产品班型 / 班级，以及最小的一组课次。

    建议指令要能被 `/assistant/interpret` 原样解析，而那个接口只接受库里存在的业务
    实体（`_validated_assistant_scope` 对未知实体直接 422）。所以候选值一律从课次表
    取，拼出来的指令里的班级、产品班型必然落在合法集合内，而不是模型编出来的名字。
    """
    conditions = []
    if scope.get("business_lines"):
        conditions.append(CourseSession.business_line.in_(scope["business_lines"]))
    if scope.get("product_types"):
        conditions.append(CourseSession.product_type.in_(scope["product_types"]))
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
    rows = db.execute(
        select(
            CourseSession.business_line,
            CourseSession.product_type,
            CourseSession.class_business_id,
            func.count(CourseSession.id),
            func.min(CourseSession.lesson_date),
            func.max(CourseSession.lesson_date),
        )
        .where(*conditions)
        .group_by(
            CourseSession.business_line,
            CourseSession.product_type,
            CourseSession.class_business_id,
        )
    ).all()
    groups = [
        {
            "business_line": row[0] or "",
            "product_type": row[1] or "",
            "class_business_id": row[2] or "",
            "session_count": int(row[3] or 0),
            "date_from": _iso(row[4]),
            "date_to": _iso(row[5]),
        }
        for row in rows
    ]
    dates = sorted(
        value for group in groups for value in (group["date_from"], group["date_to"]) if value
    )
    # 重跑最容易排得出来的是课次最少的那一组；同数按班级、班型定序，保证同一份数据
    # 每次给出同一条建议。全是零星单节课时退回最大的一组，免得建议一个只有一节课的批次。
    ordered = sorted(
        groups,
        key=lambda item: (
            item["session_count"],
            item["class_business_id"],
            item["product_type"],
        ),
    )
    qualified = [item for item in ordered if item["session_count"] >= MIN_RETRY_GROUP_SESSIONS]
    retry_group = next(iter(qualified or ordered[-1:]), None)
    return {
        "session_count": sum(group["session_count"] for group in groups),
        "group_count": len(groups),
        "business_lines": sorted(
            {item["business_line"] for item in groups if item["business_line"]}
        )[:12],
        "product_types": sorted(
            {item["product_type"] for item in groups if item["product_type"]}
        )[:12],
        "class_business_ids": sorted(
            {item["class_business_id"] for item in groups if item["class_business_id"]}
        )[:12],
        "lesson_date_range": [dates[0], dates[-1]] if dates else None,
        "retry_group": retry_group,
    }


def build_explanation_facts(db: Session, run: SolverRun) -> dict[str, Any]:
    """整理一次求解的确定性事实包。这里出现的每个数字都来自代码，不来自模型。"""
    result = dict(run.result_payload or {})
    request = dict(run.request_payload or {})
    assignments = list(result.get("assignments") or [])
    schedule = db.scalar(select(ScheduleVersion).where(ScheduleVersion.solver_run_id == run.id))
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
        "scope_candidates": _scope_candidates(db, request_scope),
        "conflicts": {
            "rule_ids": list(run.conflict_rule_ids or []),
            "rules": [
                describe_conflict_rule(db, business_id)
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
        "active_rules": list(
            db.scalars(
                select(Rule.business_id).where(Rule.status == "active").order_by(Rule.business_id)
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


def _retry_scope_clause(
    scope: dict[str, Any], candidates: dict[str, Any], span_days: int
) -> str:
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
