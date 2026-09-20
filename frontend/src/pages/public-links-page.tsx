import { Copy, Link2, QrCode, RotateCcw, Share2, SquareSlash } from "lucide-react";
import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  getListPublicLinksApiV1ScheduleSetsScheduleSetIdPublicLinksGetQueryKey,
  useCreatePublicLinkApiV1ScheduleSetsScheduleSetIdPublicLinksPost,
  useListCampusesApiV1CampusesGet,
  useListClassGroupsApiV1ClassGroupsGet,
  useListPublicLinksApiV1ScheduleSetsScheduleSetIdPublicLinksGet,
  useListTeachersApiV1TeachersGet,
  useRevokePublicLinkApiV1PublicLinksLinkIdDelete,
  useRotatePublicLinkApiV1PublicLinksLinkIdRotatePost,
} from "@/api/generated/client";
import type { PublicLinkCreateScope, PublicLinkResponse, PublicLinkSecretResponse } from "@/api/generated/models";
import { ShareDialog, type ShareableLink } from "@/components/public/share-dialog";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DataTable } from "@/components/data-table";
import { ErrorState, LoadingState, PageHeader } from "@/components/page";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { useOutletContext } from "react-router-dom";
import type { AppOutletContext } from "@/app/user-context";
import type { ColumnDef } from "@tanstack/react-table";
import { datetime, errorMessage } from "@/lib/format";
import { semesterEndEstimate } from "@/lib/public-api";

const SCOPE_BADGES: Record<PublicLinkCreateScope, { label: string; tone: BadgeTone }> = {
  class: { label: "班级", tone: "blue" },
  teacher: { label: "教师", tone: "yellow" },
  school: { label: "全校", tone: "green" },
};

const STATUS_BADGES: Record<string, { label: string; tone: BadgeTone }> = {
  active: { label: "生效", tone: "green" },
  revoked: { label: "已停用", tone: "neutral" },
  expired: { label: "已过期", tone: "yellow" },
};

type ExpiryChoice = "semester" | "180" | "custom";

interface CreateFormState {
  scope: PublicLinkCreateScope;
  campusId: string;
  resourceBusinessId: string;
  showTeacherNames: boolean;
  expiry: ExpiryChoice;
  customDate: string;
  note: string;
}

const EMPTY_FORM: CreateFormState = {
  scope: "class",
  campusId: "",
  resourceBusinessId: "",
  showTeacherNames: true,
  expiry: "180",
  customDate: "",
  note: "",
};

function expiryIso(form: CreateFormState): string {
  const date = form.expiry === "semester" ? semesterEndEstimate() : form.expiry === "180"
    ? new Date(Date.now() + 180 * 86_400_000).toISOString().slice(0, 10)
    : form.customDate;
  return `${date}T23:59:59+08:00`;
}

/**
 * 公开链接管理页（06 §3 B4）：capability-link 与 RBAC 正交，
 * 签发/轮换/停用只要求 admin/scheduler；明文链接只在创建/轮换弹窗出现一次。
 */
export function PublicLinksPage() {
  const { scheduleSet } = useOutletContext<AppOutletContext>();
  const scheduleSetId = scheduleSet?.id ?? "";
  const client = useQueryClient();

  const links = useListPublicLinksApiV1ScheduleSetsScheduleSetIdPublicLinksGet(scheduleSetId, {
    query: { enabled: Boolean(scheduleSetId) },
  });
  const campuses = useListCampusesApiV1CampusesGet();
  const classGroups = useListClassGroupsApiV1ClassGroupsGet();
  const teachers = useListTeachersApiV1TeachersGet();

  // 明文链接仅存于本次会话内存；刷新或换人后只能通过轮换重新获得。
  const [sessionSecrets, setSessionSecrets] = useState<Record<string, PublicLinkSecretResponse>>({});
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState<CreateFormState>(EMPTY_FORM);
  const [secret, setSecret] = useState<PublicLinkSecretResponse | null>(null);
  const [secretKind, setSecretKind] = useState<"create" | "rotate">("create");
  const [shareLink, setShareLink] = useState<ShareableLink | null>(null);
  const [rotateTarget, setRotateTarget] = useState<PublicLinkResponse | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<PublicLinkResponse | null>(null);

  const refresh = () => void client.invalidateQueries({
    queryKey: getListPublicLinksApiV1ScheduleSetsScheduleSetIdPublicLinksGetQueryKey(scheduleSetId),
  });

  const create = useCreatePublicLinkApiV1ScheduleSetsScheduleSetIdPublicLinksPost({
    mutation: {
      onSuccess: (created) => {
        setCreateOpen(false);
        setSecret(created);
        setSecretKind("create");
        setSessionSecrets((current) => ({ ...current, [created.id]: created }));
        setForm(EMPTY_FORM);
        refresh();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });

  const rotate = useRotatePublicLinkApiV1PublicLinksLinkIdRotatePost({
    mutation: {
      onSuccess: (rotated) => {
        setSecret(rotated);
        setSecretKind("rotate");
        setSessionSecrets((current) => ({ ...current, [rotated.id]: rotated }));
        setRotateTarget(null);
        refresh();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });

  const revoke = useRevokePublicLinkApiV1PublicLinksLinkIdDelete({
    mutation: {
      onSuccess: (_result, { linkId }) => {
        toast.success("链接已停用");
        setSessionSecrets((current) => {
          const next = { ...current };
          delete next[linkId];
          return next;
        });
        setRevokeTarget(null);
        refresh();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });

  const campusesList = useMemo(() => (Array.isArray(campuses.data) ? campuses.data : []), [campuses.data]);
  const classesList = useMemo(
    () => (Array.isArray(classGroups.data) ? classGroups.data.filter((item) => item.campus_id === form.campusId) : []),
    [classGroups.data, form.campusId],
  );
  const teachersList = useMemo(
    () => (Array.isArray(teachers.data) ? teachers.data.filter((item) => item.campus_id === form.campusId) : []),
    [teachers.data, form.campusId],
  );
  const resourceOptions = form.scope === "class" ? classesList : teachersList;

  const rows = useMemo(() => (Array.isArray(links.data) ? links.data : []), [links.data]);

  const columns = useMemo<ColumnDef<PublicLinkResponse>[]>(() => [
    {
      accessorKey: "display_name",
      header: "名称",
      cell: ({ row }) => <span className="font-medium text-zinc-900">{row.original.display_name}</span>,
    },
    {
      accessorKey: "scope",
      header: "范围",
      cell: ({ row }) => {
        const badge = SCOPE_BADGES[row.original.scope as PublicLinkCreateScope] ?? { label: row.original.scope, tone: "neutral" as BadgeTone };
        return <Badge tone={badge.tone}>{badge.label}</Badge>;
      },
    },
    {
      accessorKey: "resource_business_id",
      header: "目标",
      cell: ({ row }) => <span className="font-mono text-xs text-zinc-500">{row.original.resource_business_id ?? "—"}</span>,
    },
    {
      accessorKey: "status",
      header: "状态",
      cell: ({ row }) => {
        const badge = STATUS_BADGES[row.original.status] ?? { label: row.original.status, tone: "neutral" as BadgeTone };
        return <Badge tone={badge.tone}>{badge.label}</Badge>;
      },
    },
    {
      accessorKey: "show_teacher_names",
      header: "教师姓名",
      cell: ({ row }) => (
        <Badge tone={row.original.show_teacher_names ? "blue" : "neutral"}>
          {row.original.show_teacher_names ? "显示" : "隐藏"}
        </Badge>
      ),
    },
    {
      accessorKey: "access_count",
      header: "访问次数",
      cell: ({ row }) => <span className="tabular-nums">{row.original.access_count}</span>,
    },
    {
      accessorKey: "last_seen_at",
      header: "最后访问",
      cell: ({ row }) => <span className="text-xs text-zinc-500">{datetime(row.original.last_seen_at)}</span>,
    },
    {
      accessorKey: "note",
      header: "备注",
      cell: ({ row }) => <span className="text-xs text-zinc-500">{row.original.note || "—"}</span>,
    },
    {
      id: "actions",
      header: "操作",
      enableSorting: false,
      cell: ({ row }) => {
        const item = row.original;
        const secretLink = sessionSecrets[item.id];
        const shareable = secretLink ? secretLink.public_url : null;
        const disabled = item.status === "revoked";
        return (
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant="ghost"
              disabled={!shareable}
              title={shareable ? "打开分享弹窗" : "明文链接只在创建/轮换后展示一次，请通过轮换重新生成"}
              onClick={() => shareable && setShareLink({ displayName: item.display_name, publicUrl: shareable })}
            >
              <Share2 className="size-3.5" />
              分享
            </Button>
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setRotateTarget(item)}>
              <RotateCcw className="size-3.5" />
              轮换
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-red-600 hover:bg-red-50 hover:text-red-700"
              disabled={disabled}
              onClick={() => setRevokeTarget(item)}
            >
              <SquareSlash className="size-3.5" />
              停用
            </Button>
          </div>
        );
      },
    },
  ], [sessionSecrets]);

  const canSubmit = form.scope === "school"
    ? true
    : Boolean(form.campusId && form.resourceBusinessId && (form.expiry !== "custom" || form.customDate));

  const submitCreate = () => {
    if (!scheduleSetId || !canSubmit) return;
    create.mutate({
      scheduleSetId,
      data: {
        scope: form.scope,
        ...(form.scope === "school" ? {} : { campus_id: form.campusId, resource_business_id: form.resourceBusinessId }),
        show_teacher_names: form.showTeacherNames,
        expires_at: expiryIso(form),
        note: form.note,
      },
    });
  };

  if (links.isPending || campuses.isPending || classGroups.isPending || teachers.isPending) return <LoadingState />;
  if (links.isError) return <ErrorState retry={() => void links.refetch()} />;

  return (
    <div className="space-y-5 animate-fade-in">
      <PageHeader
        title="公开链接"
        actions={
          <Button size="sm" disabled={!scheduleSetId} onClick={() => { setForm(EMPTY_FORM); setCreateOpen(true); }}>
            <Link2 className="size-3.5" />
            新建公开链接
          </Button>
        }
      >
        <p className="mt-1 text-xs text-zinc-500">
          免登录课表页与日历订阅的凭证链接；签发/轮换/停用不进入角色权限体系，撤回即停用或轮换 token。
        </p>
      </PageHeader>

      <DataTable
        columns={columns}
        data={rows}
        empty="还没有公开链接，点击右上角「新建公开链接」创建"
        getRowId={(row) => row.id}
      />

      {/* 创建弹窗 */}
      <Dialog open={createOpen} onOpenChange={(open) => { if (!create.isPending) setCreateOpen(open); }}>
        <DialogContent className="max-w-md">
          <DialogTitle className="text-base font-semibold">新建公开链接</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-zinc-500">
            生成免登录的课表 H5 页与日历订阅；明文链接只在创建成功后展示一次。
          </DialogDescription>
          <div className="mt-4 space-y-4">
            <FieldLabel label="范围">
              <Select
                aria-label="链接范围"
                value={form.scope}
                onChange={(event) => setForm((current) => ({ ...current, scope: event.target.value as PublicLinkCreateScope, campusId: "", resourceBusinessId: "" }))}
              >
                <option value="class">班级</option>
                <option value="teacher">教师</option>
                <option value="school">全校</option>
              </Select>
            </FieldLabel>

            {form.scope !== "school" ? (
              <>
                <FieldLabel label="校区">
                  <Select
                    aria-label="校区"
                    value={form.campusId}
                    onChange={(event) => setForm((current) => ({ ...current, campusId: event.target.value, resourceBusinessId: "" }))}
                  >
                    <option value="">选择校区</option>
                    {campusesList.map((item) => (
                      <option key={item.id} value={item.id}>{item.name}</option>
                    ))}
                  </Select>
                </FieldLabel>
                <FieldLabel label={form.scope === "class" ? "班级" : "教师"}>
                  <Select
                    aria-label={form.scope === "class" ? "选择班级" : "选择教师"}
                    value={form.resourceBusinessId}
                    disabled={!form.campusId}
                    onChange={(event) => setForm((current) => ({ ...current, resourceBusinessId: event.target.value }))}
                  >
                    <option value="">{form.campusId ? (form.scope === "class" ? "选择班级" : "选择教师") : "请先选择校区"}</option>
                    {resourceOptions.map((item) => (
                      <option key={item.id} value={item.business_id}>{item.name}</option>
                    ))}
                  </Select>
                </FieldLabel>
              </>
            ) : null}

            <label className="flex items-center gap-2 text-sm text-zinc-700">
              <input
                type="checkbox"
                className="size-3.5 accent-blue-600"
                checked={form.showTeacherNames}
                onChange={(event) => setForm((current) => ({ ...current, showTeacherNames: event.target.checked }))}
              />
              在公开页显示教师姓名
            </label>

            <FieldLabel label="有效期">
              <Select
                aria-label="有效期"
                value={form.expiry}
                onChange={(event) => setForm((current) => ({ ...current, expiry: event.target.value as ExpiryChoice }))}
              >
                <option value="semester">本学期末（估算 {semesterEndEstimate()}）</option>
                <option value="180">180 天</option>
                <option value="custom">自定义日期</option>
              </Select>
            </FieldLabel>
            {form.expiry === "custom" ? (
              <input
                type="date"
                aria-label="自定义到期日期"
                className="h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20"
                value={form.customDate}
                onChange={(event) => setForm((current) => ({ ...current, customDate: event.target.value }))}
              />
            ) : null}

            <FieldLabel label="备注（可选）">
              <input
                className="h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20"
                value={form.note}
                maxLength={255}
                placeholder="例如：发到三年二班家长群"
                onChange={(event) => setForm((current) => ({ ...current, note: event.target.value }))}
              />
            </FieldLabel>
          </div>
          <div className="mt-5 flex justify-end gap-2 border-t border-zinc-100 pt-4">
            <Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button disabled={!canSubmit || create.isPending} onClick={submitCreate}>
              {create.isPending ? "创建中" : "创建"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 明文链接一次性展示（创建/轮换共用） */}
      <Dialog open={secret !== null} onOpenChange={(open) => { if (!open) setSecret(null); }}>
        <DialogContent className="max-w-md">
          <DialogTitle className="text-base font-semibold">
            {secretKind === "create" ? "链接已创建" : "链接已轮换"}
          </DialogTitle>
          <DialogDescription className="mt-1 text-sm text-zinc-500">
            {secret?.display_name} 的公开链接如下，请立即保存。
          </DialogDescription>
          <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">
            明文链接关闭后无法再次查看（系统只保存哈希）。请通过下方按钮复制或扫码分享；轮换/停用可使旧链接立即失效。
          </div>
          <div className="mt-3 break-all rounded-md border border-zinc-200 bg-zinc-50 p-2.5 font-mono text-xs text-zinc-700" data-testid="public-secret-url">
            {secret?.public_url}
          </div>
          <div className="mt-3 flex flex-wrap justify-end gap-2">
            <Button
              variant="outline"
              onClick={async () => {
                if (!secret) return;
                try {
                  if (!navigator.clipboard?.writeText) throw new Error("当前浏览器不允许自动复制");
                  await navigator.clipboard.writeText(secret.public_url);
                  toast.success("H5 链接已复制");
                } catch (error) {
                  toast.error(`复制失败，请手动复制：${errorMessage(error)}`);
                }
              }}
            >
              <Copy className="size-3.5" />
              复制链接
            </Button>
            <Button
              disabled={!secret}
              onClick={() => {
                if (!secret) return;
                setShareLink({ displayName: secret.display_name, publicUrl: secret.public_url });
              }}
            >
              <QrCode className="size-3.5" />
              分享（二维码）
            </Button>
            <Button variant="secondary" onClick={() => setSecret(null)}>我已保存，关闭</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 轮换确认：旧链接立即失效 */}
      {rotateTarget ? (
        <ConfirmDialog
          open
          danger={false}
          pending={rotate.isPending}
          title={`轮换「${rotateTarget.display_name}」的公开链接`}
          description="轮换会生成全新 token，旧链接立即失效——已分发的二维码和日历订阅都需要重新分享。确认继续？"
          confirmLabel="轮换"
          onOpenChange={(open) => { if (!open) setRotateTarget(null); }}
          onConfirm={() => rotate.mutate({ linkId: rotateTarget.id })}
        />
      ) : null}

      {/* 停用确认 */}
      {revokeTarget ? (
        <ConfirmDialog
          open
          danger
          pending={revoke.isPending}
          title={`停用「${revokeTarget.display_name}」的公开链接`}
          description="停用后公开页和日历订阅立即返回 404，且无法恢复；如需继续公开请重新创建或轮换。"
          confirmLabel="停用"
          onOpenChange={(open) => { if (!open) setRevokeTarget(null); }}
          onConfirm={() => revoke.mutate({ linkId: revokeTarget.id })}
        />
      ) : null}

      <ShareDialog
        open={shareLink !== null}
        onOpenChange={(open) => { if (!open) setShareLink(null); }}
        link={shareLink}
      />
    </div>
  );
}

function FieldLabel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm text-zinc-700">
      {label}
      <div className="mt-1.5">{children}</div>
    </label>
  );
}
