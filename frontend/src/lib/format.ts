export function percent(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

export function datetime(value: string | undefined | null): string {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
    hourCycle: "h23",
  }).format(new Date(value));
}

export function errorMessage(error: unknown): string {
  if (typeof error === "object" && error && "response" in error) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === "string") return response.data.detail;
  }
  return error instanceof Error ? error.message : "请求未完成";
}

export function formatSlot(value: string | undefined | null): string {
  if (!value) return "-";
  const s = value.replace(/^SLOT-/, "");
  const match = s.match(/^([\u4e00-\u9fa5]+|\w+)-(\d{2})(\d{2})-(\d{2})(\d{2})$/);
  if (match) {
    const [, day, h1, m1, h2, m2] = match;
    return `${day} ${h1}:${m1}-${h2}:${m2}`;
  }
  const timeOnly = s.match(/^(\d{2})(\d{2})-(\d{2})(\d{2})$/);
  if (timeOnly) {
    const [, h1, m1, h2, m2] = timeOnly;
    return `${h1}:${m1}-${h2}:${m2}`;
  }
  return s;
}

export function formatRoom(value: string | undefined | null): string {
  if (!value) return "-";
  return value.replace(/^教室-/, "");
}

export function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

