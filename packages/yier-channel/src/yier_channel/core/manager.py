from __future__ import annotations

from pathlib import Path
from typing import Any

from yier_channel.core.adapters import EventSink, MessageSink, PlatformAdapter
from yier_channel.core.models import ChannelAccountSummary, ChannelWorkspaceSnapshot


class ChannelManager:
    def __init__(
        self,
        message_sink: MessageSink | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self._platforms: dict[str, PlatformAdapter] = {}
        self._message_sink = message_sink
        self._event_sink = event_sink
        self._started = False

    def register_platform(self, adapter: PlatformAdapter) -> None:
        adapter.configure_sinks(self._message_sink, self._event_sink)
        self._platforms[adapter.platform] = adapter

    def get_platform(self, name: str) -> PlatformAdapter | None:
        return self._platforms.get(name)

    async def start(self) -> None:
        if self._started:
            return
        for adapter in self._platforms.values():
            await adapter.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        for adapter in self._platforms.values():
            await adapter.stop()
        self._started = False

    async def get_accounts(self) -> list[ChannelAccountSummary]:
        accounts: list[ChannelAccountSummary] = []
        for adapter in self._platforms.values():
            accounts.extend(await adapter.get_accounts())
        return sorted(accounts, key=lambda item: (item.platform, item.account_id))

    async def get_snapshot(self) -> ChannelWorkspaceSnapshot:
        platforms = []
        for adapter in self._platforms.values():
            platforms.append(await adapter.get_platform_summary())
        return ChannelWorkspaceSnapshot(platforms=platforms, accounts=await self.get_accounts())

    async def login(self, platform: str, account_id: str | None = None) -> dict[str, Any]:
        adapter = self._require_platform(platform)
        return await adapter.login(account_id=account_id)

    async def start_account(self, platform: str, account_id: str) -> ChannelAccountSummary:
        adapter = self._require_platform(platform)
        return await adapter.start_account(account_id)

    async def stop_account(self, platform: str, account_id: str) -> ChannelAccountSummary:
        adapter = self._require_platform(platform)
        return await adapter.stop_account(account_id)

    async def send_text(self, platform: str, account_id: str, peer_id: str, text: str) -> dict[str, Any]:
        adapter = self._require_platform(platform)
        return await adapter.send_text(account_id=account_id, peer_id=peer_id, text=text)

    async def send_file(
        self,
        platform: str,
        account_id: str,
        peer_id: str,
        file_path: Path,
        text: str = "",
    ) -> dict[str, Any]:
        adapter = self._require_platform(platform)
        return await adapter.send_file(
            account_id=account_id,
            peer_id=peer_id,
            file_path=file_path,
            text=text,
        )

    def _require_platform(self, name: str) -> PlatformAdapter:
        adapter = self.get_platform(name)
        if adapter is None:
            raise ValueError(f"Unknown channel platform: {name}")
        return adapter
