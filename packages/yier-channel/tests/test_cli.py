from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from yier_channel import cli


def test_run_cli_send_file_uses_workspace_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    sent: dict[str, object] = {}
    file_path = tmp_path / "hello.txt"
    file_path.write_text("hello", encoding="utf-8")

    class FakeService:
        def __init__(self, storage_root: Path | None = None) -> None:
            sent["storage_root"] = storage_root

        async def send_file(
            self,
            platform: str,
            account_id: str,
            peer_id: str,
            file_path: Path,
            text: str = "",
        ) -> dict[str, str]:
            sent["call"] = {
                "platform": platform,
                "account_id": account_id,
                "peer_id": peer_id,
                "file_path": file_path,
                "text": text,
            }
            return {"message_id": "mid-1", "media_kind": "file", "file_name": file_path.name}

    monkeypatch.setattr(cli, "ChannelWorkspaceService", FakeService)

    args = cli.build_parser().parse_args(
        [
            "send",
            "--platform",
            "weixin",
            "--account-id",
            "wx-a",
            "--to",
            "peer-1",
            "--file",
            str(file_path),
            "--text",
            "caption",
        ]
    )

    exit_code = asyncio.run(cli.run_cli(args))

    assert exit_code == 0
    assert sent["storage_root"] is None
    assert sent["call"] == {
        "platform": "weixin",
        "account_id": "wx-a",
        "peer_id": "peer-1",
        "file_path": file_path.resolve(),
        "text": "caption",
    }
    assert json.loads(capsys.readouterr().out) == {
        "message_id": "mid-1",
        "media_kind": "file",
        "file_name": "hello.txt",
    }


def test_run_cli_send_requires_text_or_file() -> None:
    args = cli.build_parser().parse_args(
        [
            "send",
            "--platform",
            "weixin",
            "--account-id",
            "wx-a",
            "--to",
            "peer-1",
        ]
    )

    with pytest.raises(SystemExit, match="Either --text or --file must be provided."):
        asyncio.run(cli.run_cli(args))
