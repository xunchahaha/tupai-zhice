"""真实模型冒烟：用本地真实课表的解析上下文，对着真实 DeepSeek / 智谱端点各发两句话解析。

    cd backend
    # Key 只从环境变量取，脚本不会打印它
    set AI_SMOKE_API_KEY=sk-...            (PowerShell: $env:AI_SMOKE_API_KEY="sk-...")
    uv run python scripts/ai_smoke.py --preset deepseek --model deepseek-flash
    uv run python scripts/ai_smoke.py --preset zhipu --model glm-5.3 --effort low
    uv run python scripts/ai_smoke.py --base-url https://your-gateway/v1 --model some-model --stream

它做的事：
1. 读本地库（只读）构造与正式解析完全相同的上下文（候选班级、教师、时段、固定规则标签……）；
2. 走与网页一句话排课完全相同的发包路径（厂商参数、思考、JSON 输出），连发两句**不同**的指令；
3. 打印每次的耗时、是否官方端点、用量与**缓存命中数**：第二次 cached_tokens > 0 说明前缀被缓存复用；
   两次都是 0 多半是前缀太短（GLM 建议 500 token 以上）或缓存还没生效（稍等几秒再跑一次）；
4. --stream 再走一遍流式通道（网页实际用的是流式）。

只读本地库，不会创建任务、不会改任何数据；调用真实接口会产生少量费用。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import _interpret_context, settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services.ai import AIService  # noqa: E402
from app.services.ai_providers import PRESETS, preset_by_id, resolve_profile  # noqa: E402


def _default_instructions(context: dict[str, Any]) -> list[str]:
    line = (context.get("business_lines") or ["考研"])[0]
    teachers = context.get("teachers") or []
    slots = context.get("time_slots") or []
    teacher = teachers[0]["name"] if teachers else "张老师"
    weekday = slots[0].get("weekday", "周三") if slots else "周三"
    return [
        f"帮我重排{line}的课程，{teacher}{weekday}晚上这次不能上。",
        f"{line}下周的课重新排一下，日期最多前后挪三天，教师时间尽量不要变。",
    ]


def _usage_text(usage: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in usage.items() if k != "model"}, ensure_ascii=False)


def _summary(parsed: dict[str, Any]) -> str:
    return (
        f"班级 {len(parsed.get('class_business_ids') or [])} 个 · "
        f"任务约束 {len(parsed.get('task_constraints') or [])} 条 · "
        f"未落实 {len(parsed.get('unsupported_requirements') or [])} 条"
    )


async def _stream_once(
    service: AIService, system_prompt: str, instruction: str
) -> tuple[int, dict[str, Any]]:
    thinking_events = 0
    usage: dict[str, Any] = {}
    async for kind, payload in service._chat_stream_json(system_prompt, instruction):
        if kind == "thinking":
            thinking_events += 1
        else:
            usage = payload["usage"]
    return thinking_events, usage


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--preset", choices=[item.id for item in PRESETS if item.base_url])
    parser.add_argument("--base-url")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", choices=["auto", "low", "high", "max"], default="auto")
    parser.add_argument("--schedule-set", default="default")
    parser.add_argument(
        "--instruction", action="append", help="自定义指令，可重复；缺省按本地课表生成两句"
    )
    parser.add_argument("--stream", action="store_true", help="再走一遍流式通道")
    args = parser.parse_args()

    api_key = os.environ.get("AI_SMOKE_API_KEY", "").strip()
    if not api_key:
        print("请先设置环境变量 AI_SMOKE_API_KEY（脚本不会打印它）", file=sys.stderr)
        return 2
    preset = preset_by_id(args.preset) if args.preset else None
    base_url = args.base_url or (preset.base_url if preset else "")
    if not base_url:
        print("请用 --preset 或 --base-url 指定接口地址", file=sys.stderr)
        return 2

    config = settings.model_copy(
        update={
            "ai_base_url": base_url,
            "ai_api_key": api_key,
            "ai_model": args.model,
            "ai_reasoning_effort": args.effort,
        }
    )
    profile = resolve_profile(base_url, args.model)
    print(
        f"接口 {base_url}  模型 {args.model}  厂商族 {profile.family}  "
        f"官方端点 {profile.official}  思考强度 {args.effort}"
    )

    with SessionLocal() as db:
        instructions = args.instruction or _default_instructions(
            _interpret_context(db, args.schedule_set, None, "")
        )
        service = AIService(config, db)
        for index, instruction in enumerate(instructions, start=1):
            context = _interpret_context(db, args.schedule_set, None, instruction)
            system_prompt = service._interpret_system_prompt(context)
            started = time.perf_counter()
            parsed, thinking, usage = service._chat_json(system_prompt, instruction)
            elapsed = time.perf_counter() - started
            print(f"\n[{index}] {instruction}")
            print(f"    {_summary(parsed)}  耗时 {elapsed:.1f}s  思考 {len(thinking or '')} 字")
            print(
                "    用量 "
                + json.dumps({k: v for k, v in usage.items() if k != "model"}, ensure_ascii=False)
            )
            print(f"    系统提示词约 {len(system_prompt)} 字符")
        if args.stream:
            instruction = instructions[0]
            context = _interpret_context(db, args.schedule_set, None, instruction)
            started = time.perf_counter()
            events, usage = asyncio.run(
                _stream_once(service, service._interpret_system_prompt(context), instruction)
            )
            print(
                f"\n[流式] 收到 {events} 个思考事件  耗时 {time.perf_counter() - started:.1f}s  "
                f"用量 {_usage_text(usage)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
