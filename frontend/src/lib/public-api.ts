import { API_BASE_URL } from "@/api/http";
import type {
  PublicLinkDirectoryPayload,
  PublicLinkSchedulePayload,
} from "@/api/generated/models";

// 公开面（/public/t/:token）专用：只取 API_BASE_URL 常量做裸 fetch，
// 绝不 import http 实例——避免把 Authorization 头和 401 广播带进免登录页（06 §3 B1）。
export type PublicLinkPayload = PublicLinkSchedulePayload | PublicLinkDirectoryPayload;

export function scheduleJsonUrl(token: string): string {
  return `${API_BASE_URL}/api/v1/public/links/${encodeURIComponent(token)}/schedule.json`;
}

export function icsUrl(token: string): string {
  return `${API_BASE_URL}/api/v1/public/links/${encodeURIComponent(token)}/calendar.ics`;
}

/** PROD 同源部署时 API_BASE_URL 为空串，公开面拿到的相对路径统一在此补全为绝对地址。 */
export function absoluteUrl(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `${window.location.origin}${url}`;
}

/** webcal:// 是 https 证书下的日历订阅协议别名，仅替换 scheme、路径不变。 */
export function webcalUrl(token: string): string {
  return absoluteUrl(icsUrl(token)).replace(/^http/i, "webcal");
}

/** Google 日历按 cid 订阅远端 ICS；必须是 https 绝对地址（Google 不支持认证）。 */
export function googleCalendarUrl(icsHttpsUrl: string): string {
  return `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(icsHttpsUrl)}`;
}

/** iOS 设备与 macOS 桌面对 webcal:// 都有系统级注册，可一键唤起日历；其余平台走弹层。 */
export function isApplePlatform(userAgent = navigator.userAgent): boolean {
  return /iPad|iPhone|iPod|Macintosh/.test(userAgent);
}

/** token 无效/停用/过期统一 404，不暴露存在性（06 §3 A4）；页面据此区分文案。 */
export class PublicLinkUnavailableError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(status === 404 ? "链接不存在或已失效" : `公开链接请求失败（${status}）`);
    this.name = "PublicLinkUnavailableError";
    this.status = status;
  }
}

export async function fetchPublicLink(token: string): Promise<PublicLinkPayload> {
  const response = await fetch(scheduleJsonUrl(token), { headers: { Accept: "application/json" } });
  if (!response.ok) throw new PublicLinkUnavailableError(response.status);
  return (await response.json()) as PublicLinkPayload;
}

export function isDirectoryPayload(payload: PublicLinkPayload): payload is PublicLinkDirectoryPayload {
  return "classes" in payload;
}

/** 学期末估算：秋季学期（8 月起）→ 次年 1 月底；春季学期（2-7 月）→ 7 月中。 */
export function semesterEndEstimate(now = new Date()): string {
  const year = now.getFullYear();
  if (now.getMonth() === 0) return `${year}-01-31`;
  if (now.getMonth() >= 7) return `${year + 1}-01-31`;
  return `${year}-07-15`;
}

export function addDaysIso(iso: string, days: number): string {
  const date = new Date(`${iso}T00:00:00`);
  date.setDate(date.getDate() + days);
  return toIsoDate(date);
}

export function toIsoDate(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

export function nowHhMm(now = new Date()): string {
  return `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
}
