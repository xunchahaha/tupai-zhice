import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { parseSseFrame, parseSseFrames, streamInterpretInstruction } from "@/lib/interpret-stream";

describe("SSE frame parsing", () => {
  it("parses a single frame with event and data lines", () => {
    expect(parseSseFrame('event: thinking\ndata: {"delta":"先想","elapsed":0.5}')).toEqual({
      event: "thinking",
      data: '{"delta":"先想","elapsed":0.5}',
    });
  });

  it("defaults the event name to message and joins multi-line data", () => {
    expect(parseSseFrame("data: line1\ndata:line2")).toEqual({
      event: "message",
      data: "line1\nline2",
    });
  });

  it("ignores comments and keeps only frames that carry data", () => {
    expect(parseSseFrame(": heartbeat")).toBeNull();
    expect(parseSseFrame("event: ping")).toBeNull();
  });

  it("splits complete frames out of a buffer and keeps the trailing partial", () => {
    const { events, rest } = parseSseFrames(
      'event: stage\ndata: {"stage":"connect"}\n\nevent: thin',
    );
    expect(events).toEqual([{ event: "stage", data: '{"stage":"connect"}' }]);
    expect(rest).toBe("event: thin");
  });

  it("reassembles a frame whose data line was split across chunks", () => {
    const first = parseSseFrames('event: result\ndata: {"ok":tr');
    expect(first.events).toEqual([]);
    const second = parseSseFrames(`${first.rest}ue}\n\n`);
    expect(second.events).toEqual([{ event: "result", data: '{"ok":true}' }]);
    expect(second.rest).toBe("");
  });

  it("normalizes CRLF line endings including a split CRLF at chunk boundaries", () => {
    // 帧体已完整但 \r\n\r\n 分隔符被拆成 \r\n\r + \n：第一轮不能丢帧。
    const first = parseSseFrames('event: stage\ndata: {"stage":"read"}\r\n\r');
    expect(first.events).toEqual([]);
    const second = parseSseFrames(`${first.rest}\ndata: {"delta":"x"}\r\n\r\n`);
    expect(second.events).toEqual([
      { event: "stage", data: '{"stage":"read"}' },
      { event: "message", data: '{"delta":"x"}' },
    ]);
  });
});

describe("streamInterpretInstruction", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    window.localStorage.setItem("tupai:access-token", "token-123");
    window.localStorage.setItem("tupai:schedule-set-id", "default");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  function sseBody(chunks: string[]): ReadableStream<Uint8Array> {
    const encoder = new TextEncoder();
    let index = 0;
    return new ReadableStream<Uint8Array>({
      pull(controller) {
        if (index < chunks.length) controller.enqueue(encoder.encode(chunks[index++]));
        else controller.close();
      },
    });
  }

  it("posts with auth headers and dispatches stage/thinking events before the result", async () => {
    const fetchMock = vi.fn(async (url: string | URL, init?: RequestInit) => {
      expect(String(url)).toContain("/api/v1/assistant/interpret/stream");
      expect(init?.method).toBe("POST");
      const headers = init?.headers as Record<string, string>;
      expect(headers.Authorization).toBe("Bearer token-123");
      expect(headers["X-Schedule-Set-Id"]).toBe("default");
      expect(init?.body).toBe(JSON.stringify({ instruction: "三天内重排" }));
      return new Response(
        sseBody([
          'event: stage\ndata: {"stage":"connect"}\n\n',
          'event: thinking\ndata: {"delta":"先核对","elapsed":0.2}\n\nevent: thinking\ndata: {"delta":"业务线。","elapsed":0.4}\n\n',
          'event: result\ndata: {"instruction":"三天内重排","date_window_days":3,"thinking":"先核对业务线。"}\n\n',
        ]),
        { status: 200, headers: { "content-type": "text/event-stream" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const stages: string[] = [];
    let thinking = "";
    const result = await streamInterpretInstruction("三天内重排", new AbortController().signal, {
      onStage: (stage) => stages.push(stage),
      onThinking: (delta) => {
        thinking += delta;
      },
    });
    expect(stages).toEqual(["connect"]);
    expect(thinking).toBe("先核对业务线。");
    expect(result.date_window_days).toBe(3);
    expect(result.thinking).toBe("先核对业务线。");
  });

  it("posts goal_id in the body when a bound goal is provided (07 §6.4)", async () => {
    // 续办增量解析：流式主路径请求体必须携带 goal_id（与同步回退口径一致）。
    const fetchMock = vi.fn(async (_url: string | URL, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({ instruction: "三天内重排", goal_id: "goal-77" });
      return new Response(
        sseBody(['event: result\ndata: {"instruction":"三天内重排"}\n\n']),
        { status: 200, headers: { "content-type": "text/event-stream" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await streamInterpretInstruction("三天内重排", new AbortController().signal, {}, "goal-77");
    expect(result.instruction).toBe("三天内重排");
  });

  it("re-throws the backend detail carried by an error event", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      sseBody(['event: error\ndata: {"detail":"AI 指令解析失败：模型输出不是合法 JSON"}\n\n']),
      { status: 200, headers: { "content-type": "text/event-stream" } },
    )));
    await expect(streamInterpretInstruction("三天内重排", new AbortController().signal)).rejects.toThrow(
      "AI 指令解析失败：模型输出不是合法 JSON",
    );
  });

  it("surfaces non-200 responses with the FastAPI detail so callers can fall back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "尚未配置一句话排课 AI" }),
      { status: 409 },
    )));
    await expect(streamInterpretInstruction("三天内重排", new AbortController().signal)).rejects.toThrow(
      "尚未配置一句话排课 AI",
    );
  });

  it("rejects when the stream ends without a result event", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(sseBody(['event: stage\ndata: {"stage":"connect"}\n\n']), {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    })));
    await expect(streamInterpretInstruction("三天内重排", new AbortController().signal)).rejects.toThrow(
      "连接中断",
    );
  });
});
