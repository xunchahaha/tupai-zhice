"""企业微信（WeCom）服务端 API 客户端：智能表格、日程与应用消息。

端点按 developer.work.weixin.qq.com 官方文档实现，均走
``/cgi-bin/...?access_token=...`` 查询参数鉴权；access_token 经 platform_api
内存缓存到期刷新。HTTP 层已有 MockTransport 单测覆盖，但**未经生产凭据联调**
——端点路径与批量上限常量如有出入，以官方文档为准并欢迎 issue 反馈。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ..platform_api import cached_token, chunks, store_token

DEFAULT_BASE_URL = "https://qyapi.weixin.qq.com"
TOKEN_PATH = "/cgi-bin/gettoken"
TOKEN_MARGIN_SECONDS = 300.0
# message/send 的 touser 单次上限（官方文档：'|' 连接，最多 1000 个）。
NOTIFY_MAX_RECEIVERS = 1000
# 智能表格记录批量写/删的分批上限：官方单页查询上限 1000 条；批量写上限未经
# 生产凭据核对，先按保守值收紧（ROADMAP §2.1：企微智能表格为三家最受限）。
RECORD_BATCH_SIZE = 100
RECORD_LIST_PAGE_SIZE = 100


class WeComError(RuntimeError):
    """企业微信接口调用失败（网络、HTTP 状态或业务 errcode）。"""

    def __init__(self, message: str, *, errcode: int | None = None) -> None:
        super().__init__(message)
        self.errcode = errcode


@dataclass(frozen=True)
class WeComConfig:
    corp_id: str
    corp_secret: str
    agent_id: int
    base_url: str = DEFAULT_BASE_URL


class WeComClient:
    """无状态客户端：每次调用独立建连，令牌缓存为进程级共享。"""

    def __init__(
        self,
        config: WeComConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.config = config
        self._transport = transport
        self._timeout = timeout

    # -- 底层请求 -----------------------------------------------------------

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = await client.request(method, url, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise WeComError(f"企业微信接口网络错误：{exc}") from exc
        return self._decode(response)

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise WeComError(
                f"企业微信接口返回非 JSON 响应（HTTP {response.status_code}）"
            ) from exc
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if response.status_code >= 400:
            raise WeComError(
                f"企业微信接口错误（HTTP {response.status_code}）：{response.text}",
                errcode=None,
            )
        # 企业微信以 HTTP 200 + errcode 表达业务失败（凭据错误也走这条路径）。
        errcode = body.get("errcode")
        if errcode not in (None, 0):
            raise WeComError(
                f"企业微信接口错误（errcode={errcode}）：{body.get('errmsg')}",
                errcode=int(str(errcode)),
            )
        return body

    async def access_token(self) -> str:
        """获取（或命中缓存的）access_token；verify() 用它做轻量凭据探测。"""

        cache_key = self.config.corp_secret
        token = cached_token("wecom", cache_key)
        if token:
            return token
        payload = await self._request_json(
            "GET",
            f"{self.config.base_url}{TOKEN_PATH}",
            params={"corpid": self.config.corp_id, "corpsecret": self.config.corp_secret},
        )
        token = str(payload.get("access_token") or "")
        if not token:
            raise WeComError("企业微信未返回 access_token，请核对 corpid 与应用 secret")
        store_token(
            "wecom",
            cache_key,
            token,
            float(payload.get("expires_in") or 0),
            margin_seconds=TOKEN_MARGIN_SECONDS,
        )
        return token

    async def _authorized(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        token = await self.access_token()
        return await self._request_json(
            "POST",
            f"{self.config.base_url}{path}",
            json_body=dict(payload),
            params={"access_token": token},
        )

    # -- 智能表格（wedoc smartsheet）-----------------------------------------

    async def add_records(
        self, doc_id: str, sheet_id: str, records: Sequence[Mapping[str, Any]]
    ) -> int:
        """批量新增记录（形如 ``{"values": {...}}``），按上限分批，返回写入数。"""

        written = 0
        for batch in chunks(list(records), RECORD_BATCH_SIZE):
            await self._authorized(
                "/cgi-bin/wedoc/smartsheet/add_records",
                {
                    "docid": doc_id,
                    "sheet_id": sheet_id,
                    "records": [dict(row) for row in batch],
                },
            )
            written += len(batch)
        return written

    async def update_records(
        self, doc_id: str, sheet_id: str, records: Sequence[Mapping[str, Any]]
    ) -> int:
        """批量修改记录（形如 ``{"record_id": ..., "values": {...}}``），分批提交。"""

        written = 0
        for batch in chunks(list(records), RECORD_BATCH_SIZE):
            await self._authorized(
                "/cgi-bin/wedoc/smartsheet/update_records",
                {
                    "docid": doc_id,
                    "sheet_id": sheet_id,
                    "records": [dict(row) for row in batch],
                },
            )
            written += len(batch)
        return written

    async def upsert_records(
        self, doc_id: str, sheet_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> dict[str, int]:
        """按行是否携带 ``record_id`` 拆分新建/更新，并各自按批量上限分批。"""

        creates = [row for row in rows if not row.get("record_id")]
        updates = [row for row in rows if row.get("record_id")]
        return {
            "created": await self.add_records(doc_id, sheet_id, creates),
            "updated": await self.update_records(doc_id, sheet_id, updates),
        }

    async def list_records(
        self, doc_id: str, sheet_id: str, *, max_results: int = 500, offset: int = 0
    ) -> list[dict[str, Any]]:
        """按 offset/limit 翻页拉取记录，累计到 max_results 为止。"""

        records: list[dict[str, Any]] = []
        cursor = offset
        while len(records) < max_results:
            limit = min(RECORD_LIST_PAGE_SIZE, max_results - len(records))
            payload = await self._authorized(
                "/cgi-bin/wedoc/smartsheet/get_records",
                {"docid": doc_id, "sheet_id": sheet_id, "offset": cursor, "limit": limit},
            )
            batch = list(payload.get("records") or [])
            records.extend(batch)
            if len(batch) < limit:
                break
            cursor += len(batch)
        return records[:max_results]

    async def delete_records(self, doc_id: str, sheet_id: str, record_ids: Sequence[str]) -> int:
        deleted = 0
        for batch in chunks(list(record_ids), RECORD_BATCH_SIZE):
            await self._authorized(
                "/cgi-bin/wedoc/smartsheet/delete_records",
                {"docid": doc_id, "sheet_id": sheet_id, "record_ids": list(batch)},
            )
            deleted += len(batch)
        return deleted

    # -- 日程（oa schedule）---------------------------------------------------

    async def create_schedule(
        self,
        *,
        title: str,
        start_time: int,
        end_time: int,
        description: str = "",
        attendee_ids: Sequence[str] = (),
        agent_id: int | None = None,
    ) -> str:
        """创建日程；企业微信限制：只能落在应用自建日历下，不能指定其他日历。"""

        payload = await self._authorized(
            "/cgi-bin/oa/schedule/add",
            {
                "schedule": {
                    "title": title,
                    "description": description,
                    "start_time": start_time,
                    "end_time": end_time,
                    "attendees": [{"userid": user_id} for user_id in attendee_ids],
                },
                "agentid": agent_id or self.config.agent_id,
            },
        )
        return str(payload.get("schedule_id") or "")

    async def update_schedule(
        self,
        schedule_id: str,
        *,
        title: str,
        start_time: int,
        end_time: int,
        description: str = "",
        attendee_ids: Sequence[str] = (),
        agent_id: int | None = None,
    ) -> None:
        await self._authorized(
            "/cgi-bin/oa/schedule/update",
            {
                "schedule": {
                    "schedule_id": schedule_id,
                    "title": title,
                    "description": description,
                    "start_time": start_time,
                    "end_time": end_time,
                    "attendees": [{"userid": user_id} for user_id in attendee_ids],
                },
                "agentid": agent_id or self.config.agent_id,
            },
        )

    async def delete_schedule(self, schedule_id: str) -> None:
        await self._authorized("/cgi-bin/oa/schedule/del", {"schedule_id": schedule_id})

    # -- 应用消息（notifier）--------------------------------------------------

    async def send_message(
        self, user_ids: Sequence[str], content: str, *, agent_id: int | None = None
    ) -> list[str]:
        """应用文本消息；touser 单次 ≤ 1000（'|' 连接），超出自动分批。

        返回平台回执中的无效 userid（如成员不在应用可见范围内）。
        """

        invalid: list[str] = []
        for batch in chunks(list(user_ids), NOTIFY_MAX_RECEIVERS):
            payload = await self._authorized(
                "/cgi-bin/message/send",
                {
                    "touser": "|".join(batch),
                    "msgtype": "text",
                    "agentid": agent_id or self.config.agent_id,
                    "text": {"content": content},
                },
            )
            raw_invalid = payload.get("invaliduser")
            if isinstance(raw_invalid, str) and raw_invalid:
                invalid.extend(part for part in raw_invalid.replace(";", "|").split("|") if part)
            elif isinstance(raw_invalid, list):
                invalid.extend(str(item) for item in raw_invalid if item)
        return invalid
