"""任务上下文主线（TC-1..TC-5，docs/roadmap/07-task-context.md）回归测试。

覆盖批次：
1. TC-1 解析契约扩展：task_constraints 逐条校验降级（坏一条不废整次解析）、
   hard 带参项经 draft 生成器进 goal_checklist_draft（§2.1b 写入者）、soft 不进
   清单、提示词声明新字段与显式词表；
2. TC-2 记忆动作：explicit 三条复核通过直接执行（复用生命周期函数 + 原话回链）、
   推测降级 probation 候选、id 幻觉拒绝执行、Aily 通道强制 inferred、显式词表
   逐条锁定 e2e 场景原话（§7.2 耦合声明）；
3. TC-3 任务约束编译：_compile_task_constraints 双来源 business_id/source_doc
   规则、请求来源缺 id 序号兜底、同键去重保留清单侧、unsupported 422 服务端
   拦截、执行侧 _merge_frozen_extras 合并（不触发 request_payload 覆盖陷阱）、
   解释层 frozen_rules 透出 source_doc；
4. TC-4 持久上下文：SolveGoal.context 两写入点（创建求解合并 scope/soft 约束 +
   审计 update_context；run completed 回写 work_draft 指针）、GoalResponse 暴露、
   旧目标 NULL 惰性初始化；
5. TC-5 基准三级选择：explicit_parent / goal_work_draft（仍为 draft）/
   latest_published 回退。

双会话交错沿用 tests/test_goals_correctness.py 的构造方式；解析契约用固定响应
（monkeypatch app.api.AIService.interpret_instruction，同 tests/test_ai.py 模式）。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api as api_module
import app.services.tasks as tasks_module
from app.api import settings
from app.db import SessionLocal
from app.models import (
    AuditLog,
    DataSnapshot,
    PreferenceEntry,
    ScheduleVersion,
    SolveGoal,
    SolverRun,
)
from app.schemas import AssistantInterpretRequest
from app.services.task_context import (
    EXPLICIT_CORRECT_WORDS,
    EXPLICIT_EXPIRE_WORDS,
    EXPLICIT_SAVE_WORDS,
    explicit_word_hits,
)
from app.services.tasks import _merge_frozen_extras

# ---------------------------------------------------------------- 工具构造


def _configure_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "ai_api_key", "environment-ai-key")
    monkeypatch.setattr(settings, "ai_model", "scheduling-model")


def _interpret_output(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "business_lines": [],
        "product_types": [],
        "class_business_ids": [],
        "date_from": None,
        "date_to": None,
        "date_window_days": 7,
        "recognized_rules": [],
    }
    base.update(overrides)
    return base


def _mock_interpret(monkeypatch: pytest.MonkeyPatch, output: dict[str, Any]) -> None:
    _configure_ai(monkeypatch)
    monkeypatch.setattr(
        "app.api.AIService.interpret_instruction",
        lambda *args, **kwargs: (output, None),
    )


def _get_preference(
    client: TestClient, auth_headers: dict[str, str], entry_id: str
) -> dict[str, Any]:
    listed = client.get("/api/v1/memory/preferences", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    return next(item for item in listed.json() if item["id"] == entry_id)


def _make_goal(
    scope_id: str = "default",
    *,
    instruction: str = "重排 B01 班的课",
    checklist: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> SolveGoal:
    with SessionLocal() as db:
        goal = SolveGoal(
            schedule_set_id=scope_id,
            instruction=instruction,
            checklist=checklist or [],
            context=context,
        )
        db.add(goal)
        db.commit()
        db.refresh(goal)
        return goal


def _audit_records(action: str, resource_id: str) -> list[AuditLog]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(AuditLog).where(
                    AuditLog.action == action, AuditLog.resource_id == resource_id
                )
            ).all()
        )


# ------------------------------------------------- TC-1 解析契约扩展


def test_interpret_task_constraints_validated_deduped_and_degraded(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-1 §2.2：未知主体/时段逐条降级进 unsupported，同键去重，缺 id 兜底。"""
    _mock_interpret(
        monkeypatch,
        _interpret_output(
            class_business_ids=["B01"],
            task_constraints=[
                # 有效 hard：带 id。
                {
                    "id": "tc-1",
                    "source_text": "张老师周三晚上不能上",
                    "subject_type": "teacher",
                    "subject_ids": ["T01"],
                    "slot_business_ids": ["S05", "S06"],
                    "hardness": "hard",
                },
                # 有效 soft：缺 id → 按序号兜底 tc-2。
                {
                    "source_text": "乙老师周三晚尽量别排",
                    "subject_type": "teacher",
                    "subject_ids": ["T02"],
                    "slot_business_ids": ["S05"],
                    "hardness": "soft",
                },
                # 主体未知 → 降级。
                {
                    "id": "tc-3",
                    "source_text": "不存在的老师不能上",
                    "subject_ids": ["T_NOPE"],
                    "slot_business_ids": ["S05"],
                },
                # 时段未知 → 降级。
                {
                    "id": "tc-4",
                    "source_text": "张老师避开不存在时段",
                    "subject_ids": ["T01"],
                    "slot_business_ids": ["S_NOPE"],
                },
                # 与 tc-1 同键 → 去重保留一条。
                {
                    "id": "tc-5",
                    "source_text": "张老师周三晚上不能上（重复）",
                    "subject_ids": ["T01"],
                    "slot_business_ids": ["S05", "S06"],
                },
            ],
        ),
    )
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": "重排 B01，张老师周三晚上不能上"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    constraints = payload["task_constraints"]
    assert [item["id"] for item in constraints] == ["tc-1", "tc-2"]
    assert constraints[0]["hardness"] == "hard"
    assert constraints[1]["id"] == "tc-2"  # 缺失 id 按序号兜底（§2.4）
    # 未知主体/时段的两条原文进了 unsupported（去重写回路径）。
    assert "不存在的老师不能上" in payload["unsupported_requirements"]
    assert "张老师避开不存在时段" in payload["unsupported_requirements"]
    assert any("因主体/时段无法确认" in warning for warning in payload["coverage_warnings"])
    # §2.1b：hard 带参项由 draft 生成器写入 goal_checklist_draft，
    # params.task_constraint_id 回溯解析侧 constraint id。
    forbidden = [
        item for item in payload["goal_checklist_draft"] if item["kind"] == "forbidden_slot_free"
    ]
    with_params = [item for item in forbidden if not item["params"].get("needs_params")]
    assert len(with_params) == 1
    params = with_params[0]["params"]
    assert params["subject_ids"] == ["T01"]
    assert params["slot_business_ids"] == ["S05", "S06"]
    assert params["task_constraint_id"] == "tc-1"
    # 另两条被降级剔除的要求（主体/时段无法确认）没有可验收的参数，但也不能凭空消失：
    # 清单里留一条待量化占位项，登记目标后到「目标跟踪」补参。
    placeholders = [item for item in forbidden if item["params"].get("needs_params")]
    assert len(placeholders) == 1
    assert len(forbidden) == 2
    # soft 约束不进清单（无验收意义），仍留在 task_constraints 随请求体回传。
    assert all(item["params"]["subject_ids"] != ["T02"] for item in forbidden)


_FLAGSHIP_INSTRUCTION = (
    "把下周 A 班重新排一下。张老师周三晚上不能上，尽量少动其他课程，先给我草稿。"
)
_LEGACY_TEACHER_LABEL = "具体教师的禁排或请假要求"


def _teacher_constraint(source_text: str, **overrides: Any) -> dict[str, Any]:
    return {
        "id": "tc-1",
        "source_text": source_text,
        "subject_type": "teacher",
        "subject_ids": ["T01"],
        "slot_business_ids": ["S05", "S06"],
        "hardness": "hard",
        **overrides,
    }


def _interpret_unsupported(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    instruction: str,
    **output_overrides: Any,
) -> dict[str, Any]:
    _mock_interpret(
        monkeypatch, _interpret_output(class_business_ids=["B01"], **output_overrides)
    )
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": instruction},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_interpret_flagship_sentence_is_not_blocked_by_legacy_teacher_label(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """核心示例句：教师禁排已被结构化为 task_constraints 就不再算「未进入求解」。

    前端在 unsupported_requirements 非空时禁用「确认并开始求解」——旧的教师禁排
    正则对这句话无条件追加标签，会让已经结构化成功的要求把自己拦在求解之外；
    模型按提示词把同一句原话又列进 unsupported_requirements 时同理。
    """
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        _FLAGSHIP_INSTRUCTION,
        task_constraints=[_teacher_constraint("张老师周三晚上不能上")],
        unsupported_requirements=["张老师周三晚上不能上"],
    )
    assert payload["unsupported_requirements"] == []
    assert [item["id"] for item in payload["task_constraints"]] == ["tc-1"]


def test_interpret_partial_teacher_coverage_still_blocks_uncovered_restriction(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """只结构化了张老师、李老师请假没有落到任何通道 → 旧标签保留（不许悄悄放行）。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        "重排 B01，张老师周三晚上不能上，李老师下周请假",
        task_constraints=[_teacher_constraint("张老师周三晚上不能上")],
    )
    assert _LEGACY_TEACHER_LABEL in payload["unsupported_requirements"]


def test_interpret_paraphrased_source_text_keeps_legacy_label(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """source_text 不是原话摘录（无法证明覆盖了哪句）→ 保守保留标签而不是放行。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        "重排 B01，张老师周三晚上不能上",
        task_constraints=[_teacher_constraint("教师T01周三晚间禁排")],
    )
    assert _LEGACY_TEACHER_LABEL in payload["unsupported_requirements"]


def test_interpret_rejected_constraint_text_still_listed_as_unsupported(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """被降级剔除的约束不算覆盖：原话仍在 unsupported，旧标签同样保留。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        "重排 B01，张老师周三晚上不能上",
        task_constraints=[
            _teacher_constraint("张老师周三晚上不能上", subject_ids=["T_NOPE"]),
        ],
    )
    assert "张老师周三晚上不能上" in payload["unsupported_requirements"]
    assert _LEGACY_TEACHER_LABEL in payload["unsupported_requirements"]


def test_interpret_executed_memory_action_covers_teacher_restriction_wording(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式「记住」已直接落成长期偏好并回执 → 同一句话不再被旧标签拦住求解。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        "记住，这学期张老师周三晚都不排课，另外重排 B01",
        memory_actions=[
            {
                "action": "save_preference",
                "basis": "explicit",
                "source_text": "记住，这学期张老师周三晚都不排课",
                "subject_type": "teacher",
                "subject_id": "T01",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S05", "S06"]},
            }
        ],
    )
    assert payload["memory_action_receipts"][0]["status"] == "executed"
    assert payload["unsupported_requirements"] == []


def test_interpret_failed_memory_action_does_not_cover_restriction_wording(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """记忆动作没能落地（回执 failed_degraded）不算覆盖，旧标签保留。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        "旧的张老师周三晚不排课偏好不要用了",
        memory_actions=[
            {
                "action": "expire_preference",
                "basis": "explicit",
                "source_text": "旧的张老师周三晚不排课偏好不要用了",
                "target_entry_id": "not-an-injected-id",
            }
        ],
    )
    assert payload["memory_action_receipts"][0]["status"] == "failed_degraded"
    assert _LEGACY_TEACHER_LABEL in payload["unsupported_requirements"]


def test_interpret_pending_memory_candidate_does_not_cover_restriction_wording(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """待确认候选未授权、不进本次求解 → 不算覆盖：本次禁排被误判成推测偏好时不放行。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        "张老师好像不太愿意上晚上",
        memory_actions=[
            {
                "action": "save_preference",
                "basis": "inferred",
                "source_text": "张老师好像不太愿意上晚上",
                "subject_type": "teacher",
                "subject_id": "T01",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S05", "S06"]},
            }
        ],
    )
    assert payload["memory_action_receipts"][0]["status"] == "pending_confirmation"
    assert _LEGACY_TEACHER_LABEL in payload["unsupported_requirements"]


def test_interpret_structured_soft_constraint_does_not_leave_placeholder_item(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """软约束已结构化（随请求体进求解）→ 清单里不再有永远验收不过的待量化占位项。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        "重排 B01，乙老师周三晚尽量避开",
        task_constraints=[
            _teacher_constraint(
                "乙老师周三晚尽量避开",
                id="tc-soft",
                subject_ids=["T02"],
                slot_business_ids=["S05"],
                hardness="soft",
            )
        ],
    )
    assert payload["unsupported_requirements"] == []
    assert not [
        item for item in payload["goal_checklist_draft"] if item["kind"] == "forbidden_slot_free"
    ]
    assert payload["checklist_warnings"] == []


def test_interpret_uncovered_forbidden_cue_beside_structured_constraint_keeps_placeholder(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一句话里另有没被消化的禁排语 → 占位项兜底不因别的约束成功而消失。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        "重排 B01，乙老师周三晚尽量避开，B01 周五晚不排",
        task_constraints=[
            _teacher_constraint(
                "乙老师周三晚尽量避开",
                id="tc-soft",
                subject_ids=["T02"],
                slot_business_ids=["S05"],
                hardness="soft",
            )
        ],
    )
    placeholders = [
        item
        for item in payload["goal_checklist_draft"]
        if item["kind"] == "forbidden_slot_free" and item["params"].get("needs_params")
    ]
    assert len(placeholders) == 1


def _placeholder_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in payload["goal_checklist_draft"]
        if item["kind"] == "forbidden_slot_free" and item["params"].get("needs_params")
    ]


def test_interpret_unresolved_teacher_restriction_leaves_placeholder_without_cue_word(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """「教师甲周三晚上不能上」没有命中任何禁排字面词、模型又没给结构化产物：

    要求既进不了求解也验收不了——清单必须留一条待量化占位项，登记目标后才能在
    「目标跟踪」补参；否则这条要求登记后就凭空消失，验收也不会提醒。
    """
    payload = _interpret_unsupported(
        client, auth_headers, monkeypatch, "重排 B01，教师甲周三晚上不能上"
    )
    assert _LEGACY_TEACHER_LABEL in payload["unsupported_requirements"]
    placeholders = _placeholder_items(payload)
    assert len(placeholders) == 1
    assert placeholders[0]["params"]["subject_ids"] == []
    assert any("待量化项" in warning for warning in payload["checklist_warnings"])


def test_interpret_rejected_constraint_leaves_placeholder_beside_resolved_one(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一条约束通过、另一条因主体未知被剔除：通过的那条带参进清单，被剔除的留占位项。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        "重排 B01，教师甲周三晚上不能上，某某老师周五晚不能上",
        task_constraints=[
            _teacher_constraint("教师甲周三晚上不能上"),
            _teacher_constraint(
                "某某老师周五晚不能上", id="tc-2", subject_ids=["T_NOPE"], slot_business_ids=["S09"]
            ),
        ],
    )
    assert "某某老师周五晚不能上" in payload["unsupported_requirements"]
    with_params = [
        item
        for item in payload["goal_checklist_draft"]
        if item["kind"] == "forbidden_slot_free" and not item["params"].get("needs_params")
    ]
    assert [item["params"]["subject_ids"] for item in with_params] == [["T01"]]
    assert len(_placeholder_items(payload)) == 1


def test_interpret_fully_resolved_restriction_leaves_no_placeholder(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """全部结构化成功 → 不留占位项（占位项只代表「确实没落实」）。"""
    payload = _interpret_unsupported(
        client,
        auth_headers,
        monkeypatch,
        _FLAGSHIP_INSTRUCTION,
        task_constraints=[_teacher_constraint("张老师周三晚上不能上")],
    )
    assert _placeholder_items(payload) == []


_RESUMED_INSTRUCTION = "重排 B01，教师甲周三晚上不能上"


def _interpret_bound_to_goal(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    goal_id: str,
    instruction: str,
) -> dict[str, Any]:
    _mock_interpret(monkeypatch, _interpret_output(class_business_ids=["B01"]))
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": instruction, "goal_id": goal_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _forbidden_item(key: str, **params: Any) -> dict[str, Any]:
    return {
        "key": key,
        "requirement": "禁排项",
        "kind": "forbidden_slot_free",
        "params": {"subject_type": "teacher", **params},
    }


def test_resumed_goal_with_quantized_restriction_is_not_relabelled_unsupported(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """补参后回求解页重新解析同一句原话：目标里已有带全参数的禁排项 → 不再拦。

    「登记为目标，稍后补充」→ 目标跟踪补齐主体与时段 → 重新求解，用户重新解析的
    仍是「教师甲周三晚上不能上」；旧正则会再次命中，把刚补完参数的人又拦在门外。
    """
    goal = _make_goal(
        instruction=_RESUMED_INSTRUCTION,
        checklist=[
            _forbidden_item(
                "forbidden_slot_free-draft",
                subject_ids=["T01"],
                slot_business_ids=["S05", "S06"],
            )
        ],
    )
    payload = _interpret_bound_to_goal(
        client, auth_headers, monkeypatch, goal.id, _RESUMED_INSTRUCTION
    )
    assert _LEGACY_TEACHER_LABEL not in payload["unsupported_requirements"]
    assert payload["unsupported_requirements"] == []


def test_resumed_goal_only_forgives_its_own_sentence_not_newly_appended_restrictions(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """目标里补全过一条禁排，只说明「原句」落实了；用户续办时新追加的教师请假不能被顺带放行。"""
    goal = _make_goal(
        instruction=_RESUMED_INSTRUCTION,
        checklist=[
            _forbidden_item(
                "forbidden_slot_free-draft",
                subject_ids=["T01"],
                slot_business_ids=["S05", "S06"],
            )
        ],
    )
    # 原句照旧 + 用户在输入框里追加了一句新的教师请假。
    payload = _interpret_bound_to_goal(
        client, auth_headers, monkeypatch, goal.id, f"{_RESUMED_INSTRUCTION}，李老师下周请假"
    )
    assert _LEGACY_TEACHER_LABEL in payload["unsupported_requirements"]


def test_resumed_goal_with_pending_placeholder_still_blocks(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """目标里还有待补参的占位项 = 要求仍未落实：照旧拦截，不因为绑了目标就放行。"""
    goal = _make_goal(
        instruction=_RESUMED_INSTRUCTION,
        checklist=[
            _forbidden_item(
                "forbidden_slot_free-quantized",
                subject_ids=["T01"],
                slot_business_ids=["S05"],
            ),
            _forbidden_item(
                "forbidden_slot_free-draft",
                subject_ids=[],
                slot_business_ids=[],
                needs_params=True,
            ),
        ]
    )
    payload = _interpret_bound_to_goal(
        client, auth_headers, monkeypatch, goal.id, _RESUMED_INSTRUCTION
    )
    assert _LEGACY_TEACHER_LABEL in payload["unsupported_requirements"]


def test_resumed_goal_without_any_forbidden_item_still_blocks(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """绑了目标但清单里根本没有禁排项：要求没落实过，照旧拦截。"""
    goal = _make_goal(
        instruction=_RESUMED_INSTRUCTION,
        checklist=[{"key": "coverage", "requirement": "覆盖", "kind": "coverage", "params": {}}],
    )
    payload = _interpret_bound_to_goal(
        client, auth_headers, monkeypatch, goal.id, _RESUMED_INSTRUCTION
    )
    assert _LEGACY_TEACHER_LABEL in payload["unsupported_requirements"]


def test_covered_text_helpers_are_conservative() -> None:
    """扣除口径：短片段/改写不扣，标点空白差异不影响匹配，扣除处不拼接成新句。"""
    covered = api_module._instruction_without_covered
    assert covered("张老师周三晚上不能上，B01 周五晚不排", ["张老师周三晚上不能上"]) == (
        "\n，B01 周五晚不排"
    )
    # 模型摘录时补/丢了标点、空白 → 仍能对上。
    assert covered("张老师 周三晚上，不能上", ["张老师周三晚上不能上"]) == "\n"
    # 太短的片段不参与扣除（否则一个「上」字会删掉整句里所有的「上」）。
    assert covered("张老师不能上", ["不排"]) == "张老师不能上"
    # 改写过的 source_text 对不上原话 → 什么都不扣。
    assert covered("张老师周三晚上不能上", ["教师T01周三晚间禁排"]) == "张老师周三晚上不能上"
    # unsupported 条目只有完整落在已消化片段里才剔除。
    drop = api_module._drop_covered_unsupported
    assert drop(["张老师周三晚上不能上"], ["张老师周三晚上不能上，尽量别换教室"]) == []
    assert drop(["张老师周三晚上不能上，并且不要换教室"], ["张老师周三晚上不能上"]) == [
        "张老师周三晚上不能上，并且不要换教室"
    ]


def test_interpret_forbidden_cue_without_constraints_keeps_placeholder(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§2.1b 兜底不变：有禁排语但没有任何 task_constraints → needs_params 占位项。"""
    _mock_interpret(monkeypatch, _interpret_output(class_business_ids=["B01"]))
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": "重排 B01，避开周三晚上"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["task_constraints"] == []
    forbidden = [
        item for item in payload["goal_checklist_draft"] if item["kind"] == "forbidden_slot_free"
    ]
    assert len(forbidden) == 1
    assert forbidden[0]["params"]["needs_params"] is True


def test_interpret_prompt_declares_new_fields_and_word_list() -> None:
    """TC-1/TC-2：提示词声明 task_constraints/memory_actions，词表与代码同源。"""
    prompt = api_module.AIService._interpret_system_prompt({})
    assert '"task_constraints":[]' in prompt
    assert '"memory_actions":[]' in prompt
    # 词表单一事实源：提示词与代码复核共用 services.task_context 的同一组常量。
    for word in EXPLICIT_SAVE_WORDS:
        assert word in prompt
    for word in EXPLICIT_EXPIRE_WORDS:
        assert word in prompt
    for word in EXPLICIT_CORRECT_WORDS:
        assert word in prompt
    assert "active_preferences" in prompt
    # 已结构化的要求不再抄进 unsupported；source_text 逐字摘录（覆盖判定依赖它）。
    assert "不要】再抄一份进 unsupported_requirements" in prompt
    assert "逐字摘录" in prompt


# ------------------------------------------------- TC-2 记忆动作


def test_explicit_save_preference_executes_with_receipt(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-2 §3.2：explicit 复核通过直接 confirmed，回执 executed + 原话回链。"""
    _mock_interpret(
        monkeypatch,
        _interpret_output(
            memory_actions=[
                {
                    "action": "save_preference",
                    "basis": "explicit",
                    "source_text": "张老师周三晚尽量别排",
                    "subject_type": "teacher",
                    "subject_id": "T01",
                    "predicate": "avoid_slot",
                    "constraint": {"slot_ids": ["S05", "S06"]},
                    "weight": 60,
                }
            ]
        ),
    )
    rules_before = client.get("/api/v1/rules", headers=auth_headers).json()
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": "记住，这学期张老师周三晚尽量别排"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["memory_action_receipts"]) == 1
    receipt = payload["memory_action_receipts"][0]
    assert receipt["status"] == "executed"
    assert receipt["entry_id"]
    assert "已记住" in receipt["receipt"]
    assert "可在「记忆」页修改或撤销" in receipt["receipt"]

    entry = _get_preference(client, auth_headers, receipt["entry_id"])
    assert entry["status"] == "confirmed"
    assert entry["source"] == "explicit_stated"
    assert entry["predicate"] == "avoid_slot"
    assert entry["constraint"] == {"slot_ids": ["S05", "S06"]}
    assert entry["valid_until"]
    # 原话回链：provenance.via + instruction。
    assert entry["provenance"]["via"] == "assistant_interpret"
    assert "记住" in entry["provenance"]["instruction"]
    # §2.5 红线：任务约束/记忆动作不建 Rule 行。
    rules_after = client.get("/api/v1/rules", headers=auth_headers).json()
    assert len(rules_after) == len(rules_before)
    # 审计沿用既有 create 调用点。
    assert _audit_records("create", receipt["entry_id"])


def test_inferred_save_creates_probation_candidate(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-2 §3.2：推测永不直接执行 → probation 候选（未授权不进求解）。"""
    _mock_interpret(
        monkeypatch,
        _interpret_output(
            memory_actions=[
                {
                    "action": "save_preference",
                    "basis": "inferred",
                    "source_text": "张老师好像不太愿意上晚上",
                    "subject_type": "teacher",
                    "subject_id": "T01",
                    "predicate": "avoid_slot",
                    "constraint": {"slot_ids": ["S05", "S06"]},
                }
            ]
        ),
    )
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": "张老师好像不太愿意上晚上"},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["memory_action_receipts"][0]
    assert receipt["status"] == "pending_confirmation"
    assert "记忆收件箱" in receipt["receipt"]
    # 候选经收件箱可见（回执 entry_id 仅 executed 回填，按签名检索候选）。
    listed = client.get(
        "/api/v1/memory/preferences",
        headers=auth_headers,
        params={"subject_id": "T01", "status": "probation"},
    )
    assert listed.status_code == 200, listed.text
    candidates = [
        item
        for item in listed.json()
        if item["constraint"] == {"slot_ids": ["S05", "S06"]}
        and item["predicate"] == "avoid_slot"
    ]
    assert candidates, "推测动作应产生 probation 候选"
    entry = candidates[0]
    assert entry["trial_authorized"] is False
    assert entry["modality"] == "soft"
    assert entry["provenance"]["via"] == "assistant_interpret"


def test_explicit_expire_with_injected_entry_executes(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-2 §3.2/§3.4：target_entry_id 命中注入条目 + 撤销词 → 直接 expired。"""
    created = client.post(
        "/api/v1/memory/preferences",
        headers=auth_headers,
        json={
            "subject_type": "teacher",
            "subject_id": "T01",
            "predicate": "avoid_slot",
            "constraint": {"slot_ids": ["S05", "S06"]},
            "source": "explicit_stated",
        },
    )
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]
    _mock_interpret(
        monkeypatch,
        _interpret_output(
            memory_actions=[
                {
                    "action": "expire_preference",
                    "basis": "explicit",
                    "source_text": "旧的周三晚偏好不要用了",
                    "target_entry_id": entry_id,
                    "target_status": "expired",
                }
            ]
        ),
    )
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": "旧的周三晚偏好不要用了"},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["memory_action_receipts"][0]
    assert receipt["status"] == "executed"
    assert receipt["entry_id"] == entry_id
    assert _get_preference(client, auth_headers, entry_id)["status"] == "expired"
    assert _audit_records("transition", entry_id)


def test_hallucinated_target_entry_id_never_executes(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-2 §3.4：上下文里没有的 id 一律视为幻觉，拒绝执行且不产生候选。"""
    created = client.post(
        "/api/v1/memory/preferences",
        headers=auth_headers,
        json={
            "subject_type": "teacher",
            "subject_id": "T01",
            "predicate": "avoid_slot",
            "constraint": {"slot_ids": ["S05"]},
            "source": "explicit_stated",
        },
    )
    entry_id = created.json()["id"]
    _mock_interpret(
        monkeypatch,
        _interpret_output(
            memory_actions=[
                {
                    "action": "expire_preference",
                    "basis": "explicit",
                    "source_text": "旧的不要用了",
                    "target_entry_id": "hallucinated-entry-id",
                    "target_status": "expired",
                }
            ]
        ),
    )
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": "旧的不要用了"},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["memory_action_receipts"][0]
    assert receipt["status"] == "failed_degraded"
    assert "幻觉" in receipt["receipt"]
    # 目标条目原样保留，且没有半执行。
    assert _get_preference(client, auth_headers, entry_id)["status"] == "confirmed"


def test_aily_channel_forces_inferred_handling(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """TC-2 §3.2：Aily 通道输出未经我方授权判定链，一律按 inferred 处理。"""
    output = _interpret_output(
        memory_actions=[
            {
                "action": "save_preference",
                "basis": "explicit",
                "source_text": "记住，张老师周三晚尽量别排",
                "subject_type": "teacher",
                "subject_id": "T01",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S05"]},
            }
        ]
    )
    request = AssistantInterpretRequest(instruction="记住，张老师周三晚尽量别排")
    with SessionLocal() as db:
        context = api_module._interpret_context(db, "default")
        response = api_module._finalize_assistant_interpret(
            db,
            request,
            output,
            "default",
            source="feishu_aily",
            ai_configured=False,
            aily_configured=True,
            thinking=None,
            context=context,
            actor=None,
        )
        db.commit()
    assert response.memory_actions[0].basis == "inferred"
    assert response.memory_action_receipts[0].status == "pending_confirmation"
    with SessionLocal() as db:
        # 候选经签名检索（回执 entry_id 仅 executed 回填）。
        entry = db.scalar(
            select(PreferenceEntry).where(
                PreferenceEntry.schedule_set_id == "default",
                PreferenceEntry.subject_id == "T01",
                PreferenceEntry.status == "probation",
                PreferenceEntry.constraint == {"slot_ids": ["S05"]},
            )
        )
        assert entry is not None
        assert entry.trial_authorized is False
        assert entry.provenance["via"] == "assistant_interpret"


def test_explicit_word_list_locks_e2e_scenario_phrases() -> None:
    """TC-2 词表锁定（§3.1/§7.2 耦合声明）：e2e 场景原话必须命中对应类别。"""
    assert explicit_word_hits("记住，这学期张老师周三晚尽量别排").get("save")
    for word in EXPLICIT_SAVE_WORDS:
        assert explicit_word_hits(word).get("save"), word
    assert explicit_word_hits("旧的周三晚偏好不要用了").get("expire")
    for word in EXPLICIT_EXPIRE_WORDS:
        assert explicit_word_hits(word).get("expire"), word
    assert explicit_word_hits("不是长期偏好，只是那两天请假").get("correct")
    for word in EXPLICIT_CORRECT_WORDS:
        assert explicit_word_hits(word).get("correct"), word
    # 无显式声明词 → 不得命中任何类别（explicit 复核③的否决侧）。
    assert explicit_word_hits("张老师好像不太愿意上晚上") == {}


def test_explicit_save_words_are_not_hit_when_negated() -> None:
    """记录词前带否定（不是/不算/不用/别…）是在拒绝记录，不得算显式「记录」声明。

    explicit 复核③是拦住模型误判 basis 的最后一道代码闸门：「这不算长期，先别记」
    若仍命中记录类，模型一旦误出 save_preference 就会被直接执行成长期偏好。
    """
    for text in (
        "这不算长期，先别记",
        "不是长期偏好，只是那两天请假",
        "并非长期安排，临时的",
        "不用记住这个",
        "别记住，只是这周",
    ):
        assert not explicit_word_hits(text).get("save"), text
    # 纠正类不受影响：「不是长期」本身就是纠正声明。
    assert explicit_word_hits("不是长期偏好，只是那两天请假").get("correct")
    # 正向话术照常命中（含否定词出现在别处的情形）。
    for text in ("记住，这学期张老师周三晚尽量别排", "长期都别排周三晚", "以后都不要排晚课"):
        assert explicit_word_hits(text).get("save"), text


# ------------------------------------------------- TC-3 任务约束编译


def test_compile_task_constraints_goal_source_business_ids() -> None:
    """TC-3 §2.4：goal 来源 business_id/source_doc 规则（hard 清单项 + soft context）。"""
    goal = _make_goal(
        checklist=[
            {
                "key": "forbidden_slot_free-1",
                "requirement": "教师 T01 不占用指定时段（S05、S06）——独立复核",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": ["T01"],
                    "slot_business_ids": ["S05", "S06"],
                    "task_constraint_id": "tc-1",
                },
            },
            {
                "key": "forbidden_slot_free-draft",
                "requirement": "禁排要求待量化",
                "kind": "forbidden_slot_free",
                "params": {"needs_params": True},
            },
        ],
        context={
            "schema_version": 1,
            "soft_task_constraints": [
                {
                    "id": "tc-2",
                    "subject_type": "teacher",
                    "subject_ids": ["T02"],
                    "slot_business_ids": ["S05"],
                    "source_text": "乙老师周三晚尽量别排",
                }
            ],
        },
    )
    rules = api_module._compile_task_constraints(goal=goal)
    assert [rule["business_id"] for rule in rules] == [
        f"TASK-{goal.id[:8]}-forbidden_slot_free-1",
        f"TASK-{goal.id[:8]}-soft-tc-2",
    ]
    hard, soft = rules
    assert hard["source_doc"] == f"goal:{goal.id}"
    assert hard["source_text"].startswith("教师 T01")
    assert hard["actor_type"] == "teacher"
    assert hard["actor_ids"] == ["T01"]
    assert hard["constraint_type"] == "forbidden_slot"
    assert hard["scope"] == {"slot_ids": ["S05", "S06"]}
    assert hard["hardness"] == "hard"
    # needs_params 占位项不编译（参数未齐备）。
    assert all("forbidden_slot_free-draft" not in rule["business_id"] for rule in rules)
    assert soft["hardness"] == "soft"
    assert soft["weight"] == 30
    assert soft["source_doc"] == f"goal:{goal.id}"
    assert soft["source_text"] == "乙老师周三晚尽量别排"


def test_compile_task_constraints_request_source_and_dedup() -> None:
    """TC-3 §2.4：请求来源 TASK-req-* 命名空间、缺 id 序号兜底、同键保留清单侧。"""
    goal = _make_goal(
        checklist=[
            {
                "key": "forbidden_slot_free-1",
                "requirement": "教师 T01 不占用指定时段（S05）——独立复核",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": ["T01"],
                    "slot_business_ids": ["S05"],
                },
            }
        ]
    )
    request_constraints = [
        api_module.AssistantTaskConstraint(
            source_text="张老师周三不能上",
            subject_ids=["T01"],
            slot_business_ids=["S05"],  # 与清单项同键 → 去重保留清单侧
        ),
        api_module.AssistantTaskConstraint(
            id="tc-req",
            source_text="乙老师周四不能上",
            subject_ids=["T02"],
            slot_business_ids=["S07"],
            hardness="soft",
        ),
        api_module.AssistantTaskConstraint(
            source_text="丙老师周五不能上",
            subject_ids=["T03"],
            slot_business_ids=["S09"],
        ),
    ]
    rules = api_module._compile_task_constraints(goal=goal, request_constraints=request_constraints)
    business_ids = [rule["business_id"] for rule in rules]
    assert business_ids == [
        f"TASK-{goal.id[:8]}-forbidden_slot_free-1",  # goal 来源优先
        "TASK-req-tc-req",  # 显式 id
        "TASK-req-3",  # 缺 id 按列表序号兜底
    ]
    request_rules = [rule for rule in rules if rule["business_id"].startswith("TASK-req-")]
    assert all(rule["source_doc"] == "request:task_constraint" for rule in request_rules)
    assert request_rules[0]["hardness"] == "soft"
    assert request_rules[0]["weight"] == 30
    assert request_rules[1]["hardness"] == "hard"
    # 无 goal 时请求来源独立成立（无清单可去重，首条按序号兜底 TASK-req-1）。
    no_goal_rules = api_module._compile_task_constraints(
        request_constraints=request_constraints
    )
    assert [rule["business_id"] for rule in no_goal_rules] == [
        "TASK-req-1",
        "TASK-req-tc-req",
        "TASK-req-3",
    ]
    assert all(rule["source_doc"] == "request:task_constraint" for rule in no_goal_rules)


def test_assistant_solve_rejects_unsupported_requirements(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """TC-3 §2.3：服务端契约拦截——unsupported 原样带回即 422。"""
    response = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01",
            "class_business_ids": ["B01"],
            "unsupported_requirements": ["具体教师的禁排或请假要求"],
        },
    )
    assert response.status_code == 422, response.text
    assert "存在未进入求解的要求" in response.json()["detail"]
    assert "请先在确认卡处置" in response.json()["detail"]


def test_assistant_solve_rejects_unknown_task_constraint_subjects(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """TC-1 §2.1 请求侧载体的服务端把关：未知主体/时段 → 422。"""
    response = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01",
            "class_business_ids": ["B01"],
            "task_constraints": [
                {
                    "id": "tc-1",
                    "subject_ids": ["T_NOPE"],
                    "slot_business_ids": ["S05"],
                }
            ],
        },
    )
    assert response.status_code == 422, response.text
    assert "unknown_subjects" in json.dumps(response.json(), ensure_ascii=False)


def test_assistant_solve_compiles_task_constraint_rules_and_updates_context(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """TC-3/TC-4 主链路：编译进 request_payload 独立键 + context 写入点① + 审计。"""
    goal = _make_goal(
        checklist=[
            {
                "key": "forbidden_slot_free-1",
                "requirement": "教师 T01 不占用指定时段（S05、S06）——独立复核",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": ["T01"],
                    "slot_business_ids": ["S05", "S06"],
                    "task_constraint_id": "tc-1",
                },
            }
        ]
    )
    detail_before = client.get(f"/api/v1/goals/{goal.id}", headers=auth_headers)
    assert detail_before.status_code == 200
    assert detail_before.json()["context"] is None  # 旧目标 NULL = 无上下文

    response = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
            "task_constraints": [
                {
                    "id": "tc-soft-1",
                    "source_text": "乙老师周三晚尽量别排",
                    "subject_ids": ["T02"],
                    "slot_business_ids": ["S05"],
                    "hardness": "soft",
                },
                {
                    "id": "tc-req-hard",
                    "source_text": "B01 周五第一节课不能动",
                    "subject_type": "cohort",
                    "subject_ids": ["B01"],
                    "slot_business_ids": ["S09"],
                    "hardness": "hard",
                },
                # 与清单项同键 → 去重保留清单侧。
                {
                    "id": "tc-dup",
                    "subject_ids": ["T01"],
                    "slot_business_ids": ["S05", "S06"],
                },
            ],
        },
    )
    assert response.status_code == 202, response.text
    run = response.json()
    with SessionLocal() as db:
        stored = db.get(SolverRun, run["id"])
        assert stored is not None
        request_payload = dict(stored.request_payload or {})
        task_rules = request_payload.get("task_constraint_rules") or []
        business_ids = [rule["business_id"] for rule in task_rules]
        # 有关联任务时，确认过的请求约束先成为任务的新版要求、再只从任务编译：
        # 新的硬要求进了清单（forbidden_slot_free-2），软要求进了 context，都以
        # 任务来源（goal:）命名——不再有只活在这一次请求里的 TASK-req-* 规则。
        assert business_ids == [
            f"TASK-{goal.id[:8]}-forbidden_slot_free-1",  # 原清单项
            f"TASK-{goal.id[:8]}-forbidden_slot_free-2",  # 请求硬项 → 清单新增
            f"TASK-{goal.id[:8]}-soft-tc-soft-1",  # 请求软项 → context
        ]
        assert request_payload["goal_checklist_version"] == 2  # 新增硬要求升了清单版本
        assert request_payload["task_revision"] == {
            "added_hard": ["B01 周五第一节课不能动"],
            "added_soft": ["乙老师周三晚尽量别排"],
        }
        # 覆盖陷阱防线：规则不写进 request_payload["rules"]，快照 rules 不被污染。
        assert "rules" not in request_payload
        snapshot = db.get(DataSnapshot, stored.snapshot_id)
        assert snapshot is not None
        assert "task_constraint_rules" not in (snapshot.payload or {})
        # 写入点①：scope 整体覆盖 + soft 按 id 幂等合并 + schema_version。
        goal_row = db.get(SolveGoal, goal.id)
        assert goal_row is not None
        context = dict(goal_row.context or {})
        assert context["schema_version"] == 1
        assert context["scope"]["class_business_ids"] == ["B01"]
        soft_items = context["soft_task_constraints"]
        assert [item["id"] for item in soft_items] == ["tc-soft-1"]
        assert soft_items[0]["source_text"] == "乙老师周三晚尽量别排"
    # 审计：update_context 与任务约束规则 id 留痕。
    updates = _audit_records("update_context", goal.id)
    assert len(updates) == 1
    assert updates[0].detail["task_constraint_rule_ids"] == business_ids

    # 幂等：同 id 再次提交不产生重复 soft 项，scope 照常覆盖。
    second = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01（第二轮）",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
            "task_constraints": [
                {
                    "id": "tc-soft-1",
                    "source_text": "乙老师周三晚尽量别排（更新描述）",
                    "subject_ids": ["T02"],
                    "slot_business_ids": ["S05"],
                    "hardness": "soft",
                }
            ],
        },
    )
    assert second.status_code == 202, second.text
    with SessionLocal() as db:
        goal_row = db.get(SolveGoal, goal.id)
        assert goal_row is not None
        soft_items = (goal_row.context or {})["soft_task_constraints"]
        assert [item["id"] for item in soft_items] == ["tc-soft-1"]
        assert soft_items[0]["source_text"].endswith("（更新描述）")
    assert len(_audit_records("update_context", goal.id)) == 2


def test_execute_path_merges_task_rules_without_overwriting_frozen_rules(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-3 覆盖陷阱回归（tasks.py 两处 payload.update）：执行侧把
    task_constraint_rules 追加进快照冻结规则全集之后，而不是用请求键整体覆盖。"""
    goal = _make_goal(
        checklist=[
            {
                "key": "forbidden_slot_free-1",
                "requirement": "教师 T01 不占用指定时段（S05）——独立复核",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": ["T01"],
                    "slot_business_ids": ["S05"],
                },
            }
        ]
    )
    captured: dict[str, Any] = {}

    def _capture_then_stop(payload: dict[str, Any]) -> dict[str, Any]:
        captured["payload"] = payload
        raise RuntimeError("stop-capture")

    monkeypatch.setattr(tasks_module, "solve_problem", _capture_then_stop)
    with pytest.raises(RuntimeError, match="stop-capture"):
        client.post(
            "/api/v1/assistant/solve",
            headers=auth_headers,
            json={
                "instruction": "重排 B01",
                "class_business_ids": ["B01"],
                "goal_id": goal.id,
                "wait": True,
            },
        )
    monkeypatch.undo()
    payload = captured["payload"]
    business_ids = [rule["business_id"] for rule in payload["rules"]]
    # 快照冻结规则在前（可为空），任务约束规则追加在后。
    assert f"TASK-{goal.id[:8]}-forbidden_slot_free-1" in business_ids
    assert business_ids.index(f"TASK-{goal.id[:8]}-forbidden_slot_free-1") >= 0


def test_merge_frozen_extras_appends_memory_and_task_rules() -> None:
    """TC-3：_merge_frozen_extras 单元行为——显式规则 + 记忆编译产物 + 任务规则。"""
    payload: dict[str, Any] = {
        "rules": [{"business_id": "RL-1"}],
        "task_constraint_rules": [{"business_id": "TASK-abc-forbidden_slot_free-1"}],
        "memory": {
            "status": "ok",
            "compiled_rules": [{"business_id": "MEM-1", "memory_entry_id": "e1"}],
        },
    }
    _merge_frozen_extras(payload, "default", None)
    assert [rule["business_id"] for rule in payload["rules"]] == [
        "RL-1",
        "MEM-1",
        "TASK-abc-forbidden_slot_free-1",
    ]
    # compile_failed：无偏好求解但任务约束照常合并，解释层显式提示由调用方负责。
    failed: dict[str, Any] = {
        "rules": [{"business_id": "RL-1"}],
        "task_constraint_rules": [{"business_id": "TASK-x-soft-1"}],
        "memory": {"status": "compile_failed", "detail": "boom", "compiled_rules": []},
    }
    _merge_frozen_extras(failed, "default", None)
    assert [rule["business_id"] for rule in failed["rules"]] == ["RL-1", "TASK-x-soft-1"]


def test_explanation_describes_task_constraint_rule_with_source_doc(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """TC-3 解释层：TASK- 规则冲突转述取自冻结 request_payload 并透出 source_doc。"""
    goal = _make_goal()
    task_rule_id = f"TASK-{goal.id[:8]}-forbidden_slot_free-1"
    with SessionLocal() as db:
        snapshot = DataSnapshot(
            schedule_set_id="default",
            revision=1,
            checksum=f"task-ctx-{uuid4().hex[:8]}",
            payload={"rules": [{"business_id": "RL-SNAPSHOT", "source_text": "快照规则"}]},
        )
        db.add(snapshot)
        db.flush()
        run = SolverRun(
            schedule_set_id="default",
            snapshot_id=snapshot.id,
            run_type="initial",
            status="completed",
            model_status="OPTIMAL",
            request_payload={
                "task_constraint_rules": [
                    {
                        "business_id": task_rule_id,
                        "source_text": "教师 T01 不占用指定时段（S05）——独立复核",
                        "actor_type": "teacher",
                        "actor_ids": ["T01"],
                        "constraint_type": "forbidden_slot",
                        "scope": {"slot_ids": ["S05"]},
                        "hardness": "hard",
                        "weight": 100,
                        "source_doc": f"goal:{goal.id}",
                    }
                ]
            },
            goal_id=goal.id,
        )
        db.add(run)
        db.commit()
        run_id = run.id
    with SessionLocal() as db:
        run_row = db.get(SolverRun, run_id)
        assert run_row is not None
        run_row.conflict_rule_ids = [task_rule_id]
        facts = api_module.build_explanation_facts(db, run_row)
    descriptions = {rule["business_id"]: rule for rule in facts["conflicts"]["rules"]}
    task_described = descriptions[task_rule_id]
    # 「库里找不到对应规则」兜底不再出现：冻结分支接住了 TASK- 规则。
    assert task_described["origin"] == "user_rule"
    assert task_described["source_doc"] == f"goal:{goal.id}"
    assert task_described["meaning"].startswith("教师 T01")
    assert task_described["hardness"] == "hard"


def test_interpret_context_injects_goal_task_context(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """TC-4 §4.3：goal_id 有值时解析上下文注入 task_context 节；无效 goal 静默缺省。"""
    goal = _make_goal(
        checklist=[
            {
                "key": "forbidden_slot_free-1",
                "requirement": "教师 T01 不占用指定时段（S05）——独立复核",
                "kind": "forbidden_slot_free",
                "params": {
                    "subject_type": "teacher",
                    "subject_ids": ["T01"],
                    "slot_business_ids": ["S05"],
                },
            }
        ],
        context={
            "schema_version": 1,
            "scope": {"class_business_ids": ["B01"]},
            "soft_task_constraints": [
                {
                    "id": "tc-soft-1",
                    "subject_type": "teacher",
                    "subject_ids": ["T02"],
                    "slot_business_ids": ["S05"],
                    "source_text": "乙老师周三晚尽量别排",
                }
            ],
        },
    )
    with SessionLocal() as db:
        context = api_module._interpret_context(db, "default", goal.id)
        missing = api_module._interpret_context(db, "default", "not-a-goal")
        cross = api_module._interpret_context(db, "default", "cross-set-goal")
    task_context = context["task_context"]
    assert task_context["goal_id"] == goal.id
    assert task_context["instruction"] == "重排 B01 班的课"
    assert task_context["scope"] == {"class_business_ids": ["B01"]}
    kinds = [item.get("key") for item in task_context["active_task_constraints"]]
    assert "forbidden_slot_free-1" in kinds
    assert any(item.get("id") == "tc-soft-1" for item in task_context["active_task_constraints"])
    assert task_context["latest_run"]["run_id"] is None
    # 无效/跨方案 goal → 上下文缺省（解析退化为全新指令）。
    assert "task_context" not in missing
    assert "task_context" not in cross


def test_interpret_endpoint_accepts_goal_id(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-4 §4.3 契约：AssistantInterpretRequest.goal_id 进入解析请求。"""
    goal = _make_goal()
    captured: dict[str, Any] = {}

    def _fake(
        self: Any, instruction: str, *, context: dict[str, Any]
    ) -> tuple[dict[str, Any], None]:
        captured["context"] = context
        return _interpret_output(class_business_ids=["B01"]), None

    _configure_ai(monkeypatch)
    monkeypatch.setattr(api_module.AIService, "interpret_instruction", _fake)
    response = client.post(
        "/api/v1/assistant/interpret",
        headers=auth_headers,
        json={"instruction": "在既有目标上把范围扩到 B02", "goal_id": goal.id},
    )
    assert response.status_code == 200, response.text
    assert captured["context"]["task_context"]["goal_id"] == goal.id


# ------------------------------------------------- TC-4 工作草稿指针


def test_run_completion_writes_work_draft_pointer(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """TC-4 写入点②：run completed 产出草稿 → context.work_draft_schedule_id 指向草稿。"""
    goal = _make_goal()
    response = client.post(
        "/api/v1/solver-runs",
        headers=auth_headers,
        json={
            "goal_id": goal.id,
            "class_business_ids": ["B01"],
            "wait": True,
            "time_limit_seconds": 5,
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    with SessionLocal() as db:
        version = db.scalar(select(ScheduleVersion).where(ScheduleVersion.solver_run_id == run_id))
        assert version is not None
        assert version.status == "draft"
        goal_row = db.get(SolveGoal, goal.id)
        assert goal_row is not None
        context = dict(goal_row.context or {})
        assert context["work_draft_schedule_id"] == version.id
        assert context["schema_version"] == 1


# ------------------------------------------------- TC-5 基准三级选择


def _make_version(
    scope_id: str, *, status: str, version_no: int, solver_run_id: str | None = None
) -> ScheduleVersion:
    with SessionLocal() as db:
        if solver_run_id is None:
            # 始终新建自足快照（course_sessions=[]，与生产冻结契约一致——生产快照
            # 由 build_snapshot_payload 的 _model_dict 冻结，条目首字段就是 id）。
            # 不能顺手复用库里的任意旧快照：create_solver_run 选中本夹具的版本为
            # 基准时会经 schedule_response → version_course_map（snapshot.py:36
            # `item["id"]`）读该 run 的快照；全量跑时其他用例留下的快照条目可能
            # 没有 id 键（如 test_goals_correctness._make_run 的构造），复用会让
            # 本测试随快照命中情况偶发 KeyError: 'id'。不放宽 version_course_map
            # 的 id 要求——那是真实格式漂移防线，坏夹具不该让它开口子。
            snapshot = DataSnapshot(
                schedule_set_id=scope_id,
                revision=1,
                checksum=f"baseline-{uuid4().hex[:8]}",
                payload={"course_sessions": []},
            )
            db.add(snapshot)
            db.flush()
            run = SolverRun(
                schedule_set_id=scope_id,
                snapshot_id=snapshot.id,
                run_type="initial",
                status="completed",
                model_status="OPTIMAL",
                request_payload={},
            )
            db.add(run)
            db.flush()
            solver_run_id = run.id
        version = ScheduleVersion(
            schedule_set_id=scope_id,
            version_no=version_no,
            name=f"课表版本 V{version_no}",
            status=status,
            solver_run_id=solver_run_id,
            metrics={},
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version


def test_baseline_prefers_goal_work_draft_then_published(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """TC-5 §4.6：三级基准——显式 parent > goal 工作草稿（仍为 draft）> 最新已发布。"""
    published = _make_version("default", status="published", version_no=901)
    draft = _make_version("default", status="draft", version_no=902)
    goal = _make_goal(context={"schema_version": 1, "work_draft_schedule_id": draft.id})
    # ③ 无 goal → 最新已发布（现状不变）。
    run = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={"instruction": "重排 B01", "class_business_ids": ["B01"]},
    )
    assert run.status_code == 202, run.text
    with SessionLocal() as db:
        stored = db.get(SolverRun, run.json()["id"])
        assert stored is not None
        assert stored.request_payload["baseline_source"] == "latest_published"
        assert stored.request_payload["parent_schedule_id"] == published.id

    # ② goal 工作草稿（仍为 draft）→ 以它为基准。
    run = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
        },
    )
    assert run.status_code == 202, run.text
    with SessionLocal() as db:
        stored = db.get(SolverRun, run.json()["id"])
        assert stored is not None
        assert stored.request_payload["baseline_source"] == "goal_work_draft"
        assert stored.request_payload["parent_schedule_id"] == draft.id

    # 工作草稿已发布 → 惰性校验失败，回退最新已发布。
    with SessionLocal() as db:
        version_row = db.get(ScheduleVersion, draft.id)
        assert version_row is not None
        version_row.status = "published"
        db.commit()
    run = client.post(
        "/api/v1/assistant/solve",
        headers=auth_headers,
        json={
            "instruction": "重排 B01",
            "class_business_ids": ["B01"],
            "goal_id": goal.id,
        },
    )
    assert run.status_code == 202, run.text
    with SessionLocal() as db:
        stored = db.get(SolverRun, run.json()["id"])
        assert stored is not None
        assert stored.request_payload["baseline_source"] == "latest_published"

    # ① 显式 parent 优先于一切（reschedule/aily 路径的 extra 传入）。
    from app.schemas import AilySolveRequest

    with SessionLocal() as db:
        explicit = api_module.create_solver_run(
            db,
            None,
            AilySolveRequest(class_business_ids=["B01"]),
            "default",
            extra={"parent_schedule_id": published.id},
            goal_id=goal.id,
        )
        assert explicit.request_payload["baseline_source"] == "explicit_parent"
        assert explicit.request_payload["parent_schedule_id"] == published.id
