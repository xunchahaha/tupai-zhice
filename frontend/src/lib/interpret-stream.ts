import { type AssistantInterpretResponse } from "@/api/generated/models";
import { API_BASE_URL, authStore, scheduleSetStore } from "@/api/http";

/** thinking 由后端流式接口透出，orval 生成模型尚未包含，按后端契约就地扩展。 */
export type Interpretation = AssistantInterpretResponse & {
  thinking?: string | null;
  business_lines: string[];
  product_types: string[];
  class_business_ids: string[];
  recognized_rules: string[];
  solver_rules: string[];
  date_window_days: number;
};

/** 与后端 SSE 事件一一对应（stage/thinking/result/error）。 */
export interface InterpretStreamHandlers {
  onStage?: (stage: string) => void;
  onThinking?: (delta: string, elapsedSeconds: number) => void;
}

export interface SseEvent {
  event: string;
  data: string;
}

/** 解析一条完整的 SSE 帧：event 行可省略（默认 message），data 行可多行。 */
export function parseSseFrame(frame: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  return { event, data: dataLines.join("\n") };
}

/**
 * 从缓冲里切出所有完整帧（以空行分隔），剩余半帧留给调用方拼下一个 chunk。
 * 先把 \r\n 归一成 \n，chunk 边界拆开 \r\n 时会在下一轮拼接后正确归一。
 */
export function parseSseFrames(buffer: string): { events: SseEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const events: SseEvent[] = [];
  let start = 0;
  for (;;) {
    const end = normalized.indexOf("\n\n", start);
    if (end < 0) break;
    const parsed = parseSseFrame(normalized.slice(start, end));
    if (parsed) events.push(parsed);
    start = end + 2;
  }
  return { events, rest: normalized.slice(start) };
}

/**
 * 流式解析一句话排课指令：POST fetch + 手写 SSE 读帧（EventSource 不支持 POST）。
 * 失败（网络 / 非 200 / 协议中断）一律抛出，由调用方回退到同步接口；
 * signal 同时承担取消职责：abort 后端通过断开连接感知并取消上游模型请求。
 */
export async function streamInterpretInstruction(
  instruction: string,
  signal: AbortSignal,
  handlers: InterpretStreamHandlers = {},
): Promise<Interpretation> {
  const token = authStore.get();
  const scheduleSetId = scheduleSetStore.get();
  const response = await fetch(`${API_BASE_URL}/api/v1/assistant/interpret/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(scheduleSetId ? { "X-Schedule-Set-Id": scheduleSetId } : {}),
    },
    body: JSON.stringify({ instruction }),
    signal,
  });
  if (!response.ok) {
    // 流式端点在流开始前就会失败（409 未配置 / 422 / 401）；401 交给同步回退
    // 走 axios 拦截器统一处理下线逻辑，这里只抛出让调用方回退。
    let detail = `AI 流式解析请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {
      // 非 JSON 错误体（如网关 HTML），保留默认信息。
    }
    throw new Error(detail);
  }
  if (!response.body) throw new Error("当前环境不支持流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseFrames(buffer);
    buffer = rest;
    for (const item of events) {
      if (item.event === "thinking") {
        const payload = JSON.parse(item.data) as { delta?: string; elapsed?: number };
        handlers.onThinking?.(payload.delta ?? "", payload.elapsed ?? 0);
      } else if (item.event === "stage") {
        const payload = JSON.parse(item.data) as { stage?: string };
        if (payload.stage) handlers.onStage?.(payload.stage);
      } else if (item.event === "result") {
        return JSON.parse(item.data) as Interpretation;
      } else if (item.event === "error") {
        const payload = JSON.parse(item.data) as { detail?: unknown };
        throw new Error(
          typeof payload.detail === "string" ? payload.detail : "AI 流式解析失败",
        );
      }
    }
  }
  throw new Error("AI 流式解析连接中断，未收到完整结果");
}
