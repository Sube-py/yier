from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from yier_channel.core.adapters import PlatformAdapter
from yier_channel.core.models import (
    ChannelAccountSummary,
    ChannelMessage,
    ChannelMeta,
    ChannelPlatformSummary,
)
from yier_channel.platforms.weixin.api import DEFAULT_BASE_URL, WeixinAPI
from yier_channel.storage.weixin import WeixinStorage


class WeixinAdapter(PlatformAdapter):
    def __init__(self, storage_root: Path, base_url: str = DEFAULT_BASE_URL) -> None:
        super().__init__()
        self.storage = WeixinStorage(storage_root)
        self.api = WeixinAPI(base_url=base_url)
        self._poll_tasks: dict[str, asyncio.Task[None]] = {}
        self._account_states: dict[str, ChannelAccountSummary] = {}
        self._context_tokens: dict[tuple[str, str], str] = {}
        self._login_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def platform(self) -> str:
        return "weixin"

    async def start(self) -> None:
        for account_id in self.storage.list_accounts():
            summary = self._load_summary(account_id)
            if summary.configured and summary.enabled:
                await self.start_account(account_id)

    async def stop(self) -> None:
        for account_id in list(self._poll_tasks):
            await self.stop_account(account_id)
        for task in self._login_tasks.values():
            task.cancel()
        self._login_tasks.clear()

    async def get_accounts(self) -> list[ChannelAccountSummary]:
        account_ids = set(self.storage.list_accounts()) | set(self._account_states)
        return [self._load_summary(account_id) for account_id in sorted(account_ids)]

    async def get_platform_summary(self) -> ChannelPlatformSummary:
        accounts = await self.get_accounts()
        return ChannelPlatformSummary(
            name=self.platform,
            label="Weixin",
            implemented=True,
            account_count=len(accounts),
            running_count=sum(1 for account in accounts if account.running),
        )

    async def login(self, account_id: str | None = None) -> dict[str, Any]:
        qr_payload = await self.api.start_qr_login()
        session_key = account_id or str(uuid4())
        qrcode = str(qr_payload.get("qrcode", ""))
        qrcode_url = str(qr_payload.get("qrcode_img_content", ""))
        summary = self._load_summary(session_key)
        summary.login_status = "waiting"
        self._set_summary(summary)
        await self.emit_event(
            "channel_login_qr",
            {
                "platform": self.platform,
                "account_id": session_key,
                "qrcode_url": qrcode_url,
                "status": "waiting",
            },
        )
        task = asyncio.create_task(self._watch_login(session_key, qrcode))
        self._login_tasks[session_key] = task
        return {
            "platform": self.platform,
            "account_id": session_key,
            "qrcode": qrcode,
            "qrcode_url": qrcode_url,
            "status": "waiting",
        }

    async def start_account(self, account_id: str) -> ChannelAccountSummary:
        summary = self._load_summary(account_id)
        if not summary.configured:
            raise ValueError(f"Weixin account {account_id} is not configured")
        if account_id in self._poll_tasks and not self._poll_tasks[account_id].done():
            return summary
        summary.running = True
        summary.last_error = None
        self._set_summary(summary)
        task = asyncio.create_task(self._poll_account(account_id))
        self._poll_tasks[account_id] = task
        await self._emit_state(summary)
        return summary

    async def stop_account(self, account_id: str) -> ChannelAccountSummary:
        task = self._poll_tasks.pop(account_id, None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        summary = self._load_summary(account_id)
        summary.running = False
        self._set_summary(summary)
        await self._emit_state(summary)
        return summary

    async def send_text(self, account_id: str, peer_id: str, text: str) -> dict[str, Any]:
        payload = self.storage.load_account(account_id)
        token = str(payload.get("token", ""))
        context_token = self._context_tokens.get((account_id, peer_id))
        if not token:
            raise ValueError(f"Weixin account {account_id} does not have a token")
        if not context_token:
            raise ValueError(f"Missing context token for {account_id}:{peer_id}")
        result = await self.api.send_message(
            token=token,
            to_user_id=peer_id,
            text=text,
            context_token=context_token,
        )
        summary = self._load_summary(account_id)
        summary.last_outbound_at = int(time.time() * 1000)
        summary.last_error = None
        self._set_summary(summary)
        message_id = str(result.get("message_id", uuid4()))
        await self.emit_event(
            "channel_outbound_message",
            {
                "platform": self.platform,
                "account_id": account_id,
                "peer_id": peer_id,
                "content": text,
                "message_id": message_id,
                "timestamp_ms": summary.last_outbound_at,
            },
        )
        await self._emit_state(summary)
        return {"message_id": message_id}

    async def _watch_login(self, account_id: str, qrcode: str) -> None:
        try:
            while True:
                status_payload = await self.api.get_qr_status(qrcode)
                status = str(status_payload.get("status", "wait"))
                if status == "confirmed":
                    bot_token = str(status_payload.get("bot_token", ""))
                    user_id = str(status_payload.get("ilink_user_id", ""))
                    bot_id = str(status_payload.get("ilink_bot_id", account_id))
                    normalized_account_id = bot_id.replace("@", "-").replace(".", "-") or account_id
                    payload = self.storage.load_account(normalized_account_id)
                    payload["token"] = bot_token
                    payload["user_id"] = user_id
                    payload["enabled"] = True
                    payload["base_url"] = self.api.base_url
                    self.storage.save_account(normalized_account_id, payload)
                    summary = self._load_summary(normalized_account_id)
                    summary.configured = True
                    summary.enabled = True
                    summary.login_status = "confirmed"
                    self._set_summary(summary)
                    await self.emit_event(
                        "channel_login_qr",
                        {
                            "platform": self.platform,
                            "account_id": normalized_account_id,
                            "status": "confirmed",
                        },
                    )
                    await self._emit_state(summary)
                    return

                summary = self._load_summary(account_id)
                summary.login_status = status
                self._set_summary(summary)
                await self.emit_event(
                    "channel_login_qr",
                    {
                        "platform": self.platform,
                        "account_id": account_id,
                        "status": status,
                    },
                )
                if status == "expired":
                    return
                await asyncio.sleep(2)
        except Exception as exc:
            summary = self._load_summary(account_id)
            summary.last_error = str(exc)
            self._set_summary(summary)
            await self.emit_event(
                "channel_error",
                {
                    "platform": self.platform,
                    "account_id": account_id,
                    "message": str(exc),
                },
            )
            await self._emit_state(summary)
        finally:
            self._login_tasks.pop(account_id, None)

    async def _poll_account(self, account_id: str) -> None:
        backoff_seconds = 2
        while True:
            try:
                payload = self.storage.load_account(account_id)
                token = str(payload.get("token", ""))
                if not token:
                    raise ValueError(f"Weixin account {account_id} does not have a token")
                get_updates_buf = self.storage.load_sync_buf(account_id)
                response = await self.api.get_updates(token=token, get_updates_buf=get_updates_buf)
                next_buf = response.get("get_updates_buf")
                if isinstance(next_buf, str) and next_buf:
                    self.storage.save_sync_buf(account_id, next_buf)
                for item in response.get("msgs", []) or []:
                    await self._handle_inbound_item(account_id, item)
                backoff_seconds = 2
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                summary = self._load_summary(account_id)
                summary.last_error = str(exc)
                summary.running = True
                self._set_summary(summary)
                await self.emit_event(
                    "channel_error",
                    {
                        "platform": self.platform,
                        "account_id": account_id,
                        "message": str(exc),
                    },
                )
                await self._emit_state(summary)
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 30)

    async def _handle_inbound_item(self, account_id: str, item: dict[str, Any]) -> None:
        peer_id = str(item.get("from_user_id", "")).strip()
        if not peer_id:
            return
        content = self._extract_text(item)
        if not content:
            return
        context_token = str(item.get("context_token", "")).strip()
        if context_token:
            self._context_tokens[(account_id, peer_id)] = context_token
        timestamp_ms = int(item.get("create_time_ms") or time.time() * 1000)
        session_id = f"channel:{self.platform}:{account_id}:{peer_id}"
        message = ChannelMessage(
            id=str(item.get("message_id") or uuid4()),
            session_id=session_id,
            content=content,
            direction="inbound",
            channel_meta=ChannelMeta(
                platform=self.platform,
                account_id=account_id,
                peer_id=peer_id,
            ),
            timestamp_ms=timestamp_ms,
            raw=item,
        )
        summary = self._load_summary(account_id)
        summary.last_inbound_at = timestamp_ms
        summary.running = True
        summary.last_error = None
        self._set_summary(summary)
        await self.emit_event(
            "channel_inbound_message",
            {
                "platform": self.platform,
                "account_id": account_id,
                "peer_id": peer_id,
                "session_id": session_id,
                "content": content,
                "timestamp_ms": timestamp_ms,
            },
        )
        await self._emit_state(summary)
        await self.emit_message(message)

    def _extract_text(self, item: dict[str, Any]) -> str:
        for entry in item.get("item_list", []) or []:
            if int(entry.get("type") or 0) == 1:
                text_item = entry.get("text_item") or {}
                text = str(text_item.get("text") or "").strip()
                if text:
                    return text
        return ""

    def _load_summary(self, account_id: str) -> ChannelAccountSummary:
        if account_id in self._account_states:
            return self._account_states[account_id].model_copy(deep=True)
        payload = self.storage.load_account(account_id)
        return ChannelAccountSummary(
            platform=self.platform,
            account_id=account_id,
            configured=bool(str(payload.get("token", "")).strip()),
            enabled=bool(payload.get("enabled", True)),
            running=False,
            name=str(payload.get("name", "")).strip() or None,
        )

    def _set_summary(self, summary: ChannelAccountSummary) -> None:
        self._account_states[summary.account_id] = summary.model_copy(deep=True)

    async def _emit_state(self, summary: ChannelAccountSummary) -> None:
        await self.emit_event(
            "channel_account_state",
            summary.model_dump(),
        )
