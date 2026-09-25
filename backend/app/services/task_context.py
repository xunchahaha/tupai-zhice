"""任务上下文主线的共享常量与纯函数（docs/roadmap/07-task-context.md）。

本模块是**单一事实源**：显式声明词表同时被
- 提示词（services/ai.py `_interpret_system_prompt`，告诉模型判定规则），与
- 代码复核（api.py `_execute_memory_actions` 的 explicit 三条复核③）

共用——词表漏配一个词，对应话术的 explicit 复核就会降级 probation 候选
（e2e S2/S3 的状态断言随之失败，见设计 §3.1 的耦合声明与 §7.2 的场景原话）。
改词表或改 e2e stub 原话必须同批过词表锁定用例（tests/test_task_context.py）。
"""

from __future__ import annotations

import re

# 显式声明词表（设计 §3.1）：三组话术类别，提示词与代码复核共用。
# 记录类：用户明确要求把偏好记下来（save_preference 的 explicit 判定词）。
EXPLICIT_SAVE_WORDS: tuple[str, ...] = (
    "记住",
    "以后都",
    "这学期都",
    "以后一直",
    "长期",
)
# 撤销/失效类：让既有偏好退场（expire_preference 的 explicit 判定词）。
EXPLICIT_EXPIRE_WORDS: tuple[str, ...] = (
    "不要用了",
    "不要用",
    "别用了",
    "不用了",
    "别再用",
    "作废",
    "撤销",
)
# 纠正类：不是长期偏好（纠正动作的 explicit 判定词，落到 rejected/expired）。
EXPLICIT_CORRECT_WORDS: tuple[str, ...] = (
    "不是长期",
    "不是偏好",
    "只是那两天",
    "只是请假",
    "临时",
)

_EXPLICIT_WORD_GROUPS: dict[str, tuple[str, ...]] = {
    "save": EXPLICIT_SAVE_WORDS,
    "expire": EXPLICIT_EXPIRE_WORDS,
    "correct": EXPLICIT_CORRECT_WORDS,
}

# 每组一个正则（按词长倒序拼 alternation，避免「不要用」截胡「不要用了」），
# 三组共用同一个匹配口径：提示词里展示原词，代码侧用本函数复核。
_EXPLICIT_PATTERNS: dict[str, re.Pattern[str]] = {
    group: re.compile("|".join(re.escape(word) for word in sorted(words, key=len, reverse=True)))
    for group, words in _EXPLICIT_WORD_GROUPS.items()
}


def explicit_word_hits(text: str) -> dict[str, list[str]]:
    """返回原话命中的显式声明词，按类别归组（代码侧 explicit 复核③）。

    空原话/未命中返回 {}；命中多组时各组独立列出（同句话术可以既含纠正词
    又含撤销词，如「不是长期偏好，旧的那条不要用了」）。
    """
    if not text:
        return {}
    hits: dict[str, list[str]] = {}
    for group, pattern in _EXPLICIT_PATTERNS.items():
        matched = sorted(set(pattern.findall(text)))
        if matched:
            hits[group] = matched
    return hits


def has_explicit_declaration(text: str, *groups: str) -> bool:
    """原话是否命中指定类别（缺省任一类）的显式声明词。"""
    hits = explicit_word_hits(text)
    targets = groups or tuple(_EXPLICIT_PATTERNS)
    return any(hits.get(group) for group in targets)


# 谓词中文标签（回执文案用，与前端 memory-page.tsx PREDICATE_LABELS 同表）。
PREDICATE_LABELS: dict[str, str] = {
    "avoid_slot": "避开时段",
    "prefer_slot": "偏好时段",
    "avoid_room": "避开教室",
    "prefer_room": "偏好教室",
    "consecutive_sessions": "连续上课",
    "max_daily_load": "日负荷上限",
}


def predicate_label(predicate: str | None) -> str:
    if not predicate:
        return "未知偏好"
    return PREDICATE_LABELS.get(predicate, predicate)
