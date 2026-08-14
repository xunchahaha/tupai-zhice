from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import PROJECT_ROOT, get_settings
from .db import get_db
from .models import (
    AuditLog,
    CalendarEventBinding,
    Campus,
    ClassGroup,
    CourseSession,
    IntegrationSync,
    RescheduleEvent,
    Room,
    Rule,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    Teacher,
    TimeSlot,
    User,
    utcnow,
)
from .schemas import (
    AilyContextResponse,
    AilyRuleBatch,
    AilySolveRequest,
    AssignmentResponse,
    AssistantInterpretRequest,
    AssistantInterpretResponse,
    AssistantSolveRequest,
    AuditLogResponse,
    BatchOperationResponse,
    CalendarEventBindingResponse,
    CalendarPublishRequest,
    CalendarPublishResponse,
    CampusCreate,
    CampusResponse,
    ClassGroupBatchUpdate,
    ClassGroupPayload,
    ClassGroupResponse,
    CourseSessionBatchDelete,
    CourseSessionBatchUpdate,
    CourseSessionPayload,
    CourseSessionResponse,
    CourseSessionUpdate,
    FeishuAppConfigurationInput,
    FeishuAppConfigurationResponse,
    FeishuConnectionResponse,
    FeishuOAuthStartResponse,
    FeishuSyncRequest,
    FeishuWorkspaceCreate,
    FeishuWorkspaceResponse,
    ImportResult,
    IntegrationSyncResponse,
    MasterDataBatchDelete,
    OverviewResponse,
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
    SolveRequest,
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
    UserStatusUpdate,
)
from .security import (
    create_access_token,
    get_current_user,
    hash_password,
    require_roles,
    verify_password,
)
from .services.converter_zhengzhou import import_schedule_workbook
from .services.feishu import FeishuService, FeishuServiceError, json_text
from .services.snapshot import create_snapshot
from .services.tasks import enqueue_solver_run, execute_solver_run
from .services.xlsx_io import export_schedule_xlsx

logger = logging.getLogger("tupai.feishu")

settings = get_settings()
router = APIRouter(prefix=settings.api_prefix)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
Db = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminOrScheduler = Annotated[User, Depends(require_roles("admin", "scheduler"))]
Approver = Annotated[User, Depends(require_roles("admin", "approver"))]
Admin = Annotated[User, Depends(require_roles("admin"))]


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


def get_or_404(db: Session, model: type[Any], object_id: str) -> Any:
    instance = db.get(model, object_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return instance


def schedule_response(db: Session, schedule: ScheduleVersion) -> ScheduleResponse:
    rows = list(
        db.scalars(
            select(ScheduleAssignment).where(ScheduleAssignment.schedule_version_id == schedule.id)
        )
    )
    courses = {
        item.id: item
        for item in db.scalars(
            select(CourseSession).where(
                CourseSession.id.in_([row.course_session_id for row in rows])
            )
        )
    }
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
            )
            for row in rows
        ],
    )


@router.post("/auth/token", response_model=TokenResponse, tags=["auth"])
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: Db) -> TokenResponse:
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user.last_login_at = utcnow()
    db.commit()
    return TokenResponse(
        access_token=create_access_token(user), user=UserResponse.model_validate(user)
    )


@router.get("/auth/me", response_model=UserResponse, tags=["auth"])
def current_user(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.get("/users", response_model=list[UserResponse], tags=["accounts"])
def list_users(db: Db, user: Admin) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at, User.username)))


@router.post("/users", response_model=UserResponse, status_code=201, tags=["accounts"])
def create_user(payload: UserCreate, db: Db, user: Admin) -> User:
    instance = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="viewer",
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
def update_user_status(
    user_id: str, payload: UserStatusUpdate, db: Db, user: Admin
) -> User:
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


@router.post("/users/{user_id}/reset-password", status_code=204, tags=["accounts"])
def reset_user_password(
    user_id: str, payload: UserPasswordReset, db: Db, user: Admin
) -> Response:
    target = get_or_404(db, User, user_id)
    target.password_hash = hash_password(payload.password)
    audit(db, user, "reset_password", "user", target.id, {"username": target.username})
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
def overview(db: Db, user: CurrentUser) -> OverviewResponse:
    latest_run = db.scalar(select(SolverRun).order_by(SolverRun.created_at.desc()))
    latest_schedule = db.scalar(select(ScheduleVersion).order_by(ScheduleVersion.version_no.desc()))
    latest_sync = db.scalar(select(IntegrationSync).order_by(IntegrationSync.created_at.desc()))
    counts = {
        "teachers": int(db.scalar(select(func.count(Teacher.id))) or 0),
        "class_groups": int(db.scalar(select(func.count(ClassGroup.id))) or 0),
        "rooms": int(db.scalar(select(func.count(Room.id))) or 0),
        "time_slots": int(db.scalar(select(func.count(TimeSlot.id))) or 0),
        "course_sessions": int(db.scalar(select(func.count(CourseSession.id))) or 0),
        "schedule_versions": int(db.scalar(select(func.count(ScheduleVersion.id))) or 0),
    }
    return OverviewResponse(
        counts=counts,
        latest_run=SolverRunResponse.model_validate(latest_run) if latest_run else None,
        latest_schedule=schedule_response(db, latest_schedule) if latest_schedule else None,
        pending_rules=int(
            db.scalar(select(func.count(Rule.id)).where(Rule.status == "awaiting_confirmation"))
            or 0
        ),
        pending_reschedules=int(
            db.scalar(
                select(func.count(RescheduleEvent.id)).where(
                    RescheduleEvent.status.in_(["pending", "candidate_ready"])
                )
            )
            or 0
        ),
        latest_sync_status=latest_sync.status if latest_sync else None,
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


@router.post("/imports/xlsx", response_model=ImportResult, tags=["imports"])
def import_xlsx(
    db: Db, user: AdminOrScheduler, file: Annotated[UploadFile, File(...)]
) -> ImportResult:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="只接受 .xlsx 工作簿")
    target = PROJECT_ROOT / "data" / "imports" / "uploaded.xlsx"
    target.write_bytes(file.file.read())
    result = import_schedule_workbook(db, target)
    audit(db, user, "import_xlsx", "workbook", file.filename, result)
    db.commit()
    return ImportResult(
        source=file.filename or target.name,
        campuses=1,
        teachers=result["teachers"],
        class_groups=result["class_groups"],
        rooms=result["rooms"],
        time_slots=result["time_slots"],
        course_sessions=result["course_sessions_created"],
        rules=0,
    )


@router.get("/campuses", response_model=list[CampusResponse], tags=["master-data"])
def list_campuses(db: Db, user: CurrentUser) -> list[Campus]:
    return list(db.scalars(select(Campus).order_by(Campus.business_id)))


@router.post("/campuses", response_model=CampusResponse, status_code=201, tags=["master-data"])
def create_campus(payload: CampusCreate, db: Db, user: AdminOrScheduler) -> Campus:
    instance = Campus(**payload.model_dump())
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
    db: Session, model: type[Any], object_ids: list[str], resource_label: str
) -> list[Any]:
    rows = list(db.scalars(select(model).where(model.id.in_(object_ids))))
    found_ids = {item.id for item in rows}
    missing = [item for item in object_ids if item not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"未找到 {len(missing)} 条{resource_label}记录")
    return rows


def raise_reference_conflict(
    items: list[Any], referenced: set[tuple[str, str]], resource_label: str, references: str
) -> None:
    labels = [
        item.business_id
        for item in items
        if (item.campus_id, item.business_id) in referenced
    ]
    if not labels:
        return
    preview = "、".join(labels[:5])
    suffix = "等" if len(labels) > 5 else ""
    raise HTTPException(
        status_code=409,
        detail=(
            f"{len(labels)} 条{resource_label}已被{references}引用，"
            f"不能直接删除：{preview}{suffix}"
        ),
    )


def ensure_teachers_deletable(db: Session, teachers: list[Teacher]) -> None:
    identities = {(item.campus_id, item.business_id) for item in teachers}
    campus_ids = {item[0] for item in identities}
    business_ids = {item[1] for item in identities}
    class_references = {
        (campus_id, business_id)
        for campus_id, business_id in db.execute(
            select(ClassGroup.campus_id, ClassGroup.teacher_business_id).where(
                ClassGroup.campus_id.in_(campus_ids),
                ClassGroup.teacher_business_id.in_(business_ids),
            )
        )
    }
    course_references = {
        (campus_id, business_id)
        for campus_id, business_id in db.execute(
            select(CourseSession.campus_id, CourseSession.teacher_business_id).where(
                CourseSession.campus_id.in_(campus_ids),
                CourseSession.teacher_business_id.in_(business_ids),
            )
        )
    }
    raise_reference_conflict(
        teachers,
        identities & (class_references | course_references),
        "教师",
        "班级或课程",
    )


def ensure_class_groups_deletable(db: Session, classes: list[ClassGroup]) -> None:
    identities = {(item.campus_id, item.business_id) for item in classes}
    campus_ids = {item[0] for item in identities}
    business_ids = {item[1] for item in identities}
    references = {
        (campus_id, business_id)
        for campus_id, business_id in db.execute(
            select(CourseSession.campus_id, CourseSession.class_business_id).where(
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
def list_teachers(db: Db, user: CurrentUser) -> list[Teacher]:
    return list(db.scalars(select(Teacher).order_by(Teacher.business_id)))


@router.post("/teachers", response_model=TeacherResponse, status_code=201, tags=["master-data"])
def create_teacher(payload: TeacherPayload, db: Db, user: AdminOrScheduler) -> Teacher:
    instance = Teacher(**payload.model_dump())
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.put("/teachers/{object_id}", response_model=TeacherResponse, tags=["master-data"])
def update_teacher(
    object_id: str, payload: TeacherPayload, db: Db, user: AdminOrScheduler
) -> Teacher:
    instance = get_or_404(db, Teacher, object_id)
    for key, value in payload.model_dump().items():
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
    payload: TeacherBatchUpdate, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    teachers: list[Teacher] = selected_master_rows(db, Teacher, payload.object_ids, "教师")
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
    payload: MasterDataBatchDelete, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    teachers: list[Teacher] = selected_master_rows(db, Teacher, payload.object_ids, "教师")
    ensure_teachers_deletable(db, teachers)
    for teacher in teachers:
        db.delete(teacher)
    audit(db, user, "batch_delete", "teacher", None, {"count": len(teachers)})
    db.commit()
    return BatchOperationResponse(affected_count=len(teachers))


@router.get("/class-groups", response_model=list[ClassGroupResponse], tags=["master-data"])
def list_class_groups(db: Db, user: CurrentUser) -> list[ClassGroup]:
    return list(db.scalars(select(ClassGroup).order_by(ClassGroup.business_id)))


@router.post(
    "/class-groups", response_model=ClassGroupResponse, status_code=201, tags=["master-data"]
)
def create_class_group(payload: ClassGroupPayload, db: Db, user: AdminOrScheduler) -> ClassGroup:
    instance = ClassGroup(**payload.model_dump())
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.put("/class-groups/{object_id}", response_model=ClassGroupResponse, tags=["master-data"])
def update_class_group(
    object_id: str, payload: ClassGroupPayload, db: Db, user: AdminOrScheduler
) -> ClassGroup:
    instance = get_or_404(db, ClassGroup, object_id)
    for key, value in payload.model_dump().items():
        setattr(instance, key, value)
    audit(db, user, "update", "class_group", object_id)
    db.commit()
    db.refresh(instance)
    return instance


def validate_class_group_teacher(
    db: Session, classes: list[ClassGroup], teacher_business_id: str
) -> None:
    campus_ids = {item.campus_id for item in classes}
    matched_campuses = set(
        db.scalars(
            select(Teacher.campus_id).where(
                Teacher.business_id == teacher_business_id,
                Teacher.campus_id.in_(campus_ids),
            )
        )
    )
    if matched_campuses != campus_ids:
        raise HTTPException(status_code=422, detail="指定教师不属于所选班级的校区")


@router.post(
    "/class-groups/batch-update",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_update_class_groups(
    payload: ClassGroupBatchUpdate, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    classes: list[ClassGroup] = selected_master_rows(
        db, ClassGroup, payload.object_ids, "班级"
    )
    changes = payload.model_dump(exclude={"object_ids"}, exclude_unset=True)
    teacher_business_id = changes.get("teacher_business_id")
    if teacher_business_id is not None:
        validate_class_group_teacher(db, classes, teacher_business_id)
    for class_group in classes:
        for key, value in changes.items():
            setattr(class_group, key, value)
    audit(
        db,
        user,
        "batch_update",
        "class_group",
        None,
        {"count": len(classes), "fields": sorted(changes)},
    )
    db.commit()
    return BatchOperationResponse(affected_count=len(classes))


@router.post(
    "/class-groups/batch-delete",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_delete_class_groups(
    payload: MasterDataBatchDelete, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    classes: list[ClassGroup] = selected_master_rows(
        db, ClassGroup, payload.object_ids, "班级"
    )
    ensure_class_groups_deletable(db, classes)
    for class_group in classes:
        db.delete(class_group)
    audit(db, user, "batch_delete", "class_group", None, {"count": len(classes)})
    db.commit()
    return BatchOperationResponse(affected_count=len(classes))


@router.get("/rooms", response_model=list[RoomResponse], tags=["master-data"])
def list_rooms(db: Db, user: CurrentUser) -> list[Room]:
    return list(db.scalars(select(Room).order_by(Room.business_id)))


@router.post("/rooms", response_model=RoomResponse, status_code=201, tags=["master-data"])
def create_room(payload: RoomPayload, db: Db, user: AdminOrScheduler) -> Room:
    instance = Room(**payload.model_dump())
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.put("/rooms/{object_id}", response_model=RoomResponse, tags=["master-data"])
def update_room(object_id: str, payload: RoomPayload, db: Db, user: AdminOrScheduler) -> Room:
    instance = get_or_404(db, Room, object_id)
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
    payload: RoomBatchUpdate, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    rooms: list[Room] = selected_master_rows(db, Room, payload.object_ids, "教室")
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
    payload: MasterDataBatchDelete, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    rooms: list[Room] = selected_master_rows(db, Room, payload.object_ids, "教室")
    ensure_rooms_deletable(db, rooms)
    for room in rooms:
        db.delete(room)
    audit(db, user, "batch_delete", "room", None, {"count": len(rooms)})
    db.commit()
    return BatchOperationResponse(affected_count=len(rooms))


@router.get("/time-slots", response_model=list[TimeSlotResponse], tags=["master-data"])
def list_time_slots(db: Db, user: CurrentUser) -> list[TimeSlot]:
    return list(db.scalars(select(TimeSlot).order_by(TimeSlot.sequence)))


@router.post("/time-slots", response_model=TimeSlotResponse, status_code=201, tags=["master-data"])
def create_time_slot(payload: TimeSlotPayload, db: Db, user: AdminOrScheduler) -> TimeSlot:
    instance = TimeSlot(**payload.model_dump())
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.put("/time-slots/{object_id}", response_model=TimeSlotResponse, tags=["master-data"])
def update_time_slot(
    object_id: str, payload: TimeSlotPayload, db: Db, user: AdminOrScheduler
) -> TimeSlot:
    instance = get_or_404(db, TimeSlot, object_id)
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
    payload: TimeSlotBatchUpdate, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    slots: list[TimeSlot] = selected_master_rows(db, TimeSlot, payload.object_ids, "时段")
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
    payload: MasterDataBatchDelete, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    slots: list[TimeSlot] = selected_master_rows(db, TimeSlot, payload.object_ids, "时段")
    ensure_time_slots_deletable(db, slots)
    for slot in slots:
        db.delete(slot)
    audit(db, user, "batch_delete", "time_slot", None, {"count": len(slots)})
    db.commit()
    return BatchOperationResponse(affected_count=len(slots))


@router.get("/course-sessions", response_model=list[CourseSessionResponse], tags=["master-data"])
def list_course_sessions(db: Db, user: CurrentUser) -> list[CourseSession]:
    return list(db.scalars(select(CourseSession).order_by(CourseSession.business_id)))


@router.post(
    "/course-sessions",
    response_model=CourseSessionResponse,
    status_code=201,
    tags=["master-data"],
)
def create_course_session(
    payload: CourseSessionPayload, db: Db, user: AdminOrScheduler
) -> CourseSession:
    instance = CourseSession(**payload.model_dump())
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.put(
    "/course-sessions/{object_id}", response_model=CourseSessionResponse, tags=["master-data"]
)
def update_course_session(
    object_id: str, payload: CourseSessionUpdate, db: Db, user: AdminOrScheduler
) -> CourseSession:
    instance = get_or_404(db, CourseSession, object_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(instance, key, value)
    audit(db, user, "update", "course_session", object_id)
    db.commit()
    db.refresh(instance)
    return instance


def selected_course_sessions(db: Session, object_ids: list[str]) -> list[CourseSession]:
    rows = list(db.scalars(select(CourseSession).where(CourseSession.id.in_(object_ids))))
    found_ids = {item.id for item in rows}
    missing = [item for item in object_ids if item not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"未找到 {len(missing)} 条课程记录")
    return rows


def validate_course_room(
    db: Session, courses: list[CourseSession], room_business_id: str | None
) -> None:
    if room_business_id is None:
        return
    campus_ids = {item.campus_id for item in courses}
    matched_campuses = set(
        db.scalars(
            select(Room.campus_id).where(
                Room.business_id == room_business_id,
                Room.campus_id.in_(campus_ids),
            )
        )
    )
    if matched_campuses != campus_ids:
        raise HTTPException(status_code=422, detail="指定教室不属于所选课程的校区")


def ensure_course_sessions_deletable(db: Session, courses: list[CourseSession]) -> None:
    course_ids = [item.id for item in courses]
    referenced_ids = set(
        db.scalars(
            select(ScheduleAssignment.course_session_id).where(
                ScheduleAssignment.course_session_id.in_(course_ids)
            )
        )
    )
    referenced_ids.update(
        db.scalars(
            select(CalendarEventBinding.course_session_id).where(
                CalendarEventBinding.course_session_id.in_(course_ids)
            )
        )
    )
    if referenced_ids:
        labels = [item.business_id for item in courses if item.id in referenced_ids]
        preview = "、".join(labels[:5])
        suffix = "等" if len(labels) > 5 else ""
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(labels)} 条课程已被课表版本或飞书日程引用，"
                f"不能直接删除：{preview}{suffix}"
            ),
        )


@router.post(
    "/course-sessions/batch-update",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_update_course_sessions(
    payload: CourseSessionBatchUpdate, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    courses = selected_course_sessions(db, payload.object_ids)
    changes = payload.model_dump(exclude={"object_ids"}, exclude_unset=True)
    if "original_room_business_id" in changes:
        validate_course_room(db, courses, changes["original_room_business_id"])
    for course in courses:
        for key, value in changes.items():
            setattr(course, key, value)
    audit(
        db,
        user,
        "batch_update",
        "course_session",
        None,
        {"count": len(courses), "fields": sorted(changes)},
    )
    db.commit()
    return BatchOperationResponse(affected_count=len(courses))


@router.post(
    "/course-sessions/batch-delete",
    response_model=BatchOperationResponse,
    tags=["master-data"],
)
def batch_delete_course_sessions(
    payload: CourseSessionBatchDelete, db: Db, user: AdminOrScheduler
) -> BatchOperationResponse:
    courses = selected_course_sessions(db, payload.object_ids)
    ensure_course_sessions_deletable(db, courses)
    for course in courses:
        db.delete(course)
    audit(db, user, "batch_delete", "course_session", None, {"count": len(courses)})
    db.commit()
    return BatchOperationResponse(affected_count=len(courses))


MASTER_MODELS = {
    "teachers": Teacher,
    "class-groups": ClassGroup,
    "rooms": Room,
    "time-slots": TimeSlot,
    "course-sessions": CourseSession,
}


@router.delete("/master-data/{resource}/{object_id}", status_code=204, tags=["master-data"])
def delete_master_data(resource: str, object_id: str, db: Db, user: AdminOrScheduler) -> Response:
    model = MASTER_MODELS.get(resource)
    if model is None:
        raise HTTPException(status_code=404, detail="未知主数据资源")
    instance = get_or_404(db, model, object_id)
    if resource == "teachers":
        ensure_teachers_deletable(db, [instance])
    elif resource == "class-groups":
        ensure_class_groups_deletable(db, [instance])
    elif resource == "rooms":
        ensure_rooms_deletable(db, [instance])
    elif resource == "time-slots":
        ensure_time_slots_deletable(db, [instance])
    elif resource == "course-sessions":
        ensure_course_sessions_deletable(db, [instance])
    db.delete(instance)
    audit(db, user, "delete", resource, object_id)
    db.commit()
    return Response(status_code=204)


RULE_CONSTRAINTS: dict[str, dict[str, Any]] = {
    "declared_constraint": {"label": "制度声明", "scope": [], "hardness": ["hard", "soft"]},
    "不重叠": {"label": "不重叠", "scope": [], "hardness": ["hard", "soft"]},
    "容量": {"label": "容量", "scope": [], "hardness": ["hard", "soft"]},
    "设备": {"label": "设备", "scope": [], "hardness": ["hard", "soft"]},
    "可用性": {"label": "可用性", "scope": [], "hardness": ["hard", "soft"]},
    "偏好": {"label": "偏好", "scope": [], "hardness": ["soft"]},
    "稳定性": {"label": "稳定性", "scope": [], "hardness": ["soft"]},
    "fixed_slot": {"label": "固定时段", "scope": ["slot_id"], "hardness": ["hard", "soft"]},
    "forbidden_slot": {
        "label": "禁排时段",
        "scope": ["slot_ids"],
        "hardness": ["hard", "soft"],
    },
    "unavailable_slot": {
        "label": "不可用时段",
        "scope": ["slot_ids"],
        "hardness": ["hard", "soft"],
    },
    "fixed_room": {"label": "固定教室", "scope": ["room_id"], "hardness": ["hard", "soft"]},
    "preferred_slot": {"label": "偏好时段", "scope": ["slot_ids"], "hardness": ["soft"]},
    "consecutive_sessions": {
        "label": "连续课次",
        "scope": ["minimum_consecutive"],
        "hardness": ["soft"],
    },
}


def validate_rule_entities(db: Session, payload: RuleCreate | RuleUpdate) -> None:
    catalog = RULE_CONSTRAINTS.get(payload.constraint_type)
    if catalog is None:
        raise HTTPException(status_code=422, detail="不支持的约束类型")
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
    model_by_actor = {
        "teacher": Teacher,
        "class": ClassGroup,
        "room": Room,
        "course": CourseSession,
    }
    model: Any = model_by_actor.get(payload.actor_type)
    if model is None or not payload.actor_ids:
        return
    existing = set(
        db.scalars(select(model.business_id).where(model.business_id.in_(payload.actor_ids))).all()
    )
    missing = sorted(set(payload.actor_ids) - existing)
    if missing:
        raise HTTPException(status_code=422, detail=f"规则引用了不存在的实体: {', '.join(missing)}")

    slot_ids = set(payload.scope.get("slot_ids") or [])
    if payload.scope.get("slot_id"):
        slot_ids.add(str(payload.scope["slot_id"]))
    if slot_ids:
        existing_slots = set(
            db.scalars(select(TimeSlot.business_id).where(TimeSlot.business_id.in_(slot_ids))).all()
        )
        missing_slots = sorted(slot_ids - existing_slots)
        if missing_slots:
            raise HTTPException(
                status_code=422, detail=f"规则引用了不存在的时段: {', '.join(missing_slots)}"
            )
    room_id = payload.scope.get("room_id")
    if room_id and db.scalar(select(Room.id).where(Room.business_id == room_id)) is None:
        raise HTTPException(status_code=422, detail=f"规则引用了不存在的教室: {room_id}")


@router.get("/rules", response_model=list[RuleResponse], tags=["rules"])
def list_rules(
    db: Db, user: CurrentUser, rule_status: str | None = Query(default=None, alias="status")
) -> list[Rule]:
    statement = select(Rule).order_by(Rule.business_id)
    if rule_status:
        statement = statement.where(Rule.status == rule_status)
    return list(db.scalars(statement))


@router.post("/rules", response_model=RuleResponse, status_code=201, tags=["rules"])
def create_rule(payload: RuleCreate, db: Db, user: AdminOrScheduler) -> Rule:
    validate_rule_entities(db, payload)
    business_id = (
        payload.business_id or f"RL-{int(db.scalar(select(func.count(Rule.id))) or 0) + 1:04d}"
    )
    data = payload.model_dump(exclude={"business_id"})
    instance = Rule(business_id=business_id, **data)
    db.add(instance)
    audit(db, user, "create", "rule", business_id, data)
    db.commit()
    db.refresh(instance)
    return instance


@router.put("/rules/{rule_id}", response_model=RuleResponse, tags=["rules"])
def update_rule(rule_id: str, payload: RuleUpdate, db: Db, user: AdminOrScheduler) -> Rule:
    rule = get_or_404(db, Rule, rule_id)
    if rule.status not in {"draft", "awaiting_confirmation"}:
        raise HTTPException(status_code=409, detail="只有待确认规则可以编辑")
    validate_rule_entities(db, payload)
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    rule.version += 1
    audit(db, user, "update", "rule", rule.business_id, payload.model_dump())
    db.commit()
    db.refresh(rule)
    return rule


@router.post("/rules/{rule_id}/transition", response_model=RuleResponse, tags=["rules"])
def transition_rule(rule_id: str, payload: RuleTransition, db: Db, user: AdminOrScheduler) -> Rule:
    rule = get_or_404(db, Rule, rule_id)
    allowed = {
        "draft": {"active", "rejected"},
        "awaiting_confirmation": {"active", "rejected"},
        "active": {"retired"},
        "rejected": set(),
        "retired": set(),
    }
    if payload.status not in allowed.get(rule.status, set()):
        raise HTTPException(status_code=409, detail=f"不允许从 {rule.status} 转为 {payload.status}")
    rule.status = payload.status
    rule.version += 1
    if payload.status == "active":
        rule.approved_by = user.id
    audit(db, user, "transition", "rule", rule.business_id, payload.model_dump())
    db.commit()
    db.refresh(rule)
    return rule


def create_solver_run(
    db: Session,
    user_id: str | None,
    request: SolveRequest | AilySolveRequest,
    run_type: str = "initial",
    extra: dict[str, Any] | None = None,
) -> SolverRun:
    snapshot = create_snapshot(db, user_id)
    payload = {
        "time_limit_seconds": request.time_limit_seconds,
        "preference_weight": getattr(request, "preference_weight", 100),
        "seat_waste_weight": getattr(request, "seat_waste_weight", 1),
        "change_weight": getattr(request, "change_weight", 100000),
        "random_seed": settings.solver_random_seed,
        "business_lines": request.business_lines,
        "product_types": request.product_types,
        "class_business_ids": request.class_business_ids,
        "date_from": request.date_from.isoformat() if request.date_from else None,
        "date_to": request.date_to.isoformat() if request.date_to else None,
        "date_window_days": request.date_window_days,
        "solver_rules": request.solver_rules,
    }
    if isinstance(request, AilySolveRequest):
        payload["instruction"] = request.instruction
    run_extra = dict(extra or {})
    is_partial_scope = bool(
        request.business_lines
        or request.product_types
        or request.class_business_ids
        or request.date_from
        or request.date_to
    )
    if is_partial_scope and "parent_schedule_id" not in run_extra:
        parent = db.scalar(
            select(ScheduleVersion)
            .where(ScheduleVersion.status == "published")
            .order_by(ScheduleVersion.version_no.desc())
        )
        if parent:
            parent_response = schedule_response(db, parent)
            run_extra["parent_schedule_id"] = parent.id
            run_extra["previous_assignments"] = [
                item.model_dump(mode="json") for item in parent_response.assignments
            ]
    payload.update(run_extra)
    run = SolverRun(
        snapshot_id=snapshot.id,
        run_type=run_type,
        status="queued",
        request_payload=payload,
        created_by=user_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.post("/solver-runs", response_model=SolverRunResponse, status_code=202, tags=["solver"])
def submit_solver_run(request: SolveRequest, db: Db, user: AdminOrScheduler) -> SolverRun:
    run = create_solver_run(db, user.id, request)
    audit(db, user, "submit", "solver_run", run.id)
    db.commit()
    if request.wait:
        execute_solver_run(run.id)
    else:
        enqueue_solver_run(run.id)
    db.refresh(run)
    return run


@router.get("/solver-runs", response_model=list[SolverRunResponse], tags=["solver"])
def list_solver_runs(db: Db, user: CurrentUser) -> list[SolverRun]:
    return list(db.scalars(select(SolverRun).order_by(SolverRun.created_at.desc()).limit(50)))


@router.get("/solver-runs/{run_id}", response_model=SolverRunResponse, tags=["solver"])
def get_solver_run(run_id: str, db: Db, user: CurrentUser) -> SolverRun:
    return get_or_404(db, SolverRun, run_id)


@router.get("/solver-runs/{run_id}/events", tags=["solver"])
async def solver_run_events(run_id: str, user: CurrentUser) -> StreamingResponse:
    async def event_stream():
        last_payload = ""
        while True:
            from .db import SessionLocal

            with SessionLocal() as db:
                run = db.get(SolverRun, run_id)
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


@router.get("/schedules", response_model=list[ScheduleResponse], tags=["schedules"])
def list_schedules(db: Db, user: CurrentUser) -> list[ScheduleResponse]:
    versions = list(db.scalars(select(ScheduleVersion).order_by(ScheduleVersion.version_no.desc())))
    return [schedule_response(db, item) for item in versions]


@router.get("/schedules/{schedule_id}", response_model=ScheduleResponse, tags=["schedules"])
def get_schedule(schedule_id: str, db: Db, user: CurrentUser) -> ScheduleResponse:
    return schedule_response(db, get_or_404(db, ScheduleVersion, schedule_id))


@router.get(
    "/schedules/{schedule_id}/diff/{target_schedule_id}",
    response_model=ScheduleDiffResponse,
    tags=["schedules"],
)
def diff_schedules(
    schedule_id: str, target_schedule_id: str, db: Db, user: CurrentUser
) -> ScheduleDiffResponse:
    base = schedule_response(db, get_or_404(db, ScheduleVersion, schedule_id))
    target = schedule_response(db, get_or_404(db, ScheduleVersion, target_schedule_id))
    before = {item.course_business_id: item for item in base.assignments}
    after = {item.course_business_id: item for item in target.assignments}
    items: list[ScheduleDiffItem] = []
    for course_id in sorted(set(before) | set(after)):
        old = before.get(course_id)
        new = after.get(course_id)
        if old is None:
            kind = "added"
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
def publish_schedule(schedule_id: str, db: Db, user: Approver) -> ScheduleResponse:
    schedule = get_or_404(db, ScheduleVersion, schedule_id)
    if schedule.status != "draft":
        raise HTTPException(status_code=409, detail="只有草稿版本可以发布")
    for published in db.scalars(
        select(ScheduleVersion).where(ScheduleVersion.status == "published")
    ):
        published.status = "archived"
    schedule.status = "published"
    schedule.approved_by = user.id
    schedule.published_at = utcnow()
    audit(db, user, "publish", "schedule", schedule.id)
    db.commit()
    db.refresh(schedule)
    return schedule_response(db, schedule)


@router.post(
    "/schedules/{schedule_id}/rollback", response_model=ScheduleResponse, tags=["schedules"]
)
def rollback_schedule(schedule_id: str, db: Db, user: Approver) -> ScheduleResponse:
    target = get_or_404(db, ScheduleVersion, schedule_id)
    if target.status not in {"archived", "rolled_back"}:
        raise HTTPException(status_code=409, detail="只能回滚到已经发布过的历史版本")
    for published in db.scalars(
        select(ScheduleVersion).where(ScheduleVersion.status == "published")
    ):
        published.status = "rolled_back"
    target.status = "published"
    target.approved_by = user.id
    target.published_at = utcnow()
    audit(db, user, "rollback", "schedule", target.id)
    db.commit()
    db.refresh(target)
    return schedule_response(db, target)


@router.get("/schedules/{schedule_id}/export.xlsx", tags=["schedules"])
def export_schedule(schedule_id: str, db: Db, user: CurrentUser) -> Response:
    schedule = get_or_404(db, ScheduleVersion, schedule_id)
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
    schedule_id: str, db: Db, user: CurrentUser
) -> list[CalendarEventBinding]:
    get_or_404(db, ScheduleVersion, schedule_id)
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
) -> CalendarPublishResponse:
    schedule = get_or_404(db, ScheduleVersion, schedule_id)
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
                CourseSession.id.in_([item.course_session_id for item in assignments])
            )
        )
    }
    teacher_ids = {item.teacher_business_id for item in courses.values()}
    teachers = {
        item.business_id: item
        for item in db.scalars(select(Teacher).where(Teacher.business_id.in_(teacher_ids)))
    }
    rooms = {item.business_id: item for item in db.scalars(select(Room))}
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
        if not calendar_user_id or not course.fixed_start_time or not course.fixed_end_time:
            skipped_unmapped += 1
            continue
        try:
            start = datetime.combine(
                assignment.lesson_date, _parse_clock(course.fixed_start_time), SHANGHAI_TZ
            )
            end = datetime.combine(
                assignment.lesson_date, _parse_clock(course.fixed_end_time), SHANGHAI_TZ
            )
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

    conflicts = []
    conflicted_course_ids: set[str] = set()
    for assignment, course, calendar_user_id, start, end in publishable:
        if any(
            _intervals_overlap(start, end, interval)
            for interval in busy_by_user.get(calendar_user_id, [])
        ):
            conflicted_course_ids.add(course.id)
            conflicts.append(
                {
                    "course_session_id": course.id,
                    "calendar_user_id": calendar_user_id,
                    "lesson_date": assignment.lesson_date,
                    "start_time": course.fixed_start_time,
                    "end_time": course.fixed_end_time,
                    "source": "feishu_freebusy",
                }
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
                        {
                            "course_session_id": course.id,
                            "calendar_user_id": calendar_user_id,
                            "lesson_date": assignment.lesson_date,
                            "start_time": course.fixed_start_time,
                            "end_time": course.fixed_end_time,
                            "source": "schedule_overlap",
                        }
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
                                assignment.lesson_date, course.fixed_start_time
                            ),
                            "timezone": "Asia/Shanghai",
                        },
                        "end_time": {
                            "timestamp": _timestamp_epoch(
                                assignment.lesson_date, course.fixed_end_time
                            ),
                            "timezone": "Asia/Shanghai",
                        },
                        "visibility": "private",
                        "free_busy_status": "busy",
                        "location": {
                            "name": room.name if room else assignment.room_business_id
                        },
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


@router.get("/reschedule-events", response_model=list[RescheduleResponse], tags=["reschedule"])
def list_reschedule_events(db: Db, user: CurrentUser) -> list[RescheduleEvent]:
    return list(db.scalars(select(RescheduleEvent).order_by(RescheduleEvent.created_at.desc())))


@router.post(
    "/reschedule-events", response_model=RescheduleResponse, status_code=202, tags=["reschedule"]
)
def create_reschedule_event(
    request: RescheduleCreate, db: Db, user: AdminOrScheduler
) -> RescheduleEvent:
    parent = get_or_404(db, ScheduleVersion, request.parent_schedule_id)
    parent_response = schedule_response(db, parent)
    event_payload = request.model_dump(
        exclude={"parent_schedule_id", "description", "time_limit_seconds"}
    )
    event = RescheduleEvent(
        event_type=request.event_type,
        description=request.description,
        payload=event_payload,
        status="pending",
        parent_schedule_id=parent.id,
        created_by=user.id,
    )
    db.add(event)
    db.flush()
    run = create_solver_run(
        db,
        user.id,
        AilySolveRequest(time_limit_seconds=request.time_limit_seconds),
        run_type="reschedule",
        extra={
            "parent_schedule_id": parent.id,
            "event": event_payload,
            "previous_assignments": [item.model_dump() for item in parent_response.assignments],
            "change_weight": 100000,
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
    db: Db, user: CurrentUser, limit: int = Query(100, ge=1, le=500)
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
    "/integrations/feishu/connection",
    response_model=FeishuConnectionResponse,
    tags=["integrations"],
)
def feishu_connection(db: Db, user: CurrentUser) -> dict[str, Any]:
    return FeishuService(settings, db).connection_view(user.id)


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
    query = urlencode({"feishu": "connected"})
    return RedirectResponse(f"{frontend}/integrations?{query}")


@router.post(
    "/integrations/feishu/workspaces",
    response_model=FeishuWorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["integrations"],
)
def create_feishu_workspace(request: FeishuWorkspaceCreate, db: Db, user: Admin) -> dict[str, Any]:
    service = FeishuService(settings, db)
    try:
        workspace = service.create_workspace(user.id, request.name)
    except FeishuServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit(db, user, "create", "feishu_workspace", workspace.id, {"name": workspace.name})
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


def export_resource_rows(db: Session, resource: str) -> list[dict[str, Any]]:
    if resource == "teachers":
        return [
            {
                "业务标识": item.business_id,
                "教师名称": item.name,
                "学科": item.subject,
                "飞书用户标识": item.calendar_user_id or "",
            }
            for item in db.scalars(select(Teacher).order_by(Teacher.business_id))
        ]
    if resource == "class_groups":
        return [
            {
                "业务标识": item.business_id,
                "班级名称": item.name,
                "班型": item.grade,
                "业务线": item.subject,
                "教师标识": item.teacher_business_id,
            }
            for item in db.scalars(select(ClassGroup).order_by(ClassGroup.business_id))
        ]
    if resource == "rooms":
        return [
            {
                "业务标识": item.business_id,
                "教室名称": item.name,
                "是否启用": "是" if item.is_active else "否",
            }
            for item in db.scalars(select(Room).order_by(Room.business_id))
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
            for item in db.scalars(select(TimeSlot).order_by(TimeSlot.sequence))
        ]
    if resource == "course_sessions":
        teachers = {item.business_id: item for item in db.scalars(select(Teacher))}
        return [
            {
                "业务标识": item.business_id,
                "业务线": item.business_line,
                "产品班型": item.product_type,
                "班级标识": item.class_business_id,
                "教师标识": item.teacher_business_id,
                "具体日程账号": item.calendar_user_id
                or (
                    teachers[item.teacher_business_id].calendar_user_id
                    if item.teacher_business_id in teachers
                    else ""
                )
                or "",
                "学科": item.subject,
                "课节名称": item.lesson_name,
                "编排来源": item.schedule_source,
                "编排阶段": item.stage,
                "计划课次": item.planned_sessions,
                "计划课时": item.planned_hours,
                "课次序号": item.session_no,
                "上课日期": item.lesson_date.isoformat() if item.lesson_date else "",
                "时长分钟": item.duration_minutes,
                "建议时段": item.suggested_slot_id or "",
                "固定开始时间": item.fixed_start_time,
                "固定结束时间": item.fixed_end_time,
                "原始教室标识": item.original_room_business_id or "",
                "是否锁定": "是" if item.is_locked else "否",
            }
            for item in db.scalars(select(CourseSession).order_by(CourseSession.business_id))
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
            for item in db.scalars(select(Rule).order_by(Rule.business_id))
        ]
    if resource == "schedule":
        versions = list(
            db.scalars(
                select(ScheduleVersion)
                .where(ScheduleVersion.published_at.is_not(None))
                .order_by(ScheduleVersion.version_no)
            )
        )
        sessions = {item.id: item for item in db.scalars(select(CourseSession))}
        teachers = {item.business_id: item for item in db.scalars(select(Teacher))}
        slots = {item.business_id: item for item in db.scalars(select(TimeSlot))}
        rooms = {item.business_id: item for item in db.scalars(select(Room))}
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
                        "业务标识": f"{version.id}:{session.business_id}",
                        "版本标识": version.id,
                        "版本号": version.version_no,
                        "版本名称": version.name,
                        "是否当前版本": "是" if version.status == "published" else "否",
                        "发布状态": status_labels.get(version.status, version.status),
                        "场次标识": session.business_id,
                        "业务线": session.business_line,
                        "产品班型": session.product_type,
                        "班级标识": session.class_business_id,
                        "教师标识": session.teacher_business_id,
                        "具体日程账号": session.calendar_user_id
                        or (
                            teachers[session.teacher_business_id].calendar_user_id
                            if session.teacher_business_id in teachers
                            else ""
                        )
                        or "",
                        "学科": session.subject,
                        "上课日期": assignment.lesson_date.isoformat()
                        if assignment.lesson_date
                        else "",
                        "时段标识": assignment.slot_business_id,
                        "星期": slot.weekday if slot else "",
                        "开始时间": session.fixed_start_time,
                        "结束时间": session.fixed_end_time,
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
        return rows
    if resource == "public_summary":
        summary = _public_summary(db)
        updated_at = utcnow().isoformat()
        public_rows: list[dict[str, Any]] = [
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
    return []


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
def feishu_sync(request: FeishuSyncRequest, db: Db, user: AdminOrScheduler) -> IntegrationSync:
    sync = IntegrationSync(
        direction=request.direction,
        resource=request.resource,
        status="running",
        mode="live",
    )
    db.add(sync)
    db.flush()
    try:
        rows = export_resource_rows(db, request.resource)
        result = FeishuService(settings, db).sync_rows(
            user.id,
            request.resource,
            rows,
            request.workspace_id,
        )
        sync.records_read = int(result["records_read"])
        sync.records_written = int(result["records_written"])
        sync.detail = result
        sync.status = "completed"
    except (FeishuServiceError, httpx.HTTPError) as exc:
        sync.status = "failed"
        sync.detail = {"error": str(exc)}
        audit(db, user, "sync", "feishu", sync.id, sync.detail)
        db.commit()
        code = (
            status.HTTP_409_CONFLICT
            if isinstance(exc, FeishuServiceError)
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    audit(db, user, "sync", "feishu", sync.id, sync.detail)
    db.commit()
    db.refresh(sync)
    return sync


@router.get(
    "/integrations/feishu/syncs",
    response_model=list[IntegrationSyncResponse],
    tags=["integrations"],
)
def list_feishu_syncs(db: Db, user: CurrentUser) -> list[IntegrationSync]:
    return list(
        db.scalars(select(IntegrationSync).order_by(IntegrationSync.created_at.desc()).limit(50))
    )


def require_aily_key(x_aily_key: Annotated[str, Header()]) -> None:
    if x_aily_key != settings.aily_skill_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Aily 集成密钥无效")


@router.get(
    "/aily/context",
    response_model=AilyContextResponse,
    tags=["aily"],
    dependencies=[Depends(require_aily_key)],
)
def aily_context(db: Db) -> AilyContextResponse:
    teachers = {item.business_id: item for item in db.scalars(select(Teacher))}

    def course_context(item: CourseSession) -> dict[str, Any]:
        teacher = teachers.get(item.teacher_business_id)
        calendar_user_id = item.calendar_user_id or (
            teacher.calendar_user_id if teacher else None
        )
        return {
            "business_id": item.business_id,
            "business_line": item.business_line,
            "product_type": item.product_type,
            "class_business_id": item.class_business_id,
            "teacher_business_id": item.teacher_business_id,
            "calendar_user_id": calendar_user_id,
            "calendar_mapping_status": "mapped" if calendar_user_id else "unmapped",
            "subject": item.subject,
            "lesson_date": item.lesson_date.isoformat() if item.lesson_date else None,
            "fixed_start_time": item.fixed_start_time,
            "fixed_end_time": item.fixed_end_time,
            "original_room_business_id": item.original_room_business_id,
        }

    return AilyContextResponse(
        entities={
            "teachers": [
                {"business_id": item.business_id, "name": item.name, "subject": item.subject}
                for item in db.scalars(select(Teacher).order_by(Teacher.business_id))
            ],
            "classes": [
                {"business_id": item.business_id, "name": item.name, "grade": item.grade}
                for item in db.scalars(select(ClassGroup).order_by(ClassGroup.business_id))
            ],
            "rooms": [
                {
                    "business_id": item.business_id,
                    "name": item.name,
                }
                for item in db.scalars(select(Room).order_by(Room.business_id))
            ],
            "courses": [
                course_context(item)
                for item in db.scalars(select(CourseSession).order_by(CourseSession.business_id))
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
                for item in db.scalars(select(TimeSlot).order_by(TimeSlot.sequence))
            ],
        },
        constraint_catalog=[{"type": key, **value} for key, value in RULE_CONSTRAINTS.items()],
        output_contract={
            "status": "awaiting_confirmation",
            "hard_rule_policy": "硬约束必须由教务人工确认后生效",
            "entity_policy": "actor_ids 和 scope 中的业务 ID 必须来自 entities",
            "batch_endpoint": "/api/v1/aily/rule-proposals",
            "solve_endpoint": "/api/v1/assistant/solve",
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
def aily_rule_proposals(batch: AilyRuleBatch, db: Db) -> list[Rule]:
    created: list[Rule] = []
    base_number = int(db.scalar(select(func.count(Rule.id))) or 0)
    for index, proposal in enumerate(batch.proposals, start=1):
        validate_rule_entities(db, proposal)
        business_id = proposal.business_id or f"AILY-{base_number + index:04d}"
        data = proposal.model_dump(exclude={"business_id"})
        data["source_text"] = proposal.source_text or batch.source_text
        data["source_doc"] = proposal.source_doc or batch.source_doc
        data["status"] = "awaiting_confirmation"
        rule = Rule(business_id=business_id, **data)
        db.add(rule)
        created.append(rule)
    audit(db, None, "propose", "aily_rule_batch", None, {"source_text": batch.source_text})
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
def aily_solve(request: AilySolveRequest, db: Db) -> SolverRun:
    run = create_solver_run(db, None, request)
    enqueue_solver_run(run.id)
    return run


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


SOLVER_RULE_LABELS = {
    "fixed_time": "固定时段不可调整",
    "room_no_overlap": "同一教室真实时间区间不可重叠",
    "calendar_no_overlap": "同一具体日程账号不可重叠",
    "minimize_changes": "优先最小化日期和教室变更",
}


def _solver_rules_from_labels(labels: list[str]) -> list[str]:
    matched = [key for key, label in SOLVER_RULE_LABELS.items() if label in labels]
    # 固定时段及两类资源冲突是企业确认的基础硬约束，不能因模型漏字段而消失。
    mandatory = ["fixed_time", "room_no_overlap", "calendar_no_overlap"]
    return list(dict.fromkeys([*mandatory, *matched]))


def _validated_assistant_scope(db: Session, parsed: dict[str, Any]) -> dict[str, Any]:
    available = {
        "business_lines": {
            item for item in db.scalars(select(CourseSession.business_line)).all() if item
        },
        "product_types": {
            item for item in db.scalars(select(CourseSession.product_type)).all() if item
        },
        "class_business_ids": set(db.scalars(select(CourseSession.class_business_id)).all()),
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


@router.post(
    "/assistant/interpret",
    response_model=AssistantInterpretResponse,
    tags=["aily", "assistant"],
)
def assistant_interpret(
    request: AssistantInterpretRequest, db: Db, user: AdminOrScheduler
) -> AssistantInterpretResponse:
    configuration = FeishuService(settings, db).configuration_view()
    aily_app_id = settings.aily_app_id or configuration.get("aily_app_id")
    aily_skill_id = settings.aily_skill_id or configuration.get("aily_skill_id")
    if not aily_app_id or not aily_skill_id:
        raise HTTPException(
            status_code=409,
            detail="尚未配置飞书 Aily，请先前往“飞书集成”填写 Aily 应用标识和技能标识。",
        )
    try:
        aily_output = FeishuService(settings, db).start_aily_skill(
            user.id,
            app_id=str(aily_app_id),
            skill_id=str(aily_skill_id),
            query=request.instruction,
            input_payload={
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
            },
        )
    except (FeishuServiceError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"飞书 Aily 解析失败：{exc}") from exc
    required_fields = {
        "business_lines",
        "product_types",
        "class_business_ids",
        "date_from",
        "date_to",
        "date_window_days",
        "recognized_rules",
    }
    missing_fields = sorted(required_fields - set(aily_output))
    if missing_fields:
        raise HTTPException(
            status_code=502,
            detail=f"飞书 Aily 输出缺少字段：{', '.join(missing_fields)}",
        )
    parsed = {
        "business_lines": _string_list(aily_output["business_lines"]),
        "product_types": _string_list(aily_output["product_types"]),
        "class_business_ids": _string_list(aily_output["class_business_ids"]),
        "date_from": aily_output["date_from"],
        "date_to": aily_output["date_to"],
        "date_window_days": aily_output["date_window_days"],
        "recognized_rules": _string_list(aily_output["recognized_rules"]),
    }
    parsed["solver_rules"] = _solver_rules_from_labels(parsed["recognized_rules"])
    try:
        parsed = _validated_assistant_scope(db, parsed)
        normalized = AssistantInterpretResponse(
            instruction=request.instruction,
            source="feishu_aily",
            aily_configured=True,
            **parsed,
            summary="已解析排课范围和固定业务规则，请教务确认后启动 CP-SAT 求解。",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Aily 返回结构不合法：{exc}") from exc
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
    "/assistant/solve",
    response_model=SolverRunResponse,
    status_code=202,
    tags=["aily", "assistant"],
)
def assistant_solve(
    request: AssistantSolveRequest, db: Db, user: AdminOrScheduler
) -> SolverRun:
    """Login-session entry point for Aily's natural-language scheduling skill.

    Aily may call this endpoint after turning the instruction into structured
    filters/rules. The instruction is persisted for traceability, while the
    same CP-SAT path as the regular solver is used for deterministic execution.
    """
    scope = _validated_assistant_scope(db, request.model_dump())
    selected_count = db.scalar(
        select(func.count(CourseSession.id)).where(
            *(
                [CourseSession.business_line.in_(scope["business_lines"])]
                if scope["business_lines"]
                else []
            ),
            *(
                [CourseSession.product_type.in_(scope["product_types"])]
                if scope["product_types"]
                else []
            ),
            *(
                [CourseSession.class_business_id.in_(scope["class_business_ids"])]
                if scope["class_business_ids"]
                else []
            ),
            *([CourseSession.lesson_date >= request.date_from] if request.date_from else []),
            *([CourseSession.lesson_date <= request.date_to] if request.date_to else []),
        )
    )
    if not selected_count:
        raise HTTPException(status_code=422, detail="确认的排课范围没有匹配到课次")
    run = create_solver_run(db, user.id, request, extra={"assistant_entry": True})
    audit(db, user, "assistant_solve", "solver_run", run.id, {"instruction": request.instruction})
    db.commit()
    if request.wait:
        execute_solver_run(run.id)
    else:
        enqueue_solver_run(run.id)
    db.refresh(run)
    return run


def _public_summary(db: Session) -> PublicScheduleSummary:
    schedule = db.scalar(
        select(ScheduleVersion)
        .where(ScheduleVersion.status == "published")
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
    courses = {
        item.id: item
        for item in db.scalars(
            select(CourseSession).where(
                CourseSession.id.in_([item.course_session_id for item in assignments])
            )
        )
    }
    rooms = {item.business_id: item for item in db.scalars(select(Room))}
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
    preview: list[dict[str, str]] = []
    for index, assignment in enumerate(assignments[:6], start=1):
        course = courses.get(assignment.course_session_id)
        fixed_start = course.fixed_start_time if course else ""
        start_hour = _parse_clock(fixed_start).hour if fixed_start else 12
        preview.append(
            {
                "class_label": f"班级{chr(64 + index)}",
                "room_label": f"教室{chr(64 + index)}",
                "month": (
                    "月份"
                    if assignment.lesson_date is None
                    else assignment.lesson_date.strftime("%Y-%m")
                ),
                "time_period": (
                    "上午" if start_hour < 12 else ("下午" if start_hour < 18 else "晚间")
                ),
            }
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
