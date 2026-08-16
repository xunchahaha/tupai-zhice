import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ColumnDef, type RowSelectionState } from "@tanstack/react-table";
import { CalendarDays, Columns3, Download, Eraser, FileUp, ListChecks, Pencil, Plus, RefreshCw, Search, Trash2, X } from "lucide-react";
import { type Dispatch, type FormEvent, type ReactNode, type SetStateAction, useEffect, useMemo, useRef, useState } from "react";
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
  type CourseSessionFilter,
  type CourseSessionPayload,
  type CourseSessionResponse,
  type ImportResult,
  type RoomResponse,
  type TeacherResponse,
  type TimeSlotResponse,
} from "@/api/generated/models";
import { http } from "@/api/http";
import { type AppOutletContext } from "@/app/user-context";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DataTable } from "@/components/data-table";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { ColumnHeader, CopyableId, TableText, TagList } from "@/components/table-cell";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/cn";
import { errorMessage } from "@/lib/format";

/**
 * 逐条选择（object_ids）的单次上限，来自后端 schema 的 max_length=1000。
 *
 * 课程场次不再受它约束：选中的正好是「当前筛选结果的全部」时，走后端的
 * filter + expected_count 入参，一条 id 都不用传，两万条也是一次请求。
 * 其余主数据（教师/班级/教室/时段）体量小，仍然走 object_ids。
 */
const BATCH_LIMIT = 1000;

/** 超过这个条数的批量删除要求再打一次勾——一次点击就能清空全库的操作不该只有一层确认。 */
const DELETE_DOUBLE_CONFIRM_THRESHOLD = 100;

type MasterResource = "teachers" | "class-groups" | "rooms" | "time-slots" | "course-sessions";
type EntityKind = "teacher" | "class" | "room" | "slot";
type CourseColumnKey = "business_id" | "business_line" | "product_type" | "stage" | "lesson_name" | "candidate_time";
type EntityDialogMode = "create" | "edit";
type CourseDialogMode = "create" | "edit";
type CourseBatchAction = "date" | "room" | "delete";
type EntityBatchAction = "teacher-subject" | "teacher-calendar" | "teacher-group" | "room-status" | "slot-status" | "delete";

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

/**
 * 后端要求 object_ids 与 filter 二选一：都传或都不传都是 422。
 * expected_count 是防呆——服务端命中数和前端算出来的条数对不上就整批拒绝，
 * 避免「列表已经变了，用户却按着旧的条数点了删除」。
 */
type CourseBatchScope =
  | { object_ids: string[]; expected_count: number }
  | { filter: CourseSessionFilter; expected_count: number };

type CourseBatchUpdatePayload = CourseBatchScope & {
  lesson_date?: string;
  original_room_business_id?: string;
};

interface SlotDraft {
  businessId: string;
  weekday: string;
  startTime: string;
  endTime: string;
  kind: string;
  sequence: string;
  isOpen: boolean;
}

const inputClass = "mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm shadow-2xs outline-none transition-all duration-150 focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20";
const compactInputClass = "h-8 rounded-md border border-zinc-300 bg-white px-2.5 text-xs shadow-2xs outline-none transition-all duration-150 focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20";

/** 可关闭的课程列。课程 ID 是内部标识，默认不占版面，需要时再打开。 */
const COURSE_OPTIONAL_COLUMNS: Array<{ key: CourseColumnKey; label: string }> = [
  { key: "business_id", label: "课程 ID" },
  { key: "business_line", label: "业务线" },
  { key: "product_type", label: "班型" },
  { key: "stage", label: "编排阶段" },
  { key: "lesson_name", label: "课节名称" },
  { key: "candidate_time", label: "候选时段" },
];
const DEFAULT_COURSE_COLUMNS: Record<CourseColumnKey, boolean> = {
  business_id: false,
  business_line: true,
  product_type: true,
  stage: true,
  lesson_name: true,
  candidate_time: true,
};

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

function courseProducts(course: CourseSessionResponse): string[] {
  return Array.from(new Set([...(course.product_types ?? []), course.product_type ?? ""].filter(Boolean)));
}

function courseTeachers(course: CourseSessionResponse): string[] {
  return Array.from(new Set([...(course.teacher_business_ids ?? []), course.teacher_business_id].filter(Boolean)));
}

function courseLessons(course: CourseSessionResponse): string[] {
  return Array.from(new Set([...(course.lesson_names ?? []), course.lesson_name ?? ""].filter(Boolean)));
}

function courseStages(course: CourseSessionResponse): string[] {
  return Array.from(new Set([...(course.stages ?? []), course.stage ?? ""].filter(Boolean)));
}

function courseClockWindows(course: CourseSessionResponse): string[] {
  const windows = (course.candidate_clock_windows ?? []).map((item) => `${item.start_time ?? ""}-${item.end_time ?? ""}`);
  return windows.length ? windows : [`${course.fixed_start_time ?? ""}-${course.fixed_end_time ?? ""}`].filter((item) => item !== "-");
}

function courseRooms(course: CourseSessionResponse): string[] {
  return Array.from(new Set([...(course.candidate_room_business_ids ?? []), course.original_room_business_id ?? ""].filter(Boolean)));
}

export function MasterDataPage() {
  const { user } = useOutletContext<AppOutletContext>();
  // Teachers, classes, rooms and sessions are shared source data for every
  // timetable.  Restrict edits to administrators so a scheduler assigned to
  // only one plan cannot change another plan's future inputs.
  const readOnly = user.role !== "admin";
  const client = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [importReport, setImportReport] = useState<ImportResult | null>(null);
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
  const [courseColumnPanel, setCourseColumnPanel] = useState(false);
  const [visibleCourseColumns, setVisibleCourseColumns] = useState<Record<CourseColumnKey, boolean>>(DEFAULT_COURSE_COLUMNS);
  const [entityBatch, setEntityBatch] = useState<EntityBatchState | null>(null);
  const [entityBatchValue, setEntityBatchValue] = useState("");
  const [batchAction, setBatchAction] = useState<CourseBatchAction | null>(null);
  const [batchDate, setBatchDate] = useState("");
  const [batchRoom, setBatchRoom] = useState("");
  const [campusId, setCampusId] = useState("");
  const [deletingKey, setDeletingKey] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [teacherDraft, setTeacherDraft] = useState({ businessId: "", name: "", subject: "", calendarUserId: "", isGroup: false });
  const [classDraft, setClassDraft] = useState({ businessId: "", name: "" });
  const [tracksTarget, setTracksTarget] = useState<ClassGroupResponse | null>(null);
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
    mutationFn: async (payload: CourseBatchScope) => (await http.post<BatchOperationResult>("/api/v1/course-sessions/batch-delete", payload)).data,
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
  const afterImport = (result: ImportResult) => {
    [getListCampusesApiV1CampusesGetQueryKey(), getListTeachersApiV1TeachersGetQueryKey(), getListClassGroupsApiV1ClassGroupsGetQueryKey(), getListRoomsApiV1RoomsGetQueryKey(), getListTimeSlotsApiV1TimeSlotsGetQueryKey(), getListCourseSessionsApiV1CourseSessionsGetQueryKey()].forEach(invalidate);
    setTeacherSelection({});
    setClassSelection({});
    setRoomSelection({});
    setSlotSelection({});
    setCourseSelection({});
    setImportReport(result);
    toast.success(`主数据已导入：新建 ${result.course_sessions} 个课次`);
  };
  const upload = useImportXlsxApiV1ImportsXlsxPost({ mutation: { onSuccess: afterImport, onError: createError } });

  const courseOptions = useMemo(() => {
    const source = Array.isArray(courseQuery.data) ? courseQuery.data : [];
    const unique = (values: string[]) => Array.from(new Set(values.filter(Boolean))).sort((a, b) => a.localeCompare(b, "zh-CN"));
    return {
      businessLines: unique(source.map((item) => item.business_line ?? "")),
      productTypes: unique(source.flatMap(courseProducts)),
      classes: unique(source.map((item) => item.class_business_id)),
      teachers: unique(source.flatMap(courseTeachers)),
    };
  }, [courseQuery.data]);

  const filteredCourses = useMemo(() => {
    const query = courseSearch.trim().toLocaleLowerCase();
    const list = Array.isArray(courseQuery.data) ? courseQuery.data : [];
    return list.filter((course) => {
      if (!course) return false;
      const searchable = [course.business_id, course.class_business_id, course.teacher_business_id, course.lesson_name ?? "", course.subject ?? "", course.business_line ?? "", course.product_type ?? "", ...courseProducts(course), ...courseLessons(course), ...courseRooms(course)].join(" ").toLocaleLowerCase();
      return (!query || searchable.includes(query))
        && (!businessLineFilter || course.business_line === businessLineFilter)
        && (!productTypeFilter || courseProducts(course).includes(productTypeFilter))
        && (!classFilter || course.class_business_id === classFilter)
        && (!teacherFilter || courseTeachers(course).includes(teacherFilter))
        && (!dateFilter || course.lesson_date === dateFilter);
    });
  }, [businessLineFilter, classFilter, courseQuery.data, courseSearch, dateFilter, productTypeFilter, teacherFilter]);

  const filteredTeachers = useMemo(() => filterBySearch(Array.isArray(teacherQuery.data) ? teacherQuery.data : [], teacherSearch, (item) => [item.business_id, item.name, item.subject ?? "", item.calendar_user_id ?? "", item.is_group ? "教研组" : "单体教师"]), [teacherQuery.data, teacherSearch]);
  const filteredClasses = useMemo(() => filterBySearch(Array.isArray(classQuery.data) ? classQuery.data : [], classSearch, (item) => [item.business_id, item.name, ...(Array.isArray(item.business_lines) ? item.business_lines : []), ...(Array.isArray(item.product_types) ? item.product_types : []), ...(Array.isArray(item.subjects) ? item.subjects : []), ...(Array.isArray(item.teacher_business_ids) ? item.teacher_business_ids : [])]), [classQuery.data, classSearch]);
  const filteredRooms = useMemo(() => filterBySearch(Array.isArray(roomQuery.data) ? roomQuery.data : [], roomSearch, (item) => [item.business_id, item.name, item.is_active ? "启用" : "停用"]), [roomQuery.data, roomSearch]);
  const filteredSlots = useMemo(() => filterBySearch(Array.isArray(slotQuery.data) ? slotQuery.data : [], slotSearch, (item) => [item.business_id, item.weekday, item.start_time, item.end_time, item.kind ?? "", item.is_open ? "开放" : "关闭"]), [slotQuery.data, slotSearch]);

  const selectedTeacherIds = selectedIds(teacherSelection);
  const selectedClassIds = selectedIds(classSelection);
  const selectedRoomIds = selectedIds(roomSelection);
  const selectedSlotIds = selectedIds(slotSelection);
  // 课程量级大（真实数据近两万条），选中集合与筛选结果的比对放在 memo 里算一次。
  // allFiltered = 「选中的正好是当前筛选结果的全部」，这是能走 filter 入参、不受 1000 条限制的唯一条件。
  const courseSelectionStats = useMemo(() => {
    const ids = selectedIds(courseSelection);
    const visible = new Set(filteredCourses.map((course) => course.id));
    let inFilter = 0;
    for (const id of ids) if (visible.has(id)) inFilter += 1;
    return { ids, offFilter: ids.length - inFilter, allFiltered: filteredCourses.length > 0 && ids.length === filteredCourses.length && inFilter === filteredCourses.length };
  }, [courseSelection, filteredCourses]);
  const selectedCourseIds = courseSelectionStats.ids;
  const clearCourseFilters = () => { setCourseSearch(""); setBusinessLineFilter(""); setProductTypeFilter(""); setClassFilter(""); setTeacherFilter(""); setDateFilter(""); };
  const hasCourseFilters = Boolean(courseSearch || businessLineFilter || productTypeFilter || classFilter || teacherFilter || dateFilter);

  /**
   * 把页面上的 5 个下拉 + 搜索框原样翻译成后端的 CourseSessionFilter。
   * 字段与 filteredCourses 逐条同源，两边命中同一批行；服务端的 search 也是
   * 同样 8 个字段同样顺序拼接后做子串匹配。这里少传一个条件就会多删一批课。
   */
  const courseFilterPayload = useMemo<CourseSessionFilter>(() => {
    const search = courseSearch.trim();
    return {
      ...(search ? { search } : {}),
      ...(businessLineFilter ? { business_line: businessLineFilter } : {}),
      ...(productTypeFilter ? { product_type: productTypeFilter } : {}),
      ...(classFilter ? { class_business_id: classFilter } : {}),
      ...(teacherFilter ? { teacher_business_id: teacherFilter } : {}),
      ...(dateFilter ? { lesson_date: dateFilter } : {}),
    };
  }, [businessLineFilter, classFilter, courseSearch, dateFilter, productTypeFilter, teacherFilter]);

  const courseBatchCount = courseSelectionStats.allFiltered ? filteredCourses.length : selectedCourseIds.length;
  // 逐条选择超过 1000 条时没法提交：后端 object_ids 上限如此，而这批 id 又不等于整个筛选结果。
  const courseBatchBlocked = !courseSelectionStats.allFiltered && selectedCourseIds.length > BATCH_LIMIT;
  const courseBatchScope = (): CourseBatchScope | null => {
    if (courseSelectionStats.allFiltered) return { filter: courseFilterPayload, expected_count: filteredCourses.length };
    if (!selectedCourseIds.length || courseBatchBlocked) return null;
    return { object_ids: selectedCourseIds, expected_count: selectedCourseIds.length };
  };

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
    if (kind === "teacher") setTeacherDraft({ businessId: "", name: "", subject: "", calendarUserId: "", isGroup: false });
    if (kind === "class") setClassDraft({ businessId: "", name: "" });
    if (kind === "room") setRoomDraft({ businessId: "", name: "", isActive: true });
    if (kind === "slot") setSlotDraft({ businessId: "", weekday: "", startTime: "09:00", endTime: "12:00", kind: "", sequence: "0", isOpen: true });
    setEntityDialog({ kind, mode: "create" });
  };

  const openEditEntity = (kind: EntityKind, item: TeacherResponse | ClassGroupResponse | RoomResponse | TimeSlotResponse) => {
    setCampusId(item.campus_id);
    if (kind === "teacher") {
      const teacher = item as TeacherResponse;
      setTeacherDraft({ businessId: teacher.business_id, name: teacher.name, subject: teacher.subject ?? "", calendarUserId: teacher.calendar_user_id ?? "", isGroup: Boolean(teacher.is_group) });
    } else if (kind === "class") {
      const classGroup = item as ClassGroupResponse;
      setClassDraft({ businessId: classGroup.business_id, name: classGroup.name });
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
      const data = { campus_id: campusId, business_id: businessId, name: teacherDraft.name.trim() || businessId, subject: teacherDraft.subject.trim(), calendar_user_id: teacherDraft.calendarUserId.trim() || null, is_group: teacherDraft.isGroup };
      if (editing && entityDialog.id) updateTeacher.mutate({ objectId: entityDialog.id, data }); else createTeacher.mutate({ data });
    } else if (entityDialog.kind === "class") {
      const businessId = classDraft.businessId.trim();
      // 班级只写身份：班型/业务线/教师是课次的属性，由后端从 course_sessions 聚合。
      const data = { campus_id: campusId, business_id: businessId, name: classDraft.name.trim() || businessId };
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
    const scope = courseBatchScope();
    if (!batchAction || !scope) return;
    if (batchAction === "delete") {
      batchDeleteCourses.mutate(scope);
    } else if (batchAction === "date" && batchDate) {
      batchUpdateCourses.mutate({ ...scope, lesson_date: batchDate });
    } else if (batchAction === "room" && batchRoom) {
      batchUpdateCourses.mutate({ ...scope, original_room_business_id: batchRoom });
    }
  };

  const clearEntitySelection = (kind: EntityKind) => {
    if (kind === "teacher") setTeacherSelection({});
    else if (kind === "class") setClassSelection({});
    else if (kind === "room") setRoomSelection({});
    else setSlotSelection({});
  };

  const selectAllFiltered = (items: Array<{ id: string }>, apply: (selection: RowSelectionState) => void) => {
    // 整份替换而不是合并：结果就是「当前筛选结果的全部」，
    // 顺带把其它页、其它筛选条件下残留的勾选清掉。不再截断——课程走 filter 入参没有上限，
    // 且与表头复选框（getIsAllRowsSelected）保持同一个语义。
    apply(Object.fromEntries(items.map((item) => [item.id, true])));
  };

  const selectAllForEntity = (kind: EntityKind) => {
    if (kind === "teacher") selectAllFiltered(filteredTeachers, setTeacherSelection);
    else if (kind === "class") selectAllFiltered(filteredClasses, setClassSelection);
    else if (kind === "room") selectAllFiltered(filteredRooms, setRoomSelection);
    else selectAllFiltered(filteredSlots, setSlotSelection);
  };

  const offFilterCount = (selection: RowSelectionState, items: Array<{ id: string }>) => {
    const visible = new Set(items.map((item) => item.id));
    return selectedIds(selection).filter((id) => !visible.has(id)).length;
  };

  const idsForEntityKind = (kind: EntityKind) => kind === "teacher"
    ? selectedTeacherIds
    : kind === "class"
      ? selectedClassIds
      : kind === "room"
        ? selectedRoomIds
        : selectedSlotIds;

  const openEntityBatch = (kind: EntityKind, action: EntityBatchAction) => {
    setEntityBatchValue(action === "room-status" || action === "slot-status" || action === "teacher-group" ? "true" : "");
    setEntityBatch({ kind, action });
  };

  const runEntityBatch = () => {
    if (!entityBatch) return;
    const objectIds = idsForEntityKind(entityBatch.kind);
    if (!objectIds.length || objectIds.length > BATCH_LIMIT) return;
    const resource = entityConfig(entityBatch.kind).resource;
    if (entityBatch.action === "delete") {
      batchDeleteEntities.mutate({ resource, objectIds });
      return;
    }
    const changes: Record<string, string | boolean | null> = {};
    if (entityBatch.action === "teacher-subject") changes.subject = entityBatchValue.trim();
    if (entityBatch.action === "teacher-calendar") changes.calendar_user_id = entityBatchValue.trim() || null;
    if (entityBatch.action === "teacher-group") changes.is_group = entityBatchValue === "true";
    if (entityBatch.action === "room-status") changes.is_active = entityBatchValue === "true";
    if (entityBatch.action === "slot-status") changes.is_open = entityBatchValue === "true";
    batchUpdateEntities.mutate({ resource, objectIds, changes });
  };

  const actionsColumn = <T,>(cell: (row: T) => ReactNode): ColumnDef<T> => ({
    id: "actions",
    header: () => <ColumnHeader>操作</ColumnHeader>,
    enableSorting: false,
    cell: ({ row }) => cell(row.original),
  });

  const teacherColumns: ColumnDef<TeacherResponse>[] = [
    { id: "teacher", header: () => <ColumnHeader>教师</ColumnHeader>, cell: ({ row }) => <IdentityValue item={row.original} className="w-48" /> },
    { accessorFn: (row) => row.is_group ? "教研组" : "单体教师", id: "teacher_type", header: () => <ColumnHeader>教师类型</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.is_group ? "教研组" : "单体教师"} className="w-20" /> },
    { accessorKey: "subject", header: () => <ColumnHeader>学科</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.subject} className="w-24" /> },
    { accessorKey: "calendar_user_id", header: () => <ColumnHeader>飞书日程账号</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.calendar_user_id} className="w-40" /> },
    ...(!readOnly ? [actionsColumn<TeacherResponse>((item) => <RowActions editLabel="编辑教师" onEdit={() => openEditEntity("teacher", item)} deleting={deletingKey === `teachers:${item.id}`} onDelete={() => confirmDelete({ id: item.id, label: identityLabel(item), resource: "teachers", resourceLabel: "教师" })} />)] : []),
  ];
  // 走班制下一个班同时有多个班型、多个教师（暑期集训营OMO4班 就同时有 含数学 / 无数学）。
  // 这几列全是多值：显示前 2 个 +「+N」展开，列宽固定，展开只增加行高、不撑宽表格。
  const classColumns: ColumnDef<ClassGroupResponse>[] = [
    { id: "class", header: () => <ColumnHeader>班级</ColumnHeader>, cell: ({ row }) => <IdentityValue item={row.original} className="w-44" /> },
    { id: "business_lines", header: () => <ColumnHeader>业务线</ColumnHeader>, enableSorting: false, cell: ({ row }) => <TagList values={row.original.business_lines} className="w-16" empty={emptyClassHint(row.original)} /> },
    { id: "product_types", header: () => <ColumnHeader>班型</ColumnHeader>, enableSorting: false, cell: ({ row }) => <TagList values={row.original.product_types} className="w-40" empty={emptyClassHint(row.original)} /> },
    { id: "teacher_business_ids", header: () => <ColumnHeader>教师</ColumnHeader>, enableSorting: false, cell: ({ row }) => <TagList values={row.original.teacher_business_ids} className="w-36" empty={emptyClassHint(row.original)} /> },
    { accessorKey: "session_count", header: () => <ColumnHeader>课次数</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.session_count} className="w-12 tabular-nums" /> },
    { id: "tracks", header: () => <ColumnHeader>走班明细</ColumnHeader>, enableSorting: false, cell: ({ row }) => <TracksButton item={row.original} onOpen={() => setTracksTarget(row.original)} /> },
    ...(!readOnly ? [actionsColumn<ClassGroupResponse>((item) => <RowActions editLabel="编辑班级" onEdit={() => openEditEntity("class", item)} deleting={deletingKey === `class-groups:${item.id}`} onDelete={() => confirmDelete({ id: item.id, label: identityLabel(item), resource: "class-groups", resourceLabel: "班级" })} />)] : []),
  ];
  const roomColumns: ColumnDef<RoomResponse>[] = [
    { id: "room", header: () => <ColumnHeader>教室</ColumnHeader>, cell: ({ row }) => <IdentityValue item={row.original} className="w-48" /> },
    { accessorFn: (row) => row.is_active ? "启用" : "停用", id: "active", header: () => <ColumnHeader>状态</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.is_active ? "启用" : "停用"} className="w-16" /> },
    ...(!readOnly ? [actionsColumn<RoomResponse>((item) => <RowActions editLabel="编辑教室" onEdit={() => openEditEntity("room", item)} deleting={deletingKey === `rooms:${item.id}`} onDelete={() => confirmDelete({ id: item.id, label: identityLabel(item), resource: "rooms", resourceLabel: "教室" })} />)] : []),
  ];
  const slotColumns: ColumnDef<TimeSlotResponse>[] = [
    { accessorKey: "business_id", header: () => <ColumnHeader>时段 ID</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.business_id} className="w-40" /> },
    { accessorKey: "weekday", header: () => <ColumnHeader>星期</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.weekday} className="w-16" /> },
    { accessorKey: "start_time", header: () => <ColumnHeader>开始</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.start_time} className="w-16 tabular-nums" /> },
    { accessorKey: "end_time", header: () => <ColumnHeader>结束</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.end_time} className="w-16 tabular-nums" /> },
    { accessorKey: "kind", header: () => <ColumnHeader>类型</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.kind} className="w-16" /> },
    { accessorKey: "sequence", header: () => <ColumnHeader>序号</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.sequence} className="w-14 tabular-nums" /> },
    { id: "open", header: () => <ColumnHeader>状态</ColumnHeader>, accessorFn: (row) => row.is_open ? "开放" : "关闭", cell: ({ row }) => <TableText value={row.original.is_open ? "开放" : "关闭"} className="w-16" /> },
    ...(!readOnly ? [actionsColumn<TimeSlotResponse>((item) => <RowActions editLabel="编辑时段" onEdit={() => openEditEntity("slot", item)} deleting={deletingKey === `time-slots:${item.id}`} onDelete={() => confirmDelete({ id: item.id, label: item.business_id, resource: "time-slots", resourceLabel: "时段" })} />)] : []),
  ];
  // 每一列都给了固定宽度：排序换掉当前页数据时列宽不会跳，整表宽度也就固定下来了。
  const courseColumns: ColumnDef<CourseSessionResponse>[] = [
    ...(visibleCourseColumns.business_id ? [{ accessorKey: "business_id", header: () => <ColumnHeader>课程 ID</ColumnHeader>, cell: ({ row }: { row: { original: CourseSessionResponse } }) => <CopyableId value={row.original.business_id} className="w-32" /> } as ColumnDef<CourseSessionResponse>] : []),
    ...(visibleCourseColumns.business_line ? [{ accessorKey: "business_line", header: () => <ColumnHeader>业务线</ColumnHeader>, cell: ({ row }: { row: { original: CourseSessionResponse } }) => <TableText value={row.original.business_line} className="w-16" /> } as ColumnDef<CourseSessionResponse>] : []),
    ...(visibleCourseColumns.product_type ? [{ id: "product_type", header: () => <ColumnHeader>产品班型</ColumnHeader>, accessorFn: (row: CourseSessionResponse) => courseProducts(row).join(" / "), cell: ({ row }: { row: { original: CourseSessionResponse } }) => <TagList values={courseProducts(row.original)} className="w-44" /> } as ColumnDef<CourseSessionResponse>] : []),
    { accessorKey: "class_business_id", header: () => <ColumnHeader>班级标签</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.class_business_id} className="w-32" /> },
    { id: "teacher_business_id", header: () => <ColumnHeader>教师</ColumnHeader>, accessorFn: (row) => courseTeachers(row).join(" / "), cell: ({ row }) => <TagList values={courseTeachers(row.original)} className="w-36" /> },
    ...(visibleCourseColumns.stage ? [{ id: "stage", header: () => <ColumnHeader>编排阶段</ColumnHeader>, accessorFn: (row: CourseSessionResponse) => courseStages(row).join(" / "), cell: ({ row }: { row: { original: CourseSessionResponse } }) => <TagList values={courseStages(row.original)} className="w-36" /> } as ColumnDef<CourseSessionResponse>] : []),
    ...(visibleCourseColumns.lesson_name ? [{ id: "lesson_name", header: () => <ColumnHeader>课节名称</ColumnHeader>, accessorFn: (row: CourseSessionResponse) => courseLessons(row).join(" / "), cell: ({ row }: { row: { original: CourseSessionResponse } }) => <TagList values={courseLessons(row.original)} className="w-48" /> } as ColumnDef<CourseSessionResponse>] : []),
    { accessorKey: "session_no", header: () => <ColumnHeader>课次序号</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.session_no} className="w-12 tabular-nums" /> },
    { accessorKey: "lesson_date", header: () => <ColumnHeader>上课日期</ColumnHeader>, cell: ({ row }) => <TableText value={row.original.lesson_date} className="w-24 tabular-nums" /> },
    ...(visibleCourseColumns.candidate_time ? [{ id: "candidate_time", header: () => <ColumnHeader>候选时段</ColumnHeader>, accessorFn: (row: CourseSessionResponse) => courseClockWindows(row).join(" / "), cell: ({ row }: { row: { original: CourseSessionResponse } }) => <TagList values={courseClockWindows(row.original)} className="w-36 tabular-nums" /> } as ColumnDef<CourseSessionResponse>] : []),
    { id: "candidate_rooms", header: () => <ColumnHeader>候选教室</ColumnHeader>, accessorFn: (row) => courseRooms(row).join(" / "), cell: ({ row }) => <TagList values={courseRooms(row.original)} className="w-28" /> },
    ...(!readOnly ? [actionsColumn<CourseSessionResponse>((item) => <RowActions editLabel="编辑课程" editTitle="编辑日期、教室和日程账号" onEdit={() => { setCourseDraft(draftFromCourse(item)); setCourseDialogMode("edit"); }} deleting={deletingKey === `course-sessions:${item.id}`} onDelete={() => confirmDelete({ id: item.id, label: item.business_id, resource: "course-sessions", resourceLabel: "课程" })} />)] : []),
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
      {readOnly ? <section className="border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-600">教师、班级、教室和课程场次在所有课表方案中复用；为避免影响其他方案，只有管理员可以修改或导入。</section> : null}
      {importReport ? <ImportReportPanel report={importReport} onDismiss={() => setImportReport(null)} /> : null}
      {loading ? <LoadingState /> : failed ? <ErrorState retry={refresh} /> : (
        <Tabs defaultValue="teachers">
          <TabsList><TabsTrigger value="teachers">教师</TabsTrigger><TabsTrigger value="classes">班级</TabsTrigger><TabsTrigger value="rooms">教室</TabsTrigger><TabsTrigger value="slots">时段</TabsTrigger><TabsTrigger value="courses">课程场次</TabsTrigger></TabsList>
          <TabsContent value="teachers" className="pt-4"><EntityToolbar label="教师" total={teacherQuery.data?.length ?? 0} filtered={filteredTeachers.length} selected={selectedTeacherIds.length} offFilter={offFilterCount(teacherSelection, filteredTeachers)} onSelectAll={() => selectAllForEntity("teacher")} onClearSelection={() => clearEntitySelection("teacher")} search={teacherSearch} setSearch={setTeacherSearch} readOnly={readOnly} onAdd={() => openCreateEntity("teacher")} disabled={!campusId} actions={[{ label: "批量改教师类型", action: "teacher-group" }, { label: "批量改学科", action: "teacher-subject" }, { label: "批量改日程账号", action: "teacher-calendar" }]} onBatch={(action) => openEntityBatch("teacher", action)} /><DataTable columns={teacherColumns} data={filteredTeachers} paginated defaultPageSize={50} selectable={!readOnly} getRowId={(row) => row.id} selectedRowIds={teacherSelection} onSelectionChange={setTeacherSelection} /></TabsContent>
          <TabsContent value="classes" className="pt-4"><EntityToolbar label="班级" total={classQuery.data?.length ?? 0} filtered={filteredClasses.length} selected={selectedClassIds.length} offFilter={offFilterCount(classSelection, filteredClasses)} onSelectAll={() => selectAllForEntity("class")} onClearSelection={() => clearEntitySelection("class")} search={classSearch} setSearch={setClassSearch} readOnly={readOnly} onAdd={() => openCreateEntity("class")} disabled={!campusId} actions={[]} onBatch={(action) => openEntityBatch("class", action)} note="班型、教师、课次数由课程场次实时汇总，不能在班级上直接改；要改教师请到「课程场次」页签改对应课次。" /><DataTable columns={classColumns} data={filteredClasses} paginated defaultPageSize={50} selectable={!readOnly} getRowId={(row) => row.id} selectedRowIds={classSelection} onSelectionChange={setClassSelection} /></TabsContent>
          <TabsContent value="rooms" className="pt-4"><EntityToolbar label="教室" total={roomQuery.data?.length ?? 0} filtered={filteredRooms.length} selected={selectedRoomIds.length} offFilter={offFilterCount(roomSelection, filteredRooms)} onSelectAll={() => selectAllForEntity("room")} onClearSelection={() => clearEntitySelection("room")} search={roomSearch} setSearch={setRoomSearch} readOnly={readOnly} onAdd={() => openCreateEntity("room")} disabled={!campusId} actions={[{ label: "批量启用/停用", action: "room-status" }]} onBatch={(action) => openEntityBatch("room", action)} /><DataTable columns={roomColumns} data={filteredRooms} paginated defaultPageSize={50} selectable={!readOnly} getRowId={(row) => row.id} selectedRowIds={roomSelection} onSelectionChange={setRoomSelection} /></TabsContent>
          <TabsContent value="slots" className="pt-4"><EntityToolbar label="时段" total={slotQuery.data?.length ?? 0} filtered={filteredSlots.length} selected={selectedSlotIds.length} offFilter={offFilterCount(slotSelection, filteredSlots)} onSelectAll={() => selectAllForEntity("slot")} onClearSelection={() => clearEntitySelection("slot")} search={slotSearch} setSearch={setSlotSearch} readOnly={readOnly} onAdd={() => openCreateEntity("slot")} disabled={!campusId} actions={[{ label: "批量开放/关闭", action: "slot-status" }]} onBatch={(action) => openEntityBatch("slot", action)} /><DataTable columns={slotColumns} data={filteredSlots} paginated defaultPageSize={50} selectable={!readOnly} getRowId={(row) => row.id} selectedRowIds={slotSelection} onSelectionChange={setSlotSelection} /></TabsContent>
          <TabsContent value="courses" className="pt-4">
            <CourseToolbar
              total={courseQuery.data?.length ?? 0}
              filtered={filteredCourses.length}
              selected={selectedCourseIds.length}
              allFiltered={courseSelectionStats.allFiltered}
              blocked={courseBatchBlocked}
              offFilter={courseSelectionStats.offFilter}
              onSelectAll={() => selectAllFiltered(filteredCourses, setCourseSelection)}
              onClearSelection={() => setCourseSelection({})}
              columns={visibleCourseColumns}
              setColumns={setVisibleCourseColumns}
              columnPanelOpen={courseColumnPanel}
              toggleColumnPanel={() => setCourseColumnPanel((open) => !open)}
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
          <DialogDescription className="mt-1 text-sm text-zinc-500">{entityDialog?.kind === "slot" ? "时段用于描述可排课时间窗口；课程自身的固定开始、结束时间仍以课程数据为准。" : entityDialog?.kind === "class" ? "班级只登记身份。班型、业务线、教师由这个班的课程场次实时汇总，新建的班在排课次之前这几项都是空的。" : entityDialog?.kind === "teacher" ? "单体教师同一时间只能上一节课；教研组允许并行开课。修改后从下一次求解开始生效，不会自动重排已发布课表。" : "名称与业务标签一致时，名称可以留空，系统会自动复用标签。"}</DialogDescription>
          <form className="mt-5 space-y-4" onSubmit={submitEntity}>
            <label className="block text-sm text-zinc-700">所属校区<Select selectSize="md" containerClassName="mt-1.5" value={campusId} onChange={(event) => setCampusId(event.target.value)} required>{(Array.isArray(campusQuery.data) ? campusQuery.data : []).map((campus) => <option key={campus.id} value={campus.id}>{campus.name}</option>)}</Select></label>
            {entityDialog?.kind === "teacher" ? <><label className="block text-sm text-zinc-700">教师标签<input className={inputClass} value={teacherDraft.businessId} onChange={(event) => setTeacherDraft({ ...teacherDraft, businessId: event.target.value })} required /></label><label className="block text-sm text-zinc-700">显示名称（选填）<input className={inputClass} value={teacherDraft.name} onChange={(event) => setTeacherDraft({ ...teacherDraft, name: event.target.value })} placeholder="留空则与标签一致" /></label><FormSelect label="教师类型" value={teacherDraft.isGroup ? "group" : "person"} setValue={(value) => setTeacherDraft({ ...teacherDraft, isGroup: value === "group" })} required options={[{ value: "person", label: "单体教师（时间不可重叠）" }, { value: "group", label: "教研组（允许并行开课）" }]} /><label className="block text-sm text-zinc-700">学科<input className={inputClass} value={teacherDraft.subject} onChange={(event) => setTeacherDraft({ ...teacherDraft, subject: event.target.value })} /></label><label className="block text-sm text-zinc-700">飞书日程账号（选填）<input className={inputClass} value={teacherDraft.calendarUserId} onChange={(event) => setTeacherDraft({ ...teacherDraft, calendarUserId: event.target.value })} /></label></> : entityDialog?.kind === "class" ? <><label className="block text-sm text-zinc-700">班级标签<input className={inputClass} value={classDraft.businessId} onChange={(event) => setClassDraft({ ...classDraft, businessId: event.target.value })} required /></label><label className="block text-sm text-zinc-700">显示名称（选填）<input className={inputClass} value={classDraft.name} onChange={(event) => setClassDraft({ ...classDraft, name: event.target.value })} placeholder="留空则与标签一致" /></label></> : entityDialog?.kind === "room" ? <><label className="block text-sm text-zinc-700">教室标签<input className={inputClass} value={roomDraft.businessId} onChange={(event) => setRoomDraft({ ...roomDraft, businessId: event.target.value })} required /></label><label className="block text-sm text-zinc-700">显示名称（选填）<input className={inputClass} value={roomDraft.name} onChange={(event) => setRoomDraft({ ...roomDraft, name: event.target.value })} placeholder="留空则与标签一致" /></label><label className="flex items-center gap-2 text-sm text-zinc-700"><input type="checkbox" checked={roomDraft.isActive} onChange={(event) => setRoomDraft({ ...roomDraft, isActive: event.target.checked })} />启用教室</label></> : <><label className="block text-sm text-zinc-700">时段标签<input className={inputClass} value={slotDraft.businessId} onChange={(event) => setSlotDraft({ ...slotDraft, businessId: event.target.value })} required /></label><div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm text-zinc-700">星期<input className={inputClass} value={slotDraft.weekday} onChange={(event) => setSlotDraft({ ...slotDraft, weekday: event.target.value })} placeholder="例如：周一" required /></label><label className="block text-sm text-zinc-700">类型<input className={inputClass} value={slotDraft.kind} onChange={(event) => setSlotDraft({ ...slotDraft, kind: event.target.value })} placeholder="例如：上午" /></label><label className="block text-sm text-zinc-700">开始时间<input className={inputClass} type="time" value={slotDraft.startTime} onChange={(event) => setSlotDraft({ ...slotDraft, startTime: event.target.value })} required /></label><label className="block text-sm text-zinc-700">结束时间<input className={inputClass} type="time" value={slotDraft.endTime} onChange={(event) => setSlotDraft({ ...slotDraft, endTime: event.target.value })} required /></label><label className="block text-sm text-zinc-700">排序序号<input className={inputClass} type="number" min="0" value={slotDraft.sequence} onChange={(event) => setSlotDraft({ ...slotDraft, sequence: event.target.value })} /></label></div><label className="flex items-center gap-2 text-sm text-zinc-700"><input type="checkbox" checked={slotDraft.isOpen} onChange={(event) => setSlotDraft({ ...slotDraft, isOpen: event.target.checked })} />开放时段</label></>}
            <div className="flex justify-end gap-2 border-t border-zinc-100 pt-4"><Button type="button" variant="outline" onClick={closeEntityDialog}>取消</Button><Button type="submit" disabled={isCreating || !campusId}>{isCreating ? "保存中" : "保存"}</Button></div>
          </form>
        </DialogContent>
      </Dialog>

      <CourseEditorDialog
        open={courseDialogMode !== null}
        mode={courseDialogMode ?? "create"}
        draft={courseDraft}
        setDraft={setCourseDraft}
        campuses={Array.isArray(campusQuery.data) ? campusQuery.data : []}
        teachers={Array.isArray(teacherQuery.data) ? teacherQuery.data : []}
        classes={Array.isArray(classQuery.data) ? classQuery.data : []}
        rooms={Array.isArray(roomQuery.data) ? roomQuery.data : []}
        saving={isCourseSaving}
        close={closeCourseDialog}
        submit={submitCourse}
      />
      <BatchCourseDialog open={batchAction !== null} action={batchAction ?? "date"} affected={courseBatchCount} allFiltered={courseSelectionStats.allFiltered} date={batchDate} setDate={setBatchDate} room={batchRoom} setRoom={setBatchRoom} rooms={Array.isArray(roomQuery.data) ? roomQuery.data : []} saving={isBatchSaving} close={() => setBatchAction(null)} submit={runBatchAction} />
      <EntityBatchDialog open={entityBatch !== null} state={entityBatch} selected={entityBatch ? idsForEntityKind(entityBatch.kind).length : 0} value={entityBatchValue} setValue={setEntityBatchValue} saving={batchUpdateEntities.isPending || batchDeleteEntities.isPending} close={() => setEntityBatch(null)} submit={runEntityBatch} />
      <ClassTracksDialog item={tracksTarget} close={() => setTracksTarget(null)} />
      <ConfirmDialog open={deleteTarget !== null} title={deleteTarget ? `删除${deleteTarget.resourceLabel}` : "删除记录"} description={deleteTarget ? `确认删除“${deleteTarget.label}”？被课程、课表版本或飞书日程引用的记录会被系统拦截。` : ""} confirmLabel="确认删除" danger pending={remove.isPending} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }} onConfirm={executeDelete} />
    </div>
  );
}

function CourseToolbar({ total, filtered, selected, allFiltered, blocked, offFilter, onSelectAll, onClearSelection, columns, setColumns, columnPanelOpen, toggleColumnPanel, search, setSearch, businessLine, setBusinessLine, productType, setProductType, classBusinessId, setClassBusinessId, teacherBusinessId, setTeacherBusinessId, lessonDate, setLessonDate, options, hasFilters, clearFilters, readOnly, onAdd, onBatch }: { total: number; filtered: number; selected: number; allFiltered: boolean; blocked: boolean; offFilter: number; onSelectAll: () => void; onClearSelection: () => void; columns: Record<CourseColumnKey, boolean>; setColumns: Dispatch<SetStateAction<Record<CourseColumnKey, boolean>>>; columnPanelOpen: boolean; toggleColumnPanel: () => void; search: string; setSearch: (value: string) => void; businessLine: string; setBusinessLine: (value: string) => void; productType: string; setProductType: (value: string) => void; classBusinessId: string; setClassBusinessId: (value: string) => void; teacherBusinessId: string; setTeacherBusinessId: (value: string) => void; lessonDate: string; setLessonDate: (value: string) => void; options: { businessLines: string[]; productTypes: string[]; classes: string[]; teachers: string[] }; hasFilters: boolean; clearFilters: () => void; readOnly: boolean; onAdd: () => void; onBatch: (action: CourseBatchAction) => void }) {
  const disabled = selected === 0 || blocked;
  return <div className="mb-3 space-y-3 border border-zinc-200 bg-white p-3">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-zinc-500">
        <span className="tabular-nums">筛选 {filtered} / {total} 条</span>
        {!readOnly ? <SelectionControls selected={selected} filtered={filtered} allFiltered={allFiltered} offFilter={offFilter} onSelectAll={onSelectAll} onClear={onClearSelection} /> : null}
      </div>
      {!readOnly ? <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={disabled} onClick={() => onBatch("date")}><CalendarDays className="size-3.5" />批量改日期</Button><Button size="sm" variant="outline" disabled={disabled} onClick={() => onBatch("room")}>批量改教室</Button><Button size="sm" variant="outline" disabled={disabled} onClick={() => onBatch("delete")}><Trash2 className="size-3.5 text-red-600" />批量删除</Button><Button size="sm" onClick={onAdd}><Plus className="size-3.5" />新增课程</Button></div> : <span className="text-xs text-zinc-400">共享主数据由管理员维护</span>}
    </div>
    {blocked ? <CourseBatchBlockedNotice selected={selected} /> : null}
    <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-6">
      <label className="relative min-w-0"><Search className="absolute left-2.5 top-2 size-3.5 text-zinc-400" /><input aria-label="搜索课程" maxLength={200} className={`${compactInputClass} w-full min-w-0 pl-8`} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="课程、班级、教师、教室" /></label>
      <FilterSelect label="全部业务线" value={businessLine} setValue={setBusinessLine} options={options.businessLines} />
      <FilterSelect label="全部班型" value={productType} setValue={setProductType} options={options.productTypes} />
      <FilterSelect label="全部班级" value={classBusinessId} setValue={setClassBusinessId} options={options.classes} />
      <FilterSelect label="全部教师" value={teacherBusinessId} setValue={setTeacherBusinessId} options={options.teachers} />
      <div className="flex min-w-0 gap-2"><input aria-label="筛选上课日期" type="date" className={`${compactInputClass} min-w-0 flex-1`} value={lessonDate} onChange={(event) => setLessonDate(event.target.value)} />{hasFilters ? <Button size="icon" variant="ghost" title="清除筛选" aria-label="清除筛选" onClick={clearFilters}><X className="size-3.5" /></Button> : null}</div>
    </div>
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-zinc-100 pt-2">
      <Button size="sm" variant="ghost" aria-expanded={columnPanelOpen} onClick={toggleColumnPanel}><Columns3 className="size-3.5" />显示列</Button>
      {columnPanelOpen ? <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-zinc-600">
        {COURSE_OPTIONAL_COLUMNS.map((column) => <label key={column.key} className="flex items-center gap-1.5">
          <input type="checkbox" className="size-3.5 accent-blue-600" checked={columns[column.key]} onChange={(event) => setColumns((current) => ({ ...current, [column.key]: event.target.checked }))} />
          {column.label}
        </label>)}
        <span className="text-zinc-400">课程 ID 是内部标识，默认隐藏；打开后截断显示，悬停看全量、点击图标复制。</span>
      </div> : null}
    </div>
  </div>;
}

/**
 * 选中数量 + 全选筛选结果 + 一键清除（清的是整个选中集合，含其它分页、其它筛选条件下的残留）。
 * allFiltered 时文案说「全部 N 条」——课程走的是按条件整批处理，不是 N 个 id，说清楚这一点很重要。
 */
function SelectionControls({ selected, filtered, allFiltered, offFilter, onSelectAll, onClear }: { selected: number; filtered: number; allFiltered: boolean; offFilter: number; onSelectAll: () => void; onClear: () => void }) {
  return <>
    <span className={cn("tabular-nums", selected > 0 ? "font-medium text-blue-700" : "text-zinc-400")}>{allFiltered ? `已选 全部 ${filtered} 条` : `已选 ${selected} 条`}</span>
    {offFilter > 0 ? <span className="tabular-nums text-amber-700">其中 {offFilter} 条不在当前筛选内</span> : null}
    <Button size="sm" variant="outline" disabled={filtered === 0 || allFiltered} onClick={onSelectAll} title={allFiltered ? `当前筛选出的 ${filtered} 条已全部选中` : `选中当前筛选出的 ${filtered} 条`}>
      <ListChecks className="size-3.5" />全选筛选结果{filtered > 0 ? `（${filtered} 条）` : ""}
    </Button>
    <Button size="sm" variant="ghost" disabled={selected === 0} onClick={onClear}><Eraser className="size-3.5" />清除选择</Button>
  </>;
}

/**
 * 课程逐条选择超过 1000 条、又不是「整个筛选结果」时的死角：
 * 后端 object_ids 上限 1000，而 filter 入参只能表达完整的筛选结果，表达不了「全选后再排除几条」。
 */
function CourseBatchBlockedNotice({ selected }: { selected: number }) {
  return <p className="border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">已选 {selected} 条，逐条选择单次上限 {BATCH_LIMIT} 条，批量操作已暂时禁用。点「全选筛选结果」按条件整批处理不受这个上限限制；如果要的就是这几条，请先收窄筛选条件再全选。</p>;
}

function BatchLimitNotice({ selected }: { selected: number }) {
  return <p className="border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">已选 {selected} 条，超过后端批量接口单次上限 {BATCH_LIMIT} 条，批量操作已暂时禁用。请先「清除选择」再缩小筛选范围分批处理。</p>;
}

function FilterSelect({ label, value, setValue, options }: { label: string; value: string; setValue: (value: string) => void; options: string[] }) {
  // min-w-0：select 的固有宽度由最长选项决定（班级标签很长），不压住会把整行网格撑出容器。
  return (
    <Select
      aria-label={label}
      selectSize="sm"
      containerClassName="w-full min-w-0"
      value={value}
      onChange={(event) => setValue(event.target.value)}
    >
      <option value="">{label}</option>
      {options.map((option) => (
        <option key={option} value={option}>{option}</option>
      ))}
    </Select>
  );
}

function CourseEditorDialog({
  open,
  mode,
  draft,
  setDraft,
  campuses: rawCampuses,
  teachers: rawTeachers,
  classes: rawClasses,
  rooms: rawRooms,
  saving,
  close,
  submit,
}: {
  open: boolean;
  mode: "create" | "edit";
  draft: CourseDraft;
  setDraft: (value: CourseDraft) => void;
  campuses: Array<{ id: string; name: string }>;
  teachers: TeacherResponse[];
  classes: ClassGroupResponse[];
  rooms: RoomResponse[];
  saving: boolean;
  close: () => void;
  submit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const editing = mode === "edit";
  const campuses = Array.isArray(rawCampuses) ? rawCampuses : [];
  const teachers = Array.isArray(rawTeachers) ? rawTeachers : [];
  const classes = Array.isArray(rawClasses) ? rawClasses : [];
  const rooms = Array.isArray(rawRooms) ? rawRooms : [];

  const selectClass = (classBusinessId: string) => {
    const matched = classes.find((item) => item.business_id === classBusinessId);
    const only = matched?.tracks?.length === 1 ? matched.tracks[0] : null;
    setDraft({
      ...draft,
      classBusinessId,
      businessLine: only?.business_line ?? draft.businessLine,
      productType: only?.product_type ?? draft.productType,
      subject: only?.subject ?? draft.subject,
    });
  };
  return <Dialog open={open} onOpenChange={(next) => { if (!next) close(); }}><DialogContent className="max-h-[90vh] max-w-4xl overflow-y-auto"><DialogTitle className="text-base font-semibold">{editing ? `编辑课程 ${draft.businessId}` : "新增课程场次"}</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">{editing ? "现有课程只调整上课日期、授课教室和飞书日程账号；教师与固定时段保持原数据。" : "新增时确定教师和固定上课时段，保存后只允许调整日期与教室。"}</DialogDescription><form className="mt-5 space-y-5" onSubmit={submit}>{editing ? <div className="grid gap-3 border border-zinc-200 bg-zinc-50 p-4 text-sm sm:grid-cols-2 lg:grid-cols-4"><ReadOnlyField label="班级" value={draft.classBusinessId} /><ReadOnlyField label="教师" value={draft.teacherBusinessId} /><ReadOnlyField label="固定时段" value={`${draft.fixedStartTime}-${draft.fixedEndTime}`} /><ReadOnlyField label="课次序号" value={draft.sessionNo} /></div> : <><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"><FormSelect label="所属校区" value={draft.campusId} setValue={(value) => setDraft({ ...draft, campusId: value })} required options={campuses.map((item) => ({ value: item.id, label: item.name }))} /><FormInput label="课程业务 ID" value={draft.businessId} setValue={(value) => setDraft({ ...draft, businessId: value })} required /><FormSelect label="班级" value={draft.classBusinessId} setValue={selectClass} required options={classes.map((item) => ({ value: item.business_id, label: identityLabel(item) }))} /><FormSelect label="教师" value={draft.teacherBusinessId} setValue={(value) => setDraft({ ...draft, teacherBusinessId: value })} required options={teachers.map((item) => ({ value: item.business_id, label: identityLabel(item) }))} /><FormInput label="业务线" value={draft.businessLine} setValue={(value) => setDraft({ ...draft, businessLine: value })} /><FormInput label="班型" value={draft.productType} setValue={(value) => setDraft({ ...draft, productType: value })} /><FormInput label="课节名称" value={draft.lessonName} setValue={(value) => setDraft({ ...draft, lessonName: value })} required /><FormInput label="学科" value={draft.subject} setValue={(value) => setDraft({ ...draft, subject: value })} /><FormInput label="课次序号" type="number" min="0" value={draft.sessionNo} setValue={(value) => setDraft({ ...draft, sessionNo: value })} required /><FormInput label="固定开始时间" type="time" value={draft.fixedStartTime} setValue={(value) => setDraft({ ...draft, fixedStartTime: value })} required /><FormInput label="固定结束时间" type="time" value={draft.fixedEndTime} setValue={(value) => setDraft({ ...draft, fixedEndTime: value })} required /><FormInput label="时长（分钟）" type="number" min="1" value={draft.durationMinutes} setValue={(value) => setDraft({ ...draft, durationMinutes: value })} required /><FormInput label="编排来源" value={draft.scheduleSource} setValue={(value) => setDraft({ ...draft, scheduleSource: value })} /><FormInput label="编排阶段" value={draft.stage} setValue={(value) => setDraft({ ...draft, stage: value })} /><FormInput label="计划课次" type="number" min="0" value={draft.plannedSessions} setValue={(value) => setDraft({ ...draft, plannedSessions: value })} /><FormInput label="计划课时" type="number" min="0" value={draft.plannedHours} setValue={(value) => setDraft({ ...draft, plannedHours: value })} /></div><label className="flex items-center gap-2 text-sm text-zinc-700"><input type="checkbox" checked={draft.isLocked} onChange={(event) => setDraft({ ...draft, isLocked: event.target.checked })} />创建为锁定课程（求解时保持原日期和教室）</label></>}<div className="grid gap-4 border-t border-zinc-100 pt-4 sm:grid-cols-3"><FormInput label="上课日期" type="date" value={draft.lessonDate} setValue={(value) => setDraft({ ...draft, lessonDate: value })} required /><FormSelect label="授课教室" value={draft.roomBusinessId} setValue={(value) => setDraft({ ...draft, roomBusinessId: value })} required options={rooms.filter((item) => item.is_active || item.business_id === draft.roomBusinessId).map((item) => ({ value: item.business_id, label: identityLabel(item) }))} /><FormInput label="飞书日程账号（选填）" value={draft.calendarUserId} setValue={(value) => setDraft({ ...draft, calendarUserId: value })} /></div><div className="flex justify-end gap-2 border-t border-zinc-100 pt-4"><Button type="button" variant="outline" onClick={close}>取消</Button><Button type="submit" disabled={saving}>{saving ? "保存中" : "保存课程"}</Button></div></form></DialogContent></Dialog>;
}

function BatchCourseDialog({ open, action, affected, allFiltered, date, setDate, room, setRoom, rooms: rawRooms, saving, close, submit }: { open: boolean; action: CourseBatchAction; affected: number; allFiltered: boolean; date: string; setDate: (value: string) => void; room: string; setRoom: (value: string) => void; rooms: RoomResponse[]; saving: boolean; close: () => void; submit: () => void }) {
  const deleting = action === "delete";
  const rooms = Array.isArray(rawRooms) ? rawRooms : [];
  const [acknowledged, setAcknowledged] = useState(false);
  // 每次重新打开都要求重新确认，不然「上次勾过」会一路带到下一次删除。
  useEffect(() => { if (open) setAcknowledged(false); }, [open, action]);
  const title = action === "date" ? "批量修改上课日期" : action === "room" ? "批量修改授课教室" : "批量删除课程";
  const needsAck = deleting && affected > DELETE_DOUBLE_CONFIRM_THRESHOLD;
  const valid = (deleting ? true : action === "date" ? Boolean(date) : Boolean(room)) && (!needsAck || acknowledged);
  return <Dialog open={open} onOpenChange={(next) => { if (!next && !saving) close(); }}><DialogContent><DialogTitle className="text-base font-semibold">{title}</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">本次将{deleting ? "删除" : "修改"} <span className="font-semibold tabular-nums text-zinc-800">{affected}</span> 条课程{allFiltered ? "（当前筛选结果的全部）" : ""}。</DialogDescription><div className="mt-5 space-y-4">{action === "date" ? <FormInput label="新的上课日期" type="date" value={date} setValue={setDate} required /> : action === "room" ? <FormSelect label="新的授课教室" value={room} setValue={setRoom} required options={rooms.filter((item) => item.is_active).map((item) => ({ value: item.business_id, label: identityLabel(item) }))} /> : <div className="border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-900">将删除 {affected} 条课程，操作不可撤销。已被课表版本或飞书日程引用的课程会被系统整批拦截，其余未引用课程才可以删除。</div>}{needsAck ? <label className="flex items-start gap-2 text-sm text-red-900"><input type="checkbox" className="mt-1 size-3.5 accent-red-600" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />我确认删除这 {affected} 条课程，且知道无法撤销。</label> : null}</div><div className="mt-5 flex justify-end gap-2 border-t border-zinc-100 pt-4"><Button variant="outline" disabled={saving} onClick={close}>取消</Button><Button variant={deleting ? "danger" : "primary"} onClick={submit} disabled={saving || !valid}>{saving ? "处理中" : deleting ? `确认删除 ${affected} 条` : `确认修改 ${affected} 条`}</Button></div></DialogContent></Dialog>;
}

function EntityBatchDialog({ open, state, selected, value, setValue, saving, close, submit }: { open: boolean; state: EntityBatchState | null; selected: number; value: string; setValue: (value: string) => void; saving: boolean; close: () => void; submit: () => void }) {
  if (!state) return null;
  const title = batchActionLabel(state.action);
  const deleting = state.action === "delete";
  const valid = deleting || state.action === "teacher-calendar" || Boolean(value);
  return <Dialog open={open} onOpenChange={(next) => { if (!next && !saving) close(); }}><DialogContent><DialogTitle className="text-base font-semibold">{title}</DialogTitle><DialogDescription className="mt-1 text-sm text-zinc-500">本次将{deleting ? "删除" : "修改"}已选择的 <span className="font-semibold tabular-nums text-zinc-800">{selected}</span> 条{entityConfig(state.kind).label}记录。</DialogDescription><div className="mt-5">{deleting ? <div className="border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-900">将删除 {selected} 条记录，操作不可撤销。删除前会检查课程、课表版本与飞书日程引用；存在引用的记录会被拦截。</div> : state.action === "teacher-group" ? <FormSelect label="新的教师类型" value={value} setValue={setValue} required options={[{ value: "false", label: "单体教师（时间不可重叠）" }, { value: "true", label: "教研组（允许并行开课）" }]} /> : state.action === "room-status" ? <FormSelect label="新的教室状态" value={value} setValue={setValue} required options={[{ value: "true", label: "启用" }, { value: "false", label: "停用" }]} /> : state.action === "slot-status" ? <FormSelect label="新的时段状态" value={value} setValue={setValue} required options={[{ value: "true", label: "开放" }, { value: "false", label: "关闭" }]} /> : <FormInput label={batchFieldLabel(state.action)} value={value} setValue={setValue} required={state.action !== "teacher-calendar"} />}</div><div className="mt-5 flex justify-end gap-2 border-t border-zinc-100 pt-4"><Button variant="outline" disabled={saving} onClick={close}>取消</Button><Button variant={deleting ? "danger" : "primary"} onClick={submit} disabled={saving || !valid}>{saving ? "处理中" : deleting ? `确认删除 ${selected} 条` : `确认修改 ${selected} 条`}</Button></div></DialogContent></Dialog>;
}

/** 走班明细：一个班里「哪条业务线的哪种班型、上什么课、谁教、多少课次」，一行一条轨道。 */
function ClassTracksDialog({ item, close }: { item: ClassGroupResponse | null; close: () => void }) {
  const tracks = item && Array.isArray(item.tracks) ? item.tracks : [];
  return <Dialog open={item !== null} onOpenChange={(next) => { if (!next) close(); }}><DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
    <DialogTitle className="text-base font-semibold">{item ? `${identityLabel(item)} · 走班明细` : "走班明细"}</DialogTitle>
    <DialogDescription className="mt-1 text-sm text-zinc-500">同一个班里选不同课的学生走不同的轨道，教师也就不同。这里是按「业务线 × 班型 × 科目」拆开后的真实授课人。</DialogDescription>
    <div className="mt-5">{tracks.length ? <div className="overflow-x-auto border border-zinc-200"><table className="w-full min-w-[560px] border-collapse text-left text-sm">
      <thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="h-9 border-b border-zinc-200 px-3 font-medium">业务线</th><th className="h-9 border-b border-zinc-200 px-3 font-medium">班型</th><th className="h-9 border-b border-zinc-200 px-3 font-medium">科目</th><th className="h-9 border-b border-zinc-200 px-3 font-medium">教师</th><th className="h-9 border-b border-zinc-200 px-3 font-medium">课次数</th></tr></thead>
      <tbody>{tracks.map((track) => <tr key={`${track.business_line}/${track.product_type}/${track.subject}/${track.teacher_business_id}`} className="border-b border-zinc-100 last:border-0">
        <td className="h-10 px-3 align-middle text-zinc-700">{track.business_line || "—"}</td>
        <td className="h-10 px-3 align-middle text-zinc-700">{track.product_type || "—"}</td>
        <td className="h-10 px-3 align-middle text-zinc-700">{track.subject || "—"}</td>
        <td className="h-10 px-3 align-middle text-zinc-700">{track.teacher_business_id || "—"}</td>
        <td className="h-10 px-3 align-middle tabular-nums text-zinc-700">{track.session_count}</td>
      </tr>)}</tbody>
    </table></div> : <p className="border border-zinc-200 bg-zinc-50 p-4 text-sm leading-6 text-zinc-500">这个班暂无课次，所以没有班型、教师和走班轨道。导入课表或新增课程场次后会自动出现。</p>}</div>
    <div className="mt-5 flex justify-end border-t border-zinc-100 pt-4"><Button variant="outline" onClick={close}>关闭</Button></div>
  </DialogContent></Dialog>;
}

function FormInput({ label, value, setValue, type = "text", required = false, min }: { label: string; value: string; setValue: (value: string) => void; type?: string; required?: boolean; min?: string }) {
  return <label className="block text-sm text-zinc-700">{label}<input className={inputClass} type={type} min={min} value={value} onChange={(event) => setValue(event.target.value)} required={required} /></label>;
}

function FormSelect({ label, value, setValue, options: rawOptions, required = false }: { label: string; value: string; setValue: (value: string) => void; options?: Array<{ value: string; label: string }>; required?: boolean }) {
  const options = Array.isArray(rawOptions) ? rawOptions : [];
  return (
    <label className="block text-sm text-zinc-700">
      {label}
      <Select
        selectSize="md"
        containerClassName="mt-1.5"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        required={required}
      >
        <option value="">请选择</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </Select>
    </label>
  );
}

function ReadOnlyField({ label, value }: { label: string; value: string }) {
  return <div><div className="text-xs text-zinc-400">{label}</div><div className="mt-1 truncate font-medium text-zinc-700" title={value}>{value || "-"}</div></div>;
}

function identityLabel(item: { business_id: string; name: string }) {
  return item.name === item.business_id ? item.name : `${item.name}（${item.business_id}）`;
}

/**
 * 没有任何课次的班级，聚合出来的班型/教师/科目全是空数组。这不是数据丢了，
 * 是这个班还没排课——必须显式说出来，否则和「后端漏字段」长得一模一样。
 */
function emptyClassHint(item: ClassGroupResponse) {
  return item.session_count === 0 ? <span className="text-zinc-400">暂无课次</span> : "—";
}

function TracksButton({ item, onOpen }: { item: ClassGroupResponse; onOpen: () => void }) {
  if (!item.tracks.length) return <span className="block w-20 truncate text-zinc-300">—</span>;
  return <button type="button" className="w-20 truncate text-left text-xs text-blue-600 hover:underline" onClick={onOpen} title="查看这个班每门课分别由谁上">{item.tracks.length} 条轨道</button>;
}

function IdentityValue({ item, className }: { item: { business_id: string; name: string }; className?: string }) {
  if (item.name === item.business_id) return <span className={cn("block truncate", className)} title={item.name}>{item.name}</span>;
  return <div className={cn("min-w-0", className)}><div className="truncate" title={item.name}>{item.name}</div><div className="truncate font-mono text-[11px] text-zinc-400" title={item.business_id}>{item.business_id}</div></div>;
}

function DeleteButton({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  return <Button size="icon" variant="ghost" aria-label="删除" title="删除" disabled={disabled} onClick={onClick}><Trash2 className="size-3.5 text-red-600" /></Button>;
}

function RowActions({ editLabel, editTitle, onEdit, deleting, onDelete }: { editLabel: string; editTitle?: string; onEdit: () => void; deleting: boolean; onDelete: () => void }) {
  return <div className="flex items-center gap-1"><Button size="icon" variant="ghost" aria-label={editLabel} title={editTitle ?? editLabel} onClick={onEdit}><Pencil className="size-3.5 text-blue-600" /></Button><DeleteButton disabled={deleting} onClick={onDelete} /></div>;
}

function EntityToolbar({ label, total, filtered, selected, offFilter, onSelectAll, onClearSelection, search, setSearch, readOnly, onAdd, disabled, actions, onBatch, note }: { label: string; total: number; filtered: number; selected: number; offFilter: number; onSelectAll: () => void; onClearSelection: () => void; search: string; setSearch: (value: string) => void; readOnly: boolean; onAdd: () => void; disabled: boolean; actions: Array<{ label: string; action: EntityBatchAction }>; onBatch: (action: EntityBatchAction) => void; note?: string }) {
  const overLimit = selected > BATCH_LIMIT;
  return <div className="mb-3 space-y-3 border border-zinc-200 bg-white p-3">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-zinc-500">
        <label className="relative"><Search className="absolute left-2.5 top-2 size-3.5 text-zinc-400" /><input aria-label={`搜索${label}`} className={`${compactInputClass} w-64 max-w-full pl-8`} value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`搜索${label}标签或属性`} /></label>
        <span className="tabular-nums">筛选 {filtered} / {total} 条</span>
        {!readOnly ? <SelectionControls selected={selected} filtered={filtered} allFiltered={filtered > 0 && selected === filtered && offFilter === 0} offFilter={offFilter} onSelectAll={onSelectAll} onClear={onClearSelection} /> : null}
      </div>
      {readOnly ? <span className="text-xs text-zinc-400">共享主数据由管理员维护</span> : <div className="flex flex-wrap gap-2">{actions.map((item) => <Button key={item.action} size="sm" variant="outline" disabled={selected === 0 || overLimit} onClick={() => onBatch(item.action)}>{item.label}</Button>)}<Button size="sm" variant="outline" disabled={selected === 0 || overLimit} onClick={() => onBatch("delete")}><Trash2 className="size-3.5 text-red-600" />批量删除</Button><Button size="sm" onClick={onAdd} disabled={disabled}><Plus className="size-3.5" />新增{label}</Button></div>}
    </div>
    {note ? <p className="text-xs leading-5 text-zinc-500">{note}</p> : null}
    {overLimit ? <BatchLimitNotice selected={selected} /> : null}
  </div>;
}

function selectedIds(selection: RowSelectionState) {
  return Object.entries(selection).filter(([, selected]) => selected).map(([id]) => id);
}

function filterBySearch<T>(rawItems: T[], search: string, fields: (item: T) => string[]) {
  const items = Array.isArray(rawItems) ? rawItems : [];
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
  if (action === "teacher-group") return "批量修改教师类型";
  if (action === "teacher-subject") return "批量修改学科";
  if (action === "teacher-calendar") return "批量修改飞书日程账号";
  if (action === "room-status") return "批量修改教室状态";
  return "批量修改时段状态";
}

function batchFieldLabel(action: EntityBatchAction) {
  if (action === "teacher-calendar") return "新的飞书日程账号（留空可清除）";
  return "新的学科";
}

function ImportReportPanel({ report, onDismiss }: { report: ImportResult; onDismiss: () => void }) {
  const dropped = report.rows_dropped_placeholder_room ?? 0;
  const stats: Array<{ label: string; value: string }> = [
    { label: "读取行数", value: String(report.rows_total ?? 0) },
    { label: "教室待确认丢弃", value: String(dropped) },
    { label: "参与排课行数", value: String(report.rows_kept ?? 0) },
    { label: "完全去重后来源行", value: String(report.rows_deduped ?? 0) },
    { label: "预处理后教学需求", value: String(report.preprocessed_demands ?? 0) },
    { label: "多产品共享需求", value: String(report.multi_product_demands ?? 0) },
    { label: "多课节名称需求", value: String(report.multi_lesson_name_demands ?? 0) },
    { label: "多候选时段需求", value: String(report.multi_slot_demands ?? 0) },
    { label: "本次新建课次", value: String(report.course_sessions) },
  ];
  return (
    <section className="border border-zinc-200 bg-white">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3">
        <div className="text-sm font-semibold">导入报告 · {report.source}</div>
        <Button size="sm" variant="ghost" onClick={onDismiss} aria-label="关闭导入报告">
          <X className="size-3.5" />
        </Button>
      </div>
      <dl className="grid gap-px bg-zinc-200 sm:grid-cols-3 lg:grid-cols-5">
        {stats.map((item) => (
          <div key={item.label} className="bg-white px-4 py-3">
            <dt className="text-xs text-zinc-400">{item.label}</dt>
            <dd className="mt-1 text-lg font-semibold tabular-nums text-zinc-800">{item.value}</dd>
          </div>
        ))}
      </dl>
      <p className="border-t border-zinc-200 px-4 py-3 text-xs leading-5 text-zinc-600">
        导入先按完整 14 列去重，再按“业务线 × 班级标签 × 课次序号 × 上课日期 × 学科”合并为教学需求；
        产品班型、编排阶段、课节名称、候选时段和候选教室分别保留，不再互相冒充。
      </p>
      {dropped ? (
        <p className="border-t border-zinc-200 px-4 py-3 text-xs leading-5 text-amber-800">
          「教室-待校区确认」的 {dropped} 行已整行丢弃，涉及 {report.dropped_lesson_groups ?? 0} 个
          「班级 × 日期 × 时段」课次组，这些行不进入教室排课范围
          {report.dropped_classes?.length ? `：${report.dropped_classes.join("、")}` : ""}。
        </p>
      ) : null}
    </section>
  );
}
