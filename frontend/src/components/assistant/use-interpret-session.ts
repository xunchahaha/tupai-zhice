import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { http } from "@/api/http";
import { type AssistantProbe } from "@/components/assistant/use-assistant-probe";
import { type useSubmitGate } from "@/components/assistant/use-submit-gate";
import { buildRefineInstruction, INTERPRET_STAGES, type InterpretPhase } from "@/lib/assistant-task";
import { errorMessage } from "@/lib/format";
import { type Interpretation, streamInterpretInstruction } from "@/lib/interpret-stream";

export interface RefineState {
  open: boolean;
  text: string;
}

const CLOSED_REFINE: RefineState = { open: false, text: "" };

/**
 * 「需求 → AI 解析 → 待确认的理解」这一段状态机：流式解析 + 同步回退、取消、失败重试、继续调整。
 * 与求解/任务状态解耦——解析结果怎样影响范围草稿与任务提示，由调用方通过 onParsed 接手。
 * 所有解析入口都经提交闸门（扩大范围待确认时一律不放行）。
 */
export function useInterpretSession({
  probe,
  gated,
  goalIdForParse,
  onParsed,
}: {
  probe: AssistantProbe;
  gated: ReturnType<typeof useSubmitGate>["gated"];
  /** 解析时带上的任务 id（增量解析的前置条件）；没有任务、任务已结束时为空串。 */
  goalIdForParse: string;
  onParsed: (data: Interpretation, boundGoalId: string) => void;
}) {
  const [instruction, setInstruction] = useState("");
  // 任务的原始需求：任务条「你交代了什么」展示它；后续「继续调整」不改写它。
  const [originalInstruction, setOriginalInstruction] = useState("");
  const [lastSent, setLastSent] = useState("");
  const [interpretation, setInterpretation] = useState<Interpretation | null>(null);
  // 解析结果已被确认并发起求解：确认卡让位给进行中/结果卡。
  const [confirmed, setConfirmed] = useState(false);
  const [phase, setPhase] = useState<InterpretPhase>("idle");
  const [stageIndex, setStageIndex] = useState(0);
  // 流式解析期间实时追加的思考文本；完成后的回看仍以 result 里的 thinking 全文为准。
  const [liveThinking, setLiveThinking] = useState("");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [parsedSeconds, setParsedSeconds] = useState(0);
  const [interpretError, setInterpretError] = useState("");
  const [refine, setRefine] = useState<RefineState>(CLOSED_REFINE);
  const interpretAbort = useRef<AbortController | null>(null);
  const interpretStartedAt = useRef(0);
  // 收到后端 stage 事件后锁定轮播（事件即真实进度），避免计时器把阶段倒拨回去。
  const stageLocked = useRef(false);

  // thinking 阶段的阶段文案（每 2.5s 顺延）与已用时计时器；离开 thinking 即清理。
  useEffect(() => {
    if (phase !== "thinking") return;
    const startedAt = interpretStartedAt.current || Date.now();
    setElapsedSeconds(0);
    setStageIndex(0);
    const timer = window.setInterval(() => {
      const seconds = (Date.now() - startedAt) / 1000;
      setElapsedSeconds(seconds);
      if (!stageLocked.current) setStageIndex(Math.min(Math.floor(seconds / 2.5), INTERPRET_STAGES.length - 1));
    }, 100);
    return () => window.clearInterval(timer);
  }, [phase]);

  const applyInterpretation = (data: Interpretation, boundGoalId: string) => {
    setParsedSeconds((Date.now() - interpretStartedAt.current) / 1000);
    setInterpretation(data);
    setConfirmed(false);
    setRefine(CLOSED_REFINE);
    probe.setEngine(data.source === "feishu_aily" ? "Aily（可选通道）" : "通用 AI 模型");
    onParsed(data, boundGoalId);
    setPhase("parsed");
    toast.success(data.source === "feishu_aily" ? "Aily（可选通道） 已完成解析" : "AI 模型已完成解析");
  };

  const handleInterpretFailure = (message: string) => {
    setInterpretError(message);
    setPhase("failed");
    toast.error(message);
    if (message.includes("配置一句话排课 AI")) probe.setReady(false);
  };

  /** 解析一段需求文本；text 省略时解析需求输入框当前内容。 */
  const interpret = gated(async (text: string = instruction) => {
    // 新的解析取代在途的解析：旧请求即使还会返回，结果也不能落到新一轮上。
    interpretAbort.current?.abort();
    const controller = new AbortController();
    interpretAbort.current = controller;
    const isCurrent = () => interpretAbort.current === controller;
    interpretStartedAt.current = Date.now();
    const boundGoalId = goalIdForParse;
    setLastSent(text);
    setOriginalInstruction((previous) => previous || text);
    setInterpretError("");
    setLiveThinking("");
    stageLocked.current = false;
    setPhase("thinking");
    try {
      // 优先走 SSE 流式接口：thinking 增量实时上屏，result 与同步接口同构。
      // 07 §6.4：已绑定任务时两条通道都必须携带 goal_id（续办增量解析的前置条件）。
      const data = await streamInterpretInstruction(text, controller.signal, {
        onThinking: (delta) => { if (isCurrent()) setLiveThinking((previous) => previous + delta); },
        onStage: (stage) => {
          const index = INTERPRET_STAGES.findIndex((item) => item.key === stage);
          if (index >= 0 && isCurrent()) {
            stageLocked.current = true;
            setStageIndex(index);
          }
        },
      }, boundGoalId || undefined);
      if (!isCurrent()) return;
      applyInterpretation(data, boundGoalId);
    } catch {
      // 用户主动取消、或被切走/取代不算失败：只有仍是当前这一轮时才回到初始态等下一次解析。
      if (controller.signal.aborted) {
        if (isCurrent()) setPhase("idle");
        return;
      }
      try {
        // 流式通道不可用（网络/网关缓冲/协议中断）时回退老接口，降级路径必须保留；
        // 同步回退的请求体与流式主路径口径一致（同样带 goal_id）。
        const { data } = await http.post<Interpretation>(
          "/api/v1/assistant/interpret",
          { instruction: text, ...(boundGoalId ? { goal_id: boundGoalId } : {}) },
          { signal: controller.signal },
        );
        if (!isCurrent()) return;
        applyInterpretation(data, boundGoalId);
      } catch (error) {
        if (controller.signal.aborted) {
          if (isCurrent()) setPhase("idle");
          return;
        }
        if (isCurrent()) handleInterpretFailure(errorMessage(error));
      }
    } finally {
      if (isCurrent()) interpretAbort.current = null;
    }
  });
  const cancelInterpret = () => interpretAbort.current?.abort();
  const retryInterpret = () => void interpret(lastSent || instruction);

  /** 在结果基础上继续改：有任务时只发追加的话（后端据任务上下文增量解析），没有任务就拼到原始需求后面。 */
  const refineInterpret = (extra: string) => {
    const base = originalInstruction || interpretation?.instruction || instruction;
    const text = buildRefineInstruction({ goalBound: Boolean(goalIdForParse), base, extra });
    setInstruction(text);
    return interpret(text);
  };

  const editInstruction = (value: string) => {
    setInstruction(value);
    if (phase === "failed") {
      setPhase("idle");
      setInterpretError("");
    }
  };

  /** 把 AI 给出的建议指令填进「修改要求」输入，省掉「复制—滚动—粘贴」三步。 */
  const applySuggestedInstruction = (value: string) => {
    setRefine({ open: true, text: value });
    toast.success("建议指令已填入，确认无误后可直接解析并重跑");
  };

  /** 回到空白：放弃在途解析并清空需求、理解与回看。 */
  const reset = () => {
    interpretAbort.current?.abort();
    interpretAbort.current = null;
    setPhase("idle");
    setInterpretation(null);
    setConfirmed(false);
    setInterpretError("");
    setLiveThinking("");
    setInstruction("");
    setOriginalInstruction("");
    setLastSent("");
    setRefine(CLOSED_REFINE);
  };

  return {
    instruction,
    setInstruction,
    editInstruction,
    originalInstruction,
    interpretation,
    confirmed,
    setConfirmed,
    phase,
    stageIndex,
    liveThinking,
    elapsedSeconds,
    parsedSeconds,
    interpretError,
    refine,
    setRefine,
    interpret,
    cancelInterpret,
    retryInterpret,
    refineInterpret,
    applySuggestedInstruction,
    reset,
  };
}
