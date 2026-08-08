from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import Settings


class FeishuClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._token: str | None = None
        self._expires_at = 0.0

    @property
    def mode(self) -> str:
        return "live" if self.settings.feishu_credentials_configured else "unconfigured"

    def require_sync_ready(self) -> None:
        missing = self.settings.feishu_missing_fields
        if missing:
            raise RuntimeError(f"请先配置飞书环境变量：{'、'.join(missing)}")
        missing_resources = self.settings.feishu_missing_resources
        if missing_resources:
            raise RuntimeError(
                f"请先补齐多维表格映射：{'、'.join(missing_resources)}"
            )

    def _tenant_token(self) -> str:
        if self._token and time.time() < self._expires_at:
            return self._token
        response = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self.settings.feishu_app_id,
                "app_secret": self.settings.feishu_app_secret,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"飞书应用凭据校验失败：{payload.get('msg', '接口返回错误')}")
        self._token = payload["tenant_access_token"]
        self._expires_at = time.time() + int(payload.get("expire", 7200)) - 120
        return self._token

    def test_connection(self) -> dict[str, Any]:
        missing_fields = self.settings.feishu_missing_fields
        missing_resources = self.settings.feishu_missing_resources
        if missing_fields:
            return {
                "mode": "unconfigured",
                "configured": False,
                "connected": False,
                "table_mapping_configured": False,
                "missing_fields": missing_fields,
                "missing_resources": missing_resources,
                "message": "尚未配置飞书生产连接，请按下方引导完成配置。",
                "console_url": "https://open.feishu.cn/app/",
                "docs_url": "https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal",
            }
        try:
            self._tenant_token()
        except (httpx.HTTPError, RuntimeError) as exc:
            return {
                "mode": "live",
                "configured": True,
                "connected": False,
                "table_mapping_configured": not missing_resources,
                "missing_fields": [],
                "missing_resources": missing_resources,
                "message": f"飞书生产连接检查失败：{exc}",
                "console_url": "https://open.feishu.cn/app/",
                "docs_url": "https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal",
            }
        message = "飞书生产连接已验证。"
        if missing_resources:
            message += f" 还需补齐 {len(missing_resources)} 个多维表格映射。"
        return {
            "mode": "live",
            "configured": True,
            "connected": True,
            "table_mapping_configured": not missing_resources,
            "missing_fields": [],
            "missing_resources": missing_resources,
            "message": message,
            "console_url": "https://open.feishu.cn/app/",
            "docs_url": "https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal",
        }

    def list_records(self, table_id: str) -> list[dict[str, Any]]:
        self.require_sync_ready()
        if not table_id:
            raise RuntimeError("缺少飞书数据表标识")
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            response = httpx.get(
                "https://open.feishu.cn/open-apis/bitable/v1/apps/"
                f"{self.settings.feishu_bitable_app_token}/tables/{table_id}/records",
                headers={"Authorization": f"Bearer {self._tenant_token()}"},
                params={"page_size": 500, "page_token": page_token},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise RuntimeError(f"飞书记录读取失败：{payload.get('msg', '接口返回错误')}")
            data = payload["data"]
            records.extend(data.get("items", []))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return records

    def batch_create(self, table_id: str, fields: list[dict[str, Any]]) -> int:
        self.require_sync_ready()
        if not table_id:
            raise RuntimeError("缺少飞书数据表标识")
        written = 0
        for start in range(0, len(fields), 1000):
            batch = [{"fields": item} for item in fields[start : start + 1000]]
            response = httpx.post(
                "https://open.feishu.cn/open-apis/bitable/v1/apps/"
                f"{self.settings.feishu_bitable_app_token}/tables/{table_id}/records/batch_create",
                headers={"Authorization": f"Bearer {self._tenant_token()}"},
                json={"records": batch},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise RuntimeError(f"飞书记录写入失败：{payload.get('msg', '接口返回错误')}")
            written += len(batch)
        return written
