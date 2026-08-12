from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any
from urllib.parse import urlencode

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
    AuditLogResponse,
    CampusCreate,
    CampusResponse,
    ClassGroupPayload,
    ClassGroupResponse,
    CourseSessionPayload,
    CourseSessionResponse,
    FeishuAppConfigurationInput,
    FeishuAppConfigurationResponse,
    FeishuConnectionResponse,
    FeishuOAuthStartResponse,
    FeishuSyncRequest,
    FeishuWorkspaceCreate,
    FeishuWorkspaceResponse,
    ImportResult,
    IntegrationSyncResponse,
    OverviewResponse,
    RescheduleCreate,
    RescheduleResponse,
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
    TeacherPayload,
    TeacherResponse,
    TimeSlotPayload,
    TimeSlotResponse,
    TokenResponse,
    UserResponse,
)
from .security import create_access_token, get_current_user, require_roles, verify_password
from .services.converter_zhengzhou import import_schedule_workbook
from .services.feishu import FeishuService, FeishuServiceError, json_text
from .services.snapshot import create_snapshot
from .services.tasks import enqueue_solver_run, execute_solver_run
from .services.xlsx_io import export_schedule_xlsx

logger = logging.getLogger("tupai.feishu")

settings = get_settings()
router = APIRouter(prefix=settings.api_prefix)
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
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return TokenResponse(
        access_token=create_access_token(user), user=UserResponse.model_validate(user)
    )


@router.get("/auth/me", response_model=UserResponse, tags=["auth"])
def current_user(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


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
    object_id: str, payload: CourseSessionPayload, db: Db, user: AdminOrScheduler
) -> CourseSession:
    instance = get_or_404(db, CourseSession, object_id)
    for key, value in payload.model_dump().items():
        setattr(instance, key, value)
    audit(db, user, "update", "course_session", object_id)
    db.commit()
    db.refresh(instance)
    return instance


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
    }
    payload.update(extra or {})
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
        elif (old.slot_business_id, old.room_business_id) != (
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
                before_slot_id=old.slot_business_id if old else None,
                before_room_id=old.room_business_id if old else None,
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
        return [
            {
                "业务标识": item.business_id,
                "班级标识": item.class_business_id,
                "教师标识": item.teacher_business_id,
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
                        "班级标识": session.class_business_id,
                        "教师标识": session.teacher_business_id,
                        "学科": session.subject,
                        "时段标识": assignment.slot_business_id,
                        "星期": slot.weekday if slot else "",
                        "开始时间": slot.start_time if slot else "",
                        "结束时间": slot.end_time if slot else "",
                        "教室标识": assignment.room_business_id,
                        "教室名称": room.name if room else "",
                        "变更类型": change_labels.get(
                            assignment.change_kind, assignment.change_kind
                        ),
                    }
                )
        return rows
    return []


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
                {
                    "business_id": item.business_id,
                    "class_business_id": item.class_business_id,
                    "teacher_business_id": item.teacher_business_id,
                    "subject": item.subject,
                }
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
