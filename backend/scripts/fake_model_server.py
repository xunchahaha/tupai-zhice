"""端到端测试用的固定输出「假模型」（OpenAI-compatible /chat/completions）。

Playwright 业务场景（frontend/tests/e2e/task-context-flow.spec.ts）需要走通
「真实前端 → 真实解析收口 → 真实 CP-SAT → 真实验收」整条链路，唯一必须固定的是
模型输出——把 stub 放在浏览器里（page.route）会绕过后端的 `_finalize_assistant_interpret`，
恰好把最容易出问题的那一层挡在测试之外。所以这里起一个只依赖标准库的本地服务，
后端按普通 OpenAI-compatible 接口配置指向它，其余代码路径与生产完全一致。

约定：
- 解析指令（system prompt 含「排课指令解析 AI」）按 user 消息里的场景标记匹配固定输出；
  没有匹配的场景返回 422，避免用例悄悄跑在错误的输出上。
- 结果解读（system prompt 含「结果解读助手」）返回最小合法 JSON，用例不依赖其措辞。
- 同时支持流式（SSE，前端解析走这条）与非流式（同步回退）。
- 场景里的业务标识（B01/T01/S05…）来自 app.services.seed.seed_demo_data。

    python scripts/fake_model_server.py --port 8002
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


# 场景标记必须是用例里输入的原话片段；输出里的 source_text 逐字摘自原话（与真实
# 提示词的要求一致，后端据此判断这句话已被结构化）。
def _expire_teacher_preference(context: dict[str, Any]) -> dict[str, Any] | None:
    """撤销场景：目标条目 id 只能取解析上下文里注入的真实活跃偏好（与真实模型同约束）。"""
    target = next(
        (
            item
            for item in context.get("active_preferences") or []
            if item.get("subject_id") == "T02" and item.get("predicate") == "avoid_slot"
        ),
        None,
    )
    if target is None:
        return None
    return {
        "business_lines": [],
        "product_types": [],
        "class_business_ids": [],
        "date_from": None,
        "date_to": None,
        "date_window_days": 7,
        "recognized_rules": [],
        "task_constraints": [],
        "memory_actions": [
            {
                "action": "expire_preference",
                "basis": "explicit",
                "source_text": "旧的教师乙周三晚偏好不要用了",
                "target_entry_id": target["id"],
                "target_status": "expired",
            }
        ],
        "unsupported_requirements": [],
    }


def _teacher_unresolved_then_resolved(context: dict[str, Any]) -> dict[str, Any]:
    """「丙老师」无法唯一确定 → 第一次解析给不出主体；补参后带着目标续办的第二次解析
    （上下文里有 task_context）里，禁排已经在目标清单里，模型只需要给出范围。"""
    output: dict[str, Any] = {
        "business_lines": [],
        "product_types": [],
        "class_business_ids": ["B03"],
        "date_from": None,
        "date_to": None,
        "date_window_days": 7,
        "recognized_rules": [],
        "task_constraints": [],
        "memory_actions": [],
        "unsupported_requirements": [],
    }
    if context.get("task_context"):
        return output
    output["task_constraints"] = [
        {
            "id": "tc-1",
            "source_text": "丙老师周五晚上不能上",
            "subject_type": "teacher",
            "subject_ids": [],
            "slot_business_ids": ["S09", "S10"],
            "hardness": "hard",
        }
    ]
    return output


SCENARIOS: dict[str, Any] = {
    # 核心示例句：只排一个班，教师本次不能上周三晚，尽量少动其他课程。
    # 模型「多此一举」把同一句话又抄进 unsupported_requirements——这正是
    # 修复前会把求解按钮永久禁用的形态，端到端回归就锁在这上面。
    "教师甲周三晚上不能上": {
        "business_lines": [],
        "product_types": [],
        "class_business_ids": ["B01"],
        "date_from": None,
        "date_to": None,
        "date_window_days": 7,
        "recognized_rules": ["优先最小化日期和教室变更"],
        "task_constraints": [
            {
                "id": "tc-1",
                "source_text": "教师甲周三晚上不能上",
                "subject_type": "teacher",
                "subject_ids": ["T01"],
                "slot_business_ids": ["S05", "S06"],
                "hardness": "hard",
            }
        ],
        "memory_actions": [],
        "unsupported_requirements": ["教师甲周三晚上不能上"],
    },
    # 显式长期偏好：命中「记住」，主体与时段可从候选唯一确定 → explicit 直接执行。
    "记住，这学期教师乙周三晚尽量别排": {
        "business_lines": [],
        "product_types": [],
        "class_business_ids": ["B02"],
        "date_from": None,
        "date_to": None,
        "date_window_days": 7,
        "recognized_rules": [],
        "task_constraints": [],
        "memory_actions": [
            {
                "action": "save_preference",
                "basis": "explicit",
                "source_text": "记住，这学期教师乙周三晚尽量别排",
                "subject_type": "teacher",
                "subject_id": "T02",
                "predicate": "avoid_slot",
                "constraint": {"slot_ids": ["S05", "S06"]},
            }
        ],
        "unsupported_requirements": [],
    },
    # 主体无法确认：先登记目标，补参后再解析同一句话（两次输出见函数说明）。
    "丙老师周五晚上不能上": _teacher_unresolved_then_resolved,
    # 显式撤销：命中「不要用了」，目标取上下文里真实存在的偏好条目。
    "旧的教师乙周三晚偏好不要用了": _expire_teacher_preference,
}

EXPLANATION = {
    "headline": "本次求解已完成（e2e 固定解读）",
    "explanation": ["该解读由端到端测试的假模型固定输出，不代表真实模型。"],
    "next_actions": [],
    "intent_review": {"verdict": "unclear", "concerns": []},
}


def _messages(body: dict[str, Any]) -> tuple[str, str]:
    system = user = ""
    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        if item.get("role") == "system":
            system = str(item.get("content") or "")
        elif item.get("role") == "user":
            user = str(item.get("content") or "")
    return system, user


def _context(system: str) -> dict[str, Any]:
    """取出提示词里「输入上下文：」后面的 JSON（候选值与活跃偏好）。"""
    marker = "输入上下文："
    start = system.find(marker)
    if start < 0:
        return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(system[start + len(marker):])
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _answer(body: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str]:
    """返回 (状态码, 模型 JSON 输出, 思考文本)。"""
    system, user = _messages(body)
    if "排课指令解析 AI" in system:
        for marker, scenario in SCENARIOS.items():
            if marker not in user:
                continue
            output = scenario(_context(system)) if callable(scenario) else scenario
            if output is None:
                return 422, None, ""
            return 200, output, f"固定场景「{marker}」：按候选值结构化原话。"
        return 422, None, ""
    if "结果解读助手" in system:
        return 200, EXPLANATION, ""
    return 422, None, ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 静默
        return

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, b'{"ok":true}', "application/json")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, b'{"error":"bad json"}', "application/json")
            return
        status, output, thinking = _answer(body)
        if output is None:
            message = json.dumps(
                {"error": {"message": "fake model: no canned response for this request"}},
                ensure_ascii=False,
            )
            self._send(status, message.encode("utf-8"), "application/json")
            return
        content = json.dumps(output, ensure_ascii=False)
        usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        if body.get("stream"):
            middle = len(content) // 2
            chunks: list[dict[str, Any]] = []
            if thinking:
                chunks.append({"choices": [{"delta": {"reasoning_content": thinking}}]})
            chunks.append({"choices": [{"delta": {"content": content[:middle]}}]})
            chunks.append({"choices": [{"delta": {"content": content[middle:]}}]})
            chunks.append({"choices": [], "usage": usage})
            lines = [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks]
            payload = "".join(lines) + "data: [DONE]\n\n"
            self._send(200, payload.encode("utf-8"), "text/event-stream")
            return
        message_body: dict[str, Any] = {"role": "assistant", "content": content}
        if thinking:
            message_body["reasoning_content"] = thinking
        response = {"choices": [{"message": message_body}], "usage": usage}
        encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self._send(200, encoded, "application/json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake model server on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
