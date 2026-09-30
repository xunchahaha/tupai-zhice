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

# 旧版兜底正则命中「具体教师禁排/请假」话术时追加进 unsupported_requirements 的标签
# （api._LEGACY_UNSUPPORTED_PATTERNS 生成，确认卡展示，清单草稿据此判断是否需要
# 待量化占位项）——两处必须是同一个字符串，集中在这里避免各写一份。
TEACHER_RESTRICTION_LABEL = "具体教师的禁排或请假要求"

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

# 记录类词表前缀否定：「不是长期偏好」「这不算长期」「不用记住」里的记录词不是
# 记录声明，反而是在拒绝记录。只作用于记录类——撤销/纠正类的词本身就带否定
# （「不要用了」「不是长期」），套否定前缀会把它们自己否掉。
_SAVE_NEGATORS: tuple[str, ...] = (
    "不是",
    "并非",
    "不算",
    "不要",
    "不用",
    "不必",
    "不需要",
    "没必要",
    "别",
    "非",
)
_SAVE_NEGATION_GUARD = "".join(f"(?<!{re.escape(word)})" for word in _SAVE_NEGATORS)


def _group_pattern(group: str, words: tuple[str, ...]) -> re.Pattern[str]:
    # 按词长倒序拼 alternation，避免「不要用」截胡「不要用了」。
    alternation = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    guard = _SAVE_NEGATION_GUARD if group == "save" else ""
    return re.compile(f"{guard}(?:{alternation})")


# 每组一个正则，三组共用同一个匹配口径：提示词里展示原词，代码侧用本函数复核。
_EXPLICIT_PATTERNS: dict[str, re.Pattern[str]] = {
    group: _group_pattern(group, words) for group, words in _EXPLICIT_WORD_GROUPS.items()
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


# ---------------------------------------------------------------- 动作级授权绑定
#
# 整句指令里出现一次「记住」，不等于这句话里每条动作都被授权：
# 「记住张老师偏好上午；李老师这次先放周四，不要记成长期偏好」——第二条是一次性安排，
# 不能借第一条的「记住」变成长期偏好。授权必须绑定到**该动作自己的原话片段**：
# 片段所在的分句里命中显式声明词、且词前没有否定、分句里没有「不要记/别记」这类
# 明确拒绝，才算这条动作被授权；绑不上的动作由调用方降级成待确认候选。

# 分句边界：句末标点、分号、换行。逗号/顿号/冒号不切——「记住：…」「…，记住」是同一句。
_CLAUSE_BOUNDARY = re.compile(r"[。！？!?；;\n\r]")
# 动作原话片段至少这么长才能证明自己指的是哪一句（与 api._MIN_COVERED_TEXT_CHARS 同值）。
_MIN_SOURCE_CHARS = 4
# 词前否定窗口：否定词出现在命中词之前 6 个字内、且中间没有逗号类停顿。
_NEGATION_WINDOW = 6
_NEGATION_PAUSE = "，,、：:（）()"
_WINDOW_NEGATION = re.compile(
    r"不是|并非|不算|不要|不用|不必|不需要|没必要|先不|暂不|不想|(?<![特区分差辨告道离])别"
)
# 明确拒绝记录：「不要记成长期偏好」「别记」「先不存」——词前窗口抓不到「不要记成长期」
# 里隔着两个字的否定，这里按短语整体否决记录类授权。
_REFUSE_SAVE = re.compile(
    r"(?:不要|不用|不必|先不|暂不|不想|(?<![特区分差辨告道离])别)\s*(?:记|存|保存|长期)"
)


def _negated_before(text: str, start: int) -> bool:
    prefix = text[max(0, start - _NEGATION_WINDOW) : start]
    for mark in _NEGATION_PAUSE:
        cut = prefix.rfind(mark)
        if cut != -1:
            prefix = prefix[cut + 1 :]
    return bool(_WINDOW_NEGATION.search(prefix))


def authorization_clause(instruction: str, source_text: str) -> tuple[str | None, str | None]:
    """定位动作原话片段所在的分句，返回 (分句文本, 绑不上的原因)。

    片段靠「忽略标点/空白」的逐字匹配在指令里定位（模型摘录常带走或补上标点）；
    定位不到、太短、或跨越多个分句（无法确定授权范围）都返回原因，由调用方降级。
    """
    chars = [ch for ch in source_text or "" if re.match(r"[^\W_]", ch)]
    if len(chars) < _MIN_SOURCE_CHARS:
        return None, "动作没有可定位的原话片段，无法确认授权属于哪一句"
    pattern = re.compile(r"[\W_]*".join(re.escape(ch) for ch in chars))
    found = pattern.search(instruction)
    if found is None:
        return None, "动作的原话片段不在本次指令里，无法确认授权属于哪一句"
    start, end = found.span()
    if _CLAUSE_BOUNDARY.search(instruction[start:end]):
        return None, "动作的原话片段跨越多句，无法确定授权范围"
    boundaries = list(_CLAUSE_BOUNDARY.finditer(instruction, 0, start))
    clause_start = boundaries[-1].end() if boundaries else 0
    tail = _CLAUSE_BOUNDARY.search(instruction, end)
    clause_end = tail.start() if tail else len(instruction)
    return instruction[clause_start:clause_end], None


def scoped_word_hits(clause: str) -> dict[str, list[str]]:
    """分句内的显式声明词命中：带词前否定窗口，记录类再受「明确拒绝记录」否决。

    与 `explicit_word_hits` 同一张词表；区别是这里只看动作自己的分句、
    否定可以隔着几个字（「不要记成长期偏好」），撤销类里本身不带否定的词
    （撤销/作废）同样受词前否定约束。
    """
    hits: dict[str, list[str]] = {}
    for group, words in _EXPLICIT_WORD_GROUPS.items():
        alternation = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
        matched: set[str] = set()
        for found in re.finditer(alternation, clause):
            word = found.group()
            negatable = group == "save" or word in {"撤销", "作废"}
            if negatable and _negated_before(clause, found.start()):
                continue
            matched.add(word)
        if matched:
            hits[group] = sorted(matched)
    if "save" in hits and _REFUSE_SAVE.search(clause):
        del hits["save"]
    return hits
