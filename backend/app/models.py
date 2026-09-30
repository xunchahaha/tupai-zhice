from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .timezone import ShanghaiDateTime, shanghai_now

DEFAULT_SCHEDULE_SET_ID = "default"


def new_id() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(ShanghaiDateTime(), default=shanghai_now)
    updated_at: Mapped[datetime] = mapped_column(
        ShanghaiDateTime(), default=shanghai_now, onupdate=shanghai_now
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(ShanghaiDateTime(), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(ShanghaiDateTime(), default=shanghai_now)
    # 令牌版本用于吊销：改密时自增，旧 JWT 携带的版本不再匹配即失效。
    # 不用签发时间做判断——JWT 的 iat 只有秒级精度，同一秒内会误伤新令牌。
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(ShanghaiDateTime(), nullable=True)


class ScheduleSet(TimestampMixin, Base):
    """A named scheduling scope shown in the top bar.

    Versions remain separate objects inside a schedule set.  The default row keeps
    the existing single-schedule installation backwards compatible.
    """

    __tablename__ = "schedule_sets"
    __table_args__ = (UniqueConstraint("name"), UniqueConstraint("code"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class ScheduleSetMember(TimestampMixin, Base):
    """Per-schedule-set visibility and operation permission."""

    __tablename__ = "schedule_set_members"
    __table_args__ = (
        UniqueConstraint("schedule_set_id", "user_id"),
        # Keep both lookup directions fast for the top-bar list and admin matrix.
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    access_role: Mapped[str] = mapped_column(String(20), default="viewer")
    granted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class FeishuAppConfiguration(TimestampMixin, Base):
    __tablename__ = "feishu_app_configurations"

    id: Mapped[str] = mapped_column(String(30), primary_key=True, default="default")
    app_id: Mapped[str] = mapped_column(String(100))
    app_secret_encrypted: Mapped[str] = mapped_column(Text)
    oauth_redirect_uri: Mapped[str] = mapped_column(String(500))
    frontend_url: Mapped[str] = mapped_column(String(500))
    aily_app_id: Mapped[str] = mapped_column(String(100), default="")
    aily_skill_id: Mapped[str] = mapped_column(String(100), default="")
    configured_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class AIProviderConfiguration(TimestampMixin, Base):
    __tablename__ = "ai_provider_configurations"

    id: Mapped[str] = mapped_column(String(30), primary_key=True, default="default")
    provider: Mapped[str] = mapped_column(String(50), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(500))
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(200))
    configured_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class FeishuOAuthState(TimestampMixin, Base):
    __tablename__ = "feishu_oauth_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pkce_verifier_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(ShanghaiDateTime(), index=True)
    used_at: Mapped[datetime | None] = mapped_column(ShanghaiDateTime(), nullable=True)


class FeishuConnection(TimestampMixin, Base):
    __tablename__ = "feishu_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    access_expires_at: Mapped[datetime] = mapped_column(ShanghaiDateTime(), index=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        ShanghaiDateTime(), nullable=True
    )
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class FeishuWorkspace(TimestampMixin, Base):
    __tablename__ = "feishu_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(ForeignKey("feishu_connections.id"), index=True)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160))
    app_token: Mapped[str] = mapped_column(String(100), unique=True)
    default_table_id: Mapped[str] = mapped_column(String(100))
    folder_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    url: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class FeishuTableBinding(TimestampMixin, Base):
    __tablename__ = "feishu_table_bindings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "resource"),
        UniqueConstraint("workspace_id", "table_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("feishu_workspaces.id"), index=True)
    resource: Mapped[str] = mapped_column(String(40))
    table_name: Mapped[str] = mapped_column(String(80))
    table_id: Mapped[str] = mapped_column(String(100))


class FeishuRecordBinding(TimestampMixin, Base):
    __tablename__ = "feishu_record_bindings"
    __table_args__ = (
        UniqueConstraint("table_binding_id", "business_key"),
        UniqueConstraint("table_binding_id", "record_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    table_binding_id: Mapped[str] = mapped_column(
        ForeignKey("feishu_table_bindings.id"), index=True
    )
    business_key: Mapped[str] = mapped_column(String(180))
    record_id: Mapped[str] = mapped_column(String(100))


class Campus(TimestampMixin, Base):
    __tablename__ = "campuses"
    __table_args__ = (UniqueConstraint("schedule_set_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    business_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))


class Teacher(TimestampMixin, Base):
    __tablename__ = "teachers"
    __table_args__ = (UniqueConstraint("schedule_set_id", "campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    campus_id: Mapped[str] = mapped_column(ForeignKey("campuses.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    subject: Mapped[str] = mapped_column(String(80), default="")
    calendar_user_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 教研组代表多名自然人，可以同时开课；自然人不行。求解器据此决定是否建教师互斥。
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)


class ClassGroup(TimestampMixin, Base):
    """班级只保留身份。

    走班制下一个班级里同时存在若干条轨道（选数学的和不选的分流），班型/业务线/教师
    都是课次的属性在班级上的投影，不是班级自己的属性。以前用众数把多值压成单值，
    压出来的组合彼此不保证来自同一批课次（会出现「班型=无数学 + 教师=数学教研组」
    这种自相矛盾的行）。现在改为从 course_sessions 实时聚合，不留第二份真相。
    """

    __tablename__ = "class_groups"
    __table_args__ = (UniqueConstraint("schedule_set_id", "campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    campus_id: Mapped[str] = mapped_column(ForeignKey("campuses.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))


class Room(TimestampMixin, Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("schedule_set_id", "campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    campus_id: Mapped[str] = mapped_column(ForeignKey("campuses.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TimeSlot(TimestampMixin, Base):
    __tablename__ = "time_slots"
    __table_args__ = (UniqueConstraint("schedule_set_id", "campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    campus_id: Mapped[str] = mapped_column(ForeignKey("campuses.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(40), index=True)
    weekday: Mapped[str] = mapped_column(String(20))
    start_time: Mapped[str] = mapped_column(String(10))
    end_time: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(30), default="")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)


class CourseSession(TimestampMixin, Base):
    __tablename__ = "course_sessions"
    __table_args__ = (UniqueConstraint("schedule_set_id", "campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    campus_id: Mapped[str] = mapped_column(ForeignKey("campuses.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(50), index=True)
    source_row_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    business_line: Mapped[str] = mapped_column(String(40), default="", index=True)
    product_type: Mapped[str] = mapped_column(String(120), default="", index=True)
    # 郑州源表的一节教学需求可能同时归属多个产品班型。保留旧的 product_type 作为
    # 兼容/排序主值，完整归属放在 product_types 与 product_contexts，避免导入时按
    # 字典序任取一条而丢失产品关系。
    product_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    product_contexts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # 这三列是「按筛选条件批量」和班级聚合的过滤/分组键，全表扫在真实数据量下太慢。
    class_business_id: Mapped[str] = mapped_column(String(40), index=True)
    teacher_business_id: Mapped[str] = mapped_column(String(40), index=True)
    teacher_business_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    calendar_user_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    subject: Mapped[str] = mapped_column(String(80), default="")
    lesson_name: Mapped[str] = mapped_column(String(120), default="")
    lesson_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    schedule_source: Mapped[str] = mapped_column(String(80), default="")
    stage: Mapped[str] = mapped_column(String(40), default="")
    stages: Mapped[list[str]] = mapped_column(JSON, default=list)
    planned_sessions: Mapped[int] = mapped_column(Integer, default=0)
    planned_hours: Mapped[float] = mapped_column(Float, default=0)
    session_no: Mapped[int] = mapped_column(Integer, default=0)
    lesson_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=90)
    suggested_slot_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    candidate_slot_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    candidate_clock_windows: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    fixed_start_time: Mapped[str] = mapped_column(String(10), default="")
    fixed_end_time: Mapped[str] = mapped_column(String(10), default="")
    original_room_business_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    candidate_room_business_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_variant_count: Mapped[int] = mapped_column(Integer, default=1)
    # 重导移除的课次保留历史引用，但不再进入当前待排集合。
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)


class Rule(TimestampMixin, Base):
    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("schedule_set_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    business_id: Mapped[str] = mapped_column(String(50), index=True)
    source_text: Mapped[str] = mapped_column(Text)
    actor_type: Mapped[str] = mapped_column(String(40), default="system")
    actor_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    constraint_type: Mapped[str] = mapped_column(String(60))
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    hardness: Mapped[str] = mapped_column(String(10), default="hard")
    weight: Mapped[int | None] = mapped_column(Integer, nullable=True)
    structured_expression: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_doc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="awaiting_confirmation")
    version: Mapped[int] = mapped_column(Integer, default=1)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class DataSnapshot(TimestampMixin, Base):
    __tablename__ = "data_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, index=True)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class SolverRun(TimestampMixin, Base):
    __tablename__ = "solver_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("data_snapshots.id"))
    run_type: Mapped[str] = mapped_column(String(30), default="initial")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    model_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    objective_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    wall_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    conflict_rule_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority_rule_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority_explanations: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 求解结果的人话解释：确定性事实包由代码算，措辞由 AI 写，生成后落库避免重复计费。
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 偏好记忆使用情况（MEM-C1）：创建任务时即编译并冻结，求解与解释只读这里，
    # 改记忆不影响在途求解的可复现性。结构与 snapshot.payload["memory"] 一致。
    memory_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 目标验收闭环（MEM-C3）：关联的持久目标与最近一次验收报告。
    # goal_id 在创建任务时指定（POST /solver-runs body.goal_id），任务到达
    # completed 后由 services/goal.py 自动验收并落 goal_report。
    goal_id: Mapped[str | None] = mapped_column(
        ForeignKey("solve_goals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    goal_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    @property
    def presolve_infeasible(self) -> bool:
        """结论是否来自求解前预检——CP-SAT 未运行，不能表述为「已证明无解」。"""
        return bool((self.result_payload or {}).get("presolve_infeasible"))

    @property
    def time_limit_seconds(self) -> float | None:
        """这次求解提交时的时间预算——「加预算」按它计算，而不是按界面草稿的默认值。"""
        raw = (self.request_payload or {}).get("time_limit_seconds")
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def task_revision(self) -> dict[str, Any] | None:
        """本次求解创建时对任务要求做过的修订摘要（新增/收紧/替换/保留的要求）。"""
        raw = (self.request_payload or {}).get("task_revision")
        return dict(raw) if isinstance(raw, dict) else None

    @property
    def goal_checklist_version(self) -> int | None:
        """求解创建时冻结的任务依据版本；与任务当前版本不同说明任务要求在这之后改过。"""
        raw = (self.request_payload or {}).get("goal_checklist_version")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def rerun_of(self) -> str | None:
        """由哪次求解「按原参数重跑」而来。"""
        raw = (self.request_payload or {}).get("rerun_of")
        return str(raw) if raw else None


class ScheduleVersion(TimestampMixin, Base):
    __tablename__ = "schedule_versions"
    __table_args__ = (UniqueConstraint("schedule_set_id", "version_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("schedule_versions.id"), nullable=True)
    solver_run_id: Mapped[str] = mapped_column(ForeignKey("solver_runs.id"), unique=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(ShanghaiDateTime(), nullable=True)

    assignments: Mapped[list[ScheduleAssignment]] = relationship(
        back_populates="schedule_version", cascade="all, delete-orphan"
    )


class ScheduleAssignment(TimestampMixin, Base):
    __tablename__ = "schedule_assignments"
    __table_args__ = (UniqueConstraint("schedule_version_id", "course_session_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_version_id: Mapped[str] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    # 删课次/删版本前都要反查「这条课次被哪些版本引用」，唯一约束建的是复合索引，单列查不走。
    course_session_id: Mapped[str] = mapped_column(ForeignKey("course_sessions.id"), index=True)
    lesson_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    slot_business_id: Mapped[str] = mapped_column(String(40))
    room_business_id: Mapped[str] = mapped_column(String(40))
    change_kind: Mapped[str] = mapped_column(String(30), default="assigned")

    schedule_version: Mapped[ScheduleVersion] = relationship(back_populates="assignments")


class CalendarEventBinding(TimestampMixin, Base):
    __tablename__ = "calendar_event_bindings"
    __table_args__ = (
        UniqueConstraint("schedule_version_id", "course_session_id"),
        UniqueConstraint("idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_version_id: Mapped[str] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    course_session_id: Mapped[str] = mapped_column(ForeignKey("course_sessions.id"), index=True)
    calendar_id: Mapped[str] = mapped_column(String(120))
    event_id: Mapped[str] = mapped_column(String(120))
    calendar_user_id: Mapped[str] = mapped_column(String(120), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="created", index=True)


class RescheduleEvent(TimestampMixin, Base):
    __tablename__ = "reschedule_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    # 调课归因（一键理由）：区分「想换」与「被迫换」，是偏好挖掘的消噪关键。
    declared_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    parent_schedule_id: Mapped[str] = mapped_column(ForeignKey("schedule_versions.id"))
    solver_run_id: Mapped[str | None] = mapped_column(ForeignKey("solver_runs.id"), nullable=True)
    candidate_schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PreferenceEntry(TimestampMixin, Base):
    """L1 声明性记忆：老师/教室/班级/课程的需求与习惯（docs/roadmap/02 §3.1）。

    subject 是多态主体（teacher/classroom/cohort/course），存业务标识而非外键——
    求解器与调课链路全部以 business_id 定位主体。三条红线写死在使用方：
    ① induced_from_adjustment 条目永不自动升硬约束（转正式 Rule 须显式确认）；
    ② induced 条目初始 status 恒为 probation（挖掘端点负责）；
    ③ 三态拆分（MEM-C1）：probation 且未授权试用（trial_authorized=False）的候选
    一律不进求解输入——无感采集，不无感改变排课。
    """

    __tablename__ = "preference_entries"
    __table_args__ = (
        # 同一方案内的查询按「主体+状态」走；约束内容是 JSON，重复判定在写入方用
        # 规范化 JSON 比对完成，数据库层不做 JSON 唯一约束（SQLite/MySQL 语义不一致）。
        Index("ix_preference_entries_scope_status", "schedule_set_id", "status"),
        Index("ix_preference_entries_subject", "schedule_set_id", "subject_type", "subject_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    subject_type: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[str] = mapped_column(String(50), index=True)
    predicate: Mapped[str] = mapped_column(String(40))
    constraint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    modality: Mapped[str] = mapped_column(String(10), default="soft")
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    source: Mapped[str] = mapped_column(String(30))
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    weight: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(20), default="probation", index=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 三态拆分（MEM-C1）：教务显式「授权试用」后才允许 probation 条目以小权重
    # 参与求解，trial_until 到期自动退出；纯候选永远不影响排课。
    trial_authorized: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    trial_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 矛盾消解（MEM-C2 修正 4 / MEM-D1 proposed_conflict / MEM-E1a 授权口径）：
    # 与同主体同谓词的另一活跃条目实际生效窗口重叠且约束互斥时，标记落在提出方
    # 上——提出方按授权状态判定：未授权条目（probation 且未授权试用）永远是提出
    # 方，创建时间仅用于同授权级别内的归属兜底。conflict 的编译排除效果只作用于
    # 未授权条目（本就不进求解输入，outcome=conflict_unresolved）；已授权条目带
    # 标记照常编译（outcome=applied，detail 注明存在未裁决冲突提议）。教务三动作
    # 裁决（保留旧弃新/以新替旧/授权试用）后由 refresh_conflict_flags 重算清除。
    conflict: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PreferenceRejection(TimestampMixin, Base):
    """拒绝记忆（MEM-C2 修正 4）：拒绝候选时落库，防止同类候选重复打扰。

    去重口径 = subject_type + subject_id + predicate + constraint 规范化 JSON 签名；
    evidence 保存被拒候选引用的证据事件 id 集合——再挖掘时证据 ⊆ 已拒证据则
    不复现，出现实质新证据才允许重提（候选 provenance 标注此前被拒原因）。
    reason 是受控枚举字符串（临时请假/主体识别错误/归纳错误/确实有偏好但已改变/
    其他），自由文本备注放 note。
    """

    __tablename__ = "preference_rejections"
    __table_args__ = (
        # 拒绝记录按「方案+主体+谓词」检索，约束签名比对在写入方用规范化 JSON 完成。
        Index(
            "ix_preference_rejections_subject",
            "schedule_set_id",
            "subject_type",
            "subject_id",
            "predicate",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    subject_type: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[str] = mapped_column(String(50))
    predicate: Mapped[str] = mapped_column(String(40))
    constraint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    rejected_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class SolveGoal(TimestampMixin, Base):
    """L3 当前任务工作记录：持久目标验收闭环（docs/roadmap/02 §6 MEM-C3）。

    核心思想：求解任务结束 ≠ 目标完成。把用户的一句话目标拆成可逐项验收的
    checklist（kind 与 services/goal.py 验收器一一对应），每次关联的 SolverRun
    到达 completed 后由**代码验收器**出报告（缺口 + 证据 + 允许的下一步），
    回灌同一目标；报告落在 SolverRun.goal_report，本表只保留最新状态。

    状态机：open → achieved（逐项通过；draft_only 目标在合格草稿交付即完成，
    发布永远不在目标自动动作里）／awaiting_decision（存在需教务放宽或裁决的
    缺口）／abandoned（人工放弃）。停止规则写死在验收器里：不存在允许的补救
    动作就保持 open 并附终态报告，绝不自动无限重跑。latest_run_id 是展示用
    快捷指针（无外键，避免与 solver_runs.goal_id 成环），权威关联以
    SolverRun.goal_id 为准。
    """

    __tablename__ = "solve_goals"
    __table_args__ = (
        Index("ix_solve_goals_scope_status", "schedule_set_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    # 原始指令原文：验收口径永远能回溯到用户的原话，而不是解析中间产物。
    instruction: Mapped[str] = mapped_column(Text)
    # 逐项验收清单：[{key, requirement, kind, params}]，kind 枚举见
    # services/goal.py GOAL_CHECKLIST_KINDS。
    checklist: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # 清单修订历史（MEM-D3）：PATCH /goals/{id}/checklist 每次保存把旧清单快照
    # 进这里（[{version, saved_at, saved_by, items}]，version 是被替换清单的
    # 版本号）。审计靠快照本身，不改写 checklist——正在验收的口径永远以
    # checklist 为准；当前版本号以下面的 checklist_revision 持久化列为准。
    checklist_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # 清单修订版本号（MEM-F/F2 第五轮复审收口）：持久化计数列，初始 v1，
    # PATCH /goals/{id}/checklist 修订时与修订写入在同一事务 +1（SQL 表达式
    # 自增，递增由数据库单写者保证）。验收写回以它为乐观锁：条件 UPDATE 携带
    # `checklist_revision = 评估时版本 AND status <> 'abandoned'`，行数=0 即
    # 「评估到写回之间清单已前移或目标已放弃」，报告只留档、不改写目标当前结论。
    checklist_revision: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    latest_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # 验收状态（MEM-D2/D6）：goal.status 是目标状态机（open/achieved/…），
    # 这里是「最近一次验收执行本身」的三态——run completed 时先置 pending
    #（报告在独立事务里异步生成），验收成功 → completed，验收异常 → failed
    # 并把原因写进 acceptance_detail（不再只打日志）。前端据此展示
    # 「验收中…／验收失败」，避免 completed 后干等一份永远不会出现的报告。
    acceptance_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    acceptance_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 任务上下文（TC-4，docs/roadmap/07-task-context.md §4.1）：当前工作状态
    # 的唯一持久归宿，schema_version=1。三键——
    # scope：最近一次求解确认的范围草稿（决策时点快照，整体覆盖）；
    # soft_task_constraints：软任务约束 [{id, subject_type, subject_ids,
    #   slot_business_ids, source_text}]（编译见 api._compile_task_constraints
    #   的 goal 来源 soft 分支；硬约束不在这里，唯一归宿是 checklist）；
    # work_draft_schedule_id：该目标正在调整的工作草稿版本指针（写入点②=
    #   run completed 反查 ScheduleVersion.solver_run_id；发布/回滚不改指针，
    #   使用方按「仍为 draft」惰性校验——api.create_solver_run 三级基准）。
    # 不设 decisions 字段（决策明细由 AuditLog 全量承载，无声明消费者）；不设
    # 独立版本列、不参与验收乐观锁（口径保护由 checklist_revision 全套覆盖）；
    # 并发写风险由决策点单写者 + 审计（action="update_context"）兜底。
    # NULL = 旧目标「无上下文」，续办时惰性初始化回填。
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class IntegrationSync(TimestampMixin, Base):
    __tablename__ = "integration_syncs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"),
        default=DEFAULT_SCHEDULE_SET_ID,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(30), default="feishu")
    direction: Mapped[str] = mapped_column(String(20))
    resource: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    mode: Mapped[str] = mapped_column(String(20), default="live")
    records_read: Mapped[int] = mapped_column(Integer, default=0)
    records_written: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class IntegrationCredential(TimestampMixin, Base):
    """第三方集成凭据（钉钉/企业微信等）：每集成一行，密钥 Fernet 加密 JSON。

    v1 凭据经设置页按 manifest 的 config schema 直填（无管理端 OAuth 安装流，
    roadmap §2.3 的多实例安装表留二期）；存取见 integrations/credentials.py。
    """

    __tablename__ = "integration_credentials"

    integration_type: Mapped[str] = mapped_column(String(30), primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secrets_encrypted: Mapped[str] = mapped_column(Text, default="")
    configured_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class ImportMappingHistory(TimestampMixin, Base):
    """L2 智能导入：按表头指纹记忆上次生效的列映射（docs/roadmap/01 IMP-4）。

    指纹是「规范化表头序列」的 sha256，列序敏感——同一份教务导出改大小写/全半角
    不换指纹，调列序或增删列就算新表。映射决策是方案内私有数据，按 schedule_set_id
    隔离并复合唯一：每个指纹只保留最近一次 commit 生效的映射（含手动修正）。
    """

    __tablename__ = "import_mapping_history"
    __table_args__ = (UniqueConstraint("schedule_set_id", "header_fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"), index=True
    )
    header_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sheet_name: Mapped[str] = mapped_column(String(255), default="")
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime] = mapped_column(ShanghaiDateTime(), default=shanghai_now)


class AssistantInterpretReceipt(TimestampMixin, Base):
    """一条用户指令的解析回执：让「有副作用的解析」可以安全重试（评审 R4）。

    解析接口会直接执行用户明确授权的记忆动作（「记住…」）并提交，再把结果发给前端。
    提交之后结果在网络里丢了，前端会用同一句话回退到同步接口——没有共同标识就分不出
    这是重试，偏好会被再建一条、重复计权。客户端给一次指令生成一个 request_id，
    流式、同步回退、失败重试都带同一个；服务端在**动作执行的同一事务**里写入本回执
    （方案 + request_id 唯一，存下当时的完整响应），重试直接返回原响应，不再调用
    模型、不再执行动作。并发的两次重试撞唯一约束，输家整体回滚后读回赢家的回执。
    只为产生了副作用（有已执行或已入收件箱的记忆动作）的解析落回执，纯读取不占行。
    """

    __tablename__ = "assistant_interpret_receipts"
    __table_args__ = (UniqueConstraint("schedule_set_id", "request_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(64))
    instruction: Mapped[str] = mapped_column(Text)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(ShanghaiDateTime(), default=shanghai_now)


class PublicLinkToken(TimestampMixin, Base):
    """公开课表层的 capability-link 凭证（docs/roadmap/06 §3 A1）。

    库里只存 SHA-256 哈希，明文 token 仅在创建/轮换响应返回一次；token_hint
    （末 4 位）用于管理端辨认。公开面与角色权限体系正交：签发/轮换/停用复用
    管理端 admin/scheduler 角色，撤回手段 = 停用/轮换/过期。
    """

    __tablename__ = "public_link_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_set_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_sets.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hint: Mapped[str] = mapped_column(String(8))
    scope: Mapped[str] = mapped_column(String(20))
    campus_id: Mapped[str | None] = mapped_column(ForeignKey("campuses.id"), nullable=True)
    resource_business_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    display_name: Mapped[str] = mapped_column(String(160))
    show_teacher_names: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        ShanghaiDateTime(), nullable=True, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(ShanghaiDateTime(), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(ShanghaiDateTime(), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(255), default="")
