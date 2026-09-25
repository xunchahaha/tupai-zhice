"""第七轮复审收口（问题 1）：解释层的方案隔离与历史快照回归测试。

复审认定的三处读现库缺陷与后果：

1. describe_conflict_rule 只按 Rule.business_id 查询且读当前行——多方案下会把
   方案乙的同名规则解释进方案甲的任务；规则被改后重新解释旧任务会用改后的
   原文解释改前的结果。
2. _scope_candidates 的课次查询缺 schedule_set_id 条件——重试候选混入其它
   方案的同名班级/课次，建议指令可能指向当前方案不存在的实体。
3. build_explanation_facts 的 active_rules 查全库 active 规则——把别的方案的
   规则当成本次求解的输入。

修复口径：解释事实优先取 run 对应 DataSnapshot 的冻结规则与冻结课次；回退
现库时所有查询强制按 run.schedule_set_id 过滤。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Campus,
    CourseSession,
    DataSnapshot,
    Room,
    Rule,
    SolverRun,
    Teacher,
    TimeSlot,
)
from app.services.explain import build_explanation_facts


def _make_scope(client: TestClient, auth_headers: dict[str, str], name: str) -> dict[str, Any]:
    """独立课表方案：最小主数据（1 教室 / 1 个人教师 / 1 个时段）。"""
    created = client.post(
        "/api/v1/schedule-sets",
        headers=auth_headers,
        json={"name": name},
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name=f"{name}校区")
        db.add(campus)
        db.flush()
        db.add_all(
            [
                Room(schedule_set_id=scope_id, campus_id=campus.id, business_id="R1", name="教室1"),
                Teacher(
                    schedule_set_id=scope_id, campus_id=campus.id, business_id="T9", name="教师九"
                ),
                TimeSlot(
                    schedule_set_id=scope_id,
                    campus_id=campus.id,
                    business_id="S1",
                    weekday="周一",
                    start_time="08:30",
                    end_time="11:30",
                ),
            ]
        )
        db.commit()
    return {"headers": {**auth_headers, "X-Schedule-Set-Id": scope_id}, "scope_id": scope_id}


def _add_course(
    scope_id: str,
    business_id: str,
    *,
    class_business_id: str = "B1",
    teacher_business_id: str = "T9",
    lesson_date: date | None = None,
) -> CourseSession:
    with SessionLocal() as db:
        campus_id = db.scalar(select(Campus.id).where(Campus.schedule_set_id == scope_id))
        assert campus_id is not None
        course = CourseSession(
            schedule_set_id=scope_id,
            campus_id=campus_id,
            business_id=business_id,
            class_business_id=class_business_id,
            teacher_business_id=teacher_business_id,
            lesson_date=lesson_date,
            fixed_start_time="08:30",
            fixed_end_time="11:30",
        )
        db.add(course)
        db.commit()
        db.refresh(course)
        return course


def _add_rule(scope_id: str, business_id: str, source_text: str) -> Rule:
    with SessionLocal() as db:
        rule = Rule(
            schedule_set_id=scope_id,
            business_id=business_id,
            source_text=source_text,
            constraint_type="forbidden_slot",
            scope={"slot_ids": ["S1"]},
            hardness="hard",
            status="active",
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule


def _frozen_rule(rule: Rule) -> dict[str, Any]:
    """模拟 build_snapshot_payload 冻结的规则形状（JSON 化字段）。"""
    return {
        "id": rule.id,
        "business_id": rule.business_id,
        "source_text": rule.source_text,
        "actor_type": rule.actor_type,
        "actor_ids": list(rule.actor_ids or []),
        "constraint_type": rule.constraint_type,
        "scope": dict(rule.scope or {}),
        "hardness": rule.hardness,
        "weight": rule.weight,
        "structured_expression": dict(rule.structured_expression or {}),
        "source_doc": rule.source_doc,
        "confidence": rule.confidence,
        "version": rule.version,
    }


def _frozen_session(course: CourseSession) -> dict[str, Any]:
    """模拟 build_snapshot_payload 冻结的课次形状（lesson_date 是 ISO 字符串）。"""
    return {
        "id": course.id,
        "business_id": course.business_id,
        "business_line": course.business_line,
        "product_type": course.product_type,
        "product_types": list(course.product_types or []),
        "class_business_id": course.class_business_id,
        "teacher_business_id": course.teacher_business_id,
        "teacher_business_ids": list(course.teacher_business_ids or []),
        "lesson_date": course.lesson_date.isoformat() if course.lesson_date else None,
    }


def _make_run(
    scope_id: str,
    *,
    payload: dict[str, Any],
    conflict_rule_ids: list[str] | None = None,
) -> SolverRun:
    """直接落一条 completed 的 SolverRun（不跑 CP-SAT，只喂解释层）。"""
    with SessionLocal() as db:
        snapshot = DataSnapshot(
            schedule_set_id=scope_id,
            revision=1,
            checksum=f"explain-iso-{uuid4().hex[:8]}",
            payload=payload,
        )
        db.add(snapshot)
        db.flush()
        run = SolverRun(
            schedule_set_id=scope_id,
            snapshot_id=snapshot.id,
            run_type="initial",
            status="completed",
            model_status="INFEASIBLE",
            request_payload={},
            result_payload={"assignments": []},
            conflict_rule_ids=conflict_rule_ids or [],
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run


def test_explanation_facts_stay_within_own_schedule_set(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """两套方案并存：甲的解释与重试候选不得含乙的任何课程/规则。

    乙故意使用与甲相同的班级标识（B-甲）和相同 business_id 的规则（R-SHARED，
    原文不同）——正是复审指出的「按 business_id/无方案条件查询」会串台的形态。
    """
    jia = _make_scope(client, auth_headers, f"解释隔离甲-{uuid4().hex[:6]}")
    yi = _make_scope(client, auth_headers, f"解释隔离乙-{uuid4().hex[:6]}")
    course_jia = _add_course(
        jia["scope_id"], "C-A1", class_business_id="B-甲", lesson_date=date(2026, 10, 5)
    )
    rule_shared = _add_rule(jia["scope_id"], "R-SHARED", "甲的共享规则原文")
    _add_rule(jia["scope_id"], "R-JIA-ONLY", "甲独有的规则")
    _add_course(yi["scope_id"], "C-B1", class_business_id="B-甲", lesson_date=date(2026, 10, 6))
    _add_rule(yi["scope_id"], "R-SHARED", "乙的共享规则原文")
    _add_rule(yi["scope_id"], "R-YI-ONLY", "乙独有的规则")

    # 路径一：快照冻结路径（payload 带 rules/course_sessions）。
    run = _make_run(
        jia["scope_id"],
        payload={
            "rules": [
                _frozen_rule(rule_shared),
                {"business_id": "R-JIA-ONLY", "source_text": "甲独有的规则"},
            ],
            "course_sessions": [_frozen_session(course_jia)],
        },
        conflict_rule_ids=["R-SHARED"],
    )

    with SessionLocal() as db:
        facts = build_explanation_facts(db, db.get(SolverRun, run.id))
    conflicts = facts["conflicts"]["rules"]
    assert conflicts[0]["meaning"] == "甲的共享规则原文"
    assert conflicts[0]["source"] == "snapshot"
    assert facts["active_rules"] == ["R-JIA-ONLY", "R-SHARED"]
    candidates = facts["scope_candidates"]
    assert candidates["origin"] == "snapshot"
    assert candidates["session_count"] == 1
    assert candidates["class_business_ids"] == ["B-甲"]
    assert candidates["retry_group"] is not None
    assert candidates["retry_group"]["session_count"] == 1

    # 路径二：现库回退路径（payload 无 rules/course_sessions 键）——所有查询
    # 强制按 run.schedule_set_id 过滤，乙的同名班级与规则同样不得混入。
    run2 = _make_run(jia["scope_id"], payload={}, conflict_rule_ids=["R-SHARED"])
    with SessionLocal() as db:
        facts2 = build_explanation_facts(db, db.get(SolverRun, run2.id))
    conflicts2 = facts2["conflicts"]["rules"]
    assert conflicts2[0]["meaning"] == "甲的共享规则原文"
    assert conflicts2[0]["source"] == "current_db"
    assert facts2["active_rules"] == ["R-JIA-ONLY", "R-SHARED"]
    candidates2 = facts2["scope_candidates"]
    assert candidates2["origin"] == "current_db"
    assert candidates2["session_count"] == 1
    assert candidates2["class_business_ids"] == ["B-甲"]
    assert candidates2["retry_group"]["session_count"] == 1
    assert "R-YI-ONLY" not in facts2["active_rules"]


def test_reexplain_old_run_uses_snapshot_rule_text(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """修改正式规则后重新解释旧任务：展示的仍是旧快照里的规则原文（含硬度）。"""
    scope = _make_scope(client, auth_headers, f"解释隔离快照-{uuid4().hex[:6]}")
    _add_course(scope["scope_id"], "C1", class_business_id="B1", lesson_date=date(2026, 10, 5))
    rule = _add_rule(scope["scope_id"], "R-1", "v1 原文：周三不排 B1")
    run = _make_run(
        scope["scope_id"],
        payload={"rules": [_frozen_rule(rule)], "course_sessions": []},
        conflict_rule_ids=["R-1"],
    )
    # 求解之后教务改了规则原文与硬度：旧任务的解释不得跟着变。
    with SessionLocal() as db:
        row = db.get(Rule, rule.id)
        assert row is not None
        row.source_text = "v2 改后：周四不排 B1"
        row.hardness = "soft"
        db.commit()

    with SessionLocal() as db:
        facts = build_explanation_facts(db, db.get(SolverRun, run.id))
    conflict = facts["conflicts"]["rules"][0]
    assert conflict["meaning"] == "v1 原文：周三不排 B1"
    assert conflict["hardness"] == "hard"
    assert conflict["source"] == "snapshot"
    # active_rules 同样取冻结全集，改规则不影响「本次求解输入是什么」的答复。
    assert facts["active_rules"] == ["R-1"]
