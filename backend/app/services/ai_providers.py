"""AI 厂商适配层：预设清单、按厂商识别、各家发包差异、用量口径。

排课助手只需要一种能力——给一段提示词，拿回一个 JSON 对象，走 OpenAI Chat Completions
协议。各家在这条协议上的差异集中在这里，`AIService` 不关心是谁家的模型。
依据各家官方文档（2026-10 核对）：

DeepSeek（https://api-docs.deepseek.com）
- 上下文缓存**默认开启，没有任何请求头或参数**：命中靠请求前缀与此前请求完全一致，
  所以提示词要把稳定内容放在前面（见 `AIService._interpret_system_prompt`）；命中情况看
  ``usage.prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``。
- 思考模式默认开启：``thinking.type`` 与 ``reasoning_effort``（low/high/max）；思考模式下
  ``temperature`` 不生效但不报错。
- JSON 输出：``response_format={"type":"json_object"}``，提示词里必须出现 json 字样并给出
  示例，要设置足够的 ``max_tokens``；官方承认偶尔返回空内容，重试一次即可。

智谱 GLM（https://docs.bigmodel.cn，国际站 https://docs.z.ai）
- 原厂 API Key 直接用标准端点 ``https://open.bigmodel.cn/api/paas/v4``（OpenAI 协议，国际站
  ``https://api.z.ai/api/paas/v4``）。**Coding Plan 端点（``/api/coding/paas/v4``）官方限定
  只能在指定编码工具里用，自建应用必须走标准 API**，所以不做预设。
- GLM-5.3 / 5.3-Flash **强制思考**：发 ``thinking.type="disabled"`` 会直接失败；
  ``reasoning_effort`` 取 low/high/max（GLM-5.2 及以上支持），默认 max 很慢很贵，
  抽取类任务用 low 起步。
- 上下文缓存隐式自动（前缀建议 500 token 以上），命中在
  ``usage.prompt_tokens_details.cached_tokens``。
- ``response_format=json_object`` 受支持；``temperature`` 取值 [0, 1]。

只有请求的是厂商**官方域名**时才加厂商专有参数（``thinking``/``reasoning_effort``/
``max_tokens``）：经第三方网关转发时，网关未必透传这些参数，贸然带上可能被拒绝；这类地址
只做无害的适配（用量口径、DeepSeek 空内容重试）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

Family = Literal["deepseek", "glm", "generic"]
ReasoningEffort = Literal["auto", "low", "high", "max"]
REASONING_EFFORTS: tuple[str, ...] = ("auto", "low", "high", "max")

# 官方域名 → 厂商族。
_OFFICIAL_HOSTS: dict[str, Family] = {
    "api.deepseek.com": "deepseek",
    "open.bigmodel.cn": "glm",
    "api.z.ai": "glm",
}

# DeepSeek 官方端点的输出上限：思考与 JSON 都计入输出，给足才不会把 JSON 截断
# （官方要求合理设置 max_tokens）。
DEEPSEEK_MAX_TOKENS = 32768


@dataclass(frozen=True)
class ProviderPreset:
    """设置页「添加供应商」里的一张卡片：选中后填好接口地址与常用模型。"""

    id: str
    label: str
    family: Family
    base_url: str
    models: tuple[str, ...]
    key_url: str
    docs_url: str
    notes: tuple[str, ...]


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="custom",
        label="自定义配置",
        family="generic",
        base_url="",
        models=(),
        key_url="",
        docs_url="",
        notes=("任意 OpenAI-compatible 接口：填写版本根路径（通常以 /v1 结尾）、模型名与 Key。",),
    ),
    ProviderPreset(
        id="deepseek",
        label="DeepSeek",
        family="deepseek",
        base_url="https://api.deepseek.com",
        models=("deepseek-flash", "deepseek-v4-pro"),
        key_url="https://platform.deepseek.com/api_keys",
        docs_url="https://api-docs.deepseek.com/",
        notes=(
            "上下文缓存默认开启，无需任何请求头或参数；系统会把稳定的规则放在提示词最前面以提高命中率。",
            "思考模式默认开启，可在下方调整思考强度。",
        ),
    ),
    ProviderPreset(
        id="zhipu",
        label="智谱 GLM",
        family="glm",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=("glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-4.7"),
        key_url="https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
        docs_url="https://docs.bigmodel.cn/",
        notes=(
            "直接使用智谱开放平台的原厂 API Key，走标准 API 端点。",
            "GLM-5.3 系列强制思考，系统会按官方要求发送思考参数。",
            "GLM Coding Plan 的专属端点只允许在官方指定的编码工具里用，自建应用请用标准 API Key。",
        ),
    ),
    ProviderPreset(
        id="zai",
        label="Z.ai GLM（国际站）",
        family="glm",
        base_url="https://api.z.ai/api/paas/v4",
        models=("glm-5.3", "glm-5.2", "glm-4.7"),
        key_url="https://z.ai/manage-apikey/apikey-list",
        docs_url="https://docs.z.ai/",
        notes=("Z.ai 国际站的 GLM，协议与国内站一致。",),
    ),
)


def preset_by_id(preset_id: str) -> ProviderPreset | None:
    return next((item for item in PRESETS if item.id == preset_id), None)


def preset_id_for(base_url: str) -> str:
    """按接口地址认出是哪张预设卡片（认不出是自定义）。"""
    normalized = base_url.strip().rstrip("/").lower()
    for preset in PRESETS:
        if preset.base_url and preset.base_url.rstrip("/").lower() == normalized:
            return preset.id
    return "custom"


@dataclass(frozen=True)
class ProviderProfile:
    """一次调用面对的是哪家的模型、是不是官方端点。"""

    family: Family
    official: bool
    model: str

    @property
    def reasoning_may_hold_answer(self) -> bool:
        """只有认不出厂商的第三方接口，才可能把最终答案错放进 reasoning_content。

        DeepSeek / GLM 的 reasoning_content 就是思考过程本身：content 为空时它只是没写完的草稿
        （输出被截断，或偶发的空响应），从里面捞出第一个 `{...}` 当答案会悄悄得到错误的解析。
        """
        return self.family == "generic"

    @property
    def retry_on_empty(self) -> bool:
        """DeepSeek 的 JSON 输出偶尔返回空内容（官方文档承认），重试一次即可。"""
        return self.family == "deepseek"


def resolve_profile(base_url: str, model: str) -> ProviderProfile:
    host = (urlsplit(base_url).hostname or "").lower()
    for official_host, family in _OFFICIAL_HOSTS.items():
        if host == official_host or host.endswith(f".{official_host}"):
            return ProviderProfile(family, True, model)
    name = model.lower()
    if "deepseek" in name:
        return ProviderProfile("deepseek", False, model)
    if re.search(r"(?<![a-z])glm(?![a-z])", name):
        return ProviderProfile("glm", False, model)
    return ProviderProfile("generic", False, model)


def _glm_version(model: str) -> tuple[int, int] | None:
    match = re.match(r"^glm-(\d+)(?:\.(\d+))?", model.lower())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def _supports_thinking_controls(profile: ProviderProfile) -> bool:
    """官方模型是否文档化了 thinking / reasoning_effort（不支持的模型不带，免得被拒绝）。"""
    name = profile.model.lower()
    if profile.family == "deepseek":
        return re.match(r"^deepseek-(flash|pro|v\d)", name) is not None
    if profile.family == "glm":
        version = _glm_version(name)
        return version is not None and version >= (5, 2)  # reasoning_effort 仅 GLM-5.2 及以上
    return False


def resolve_effort(setting: str) -> str:
    """auto = 抽取类任务用 low 起步（GLM-5.3 默认 max 很慢很贵，官方迁移指南也建议 low 起步）。"""
    return "low" if setting not in {"low", "high", "max"} else setting


def build_chat_body(
    profile: ProviderProfile,
    *,
    messages: list[dict[str, str]],
    stream: bool,
    reasoning_effort: str = "auto",
) -> dict[str, Any]:
    """组装一次 JSON 对象请求的请求体，厂商差异都在这里。"""
    body: dict[str, Any] = {
        "model": profile.model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    if stream:
        body["stream"] = True
        # GLM 官方端点的流式用量本来就在最后一个 chunk 里返回，不带这个未文档化的参数。
        if not (profile.official and profile.family == "glm"):
            body["stream_options"] = {"include_usage": True}
    if not profile.official:
        return body
    if not _supports_thinking_controls(profile):
        return body  # 官方文档没有覆盖的模型名（旧名等）只带通用字段
    if profile.family == "deepseek":
        body["max_tokens"] = DEEPSEEK_MAX_TOKENS
    # GLM-5.3 强制思考，`disabled` 会失败：这里永远只发 enabled。
    body["thinking"] = {"type": "enabled"}
    body["reasoning_effort"] = resolve_effort(reasoning_effort)
    return body


def normalize_usage(model: str, raw: Any) -> dict[str, Any]:
    """统一各家的用量口径，并带上缓存命中情况。

    DeepSeek：``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``；
    GLM（及 OpenAI 系）：``prompt_tokens_details.cached_tokens``。
    """
    usage = raw if isinstance(raw, dict) else {}
    prompt_tokens = usage.get("prompt_tokens")
    details = usage.get("prompt_tokens_details")
    cached = usage.get("prompt_cache_hit_tokens")
    if cached is None and isinstance(details, dict):
        cached = details.get("cached_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None and isinstance(prompt_tokens, int) and isinstance(cached, int):
        miss = max(prompt_tokens - cached, 0)
    completion_details = usage.get("completion_tokens_details")
    reasoning = (
        completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
    )
    normalized: dict[str, Any] = {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    # 缓存与思考用量只在厂商返回时才带（会随 provenance 落库，没有就不凭空写 null）。
    for key, value in (
        ("cached_tokens", cached),
        ("cache_miss_tokens", miss),
        ("reasoning_tokens", reasoning),
    ):
        if value is not None:
            normalized[key] = value
    return normalized
