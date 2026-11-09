import { formatSlot } from "@/lib/format";
import { translateSystemConstraints } from "@/lib/labels";

/**
 * 把内部编号换成人话的一组小函数。
 * 只改「可见正文」；<option> 的 value、URL 参数、提交载荷一律不要经过这里。
 */

/** "18:30:00" → "18:30"；不认识的格式原样（去首尾空白）返回。 */
function trimSeconds(value: string): string {
  const text = value.trim();
  const match = text.match(/^(\d{1,2}:\d{2}):\d{2}(?:\.\d+)?$/);
  return match ? match[1] : text;
}

/**
 * "18:30:00","21:30" → "18:30-21:30"（截掉秒，半角连字符，和 formatSlot 一致）。
 * 缺一端时只返回有值的那端；两端都缺返回 ""。
 */
export function formatTimeRange(start?: string | null, end?: string | null): string {
  const from = start ? trimSeconds(start) : "";
  const to = end ? trimSeconds(end) : "";
  if (from && to) return `${from}-${to}`;
  return from || to;
}

/**
 * 返回「周三 18:30-21:30」；weekday 或起止时间缺失时退回 formatSlot(business_id)。
 * 连 business_id 也没有时，尽量拼出已有的部分，全空才返回 "-"。
 */
export function formatSlotLabel(slot: {
  weekday?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  business_id?: string | null;
}): string {
  const weekday = slot.weekday?.trim() ?? "";
  const hasStart = Boolean(slot.start_time?.trim());
  const hasEnd = Boolean(slot.end_time?.trim());
  if (weekday && hasStart && hasEnd) return `${weekday} ${formatTimeRange(slot.start_time, slot.end_time)}`;
  if (slot.business_id) return formatSlot(slot.business_id);
  const partial = [weekday, formatTimeRange(slot.start_time, slot.end_time)].filter(Boolean).join(" ");
  return partial || "-";
}

const SLOT_ID = /SLOT-[^\s，、；;。)）]+/g;
const TASK_ID = /TASK-[0-9a-f]{8}[-\w]*/g;
const SAMPLE_SESSIONS_CLAUSE = /[；;]\s*示例课次[:：][^；;。]*/g;
const SESSION_ID = /\b[A-Z]+-[A-Z]{2}-[0-9A-F]{16}\b/g;

/**
 * 一组句子里出现的任务约束编号 → 人话名称，按首次出现编号：只有一条时叫「本次任务要求」，
 * 多条时叫「本次任务要求 1 / 2 …」，换完之后仍能分清「保留哪一条」。
 */
export function taskIdLabels(texts: readonly string[]): Map<string, string> {
  const ids: string[] = [];
  for (const text of texts) {
    for (const [id] of (text ?? "").matchAll(TASK_ID)) if (!ids.includes(id)) ids.push(id);
  }
  return new Map(ids.map((id, index) => [id, ids.length > 1 ? `本次任务要求 ${index + 1}` : "本次任务要求"]));
}

/**
 * 把求解器、诊断返回的句子里的内部编号换成人话，依次：
 * 1. 系统约束编号 → 中文名（translateSystemConstraints）；
 * 2. SLOT-… → formatSlot 的「周三 18:30-21:30」；
 * 3. TASK-xxxxxxxx… → 「本次任务要求」，不同的任务约束各自编号（见 taskIdLabels；
 *    多句共用一套编号时由调用方传入 labels）；
 * 4. 删掉「；示例课次：…」子句；
 * 5. 残留的课次编号（如 CAMPUS-ZZ-34FB7F8AE46BC344）→ 「某课次」。
 * 没有匹配时原样返回。
 */
export function humanizeIds(text: string, labels: ReadonlyMap<string, string> = taskIdLabels([text])): string {
  if (!text) return text;
  return translateSystemConstraints(text)
    .replace(SLOT_ID, (match) => formatSlot(match))
    .replace(TASK_ID, (match) => labels.get(match) ?? "本次任务要求")
    .replace(SAMPLE_SESSIONS_CLAUSE, "")
    .replace(SESSION_ID, "某课次");
}

/**
 * 求解引擎的自述（含 CP-SAT，或以「日期感知模型」开头）返回 true，
 * 调用方把它放进 <TechDetails>，不进正文。
 */
export function isTechnicalNote(text: string): boolean {
  if (!text) return false;
  return text.includes("CP-SAT") || text.trimStart().startsWith("日期感知模型");
}

/**
 * 下拉选项的可见名：通常只写名称；同一列表里有重名（不同校区都叫「301」）时，
 * 给重名的几项补上编号区分。option 的 value 仍由调用方用 business_id，这里只管文字。
 */
export function distinctNameLabel<T extends { name?: string | null; business_id: string }>(items: readonly T[]): (item: T) => string {
  const nameOf = (item: T) => item.name?.trim() || item.business_id;
  const seen = new Map<string, number>();
  for (const item of items) seen.set(nameOf(item), (seen.get(nameOf(item)) ?? 0) + 1);
  return (item) => {
    const name = nameOf(item);
    return (seen.get(name) ?? 0) > 1 && name !== item.business_id ? `${name}（${item.business_id}）` : name;
  };
}

/** 系统自动起的版本名（「课表版本 V4」）返回 true；人起的名字返回 false。 */
export function isAutoVersionName(name?: string | null): boolean {
  if (!name) return false;
  return /^课表版本\s*V\d+$/i.test(name.trim());
}
