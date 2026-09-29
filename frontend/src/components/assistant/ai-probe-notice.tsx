import { Link } from "react-router-dom";

import { type AssistantProbe } from "@/components/assistant/use-assistant-probe";
import { settingsPath } from "@/lib/routes";

/**
 * 「让 AI 解析」不可用时的一句话说明，按探测的三态区分：读取中 / 读取失败（可重试）/ 确实没配置（可去配置）。
 * 不能把读取中或读取失败都说成「尚未接入」——那会把人引去配置一个其实配好了的 AI。
 * 返回行内片段，由调用方决定放在段落还是提示条里；AI 可用时不渲染。
 */
export function AiProbeNotice({ probe, missingText }: { probe: AssistantProbe; missingText: string }) {
  if (probe.ready === true) return null;
  const linkClass = "ml-2 text-blue-700 underline-offset-2 hover:underline";
  if (probe.ready === false) {
    return (
      <span>
        {missingText}
        <Link className={linkClass} to={settingsPath("ai")}>去配置</Link>
      </span>
    );
  }
  if (probe.probeError) {
    return (
      <span>
        AI 配置读取失败（{probe.probeError}），暂时不能解析；可以重试，或先用「手动排课」。
        <button type="button" className={linkClass} onClick={probe.probe}>重试</button>
      </span>
    );
  }
  return <span>正在读取 AI 配置，稍后即可解析；也可以先用「手动排课」。</span>;
}
