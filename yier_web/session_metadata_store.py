from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SessionMetadataStore:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def load(self, session_id: str) -> dict[str, Any] | None:
        path = self._session_file(session_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def save(self, session_id: str, payload: dict[str, Any]) -> None:
        path = self._session_file(session_id)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete(self, session_id: str) -> bool:
        path = self._session_file(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _session_file(self, session_id: str) -> Path:
        safe_session_id = session_id.replace("/", "_")
        return self.base_path / f"{safe_session_id}.json"
