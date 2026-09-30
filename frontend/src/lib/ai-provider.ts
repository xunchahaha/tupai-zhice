import { type AIProviderPresetResponse } from "@/api/generated/models";

export type ReasoningEffort = "auto" | "low" | "high" | "max";

export const CUSTOM_PRESET_ID = "custom";


/** 官方端点才会带思考参数；其它接口（含第三方网关）思考强度不起作用。 */
const OFFICIAL_HOSTS = ["api.deepseek.com", "open.bigmodel.cn", "api.z.ai"];

function hostOf(baseUrl: string): string {
  try {
    return new URL(baseUrl.trim()).hostname.toLowerCase();
  } catch {
    return "";
  }
}

export function isOfficialEndpoint(baseUrl: string): boolean {
  const host = hostOf(baseUrl);
  return OFFICIAL_HOSTS.some((item) => host === item || host.endsWith(`.${item}`));
}

/** 智谱 Coding Plan 的专属端点：官方限定只能在指定编码工具里用，自建应用必须走标准 API。 */
export function isCodingPlanEndpoint(baseUrl: string): boolean {
  return /\/api\/coding\//i.test(baseUrl);
}

/** 按接口地址认出当前是哪张预设卡片（认不出就是自定义）。 */
export function presetIdForUrl(presets: AIProviderPresetResponse[], baseUrl: string): string {
  const normalized = baseUrl.trim().replace(/\/+$/, "").toLowerCase();
  const hit = presets.find((item) => item.base_url && item.base_url.replace(/\/+$/, "").toLowerCase() === normalized);
  return hit?.id ?? CUSTOM_PRESET_ID;
}
