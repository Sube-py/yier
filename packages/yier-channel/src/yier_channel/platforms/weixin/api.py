from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any

import httpx

from yier_channel.platforms.weixin.cdn import aes_ecb_padded_size


DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
UPLOAD_MEDIA_IMAGE = 1
UPLOAD_MEDIA_VIDEO = 2
UPLOAD_MEDIA_FILE = 3


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

    async def send_message_items(
        self,
        token: str,
        to_user_id: str,
        context_token: str,
        item_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": secrets.token_hex(8),
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": item_list,
            },
            "base_info": {"channel_version": "0.1.0"},
        }
        return await self._post("ilink/bot/sendmessage", payload, token=token, timeout_ms=15_000)

    async def get_upload_url(
        self,
        token: str,
        *,
        filekey: str,
        media_type: int,
        to_user_id: str,
        plaintext: bytes,
        aes_key_hex: str,
    ) -> dict[str, Any]:
        payload = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": len(plaintext),
            "rawfilemd5": hashlib.md5(plaintext).hexdigest(),
            "filesize": aes_ecb_padded_size(len(plaintext)),
            "no_need_thumb": True,
            "aeskey": aes_key_hex,
            "base_info": {"channel_version": "0.1.0"},
        }
        return await self._post("ilink/bot/getuploadurl", payload, token=token, timeout_ms=15_000)

    async def send_image_message(
        self,
        token: str,
        to_user_id: str,
        context_token: str,
        *,
        encrypted_query_param: str,
        aes_key_hex: str,
        ciphertext_size: int,
        text: str = "",
    ) -> dict[str, Any]:
        item_list: list[dict[str, Any]] = []
        if text:
            item_list.append({"type": 1, "text_item": {"text": text}})
        item_list.append(
            {
                "type": 2,
                "image_item": {
                    "media": {
                        "encrypt_query_param": encrypted_query_param,
                        "aes_key": base64.b64encode(bytes.fromhex(aes_key_hex)).decode("utf-8"),
                        "encrypt_type": 1,
                    },
                    "mid_size": ciphertext_size,
                },
            }
        )
        return await self.send_message_items(
            token=token,
            to_user_id=to_user_id,
            context_token=context_token,
            item_list=item_list,
        )

    async def send_video_message(
        self,
        token: str,
        to_user_id: str,
        context_token: str,
        *,
        encrypted_query_param: str,
        aes_key_hex: str,
        ciphertext_size: int,
        text: str = "",
    ) -> dict[str, Any]:
        item_list: list[dict[str, Any]] = []
        if text:
            item_list.append({"type": 1, "text_item": {"text": text}})
        item_list.append(
            {
                "type": 5,
                "video_item": {
                    "media": {
                        "encrypt_query_param": encrypted_query_param,
                        "aes_key": base64.b64encode(bytes.fromhex(aes_key_hex)).decode("utf-8"),
                        "encrypt_type": 1,
                    },
                    "video_size": ciphertext_size,
                },
            }
        )
        return await self.send_message_items(
            token=token,
            to_user_id=to_user_id,
            context_token=context_token,
            item_list=item_list,
        )

    async def send_file_message(
        self,
        token: str,
        to_user_id: str,
        context_token: str,
        *,
        encrypted_query_param: str,
        aes_key_hex: str,
        plaintext_size: int,
        file_name: str,
        text: str = "",
    ) -> dict[str, Any]:
        item_list: list[dict[str, Any]] = []
        if text:
            item_list.append({"type": 1, "text_item": {"text": text}})
        item_list.append(
            {
                "type": 4,
                "file_item": {
                    "media": {
                        "encrypt_query_param": encrypted_query_param,
                        "aes_key": base64.b64encode(bytes.fromhex(aes_key_hex)).decode("utf-8"),
                        "encrypt_type": 1,
                    },
                    "file_name": file_name,
                    "len": str(plaintext_size),
                },
            }
        )
        return await self.send_message_items(
            token=token,
            to_user_id=to_user_id,
            context_token=context_token,
            item_list=item_list,
        )

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
