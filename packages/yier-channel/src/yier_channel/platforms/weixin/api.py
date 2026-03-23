from __future__ import annotations

import base64
import secrets
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000


class WeixinAPI:
    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    async def start_qr_login(self, bot_type: str = "3") -> dict[str, Any]:
        url = f"{self.base_url}/ilink/bot/get_bot_qrcode"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params={"bot_type": bot_type})
            response.raise_for_status()
        return response.json()

    async def get_qr_status(self, qrcode: str) -> dict[str, Any]:
        url = f"{self.base_url}/ilink/bot/get_qrcode_status"
        async with httpx.AsyncClient(timeout=40.0) as client:
            response = await client.get(
                url,
                params={"qrcode": qrcode},
                headers={"iLink-App-ClientVersion": "1"},
            )
            response.raise_for_status()
        return response.json()

    async def get_updates(
        self,
        token: str,
        get_updates_buf: str,
        timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS,
    ) -> dict[str, Any]:
        payload = {"get_updates_buf": get_updates_buf, "base_info": {"channel_version": "0.1.0"}}
        try:
            return await self._post(
                "ilink/bot/getupdates",
                payload,
                token=token,
                timeout_ms=timeout_ms,
            )
        except httpx.TimeoutException:
            return {"ret": 0, "msgs": [], "get_updates_buf": get_updates_buf}

    async def send_message(
        self,
        token: str,
        to_user_id: str,
        text: str,
        context_token: str,
    ) -> dict[str, Any]:
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": secrets.token_hex(8),
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [
                    {
                        "type": 1,
                        "text_item": {"text": text},
                    }
                ],
            },
            "base_info": {"channel_version": "0.1.0"},
        }
        return await self._post("ilink/bot/sendmessage", payload, token=token, timeout_ms=15_000)

    async def get_config(self, token: str, ilink_user_id: str, context_token: str | None) -> dict[str, Any]:
        payload = {
            "ilink_user_id": ilink_user_id,
            "context_token": context_token,
            "base_info": {"channel_version": "0.1.0"},
        }
        return await self._post("ilink/bot/getconfig", payload, token=token, timeout_ms=10_000)

    async def send_typing(self, token: str, ilink_user_id: str, typing_ticket: str, status: int) -> dict[str, Any]:
        payload = {
            "ilink_user_id": ilink_user_id,
            "typing_ticket": typing_ticket,
            "status": status,
            "base_info": {"channel_version": "0.1.0"},
        }
        return await self._post("ilink/bot/sendtyping", payload, token=token, timeout_ms=10_000)

    async def _post(self, path: str, payload: dict[str, Any], token: str, timeout_ms: int) -> dict[str, Any]:
        timeout = httpx.Timeout(timeout_ms / 1000)
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token}",
            "X-WECHAT-UIN": base64.b64encode(str(secrets.randbelow(2**32)).encode("utf-8")).decode("utf-8"),
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{self.base_url}/{path}", json=payload, headers=headers)
            response.raise_for_status()
        return response.json()
