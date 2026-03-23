from __future__ import annotations

from pathlib import Path
from typing import Any

from yier_channel.core.manager import ChannelManager
from yier_channel.core.models import ChannelAccountSummary, ChannelConfig, ChannelWorkspaceSnapshot
from yier_channel.core.registry import PlatformSpec, list_platforms, register_platform
from yier_channel.platforms.weixin.adapter import WeixinAdapter
from yier_channel.storage.files import read_json, write_json


class ChannelWorkspaceService:
    def __init__(
        self,
        storage_root: Path | None = None,
        manager: ChannelManager | None = None,
    ) -> None:
        self.storage_root = (storage_root or Path.home() / ".yier" / "channels").resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.config_path = self.storage_root / "config.json"
        self.manager = manager or ChannelManager()
        register_platform(PlatformSpec(name="weixin", label="Weixin", implemented=True))
        self.manager.register_platform(WeixinAdapter(self.storage_root))

    async def start(self) -> None:
        await self.manager.start()

    async def stop(self) -> None:
        await self.manager.stop()

    def load_config(self) -> ChannelConfig:
        payload = read_json(self.config_path, {})
        return ChannelConfig.model_validate(payload if isinstance(payload, dict) else {})

    def save_config(self, payload: dict[str, Any]) -> ChannelConfig:
        config = ChannelConfig.model_validate(payload)
        write_json(self.config_path, config.model_dump())
        return config

    async def get_workspace_snapshot(self) -> ChannelWorkspaceSnapshot:
        return await self.manager.get_snapshot()

    async def get_accounts(self) -> list[ChannelAccountSummary]:
        return await self.manager.get_accounts()

    async def login(self, platform: str, account_id: str | None = None) -> dict[str, Any]:
        return await self.manager.login(platform=platform, account_id=account_id)

    async def start_account(self, platform: str, account_id: str) -> ChannelAccountSummary:
        return await self.manager.start_account(platform=platform, account_id=account_id)

    async def stop_account(self, platform: str, account_id: str) -> ChannelAccountSummary:
        return await self.manager.stop_account(platform=platform, account_id=account_id)

    async def send_text(self, platform: str, account_id: str, peer_id: str, text: str) -> dict[str, Any]:
        return await self.manager.send_text(
            platform=platform,
            account_id=account_id,
            peer_id=peer_id,
            text=text,
        )

    def get_registered_platforms(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "label": spec.label,
                "implemented": spec.implemented,
            }
            for spec in list_platforms()
        ]
