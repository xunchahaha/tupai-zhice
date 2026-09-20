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
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    @property
    def presolve_infeasible(self) -> bool:
        """结论是否来自求解前预检——CP-SAT 未运行，不能表述为「已证明无解」。"""
        return bool((self.result_payload or {}).get("presolve_infeasible"))


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
    求解器与调课链路全部以 business_id 定位主体。两条红线写死在使用方：
    ① induced_from_adjustment 条目永不升硬约束（transition 端点 422）；
    ② induced 条目初始 status 恒为 probation（挖掘端点负责）。
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
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


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
