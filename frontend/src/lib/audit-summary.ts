import { humanizeIds } from "@/lib/humanize";
import { eventTypeLabel } from "@/lib/labels";

/**
 * 审计日志的一句话摘要：把 detail 里的原始 JSON 讲成人话，原文由调用方收进「原始记录」折叠块。
 * error 单独返回，调用方要把它醒目地单独显示（失败原因不能和摘要混在一起变淡）；
 * warning 同理：日历下发的冲突数、导入时整行丢弃的行数不能跟着原始记录一起被折叠。
 */
export interface AuditSummary {
  text: string;
  error?: string;
  warning?: string;
}

export interface AuditLogLike {
  action: string;
  resource_type?: string | null;
  detail?: Record<string, unknown> | null;
}

const INSTRUCTION_LIMIT = 40;

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

/** 非负整数才算一个可信的条数；缺失或类型不对返回 null，摘要就不写这个数。 */
function count(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

const SYNC_TRIGGER_TEXT: Record<string, string> = {
  version_publish: "发布后同步",
  version_rollback: "回滚后同步",
  manual_batch: "手动重新同步",
  single_resource: "单表同步",
};

function rescheduleSummary(detail: Record<string, unknown>): string {
  const eventType = text(detail.event_type);
  const subject = text(detail.teacher_business_id) || text(detail.room_business_id);
  const from = text(detail.date_from);
  const to = text(detail.date_to);
  const dates = from ? (to && to !== from ? `${from} ~ ${to}` : from) : "";
  const parts = [eventType ? eventTypeLabel(eventType) : "调课事件", subject, dates].filter(Boolean);
  return `${parts.join(" · ")}${detail.include_neighbors === false ? "（只调这一节）" : ""}`;
}

function instructionSummary(detail: Record<string, unknown>): string {
  const chars = Array.from(text(detail.instruction));
  if (!chars.length) return "需求指令";
  const head = chars.slice(0, INSTRUCTION_LIMIT).join("");
  return `「${head}${chars.length > INSTRUCTION_LIMIT ? "…" : ""}」`;
}

function contextSummary(detail: Record<string, unknown>): string {
  const scope = record(detail.scope);
  const from = text(scope.date_from) || text(detail.date_from);
  const to = text(scope.date_to) || text(detail.date_to);
  const range = from || to ? `范围 ${from || "?"} ~ ${to || "?"}` : "范围已更新";
  const rules = Array.isArray(detail.task_constraint_rule_ids) ? detail.task_constraint_rule_ids.length : 0;
  return rules > 0 ? `${range} · ${rules} 条本次要求` : range;
}

export function auditSummary(log: AuditLogLike): AuditSummary {
  const detail = record(log.detail);
  const error = text(detail.error);
  let warning = "";
  let summary: string;
  if (log.resource_type === "reschedule_event" || log.action === "reschedule_event") {
    summary = rescheduleSummary(detail);
  } else if (log.action === "assistant_solve" || log.action === "assistant_interpret") {
    summary = instructionSummary(detail);
  } else if (log.action === "update_context") {
    summary = contextSummary(detail);
  } else if (log.action === "sync") {
    summary = SYNC_TRIGGER_TEXT[text(detail.trigger)] ?? "同步";
  } else if (log.action === "batch_delete") {
    const deleted = count(detail.count);
    summary = deleted == null ? "批量删除" : `删除 ${deleted} 条`;
  } else if (log.action === "calendar_publish") {
    const published = count(detail.published);
    const conflicts = count(detail.conflicts);
    summary = published == null ? "下发日历" : `下发 ${published} 条`;
    if (conflicts) warning = `冲突 ${conflicts} 条`;
  } else if (log.action === "import_xlsx" || log.action === "import_commit") {
    const created = count(detail.course_sessions_created);
    summary = created == null ? "导入工作簿" : `新建 ${created} 课次`;
    const dropped = count(detail.rows_dropped_placeholder_room);
    if (dropped) warning = `整行丢弃 ${dropped} 行（占位教室）`;
  } else if (log.action === "publish") {
    summary = "发布为当前课表";
  } else if (log.action === "rollback") {
    summary = "回滚为当前课表";
  } else {
    // 不认识的动作不拿字段个数冒充条数（{count: 500} 不能写成「1 项明细」），只指向原始记录。
    summary = Object.keys(detail).length ? "见原始记录" : "-";
  }
  return {
    text: humanizeIds(summary),
    ...(error ? { error: humanizeIds(error) } : {}),
    ...(warning ? { warning } : {}),
  };
}
