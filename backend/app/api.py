from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import tempfile
import time as time_module
from collections import Counter
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar
from typing import cast as type_cast
from urllib.parse import urlencode

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import ValidationError
from sqlalchemy import (
    ColumnElement,
    String,
    cast,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .config import PROJECT_ROOT, get_settings
from .db import get_db
from .integrations import registry as integration_registry
from .integrations.base import Capability, Integration
from .integrations.credentials import CredentialStore, IntegrationCredentialError
from .integrations.feishu.adapter import UNCONFIGURED_DETAIL as FEISHU_UNCONFIGURED_DETAIL
from .integrations.platform_api import clear_token_cache
from .models import (
    DEFAULT_SCHEDULE_SET_ID,
    AuditLog,
    CalendarEventBinding,
    Campus,
    ClassGroup,
    CourseSession,
    ImportMappingHistory,
    IntegrationSync,
    PreferenceEntry,
    PreferenceRejection,
    PublicLinkToken,
    RescheduleEvent,
    Room,
    Rule,
    ScheduleAssignment,
    ScheduleSet,
    ScheduleSetMember,
    ScheduleVersion,
    SolveGoal,
    SolverRun,
    Teacher,
    TimeSlot,
    User,
)
from .schemas import (
    AilyContextResponse,
    AilyRuleBatch,
    AilySolveRequest,
    AIProviderConfigurationInput,
    AIProviderConfigurationResponse,
    AssignmentResponse,
    AssistantInterpretRequest,
    AssistantInterpretResponse,
    AssistantSolveRequest,
    AuditLogResponse,
    BatchOperationResponse,
    CalendarConflict,
    CalendarEventBindingResponse,
    CalendarPublishRequest,
    CalendarPublishResponse,
    CampusCreate,
    CampusResponse,
    ClassGroupPayload,
    ClassGroupResponse,
    ClassGroupTrack,
    ConstraintCatalogEntry,
    CourseSessionBatchDelete,
    CourseSessionBatchUpdate,
    CourseSessionFilter,
    CourseSessionPayload,
    CourseSessionResponse,
    CourseSessionUpdate,
    FeishuAppConfigurationInput,
    FeishuAppConfigurationResponse,
    FeishuBatchSyncRequest,
    FeishuBatchSyncResponse,
    FeishuConnectionResponse,
    FeishuOAuthStartResponse,
    FeishuSyncRequest,
    FeishuWorkspaceCreate,
    FeishuWorkspaceResponse,
    GoalChecklistItem,
    GoalCreateRequest,
    GoalDetailResponse,
    GoalResponse,
    ImportColumnMapping,
    ImportCommitResponse,
    ImportHeaderCandidate,
    ImportMappingInput,
    ImportPreviewResponse,
    ImportPreviewStats,
    ImportResult,
    ImportSheetOverview,
    IntegrationConfigurationInput,
    IntegrationConfigurationResponse,
    IntegrationManifestResponse,
    IntegrationSyncResponse,
    IntegrationVerifyResponse,
    MasterDataBatchDelete,
    MiningRunResponse,
    OverviewAnalyticsResponse,
    OverviewResponse,
    PasswordChange,
    PreferenceConvertRequest,
    PreferenceCreate,
    PreferenceResponse,
    PreferenceTransition,
    PreferenceUpdate,
    PublicLinkBatchResponse,
    PublicLinkCreate,
    PublicLinkDirectoryPayload,
    PublicLinkResponse,
    PublicLinkSchedulePayload,
    PublicLinkSecretResponse,
    PublicScheduleShareItem,
    PublicScheduleSummary,
    RescheduleCreate,
    RescheduleResponse,
    RoomBatchUpdate,
    RoomPayload,
    RoomResponse,
    RuleCreate,
    RuleResponse,
    RuleTransition,
    RuleUpdate,
    ScheduleDiffItem,
    ScheduleDiffResponse,
    ScheduleResponse,
    ScheduleSetCreate,
    ScheduleSetMemberResponse,
    ScheduleSetMemberUpsert,
    ScheduleSetResponse,
    ScheduleSetUpdate,
    ScheduleSummaryResponse,
    SolveRequest,
    SolverRunExplanation,
    SolverRunResponse,
    TeacherBatchUpdate,
    TeacherPayload,
    TeacherResponse,
    TimeSlotBatchUpdate,
    TimeSlotPayload,
    TimeSlotResponse,
    TokenResponse,
    UserCreate,
    UserPasswordReset,
    UserResponse,
    UserRoleUpdate,
    UserStatusUpdate,
)
from .security import (
    create_access_token,
    get_current_user,
    hash_password,
    require_roles,
    verify_password,
)
from .services.ai import AIService, AIServiceError
from .services.converter_core import (
    TEMPLATE_HEADERS,
    import_canonical_rows,
    parse_template_records,
)
from .services.converter_zhengzhou import (
    CAMPUS_BUSINESS_ID,
    CAMPUS_NAME,
    OFFICIAL_VERSION_SUFFIX,
    WorkbookFormatError,
    import_schedule_workbook,
)
from .services.explain import (
    SOLVER_RULE_LABELS,
    build_explanation_facts,
    build_retry_instruction,
    deterministic_summary,
)
from .services.feishu import FeishuService, FeishuServiceError, json_text
from .services.goal import (
    GOAL_CHECKLIST_KINDS,
    build_checklist,
    draft_checklist_from_interpretation,
    ensure_bottom_line_items,
    goal_run_counts,
)
from .services.ics import build_public_calendar_ics, calendar_etag
from .services.import_mapping import (
    ColumnMapping,
    ImportMappingError,
    apply_historical_mapping,
    apply_manual_mapping,
    build_records,
    column_samples,
    detect_header_candidates,
    header_fingerprint,
    header_texts,
    history_payload,
    parse_uploaded_workbook,
    pick_default_sheet,
    suggest_mapping,
)
from .services.memory_solver import (
    ALL_PREDICATES,
    MINED_DEFAULT_CONFIDENCE,
    MINED_DEFAULT_WEIGHT,
    PREDICATE_SOLVER_PATHS,
    PREFERENCE_RULE_KINDS,
    PREFERENCE_TRANSITIONS,
    RULE_ACTOR_TYPES,
    candidate_entry_validity,
    compile_failed_state,
    compile_memory_state,
    default_valid_until_for_scope,
    deterministic_preference_candidates,
    learning_basis_events,
    mining_event_view,
    normalized_constraint,
    refresh_conflict_flags,
    resolve_conflicts_for_new_entry,
    validate_ai_candidates,
)
from .services.overview_analytics import build_overview_analytics
from .services.public_projection import (
    _assignment_rows,
    _current_published_schedule,
    _public_adjustment_notice_rows,
    _public_class_identity,
    _public_class_index_rows,
    _public_class_links_rows,
    _public_class_schedule_rows,
    _public_projection_key,  # noqa: F401  re-export：公开投影回归测试仍从 app.api 引用
    _public_projection_updated_at,
    _public_schedule_sort_key,
    _published_adjustment_count,
    public_class_payload,
    public_directory_payload,
    public_schedule_entries,
    public_teacher_payload,
)
from .services.snapshot import build_snapshot_payload, create_snapshot, version_course_map
from .services.solver import _has_date_information, _selected_sessions, _session_matches_rule
from .services.tasks import count_hard_conflicts, enqueue_solver_run, execute_solver_run
from .services.xlsx_io import export_schedule_xlsx
from .timezone import SHANGHAI_TZ, as_utc, shanghai_now

logger = logging.getLogger("tupai.feishu")

LOGIN_FAILURE_LIMIT = 8
LOGIN_LOCKOUT_MINUTES = 15
DEMO_AILY_KEY = "aily-demo-key"
SQLITE_WRITE_RETRY_DELAYS_SECONDS = (0.05, 0.15)
DATABASE_BUSY_DETAIL = "数据正在同步或被其他操作占用，请稍后重试"

WriteResult = TypeVar("WriteResult")


def _aware_utc(value: datetime) -> datetime:
    normalized = as_utc(value)
    assert normalized is not None
    return normalized


settings = get_settings()
router = APIRouter(prefix=settings.api_prefix)
Db = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminOrScheduler = Annotated[User, Depends(require_roles("admin", "scheduler"))]
AdminSchedulerOrApprover = Annotated[User, Depends(require_roles("admin", "scheduler", "approver"))]
Approver = Annotated[User, Depends(require_roles("admin", "approver"))]
Admin = Annotated[User, Depends(require_roles("admin"))]


SCHEDULE_SET_HEADER = "X-Schedule-Set-Id"
ACCESS_ORDER = {"viewer": 1, "scheduler": 2, "approver": 3}


def _ensure_default_schedule_set(db: Session) -> ScheduleSet:
    default = db.get(ScheduleSet, "default")
    if default is None:
        default = ScheduleSet(
            id="default",
            code="SET001",
            name="第一套课表",
            display_order=0,
            is_active=True,
        )
        db.add(default)
        db.flush()
    return default


def _role_allows_access(user: User, access_role: str) -> bool:
    if user.role == "admin":
        return True
    if access_role not in ACCESS_ORDER:
        return False
    if user.role == "viewer":
        return access_role == "viewer"
    if user.role == "scheduler":
        return access_role in {"viewer", "scheduler"}
    if user.role == "approver":
        return access_role in {"viewer", "approver"}
    return False


def _visible_schedule_sets(db: Session, user: User) -> list[tuple[ScheduleSet, str]]:
    _ensure_default_schedule_set(db)
    if user.role == "admin":
        schedule_sets = list(
            db.scalars(select(ScheduleSet).where(ScheduleSet.is_active.is_(True)))
        )
        return [
            (item, "approver")
            for item in sorted(schedule_sets, key=lambda item: (item.display_order, item.name))
        ]
    member_rows: list[tuple[ScheduleSet, str]] = [
        (schedule_set, access_role)
        for schedule_set, access_role in db.execute(
            select(ScheduleSet, ScheduleSetMember.access_role)
            .join(ScheduleSetMember, ScheduleSetMember.schedule_set_id == ScheduleSet.id)
            .where(
                ScheduleSet.is_active.is_(True),
                ScheduleSetMember.user_id == user.id,
                ScheduleSetMember.is_active.is_(True),
            )
        ).all()
    ]
    return sorted(member_rows, key=lambda row: (row[0].display_order, row[0].name))


def resolve_schedule_set(
    db: Db,
    user: CurrentUser,
    header_id: Annotated[str | None, Header(alias=SCHEDULE_SET_HEADER)] = None,
) -> ScheduleSet:
    visible = _visible_schedule_sets(db, user)
    by_id = {item.id: (item, access) for item, access in visible}
    if header_id:
        selected = by_id.get(header_id.strip())
        if selected is None:
            raise HTTPException(status_code=404, detail="当前账号没有该课表的访问权限")
        return selected[0]
    if len(visible) == 1:
        return visible[0][0]
    # Preserve existing integrations and bookmarked API calls during the upgrade
    # from a single timetable: when the legacy/default set is visible, it is the
    # deterministic fallback.  The web client still always sends the header once
    # its top-bar selector has loaded.
    if DEFAULT_SCHEDULE_SET_ID in by_id:
        return by_id[DEFAULT_SCHEDULE_SET_ID][0]
    if not visible:
        raise HTTPException(status_code=403, detail="当前账号尚未分配任何课表")
    raise HTTPException(status_code=409, detail="当前账号可访问多套课表，请先选择课表")


ScheduleScope = Annotated[ScheduleSet, Depends(resolve_schedule_set)]


def resolve_aily_schedule_scope(
    db: Db,
    header_id: Annotated[str | None, Header(alias=SCHEDULE_SET_HEADER)] = None,
) -> ScheduleSet:
    """Select the timetable used by a key-authenticated Aily call.

    Aily has no interactive user identity to resolve against the membership
    matrix. It can use the only active timetable automatically, but must send
    the same selector header as the web client once multiple sets exist.
    """
    schedule_set_id = header_id.strip() if header_id and header_id.strip() else None
    if schedule_set_id:
        schedule_set = db.scalar(
            select(ScheduleSet).where(
                ScheduleSet.id == schedule_set_id,
                ScheduleSet.is_active.is_(True),
            )
        )
        if schedule_set is None:
            raise HTTPException(status_code=404, detail="指定的课表方案不存在或已停用")
        return schedule_set

    active_sets = list(
        db.scalars(
            select(ScheduleSet)
            .where(ScheduleSet.is_active.is_(True))
            .order_by(ScheduleSet.display_order, ScheduleSet.name)
        )
    )
    if len(active_sets) == 1:
        return active_sets[0]
    if not active_sets:
        raise HTTPException(status_code=404, detail="没有可供 Aily 使用的启用课表方案")
    raise HTTPException(
        status_code=409,
        detail=f"当前存在多套课表，Aily 请求必须提供 {SCHEDULE_SET_HEADER}",
    )


AilyScheduleScope = Annotated[ScheduleSet, Depends(resolve_aily_schedule_scope)]


def _schedule_access(db: Session, user: User, schedule_set_id: str) -> str:
    if user.role == "admin":
        return "approver"
    member = db.scalar(
        select(ScheduleSetMember).where(
            ScheduleSetMember.schedule_set_id == schedule_set_id,
            ScheduleSetMember.user_id == user.id,
            ScheduleSetMember.is_active.is_(True),
        )
    )
    if member is None or not _role_allows_access(user, member.access_role):
        raise HTTPException(status_code=404, detail="当前账号没有该课表的访问权限")
    return member.access_role


def _require_schedule_access(db: Session, user: User, schedule_set_id: str, required: str) -> str:
    actual = _schedule_access(db, user, schedule_set_id)
    if ACCESS_ORDER[actual] < ACCESS_ORDER[required]:
        raise HTTPException(status_code=403, detail="当前账号没有执行该课表操作的权限")
    return actual


def resolve_viewer_scope(db: Db, user: CurrentUser, scope: ScheduleScope) -> ScheduleSet:
    _require_schedule_access(db, user, scope.id, "viewer")
    return scope


def resolve_scheduler_scope(db: Db, user: AdminOrScheduler, scope: ScheduleScope) -> ScheduleSet:
    _require_schedule_access(db, user, scope.id, "scheduler")
    return scope


def resolve_scheduler_or_approver_scope(
    db: Db, user: AdminSchedulerOrApprover, scope: ScheduleScope
) -> ScheduleSet:
    # Approvers rank above schedulers in a timetable's access matrix and need
    # this scope only for the published-data retry endpoint.
    _require_schedule_access(db, user, scope.id, "scheduler")
    return scope


def resolve_approver_scope(db: Db, user: Approver, scope: ScheduleScope) -> ScheduleSet:
    _require_schedule_access(db, user, scope.id, "approver")
    return scope


ViewerScope = Annotated[ScheduleSet, Depends(resolve_viewer_scope)]
SchedulerScope = Annotated[ScheduleSet, Depends(resolve_scheduler_scope)]
SchedulerOrApproverScope = Annotated[ScheduleSet, Depends(resolve_scheduler_or_approver_scope)]
ApproverScope = Annotated[ScheduleSet, Depends(resolve_approver_scope)]


def audit(
    db: Session,
    actor: User | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail or {},
        )
    )


def _is_transient_sqlite_lock(db: Session, exc: OperationalError) -> bool:
    """Recognize only SQLite's single-writer conflict, not arbitrary DB errors."""
    if db.get_bind().dialect.name != "sqlite":
        return False
    message = str(exc.orig or exc).lower()
    return "database is locked" in message or "database table is locked" in message


def _commit_write_with_sqlite_retry(
    db: Session, write: Callable[[], WriteResult]
) -> WriteResult:
    """Retry a short SQLite writer collision without splitting audit/state commits.

    A retry reruns the whole mutation after a rollback. That keeps the audit row
    and the state change in one transaction, so successful role changes retain
    their audit trail.
    """
    for attempt in range(len(SQLITE_WRITE_RETRY_DELAYS_SECONDS) + 1):
        try:
            result = write()
            db.commit()
            return result
        except OperationalError as exc:
            db.rollback()
            if not _is_transient_sqlite_lock(db, exc):
                raise
            if attempt == len(SQLITE_WRITE_RETRY_DELAYS_SECONDS):
                raise HTTPException(status_code=409, detail=DATABASE_BUSY_DETAIL) from exc
            time_module.sleep(SQLITE_WRITE_RETRY_DELAYS_SECONDS[attempt])

    raise AssertionError("unreachable")


def get_or_404(db: Session, model: type[Any], object_id: str) -> Any:
    instance = db.get(model, object_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return instance


def get_scoped_or_404(
    db: Session, model: type[Any], object_id: str, schedule_set: ScheduleSet
) -> Any:
    """Fetch a per-schedule-set object without trusting the request header alone."""
    instance = get_or_404(db, model, object_id)
    if getattr(instance, "schedule_set_id", schedule_set.id) != schedule_set.id:
        # Deliberately return the same shape as a missing object: IDs must not be
        # usable as a side channel for discovering another operator's schedules.
        raise HTTPException(status_code=404, detail="资源不存在")
    return instance


def require_scope_access(db: Session, user: User, scope: ScheduleSet, required: str) -> None:
    _require_schedule_access(db, user, scope.id, required)


def schedule_response(db: Session, schedule: ScheduleVersion) -> ScheduleResponse:
    rows = list(
        db.scalars(
            select(ScheduleAssignment).where(ScheduleAssignment.schedule_version_id == schedule.id)
        )
    )
    courses = version_course_map(db, schedule)
    if any(row.course_session_id not in courses for row in rows):
        raise HTTPException(status_code=409, detail="课表数据完整性错误：存在缺失或跨方案课次")
    return ScheduleResponse(
        id=schedule.id,
        version_no=schedule.version_no,
        name=schedule.name,
        status=schedule.status,
        parent_id=schedule.parent_id,
        solver_run_id=schedule.solver_run_id,
        metrics=schedule.metrics,
        published_at=schedule.published_at,
        created_at=schedule.created_at,
        assignments=[
            AssignmentResponse(
                course_session_id=row.course_session_id,
                course_business_id=courses[row.course_session_id].business_id,
                class_business_id=courses[row.course_session_id].class_business_id,
                teacher_business_id=courses[row.course_session_id].teacher_business_id,
                lesson_date=row.lesson_date,
                slot_business_id=row.slot_business_id,
                room_business_id=row.room_business_id,
                change_kind=row.change_kind,
                course=CourseSessionResponse.model_validate(courses[row.course_session_id]),
            )
            for row in rows
        ],
    )


@router.post("/auth/token", response_model=TokenResponse, tags=["auth"])
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: Db) -> TokenResponse:
    user = db.scalar(select(User).where(User.username == form.username))
    now = shanghai_now()
    if user and user.locked_until and _aware_utc(user.locked_until) > now:
        remaining = int((_aware_utc(user.locked_until) - now).total_seconds() // 60) + 1
        raise HTTPException(
            status_code=429, detail=f"登录失败次数过多，请在 {remaining} 分钟后重试"
        )
    if not user or not user.is_active or not verify_password(form.password, user.password_hash):
        if user is not None:
            # 逐次累加失败次数，达到阈值后锁定，避免公开默认口令被直接爆破。
            user.failed_login_count += 1
            if user.failed_login_count >= LOGIN_FAILURE_LIMIT:
                user.failed_login_count = 0
                user.locked_until = now + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
            db.commit()
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    return TokenResponse(
        access_token=create_access_token(user), user=UserResponse.model_validate(user)
    )


@router.get("/auth/me", response_model=UserResponse, tags=["auth"])
def current_user(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


def _schedule_set_response(db: Session, item: ScheduleSet, access_role: str) -> dict[str, Any]:
    current = db.scalar(
        select(ScheduleVersion)
        .where(
            ScheduleVersion.schedule_set_id == item.id,
            ScheduleVersion.status == "published",
        )
        .order_by(ScheduleVersion.published_at.desc(), ScheduleVersion.version_no.desc())
    )
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "display_order": item.display_order,
        "is_active": item.is_active,
        "access_role": access_role,
        "current_version_id": current.id if current else None,
        "current_version_name": current.name if current else None,
        "current_version_no": current.version_no if current else None,
    }


@router.get("/schedule-sets", response_model=list[ScheduleSetResponse], tags=["schedule-sets"])
def list_schedule_sets(db: Db, user: CurrentUser) -> list[dict[str, Any]]:
    return [
        _schedule_set_response(db, item, access)
        for item, access in _visible_schedule_sets(db, user)
    ]


@router.post(
    "/schedule-sets",
    response_model=ScheduleSetResponse,
    status_code=201,
    tags=["schedule-sets"],
)
def create_schedule_set(payload: ScheduleSetCreate, db: Db, user: Admin) -> dict[str, Any]:
    _ensure_default_schedule_set(db)
    count = int(db.scalar(select(func.count(ScheduleSet.id))) or 0)
    code = f"SET{count + 1:03d}"
    while db.scalar(select(ScheduleSet.id).where(ScheduleSet.code == code)):
        count += 1
        code = f"SET{count + 1:03d}"
    instance = ScheduleSet(
        code=code,
        name=payload.name,
        display_order=count,
        is_active=True,
        created_by=user.id,
    )
    db.add(instance)
    try:
        db.flush()
        audit(
            db,
            user,
            "create",
            "schedule_set",
            instance.id,
            {"name": instance.name, "code": code},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="课表名称已存在") from exc
    db.refresh(instance)
    return _schedule_set_response(db, instance, "approver")


@router.patch(
    "/schedule-sets/{schedule_set_id}",
    response_model=ScheduleSetResponse,
    tags=["schedule-sets"],
)
def rename_schedule_set(
    schedule_set_id: str, payload: ScheduleSetUpdate, db: Db, user: Admin
) -> dict[str, Any]:
    instance = get_or_404(db, ScheduleSet, schedule_set_id)
    instance.name = payload.name
    audit(db, user, "rename", "schedule_set", instance.id, {"name": payload.name})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="课表名称已存在") from exc
    db.refresh(instance)
    return _schedule_set_response(db, instance, "approver")


@router.get(
    "/schedule-sets/{schedule_set_id}/members",
    response_model=list[ScheduleSetMemberResponse],
    tags=["schedule-sets"],
)
def list_schedule_set_members(schedule_set_id: str, db: Db, user: Admin) -> list[dict[str, Any]]:
    get_or_404(db, ScheduleSet, schedule_set_id)
    rows = db.execute(
        select(ScheduleSetMember, User)
        .join(User, User.id == ScheduleSetMember.user_id)
        .where(ScheduleSetMember.schedule_set_id == schedule_set_id)
        .order_by(User.username)
    ).all()
    return [
        {
            "id": member.id,
            "schedule_set_id": member.schedule_set_id,
            "user_id": member.user_id,
            "username": target.username,
            "user_role": target.role,
            "access_role": member.access_role,
            "is_active": member.is_active,
            "granted_by": member.granted_by,
            "created_at": member.created_at,
        }
        for member, target in rows
    ]


@router.put(
    "/schedule-sets/{schedule_set_id}/members/{user_id}",
    response_model=ScheduleSetMemberResponse,
    tags=["schedule-sets"],
)
def upsert_schedule_set_member(
    schedule_set_id: str,
    user_id: str,
    payload: ScheduleSetMemberUpsert,
    db: Db,
    user: Admin,
) -> dict[str, Any]:
    if payload.user_id != user_id:
        raise HTTPException(status_code=422, detail="路径用户与请求体用户不一致")
    get_or_404(db, ScheduleSet, schedule_set_id)
    target = get_or_404(db, User, user_id)
    if target.role == "admin":
        raise HTTPException(status_code=409, detail="管理员默认可管理全部课表，无需单独分配")
    if not target.is_active:
        raise HTTPException(status_code=409, detail="停用账号不能分配课表权限")
    if not _role_allows_access(target, payload.access_role):
        raise HTTPException(status_code=422, detail="课表权限不能高于成员的全局角色")
    member = db.scalar(
        select(ScheduleSetMember).where(
            ScheduleSetMember.schedule_set_id == schedule_set_id,
            ScheduleSetMember.user_id == user_id,
        )
    )
    if member is None:
        member = ScheduleSetMember(
            schedule_set_id=schedule_set_id,
            user_id=user_id,
            granted_by=user.id,
        )
        db.add(member)
    member.access_role = payload.access_role
    member.is_active = True
    member.granted_by = user.id
    audit(
        db,
        user,
        "grant_schedule_access",
        "schedule_set_member",
        member.id,
        {
            "schedule_set_id": schedule_set_id,
            "user_id": user_id,
            "access_role": payload.access_role,
        },
    )
    db.commit()
    db.refresh(member)
    return {
        "id": member.id,
        "schedule_set_id": member.schedule_set_id,
        "user_id": member.user_id,
        "username": target.username,
        "user_role": target.role,
        "access_role": member.access_role,
        "is_active": member.is_active,
        "granted_by": member.granted_by,
        "created_at": member.created_at,
    }


@router.delete(
    "/schedule-sets/{schedule_set_id}/members/{user_id}",
    status_code=204,
    tags=["schedule-sets"],
)
def revoke_schedule_set_member(schedule_set_id: str, user_id: str, db: Db, user: Admin) -> Response:
    get_or_404(db, ScheduleSet, schedule_set_id)
    member = db.scalar(
        select(ScheduleSetMember).where(
            ScheduleSetMember.schedule_set_id == schedule_set_id,
            ScheduleSetMember.user_id == user_id,
        )
    )
    if member is None:
        return Response(status_code=204)
    member.is_active = False
    audit(
        db,
        user,
        "revoke_schedule_access",
        "schedule_set_member",
        member.id,
        {"schedule_set_id": schedule_set_id, "user_id": user_id},
    )
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 公开课表层 · 管理端点（docs/roadmap/06 §3 A3）。
# 与管理端 RBAC 正交的 capability-link：签发/轮换/停用 = admin/scheduler；
# 明文 token 只在创建与轮换响应返回一次，列表与审计日志只落 token_hint。
# ---------------------------------------------------------------------------

PublicLinkManager = Annotated[User, Depends(require_roles("admin", "scheduler"))]
PUBLIC_LINK_SEEN_THROTTLE = timedelta(minutes=10)


def resolve_public_link_scope(
    schedule_set_id: str, db: Db, user: PublicLinkManager
) -> ScheduleSet:
    """按路径参数定位课表方案，并要求 viewer 级访问（作用域语义同 ViewerScope）。"""

    scope = get_or_404(db, ScheduleSet, schedule_set_id)
    _require_schedule_access(db, user, scope.id, "viewer")
    return scope


PublicLinkScope = Annotated[ScheduleSet, Depends(resolve_public_link_scope)]


def _public_link_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_link_hint(token: str) -> str:
    return token[-4:]


def _public_link_status(link: PublicLinkToken, now: datetime) -> str:
    if link.revoked_at is not None:
        return "revoked"
    if link.expires_at is not None and _aware_utc(link.expires_at) <= now:
        return "expired"
    return "active"


def _public_link_view(link: PublicLinkToken) -> dict[str, Any]:
    """管理端链接视图；明文 token 永不进入任何列表/详情响应（06 §3 A3）。"""

    return {
        "id": link.id,
        "schedule_set_id": link.schedule_set_id,
        "scope": link.scope,
        "campus_id": link.campus_id,
        "resource_business_id": link.resource_business_id,
        "display_name": link.display_name,
        "show_teacher_names": link.show_teacher_names,
        "token_hint": link.token_hint,
        "status": _public_link_status(link, shanghai_now()),
        "expires_at": link.expires_at,
        "last_seen_at": link.last_seen_at,
        "access_count": link.access_count,
        "note": link.note,
        "created_at": link.created_at,
        "created_by": link.created_by,
    }


def _public_link_url(token: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}/public/t/{token}"


def _resolve_public_link_target(
    db: Session, payload: PublicLinkCreate, scope: ScheduleSet
) -> str:
    """创建时校验链接目标存在，并返回默认 display_name（06 §3 A3）。"""

    if payload.scope == "school":
        if payload.campus_id or payload.resource_business_id:
            raise HTTPException(status_code=422, detail="school 范围不绑定校区或班级/教师")
        return scope.name
    if not payload.campus_id or not payload.resource_business_id:
        raise HTTPException(
            status_code=422,
            detail="class/teacher 范围必须提供 campus_id 与 resource_business_id",
        )
    campus = db.scalar(
        select(Campus).where(
            Campus.schedule_set_id == scope.id,
            Campus.id == payload.campus_id,
        )
    )
    if campus is None:
        raise HTTPException(status_code=422, detail="校区不存在或不属于当前课表方案")
    if payload.scope == "class":
        class_group = db.scalar(
            select(ClassGroup).where(
                ClassGroup.schedule_set_id == scope.id,
                ClassGroup.campus_id == payload.campus_id,
                ClassGroup.business_id == payload.resource_business_id,
            )
        )
        if class_group is None:
            raise HTTPException(status_code=422, detail="班级不存在")
        return class_group.name
    teacher = db.scalar(
        select(Teacher).where(
            Teacher.schedule_set_id == scope.id,
            Teacher.campus_id == payload.campus_id,
            Teacher.business_id == payload.resource_business_id,
        )
    )
    if teacher is None:
        raise HTTPException(status_code=422, detail="教师不存在")
    return teacher.name


def _get_managed_public_link(db: Session, user: User, link_id: str) -> PublicLinkToken:
    link = db.get(PublicLinkToken, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    # 链接管理入口同样要求对所属课表方案的 viewer 级访问。
    _require_schedule_access(db, user, link.schedule_set_id, "viewer")
    return link


@router.get(
    "/schedule-sets/{schedule_set_id}/public-links",
    response_model=list[PublicLinkResponse],
    tags=["public-links"],
)
def list_public_links(
    db: Db, user: PublicLinkManager, scope: PublicLinkScope
) -> list[dict[str, Any]]:
    return [
        _public_link_view(link)
        for link in db.scalars(
            select(PublicLinkToken)
            .where(PublicLinkToken.schedule_set_id == scope.id)
            .order_by(PublicLinkToken.created_at.desc(), PublicLinkToken.id)
        )
    ]


@router.post(
    "/schedule-sets/{schedule_set_id}/public-links",
    response_model=PublicLinkSecretResponse,
    status_code=201,
    tags=["public-links"],
)
def create_public_link(
    payload: PublicLinkCreate, db: Db, user: PublicLinkManager, scope: PublicLinkScope
) -> dict[str, Any]:
    default_name = _resolve_public_link_target(db, payload, scope)
    display_name = (payload.display_name or default_name).strip() or default_name
    expires_at = payload.expires_at or shanghai_now() + timedelta(
        days=settings.public_default_ttl_days
    )
    if _aware_utc(expires_at) <= shanghai_now():
        raise HTTPException(status_code=422, detail="expires_at 必须晚于当前时间")
    token = secrets.token_urlsafe(32)
    link = PublicLinkToken(
        schedule_set_id=scope.id,
        token_hash=_public_link_hash(token),
        token_hint=_public_link_hint(token),
        scope=payload.scope,
        campus_id=payload.campus_id,
        resource_business_id=payload.resource_business_id,
        display_name=display_name,
        show_teacher_names=payload.show_teacher_names,
        created_by=user.id,
        expires_at=expires_at,
        note=payload.note,
    )
    db.add(link)
    audit(
        db,
        user,
        "create",
        "public_link",
        link.id,
        {
            "schedule_set_id": scope.id,
            "scope": payload.scope,
            "resource_business_id": payload.resource_business_id,
            "token_hint": link.token_hint,
        },
    )
    db.commit()
    db.refresh(link)
    response = _public_link_view(link)
    response["token"] = token
    response["public_url"] = _public_link_url(token)
    return response


@router.post(
    "/schedule-sets/{schedule_set_id}/public-links/batch",
    response_model=PublicLinkBatchResponse,
    tags=["public-links"],
)
def create_public_links_batch(
    db: Db, user: PublicLinkManager, scope: PublicLinkScope
) -> dict[str, Any]:
    """按当前发布版本（public_class_index）为全部班级批量创建 class 链接。

    校验、默认有效期与审计沿用单条创建（06 §3 B5）；明文 token 只在本响应
    出现一次，库内只存哈希；单个班级目标校验不过不中断其余班级。
    """

    expires_at = shanghai_now() + timedelta(days=settings.public_default_ttl_days)
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for row in _public_class_index_rows(db, scope.id):
        # 班级标识形如 "<campus_id>:<business_id>"（public_projection 口径）。
        identity = str(row.get("班级标识") or "")
        campus_id, _, class_business_id = identity.rpartition(":")
        class_name = str(row.get("班级名称") or "未分班")
        try:
            # 与单条创建同一份目标校验（class 查 ClassGroup、campus 归属）。
            display_name = _resolve_public_link_target(
                db,
                PublicLinkCreate(
                    scope="class",
                    campus_id=campus_id,
                    resource_business_id=class_business_id,
                ),
                scope,
            )
        except HTTPException as exc:
            failed.append(
                {
                    "campus_id": campus_id,
                    "class_business_id": class_business_id,
                    "class_name": class_name,
                    "detail": str(exc.detail),
                }
            )
            continue
        token = secrets.token_urlsafe(32)
        link = PublicLinkToken(
            schedule_set_id=scope.id,
            token_hash=_public_link_hash(token),
            token_hint=_public_link_hint(token),
            scope="class",
            campus_id=campus_id,
            resource_business_id=class_business_id,
            display_name=display_name,
            show_teacher_names=True,
            created_by=user.id,
            expires_at=expires_at,
            note="按发布版本批量生成",
        )
        db.add(link)
        audit(
            db,
            user,
            "create",
            "public_link",
            link.id,
            {
                "schedule_set_id": scope.id,
                "scope": "class",
                "resource_business_id": class_business_id,
                "token_hint": link.token_hint,
            },
        )
        created.append(
            {
                "display_name": link.display_name,
                "url": _public_link_url(token),
                "token": token,
            }
        )
    db.commit()
    return {"created": created, "failed": failed}


@router.post(
    "/public-links/{link_id}/rotate",
    response_model=PublicLinkSecretResponse,
    tags=["public-links"],
)
def rotate_public_link(link_id: str, db: Db, user: PublicLinkManager) -> dict[str, Any]:
    link = _get_managed_public_link(db, user, link_id)
    if link.revoked_at is not None:
        raise HTTPException(status_code=409, detail="链接已停用，不能轮换")
    link.revoked_at = shanghai_now()
    token = secrets.token_urlsafe(32)
    rotated = PublicLinkToken(
        schedule_set_id=link.schedule_set_id,
        token_hash=_public_link_hash(token),
        token_hint=_public_link_hint(token),
        scope=link.scope,
        campus_id=link.campus_id,
        resource_business_id=link.resource_business_id,
        display_name=link.display_name,
        show_teacher_names=link.show_teacher_names,
        created_by=user.id,
        expires_at=link.expires_at,
        note=link.note,
    )
    db.add(rotated)
    audit(
        db,
        user,
        "rotate",
        "public_link",
        rotated.id,
        {
            "previous_link_id": link.id,
            "schedule_set_id": link.schedule_set_id,
            "token_hint": rotated.token_hint,
        },
    )
    db.commit()
    db.refresh(rotated)
    response = _public_link_view(rotated)
    response["token"] = token
    response["public_url"] = _public_link_url(token)
    return response


@router.delete("/public-links/{link_id}", status_code=204, tags=["public-links"])
def revoke_public_link(link_id: str, db: Db, user: PublicLinkManager) -> Response:
    link = _get_managed_public_link(db, user, link_id)
    if link.revoked_at is None:
        link.revoked_at = shanghai_now()
        audit(
            db,
            user,
            "revoke",
            "public_link",
            link.id,
            {"schedule_set_id": link.schedule_set_id, "token_hint": link.token_hint},
        )
        db.commit()
    return Response(status_code=204)


@router.get("/users", response_model=list[UserResponse], tags=["accounts"])
def list_users(db: Db, user: Admin) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at, User.username)))


@router.post("/users", response_model=UserResponse, status_code=201, tags=["accounts"])
def create_user(payload: UserCreate, db: Db, user: Admin) -> User:
    instance = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        created_by=user.id,
    )
    db.add(instance)
    try:
        db.flush()
        default_set = _ensure_default_schedule_set(db)
        if payload.role != "admin":
            db.add(
                ScheduleSetMember(
                    schedule_set_id=default_set.id,
                    user_id=instance.id,
                    access_role=("approver" if payload.role == "approver" else payload.role),
                    granted_by=user.id,
                )
            )
        audit(
            db,
            user,
            "create",
            "user",
            instance.id,
            {"username": instance.username, "role": instance.role},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    db.refresh(instance)
    return instance


@router.patch("/users/{user_id}/status", response_model=UserResponse, tags=["accounts"])
def update_user_status(user_id: str, payload: UserStatusUpdate, db: Db, user: Admin) -> User:
    target = get_or_404(db, User, user_id)
    if target.id == user.id and not payload.is_active:
        raise HTTPException(status_code=409, detail="当前登录账号不能停用自己")
    if target.role == "admin" and target.is_active and not payload.is_active:
        active_admins = int(
            db.scalar(
                select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))
            )
            or 0
        )
        if active_admins <= 1:
            raise HTTPException(status_code=409, detail="至少需要保留一个启用中的管理员")
    target.is_active = payload.is_active
    audit(
        db,
        user,
        "enable" if payload.is_active else "disable",
        "user",
        target.id,
        {"username": target.username},
    )
    db.commit()
    db.refresh(target)
    return target


@router.patch("/users/{user_id}/role", response_model=UserResponse, tags=["accounts"])
def update_user_role(user_id: str, payload: UserRoleUpdate, db: Db, user: Admin) -> User:
    target = get_or_404(db, User, user_id)
    if target.role == payload.role:
        return target
    actor_id = user.id

    def apply_role_change() -> User:
        # Re-read every retry: rollback discards both the membership changes and
        # the pending audit entry from the failed transaction.
        actor = get_or_404(db, User, actor_id)
        target = get_or_404(db, User, user_id)
        if target.role == payload.role:
            return target
        if target.role == "admin":
            active_admins = int(
                db.scalar(
                    select(func.count(User.id)).where(
                        User.role == "admin", User.is_active.is_(True)
                    )
                )
                or 0
            )
            if active_admins <= 1:
                raise HTTPException(status_code=409, detail="至少需要保留一个启用中的管理员")
        previous = target.role
        target.role = payload.role
        access_ceiling = payload.role if payload.role in ACCESS_ORDER else None
        adjusted_memberships = 0
        restored_default_membership = False
        if access_ceiling is not None:
            default_set = _ensure_default_schedule_set(db)
            memberships = list(
                db.scalars(
                    select(ScheduleSetMember).where(
                        ScheduleSetMember.user_id == target.id,
                    )
                )
            )
            default_membership = next(
                (item for item in memberships if item.schedule_set_id == default_set.id),
                None,
            )
            if default_membership is None:
                db.add(
                    ScheduleSetMember(
                        schedule_set_id=default_set.id,
                        user_id=target.id,
                        access_role=access_ceiling,
                        granted_by=actor.id,
                    )
                )
                restored_default_membership = True
            elif (
                not default_membership.is_active
                or default_membership.access_role != access_ceiling
            ):
                default_membership.access_role = access_ceiling
                default_membership.is_active = True
                default_membership.granted_by = actor.id
                restored_default_membership = True

            for membership in memberships:
                if membership.schedule_set_id == default_set.id or not membership.is_active:
                    continue
                if not _role_allows_access(target, membership.access_role):
                    membership.access_role = access_ceiling
                    adjusted_memberships += 1
        audit(
            db,
            actor,
            "update_role",
            "user",
            target.id,
            {
                "username": target.username,
                "from": previous,
                "to": payload.role,
                "adjusted_schedule_memberships": adjusted_memberships,
                "restored_default_schedule_membership": restored_default_membership,
            },
        )
        return target

    target = _commit_write_with_sqlite_retry(db, apply_role_change)
    db.refresh(target)
    return target


@router.post("/users/{user_id}/reset-password", status_code=204, tags=["accounts"])
def reset_user_password(user_id: str, payload: UserPasswordReset, db: Db, user: Admin) -> Response:
    target = get_or_404(db, User, user_id)
    target.password_hash = hash_password(payload.password)
    # 重置密码必须让该账号手里的旧令牌立即失效。
    target.password_changed_at = shanghai_now()
    target.token_version += 1
    target.failed_login_count = 0
    target.locked_until = None
    audit(db, user, "reset_password", "user", target.id, {"username": target.username})
    db.commit()
    return Response(status_code=204)


@router.post("/auth/change-password", status_code=204, tags=["auth"])
def change_own_password(payload: PasswordChange, db: Db, user: CurrentUser) -> Response:
    """用户自助改密。此前只能由管理员重置，临时口令只能线下传达。"""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = shanghai_now()
    user.token_version += 1
    audit(db, user, "change_password", "user", user.id, {"username": user.username})
    db.commit()
    return Response(status_code=204)


@router.get("/health/live", tags=["operations"])
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", tags=["operations"])
def health_ready(db: Db) -> dict[str, str]:
    db.scalar(select(func.count(User.id)))
    return {"status": "ready"}


@router.get("/overview", response_model=OverviewResponse, tags=["overview"])
def overview(db: Db, user: CurrentUser, scope: ViewerScope) -> OverviewResponse:
    latest_run = db.scalar(
        select(SolverRun)
        .where(SolverRun.schedule_set_id == scope.id)
        .order_by(SolverRun.created_at.desc())
    )
    latest_schedule = db.scalar(
        select(ScheduleVersion)
        .where(ScheduleVersion.schedule_set_id == scope.id)
        .order_by(ScheduleVersion.version_no.desc())
    )
    latest_sync = db.scalar(
        select(IntegrationSync)
        .where(IntegrationSync.schedule_set_id == scope.id)
        .order_by(IntegrationSync.created_at.desc())
    )
    counts = {
        "teachers": int(
            db.scalar(select(func.count(Teacher.id)).where(Teacher.schedule_set_id == scope.id))
            or 0
        ),
        "class_groups": int(
            db.scalar(
                select(func.count(ClassGroup.id)).where(ClassGroup.schedule_set_id == scope.id)
            )
            or 0
        ),
        "rooms": int(
            db.scalar(select(func.count(Room.id)).where(Room.schedule_set_id == scope.id)) or 0
        ),
        "time_slots": int(
            db.scalar(select(func.count(TimeSlot.id)).where(TimeSlot.schedule_set_id == scope.id))
            or 0
        ),
        "course_sessions": int(
            db.scalar(
                select(func.count(CourseSession.id)).where(
                    CourseSession.schedule_set_id == scope.id, CourseSession.is_active.is_(True)
                )
            )
            or 0
        ),
        "schedule_versions": int(
            db.scalar(
                select(func.count(ScheduleVersion.id)).where(
                    ScheduleVersion.schedule_set_id == scope.id
                )
            )
            or 0
        ),
    }
    return OverviewResponse(
        counts=counts,
        latest_run=SolverRunResponse.model_validate(latest_run) if latest_run else None,
        latest_schedule=schedule_response(db, latest_schedule) if latest_schedule else None,
        pending_rules=int(
            db.scalar(
                select(func.count(Rule.id)).where(
                    Rule.schedule_set_id == scope.id,
                    Rule.status == "awaiting_confirmation",
                )
            )
            or 0
        ),
        pending_reschedules=int(
            db.scalar(
                select(func.count(RescheduleEvent.id)).where(
                    RescheduleEvent.schedule_set_id == scope.id,
                    RescheduleEvent.status.in_(["pending", "candidate_ready"]),
                )
            )
            or 0
        ),
        latest_sync_status=latest_sync.status if latest_sync else None,
    )


@router.get(
    "/overview/analytics",
    response_model=OverviewAnalyticsResponse,
    tags=["overview"],
)
def overview_analytics(
    db: Db,
    user: CurrentUser,
    scope: ViewerScope,
    schedule_id: Annotated[
        str | None,
        Query(description="指定课表版本；为空时优先当前发布版本，再取最新版本"),
    ] = None,
    date_from: Annotated[date | None, Query(description="统计起始日期（含）")] = None,
    date_to: Annotated[date | None, Query(description="统计结束日期（含）")] = None,
    sync_window_hours: Annotated[
        int,
        Query(ge=1, le=24 * 30, description="飞书同步遥测回看小时数"),
    ] = 24,
) -> OverviewAnalyticsResponse:
    """Return dashboard analytics without expanding the legacy overview DTO."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from 必须早于或等于 date_to")
    if schedule_id:
        schedule = get_scoped_or_404(db, ScheduleVersion, schedule_id, scope)
    else:
        schedule = db.scalar(
            select(ScheduleVersion)
            .where(
                ScheduleVersion.schedule_set_id == scope.id,
                ScheduleVersion.status == "published",
            )
            .order_by(ScheduleVersion.published_at.desc(), ScheduleVersion.version_no.desc())
        )
        if schedule is None:
            schedule = db.scalar(
                select(ScheduleVersion)
                .where(ScheduleVersion.schedule_set_id == scope.id)
                .order_by(ScheduleVersion.version_no.desc())
            )
    return OverviewAnalyticsResponse.model_validate(
        build_overview_analytics(
            db,
            scope.id,
            schedule,
            date_from=date_from,
            date_to=date_to,
            sync_window_hours=sync_window_hours,
        )
    )


def build_import_result(result: dict[str, Any], source: str) -> ImportResult:
    """把核心管线的质量报告映射为导入响应（模板直通与智能导入共用）。"""
    dropped = result["warnings"]["dropped_placeholder_room"]
    preprocessing = result["duplicate_lessons"]
    return ImportResult(
        source=source,
        campuses=1,
        teachers=result["teachers"],
        class_groups=result["class_groups"],
        rooms=result["rooms"],
        time_slots=result["time_slots"],
        course_sessions=result["course_sessions_created"],
        rules=0,
        rows_total=result["rows_total"],
        rows_dropped_placeholder_room=dropped["dropped_rows"],
        dropped_lesson_groups=dropped["dropped_lesson_groups"],
        dropped_classes=dropped["affected_classes"],
        rows_kept=result["rows_kept"],
        rows_deduped=result["rows_deduped"],
        preprocessed_demands=preprocessing["preprocessed_demands"],
        collapsed_source_variants=preprocessing["collapsed_source_variants"],
        multi_product_demands=preprocessing["multi_product_demands"],
        multi_lesson_name_demands=preprocessing["multi_lesson_name_demands"],
        multi_slot_demands=preprocessing["multi_slot_demands"],
        rows_skipped=result["rows_skipped"],
        skipped_examples=result["skipped_examples"],
        duplicate_lessons=result["duplicate_lessons"]["conflicting_lessons"],
        class_slot_conflicts=result["class_slot_conflicts"]["conflicting_groups"],
        orphans_deleted=result["orphans"]["deleted"],
        orphans_retained=result["orphans"]["retained_by_schedule"],
        schedule_version_id=result["schedule_versions"][0]["id"],
        schedule_version_no=result["schedule_versions"][0]["version_no"],
    )


@router.get("/imports/sample.xlsx", tags=["imports"])
def download_sample_workbook(user: CurrentUser) -> FileResponse:
    sample = PROJECT_ROOT / "data" / "imports" / "sample.xlsx"
    if not sample.exists():
        raise HTTPException(status_code=404, detail="示例文件不存在")
    return FileResponse(
        sample,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="途排智策_官方课表数据源示例.xlsx",
    )


MAX_IMPORT_BYTES = 64 * 1024 * 1024


@router.post("/imports/xlsx", response_model=ImportResult, tags=["imports"])
def import_xlsx(
    db: Db,
    user: Admin,
    scope: ViewerScope,
    file: Annotated[UploadFile, File(...)],
    campus_business_id: Annotated[str, Query(max_length=40)] = CAMPUS_BUSINESS_ID,
    campus_name: Annotated[str, Query(max_length=120)] = CAMPUS_NAME,
) -> ImportResult:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="只接受 .xlsx 工作簿")
    payload = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(payload) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=413, detail=f"工作簿超过 {MAX_IMPORT_BYTES // (1024 * 1024)} MB 上限"
        )
    if not payload:
        raise HTTPException(status_code=400, detail="上传的工作簿是空文件")
    # 每次上传写到独立临时文件：此前所有用户共用 data/imports/uploaded.xlsx，
    # 并发导入会互相覆盖，而且把用户数据写进了代码仓库目录。
    with tempfile.TemporaryDirectory(prefix="tupai-import-") as directory:
        target = Path(directory) / "uploaded.xlsx"
        target.write_bytes(payload)
        try:
            result = import_schedule_workbook(
                db,
                target,
                campus_business_id=campus_business_id.strip() or CAMPUS_BUSINESS_ID,
                campus_name=campus_name.strip() or CAMPUS_NAME,
                schedule_set_id=scope.id,
            )
        except WorkbookFormatError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(db, user, "import_xlsx", "workbook", file.filename, result)
    db.commit()
    return build_import_result(result, file.filename or target.name)


# ---------------------------------------------------------------------------
# L2 智能映射导入：任意 XLSX/CSV → 列映射建议 → 行级校验 → 确认后落库。
# 坐标（column_index / header_row_index）一律为 0-based 网格下标。
# ---------------------------------------------------------------------------


def _read_upload_bytes(file: UploadFile, extensions: tuple[str, ...], label: str) -> bytes:
    name = (file.filename or "").lower()
    if not name.endswith(extensions):
        raise HTTPException(status_code=400, detail=f"{label}只接受 {' / '.join(extensions)} 文件")
    payload = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(payload) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=413, detail=f"文件超过 {MAX_IMPORT_BYTES // (1024 * 1024)} MB 上限"
        )
    if not payload:
        raise HTTPException(status_code=400, detail="上传的文件是空文件")
    return payload


def _parse_mapping_json(mapping_json: str | None) -> ImportMappingInput | None:
    if mapping_json is None or not mapping_json.strip():
        return None
    try:
        return ImportMappingInput.model_validate_json(mapping_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=f"mapping_json 不是合法的映射定义：{exc}"
        ) from exc


def _resolve_import_sheet(
    grids: dict[str, list[list[Any]]], requested: str | None
) -> tuple[str, list[list[Any]]]:
    if requested is not None:
        if requested not in grids:
            raise HTTPException(
                status_code=422,
                detail=f"文件没有名为「{requested}」的工作表，实际为：{sorted(grids)}",
            )
        return requested, grids[requested]
    selected = pick_default_sheet(grids)
    return selected, grids[selected]


def _resolve_header_row(
    grid: list[list[Any]], requested: int | None
) -> int:
    if requested is not None:
        if not 0 <= requested < len(grid):
            raise HTTPException(
                status_code=422,
                detail=f"header_row_index={requested} 超出工作表范围（共 {len(grid)} 行）",
            )
        return requested
    candidates = detect_header_candidates(grid)
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="无法在前 10 行内自动识别表头行，请在映射里指定 header_row_index",
        )
    return candidates[0].row_index


def _manual_mapping_from_input(
    grid: list[list[Any]], header_row_index: int, override: ImportMappingInput
) -> list[ColumnMapping]:
    headers = header_texts(grid, header_row_index)
    name_to_index = {name: index for index, name in enumerate(headers) if name}
    entries = []
    for item in override.columns:
        if item.column_index is not None:
            column_index = item.column_index
        elif item.column in name_to_index:
            column_index = name_to_index[item.column]
        else:
            raise ImportMappingError(f"映射里的列「{item.column}」在工作表表头中不存在")
        entries.append((column_index, item.column, item.target))
    return apply_manual_mapping(headers, entries)


def _ai_column_resolver(db: Session) -> Callable[[list[dict[str, Any]]], dict[int, str]] | None:
    """AI 已配置时返回语义映射回调；未配置或不可用时返回 None，前三层照常工作。"""
    service = AIService(settings, db)
    try:
        if not service.configuration_view()["configured"]:
            return None
    except AIServiceError:
        return None

    def resolver(columns: list[dict[str, Any]]) -> dict[int, str]:
        return service.map_import_columns(columns, list(TEMPLATE_HEADERS))

    return resolver


def _import_mapping_view(mapping: list[ColumnMapping]) -> list[ImportColumnMapping]:
    return [
        ImportColumnMapping(
            column=item.column,
            column_index=item.column_index,
            target=item.target,
            confidence=item.confidence,
            rationale=item.rationale,
            matched_by=item.matched_by,
            sample_values=item.sample_values,
        )
        for item in mapping
    ]


def _lookup_import_mapping_history(
    db: Session, schedule_set_id: str, fingerprint: str
) -> ImportMappingHistory | None:
    return db.scalar(
        select(ImportMappingHistory).where(
            ImportMappingHistory.schedule_set_id == schedule_set_id,
            ImportMappingHistory.header_fingerprint == fingerprint,
        )
    )


def _record_import_mapping_history(
    db: Session,
    *,
    schedule_set_id: str,
    fingerprint: str,
    mapping: list[ColumnMapping],
    sheet: str | None,
    header_row_index: int,
) -> None:
    """commit 成功后按指纹记忆本次生效的映射（含手动修正），同一指纹只留最新一份。

    调用方负责在 import 成功之后、事务提交之前调用——失败导入不留决策记忆。
    """
    payload = history_payload(mapping, sheet=sheet, header_row_index=header_row_index)
    history = _lookup_import_mapping_history(db, schedule_set_id, fingerprint)
    if history is None:
        db.add(
            ImportMappingHistory(
                schedule_set_id=schedule_set_id,
                header_fingerprint=fingerprint,
                mapping=payload,
                sheet_name=sheet or "",
                used_count=1,
                last_used_at=shanghai_now(),
            )
        )
        return
    history.mapping = payload
    history.sheet_name = sheet or ""
    history.used_count += 1
    history.last_used_at = shanghai_now()


def _parse_cell_overrides(raw: str | None) -> dict[str, dict[str, Any]]:
    """解析 ``cell_overrides`` 表单字段：`{行号: {表头文本: 新值}}`。

    行号与 issues 报告同口径（工作表内 1-based Excel 行号）。结构不对直接 422，
    让调用方立刻发现传错了形状，而不是静默丢修复。
    """
    if raw is None or not raw.strip():
        return {}
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422, detail=f"cell_overrides 不是合法的 JSON：{exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=422,
            detail="cell_overrides 必须是 {行号: {表头文本: 新值}} 形式的 JSON 对象",
        )
    for row_key, cells in parsed.items():
        if not isinstance(cells, dict):
            raise HTTPException(
                status_code=422,
                detail=f"cell_overrides 第「{row_key}」行必须是 {{表头文本: 新值}} 对象",
            )
        for column_key, value in cells.items():
            if not isinstance(column_key, str):
                raise HTTPException(
                    status_code=422,
                    detail=f"cell_overrides 的表头文本必须是字符串：{column_key!r}",
                )
            if isinstance(value, (dict, list)):
                raise HTTPException(
                    status_code=422,
                    detail=f"cell_overrides 的单元格新值必须是标量：{column_key!r}",
                )
    return parsed


def _apply_cell_overrides(
    records: list[dict[str, Any]],
    row_numbers: Sequence[int],
    overrides: dict[str, dict[str, Any]],
    targets: dict[int, str],
    headers: Sequence[str],
) -> tuple[int, int]:
    """把单元格修复写到记录流上（解析后、校验前），返回（生效数, 忽略数）。

    列名允许两种写法：14 列规范字段名，或原文件表头文本（经映射反查目标字段）。
    行号不存在（超界、指向表头行）或列未映射的条目一律忽略并计数，不猜测。
    """
    applied = 0
    ignored = 0
    row_index = {number: offset for offset, number in enumerate(row_numbers)}
    header_to_target = {
        headers[index]: target for index, target in targets.items() if index < len(headers)
    }
    for row_key, cells in overrides.items():
        try:
            record = records[row_index[int(row_key)]]
        except (KeyError, ValueError):
            ignored += len(cells)
            continue
        for column_key, value in cells.items():
            if column_key in record:
                record[column_key] = value
            elif column_key in header_to_target:
                record[header_to_target[column_key]] = value
            else:
                ignored += 1
                continue
            applied += 1
    return applied, ignored


@router.post("/imports/preview", response_model=ImportPreviewResponse, tags=["imports"])
def preview_import(
    db: Db,
    user: Admin,
    scope: ViewerScope,
    file: Annotated[UploadFile, File(...)],
    mapping_json: Annotated[str | None, Form()] = None,
    cell_overrides: Annotated[str | None, Form()] = None,
) -> ImportPreviewResponse:
    """解析上传文件并给出列映射建议与行级校验报告，只解析不落库。

    携带 ``mapping_json``（用户修正后的映射）时按其重跑校验，用于 Fix 循环；
    未配置 AI 语义层时只走别名/规范化/模糊/形状四层匹配。无 ``mapping_json`` 时
    若表头指纹命中上次导入的映射记忆（同方案内），直接按历史决策预填。可选
    ``cell_overrides``（``{行号: {表头文本: 新值}}``）在解析后、校验前原地修复单元格。
    """
    payload = _read_upload_bytes(file, (".xlsx", ".csv"), "智能导入")
    grids = parse_uploaded_workbook(file.filename or "", payload)
    override = _parse_mapping_json(mapping_json)
    sheet_name, grid = _resolve_import_sheet(grids, override.sheet if override else None)
    candidates = detect_header_candidates(grid)
    header_row_index = _resolve_header_row(
        grid, override.header_row_index if override else None
    )
    headers = header_texts(grid, header_row_index)
    fingerprint = header_fingerprint(headers)
    historical_match = False
    try:
        if override is not None:
            mapping = _manual_mapping_from_input(grid, header_row_index, override)
            ai_used = False
        else:
            # 历史命中时整份覆盖自动建议（用户上次亲手确认过，含手动修正的列），
            # 命中就不再问 AI——记忆比语义提名更可信。
            history = _lookup_import_mapping_history(db, scope.id, fingerprint)
            historical = (
                apply_historical_mapping(headers, history.mapping) if history else None
            )
            if historical is not None:
                mapping = historical
                historical_match = True
                ai_used = False
            else:
                mapping, ai_used = suggest_mapping(
                    headers,
                    column_samples(grid, header_row_index),
                    ai_resolver=_ai_column_resolver(db),
                )
    except ImportMappingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    targets = {item.column_index: item.target for item in mapping if item.target}
    unmatched_columns = [item.column for item in mapping if item.target is None and item.column]
    missing_fields = [name for name in TEMPLATE_HEADERS if name not in targets.values()]
    records, row_numbers = build_records(grid, header_row_index, targets)
    overrides_applied, ignored_overrides = _apply_cell_overrides(
        records, row_numbers, _parse_cell_overrides(cell_overrides), targets, headers
    )
    parsed, skipped, blank_rows = parse_template_records(records, row_numbers, max_skipped=200)
    sheets = [
        ImportSheetOverview(
            name=name,
            row_count=len(sheet_grid),
            column_count=max((len(row) for row in sheet_grid), default=0),
            nonempty_cells=sum(
                1
                for row in sheet_grid
                for value in row
                if value is not None and str(value).strip()
            ),
        )
        for name, sheet_grid in grids.items()
    ]
    return ImportPreviewResponse(
        source=file.filename or "upload",
        sheets=sheets,
        selected_sheet=sheet_name,
        header_row_index=header_row_index,
        header_candidates=[
            ImportHeaderCandidate(
                row_index=item.row_index, score=item.score, sample=item.sample
            )
            for item in candidates
        ],
        mapping=_import_mapping_view(mapping),
        unmatched_columns=unmatched_columns,
        missing_fields=missing_fields,
        issues=skipped,
        historical_match=historical_match,
        ignored_overrides=ignored_overrides,
        stats=ImportPreviewStats(
            rows_total=len(records),
            rows_valid=len(parsed),
            # 真实跳过数按「总数 - 有效 - 空行」算，不受 skipped 清单截断影响。
            rows_skipped=len(records) - len(parsed) - blank_rows,
            rows_ignored_blank=blank_rows,
            columns_total=len(headers),
            mapped_columns=len(targets),
            ai_mapping_used=ai_used,
            overrides_applied=overrides_applied,
        ),
    )


@router.post("/imports/commit", response_model=ImportCommitResponse, tags=["imports"])
def commit_import(
    db: Db,
    user: Admin,
    scope: ViewerScope,
    file: Annotated[UploadFile, File(...)],
    mapping_json: Annotated[str, Form(...)],
    mode: Annotated[Literal["insert", "upsert"], Form()] = "upsert",
    cell_overrides: Annotated[str | None, Form()] = None,
    campus_business_id: Annotated[str, Query(max_length=40)] = CAMPUS_BUSINESS_ID,
    campus_name: Annotated[str, Query(max_length=120)] = CAMPUS_NAME,
) -> ImportCommitResponse:
    """按确认的映射把上传文件正式导入，返回与模板直通导入同构的质量报告。

    ``mode=upsert`` 沿用现有业务键（班级+课次序号+课节名称+上课日期+上课时段）
    重复导入即更新；``mode=insert`` 只新增，已存在的课次原样保留且不做孤儿清理。
    ``cell_overrides``（``{行号: {表头文本: 新值}}``）与 preview 同口径，解析后、
    校验前原地修复单元格；导入成功后把生效映射（含手动修正）按表头指纹记忆。
    """
    payload = _read_upload_bytes(file, (".xlsx", ".csv"), "智能导入")
    override = _parse_mapping_json(mapping_json)
    if override is None:
        raise HTTPException(status_code=422, detail="commit 必须携带 mapping_json")
    grids = parse_uploaded_workbook(file.filename or "", payload)
    sheet_name, grid = _resolve_import_sheet(grids, override.sheet)
    header_row_index = _resolve_header_row(grid, override.header_row_index)
    try:
        mapping = _manual_mapping_from_input(grid, header_row_index, override)
    except ImportMappingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    targets = {item.column_index: item.target for item in mapping if item.target}
    if not targets:
        raise HTTPException(status_code=422, detail="映射没有命中任何规范字段，无法导入")
    headers = header_texts(grid, header_row_index)
    records, row_numbers = build_records(grid, header_row_index, targets)
    overrides_applied, ignored_overrides = _apply_cell_overrides(
        records, row_numbers, _parse_cell_overrides(cell_overrides), targets, headers
    )
    try:
        result = import_canonical_rows(
            db,
            records,
            campus_business_id=campus_business_id.strip() or CAMPUS_BUSINESS_ID,
            campus_name=campus_name.strip() or CAMPUS_NAME,
            schedule_set_id=scope.id,
            source_name=file.filename or "upload",
            checksum=hashlib.sha256(payload).hexdigest(),
            import_mode=mode,
        )
    except WorkbookFormatError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # 导入成功才记忆映射决策：失败导入的记忆只会把坏映射二次带给用户。
    _record_import_mapping_history(
        db,
        schedule_set_id=scope.id,
        fingerprint=header_fingerprint(headers),
        mapping=mapping,
        sheet=override.sheet,
        header_row_index=header_row_index,
    )
    audit(db, user, "import_commit", "workbook", file.filename, result)
    db.commit()
    return ImportCommitResponse(
        **build_import_result(result, file.filename or "upload").model_dump(),
        mode=result["import_mode"],
        course_sessions_updated=result["course_sessions_updated"],
        course_sessions_skipped_existing=result["course_sessions_skipped_existing"],
        overrides_applied=overrides_applied,
        ignored_overrides=ignored_overrides,
    )


@router.get("/campuses", response_model=list[CampusResponse], tags=["master-data"])
def list_campuses(db: Db, user: CurrentUser, scope: ViewerScope) -> list[Campus]:
    return list(
        db.scalars(
            select(Campus).where(Campus.schedule_set_id == scope.id).order_by(Campus.business_id)
        )
    )


@router.post("/campuses", response_model=CampusResponse, status_code=201, tags=["master-data"])
def create_campus(payload: CampusCreate, db: Db, user: Admin, scope: ViewerScope) -> Campus:
    instance = Campus(schedule_set_id=scope.id, **payload.model_dump())
    db.add(instance)
    try:
        audit(db, user, "create", "campus", instance.id, payload.model_dump())
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="业务 ID 已存在") from exc
    db.refresh(instance)
    return instance


def selected_master_rows(
    db: Session, model: type[Any], object_ids: list[str], resource_label: str, schedule_set_id: str
) -> list[Any]:
    rows = list(
        db.scalars(
            select(model).where(model.schedule_set_id == schedule_set_id, model.id.in_(object_ids))
        )
    )
    found_ids = {item.id for item in rows}
    missing = [item for item in object_ids if item not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"未找到 {len(missing)} 条{resource_label}记录")
    return rows


def raise_reference_conflict(
    items: list[Any], referenced: set[tuple[str, str]], resource_label: str, references: str
) -> None:
    labels = [
        item.business_id for item in items if (item.campus_id, item.business_id) in referenced
    ]
    if not labels:
        return
    preview = "、".join(labels[:5])
    suffix = "等" if len(labels) > 5 else ""
    raise HTTPException(
        status_code=409,
        detail=(
            f"{len(labels)} 条{resource_label}已被{references}引用，不能直接删除：{preview}{suffix}"
        ),
    )


def ensure_teachers_deletable(db: Session, teachers: list[Teacher]) -> None:
    """课次是教师的唯一引用源。

    班级不再存 teacher_business_id——班级上的教师本来就是从课次聚合出来的，
    再拿它当第二个引用源只是把同一条事实数两遍。
    """
    identities = {(item.campus_id, item.business_id) for item in teachers}
    campus_ids = {item[0] for item in identities}
    business_ids = {item[1] for item in identities}
    course_references = {
        (campus_id, business_id)
        for campus_id, business_id in db.execute(
            select(CourseSession.campus_id, CourseSession.teacher_business_id).where(
                CourseSession.schedule_set_id == teachers[0].schedule_set_id,
                CourseSession.campus_id.in_(campus_ids),
                CourseSession.teacher_business_id.in_(business_ids),
            )
        )
    }
    raise_reference_conflict(teachers, identities & course_references, "教师", "课程")


def ensure_class_groups_deletable(db: Session, classes: list[ClassGroup]) -> None:
    identities = {(item.campus_id, item.business_id) for item in classes}
    campus_ids = {item[0] for item in identities}
    business_ids = {item[1] for item in identities}
    references = {
        (campus_id, business_id)
        for campus_id, business_id in db.execute(
            select(CourseSession.campus_id, CourseSession.class_business_id).where(
                CourseSession.schedule_set_id == classes[0].schedule_set_id,
                CourseSession.campus_id.in_(campus_ids),
                CourseSession.class_business_id.in_(business_ids),
            )
        )
    }
    raise_reference_conflict(classes, identities & references, "班级", "课程")


def ensure_rooms_deletable(db: Session, rooms: list[Room]) -> None:
    identities = {(item.campus_id, item.business_id) for item in rooms}
    campus_ids = {item[0] for item in identities}
    business_ids = {item[1] for item in identities}
    course_references = {
        (campus_id, business_id)
        for campus_id, business_id in db.execute(
            select(CourseSession.campus_id, CourseSession.original_room_business_id).where(
                CourseSession.schedule_set_id == rooms[0].schedule_set_id,
                CourseSession.campus_id.in_(campus_ids),
                CourseSession.original_room_business_id.in_(business_ids),
            )
        )
        if business_id is not None
    }
    schedule_references = {
        (campus_id, business_id)
        for campus_id, business_id in db.execute(
            select(CourseSession.campus_id, ScheduleAssignment.room_business_id)
            .join(
                ScheduleAssignment,
                ScheduleAssignment.course_session_id == CourseSession.id,
            )
            .where(
                CourseSession.schedule_set_id == rooms[0].schedule_set_id,
                CourseSession.campus_id.in_(campus_ids),
                ScheduleAssignment.room_business_id.in_(business_ids),
            )
        )
    }
    raise_reference_conflict(
        rooms,
        identities & (course_references | schedule_references),
        "教室",
        "课程或课表版本",
    )


def ensure_time_slots_deletable(db: Session, slots: list[TimeSlot]) -> None:
    identities = {(item.campus_id, item.business_id) for item in slots}
    campus_ids = {item[0] for item in identities}
    business_ids = {item[1] for item in identities}
    course_references = {
        (campus_id, business_id)
        for campus_id, business_id in db.execute(
            select(CourseSession.campus_id, CourseSession.suggested_slot_id).where(
                CourseSession.schedule_set_id == slots[0].schedule_set_id,
                CourseSession.campus_id.in_(campus_ids),
                CourseSession.suggested_slot_id.in_(business_ids),
            )
        )
        if business_id is not None
    }
    schedule_references = {
        (campus_id, business_id)
        for campus_id, business_id in db.execute(
            select(CourseSession.campus_id, ScheduleAssignment.slot_business_id)
            .join(
                ScheduleAssignment,
                ScheduleAssignment.course_session_id == CourseSession.id,
            )
            .where(
                CourseSession.schedule_set_id == slots[0].schedule_set_id,
                CourseSession.campus_id.in_(campus_ids),
                ScheduleAssignment.slot_business_id.in_(business_ids),
            )
        )
    }
    raise_reference_conflict(
        slots,
        identities & (course_references | schedule_references),
        "时段",
        "课程或课表版本",
    )


@router.get("/teachers", response_model=list[TeacherResponse], tags=["master-data"])
def list_teachers(db: Db, user: CurrentUser, scope: ViewerScope) -> list[Teacher]:
    return list(
        db.scalars(
            select(Teacher).where(Teacher.schedule_set_id == scope.id).order_by(Teacher.business_id)
        )
    )


@router.post("/teachers", response_model=TeacherResponse, status_code=201, tags=["master-data"])
def create_teacher(payload: TeacherPayload, db: Db, user: Admin, scope: ViewerScope) -> Teacher:
    instance = Teacher(schedule_set_id=scope.id, **payload.model_dump())
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.put("/teachers/{object_id}", response_model=TeacherResponse, tags=["master-data"])
def update_teacher(
    object_id: str, payload: TeacherPayload, db: Db, user: Admin, scope: ViewerScope
) -> Teacher:
    instance = get_scoped_or_404(db, Teacher, object_id, scope)
    values = payload.model_dump()
    # is_group 是后加到教师契约里的字段。旧客户端没有这个字段时应保留原类型，
    # 避免仅修改名称或学科就把既有教研组静默重置为单体教师。
    if "is_group" not in payload.model_fields_set:
        values.pop("is_group")
    for key, value in values.items():
        setattr(instance, key, value)
    audit(db, user, "update", "teacher", object_id)
    db.commit()
    db.refresh(instance)
    return instance


@router.post(
    "/teachers/batch-update",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_update_teachers(
    payload: TeacherBatchUpdate, db: Db, user: Admin, scope: ViewerScope
) -> BatchOperationResponse:
    teachers: list[Teacher] = selected_master_rows(
        db, Teacher, payload.object_ids, "教师", scope.id
    )
    changes = payload.model_dump(exclude={"object_ids"}, exclude_unset=True)
    for teacher in teachers:
        for key, value in changes.items():
            setattr(teacher, key, value)
    audit(
        db,
        user,
        "batch_update",
        "teacher",
        None,
        {"count": len(teachers), "fields": sorted(changes)},
    )
    db.commit()
    return BatchOperationResponse(affected_count=len(teachers))


@router.post(
    "/teachers/batch-delete",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_delete_teachers(
    payload: MasterDataBatchDelete, db: Db, user: Admin, scope: ViewerScope
) -> BatchOperationResponse:
    teachers: list[Teacher] = selected_master_rows(
        db, Teacher, payload.object_ids, "教师", scope.id
    )
    ensure_teachers_deletable(db, teachers)
    for teacher in teachers:
        db.delete(teacher)
    audit(db, user, "batch_delete", "teacher", None, {"count": len(teachers)})
    db.commit()
    return BatchOperationResponse(affected_count=len(teachers))


def class_group_track_index(
    db: Session, schedule_set_id: str
) -> dict[tuple[str, str], list[ClassGroupTrack]]:
    """按课程保存的完整产品归属展开班级轨道。"""
    index: dict[tuple[str, str], list[ClassGroupTrack]] = {}
    grouped: Counter[tuple[str, str, str, str, str, str]] = Counter()
    for course in db.scalars(
        select(CourseSession)
        .where(CourseSession.schedule_set_id == schedule_set_id, CourseSession.is_active.is_(True))
        .order_by(CourseSession.business_id)
    ):
        product_types = list(course.product_types or [])
        if course.product_type and course.product_type not in product_types:
            product_types.append(course.product_type)
        teacher_ids = list(course.teacher_business_ids or [])
        if course.teacher_business_id and course.teacher_business_id not in teacher_ids:
            teacher_ids.append(course.teacher_business_id)
        for product_type in sorted(set(product_types or [""])):
            for teacher in sorted(set(teacher_ids or [""])):
                grouped[
                    (
                        course.campus_id,
                        course.class_business_id,
                        course.business_line or "",
                        product_type,
                        course.subject or "",
                        teacher,
                    )
                ] += 1
    for key, count in sorted(grouped.items(), key=lambda item: item[0]):
        campus_id, class_business_id, business_line, product_type, subject, teacher = key
        index.setdefault((campus_id, class_business_id), []).append(
            ClassGroupTrack(
                business_line=business_line or "",
                product_type=product_type or "",
                subject=subject or "",
                teacher_business_id=teacher or "",
                session_count=int(count),
            )
        )
    return index


def class_group_response(item: ClassGroup, tracks: list[ClassGroupTrack]) -> ClassGroupResponse:
    def distinct(values: list[str]) -> list[str]:
        # 空串不是一个班型/教师，只是课次上没填，别让它占一个 chip。
        return sorted({value for value in values if value})

    return ClassGroupResponse(
        id=item.id,
        campus_id=item.campus_id,
        business_id=item.business_id,
        name=item.name,
        business_lines=distinct([track.business_line for track in tracks]),
        product_types=distinct([track.product_type for track in tracks]),
        subjects=distinct([track.subject for track in tracks]),
        teacher_business_ids=distinct([track.teacher_business_id for track in tracks]),
        session_count=sum(track.session_count for track in tracks),
        tracks=tracks,
    )


def class_group_responses(
    db: Session,
    classes: list[ClassGroup],
    schedule_set_id: str = DEFAULT_SCHEDULE_SET_ID,
) -> list[ClassGroupResponse]:
    index = class_group_track_index(db, schedule_set_id)
    return [
        class_group_response(item, index.get((item.campus_id, item.business_id), []))
        for item in classes
    ]


@router.get("/class-groups", response_model=list[ClassGroupResponse], tags=["master-data"])
def list_class_groups(db: Db, user: CurrentUser, scope: ViewerScope) -> list[ClassGroupResponse]:
    classes = list(
        db.scalars(
            select(ClassGroup)
            .where(ClassGroup.schedule_set_id == scope.id)
            .order_by(ClassGroup.business_id)
        )
    )
    return class_group_responses(db, classes, scope.id)


@router.post(
    "/class-groups", response_model=ClassGroupResponse, status_code=201, tags=["master-data"]
)
def create_class_group(
    payload: ClassGroupPayload, db: Db, user: Admin, scope: ViewerScope
) -> ClassGroupResponse:
    instance = ClassGroup(schedule_set_id=scope.id, **payload.model_dump())
    db.add(instance)
    try:
        audit(db, user, "create", "class_group", instance.id, payload.model_dump())
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="业务 ID 已存在") from exc
    db.refresh(instance)
    return class_group_responses(db, [instance], instance.schedule_set_id)[0]


@router.put("/class-groups/{object_id}", response_model=ClassGroupResponse, tags=["master-data"])
def update_class_group(
    object_id: str,
    payload: ClassGroupPayload,
    db: Db,
    user: Admin,
    scope: ViewerScope,
) -> ClassGroupResponse:
    instance = get_scoped_or_404(db, ClassGroup, object_id, scope)
    for key, value in payload.model_dump().items():
        setattr(instance, key, value)
    audit(db, user, "update", "class_group", object_id)
    db.commit()
    db.refresh(instance)
    return class_group_responses(db, [instance], instance.schedule_set_id)[0]


@router.post(
    "/class-groups/batch-delete",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_delete_class_groups(
    payload: MasterDataBatchDelete, db: Db, user: Admin, scope: ViewerScope
) -> BatchOperationResponse:
    classes: list[ClassGroup] = selected_master_rows(
        db, ClassGroup, payload.object_ids, "班级", scope.id
    )
    ensure_class_groups_deletable(db, classes)
    for class_group in classes:
        db.delete(class_group)
    audit(db, user, "batch_delete", "class_group", None, {"count": len(classes)})
    db.commit()
    return BatchOperationResponse(affected_count=len(classes))


@router.get("/rooms", response_model=list[RoomResponse], tags=["master-data"])
def list_rooms(db: Db, user: CurrentUser, scope: ViewerScope) -> list[Room]:
    return list(
        db.scalars(select(Room).where(Room.schedule_set_id == scope.id).order_by(Room.business_id))
    )


@router.post("/rooms", response_model=RoomResponse, status_code=201, tags=["master-data"])
def create_room(payload: RoomPayload, db: Db, user: Admin, scope: ViewerScope) -> Room:
    instance = Room(schedule_set_id=scope.id, **payload.model_dump())
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.put("/rooms/{object_id}", response_model=RoomResponse, tags=["master-data"])
def update_room(
    object_id: str, payload: RoomPayload, db: Db, user: Admin, scope: ViewerScope
) -> Room:
    instance = get_scoped_or_404(db, Room, object_id, scope)
    for key, value in payload.model_dump().items():
        setattr(instance, key, value)
    audit(db, user, "update", "room", object_id)
    db.commit()
    db.refresh(instance)
    return instance


@router.post(
    "/rooms/batch-update",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_update_rooms(
    payload: RoomBatchUpdate, db: Db, user: Admin, scope: ViewerScope
) -> BatchOperationResponse:
    rooms: list[Room] = selected_master_rows(db, Room, payload.object_ids, "教室", scope.id)
    for room in rooms:
        room.is_active = payload.is_active
    audit(
        db,
        user,
        "batch_update",
        "room",
        None,
        {"count": len(rooms), "fields": ["is_active"]},
    )
    db.commit()
    return BatchOperationResponse(affected_count=len(rooms))


@router.post(
    "/rooms/batch-delete",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_delete_rooms(
    payload: MasterDataBatchDelete, db: Db, user: Admin, scope: ViewerScope
) -> BatchOperationResponse:
    rooms: list[Room] = selected_master_rows(db, Room, payload.object_ids, "教室", scope.id)
    ensure_rooms_deletable(db, rooms)
    for room in rooms:
        db.delete(room)
    audit(db, user, "batch_delete", "room", None, {"count": len(rooms)})
    db.commit()
    return BatchOperationResponse(affected_count=len(rooms))


@router.get("/time-slots", response_model=list[TimeSlotResponse], tags=["master-data"])
def list_time_slots(db: Db, user: CurrentUser, scope: ViewerScope) -> list[TimeSlot]:
    return list(
        db.scalars(
            select(TimeSlot).where(TimeSlot.schedule_set_id == scope.id).order_by(TimeSlot.sequence)
        )
    )


@router.post("/time-slots", response_model=TimeSlotResponse, status_code=201, tags=["master-data"])
def create_time_slot(payload: TimeSlotPayload, db: Db, user: Admin, scope: ViewerScope) -> TimeSlot:
    instance = TimeSlot(schedule_set_id=scope.id, **payload.model_dump())
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.put("/time-slots/{object_id}", response_model=TimeSlotResponse, tags=["master-data"])
def update_time_slot(
    object_id: str,
    payload: TimeSlotPayload,
    db: Db,
    user: Admin,
    scope: ViewerScope,
) -> TimeSlot:
    instance = get_scoped_or_404(db, TimeSlot, object_id, scope)
    for key, value in payload.model_dump().items():
        setattr(instance, key, value)
    audit(db, user, "update", "time_slot", object_id)
    db.commit()
    db.refresh(instance)
    return instance


@router.post(
    "/time-slots/batch-update",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_update_time_slots(
    payload: TimeSlotBatchUpdate, db: Db, user: Admin, scope: ViewerScope
) -> BatchOperationResponse:
    slots: list[TimeSlot] = selected_master_rows(db, TimeSlot, payload.object_ids, "时段", scope.id)
    for slot in slots:
        slot.is_open = payload.is_open
    audit(
        db,
        user,
        "batch_update",
        "time_slot",
        None,
        {"count": len(slots), "fields": ["is_open"]},
    )
    db.commit()
    return BatchOperationResponse(affected_count=len(slots))


@router.post(
    "/time-slots/batch-delete",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_delete_time_slots(
    payload: MasterDataBatchDelete, db: Db, user: Admin, scope: ViewerScope
) -> BatchOperationResponse:
    slots: list[TimeSlot] = selected_master_rows(db, TimeSlot, payload.object_ids, "时段", scope.id)
    ensure_time_slots_deletable(db, slots)
    for slot in slots:
        db.delete(slot)
    audit(db, user, "batch_delete", "time_slot", None, {"count": len(slots)})
    db.commit()
    return BatchOperationResponse(affected_count=len(slots))


@router.get("/course-sessions", response_model=list[CourseSessionResponse], tags=["master-data"])
def list_course_sessions(db: Db, user: CurrentUser, scope: ViewerScope) -> list[CourseSession]:
    return list(
        db.scalars(
            select(CourseSession)
            .where(CourseSession.schedule_set_id == scope.id, CourseSession.is_active.is_(True))
            .order_by(CourseSession.business_id)
        )
    )


@router.post(
    "/course-sessions",
    response_model=CourseSessionResponse,
    status_code=201,
    tags=["master-data"],
)
def create_course_session(
    payload: CourseSessionPayload, db: Db, user: Admin, scope: ViewerScope
) -> CourseSession:
    values = payload.model_dump()
    if values["product_type"] and not values["product_types"]:
        values["product_types"] = [values["product_type"]]
    if values["teacher_business_id"] and not values["teacher_business_ids"]:
        values["teacher_business_ids"] = [values["teacher_business_id"]]
    if values["lesson_name"] and not values["lesson_names"]:
        values["lesson_names"] = [values["lesson_name"]]
    if values["stage"] and not values["stages"]:
        values["stages"] = [values["stage"]]
    if values["suggested_slot_id"] and not values["candidate_slot_ids"]:
        values["candidate_slot_ids"] = [values["suggested_slot_id"]]
    if (
        values["fixed_start_time"]
        and values["fixed_end_time"]
        and not values["candidate_clock_windows"]
    ):
        values["candidate_clock_windows"] = [
            {
                "start_time": values["fixed_start_time"],
                "end_time": values["fixed_end_time"],
            }
        ]
    if values["original_room_business_id"] and not values["candidate_room_business_ids"]:
        values["candidate_room_business_ids"] = [values["original_room_business_id"]]
    instance = CourseSession(schedule_set_id=scope.id, **values)
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.put(
    "/course-sessions/{object_id}", response_model=CourseSessionResponse, tags=["master-data"]
)
def update_course_session(
    object_id: str,
    payload: CourseSessionUpdate,
    db: Db,
    user: Admin,
    scope: ViewerScope,
) -> CourseSession:
    instance = get_scoped_or_404(db, CourseSession, object_id, scope)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(instance, key, value)
    if "original_room_business_id" in changes:
        instance.candidate_room_business_ids = (
            [changes["original_room_business_id"]] if changes["original_room_business_id"] else []
        )
    audit(db, user, "update", "course_session", object_id)
    db.commit()
    db.refresh(instance)
    return instance


def selected_course_sessions(
    db: Session, object_ids: list[str], schedule_set_id: str
) -> list[CourseSession]:
    rows = list(
        db.scalars(
            select(CourseSession).where(
                CourseSession.schedule_set_id == schedule_set_id, CourseSession.id.in_(object_ids)
            )
        )
    )
    found_ids = {item.id for item in rows}
    missing = [item for item in object_ids if item not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"未找到 {len(missing)} 条课程记录")
    return rows


# 与主数据页 filteredCourses 的搜索口径逐字段对齐，顺序不能改：跨字段的查询词
# （例如「考研 暑期」）只有拼接顺序一致，服务端和前端才会命中同一批行。
COURSE_SEARCH_COLUMNS = (
    CourseSession.business_id,
    CourseSession.class_business_id,
    CourseSession.teacher_business_id,
    cast(CourseSession.teacher_business_ids, String),
    CourseSession.lesson_name,
    CourseSession.subject,
    CourseSession.business_line,
    CourseSession.product_type,
    cast(CourseSession.product_types, String),
    cast(CourseSession.lesson_names, String),
    CourseSession.original_room_business_id,
    cast(CourseSession.candidate_room_business_ids, String),
)


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _course_product_values(course: CourseSession) -> list[str]:
    values = [str(item) for item in course.product_types or [] if item]
    if course.product_type and course.product_type not in values:
        values.append(course.product_type)
    return sorted(set(values))


def _course_product_criterion(product_type: str) -> ColumnElement[bool]:
    return or_(
        CourseSession.product_type == product_type,
        _json_array_contains(CourseSession.product_types, product_type),
    )


def _json_array_contains(values: Any, target: str) -> ColumnElement[bool]:
    elements = func.json_each(values).table_valued("key", "value")
    return exists(select(literal(1)).select_from(elements).where(elements.c.value == target))


def _course_list_dimension_criterion(primary: Any, values: Any, target: str) -> ColumnElement[bool]:
    return or_(
        primary == target,
        _json_array_contains(values, target),
    )


def _course_products_criterion(product_types: list[str]) -> ColumnElement[bool]:
    return or_(*(_course_product_criterion(item) for item in product_types))


def _all_course_product_types(db: Session, schedule_set_id: str) -> set[str]:
    return {
        product_type
        for course in db.scalars(
            select(CourseSession).where(
                CourseSession.schedule_set_id == schedule_set_id, CourseSession.is_active.is_(True)
            )
        )
        for product_type in _course_product_values(course)
    }


def course_session_criteria(
    schedule_set_id: str, spec: CourseSessionFilter | None, object_ids: list[str] | None
) -> list[ColumnElement[bool]]:
    """把两种选择方式统一成一组 where 条件。

    条件形态（而不是 id 列表）是刻意的：按条件删两万条时，引用检查和删除都能写成
    子查询，不用把两万个绑定变量塞进 IN——SQLite 的变量上限只有三万出头。
    """
    criteria: list[ColumnElement[bool]] = [
        CourseSession.schedule_set_id == schedule_set_id, CourseSession.is_active.is_(True)
    ]
    if object_ids is not None:
        return [*criteria, CourseSession.id.in_(object_ids)]
    if spec is None:
        return criteria

    equality: tuple[tuple[Any, Any], ...] = (
        (CourseSession.campus_id, spec.campus_id),
        (CourseSession.business_line, spec.business_line),
        (CourseSession.class_business_id, spec.class_business_id),
        (CourseSession.subject, spec.subject),
        (CourseSession.lesson_date, spec.lesson_date),
    )
    for column, value in equality:
        if value is not None:
            criteria.append(column == value)
    if spec.product_type is not None:
        criteria.append(_course_product_criterion(spec.product_type))
    if spec.teacher_business_id is not None:
        criteria.append(
            _course_list_dimension_criterion(
                CourseSession.teacher_business_id,
                CourseSession.teacher_business_ids,
                spec.teacher_business_id,
            )
        )
    if spec.original_room_business_id is not None:
        criteria.append(
            _course_list_dimension_criterion(
                CourseSession.original_room_business_id,
                CourseSession.candidate_room_business_ids,
                spec.original_room_business_id,
            )
        )
    if spec.lesson_date_from is not None:
        criteria.append(CourseSession.lesson_date >= spec.lesson_date_from)
    if spec.lesson_date_to is not None:
        criteria.append(CourseSession.lesson_date <= spec.lesson_date_to)
    if spec.search:
        blob = type_cast(ColumnElement[str], func.coalesce(COURSE_SEARCH_COLUMNS[0], ""))
        for column in COURSE_SEARCH_COLUMNS[1:]:
            blob = blob + literal(" ") + func.coalesce(column, "")
        criteria.append(
            func.lower(blob).like(_like_pattern(spec.search.strip().lower()), escape="\\")
        )
    return criteria


def count_course_sessions(db: Session, criteria: list[ColumnElement[bool]]) -> int:
    return int(db.scalar(select(func.count()).select_from(CourseSession).where(*criteria)) or 0)


def ensure_expected_count(actual: int, expected: int | None) -> None:
    """防呆：界面上看到多少条就必须命中多少条。

    筛选条件在两次请求之间可能漂移（别人刚导入了一批课次，或自己刚改过日期），
    此时按旧的判断继续删就是误删。对不上一律拒绝，让前端刷新后重来。
    """
    if expected is None or actual == expected:
        return
    raise HTTPException(
        status_code=422,
        detail=(
            f"筛选结果已变化：预期命中 {expected} 条，实际命中 {actual} 条。"
            "请刷新课程列表后重新确认再操作。"
        ),
    )


def validate_course_room(
    db: Session,
    schedule_set_id: str,
    criteria: list[ColumnElement[bool]],
    room_business_id: str | None,
) -> None:
    if room_business_id is None:
        return
    campus_ids = set(db.scalars(select(CourseSession.campus_id).where(*criteria).distinct()))
    matched_campuses = set(
        db.scalars(
            select(Room.campus_id).where(
                Room.schedule_set_id == schedule_set_id,
                Room.business_id == room_business_id,
                Room.campus_id.in_(campus_ids),
            )
        )
    )
    if matched_campuses != campus_ids:
        raise HTTPException(status_code=422, detail="指定教室不属于所选课程的校区")


def ensure_course_sessions_deletable(db: Session, criteria: list[ColumnElement[bool]]) -> None:
    """被课表版本或飞书日程引用的课次一条都不能删，整批拒绝。

    用半连接而不是 id 列表：按条件删的场景下 id 列表可能上万条，撑爆绑定变量上限。
    """
    selected_ids = select(CourseSession.id).where(*criteria)
    referenced = (
        select(CourseSession.business_id)
        .where(
            CourseSession.id.in_(selected_ids),
            or_(
                CourseSession.id.in_(select(ScheduleAssignment.course_session_id)),
                CourseSession.id.in_(select(CalendarEventBinding.course_session_id)),
            ),
        )
        .order_by(CourseSession.business_id)
    )
    labels = list(db.scalars(referenced.limit(6)))
    if not labels:
        return
    total = int(db.scalar(select(func.count()).select_from(referenced.subquery())) or 0)
    preview = "、".join(labels[:5])
    suffix = "等" if total > 5 else ""
    raise HTTPException(
        status_code=409,
        detail=(f"{total} 条课程已被课表版本或飞书日程引用，不能直接删除：{preview}{suffix}"),
    )


@router.post(
    "/course-sessions/batch-update",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_update_course_sessions(
    payload: CourseSessionBatchUpdate, db: Db, user: Admin, scope: ViewerScope
) -> BatchOperationResponse:
    if payload.object_ids is not None:
        selected_course_sessions(db, payload.object_ids, scope.id)
    criteria = course_session_criteria(scope.id, payload.filter, payload.object_ids)
    matched = count_course_sessions(db, criteria)
    ensure_expected_count(matched, payload.expected_count)
    changes = payload.model_dump(
        exclude={"object_ids", "filter", "expected_count"}, exclude_unset=True
    )
    if "original_room_business_id" in changes:
        validate_course_room(db, scope.id, criteria, changes["original_room_business_id"])
        changes["candidate_room_business_ids"] = (
            [changes["original_room_business_id"]] if changes["original_room_business_id"] else []
        )
    if matched:
        db.execute(
            update(CourseSession)
            .where(*criteria)
            .values(**changes, updated_at=shanghai_now())
            .execution_options(synchronize_session=False)
        )
    audit(
        db,
        user,
        "batch_update",
        "course_session",
        None,
        {
            "count": matched,
            "fields": sorted(changes),
            "selection": "filter" if payload.filter is not None else "object_ids",
            "filter": payload.filter.model_dump(mode="json", exclude_none=True)
            if payload.filter is not None
            else None,
        },
    )
    db.commit()
    return BatchOperationResponse(affected_count=matched)


@router.post(
    "/course-sessions/batch-delete",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_delete_course_sessions(
    payload: CourseSessionBatchDelete, db: Db, user: Admin, scope: ViewerScope
) -> BatchOperationResponse:
    if payload.object_ids is not None:
        selected_course_sessions(db, payload.object_ids, scope.id)
    criteria = course_session_criteria(scope.id, payload.filter, payload.object_ids)
    matched = count_course_sessions(db, criteria)
    ensure_expected_count(matched, payload.expected_count)
    ensure_course_sessions_deletable(db, criteria)
    if matched:
        db.execute(
            delete(CourseSession).where(*criteria).execution_options(synchronize_session=False)
        )
    audit(
        db,
        user,
        "batch_delete",
        "course_session",
        None,
        {
            "count": matched,
            "selection": "filter" if payload.filter is not None else "object_ids",
            "filter": payload.filter.model_dump(mode="json", exclude_none=True)
            if payload.filter is not None
            else None,
        },
    )
    db.commit()
    return BatchOperationResponse(affected_count=matched)


MASTER_MODELS = {
    "teachers": Teacher,
    "class-groups": ClassGroup,
    "rooms": Room,
    "time-slots": TimeSlot,
    "course-sessions": CourseSession,
}


@router.delete("/master-data/{resource}/{object_id}", status_code=204, tags=["master-data"])
def delete_master_data(
    resource: str,
    object_id: str,
    db: Db,
    user: Admin,
    scope: ViewerScope,
) -> Response:
    model = MASTER_MODELS.get(resource)
    if model is None:
        raise HTTPException(status_code=404, detail="未知主数据资源")
    instance = get_scoped_or_404(db, model, object_id, scope)
    if resource == "teachers":
        ensure_teachers_deletable(db, [instance])
    elif resource == "class-groups":
        ensure_class_groups_deletable(db, [instance])
    elif resource == "rooms":
        ensure_rooms_deletable(db, [instance])
    elif resource == "time-slots":
        ensure_time_slots_deletable(db, [instance])
    elif resource == "course-sessions":
        ensure_course_sessions_deletable(
            db, [CourseSession.schedule_set_id == scope.id, CourseSession.id == instance.id]
        )
    db.delete(instance)
    audit(db, user, "delete", resource, object_id)
    db.commit()
    return Response(status_code=204)


# 求解器有两条路径，能力并不对等，目录必须如实标注每个类型在哪条路径下真正生效：
#   date —— 日期感知路径（_solve_date_aware），课次自带日期与固定起止时间时走，郑州真实数据用它；
#   slot —— 时段矩阵路径（_build_model），经典排课模型，种子演示数据用它。
# solver_paths 为空表示该组合只登记留痕、不进入任何模型，前端据此提示教务。
DATE_PATH = "date"
SLOT_PATH = "slot"

_SLOT_ONE = {"name": "slot_id", "label": "时段", "kind": "slot"}
_SLOT_MANY = {"name": "slot_ids", "label": "时段", "kind": "slot", "multiple": True}
_ROOM_ONE = {"name": "room_id", "label": "教室", "kind": "room"}
_ROOM_MANY = {"name": "room_ids", "label": "教室", "kind": "room", "multiple": True}
_DATE_ONE = {"name": "date", "label": "日期", "kind": "date"}
_DATE_RANGE_FIELDS = [
    {"name": "date_from", "label": "最早日期", "kind": "date", "required": False},
    {"name": "date_to", "label": "最晚日期", "kind": "date", "required": False},
    {
        "name": "date_window_days",
        "label": "相对原日期可浮动天数",
        "kind": "integer",
        "required": False,
        "minimum": 0,
    },
]
_DATE_RANGE_SCOPE = ["date_from", "date_to", "date_window_days", "before_days", "after_days"]
_DATE_RANGE_HINT = "填绝对区间（最早/最晚日期）或相对浮动天数，两者可同时给出，取交集。"
# 日期路径里每移动一天要扣 change_weight（求解请求默认 100000），
# 软的日期规则权重低于它时不会真的改日期，录入界面必须先讲清楚这个取舍。
_DATE_SOFT_WEIGHT_HINT = (
    "有父课表时优先最小化变更课次数，软偏好仅用于同等变更数之间选择。"
    "首次排课时每移动一天扣 change_weight"
    "（求解请求默认 100000）。权重低于它时日期不会变，只有调高权重或调低求解页的"
    "改动权重才会生效。"
)

RULE_CONSTRAINTS: dict[str, dict[str, Any]] = {
    "declared_constraint": {
        "label": "制度声明",
        "description": "尚未结构化的自然语言规则，只登记留痕，不进入求解模型。",
        "scope": [],
        "scope_fields": [],
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [], "soft": []},
    },
    "fixed_slot": {
        "label": "固定时段",
        "description": "课次必须排在指定时段。",
        "scope": ["slot_id"],
        "scope_fields": [_SLOT_ONE],
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH, SLOT_PATH], "soft": [DATE_PATH, SLOT_PATH]},
    },
    "forbidden_slot": {
        "label": "禁排时段",
        "description": "课次不得排在这些时段。",
        "scope": ["slot_ids"],
        "scope_fields": [_SLOT_MANY],
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH, SLOT_PATH], "soft": [DATE_PATH, SLOT_PATH]},
    },
    "unavailable_slot": {
        "label": "不可用时段",
        "description": "与禁排时段等价，用于表达对象本身不可用（如教师请假时段）。",
        "scope": ["slot_ids"],
        "scope_fields": [_SLOT_MANY],
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH, SLOT_PATH], "soft": [DATE_PATH, SLOT_PATH]},
    },
    "preferred_slot": {
        "label": "偏好时段",
        "description": "尽量排在这些时段，排不下时按权重扣分。",
        "scope": ["slot_ids"],
        "scope_fields": [_SLOT_MANY],
        "hardness": ["soft"],
        "solver_paths": {"soft": [DATE_PATH, SLOT_PATH]},
    },
    "consecutive_sessions": {
        "label": "连续课次",
        "description": (
            "同一对象的课次尽量连排。当前模型按两两相邻计分，尚未按 minimum_consecutive 精确建模。"
        ),
        "scope": ["minimum_consecutive"],
        "scope_fields": [
            {
                "name": "minimum_consecutive",
                "label": "最少连排节数",
                "kind": "integer",
                "minimum": 2,
            }
        ],
        "hardness": ["soft"],
        "solver_paths": {"soft": [SLOT_PATH]},
    },
    "fixed_room": {
        "label": "固定教室",
        "description": "课次必须安排在指定教室。",
        "scope": ["room_id"],
        "scope_fields": [_ROOM_ONE],
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH, SLOT_PATH], "soft": [DATE_PATH, SLOT_PATH]},
    },
    "preferred_room": {
        "label": "偏好教室",
        "description": "尽量安排在这些教室，用别的教室按权重扣分。",
        "scope": ["room_ids"],
        "scope_fields": [_ROOM_MANY],
        "hardness": ["soft"],
        "solver_paths": {"soft": [DATE_PATH]},
    },
    "forbidden_room": {
        "label": "禁用教室",
        "description": "课次不得安排在这些教室。",
        "scope": ["room_ids"],
        "scope_fields": [_ROOM_MANY],
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH], "soft": [DATE_PATH]},
    },
    "unavailable_room": {
        "label": "不可用教室",
        "description": "与禁用教室等价，用于表达教室本身不可用（如场地维修）。",
        "scope": ["room_ids"],
        "scope_fields": [_ROOM_MANY],
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH], "soft": [DATE_PATH]},
    },
    "fixed_date": {
        "label": "固定日期",
        "description": "课次必须落在指定日期。",
        "scope": ["date", "date_from"],
        "scope_fields": [_DATE_ONE],
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH], "soft": [DATE_PATH]},
        "soft_weight_hint": _DATE_SOFT_WEIGHT_HINT,
    },
    "preferred_date": {
        "label": "偏好日期",
        "description": "尽量落在指定日期，排到别的日期按权重扣分。",
        "scope": ["date", "date_from"],
        "scope_fields": [_DATE_ONE],
        "hardness": ["soft"],
        "solver_paths": {"soft": [DATE_PATH]},
        "soft_weight_hint": _DATE_SOFT_WEIGHT_HINT,
    },
    "date_range": {
        "label": "日期范围",
        "description": f"课次只能落在给定的日期范围内。{_DATE_RANGE_HINT}",
        "scope": _DATE_RANGE_SCOPE,
        "scope_fields": _DATE_RANGE_FIELDS,
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH], "soft": [DATE_PATH]},
        "soft_weight_hint": _DATE_SOFT_WEIGHT_HINT,
    },
    "date_window": {
        "label": "日期范围（别名：浮动窗口）",
        "description": f"与「日期范围」完全等价，保留给助手与 Aily 已有的表达。{_DATE_RANGE_HINT}",
        "scope": _DATE_RANGE_SCOPE,
        "scope_fields": _DATE_RANGE_FIELDS,
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH], "soft": [DATE_PATH]},
        "soft_weight_hint": _DATE_SOFT_WEIGHT_HINT,
        "alias_of": "date_range",
    },
    "allowed_date_range": {
        "label": "日期范围（别名：允许区间）",
        "description": f"与「日期范围」完全等价，保留给助手与 Aily 已有的表达。{_DATE_RANGE_HINT}",
        "scope": _DATE_RANGE_SCOPE,
        "scope_fields": _DATE_RANGE_FIELDS,
        "hardness": ["hard", "soft"],
        "solver_paths": {"hard": [DATE_PATH], "soft": [DATE_PATH]},
        "soft_weight_hint": _DATE_SOFT_WEIGHT_HINT,
        "alias_of": "date_range",
    },
}

DATE_SCOPE_KEYS = ("date", "date_from", "date_to")
DAY_COUNT_SCOPE_KEYS = ("date_window_days", "window_days", "before_days", "after_days", "days")


def _scope_id_set(scope: dict[str, Any], single_key: str, plural_key: str) -> set[str]:
    values = scope.get(plural_key) or []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    collected = {str(item) for item in values if item not in (None, "")}
    if scope.get(single_key) not in (None, ""):
        collected.add(str(scope[single_key]))
    return collected


def _validate_scope_dates(scope: dict[str, Any]) -> None:
    parsed: dict[str, date] = {}
    for key in DATE_SCOPE_KEYS:
        raw = scope.get(key)
        if raw in (None, ""):
            continue
        if isinstance(raw, date) and not isinstance(raw, datetime):
            parsed[key] = raw
            continue
        try:
            parsed[key] = date.fromisoformat(str(raw)[:10])
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"规则范围的 {key} 不是合法日期，需要 YYYY-MM-DD 格式：{raw}",
            ) from None
    if "date_from" in parsed and "date_to" in parsed and parsed["date_from"] > parsed["date_to"]:
        raise HTTPException(status_code=422, detail="规则范围的 date_from 必须早于或等于 date_to")


def _validate_scope_integers(catalog: dict[str, Any], scope: dict[str, Any]) -> None:
    minimums = {key: 0 for key in DAY_COUNT_SCOPE_KEYS}
    for field in catalog.get("scope_fields") or []:
        if field.get("kind") == "integer":
            minimums[str(field["name"])] = int(field.get("minimum", 0))
    for key, minimum in minimums.items():
        raw = scope.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, bool):
            value = None
        else:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = None
        if value is None or value < minimum:
            raise HTTPException(
                status_code=422, detail=f"规则范围的 {key} 必须是不小于 {minimum} 的整数：{raw}"
            )


def validate_rule_entities(
    db: Session, payload: RuleCreate | RuleUpdate, schedule_set_id: str
) -> None:
    catalog = RULE_CONSTRAINTS.get(payload.constraint_type)
    if catalog is None:
        supported = "、".join(sorted(RULE_CONSTRAINTS))
        raise HTTPException(
            status_code=422,
            detail=f"不支持的约束类型：{payload.constraint_type}。可用类型：{supported}",
        )
    if payload.hardness not in catalog["hardness"]:
        raise HTTPException(status_code=422, detail="该约束类型不支持当前硬软属性")
    if payload.hardness == "soft" and (payload.weight is None or payload.weight <= 0):
        raise HTTPException(status_code=422, detail="软约束必须提供正整数权重")
    required_scope = catalog["scope"]
    if required_scope and not any(
        payload.scope.get(key) not in (None, "", [], ()) for key in required_scope
    ):
        raise HTTPException(
            status_code=422, detail=f"规则范围缺少字段: {', '.join(required_scope)}"
        )
    _validate_scope_dates(payload.scope)
    _validate_scope_integers(catalog, payload.scope)

    model_by_actor = {
        "teacher": Teacher,
        "class": ClassGroup,
        "room": Room,
        "course": CourseSession,
    }
    model: Any = model_by_actor.get(payload.actor_type)
    if model is not None and payload.actor_ids:
        existing = set(
            db.scalars(
                select(model.business_id).where(
                    model.schedule_set_id == schedule_set_id,
                    model.business_id.in_(payload.actor_ids),
                )
            ).all()
        )
        missing = sorted(set(payload.actor_ids) - existing)
        if missing:
            raise HTTPException(
                status_code=422, detail=f"规则引用了不存在的实体: {', '.join(missing)}"
            )

    # scope 里的业务 ID 与 actor_ids 无关：全局规则（actor_ids 为空）同样会把
    # slot/room 交给求解器，早退会让不存在的时段悄悄进入模型。
    slot_ids = _scope_id_set(payload.scope, "slot_id", "slot_ids")
    if slot_ids:
        existing_slots = set(
            db.scalars(
                select(TimeSlot.business_id).where(
                    TimeSlot.schedule_set_id == schedule_set_id, TimeSlot.business_id.in_(slot_ids)
                )
            ).all()
        )
        missing_slots = sorted(slot_ids - existing_slots)
        if missing_slots:
            raise HTTPException(
                status_code=422, detail=f"规则引用了不存在的时段: {', '.join(missing_slots)}"
            )
    room_ids = _scope_id_set(payload.scope, "room_id", "room_ids")
    if room_ids:
        existing_rooms = set(
            db.scalars(
                select(Room.business_id).where(
                    Room.schedule_set_id == schedule_set_id, Room.business_id.in_(room_ids)
                )
            ).all()
        )
        missing_rooms = sorted(room_ids - existing_rooms)
        if missing_rooms:
            raise HTTPException(
                status_code=422, detail=f"规则引用了不存在的教室: {', '.join(missing_rooms)}"
            )


@router.get(
    "/rules/constraint-catalog", response_model=list[ConstraintCatalogEntry], tags=["rules"]
)
def list_constraint_catalog(user: CurrentUser) -> list[dict[str, Any]]:
    """约束类型目录：可选类型、各自的范围字段、以及在哪条求解路径下真正生效。"""
    return [{"type": key, **value} for key, value in RULE_CONSTRAINTS.items()]


@router.get("/rules", response_model=list[RuleResponse], tags=["rules"])
def list_rules(
    db: Db,
    user: CurrentUser,
    scope: ViewerScope,
    rule_status: str | None = Query(default=None, alias="status"),
) -> list[Rule]:
    statement = select(Rule).where(Rule.schedule_set_id == scope.id).order_by(Rule.business_id)
    if rule_status:
        statement = statement.where(Rule.status == rule_status)
    return list(db.scalars(statement))


@router.post("/rules", response_model=RuleResponse, status_code=201, tags=["rules"])
def create_rule(payload: RuleCreate, db: Db, user: AdminOrScheduler, scope: SchedulerScope) -> Rule:
    validate_rule_entities(db, payload, scope.id)
    rule_count = db.scalar(select(func.count(Rule.id)).where(Rule.schedule_set_id == scope.id))
    business_id = payload.business_id or f"RL-{int(rule_count or 0) + 1:04d}"
    data = payload.model_dump(exclude={"business_id"})
    # 状态只能由 transition 端点推进，接口不接受调用方直接写 active。
    instance = Rule(
        schedule_set_id=scope.id,
        business_id=business_id,
        status="awaiting_confirmation",
        **data,
    )
    db.add(instance)
    audit(db, user, "create", "rule", business_id, data)
    db.commit()
    db.refresh(instance)
    return instance


@router.put("/rules/{rule_id}", response_model=RuleResponse, tags=["rules"])
def update_rule(
    rule_id: str, payload: RuleUpdate, db: Db, user: AdminOrScheduler, scope: SchedulerScope
) -> Rule:
    rule = get_scoped_or_404(db, Rule, rule_id, scope)
    if rule.status not in {"draft", "awaiting_confirmation"}:
        raise HTTPException(status_code=409, detail="只有待确认规则可以编辑")
    validate_rule_entities(db, payload, scope.id)
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    rule.version += 1
    audit(db, user, "update", "rule", rule.business_id, payload.model_dump())
    db.commit()
    db.refresh(rule)
    return rule


@router.post("/rules/{rule_id}/transition", response_model=RuleResponse, tags=["rules"])
def transition_rule(
    rule_id: str,
    payload: RuleTransition,
    db: Db,
    user: AdminOrScheduler,
    scope: SchedulerScope,
) -> Rule:
    rule = get_scoped_or_404(db, Rule, rule_id, scope)
    allowed = {
        "draft": {"active", "rejected"},
        "awaiting_confirmation": {"active", "rejected"},
        "active": {"retired"},
        "rejected": set(),
        "retired": set(),
    }
    if payload.status not in allowed.get(rule.status, set()):
        raise HTTPException(status_code=409, detail=f"不允许从 {rule.status} 转为 {payload.status}")
    if payload.status == "active":
        coverage = build_snapshot_payload(db, scope.id)
        coverage["rules"] = [{
            "business_id": rule.business_id, "constraint_type": rule.constraint_type,
            "hardness": rule.hardness, "scope": rule.scope,
            "actor_type": rule.actor_type, "actor_ids": rule.actor_ids,
        }]
        _validate_rule_coverage(coverage)
    rule.status = payload.status
    rule.version += 1
    if payload.status == "active":
        rule.approved_by = user.id
    audit(db, user, "transition", "rule", rule.business_id, payload.model_dump())
    db.commit()
    db.refresh(rule)
    return rule


# ---------------------------------------------------------------- 记忆层（L1 偏好库）
# 设计与红线见 docs/roadmap/02-agent-memory.md §3：induced 条目永不升硬约束、
# 挖掘候选一律先观察（probation）、过期作废不删除。

# 多态主体 → 主数据模型。dict 值标注为 type[Any]：四种模型的字段集合不同，
# 统一走 business_id / schedule_set_id 两个共有列查询。
_PREFERENCE_SUBJECT_MODELS: dict[str, type[Any]] = {
    "teacher": Teacher,
    "classroom": Room,
    "cohort": ClassGroup,
    "course": CourseSession,
}


def _validate_preference_subject(
    db: Session, subject_type: str, subject_id: str, schedule_set_id: str
) -> None:
    model = _PREFERENCE_SUBJECT_MODELS.get(subject_type)
    if model is None:
        raise HTTPException(status_code=422, detail=f"未知的偏好主体类型：{subject_type}")
    instance = db.scalar(
        select(model).where(
            model.schedule_set_id == schedule_set_id, model.business_id == subject_id
        )
    )
    if instance is None:
        raise HTTPException(
            status_code=422, detail=f"偏好主体不存在：{subject_type} {subject_id}"
        )


@router.get("/memory/preferences", response_model=list[PreferenceResponse], tags=["memory"])
def list_preferences(
    db: Db,
    user: CurrentUser,
    scope: ViewerScope,
    subject_type: str | None = None,
    subject_id: str | None = None,
    status: str | None = Query(default=None),
) -> list[PreferenceEntry]:
    statement = select(PreferenceEntry).where(PreferenceEntry.schedule_set_id == scope.id)
    if subject_type:
        statement = statement.where(PreferenceEntry.subject_type == subject_type)
    if subject_id:
        statement = statement.where(PreferenceEntry.subject_id == subject_id)
    if status:
        statement = statement.where(PreferenceEntry.status == status)
    return list(
        db.scalars(statement.order_by(PreferenceEntry.created_at.desc(), PreferenceEntry.id))
    )


@router.post(
    "/memory/preferences",
    response_model=PreferenceResponse,
    status_code=201,
    tags=["memory"],
)
def create_preference(
    payload: PreferenceCreate, db: Db, user: AdminOrScheduler, scope: SchedulerScope
) -> PreferenceEntry:
    if payload.predicate not in ALL_PREDICATES:
        raise HTTPException(status_code=422, detail=f"未知的偏好谓词：{payload.predicate}")
    _validate_preference_subject(db, payload.subject_type, payload.subject_id, scope.id)
    today = shanghai_now().date()
    entry = PreferenceEntry(
        schedule_set_id=scope.id,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        predicate=payload.predicate,
        constraint=payload.constraint,
        modality=payload.modality,
        confidence=payload.confidence,
        source=payload.source,
        evidence=payload.evidence,
        weight=payload.weight,
        # 显式声明的偏好无需再走确认队列；挖掘候选才从 probation 起步。
        status="confirmed",
        valid_from=today,
        # MEM-C1：默认有效期优先取本方案主数据最大上课日期（随学期失效），
        # 方案内没有任何课次时回落「今天 + 180 天」。
        valid_until=payload.valid_until or default_valid_until_for_scope(db, scope.id),
        provenance={
            "origin": "api",
            "created_by": user.id,
            "note": payload.note,
            "created_at": shanghai_now().isoformat(),
        },
    )
    db.add(entry)
    # 矛盾消解（MEM-C2 修正 4）：手工创建同样做三分支消解（替代/分时段/存疑冲突）。
    db.flush()  # 先取 id，供旧条目 provenance 记 superseded_by / conflict_with
    resolve_conflicts_for_new_entry(db, entry)
    audit(db, user, "create", "preference_entry", entry.id, payload.model_dump(mode="json"))
    db.commit()
    db.refresh(entry)
    return entry


@router.patch("/memory/preferences/{entry_id}", response_model=PreferenceResponse, tags=["memory"])
def update_preference(
    entry_id: str, payload: PreferenceUpdate, db: Db, user: AdminOrScheduler, scope: SchedulerScope
) -> PreferenceEntry:
    entry = get_scoped_or_404(db, PreferenceEntry, entry_id, scope)
    if entry.status not in {"probation", "confirmed"}:
        # rejected/expired 是历史审计记录，改内容会让记忆链不可信。
        raise HTTPException(status_code=409, detail=f"{entry.status} 状态的偏好不能编辑")
    data = payload.model_dump(exclude_unset=True)
    if data.get("predicate") is not None and data["predicate"] not in ALL_PREDICATES:
        raise HTTPException(status_code=422, detail=f"未知的偏好谓词：{data['predicate']}")
    # 记下编辑前的同组键：改谓词时旧组也要重算冲突标。
    original_group = (entry.subject_type, entry.subject_id, entry.predicate)
    for key in ("weight", "predicate", "valid_until"):
        if key in data:
            setattr(entry, key, data[key])
    if data.get("constraint") is not None:
        entry.constraint = data["constraint"]
    if data.get("scope"):
        # scope 是 constraint 的便捷合并入口，不整体替换已有约束。
        merged = dict(entry.constraint or {})
        merged.update(data["scope"])
        entry.constraint = merged
    entry.provenance = {
        **(entry.provenance or {}),
        "last_edited_by": user.id,
        "last_edited_at": shanghai_now().isoformat(),
    }
    audit(db, user, "update", "preference_entry", entry.id, payload.model_dump(mode="json"))
    # 唯一冲突重算入口（MEM-D1 D2）：编辑约束/有效期/谓词后重算 proposed_conflict
    # 标——编辑把互斥改兼容（或窗口错开）时自动清标，改出互斥时标记落编辑一方
    # （组内较新条目）。
    refresh_conflict_flags(db, entry.schedule_set_id, *original_group)
    if (entry.subject_type, entry.subject_id, entry.predicate) != original_group:
        refresh_conflict_flags(
            db,
            entry.schedule_set_id,
            entry.subject_type,
            entry.subject_id,
            entry.predicate,
        )
    db.commit()
    db.refresh(entry)
    return entry


@router.post(
    "/memory/preferences/{entry_id}/transition",
    response_model=PreferenceResponse,
    tags=["memory"],
)
def transition_preference(
    entry_id: str,
    payload: PreferenceTransition,
    db: Db,
    user: AdminOrScheduler,
    scope: SchedulerScope,
) -> PreferenceEntry:
    entry = get_scoped_or_404(db, PreferenceEntry, entry_id, scope)
    if payload.action == "authorize_trial":
        # 三态拆分（MEM-C1）：授权试用只对 probation 条目可用；条目保持 probation，
        # 以小权重参与求解，trial_until 到期自动退出。这是教务显式动作，不是自动行为。
        if entry.status != "probation":
            raise HTTPException(
                status_code=409, detail=f"{entry.status} 状态的偏好无需授权试用"
            )
        today = shanghai_now().date()
        entry.trial_authorized = True
        entry.trial_until = today + timedelta(days=payload.trial_days)
        provenance = {
            **(entry.provenance or {}),
            "trial_authorized_by": user.id,
            "trial_authorized_at": shanghai_now().isoformat(),
            "trial_days": payload.trial_days,
            "trial_reason": payload.reason,
        }
        if entry.conflict:
            # 冲突裁决之一（MEM-D1）：授权试用即对候选提出的 proposed_conflict 做
            # 出显式裁决——旧新并存（旧条目全权、本条以试用期小权重参与）。记录
            # 已裁决共存的对方 id，refresh_conflict_flags 据此不再重打标记。
            resolved = {
                str(item) for item in provenance.get("conflict_resolved_with") or []
            } | {str(item) for item in provenance.get("conflict_with") or []}
            provenance["conflict_resolved_with"] = sorted(resolved)
            provenance["conflict_resolved_at"] = shanghai_now().isoformat()
            provenance["conflict_resolved_mode"] = "authorize_trial"
            entry.conflict = False
        entry.provenance = provenance
        # 唯一冲突重算入口（MEM-D1）：授权试用后同组 proposed_conflict 标照常重算。
        refresh_conflict_flags(
            db, entry.schedule_set_id, entry.subject_type, entry.subject_id, entry.predicate
        )
        audit(
            db,
            user,
            "authorize_trial",
            "preference_entry",
            entry.id,
            payload.model_dump(mode="json"),
        )
        db.commit()
        db.refresh(entry)
        return entry
    assert payload.target_status is not None  # schema validator 已保证
    if payload.target_status not in PREFERENCE_TRANSITIONS.get(entry.status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"不允许从 {entry.status} 转为 {payload.target_status}",
        )
    # 红线①：从调课归纳的偏好永不自动升为硬约束——硬约束只能来自教务显式
    # 声明（explicit_stated）或管理员指令（admin_directive），防止记忆漂移
    # 演变成数据事故。
    if payload.target_modality == "hard" and entry.source == "induced_from_adjustment":
        raise HTTPException(
            status_code=422,
            detail="从调课归纳的偏好不能升级为硬约束；硬约束只能来自显式声明或管理员指令",
        )
    if payload.target_modality is not None:
        entry.modality = payload.target_modality
    entry.status = payload.target_status
    if payload.target_status in {"rejected", "expired"}:
        # 离开活跃集：本条提出的 proposed_conflict 随本次裁决尘埃落定，不再挂在
        # 历史卡片上（refresh 只重算仍活跃的条目）。
        entry.conflict = False
    provenance = {
        **(entry.provenance or {}),
        "last_transition_by": user.id,
        "last_transition_at": shanghai_now().isoformat(),
        "last_transition_reason": payload.reason,
    }
    if payload.target_status == "expired" and payload.supersedes:
        # 冲突裁决之一（MEM-D1）：「以新替旧」= 旧条目 transition 到 expired 时带
        # supersedes=<候选 id>，审计链记 superseded_by；随后候选再走一次 transition
        # 到 confirmed 即完成切换。切换只发生在显式裁决之后。
        successor = db.scalar(
            select(PreferenceEntry).where(
                PreferenceEntry.id == payload.supersedes,
                PreferenceEntry.schedule_set_id == entry.schedule_set_id,
            )
        )
        if successor is None or successor.id == entry.id:
            raise HTTPException(
                status_code=422, detail="supersedes 指向的偏好条目不存在或不合法"
            )
        provenance["superseded_by"] = successor.id
    entry.provenance = provenance
    if payload.target_status == "rejected":
        # 拒绝记忆（MEM-C2 修正 4）：拒绝原因按受控枚举落 preference_rejections，
        # 同签名同证据的候选不再复现；API 缺省「其他」，前端必填。同签名同证据
        # 同原因的重复拒绝不重复落库（幂等，审计靠 preference_entries 链）。
        _record_preference_rejection(
            db,
            entry=entry,
            reason=payload.rejection_reason or "other",
            note=payload.reason,
            rejected_by=user.id,
        )
    # 矛盾消解（MEM-C2 修正 4 / MEM-D1）：条目状态变化（confirmed/rejected/expired）
    # 后重算同组 proposed_conflict 标——提出方的对方离开活跃集即自动清标。
    refresh_conflict_flags(
        db, entry.schedule_set_id, entry.subject_type, entry.subject_id, entry.predicate
    )
    audit(db, user, "transition", "preference_entry", entry.id, payload.model_dump(mode="json"))
    db.commit()
    db.refresh(entry)
    return entry


def _record_preference_rejection(
    db: Session, *, entry: PreferenceEntry, reason: str, note: str | None, rejected_by: str | None
) -> None:
    """把一次拒绝写入 preference_rejections（幂等：完全相同的签名+证据+原因跳过）。"""
    signature = (
        entry.subject_type,
        entry.subject_id,
        entry.predicate,
        normalized_constraint(entry.constraint),
        reason,
    )
    evidence = sorted({str(item) for item in entry.evidence or []})
    for existing in db.scalars(
        select(PreferenceRejection).where(
            PreferenceRejection.schedule_set_id == entry.schedule_set_id,
            PreferenceRejection.subject_type == entry.subject_type,
            PreferenceRejection.subject_id == entry.subject_id,
            PreferenceRejection.predicate == entry.predicate,
            PreferenceRejection.reason == reason,
        )
    ):
        if (
            normalized_constraint(existing.constraint) == signature[3]
            and sorted({str(item) for item in existing.evidence or []}) == evidence
        ):
            return
    db.add(
        PreferenceRejection(
            schedule_set_id=entry.schedule_set_id,
            subject_type=entry.subject_type,
            subject_id=entry.subject_id,
            predicate=entry.predicate,
            constraint=entry.constraint or {},
            reason=reason,
            note=note,
            evidence=evidence,
            rejected_by=rejected_by,
        )
    )


@router.post(
    "/memory/preferences/{entry_id}/convert-to-rule",
    response_model=RuleResponse,
    status_code=201,
    tags=["memory"],
)
def convert_preference_to_rule(
    entry_id: str,
    payload: PreferenceConvertRequest,
    db: Db,
    user: Admin,
    scope: SchedulerScope,
) -> Rule:
    """把 hard 偏好条目转成正式规则（MEM-C1 修正 2）：偏好库只管理软偏好。

    正式规则 hardness=hard、kind 对齐 constraint-catalog 类型；provenance 经
    source_doc 回链 memory:<entry_id>。条目 transition 到 expired 并记录 rule_id，
    过期作废不删除，审计链保留。induced 来源必须显式传 confirmed_conversion=true
    （红线①的兜底：归纳出的偏好不得在无人确认时变成硬规则）。
    """
    entry = get_scoped_or_404(db, PreferenceEntry, entry_id, scope)
    if entry.modality != "hard":
        raise HTTPException(status_code=409, detail="只有 modality=hard 的偏好需要转正式规则")
    if entry.status not in {"probation", "confirmed"}:
        raise HTTPException(status_code=409, detail=f"{entry.status} 状态的偏好不能转换")
    if entry.source == "induced_from_adjustment" and not payload.confirmed_conversion:
        raise HTTPException(
            status_code=422,
            detail="从调课归纳的偏好转硬规则必须显式确认：请传 confirmed_conversion=true",
        )
    path = PREDICATE_SOLVER_PATHS.get(entry.predicate)
    kind = PREFERENCE_RULE_KINDS.get(entry.predicate)
    if path is None or kind is None:
        raise HTTPException(
            status_code=422,
            detail=f"谓词 {entry.predicate} 没有对齐的硬规则类型，不能转换为正式规则",
        )
    constraint = entry.constraint or {}
    scope_key = path.get("scope_key")
    raw_values = constraint.get(scope_key) if scope_key else None
    values = [str(item) for item in raw_values or [] if item]
    if kind in {"fixed_slot", "fixed_room"}:
        # 「偏好」硬化为「固定」后语义是必须落在其中：只能有一个目标，scope
        # 用目录的单数键（slot_id/room_id），与两条求解路径的读取口径一致。
        if len(values) != 1:
            raise HTTPException(
                status_code=422,
                detail="偏好转固定类型必须恰好指定一个时段/教室；请先在偏好里修订约束",
            )
        rule_scope: dict[str, Any] = {"slot_id" if kind == "fixed_slot" else "room_id": values[0]}
    else:
        if not values:
            raise HTTPException(status_code=422, detail="偏好约束里没有可转换的时段/教室")
        rule_scope = {scope_key: values} if scope_key else {}
    # 生效日期窗口随转换保留（审计与未来硬规则日期窗的依据）；当前硬规则路径
    # 暂不按日期过滤，求解范围仍由方案与规则范围决定。
    if entry.valid_from is not None:
        rule_scope["date_from"] = entry.valid_from.isoformat()
    if entry.valid_until is not None:
        rule_scope["date_to"] = entry.valid_until.isoformat()
    rule = Rule(
        schedule_set_id=scope.id,
        business_id=f"MEMRULE-{entry.id}",
        source_text=f"由记忆偏好转换：{entry.subject_type} {entry.subject_id} {entry.predicate}"
        + (f"（{entry.provenance.get('note')}）" if (entry.provenance or {}).get("note") else ""),
        actor_type=RULE_ACTOR_TYPES.get(entry.subject_type, entry.subject_type),
        actor_ids=[entry.subject_id],
        constraint_type=kind,
        scope=rule_scope,
        hardness="hard",
        weight=None,
        source_doc=f"memory:{entry.id}",
        confidence=float(entry.confidence or 0.0),
        status="active",
        approved_by=user.id,
    )
    db.add(rule)
    entry.status = "expired"
    entry.conflict = False  # 离开活跃集，提出的冲突裁决已了结（转正式规则）
    entry.provenance = {
        **(entry.provenance or {}),
        "converted_to_rule_by": user.id,
        "converted_to_rule_at": shanghai_now().isoformat(),
        "rule_id": rule.business_id,
    }
    audit(
        db,
        user,
        "convert_to_rule",
        "preference_entry",
        entry.id,
        {"rule_business_id": rule.business_id, **payload.model_dump(mode="json")},
    )
    db.commit()
    db.refresh(rule)
    return rule


def _persist_mining_candidates(
    db: Session,
    schedule_set_id: str,
    candidates: list[dict[str, Any]],
    *,
    provenance_base: dict[str, Any],
) -> tuple[list[PreferenceEntry], int, int]:
    """候选一律落库为 probation + induced_from_adjustment；重复候选与已拒候选去重。

    去重三道（MEM-C2 修正 4）：
    ① 同 subject+predicate+constraint 已存在 probation/confirmed 则跳过；
    ② 同签名的拒绝记录里，候选 evidence ⊆ 已拒 evidence → 跳过（不再打扰）；
    ③ 含新证据 → 允许重提，但候选 provenance.previously_rejected 标注此前被拒
       原因，前端卡片渲染「此前被拒」徽标。
    落库后对每个新条目做同主体同谓词的矛盾消解（替代/分时段/存疑冲突）。
    返回（新建条目, 跳过-已存在, 跳过-已拒）。
    """
    existing = db.scalars(
        select(PreferenceEntry).where(
            PreferenceEntry.schedule_set_id == schedule_set_id,
            PreferenceEntry.status.in_(["probation", "confirmed"]),
        )
    )
    existing_keys = {
        (
            item.subject_type,
            item.subject_id,
            item.predicate,
            normalized_constraint(item.constraint),
        )
        for item in existing
    }
    # 拒绝记录按签名归集：{签名 -> [拒绝记录]}。拒绝记录量级 = 拒绝次数，全量
    # 取回后在 Python 比对规范化 JSON（SQLite/MySQL 对 JSON 索引语义不一致）。
    rejections: dict[tuple[str, str, str, str], list[PreferenceRejection]] = {}
    for record in db.scalars(
        select(PreferenceRejection).where(
            PreferenceRejection.schedule_set_id == schedule_set_id
        )
    ):
        key = (
            record.subject_type,
            record.subject_id,
            record.predicate,
            normalized_constraint(record.constraint),
        )
        rejections.setdefault(key, []).append(record)
    today = shanghai_now().date()
    created: list[PreferenceEntry] = []
    skipped_existing = 0
    skipped_rejected = 0
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        key = (
            str(candidate["subject_type"]),
            str(candidate["subject_id"]),
            str(candidate["predicate"]),
            normalized_constraint(candidate.get("constraint")),
        )
        if key in existing_keys or key in seen:
            skipped_existing += 1
            continue
        candidate_evidence = {str(item) for item in candidate.get("evidence_ids") or []}
        prior = rejections.get(key) or []
        if prior:
            rejected_evidence: set[str] = set()
            for record in prior:
                rejected_evidence.update(str(item) for item in record.evidence or [])
            if candidate_evidence and candidate_evidence <= rejected_evidence:
                skipped_rejected += 1
                continue
        seen.add(key)
        # 带新证据重提：标注此前被拒原因（取最近一次拒绝记录），教务可见。
        latest_rejection = max(prior, key=lambda record: record.created_at) if prior else None
        candidate_constraint = candidate.get("constraint") or {}
        # MEM-D1（§7 D2）：候选 constraint 自带日期窗口时，条目级默认有效期取
        # 「覆盖该窗口的最小合理范围」（不得比 constraint 窗口更宽）；无日期维持
        # 原默认（今天起 +180 天）。
        valid_from, valid_until = candidate_entry_validity(candidate_constraint, today)
        entry = PreferenceEntry(
            schedule_set_id=schedule_set_id,
            subject_type=key[0],
            subject_id=key[1],
            predicate=key[2],
            constraint=candidate_constraint,
            modality="soft",
            confidence=MINED_DEFAULT_CONFIDENCE,
            # 红线③：挖掘产生的条目初始状态恒为 probation，权重与硬约束
            # 资格都要等教务确认后才生效。
            source="induced_from_adjustment",
            status="probation",
            evidence=sorted(candidate_evidence),
            weight=MINED_DEFAULT_WEIGHT,
            valid_from=valid_from,
            valid_until=valid_until,
            provenance={
                **provenance_base,
                "rationale": candidate.get("rationale"),
                **(
                    {
                        "previously_rejected": {
                            "reason": latest_rejection.reason,
                            "note": latest_rejection.note,
                            "rejected_at": latest_rejection.created_at.isoformat(),
                        }
                    }
                    if latest_rejection is not None
                    else {}
                ),
            },
        )
        db.add(entry)
        db.flush()  # 先取 id，供旧条目 provenance 记 superseded_by / conflict_with
        resolve_conflicts_for_new_entry(db, entry)
        created.append(entry)
    return created, skipped_existing, skipped_rejected


@router.post("/memory/mining-runs", response_model=MiningRunResponse, tags=["memory"])
def create_memory_mining_run(
    db: Db, user: AdminOrScheduler, scope: SchedulerScope
) -> MiningRunResponse:
    """回顾本学期的调课事件，归纳偏好候选（human-in-the-loop 的入口）。

    可学习事件经公共前置筛选（MEM-D1 §7 D3，口径见
    memory_solver.learning_basis_events）：当前方案内、最近 90 天滚动窗口、
    候选未被取消或拒绝、declared_reason 非临时被迫类。配置了 AI 走模型归纳
    （模型只提名，代码按白名单与证据支持性裁决；输入已预筛，提示词不再要求
    模型自行过滤）；未配置或调用失败优雅降级为确定性统计：同主体+同类型+
    同归因类调课 ≥2 次即产生候选。
    """
    events = learning_basis_events(db, scope.id)
    views = [mining_event_view(event) for event in events]
    engine = "deterministic"
    raw_candidates: list[dict[str, Any]] | None = None
    skipped_invalid = 0
    ai_error: str | None = None
    model_name: str | None = None
    usage: dict[str, Any] | None = None
    service = AIService(settings, db)
    if service.configuration_view()["configured"]:
        try:
            raw_candidates, usage = service.mine_preferences(views)
            engine = "ai"
            model_name = service.configuration_view().get("model")
        except AIServiceError as exc:
            ai_error = str(exc)
            logger.warning("偏好挖掘 AI 调用失败，退化为确定性统计：%s", exc)
    if raw_candidates is not None:
        candidates, skipped_invalid = validate_ai_candidates(raw_candidates, views)
    else:
        candidates = deterministic_preference_candidates(views)
    created, skipped_existing, skipped_rejected = _persist_mining_candidates(
        db,
        scope.id,
        candidates,
        provenance_base={
            "origin": "mining",
            "engine": engine,
            "model": model_name,
            "mined_at": shanghai_now().isoformat(),
            "mined_by": user.id,
            "events_scanned": len(views),
            "usage": usage,
        },
    )
    audit(
        db,
        user,
        "create",
        "memory_mining_run",
        None,
        {
            "engine": engine,
            "events_scanned": len(views),
            "created": len(created),
            "skipped_existing": skipped_existing,
            "skipped_rejected": skipped_rejected,
            "skipped_invalid": skipped_invalid,
        },
    )
    db.commit()
    for entry in created:
        db.refresh(entry)
    return MiningRunResponse(
        engine=engine,
        events_scanned=len(views),
        created=[PreferenceResponse.model_validate(entry) for entry in created],
        skipped_existing=skipped_existing,
        skipped_rejected=skipped_rejected,
        skipped_invalid=skipped_invalid,
        ai_error=ai_error,
    )


def _validate_rule_coverage(payload: dict[str, Any]) -> None:
    selected = _selected_sessions(payload)
    unsupported = []
    for rule in payload.get("rules", []):
        catalog = RULE_CONSTRAINTS.get(rule["constraint_type"], {})
        paths = catalog.get("solver_paths", {}).get(rule.get("hardness", "hard"), [])
        matches = [course for course in selected if any(
            _session_matches_rule(course, str(room.get("business_id", "")), rule)
            for room in payload.get("rooms", []) or [{}]
        )]
        required_paths = {"date" if _has_date_information(course) else "slot" for course in matches}
        excessive_consecutive = (
            rule["constraint_type"] == "consecutive_sessions"
            and int((rule.get("scope") or {}).get("minimum_consecutive", 2)) != 2
        )
        if not paths or excessive_consecutive or required_paths - set(paths):
            unsupported.append(str(rule["business_id"]))
    if unsupported:
        raise HTTPException(status_code=422, detail=(
            "以下规则尚未接入当前求解模型，请先调整规则类型、范围或停用后再求解："
            + "、".join(unsupported)
        ))


def _resolve_goal_for_run(
    db: Session, goal_id: str | None, schedule_set_id: str
) -> SolveGoal | None:
    """求解任务关联目标（MEM-C3）的守卫：404 防跨方案探测，409 拒绝已放弃目标。

    已放弃的目标不接受新任务——验收器对 abandoned 不出报告，静默接受会造出
    一条永远不会被验收的求解记录。
    """
    if not goal_id:
        return None
    goal = get_or_404(db, SolveGoal, goal_id)
    if goal.schedule_set_id != schedule_set_id:
        raise HTTPException(status_code=404, detail="资源不存在")
    if goal.status == "abandoned":
        raise HTTPException(status_code=409, detail="目标已放弃，不能再关联新的求解任务")
    return goal


def create_solver_run(
    db: Session,
    user_id: str | None,
    request: SolveRequest | AilySolveRequest,
    schedule_set_id: str = DEFAULT_SCHEDULE_SET_ID,
    run_type: str = "initial",
    extra: dict[str, Any] | None = None,
    goal_id: str | None = None,
) -> SolverRun:
    # MEM-C1（§6 修正 6）：创建任务时即编译偏好记忆并冻结——快照带 memory 节，
    # run 落 memory_usage；执行路径只读快照，改记忆不影响在途求解的可复现性。
    # 编译整体失败不拦截排课主链路：memory 节标 compile_failed，解释层显式提示。
    try:
        memory_state = compile_memory_state(db, schedule_set_id)
    except Exception as exc:  # noqa: BLE001 - 任何记忆层故障都不应拦下课表求解
        logger.exception("偏好记忆编译失败，本次求解将不带偏好进行")
        memory_state = compile_failed_state(str(exc))
    snapshot = create_snapshot(db, user_id, schedule_set_id, memory=memory_state)
    payload: dict[str, Any] = {
        "time_limit_seconds": request.time_limit_seconds,
        "change_weight": getattr(request, "change_weight", 100000),
        "random_seed": settings.solver_random_seed,
        "search_workers": settings.solver_search_workers,
        "business_lines": request.business_lines,
        "product_types": request.product_types,
        "class_business_ids": request.class_business_ids,
        "course_business_ids": getattr(request, "course_business_ids", []),
        "date_from": request.date_from.isoformat() if request.date_from else None,
        "date_to": request.date_to.isoformat() if request.date_to else None,
        "date_window_days": request.date_window_days,
        "solver_rules": request.solver_rules,
    }
    if isinstance(request, AilySolveRequest):
        payload["instruction"] = request.instruction
    run_extra = dict(extra or {})
    if "parent_schedule_id" not in run_extra:
        parent = db.scalar(
            select(ScheduleVersion)
            .where(
                ScheduleVersion.schedule_set_id == schedule_set_id,
                ScheduleVersion.status == "published",
            )
            .order_by(ScheduleVersion.version_no.desc())
        )
        if parent:
            parent_response = schedule_response(db, parent)
            run_extra["parent_schedule_id"] = parent.id
            run_extra["previous_assignments"] = [
                item.model_dump(mode="json") for item in parent_response.assignments
            ]
    payload.update(run_extra)
    inactive_ids = {item["id"] for item in snapshot.payload.get("course_sessions", [])
                    if not item.get("is_active", True)}
    payload["excluded_course_session_ids"] = sorted(inactive_ids)
    if "previous_assignments" in payload:
        payload["previous_assignments"] = [item for item in payload["previous_assignments"]
                                           if item["course_session_id"] not in inactive_ids]
    if payload.get("assistant_entry") and not _selected_sessions({**snapshot.payload, **payload}):
        raise HTTPException(status_code=422, detail="确认的排课范围没有匹配到课次")
    _validate_rule_coverage({**snapshot.payload, **payload})
    run = SolverRun(
        schedule_set_id=schedule_set_id,
        snapshot_id=snapshot.id,
        run_type=run_type,
        status="queued",
        request_payload=payload,
        memory_usage=memory_state,
        goal_id=goal_id,
        created_by=user_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


class SolverRunExplanationDetail(SolverRunExplanation):
    """比 `SolverRunExplanation` 多一段可直接粘回「一句话排课」输入框的指令草稿。

    这段文字由 `explain.build_retry_instruction` 确定性拼出，不是模型写的，
    因此不与 AI 措辞共用契约；求解正常结束时为 None。
    """

    suggested_instruction: str | None = None


class SolverRunDetailResponse(SolverRunResponse):
    """求解任务响应，解释部分带上建议指令。

    解释以 JSON 落在 `SolverRun.explanation` 里，列表和详情都要能把这段建议原样带回
    前端，否则刷新页面后建议指令就消失了。
    """

    explanation: SolverRunExplanationDetail | None = None


@router.post(
    "/solver-runs", response_model=SolverRunDetailResponse, status_code=202, tags=["solver"]
)
def submit_solver_run(
    request: SolveRequest, db: Db, user: AdminOrScheduler, scope: SchedulerScope
) -> SolverRun:
    goal = _resolve_goal_for_run(db, request.goal_id, scope.id)
    run = create_solver_run(db, user.id, request, scope.id, goal_id=goal.id if goal else None)
    audit(
        db,
        user,
        "submit",
        "solver_run",
        run.id,
        {"goal_id": run.goal_id} if run.goal_id else None,
    )
    db.commit()
    if request.wait:
        execute_solver_run(run.id)
    else:
        enqueue_solver_run(run.id)
    db.refresh(run)
    return run


@router.get("/solver-runs", response_model=list[SolverRunDetailResponse], tags=["solver"])
def list_solver_runs(db: Db, user: CurrentUser, scope: ViewerScope) -> list[SolverRun]:
    return list(
        db.scalars(
            select(SolverRun)
            .where(SolverRun.schedule_set_id == scope.id)
            .order_by(SolverRun.created_at.desc())
            .limit(50)
        )
    )


@router.get("/solver-runs/{run_id}", response_model=SolverRunDetailResponse, tags=["solver"])
def get_solver_run(run_id: str, db: Db, user: CurrentUser, scope: ViewerScope) -> SolverRun:
    return get_scoped_or_404(db, SolverRun, run_id, scope)


@router.post(
    "/solver-runs/{run_id}/explanation",
    response_model=SolverRunExplanationDetail,
    tags=["solver"],
)
def explain_solver_run(
    run_id: str,
    db: Db,
    user: AdminOrScheduler,
    scope: SchedulerScope,
    refresh: bool = Query(default=False, description="忽略已存解释，重新调用 AI 生成"),
) -> dict[str, Any]:
    """把求解结论翻译成教务读得懂的话，并做一次意图核对。

    事实包由代码算，AI 只负责措辞与意图核对；模型不可用时退回确定性兜底解释，
    界面仍然拿得到 SYSTEM-* 的业务口径翻译，而不是裸标识。
    """
    run = get_scoped_or_404(db, SolverRun, run_id, scope)
    if run.status not in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="求解尚未结束，暂时无法解释结果")
    if run.explanation and not refresh:
        cached = dict(run.explanation)
        # 这次求解之前落库的解释没有建议指令这一段，按当前事实补一次，
        # 免得同样排不出来的两条记录一条给得出建议、一条给不出。
        if "suggested_instruction" not in cached:
            cached["suggested_instruction"] = build_retry_instruction(
                build_explanation_facts(db, run)
            )
        return cached

    facts = build_explanation_facts(db, run)
    payload = deterministic_summary(facts)
    try:
        payload = AIService(settings, db).explain_solver_run(facts)
    except AIServiceError as exc:
        payload["ai_error"] = str(exc)
    # 建议指令要能被 /assistant/interpret 原样解析，所以无论措辞来自模型还是兜底，
    # 都用同一段确定性文本，不让模型自由发挥出解析不了的指令。
    payload["suggested_instruction"] = build_retry_instruction(facts)

    run.explanation = payload
    audit(
        db,
        user,
        "explain",
        "solver_run",
        run.id,
        {"source": payload.get("source"), "refresh": refresh},
    )
    db.commit()
    return payload


@router.get("/solver-runs/{run_id}/events", tags=["solver"])
async def solver_run_events(
    run_id: str, user: CurrentUser, scope: ViewerScope
) -> StreamingResponse:
    async def event_stream():
        last_payload = ""
        while True:
            from .db import SessionLocal

            with SessionLocal() as db:
                run = db.scalar(
                    select(SolverRun).where(
                        SolverRun.id == run_id,
                        SolverRun.schedule_set_id == scope.id,
                    )
                )
                if run is None:
                    yield 'event: error\ndata: {"detail":"资源不存在"}\n\n'
                    return
                payload = json.dumps(
                    {
                        "id": run.id,
                        "status": run.status,
                        "model_status": run.model_status,
                        "objective_value": run.objective_value,
                        "best_bound": run.best_bound,
                        "wall_time_seconds": run.wall_time_seconds,
                        "conflict_rule_ids": run.conflict_rule_ids,
                        "priority_rule_ids": run.priority_rule_ids,
                        "priority_explanations": run.priority_explanations,
                        "explanation": run.explanation,
                        "error_message": run.error_message,
                    },
                    ensure_ascii=False,
                )
                if payload != last_payload:
                    yield f"event: progress\ndata: {payload}\n\n"
                    last_payload = payload
                if run.status in {"completed", "failed"}:
                    return
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _goal_response(db: Session, goal: SolveGoal, run_count: int | None = None) -> GoalResponse:
    if run_count is None:
        run_count = int(
            db.scalar(select(func.count(SolverRun.id)).where(SolverRun.goal_id == goal.id))
            or 0
        )
    response = GoalResponse.model_validate(goal)
    response.run_count = run_count
    return response


@router.post("/goals", response_model=GoalResponse, status_code=201, tags=["goals"])
def create_goal(
    payload: GoalCreateRequest, db: Db, user: AdminOrScheduler, scope: SchedulerScope
) -> GoalResponse:
    """把一句话目标登记为可逐项验收的持久目标（MEM-C3）。

    checklist 缺省时按结构化范围字段确定性生成；显式传入则原样保存（kind 由
    schema 枚举把关）。基准版本必须属于当前方案，「尽量少改」的验收上限在这里
    一次定清，验收器绝不会把「尽量」升级为「绝不」。底线验收项（MEM-D2/D4c：
    deliverable_exists / no_hard_conflicts / 有明确目标集合时的 coverage）无论
    自定义还是自动生成都强制并入，不可删除。
    """
    if payload.checklist is not None:
        if not payload.checklist:
            raise HTTPException(status_code=422, detail="验收清单不能为空")
        keys = [item.key for item in payload.checklist]
        if len(set(keys)) != len(keys):
            raise HTTPException(status_code=422, detail="验收清单的 key 不能重复")
        checklist = [item.model_dump() for item in payload.checklist]
    else:
        checklist = build_checklist(
            payload.instruction,
            business_lines=payload.business_lines,
            product_types=payload.product_types,
            class_business_ids=payload.class_business_ids,
            course_business_ids=payload.course_business_ids,
            date_from=payload.date_from.isoformat() if payload.date_from else None,
            date_to=payload.date_to.isoformat() if payload.date_to else None,
            forbidden_slots=[item.model_dump() for item in payload.forbidden_slots],
            max_changes=payload.max_changes,
            baseline_schedule_version_id=payload.baseline_schedule_version_id,
            forbid_publish=payload.forbid_publish,
        )
    # 底线验收与自定义清单并列（MEM-D2/D4c）：缺失即补齐，用户清单不可删除底线。
    checklist = ensure_bottom_line_items(
        checklist,
        has_target_set=bool(
            payload.course_business_ids
            or payload.business_lines
            or payload.product_types
            or payload.class_business_ids
        ),
    )
    if payload.baseline_schedule_version_id:
        baseline = db.get(ScheduleVersion, payload.baseline_schedule_version_id)
        if baseline is None or baseline.schedule_set_id != scope.id:
            raise HTTPException(status_code=422, detail="基准版本不存在或不属于当前方案")
    goal = SolveGoal(
        schedule_set_id=scope.id,
        instruction=payload.instruction,
        checklist=checklist,
        status="open",
        created_by=user.id,
    )
    db.add(goal)
    db.flush()
    audit(
        db,
        user,
        "create",
        "solve_goal",
        goal.id,
        {
            "checklist_kinds": sorted(
                {
                    str(item.get("kind"))
                    for item in checklist
                    if str(item.get("kind")) in GOAL_CHECKLIST_KINDS
                }
            ),
            "forbid_publish": payload.forbid_publish,
        },
    )
    db.commit()
    db.refresh(goal)
    return _goal_response(db, goal, 0)


@router.get("/goals", response_model=list[GoalResponse], tags=["goals"])
def list_goals(db: Db, user: CurrentUser, scope: ViewerScope) -> list[GoalResponse]:
    goals = list(
        db.scalars(
            select(SolveGoal)
            .where(SolveGoal.schedule_set_id == scope.id)
            .order_by(SolveGoal.created_at.desc())
            .limit(100)
        )
    )
    counts = goal_run_counts(db, scope.id)
    return [_goal_response(db, goal, counts.get(goal.id, 0)) for goal in goals]


@router.get("/goals/{goal_id}", response_model=GoalDetailResponse, tags=["goals"])
def get_goal(goal_id: str, db: Db, user: CurrentUser, scope: ViewerScope) -> GoalDetailResponse:
    goal = get_scoped_or_404(db, SolveGoal, goal_id, scope)
    runs = list(
        db.scalars(
            select(SolverRun)
            .where(SolverRun.goal_id == goal.id)
            .order_by(SolverRun.created_at.desc())
            .limit(50)
        )
    )
    response = GoalDetailResponse.model_validate(goal)
    response.runs = [SolverRunResponse.model_validate(run) for run in runs]
    latest = next((run for run in runs if run.id == goal.latest_run_id), None)
    response.latest_report = latest.goal_report if latest is not None else None
    response.run_count = len(runs)
    return response


@router.post("/goals/{goal_id}/abandon", response_model=GoalResponse, tags=["goals"])
def abandon_goal(
    goal_id: str, db: Db, user: AdminOrScheduler, scope: SchedulerScope
) -> GoalResponse:
    """人工放弃目标（终态）。放弃是显式的人的决定，验收器不会自动放弃任何目标。"""
    goal = get_scoped_or_404(db, SolveGoal, goal_id, scope)
    if goal.status == "abandoned":
        raise HTTPException(status_code=409, detail="目标已经放弃")
    goal.status = "abandoned"
    audit(db, user, "abandon", "solve_goal", goal.id, {"instruction": goal.instruction[:200]})
    db.commit()
    db.refresh(goal)
    return _goal_response(db, goal)


@router.get("/schedules", response_model=list[ScheduleSummaryResponse], tags=["schedules"])
def list_schedules(db: Db, user: CurrentUser, scope: ViewerScope) -> list[ScheduleSummaryResponse]:
    versions = list(
        db.scalars(
            select(ScheduleVersion)
            .where(ScheduleVersion.schedule_set_id == scope.id)
            .order_by(ScheduleVersion.version_no.desc())
        )
    )
    version_ids = [item.id for item in versions]
    counts: dict[str, int] = (
        {
            str(version_id): int(total)
            for version_id, total in db.execute(
                select(
                    ScheduleAssignment.schedule_version_id,
                    func.count(ScheduleAssignment.id),
                )
                .where(ScheduleAssignment.schedule_version_id.in_(version_ids))
                .group_by(ScheduleAssignment.schedule_version_id)
            ).all()
        }
        if version_ids
        else {}
    )
    return [
        ScheduleSummaryResponse(
            id=item.id,
            version_no=item.version_no,
            name=item.name,
            status=item.status,
            parent_id=item.parent_id,
            solver_run_id=item.solver_run_id,
            metrics=item.metrics,
            assignment_count=counts.get(item.id, 0),
            published_at=item.published_at,
            created_at=item.created_at,
        )
        for item in versions
    ]


@router.get("/schedules/{schedule_id}", response_model=ScheduleResponse, tags=["schedules"])
def get_schedule(
    schedule_id: str, db: Db, user: CurrentUser, scope: ViewerScope
) -> ScheduleResponse:
    return schedule_response(db, get_scoped_or_404(db, ScheduleVersion, schedule_id, scope))


@router.get(
    "/schedules/{schedule_id}/diff/{target_schedule_id}",
    response_model=ScheduleDiffResponse,
    tags=["schedules"],
)
def diff_schedules(
    schedule_id: str,
    target_schedule_id: str,
    db: Db,
    user: CurrentUser,
    scope: ViewerScope,
) -> ScheduleDiffResponse:
    base = schedule_response(db, get_scoped_or_404(db, ScheduleVersion, schedule_id, scope))
    target = schedule_response(
        db, get_scoped_or_404(db, ScheduleVersion, target_schedule_id, scope)
    )
    before = {item.course_business_id: item for item in base.assignments}
    after = {item.course_business_id: item for item in target.assignments}
    items: list[ScheduleDiffItem] = []
    for course_id in sorted(set(before) | set(after)):
        old = before.get(course_id)
        new = after.get(course_id)
        if old is None:
            kind: Literal["added", "removed", "moved", "unchanged"] = "added"
        elif new is None:
            kind = "removed"
        elif (old.lesson_date, old.slot_business_id, old.room_business_id) != (
            new.lesson_date,
            new.slot_business_id,
            new.room_business_id,
        ):
            kind = "moved"
        else:
            kind = "unchanged"
        reference = new or old
        assert reference is not None
        items.append(
            ScheduleDiffItem(
                course_business_id=course_id,
                class_business_id=reference.class_business_id,
                teacher_business_id=reference.teacher_business_id,
                before_lesson_date=old.lesson_date if old else None,
                before_slot_id=old.slot_business_id if old else None,
                before_room_id=old.room_business_id if old else None,
                after_lesson_date=new.lesson_date if new else None,
                after_slot_id=new.slot_business_id if new else None,
                after_room_id=new.room_business_id if new else None,
                change_kind=kind,
            )
        )
    changed = sum(item.change_kind != "unchanged" for item in items)
    return ScheduleDiffResponse(
        base_schedule_id=base.id,
        target_schedule_id=target.id,
        changed_count=changed,
        unchanged_count=len(items) - changed,
        items=items,
    )


@router.post(
    "/schedules/{schedule_id}/publish", response_model=ScheduleResponse, tags=["schedules"]
)
def publish_schedule(
    schedule_id: str, db: Db, user: Approver, scope: ApproverScope
) -> ScheduleResponse:
    schedule = get_scoped_or_404(db, ScheduleVersion, schedule_id, scope)
    if schedule.status != "draft":
        raise HTTPException(status_code=409, detail="只有草稿版本可以发布")
    # 发布门禁：独立于求解器重算一遍硬冲突，求解器建模有误时在这里兜住。
    response = schedule_response(db, schedule)
    conflicts = count_hard_conflicts(
        db, [item.model_dump(mode="json") for item in response.assignments], scope.id,
        course_overrides=version_course_map(db, schedule),
    )
    if conflicts["total"]:
        detail = "、".join(
            f"{label}冲突 {conflicts[key]} 条"
            for key, label in (
                ("room", "教室"),
                ("class", "班级"),
                ("teacher", "教师"),
                ("calendar", "日程账号"),
                ("integrity", "数据完整性"),
            )
            if conflicts[key]
        )
        raise HTTPException(status_code=409, detail=f"课表存在硬冲突，不能发布：{detail}")
    currently_published = list(
        db.scalars(
            select(ScheduleVersion)
            .where(
                ScheduleVersion.schedule_set_id == scope.id,
                ScheduleVersion.status == "published",
            )
            .order_by(ScheduleVersion.published_at.desc(), ScheduleVersion.version_no.desc())
        )
    )
    for published in currently_published:
        published.status = "archived"
    schedule.status = "published"
    schedule.approved_by = user.id
    schedule.published_at = shanghai_now()
    audit(
        db,
        user,
        "publish",
        "schedule",
        schedule.id,
        {
            "replaced_published_schedule_id": (
                currently_published[0].id if currently_published else None
            )
        },
    )
    db.commit()
    db.refresh(schedule)
    trigger_published_data_sync(db, user, scope.id, event="publish")
    return schedule_response(db, schedule)


@router.post(
    "/schedules/{schedule_id}/rollback", response_model=ScheduleResponse, tags=["schedules"]
)
def rollback_schedule(
    schedule_id: str, db: Db, user: Approver, scope: ApproverScope
) -> ScheduleResponse:
    target = get_scoped_or_404(db, ScheduleVersion, schedule_id, scope)
    if target.status not in {"archived", "rolled_back"}:
        raise HTTPException(status_code=409, detail="只能回滚到已经发布过的历史版本")
    currently_published = list(
        db.scalars(
            select(ScheduleVersion)
            .where(
                ScheduleVersion.schedule_set_id == scope.id,
                ScheduleVersion.status == "published",
            )
            .order_by(ScheduleVersion.published_at.desc(), ScheduleVersion.version_no.desc())
        )
    )
    for published in currently_published:
        published.status = "rolled_back"
    target.status = "published"
    target.approved_by = user.id
    target.published_at = shanghai_now()
    audit(
        db,
        user,
        "rollback",
        "schedule",
        target.id,
        {
            "replaced_published_schedule_id": (
                currently_published[0].id if currently_published else None
            )
        },
    )
    db.commit()
    db.refresh(target)
    trigger_published_data_sync(db, user, scope.id, event="rollback")
    return schedule_response(db, target)


def ensure_schedule_deletable(db: Session, schedule: ScheduleVersion) -> None:
    """四条硬拦。全部返回 409，话术要说清楚拦的是什么、下一步该做什么。

    最贵的一条是日历绑定：飞书侧只有建日程没有删日程的能力，
    CalendarEventBinding.idempotency_key 是我们和真实日程之间唯一的映射。删掉绑定行，
    老师日历里就留下一批谁也收不回的日程，而且下次发布还会重复创建。所以无条件拦。
    """
    if schedule.status == "published":
        raise HTTPException(
            status_code=409, detail="当前正在使用的版本不能删除，请先回滚到其他版本"
        )
    if schedule.name.endswith(OFFICIAL_VERSION_SUFFIX):
        raise HTTPException(status_code=409, detail="官方原始课表是导入基线，不能删除")

    children = list(
        db.scalars(
            select(ScheduleVersion.version_no)
            .where(ScheduleVersion.parent_id == schedule.id)
            .order_by(ScheduleVersion.version_no)
        )
    )
    if children:
        labels = "、".join(f"v{item}" for item in children)
        raise HTTPException(
            status_code=409,
            detail=f"该版本是 {labels} 的来源版本，请先删除这些版本",
        )

    parent_events = int(
        db.scalar(
            select(func.count())
            .select_from(RescheduleEvent)
            .where(RescheduleEvent.parent_schedule_id == schedule.id)
        )
        or 0
    )
    if parent_events:
        raise HTTPException(
            status_code=409,
            detail=f"该版本被 {parent_events} 条调课事件引用，删除会断掉调课审计链",
        )

    bindings = int(
        db.scalar(
            select(func.count())
            .select_from(CalendarEventBinding)
            .where(CalendarEventBinding.schedule_version_id == schedule.id)
        )
        or 0
    )
    if bindings:
        raise HTTPException(
            status_code=409,
            detail=(
                f"该版本已下发飞书日历（{bindings} 条日程），删除后无法回收已创建的日程，不允许删除"
            ),
        )


@router.delete("/schedules/{schedule_id}", status_code=204, tags=["schedules"])
def delete_schedule(schedule_id: str, db: Db, user: Approver, scope: ApproverScope) -> Response:
    """删除课表版本。删除是发布/回滚的破坏性孪生操作，权限同为 Approver。

    SolverRun 与 DataSnapshot 一律保留——求解痕迹是审计链，不随版本消失。
    """
    schedule = get_scoped_or_404(db, ScheduleVersion, schedule_id, scope)
    ensure_schedule_deletable(db, schedule)

    assignment_count = int(
        db.scalar(
            select(func.count())
            .select_from(ScheduleAssignment)
            .where(ScheduleAssignment.schedule_version_id == schedule.id)
        )
        or 0
    )
    # 被当作候选的版本正是最该能删的东西（调课跑出来的废候选）。这一列可空，
    # 同事务里置空并把事件状态改掉——留在 candidate_ready 会让调课页显示
    # 「候选已生成」却点不开。
    discarded_events = list(
        db.scalars(
            select(RescheduleEvent).where(
                RescheduleEvent.schedule_set_id == scope.id,
                RescheduleEvent.candidate_schedule_id == schedule.id,
            )
        )
    )
    for event in discarded_events:
        event.candidate_schedule_id = None
        event.status = "candidate_discarded"

    # 上万行 assignment 走 ORM 级联是逐行 DELETE，这里直接批量删。
    db.execute(
        delete(ScheduleAssignment).where(ScheduleAssignment.schedule_version_id == schedule.id)
    )
    # 行删掉之后 resource_id 那个 UUID 什么都查不回来，detail 是唯一幸存的记录。
    audit(
        db,
        user,
        "delete",
        "schedule",
        schedule.id,
        {
            "version_no": schedule.version_no,
            "name": schedule.name,
            "status": schedule.status,
            "assignment_count": assignment_count,
            "solver_run_id": schedule.solver_run_id,
            "discarded_candidate_events": [item.id for item in discarded_events],
        },
    )
    db.delete(schedule)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/schedules/{schedule_id}/export.xlsx", tags=["schedules"])
def export_schedule(schedule_id: str, db: Db, user: CurrentUser, scope: ViewerScope) -> Response:
    schedule = get_scoped_or_404(db, ScheduleVersion, schedule_id, scope)
    content = export_schedule_xlsx(db, schedule)
    filename = f"tupai-schedule-v{schedule.version_no}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/schedules/{schedule_id}/calendar-bindings",
    response_model=list[CalendarEventBindingResponse],
    tags=["schedules", "integrations"],
)
def list_calendar_bindings(
    schedule_id: str, db: Db, user: CurrentUser, scope: ViewerScope
) -> list[CalendarEventBinding]:
    get_scoped_or_404(db, ScheduleVersion, schedule_id, scope)
    return list(
        db.scalars(
            select(CalendarEventBinding)
            .where(CalendarEventBinding.schedule_version_id == schedule_id)
            .order_by(CalendarEventBinding.created_at, CalendarEventBinding.course_session_id)
        )
    )


@router.post(
    "/schedules/{schedule_id}/calendar-publish",
    response_model=CalendarPublishResponse,
    tags=["schedules", "integrations"],
)
def publish_schedule_to_calendar(
    schedule_id: str,
    request: CalendarPublishRequest,
    db: Db,
    user: AdminOrScheduler,
    scope: SchedulerScope,
) -> CalendarPublishResponse:
    schedule = get_scoped_or_404(db, ScheduleVersion, schedule_id, scope)
    assignments = list(
        db.scalars(
            select(ScheduleAssignment)
            .where(ScheduleAssignment.schedule_version_id == schedule.id)
            .order_by(ScheduleAssignment.lesson_date, ScheduleAssignment.course_session_id)
        )
    )
    courses = {
        item.id: item
        for item in db.scalars(
            select(CourseSession).where(
                CourseSession.schedule_set_id == scope.id,
                CourseSession.id.in_([item.course_session_id for item in assignments]),
            )
        )
    }
    teacher_ids = {item.teacher_business_id for item in courses.values()}
    teachers = {
        item.business_id: item
        for item in db.scalars(
            select(Teacher).where(
                Teacher.schedule_set_id == scope.id, Teacher.business_id.in_(teacher_ids)
            )
        )
    }
    rooms = {
        item.business_id: item
        for item in db.scalars(select(Room).where(Room.schedule_set_id == scope.id))
    }
    slots = {
        item.business_id: item
        for item in db.scalars(select(TimeSlot).where(TimeSlot.schedule_set_id == scope.id))
    }
    existing_bindings = {
        item.course_session_id: item
        for item in db.scalars(
            select(CalendarEventBinding).where(
                CalendarEventBinding.schedule_version_id == schedule.id
            )
        )
    }
    publishable: list[tuple[ScheduleAssignment, CourseSession, str, datetime, datetime]] = []
    skipped_unmapped = 0
    for assignment in assignments:
        binding = existing_bindings.get(assignment.course_session_id)
        if binding and binding.status in {"published", "published_with_conflict"}:
            continue
        course = courses.get(assignment.course_session_id)
        if course is None or assignment.lesson_date is None:
            skipped_unmapped += 1
            continue
        teacher = teachers.get(course.teacher_business_id)
        calendar_user_id = (course.calendar_user_id or "").strip() or (
            (teacher.calendar_user_id or "").strip() if teacher else ""
        )
        slot = slots.get(assignment.slot_business_id)
        start_time = str((slot.start_time if slot else None) or course.fixed_start_time or "")
        end_time = str((slot.end_time if slot else None) or course.fixed_end_time or "")
        if not calendar_user_id or not start_time or not end_time:
            skipped_unmapped += 1
            continue
        try:
            start = datetime.combine(assignment.lesson_date, _parse_clock(start_time), SHANGHAI_TZ)
            end = datetime.combine(assignment.lesson_date, _parse_clock(end_time), SHANGHAI_TZ)
        except ValueError:
            skipped_unmapped += 1
            continue
        if end <= start:
            skipped_unmapped += 1
            continue
        publishable.append((assignment, course, calendar_user_id, start, end))

    service = FeishuService(settings, db)
    busy_by_user: dict[str, list[tuple[datetime, datetime]]] = {}
    if publishable:
        # 集成抽象层接缝（app/integrations/README.md）：日历能力经 registry 运行时
        # 协商。无可用的日历集成（当前即飞书未配置应用）时保持原引导文案，状态码
        # 按运行约定归入 409「配置未就绪」；无可下发课次时不过门控，与原实现一致。
        if not integration_registry.has_capability(
            Capability.CALENDAR, settings=settings, db=db
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=FEISHU_UNCONFIGURED_DETAIL,
            )
        min_date = min(item[3].date() for item in publishable)
        max_date = max(item[4].date() for item in publishable)
        for chunk_start in (
            min_date + timedelta(days=offset)
            for offset in range(0, (max_date - min_date).days + 1, 14)
        ):
            chunk_end = min(chunk_start + timedelta(days=14), max_date + timedelta(days=1))
            relevant_users = sorted(
                {
                    item[2]
                    for item in publishable
                    if item[3].date() < chunk_end and item[4].date() >= chunk_start
                }
            )
            for user_offset in range(0, len(relevant_users), 10):
                user_chunk = relevant_users[user_offset : user_offset + 10]
                payload = service.batch_freebusy(
                    user.id,
                    user_ids=user_chunk,
                    time_min=datetime.combine(chunk_start, time.min, SHANGHAI_TZ).isoformat(),
                    time_max=datetime.combine(chunk_end, time.min, SHANGHAI_TZ).isoformat(),
                )
                response_lists = payload.get("freebusy_lists") or payload.get("freebusy_list")
                if isinstance(response_lists, list):
                    for index, item in enumerate(response_lists):
                        if not isinstance(item, dict):
                            continue
                        target_user = str(
                            item.get("user_id")
                            or item.get("open_id")
                            or (user_chunk[index] if index < len(user_chunk) else "")
                        )
                        if target_user:
                            busy_by_user.setdefault(target_user, []).extend(_busy_intervals(item))
                else:
                    intervals = _busy_intervals(payload)
                    for target_user in user_chunk:
                        busy_by_user.setdefault(target_user, []).extend(intervals)

    conflicts: list[CalendarConflict] = []
    conflicted_course_ids: set[str] = set()
    for assignment, course, calendar_user_id, start, end in publishable:
        if any(
            _intervals_overlap(start, end, interval)
            for interval in busy_by_user.get(calendar_user_id, [])
        ):
            conflicted_course_ids.add(course.id)
            conflicts.append(
                CalendarConflict(
                    course_session_id=course.id,
                    calendar_user_id=calendar_user_id,
                    lesson_date=assignment.lesson_date,
                    start_time=start.strftime("%H:%M"),
                    end_time=end.strftime("%H:%M"),
                    source="feishu_freebusy",
                )
            )
    by_calendar_user: dict[
        str, list[tuple[ScheduleAssignment, CourseSession, datetime, datetime]]
    ] = {}
    for assignment, course, calendar_user_id, start, end in publishable:
        by_calendar_user.setdefault(calendar_user_id, []).append((assignment, course, start, end))
    for calendar_user_id, items in by_calendar_user.items():
        ordered = sorted(items, key=lambda item: (item[2], item[3], item[1].id))
        for index, current in enumerate(ordered):
            for other in ordered[index + 1 :]:
                if not _intervals_overlap(current[2], current[3], (other[2], other[3])):
                    continue
                for assignment, course, _, _ in (current, other):
                    if course.id in conflicted_course_ids:
                        continue
                    conflicted_course_ids.add(course.id)
                    conflicts.append(
                        CalendarConflict(
                            course_session_id=course.id,
                            calendar_user_id=calendar_user_id,
                            lesson_date=assignment.lesson_date,
                            start_time=start.strftime("%H:%M"),
                            end_time=end.strftime("%H:%M"),
                            source="schedule_overlap",
                        )
                    )

    published = 0
    if not request.dry_run:
        for assignment, course, calendar_user_id, _start, _end in publishable:
            idempotency_key = f"tupai:{schedule.id}:{course.id}"
            room = rooms.get(assignment.room_business_id)
            assert assignment.lesson_date is not None
            binding = existing_bindings.get(course.id)
            if binding is None:
                response = service.create_calendar_event(
                    user.id,
                    calendar_id=request.calendar_id,
                    idempotency_key=idempotency_key,
                    event={
                        "summary": course.lesson_name or "课程安排",
                        "description": (
                            f"途排智策课表 V{schedule.version_no}；"
                            f"班级 {course.class_business_id}；场次 {course.business_id}"
                        ),
                        "start_time": {
                            "timestamp": _timestamp_epoch(
                                assignment.lesson_date, _start.strftime("%H:%M")
                            ),
                            "timezone": "Asia/Shanghai",
                        },
                        "end_time": {
                            "timestamp": _timestamp_epoch(
                                assignment.lesson_date, _end.strftime("%H:%M")
                            ),
                            "timezone": "Asia/Shanghai",
                        },
                        "visibility": "private",
                        "free_busy_status": "busy",
                        "location": {"name": room.name if room else assignment.room_business_id},
                    },
                )
                binding = CalendarEventBinding(
                    schedule_version_id=schedule.id,
                    course_session_id=course.id,
                    calendar_id=request.calendar_id,
                    event_id=_event_id(response),
                    calendar_user_id=calendar_user_id,
                    idempotency_key=idempotency_key,
                    status="event_created",
                )
                db.add(binding)
                existing_bindings[course.id] = binding
                # The external event already exists at this point. Persist its
                # ID before adding the attendee so retries do not duplicate it.
                db.commit()
            try:
                service.add_event_attendee(
                    user.id,
                    calendar_id=binding.calendar_id,
                    event_id=binding.event_id,
                    attendee_user_id=calendar_user_id,
                    need_notification=request.need_notification,
                )
            except Exception:
                binding.status = "attendee_failed"
                db.commit()
                raise
            binding.status = (
                "published_with_conflict" if course.id in conflicted_course_ids else "published"
            )
            db.commit()
            published += 1
        audit(
            db,
            user,
            "calendar_publish",
            "schedule",
            schedule.id,
            {"published": published, "conflicts": len(conflicts)},
        )
        db.commit()

    return CalendarPublishResponse(
        schedule_id=schedule.id,
        dry_run=request.dry_run,
        would_publish=len(publishable),
        published=published,
        existing=sum(
            item.status in {"published", "published_with_conflict"}
            for item in existing_bindings.values()
        ),
        skipped_unmapped=skipped_unmapped,
        conflict_count=len(conflicts),
        conflicts=conflicts,
    )


def reschedule_neighborhood(
    db: Session, request: RescheduleCreate, parent: ScheduleResponse, schedule_set_id: str
) -> set[str]:
    """把调课求解收敛到受影响的局部邻域。

    文档要求「冻结邻域外变量、只在局部求解」。种子是被事件直接命中的课次；
    邻域再纳入同一时间窗口内共用班级或教室的课次——它们是腾挪空间的来源。
    邻域外的课次不参与决策，但会作为固定占用进入模型，不会被别的课占掉。
    返回空集表示不限定范围（求解器按全量处理）。
    """
    teachers = {
        item.business_id: item
        for item in db.scalars(select(Teacher).where(Teacher.schedule_set_id == schedule_set_id))
    }
    courses = {
        item.id: item
        for item in db.scalars(
            select(CourseSession).where(CourseSession.schedule_set_id == schedule_set_id)
        )
    }

    def targets(assignment: AssignmentResponse) -> bool:
        course = courses.get(assignment.course_session_id)
        if course is None:
            return False
        if request.course_business_id and course.business_id == request.course_business_id:
            return True
        if request.event_type == "room_outage":
            return bool(
                request.room_business_id and assignment.room_business_id == request.room_business_id
            )
        if request.event_type == "teacher_leave":
            teacher = teachers.get(assignment.teacher_business_id)
            calendar_user_id = course.calendar_user_id or (
                teacher.calendar_user_id if teacher else None
            )
            return bool(
                (
                    request.teacher_business_id
                    and assignment.teacher_business_id == request.teacher_business_id
                )
                or (
                    request.teacher_business_id
                    and calendar_user_id
                    and calendar_user_id == request.teacher_business_id
                )
            )
        return False

    seeds = [item for item in parent.assignments if targets(item)]
    if not seeds:
        return set()
    seed_dates = [item.lesson_date for item in seeds if item.lesson_date]
    if not seed_dates:
        return {
            courses[item.course_session_id].business_id
            for item in seeds
            if item.course_session_id in courses
        }
    window = timedelta(days=request.neighborhood_days)
    lower, upper = min(seed_dates) - window, max(seed_dates) + window
    seed_classes = {item.class_business_id for item in seeds}
    seed_rooms = {item.room_business_id for item in seeds}
    neighborhood: set[str] = set()
    for item in parent.assignments:
        course = courses.get(item.course_session_id)
        if course is None:
            continue
        if item.lesson_date and not (lower <= item.lesson_date <= upper):
            continue
        if (
            item in seeds
            or item.class_business_id in seed_classes
            or item.room_business_id in seed_rooms
        ):
            neighborhood.add(course.business_id)
    return neighborhood


@router.get("/reschedule-events", response_model=list[RescheduleResponse], tags=["reschedule"])
def list_reschedule_events(db: Db, user: CurrentUser, scope: ViewerScope) -> list[RescheduleEvent]:
    return list(
        db.scalars(
            select(RescheduleEvent)
            .where(RescheduleEvent.schedule_set_id == scope.id)
            .order_by(RescheduleEvent.created_at.desc())
        )
    )


@router.post(
    "/reschedule-events", response_model=RescheduleResponse, status_code=202, tags=["reschedule"]
)
def create_reschedule_event(
    request: RescheduleCreate, db: Db, user: AdminOrScheduler, scope: SchedulerScope
) -> RescheduleEvent:
    parent = get_scoped_or_404(db, ScheduleVersion, request.parent_schedule_id, scope)
    if request.event_type == "teacher_leave" and not (
        request.slot_business_ids or request.date_from
    ):
        # 不限定时段也不限定日期的请假等于「该教师全程不可用」，只会产出必然无解的任务。
        raise HTTPException(status_code=422, detail="教师请假必须至少指定时段或日期范围")
    if request.date_from and request.date_to and request.date_from > request.date_to:
        raise HTTPException(status_code=422, detail="事件的开始日期不能晚于结束日期")
    parent_response = schedule_response(db, parent)
    event_payload = request.model_dump(
        mode="json",
        exclude={"parent_schedule_id", "description", "time_limit_seconds"}
    )
    event = RescheduleEvent(
        schedule_set_id=scope.id,
        event_type=request.event_type,
        description=request.description,
        declared_reason=request.declared_reason,
        payload=event_payload,
        status="pending",
        parent_schedule_id=parent.id,
        created_by=user.id,
    )
    db.add(event)
    db.flush()
    neighborhood = reschedule_neighborhood(db, request, parent_response, scope.id)
    run = create_solver_run(
        db,
        user.id,
        AilySolveRequest(time_limit_seconds=request.time_limit_seconds),
        scope.id,
        run_type="reschedule",
        extra={
            "parent_schedule_id": parent.id,
            "event": event_payload,
            "previous_assignments": [
                item.model_dump(mode="json") for item in parent_response.assignments
            ],
            "change_weight": 100000,
            "course_business_ids": sorted(neighborhood),
        },
    )
    event.solver_run_id = run.id
    audit(db, user, "create", "reschedule_event", event.id, event_payload)
    db.commit()
    db.refresh(event)
    enqueue_solver_run(run.id)
    return event


@router.get("/audit-logs", response_model=list[AuditLogResponse], tags=["audit"])
def list_audit_logs(
    db: Db, user: Admin, limit: int = Query(100, ge=1, le=500)
) -> list[AuditLogResponse]:
    rows = list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)))
    return [
        AuditLogResponse(
            id=row.id,
            actor_id=row.actor_id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            detail=row.detail,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get(
    "/integrations",
    response_model=list[IntegrationManifestResponse],
    tags=["integrations"],
)
def list_integrations(db: Db, user: AdminOrScheduler) -> list[IntegrationManifestResponse]:
    """集成清单：manifest 元数据 + 运行时状态，供「设置 → 集成」卡片渲染。

    清单本身不触发网络探测；「测试连接」经 POST /integrations/{id}/verify
    显式发起。status 规则：仅声明 manifest 的 planned 集成为 planned；适配器
    集成运行时能提供任一能力即 configured，否则回落到 manifest 的 status_class
    （如飞书未配置应用时为 available）。
    """
    runtime_capabilities = {
        item.manifest.id: item.capabilities()
        for item in integration_registry.get_integrations(settings=settings, db=db)
    }
    items: list[IntegrationManifestResponse] = []
    for manifest in integration_registry.manifests():
        runtime_status: str = manifest.status_class
        if runtime_capabilities.get(manifest.id):
            runtime_status = "configured"
        items.append(
            IntegrationManifestResponse(
                id=manifest.id,
                name=manifest.name,
                description=manifest.description,
                capabilities=sorted(capability.value for capability in manifest.capabilities),
                status=runtime_status,
                docs_url=manifest.docs_url,
            )
        )
    return items


@router.post(
    "/integrations/{integration_id}/verify",
    response_model=IntegrationVerifyResponse,
    tags=["integrations"],
)
async def verify_integration(
    integration_id: str, db: Db, user: AdminOrScheduler
) -> dict[str, Any]:
    """统一「测试连接」（≈ Airbyte Check / Grafana testDatasource）。

    registry 在请求现场实例化适配器后执行其轻量探测：local 恒 ok；飞书只读
    本地配置状态；钉钉/企业微信用一次 access_token 请求探测（带缓存）。适配器
    契约（integrations/base.py）：verify() 不抛异常，失败以 VerifyResult 表达；
    planned 集成（无适配器实例）与未知 id 同形 404。
    """

    integration = integration_registry.get_integration(integration_id, settings=settings, db=db)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="该集成不存在或尚未提供适配器",
        )
    result = await integration.verify()
    return {"ok": result.ok, "detail": result.detail}


@router.get(
    "/integrations/ai/configuration",
    response_model=AIProviderConfigurationResponse,
    tags=["integrations", "ai"],
)
def ai_configuration(db: Db, user: CurrentUser) -> dict[str, Any]:
    return AIService(settings, db).configuration_view()


@router.post(
    "/integrations/ai/configuration",
    response_model=AIProviderConfigurationResponse,
    tags=["integrations", "ai"],
)
def configure_ai_provider(
    request: AIProviderConfigurationInput, db: Db, user: Admin
) -> dict[str, Any]:
    service = AIService(settings, db)
    try:
        service.save_configuration(
            user.id,
            provider=request.provider,
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
        )
    except AIServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit(
        db,
        user,
        "configure",
        "ai_provider",
        None,
        {
            "provider": request.provider,
            "base_url": request.base_url,
            "model": request.model,
        },
    )
    db.commit()
    return service.configuration_view()


def _configurable_integration_or_404(integration_id: str, db: Session) -> Integration:
    """凭据配置端点只服务声明了 config schema 的适配器（钉钉/企业微信）。

    未知 id 与 planned 集成（无适配器实例）统一 404；本地模式、飞书等未声明
    config schema 的集成沿用各自专用通道，不经本端点。
    """

    integration = integration_registry.get_integration(integration_id, settings=settings, db=db)
    if integration is None or integration.manifest.config_schema is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="该集成不存在或暂不支持凭据配置",
        )
    return integration


def _integration_configuration_response(
    integration_id: str, db: Session
) -> IntegrationConfigurationResponse:
    store = CredentialStore(settings, db)
    try:
        masked = store.view(integration_id)
    except IntegrationCredentialError as exc:
        # 主密钥轮换/损坏时保持「测试连接」风格的软失败，指引重新配置。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    integration = integration_registry.get_integration(integration_id, settings=settings, db=db)
    configured = bool(integration.capabilities()) if integration is not None else False
    return IntegrationConfigurationResponse(
        integration_id=integration_id,
        configured=configured,
        config=masked["config"],
        secrets_configured=masked["secrets_configured"],
        updated_at=masked["updated_at"],
    )


@router.get(
    "/integrations/{integration_id}/configuration",
    response_model=IntegrationConfigurationResponse,
    tags=["integrations"],
)
def get_integration_configuration(
    integration_id: str, db: Db, user: AdminOrScheduler
) -> IntegrationConfigurationResponse:
    """集成凭据配置回读（脱敏）：密钥字段只给「是否已配置」布尔。"""

    _configurable_integration_or_404(integration_id, db)
    return _integration_configuration_response(integration_id, db)


@router.put(
    "/integrations/{integration_id}/configuration",
    response_model=IntegrationConfigurationResponse,
    tags=["integrations"],
)
def put_integration_configuration(
    integration_id: str, request: IntegrationConfigurationInput, db: Db, user: Admin
) -> IntegrationConfigurationResponse:
    """按 manifest config schema 直填并加密保存集成凭据（v1 无 OAuth 安装流）。

    保存语义为合并（见 IntegrationConfigurationInput）；保存后清空对应平台
    的 access_token 内存缓存，避免旧凭据的令牌继续生效。
    """

    _configurable_integration_or_404(integration_id, db)
    CredentialStore(settings, db).save(integration_id, request.config, configured_by=user.id)
    clear_token_cache()
    audit(
        db,
        user,
        "configure",
        "integration_credential",
        integration_id,
        {"fields": sorted(request.config)},
    )
    db.commit()
    return _integration_configuration_response(integration_id, db)


@router.get(
    "/integrations/feishu/connection",
    response_model=FeishuConnectionResponse,
    tags=["integrations"],
)
def feishu_connection(db: Db, user: CurrentUser, scope: ViewerScope) -> dict[str, Any]:
    return FeishuService(settings, db).connection_view(user.id, scope.id)


@router.post(
    "/integrations/feishu/app-configuration",
    response_model=FeishuAppConfigurationResponse,
    tags=["integrations"],
)
def configure_feishu_app(
    request: FeishuAppConfigurationInput, db: Db, user: Admin
) -> dict[str, Any]:
    service = FeishuService(settings, db)
    try:
        service.save_app_configuration(
            user.id,
            request.app_id,
            request.app_secret,
            request.oauth_redirect_uri,
            request.frontend_url,
            request.aily_app_id,
            request.aily_skill_id,
        )
    except FeishuServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit(
        db,
        user,
        "configure",
        "feishu_app",
        None,
        {"app_id": request.app_id, "oauth_redirect_uri": request.oauth_redirect_uri},
    )
    db.commit()
    return service.configuration_view()


@router.post(
    "/integrations/feishu/oauth/start",
    response_model=FeishuOAuthStartResponse,
    tags=["integrations"],
)
def start_feishu_oauth(db: Db, user: Admin) -> dict[str, Any]:
    service = FeishuService(settings, db)
    try:
        result = service.create_oauth_start(user.id)
    except FeishuServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit(db, user, "start_oauth", "feishu", None)
    db.commit()
    return result


@router.get("/integrations/feishu/oauth/callback", tags=["integrations"])
def complete_feishu_oauth(
    db: Db,
    state: str = Query(min_length=16),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    service = FeishuService(settings, db)
    frontend = service.frontend_url()
    if error or not code:
        query = urlencode({"feishu": "cancelled"})
        return RedirectResponse(f"{frontend}/integrations?{query}")
    try:
        connection = service.complete_oauth(code, state)
    except (FeishuServiceError, httpx.HTTPError) as exc:
        logger.exception("飞书 OAuth 回调失败：%s", exc)
        query = urlencode({"feishu": "error"})
        return RedirectResponse(f"{frontend}/integrations?{query}")
    actor = db.get(User, connection.user_id)
    audit(db, actor, "connect", "feishu", connection.id)
    db.commit()
    query = urlencode(
        {"feishu": "connected" if connection.status == "active" else "reauthorization_required"}
    )
    return RedirectResponse(f"{frontend}/integrations?{query}")


@router.post(
    "/integrations/feishu/workspaces",
    response_model=FeishuWorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["integrations"],
)
def create_feishu_workspace(
    request: FeishuWorkspaceCreate, db: Db, user: Admin, scope: ViewerScope
) -> dict[str, Any]:
    service = FeishuService(settings, db)
    requested_name = request.name.strip()
    workspace_name = (
        requested_name if scope.name in requested_name else f"{scope.name}｜{requested_name}"
    )
    try:
        workspace = service.create_workspace(user.id, workspace_name, scope.id)
    except FeishuServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit(
        db,
        user,
        "create",
        "feishu_workspace",
        workspace.id,
        {"name": workspace.name, "schedule_set_id": scope.id},
    )
    db.commit()
    return service.workspace_view(workspace)


@router.delete(
    "/integrations/feishu/connection",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["integrations"],
)
def disconnect_feishu(db: Db, user: Admin) -> Response:
    service = FeishuService(settings, db)
    service.disconnect(user.id)
    audit(db, user, "disconnect", "feishu", None)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def export_resource_rows(
    db: Session, resource: str, schedule_set_id: str = DEFAULT_SCHEDULE_SET_ID
) -> list[dict[str, Any]]:
    if resource == "teachers":
        return [
            {
                "业务标识": item.business_id,
                "教师名称": item.name,
                "学科": item.subject,
                "飞书用户标识": item.calendar_user_id or "",
            }
            for item in db.scalars(
                select(Teacher)
                .where(Teacher.schedule_set_id == schedule_set_id)
                .order_by(Teacher.business_id)
            )
        ]
    if resource == "class_groups":
        # 飞书「班级」表的班型/业务线/教师标识都是单值文本字段，多值只能拼串。
        # 字段名保持不变，避免已同步过的表被迫重建。
        classes = list(
            db.scalars(
                select(ClassGroup)
                .where(ClassGroup.schedule_set_id == schedule_set_id)
                .order_by(ClassGroup.business_id)
            )
        )
        return [
            {
                "业务标识": item.business_id,
                "班级名称": item.name,
                "班型": " / ".join(item.product_types),
                "业务线": " / ".join(item.business_lines),
                "教师标识": " / ".join(item.teacher_business_ids),
            }
            for item in class_group_responses(db, classes, schedule_set_id)
        ]
    if resource == "rooms":
        return [
            {
                "业务标识": item.business_id,
                "教室名称": item.name,
                "是否启用": "是" if item.is_active else "否",
            }
            for item in db.scalars(
                select(Room)
                .where(Room.schedule_set_id == schedule_set_id)
                .order_by(Room.business_id)
            )
        ]
    if resource == "time_slots":
        return [
            {
                "业务标识": item.business_id,
                "星期": item.weekday,
                "开始时间": item.start_time,
                "结束时间": item.end_time,
                "类型": item.kind,
                "顺序": item.sequence,
                "是否开放": "是" if item.is_open else "否",
            }
            for item in db.scalars(
                select(TimeSlot)
                .where(TimeSlot.schedule_set_id == schedule_set_id)
                .order_by(TimeSlot.sequence)
            )
        ]
    if resource == "course_sessions":
        teachers = {
            item.business_id: item
            for item in db.scalars(
                select(Teacher).where(Teacher.schedule_set_id == schedule_set_id)
            )
        }
        return [
            {
                "业务标识": item.business_id,
                "业务线": item.business_line,
                "产品班型": " / ".join(_course_product_values(item)),
                "班级标识": item.class_business_id,
                "教师标识": " / ".join(item.teacher_business_ids or [item.teacher_business_id]),
                "具体日程账号": item.calendar_user_id
                or (
                    teachers[item.teacher_business_id].calendar_user_id
                    if item.teacher_business_id in teachers
                    else ""
                )
                or "",
                "学科": item.subject,
                "课节名称": " / ".join(item.lesson_names or [item.lesson_name]),
                "编排来源": item.schedule_source,
                "编排阶段": " / ".join(item.stages or [item.stage]),
                "计划课次": item.planned_sessions,
                "计划课时": item.planned_hours,
                "课次序号": item.session_no,
                "上课日期": item.lesson_date.isoformat() if item.lesson_date else "",
                "时长分钟": item.duration_minutes,
                "建议时段": " / ".join(
                    item.candidate_slot_ids
                    or ([item.suggested_slot_id] if item.suggested_slot_id else [])
                ),
                "固定开始时间": item.fixed_start_time,
                "固定结束时间": item.fixed_end_time,
                "原始教室标识": item.original_room_business_id or "",
                "是否锁定": "是" if item.is_locked else "否",
            }
            for item in db.scalars(
                select(CourseSession)
                .where(CourseSession.schedule_set_id == schedule_set_id)
                .order_by(CourseSession.business_id)
            )
        ]
    if resource == "rules":
        hardness_labels = {"hard": "硬约束", "soft": "软约束"}
        status_labels = {
            "draft": "草稿",
            "awaiting_confirmation": "待确认",
            "active": "已生效",
            "rejected": "已拒绝",
            "retired": "已停用",
        }
        return [
            {
                "业务标识": item.business_id,
                "规则原文": item.source_text,
                "作用对象类型": item.actor_type,
                "作用对象标识": "|".join(item.actor_ids),
                "约束类型": item.constraint_type,
                "约束范围": json_text(item.scope),
                "硬软类型": hardness_labels.get(item.hardness, item.hardness),
                "权重": item.weight,
                "状态": status_labels.get(item.status, item.status),
                "版本": item.version,
                "来源文档": item.source_doc or "",
            }
            for item in db.scalars(
                select(Rule)
                .where(Rule.schedule_set_id == schedule_set_id)
                .order_by(Rule.business_id)
            )
        ]
    if resource == "schedule":
        # The Feishu "课表" table is the current operational projection.  The
        # local database remains the source of truth for version history; it
        # must not be appended to the same public sync table on every publish.
        current_version = _current_published_schedule(db, schedule_set_id)
        versions = [current_version] if current_version is not None else []
        sessions = version_course_map(db, current_version) if current_version else {}
        teachers = {
            item.business_id: item
            for item in db.scalars(
                select(Teacher).where(Teacher.schedule_set_id == schedule_set_id)
            )
        }
        slots = {
            item.business_id: item
            for item in db.scalars(
                select(TimeSlot).where(TimeSlot.schedule_set_id == schedule_set_id)
            )
        }
        rooms = {
            item.business_id: item
            for item in db.scalars(
                select(Room).where(Room.schedule_set_id == schedule_set_id)
            )
        }
        class_map = {
            (item.campus_id, item.business_id): item
            for item in db.scalars(
                select(ClassGroup).where(ClassGroup.schedule_set_id == schedule_set_id)
            )
        }
        status_labels = {
            "published": "当前发布",
            "archived": "历史发布",
            "rolled_back": "已回滚",
        }
        change_labels = {
            "assigned": "初次分配",
            "moved": "已调整",
            "unchanged": "未变化",
        }
        rows: list[dict[str, Any]] = []
        for version in versions:
            for assignment in version.assignments:
                session = sessions.get(assignment.course_session_id)
                if session is None:
                    continue
                slot = slots.get(assignment.slot_business_id)
                room = rooms.get(assignment.room_business_id)
                rows.append(
                    {
                        # The Feishu row represents the current operational
                        # assignment, not a history entry.  Keep this key
                        # stable across V1 -> V2 so the next sync updates the
                        # existing row instead of creating another 4,000 rows.
                        "业务标识": _public_projection_key(
                            schedule_set_id, "schedule", session.business_id
                        ),
                        "版本标识": version.id,
                        "版本号": version.version_no,
                        "版本名称": version.name,
                        "是否当前版本": "是" if version.status == "published" else "否",
                        "发布状态": status_labels.get(version.status, version.status),
                        "场次标识": session.business_id,
                        "业务线": session.business_line,
                        "产品班型": " / ".join(_course_product_values(session)),
                        # Keep the public class identity unique when two
                        # campuses reuse the same business class code.
                        "班级标识": _public_class_identity(session),
                        "班级名称": (
                            class_map[(session.campus_id, session.class_business_id)].name
                            if (session.campus_id, session.class_business_id) in class_map
                            else "未分班"
                        ),
                        "教师标识": session.teacher_business_id,
                        "具体日程账号": session.calendar_user_id
                        or (
                            teachers[session.teacher_business_id].calendar_user_id
                            if session.teacher_business_id in teachers
                            else ""
                        )
                        or "",
                        "学科": session.subject,
                        "课程名称": " / ".join(session.lesson_names or [session.lesson_name]),
                        "上课日期": assignment.lesson_date.isoformat()
                        if assignment.lesson_date
                        else "",
                        "排序键": " ".join(
                            item
                            for item in (
                                assignment.lesson_date.isoformat()
                                if assignment.lesson_date
                                else "",
                                slot.start_time if slot else session.fixed_start_time,
                                slot.end_time if slot else session.fixed_end_time,
                            )
                            if item
                        ),
                        "时段标识": assignment.slot_business_id,
                        "星期": slot.weekday if slot else "",
                        "开始时间": slot.start_time if slot else session.fixed_start_time,
                        "结束时间": slot.end_time if slot else session.fixed_end_time,
                        "固定开始时间": session.fixed_start_time,
                        "固定结束时间": session.fixed_end_time,
                        "原始教室标识": session.original_room_business_id or "",
                        "教室标识": assignment.room_business_id,
                        "教室名称": room.name if room else "",
                        "变更类型": change_labels.get(
                            assignment.change_kind, assignment.change_kind
                        ),
                    }
                )
        return sorted(rows, key=_public_schedule_sort_key)
    if resource == "public_summary":
        summary = _public_summary(db, schedule_set_id)
        published = _current_published_schedule(db, schedule_set_id)
        updated_at = _public_projection_updated_at(published)
        assignments = _assignment_rows(db, published.id if published else None)
        courses = version_course_map(db, published) if published else {}
        assignment_dates = sorted(
            item.lesson_date for item in assignments if item.lesson_date is not None
        )
        covered_classes = {
            (course.campus_id, course.class_business_id)
            for item in assignments
            if (course := courses.get(item.course_session_id)) is not None
            and course.class_business_id
        }
        coverage = (
            assignment_dates[0].isoformat()
            if len(assignment_dates) == 1
            else (
                f"{assignment_dates[0].isoformat()} 至 {assignment_dates[-1].isoformat()}"
                if assignment_dates
                else ""
            )
        )
        public_rows: list[dict[str, Any]] = [
            {
                "业务标识": "published_version",
                "指标名称": "当前发布版本",
                "指标值": f"V{published.version_no}" if published else "未发布",
                "月份": "",
                "产品线匿名标签": "",
                "更新时间": updated_at,
            },
            {
                "业务标识": "published_at",
                "指标名称": "发布时间",
                "指标值": _public_projection_updated_at(published),
                "月份": "",
                "产品线匿名标签": "",
                "更新时间": updated_at,
            },
            {
                "业务标识": "coverage_dates",
                "指标名称": "排课覆盖日期",
                "指标值": coverage,
                "月份": "",
                "产品线匿名标签": "",
                "更新时间": updated_at,
            },
            {
                "业务标识": "covered_classes",
                "指标名称": "覆盖班级数",
                "指标值": str(len(covered_classes)),
                "月份": "",
                "产品线匿名标签": "",
                "更新时间": updated_at,
            },
            {
                "业务标识": "adjusted_sessions",
                "指标名称": "调整课次",
                "指标值": str(_published_adjustment_count(db, schedule_set_id)),
                "月份": "",
                "产品线匿名标签": "",
                "更新时间": updated_at,
            },
            {
                "业务标识": "total_sessions",
                "指标名称": "总课次",
                "指标值": str(summary.total_sessions),
                "月份": "",
                "产品线匿名标签": "",
                "更新时间": updated_at,
            },
            {
                "业务标识": "room_utilization",
                "指标名称": "教室利用率",
                "指标值": f"{summary.room_utilization:.4f}",
                "月份": "",
                "产品线匿名标签": "",
                "更新时间": updated_at,
            },
        ]
        public_rows.extend(
            {
                "业务标识": f"line:{label}",
                "指标名称": "产品线占比",
                "指标值": f"{share:.4f}",
                "月份": "",
                "产品线匿名标签": label,
                "更新时间": updated_at,
            }
            for label, share in summary.business_line_share.items()
        )
        public_rows.extend(
            {
                "业务标识": f"month:{month}",
                "指标名称": "月度课次",
                "指标值": str(count),
                "月份": month,
                "产品线匿名标签": "",
                "更新时间": updated_at,
            }
            for month, count in summary.monthly_sessions.items()
        )
        return public_rows
    if resource == "public_class_schedule":
        return _public_class_schedule_rows(db, schedule_set_id)
    if resource == "public_class_links":
        return _public_class_links_rows(db, schedule_set_id)
    if resource == "public_adjustment_notice":
        return _public_adjustment_notice_rows(db, schedule_set_id)
    return []


def _sync_detail(
    result: dict[str, Any] | None,
    *,
    trigger: str,
    schedule_set_id: str,
    duration_ms: float | None = None,
    retry_count: int | None = None,
    error: str | None = None,
    reauthorization_required: bool = False,
) -> dict[str, Any]:
    """Keep every resource result self-describing in the sync history."""
    detail = {
        "trigger": trigger,
        "schedule_set_id": schedule_set_id,
        **(result or {}),
    }
    if duration_ms is not None:
        detail["duration_ms"] = round(max(0.0, duration_ms), 2)
    if retry_count is not None:
        # The service-level checkpoint includes shared preflight and also
        # exists when sync_rows raises before it can return a result.
        detail["retry_count"] = max(0, retry_count)
    if error:
        detail["error"] = error
    if reauthorization_required:
        detail["reauthorization_required"] = True
    return detail


def _sync_result_failure(result: dict[str, Any]) -> tuple[str | None, bool]:
    """Promote explicit sub-step failures to the resource-level status.

    Missing optional scopes remain visible as skipped/warning statuses.  Only
    an attempted sub-step that reports ``failed`` makes the resource fail;
    otherwise a failed class-view or duplicate-cleanup call would be rendered
    as a healthy completed sync even though its own result says otherwise.
    """

    failures: list[str] = []
    reauthorization_required = False
    for key, label in (
        ("duplicate_cleanup", "重复记录清理"),
        ("view_sync", "班级视图同步"),
    ):
        step = result.get(key)
        if not isinstance(step, dict) or step.get("status") != "failed":
            continue
        message = str(step.get("error") or f"{label}未完成")
        failures.append(f"{label}失败：{message}")
        reauthorization_required = reauthorization_required or bool(
            step.get("reauthorization_required")
        )
    return ("；".join(failures) or None, reauthorization_required)


def sync_feishu_resources(
    db: Session,
    user: User,
    schedule_set_id: str,
    resources: Sequence[str],
    *,
    workspace_id: str | None = None,
    trigger: str,
) -> FeishuBatchSyncResponse:
    """Synchronize independently logged resources without making the batch atomic.

    A timetable may contain useful master-data updates even when a later table
    (for example the public summary) cannot be written.  Each table therefore
    receives its own IntegrationSync row and failure is returned as a readable
    per-resource result instead of discarding successful writes.
    """
    service = FeishuService(settings, db)
    syncs: list[IntegrationSync] = []
    preflight_error: str | None = None
    preflight_reauthorization_required = False
    # Attribute the shared preflight to the first resource only.  This keeps
    # the sum/average telemetry truthful instead of multiplying one network
    # operation by the number of resources in the batch.
    first_resource_started = time_module.perf_counter()
    first_retry_checkpoint = service.request_retry_count
    try:
        # Inspect the existing Base once for this batch.  It adopts matching
        # tables, adds only missing tables/fields, and caches the result for
        # the per-resource exports below.
        service.prepare_sync_resources(
            user.id,
            list(resources),
            workspace_id,
            schedule_set_id,
        )
    except (FeishuServiceError, httpx.HTTPError) as exc:
        # Still persist one readable result per requested resource; a batch
        # should not vanish merely because its readiness check failed.
        preflight_error = str(exc)
        preflight_reauthorization_required = (
            isinstance(exc, FeishuServiceError) and exc.reauthorization_required
        )
    except Exception as exc:
        # Preserve the endpoint's per-resource isolation even for an
        # unexpected readiness-check failure.  Every requested resource gets
        # a durable failed result instead of leaving the whole batch invisible.
        logger.exception(
            "飞书批量同步预检异常：schedule_set_id=%s", schedule_set_id
        )
        preflight_error = str(exc)
    for index, resource in enumerate(resources):
        sync = IntegrationSync(
            schedule_set_id=schedule_set_id,
            direction="export",
            resource=resource,
            status="running",
            mode="live",
        )
        db.add(sync)
        # Never hold SQLite's write lock while a Feishu request is waiting.
        # The running record is durable before any network operation begins.
        db.commit()
        sync_started = (
            first_resource_started if index == 0 else time_module.perf_counter()
        )
        retry_checkpoint = (
            first_retry_checkpoint if index == 0 else service.request_retry_count
        )
        try:
            if preflight_error is not None:
                raise FeishuServiceError(
                    preflight_error,
                    reauthorization_required=preflight_reauthorization_required,
                )
            rows = export_resource_rows(db, resource, schedule_set_id)
            # Materialize rows before remote I/O and release the read
            # transaction as well; rollback/role changes can then commit while
            # Feishu is processing this resource.
            db.commit()
            result = service.sync_rows(
                user.id,
                resource,
                rows,
                workspace_id,
                schedule_set_id,
            )
            sync.records_read = int(result["records_read"])
            sync.records_written = int(result["records_written"])
            result_error, result_reauthorization_required = _sync_result_failure(result)
            sync.detail = _sync_detail(
                result,
                trigger=trigger,
                schedule_set_id=schedule_set_id,
                duration_ms=(time_module.perf_counter() - sync_started) * 1000,
                retry_count=service.request_retry_count - retry_checkpoint,
                error=result_error,
                reauthorization_required=result_reauthorization_required,
            )
            sync.status = "failed" if result_error else "completed"
        except (FeishuServiceError, httpx.HTTPError) as exc:
            logger.warning(
                "飞书批量同步失败：schedule_set_id=%s resource=%s",
                schedule_set_id,
                resource,
                exc_info=True,
            )
            sync.status = "failed"
            sync.detail = _sync_detail(
                None,
                trigger=trigger,
                schedule_set_id=schedule_set_id,
                duration_ms=(time_module.perf_counter() - sync_started) * 1000,
                retry_count=service.request_retry_count - retry_checkpoint,
                error=str(exc),
                reauthorization_required=(
                    isinstance(exc, FeishuServiceError) and exc.reauthorization_required
                ),
            )
        except Exception as exc:  # An unexpected failure must not discard other results.
            logger.exception(
                "飞书批量同步异常：schedule_set_id=%s resource=%s", schedule_set_id, resource
            )
            sync.status = "failed"
            sync.detail = _sync_detail(
                None,
                trigger=trigger,
                schedule_set_id=schedule_set_id,
                duration_ms=(time_module.perf_counter() - sync_started) * 1000,
                retry_count=service.request_retry_count - retry_checkpoint,
                error=str(exc),
            )
        audit(db, user, "sync", "feishu", sync.id, sync.detail)
        db.commit()
        db.refresh(sync)
        syncs.append(sync)

    failed_count = sum(item.status != "completed" for item in syncs)
    completed_count = len(syncs) - failed_count
    batch_status: Literal["completed", "partial", "failed"]
    if failed_count == 0:
        batch_status = "completed"
    elif completed_count == 0:
        batch_status = "failed"
    else:
        batch_status = "partial"
    return FeishuBatchSyncResponse(
        schedule_set_id=schedule_set_id,
        status=batch_status,
        completed_count=completed_count,
        failed_count=failed_count,
        records_read=sum(item.records_read for item in syncs),
        records_written=sum(item.records_written for item in syncs),
        results=[IntegrationSyncResponse.model_validate(item) for item in syncs],
    )


def trigger_published_data_sync(
    db: Session, user: User, schedule_set_id: str, *, event: Literal["publish", "rollback"]
) -> None:
    """Best-effort sync after a local version change.

    The schedule state has already been committed when this is called.  Failure
    is recorded in IntegrationSync and intentionally never changes that local
    publication or rollback result.
    """
    try:
        result = sync_feishu_resources(
            db,
            user,
            schedule_set_id,
            (
                "schedule",
                "public_summary",
                "public_adjustment_notice",
                "public_class_links",
            ),
            trigger=f"version_{event}",
        )
        if result.failed_count:
            logger.warning(
                "版本%s后的飞书发布数据同步未全部完成：schedule_set_id=%s failed=%s",
                "发布" if event == "publish" else "回滚",
                schedule_set_id,
                result.failed_count,
            )
    except Exception:
        # The local state is committed before this helper runs.  Roll back only
        # an unexpected sync-side transaction and preserve the version change.
        db.rollback()
        logger.exception(
            "版本%s后的飞书发布数据同步触发异常：schedule_set_id=%s",
            "发布" if event == "publish" else "回滚",
            schedule_set_id,
        )


def _parse_clock(value: str) -> time:
    """Parse the fixed clock text used by the official workbook."""
    normalized = value.strip().replace("：", ":")
    parts = normalized.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"无法解析时间: {value}")
    return time(hour=int(parts[0]), minute=int(parts[1]))


def _timestamp_epoch(date_value: date, clock_value: str) -> str:
    value = datetime.combine(date_value, _parse_clock(clock_value), SHANGHAI_TZ)
    return str(int(value.timestamp()))


def _busy_intervals(payload: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    """Normalize the several freebusy response shapes used by Feishu versions."""
    candidates: list[Any] = []
    for key in (
        "freebusy_lists",
        "freebusy_list",
        "freebusy_items",
        "freebusy",
        "busy",
        "busy_periods",
        "items",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    if not candidates and isinstance(payload.get("data"), dict):
        return _busy_intervals(payload["data"])
    intervals: list[tuple[datetime, datetime]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        start = item.get("start_time") or item.get("start") or item.get("time_min")
        end = item.get("end_time") or item.get("end") or item.get("time_max")
        if isinstance(start, dict):
            start = start.get("timestamp") or start.get("date")
        if isinstance(end, dict):
            end = end.get("timestamp") or end.get("date")
        try:
            if isinstance(start, str) and start.isdigit():
                start_dt = datetime.fromtimestamp(int(start), tz=SHANGHAI_TZ)
            else:
                start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            if isinstance(end, str) and end.isdigit():
                end_dt = datetime.fromtimestamp(int(end), tz=SHANGHAI_TZ)
            else:
                end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=SHANGHAI_TZ)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=SHANGHAI_TZ)
            if end_dt > start_dt:
                intervals.append((start_dt.astimezone(SHANGHAI_TZ), end_dt.astimezone(SHANGHAI_TZ)))
        except (TypeError, ValueError, OverflowError):
            continue
    return intervals


def _intervals_overlap(start: datetime, end: datetime, busy: tuple[datetime, datetime]) -> bool:
    return start < busy[1] and busy[0] < end


def _event_id(payload: dict[str, Any]) -> str:
    for candidate in (payload.get("event_id"), payload.get("id")):
        if candidate:
            return str(candidate)
    nested = payload.get("event")
    if isinstance(nested, dict):
        return _event_id(nested)
    data = payload.get("data")
    if isinstance(data, dict):
        return _event_id(data)
    raise FeishuServiceError("飞书创建日程响应缺少 event_id")


@router.post(
    "/integrations/feishu/sync",
    response_model=IntegrationSyncResponse,
    tags=["integrations"],
)
def feishu_sync(
    request: FeishuSyncRequest,
    db: Db,
    user: AdminOrScheduler,
    scope: SchedulerScope,
) -> IntegrationSync:
    service = FeishuService(settings, db)
    sync = IntegrationSync(
        schedule_set_id=scope.id,
        direction=request.direction,
        resource=request.resource,
        status="running",
        mode="live",
    )
    db.add(sync)
    # Persist the running state first; the following remote calls must not
    # monopolize SQLite's write lock.
    db.commit()
    sync_started = time_module.perf_counter()
    retry_checkpoint = service.request_retry_count
    try:
        rows = export_resource_rows(db, request.resource, scope.id)
        db.commit()
        service.prepare_sync_resources(
            user.id,
            [request.resource],
            request.workspace_id,
            scope.id,
        )
        result = service.sync_rows(
            user.id,
            request.resource,
            rows,
            request.workspace_id,
            scope.id,
        )
        sync.records_read = int(result["records_read"])
        sync.records_written = int(result["records_written"])
        result_error, result_reauthorization_required = _sync_result_failure(result)
        sync.detail = _sync_detail(
            result,
            trigger="single_resource",
            schedule_set_id=scope.id,
            duration_ms=(time_module.perf_counter() - sync_started) * 1000,
            retry_count=service.request_retry_count - retry_checkpoint,
            error=result_error,
            reauthorization_required=result_reauthorization_required,
        )
        sync.status = "failed" if result_error else "completed"
    except (FeishuServiceError, httpx.HTTPError) as exc:
        sync.status = "failed"
        sync.detail = _sync_detail(
            None,
            trigger="single_resource",
            schedule_set_id=scope.id,
            duration_ms=(time_module.perf_counter() - sync_started) * 1000,
            retry_count=service.request_retry_count - retry_checkpoint,
            error=str(exc),
            reauthorization_required=(
                isinstance(exc, FeishuServiceError) and exc.reauthorization_required
            ),
        )
        audit(db, user, "sync", "feishu", sync.id, sync.detail)
        db.commit()
        code = (
            status.HTTP_409_CONFLICT
            if isinstance(exc, FeishuServiceError)
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "飞书单资源同步异常：schedule_set_id=%s resource=%s",
            scope.id,
            request.resource,
        )
        sync.status = "failed"
        sync.detail = _sync_detail(
            None,
            trigger="single_resource",
            schedule_set_id=scope.id,
            duration_ms=(time_module.perf_counter() - sync_started) * 1000,
            retry_count=service.request_retry_count - retry_checkpoint,
            error=str(exc),
        )
        audit(db, user, "sync", "feishu", sync.id, sync.detail)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    audit(db, user, "sync", "feishu", sync.id, sync.detail)
    db.commit()
    db.refresh(sync)
    return sync


@router.post(
    "/integrations/feishu/sync-batch",
    response_model=FeishuBatchSyncResponse,
    tags=["integrations"],
)
def feishu_sync_batch(
    request: FeishuBatchSyncRequest,
    db: Db,
    user: AdminSchedulerOrApprover,
    scope: SchedulerOrApproverScope,
) -> FeishuBatchSyncResponse:
    """One-click export for the currently selected schedule-set workspace.

    Unlike a single-table retry, this endpoint always returns every requested
    resource result.  A failed resource is logged but does not hide the
    successes from the operator.
    """
    return sync_feishu_resources(
        db,
        user,
        scope.id,
        request.resources,
        workspace_id=request.workspace_id,
        trigger="manual_batch",
    )


@router.get(
    "/integrations/feishu/syncs",
    response_model=list[IntegrationSyncResponse],
    tags=["integrations"],
)
def list_feishu_syncs(db: Db, user: CurrentUser, scope: ViewerScope) -> list[IntegrationSync]:
    return list(
        db.scalars(
            select(IntegrationSync)
            .where(IntegrationSync.schedule_set_id == scope.id)
            .order_by(IntegrationSync.created_at.desc())
            .limit(50)
        )
    )


def require_aily_key(x_aily_key: Annotated[str, Header()]) -> None:
    expected = settings.aily_skill_api_key or ""
    if not expected or expected == DEMO_AILY_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="尚未配置 Aily 集成密钥，该接口已禁用",
        )
    if not secrets.compare_digest(x_aily_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Aily 集成密钥无效")


@router.get(
    "/aily/context",
    response_model=AilyContextResponse,
    tags=["aily"],
    dependencies=[Depends(require_aily_key)],
)
def aily_context(db: Db, schedule_scope: AilyScheduleScope) -> AilyContextResponse:
    teachers = {item.business_id: item for item in db.scalars(select(Teacher))}

    def course_context(item: CourseSession) -> dict[str, Any]:
        teacher = teachers.get(item.teacher_business_id)
        calendar_user_id = item.calendar_user_id or (teacher.calendar_user_id if teacher else None)
        return {
            "business_id": item.business_id,
            "business_line": item.business_line,
            "product_type": item.product_type,
            "product_types": _course_product_values(item),
            "product_contexts": item.product_contexts,
            "class_business_id": item.class_business_id,
            "teacher_business_id": item.teacher_business_id,
            "teacher_business_ids": item.teacher_business_ids,
            "calendar_user_id": calendar_user_id,
            "calendar_mapping_status": "mapped" if calendar_user_id else "unmapped",
            "subject": item.subject,
            "lesson_names": item.lesson_names,
            "stages": item.stages,
            "lesson_date": item.lesson_date.isoformat() if item.lesson_date else None,
            "fixed_start_time": item.fixed_start_time,
            "fixed_end_time": item.fixed_end_time,
            "candidate_clock_windows": item.candidate_clock_windows,
            "original_room_business_id": item.original_room_business_id,
            "candidate_room_business_ids": item.candidate_room_business_ids,
        }

    return AilyContextResponse(
        schedule_set_id=schedule_scope.id,
        entities={
            "teachers": [
                {"business_id": item.business_id, "name": item.name, "subject": item.subject}
                for item in db.scalars(
                    select(Teacher)
                    .where(Teacher.schedule_set_id == schedule_scope.id)
                    .order_by(Teacher.business_id)
                )
            ],
            "classes": [
                {
                    "business_id": item.business_id,
                    "name": item.name,
                    "product_types": item.product_types,
                    "teacher_business_ids": item.teacher_business_ids,
                }
                for item in class_group_responses(
                    db,
                    list(
                        db.scalars(
                            select(ClassGroup)
                            .where(ClassGroup.schedule_set_id == schedule_scope.id)
                            .order_by(ClassGroup.business_id)
                        )
                    ),
                    schedule_scope.id,
                )
            ],
            "rooms": [
                {
                    "business_id": item.business_id,
                    "name": item.name,
                }
                for item in db.scalars(
                    select(Room)
                    .where(Room.schedule_set_id == schedule_scope.id)
                    .order_by(Room.business_id)
                )
            ],
            "courses": [
                course_context(item)
                for item in db.scalars(
                    select(CourseSession)
                    .where(
                        CourseSession.schedule_set_id == schedule_scope.id,
                        CourseSession.is_active.is_(True)
                    )
                    .order_by(CourseSession.business_id)
                )
            ],
            "time_slots": [
                {
                    "business_id": item.business_id,
                    "weekday": item.weekday,
                    "start_time": item.start_time,
                    "end_time": item.end_time,
                    "kind": item.kind,
                    "sequence": item.sequence,
                }
                for item in db.scalars(
                    select(TimeSlot)
                    .where(TimeSlot.schedule_set_id == schedule_scope.id)
                    .order_by(TimeSlot.sequence)
                )
            ],
        },
        constraint_catalog=[{"type": key, **value} for key, value in RULE_CONSTRAINTS.items()],
        output_contract={
            "status": "awaiting_confirmation",
            "hard_rule_policy": "硬约束必须由教务人工确认后生效",
            "entity_policy": "actor_ids 和 scope 中的业务 ID 必须来自 entities",
            "batch_endpoint": "/api/v1/aily/rule-proposals",
            "solve_endpoint": "/api/v1/aily/solve",
            "schedule_set_header": SCHEDULE_SET_HEADER,
            "skill_contract": {
                "input": "自然语言排课意图 + 可选结构化筛选",
                "output": "结构化范围/规则 + solver_run_id",
                "external_call": "由飞书 Aily Skill 按该契约调用；本服务不伪造外部调用记录",
            },
        },
    )


@router.post(
    "/aily/rule-proposals",
    response_model=list[RuleResponse],
    tags=["aily"],
    dependencies=[Depends(require_aily_key)],
)
def aily_rule_proposals(
    batch: AilyRuleBatch, db: Db, schedule_scope: AilyScheduleScope
) -> list[Rule]:
    created: list[Rule] = []
    base_number = int(
        db.scalar(select(func.count(Rule.id)).where(Rule.schedule_set_id == schedule_scope.id)) or 0
    )
    for index, proposal in enumerate(batch.proposals, start=1):
        validate_rule_entities(db, proposal, schedule_scope.id)
        business_id = proposal.business_id or f"AILY-{base_number + index:04d}"
        data = proposal.model_dump(exclude={"business_id"})
        data["source_text"] = proposal.source_text or batch.source_text
        data["source_doc"] = proposal.source_doc or batch.source_doc
        data["status"] = "awaiting_confirmation"
        rule = Rule(schedule_set_id=schedule_scope.id, business_id=business_id, **data)
        db.add(rule)
        created.append(rule)
    audit(
        db,
        None,
        "propose",
        "aily_rule_batch",
        None,
        {"source_text": batch.source_text, "schedule_set_id": schedule_scope.id},
    )
    db.commit()
    for rule in created:
        db.refresh(rule)
    return created


@router.post(
    "/aily/solve",
    response_model=SolverRunResponse,
    status_code=202,
    tags=["aily"],
    dependencies=[Depends(require_aily_key)],
)
def aily_solve(request: AilySolveRequest, db: Db, schedule_scope: AilyScheduleScope) -> SolverRun:
    run = create_solver_run(db, None, request, schedule_scope.id)
    enqueue_solver_run(run.id)
    return run


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _solver_rules_from_labels(labels: list[str]) -> list[str]:
    matched = [key for key, label in SOLVER_RULE_LABELS.items() if label in labels]
    # 固定时段及两类资源冲突是企业确认的基础硬约束，不能因模型漏字段而消失。
    mandatory = ["fixed_time", "room_no_overlap", "teacher_no_overlap", "calendar_no_overlap"]
    return list(dict.fromkeys([*mandatory, *matched]))


def _validated_assistant_scope(
    db: Session, parsed: dict[str, Any], schedule_set_id: str
) -> dict[str, Any]:
    available = {
        "business_lines": {
            item
            for item in db.scalars(
                select(CourseSession.business_line).where(
                    CourseSession.schedule_set_id == schedule_set_id,
                    CourseSession.is_active.is_(True)
                )
            ).all()
            if item
        },
        "product_types": {item for item in _all_course_product_types(db, schedule_set_id) if item},
        "class_business_ids": set(
            db.scalars(
                select(CourseSession.class_business_id).where(
                    CourseSession.schedule_set_id == schedule_set_id,
                    CourseSession.is_active.is_(True)
                )
            ).all()
        ),
    }
    invalid: dict[str, list[str]] = {}
    for field, valid_values in available.items():
        values = _string_list(parsed.get(field))
        unknown = sorted(set(values) - valid_values)
        if unknown:
            invalid[field] = unknown
        parsed[field] = values
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={"message": "排课范围包含未知业务实体", **invalid},
        )
    return parsed


def _interpret_context(db: Session, schedule_set_id: str) -> dict[str, Any]:
    """AI 解析用的业务候选值上下文，同步与流式两条 interpret 通道共用。"""
    return {
        "business_lines": sorted(
            {
                item
                for item in db.scalars(
                    select(CourseSession.business_line).where(
                        CourseSession.schedule_set_id == schedule_set_id,
                        CourseSession.is_active.is_(True)
                    )
                ).all()
                if item
            }
        ),
        "product_types": sorted(_all_course_product_types(db, schedule_set_id)),
        "class_business_ids": sorted(
            set(
                db.scalars(
                    select(CourseSession.class_business_id).where(
                        CourseSession.schedule_set_id == schedule_set_id,
                        CourseSession.is_active.is_(True)
                    )
                ).all()
            )
        ),
        "fixed_rule_labels": list(SOLVER_RULE_LABELS.values()),
    }


def _finalize_assistant_interpret(
    db: Session,
    request: AssistantInterpretRequest,
    output: dict[str, Any],
    schedule_set_id: str,
    *,
    source: Literal["openai_compatible", "feishu_aily"],
    ai_configured: bool,
    aily_configured: bool,
    thinking: str | None,
) -> AssistantInterpretResponse:
    """把模型输出规范化为 AssistantInterpretResponse，同步与流式 interpret 共用。

    字段缺失/未知实体/结构不合法沿用同步接口的状态码与文案（502/422）。
    """
    required_fields = {
        "business_lines",
        "product_types",
        "class_business_ids",
        "date_from",
        "date_to",
        "date_window_days",
        "recognized_rules",
    }
    missing_fields = sorted(required_fields - set(output))
    if missing_fields:
        raise HTTPException(
            status_code=502,
            detail=f"AI 输出缺少字段：{', '.join(missing_fields)}",
        )
    parsed = {
        "business_lines": _string_list(output["business_lines"]),
        "product_types": _string_list(output["product_types"]),
        "class_business_ids": _string_list(output["class_business_ids"]),
        "date_from": output["date_from"],
        "date_to": output["date_to"],
        "date_window_days": output["date_window_days"],
        "recognized_rules": _string_list(output["recognized_rules"]),
    }
    unsupported = _string_list(output.get("unsupported_requirements"))
    for pattern, label in (
        (r"(?:老师|教师).*(?:请假|不.{0,4}(?:排|上|课)|只能|只上)", "具体教师的禁排或请假要求"),
        (r"(?:只能用|指定|固定|只用).{0,12}(?:教室|房间)", "指定教室要求"),
        (r"连续|连排", "精确连续课次要求"),
    ):
        if re.search(pattern, request.instruction):
            unsupported.append(label)
    known_labels = set(SOLVER_RULE_LABELS.values())
    unsupported.extend(label for label in parsed["recognized_rules"] if label not in known_labels)
    parsed["recognized_rules"] = [
        label for label in parsed["recognized_rules"] if label in known_labels
    ]
    parsed["unsupported_requirements"] = list(dict.fromkeys(unsupported))
    parsed["coverage_warnings"] = [
        "仅下列结构化范围和规则开关进入求解；本接口不会自动创建教师、教室或连续课次规则。",
        "教研组未落实到个人教师时，零冲突仅代表已建模对象的检查结果。",
    ]
    if "unsupported_requirements" not in output:
        parsed["coverage_warnings"].append("模型未返回逐项需求覆盖检查，请逐项核对原指令。")
    parsed["solver_rules"] = _solver_rules_from_labels(parsed["recognized_rules"])
    try:
        parsed = _validated_assistant_scope(db, parsed, schedule_set_id)
        # 目标验收闭环（MEM-C3）：解析成功即按结构化范围预填清单草稿，前端可
        # 增删项后再创建 goal。草稿由代码确定性生成，不依赖模型措辞。
        checklist_draft, checklist_warnings = draft_checklist_from_interpretation(
            request.instruction, parsed
        )
        return AssistantInterpretResponse(
            instruction=request.instruction,
            source=source,
            ai_configured=ai_configured,
            aily_configured=aily_configured,
            **parsed,
            goal_checklist_draft=[
                GoalChecklistItem.model_validate(item) for item in checklist_draft
            ],
            checklist_warnings=checklist_warnings,
            thinking=thinking,
            summary=(
                "通用 AI 模型已解析排课范围和固定业务规则"
                if source == "openai_compatible"
                else "飞书 Aily 已解析排课范围和固定业务规则"
            )
            + "，请教务确认后启动 CP-SAT 求解。",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"AI 返回结构不合法：{exc}") from exc


def _sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# Aily 解析通道的输出契约说明，同步与流式 interpret 共用同一份。
AILY_INTERPRET_CONTRACT = {
    "contract": {
        "business_lines": "string[]",
        "product_types": "string[]",
        "class_business_ids": "string[]",
        "date_from": "YYYY-MM-DD|null",
        "date_to": "YYYY-MM-DD|null",
        "date_window_days": "integer",
        "recognized_rules": "string[]",
        "solver_rules": (
            "fixed_time|room_no_overlap|calendar_no_overlap|minimize_changes[]"
        ),
    }
}


@router.post(
    "/assistant/interpret",
    response_model=AssistantInterpretResponse,
    tags=["aily", "assistant"],
)
def assistant_interpret(
    request: AssistantInterpretRequest,
    db: Db,
    user: AdminOrScheduler,
    schedule_scope: SchedulerScope,
) -> AssistantInterpretResponse:
    ai_service = AIService(settings, db)
    ai_configuration = ai_service.configuration_view()
    configuration = FeishuService(settings, db).configuration_view()
    aily_app_id = settings.aily_app_id or configuration.get("aily_app_id")
    aily_skill_id = settings.aily_skill_id or configuration.get("aily_skill_id")
    aily_configured = bool(aily_app_id and aily_skill_id)
    source: Literal["openai_compatible", "feishu_aily"]
    output: dict[str, Any]
    thinking: str | None = None
    if ai_configuration["configured"]:
        try:
            output, thinking = ai_service.interpret_instruction(
                request.instruction, context=_interpret_context(db, schedule_scope.id)
            )
        except AIServiceError as exc:
            raise HTTPException(status_code=502, detail=f"AI 指令解析失败：{exc}") from exc
        source = "openai_compatible"
    elif aily_configured:
        try:
            output = FeishuService(settings, db).start_aily_skill(
                user.id,
                app_id=str(aily_app_id),
                skill_id=str(aily_skill_id),
                query=request.instruction,
                input_payload=dict(AILY_INTERPRET_CONTRACT),
            )
        except (FeishuServiceError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=f"飞书 Aily 解析失败：{exc}") from exc
        source = "feishu_aily"
    else:
        raise HTTPException(
            status_code=409,
            detail="尚未配置一句话排课 AI，请先前往“飞书集成”填写模型接口配置。",
        )
    normalized = _finalize_assistant_interpret(
        db,
        request,
        output,
        schedule_scope.id,
        source=source,
        ai_configured=bool(ai_configuration["configured"]),
        aily_configured=aily_configured,
        thinking=thinking,
    )
    audit(
        db,
        user,
        "assistant_interpret",
        "instruction",
        None,
        {"source": normalized.source, "instruction": request.instruction},
    )
    db.commit()
    return normalized


@router.post(
    "/assistant/interpret/stream",
    tags=["aily", "assistant"],
)
async def assistant_interpret_stream(
    request: AssistantInterpretRequest,
    db: Db,
    user: AdminOrScheduler,
    schedule_scope: SchedulerScope,
) -> StreamingResponse:
    """一句话排课的 SSE 流式解析，事件协议见 docs/对接资料 的「后端接口与运行约定」。

    事件序列：`stage`（connect/read/validate）→ 若干 `thinking` 增量 → `result`
    （完整 AssistantInterpretResponse JSON，与同步接口同构）→ 出错时 `error`。
    首包立即下行，既作连接确认也让反代尽早开始转发；客户端断开时
    StreamingResponse 会取消本生成器，httpx 上游流随之关闭。Aily 无流式，
    退化为单条 result 事件（伪流式）。AI 未配置且无 Aily 时仍返回 409 JSON。
    """
    ai_service = AIService(settings, db)
    ai_configuration = ai_service.configuration_view()
    configuration = FeishuService(settings, db).configuration_view()
    aily_app_id = settings.aily_app_id or configuration.get("aily_app_id")
    aily_skill_id = settings.aily_skill_id or configuration.get("aily_skill_id")
    aily_configured = bool(aily_app_id and aily_skill_id)
    if not ai_configuration["configured"] and not aily_configured:
        raise HTTPException(
            status_code=409,
            detail="尚未配置一句话排课 AI，请先前往“飞书集成”填写模型接口配置。",
        )
    started = time_module.perf_counter()

    async def event_stream() -> AsyncIterator[str]:
        def elapsed() -> float:
            return time_module.perf_counter() - started

        # 首包立即发送：客户端据此确认连接，不会被反代缓冲卡到请求结束。
        yield _sse_event("stage", {"stage": "connect"})
        if ai_configuration["configured"]:
            try:
                yield _sse_event("stage", {"stage": "read"})
                async for kind, payload in ai_service.stream_interpret_instruction(
                    request.instruction,
                    context=_interpret_context(db, schedule_scope.id),
                ):
                    if kind == "thinking":
                        yield _sse_event(
                            "thinking", {"delta": payload, "elapsed": elapsed()}
                        )
                        continue
                    yield _sse_event("stage", {"stage": "validate"})
                    normalized = _finalize_assistant_interpret(
                        db,
                        request,
                        payload["parsed"],
                        schedule_scope.id,
                        source="openai_compatible",
                        ai_configured=True,
                        aily_configured=aily_configured,
                        thinking=payload["thinking"],
                    )
                    audit(
                        db,
                        user,
                        "assistant_interpret",
                        "instruction",
                        None,
                        {
                            "source": normalized.source,
                            "instruction": request.instruction,
                            "stream": True,
                        },
                    )
                    db.commit()
                    yield _sse_event("result", normalized.model_dump(mode="json"))
            except AIServiceError as exc:
                yield _sse_event("error", {"detail": f"AI 指令解析失败：{exc}"})
            except HTTPException as exc:
                yield _sse_event("error", {"detail": exc.detail})
        else:
            try:
                yield _sse_event("stage", {"stage": "read"})
                # Aily 是阻塞 httpx 调用，放线程池执行，不阻塞事件循环。
                output = await asyncio.to_thread(
                    FeishuService(settings, db).start_aily_skill,
                    user.id,
                    app_id=str(aily_app_id),
                    skill_id=str(aily_skill_id),
                    query=request.instruction,
                    input_payload=dict(AILY_INTERPRET_CONTRACT),
                )
                yield _sse_event("stage", {"stage": "validate"})
                normalized = _finalize_assistant_interpret(
                    db,
                    request,
                    output,
                    schedule_scope.id,
                    source="feishu_aily",
                    ai_configured=False,
                    aily_configured=True,
                    thinking=None,
                )
                audit(
                    db,
                    user,
                    "assistant_interpret",
                    "instruction",
                    None,
                    {
                        "source": normalized.source,
                        "instruction": request.instruction,
                        "stream": True,
                    },
                )
                db.commit()
                yield _sse_event("result", normalized.model_dump(mode="json"))
            except (FeishuServiceError, httpx.HTTPError) as exc:
                yield _sse_event("error", {"detail": f"飞书 Aily 解析失败：{exc}"})
            except HTTPException as exc:
                yield _sse_event("error", {"detail": exc.detail})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/assistant/solve",
    response_model=SolverRunResponse,
    status_code=202,
    tags=["aily", "assistant"],
)
def assistant_solve(
    request: AssistantSolveRequest,
    db: Db,
    user: AdminOrScheduler,
    schedule_scope: SchedulerScope,
) -> SolverRun:
    """Login-session entry point for Aily's natural-language scheduling skill.

    Aily may call this endpoint after turning the instruction into structured
    filters/rules. The instruction is persisted for traceability, while the
    same CP-SAT path as the regular solver is used for deterministic execution.
    """
    _validated_assistant_scope(db, request.model_dump(), schedule_scope.id)
    goal = _resolve_goal_for_run(db, request.goal_id, schedule_scope.id)
    run = create_solver_run(
        db,
        user.id,
        request,
        schedule_scope.id,
        extra={"assistant_entry": True},
        goal_id=goal.id if goal else None,
    )
    audit(
        db,
        user,
        "assistant_solve",
        "solver_run",
        run.id,
        {"instruction": request.instruction, "goal_id": run.goal_id},
    )
    db.commit()
    if request.wait:
        execute_solver_run(run.id)
    else:
        enqueue_solver_run(run.id)
    db.refresh(run)
    return run


def _public_summary(
    db: Session, schedule_set_id: str = DEFAULT_SCHEDULE_SET_ID
) -> PublicScheduleSummary:
    schedule = db.scalar(
        select(ScheduleVersion)
        .where(
            ScheduleVersion.schedule_set_id == schedule_set_id,
            ScheduleVersion.status == "published",
        )
        .order_by(ScheduleVersion.version_no.desc())
    )
    assignments = (
        list(
            db.scalars(
                select(ScheduleAssignment)
                .where(ScheduleAssignment.schedule_version_id == schedule.id)
                .order_by(ScheduleAssignment.lesson_date, ScheduleAssignment.course_session_id)
            )
        )
        if schedule
        else []
    )
    courses = version_course_map(db, schedule) if schedule else {}
    rooms = {
        item.business_id: item
        for item in db.scalars(select(Room).where(Room.schedule_set_id == schedule_set_id))
    }
    line_counts: Counter[str] = Counter()
    monthly_counts: Counter[str] = Counter()
    room_period_keys: set[tuple[str, date | None, str, str]] = set()
    date_period_keys: set[tuple[date, str, str]] = set()
    for assignment in assignments:
        course = courses.get(assignment.course_session_id)
        if not course:
            continue
        line = course.business_line or "未分类"
        line_counts[line] += 1
        if assignment.lesson_date:
            monthly_counts[assignment.lesson_date.strftime("%Y-%m")] += 1
        room = rooms.get(assignment.room_business_id)
        if room and room.is_active:
            room_period_keys.add(
                (
                    assignment.room_business_id,
                    assignment.lesson_date,
                    course.fixed_start_time,
                    course.fixed_end_time,
                )
            )
        if assignment.lesson_date:
            date_period_keys.add(
                (assignment.lesson_date, course.fixed_start_time, course.fixed_end_time)
            )
    total = sum(line_counts.values())
    active_rooms = max(sum(1 for item in rooms.values() if item.is_active), 1)
    capacity = active_rooms * max(len(date_period_keys), 1)
    # Only anonymous labels and coarse time categories are exposed publicly.
    preview: list[PublicScheduleShareItem] = []
    for index, assignment in enumerate(assignments[:6], start=1):
        course = courses.get(assignment.course_session_id)
        fixed_start = course.fixed_start_time if course else ""
        start_hour = _parse_clock(fixed_start).hour if fixed_start else 12
        preview.append(
            PublicScheduleShareItem(
                class_label=f"班级{chr(64 + index)}",
                room_label=f"教室{chr(64 + index)}",
                month=(
                    "月份"
                    if assignment.lesson_date is None
                    else assignment.lesson_date.strftime("%Y-%m")
                ),
                time_period=(
                    "上午" if start_hour < 12 else ("下午" if start_hour < 18 else "晚间")
                ),
            )
        )
    return PublicScheduleSummary(
        data_policy="仅公开匿名汇总与脱敏投影，不包含原始班型、班级、教室、教研组、课节或完整日期明细",
        total_sessions=total,
        business_line_share={
            f"产品线{chr(65 + index)}": round(count / total, 4) if total else 0.0
            for index, count in enumerate(sorted(line_counts.values(), reverse=True))
        },
        monthly_sessions=dict(sorted(monthly_counts.items())),
        room_utilization=round(len(room_period_keys) / capacity, 4) if capacity else 0.0,
        preview=preview,
    )


# ---------------------------------------------------------------------------
# 公开课表层 · 公开端点（免登录，docs/roadmap/06 §3 A4）。
# 端点不声明 CurrentUser/ViewerScope 即绕过 JWT（先例 require_aily_key）；
# 伪造/过期/停用 token 一律 404，不暴露存在性；响应为显式 Pydantic 白名单，
# 禁止整模型透传（06 §4）。
# ---------------------------------------------------------------------------


def _resolve_active_public_link(db: Session, token: str) -> PublicLinkToken:
    """按明文 token 定位有效链接；无效/停用/过期与伪造同形 404。"""

    if not settings.public_links_enabled:
        raise HTTPException(status_code=404, detail="资源不存在")
    link = db.scalar(
        select(PublicLinkToken).where(
            PublicLinkToken.token_hash == _public_link_hash(token)
        )
    )
    schedule_set = db.get(ScheduleSet, link.schedule_set_id) if link else None
    now = shanghai_now()
    if (
        link is None
        or schedule_set is None
        or not schedule_set.is_active
        or link.revoked_at is not None
        or (link.expires_at is not None and _aware_utc(link.expires_at) <= now)
    ):
        raise HTTPException(status_code=404, detail="资源不存在")
    return link


def _touch_public_link(db: Session, link: PublicLinkToken) -> None:
    """命中后节流更新访问计数：10 分钟内的重复拉取只记一次（06 §3 A6）。"""

    now = shanghai_now()
    if (
        link.last_seen_at is not None
        and _aware_utc(link.last_seen_at) > now - PUBLIC_LINK_SEEN_THROTTLE
    ):
        return
    link.last_seen_at = now
    link.access_count += 1
    db.commit()


@router.get(
    "/public/links/{token}/schedule.json",
    response_model=PublicLinkSchedulePayload | PublicLinkDirectoryPayload,
    tags=["public"],
)
def public_link_schedule(token: str, db: Db) -> dict[str, Any]:
    link = _resolve_active_public_link(db, token)
    if link.scope == "school":
        payload: dict[str, Any] = public_directory_payload(db, link.schedule_set_id)
    elif link.scope == "teacher":
        payload = public_teacher_payload(
            db,
            link.schedule_set_id,
            link.campus_id or "",
            link.resource_business_id or "",
            show_teacher_names=link.show_teacher_names,
        )
    else:
        payload = public_class_payload(
            db,
            link.schedule_set_id,
            link.campus_id or "",
            link.resource_business_id or "",
            show_teacher_names=link.show_teacher_names,
        )
    _touch_public_link(db, link)
    return payload


@router.get(
    "/public/links/{token}/class/{campus_id}/{class_business_id}/schedule.json",
    response_model=PublicLinkSchedulePayload,
    tags=["public"],
)
def public_link_class_schedule(
    token: str, campus_id: str, class_business_id: str, db: Db
) -> dict[str, Any]:
    """school 目录链接下钻到单个班级的公开 payload（06 §3 B6 督导公示）。

    仅 school scope token 有效：class/teacher 链接本就绑定单一资源，无需下钻。
    scope 不符与伪造/过期/停用一样同形 404；节流计数沿用现有公开端点模式。
    """

    link = _resolve_active_public_link(db, token)
    if link.scope != "school":
        raise HTTPException(status_code=404, detail="资源不存在")
    payload = public_class_payload(
        db,
        link.schedule_set_id,
        campus_id,
        class_business_id,
        show_teacher_names=link.show_teacher_names,
    )
    _touch_public_link(db, link)
    return payload


@router.get("/public/links/{token}/calendar.ics", tags=["public"])
def public_link_calendar(
    token: str,
    db: Db,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    link = _resolve_active_public_link(db, token)
    schedule = _current_published_schedule(db, link.schedule_set_id)
    if link.scope == "teacher":
        entries = public_schedule_entries(
            db,
            link.schedule_set_id,
            campus_id=link.campus_id,
            teacher_business_id=link.resource_business_id,
        )
    elif link.scope == "class":
        entries = public_schedule_entries(
            db,
            link.schedule_set_id,
            campus_id=link.campus_id,
            class_business_id=link.resource_business_id,
        )
    else:
        entries = public_schedule_entries(db, link.schedule_set_id)

    etag_value = calendar_etag(
        schedule.id if schedule else None,
        schedule.published_at if schedule else None,
    )
    etag = f'"{etag_value}"'
    headers = {"Cache-Control": "public, max-age=3600", "ETag": etag}
    request_etag = if_none_match.strip() if if_none_match else ""
    if request_etag in {etag, f"W/{etag}", etag_value}:
        return Response(status_code=304, headers=headers)
    ics_bytes = build_public_calendar_ics(
        display_name=link.display_name,
        scope=link.scope,
        resource_business_id=link.resource_business_id,
        show_teacher_names=link.show_teacher_names,
        version_no=schedule.version_no if schedule else None,
        published_at=schedule.published_at if schedule else None,
        rows=entries,
    )
    _touch_public_link(db, link)
    return Response(
        content=ics_bytes,
        media_type="text/calendar; charset=utf-8",
        headers=headers,
    )
