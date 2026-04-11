from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codex_app_server import (
    AppServerConfig,
    AsyncCodex,
    AsyncThread,
    ThreadSortKey,
    ThreadSourceKind,
)
from codex_app_server.generated.v2_all import (
    CustomSessionSource,
    OtherSubAgentSource,
    SubAgentSessionSource,
    Thread as ThreadV2,
    ThreadReadResponse,
    ThreadSpawnSubAgentSource,
)

from yier_web.codex.sdk.config import DEFAULT_CODEX_LAUNCHER, build_app_server_config
from yier_web.schemas import (
    CodexNativeSessionSummary,
    CodexPairingExtensionSummary,
    CodexProjectGroup,
    CodexWorkspaceResponse,
)

if TYPE_CHECKING:
    from yier_web.config import AppConfigService

INTERACTIVE_THREAD_SOURCE_KINDS = [
    ThreadSourceKind.cli,
    ThreadSourceKind.vscode,
    ThreadSourceKind.exec,
    ThreadSourceKind.app_server,
]


@dataclass(slots=True)
class _IndexEntry:
    thread_name: str | None
    updated_at: str | None


class CodexWorkspaceService:
    def __init__(
        self,
        home_dir: Path,
        config_service: "AppConfigService | None" = None,
    ) -> None:
        self.home_dir = home_dir.resolve()
        self.config_service = config_service
        self.codex_home = self.home_dir / ".codex"
        self.index_path = self.codex_home / "session_index.jsonl"
        self.sessions_dir = self.codex_home / "sessions"
        self.app_pairing_extensions_dir = (
            self.home_dir
            / "Library"
            / "Application Support"
            / "com.openai.chat"
            / "app_pairing_extensions"
        )
        self._codex: AsyncCodex | None = None
        self._codex_config: AppServerConfig | None = None
        self._codex_lock = asyncio.Lock()

    async def stop(self) -> None:
        await self._close_shared_codex()

    async def load_workspace(self) -> CodexWorkspaceResponse:
        sessions = await self.list_active_sessions()
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
        return CodexWorkspaceResponse(
            projects=project_groups,
            paired_editors=self.list_paired_editors(),
        )

    async def get_active_session(
        self, thread_id: str
    ) -> CodexNativeSessionSummary | None:
        normalized_thread_id = thread_id.strip()
        if not normalized_thread_id:
            return None
        for session in await self.list_active_sessions():
            if session.thread_id == normalized_thread_id:
                return session
        return None

    async def read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool = True,
    ) -> ThreadReadResponse | None:
        normalized_thread_id = thread_id.strip()
        if not normalized_thread_id:
            return None

        config = self._sdk_config()
        if config is None:
            return None

        codex = await self._shared_codex(config)
        if codex is None:
            return None
        try:
            return await AsyncThread(codex, normalized_thread_id).read(
                include_turns=include_turns
            )
        except Exception:
            await self._invalidate_shared_codex(codex)
            return None

    async def list_active_sessions(self) -> list[CodexNativeSessionSummary]:
        sdk_sessions = await self._list_active_sessions_from_sdk()
        if sdk_sessions is not None:
            return sdk_sessions
        return self._list_active_sessions_from_disk()

    def list_paired_editors(self) -> list[CodexPairingExtensionSummary]:
        if not self.app_pairing_extensions_dir.exists():
            return []

        editors: list[CodexPairingExtensionSummary] = []
        for descriptor_file in sorted(self.app_pairing_extensions_dir.iterdir()):
            if not descriptor_file.is_file():
                continue
            editor = self._extract_paired_editor(descriptor_file)
            if editor is not None:
                editors.append(editor)

        editors.sort(
            key=lambda item: (
                item.last_seen_at,
                item.workspace_name.lower(),
                item.id.lower(),
            ),
            reverse=True,
        )
        return editors

    def paired_editors_signature(
        self,
        editors: list[CodexPairingExtensionSummary] | None = None,
    ) -> str:
        current_editors = editors if editors is not None else self.list_paired_editors()
        return json.dumps(
            [editor.model_dump(mode="json") for editor in current_editors],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def _list_active_sessions_from_sdk(
        self,
    ) -> list[CodexNativeSessionSummary] | None:
        config = self._sdk_config()
        if config is None:
            return None

        codex = await self._shared_codex(config)
        if codex is None:
            return None
        try:
            sessions: dict[str, CodexNativeSessionSummary] = {}
            cursor: str | None = None
            while True:
                response = await codex.thread_list(
                    archived=False,
                    cursor=cursor,
                    limit=100,
                    sort_key=ThreadSortKey.updated_at,
                    source_kinds=INTERACTIVE_THREAD_SOURCE_KINDS,
                )
                for thread in response.data:
                    session = self._extract_sdk_session(thread)
                    if session is None:
                        continue
                    sessions[session.thread_id] = session

                cursor = response.next_cursor
                if not cursor:
                    break
        except Exception:
            await self._invalidate_shared_codex(codex)
            return None

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

    async def _shared_codex(self, config: AppServerConfig) -> AsyncCodex | None:
        stale_codex: AsyncCodex | None = None
        async with self._codex_lock:
            if self._codex is not None and self._codex_config == config:
                return self._codex

            stale_codex = self._codex
            self._codex = None
            self._codex_config = None

            codex = AsyncCodex(config=config)
            try:
                await codex.__aenter__()
            except Exception:
                await codex.close()
                raise

            self._codex = codex
            self._codex_config = config

        if stale_codex is not None:
            await stale_codex.close()
        return self._codex

    async def _invalidate_shared_codex(self, codex: AsyncCodex) -> None:
        async with self._codex_lock:
            if self._codex is not codex:
                return
            self._codex = None
            self._codex_config = None
        await codex.close()

    async def _close_shared_codex(self) -> None:
        async with self._codex_lock:
            codex = self._codex
            self._codex = None
            self._codex_config = None
        if codex is not None:
            await codex.close()

    def _extract_paired_editor(
        self, descriptor_file: Path
    ) -> CodexPairingExtensionSummary | None:
        try:
            payload = json.loads(descriptor_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        socket_path_value = payload.get("socketPath")
        if not isinstance(socket_path_value, str) or not socket_path_value.strip():
            return None

        socket_path = Path(socket_path_value)
        try:
            is_online = socket_path.exists() and socket_path.is_socket()
        except OSError:
            is_online = False

        if not is_online:
            return None

        capabilities_payload = payload.get("capabilities")
        if isinstance(capabilities_payload, dict):
            capability_names = [
                str(name) for name, enabled in capabilities_payload.items() if enabled
            ]
        else:
            capability_names = []
        capability_names.sort()

        timestamp = payload.get("timestamp")
        if isinstance(timestamp, (int, float)):
            last_seen_at = float(timestamp)
        else:
            last_seen_at = descriptor_file.stat().st_mtime * 1000

        descriptor_id = payload.get("id")
        if not isinstance(descriptor_id, str) or not descriptor_id.strip():
            descriptor_id = descriptor_file.name

        app_name = payload.get("appName")
        workspace_name = payload.get("workspaceName")
        extension_name = payload.get("extensionName")
        extension_version = payload.get("extensionVersion")
        bundle_id = payload.get("bundleID")
        marketplace_id = payload.get("marketplaceID")

        return CodexPairingExtensionSummary(
            id=descriptor_id,
            app_name=app_name.strip()
            if isinstance(app_name, str)
            else "Unknown editor",
            workspace_name=workspace_name.strip()
            if isinstance(workspace_name, str)
            else "",
            extension_name=extension_name.strip()
            if isinstance(extension_name, str)
            else "",
            extension_version=extension_version.strip()
            if isinstance(extension_version, str)
            else "",
            bundle_id=bundle_id.strip() if isinstance(bundle_id, str) else "",
            marketplace_id=marketplace_id.strip()
            if isinstance(marketplace_id, str)
            else "",
            capability_names=capability_names,
            capability_count=len(capability_names),
            socket_path=str(socket_path),
            is_online=is_online,
            needs_reload=bool(payload.get("needsReload")),
            last_seen_at=last_seen_at,
        )

    def _sdk_config(self) -> AppServerConfig | None:
        launcher_command = DEFAULT_CODEX_LAUNCHER
        client_cwd: str | None = None
        if self.config_service is not None:
            settings = self.config_service.load_web_settings().codex
            launcher_command = settings.launcher_command or DEFAULT_CODEX_LAUNCHER
            client_cwd = str(self.config_service.project_root)
        if client_cwd is None:
            client_cwd = str(self.home_dir)
        try:
            return build_app_server_config(
                launcher_command=launcher_command,
                cwd=client_cwd,
                client_name="yier_web_workspace",
                client_title="Yier Web Workspace",
            )
        except (RuntimeError, ValueError):
            return None

    def _extract_sdk_session(
        self, thread: ThreadV2
    ) -> CodexNativeSessionSummary | None:
        thread_id = thread.id.strip()
        if not thread_id:
            return None

        if thread.ephemeral:
            return None

        cwd = thread.cwd
        name = self._compact_text(thread.name)
        preview = self._compact_text(thread.preview)
        if preview is None:
            preview = name or thread_id
        title = name or preview or thread_id
        project, project_path = self._derive_project_root(cwd)

        return CodexNativeSessionSummary(
            thread_id=thread_id,
            title=title,
            preview=preview,
            updated_at=float(thread.updated_at),
            started_at=float(thread.created_at),
            status=self._thread_status(thread),
            cwd=cwd,
            project=project,
            project_path=project_path,
            source=self._thread_source(thread),
        )

    def _thread_source(self, thread: ThreadV2) -> str:
        source_root = thread.source.root
        if isinstance(source_root, CustomSessionSource):
            return source_root.custom
        if isinstance(source_root, SubAgentSessionSource):
            sub_agent_source = source_root.sub_agent.root
            if isinstance(sub_agent_source, OtherSubAgentSource):
                return sub_agent_source.other
            if isinstance(sub_agent_source, ThreadSpawnSubAgentSource):
                return "threadSpawn"
            return sub_agent_source.value
        return source_root.value

    def _thread_status(self, thread: ThreadV2) -> str:
        return thread.status.root.type

    def _list_active_sessions_from_disk(self) -> list[CodexNativeSessionSummary]:
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
                first_user_message = (
                    self._extract_first_user_message(row_type, payload)
                    or first_user_message
                )

        if not thread_id:
            return None

        index_entry = index.get(thread_id)
        indexed_updated_at = self._parse_timestamp(
            index_entry.updated_at if index_entry else None
        )
        if indexed_updated_at is not None:
            updated_at = indexed_updated_at

        project, project_path = self._derive_project_root(cwd)
        title = (
            self._compact_text(index_entry.thread_name if index_entry else None)
            or first_user_message
            or thread_id
        )
        preview = first_user_message or title

        return CodexNativeSessionSummary(
            thread_id=thread_id,
            title=title,
            preview=preview,
            updated_at=updated_at,
            started_at=started_at,
            status="idle",
            cwd=cwd,
            project=project,
            project_path=project_path,
        )

    def _extract_first_user_message(self, row_type: Any, payload: Any) -> str | None:
        if (
            row_type == "event_msg"
            and isinstance(payload, dict)
            and payload.get("type") == "user_message"
        ):
            message = payload.get("message")
            if isinstance(message, str):
                return self._normalize_user_message(message)

        if row_type == "response_item" and isinstance(payload, dict):
            if payload.get("type") == "message" and payload.get("role") == "user":
                return self._normalize_user_message(
                    self._parse_text_content(payload.get("content"))
                )
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
