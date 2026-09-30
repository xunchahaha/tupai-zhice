import { ExternalLink, FlaskConical, ShieldCheck } from "lucide-react";

import { type AIConnectionTestResponse, type AIProviderPresetResponse } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { type ReasoningEffort, isCodingPlanEndpoint, isOfficialEndpoint } from "@/lib/ai-provider";
import { cn } from "@/lib/cn";

const EFFORT_OPTIONS = [
  { value: "auto", label: "自动（推荐：抽取类任务按低）" },
  { value: "low", label: "低（最快最省）" },
  { value: "high", label: "高" },
  { value: "max", label: "最大（最慢最贵）" },
];

function describeTest(result: AIConnectionTestResponse): string {
  if (!result.ok) return result.message;
  const parts = [result.message];
  if (result.latency_ms != null) parts.push(`${(result.latency_ms / 1000).toFixed(1)} 秒`);
  parts.push(result.thinking_returned ? "已返回思考过程" : "未返回思考过程");
  const usage = (result.usage ?? {}) as Record<string, unknown>;
  if (typeof usage.prompt_tokens === "number") {
    const cached = typeof usage.cached_tokens === "number" ? usage.cached_tokens : null;
    parts.push(cached == null ? `输入 ${usage.prompt_tokens} tokens` : `输入 ${usage.prompt_tokens} tokens（缓存命中 ${cached}）`);
  }
  return parts.join(" · ");
}

/**
 * 「添加供应商」：先选一张供应商卡片（自定义 / DeepSeek / 智谱 GLM …），自动带出接口地址与常用模型，
 * 再填 API Key。发包差异（思考参数、缓存友好的提示词、空内容重试）由后端按接口地址与模型名自动适配，
 * 这里只负责把配置填对；「测试连接」用尚未保存的配置发一次最小请求。
 */
export function AiProviderForm({
  presets,
  presetId,
  onSelectPreset,
  baseUrl,
  setBaseUrl,
  apiKey,
  setApiKey,
  apiKeyConfigured,
  savedBaseUrl,
  model,
  setModel,
  effort,
  setEffort,
  configured,
  saving,
  save,
  cancel,
  testing,
  testResult,
  test,
}: {
  presets: AIProviderPresetResponse[];
  presetId: string;
  onSelectPreset: (preset: AIProviderPresetResponse) => void;
  baseUrl: string;
  setBaseUrl: (value: string) => void;
  apiKey: string;
  setApiKey: (value: string) => void;
  apiKeyConfigured: boolean;
  /** 已保存配置的接口地址：换了地址就是换了厂商，不能再沿用旧厂商的 Key。 */
  savedBaseUrl: string | null;
  model: string;
  setModel: (value: string) => void;
  effort: ReasoningEffort;
  setEffort: (value: ReasoningEffort) => void;
  configured: boolean;
  saving: boolean;
  save: () => void;
  cancel: () => void;
  testing: boolean;
  testResult: AIConnectionTestResponse | null;
  test: () => void;
}) {
  const preset = presets.find((item) => item.id === presetId);
  const official = isOfficialEndpoint(baseUrl);
  const keyTooShort = apiKey.length > 0 && apiKey.length < 8;
  const urlOk = /^https?:\/\//.test(baseUrl.trim());
  const normalize = (value: string | null) => (value ?? "").trim().replace(/\/+$/, "");
  const addressChanged = apiKeyConfigured && normalize(savedBaseUrl) !== normalize(baseUrl);
  const keyMissing = (!apiKeyConfigured || addressChanged) && apiKey.length < 8;
  const canSubmit = urlOk && Boolean(model.trim()) && !keyMissing && !keyTooShort;
  return (
    <div id="ai-configuration" className="space-y-4">
      <div className="border border-blue-200 bg-blue-50/60 p-4 text-sm text-blue-950">
        <div className="font-semibold">Aily 标识已从必填项移除</div>
        <p className="mt-2 text-xs leading-5 text-blue-900/75">
          这里使用标准 OpenAI-compatible 模型接口：选一个供应商（也可以自定义企业已有的模型网关），填写 API Key 与模型名称即可。飞书自建应用的{" "}
          <code>cli_...</code> 继续用于飞书数据和日历授权。
        </p>
      </div>

      <section aria-label="选择供应商" className="space-y-2">
        <div className="text-sm font-medium text-zinc-800">选择供应商</div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {presets.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={item.id === presetId}
              onClick={() => onSelectPreset(item)}
              className={cn(
                "h-10 rounded-lg px-3 text-left text-sm transition-colors",
                item.id === presetId ? "bg-blue-600 font-medium text-white" : "bg-zinc-100 text-zinc-700 hover:bg-zinc-200",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
        {preset && preset.notes.length ? (
          <ul className="space-y-1 rounded-md bg-zinc-50 p-3 text-xs leading-5 text-zinc-600">
            {preset.notes.map((note) => <li key={note}>· {note}</li>)}
            {preset.key_url || preset.docs_url ? (
              <li className="flex flex-wrap gap-3 pt-1">
                {preset.key_url ? (
                  <a className="inline-flex items-center gap-1 text-blue-700 hover:underline" href={preset.key_url} target="_blank" rel="noreferrer">
                    获取 API Key<ExternalLink className="size-3" />
                  </a>
                ) : null}
                {preset.docs_url ? (
                  <a className="inline-flex items-center gap-1 text-blue-700 hover:underline" href={preset.docs_url} target="_blank" rel="noreferrer">
                    官方文档<ExternalLink className="size-3" />
                  </a>
                ) : null}
              </li>
            ) : null}
          </ul>
        ) : null}
      </section>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm text-zinc-700 sm:col-span-2">
          接口地址
          <input
            aria-label="AI 接口地址"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://provider.example/v1"
            className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 font-mono text-sm outline-none focus:border-blue-500"
          />
          <span className="mt-1 block text-xs leading-5 text-zinc-500">
            填写到版本根路径，系统会调用其 <code>/chat/completions</code>。
          </span>
          {isCodingPlanEndpoint(baseUrl) ? (
            <span role="alert" className="mt-1 block text-xs leading-5 text-amber-700">
              这是智谱 GLM Coding Plan 的专属端点，官方限定只能在指定的编码工具里使用；自建应用请改用标准 API（
              <code>https://open.bigmodel.cn/api/paas/v4</code>）和标准 API Key。
            </span>
          ) : null}
        </label>
        <label className="text-sm text-zinc-700">
          模型名称或接入点 ID
          <input
            aria-label="AI 模型名称"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="例如：deepseek-flash、glm-5.3 或 ep-..."
            className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 font-mono text-sm outline-none focus:border-blue-500"
          />
          {preset && preset.models.length ? (
            <span className="mt-1.5 flex flex-wrap gap-1.5">
              {preset.models.map((name) => (
                <button
                  key={name}
                  type="button"
                  aria-label={`使用模型 ${name}`}
                  onClick={() => setModel(name)}
                  className={cn(
                    "rounded border px-1.5 py-0.5 font-mono text-[11px]",
                    name === model ? "border-blue-500 bg-blue-50 text-blue-700" : "border-zinc-200 text-zinc-600 hover:border-zinc-400",
                  )}
                >
                  {name}
                </button>
              ))}
            </span>
          ) : null}
        </label>
        <label className="text-sm text-zinc-700">
          API Key
          <input
            aria-label="AI API Key"
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={
              addressChanged ? "接口地址已更改，请填写新的 API Key" : apiKeyConfigured ? "留空则继续使用已保存密钥" : "填写模型平台 API Key"
            }
            autoComplete="new-password"
            className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 px-3 text-sm outline-none focus:border-blue-500"
          />
        </label>
        {official ? (
          <label className="text-sm text-zinc-700">
            思考强度
            <Select
              aria-label="思考强度"
              selectSize="sm"
              containerClassName="mt-1.5"
              value={effort}
              onChange={(event) => setEffort(event.target.value as ReasoningEffort)}
              options={EFFORT_OPTIONS}
            />
            <span className="mt-1 block text-xs leading-5 text-zinc-500">
              官方端点会按这个强度发送思考参数（仅对支持的模型生效：DeepSeek flash/pro 系列、GLM-5.2 及以上，其它模型会忽略）；强度越高越慢越贵。GLM-5.3 系列强制思考，不能关闭。
            </span>
          </label>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-4">
        <Button onClick={save} disabled={saving || !canSubmit}>
          <ShieldCheck className="size-4" />
          {saving ? "正在加密保存" : "保存 AI 配置"}
        </Button>
        <Button variant="outline" onClick={test} disabled={testing || !canSubmit}>
          <FlaskConical className="size-4" />
          {testing ? "正在测试…" : "测试连接"}
        </Button>
        {configured ? <Button size="sm" variant="ghost" onClick={cancel}>取消</Button> : null}
        <span className="text-xs text-zinc-500">API Key 只提交给本地后端并加密保存，页面不会回显。</span>
      </div>
      {testResult ? (
        <div
          role="status"
          aria-label="连接测试结果"
          className={cn(
            "rounded-md border px-3 py-2 text-xs leading-5",
            testResult.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-700",
          )}
        >
          {testResult.ok ? <Badge tone="green" className="mr-2">通过</Badge> : <Badge tone="red" className="mr-2">失败</Badge>}
          {describeTest(testResult)}
        </div>
      ) : null}
    </div>
  );
}
