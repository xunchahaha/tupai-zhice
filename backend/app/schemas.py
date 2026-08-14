from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["admin", "scheduler", "approver", "viewer"]
RuleStatus = Literal["draft", "awaiting_confirmation", "active", "rejected", "retired"]
SolverRule = Literal[
    "fixed_time",
    "room_no_overlap",
    "teacher_no_overlap",
    "calendar_no_overlap",
    "minimize_changes",
]


def default_solver_rules() -> list[SolverRule]:
    return [
        "fixed_time",
        "room_no_overlap",
        "teacher_no_overlap",
        "calendar_no_overlap",
        "minimize_changes",
    ]


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
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None
    created_by: str | None = None


class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    role: Role = "viewer"


class UserRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class UserStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserPasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=8, max_length=128)


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
    calendar_user_id: str | None = None


class TeacherResponse(TeacherPayload, ORMModel):
    id: str


class TeacherBatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(min_length=1, max_length=1000)
    subject: str = ""
    calendar_user_id: str | None = None

    @model_validator(mode="after")
    def validate_requested_changes(self) -> TeacherBatchUpdate:
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("教师记录不能重复选择")
        editable_fields = {"subject", "calendar_user_id"}
        if not (self.model_fields_set & editable_fields):
            raise ValueError("请至少指定一个要批量修改的字段")
        return self


class ClassGroupPayload(BaseModel):
    campus_id: str
    business_id: str
    name: str
    grade: str = ""
    subject: str = ""
    teacher_business_id: str


class ClassGroupResponse(ClassGroupPayload, ORMModel):
    id: str


class ClassGroupBatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(min_length=1, max_length=1000)
    grade: str = ""
    subject: str = ""
    teacher_business_id: str = Field(default="", min_length=1)

    @model_validator(mode="after")
    def validate_requested_changes(self) -> ClassGroupBatchUpdate:
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("班级记录不能重复选择")
        editable_fields = {"grade", "subject", "teacher_business_id"}
        if not (self.model_fields_set & editable_fields):
            raise ValueError("请至少指定一个要批量修改的字段")
        return self


class RoomPayload(BaseModel):
    campus_id: str
    business_id: str
    name: str
    is_active: bool = True


class RoomResponse(RoomPayload, ORMModel):
    id: str


class RoomBatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(min_length=1, max_length=1000)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_requested_changes(self) -> RoomBatchUpdate:
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("教室记录不能重复选择")
        if "is_active" not in self.model_fields_set:
            raise ValueError("请指定批量启用或停用状态")
        return self


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


class TimeSlotBatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(min_length=1, max_length=1000)
    is_open: bool = True

    @model_validator(mode="after")
    def validate_requested_changes(self) -> TimeSlotBatchUpdate:
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("时段记录不能重复选择")
        if "is_open" not in self.model_fields_set:
            raise ValueError("请指定批量开放或关闭状态")
        return self


class MasterDataBatchDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_selected_records(self) -> MasterDataBatchDelete:
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("主数据记录不能重复选择")
        return self


class CourseSessionPayload(BaseModel):
    campus_id: str
    business_id: str
    source_row_id: str = ""
    business_line: str = ""
    product_type: str = ""
    class_business_id: str
    teacher_business_id: str
    calendar_user_id: str | None = None
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
    fixed_start_time: str = ""
    fixed_end_time: str = ""
    original_room_business_id: str | None = None
    is_locked: bool = False


class CourseSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lesson_date: date | None = None
    original_room_business_id: str | None = None
    calendar_user_id: str | None = None


class CourseSessionBatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(min_length=1, max_length=1000)
    lesson_date: date | None = None
    original_room_business_id: str | None = None

    @model_validator(mode="after")
    def validate_requested_changes(self) -> CourseSessionBatchUpdate:
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("课程记录不能重复选择")
        editable_fields = {"lesson_date", "original_room_business_id"}
        if not (self.model_fields_set & editable_fields):
            raise ValueError("请至少指定一个要批量修改的字段")
        return self


class CourseSessionBatchDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_selected_courses(self) -> CourseSessionBatchDelete:
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("课程记录不能重复选择")
        return self


class BatchOperationResponse(BaseModel):
    affected_count: int = Field(ge=0)


class CourseSessionResponse(CourseSessionPayload, ORMModel):
    id: str


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    # 状态只读：创建接口不接受，但响应必须带上，前端靠它驱动确认流程。
    status: RuleStatus
    version: int
    approved_by: str | None
    created_at: datetime


class RuleTransition(BaseModel):
    status: Literal["active", "rejected", "retired"]
    reason: str | None = None


class SolveRequest(BaseModel):
    time_limit_seconds: float = Field(default=30, ge=1, le=900)
    change_weight: int = Field(default=100000, ge=0, le=1000000)
    business_lines: list[str] = Field(default_factory=list)
    product_types: list[str] = Field(default_factory=list)
    class_business_ids: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    date_window_days: int = Field(default=7, ge=0, le=31)
    solver_rules: list[SolverRule] = Field(default_factory=default_solver_rules)
    wait: bool = False

    @model_validator(mode="after")
    def validate_date_range(self) -> SolveRequest:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 必须早于或等于 date_to")
        return self


class SolverRunResponse(ORMModel):
    id: str
    snapshot_id: str
    run_type: str
    status: str
    model_status: str | None
    presolve_infeasible: bool = False
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
    lesson_date: date | None = None
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
    before_lesson_date: date | None = None
    before_slot_id: str | None = None
    before_room_id: str | None = None
    after_lesson_date: date | None = None
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
    date_from: date | None = None
    date_to: date | None = None
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
    rows_total: int = 0
    rows_dropped_placeholder_room: int = 0
    dropped_lesson_groups: int = 0
    dropped_classes: list[str] = Field(default_factory=list)
    rows_kept: int = 0
    rows_deduped: int = 0
    rows_skipped: int = 0
    skipped_examples: list[dict[str, Any]] = Field(default_factory=list)
    duplicate_lessons: int = 0
    class_slot_conflicts: int = 0
    orphans_deleted: int = 0


class OverviewResponse(BaseModel):
    counts: dict[str, int]
    latest_run: SolverRunResponse | None
    latest_schedule: ScheduleResponse | None
    pending_rules: int
    pending_reschedules: int
    latest_sync_status: str | None


class AIProviderConfigurationInput(BaseModel):
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    model: str = Field(min_length=1, max_length=200)


class AIProviderConfigurationResponse(BaseModel):
    configured: bool
    source: Literal["environment", "frontend", "none"]
    provider: str | None
    base_url: str | None
    api_key_configured: bool
    model: str | None


class FeishuAppConfigurationInput(BaseModel):
    app_id: str = Field(min_length=4, max_length=100)
    app_secret: str | None = Field(default=None, max_length=200)
    oauth_redirect_uri: str = Field(min_length=10, max_length=500)
    frontend_url: str = Field(min_length=8, max_length=500)
    aily_app_id: str = Field(default="", max_length=100)
    aily_skill_id: str = Field(default="", max_length=100)


class FeishuAppConfigurationResponse(BaseModel):
    configured: bool
    source: Literal["environment", "frontend", "none"]
    app_id: str | None
    secret_configured: bool
    oauth_redirect_uri: str
    frontend_url: str
    aily_configured: bool
    aily_app_id: str | None
    aily_skill_id: str | None


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
        "teachers",
        "class_groups",
        "rooms",
        "time_slots",
        "course_sessions",
        "rules",
        "schedule",
        "public_summary",
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
    instruction: str = Field(default="生成满足当前已确认规则的课表", min_length=2, max_length=2000)
    business_lines: list[str] = Field(default_factory=list)
    product_types: list[str] = Field(default_factory=list)
    class_business_ids: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    date_window_days: int = Field(default=7, ge=0, le=31)
    solver_rules: list[SolverRule] = Field(default_factory=default_solver_rules)

    @model_validator(mode="after")
    def validate_date_range(self) -> AilySolveRequest:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 必须早于或等于 date_to")
        return self


class AssistantSolveRequest(AilySolveRequest):
    wait: bool = False


class AssistantInterpretRequest(BaseModel):
    instruction: str = Field(min_length=2, max_length=2000)


class AssistantInterpretResponse(BaseModel):
    instruction: str
    source: Literal["openai_compatible", "feishu_aily"]
    ai_configured: bool
    aily_configured: bool
    business_lines: list[str] = Field(default_factory=list)
    product_types: list[str] = Field(default_factory=list)
    class_business_ids: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    date_window_days: int = Field(default=7, ge=0, le=31)
    recognized_rules: list[str] = Field(default_factory=list)
    solver_rules: list[SolverRule] = Field(default_factory=default_solver_rules)
    summary: str


class CalendarPublishRequest(BaseModel):
    calendar_id: str = Field(default="primary", min_length=1, max_length=120)
    # 保留旧字段以兼容既有调用；忙闲冲突只做提示，不再阻止下发。
    block_on_conflict: bool = False
    need_notification: bool = True
    dry_run: bool = False


class CalendarConflict(BaseModel):
    course_session_id: str
    calendar_user_id: str
    lesson_date: date
    start_time: str
    end_time: str
    source: str


class CalendarPublishResponse(BaseModel):
    schedule_id: str
    dry_run: bool = False
    would_publish: int = 0
    published: int
    existing: int
    skipped_unmapped: int
    conflict_count: int = 0
    conflicts: list[CalendarConflict] = Field(default_factory=list)


class CalendarEventBindingResponse(ORMModel):
    id: str
    schedule_version_id: str
    course_session_id: str
    calendar_id: str
    event_id: str
    calendar_user_id: str
    idempotency_key: str
    status: str
    created_at: datetime
    updated_at: datetime


class PublicScheduleShareItem(BaseModel):
    class_label: str
    room_label: str
    month: str
    time_period: str


class PublicScheduleSummary(BaseModel):
    data_policy: str
    total_sessions: int
    business_line_share: dict[str, float]
    monthly_sessions: dict[str, int]
    room_utilization: float
    preview: list[PublicScheduleShareItem]
