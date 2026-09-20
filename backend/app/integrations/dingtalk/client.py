"""钉钉开放平台 HTTP 客户端：AI 表格记录、日程、工作通知与 OA 审批封装。

端点按 open.dingtalk.com 官方文档实现：新版 v1.0 接口（api.dingtalk.com）用
``x-acs-dingtalk-access-token`` 请求头鉴权，旧版 topapi（oapi.dingtalk.com）用
``access_token`` 查询参数鉴权；access_token 经 platform_api 内存缓存到期刷新。
HTTP 层已有 MockTransport 单测覆盖，但**未经生产凭据联调**——端点路径与批量
上限常量如有出入，以官方文档为准并欢迎 issue 反馈。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ..platform_api import cached_token, chunks, store_token

DEFAULT_BASE_URL = "https://api.dingtalk.com"
LEGACY_BASE_URL = "https://oapi.dingtalk.com"
TOKEN_PATH = "/v1.0/oauth2/accessToken"
TOKEN_MARGIN_SECONDS = 300.0
# 钉钉以当前用户默认日历承接日程（calendarId 固定为 calendar.default）。
DEFAULT_CALENDAR_ID = "calendar.default"
# 工作通知 asyncsend_v2 的 userid_list 单次上限（官方文档：100 人）。
NOTIFY_MAX_RECEIVERS = 100
# AI 表格记录批量写/删的分批上限：官方查询 maxResults≤100；批量写上限未经
# 生产凭据核对，先按同量级收紧（ROADMAP §2.1：AI 表格能力弱于飞书多维表格）。
RECORD_BATCH_SIZE = 100
RECORD_LIST_PAGE_SIZE = 100


class DingTalkError(RuntimeError):
    """钉钉接口调用失败（网络、HTTP 状态或业务 errcode）。"""

    def __init__(self, message: str, *, code: str | int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DingTalkConfig:
    app_key: str
    app_secret: str
    base_url: str = DEFAULT_BASE_URL
    # OA 审批模板编码：企业必须预先在审批后台建好模板，为空则审批能力不可用。
    process_code: str = ""


class DingTalkClient:
    """无状态客户端：每次调用独立建连，令牌缓存为进程级共享。"""

    def __init__(
        self,
        config: DingTalkConfig,
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
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = await client.request(
                    method, url, json=json_body, params=params, headers=headers
                )
        except httpx.HTTPError as exc:
            raise DingTalkError(f"钉钉接口网络错误：{exc}") from exc
        return self._decode(response)

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise DingTalkError(
                f"钉钉接口返回非 JSON 响应（HTTP {response.status_code}）"
            ) from exc
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if response.status_code >= 400:
            code = body.get("code") or body.get("errcode")
            message = str(body.get("message") or body.get("errmsg") or response.text)
            raise DingTalkError(
                f"钉钉接口错误（HTTP {response.status_code}，code={code}）：{message}", code=code
            )
        # 旧版 topapi 以 HTTP 200 + errcode 表达业务失败。
        errcode = body.get("errcode")
        if errcode not in (None, 0):
            raise DingTalkError(
                f"钉钉接口错误（errcode={errcode}）：{body.get('errmsg')}", code=errcode
            )
        return body

    async def access_token(self) -> str:
        """获取（或命中缓存的）access_token；verify() 用它做轻量凭据探测。"""

        cache_key = self.config.app_key
        token = cached_token("dingtalk", cache_key)
        if token:
            return token
        payload = await self._request_json(
            "POST",
            f"{self.config.base_url}{TOKEN_PATH}",
            json_body={"appKey": self.config.app_key, "appSecret": self.config.app_secret},
        )
        token = str(payload.get("accessToken") or "")
        if not token:
            raise DingTalkError("钉钉未返回 accessToken，请核对 AppKey 与 AppSecret")
        store_token(
            "dingtalk",
            cache_key,
            token,
            float(payload.get("expireIn") or 0),
            margin_seconds=TOKEN_MARGIN_SECONDS,
        )
        return token

    async def _authorized_new_api(
        self, method: str, path: str, *, json_body: Any = None
    ) -> dict[str, Any]:
        token = await self.access_token()
        return await self._request_json(
            method,
            f"{self.config.base_url}{path}",
            json_body=json_body,
            headers={"x-acs-dingtalk-access-token": token},
        )

    async def _authorized_legacy(
        self, path: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        token = await self.access_token()
        return await self._request_json(
            "POST",
            f"{LEGACY_BASE_URL}{path}",
            json_body=dict(payload),
            params={"access_token": token},
        )

    # -- AI 表格（notable）--------------------------------------------------

    async def create_records(
        self, base_id: str, sheet: str, records: Sequence[Mapping[str, Any]]
    ) -> int:
        """批量新建记录（形如 ``{"fields": {...}}``），按上限分批，返回写入数。"""

        written = 0
        for batch in chunks(list(records), RECORD_BATCH_SIZE):
            await self._authorized_new_api(
                "POST",
                f"/v1.0/notable/bases/{base_id}/sheets/{sheet}/records",
                json_body={"records": [dict(row) for row in batch]},
            )
            written += len(batch)
        return written

    async def update_records(
        self, base_id: str, sheet: str, records: Sequence[Mapping[str, Any]]
    ) -> int:
        """批量修改记录（形如 ``{"id": ..., "fields": {...}}``），按上限分批。"""

        written = 0
        for batch in chunks(list(records), RECORD_BATCH_SIZE):
            await self._authorized_new_api(
                "PUT",
                f"/v1.0/notable/bases/{base_id}/sheets/{sheet}/records",
                json_body={"records": [dict(row) for row in batch]},
            )
            written += len(batch)
        return written

    async def upsert_records(
        self, base_id: str, sheet: str, rows: Sequence[Mapping[str, Any]]
    ) -> dict[str, int]:
        """按行是否携带 ``id`` 拆分新建/更新，并各自按批量上限分批提交。"""

        creates = [row for row in rows if not row.get("id")]
        updates = [row for row in rows if row.get("id")]
        return {
            "created": await self.create_records(base_id, sheet, creates),
            "updated": await self.update_records(base_id, sheet, updates),
        }

    async def list_records(
        self, base_id: str, sheet: str, *, max_results: int = 500
    ) -> list[dict[str, Any]]:
        """按 nextToken 翻页拉取记录，累计到 max_results 为止。"""

        records: list[dict[str, Any]] = []
        next_token = ""
        while len(records) < max_results:
            body: dict[str, Any] = {
                "maxResults": min(RECORD_LIST_PAGE_SIZE, max_results - len(records))
            }
            if next_token:
                body["nextToken"] = next_token
            payload = await self._authorized_new_api(
                "POST",
                f"/v1.0/notable/bases/{base_id}/sheets/{sheet}/records/query",
                json_body=body,
            )
            records.extend(payload.get("records") or [])
            next_token = str(payload.get("nextToken") or "")
            if not next_token:
                break
        return records[:max_results]

    async def delete_records(self, base_id: str, sheet: str, record_ids: Sequence[str]) -> int:
        deleted = 0
        for batch in chunks(list(record_ids), RECORD_BATCH_SIZE):
            await self._authorized_new_api(
                "DELETE",
                f"/v1.0/notable/bases/{base_id}/sheets/{sheet}/records",
                json_body={"recordIds": list(batch)},
            )
            deleted += len(batch)
        return deleted

    # -- 日程 ----------------------------------------------------------------

    async def create_event(
        self,
        user_id: str,
        *,
        summary: str,
        start: str,
        end: str,
        description: str = "",
        time_zone: str = "Asia/Shanghai",
        attendee_ids: Sequence[str] = (),
    ) -> str:
        """在用户默认日历创建日程；start/end 为含时区偏移的 ISO8601 文本。"""

        payload = await self._authorized_new_api(
            "POST",
            f"/v1.0/calendar/users/{user_id}/calendars/{DEFAULT_CALENDAR_ID}/events",
            json_body={
                "summary": summary,
                "description": description,
                "start": {"dateTime": start, "timeZone": time_zone},
                "end": {"dateTime": end, "timeZone": time_zone},
                "attendee": {"duserIds": list(attendee_ids)},
            },
        )
        return str(payload.get("id") or "")

    async def update_event(
        self,
        user_id: str,
        event_id: str,
        *,
        summary: str,
        start: str,
        end: str,
        description: str = "",
        time_zone: str = "Asia/Shanghai",
    ) -> None:
        await self._authorized_new_api(
            "PUT",
            f"/v1.0/calendar/users/{user_id}/calendars/{DEFAULT_CALENDAR_ID}/events/{event_id}",
            json_body={
                "summary": summary,
                "description": description,
                "start": {"dateTime": start, "timeZone": time_zone},
                "end": {"dateTime": end, "timeZone": time_zone},
            },
        )

    async def delete_event(self, user_id: str, event_id: str) -> None:
        await self._authorized_new_api(
            "DELETE",
            f"/v1.0/calendar/users/{user_id}/calendars/{DEFAULT_CALENDAR_ID}/events/{event_id}",
        )

    # -- 工作通知（notifier）-------------------------------------------------

    async def send_work_notification(
        self, *, agent_id: str, user_ids: Sequence[str], content: str
    ) -> list[str]:
        """工作通知（企业异步任务）：单次 ≤ 100 人，超出自动分批，返回任务 ID。"""

        task_ids: list[str] = []
        for batch in chunks(list(user_ids), NOTIFY_MAX_RECEIVERS):
            payload = await self._authorized_legacy(
                "/topapi/message/corpconversation/asyncsend_v2",
                {
                    "agent_id": agent_id,
                    "userid_list": ",".join(batch),
                    "msg": {"msgtype": "text", "text": {"content": content}},
                },
            )
            result = payload.get("result")
            task_id = result.get("task_id") if isinstance(result, dict) else None
            if task_id is not None:
                task_ids.append(str(task_id))
        return task_ids

    # -- OA 审批（approval）--------------------------------------------------

    async def create_approval_instance(
        self,
        *,
        originator_user_id: str,
        dept_id: int,
        form_values: Sequence[Mapping[str, str]],
        process_code: str | None = None,
    ) -> str:
        """代发起 OA 审批实例；只能基于企业已预建审批模板（process_code）。"""

        code = (process_code or self.config.process_code).strip()
        if not code:
            raise DingTalkError("未配置审批模板 process_code，无法发起钉钉 OA 审批")
        payload = await self._authorized_new_api(
            "POST",
            "/v1.0/workflow/processInstances",
            json_body={
                "processCode": code,
                "originatorUserId": originator_user_id,
                "deptId": dept_id,
                "formComponentValues": [dict(item) for item in form_values],
            },
        )
        return str(payload.get("instanceId") or "")
