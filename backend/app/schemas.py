from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["admin", "scheduler", "approver", "viewer"]
RuleStatus = Literal["draft", "awaiting_confirmation", "active", "rejected", "retired"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class UserResponse(ORMModel):
    id: str
    username: str
    role: Role


class CampusCreate(BaseModel):
    business_id: str
    name: str


class CampusResponse(CampusCreate, ORMModel):
    id: str


class TeacherPayload(BaseModel):
    campus_id: str
    business_id: str
    name: str
    subject: str = ""


class TeacherResponse(TeacherPayload, ORMModel):
    id: str


class ClassGroupPayload(BaseModel):
    campus_id: str
    business_id: str
    name: str
    grade: str = ""
    subject: str = ""
    teacher_business_id: str


class ClassGroupResponse(ClassGroupPayload, ORMModel):
    id: str


class RoomPayload(BaseModel):
    campus_id: str
    business_id: str
    name: str
    is_active: bool = True


class RoomResponse(RoomPayload, ORMModel):
    id: str


class TimeSlotPayload(BaseModel):
    campus_id: str
    business_id: str
    weekday: str
    start_time: str
    end_time: str
    kind: str = ""
    sequence: int = 0
    is_open: bool = True


class TimeSlotResponse(TimeSlotPayload, ORMModel):
    id: str


class CourseSessionPayload(BaseModel):
    campus_id: str
    business_id: str
    class_business_id: str
    teacher_business_id: str
    subject: str = ""
    lesson_name: str = ""
    schedule_source: str = ""
    stage: str = ""
    planned_sessions: int = Field(default=0, ge=0)
    planned_hours: float = Field(default=0, ge=0)
    session_no: int = Field(default=0, ge=0)
    lesson_date: date | None = None
    duration_minutes: int = Field(default=90, gt=0)
    suggested_slot_id: str | None = None
    is_locked: bool = False


class CourseSessionResponse(CourseSessionPayload, ORMModel):
    id: str


class RuleCreate(BaseModel):
    business_id: str | None = None
    source_text: str
    actor_type: str
    actor_ids: list[str] = Field(default_factory=list)
    constraint_type: str
    scope: dict[str, Any] = Field(default_factory=dict)
    hardness: Literal["hard", "soft"]
    weight: int | None = None
    structured_expression: dict[str, Any] = Field(default_factory=dict)
    source_doc: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: RuleStatus = "awaiting_confirmation"


class RuleUpdate(BaseModel):
    source_text: str
    actor_type: str
    actor_ids: list[str] = Field(default_factory=list)
    constraint_type: str
    scope: dict[str, Any] = Field(default_factory=dict)
    hardness: Literal["hard", "soft"]
    weight: int | None = None
    structured_expression: dict[str, Any] = Field(default_factory=dict)
    source_doc: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class RuleResponse(RuleCreate, ORMModel):
    id: str
    business_id: str
    version: int
    approved_by: str | None
    created_at: datetime


class RuleTransition(BaseModel):
    status: Literal["active", "rejected", "retired"]
    reason: str | None = None


class SolveRequest(BaseModel):
    time_limit_seconds: float = Field(default=30, ge=1, le=900)
    preference_weight: int = Field(default=100, ge=0, le=10000)
    seat_waste_weight: int = Field(default=1, ge=0, le=1000)
    change_weight: int = Field(default=100000, ge=0, le=1000000)
    wait: bool = False


class SolverRunResponse(ORMModel):
    id: str
    snapshot_id: str
    run_type: str
    status: str
    model_status: str | None
    objective_value: float | None
    best_bound: float | None
    wall_time_seconds: float | None
    conflict_rule_ids: list[str]
    priority_rule_ids: list[str]
    priority_explanations: list[str]
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class AssignmentResponse(BaseModel):
    course_session_id: str
    course_business_id: str
    class_business_id: str
    teacher_business_id: str
    slot_business_id: str
    room_business_id: str
    change_kind: str


class ScheduleResponse(ORMModel):
    id: str
    version_no: int
    name: str
    status: str
    parent_id: str | None
    solver_run_id: str
    metrics: dict[str, Any]
    published_at: datetime | None
    created_at: datetime
    assignments: list[AssignmentResponse] = Field(default_factory=list)


class ScheduleDiffItem(BaseModel):
    course_business_id: str
    class_business_id: str
    teacher_business_id: str
    before_slot_id: str | None = None
    before_room_id: str | None = None
    after_slot_id: str | None = None
    after_room_id: str | None = None
    change_kind: Literal["added", "removed", "moved", "unchanged"]


class ScheduleDiffResponse(BaseModel):
    base_schedule_id: str
    target_schedule_id: str
    changed_count: int
    unchanged_count: int
    items: list[ScheduleDiffItem]


class RescheduleCreate(BaseModel):
    event_type: Literal["teacher_leave", "room_outage", "extra_class"]
    description: str
    parent_schedule_id: str
    teacher_business_id: str | None = None
    room_business_id: str | None = None
    slot_business_ids: list[str] = Field(default_factory=list)
    course_business_id: str | None = None
    time_limit_seconds: float = Field(default=30, ge=1, le=900)


class RescheduleResponse(ORMModel):
    id: str
    event_type: str
    description: str
    payload: dict[str, Any]
    status: str
    parent_schedule_id: str
    solver_run_id: str | None
    candidate_schedule_id: str | None
    created_at: datetime


class ImportResult(BaseModel):
    source: str
    campuses: int
    teachers: int
    class_groups: int
    rooms: int
    time_slots: int
    course_sessions: int
    rules: int


class OverviewResponse(BaseModel):
    counts: dict[str, int]
    latest_run: SolverRunResponse | None
    latest_schedule: ScheduleResponse | None
    pending_rules: int
    pending_reschedules: int
    latest_sync_status: str | None


class FeishuAppConfigurationInput(BaseModel):
    app_id: str = Field(min_length=4, max_length=100)
    app_secret: str = Field(min_length=8, max_length=200)
    oauth_redirect_uri: str = Field(min_length=10, max_length=500)
    frontend_url: str = Field(min_length=8, max_length=500)


class FeishuAppConfigurationResponse(BaseModel):
    configured: bool
    source: Literal["environment", "frontend", "none"]
    app_id: str | None
    secret_configured: bool
    oauth_redirect_uri: str
    frontend_url: str


class FeishuConnectionResponse(BaseModel):
    status: Literal["unconfigured", "not_authorized", "connected", "reauthorization_required"]
    app_configured: bool
    authorized: bool
    missing_fields: list[str]
    granted_scopes: list[str]
    missing_scopes: list[str]
    access_expires_at: datetime | None
    message: str
    console_url: str
    docs_url: str
    app_configuration: FeishuAppConfigurationResponse
    workspace: FeishuWorkspaceResponse | None


class FeishuOAuthStartResponse(BaseModel):
    authorization_url: str
    expires_at: datetime


class FeishuWorkspaceCreate(BaseModel):
    name: str = Field(default="途排智策排课空间", min_length=2, max_length=160)


class FeishuTableBindingResponse(ORMModel):
    resource: str
    table_name: str
    table_id: str


class FeishuWorkspaceResponse(ORMModel):
    id: str
    name: str
    url: str
    status: str
    last_error: str | None
    tables: list[FeishuTableBindingResponse] = Field(default_factory=list)
    created_at: datetime


class FeishuSyncRequest(BaseModel):
    direction: Literal["export"] = "export"
    resource: Literal[
        "teachers", "class_groups", "rooms", "time_slots", "course_sessions", "rules", "schedule"
    ]
    workspace_id: str | None = None


class IntegrationSyncResponse(ORMModel):
    id: str
    provider: str
    direction: str
    resource: str
    status: str
    mode: str
    records_read: int
    records_written: int
    detail: dict[str, Any]
    created_at: datetime


class AuditLogResponse(ORMModel):
    id: str
    actor_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    detail: dict[str, Any]
    created_at: datetime


class AilyRuleBatch(BaseModel):
    source_text: str = Field(min_length=2, max_length=2000)
    source_doc: str | None = None
    proposals: list[RuleCreate] = Field(min_length=1, max_length=50)


class AilyContextResponse(BaseModel):
    entities: dict[str, list[dict[str, Any]]]
    constraint_catalog: list[dict[str, Any]]
    output_contract: dict[str, Any]


class AilySolveRequest(BaseModel):
    time_limit_seconds: float = Field(default=30, ge=1, le=900)
