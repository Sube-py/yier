from __future__ import annotations

from pathlib import Path
from typing import Any

from yier_channel.storage.files import read_json, read_text, write_json, write_text


class WeixinStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.accounts_dir = self.root / "weixin" / "accounts"
        self.sync_buf_dir = self.root / "weixin" / "sync_buf"
        self.monitor_dir = self.root / "monitor"
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        self.sync_buf_dir.mkdir(parents=True, exist_ok=True)
        self.monitor_dir.mkdir(parents=True, exist_ok=True)

    def list_accounts(self) -> list[str]:
        return sorted(path.stem for path in self.accounts_dir.glob("*.json"))

    def load_account(self, account_id: str) -> dict[str, Any]:
        payload = read_json(self.accounts_dir / f"{account_id}.json", {})
        return payload if isinstance(payload, dict) else {}

    def save_account(self, account_id: str, payload: dict[str, Any]) -> None:
        write_json(self.accounts_dir / f"{account_id}.json", payload)

    def load_sync_buf(self, account_id: str) -> str:
        return read_text(self.sync_buf_dir / f"{account_id}.txt")

    def save_sync_buf(self, account_id: str, value: str) -> None:
        write_text(self.sync_buf_dir / f"{account_id}.txt", value)
