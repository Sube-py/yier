from __future__ import annotations

import asyncio
from pathlib import Path

from yier_channel.service import ChannelWorkspaceService


def test_workspace_service_send_file_delegates_to_manager(tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    service = ChannelWorkspaceService(storage_root=tmp_path)

    async def fake_send_file(
        *,
        platform: str,
        account_id: str,
        peer_id: str,
        file_path: Path,
        text: str = "",
    ) -> dict[str, str]:
        calls["payload"] = {
            "platform": platform,
            "account_id": account_id,
            "peer_id": peer_id,
            "file_path": file_path,
            "text": text,
        }
        return {"message_id": "mid-1"}

    service.manager.send_file = fake_send_file  # type: ignore[method-assign]

    file_path = tmp_path / "attachment.pdf"
    result = asyncio.run(
        service.send_file(
            platform="weixin",
            account_id="wx-a",
            peer_id="peer-1",
            file_path=file_path,
            text="memo",
        )
    )

    assert result == {"message_id": "mid-1"}
    assert calls["payload"] == {
        "platform": "weixin",
        "account_id": "wx-a",
        "peer_id": "peer-1",
        "file_path": file_path,
        "text": "memo",
    }
