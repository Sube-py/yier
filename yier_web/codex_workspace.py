from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from yier_web.schemas import CodexNativeSessionSummary, CodexProjectGroup, CodexWorkspaceResponse


@dataclass(slots=True)
class _IndexEntry:
    thread_name: str | None
    updated_at: str | None


class CodexWorkspaceService:
    def __init__(self, home_dir: Path) -> None:
        self.home_dir = home_dir.resolve()
        self.codex_home = self.home_dir / ".codex"
        self.index_path = self.codex_home / "session_index.jsonl"
        self.sessions_dir = self.codex_home / "sessions"

    def load_workspace(self) -> CodexWorkspaceResponse:
        sessions = self.list_active_sessions()
        projects: dict[str, list[CodexNativeSessionSummary]] = {}
        for session in sessions:
            projects.setdefault(session.project_path, []).append(session)

        project_groups: list[CodexProjectGroup] = []
        for project_path, project_sessions in projects.items():
            first = project_sessions[0]
            project_groups.append(
                CodexProjectGroup(
                    project=first.project,
                    project_path=project_path,
                    session_count=len(project_sessions),
                    sessions=project_sessions,
                )
            )

        project_groups.sort(
            key=lambda group: (
                group.sessions[0].updated_at if group.sessions else 0.0,
                group.project.lower(),
            ),
            reverse=True,
        )
        return CodexWorkspaceResponse(projects=project_groups)

    def get_active_session(self, thread_id: str) -> CodexNativeSessionSummary | None:
        normalized_thread_id = thread_id.strip()
        if not normalized_thread_id:
            return None
        for session in self.list_active_sessions():
            if session.thread_id == normalized_thread_id:
                return session
        return None

    def list_active_sessions(self) -> list[CodexNativeSessionSummary]:
        if not self.sessions_dir.exists():
            return []

        index = self._load_index()
        sessions: dict[str, CodexNativeSessionSummary] = {}
        for session_file in self.sessions_dir.rglob("*.jsonl"):
            session = self._extract_session(session_file, index)
            if session is None:
                continue
            sessions[session.thread_id] = session

        ordered = list(sessions.values())
        ordered.sort(
            key=lambda item: (
                item.updated_at,
                item.started_at,
                item.thread_id,
            ),
            reverse=True,
        )
        return ordered

    def _load_index(self) -> dict[str, _IndexEntry]:
        if not self.index_path.exists():
            return {}

        index: dict[str, _IndexEntry] = {}
        for raw_line in self.index_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            thread_id = row.get("id")
            if not isinstance(thread_id, str) or not thread_id.strip():
                continue
            thread_name = row.get("thread_name")
            updated_at = row.get("updated_at")
            index[thread_id] = _IndexEntry(
                thread_name=thread_name if isinstance(thread_name, str) else None,
                updated_at=updated_at if isinstance(updated_at, str) else None,
            )
        return index

    def _extract_session(
        self,
        session_file: Path,
        index: dict[str, _IndexEntry],
    ) -> CodexNativeSessionSummary | None:
        thread_id = ""
        started_at = 0.0
        updated_at = 0.0
        cwd = ""
        first_user_message = ""

        with session_file.open(encoding="utf-8") as handle:
            for raw_line in handle:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    row = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                timestamp = self._parse_timestamp(row.get("timestamp"))
                if timestamp is not None:
                    updated_at = max(updated_at, timestamp)

                row_type = row.get("type")
                payload = row.get("payload")
                if row_type == "session_meta" and isinstance(payload, dict):
                    payload_id = payload.get("id")
                    if isinstance(payload_id, str):
                        thread_id = payload_id.strip()
                    payload_timestamp = self._parse_timestamp(payload.get("timestamp"))
                    if payload_timestamp is not None:
                        started_at = payload_timestamp
                    payload_cwd = payload.get("cwd")
                    if isinstance(payload_cwd, str):
                        cwd = payload_cwd
                    continue

                if row_type == "turn_context" and isinstance(payload, dict) and not cwd:
                    payload_cwd = payload.get("cwd")
                    if isinstance(payload_cwd, str):
                        cwd = payload_cwd

                if first_user_message:
                    continue
                first_user_message = self._extract_first_user_message(row_type, payload) or first_user_message

        if not thread_id:
            return None

        index_entry = index.get(thread_id)
        indexed_updated_at = self._parse_timestamp(index_entry.updated_at if index_entry else None)
        if indexed_updated_at is not None:
            updated_at = indexed_updated_at

        project, project_path = self._derive_project_root(cwd)
        title = self._compact_text(index_entry.thread_name if index_entry else None) or first_user_message or thread_id
        preview = first_user_message or title

        return CodexNativeSessionSummary(
            thread_id=thread_id,
            title=title,
            preview=preview,
            updated_at=updated_at,
            started_at=started_at,
            cwd=cwd,
            project=project,
            project_path=project_path,
        )

    def _extract_first_user_message(self, row_type: Any, payload: Any) -> str | None:
        if row_type == "event_msg" and isinstance(payload, dict) and payload.get("type") == "user_message":
            message = payload.get("message")
            if isinstance(message, str):
                return self._normalize_user_message(message)

        if row_type == "response_item" and isinstance(payload, dict):
            if payload.get("type") == "message" and payload.get("role") == "user":
                return self._normalize_user_message(self._parse_text_content(payload.get("content")))
        return None

    def _parse_text_content(self, content: Any) -> str | None:
        if not isinstance(content, list):
            return None

        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"input_text", "output_text", "text"}:
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

        if not parts:
            return None
        return " ".join(parts)

    def _normalize_user_message(self, text: str | None) -> str | None:
        compacted = self._compact_text(text, limit=400)
        if compacted is None:
            return None

        markers = [
            "## My request for Codex:",
            "My request for Codex:",
            "## My request:",
            "My request:",
        ]
        for marker in markers:
            if marker in compacted:
                compacted = compacted.split(marker, 1)[1].strip()
                break

        if compacted.startswith("# AGENTS.md instructions for "):
            return None
        return self._compact_text(compacted)

    def _compact_text(self, text: str | None, limit: int = 72) -> str | None:
        if text is None:
            return None
        compacted = " ".join(text.split())
        if not compacted:
            return None
        if len(compacted) <= limit:
            return compacted
        return f"{compacted[: limit - 3]}..."

    def _parse_timestamp(self, raw_value: Any) -> float | None:
        if not isinstance(raw_value, str) or not raw_value.strip():
            return None
        normalized = raw_value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return parsed.timestamp()

    def _derive_project_root(self, cwd_text: str) -> tuple[str, str]:
        if not cwd_text:
            return "", ""

        cwd = Path(cwd_text).expanduser()
        if cwd.exists():
            try:
                result = subprocess.run(
                    ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError:
                result = None
            if result is not None and result.returncode == 0:
                project_root = Path(result.stdout.strip()).resolve()
                return project_root.name, str(project_root)

        return cwd.name, str(cwd)
