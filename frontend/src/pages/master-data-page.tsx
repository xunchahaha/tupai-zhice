import { useQueryClient } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { Download, FileUp, Plus, RefreshCw, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  getListCampusesApiV1CampusesGetQueryKey,
  getListClassGroupsApiV1ClassGroupsGetQueryKey,
  getListCourseSessionsApiV1CourseSessionsGetQueryKey,
  getListRoomsApiV1RoomsGetQueryKey,
  getListTeachersApiV1TeachersGetQueryKey,
  getListTimeSlotsApiV1TimeSlotsGetQueryKey,
  useCreateClassGroupApiV1ClassGroupsPost,
  useCreateRoomApiV1RoomsPost,
  useCreateTeacherApiV1TeachersPost,
  useDeleteMasterDataApiV1MasterDataResourceObjectIdDelete,
  useImportXlsxApiV1ImportsXlsxPost,
  useListCampusesApiV1CampusesGet,
  useListClassGroupsApiV1ClassGroupsGet,
  useListCourseSessionsApiV1CourseSessionsGet,
  useListRoomsApiV1RoomsGet,
  useListTeachersApiV1TeachersGet,
  useListTimeSlotsApiV1TimeSlotsGet,
} from "@/api/generated/client";
import {
  type ClassGroupResponse,
  type CourseSessionResponse,
  type RoomResponse,
  type TeacherResponse,
  type TimeSlotResponse,
} from "@/api/generated/models";
import { http } from "@/api/http";
import { DataTable } from "@/components/data-table";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { errorMessage } from "@/lib/format";

type MasterResource = "teachers" | "class-groups" | "rooms";
type CreateKind = "teacher" | "class" | "room";

interface DeleteTarget {
  id: string;
  label: string;
  resource: MasterResource;
  resourceLabel: string;
}

const inputClass = "mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm outline-none focus:border-blue-500";
const slots: ColumnDef<TimeSlotResponse>[] = [
  { accessorKey: "business_id", header: "时段 ID" },
  { accessorKey: "weekday", header: "星期" },
  { accessorKey: "start_time", header: "开始" },
  { accessorKey: "end_time", header: "结束" },
  { accessorKey: "kind", header: "类型" },
  { accessorKey: "sequence", header: "序号" },
];
const courses: ColumnDef<CourseSessionResponse>[] = [
  { accessorKey: "class_business_id", header: "班级标签" },
  { accessorKey: "teacher_business_id", header: "教师 ID" },
  { accessorKey: "lesson_name", header: "课节名称" },
  { accessorKey: "subject", header: "学科" },
  { accessorKey: "schedule_source", header: "编排来源" },
  { accessorKey: "stage", header: "编排阶段" },
  { accessorKey: "planned_sessions", header: "计划课次" },
  { accessorKey: "session_no", header: "课次序号" },
  { accessorKey: "lesson_date", header: "上课日期" },
  { accessorKey: "duration_minutes", header: "时长(分)" },
];

export function MasterDataPage() {
  const client = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [createKind, setCreateKind] = useState<CreateKind | null>(null);
  const [campusId, setCampusId] = useState("");
  const [deletingKey, setDeletingKey] = useState("");
  const [teacherDraft, setTeacherDraft] = useState({ businessId: "", name: "", subject: "", calendarUserId: "" });
  const [classDraft, setClassDraft] = useState({ businessId: "", name: "", businessLine: "", productType: "", teacherBusinessId: "" });
  const [roomDraft, setRoomDraft] = useState({ businessId: "", name: "", isActive: true });

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
  const closeCreate = () => setCreateKind(null);
  const createError = (error: unknown) => toast.error(errorMessage(error));
  const createTeacher = useCreateTeacherApiV1TeachersPost({
    mutation: {
      onSuccess: () => {
        invalidate(getListTeachersApiV1TeachersGetQueryKey());
        setTeacherDraft({ businessId: "", name: "", subject: "", calendarUserId: "" });
        closeCreate();
        toast.success("教师已新增");
      },
      onError: createError,
    },
  });
  const createClass = useCreateClassGroupApiV1ClassGroupsPost({
    mutation: {
      onSuccess: () => {
        invalidate(getListClassGroupsApiV1ClassGroupsGetQueryKey());
        setClassDraft({ businessId: "", name: "", businessLine: "", productType: "", teacherBusinessId: "" });
        closeCreate();
        toast.success("班级已新增");
      },
      onError: createError,
    },
  });
  const createRoom = useCreateRoomApiV1RoomsPost({
    mutation: {
      onSuccess: () => {
        invalidate(getListRoomsApiV1RoomsGetQueryKey());
        setRoomDraft({ businessId: "", name: "", isActive: true });
        closeCreate();
        toast.success("教室已新增");
      },
      onError: createError,
    },
  });
  const remove = useDeleteMasterDataApiV1MasterDataResourceObjectIdDelete({
    mutation: {
      onSuccess: (_, variables) => {
        const queryKey = variables.resource === "teachers"
          ? getListTeachersApiV1TeachersGetQueryKey()
          : variables.resource === "class-groups"
            ? getListClassGroupsApiV1ClassGroupsGetQueryKey()
            : getListRoomsApiV1RoomsGetQueryKey();
        invalidate(queryKey);
        toast.success("记录已删除");
      },
      onError: (error) => toast.error(errorMessage(error)),
      onSettled: () => setDeletingKey(""),
    },
  });

  const refresh = () => void Promise.all([
    campusQuery.refetch(),
    teacherQuery.refetch(),
    classQuery.refetch(),
    roomQuery.refetch(),
    slotQuery.refetch(),
    courseQuery.refetch(),
  ]);
  const afterImport = () => {
    [
      getListCampusesApiV1CampusesGetQueryKey(),
      getListTeachersApiV1TeachersGetQueryKey(),
      getListClassGroupsApiV1ClassGroupsGetQueryKey(),
      getListRoomsApiV1RoomsGetQueryKey(),
      getListTimeSlotsApiV1TimeSlotsGetQueryKey(),
      getListCourseSessionsApiV1CourseSessionsGetQueryKey(),
    ].forEach(invalidate);
    toast.success("主数据已导入");
  };
  const upload = useImportXlsxApiV1ImportsXlsxPost({
    mutation: { onSuccess: afterImport, onError: (error) => toast.error(errorMessage(error)) },
  });
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
  const confirmDelete = (target: DeleteTarget) => {
    if (!window.confirm(`确认删除${target.resourceLabel}“${target.label}”？删除后相关引用可能需要重新整理。`)) return;
    const key = `${target.resource}:${target.id}`;
    setDeletingKey(key);
    remove.mutate({ resource: target.resource, objectId: target.id });
  };
  const submitCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!campusId || !createKind) return;
    if (createKind === "teacher") {
      const businessId = teacherDraft.businessId.trim();
      createTeacher.mutate({
        data: {
          campus_id: campusId,
          business_id: businessId,
          name: teacherDraft.name.trim() || businessId,
          subject: teacherDraft.subject.trim(),
          calendar_user_id: teacherDraft.calendarUserId.trim() || null,
        },
      });
    } else if (createKind === "class") {
      const businessId = classDraft.businessId.trim();
      createClass.mutate({
        data: {
          campus_id: campusId,
          business_id: businessId,
          name: classDraft.name.trim() || businessId,
          subject: classDraft.businessLine.trim(),
          grade: classDraft.productType.trim(),
          teacher_business_id: classDraft.teacherBusinessId,
        },
      });
    } else {
      const businessId = roomDraft.businessId.trim();
      createRoom.mutate({
        data: {
          campus_id: campusId,
          business_id: businessId,
          name: roomDraft.name.trim() || businessId,
          is_active: roomDraft.isActive,
        },
      });
    }
  };

  const teacherColumns: ColumnDef<TeacherResponse>[] = [
    { id: "teacher", header: "教师（教研组）", cell: ({ row }) => <IdentityValue item={row.original} /> },
    { accessorKey: "subject", header: "学科" },
    { accessorKey: "calendar_user_id", header: "飞书日程账号" },
    {
      id: "actions",
      header: "操作",
      enableSorting: false,
      cell: ({ row }) => <DeleteButton disabled={deletingKey === `teachers:${row.original.id}`} onClick={() => confirmDelete({ id: row.original.id, label: identityLabel(row.original), resource: "teachers", resourceLabel: "教师" })} />,
    },
  ];
  const classColumns: ColumnDef<ClassGroupResponse>[] = [
    { id: "class", header: "班级", cell: ({ row }) => <IdentityValue item={row.original} /> },
    { accessorKey: "subject", header: "业务线" },
    { accessorKey: "grade", header: "班型" },
    { accessorKey: "teacher_business_id", header: "教师（教研组）" },
    {
      id: "actions",
      header: "操作",
      enableSorting: false,
      cell: ({ row }) => <DeleteButton disabled={deletingKey === `class-groups:${row.original.id}`} onClick={() => confirmDelete({ id: row.original.id, label: identityLabel(row.original), resource: "class-groups", resourceLabel: "班级" })} />,
    },
  ];
  const roomColumns: ColumnDef<RoomResponse>[] = [
    { id: "room", header: "教室", cell: ({ row }) => <IdentityValue item={row.original} /> },
    { accessorFn: (row) => row.is_active ? "启用" : "停用", id: "active", header: "状态" },
    {
      id: "actions",
      header: "操作",
      enableSorting: false,
      cell: ({ row }) => <DeleteButton disabled={deletingKey === `rooms:${row.original.id}`} onClick={() => confirmDelete({ id: row.original.id, label: identityLabel(row.original), resource: "rooms", resourceLabel: "教室" })} />,
    },
  ];

  const loading = [campusQuery, teacherQuery, classQuery, roomQuery, slotQuery, courseQuery].some((item) => item.isPending);
  const failed = [campusQuery, teacherQuery, classQuery, roomQuery, slotQuery, courseQuery].some((item) => item.isError);
  const isCreating = createTeacher.isPending || createClass.isPending || createRoom.isPending;
  const createLabel = createKind === "teacher" ? "教师" : createKind === "class" ? "班级" : "教室";

  return (
    <div className="space-y-5">
      <input
        ref={file}
        className="hidden"
        type="file"
        accept=".xlsx"
        onChange={(event) => {
          const selected = event.target.files?.[0];
          if (selected) upload.mutate({ data: { file: selected as unknown as string } });
          event.target.value = "";
        }}
      />
      <PageHeader
        title="主数据"
        actions={<>
          <Button size="sm" variant="outline" onClick={refresh}><RefreshCw className="size-3.5" />刷新</Button>
          <Button size="sm" variant="outline" onClick={() => void downloadSample()}><Download className="size-3.5" />下载官方模板</Button>
          <Button size="sm" variant="secondary" onClick={() => file.current?.click()} disabled={upload.isPending}><FileUp className="size-3.5" />导入 XLSX</Button>
        </>}
      />
      {loading ? <LoadingState /> : failed ? <ErrorState retry={refresh} /> : (
        <Tabs defaultValue="teachers">
          <TabsList>
            <TabsTrigger value="teachers">教师</TabsTrigger>
            <TabsTrigger value="classes">班级</TabsTrigger>
            <TabsTrigger value="rooms">教室</TabsTrigger>
            <TabsTrigger value="slots">时段</TabsTrigger>
            <TabsTrigger value="courses">课程场次</TabsTrigger>
          </TabsList>
          <TabsContent value="teachers" className="pt-4">
            <EntityToolbar label="教师" count={teacherQuery.data?.length ?? 0} onAdd={() => setCreateKind("teacher")} disabled={!campusId} />
            <DataTable columns={teacherColumns} data={teacherQuery.data ?? []} />
          </TabsContent>
          <TabsContent value="classes" className="pt-4">
            <EntityToolbar label="班级" count={classQuery.data?.length ?? 0} onAdd={() => setCreateKind("class")} disabled={!campusId || !teacherQuery.data?.length} />
            <DataTable columns={classColumns} data={classQuery.data ?? []} />
          </TabsContent>
          <TabsContent value="rooms" className="pt-4">
            <EntityToolbar label="教室" count={roomQuery.data?.length ?? 0} onAdd={() => setCreateKind("room")} disabled={!campusId} />
            <DataTable columns={roomColumns} data={roomQuery.data ?? []} />
          </TabsContent>
          <TabsContent value="slots" className="pt-4"><DataTable columns={slots} data={slotQuery.data ?? []} /></TabsContent>
          <TabsContent value="courses" className="pt-4"><DataTable columns={courses} data={courseQuery.data ?? []} paginated defaultPageSize={50} /></TabsContent>
        </Tabs>
      )}
      <Dialog open={createKind !== null} onOpenChange={(open) => { if (!open) closeCreate(); }}>
        <DialogContent>
          <DialogTitle className="text-base font-semibold">新增{createLabel}</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-zinc-500">名称与业务标签一致时，名称可以留空，系统会自动复用标签。</DialogDescription>
          <form className="mt-5 space-y-4" onSubmit={submitCreate}>
            <label className="block text-sm text-zinc-700">所属校区
              <select className={inputClass} value={campusId} onChange={(event) => setCampusId(event.target.value)} required>
                {campusQuery.data?.map((campus) => <option key={campus.id} value={campus.id}>{campus.name}</option>)}
              </select>
            </label>
            {createKind === "teacher" ? <>
              <label className="block text-sm text-zinc-700">教师（教研组）标签<input className={inputClass} value={teacherDraft.businessId} onChange={(event) => setTeacherDraft({ ...teacherDraft, businessId: event.target.value })} required /></label>
              <label className="block text-sm text-zinc-700">显示名称（选填）<input className={inputClass} value={teacherDraft.name} onChange={(event) => setTeacherDraft({ ...teacherDraft, name: event.target.value })} placeholder="留空则与标签一致" /></label>
              <label className="block text-sm text-zinc-700">学科<input className={inputClass} value={teacherDraft.subject} onChange={(event) => setTeacherDraft({ ...teacherDraft, subject: event.target.value })} /></label>
              <label className="block text-sm text-zinc-700">飞书日程账号（选填）<input className={inputClass} value={teacherDraft.calendarUserId} onChange={(event) => setTeacherDraft({ ...teacherDraft, calendarUserId: event.target.value })} /></label>
            </> : createKind === "class" ? <>
              <label className="block text-sm text-zinc-700">班级标签<input className={inputClass} value={classDraft.businessId} onChange={(event) => setClassDraft({ ...classDraft, businessId: event.target.value })} required /></label>
              <label className="block text-sm text-zinc-700">显示名称（选填）<input className={inputClass} value={classDraft.name} onChange={(event) => setClassDraft({ ...classDraft, name: event.target.value })} placeholder="留空则与标签一致" /></label>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block text-sm text-zinc-700">业务线<input className={inputClass} value={classDraft.businessLine} onChange={(event) => setClassDraft({ ...classDraft, businessLine: event.target.value })} /></label>
                <label className="block text-sm text-zinc-700">班型<input className={inputClass} value={classDraft.productType} onChange={(event) => setClassDraft({ ...classDraft, productType: event.target.value })} /></label>
              </div>
              <label className="block text-sm text-zinc-700">教师（教研组）
                <select className={inputClass} value={classDraft.teacherBusinessId} onChange={(event) => setClassDraft({ ...classDraft, teacherBusinessId: event.target.value })} required>
                  <option value="">请选择</option>
                  {teacherQuery.data?.map((teacher) => <option key={teacher.id} value={teacher.business_id}>{identityLabel(teacher)}</option>)}
                </select>
              </label>
            </> : <>
              <label className="block text-sm text-zinc-700">教室标签<input className={inputClass} value={roomDraft.businessId} onChange={(event) => setRoomDraft({ ...roomDraft, businessId: event.target.value })} required /></label>
              <label className="block text-sm text-zinc-700">显示名称（选填）<input className={inputClass} value={roomDraft.name} onChange={(event) => setRoomDraft({ ...roomDraft, name: event.target.value })} placeholder="留空则与标签一致" /></label>
              <label className="flex items-center gap-2 text-sm text-zinc-700"><input type="checkbox" checked={roomDraft.isActive} onChange={(event) => setRoomDraft({ ...roomDraft, isActive: event.target.checked })} />创建后启用</label>
            </>}
            <div className="flex justify-end gap-2 border-t border-zinc-100 pt-4">
              <Button variant="outline" onClick={closeCreate}>取消</Button>
              <Button type="submit" disabled={isCreating || !campusId}>{isCreating ? "保存中" : "保存"}</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
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

function EntityToolbar({ label, count, onAdd, disabled }: { label: string; count: number; onAdd: () => void; disabled: boolean }) {
  return <div className="mb-3 flex items-center justify-between"><span className="text-xs text-zinc-500">共 {count} 条</span><Button size="sm" onClick={onAdd} disabled={disabled}><Plus className="size-3.5" />新增{label}</Button></div>;
}
