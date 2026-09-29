import { useCallback, useEffect, useState } from "react";

import { http } from "@/api/http";
import { errorMessage } from "@/lib/format";

/**
 * 「让 AI 解析」入口是否可用。通用 AI 与飞书 Aily 是两条相互独立的通道：分别请求、分别记账，
 * 任一条读取失败都不拖垮另一条（此前 Promise.all 会让飞书读取失败把已配置好的通用 AI 入口
 * 一起打成不可用）。入口可用性 = 通用 AI 已配置，或已确认 Aily 可用；
 * 只有「AI 配置读取失败且没有 Aily 兜底」才给出探测错误并提供重试。
 */
export function useAssistantProbe() {
  const [ready, setReady] = useState<boolean | null>(null);
  const [engine, setEngine] = useState("AI 模型");
  const [probeError, setProbeError] = useState("");
  const probe = useCallback(() => {
    setProbeError("");
    let ai: { configured: boolean; model: string | null } | "failed" | null = null;
    let aiReadError = "";
    let aily: boolean | null = null;
    const refresh = () => {
      const aiConfigured = ai !== null && ai !== "failed" && ai.configured;
      const aiModel = ai !== null && ai !== "failed" ? ai.model : null;
      const ailyUsable = aily === true;
      if (ai === null && !ailyUsable) return; // 两条通道都还没有可用结论，继续等待
      if (ai !== null && ai !== "failed" && !aiConfigured && aily === null) return; // 通用 AI 未配置，等飞书结论后再定，避免「待配置」闪跳
      if (ai === "failed" && aily === null) return; // AI 配置读取失败，等飞书兜底结论
      if (ai === "failed" && !ailyUsable) {
        setReady(null);
        setProbeError(aiReadError || "AI 配置读取失败");
        return;
      }
      if (ai === null) {
        // 通用 AI 配置仍在读取，但 Aily 已确认可用：先放行入口，不等可选通道。
        setProbeError("");
        setReady(true);
        setEngine("Aily（可选通道）");
        return;
      }
      setProbeError("");
      setReady(aiConfigured || ailyUsable);
      setEngine(aiConfigured ? (aiModel || "通用 AI 模型") : "Aily（可选通道）");
    };
    http.get<{ configured: boolean; model: string | null }>("/api/v1/integrations/ai/configuration")
      .then(({ data }) => { ai = data; })
      .catch((error) => { ai = "failed"; aiReadError = errorMessage(error); })
      .finally(refresh);
    // 可选集成的读取失败只失去 Aily 增益，绝不阻断独立路径。
    http.get<{ app_configuration: { aily_configured: boolean } }>("/api/v1/integrations/feishu/connection")
      .then(({ data }) => { aily = Boolean(data.app_configuration?.aily_configured); })
      .catch(() => { aily = false; })
      .finally(refresh);
  }, []);
  useEffect(probe, [probe]);
  return { ready, engine, probeError, probe, setReady, setEngine };
}

export type AssistantProbe = ReturnType<typeof useAssistantProbe>;
