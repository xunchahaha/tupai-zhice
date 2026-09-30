from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .timezone import as_shanghai

Role = Literal["admin", "scheduler", "approver", "viewer"]
ScheduleAccessRole = Literal["viewer", "scheduler", "approver"]
RuleStatus = Literal["draft", "awaiting_confirmation", "active", "rejected", "retired"]
SolverRule = Literal[
    "fixed_time",
    "room_no_overlap",
    "teacher_no_overlap",
    "calendar_no_overlap",
    "minimize_changes",
]

# 教室和教师资源冲突是排课结果的基本有效性条件，不作为业务人员可关闭的选项。
MANDATORY_SOLVER_RULES: tuple[SolverRule, ...] = ("room_no_overlap", "teacher_no_overlap")


def default_solver_rules() -> list[SolverRule]:
    return [
        "fixed_time",
        "room_no_overlap",
        "teacher_no_overlap",
        "calendar_no_overlap",
        "minimize_changes",
    ]


def normalize_solver_rules(rules: list[SolverRule]) -> list[SolverRule]:
    """去重并补齐系统级硬约束，统一手动求解与一句话排课的行为。"""
    return list(dict.fromkeys([*rules, *MANDATORY_SOLVER_RULES]))


class ShanghaiTimestampResponse(BaseModel):
    """Normalize every response datetime to the application's UTC+8 policy.

    ORM values already round-trip through ``ShanghaiDateTime``.  This extra
    response boundary protects manually-built DTOs and non-SQLite deployments
    from leaking a UTC or timezone-naive timestamp into JSON.
    """

    @model_validator(mode="after")
    def normalize_datetime_fields(self) -> ShanghaiTimestampResponse:
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if isinstance(value, datetime):
                normalized = as_shanghai(value)
                if normalized is not None:
                    setattr(self, field_name, normalized)
        return self


class ORMModel(ShanghaiTimestampResponse):
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


class ScheduleSetCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=2, max_length=160)


class ScheduleSetUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=2, max_length=160)


class ScheduleSetResponse(ORMModel):
    id: str
    code: str
    name: str
    display_order: int
    is_active: bool
    access_role: ScheduleAccessRole
    current_version_id: str | None = None
    current_version_name: str | None = None
    current_version_no: int | None = None


class ScheduleSetMemberUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    access_role: ScheduleAccessRole


class ScheduleSetMemberResponse(ORMModel):
    id: str
    schedule_set_id: str
    user_id: str
    username: str
    user_role: Role
    access_role: ScheduleAccessRole
    is_active: bool
    granted_by: str | None = None
    created_at: datetime


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
    is_group: bool = False


class TeacherResponse(TeacherPayload, ORMModel):
    id: str


class TeacherBatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(min_length=1, max_length=1000)
    subject: str = ""
    calendar_user_id: str | None = None
    is_group: bool = False

    @model_validator(mode="after")
    def validate_requested_changes(self) -> TeacherBatchUpdate:
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("教师记录不能重复选择")
        editable_fields = {"subject", "calendar_user_id", "is_group"}
        if not (self.model_fields_set & editable_fields):
            raise ValueError("请至少指定一个要批量修改的字段")
        return self


class ClassGroupPayload(BaseModel):
    """班级的可写字段只有身份。班型/业务线/教师是课次的属性，不在这里填。

    extra="forbid" 是刻意的：留一个「看起来能填班型」的入口，等于允许有人手填一个和
    课次矛盾的值，而界面上显示的又是聚合结果，两边对不上却谁都不报错。
    """

    model_config = ConfigDict(extra="forbid")

    campus_id: str
    business_id: str
    name: str


class ClassGroupTrack(BaseModel):
    """走班轨道：这个班里「哪条业务线的哪种班型、上什么课、谁教」的一条真实分流。

    (班级, 班型, 科目) → 教师 在源数据里是个函数，所以轨道能把「哪门课谁教」说清楚，
    而两个互相断了关系的平铺数组说不清。
    """

    business_line: str
    product_type: str
    subject: str
    teacher_business_id: str
    session_count: int = Field(ge=0)


class ClassGroupResponse(ClassGroupPayload, ORMModel):
    """班型/业务线/教师全部由 course_sessions 实时聚合，库里不存这三个单值列。

    手工新建、还没有任何课次的班级，这些数组一律为空、session_count 为 0——
    这不是数据丢了，是这个班还没排课。
    """

    # 全部必填：服务端每次都算得出来，声明成可选只会让前端多写一圈 ?? [] 的兜底。
    id: str
    business_lines: list[str]
    product_types: list[str]
    subjects: list[str]
    teacher_business_ids: list[str]
    session_count: int = Field(ge=0)
    tracks: list[ClassGroupTrack]


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
    product_types: list[str] = Field(default_factory=list)
    product_contexts: list[dict[str, Any]] = Field(default_factory=list)
    class_business_id: str
    teacher_business_id: str
    teacher_business_ids: list[str] = Field(default_factory=list)
    calendar_user_id: str | None = None
    subject: str = ""
    lesson_name: str = ""
    lesson_names: list[str] = Field(default_factory=list)
    schedule_source: str = ""
    stage: str = ""
    stages: list[str] = Field(default_factory=list)
    planned_sessions: int = Field(default=0, ge=0)
    planned_hours: float = Field(default=0, ge=0)
    session_no: int = Field(default=0, ge=0)
    lesson_date: date | None = None
    duration_minutes: int = Field(default=90, gt=0)
    suggested_slot_id: str | None = None
    candidate_slot_ids: list[str] = Field(default_factory=list)
    candidate_clock_windows: list[dict[str, str]] = Field(default_factory=list)
    fixed_start_time: str = ""
    fixed_end_time: str = ""
    original_room_business_id: str | None = None
    candidate_room_business_ids: list[str] = Field(default_factory=list)
    source_variant_count: int = Field(default=1, ge=1)
    is_locked: bool = False


class CourseSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lesson_date: date | None = None
    original_room_business_id: str | None = None
    calendar_user_id: str | None = None


COURSE_BATCH_ID_LIMIT = 1000


class CourseSessionFilter(BaseModel):
    """课程场次的筛选条件，与主数据页课程页签的筛选器一一对应。

    `search` 复刻前端的跨字段子串匹配：八个字段按固定顺序用空格拼接后做包含判断，
    顺序不能改——「考研 暑期」这种跨字段的查询词只有拼接顺序一致才命中同一批行。
    全部字段留空表示「全部课程」，这正是「全选筛选结果」在没有任何筛选时的语义。
    """

    model_config = ConfigDict(extra="forbid")

    campus_id: str | None = None
    business_line: str | None = None
    product_type: str | None = None
    class_business_id: str | None = None
    teacher_business_id: str | None = None
    subject: str | None = None
    original_room_business_id: str | None = None
    lesson_date: date | None = None
    lesson_date_from: date | None = None
    lesson_date_to: date | None = None
    search: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_date_window(self) -> CourseSessionFilter:
        if (
            self.lesson_date_from is not None
            and self.lesson_date_to is not None
            and self.lesson_date_from > self.lesson_date_to
        ):
            raise ValueError("日期范围的起始日不能晚于结束日")
        return self


class CourseSessionSelection(BaseModel):
    """批量操作的选择方式：按 id 逐条选，或按筛选条件整批选，二选一。

    保留 object_ids 是为了兼容既有调用；加 filter 是为了让「全选两万条」不必把两万个
    id 塞进请求体。expected_count 是防呆：前端把界面上看到的条数一起报上来，服务端
    命中数对不上就拒绝——筛选结果在两次请求之间漂移过，就不该按旧的判断继续删。
    """

    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] | None = Field(default=None, max_length=COURSE_BATCH_ID_LIMIT)
    filter: CourseSessionFilter | None = None
    expected_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_selection(self) -> CourseSessionSelection:
        if (self.object_ids is None) == (self.filter is None):
            raise ValueError("请二选一：传 object_ids 逐条选择，或传 filter 按筛选条件选择")
        if self.object_ids is not None:
            if not self.object_ids:
                raise ValueError("请至少选择一条课程记录")
            if len(set(self.object_ids)) != len(self.object_ids):
                raise ValueError("课程记录不能重复选择")
        return self


class CourseSessionBatchUpdate(CourseSessionSelection):
    lesson_date: date | None = None
    original_room_business_id: str | None = None

    @model_validator(mode="after")
    def validate_requested_changes(self) -> CourseSessionBatchUpdate:
        editable_fields = {"lesson_date", "original_room_business_id"}
        if not (self.model_fields_set & editable_fields):
            raise ValueError("请至少指定一个要批量修改的字段")
        return self


class CourseSessionBatchDelete(CourseSessionSelection):
    @model_validator(mode="after")
    def validate_delete_guard(self) -> CourseSessionBatchDelete:
        if self.filter is not None and self.expected_count is None:
            raise ValueError("按筛选条件删除必须提供 expected_count，用于校验命中条数")
        return self


class BatchOperationResponse(BaseModel):
    affected_count: int = Field(ge=0)


class CourseSessionResponse(CourseSessionPayload, ORMModel):
    id: str


class ConstraintScopeField(BaseModel):
    """规则范围里的一个可填字段，前端据此渲染对应控件。"""

    name: str
    label: str
    kind: Literal["slot", "room", "date", "integer"]
    multiple: bool = False
    required: bool = True
    minimum: int | None = None


class ConstraintCatalogEntry(BaseModel):
    type: str
    label: str
    description: str = ""
    # scope 里任意一个字段有值即视为范围完整。
    scope: list[str] = Field(default_factory=list)
    scope_fields: list[ConstraintScopeField] = Field(default_factory=list)
    hardness: list[Literal["hard", "soft"]]
    # 硬软属性 → 真正生效的求解路径（date=日期感知，slot=时段矩阵）。空表示只登记不建模。
    solver_paths: dict[Literal["hard", "soft"], list[Literal["date", "slot"]]] = Field(
        default_factory=dict
    )
    # 选软约束时额外提示的权衡口径，例如权重要压过「减少改动」才会真的改日期。
    soft_weight_hint: str | None = None
    alias_of: str | None = None


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


PreferenceSubjectType = Literal["teacher", "classroom", "cohort", "course"]
PreferenceSource = Literal["explicit_stated", "admin_directive", "induced_from_adjustment"]
PreferenceStatus = Literal["probation", "confirmed", "rejected", "expired"]


class PreferenceCreate(BaseModel):
    """手工登记一条偏好。source 只接受显式来源：挖掘产生的条目一律走
    POST /memory/mining-runs，初始状态恒为 probation。"""

    model_config = ConfigDict(extra="forbid")

    subject_type: PreferenceSubjectType
    subject_id: str = Field(min_length=1, max_length=50)
    predicate: str = Field(min_length=1, max_length=40)
    constraint: dict[str, Any] = Field(default_factory=dict)
    modality: Literal["hard", "soft"] = "soft"
    confidence: float = Field(default=0.8, ge=0, le=1)
    source: Literal["explicit_stated", "admin_directive"]
    evidence: list[str] = Field(default_factory=list)
    weight: int = Field(default=50, ge=0, le=100)
    # 红线②：不传时由后端默认「当前日期 + 180 天」，并在响应中回显。
    valid_until: date | None = None
    note: str | None = Field(default=None, max_length=500)


class PreferenceUpdate(BaseModel):
    """ probation/confirmed 条目的内容修订；状态只能走 transition 端点。"""

    model_config = ConfigDict(extra="forbid")

    weight: int | None = Field(default=None, ge=0, le=100)
    predicate: str | None = Field(default=None, min_length=1, max_length=40)
    constraint: dict[str, Any] | None = None
    # scope 是 constraint 的便捷写法：提供的键合并进现有 constraint，不做整体替换。
    scope: dict[str, Any] | None = None
    valid_until: date | None = None


class PreferenceTransition(BaseModel):
    """状态迁移（action=transition，默认）或授权试用（action=authorize_trial）。

    三态拆分（MEM-C1）：授权试用只对 probation 条目可用，条目保持 probation，
    以 trial_authorized + trial_until 参与小权重试用；采纳（confirmed）仍是正式生效路径。
    MEM-C2 修正 4：target_status=rejected 时建议带 rejection_reason（拒绝原因五选，
    落 preference_rejections 供去重）；缺省按「其他」处理。前端必填，API 兜底。
    MEM-D1 冲突裁决三动作全部走本端点：保留旧弃新 = 候选 rejected；
    以新替旧 = 旧条目 expired 且带 supersedes=<候选 id>（provenance 记
    superseded_by）、候选再 confirmed；授权试用 = action=authorize_trial
    （旧新并存，旧全权、候选小权重）。
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["transition", "authorize_trial"] = "transition"
    target_status: Literal["confirmed", "rejected", "expired"] | None = None
    # 红线①：induced_from_adjustment 条目传 hard 一律 422。
    target_modality: Literal["hard", "soft"] | None = None
    # 冲突裁决「以新替旧」（MEM-D1）：target_status=expired 时可选指向替代它的
    # 候选 id，旧条目 provenance 记 superseded_by 形成显式审计链。
    supersedes: str | None = Field(default=None, min_length=1, max_length=36)
    trial_days: int = Field(default=30, ge=1, le=365)
    reason: str | None = Field(default=None, max_length=500)
    # 拒绝原因（MEM-C2 修正 4）：临时请假 / 主体识别错误 / 归纳错误 /
    # 确实有偏好但已改变 / 其他（自由文本备注仍走 reason 字段）。
    rejection_reason: Literal[
        "temporary_leave",
        "subject_misidentified",
        "wrong_generalization",
        "preference_changed",
        "other",
    ] | None = None

    @model_validator(mode="after")
    def validate_action(self) -> PreferenceTransition:
        if self.action == "transition" and self.target_status is None:
            raise ValueError("target_status 不能为空")
        if self.action == "authorize_trial" and self.target_status is not None:
            raise ValueError("授权试用不改变状态，请勿传 target_status")
        return self


class PreferenceConvertRequest(BaseModel):
    """把 hard 条目转成正式规则。induced 来源必须显式勾选确认（红线①的兜底）。"""

    model_config = ConfigDict(extra="forbid")

    confirmed_conversion: bool = False


class PreferenceResponse(ORMModel):
    id: str
    schedule_set_id: str
    subject_type: str
    subject_id: str
    predicate: str
    constraint: dict[str, Any]
    modality: str
    confidence: float
    source: str
    evidence: list[str]
    weight: int
    status: str
    valid_from: date | None = None
    valid_until: date | None = None
    trial_authorized: bool = False
    trial_until: date | None = None
    # 矛盾消解（MEM-C2 修正 4 / MEM-D1 / MEM-E1a）：proposed_conflict 标——本条
    # 与同主体同类条目实际生效窗口重叠且约束互斥时落在提出方上（按授权状态判定：
    # 未授权条目永远是提出方，同级取较新者）；被点名的对侧不受影响、照常编译。
    # conflict 的编译排除只作用于未授权条目（conflict_unresolved）；已授权条目带
    # 标记照常 applied（detail 注明），教务按三动作裁决（保留旧弃新/以新替旧/
    # 授权试用）后由后端重算清除；前端 amber 徽标提示。
    conflict: bool = False
    provenance: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PreferenceAdjudicateReplace(BaseModel):
    """「以新替旧」原子裁决请求（MEM-E3）。

    旧条目退场原因沿用拒绝原因五选枚举（记录进旧条目 provenance 与审计日志，
    缺省「其他」）；被替换的旧条目缺省取候选的唯一活跃冲突对端，多于一对时
    必须显式传 old_entry_id。
    """

    model_config = ConfigDict(extra="forbid")

    old_entry_id: str | None = Field(default=None, min_length=1, max_length=36)
    rejection_reason: Literal[
        "temporary_leave",
        "subject_misidentified",
        "wrong_generalization",
        "preference_changed",
        "other",
    ] = "other"


class PreferenceAdjudicateReplaceResponse(BaseModel):
    """「以新替旧」裁决结果：候选与旧条目的当前状态。

    detail=replaced 表示本次事务完成了切换；already_applied 表示此前已裁决
    （重复调用幂等），未做二次变更。
    """

    candidate: PreferenceResponse
    old_entry: PreferenceResponse
    detail: Literal["replaced", "already_applied"]


class MiningRunResponse(BaseModel):
    """一次偏好挖掘的结果。created 即落库后的候选清单（status=probation）。

    MEM-C2 修正 4：skipped_rejected 计入「证据 ⊆ 已拒证据被跳过」的候选数；
    带新证据重提的候选照常落库，但其 provenance.previously_rejected 标注此前
    被拒原因，由前端卡片渲染。
    """

    engine: Literal["ai", "deterministic"]
    events_scanned: int
    created: list[PreferenceResponse]
    skipped_existing: int = 0
    skipped_rejected: int = 0
    skipped_invalid: int = 0
    ai_error: str | None = None


GoalChecklistKind = Literal[
    "deliverable_exists",
    "coverage",
    "no_duplicate_lessons",
    "forbidden_slot_free",
    "no_hard_conflicts",
    "max_changes",
    "draft_only",
    "date_range_match",
]


class GoalChecklistItem(BaseModel):
    """逐项验收项（MEM-C3）。kind 与 services/goal.py 验收器一一对应。

    params 是各验收器自己的参数包（coverage 的范围条件、forbidden_slot_free 的
    主体+时段、max_changes 的上限与基准版本、date_range_match 的日期端点）；
    结构由验收器解释，这里不做 cross-field 校验——未知参数在验收时按
    「无法核对=不通过」处理，绝不静默放行。params.bottom_line=True 标记底线
    验收项（MEM-D2/D4c：创建目标时强制并入，不可删除，前端打「底线」徽标）。
    """

    key: str = Field(min_length=1, max_length=80)
    requirement: str = Field(min_length=1, max_length=500)
    kind: GoalChecklistKind
    params: dict[str, Any] = Field(default_factory=dict)


class GoalForbiddenSlot(BaseModel):
    """创建目标时声明禁排复核项：主体 + 时段集合（独立于求解器自报）。"""

    subject_type: Literal["teacher", "classroom", "cohort", "course"] = "teacher"
    subject_ids: list[str] = Field(default_factory=list)
    slot_business_ids: list[str] = Field(min_length=1)


class GoalChecklistReplaceRequest(BaseModel):
    """整体替换验收清单（MEM-D3 `PATCH /goals/{id}/checklist` 的 body）。

    传完整 checklist 数组（不是增量 patch）：验收口径必须始终是一份完整、
    自洽的清单，增量合并会把「删了一项」表达成「没提这一项」。kind 白名单由
    `GoalChecklistItem` 的 Literal 把关；key 唯一与底线项强制并入在 API 层
    复用创建目标时的同一套校验（`ensure_bottom_line_items`）。

    MEM-E2/E2b：`scope` 可选——body 显式给出的范围字段（业务线/班型/班级/课次/
    日期端点）用于补全/更新底线 coverage 的参数；不传或传 None 的字段保留旧
    coverage 项的范围参数（修订不丢范围）。
    """

    checklist: list[GoalChecklistItem]
    scope: GoalScopePatch | None = None


class GoalScopePatch(BaseModel):
    """修订清单时可选的范围字段（MEM-E2/E2b → MEM-F/F3 三态语义）。

    **显式 scope 优先于清单 coverage 既有参数**。字段级三态（按字段是否在
    请求 body 里显式出现判定，实现口径是 pydantic v2 的 ``model_fields_set``
    ——真值判断会把「显式 []」和「未提供」混为一谈）：

    - 未提交（字段不出现）：保留旧值（无旧值则该维度无限制）；
    - 显式空列表 / 显式 ``null`` 日期：清除该维度限制；
    - 显式非空：替换为新值。
    """

    business_lines: list[str] | None = None
    product_types: list[str] | None = None
    class_business_ids: list[str] | None = None
    course_business_ids: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None


class GoalCreateRequest(BaseModel):
    """创建持久目标。

    checklist 缺省时由 services/goal.build_checklist 按下方结构化范围字段
    确定性生成（业务范围→coverage、日期→date_range_match、forbidden_slots→
    逐条 forbidden_slot_free、forbid_publish→draft_only、max_changes→上限项）。
    """

    instruction: str = Field(min_length=2, max_length=2000)
    checklist: list[GoalChecklistItem] | None = None
    business_lines: list[str] = Field(default_factory=list)
    product_types: list[str] = Field(default_factory=list)
    class_business_ids: list[str] = Field(default_factory=list)
    course_business_ids: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    forbidden_slots: list[GoalForbiddenSlot] = Field(default_factory=list)
    max_changes: int | None = Field(default=None, ge=0, le=100000)
    baseline_schedule_version_id: str | None = None
    # 实际从哪份课表继续调整（例如从课表选中某份草稿的课次交给助手）：登记任务时就记进
    # goal.context.base_schedule_id。与上面的 baseline_schedule_version_id 含义不同——
    # 后者只是「变更数验收/对比」用的基准，不决定求解从哪份课表出发。
    base_schedule_id: str | None = None
    forbid_publish: bool = True

    @model_validator(mode="after")
    def validate_date_range(self) -> GoalCreateRequest:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 必须早于或等于 date_to")
        return self


class GoalResponse(ORMModel):
    id: str
    schedule_set_id: str
    instruction: str
    checklist: list[dict[str, Any]]
    # 清单版本号（MEM-D3）：初始清单为 v1，每次 PATCH /checklist 修订 +1；
    # 修订历史在 GoalDetailResponse.checklist_history。
    checklist_version: int = 1
    status: str
    # 最近一次验收执行本身的状态（MEM-D2/D6）：pending=报告未生成、
    # completed=报告已落库、failed=验收异常（原因见 acceptance_detail）。
    acceptance_status: str = "pending"
    acceptance_detail: str | None = None
    latest_run_id: str | None = None
    run_count: int = 0
    # MEM-E2/E2a：清单修订后旧报告保留但结论过期——PATCH 响应带该字段标注
    # latest_run 报告所属的清单版本（is_current_version=False，前端展示
    # 「历史版本 v{n} 的结论」而非当成当前口径）。
    latest_report_meta: dict[str, Any] | None = None
    # 任务上下文（TC-4 §4.1）：当前工作状态（scope/soft_task_constraints/
    # work_draft_schedule_id，schema_version=1）。旧目标为 null=「无上下文」，
    # 续办按惰性初始化回填。context 是工作状态而非验收口径——不参与验收
    # 乐观锁（口径保护由 checklist_version 承担），决策明细在 AuditLog。
    context: dict[str, Any] | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class SolveRequest(BaseModel):
    time_limit_seconds: float = Field(default=30, ge=1, le=900)
    course_business_ids: list[str] = Field(default_factory=list)
    # 显式基准版本：手动排课/加预算重跑也要能沿用任务原来的基准，而不是回退到当前已发布版本。
    parent_schedule_id: str | None = None
    change_weight: int = Field(default=100000, ge=0, le=1000000)
    business_lines: list[str] = Field(default_factory=list)
    product_types: list[str] = Field(default_factory=list)
    class_business_ids: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    date_window_days: int = Field(default=7, ge=0, le=31)
    solver_rules: list[SolverRule] = Field(default_factory=default_solver_rules)
    # 目标验收闭环（MEM-C3）：可选关联持久目标，run 到达 completed 后自动验收。
    goal_id: str | None = None
    wait: bool = False

    @model_validator(mode="after")
    def validate_date_range(self) -> SolveRequest:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 必须早于或等于 date_to")
        self.solver_rules = normalize_solver_rules(self.solver_rules)
        return self


class IntentReview(BaseModel):
    """把教务原始那句话和最终课表对照的结论。仅供参考，不改变硬约束判定。"""

    verdict: Literal["matched", "deviated", "unclear"]
    concerns: list[str] = Field(default_factory=list)


class SolverRunExplanation(BaseModel):
    headline: str
    explanation: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    intent_review: IntentReview | None = None
    # deterministic=纯代码生成的兜底解释；ai=模型润色过的版本。
    source: Literal["deterministic", "ai"]
    usage: dict[str, Any] | None = None
    # AI 调用失败时保留原因，界面据此说明为什么只有兜底解释。
    ai_error: str | None = None


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
    explanation: SolverRunExplanation | None = None
    # 创建任务时冻结的偏好记忆使用情况（结构同 snapshot.payload["memory"]）。
    memory_usage: dict[str, Any] | None = None
    # 目标验收闭环（MEM-C3）：关联目标与最近一次自动验收报告（completed 后生成）。
    goal_id: str | None = None
    goal_report: dict[str, Any] | None = None
    # 提交时的时间预算：「加预算重跑」以它为基数，不能按界面草稿的默认值算。
    time_limit_seconds: float | None = None
    # 这次求解创建时对任务要求做过的修订摘要（added_hard/tightened/added_soft/
    # replaced_soft/kept_hard 原话列表），没有修订为 None。
    task_revision: dict[str, list[str]] | None = None
    # 由哪次求解按原参数重跑而来。
    rerun_of: str | None = None
    # 求解创建时冻结的任务依据版本：和任务当前版本不同 = 任务要求在这次求解之后改过，
    # 这时「按原参数重跑」不再成立（后端 409），界面据此说明而不是让人点了才报错。
    goal_checklist_version: int | None = None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class SolverRunRerunRequest(BaseModel):
    """按原求解的冻结参数重跑：范围、规则、权重、数据与记忆都沿用，只改时间预算。

    time_limit_seconds 缺省 = 按原预算加大（×3，至少 90 秒，上限 900 秒）；
    显式给出则用给定值。需要换数据、规则、记忆或基准时是另一个动作——重新排课。
    """

    time_limit_seconds: float | None = Field(default=None, ge=1, le=900)
    wait: bool = False


class GoalDetailResponse(GoalResponse):
    """目标详情：runs 历史（各 run 带自己的 goal_report）+ 最近一次验收报告。"""

    runs: list[SolverRunResponse] = Field(default_factory=list)
    latest_report: dict[str, Any] | None = None
    # 清单修订历史（MEM-D3）：[{version, saved_at, saved_by, items}] 只读快照，
    # version 是被替换清单的版本号（当前版本 = len(self.checklist_history)+1）。
    checklist_history: list[dict[str, Any]] = Field(default_factory=list)


class AssignmentResponse(BaseModel):
    course_session_id: str
    course_business_id: str
    class_business_id: str
    teacher_business_id: str
    lesson_date: date | None = None
    slot_business_id: str
    room_business_id: str
    change_kind: str
    course: CourseSessionResponse | None = None


class ScheduleSummaryResponse(ORMModel):
    """课表列表项。不内联 assignments——真实数据下每个版本上万行，
    列表接口会把全部版本的排课行一次性吐给前端。"""

    id: str
    version_no: int
    name: str
    status: str
    parent_id: str | None
    solver_run_id: str
    metrics: dict[str, Any]
    assignment_count: int = 0
    published_at: datetime | None
    created_at: datetime


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
    # 一键归因理由（前端 chips 由 MEM-B 提供）：区分「想换」与「被迫换」，
    # 供偏好挖掘消噪。可空，不填不影响调课流程。
    declared_reason: str | None = Field(default=None, max_length=80)
    teacher_business_id: str | None = None
    room_business_id: str | None = None
    slot_business_ids: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    course_business_id: str | None = None
    # 是否把种子课次前后 neighborhood_days 天内同班/同教室的课次也纳入可挪动范围。
    # 默认 True 保持既有「局部邻域」行为；只调整选中的某一节课时前端显式传 False。
    include_neighbors: bool = True
    neighborhood_days: int = Field(default=7, ge=0, le=31)
    time_limit_seconds: float = Field(default=30, ge=1, le=900)


class RescheduleResponse(ORMModel):
    id: str
    event_type: str
    description: str
    declared_reason: str | None = None
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
    preprocessed_demands: int = 0
    collapsed_source_variants: int = 0
    multi_product_demands: int = 0
    multi_lesson_name_demands: int = 0
    multi_slot_demands: int = 0
    rows_skipped: int = 0
    skipped_examples: list[dict[str, Any]] = Field(default_factory=list)
    duplicate_lessons: int = 0
    class_slot_conflicts: int = 0
    orphans_deleted: int = 0
    orphans_retained: int = 0
    schedule_version_id: str | None = None
    schedule_version_no: int | None = None


class ImportSheetOverview(BaseModel):
    """导入向导：单个工作表的概览。"""

    name: str
    row_count: int
    column_count: int
    nonempty_cells: int


class ImportHeaderCandidate(BaseModel):
    """导入向导：表头行候选（按像表头的程度降序）。"""

    row_index: int  # 0-based 网格行号
    score: float
    sample: list[str]


class ImportColumnMapping(BaseModel):
    """导入向导：一列到 14 列规范字段的映射建议。

    confidence 低于 0.5 的列 target 恒为 None（不硬猜），由用户在向导中手动指定。
    """

    column: str
    column_index: int  # 0-based 网格列号
    target: str | None
    confidence: float
    rationale: str
    # exact / alias / normalized / fuzzy / llm / historical / manual / unmatched
    matched_by: str
    sample_values: list[str] = Field(default_factory=list)


class ImportPreviewStats(BaseModel):
    rows_total: int
    rows_valid: int
    rows_skipped: int
    rows_ignored_blank: int = 0
    columns_total: int
    mapped_columns: int
    ai_mapping_used: bool = False
    overrides_applied: int = 0


class ImportPreviewResponse(BaseModel):
    """`POST /imports/preview` 响应：只解析不落库。"""

    source: str
    sheets: list[ImportSheetOverview]
    selected_sheet: str
    header_row_index: int
    header_candidates: list[ImportHeaderCandidate]
    mapping: list[ImportColumnMapping]
    unmatched_columns: list[str]
    missing_fields: list[str]
    issues: list[dict[str, Any]] = Field(default_factory=list)
    stats: ImportPreviewStats
    # 无 mapping_json 时命中了「上次导入的映射记忆」（按表头指纹匹配）。
    historical_match: bool = False
    # cell_overrides 里行号/列名对不上的条数：宁可忽略报数也不猜。
    ignored_overrides: int = 0


class ImportMappingColumnInput(BaseModel):
    """用户修正后的单列映射（Fix 循环回传）。"""

    column: str
    column_index: int | None = None
    target: str | None = None  # None 表示该列不导入


class ImportMappingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    sheet: str | None = None  # 缺省取非空单元格最多的工作表
    header_row_index: int | None = None  # 缺省用自动检测的最佳候选
    columns: list[ImportMappingColumnInput] = Field(min_length=1)


class ImportCommitResponse(ImportResult):
    """`POST /imports/commit` 响应：与模板直通导入同构，另附映射导入的模式信息。"""

    mode: str
    course_sessions_updated: int = 0
    course_sessions_skipped_existing: int = 0
    overrides_applied: int = 0
    ignored_overrides: int = 0


class OverviewResponse(BaseModel):
    counts: dict[str, int]
    latest_run: SolverRunResponse | None
    latest_schedule: ScheduleResponse | None
    pending_rules: int
    pending_reschedules: int
    latest_sync_status: str | None


class TeacherWorkloadItem(BaseModel):
    """课表版本内一名教师的课时负荷。"""

    campus_id: str
    campus_name: str
    teacher_business_id: str
    teacher_name: str
    subject: str = ""
    total_sessions: int = Field(ge=0)
    total_hours: float = Field(ge=0)
    load_share: float = Field(ge=0, le=1)
    rank: int | None = Field(default=None, ge=1)


class TeacherWorkloadBucket(BaseModel):
    label: str
    min_sessions: int = Field(ge=0)
    max_sessions: int | None = Field(default=None, ge=0)
    teacher_count: int = Field(ge=0)


class TeacherWorkloadDistribution(BaseModel):
    schedule_set_id: str
    schedule_version_id: str | None
    schedule_version_no: int | None
    date_from: date | None
    date_to: date | None
    total_teachers: int = Field(ge=0)
    assigned_teachers: int = Field(ge=0)
    total_sessions: int = Field(ge=0)
    total_hours: float = Field(ge=0)
    top_teachers: list[TeacherWorkloadItem] = Field(default_factory=list)
    teachers: list[TeacherWorkloadItem] = Field(default_factory=list)
    buckets: list[TeacherWorkloadBucket] = Field(default_factory=list)


class RoomSlotHeatmapCell(BaseModel):
    weekday: str
    slot_business_id: str
    slot_label: str
    period: str
    sequence: int
    occupied_room_slots: int = Field(ge=0)
    available_room_slots: int = Field(ge=0)
    occupancy_rate: float = Field(ge=0, le=1)
    observed_days: int = Field(ge=0)


class RoomPeriodHeatmapCell(BaseModel):
    """7×3 汇总格：周一至周日 × 上午/下午/晚自习。"""

    weekday: str
    period: str
    occupied_room_slots: int = Field(ge=0)
    available_room_slots: int = Field(ge=0)
    occupancy_rate: float = Field(ge=0, le=1)
    observed_days: int = Field(ge=0)
    slot_count: int = Field(ge=0)


class RoomSlotHeatmap(BaseModel):
    schedule_set_id: str
    schedule_version_id: str | None
    schedule_version_no: int | None
    date_from: date | None
    date_to: date | None
    effective_date_from: date | None
    effective_date_to: date | None
    total_rooms: int = Field(ge=0)
    cells: list[RoomSlotHeatmapCell] = Field(default_factory=list)
    period_cells: list[RoomPeriodHeatmapCell] = Field(default_factory=list)


class OptimizationPenaltyItem(BaseModel):
    rule_id: str
    constraint_type: str
    label: str
    hardness: str = "soft"
    weight: float = Field(ge=0)
    violations: int | None = Field(default=None, ge=0)
    evaluated_count: int = Field(ge=0)
    penalty: float | None = Field(default=None, ge=0)
    satisfaction_rate: float | None = Field(default=None, ge=0, le=1)
    source: str = "declared"


class OptimizationPenaltyBreakdown(BaseModel):
    schedule_set_id: str
    solver_run_id: str | None
    solver_status: str | None
    objective_value: float | None
    best_bound: float | None
    total_soft_penalty: float | None
    unattributed_objective_value: float | None
    reconciliation_error: float | None = Field(default=None, ge=0)
    breakdown_source: str
    scope_source: str
    evaluated_assignment_count: int = Field(ge=0)
    soft_constraints: list[OptimizationPenaltyItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SyncHealthResourceItem(BaseModel):
    resource: str
    total_syncs: int = Field(ge=0)
    completed_syncs: int = Field(ge=0)
    failed_syncs: int = Field(ge=0)
    records_read: int = Field(ge=0)
    records_written: int = Field(ge=0)


class SyncHealthTelemetry(ShanghaiTimestampResponse):
    schedule_set_id: str
    window_start: datetime
    window_end: datetime
    total_syncs: int = Field(ge=0)
    completed_syncs: int = Field(ge=0)
    failed_syncs: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    records_read: int = Field(ge=0)
    records_written: int = Field(ge=0)
    average_duration_ms: float | None = Field(default=None, ge=0)
    latest_sync_at: datetime | None
    duration_samples: int = Field(ge=0)
    retry_samples: int = Field(ge=0)
    resources: list[SyncHealthResourceItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OverviewAnalyticsResponse(ShanghaiTimestampResponse):
    """总览页可选的分析维度；不改变原有 ``/overview`` 响应契约。"""

    schedule_set_id: str
    generated_at: datetime
    teacher_workload: TeacherWorkloadDistribution
    room_heatmap: RoomSlotHeatmap
    optimization_penalties: OptimizationPenaltyBreakdown
    sync_health: SyncHealthTelemetry


class AIProviderConfigurationInput(BaseModel):
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    # 官方端点（DeepSeek / 智谱）的思考强度；auto 对抽取类任务按 low。见 services/ai_providers.py。
    reasoning_effort: Literal["auto", "low", "high", "max"] = "auto"


class AIProviderConfigurationResponse(BaseModel):
    configured: bool
    source: Literal["environment", "frontend", "none"]
    provider: str | None
    base_url: str | None
    api_key_configured: bool
    model: str | None
    # 按接口地址认出的预设卡片（custom = 自定义）、模型厂商族、是否官方端点。
    preset: str | None = None
    family: Literal["deepseek", "glm", "generic"] | None = None
    official: bool = False
    reasoning_effort: Literal["auto", "low", "high", "max"] = "auto"


class AIProviderPresetResponse(BaseModel):
    """设置页「添加供应商」的一张预设卡片。"""

    id: str
    label: str
    family: Literal["deepseek", "glm", "generic"]
    base_url: str
    models: list[str]
    key_url: str
    docs_url: str
    notes: list[str]


class AIConnectionTestResponse(BaseModel):
    """连接测试结果：失败也返回 200 + ok=false，让页面就地显示原因。"""

    ok: bool
    message: str
    latency_ms: int | None = None
    model: str | None = None
    family: Literal["deepseek", "glm", "generic"] | None = None
    official: bool | None = None
    thinking_returned: bool | None = None
    # 流式通道（网页一句话排课实际走它）是否也通；失败时网页会自动回退到非流式。
    stream_ok: bool | None = None
    usage: dict[str, Any] | None = None


class IntegrationManifestResponse(BaseModel):
    """GET /integrations 清单项：集成元数据 + 运行时状态（不触发 verify 探测）。"""

    id: str
    name: str
    description: str
    capabilities: list[str]
    status: Literal["available", "configured", "planned"]
    docs_url: str


class IntegrationConfigurationInput(BaseModel):
    """PUT /integrations/{id}/configuration：按 manifest config schema 平铺填写。

    语义为合并：密钥字段传非空值才覆盖（留空 = 保持已存密钥）；非密钥字段
    传值覆盖、传 null 清除、未传保持不变。
    """

    config: dict[str, Any] = Field(default_factory=dict)


class IntegrationConfigurationResponse(BaseModel):
    """凭据配置回读（脱敏）：密钥字段只给「是否已配置」布尔，永不明文回传。"""

    integration_id: str
    configured: bool
    config: dict[str, Any]
    secrets_configured: dict[str, bool]
    updated_at: datetime | None = None


class IntegrationVerifyResponse(BaseModel):
    """POST /integrations/{id}/verify：统一「测试连接」结果（≈ VerifyResult）。"""

    ok: bool
    detail: str


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


class FeishuConnectionResponse(ShanghaiTimestampResponse):
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


class FeishuOAuthStartResponse(ShanghaiTimestampResponse):
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


FeishuSyncResource = Literal[
    "teachers",
    "class_groups",
    "rooms",
    "time_slots",
    "course_sessions",
    "rules",
    "schedule",
    "public_summary",
    "public_adjustment_notice",
    "public_class_links",
]


def default_feishu_sync_resources() -> list[FeishuSyncResource]:
    return [
        "teachers",
        "class_groups",
        "rooms",
        "time_slots",
        "course_sessions",
        "rules",
        "schedule",
        "public_summary",
        "public_adjustment_notice",
        "public_class_links",
    ]


class FeishuSyncRequest(BaseModel):
    direction: Literal["export"] = "export"
    resource: FeishuSyncResource
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


class FeishuBatchSyncRequest(BaseModel):
    """Batch-export selected data tables into the active timetable workspace."""

    direction: Literal["export"] = "export"
    resources: list[FeishuSyncResource] = Field(
        default_factory=default_feishu_sync_resources,
        min_length=1,
    )
    workspace_id: str | None = None

    @model_validator(mode="after")
    def ensure_unique_resources(self) -> FeishuBatchSyncRequest:
        if len(set(self.resources)) != len(self.resources):
            raise ValueError("resources 不能包含重复资源")
        return self


class FeishuBatchSyncResponse(BaseModel):
    schedule_set_id: str
    status: Literal["completed", "partial", "failed"]
    completed_count: int
    failed_count: int
    records_read: int
    records_written: int
    results: list[IntegrationSyncResponse]


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
    schedule_set_id: str
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
        self.solver_rules = normalize_solver_rules(self.solver_rules)
        return self


class AssistantTaskConstraint(BaseModel):
    """解析产物中的任务级约束（TC-1，docs/roadmap/07-task-context.md §2.1）：
    只作用于本次任务，不落全局规则库（§2.5 红线：永不创建 Rule 行、不进快照
    checksum，转长期偏好/正式规则都是人的显式动作）。

    id 由解析侧生成（如 "tc-1"）；API 直调 /assistant/solve 时客户端必须提供，
    后端对缺失 id 按列表序号兜底生成并去重（§2.4）。字段给默认值而非必填：
    模型输出缺字段时由 _finalize 逐条降级（§2.2），不让一条坏数据废掉整次解析。
    """

    id: str = Field(default="", max_length=80)
    source_text: str = Field(default="", max_length=500)
    subject_type: Literal["teacher", "classroom", "cohort"] = "teacher"
    subject_ids: list[str] = Field(default_factory=list)
    slot_business_ids: list[str] = Field(default_factory=list)
    hardness: Literal["hard", "soft"] = "hard"
    # 这条要求对任务已有要求做什么（评审：同一个临时编号不能证明是替换）：
    # add=追加（默认；「另外也要…」）；replace=替换 target_id 指向的那一条（「改成…」）；
    # remove=取消 target_id 指向的那一条。target_id 只能取上下文 active_task_constraints
    # 里出现的稳定编号；指不到具体旧项时不自动删除任何东西（见 plan_task_constraint_revision）。
    op: Literal["add", "replace", "remove"] = "add"
    target_id: str | None = Field(default=None, max_length=80)

    @field_validator("target_id")
    @classmethod
    def strip_target_id(cls, value: str | None) -> str | None:
        """编号首尾的空白不是身份的一部分：规划、矛盾检测、动作身份必须认同一个目标。"""
        return (value.strip() or None) if value is not None else None

    def operation_identity(self) -> tuple[Any, ...]:
        """一次修改动作的身份（不是约束内容）：完全相同才是重复动作。

        取消只由 (op, target_id) 决定——它没有新的要求内容，其余字段是模型顺带填的，不算身份。
        """
        if self.op == "remove":
            return ("remove", self.target_id or "")
        return (
            self.op,
            self.target_id or "",
            self.hardness,
            self.subject_type,
            tuple(sorted(self.subject_ids)),
            tuple(sorted(self.slot_business_ids)),
        )


def contradictory_targets(constraints: list[AssistantTaskConstraint]) -> set[str]:
    """同一个既有要求（target_id）上出现了身份不同的动作（改成 X 又改成 Y、改了又取消……）。

    这种请求没法确定那条旧要求到底该怎么处理：不猜先后、不取第一条。解析规范化把它们都挡在确认卡上，
    求解请求的校验同样拒绝——直接调接口的客户端也不会因为顺序不同得到不同的结果。
    """
    identities: dict[str, set[tuple[Any, ...]]] = {}
    for item in constraints:
        if item.target_id:
            identities.setdefault(item.target_id, set()).add(item.operation_identity())
    return {target for target, seen in identities.items() if len(seen) > 1}


class AssistantMemoryAction(BaseModel):
    """解析产物中的记忆动作（TC-2，docs/roadmap/07-task-context.md §3.1）。

    basis 是模型的提名，不是授权结论：explicit 必须再过代码三条复核
    （主体/谓词/参数候选校验、target_entry_id 必须命中上下文注入的真实条目、
    原话命中显式声明词表），任何一条不过即按 inferred 降级——两种授权绝不混用
    （§3.2）。Aily 通道的输出一律按 inferred 处理（§3.2，外部通道授权降级）。
    """

    action: Literal["save_preference", "expire_preference", "update_preference"]
    basis: Literal["explicit", "inferred"]
    source_text: str = Field(default="", max_length=500)
    # save_preference：长期偏好主体与参数（同 PreferenceCreate 口径）。
    subject_type: PreferenceSubjectType = "teacher"
    subject_id: str | None = Field(default=None, max_length=50)
    predicate: str | None = Field(default=None, max_length=40)  # ∈ ALL_PREDICATES
    constraint: dict[str, Any] = Field(default_factory=dict)
    weight: int = Field(default=50, ge=0, le=100)
    # 缺省走 default_valid_until_for_scope（随学期失效）。
    valid_until: date | None = None
    # expire/update_preference：目标条目 id——只能来自上下文注入的真实条目。
    target_entry_id: str | None = Field(default=None, max_length=36)
    # 纠正为「只是那两天请假」时 rejected。
    target_status: Literal["expired", "rejected"] | None = None
    rejection_reason: Literal[
        "temporary_leave",
        "subject_misidentified",
        "wrong_generalization",
        "preference_changed",
        "other",
    ] | None = None
    note: str | None = Field(default=None, max_length=500)


class AssistantMemoryActionReceipt(BaseModel):
    """记忆动作执行回执（§3.1）：确认卡逐条展示。

    executed=已直接执行（entry_id 回填，附可修改/可撤销提示）；
    pending_confirmation=降级为收件箱候选（推测或 explicit 复核未过）；
    failed_degraded=无法降级为候选的动作（推测的失效/修订）——请人工处置。
    """

    action_id: str
    status: Literal["executed", "pending_confirmation", "failed_degraded"]
    entry_id: str | None = None  # executed 时回填
    receipt: str  # 一句话回执文案（可修改/可撤销提示固定后缀）


class AssistantSolveRequest(AilySolveRequest):
    wait: bool = False
    # 显式基准版本与目标课次：课表里选中某节课交给助手时随请求带上，求解基准与范围
    # 来自所选版本、所选课次，而不是重新从当前已发布版本、整批范围开始。
    parent_schedule_id: str | None = None
    course_business_ids: list[str] = Field(default_factory=list)
    # 目标验收闭环（MEM-C3）：一句话排课确认后可关联持久目标跟踪验收。
    goal_id: str | None = None
    # 确认卡绑定的任务要求版本：这张卡是基于任务的哪一版要求解析出来的（AssistantInterpretResponse
    # .task_basis_version 原样带回）。服务端只认这个「用户当时看到并确认的」版本：任务要求在此之后
    # 已经变了（别的标签页、清单里改过、这次确认其实已提交过）就 409，要求重新解析再确认——
    # 不会在点击提交时拿最新版本给一份旧解析重新盖章。缺省 = 不校验（直调/无任务/首次登记）。
    expected_task_basis_version: int | None = Field(default=None, ge=1)
    # 任务级约束的请求侧载体（TC-1 §2.1）：API 直调 /assistant/solve（无解析
    # 前置）时任务约束的入口；编译见 api._compile_task_constraints 的请求来源
    # 分支（TASK-req-* 命名空间，一次性生效不落库）。确认卡链路也经此字段把
    # 全量解析约束带回（§6.5），后端同键去重兜底。SolveRequest（手动路径）
    # 不加——手动求解的任务约束只能经由 goal 清单（单一事实源），不允许绕过
    # 确认卡直传；AilySolveRequest 不加（外部通道维持现状）。
    task_constraints: list[AssistantTaskConstraint] = Field(default_factory=list)
    # 服务端契约拦截（TC-3 §2.3）：仅用于显式声明「已知悉未实现要求」——非空
    # 即 422。这不是豁免口：AI 客户端把解析响应里的 unsupported 原样带回即被
    # 拒绝，留空数组=「本次指令没有未实现要求」。契约层拦截而非硬保证（调用方
    # 静默省略即绕过），人面向的主防线仍是前端确认卡闸。
    unsupported_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_contradictory_edits(self) -> AssistantSolveRequest:
        conflicts = contradictory_targets(self.task_constraints)
        if conflicts:
            raise ValueError(
                f"对同一条要求（{'、'.join(sorted(conflicts))}）给出了相互矛盾的修改，"
                "请分开说明想怎么处理这一条"
            )
        return self


class AssistantInterpretRequest(BaseModel):
    instruction: str = Field(min_length=2, max_length=2000)
    # 续办增量解析（TC-4 §4.3）：携带 goal_id 时 _interpret_context 注入既有
    # 任务上下文，提示词按「对既有状态的增量修改」解释这句话。
    goal_id: str | None = None
    # 这条用户指令的幂等标识（客户端每次提交生成一个；流式、同步回退、失败重试沿用
    # 同一个）：解析会直接执行「记住…」这类显式授权的记忆动作，重试必须只执行一次。
    # 缺省 = 不做幂等（直调/旧客户端维持原行为）。
    request_id: str | None = Field(default=None, min_length=8, max_length=64)


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
    unsupported_requirements: list[str] = Field(default_factory=list)
    coverage_warnings: list[str] = Field(default_factory=list)
    # 目标验收闭环（MEM-C3）：解析成功即预填清单草稿（业务范围→coverage、
    # 禁排语→forbidden_slot_free 占位、日期→date_range_match），前端可增删项
    # 后再创建 goal。草稿项的 params 结构同 GoalChecklistItem.params。
    goal_checklist_draft: list[GoalChecklistItem] = Field(default_factory=list)
    checklist_warnings: list[str] = Field(default_factory=list)
    # 任务级约束（TC-1 §2.1）：只作用于本次任务；hard 带参项已由 draft 生成器
    # 写进 goal_checklist_draft（§2.1b），soft 经确认后随 /assistant/solve 请求体
    # 回到后端落 goal.context.soft_task_constraints（§4.2 写入点①）。
    task_constraints: list[AssistantTaskConstraint] = Field(default_factory=list)
    # 这份解析是基于哪个任务的哪一版要求做出的（续办增量解析才有）：解析时注入给模型的
    # active_task_constraints（target_id 的来源）就是这一版。确认求解时随
    # AssistantSolveRequest.expected_task_basis_version 原样带回，服务端据此识别陈旧确认。
    task_goal_id: str | None = None
    task_basis_version: int | None = None
    # 记忆动作与执行回执（TC-2 §3.1）：explicit 已直接执行（回执 executed），
    # inferred/降级进收件箱候选（pending_confirmation），无法降级的动作
    # failed_degraded——逐条回执，绝不混用两种授权。
    memory_actions: list[AssistantMemoryAction] = Field(default_factory=list)
    memory_action_receipts: list[AssistantMemoryActionReceipt] = Field(default_factory=list)
    summary: str
    # 模型思考过程（reasoning_content 与 <think> 块拼接）；Aily 通道与无思考模型为 None。
    thinking: str | None = None


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


PublicLinkScope = Literal["class", "teacher", "school"]


class PublicLinkCreate(BaseModel):
    """公开链接创建参数；明文 token 只在创建/轮换响应出现一次。"""

    scope: PublicLinkScope
    campus_id: str | None = None
    resource_business_id: str | None = None
    display_name: str | None = Field(default=None, max_length=160)
    expires_at: datetime | None = None
    show_teacher_names: bool = True
    note: str = Field(default="", max_length=255)


class PublicLinkResponse(BaseModel):
    """管理端链接视图：只有 token_hint，任何响应都不回传明文 token。"""

    id: str
    schedule_set_id: str
    scope: str
    campus_id: str | None
    resource_business_id: str | None
    display_name: str
    show_teacher_names: bool
    token_hint: str
    status: str
    expires_at: datetime | None
    last_seen_at: datetime | None
    access_count: int
    note: str
    created_at: datetime
    created_by: str | None


class PublicLinkSecretResponse(PublicLinkResponse):
    token: str
    public_url: str


class PublicLinkBatchItem(BaseModel):
    """按发布版本批量创建的成功项：明文 token 仅在本响应出现一次（06 §3 B5）。"""

    display_name: str
    url: str
    token: str


class PublicLinkBatchFailure(BaseModel):
    """批量创建的失败项：单个班级目标校验不过不中断其余班级。"""

    campus_id: str
    class_business_id: str
    class_name: str
    detail: str


class PublicLinkBatchResponse(BaseModel):
    created: list[PublicLinkBatchItem]
    failed: list[PublicLinkBatchFailure]


class PublicLinkScheduleRow(BaseModel):
    """公开课表白名单行：班级名/科目/课节名/教师姓名/教室/时间，无任何标识符。"""

    date: str
    weekday: str
    start: str
    end: str
    class_name: str
    subject: str
    lesson_name: str
    teacher_names: list[str]
    location: str


class PublicLinkAdjustment(BaseModel):
    type: str
    class_name: str
    course_name: str
    before_time: str
    after_time: str
    before_location: str
    after_location: str


class PublicLinkSchedulePayload(BaseModel):
    """class/teacher 范围的 schedule.json 响应（显式白名单，06 §3 A4）。"""

    scope: str
    display_name: str
    show_teacher_names: bool
    version_no: int | None
    published_at: str
    first_date: str
    last_date: str
    rows: list[PublicLinkScheduleRow]
    adjustments: list[PublicLinkAdjustment]


class PublicLinkDirectoryClass(BaseModel):
    class_business_id: str
    class_name: str
    session_count: int
    first_date: str
    last_date: str


class PublicLinkDirectoryPayload(BaseModel):
    """school 范围（督导公示）的班级索引响应。"""

    scope: str
    display_name: str
    version_no: int | None
    published_at: str
    classes: list[PublicLinkDirectoryClass]
