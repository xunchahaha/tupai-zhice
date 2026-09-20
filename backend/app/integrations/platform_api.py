"""国内平台 HTTP 客户端共享件：access_token 内存缓存与批量分片。

令牌缓存是飞书连接刷新锁（services/feishu.py）的简化版（roadmap §2.1 的
统一 AuthProvider 思路先落在这两个 v1 适配器上）：纯内存 + 互斥保护，不落库，
进程重启后由下一次调用自动重新获取。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from typing import TypeVar

T = TypeVar("T")

_MUTEX = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
# 默认到期前扣除 5 分钟安全余量；不足 60 秒的剩余有效期视为已过期。
DEFAULT_MARGIN_SECONDS = 300.0
MIN_TTL_SECONDS = 60.0


def cached_token(scope: str, key: str) -> str | None:
    """命中未过期（含安全余量）缓存时返回令牌，否则 None。"""

    with _MUTEX:
        cached = _CACHE.get((scope, key))
    if cached is None:
        return None
    token, expires_at = cached
    return token if time.monotonic() < expires_at else None


def store_token(
    scope: str,
    key: str,
    token: str,
    expires_in: float,
    *,
    margin_seconds: float = DEFAULT_MARGIN_SECONDS,
) -> None:
    """写入缓存；到期时间扣除安全余量，避免边界请求携带临期令牌。"""

    ttl = max(expires_in - margin_seconds, MIN_TTL_SECONDS)
    with _MUTEX:
        _CACHE[(scope, key)] = (token, time.monotonic() + ttl)


def clear_token_cache(scope: str | None = None) -> None:
    """清空缓存（凭据更换或测试隔离用）；scope=None 清全部平台。"""

    with _MUTEX:
        if scope is None:
            _CACHE.clear()
        else:
            for cache_key in [key for key in _CACHE if key[0] == scope]:
                _CACHE.pop(cache_key, None)


def chunks(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """按平台批量上限切片；size 必须为正。"""

    if size <= 0:
        raise ValueError("chunk size must be positive")
    for start in range(0, len(items), size):
        yield items[start : start + size]
