const labels: Record<string, string> = {
  active: "已生效",
  admin: "管理员",
  approver: "审批人",
  archived: "已归档",
  assigned: "已分配",
  awaiting_confirmation: "待确认",
  candidate_ready: "候选已生成",
  connected: "已连接",
  class: "班级",
  class_groups: "班级",
  completed: "已完成",
  course: "课程场次",
  course_sessions: "课程场次",
  class_group: "班级",
  create: "创建",
  creating: "创建中",
  declared_constraint: "制度声明",
  draft: "草稿",
  added: "新增",
  export: "导出",
  extra_class: "临时加课",
  failed: "失败",
  feishu: "飞书",
  FEASIBLE: "可行解",
  fixed_room: "固定教室",
  fixed_slot: "固定时段",
  forbidden_slot: "禁排时段",
  hard: "硬约束",
  import: "导入",
  INFEASIBLE: "无解",
  live: "真实连接",
  unconfigured: "未配置",
  moved: "已移动",
  not_authorized: "待授权",
  pending: "待处理",
  partial: "部分完成",
  preferred_slot: "偏好时段",
  propose: "提交候选规则",
  published: "已发布",
  queued: "排队中",
  rejected: "已拒绝",
  reauthorization_required: "需重新授权",
  retired: "已停用",
  rollback: "回滚",
  rolled_back: "已回滚",
  room: "教室",
  rooms: "教室",
  room_outage: "教室停用",
  rules: "规则",
  rule: "规则",
  reschedule_event: "调课事件",
  running: "求解中",
  scheduler: "排课员",
  schedule: "课表版本",
  public_summary: "公开展示汇总",
  public_class_schedule: "班级公开课表",
  public_class_links: "班级链接索引",
  public_adjustment_notice: "公开调课通知",
  schedule_version: "课表版本",
  solver_run: "求解任务",
  submit: "提交求解",
  sync: "同步",
  teacher: "教师",
  teachers: "教师",
  teacher_leave: "教师请假",
  time_slots: "时段",
  unchanged: "未变更",
  unavailable_slot: "不可用时段",
  unknown: "未知",
  viewer: "成员",
};

export function statusLabel(value?: string | null): string {
  if (!value) return "未设置";
  return labels[value] ?? value;
}

export function modelStatusLabel(value?: string | null, presolved = false): string {
  if (!value) return "未返回";
  if (value === "OPTIMAL") return presolved ? "范围内无课次" : "已证明最优";
  if (value === "FEASIBLE") return "可行解";
  if (value === "INFEASIBLE") return presolved ? "预检判定无解" : "已证明无解";
  if (value === "UNKNOWN") return "超时未定";
  return labels[value] ?? "未知";
}

export function actorTypeLabel(value?: string | null): string {
  if (!value) return "全局";
  return labels[value] ?? value;
}

export function constraintLabel(value?: string | null): string {
  if (!value) return "未设置";
  return labels[value] ?? value;
}

export function hardnessLabel(value?: string | null): string {
  return value === "hard" ? "硬约束" : value === "soft" ? "软约束" : "未设置";
}

export function diffKindLabel(value?: string | null): string {
  return statusLabel(value);
}

export function eventTypeLabel(value?: string | null): string {
  return statusLabel(value);
}

export function integrationModeLabel(value?: string | null): string {
  return statusLabel(value);
}

export function syncDirectionLabel(value?: string | null): string {
  return statusLabel(value);
}

export function resourceLabel(value?: string | null): string {
  return statusLabel(value);
}

export function roleLabel(value?: string | null): string {
  if (!value) return "管理员";
  if (value === "admin") return "管理员";
  if (value === "scheduler") return "排课员";
  if (value === "approver") return "审批人";
  if (value === "viewer") return "成员";
  return labels[value] ?? value;
}

export function auditActionLabel(value?: string | null): string {
  return statusLabel(value);
}

export const systemConstraintLabels: Record<string, string> = {
  "SYSTEM-FIXED-TIME": "固定时段锁定",
  "SYSTEM-CLASS-NO-OVERLAP": "班级时间不重叠",
  "SYSTEM-ROOM-NO-OVERLAP": "教室容量与不重叠",
  "SYSTEM-TEACHER-NO-OVERLAP": "教师时间不重叠",
  "SYSTEM-CALENDAR-NO-OVERLAP": "飞书日程时间不冲突",
  "SYSTEM-MAX-DAILY-HOURS": "教师单日课时上限",
  "SYSTEM-CONSECUTIVE-SESSIONS": "连续课次连堂限制",
  "SYSTEM-CAMPUS-ISOLATION": "校区场地隔离",
  "SYSTEM-DEFAULT-MAX-CONSECUTIVE-HOURS": "最大连续授课时长",
  "SYSTEM-DEFAULT-MAX-DAILY-HOURS": "单日最大授课课时",
  "SYSTEM-ROOM-CAPACITY": "教室座位容量限制",
};

export function systemConstraintLabel(key?: string | null): string {
  if (!key) return "";
  return systemConstraintLabels[key] ?? key;
}

export function translateSystemConstraints(text: string): string {
  if (!text) return "";
  let result = text;
  for (const [key, label] of Object.entries(systemConstraintLabels)) {
    result = result.replaceAll(key, label);
  }
  return result;
}
