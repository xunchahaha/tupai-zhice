import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ColumnDef, type RowSelectionState } from "@tanstack/react-table";
import { CalendarDays, Download, FileUp, Pencil, Plus, RefreshCw, Search, Trash2, X } from "lucide-react";
import { type Dispatch, type FormEvent, type SetStateAction, useEffect, useMemo, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { toast } from "sonner";

import {
  getListCampusesApiV1CampusesGetQueryKey,
  getListClassGroupsApiV1ClassGroupsGetQueryKey,
  getListCourseSessionsApiV1CourseSessionsGetQueryKey,
  getListRoomsApiV1RoomsGetQueryKey,
  getListTeachersApiV1TeachersGetQueryKey,
  getListTimeSlotsApiV1TimeSlotsGetQueryKey,
  useCreateClassGroupApiV1ClassGroupsPost,
  useCreateCourseSessionApiV1CourseSessionsPost,
  useCreateRoomApiV1RoomsPost,
  useCreateTeacherApiV1TeachersPost,
  useCreateTimeSlotApiV1TimeSlotsPost,
  useDeleteMasterDataApiV1MasterDataResourceObjectIdDelete,
  useImportXlsxApiV1ImportsXlsxPost,
  useListCampusesApiV1CampusesGet,
  useListClassGroupsApiV1ClassGroupsGet,
  useListCourseSessionsApiV1CourseSessionsGet,
  useListRoomsApiV1RoomsGet,
  useListTeachersApiV1TeachersGet,
  useListTimeSlotsApiV1TimeSlotsGet,
  useUpdateCourseSessionApiV1CourseSessionsObjectIdPut,
  useUpdateClassGroupApiV1ClassGroupsObjectIdPut,
  useUpdateRoomApiV1RoomsObjectIdPut,
  useUpdateTeacherApiV1TeachersObjectIdPut,
  useUpdateTimeSlotApiV1TimeSlotsObjectIdPut,
} from "@/api/generated/client";
import {
  type ClassGroupResponse,
  type CourseSessionPayload,
  type CourseSessionResponse,
  type RoomResponse,
  type TeacherResponse,
  type TimeSlotResponse,
  type UserResponse,
} from "@/api/generated/models";
import { http } from "@/api/http";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DataTable } from "@/components/data-table";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { errorMessage } from "@/lib/format";

type MasterResource = "teachers" | "class-groups" | "rooms" | "time-slots" | "course-sessions";
type EntityKind = "teacher" | "class" | "room" | "slot";
type EntityDialogMode = "create" | "edit";
type CourseDialogMode = "create" | "edit";
type CourseBatchAction = "date" | "room" | "delete";
type EntityBatchAction = "teacher-subject" | "teacher-calendar" | "class-line" | "class-type" | "class-teacher" | "room-status" | "slot-status" | "delete";

interface DeleteTarget {
  id: string;
  label: string;
  resource: MasterResource;
  resourceLabel: string;
}

interface EntityDialogState {
  kind: EntityKind;
  mode: EntityDialogMode;
  id?: string;
}

interface EntityBatchState {
  kind: EntityKind;
  action: EntityBatchAction;
}

interface CourseDraft {
  id: string;
  campusId: string;
  businessId: string;
  classBusinessId: string;
  teacherBusinessId: string;
  businessLine: string;
  productType: string;
  subject: string;
  lessonName: string;
  scheduleSource: string;
  stage: string;
  plannedSessions: string;
  plannedHours: string;
  sessionNo: string;
  lessonDate: string;
  durationMinutes: string;
  fixedStartTime: string;
  fixedEndTime: string;
  roomBusinessId: string;
  calendarUserId: string;
  isLocked: boolean;
}

interface BatchOperationResult {
  affected_count: number;
}

interface CourseBatchUpdatePayload {
  object_ids: string[];
  lesson_date?: string;
  original_room_business_id?: string;
}

interface SlotDraft {
  businessId: string;
  weekday: string;
  startTime: string;
  endTime: string;
  kind: string;
  sequence: string;
  isOpen: boolean;
}

const inputClass = "mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm outline-none focus:border-blue-500";
const compactInputClass = "h-8 rounded-md border border-zinc-300 bg-white px-2 text-xs outline-none focus:border-blue-500";

function emptyCourseDraft(campusId = ""): CourseDraft {
  return {
    id: "",
    campusId,
    businessId: "",
    classBusinessId: "",
    teacherBusinessId: "",
    businessLine: "",
    productType: "",
    subject: "",
    lessonName: "",
    scheduleSource: "前端手工新增",
    stage: "",
    plannedSessions: "0",
    plannedHours: "0",
    sessionNo: "1",
    lessonDate: "",
    durationMinutes: "180",
    fixedStartTime: "09:00",
    fixedEndTime: "12:00",
    roomBusinessId: "",
    calendarUserId: "",
    isLocked: false,
  };
}

function draftFromCourse(course: CourseSessionResponse): CourseDraft {
  return {
    id: course.id,
    campusId: course.campus_id,
    businessId: course.business_id,
    classBusinessId: course.class_business_id,
    teacherBusinessId: course.teacher_business_id,
    businessLine: course.business_line ?? "",
    productType: course.product_type ?? "",
    subject: course.subject ?? "",
    lessonName: course.lesson_name ?? "",
    scheduleSource: course.schedule_source ?? "",
    stage: course.stage ?? "",
    plannedSessions: String(course.planned_sessions ?? 0),
    plannedHours: String(course.planned_hours ?? 0),
    sessionNo: String(course.session_no ?? 0),
    lessonDate: course.lesson_date ?? "",
    durationMinutes: String(course.duration_minutes ?? 180),
    fixedStartTime: course.fixed_start_time ?? "",
    fixedEndTime: course.fixed_end_time ?? "",
    roomBusinessId: course.original_room_business_id ?? "",
    calendarUserId: course.calendar_user_id ?? "",
    isLocked: course.is_locked ?? false,
  };
}

export function MasterDataPage() {
  const { user } = useOutletContext<{ user: UserResponse }>();
  const readOnly = user.role === "viewer";
  const client = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [entityDialog, setEntityDialog] = useState<EntityDialogState | null>(null);
  const [courseDialogMode, setCourseDialogMode] = useState<CourseDialogMode | null>(null);
  const [courseDraft, setCourseDraft] = useState<CourseDraft>(emptyCourseDraft());
  const [teacherSelection, setTeacherSelection] = useState<RowSelectionState>({});
  const [classSelection, setClassSelection] = useState<RowSelectionState>({});
  const [roomSelection, setRoomSelection] = useState<RowSelectionState>({});
  const [slotSelection, setSlotSelection] = useState<RowSelectionState>({});
  const [courseSelection, setCourseSelection] = useState<RowSelectionState>({});
  const [teacherSearch, setTeacherSearch] = useState("");
  const [classSearch, setClassSearch] = useState("");
  const [roomSearch, setRoomSearch] = useState("");
  const [slotSearch, setSlotSearch] = useState("");
  const [courseSearch, setCourseSearch] = useState("");
  const [businessLineFilter, setBusinessLineFilter] = useState("");
  const [productTypeFilter, setProductTypeFilter] = useState("");
  const [classFilter, setClassFilter] = useState("");
  const [teacherFilter, setTeacherFilter] = useState("");
  const [dateFilter, setDateFilter] = useState("");
  const [entityBatch, setEntityBatch] = useState<EntityBatchState | null>(null);
  const [entityBatchValue, setEntityBatchValue] = useState("");
  const [batchAction, setBatchAction] = useState<CourseBatchAction | null>(null);
  const [batchDate, setBatchDate] = useState("");
  const [batchRoom, setBatchRoom] = useState("");
  const [campusId, setCampusId] = useState("");
  const [deletingKey, setDeletingKey] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [teacherDraft, setTeacherDraft] = useState({ businessId: "", name: "", subject: "", calendarUserId: "" });
  const [classDraft, setClassDraft] = useState({ businessId: "", name: "", businessLine: "", productType: "", teacherBusinessId: "" });
  const [roomDraft, setRoomDraft] = useState({ businessId: "", name: "", isActive: true });
  const [slotDraft, setSlotDraft] = useState<SlotDraft>({ businessId: "", weekday: "", startTime: "09:00", endTime: "12:00", kind: "", sequence: "0", isOpen: true });

  const campusQuery = useListCampusesApiV1CampusesGet();
  const teacherQuery = useListTeachersApiV1TeachersGet();
  const classQuery = useListClassGroupsApiV1ClassGroupsGet();
  const roomQuery = useListRoomsApiV1RoomsGet();
  const slotQuery = useListTimeSlotsApiV1TimeSlotsGet();
  const courseQuery = useListCourseSessionsApiV1CourseSessionsGet();

  useEffect(() => {
    if (!campusId && campusQuery.data?.[0]) setCampusId(campusQuery.data[0].id);
  }, [campusId, campusQuery.data]);

  const invalidate = (key: readonly unknown[]) => void client.invalidateQueries({ queryKey: key });
  const invalidateCourses = () => invalidate(getListCourseSessionsApiV1CourseSessionsGetQueryKey());
  const closeEntityDialog = () => setEntityDialog(null);
  const closeCourseDialog = () => setCourseDialogMode(null);
  const createError = (error: unknown) => toast.error(errorMessage(error));

  const entitySaved = (kind: EntityKind, action: "新增" | "更新") => {
    const config = entityConfig(kind);
    invalidate(config.queryKey);
    closeEntityDialog();
    toast.success(`${config.label}已${action}`);
  };
  const createTeacher = useCreateTeacherApiV1TeachersPost({ mutation: { onSuccess: () => entitySaved("teacher", "新增"), onError: createError } });
  const updateTeacher = useUpdateTeacherApiV1TeachersObjectIdPut({ mutation: { onSuccess: () => entitySaved("teacher", "更新"), onError: createError } });
  const createClass = useCreateClassGroupApiV1ClassGroupsPost({ mutation: { onSuccess: () => entitySaved("class", "新增"), onError: createError } });
  const updateClass = useUpdateClassGroupApiV1ClassGroupsObjectIdPut({ mutation: { onSuccess: () => entitySaved("class", "更新"), onError: createError } });
  const createRoom = useCreateRoomApiV1RoomsPost({ mutation: { onSuccess: () => entitySaved("room", "新增"), onError: createError } });
  const updateRoom = useUpdateRoomApiV1RoomsObjectIdPut({ mutation: { onSuccess: () => entitySaved("room", "更新"), onError: createError } });
  const createSlot = useCreateTimeSlotApiV1TimeSlotsPost({ mutation: { onSuccess: () => entitySaved("slot", "新增"), onError: createError } });
  const updateSlot = useUpdateTimeSlotApiV1TimeSlotsObjectIdPut({ mutation: { onSuccess: () => entitySaved("slot", "更新"), onError: createError } });
  const createCourse = useCreateCourseSessionApiV1CourseSessionsPost({ mutation: { onSuccess: () => { invalidateCourses(); closeCourseDialog(); setCourseDraft(emptyCourseDraft(campusId)); toast.success("课程场次已新增"); }, onError: createError } });
  const updateCourse = useUpdateCourseSessionApiV1CourseSessionsObjectIdPut({ mutation: { onSuccess: () => { invalidateCourses(); closeCourseDialog(); toast.success("课程日期与教室已更新"); }, onError: createError } });
  const batchUpdateCourses = useMutation({
    mutationFn: async (payload: CourseBatchUpdatePayload) => (await http.post<BatchOperationResult>("/api/v1/course-sessions/batch-update", payload)).data,
    onSuccess: (result) => { invalidateCourses(); setCourseSelection({}); setBatchAction(null); toast.success(`已批量更新 ${result.affected_count} 条课程`); },
    onError: createError,
  });
  const batchDeleteCourses = useMutation({
    mutationFn: async (objectIds: string[]) => (await http.post<BatchOperationResult>("/api/v1/course-sessions/batch-delete", { object_ids: objectIds })).data,
    onSuccess: (result) => { invalidateCourses(); setCourseSelection({}); setBatchAction(null); toast.success(`已批量删除 ${result.affected_count} 条课程`); },
    onError: createError,
  });
  const batchUpdateEntities = useMutation({
    mutationFn: async ({ resource, objectIds, changes }: { resource: Exclude<MasterResource, "course-sessions">; objectIds: string[]; changes: Record<string, string | boolean | null> }) => (
      await http.post<BatchOperationResult>(`/api/v1/${resource}/batch-update`, { object_ids: objectIds, ...changes })
    ).data,
    onSuccess: (result, variables) => {
      invalidate(queryKeyForResource(variables.resource));
      clearEntitySelection(kindForResource(variables.resource));
      setEntityBatch(null);
      setEntityBatchValue("");
      toast.success(`已批量更新 ${result.affected_count} 条记录`);
    },
    onError: createError,
  });
  const batchDeleteEntities = useMutation({
    mutationFn: async ({ resource, objectIds }: { resource: Exclude<MasterResource, "course-sessions">; objectIds: string[] }) => (
      await http.post<BatchOperationResult>(`/api/v1/${resource}/batch-delete`, { object_ids: objectIds })
    ).data,
    onSuccess: (result, variables) => {
      invalidate(queryKeyForResource(variables.resource));
      clearEntitySelection(kindForResource(variables.resource));
      setEntityBatch(null);
      toast.success(`已批量删除 ${result.affected_count} 条记录`);
    },
    onError: createError,
  });
  const remove = useDeleteMasterDataApiV1MasterDataResourceObjectIdDelete({
    mutation: {
      onSuccess: (_, variables) => {
        const queryKey = queryKeyForResource(variables.resource as MasterResource);
        invalidate(queryKey);
        setCourseSelection((current) => {
          const next = { ...current };
          delete next[variables.objectId];
          return next;
        });
        const clearDeleted = (setter: Dispatch<SetStateAction<RowSelectionState>>) => setter((current) => {
          const next = { ...current };
          delete next[variables.objectId];
          return next;
        });
        clearDeleted(setTeacherSelection);
        clearDeleted(setClassSelection);
        clearDeleted(setRoomSelection);
        clearDeleted(setSlotSelection);
        toast.success("记录已删除");
        setDeleteTarget(null);
      },
      onError: (error) => toast.error(errorMessage(error)),
      onSettled: () => setDeletingKey(""),
    },
  });

  const refresh = () => void Promise.all([campusQuery.refetch(), teacherQuery.refetch(), classQuery.refetch(), roomQuery.refetch(), slotQuery.refetch(), courseQuery.refetch()]);
  const afterImport = () => {
    [getListCampusesApiV1CampusesGetQueryKey(), getListTeachersApiV1TeachersGetQueryKey(), getListClassGroupsApiV1ClassGroupsGetQueryKey(), getListRoomsApiV1RoomsGetQueryKey(), getListTimeSlotsApiV1TimeSlotsGetQueryKey(), getListCourseSessionsApiV1CourseSessionsGetQueryKey()].forEach(invalidate);
    setTeacherSelection({});
    setClassSelection({});
    setRoomSelection({});
    setSlotSelection({});
    setCourseSelection({});
    toast.success("主数据已导入");
  };
  const upload = useImportXlsxApiV1ImportsXlsxPost({ mutation: { onSuccess: afterImport, onError: createError } });

  const courseOptions = useMemo(() => {
    const source = courseQuery.data ?? [];
    const unique = (values: string[]) => Array.from(new Set(values.filter(Boolean))).sort((a, b) => a.localeCompare(b, "zh-CN"));
    return {
      businessLines: unique(source.map((item) => item.business_line ?? "")),
      productTypes: unique(source.map((item) => item.product_type ?? "")),
      classes: unique(source.map((item) => item.class_business_id)),
      teachers: unique(source.map((item) => item.teacher_business_id)),
    };
  }, [courseQuery.data]);

  const filteredCourses = useMemo(() => {
    const query = courseSearch.trim().toLocaleLowerCase();
    return (courseQuery.data ?? []).filter((course) => {
      const searchable = [course.business_id, course.class_business_id, course.teacher_business_id, course.lesson_name ?? "", course.subject ?? "", course.business_line ?? "", course.product_type ?? "", course.original_room_business_id ?? ""].join(" ").toLocaleLowerCase();
      return (!query || searchable.includes(query))
        && (!businessLineFilter || course.business_line === businessLineFilter)
        && (!productTypeFilter || course.product_type === productTypeFilter)
        && (!classFilter || course.class_business_id === classFilter)
        && (!teacherFilter || course.teacher_business_id === teacherFilter)
        && (!dateFilter || course.lesson_date === dateFilter);
    });
  }, [businessLineFilter, classFilter, courseQuery.data, courseSearch, dateFilter, productTypeFilter, teacherFilter]);

  const filteredTeachers = useMemo(() => filterBySearch(teacherQuery.data ?? [], teacherSearch, (item) => [item.business_id, item.name, item.subject ?? "", item.calendar_user_id ?? ""]), [teacherQuery.data, teacherSearch]);
  const filteredClasses = useMemo(() => filterBySearch(classQuery.data ?? [], classSearch, (item) => [item.business_id, item.name, item.subject ?? "", item.grade ?? "", item.teacher_business_id]), [classQuery.data, classSearch]);
  const filteredRooms = useMemo(() => filterBySearch(roomQuery.data ?? [], roomSearch, (item) => [item.business_id, item.name, item.is_active ? "启用" : "停用"]), [roomQuery.data, roomSearch]);
  const filteredSlots = useMemo(() => filterBySearch(slotQuery.data ?? [], slotSearch, (item) => [item.business_id, item.weekday, item.start_time, item.end_time, item.kind ?? "", item.is_open ? "开放" : "关闭"]), [slotQuery.data, slotSearch]);

  const selectedTeacherIds = selectedIds(teacherSelection);
  const selectedClassIds = selectedIds(classSelection);
  const selectedRoomIds = selectedIds(roomSelection);
  const selectedSlotIds = selectedIds(slotSelection);
  const selectedCourseIds = Object.entries(courseSelection).filter(([, selected]) => selected).map(([id]) => id);
  const clearCourseFilters = () => { setCourseSearch(""); setBusinessLineFilter(""); setProductTypeFilter(""); setClassFilter(""); setTeacherFilter(""); setDateFilter(""); };
  const hasCourseFilters = Boolean(courseSearch || businessLineFilter || productTypeFilter || classFilter || teacherFilter || dateFilter);

  const downloadSample = async () => {
    try {
      const response = await http.get("/api/v1/imports/sample.xlsx", { responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "途排智策_官方课表数据源示例.xlsx";
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success("官方模板已下载");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const confirmDelete = (target: DeleteTarget) => setDeleteTarget(target);
  const executeDelete = () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    const key = `${target.resource}:${target.id}`;
    setDeletingKey(key);
    remove.mutate({ resource: target.resource, objectId: target.id });
  };

  const openCreateEntity = (kind: EntityKind) => {
    if (kind === "teacher") setTeacherDraft({ businessId: "", name: "", subject: "", calendarUserId: "" });
    if (kind === "class") setClassDraft({ businessId: "", name: "", businessLine: "", productType: "", teacherBusinessId: "" });
    if (kind === "room") setRoomDraft({ businessId: "", name: "", isActive: true });
    if (kind === "slot") setSlotDraft({ businessId: "", weekday: "", startTime: "09:00", endTime: "12:00", kind: "", sequence: "0", isOpen: true });
    setEntityDialog({ kind, mode: "create" });
  };

  const openEditEntity = (kind: EntityKind, item: TeacherResponse | ClassGroupResponse | RoomResponse | TimeSlotResponse) => {
    setCampusId(item.campus_id);
    if (kind === "teacher") {
      const teacher = item as TeacherResponse;
      setTeacherDraft({ businessId: teacher.business_id, name: teacher.name, subject: teacher.subject ?? "", calendarUserId: teacher.calendar_user_id ?? "" });
    } else if (kind === "class") {
      const classGroup = item as ClassGroupResponse;
      setClassDraft({ businessId: classGroup.business_id, name: classGroup.name, businessLine: classGroup.subject ?? "", productType: classGroup.grade ?? "", teacherBusinessId: classGroup.teacher_business_id });
    } else if (kind === "room") {
      const room = item as RoomResponse;
      setRoomDraft({ businessId: room.business_id, name: room.name, isActive: Boolean(room.is_active) });
    } else {
      const slot = item as TimeSlotResponse;
      setSlotDraft({ businessId: slot.business_id, weekday: slot.weekday, startTime: slot.start_time, endTime: slot.end_time, kind: slot.kind ?? "", sequence: String(slot.sequence ?? 0), isOpen: Boolean(slot.is_open) });
    }
    setEntityDialog({ kind, mode: "edit", id: item.id });
  };

  const submitEntity = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!campusId || !entityDialog) return;
    const editing = entityDialog.mode === "edit";
    if (entityDialog.kind === "teacher") {
      const businessId = teacherDraft.businessId.trim();
      const data = { campus_id: campusId, business_id: businessId, name: teacherDraft.name.trim() || businessId, subject: teacherDraft.subject.trim(), calendar_user_id: teacherDraft.calendarUserId.trim() || null };
      if (editing && entityDialog.id) updateTeacher.mutate({ objectId: entityDialog.id, data }); else createTeacher.mutate({ data });
    } else if (entityDialog.kind === "class") {
      const businessId = classDraft.businessId.trim();
      const data = { campus_id: campusId, business_id: businessId, name: classDraft.name.trim() || businessId, subject: classDraft.businessLine.trim(), grade: classDraft.productType.trim(), teacher_business_id: classDraft.teacherBusinessId };
      if (editing && entityDialog.id) updateClass.mutate({ objectId: entityDialog.id, data }); else createClass.mutate({ data });
    } else if (entityDialog.kind === "room") {
      const businessId = roomDraft.businessId.trim();
      const data = { campus_id: campusId, business_id: businessId, name: roomDraft.name.trim() || businessId, is_active: roomDraft.isActive };
      if (editing && entityDialog.id) updateRoom.mutate({ objectId: entityDialog.id, data }); else createRoom.mutate({ data });
    } else {
      const data = { campus_id: campusId, business_id: slotDraft.businessId.trim(), weekday: slotDraft.weekday.trim(), start_time: slotDraft.startTime, end_time: slotDraft.endTime, kind: slotDraft.kind.trim(), sequence: Number(slotDraft.sequence) || 0, is_open: slotDraft.isOpen };
      if (editing && entityDialog.id) updateSlot.mutate({ objectId: entityDialog.id, data }); else createSlot.mutate({ data });
    }
  };

  const submitCourse = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!courseDialogMode) return;
    if (courseDialogMode === "edit") {
      updateCourse.mutate({ objectId: courseDraft.id, data: { lesson_date: courseDraft.lessonDate || null, original_room_business_id: courseDraft.roomBusinessId || null, calendar_user_id: courseDraft.calendarUserId.trim() || null } });
      return;
    }
    const payload: CourseSessionPayload = {
      campus_id: courseDraft.campusId,
      business_id: courseDraft.businessId.trim(),
      business_line: courseDraft.businessLine.trim(),
      product_type: courseDraft.productType.trim(),
      class_business_id: courseDraft.classBusinessId,
      teacher_business_id: courseDraft.teacherBusinessId,
      calendar_user_id: courseDraft.calendarUserId.trim() || null,
      subject: courseDraft.subject.trim(),
      lesson_name: courseDraft.lessonName.trim(),
      schedule_source: courseDraft.scheduleSource.trim(),
      stage: courseDraft.stage.trim(),
      planned_sessions: Number(courseDraft.plannedSessions) || 0,
      planned_hours: Number(courseDraft.plannedHours) || 0,
      session_no: Number(courseDraft.sessionNo) || 0,
      lesson_date: courseDraft.lessonDate || null,
      duration_minutes: Number(courseDraft.durationMinutes) || 180,
      suggested_slot_id: null,
      fixed_start_time: courseDraft.fixedStartTime,
      fixed_end_time: courseDraft.fixedEndTime,
      original_room_business_id: courseDraft.roomBusinessId || null,
      is_locked: courseDraft.isLocked,
    };
    createCourse.mutate({ data: payload });
  };

  const runBatchAction = () => {
    if (!batchAction || selectedCourseIds.length === 0) return;
    if (batchAction === "delete") {
      batchDeleteCourses.mutate(selectedCourseIds);
    } else if (batchAction === "date" && batchDate) {
      batchUpdateCourses.mutate({ object_ids: selectedCourseIds, lesson_date: batchDate });
    } else if (batchAction === "room" && batchRoom) {
      batchUpdateCourses.mutate({ object_ids: selectedCourseIds, original_room_business_id: batchRoom });
    }
  };

  const clearEntitySelection = (kind: EntityKind) => {
    if (kind === "teacher") setTeacherSelection({});
    else if (kind === "class") setClassSelection({});
    else if (kind === "room") setRoomSelection({});
    else setSlotSelection({});
  };

  const idsForEntityKind = (kind: EntityKind) => kind === "teacher"
    ? selectedTeacherIds
    : kind === "class"
      ? selectedClassIds
      : kind === "room"
        ? selectedRoomIds
        : selectedSlotIds;

  const openEntityBatch = (kind: EntityKind, action: EntityBatchAction) => {
    setEntityBatchValue(action === "room-status" || action === "slot-status" ? "true" : "");
    setEntityBatch({ kind, action });
  };

  const runEntityBatch = () => {
    if (!entityBatch) return;
    const objectIds = idsForEntityKind(entityBatch.kind);
    if (!objectIds.length) return;
    const resource = entityConfig(entityBatch.kind).resource;
    if (entityBatch.action === "delete") {
      batchDeleteEntities.mutate({ resource, objectIds });
      return;
    }
    const changes: Record<string, string | boolean | null> = {};
    if (entityBatch.action === "teacher-subject") changes.subject = entityBatchValue.trim();
    if (entityBatch.action === "teacher-calendar") changes.calendar_user_id = entityBatchValue.trim() || null;
    if (entityBatch.action === "class-line") changes.subject = entityBatchValue.trim();
    if (entityBatch.action === "class-type") changes.grade = entityBatchValue.trim();
    if (entityBatch.action === "class-teacher") changes.teacher_business_id = entityBatchValue;
    if (entityBatch.action === "room-status") changes.is_active = entityBatchValue === "true";
    if (entityBatch.action === "slot-status") changes.is_open = entityBatchValue === "true";
    batchUpdateEntities.mutate({ resource, objectIds, changes });
  };

  const teacherColumns: ColumnDef<TeacherResponse>[] = [
    { id: "teacher", header: "教师（教研组）", cell: ({ row }) => <IdentityValue item={row.original} /> },
    { accessorKey: "subject", header: "学科" },
    { accessorKey: "calendar_user_id", header: "飞书日程账号" },
    ...(!readOnly ? [{ id: "actions", header: "操作", enableSorting: false, cell: ({ row }: { row: { original: TeacherResponse } }) => <RowActions editLabel="编辑教师" onEdit={() => openEditEntity("teacher", row.original)} deleting={deletingKey === `teachers:${row.original.id}`} onDelete={() => confirmDelete({ id: row.original.id, label: identityLabel(row.original), resource: "teachers", resourceLabel: "教师" })} /> }] : []),
  ];
  const classColumns: ColumnDef<ClassGroupResponse>[] = [
    { id: "class", header: "班级", cell: ({ row }) => <IdentityValue item={row.original} /> },
    { accessorKey: "subject", header: "业务线" },
    { accessorKey: "grade", header: "班型" },
    { accessorKey: "teacher_business_id", header: "教师（教研组）" },
    ...(!readOnly ? [{ id: "actions", header: "操作", enableSorting: false, cell: ({ row }: { row: { original: ClassGroupResponse } }) => <RowActions editLabel="编辑班级" onEdit={() => openEditEntity("class", row.original)} deleting={deletingKey === `class-groups:${row.original.id}`} onDelete={() => confirmDelete({ id: row.original.id, label: identityLabel(row.original), resource: "class-groups", resourceLabel: "班级" })} /> }] : []),
  ];
  const roomColumns: ColumnDef<RoomResponse>[] = [
    { id: "room", header: "教室", cell: ({ row }) => <IdentityValue item={row.original} /> },
    { accessorFn: (row) => row.is_active ? "启用" : "停用", id: "active", header: "状态" },
    ...(!readOnly ? [{ id: "actions", header: "操作", enableSorting: false, cell: ({ row }: { row: { original: RoomResponse } }) => <RowActions editLabel="编辑教室" onEdit={() => openEditEntity("room", row.original)} deleting={deletingKey === `rooms:${row.original.id}`} onDelete={() => confirmDelete({ id: row.original.id, label: identityLabel(row.original), resource: "rooms", resourceLabel: "教室" })} /> }] : []),
  ];
  const slotColumns: ColumnDef<TimeSlotResponse>[] = [
    { accessorKey: "business_id", header: "时段 ID" },
    { accessorKey: "weekday", header: "星期" },
    { accessorKey: "start_time", header: "开始" },
    { accessorKey: "end_time", header: "结束" },
    { accessorKey: "kind", header: "类型" },
    { accessorKey: "sequence", header: "序号" },
    { id: "open", header: "状态", accessorFn: (row) => row.is_open ? "开放" : "关闭" },
    ...(!readOnly ? [{ id: "actions", header: "操作", enableSorting: false, cell: ({ row }: { row: { original: TimeSlotResponse } }) => <RowActions editLabel="编辑时段" onEdit={() => openEditEntity("slot", row.original)} deleting={deletingKey === `time-slots:${row.original.id}`} onDelete={() => confirmDelete({ id: row.original.id, label: row.original.business_id, resource: "time-slots", resourceLabel: "时段" })} /> }] : []),
  ];
  const courseColumns: ColumnDef<CourseSessionResponse>[] = [
    { accessorKey: "business_id", header: "课程 ID" },
    { accessorKey: "business_line", header: "业务线" },
    { accessorKey: "product_type", header: "班型" },
    { accessorKey: "class_business_id", header: "班级标签" },
    { accessorKey: "teacher_business_id", header: "教师（教研组）" },
    { accessorKey: "lesson_name", header: "课节名称" },
    { accessorKey: "session_no", header: "课次序号" },
    { accessorKey: "lesson_date", header: "上课日期" },
    { id: "fixed_time", header: "固定时段", accessorFn: (row) => `${row.fixed_start_time ?? ""}-${row.fixed_end_time ?? ""}` },
    { accessorKey: "original_room_business_id", header: "授课教室" },
    ...(!readOnly ? [{ id: "actions", header: "操作", enableSorting: false, cell: ({ row }: { row: { original: CourseSessionResponse } }) => <RowActions editLabel="编辑课程" editTitle="编辑日期、教室和日程账号" onEdit={() => { setCourseDraft(draftFromCourse(row.original)); setCourseDialogMode("edit"); }} deleting={deletingKey === `course-sessions:${row.original.id}`} onDelete={() => confirmDelete({ id: row.original.id, label: row.original.business_id, resource: "course-sessions", resourceLabel: "课程" })} /> }] : []),
  ];

  const loading = [campusQuery, teacherQuery, classQuery, roomQuery, slotQuery, courseQuery].some((item) => item.isPending);
  const failed = [campusQuery, teacherQuery, classQuery, roomQuery, slotQuery, courseQuery].some((item) => item.isError);
  const isCreating = createTeacher.isPending || createClass.isPending || createRoom.isPending || createSlot.isPending || updateTeacher.isPending || updateClass.isPending || updateRoom.isPending || updateSlot.isPending;
  const isCourseSaving = createCourse.isPending || updateCourse.isPending;
  const isBatchSaving = batchUpdateCourses.isPending || batchDeleteCourses.isPending;
  const entityLabel = entityDialog ? entityConfig(entityDialog.kind).label : "主数据";

  return (
    <div className="space-y-5">
      <input ref={file} className="hidden" type="file" accept=".xlsx" onChange={(event) => { const selected = event.target.files?.[0]; if (selected) upload.mutate({ data: { file: selected as unknown as string } }); event.target.value = ""; }} />
      <PageHeader title="主数据" actions={<><Button size="sm" variant="outline" onClick={refresh}><RefreshCw className="size-3.5" />刷新</Button><Button size="sm" variant="outline" onClick={() => void downloadSample()}><Download className="size-3.5" />下载官方模板</Button>{!readOnly ? <Button size="sm" variant="secondary" onClick={() => file.current?.click()} disabled={upload.isPending}><FileUp className="size-3.5" />导入 XLSX</Button> : null}</>} />
      {loading ? <LoadingState /> : failed ? <ErrorState retry={refresh} /> : (
        <Tabs defaultValue="teachers">
          <TabsList><TabsTrigger value="teachers">教师</TabsTrigger><TabsTrigger value="classes">班级</TabsTrigger><TabsTrigger value="rooms">教室</TabsTrigger><TabsTrigger value="slots">时段</TabsTrigger><TabsTrigger value="courses">课程场次</TabsTrigger></TabsList>
          <TabsContent value="teachers" className="pt-4"><EntityToolbar label="教师" total={teacherQuery.data?.length ?? 0} filtered={filteredTeachers.length} selected={selectedTeacherIds.length} search={teacherSearch} setSearch={setTeacherSearch} readOnly={readOnly} onAdd={() => openCreateEntity("teacher")} disabled={!campusId} actions={[{ label: "批量改学科", action: "teacher-subject" }, { label: "批量改日程账号", action: "teacher-calendar" }]} onBatch={(action) => openEntityBatch("teacher", action)} /><DataTable columns={teacherColumns} data={filteredTeachers} paginated defaultPageSize={50} selectable={!readOnly} getRowId={(row) => row.id} selectedRowIds={teacherSelection} onSelectionChange={setTeacherSelection} /></TabsContent>
          <TabsContent value="classes" className="pt-4"><EntityToolbar label="班级" total={classQuery.data?.length ?? 0} filtered={filteredClasses.length} selected={selectedClassIds.length} search={classSearch} setSearch={setClassSearch} readOnly={readOnly} onAdd={() => openCreateEntity("class")} disabled={!campusId || !teacherQuery.data?.length} actions={[{ label: "批量改业务线", action: "class-line" }, { label: "批量改班型", action: "class-type" }, { label: "批量改教师", action: "class-teacher" }]} onBatch={(action) => openEntityBatch("class", action)} /><DataTable columns={classColumns} data={filteredClasses} paginated defaultPageSize={50} selectable={!readOnly} getRowId={(row) => row.id} selectedRowIds={classSelection} onSelectionChange={setClassSelection} /></TabsContent>
          <TabsContent value="rooms" className="pt-4"><EntityToolbar label="教室" total={roomQuery.data?.length ?? 0} filtered={filteredRooms.length} selected={selectedRoomIds.length} search={roomSearch} setSearch={setRoomSearch} readOnly={readOnly} onAdd={() => openCreateEntity("room")} disabled={!campusId} actions={[{ label: "批量启用/停用", action: "room-status" }]} onBatch={(action) => openEntityBatch("room", action)} /><DataTable columns={roomColumns} data={filteredRooms} paginated defaultPageSize={50} selectable={!readOnly} getRowId={(row) => row.id} selectedRowIds={roomSelection} onSelectionChange={setRoomSelection} /></TabsContent>
          <TabsContent value="slots" className="pt-4"><EntityToolbar label="时段" total={slotQuery.data?.length ?? 0} filtered={filteredSlots.length} selected={selectedSlotIds.length} search={slotSearch} setSearch={setSlotSearch} readOnly={readOnly} onAdd={() => openCreateEntity("slot")} disabled={!campusId} actions={[{ label: "批量开放/关闭", action: "slot-status" }]} onBatch={(action) => openEntityBatch("slot", action)} /><DataTable columns={slotColumns} data={filteredSlots} paginated defaultPageSize={50} selectable={!readOnly} getRowId={(row) => row.id} selectedRowIds={slotSelection} onSelectionChange={setSlotSelection} /></TabsContent>
          <TabsContent value="courses" className="pt-4">
            <CourseToolbar
              total={courseQuery.data?.length ?? 0}
              filtered={filteredCourses.length}
              selected={selectedCourseIds.length}
              search={courseSearch}
              setSearch={setCourseSearch}
              businessLine={businessLineFilter}
              setBusinessLine={setBusinessLineFilter}
              productType={productTypeFilter}
              setProductType={setProductTypeFilter}
              classBusinessId={classFilter}
              setClassBusinessId={setClassFilter}
              teacherBusinessId={teacherFilter}
              setTeacherBusinessId={setTeacherFilter}
              lessonDate={dateFilter}
              setLessonDate={setDateFilter}
              options={courseOptions}
              hasFilters={hasCourseFilters}
              clearFilters={clearCourseFilters}
              readOnly={readOnly}
              onAdd={() => { setCourseDraft(emptyCourseDraft(campusId)); setCourseDialogMode("create"); }}
              onBatch={setBatchAction}
            />
            <DataTable columns={courseColumns} data={filteredCourses} paginated defaultPageSize={50} selectable={!readOnly} getRowId={(row) => row.id} selectedRowIds={courseSelection} onSelectionChange={setCourseSelection} />
          </TabsContent>
        </Tabs>
      )}

      <Dialog open={entityDialog !== null} onOpenChange={(open) => { if (!open) closeEntityDialog(); }}>
        <DialogContent>
          <DialogTitle className="text-base font-semibold">{entityDialog?.mode === "edit" ? "编辑" : "新增"}{entityLabel}</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-zinc-500">{entityDialog?.kind === "slot" ? "时段用于描述可排课时间窗口；课程自身的固定开始、结束时间仍以课程数据为准。" : "名称与业务标签一致时，名称可以留空，系统会自动复用标签。"}</DialogDescription>
          <form className="mt-5 space-y-4" onSubmit={submitEntity}>
            <label className="block text-sm text-zinc-700">所属校区<select className={inputClass} value={campusId} onChange={(event) => setCampusId(event.target.value)} required>{campusQuery.data?.map((campus) => <option key={campus.id} value={campus.id}>{campus.name}</option>)}</select></label>
            {entityDialog?.kind === "teacher" ? <><label className="block text-sm text-zinc-700">教师（教研组）标签<input className={inputClass} value={teacherDraft.businessId} onChange={(event) => setTeacherDraft({ ...teacherDraft, businessId: event.target.value })} required /></label><label className="block text-sm text-zinc-700">显示名称（选填）<input className={inputClass} value={teacherDraft.name} onChange={(event) => setTeacherDraft({ ...teacherDraft, name: event.target.value })} placeholder="留空则与标签一致" /></label><label className="block text-sm text-zinc-700">学科<input className={inputClass} value={teacherDraft.subject} onChange={(event) => setTeacherDraft({ ...teacherDraft, subject: event.target.value })} /></label><label className="block text-sm text-zinc-700">飞书日程账号（选填）<input className={inputClass} value={teacherDraft.calendarUserId} onChange={(event) => setTeacherDraft({ ...teacherDraft, calendarUserId: event.target.value })} /></label></> : entityDialog?.kind === "class" ? <><label className="block text-sm text-zinc-700">班级标签<input className={inputClass} value={classDraft.businessId} onChange={(event) => setClassDraft({ ...classDraft, businessId: event.target.value })} required /></label><label className="block text-sm text-zinc-700">显示名称（选填）<input className={inputClass} value={classDraft.name} onChange={(event) => setClassDraft({ ...classDraft, name: event.target.value })} placeholder="留空则与标签一致" /></label><div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm text-zinc-700">业务线<input className={inputClass} value={classDraft.businessLine} onChange={(event) => setClassDraft({ ...classDraft, businessLine: event.target.value })} /></label><label className="block text-sm text-zinc-700">班型<input className={inputClass} value={classDraft.productType} onChange={(event) => setClassDraft({ ...classDraft, productType: event.target.value })} /></label></div><label className="block text-sm text-zinc-700">教师（教研组）<select className={inputClass} value={classDraft.teacherBusinessId} onChange={(event) => setClassDraft({ ...classDraft, teacherBusinessId: event.target.value })} required><option value="">请选择</option>{teacherQuery.data?.map((teacher) => <option key={teacher.id} value={teacher.business_id}>{identityLabel(teacher)}</option>)}</select></label></> : entityDialog?.kind === "room" ? <><label className="block text-sm text-zinc-700">教室标签<input className={inputClass} value={roomDraft.businessId} onChange={(event) => setRoomDraft({ ...roomDraft, businessId: event.target.value })} required /></label><label className="block text-sm text-zinc-700">显示名称（选填）<input className={inputClass} value={roomDraft.name} onChange={(event) => setRoomDraft({ ...roomDraft, name: event.target.value })} placeholder="留空则与标签一致" /></label><label className="flex items-center gap-2 text-sm text-zinc-700"><input type="checkbox" checked={roomDraft.isActive} onChange={(event) => setRoomDraft({ ...roomDraft, isActive: event.target.checked })} />启用教室</label></> : <><label className="block text-sm text-zinc-700">时段标签<input className={inputClass} value={slotDraft.businessId} onChange={(event) => setSlotDraft({ ...slotDraft, businessId: event.target.value })} required /></label><div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm text-zinc-700">星期<input className={inputClass} value={slotDraft.weekday} onChange={(event) => setSlotDraft({ ...slotDraft, weekday: event.target.value })} placeholder="例如：周一" required /></label><label className="block text-sm text-zinc-700">类型<input className={inputClass} value={slotDraft.kind} onChange={(event) => setSlotDraft({ ...slotDraft, kind: event.target.value })} placeholder="例如：上午" /></label><label className="block text-sm text-zinc-700">开始时间<input className={inputClass} type="time" value={slotDraft.startTime} onChange={(event) => setSlotDraft({ ...slotDraft, startTime: event.target.value })} required /></label><label className="block text-sm text-zinc-700">结束时间<input className={inputClass} type="time" value={slotDraft.endTime} onChange={(event) => setSlotDraft({ ...slotDraft, endTime: event.target.value })} required /></label><label className="block text-sm text-zinc-700">排序序号<input className={inputClass} type="number" min="0" value={slotDraft.sequence} onChange={(event) => setSlotDraft({ ...slotDraft, sequence: event.target.value })} /></label></div><label className="flex items-center gap-2 text-sm text-zinc-700"><input type="checkbox" checked={slotDraft.isOpen} onChange={(event) => setSlotDraft({ ...slotDraft, isOpen: event.target.checked })} />开放时段</label></>}
            <div className="flex justify-end gap-2 border-t border-zinc-100 pt-4"><Button type="button" variant="outline" onClick={closeEntityDialog}>取消</Button><Button type="submit" disabled={isCreating || !campusId}>{isCreating ? "保存中" : "保存"}</Button></div>
          </form>
        </DialogContent>
      </Dialog>

      <CourseEditorDialog
        open={courseDialogMode !== null}
        mode={courseDialogMode ?? "create"}
        draft={courseDraft}
        setDraft={setCourseDraft}
        campuses={campusQuery.data ?? []}
        teachers={teacherQuery.data ?? []}
        classes={classQuery.data ?? []}
        rooms={roomQuery.data ?? []}
        saving={isCourseSaving}
        close={closeCourseDialog}
        submit={submitCourse}
      />
      <BatchCourseDialog open={batchAction !== null} action={batchAction ?? "date"} selected={selectedCourseIds.length} date={batchDate} setDate={setBatchDate} room={batchRoom} setRoom={setBatchRoom} rooms={roomQuery.data ?? []} saving={isBatchSaving} close={() => setBatchAction(null)} submit={runBatchAction} />
      <EntityBatchDialog open={entityBatch !== null} state={entityBatch} selected={entityBatch ? idsForEntityKind(entityBatch.kind).length : 0} value={entityBatchValue} setValue={setEntityBatchValue} teachers={teacherQuery.data ?? []} saving={batchUpdateEntities.isPending || batchDeleteEntities.isPending} close={() => setEntityBatch(null)} submit={runEntityBatch} />
      <ConfirmDialog open={deleteTarget !== null} title={deleteTarget ? `删除${deleteTarget.resourceLabel}` : "删除记录"} description={deleteTarget ? `确认删除“${deleteTarget.label}”？被课程、课表版本或飞书日程引用的记录会被系统拦截。` : ""} confirmLabel="确认删除" danger pending={remove.isPending} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }} onConfirm={executeDelete} />
    </div>
  );
}

function CourseToolbar({ total, filtered, selected, search, setSearch, businessLine, setBusinessLine, productType, setProductType, classBusinessId, setClassBusinessId, teacherBusinessId, setTeacherBusinessId, lessonDate, setLessonDate, options, hasFilters, clearFilters, readOnly, onAdd, onBatch }: { total: number; filtered: number; selected: number; search: string; setSearch: (value: string) => void; businessLine: string; setBusinessLine: (value: string) => void; productType: string; setProductType: (value: string) => void; classBusinessId: string; setClassBusinessId: (value: string) => void; teacherBusinessId: string; setTeacherBusinessId: (value: string) => void; lessonDate: string; setLessonDate: (value: string) => void; options: { businessLines: string[]; productTypes: string[]; classes: string[]; teachers: string[] }; hasFilters: boolean; clearFilters: () => void; readOnly: boolean; onAdd: () => void; onBatch: (action: CourseBatchAction) => void }) {
  return <div className="mb-3 space-y-3 border border-zinc-200 bg-white p-3"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3 text-xs text-zinc-500"><span>筛选 {filtered} / {total} 条</span>{selected > 0 ? <span className="font-medium text-blue-700">已选择 {selected} 条</span> : null}</div>{!readOnly ? <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={selected === 0} onClick={() => onBatch("date")}><CalendarDays className="size-3.5" />批量改日期</Button><Button size="sm" variant="outline" disabled={selected === 0} onClick={() => onBatch("room")}>批量改教室</Button><Button size="sm" variant="outline" disabled={selected === 0} onClick={() => onBatch("delete")}><Trash2 className="size-3.5 text-red-600" />批量删除</Button><Button size="sm" onClick={onAdd}><Plus className="size-3.5" />新增课程</Button></div> : <span className="text-xs text-zinc-400">成员账号为只读视图</span>}</div><div className="grid gap-2 md:grid-cols-2 xl:grid-cols-6"><label className="relative"><Search className="absolute left-2.5 top-2 size-3.5 text-zinc-400" /><input aria-label="搜索课程" className={`${compactInputClass} w-full pl-8`} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="课程、班级、教师、教室" /></label><FilterSelect label="全部业务线" value={businessLine} setValue={setBusinessLine} options={options.businessLines} /><FilterSelect label="全部班型" value={productType} setValue={setProductType} options={options.productTypes} /><FilterSelect label="全部班级" value={classBusinessId} setValue={setClassBusinessId} options={options.classes} /><FilterSelect label="全部教师" value={teacherBusinessId} setValue={setTeacherBusinessId} options={options.teachers} /><div className="flex gap-2"><input aria-label="筛选上课日期" type="date" className={`${compactInputClass} min-w-0 flex-1`} value={lessonDate} onChange={(event) => setLessonDate(event.target.value)} />{hasFilters ? <Button size="icon" variant="ghost" title="清除筛选" aria-label="清除筛选" onClick={clearFilters}><X className="size-3.5" /></Button> : null}</div></div></div>;
}

function FilterSelect({ label, value, setValue, options }: { label: string; value: string; setValue: (value: string) => void; options: string[] }) {
  return <select aria-label={label} className={`${compactInputClass} w-full`} value={value} onChange={(event) => setValue(event.target.value)}><option value="">{label}</option>{options.map((option) => <option key={option} value={option}>{option}</option>)}</select>;
}

function CourseEditorDialog({ open, mode, draft, setDraft, campuses, teachers, classes, rooms, saving, close, submit }: { open: boolean; mode: CourseDialogMode; draft: CourseDraft; setDraft: (draft: CourseDraft) => void; campuses: Array<{ id: string; name: string }>; teachers: TeacherResponse[]; classes: ClassGroupResponse[]; rooms: RoomResponse[]; saving: boolean; close: () => void; submit: (event: FormEvent<HTMLFormElement>) => void }) {
  const editing = mode === "edit";
  const selectClass = (businessId: string) => {
    const selected = classes.find((item) => item.business_id === businessId);
    setDraft({ ...draft, classBusinessId: businessId, teacherBusinessId: selected?.teacher_business_id ?? draft.teacherBusinessId, businessLine: selected?.subject ?? draft.businessLine, productType: selected?.grade ?? draft.productType });
  };
  return <Dialog open={open} onOpenChange={(next) => { if (!next) close(); }}><DialogContent className="max-h-[90vh] max-w-4xl overflow-y-auto"><DialogTitle className="text-base font-semibold">{editing ? `编辑课程 ${draft.businessId}` : "新增课程场次"}</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">{editing ? "现有课程只调整上课日期、授课教室和飞书日程账号；教师（教研组）与固定时段保持原数据。" : "新增时确定教师（教研组）和固定上课时段，保存后只允许调整日期与教室。"}</DialogDescription><form className="mt-5 space-y-5" onSubmit={submit}>{editing ? <div className="grid gap-3 border border-zinc-200 bg-zinc-50 p-4 text-sm sm:grid-cols-2 lg:grid-cols-4"><ReadOnlyField label="班级" value={draft.classBusinessId} /><ReadOnlyField label="教师（教研组）" value={draft.teacherBusinessId} /><ReadOnlyField label="固定时段" value={`${draft.fixedStartTime}-${draft.fixedEndTime}`} /><ReadOnlyField label="课次序号" value={draft.sessionNo} /></div> : <><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"><FormSelect label="所属校区" value={draft.campusId} setValue={(value) => setDraft({ ...draft, campusId: value })} required options={campuses.map((item) => ({ value: item.id, label: item.name }))} /><FormInput label="课程业务 ID" value={draft.businessId} setValue={(value) => setDraft({ ...draft, businessId: value })} required /><FormSelect label="班级" value={draft.classBusinessId} setValue={selectClass} required options={classes.map((item) => ({ value: item.business_id, label: identityLabel(item) }))} /><FormSelect label="教师（教研组）" value={draft.teacherBusinessId} setValue={(value) => setDraft({ ...draft, teacherBusinessId: value })} required options={teachers.map((item) => ({ value: item.business_id, label: identityLabel(item) }))} /><FormInput label="业务线" value={draft.businessLine} setValue={(value) => setDraft({ ...draft, businessLine: value })} /><FormInput label="班型" value={draft.productType} setValue={(value) => setDraft({ ...draft, productType: value })} /><FormInput label="课节名称" value={draft.lessonName} setValue={(value) => setDraft({ ...draft, lessonName: value })} required /><FormInput label="学科" value={draft.subject} setValue={(value) => setDraft({ ...draft, subject: value })} /><FormInput label="课次序号" type="number" min="0" value={draft.sessionNo} setValue={(value) => setDraft({ ...draft, sessionNo: value })} required /><FormInput label="固定开始时间" type="time" value={draft.fixedStartTime} setValue={(value) => setDraft({ ...draft, fixedStartTime: value })} required /><FormInput label="固定结束时间" type="time" value={draft.fixedEndTime} setValue={(value) => setDraft({ ...draft, fixedEndTime: value })} required /><FormInput label="时长（分钟）" type="number" min="1" value={draft.durationMinutes} setValue={(value) => setDraft({ ...draft, durationMinutes: value })} required /><FormInput label="编排来源" value={draft.scheduleSource} setValue={(value) => setDraft({ ...draft, scheduleSource: value })} /><FormInput label="编排阶段" value={draft.stage} setValue={(value) => setDraft({ ...draft, stage: value })} /><FormInput label="计划课次" type="number" min="0" value={draft.plannedSessions} setValue={(value) => setDraft({ ...draft, plannedSessions: value })} /><FormInput label="计划课时" type="number" min="0" value={draft.plannedHours} setValue={(value) => setDraft({ ...draft, plannedHours: value })} /></div><label className="flex items-center gap-2 text-sm text-zinc-700"><input type="checkbox" checked={draft.isLocked} onChange={(event) => setDraft({ ...draft, isLocked: event.target.checked })} />创建为锁定课程（求解时保持原日期和教室）</label></>}<div className="grid gap-4 border-t border-zinc-100 pt-4 sm:grid-cols-3"><FormInput label="上课日期" type="date" value={draft.lessonDate} setValue={(value) => setDraft({ ...draft, lessonDate: value })} required /><FormSelect label="授课教室" value={draft.roomBusinessId} setValue={(value) => setDraft({ ...draft, roomBusinessId: value })} required options={rooms.filter((item) => item.is_active || item.business_id === draft.roomBusinessId).map((item) => ({ value: item.business_id, label: identityLabel(item) }))} /><FormInput label="飞书日程账号（选填）" value={draft.calendarUserId} setValue={(value) => setDraft({ ...draft, calendarUserId: value })} /></div><div className="flex justify-end gap-2 border-t border-zinc-100 pt-4"><Button type="button" variant="outline" onClick={close}>取消</Button><Button type="submit" disabled={saving}>{saving ? "保存中" : "保存课程"}</Button></div></form></DialogContent></Dialog>;
}

function BatchCourseDialog({ open, action, selected, date, setDate, room, setRoom, rooms, saving, close, submit }: { open: boolean; action: CourseBatchAction; selected: number; date: string; setDate: (value: string) => void; room: string; setRoom: (value: string) => void; rooms: RoomResponse[]; saving: boolean; close: () => void; submit: () => void }) {
  const title = action === "date" ? "批量修改上课日期" : action === "room" ? "批量修改授课教室" : "批量删除课程";
  const valid = action === "delete" || action === "date" ? action === "delete" || Boolean(date) : Boolean(room);
  return <Dialog open={open} onOpenChange={(next) => { if (!next) close(); }}><DialogContent><DialogTitle className="text-base font-semibold">{title}</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">本次将处理已选择的 {selected} 条课程。</DialogDescription><div className="mt-5">{action === "date" ? <FormInput label="新的上课日期" type="date" value={date} setValue={setDate} required /> : action === "room" ? <FormSelect label="新的授课教室" value={room} setValue={setRoom} required options={rooms.filter((item) => item.is_active).map((item) => ({ value: item.business_id, label: identityLabel(item) }))} /> : <div className="border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-900">已被课表版本或飞书日程引用的课程会被系统拦截，其余未引用课程才可以删除。</div>}</div><div className="mt-5 flex justify-end gap-2 border-t border-zinc-100 pt-4"><Button variant="outline" onClick={close}>取消</Button><Button variant={action === "delete" ? "danger" : "primary"} onClick={submit} disabled={saving || !valid}>{saving ? "处理中" : action === "delete" ? "确认批量删除" : "确认批量修改"}</Button></div></DialogContent></Dialog>;
}

function EntityBatchDialog({ open, state, selected, value, setValue, teachers, saving, close, submit }: { open: boolean; state: EntityBatchState | null; selected: number; value: string; setValue: (value: string) => void; teachers: TeacherResponse[]; saving: boolean; close: () => void; submit: () => void }) {
  if (!state) return null;
  const title = batchActionLabel(state.action);
  const deleting = state.action === "delete";
  const valid = deleting || state.action === "teacher-calendar" || Boolean(value);
  return <Dialog open={open} onOpenChange={(next) => { if (!next && !saving) close(); }}><DialogContent><DialogTitle className="text-base font-semibold">{title}</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">本次将处理已选择的 {selected} 条{entityConfig(state.kind).label}记录。</DialogDescription><div className="mt-5">{deleting ? <div className="border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-900">删除前会检查课程、班级、课表版本与飞书日程引用；存在引用的记录会被拦截。</div> : state.action === "class-teacher" ? <FormSelect label="新的教师（教研组）" value={value} setValue={setValue} required options={teachers.map((item) => ({ value: item.business_id, label: identityLabel(item) }))} /> : state.action === "room-status" ? <FormSelect label="新的教室状态" value={value} setValue={setValue} required options={[{ value: "true", label: "启用" }, { value: "false", label: "停用" }]} /> : state.action === "slot-status" ? <FormSelect label="新的时段状态" value={value} setValue={setValue} required options={[{ value: "true", label: "开放" }, { value: "false", label: "关闭" }]} /> : <FormInput label={batchFieldLabel(state.action)} value={value} setValue={setValue} required={state.action !== "teacher-calendar"} />}</div><div className="mt-5 flex justify-end gap-2 border-t border-zinc-100 pt-4"><Button variant="outline" disabled={saving} onClick={close}>取消</Button><Button variant={deleting ? "danger" : "primary"} onClick={submit} disabled={saving || !valid}>{saving ? "处理中" : deleting ? "确认批量删除" : "确认批量修改"}</Button></div></DialogContent></Dialog>;
}

function FormInput({ label, value, setValue, type = "text", required = false, min }: { label: string; value: string; setValue: (value: string) => void; type?: string; required?: boolean; min?: string }) {
  return <label className="block text-sm text-zinc-700">{label}<input className={inputClass} type={type} min={min} value={value} onChange={(event) => setValue(event.target.value)} required={required} /></label>;
}

function FormSelect({ label, value, setValue, options, required = false }: { label: string; value: string; setValue: (value: string) => void; options: Array<{ value: string; label: string }>; required?: boolean }) {
  return <label className="block text-sm text-zinc-700">{label}<select className={inputClass} value={value} onChange={(event) => setValue(event.target.value)} required={required}><option value="">请选择</option>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;
}

function ReadOnlyField({ label, value }: { label: string; value: string }) {
  return <div><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 truncate font-medium text-zinc-700" title={value}>{value || "-"}</div></div>;
}

function identityLabel(item: { business_id: string; name: string }) {
  return item.name === item.business_id ? item.name : `${item.name}（${item.business_id}）`;
}

function IdentityValue({ item }: { item: { business_id: string; name: string } }) {
  if (item.name === item.business_id) return <span>{item.name}</span>;
  return <div><div>{item.name}</div><div className="font-mono text-[11px] text-zinc-400">{item.business_id}</div></div>;
}

function DeleteButton({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  return <Button size="icon" variant="ghost" aria-label="删除" title="删除" disabled={disabled} onClick={onClick}><Trash2 className="size-3.5 text-red-600" /></Button>;
}

function RowActions({ editLabel, editTitle, onEdit, deleting, onDelete }: { editLabel: string; editTitle?: string; onEdit: () => void; deleting: boolean; onDelete: () => void }) {
  return <div className="flex items-center gap-1"><Button size="icon" variant="ghost" aria-label={editLabel} title={editTitle ?? editLabel} onClick={onEdit}><Pencil className="size-3.5 text-blue-600" /></Button><DeleteButton disabled={deleting} onClick={onDelete} /></div>;
}

function EntityToolbar({ label, total, filtered, selected, search, setSearch, readOnly, onAdd, disabled, actions, onBatch }: { label: string; total: number; filtered: number; selected: number; search: string; setSearch: (value: string) => void; readOnly: boolean; onAdd: () => void; disabled: boolean; actions: Array<{ label: string; action: EntityBatchAction }>; onBatch: (action: EntityBatchAction) => void }) {
  return <div className="mb-3 flex flex-wrap items-center justify-between gap-3 border border-zinc-200 bg-white p-3"><div className="flex flex-wrap items-center gap-3"><label className="relative"><Search className="absolute left-2.5 top-2 size-3.5 text-zinc-400" /><input aria-label={`搜索${label}`} className={`${compactInputClass} w-64 pl-8`} value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`搜索${label}标签或属性`} /></label><span className="text-xs text-zinc-500">筛选 {filtered} / {total} 条</span>{selected > 0 ? <span className="text-xs font-medium text-blue-700">已选择 {selected} 条</span> : null}</div>{readOnly ? <span className="text-xs text-zinc-400">成员账号为只读视图</span> : <div className="flex flex-wrap gap-2">{actions.map((item) => <Button key={item.action} size="sm" variant="outline" disabled={selected === 0} onClick={() => onBatch(item.action)}>{item.label}</Button>)}<Button size="sm" variant="outline" disabled={selected === 0} onClick={() => onBatch("delete")}><Trash2 className="size-3.5 text-red-600" />批量删除</Button><Button size="sm" onClick={onAdd} disabled={disabled}><Plus className="size-3.5" />新增{label}</Button></div>}</div>;
}

function selectedIds(selection: RowSelectionState) {
  return Object.entries(selection).filter(([, selected]) => selected).map(([id]) => id);
}

function filterBySearch<T>(items: T[], search: string, fields: (item: T) => string[]) {
  const query = search.trim().toLocaleLowerCase();
  if (!query) return items;
  return items.filter((item) => fields(item).join(" ").toLocaleLowerCase().includes(query));
}

function queryKeyForResource(resource: MasterResource): readonly unknown[] {
  if (resource === "teachers") return getListTeachersApiV1TeachersGetQueryKey();
  if (resource === "class-groups") return getListClassGroupsApiV1ClassGroupsGetQueryKey();
  if (resource === "rooms") return getListRoomsApiV1RoomsGetQueryKey();
  if (resource === "time-slots") return getListTimeSlotsApiV1TimeSlotsGetQueryKey();
  return getListCourseSessionsApiV1CourseSessionsGetQueryKey();
}

function entityConfig(kind: EntityKind): { label: string; resource: Exclude<MasterResource, "course-sessions">; queryKey: readonly unknown[] } {
  if (kind === "teacher") return { label: "教师", resource: "teachers", queryKey: getListTeachersApiV1TeachersGetQueryKey() };
  if (kind === "class") return { label: "班级", resource: "class-groups", queryKey: getListClassGroupsApiV1ClassGroupsGetQueryKey() };
  if (kind === "room") return { label: "教室", resource: "rooms", queryKey: getListRoomsApiV1RoomsGetQueryKey() };
  return { label: "时段", resource: "time-slots", queryKey: getListTimeSlotsApiV1TimeSlotsGetQueryKey() };
}

function kindForResource(resource: Exclude<MasterResource, "course-sessions">): EntityKind {
  if (resource === "teachers") return "teacher";
  if (resource === "class-groups") return "class";
  if (resource === "rooms") return "room";
  return "slot";
}

function batchActionLabel(action: EntityBatchAction) {
  if (action === "delete") return "批量删除";
  if (action === "teacher-subject") return "批量修改学科";
  if (action === "teacher-calendar") return "批量修改飞书日程账号";
  if (action === "class-line") return "批量修改业务线";
  if (action === "class-type") return "批量修改班型";
  if (action === "class-teacher") return "批量修改教师（教研组）";
  if (action === "room-status") return "批量修改教室状态";
  return "批量修改时段状态";
}

function batchFieldLabel(action: EntityBatchAction) {
  if (action === "teacher-subject") return "新的学科";
  if (action === "teacher-calendar") return "新的飞书日程账号（留空可清除）";
  if (action === "class-line") return "新的业务线";
  return "新的班型";
}
