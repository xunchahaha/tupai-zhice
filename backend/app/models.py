from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FeishuOAuthState(TimestampMixin, Base):
    __tablename__ = "feishu_oauth_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pkce_verifier_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeishuConnection(TimestampMixin, Base):
    __tablename__ = "feishu_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class FeishuWorkspace(TimestampMixin, Base):
    __tablename__ = "feishu_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(ForeignKey("feishu_connections.id"), index=True)
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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))


class Teacher(TimestampMixin, Base):
    __tablename__ = "teachers"
    __table_args__ = (UniqueConstraint("campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campus_id: Mapped[str] = mapped_column(ForeignKey("campuses.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    subject: Mapped[str] = mapped_column(String(80), default="")
    max_hours: Mapped[int] = mapped_column(Integer, default=8)
    unavailable_slot_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_slot_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    data_level: Mapped[str] = mapped_column(String(30), default="内部")


class ClassGroup(TimestampMixin, Base):
    __tablename__ = "class_groups"
    __table_args__ = (UniqueConstraint("campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campus_id: Mapped[str] = mapped_column(ForeignKey("campuses.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    grade: Mapped[str] = mapped_column(String(50), default="")
    subject: Mapped[str] = mapped_column(String(80), default="")
    student_count: Mapped[int] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(30), default="常规")
    required_devices: Mapped[list[str]] = mapped_column(JSON, default=list)
    teacher_business_id: Mapped[str] = mapped_column(String(40))


class Room(TimestampMixin, Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campus_id: Mapped[str] = mapped_column(ForeignKey("campuses.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    capacity: Mapped[int] = mapped_column(Integer)
    devices: Mapped[list[str]] = mapped_column(JSON, default=list)
    available_slot_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TimeSlot(TimestampMixin, Base):
    __tablename__ = "time_slots"
    __table_args__ = (UniqueConstraint("campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
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
    __table_args__ = (UniqueConstraint("campus_id", "business_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campus_id: Mapped[str] = mapped_column(ForeignKey("campuses.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(50), index=True)
    class_business_id: Mapped[str] = mapped_column(String(40))
    teacher_business_id: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(80), default="")
    student_count: Mapped[int] = mapped_column(Integer)
    required_devices: Mapped[list[str]] = mapped_column(JSON, default=list)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=90)
    suggested_slot_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)


class Rule(TimestampMixin, Base):
    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
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
    revision: Mapped[int] = mapped_column(Integer, index=True)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class SolverRun(TimestampMixin, Base):
    __tablename__ = "solver_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
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
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class ScheduleVersion(TimestampMixin, Base):
    __tablename__ = "schedule_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_no: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("schedule_versions.id"), nullable=True)
    solver_run_id: Mapped[str] = mapped_column(ForeignKey("solver_runs.id"), unique=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assignments: Mapped[list[ScheduleAssignment]] = relationship(
        back_populates="schedule_version", cascade="all, delete-orphan"
    )


class ScheduleAssignment(TimestampMixin, Base):
    __tablename__ = "schedule_assignments"
    __table_args__ = (UniqueConstraint("schedule_version_id", "course_session_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_version_id: Mapped[str] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    course_session_id: Mapped[str] = mapped_column(ForeignKey("course_sessions.id"))
    slot_business_id: Mapped[str] = mapped_column(String(40))
    room_business_id: Mapped[str] = mapped_column(String(40))
    change_kind: Mapped[str] = mapped_column(String(30), default="assigned")

    schedule_version: Mapped[ScheduleVersion] = relationship(back_populates="assignments")


class RescheduleEvent(TimestampMixin, Base):
    __tablename__ = "reschedule_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    parent_schedule_id: Mapped[str] = mapped_column(ForeignKey("schedule_versions.id"))
    solver_run_id: Mapped[str | None] = mapped_column(ForeignKey("solver_runs.id"), nullable=True)
    candidate_schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class IntegrationSync(TimestampMixin, Base):
    __tablename__ = "integration_syncs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
