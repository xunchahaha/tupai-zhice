import { useQueryClient } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { Download, FileUp, RefreshCw } from "lucide-react";
import { useRef } from "react";
import { toast } from "sonner";

import {
  getListClassGroupsApiV1ClassGroupsGetQueryKey,
  getListCourseSessionsApiV1CourseSessionsGetQueryKey,
  getListRoomsApiV1RoomsGetQueryKey,
  getListTeachersApiV1TeachersGetQueryKey,
  getListTimeSlotsApiV1TimeSlotsGetQueryKey,
  useImportXlsxApiV1ImportsXlsxPost,
  useListClassGroupsApiV1ClassGroupsGet,
  useListCourseSessionsApiV1CourseSessionsGet,
  useListRoomsApiV1RoomsGet,
  useListTeachersApiV1TeachersGet,
  useListTimeSlotsApiV1TimeSlotsGet,
} from "@/api/generated/client";
import { type ClassGroupResponse, type CourseSessionResponse, type RoomResponse, type TeacherResponse, type TimeSlotResponse } from "@/api/generated/models";
import { DataTable } from "@/components/data-table";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { errorMessage } from "@/lib/format";
import { http } from "@/api/http";

const teachers: ColumnDef<TeacherResponse>[] = [{ accessorKey: "business_id", header: "教师 ID" }, { accessorKey: "name", header: "教师（教研组）" }, { accessorKey: "subject", header: "学科" }];
const classes: ColumnDef<ClassGroupResponse>[] = [{ accessorKey: "business_id", header: "班级标签" }, { accessorKey: "name", header: "班级" }, { accessorKey: "subject", header: "业务线" }, { accessorKey: "grade", header: "班型" }, { accessorKey: "teacher_business_id", header: "教师 ID" }];
const rooms: ColumnDef<RoomResponse>[] = [{ accessorKey: "business_id", header: "教室标签" }, { accessorKey: "name", header: "教室" }, { accessorFn: (row) => row.is_active ? "启用" : "停用", id: "active", header: "状态" }];
const slots: ColumnDef<TimeSlotResponse>[] = [{ accessorKey: "business_id", header: "时段 ID" }, { accessorKey: "weekday", header: "星期" }, { accessorKey: "start_time", header: "开始" }, { accessorKey: "end_time", header: "结束" }, { accessorKey: "kind", header: "类型" }, { accessorKey: "sequence", header: "序号" }];
const courses: ColumnDef<CourseSessionResponse>[] = [{ accessorKey: "business_id", header: "场次 ID" }, { accessorKey: "class_business_id", header: "班级标签" }, { accessorKey: "teacher_business_id", header: "教师 ID" }, { accessorKey: "lesson_name", header: "课节名称" }, { accessorKey: "subject", header: "学科" }, { accessorKey: "schedule_source", header: "编排来源" }, { accessorKey: "stage", header: "编排阶段" }, { accessorKey: "planned_sessions", header: "计划课次" }, { accessorKey: "session_no", header: "课次序号" }, { accessorKey: "lesson_date", header: "上课日期" }, { accessorKey: "duration_minutes", header: "时长(分)" }];

export function MasterDataPage() {
  const client = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const teacherQuery = useListTeachersApiV1TeachersGet(); const classQuery = useListClassGroupsApiV1ClassGroupsGet(); const roomQuery = useListRoomsApiV1RoomsGet(); const slotQuery = useListTimeSlotsApiV1TimeSlotsGet(); const courseQuery = useListCourseSessionsApiV1CourseSessionsGet();
  const refresh = () => void Promise.all([teacherQuery.refetch(), classQuery.refetch(), roomQuery.refetch(), slotQuery.refetch(), courseQuery.refetch()]);
  const afterImport = () => { [getListTeachersApiV1TeachersGetQueryKey(), getListClassGroupsApiV1ClassGroupsGetQueryKey(), getListRoomsApiV1RoomsGetQueryKey(), getListTimeSlotsApiV1TimeSlotsGetQueryKey(), getListCourseSessionsApiV1CourseSessionsGetQueryKey()].forEach((key) => void client.invalidateQueries({ queryKey: key })); toast.success("主数据已导入"); };
  const upload = useImportXlsxApiV1ImportsXlsxPost({ mutation: { onSuccess: afterImport, onError: (error) => toast.error(errorMessage(error)) } });
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
  const loading = [teacherQuery, classQuery, roomQuery, slotQuery, courseQuery].some((item) => item.isPending);
  const failed = [teacherQuery, classQuery, roomQuery, slotQuery, courseQuery].some((item) => item.isError);
  return <div className="space-y-5"><input ref={file} className="hidden" type="file" accept=".xlsx" onChange={(event) => { const selected = event.target.files?.[0]; if (selected) upload.mutate({ data: { file: selected as unknown as string } }); event.target.value = ""; }} /><PageHeader title="主数据" actions={<><Button size="sm" variant="outline" onClick={refresh}><RefreshCw className="size-3.5" />刷新</Button><Button size="sm" variant="outline" onClick={() => void downloadSample()}><Download className="size-3.5" />下载官方模板</Button><Button size="sm" variant="secondary" onClick={() => file.current?.click()} disabled={upload.isPending}><FileUp className="size-3.5" />导入 XLSX</Button></>} />{loading ? <LoadingState /> : failed ? <ErrorState retry={refresh} /> : <Tabs defaultValue="teachers"><TabsList><TabsTrigger value="teachers">教师</TabsTrigger><TabsTrigger value="classes">班级</TabsTrigger><TabsTrigger value="rooms">教室</TabsTrigger><TabsTrigger value="slots">时段</TabsTrigger><TabsTrigger value="courses">课程场次</TabsTrigger></TabsList><TabsContent value="teachers" className="pt-4"><DataTable columns={teachers} data={teacherQuery.data ?? []} /></TabsContent><TabsContent value="classes" className="pt-4"><DataTable columns={classes} data={classQuery.data ?? []} /></TabsContent><TabsContent value="rooms" className="pt-4"><DataTable columns={rooms} data={roomQuery.data ?? []} /></TabsContent><TabsContent value="slots" className="pt-4"><DataTable columns={slots} data={slotQuery.data ?? []} /></TabsContent><TabsContent value="courses" className="pt-4"><DataTable columns={courses} data={courseQuery.data ?? []} /></TabsContent></Tabs>}</div>;
}
