"""钉钉集成：AI 表格 / 日历 / 工作通知 / OA 审批（v1，凭据走设置页直填）。"""

from .adapter import UNCONFIGURED_DETAIL, DingTalkAdapter

__all__ = ["DingTalkAdapter", "UNCONFIGURED_DETAIL"]
