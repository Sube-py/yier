from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SessionUIStore:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def load_activity_events(self, session_id: str) -> list[dict[str, Any]]:
        session_file = self._session_file(session_id)
        if not session_file.exists():
            return []

        payload = json.loads(session_file.read_text(encoding="utf-8"))
        activity_events = payload.get("activity_events", [])
        if not isinstance(activity_events, list):
            return []
        return [item for item in activity_events if isinstance(item, dict)]

    def load_activity_page(
        self,
        session_id: str,
        *,
        before: int | None = None,
        limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int | None]]:
        activity_events = self.load_activity_events(session_id)
        total_count = len(activity_events)
        normalized_before = total_count if before is None else max(0, min(before, total_count))

        if limit is None or limit <= 0:
            page = activity_events[:normalized_before]
            returned_count = len(page)
            return (
                page,
                {
                    "total_count": total_count,
                    "returned_count": returned_count,
                    "next_before": None,
                },
            )

        start_index = max(0, normalized_before - limit)
        page = activity_events[start_index:normalized_before]
        returned_count = len(page)
        next_before = start_index if start_index > 0 else None
        return (
            page,
            {
                "total_count": total_count,
                "returned_count": returned_count,
                "next_before": next_before,
            },
        )

    def append_activity_event(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        activity_events = self.load_activity_events(session_id)
        activity_events.append(
            {
                "event": event,
                "data": data,
            }
        )
        payload = {
            "session_id": session_id,
            "activity_events": activity_events,
        }
        self._session_file(session_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _session_file(self, session_id: str) -> Path:
        safe_session_id = session_id.replace("/", "_")
        return self.base_path / f"{safe_session_id}.json"
