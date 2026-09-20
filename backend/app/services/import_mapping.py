"""L2 智能映射导入：把任意 XLSX/CSV 的列映射到 14 列规范字段。

四层匹配（分数驱动，低置信不硬猜——「自信的错误映射」比「没映射」更危险）：

1. Exact / 别名表：表头与规范字段或业务别名逐字相等，零成本优先命中；
2. Normalized：小写、去空白标点、全半角（NFKC）归一后相等；
3. Fuzzy：``difflib`` 编辑相似度，比例需超过 0.6 才给候选，置信度随比例衰减；
4. 样本形状校验：对前 20 个非空样本判断「日期列像日期、数字列像数字、时段列像
   HH:MM-HH:MM」，形状不符把置信度压到阈值之下（转未匹配）。

可选 LLM 语义层：调用方注入 ``ai_resolver``（复用 services/ai.py 的通道），只对
剩余未匹配列提名，输出必须落在规范字段内，模型可弃权；任何失败都退回未匹配。
置信度低于 :data:`CONFIDENCE_THRESHOLD`（0.5）的列一律返回 ``target=None``。
"""

from __future__ import annotations

import csv
import difflib
import hashlib
import io
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from typing import Any

from openpyxl import load_workbook

from .converter_core import TEMPLATE_HEADERS, WorkbookFormatError

# 置信度低于该值的映射一律不应用：宁缺毋滥。
CONFIDENCE_THRESHOLD = 0.5
# 表头行只在数据前部：扫描前 10 行挑最像表头的一行。
HEADER_SCAN_ROWS = 10
# 模糊层最低相似度；低于它的连候选都不给。
FUZZY_MIN_RATIO = 0.6
# AI 语义层的固定置信度：介于规范化与模糊层之间，仍需用户在向导里确认。
AI_CONFIDENCE = 0.65
# 样本采样规模：判断列形状用的前 N 个非空值。
SAMPLE_SIZE = 20
# 形状校验通过所需的最低样本命中率。
SHAPE_MIN_RATIO = 0.5
# 历史映射的固定置信度：这是用户上次亲手确认过的决策，高于一切自动层。
HISTORICAL_CONFIDENCE = 0.95
# 指纹拼接分隔符：防止相邻表头规范化后首尾相接产生碰撞（「ab」+「c」vs「a」+「bc」）。
_FINGERPRINT_SEPARATOR = "\x1f"

AIResolver = Callable[[list[dict[str, Any]]], dict[int, str]]


class ImportMappingError(RuntimeError):
    """映射定义或上传文件不合法。调用方应当转成 4xx 而不是 500。"""


# ---------------------------------------------------------------------------
# 别名表：教务导出表的高频同义词，零成本优先命中。
# ---------------------------------------------------------------------------

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "标准业务线": ("业务线", "事业部", "产品线", "业务线名称"),
    "标准产品班型": ("产品班型", "班型", "产品类型", "班型名称"),
    "集训营班级标签": ("班级标签", "班级", "集训营班级", "班级名称", "行政班", "行政班级", "班号"),
    "教室标签": ("教室", "教室名称", "上课教室", "场地", "场地名称", "教室编号"),
    "课表编排来源": ("编排来源", "课表来源", "数据来源", "来源"),
    "编排阶段": ("阶段", "课次阶段", "阶段名称"),
    "计划课次": ("课次数量", "总课次", "课次总数", "计划次数"),
    "计划课时": ("课时", "总课时", "计划小时", "课时数"),
    "课次序号": ("序号", "课次", "第几讲", "讲次", "课次编号"),
    "课节名称": ("课节", "课程名称", "课名", "课节主题", "课程", "讲课名称"),
    "上课日期": ("日期", "开课日期", "课次日期", "授课日期", "上课日子"),
    "上课时段": ("时段", "上课时间", "时间段", "上课时间段", "时间", "授课时段", "作息"),
    "课节时长(小时)": ("课节时长", "时长", "时长(小时)", "课时长度", "单次时长", "时长小时"),
    "授课教师": ("任课教师", "授课老师", "老师", "教师", "主讲教师", "任课老师", "讲师", "授课人"),
}

ALIAS_TO_FIELD: dict[str, str] = {
    alias: target for target, aliases in FIELD_ALIASES.items() for alias in aliases
}

_KNOWN_HEADERS = set(TEMPLATE_HEADERS) | set(ALIAS_TO_FIELD)


def _normalize(text: str) -> str:
    """小写、全半角（NFKC）统一、去掉空白与标点，只留字母数字和汉字。"""
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


NORMALIZED_TO_FIELD: dict[str, str] = {
    _normalize(name): name for name in TEMPLATE_HEADERS
}
NORMALIZED_ALIAS_TO_FIELD: dict[str, str] = {
    _normalize(alias): target
    for target, aliases in FIELD_ALIASES.items()
    for alias in aliases
    if _normalize(alias)
}
_KNOWN_NORMALIZED = set(NORMALIZED_TO_FIELD) | set(NORMALIZED_ALIAS_TO_FIELD)


# ---------------------------------------------------------------------------
# 样本形状校验：纠正「表头像但数据不像」的错配。
# ---------------------------------------------------------------------------

_DATE_PATTERN = re.compile(r"\d{4}[-/.年]\s*\d{1,2}[-/.月]\s*\d{1,2}日?")
_CLOCK_RANGE_PATTERN = re.compile(
    r"\d{1,2}[:：]\d{2}\s*[-—~至到]\s*\d{1,2}[:：]\d{2}"
)


def _looks_like_date(value: Any) -> bool:
    if isinstance(value, datetime):
        return True
    if isinstance(value, date):
        return True
    return isinstance(value, str) and bool(_DATE_PATTERN.fullmatch(value.strip()))


def _looks_like_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).strip())
    except (TypeError, ValueError):
        return False
    return True


def _looks_like_clock_range(value: Any) -> bool:
    return isinstance(value, str) and bool(_CLOCK_RANGE_PATTERN.search(value.strip()))


FIELD_SHAPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "上课日期": _looks_like_date,
    "上课时段": _looks_like_clock_range,
    "计划课次": _looks_like_number,
    "计划课时": _looks_like_number,
    "课次序号": _looks_like_number,
    "课节时长(小时)": _looks_like_number,
}

FIELD_SHAPE_LABELS: dict[str, str] = {
    "上课日期": "日期",
    "上课时段": "HH:MM-HH:MM 时段",
    "计划课次": "数字",
    "计划课时": "数字",
    "课次序号": "数字",
    "课节时长(小时)": "数字",
}


# ---------------------------------------------------------------------------
# 解析上传文件
# ---------------------------------------------------------------------------


def parse_uploaded_workbook(filename: str, payload: bytes) -> dict[str, list[list[Any]]]:
    """按扩展名解析上传文件，返回「工作表名 → 行网格」。

    网格单元格保留原始类型（None/数字/字符串/日期），供形状校验判断。
    """
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return {"csv": _parse_csv(payload)}
    if name.endswith(".xlsx"):
        workbook = load_workbook(BytesIO(payload), data_only=True, read_only=True)
        try:
            return {
                sheet.title: [list(row) for row in sheet.iter_rows(values_only=True)]
                for sheet in workbook.worksheets
            }
        finally:
            workbook.close()
    raise ImportMappingError("智能导入只接受 .xlsx 或 .csv 文件")


def _parse_csv(payload: bytes) -> list[list[Any]]:
    """教务系统导出的 CSV 常见 UTF-8（带 BOM）与 GBK 两种编码，按顺序探测。"""
    text: str | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ImportMappingError("CSV 文件既不是 UTF-8 也不是 GBK 编码，无法解析")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


def pick_default_sheet(grids: dict[str, list[list[Any]]]) -> str:
    """默认选非空单元格最多的工作表——教务导出的数据页几乎总是最大的那张。"""
    def nonempty_cells(name: str) -> int:
        return sum(
            1
            for row in grids[name]
            for value in row
            if value is not None and str(value).strip()
        )

    return max(sorted(grids), key=nonempty_cells)


# ---------------------------------------------------------------------------
# 表头行候选
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeaderCandidate:
    row_index: int  # 0-based 网格行号
    score: float
    sample: list[str]


def _is_text_like(value: Any) -> bool:
    """表头单元格应当是有内容的文本，而不是数字、日期或时段。"""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return not (
        _looks_like_date(text) or _looks_like_number(text) or _looks_like_clock_range(text)
    )


def detect_header_candidates(grid: list[list[Any]]) -> list[HeaderCandidate]:
    """扫描前 10 行，按「像表头的程度」给出候选行。

    打分权重：命中已知字段/别名的比例最重要（2.0），其次是文本单元格比例（0.8）、
    取值唯一性（0.4）和列数覆盖（0.2）。全空或单格行不参与。
    """
    candidates: list[HeaderCandidate] = []
    for index in range(min(len(grid), HEADER_SCAN_ROWS)):
        cells = [value for value in grid[index] if value is not None and str(value).strip()]
        if len(cells) < 2:
            continue
        total = len(cells)
        known = sum(1 for value in cells if _is_known_header(value))
        text_like = sum(1 for value in cells if _is_text_like(value))
        unique = len({str(value).strip() for value in cells})
        score = (
            2.0 * known / total
            + 0.8 * text_like / total
            + 0.4 * unique / total
            + 0.2 * min(total / len(TEMPLATE_HEADERS), 1.0)
        )
        candidates.append(
            HeaderCandidate(index, round(score, 4), [str(value).strip() for value in cells[:14]])
        )
    candidates.sort(key=lambda item: (-item.score, item.row_index))
    return candidates[:3]


def _is_known_header(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text in _KNOWN_HEADERS:
        return True
    return _normalize(text) in _KNOWN_NORMALIZED


# ---------------------------------------------------------------------------
# 列映射建议
# ---------------------------------------------------------------------------


@dataclass
class ColumnMapping:
    column: str
    column_index: int
    target: str | None
    confidence: float
    rationale: str
    matched_by: str  # exact / alias / normalized / fuzzy / llm / manual / unmatched
    sample_values: list[str] = field(default_factory=list)


def suggest_mapping(
    headers: Sequence[Any],
    column_samples: Sequence[Sequence[Any]],
    *,
    ai_resolver: AIResolver | None = None,
) -> tuple[list[ColumnMapping], bool]:
    """为每个表头列给出目标字段建议，返回（映射清单, 是否用到了 AI 语义层）。

    ``column_samples[i]`` 是第 i 列的非空样本值（调用方负责按列抽取）。
    """
    mapping = [
        _score_column(index, headers[index] if index < len(headers) else None, column_samples)
        for index in range(len(headers))
    ]
    _resolve_target_conflicts(mapping)
    ai_used = _apply_ai_semantics(mapping, column_samples, ai_resolver)
    if ai_used:
        # AI 提名可能让两列撞同一个字段，再收敛一次（同置信度时先到先得）。
        _resolve_target_conflicts(mapping)
    return mapping, ai_used


def _score_column(
    index: int, header: Any, column_samples: Sequence[Sequence[Any]]
) -> ColumnMapping:
    text = str(header).strip() if header is not None else ""
    normalized = _normalize(text)
    samples = [
        value
        for value in (column_samples[index] if index < len(column_samples) else [])
        if value is not None and str(value).strip()
    ][:SAMPLE_SIZE]
    display = [str(value).strip() for value in samples[:3]]

    hit: tuple[str, float, str, str] | None = None  # (target, confidence, layer, rationale)
    if text and text in TEMPLATE_HEADERS:
        hit = (text, 1.0, "exact", f"表头与规范字段「{text}」完全一致")
    elif text and text in ALIAS_TO_FIELD:
        target = ALIAS_TO_FIELD[text]
        hit = (target, 0.95, "alias", f"表头命中别名表：「{text}」→「{target}」")
    elif normalized and normalized in NORMALIZED_TO_FIELD:
        target = NORMALIZED_TO_FIELD[normalized]
        hit = (target, 0.9, "normalized", f"表头归一化后与规范字段「{target}」一致")
    elif normalized and normalized in NORMALIZED_ALIAS_TO_FIELD:
        target = NORMALIZED_ALIAS_TO_FIELD[normalized]
        hit = (target, 0.85, "normalized", f"表头归一化后命中别名：「{text}」→「{target}」")

    if hit is None and normalized:
        best_ratio = FUZZY_MIN_RATIO
        best_field: str | None = None
        for field_name in TEMPLATE_HEADERS:
            ratio = difflib.SequenceMatcher(None, normalized, _normalize(field_name)).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_field = field_name
        if best_field is not None:
            confidence = round(
                0.5 + 0.3 * (best_ratio - FUZZY_MIN_RATIO) / (1 - FUZZY_MIN_RATIO), 3
            )
            hit = (
                best_field,
                confidence,
                "fuzzy",
                f"表头与「{best_field}」的文本相似度为 {best_ratio:.2f}",
            )

    if hit is None:
        return ColumnMapping(
            text, index, None, 0.0, "表头没有命中任何规范字段、别名或相近文本", "unmatched", display
        )

    target, confidence, layer, rationale = hit
    # 样本形状校验：形状不符就把置信度压到阈值之下，宁可留给用户手动指定。
    if samples:
        checker = FIELD_SHAPE_CHECKS.get(target)
        if checker is not None:
            shape_ratio = sum(1 for value in samples if checker(value)) / len(samples)
            if shape_ratio < SHAPE_MIN_RATIO:
                confidence = min(confidence, 0.3)
                rationale += (
                    f"；但前 {len(samples)} 行样本只有 {shape_ratio:.0%} 像"
                    f"{FIELD_SHAPE_LABELS[target]}，疑似「{target}」但形状校验不通过"
                )
            else:
                rationale += f"；前 {len(samples)} 行样本形状校验通过"
    else:
        rationale += "；该列没有可校验的非空样本"

    if confidence < CONFIDENCE_THRESHOLD:
        return ColumnMapping(text, index, None, confidence, rationale, "unmatched", display)
    return ColumnMapping(text, index, target, confidence, rationale, layer, display)


def _resolve_target_conflicts(mapping: list[ColumnMapping]) -> None:
    """一个规范字段只允许一列占用；同字段多列保留置信度最高者（平分先到先得）。"""
    best_by_target: dict[str, ColumnMapping] = {}
    for item in mapping:
        if item.target is None:
            continue
        current = best_by_target.get(item.target)
        if current is None or item.confidence > current.confidence:
            best_by_target[item.target] = item
    for item in mapping:
        if item.target is None:
            continue
        winner = best_by_target[item.target]
        if winner is not item:
            item.target = None
            item.matched_by = "unmatched"
            item.rationale += f"；「{winner.column}」以更高置信度占用了该字段"


def _apply_ai_semantics(
    mapping: list[ColumnMapping],
    column_samples: Sequence[Sequence[Any]],
    ai_resolver: AIResolver | None,
) -> bool:
    """LLM 语义层：只对机械匹配失败的列提名，任何失败都退回未匹配。"""
    if ai_resolver is None:
        return False
    pending = [item for item in mapping if item.target is None and item.column]
    if not pending:
        return False
    payload = [
        {
            "column_index": item.column_index,
            "column": item.column,
            "样本值": [
                str(value).strip()
                for value in (column_samples[item.column_index] or [])[:5]
                if value is not None
            ],
        }
        for item in pending
    ]
    try:
        accepted = ai_resolver(payload)
    except Exception:
        # LLM 是可选增强：接口未配置、超时、输出不合法都不阻塞导入向导。
        return False
    if not isinstance(accepted, dict):
        return False
    used = False
    for item in mapping:
        if item.target is not None or item.column_index not in accepted:
            continue
        target = accepted[item.column_index]
        if target not in TEMPLATE_HEADERS:
            continue
        item.target = target
        item.confidence = AI_CONFIDENCE
        item.matched_by = "llm"
        item.rationale = "AI 语义匹配：根据列名与样本值判定，请在提交前确认"
        used = True
    return used


# ---------------------------------------------------------------------------
# 用户修正映射（Fix 循环）与记录构建
# ---------------------------------------------------------------------------


def header_texts(grid: list[list[Any]], header_row_index: int) -> list[str]:
    """取表头行的单元格文本；超短行按缺空列补齐。"""
    row = grid[header_row_index] if 0 <= header_row_index < len(grid) else []
    return [str(value).strip() if value is not None else "" for value in row]


def column_samples(grid: list[list[Any]], header_row_index: int) -> list[list[Any]]:
    """按列抽取表头之下的前 N 个非空样本。"""
    width = max((len(row) for row in grid), default=0)
    samples: list[list[Any]] = [[] for _ in range(width)]
    for row in grid[header_row_index + 1 :]:
        for index in range(min(len(row), width)):
            value = row[index]
            if value is not None and str(value).strip() and len(samples[index]) < SAMPLE_SIZE:
                samples[index].append(value)
    return samples


def apply_manual_mapping(
    headers: Sequence[str],
    entries: Sequence[tuple[int, str, str | None]],
) -> list[ColumnMapping]:
    """把用户修正后的映射转成映射清单（Fix 循环的确定性回放）。

    ``entries`` 是 (column_index, column, target) 三元组；target 为 None 表示该列
    不导入。与自动建议不同，用户指定的映射置信度记 1.0、直接生效。
    """
    mapping: list[ColumnMapping | None] = [None] * len(headers)
    claimed: dict[str, int] = {}
    for column_index, column, target in entries:
        if not 0 <= column_index < len(headers):
            raise ImportMappingError(f"映射里的列下标 {column_index} 超出表头范围")
        if headers[column_index] and column and headers[column_index] != column:
            raise ImportMappingError(
                f"列下标 {column_index} 的表头与映射里的「{column}」不一致"
            )
        if target is not None:
            if target not in TEMPLATE_HEADERS:
                raise ImportMappingError(f"列「{column}」的目标「{target}」不是规范字段")
            if target in claimed:
                raise ImportMappingError(
                    f"「{target}」被「{headers[claimed[target]]}」和「{column}」同时映射，一个字段只能对应一列"
                )
            claimed[target] = column_index
        mapping[column_index] = ColumnMapping(
            column,
            column_index,
            target,
            1.0 if target is not None else 0.0,
            "用户在导入向导中手动指定" if target is not None else "用户指定该列不导入",
            "manual" if target is not None else "unmatched",
        )
    # 用户没提到的列：表头有名字就标未匹配，否则保持占位。
    result: list[ColumnMapping] = []
    for index, item in enumerate(mapping):
        if item is not None:
            result.append(item)
        else:
            result.append(
                ColumnMapping(
                    headers[index], index, None, 0.0, "映射未覆盖该列", "unmatched", []
                )
            )
    return result


def build_records(
    grid: list[list[Any]],
    header_row_index: int,
    targets: dict[int, str],
) -> tuple[list[dict[str, Any]], list[int]]:
    """按映射把网格裁成「规范表头 → 值」的记录流，返回（记录, 1-based 工作表行号）。"""
    ordered = sorted(targets.items())
    records: list[dict[str, Any]] = []
    row_numbers: list[int] = []
    for offset, row in enumerate(grid[header_row_index + 1 :], start=header_row_index + 2):
        records.append(
            {name: (row[index] if index < len(row) else None) for index, name in ordered}
        )
        row_numbers.append(offset)
    return records, row_numbers


# ---------------------------------------------------------------------------
# historical mapping：按表头指纹记忆上次生效的映射（docs/roadmap/01 IMP-4）
# ---------------------------------------------------------------------------


def header_fingerprint(headers: Sequence[Any]) -> str:
    """表头指纹：规范化表头序列（列序敏感）的 sha256 十六进制。

    规范化复用 :func:`_normalize`——同一份教务导出改大小写/全半角/空白不换指纹，
    调换列序或增删列就算新指纹。分隔符拼接防相邻表头首尾相接的碰撞。
    """
    joined = _FINGERPRINT_SEPARATOR.join(
        _normalize(str(value)) if value is not None else "" for value in headers
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def history_payload(
    mapping: Sequence[ColumnMapping], *, sheet: str | None, header_row_index: int
) -> dict[str, Any]:
    """把生效映射（含手动修正）序列化成与 ``ImportMappingInput`` 同构的可回放结构。"""
    return {
        "sheet": sheet,
        "header_row_index": header_row_index,
        "columns": [
            {
                "column": item.column,
                "column_index": item.column_index,
                "target": item.target,
            }
            for item in mapping
            if item.column
        ],
    }


def apply_historical_mapping(
    headers: Sequence[str], stored: Any
) -> list[ColumnMapping] | None:
    """把历史映射记录回放成映射清单；记录不合法时返回 None（退回自动建议）。

    指纹已经钉死表头序列，列名对不上只可能是脏数据（历史结构损坏、手工改库），
    宁可整份弃用也不硬套。回放走 :func:`apply_manual_mapping` 的确定性路径，
    上次「用户指定不导入」的列同样以历史为准。
    """
    if not isinstance(stored, dict) or not isinstance(stored.get("columns"), list):
        return None
    entries: list[tuple[int, str, str | None]] = []
    for item in stored["columns"]:
        if not isinstance(item, dict):
            return None
        column = item.get("column")
        column_index = item.get("column_index")
        target = item.get("target")
        if not isinstance(column, str) or not column:
            return None
        if not isinstance(column_index, int) or isinstance(column_index, bool):
            return None
        if target is not None and (not isinstance(target, str) or target not in TEMPLATE_HEADERS):
            return None
        entries.append((column_index, column, target))
    if not entries:
        return None
    try:
        mapping = apply_manual_mapping(headers, entries)
    except ImportMappingError:
        return None
    for item in mapping:
        item.confidence = HISTORICAL_CONFIDENCE
        item.matched_by = "historical"
        item.rationale = (
            "沿用上次导入时确认的映射决策"
            if item.target is not None
            else "沿用上次导入时「不导入」的决策"
        )
    return mapping


__all__ = [
    "AI_CONFIDENCE",
    "CONFIDENCE_THRESHOLD",
    "HISTORICAL_CONFIDENCE",
    "ColumnMapping",
    "FIELD_ALIASES",
    "HeaderCandidate",
    "ImportMappingError",
    "WorkbookFormatError",
    "apply_historical_mapping",
    "apply_manual_mapping",
    "build_records",
    "column_samples",
    "detect_header_candidates",
    "header_fingerprint",
    "header_texts",
    "history_payload",
    "parse_uploaded_workbook",
    "pick_default_sheet",
    "suggest_mapping",
]
