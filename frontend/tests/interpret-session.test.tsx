import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useInterpretSession } from "@/components/assistant/use-interpret-session";

const mocks = vi.hoisted(() => ({ stream: vi.fn(), post: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/interpret-stream", () => ({ streamInterpretInstruction: mocks.stream }));
vi.mock("@/api/http", () => ({ http: { post: mocks.post } }));

const probe = { setEngine: vi.fn(), setReady: vi.fn() } as never;
const parsed = { instruction: "记住，张老师周三晚都不排课", source: "openai_compatible" } as never;

function session(goalId = "") {
  return renderHook(() =>
    useInterpretSession({ probe, gated: (fn) => fn, goalIdForParse: goalId, onParsed: vi.fn() }),
  );
}

const streamIds = () => mocks.stream.mock.calls.map((call) => call[4] as string);

beforeEach(() => {
  mocks.stream.mockReset();
  mocks.post.mockReset();
});

describe("interpret request_id (idempotency of side-effecting parses)", () => {
  it("shares one id across the stream attempt and the sync fallback", async () => {
    mocks.stream.mockRejectedValue(new Error("stream cut after the server committed"));
    mocks.post.mockResolvedValue({ data: parsed });
    const { result } = session();
    await act(async () => { await result.current.interpret("记住，张老师周三晚都不排课"); });
    expect(streamIds()).toHaveLength(1);
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect((mocks.post.mock.calls[0][1] as { request_id: string }).request_id).toBe(streamIds()[0]);
  });

  it("reuses the id for a retry of a sentence that never produced a result, then issues a fresh one after a success", async () => {
    mocks.stream.mockRejectedValue(new Error("offline"));
    mocks.post.mockRejectedValue(new Error("offline"));
    const { result } = session();
    await act(async () => { await result.current.interpret("记住，张老师周三晚都不排课"); });
    await act(async () => { await result.current.interpret("记住，张老师周三晚都不排课"); });
    const [first, retry] = streamIds();
    expect(retry).toBe(first);

    mocks.stream.mockResolvedValue(parsed);
    await act(async () => { await result.current.interpret("记住，张老师周三晚都不排课"); });
    expect(streamIds()[2]).toBe(first); // 这次成功的仍是那一次操作的重试
    await act(async () => { await result.current.interpret("记住，张老师周三晚都不排课"); });
    expect(streamIds()[3]).not.toBe(first); // 成功之后再发同一句话 = 新的一次操作
  });

  it("issues a different id for a different sentence or a different task", async () => {
    mocks.stream.mockRejectedValue(new Error("offline"));
    mocks.post.mockRejectedValue(new Error("offline"));
    const { result } = session();
    await act(async () => { await result.current.interpret("记住，张老师周三晚都不排课"); });
    await act(async () => { await result.current.interpret("记住，李老师周四都不排课"); });
    const other = session("goal-1");
    await act(async () => { await other.result.current.interpret("记住，张老师周三晚都不排课"); });
    expect(new Set(streamIds()).size).toBe(3);
  });
});
