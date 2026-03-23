from __future__ import annotations

import asyncio
from pathlib import Path

from yier_channel import ChannelWorkspaceService, get_platform


def test_workspace_service_registers_weixin_platform(tmp_path: Path) -> None:
    service = ChannelWorkspaceService(storage_root=tmp_path)

    assert get_platform("weixin") is not None

    snapshot = asyncio.run(service.get_workspace_snapshot())

    assert snapshot.platforms[0].name == "weixin"


def test_workspace_service_can_save_config(tmp_path: Path) -> None:
    service = ChannelWorkspaceService(storage_root=tmp_path)

    saved = service.save_config({"enabled_platforms": ["weixin"], "weixin": {"mode": "test"}})

    assert saved.enabled_platforms == ["weixin"]
    assert service.load_config().weixin["mode"] == "test"
