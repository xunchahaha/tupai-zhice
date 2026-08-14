"""求解结果的解释层。

分工是刻意的：**事实由代码算，措辞由模型写**。

- 本模块把一次求解的结论整理成确定性的事实包（`build_explanation_facts`），
  并给出一份不依赖任何模型的兜底解释（`deterministic_summary`）。
- AI 只负责把事实包翻译成教务读得懂的话，以及做一次意图核对。
  它不判断「排得对不对」——硬冲突由 `tasks.count_hard_conflicts` 确定性重算，
  再让模型复核一个确定性组件的输出，只会把可靠性往下拉。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Rule, ScheduleVersion, SolverRun

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
        "request_scope": {
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
        },
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
        "intent_review": None,
        "source": "deterministic",
    }


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
