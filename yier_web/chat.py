from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
from time import time
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from yier_agents import (
    Agent,
    AgentEndEvent,
    BackgroundCommandManager,
    CompactionConfig,
    ErrorEvent,
    JSONSessionStore,
    LLM,
    LLMEndEvent,
    MCPManager,
    Message,
    MessageCompactEvent,
    SkillCatalog,
    Tool,
    ToolCallEndEvent,
    ToolCallStartEvent,
    create_list_background_commands_tool,
    create_list_files_tool,
    create_read_background_command_tool,
    create_read_file_tool,
    create_replace_in_file_tool,
    create_search_files_tool,
    create_send_background_command_input_tool,
    create_stop_background_command_tool,
    create_wait_background_command_tool,
    create_write_file_tool,
)
from yier_agents.src.config import AssistantSettings

from yier_web.agent_backends import ChatSessionContext, CodexAppServerBackend, YierAgentBackend
from yier_web.background_followups import FollowupQueueManager, create_queue_background_followup_tool
from yier_web.codex_workspace import CodexWorkspaceService
from yier_web.config import AppConfigService
from yier_web.codex_ipc import CodexThreadFollowerBridge
from yier_web.event_stream import EventStreamBroker
from yier_web.paired_editor_bridge import CodexPairedEditorBridge
from yier_web.session_metadata_store import SessionMetadataStore
from yier_web.session_ui_store import SessionUIStore
from yier_web.schemas import (
    BackendRuntimePayload,
    ChannelMetaPayload,
    CodexWorkspaceResponse,
    CodexWorkMode,
    PendingApproval,
    MCPRuntimeEntry,
    SessionSummary,
    StoredLLMSettings,
    StoredSessionMessage,
)
from yier_web.streaming_tools import (
    create_streaming_run_command_tool,
    create_streaming_start_background_command_tool,
)
from yier_web.tool_events import reset_tool_event_emitter, set_tool_event_emitter

StreamEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]
logger = logging.getLogger(__name__)


def _codex_ipc_debug_enabled() -> bool:
    return os.getenv("YIER_CODEX_IPC_DEBUG", "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def _codex_ipc_debug_log(message: str, **fields: Any) -> None:
    if not _codex_ipc_debug_enabled():
        return
    if fields:
        rendered = ", ".join(
            f"{key}={value!r}"
            for key, value in fields.items()
        )
        logger.warning(f"[chat-codex-ipc] {message} | {rendered}")
        return
    logger.warning(f"[chat-codex-ipc] {message}")


class ChatService:
    CODEX_PAIRING_MONITOR_INTERVAL_SECONDS = 1.0

    def __init__(
        self,
        project_root: Path,
        config_service: AppConfigService,
        mcp_manager: MCPManager | None = None,
        event_broker: EventStreamBroker | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config_service = config_service
        self.mcp_manager = mcp_manager or MCPManager(config_dir=self.config_service.yier_root)
        self.skill_catalog: SkillCatalog | None = None
        self.session_store = JSONSessionStore(self.config_service.sessions_path)
        self.transcript_store = JSONSessionStore(self.config_service.transcripts_path)
        self.session_ui_store = SessionUIStore(self.config_service.session_ui_path)
        self.session_metadata_store = SessionMetadataStore(self.config_service.session_meta_path)
        self.codex_workspace = CodexWorkspaceService(
            self.config_service.home_dir,
            config_service=self.config_service,
        )
        self.background_manager = BackgroundCommandManager(
            default_root=self.project_root,
            allow_shell=True,
            shell_program="/bin/bash",
        )
        self.followup_queue = FollowupQueueManager()
        self.event_broker = event_broker or EventStreamBroker()
        self.paired_editor_bridge = CodexPairedEditorBridge(
            home_dir=self.config_service.home_dir,
            event_broker=self.event_broker,
        )
        self.codex_ipc_bridge = CodexThreadFollowerBridge(chat_service=self)
        self.backends = {
            "yier": YierAgentBackend(self),
            "codex": CodexAppServerBackend(self),
        }
        self._agent: Agent | None = None
        self._agent_signature: tuple[Any, ...] | None = None
        self._lock = asyncio.Lock()
        self._session_run_locks: dict[str, asyncio.Lock] = {}
        self._background_owner_sessions: dict[str, str] = {}
        self._background_cursors: dict[str, dict[str, Any]] = {}
        self._ipc_stream_tasks: dict[str, asyncio.Task[None]] = {}
        self._background_supervisor_task: asyncio.Task[None] | None = None
        self._codex_pairing_monitor_task: asyncio.Task[None] | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.mcp_manager.start()
        self._started = True
        await self.paired_editor_bridge.start()
        await self.codex_ipc_bridge.start()
        await self.reload_agent(force_mcp_reconnect=False)
        self._background_supervisor_task = asyncio.create_task(self._background_supervisor_loop())
        self._codex_pairing_monitor_task = asyncio.create_task(self._codex_pairing_monitor_loop())

    async def stop(self) -> None:
        if not self._started:
            return
        if self._background_supervisor_task is not None:
            self._background_supervisor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._background_supervisor_task
            self._background_supervisor_task = None
        if self._codex_pairing_monitor_task is not None:
            self._codex_pairing_monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._codex_pairing_monitor_task
            self._codex_pairing_monitor_task = None
        for task in list(self._ipc_stream_tasks.values()):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._ipc_stream_tasks.clear()
        await self.codex_ipc_bridge.stop()
        await self.paired_editor_bridge.stop()
        await self.background_manager.close()
        await self.mcp_manager.stop()
        for backend in self.backends.values():
            await backend.stop()
        self._started = False
        self._agent = None
        self._agent_signature = None
        self._background_owner_sessions.clear()
        self._background_cursors.clear()

    async def reload_agent(self, force_mcp_reconnect: bool = False) -> None:
        async with self._lock:
            if self._started and force_mcp_reconnect:
                await self.mcp_manager.reload(force_reconnect=True)
            await self._rebuild_agent_locked()

    async def replace_mcp_servers(
        self,
        mcp_servers: dict[str, dict[str, Any]],
    ) -> dict[str, MCPRuntimeEntry]:
        self.config_service.save_mcp_servers(mcp_servers)
        if self._started:
            await self.mcp_manager.reload(force_reconnect=True)
        await self.reload_agent()
        return await self.get_mcp_status()

    async def reload_mcp(self) -> dict[str, MCPRuntimeEntry]:
        if self._started:
            await self.mcp_manager.reload(force_reconnect=True)
        await self.reload_agent()
        return await self.get_mcp_status()

    async def get_mcp_status(self) -> dict[str, MCPRuntimeEntry]:
        if self._started:
            await self.mcp_manager.reload_if_changed()
        snapshot = await self.mcp_manager.get_status()
        return {
            name: MCPRuntimeEntry(**payload)
            for name, payload in snapshot.items()
        }

    async def _codex_pairing_monitor_loop(self) -> None:
        last_signature = self.codex_workspace.paired_editors_signature()
        while True:
            await asyncio.sleep(self.CODEX_PAIRING_MONITOR_INTERVAL_SECONDS)
            try:
                paired_editors = self.codex_workspace.list_paired_editors()
                next_signature = self.codex_workspace.paired_editors_signature(paired_editors)
            except Exception:
                continue
            if next_signature == last_signature:
                continue
            last_signature = next_signature
            await self.event_broker.publish(
                "codex_pairings_updated",
                {
                    "paired_editors": [
                        editor.model_dump(mode="json")
                        for editor in paired_editors
                    ]
                },
            )

    def create_session(
        self,
        backend_id: str | None = None,
        project_path: str | None = None,
    ) -> str:
        settings = self.config_service.load_web_settings()
        resolved_backend_id = backend_id or settings.session_defaults.default_backend_id
        resolved_project_path = self.config_service.resolve_project_path(
            project_path or settings.session_defaults.default_project_path
        )
        if resolved_backend_id == "codex":
            backend = self.backends.get("codex")
            if isinstance(backend, CodexAppServerBackend):
                bootstrap = backend.bootstrap_session(Path(resolved_project_path))
                session_id = bootstrap["thread_id"]
                self.ensure_session_metadata(
                    session_id,
                    backend_id=resolved_backend_id,
                    project_path=resolved_project_path,
                    backend_state={
                        "thread_id": bootstrap["thread_id"],
                        "status": bootstrap["status"],
                        "active_flags": bootstrap["active_flags"],
                        "detail": bootstrap["detail"],
                    },
                    codex_work_mode="build",
                )
                return session_id

        session_id = str(uuid4())
        self.ensure_session_metadata(
            session_id,
            backend_id=resolved_backend_id,
            project_path=resolved_project_path,
            codex_work_mode="build" if resolved_backend_id == "codex" else None,
        )
        return session_id

    def get_session_messages(self, session_id: str) -> list[Message]:
        transcript_messages = self.transcript_store.get_session_messages(session_id)
        if transcript_messages:
            return transcript_messages
        return self.session_store.get_session_messages(session_id) or []

    def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        payload = self.session_metadata_store.load(session_id) or {}
        return self._normalize_session_metadata_payload(payload)

    def ensure_session_metadata(
        self,
        session_id: str,
        source: str = "chat",
        channel_meta: dict[str, Any] | None = None,
        backend_id: str | None = None,
        project_path: str | None = None,
        backend_state: dict[str, Any] | None = None,
        codex_work_mode: CodexWorkMode | None = None,
        title: str | None = None,
        preview: str | None = None,
        updated_at: float | None = None,
    ) -> None:
        existing = self.get_session_metadata(session_id)
        settings = self.config_service.load_web_settings()
        normalized_source = source if source in {"chat", "channel"} else existing["source"]
        default_backend_id = (
            settings.session_defaults.channel_backend_id
            if normalized_source == "channel"
            else settings.session_defaults.default_backend_id
        )
        default_project_path = (
            settings.session_defaults.channel_project_path
            if normalized_source == "channel"
            else settings.session_defaults.default_project_path
        )
        payload = {
            "session_id": session_id,
            "source": normalized_source,
            "backend_id": backend_id or existing["backend_id"] or default_backend_id,
            "project_path": self.config_service.resolve_project_path(
                project_path or existing["project_path"] or default_project_path
            ),
            "channel_meta": channel_meta if isinstance(channel_meta, dict) else existing.get("channel_meta"),
            "backend_state": (
                backend_state
                if isinstance(backend_state, dict)
                else existing.get("backend_state", {})
            ),
            "codex_work_mode": (
                codex_work_mode
                if codex_work_mode in {"plan", "build"}
                else existing.get("codex_work_mode")
            ),
            "title": title if isinstance(title, str) else existing.get("title"),
            "preview": preview if isinstance(preview, str) else existing.get("preview"),
            "updated_at": updated_at if isinstance(updated_at, (int, float)) else existing.get("updated_at"),
        }
        if payload["backend_id"] == "codex" and payload["codex_work_mode"] not in {"plan", "build"}:
            payload["codex_work_mode"] = "build"
        self.session_metadata_store.save(session_id, payload)

    async def update_paired_editor_state(
        self,
        *,
        session_id: str,
        content: str,
        selection_start: int,
        selection_end: int,
    ) -> None:
        await self.paired_editor_bridge.update_state(
            session_id=session_id,
            workspace_name=self._paired_editor_workspace_name(session_id),
            content=content,
            selection_start=selection_start,
            selection_end=selection_end,
        )

    def _paired_editor_workspace_name(self, session_id: str) -> str:
        normalized_session_id = session_id.strip()
        if not normalized_session_id:
            return "Yier"

        metadata = self.get_session_metadata(normalized_session_id)
        project_path_value = metadata.get("project_path")
        if isinstance(project_path_value, str) and project_path_value.strip():
            project_name = Path(project_path_value).name.strip()
            if project_name:
                return project_name

        title_value = metadata.get("title")
        if isinstance(title_value, str) and title_value.strip():
            return title_value.strip()

        return "Yier"

    def mark_channel_session(
        self,
        session_id: str,
        channel_meta: dict[str, Any],
    ) -> None:
        self.ensure_session_metadata(session_id, source="channel", channel_meta=channel_meta)

    def is_channel_session(self, session_id: str) -> bool:
        return self.get_session_metadata(session_id)["source"] == "channel"

    def update_session_backend_state(self, session_id: str, updates: dict[str, Any]) -> None:
        metadata = self.get_session_metadata(session_id)
        next_backend_state = {
            **metadata["backend_state"],
            **updates,
        }
        self.ensure_session_metadata(
            session_id,
            source=metadata["source"],
            channel_meta=metadata["channel_meta"],
            backend_id=metadata["backend_id"],
            project_path=metadata["project_path"],
            backend_state=next_backend_state,
            codex_work_mode=metadata["codex_work_mode"],
            title=metadata["title"],
            preview=metadata["preview"],
            updated_at=metadata["updated_at"],
        )

    def can_handle_codex_conversation(self, conversation_id: str) -> bool:
        normalized_conversation_id = conversation_id.strip()
        if not normalized_conversation_id:
            return False
        metadata = self.session_metadata_store.load(normalized_conversation_id)
        if isinstance(metadata, dict):
            normalized = self._normalize_session_metadata_payload(metadata)
            if normalized["backend_id"] == "codex":
                return True
        return self.codex_workspace.get_active_session(normalized_conversation_id) is not None

    def ensure_codex_conversation_session(self, conversation_id: str) -> str:
        normalized_conversation_id = conversation_id.strip()
        if not normalized_conversation_id:
            raise RuntimeError("Missing conversation id.")

        metadata = self.session_metadata_store.load(normalized_conversation_id)
        if isinstance(metadata, dict):
            normalized = self._normalize_session_metadata_payload(metadata)
            if normalized["backend_id"] != "codex":
                raise RuntimeError("Conversation is not backed by Codex.")
            return normalized_conversation_id

        session_id = self.open_codex_native_session(normalized_conversation_id)
        if session_id is None:
            raise RuntimeError(f"Codex conversation not found: {normalized_conversation_id}")
        return session_id

    def get_session_context(self, session_id: str) -> ChatSessionContext:
        metadata = self.get_session_metadata(session_id)
        return ChatSessionContext(
            session_id=session_id,
            source=metadata["source"],
            backend_id=metadata["backend_id"],
            project_path=Path(metadata["project_path"]).resolve(),
            channel_meta=metadata["channel_meta"],
            backend_state=metadata["backend_state"],
        )

    def _normalize_session_metadata_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self.config_service.load_web_settings()
        source = payload.get("source")
        if source not in {"chat", "channel"}:
            source = "chat"

        channel_meta = payload.get("channel_meta")
        if not isinstance(channel_meta, dict):
            channel_meta = None

        backend_id = payload.get("backend_id")
        if backend_id not in self.backends:
            backend_id = (
                settings.session_defaults.channel_backend_id
                if source == "channel"
                else settings.session_defaults.default_backend_id
            )

        default_project_path = (
            settings.session_defaults.channel_project_path
            if source == "channel"
            else settings.session_defaults.default_project_path
        )
        backend_state = payload.get("backend_state")
        if not isinstance(backend_state, dict):
            backend_state = {}

        return {
            "source": source,
            "backend_id": backend_id,
            "project_path": self.config_service.resolve_project_path(
                payload.get("project_path") or default_project_path
            ),
            "channel_meta": channel_meta,
            "backend_state": backend_state,
            "codex_work_mode": (
                payload.get("codex_work_mode")
                if payload.get("codex_work_mode") in {"plan", "build"}
                else ("build" if backend_id == "codex" else None)
            ),
            "title": payload.get("title") if isinstance(payload.get("title"), str) else "",
            "preview": payload.get("preview") if isinstance(payload.get("preview"), str) else "",
            "updated_at": float(payload["updated_at"]) if isinstance(payload.get("updated_at"), (int, float)) else 0.0,
        }

    def build_transcript_messages(self, session_id: str) -> list[StoredSessionMessage]:
        session_meta = self.get_session_metadata(session_id)
        channel_meta_payload = (
            ChannelMetaPayload.model_validate(session_meta["channel_meta"])
            if isinstance(session_meta["channel_meta"], dict)
            else None
        )
        return [
            StoredSessionMessage(
                role=message.role,
                content=message.content,
                reasoning_content=message.reasoning_content,
                tool_call_id=message.tool_call_id,
                source=session_meta["source"],
                channel_meta=channel_meta_payload,
            )
            for message in self.get_session_messages(session_id)
        ]

    def get_session_activity_events(self, session_id: str) -> list[dict[str, Any]]:
        return self.session_ui_store.load_activity_events(session_id)

    def get_codex_workspace(self) -> CodexWorkspaceResponse:
        return self.codex_workspace.load_workspace()

    def open_codex_native_session(self, thread_id: str) -> str | None:
        native_session = self.codex_workspace.get_active_session(thread_id)
        if native_session is None:
            return None

        session_id = native_session.thread_id
        self.ensure_session_metadata(
            session_id,
            backend_id="codex",
            project_path=native_session.cwd or native_session.project_path,
            backend_state={"thread_id": native_session.thread_id},
            codex_work_mode="build",
            title=native_session.title,
            preview=native_session.preview,
            updated_at=native_session.updated_at or time(),
        )
        return session_id

    def update_codex_session_mode(self, session_id: str, codex_work_mode: CodexWorkMode) -> bool:
        metadata = self.get_session_metadata(session_id)
        if metadata["backend_id"] != "codex":
            return False
        self.ensure_session_metadata(
            session_id,
            source=metadata["source"],
            channel_meta=metadata["channel_meta"],
            backend_id=metadata["backend_id"],
            project_path=metadata["project_path"],
            backend_state=metadata["backend_state"],
            codex_work_mode=codex_work_mode,
            title=metadata["title"],
            preview=metadata["preview"],
            updated_at=metadata["updated_at"],
        )
        return True

    def list_session_summaries(self, source: str | None = None) -> list[SessionSummary]:
        session_entries: dict[str, dict[str, Any]] = {}

        for directory in (
            self.config_service.transcripts_path,
            self.config_service.sessions_path,
            self.config_service.session_ui_path,
            self.config_service.session_meta_path,
        ):
            for session_file in directory.glob("*.json"):
                try:
                    payload = json.loads(session_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue

                session_id = payload.get("session_id")
                if not isinstance(session_id, str) or not session_id.strip():
                    continue

                entry = session_entries.setdefault(
                    session_id,
                    {
                        "updated_at": 0.0,
                        "messages": [],
                    },
                )
                entry["updated_at"] = max(entry["updated_at"], session_file.stat().st_mtime)
                if not entry["messages"] and isinstance(payload.get("messages"), list):
                    entry["messages"] = payload["messages"]

        summaries: list[SessionSummary] = []
        for session_id, entry in session_entries.items():
            session_meta = self.get_session_metadata(session_id)
            if source and session_meta["source"] != source:
                continue
            messages = entry["messages"] if isinstance(entry["messages"], list) else []
            title = session_meta["title"] or self._session_title(messages)
            preview = session_meta["preview"] or self._session_preview(messages)
            updated_at = max(float(entry["updated_at"]), float(session_meta["updated_at"] or 0.0))
            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    title=title,
                    preview=preview,
                    updated_at=updated_at,
                    message_count=self._message_count(messages),
                    source=session_meta["source"],
                    backend_id=session_meta["backend_id"],
                    project_path=session_meta["project_path"],
                    channel_meta=(
                        ChannelMetaPayload.model_validate(session_meta["channel_meta"])
                        if isinstance(session_meta["channel_meta"], dict)
                        else None
                    ),
                    codex_work_mode=session_meta["codex_work_mode"],
                )
            )

        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def load_session_view(
        self,
        session_id: str,
    ) -> tuple[list[StoredSessionMessage], list[dict[str, Any]]]:
        native_view = self._native_codex_session_view(session_id)
        if native_view is not None:
            return native_view
        return (
            self.build_transcript_messages(session_id),
            self.get_session_activity_events(session_id),
        )

    def _native_codex_session_view(
        self,
        session_id: str,
    ) -> tuple[list[StoredSessionMessage], list[dict[str, Any]]] | None:
        context = self.get_session_context(session_id)
        thread_id = context.backend_state.get("thread_id")
        if context.backend_id != "codex" or not isinstance(thread_id, str) or not thread_id:
            return None

        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            return None

        if backend.should_use_local_session_view(context):
            return (
                self.build_transcript_messages(session_id),
                self.get_session_activity_events(session_id),
            )

        cached_ipc_view = self._cached_codex_ipc_session_view(session_id)
        if cached_ipc_view is not None:
            return cached_ipc_view

        view = backend.load_thread_view(context)
        self.ensure_session_metadata(
            session_id,
            source=context.source,
            channel_meta=context.channel_meta,
            backend_id=context.backend_id,
            project_path=str(context.project_path),
            backend_state=context.backend_state,
            codex_work_mode=self.get_session_metadata(session_id)["codex_work_mode"],
            title=view["title"],
            preview=view["preview"],
            updated_at=view["updated_at"],
        )
        return (view["messages"], view["activity_events"])

    def _cached_codex_ipc_session_view(
        self,
        session_id: str,
    ) -> tuple[list[StoredSessionMessage], list[dict[str, Any]]] | None:
        context = self.get_session_context(session_id)
        if context.backend_id != "codex":
            return None

        backend_state = context.backend_state
        conversation_state = backend_state.get("ipc_conversation_state")
        if not isinstance(conversation_state, dict):
            return None

        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            return None

        view = backend.ipc_conversation_view(context, conversation_state)
        self.ensure_session_metadata(
            session_id,
            source=context.source,
            channel_meta=context.channel_meta,
            backend_id=context.backend_id,
            project_path=str(context.project_path),
            backend_state=context.backend_state,
            codex_work_mode=self.get_session_metadata(session_id)["codex_work_mode"],
            title=view["title"],
            preview=view["preview"],
            updated_at=view["updated_at"],
        )
        return (view["messages"], view["activity_events"])

    async def delete_session(self, session_id: str) -> bool:
        metadata = self.get_session_metadata(session_id)
        deleted = False
        deleted = self.session_store.clear_session(session_id) or deleted
        deleted = self.transcript_store.clear_session(session_id) or deleted

        session_ui_file = self.config_service.session_ui_path / f"{session_id.replace('/', '_')}.json"
        if session_ui_file.exists():
            session_ui_file.unlink()
            deleted = True
        deleted = self.session_metadata_store.delete(session_id) or deleted

        owned_background_ids = [
            background_session_id
            for background_session_id, owner_session_id in self._background_owner_sessions.items()
            if owner_session_id == session_id
        ]
        for background_session_id in owned_background_ids:
            self._background_owner_sessions.pop(background_session_id, None)
            self._background_cursors.pop(background_session_id, None)

        self._session_run_locks.pop(session_id, None)
        backend = self.backends.get(metadata["backend_id"])
        if backend is not None:
            await backend.close_session(session_id)
        return deleted

    async def stream_chat(self, session_id: str, user_message: str) -> AsyncIterator[dict[str, Any]]:
        event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        producer = asyncio.create_task(
            self._produce_stream_events(
                session_id=session_id,
                user_message=user_message,
                event_queue=event_queue,
            )
        )

        try:
            while True:
                item = await event_queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not producer.done():
                producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer

    async def _emit_stream_event(
        self,
        event: str,
        data: dict[str, Any],
        *,
        event_queue: asyncio.Queue[dict[str, Any] | None] | None = None,
        publish_to_event_broker: bool = False,
    ) -> None:
        self._handle_internal_event(event, data)
        self._persist_ui_event(event, data)
        await self.codex_ipc_bridge.notify_stream_event(event, data)
        if publish_to_event_broker:
            await self.event_broker.publish(event, data)
        if event_queue is not None:
            await event_queue.put({"event": event, "data": data})

    async def _produce_stream_events(
        self,
        session_id: str,
        user_message: str,
        event_queue: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        async def emit(event: str, data: dict[str, Any]) -> None:
            await self._emit_stream_event(
                event,
                data,
                event_queue=event_queue,
            )

        finish_reason = "stop"
        token = set_tool_event_emitter(emit)

        try:
            context = self.get_session_context(session_id)
            self.ensure_session_metadata(
                session_id,
                source=context.source,
                channel_meta=context.channel_meta,
                backend_id=context.backend_id,
                project_path=str(context.project_path),
                backend_state=context.backend_state,
            )
            self._append_transcript_message(
                session_id,
                Message(role="user", content=user_message),
            )
            backend = self.backends.get(context.backend_id)
            if backend is None:
                raise RuntimeError(f"Unknown backend: {context.backend_id}")
            if context.backend_id != "codex":
                await emit("run_started", {"session_id": session_id})
            finish_reason = await backend.stream_chat(context, user_message, emit)
        except Exception as exc:
            finish_reason = "error"
            await emit(
                "error",
                {
                    "session_id": session_id,
                    "message": str(exc),
                },
            )
        finally:
            reset_tool_event_emitter(token)
            await emit(
                "done",
                {
                    "session_id": session_id,
                    "finish_reason": finish_reason,
                },
            )
            await event_queue.put(None)

    def get_backend_runtime(self, session_id: str) -> BackendRuntimePayload:
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        if backend is None:
            return BackendRuntimePayload(
                backend_id="yier",
                label="Unknown backend",
                ready=False,
                status="error",
                detail=f"Unknown backend: {context.backend_id}",
            )
        return BackendRuntimePayload.model_validate(backend.runtime_payload(context))

    def get_pending_approvals(self, session_id: str) -> list[PendingApproval]:
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        if backend is None:
            return []
        return [PendingApproval.model_validate(item) for item in backend.pending_approvals(context)]

    async def respond_to_approval(
        self,
        session_id: str,
        request_id: str,
        decision: str,
        content: dict[str, Any] | None = None,
    ) -> bool:
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        if backend is None:
            return False
        return await backend.respond_to_approval(context, request_id, decision, content)

    async def respond_to_codex_raw_request(
        self,
        session_id: str,
        request_id: str,
        response_payload: dict[str, Any],
    ) -> bool:
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            return False
        return await backend.respond_to_raw_request(context, request_id, response_payload)

    def resolve_pending_approval_request_id(
        self,
        session_id: str,
        *,
        preferred_kind: str | None = None,
    ) -> str | None:
        pending_approvals = self.get_pending_approvals(session_id)
        if preferred_kind:
            for approval in pending_approvals:
                if approval.kind == preferred_kind:
                    return approval.request_id
        if pending_approvals:
            return pending_approvals[0].request_id
        return None

    async def start_codex_turn_in_background(
        self,
        session_id: str,
        prompt: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any]:
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            raise RuntimeError("Only Codex sessions can start IPC-controlled turns.")

        existing_task = self._ipc_stream_tasks.get(session_id)
        if existing_task is not None and not existing_task.done():
            raise RuntimeError("Codex turn is already running for this session.")

        session_lock = self._session_lock(session_id)
        if session_lock.locked():
            raise RuntimeError("Codex turn is already running for this session.")

        user_message = self._codex_input_payload_text(prompt)
        if user_message:
            self._append_transcript_message(
                session_id,
                Message(role="user", content=user_message),
            )

        _codex_ipc_debug_log(
            "start_codex_turn_in_background before backend.start_turn",
            session_id=session_id,
            prompt_type=type(prompt).__name__,
        )
        start_response = await backend.start_turn(context, prompt)
        turn = start_response.get("turn")
        if not isinstance(turn, dict):
            raise RuntimeError("Codex start turn response did not include a turn payload.")
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RuntimeError("Codex start turn response did not include a turn id.")
        _codex_ipc_debug_log(
            "start_codex_turn_in_background after backend.start_turn",
            session_id=session_id,
            turn_id=turn_id,
        )

        task = asyncio.create_task(
            self._consume_codex_turn_stream_to_event_broker(session_id, turn_id),
            name=f"codex-ipc-turn:{session_id}",
        )
        self._ipc_stream_tasks[session_id] = task

        def cleanup(completed_task: asyncio.Task[None]) -> None:
            if self._ipc_stream_tasks.get(session_id) is completed_task:
                self._ipc_stream_tasks.pop(session_id, None)

            with suppress(asyncio.CancelledError):
                exception = completed_task.exception()
            if exception is not None:
                asyncio.create_task(
                    self.event_broker.publish(
                        "error",
                        {
                            "session_id": session_id,
                            "message": str(exception),
                        },
                    )
                )

        task.add_done_callback(cleanup)
        return start_response

    async def _consume_codex_turn_stream_to_event_broker(
        self,
        session_id: str,
        turn_id: str,
    ) -> None:
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            raise RuntimeError("Codex backend is not available.")

        async def emit(event: str, data: dict[str, Any]) -> None:
            await self._emit_stream_event(
                event,
                data,
                publish_to_event_broker=True,
            )

        finish_reason = "stop"
        try:
            async with self._session_lock(session_id):
                finish_reason = await backend.consume_turn_stream(
                    context,
                    turn_id,
                    emit,
                )
        except Exception as exc:
            finish_reason = "error"
            logger.exception(
                "Failed to consume Codex turn stream for session %s",
                session_id,
            )
            await self._emit_stream_event(
                "error",
                {
                    "session_id": session_id,
                    "message": str(exc),
                },
                publish_to_event_broker=True,
            )
        finally:
            await self._emit_stream_event(
                "done",
                {
                    "session_id": session_id,
                    "finish_reason": finish_reason,
                },
                publish_to_event_broker=True,
            )

    async def steer_codex_turn(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any]:
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            raise RuntimeError("Codex backend is not available.")
        return await backend.steer_turn(context, turn_id, input_payload)

    async def interrupt_codex_turn(
        self,
        *,
        session_id: str,
        turn_id: str | None,
    ) -> dict[str, Any]:
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            raise RuntimeError("Codex backend is not available.")
        return await backend.interrupt_turn(context, turn_id)

    def edit_last_codex_user_turn(
        self,
        session_id: str,
        content: str,
    ) -> None:
        transcript = self.transcript_store.get_session_messages(session_id) or []
        for message in reversed(transcript):
            if message.role != "user":
                continue
            message.content = content
            self.transcript_store.save(session_id, transcript)
            return

    def build_codex_ipc_conversation_state(self, session_id: str) -> dict[str, Any]:
        metadata = self.get_session_metadata(session_id)
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        runtime = self.get_backend_runtime(session_id)
        backend_state = metadata["backend_state"]
        current_timestamp_ms = int(time() * 1000)
        native_state = self._native_codex_ipc_conversation_state(session_id)
        latest_model = backend_state.get("model") or ""
        latest_reasoning_effort = backend_state.get("reasoning_effort")

        if native_state is not None:
            backend = native_state["backend"]
            thread = native_state["thread"]
            turns = backend.build_ipc_turns(
                context,
                [turn for turn in thread.get("turns", []) if isinstance(turn, dict)],
            )
            created_at_ms = int(thread.get("createdAt", 0) or 0) * 1000 or current_timestamp_ms
            updated_at_ms = int(thread.get("updatedAt", 0) or 0) * 1000 or current_timestamp_ms
            title = thread.get("name") or metadata.get("title")
            source = thread.get("source") or metadata.get("source", "chat")
            cwd = thread.get("cwd") or metadata.get("project_path")
            git_info = thread.get("gitInfo", backend_state.get("git_info"))
            thread_runtime_status = native_state.get("threadRuntimeStatus") or {
                "type": runtime.status,
                "activeFlags": list(runtime.active_flags),
            }
            extra_state = {
                "preview": thread.get("preview"),
                "cliVersion": thread.get("cliVersion"),
                "ephemeral": thread.get("ephemeral"),
                "modelProvider": thread.get("modelProvider"),
                "path": thread.get("path"),
                "agentNickname": thread.get("agentNickname"),
                "agentRole": thread.get("agentRole"),
            }
        else:
            turns = self._build_fallback_codex_ipc_turns(session_id, runtime.status)
            if isinstance(backend, CodexAppServerBackend):
                turns = backend.build_ipc_turns(context, turns)
            updated_at = metadata.get("updated_at")
            created_at_ms = (
                int(updated_at * 1000)
                if isinstance(updated_at, (int, float))
                else current_timestamp_ms
            )
            updated_at_ms = created_at_ms
            title = metadata.get("title")
            source = metadata.get("source", "chat")
            cwd = metadata.get("project_path")
            git_info = backend_state.get("git_info")
            thread_runtime_status = {
                "type": runtime.status,
                "activeFlags": list(runtime.active_flags),
            }
            extra_state = {}

        pending_requests = self._codex_ipc_requests(session_id)
        latest_collaboration_mode = self._codex_ipc_collaboration_mode(
            backend_state.get("collaboration_mode"),
            latest_model=latest_model,
            latest_reasoning_effort=latest_reasoning_effort,
        )
        thread_runtime_status = self._codex_ipc_thread_runtime_status(
            thread_runtime_status,
            fallback_type=runtime.status,
            fallback_active_flags=list(runtime.active_flags),
            pending_requests=pending_requests,
            turns=turns,
        )

        conversation_state = {
            "id": session_id,
            "hostId": "local",
            "turns": turns,
            "pendingSteers": [],
            "requests": pending_requests,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
            "title": title,
            "source": source,
            "latestModel": latest_model,
            "latestReasoningEffort": latest_reasoning_effort,
            "previousTurnModel": None,
            "latestCollaborationMode": latest_collaboration_mode,
            "hasUnreadTurn": bool(backend_state.get("has_unread_turn")),
            "rolloutPath": backend_state.get("rollout_path") or "",
            "gitInfo": git_info,
            "resumeState": backend_state.get("resume_state") or "resumed",
            "latestTokenUsageInfo": backend_state.get("latest_token_usage_info"),
            "cwd": cwd,
            "threadId": runtime.thread_id or session_id,
            "threadRuntimeStatus": thread_runtime_status,
        }
        for key, value in extra_state.items():
            if value is not None:
                conversation_state[key] = value
        return conversation_state

    def apply_codex_ipc_stream_change(
        self,
        session_id: str,
        change: dict[str, Any],
    ) -> None:
        metadata = self.get_session_metadata(session_id)
        if metadata["backend_id"] != "codex":
            return

        change_type = change.get("type")
        next_conversation_state: dict[str, Any] | None = None
        if change_type == "snapshot":
            conversation_state = change.get("conversationState")
            if isinstance(conversation_state, dict):
                next_conversation_state = deepcopy(conversation_state)
        elif change_type == "patches":
            patches = change.get("patches")
            if isinstance(patches, list):
                cached_state = metadata["backend_state"].get("ipc_conversation_state")
                base_state = deepcopy(cached_state) if isinstance(cached_state, dict) else {}
                if self._apply_codex_ipc_patches(base_state, patches):
                    next_conversation_state = base_state

        if next_conversation_state is None:
            return

        backend_updates: dict[str, Any] = {
            "ipc_conversation_state": next_conversation_state,
        }
        if "hasUnreadTurn" in next_conversation_state:
            backend_updates["has_unread_turn"] = bool(
                next_conversation_state.get("hasUnreadTurn")
            )
        if "latestTokenUsageInfo" in next_conversation_state:
            backend_updates["latest_token_usage_info"] = next_conversation_state.get(
                "latestTokenUsageInfo"
            )
        if "resumeState" in next_conversation_state:
            backend_updates["resume_state"] = (
                next_conversation_state.get("resumeState") or "resumed"
            )
        self.update_session_backend_state(session_id, backend_updates)

    def _apply_codex_ipc_patches(
        self,
        root: dict[str, Any],
        patches: list[dict[str, Any]],
    ) -> bool:
        try:
            for patch in patches:
                if not isinstance(patch, dict):
                    continue
                operation = patch.get("op")
                path = patch.get("path")
                if not isinstance(operation, str) or not isinstance(path, list) or not path:
                    continue
                if operation in {"add", "replace"}:
                    self._set_codex_ipc_path(root, path, deepcopy(patch.get("value")))
                elif operation == "remove":
                    self._remove_codex_ipc_path(root, path)
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        return True

    def _set_codex_ipc_path(
        self,
        root: dict[str, Any],
        path: list[Any],
        value: Any,
    ) -> None:
        current: Any = root
        for index, segment in enumerate(path[:-1]):
            next_segment = path[index + 1]
            if isinstance(current, list):
                if not isinstance(segment, int):
                    raise TypeError("List path segment must be an integer.")
                while len(current) <= segment:
                    current.append({} if not isinstance(next_segment, int) else [])
                child = current[segment]
                if not isinstance(child, (dict, list)):
                    child = {} if not isinstance(next_segment, int) else []
                    current[segment] = child
                current = child
                continue

            if not isinstance(current, dict):
                raise TypeError("Patch parent must be a dict or list.")
            child = current.get(segment)
            if not isinstance(child, (dict, list)):
                child = {} if not isinstance(next_segment, int) else []
                current[segment] = child
            current = child

        last_segment = path[-1]
        if isinstance(current, list):
            if not isinstance(last_segment, int):
                raise TypeError("List path segment must be an integer.")
            if last_segment < len(current):
                current[last_segment] = value
            elif last_segment == len(current):
                current.append(value)
            else:
                while len(current) < last_segment:
                    current.append(None)
                current.append(value)
            return

        if not isinstance(current, dict):
            raise TypeError("Patch parent must be a dict or list.")
        current[last_segment] = value

    def _remove_codex_ipc_path(
        self,
        root: dict[str, Any],
        path: list[Any],
    ) -> None:
        current: Any = root
        for segment in path[:-1]:
            if isinstance(current, list):
                if not isinstance(segment, int):
                    raise TypeError("List path segment must be an integer.")
                current = current[segment]
                continue
            if not isinstance(current, dict):
                raise TypeError("Patch parent must be a dict or list.")
            current = current[segment]

        last_segment = path[-1]
        if isinstance(current, list):
            if not isinstance(last_segment, int):
                raise TypeError("List path segment must be an integer.")
            del current[last_segment]
            return
        if not isinstance(current, dict):
            raise TypeError("Patch parent must be a dict or list.")
        current.pop(last_segment, None)

    def _native_codex_ipc_conversation_state(self, session_id: str) -> dict[str, Any] | None:
        context = self.get_session_context(session_id)
        if context.backend_id != "codex":
            return None
        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            return None
        thread_id = context.backend_state.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        try:
            native_state = backend.load_thread_state(context)
        except Exception:
            return None
        thread = native_state.get("thread")
        if not isinstance(thread, dict):
            return None
        return {
            **native_state,
            "backend": backend,
        }

    def _codex_ipc_requests(self, session_id: str) -> list[dict[str, Any]]:
        context = self.get_session_context(session_id)
        backend = self.backends.get(context.backend_id)
        if isinstance(backend, CodexAppServerBackend):
            raw_requests = backend.pending_conversation_requests(context)
            if raw_requests:
                return raw_requests
        return [
            approval.model_dump(mode="json")
            for approval in self.get_pending_approvals(session_id)
        ]

    def _codex_ipc_collaboration_mode(
        self,
        value: Any,
        *,
        latest_model: str,
        latest_reasoning_effort: Any,
    ) -> dict[str, Any]:
        default_settings = {
            "model": latest_model,
            "reasoning_effort": (
                latest_reasoning_effort if latest_reasoning_effort is None else str(latest_reasoning_effort)
            ),
            "developer_instructions": None,
        }
        default_mode = {
            "mode": "default",
            "settings": default_settings,
        }
        if isinstance(value, dict):
            mode = value.get("mode")
            settings = value.get("settings")
            if isinstance(mode, str) and mode.strip():
                normalized = dict(value)
                normalized["mode"] = mode.strip()
                merged_settings = dict(default_settings)
                if isinstance(settings, dict):
                    model = settings.get("model")
                    if isinstance(model, str):
                        merged_settings["model"] = model
                    reasoning_effort = settings.get("reasoning_effort")
                    if reasoning_effort is None or isinstance(reasoning_effort, str):
                        merged_settings["reasoning_effort"] = reasoning_effort
                    developer_instructions = settings.get("developer_instructions")
                    if developer_instructions is None or isinstance(developer_instructions, str):
                        merged_settings["developer_instructions"] = developer_instructions
                normalized["settings"] = merged_settings
                return normalized
        if isinstance(value, str) and value.strip():
            return {
                "mode": value.strip(),
                "settings": default_settings,
            }
        return default_mode

    def _codex_ipc_thread_runtime_status(
        self,
        value: Any,
        *,
        fallback_type: str,
        fallback_active_flags: list[str],
        pending_requests: list[dict[str, Any]],
        turns: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        root = value.get("root") if isinstance(value, dict) else None
        payload = root if isinstance(root, dict) else value
        status_type = fallback_type
        active_flags = list(fallback_active_flags)
        if isinstance(payload, dict):
            raw_type = payload.get("type")
            if isinstance(raw_type, str) and raw_type:
                status_type = raw_type
            raw_active_flags = payload.get("activeFlags")
            if not isinstance(raw_active_flags, list):
                raw_active_flags = payload.get("active_flags")
            if isinstance(raw_active_flags, list):
                active_flags = [
                    str(flag.get("value") if isinstance(flag, dict) and "value" in flag else flag)
                    for flag in raw_active_flags
                ]
        elif isinstance(payload, str) and payload:
            status_type = payload

        if fallback_type == "active" and status_type in {"", "idle"}:
            status_type = "active"

        if isinstance(turns, list) and any(
            isinstance(turn, dict) and turn.get("status") == "inProgress"
            for turn in turns
        ):
            status_type = "active"

        if self._codex_ipc_has_waiting_on_approval_request(pending_requests):
            if status_type in {"", "idle"}:
                status_type = "active"
            if "waitingOnApproval" not in active_flags:
                active_flags.append("waitingOnApproval")

        return {
            "type": status_type or fallback_type,
            "activeFlags": active_flags,
        }

    def _codex_ipc_has_waiting_on_approval_request(
        self,
        pending_requests: list[dict[str, Any]],
    ) -> bool:
        waiting_methods = {
            "item/fileChange/requestApproval",
            "item/commandExecution/requestApproval",
            "item/permissions/requestApproval",
            "mcpServer/elicitation/request",
            "elicitation/create",
        }
        for request in pending_requests:
            if not isinstance(request, dict):
                continue
            method = request.get("method")
            if isinstance(method, str) and method in waiting_methods:
                return True
        return False

    def _build_fallback_codex_ipc_turns(
        self,
        session_id: str,
        runtime_status: str,
    ) -> list[dict[str, Any]]:
        transcript = self.transcript_store.get_session_messages(session_id) or []
        turns: list[dict[str, Any]] = []
        current_turn: dict[str, Any] | None = None
        turn_index = 0
        current_timestamp_ms = int(time() * 1000)

        for message in transcript:
            content = getattr(message, "content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            role = getattr(message, "role", "")
            if role == "user":
                if current_turn is not None:
                    turns.append(current_turn)
                turn_index += 1
                current_turn = {
                    "id": f"{session_id}:turn:{turn_index}",
                    "turnId": f"{session_id}:turn:{turn_index}",
                    "status": "completed",
                    "error": None,
                    "diff": None,
                    "items": [
                        {
                            "id": f"{session_id}:turn:{turn_index}:user",
                            "type": "userMessage",
                            "content": [{"type": "text", "text": content, "text_elements": []}],
                        }
                    ],
                    "turnStartedAtMs": current_timestamp_ms,
                    "finalAssistantStartedAtMs": None,
                }
                continue
            if role != "assistant":
                continue
            if current_turn is None:
                turn_index += 1
                current_turn = {
                    "id": f"{session_id}:turn:{turn_index}",
                    "turnId": f"{session_id}:turn:{turn_index}",
                    "status": "completed",
                    "error": None,
                    "diff": None,
                    "items": [],
                    "turnStartedAtMs": current_timestamp_ms,
                    "finalAssistantStartedAtMs": current_timestamp_ms,
                }
            current_turn["items"].append(
                {
                    "id": f"{session_id}:turn:{turn_index}:assistant:{len(current_turn['items'])}",
                    "type": "agentMessage",
                    "text": content,
                    "phase": "final_answer",
                    "memoryCitation": None,
                }
            )
            if current_turn.get("finalAssistantStartedAtMs") is None:
                current_turn["finalAssistantStartedAtMs"] = current_timestamp_ms

        if current_turn is not None:
            if runtime_status == "active":
                current_turn["status"] = "inProgress"
            turns.append(current_turn)
        return turns

    def build_codex_ipc_queued_followups(self, session_id: str) -> list[dict[str, Any]]:
        metadata = self.get_session_metadata(session_id)
        workspace_root = metadata.get("project_path")
        workspace_roots = [workspace_root] if isinstance(workspace_root, str) and workspace_root else []
        created_at_ms = int(time() * 1000)
        messages: list[dict[str, Any]] = []
        for item in self.followup_queue.list_items():
            if item.owner_session_id != session_id:
                continue
            messages.append(
                {
                    "id": item.queue_id,
                    "text": item.prompt,
                    "context": {"workspaceRoots": workspace_roots},
                    "cwd": workspace_root or "/",
                    "createdAt": created_at_ms,
                }
            )
        return messages

    async def _stream_with_yier_backend(
        self,
        session_id: str,
        prompt: str,
        emit: StreamEmitter,
    ) -> str:
        agent = await self._get_agent()
        if agent is None:
            await emit(
                "error",
                {
                    "session_id": session_id,
                    "message": "LLM configuration is incomplete. Update settings before chatting.",
                },
            )
            return "error"

        return await self._run_agent_prompt(
            agent=agent,
            session_id=session_id,
            prompt=prompt,
            emit=emit,
        )

    async def _run_agent_prompt(
        self,
        agent: Agent,
        session_id: str,
        prompt: str,
        emit: StreamEmitter,
    ) -> str:
        async with self._session_lock(session_id):
            token = set_tool_event_emitter(emit)
            try:
                return await self._stream_agent_prompt(
                    agent=agent,
                    session_id=session_id,
                    prompt=prompt,
                    emit=emit,
                )
            finally:
                reset_tool_event_emitter(token)

    async def _stream_agent_prompt(
        self,
        agent: Agent,
        session_id: str,
        prompt: str,
        emit: StreamEmitter,
    ) -> str:
        finish_reason = "stop"

        async for event in agent.run_stream(prompt, session_id):
            if isinstance(event, ToolCallStartEvent):
                await emit(
                    "tool_call_start",
                    {
                        "session_id": session_id,
                        "tool_name": event.tool_name,
                        "tool_call_id": event.tool_call_id,
                        "arguments": event.arguments,
                        "iteration": event.iteration,
                    },
                )
                continue

            if isinstance(event, ToolCallEndEvent):
                await emit(
                    "tool_call_end",
                    {
                        "session_id": session_id,
                        "tool_name": event.tool_name,
                        "tool_call_id": event.tool_call_id,
                        "result": event.result,
                        "is_error": event.is_error,
                        "metadata": event.metadata,
                        "raw": event.raw,
                        "iteration": event.iteration,
                    },
                )
                continue

            if isinstance(event, LLMEndEvent):
                finish_reason = event.finish_reason
                if event.message.reasoning_content:
                    await emit(
                        "reasoning",
                        {
                            "session_id": session_id,
                            "content": event.message.reasoning_content,
                            "iteration": event.iteration,
                        },
                    )
                if event.finish_reason == "stop" and event.message.content:
                    self._append_transcript_message(
                        session_id,
                        Message(role="assistant", content=event.message.content),
                    )
                    await emit(
                        "assistant_message",
                        {
                            "session_id": session_id,
                            "content": event.message.content,
                            "iteration": event.iteration,
                        },
                    )
                continue

            if isinstance(event, MessageCompactEvent):
                await emit(
                    "reasoning",
                    {
                        "session_id": session_id,
                        "content": (
                            f"Conversation memory compacted from "
                            f"{event.original_count} to {event.compacted_count} messages."
                        ),
                        "iteration": event.iteration,
                    },
                )
                continue

            if isinstance(event, ErrorEvent):
                finish_reason = "error"
                await emit(
                    "error",
                    {
                        "session_id": session_id,
                        "message": f"{event.error_type}: {event.error_message}",
                        "iteration": event.iteration,
                    },
                )
                continue

            if isinstance(event, AgentEndEvent):
                finish_reason = event.finish_reason

        return finish_reason

    def _append_transcript_message(self, session_id: str, message: Message) -> None:
        messages = self.transcript_store.get_session_messages(session_id) or []
        messages.append(message)
        self.transcript_store.save(session_id, messages)
        metadata = self.get_session_metadata(session_id)
        title = metadata["title"] or self._session_title([item.model_dump() for item in messages if hasattr(item, "model_dump")])
        preview = self._session_preview([item.model_dump() for item in messages if hasattr(item, "model_dump")])
        self.ensure_session_metadata(
            session_id,
            source=metadata["source"],
            channel_meta=metadata["channel_meta"],
            backend_id=metadata["backend_id"],
            project_path=metadata["project_path"],
            backend_state=metadata["backend_state"],
            codex_work_mode=metadata["codex_work_mode"],
            title=title,
            preview=preview,
            updated_at=time(),
        )

    def _codex_input_payload_text(
        self,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> str:
        parts: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    parts.append(normalized)
                return
            if isinstance(value, list):
                for entry in value:
                    collect(entry)
                return
            if not isinstance(value, dict):
                return

            text_value = value.get("text")
            if isinstance(text_value, str) and text_value.strip():
                parts.append(text_value.strip())

            content_value = value.get("content")
            if isinstance(content_value, str) and content_value.strip():
                parts.append(content_value.strip())
            elif isinstance(content_value, list):
                for entry in content_value:
                    collect(entry)

        collect(input_payload)
        return "\n".join(parts)

    def _session_title(self, messages: list[dict[str, Any]]) -> str:
        for message in messages:
            if message.get("role") != "user":
                continue
            content = self._normalized_message_content(message)
            if content:
                return self._truncate_text(content, 48)
        return "New session"

    def _session_preview(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            content = self._normalized_message_content(message)
            if content:
                return self._truncate_text(content, 72)
        return ""

    def _message_count(self, messages: list[dict[str, Any]]) -> int:
        return sum(
            1
            for message in messages
            if message.get("role") in {"user", "assistant"} and self._normalized_message_content(message)
        )

    def _normalized_message_content(self, message: dict[str, Any]) -> str:
        content = message.get("content")
        if not isinstance(content, str):
            return ""
        return " ".join(content.strip().split())

    def _truncate_text(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"{value[: limit - 1].rstrip()}…"

    def _persist_ui_event(self, event: str, data: dict[str, Any]) -> None:
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return

        if event not in {
            "tool_call_start",
            "tool_call_end",
            "command_start",
            "command_output",
            "command_end",
            "background_command_started",
            "background_command_output",
            "background_command_end",
            "background_followup_queued",
            "background_followup_started",
            "background_followup_finished",
            "reasoning",
            "plan",
            "approval_requested",
            "approval_resolved",
            "turn_aborted",
            "stream_error",
        }:
            return

        self.session_ui_store.append_activity_event(session_id, event, data)

    def _handle_internal_event(self, event: str, data: dict[str, Any]) -> None:
        if event != "background_command_started":
            session_id = data.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                return
            if event in {"assistant_message", "turn_completed"}:
                self.update_session_backend_state(session_id, {"has_unread_turn": True})
            elif event == "run_started":
                self.update_session_backend_state(session_id, {"has_unread_turn": False})
            return

        background_session_id = data.get("background_session_id")
        owner_session_id = data.get("session_id")
        if not isinstance(background_session_id, str) or not isinstance(owner_session_id, str):
            return

        self._background_owner_sessions[background_session_id] = owner_session_id
        self._background_cursors.setdefault(
            background_session_id,
            {
                "stdout_chars": 0,
                "stderr_chars": 0,
                "end_emitted": False,
            },
        )

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._session_run_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_run_locks[session_id] = lock
        return lock

    async def _background_supervisor_loop(self) -> None:
        while self._started:
            try:
                completed_session_ids = await self._publish_background_updates()
                await self._process_ready_followups(completed_session_ids)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.event_broker.publish(
                    "error",
                    {
                        "session_id": "",
                        "message": f"Background supervisor error: {exc}",
                    },
                )
            await asyncio.sleep(0.35)

    async def _publish_background_updates(self) -> set[str]:
        completed_session_ids: set[str] = set()

        for background_session_id, owner_session_id in list(self._background_owner_sessions.items()):
            try:
                session = self.background_manager.require_session(background_session_id)
            except (KeyError, ValueError):
                completed_session_ids.add(background_session_id)
                continue

            cursor = self._background_cursors.setdefault(
                session.session_id,
                {
                    "stdout_chars": 0,
                    "stderr_chars": 0,
                    "end_emitted": False,
                },
            )

            for stream_name, buffer_name in (("stdout", "stdout_buffer"), ("stderr", "stderr_buffer")):
                output_text = getattr(session, buffer_name).render()
                chars_key = f"{stream_name}_chars"
                previous_chars = int(cursor[chars_key])
                if len(output_text) < previous_chars:
                    previous_chars = 0
                if len(output_text) == previous_chars:
                    continue

                new_content = output_text[previous_chars:]
                cursor[chars_key] = len(output_text)
                payload = {
                    "session_id": owner_session_id,
                    "background_session_id": session.session_id,
                    "command": session.command,
                    "cwd": str(session.cwd),
                    "stream": stream_name,
                    "content": new_content,
                }
                self._persist_ui_event("background_command_output", payload)
                await self.event_broker.publish("background_command_output", payload)

            if session.is_running() or cursor["end_emitted"]:
                continue

            cursor["end_emitted"] = True
            completed_session_ids.add(session.session_id)
            payload = {
                "session_id": owner_session_id,
                "background_session_id": session.session_id,
                "command": session.command,
                "cwd": str(session.cwd),
                "state": session.state,
                "exit_code": session.exit_code,
            }
            self._persist_ui_event("background_command_end", payload)
            await self.event_broker.publish("background_command_end", payload)

        return completed_session_ids

    async def _process_ready_followups(
        self,
        completed_session_ids: set[str],
    ) -> None:
        if not completed_session_ids:
            return

        ready_items = self.followup_queue.pop_ready(completed_session_ids)
        if not ready_items:
            return

        agent = await self._get_agent()
        if agent is None:
            return

        for item in ready_items:
            started_payload = {
                "session_id": item.owner_session_id,
                "background_session_id": item.trigger_session_id,
                "queue_id": item.queue_id,
                "prompt": item.prompt,
            }
            self._persist_ui_event("background_followup_started", started_payload)
            await self.event_broker.publish("background_followup_started", started_payload)
            await self.codex_ipc_bridge.notify_stream_event("background_followup_started", started_payload)

            async def emit(event: str, data: dict[str, Any]) -> None:
                self._handle_internal_event(event, data)
                self._persist_ui_event(event, data)
                await self.event_broker.publish(event, data)

            finish_reason = await self._run_agent_prompt(
                agent=agent,
                session_id=item.owner_session_id,
                prompt=item.prompt,
                emit=emit,
            )
            finished_payload = {
                "session_id": item.owner_session_id,
                "background_session_id": item.trigger_session_id,
                "queue_id": item.queue_id,
                "finish_reason": finish_reason,
            }
            self._persist_ui_event("background_followup_finished", finished_payload)
            await self.event_broker.publish("background_followup_finished", finished_payload)
            await self.codex_ipc_bridge.notify_stream_event("background_followup_finished", finished_payload)

        for background_session_id in completed_session_ids:
            self._background_owner_sessions.pop(background_session_id, None)

    async def _get_agent(self) -> Agent | None:
        if not self._started:
            await self.start()

        async with self._lock:
            await self.mcp_manager.reload_if_changed()
            signature = self._agent_state_signature()
            if signature != self._agent_signature:
                await self._rebuild_agent_locked()
            return self._agent

    async def _rebuild_agent_locked(self) -> None:
        settings = self.config_service.load_web_settings()
        signature = self._agent_state_signature()
        if not settings.llm.is_ready:
            self._agent = None
            self._agent_signature = signature
            return

        assistant_settings = self.config_service.build_assistant_settings()
        self._configure_background_manager(assistant_settings)
        workspace_tools = self._build_workspace_tools(assistant_settings)
        mcp_tools = await self.mcp_manager.get_tools()
        llm = self._build_llm(settings.llm)
        skill_catalog = self._get_skill_catalog()
        self._agent = Agent(
            llm=llm,
            tools=[*workspace_tools, *mcp_tools],
            system_prompt=assistant_settings.system_prompt,
            verbose=False,
            max_iterations=assistant_settings.max_iterations,
            enable_memory=True,
            skill_catalog=skill_catalog,
            session_store=self.session_store,
            compaction_config=CompactionConfig(
                enabled=assistant_settings.compaction.enabled,
                trigger_message_count=assistant_settings.compaction.trigger_message_count,
                preserve_recent_messages=assistant_settings.compaction.preserve_recent_messages,
                summary_max_tokens=assistant_settings.compaction.summary_max_tokens,
            ),
        )
        self._agent_signature = signature

    def _agent_state_signature(self) -> tuple[Any, ...]:
        settings = self.config_service.load_web_settings()
        return (
            self.config_service.settings_marker(),
            tuple(settings.allowed_roots),
            self.config_service.mcp_marker(),
            self.mcp_manager.version,
        )

    def _configure_background_manager(self, assistant_settings: AssistantSettings) -> None:
        allowed_roots = tuple(self._normalized_allowed_roots(assistant_settings))
        self.background_manager.access.allowed_roots = allowed_roots
        self.background_manager.access.default_root = assistant_settings.workspace_root.resolve()
        self.background_manager.allow_shell = assistant_settings.run_command.allow_shell
        self.background_manager.shell_program = assistant_settings.run_command.shell_program

    def _build_workspace_tools(self, assistant_settings: AssistantSettings) -> list[Tool]:
        normalized_roots = self._normalized_allowed_roots(assistant_settings)
        workspace_root = assistant_settings.workspace_root

        return [
            create_list_files_tool(normalized_roots, default_root=workspace_root),
            create_read_file_tool(normalized_roots, default_root=workspace_root),
            create_replace_in_file_tool(normalized_roots, default_root=workspace_root),
            create_streaming_run_command_tool(
                normalized_roots,
                default_root=workspace_root,
                allow_shell=assistant_settings.run_command.allow_shell,
                shell_program=assistant_settings.run_command.shell_program,
            ),
            create_streaming_start_background_command_tool(self.background_manager),
            create_list_background_commands_tool(self.background_manager),
            create_read_background_command_tool(self.background_manager),
            create_wait_background_command_tool(self.background_manager),
            create_stop_background_command_tool(self.background_manager),
            create_send_background_command_input_tool(self.background_manager),
            create_queue_background_followup_tool(self.background_manager, self.followup_queue),
            create_write_file_tool(normalized_roots, default_root=workspace_root),
            create_search_files_tool(normalized_roots, default_root=workspace_root),
        ]

    def _normalized_allowed_roots(self, assistant_settings: AssistantSettings) -> list[Path]:
        roots = list(assistant_settings.allowed_roots)
        if assistant_settings.include_skill_directories_in_allowed_roots:
            roots.extend(self._get_skill_catalog().dirs())

        normalized_roots: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            resolved = Path(root).expanduser().resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            normalized_roots.append(resolved)
        return normalized_roots

    def _get_skill_catalog(self) -> SkillCatalog:
        if self.skill_catalog is None:
            self.skill_catalog = SkillCatalog.discover(
                self.project_root,
                include_project=True,
                include_global=True,
            )
        return self.skill_catalog

    def _build_llm(self, settings: StoredLLMSettings) -> LLM:
        return LLM(
            base_url=settings.base_url or None,
            api_key=settings.api_key or None,
            model=settings.model or None,
            provider=settings.provider or None,
        )
