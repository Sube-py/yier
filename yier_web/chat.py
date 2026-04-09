from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
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
from yier_web.codex.background import (
    FollowupQueueManager,
    _build_codex_background_runner_command,
    _write_codex_background_request,
    create_find_codex_projects_tool,
    create_find_codex_sessions_tool,
    create_queue_background_followup_tool,
    create_resume_codex_background_session_tool,
    create_start_codex_background_session_tool,
)
from yier_web.config import AppConfigService
from yier_web.codex.ipc import CodexConversationStateService, CodexThreadFollowerBridge
from yier_web.codex.pairing import CodexPairedEditorBridge
from yier_web.codex.sdk.workspace import CodexWorkspaceService
from yier_web.event_stream import EventStreamBroker
from yier_web.session_conversation_state_store import SessionConversationStateStore
from yier_web.session_metadata_store import SessionMetadataStore
from yier_web.session_ui_store import SessionUIStore
from yier_web.schemas import (
    BackendRuntimePayload,
    ChannelMetaPayload,
    CodexGoalLoopState,
    CodexGoalLoopStatus,
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

GOAL_LOOP_MAX_ITERATIONS = 8
GOAL_LOOP_MAX_CONSECUTIVE_FAILURES = 2
GOAL_LOOP_STATUS_MARKER = "YIER_GOAL_LOOP_STATUS:"
GOAL_LOOP_REASON_MARKER = "YIER_GOAL_LOOP_REASON:"
GOAL_LOOP_NEXT_PROMPT_MARKER = "YIER_GOAL_LOOP_NEXT_PROMPT:"


@dataclass(slots=True)
class SessionTranscriptView:
    messages: list[StoredSessionMessage]
    activity_events: list[dict[str, Any]]
    activity_history: dict[str, int | None]
    codex_turn_timings: list[dict[str, int | str | None]]


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


def _default_goal_loop_state_payload() -> dict[str, Any]:
    return CodexGoalLoopState(
        max_iterations=GOAL_LOOP_MAX_ITERATIONS,
        max_consecutive_failures=GOAL_LOOP_MAX_CONSECUTIVE_FAILURES,
    ).model_dump(mode="json")


def _clean_goal_loop_marker_value(value: str) -> str:
    return value.strip().strip("`").strip()


def _extract_goal_loop_markers(text: str) -> dict[str, str]:
    markers: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    marker_map = {
        GOAL_LOOP_STATUS_MARKER: "status",
        GOAL_LOOP_REASON_MARKER: "reason",
        GOAL_LOOP_NEXT_PROMPT_MARKER: "next_prompt",
    }

    for raw_line in text.splitlines():
        stripped_line = raw_line.strip()
        matched_prefix = next(
            (prefix for prefix in marker_map if stripped_line.startswith(prefix)),
            None,
        )
        if matched_prefix is not None:
            if current_key is not None:
                markers[current_key] = _clean_goal_loop_marker_value("\n".join(current_lines))
            current_key = marker_map[matched_prefix]
            current_lines = [stripped_line[len(matched_prefix):].lstrip()]
            continue
        if current_key is not None:
            current_lines.append(raw_line)

    if current_key is not None:
        markers[current_key] = _clean_goal_loop_marker_value("\n".join(current_lines))

    return markers


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
        self.session_conversation_state_store = SessionConversationStateStore(
            self.config_service.session_conversation_state_path
        )
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
        self.codex_conversation_state = CodexConversationStateService(self)
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
        self._timeline_sequence_counters: dict[str, int] = {}
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
        self._timeline_sequence_counters.clear()

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

    def _normalize_timeline_sequence_value(self, value: Any) -> int | None:
        if not isinstance(value, int) or value < 0:
            return None
        return value

    def _ensure_timeline_sequence_counter(self, session_id: str) -> None:
        if session_id in self._timeline_sequence_counters:
            return

        max_sequence = -1
        for sequence in self.session_ui_store.load_transcript_message_sequences(session_id):
            normalized = self._normalize_timeline_sequence_value(sequence)
            if normalized is not None:
                max_sequence = max(max_sequence, normalized)

        for event in self.session_ui_store.load_activity_events(session_id):
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            normalized = self._normalize_timeline_sequence_value(data.get("sequence"))
            if normalized is not None:
                max_sequence = max(max_sequence, normalized)

        self._timeline_sequence_counters[session_id] = max_sequence + 1

    def _reserve_timeline_sequence(
        self,
        session_id: str,
        *,
        explicit: Any | None = None,
    ) -> int:
        self._ensure_timeline_sequence_counter(session_id)
        normalized_explicit = self._normalize_timeline_sequence_value(explicit)
        if normalized_explicit is not None:
            self._timeline_sequence_counters[session_id] = max(
                self._timeline_sequence_counters[session_id],
                normalized_explicit + 1,
            )
            return normalized_explicit

        next_sequence = self._timeline_sequence_counters[session_id]
        self._timeline_sequence_counters[session_id] = next_sequence + 1
        return next_sequence

    def _annotate_timeline_sequence(
        self,
        event: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return data

        if event == "assistant_message":
            sequence = self._reserve_timeline_sequence(
                session_id,
                explicit=data.get("sequence"),
            )
            data["sequence"] = sequence
            return data

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
            "goal_loop_started",
            "goal_loop_iteration_started",
            "goal_loop_iteration_finished",
            "goal_loop_paused",
            "goal_loop_blocked",
            "goal_loop_completed",
            "goal_loop_budget_exhausted",
        }:
            return data

        sequence = self._reserve_timeline_sequence(
            session_id,
            explicit=data.get("sequence"),
        )
        data["sequence"] = sequence
        return data

    def get_session_metadata(
        self,
        session_id: str,
        *,
        include_conversation_state: bool = False,
    ) -> dict[str, Any]:
        payload = self.session_metadata_store.load(session_id) or {}
        payload = self._migrate_legacy_session_conversation_state(session_id, payload)
        normalized = self._normalize_session_metadata_payload(payload)
        if not include_conversation_state:
            return normalized

        conversation_state = self.session_conversation_state_store.load(session_id)
        if not isinstance(conversation_state, dict):
            return normalized

        return {
            **normalized,
            "backend_state": {
                **normalized["backend_state"],
                "ipc_conversation_state": conversation_state,
            },
        }

    def _migrate_legacy_session_conversation_state(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        backend_state = payload.get("backend_state")
        if not isinstance(backend_state, dict) or "ipc_conversation_state" not in backend_state:
            return payload

        next_backend_state = dict(backend_state)
        conversation_state = next_backend_state.pop("ipc_conversation_state", None)
        if isinstance(conversation_state, dict):
            self.session_conversation_state_store.save(session_id, conversation_state)
        elif conversation_state is None:
            self.session_conversation_state_store.delete(session_id)

        next_payload = {
            **payload,
            "backend_state": next_backend_state,
        }
        self.session_metadata_store.save(session_id, next_payload)
        return next_payload

    def ensure_session_metadata(
        self,
        session_id: str,
        source: str = "chat",
        channel_meta: dict[str, Any] | None = None,
        backend_id: str | None = None,
        project_path: str | None = None,
        backend_state: dict[str, Any] | None = None,
        codex_work_mode: CodexWorkMode | None = None,
        codex_goal_loop: dict[str, Any] | CodexGoalLoopState | None = None,
        title: str | None = None,
        preview: str | None = None,
        updated_at: float | None = None,
    ) -> None:
        existing = self.get_session_metadata(session_id, include_conversation_state=True)
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
        existing_backend_state = dict(existing.get("backend_state", {}))
        existing_conversation_state = existing_backend_state.pop("ipc_conversation_state", None)
        conversation_state_provided = isinstance(backend_state, dict) and "ipc_conversation_state" in backend_state
        next_backend_state = (
            dict(backend_state)
            if isinstance(backend_state, dict)
            else existing_backend_state
        )
        next_conversation_state = next_backend_state.pop(
            "ipc_conversation_state",
            existing_conversation_state,
        )
        payload = {
            "session_id": session_id,
            "source": normalized_source,
            "backend_id": backend_id or existing["backend_id"] or default_backend_id,
            "project_path": self.config_service.resolve_project_path(
                project_path or existing["project_path"] or default_project_path
            ),
            "channel_meta": channel_meta if isinstance(channel_meta, dict) else existing.get("channel_meta"),
            "backend_state": next_backend_state,
            "codex_work_mode": (
                codex_work_mode
                if codex_work_mode in {"plan", "build"}
                else existing.get("codex_work_mode")
            ),
            "codex_goal_loop": (
                self._normalize_codex_goal_loop_payload(
                    codex_goal_loop,
                    backend_id=backend_id or existing["backend_id"] or default_backend_id,
                )
                if codex_goal_loop is not None
                else existing.get("codex_goal_loop")
            ),
            "title": title if isinstance(title, str) else existing.get("title"),
            "preview": preview if isinstance(preview, str) else existing.get("preview"),
            "updated_at": updated_at if isinstance(updated_at, (int, float)) else existing.get("updated_at"),
        }
        if payload["backend_id"] == "codex" and payload["codex_work_mode"] not in {"plan", "build"}:
            payload["codex_work_mode"] = "build"
        payload["codex_goal_loop"] = self._normalize_codex_goal_loop_payload(
            payload.get("codex_goal_loop"),
            backend_id=payload["backend_id"],
        )
        self.session_metadata_store.save(session_id, payload)
        if isinstance(next_conversation_state, dict):
            self.session_conversation_state_store.save(session_id, next_conversation_state)
        elif conversation_state_provided:
            self.session_conversation_state_store.delete(session_id)

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
        metadata = self.get_session_metadata(session_id, include_conversation_state=True)
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

    def _normalize_codex_goal_loop_payload(
        self,
        value: dict[str, Any] | CodexGoalLoopState | None,
        *,
        backend_id: str,
    ) -> dict[str, Any] | None:
        if backend_id != "codex":
            return None

        payload: dict[str, Any]
        if isinstance(value, CodexGoalLoopState):
            payload = value.model_dump(mode="json")
        elif isinstance(value, dict):
            payload = value
        else:
            payload = {}

        return CodexGoalLoopState.model_validate(
            {
                **_default_goal_loop_state_payload(),
                **payload,
            }
        ).model_dump(mode="json")

    def get_codex_goal_loop_state(self, session_id: str) -> CodexGoalLoopState | None:
        metadata = self.get_session_metadata(session_id)
        payload = metadata.get("codex_goal_loop")
        if not isinstance(payload, dict):
            return None
        return CodexGoalLoopState.model_validate(payload)

    def _save_codex_goal_loop_state(
        self,
        session_id: str,
        state: CodexGoalLoopState,
    ) -> CodexGoalLoopState | None:
        metadata = self.get_session_metadata(session_id)
        if metadata["backend_id"] != "codex" or metadata["source"] == "channel":
            return None

        self.ensure_session_metadata(
            session_id,
            source=metadata["source"],
            channel_meta=metadata["channel_meta"],
            backend_id=metadata["backend_id"],
            project_path=metadata["project_path"],
            backend_state=metadata["backend_state"],
            codex_work_mode=metadata["codex_work_mode"],
            codex_goal_loop=state,
            title=metadata["title"],
            preview=metadata["preview"],
            updated_at=time(),
        )
        return state

    def _resolve_codex_tool_project_path(
        self,
        caller_session_id: str,
        project_path: str | None,
    ) -> Path:
        caller_metadata = self.get_session_metadata(caller_session_id)
        resolved_project_path = self.config_service.resolve_project_path(
            project_path or caller_metadata["project_path"]
        )
        return Path(resolved_project_path).resolve()

    async def start_codex_background_session_from_tool(
        self,
        *,
        caller_session_id: str,
        prompt: str,
        project_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Prompt must not be empty.")

        caller_metadata = self.get_session_metadata(caller_session_id)
        resolved_project_path = self._resolve_codex_tool_project_path(
            caller_session_id,
            project_path,
        )
        backend = self.backends.get("codex")
        if not isinstance(backend, CodexAppServerBackend):
            raise RuntimeError("Codex backend is not available.")

        bootstrap = backend.bootstrap_session(
            resolved_project_path,
            source=caller_metadata["source"],
            channel_meta=caller_metadata["channel_meta"],
        )
        session_id = str(bootstrap["thread_id"])
        self.ensure_session_metadata(
            session_id,
            source=caller_metadata["source"],
            channel_meta=caller_metadata["channel_meta"],
            backend_id="codex",
            project_path=str(resolved_project_path),
            backend_state={
                "thread_id": bootstrap["thread_id"],
                "status": bootstrap["status"],
                "active_flags": bootstrap["active_flags"],
                "detail": bootstrap["detail"],
            },
            codex_work_mode="build",
        )
        start_response = await self.start_codex_turn_in_background(session_id, normalized_prompt)
        turn = start_response.get("turn")
        if not isinstance(turn, dict):
            raise RuntimeError("Codex start turn response did not include a turn payload.")
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RuntimeError("Codex start turn response did not include a turn id.")

        metadata = self.get_session_metadata(session_id)
        return {
            "session_id": session_id,
            "thread_id": str(metadata["backend_state"].get("thread_id") or session_id),
            "turn_id": turn_id,
            "project_path": metadata["project_path"],
            "status": str(metadata["backend_state"].get("status") or "active"),
        }

    async def resume_codex_background_session_from_tool(
        self,
        *,
        caller_session_id: str,
        thread_id: str,
        prompt: str,
        project_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_thread_id = thread_id.strip()
        if not normalized_thread_id:
            raise ValueError("Thread id must not be empty.")
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Prompt must not be empty.")

        caller_metadata = self.get_session_metadata(caller_session_id)
        resolved_project_path = self._resolve_codex_tool_project_path(
            caller_session_id,
            project_path,
        )
        existing_metadata = self.session_metadata_store.load(normalized_thread_id)
        if isinstance(existing_metadata, dict):
            normalized = self.get_session_metadata(normalized_thread_id)
            if normalized["backend_id"] != "codex":
                raise RuntimeError("Existing session id is not backed by Codex.")
            self.ensure_session_metadata(
                normalized_thread_id,
                source=caller_metadata["source"],
                channel_meta=caller_metadata["channel_meta"],
                backend_id="codex",
                project_path=str(resolved_project_path),
                backend_state={
                    **normalized["backend_state"],
                    "thread_id": normalized_thread_id,
                },
                codex_work_mode="build",
                title=normalized["title"],
                preview=normalized["preview"],
                updated_at=normalized["updated_at"],
            )
        else:
            self.ensure_session_metadata(
                normalized_thread_id,
                source=caller_metadata["source"],
                channel_meta=caller_metadata["channel_meta"],
                backend_id="codex",
                project_path=str(resolved_project_path),
                backend_state={"thread_id": normalized_thread_id},
                codex_work_mode="build",
            )

        start_response = await self.start_codex_turn_in_background(
            normalized_thread_id,
            normalized_prompt,
        )
        turn = start_response.get("turn")
        if not isinstance(turn, dict):
            raise RuntimeError("Codex start turn response did not include a turn payload.")
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RuntimeError("Codex start turn response did not include a turn id.")

        metadata = self.get_session_metadata(normalized_thread_id)
        return {
            "session_id": normalized_thread_id,
            "thread_id": str(
                metadata["backend_state"].get("thread_id") or normalized_thread_id
            ),
            "turn_id": turn_id,
            "project_path": metadata["project_path"],
            "status": str(metadata["backend_state"].get("status") or "active"),
        }

    def can_handle_codex_conversation(self, conversation_id: str) -> bool:
        normalized_conversation_id = conversation_id.strip()
        if not normalized_conversation_id:
            return False
        metadata = self.get_session_metadata(normalized_conversation_id)
        if metadata["backend_id"] == "codex":
            return True
        return self.codex_workspace.get_active_session(normalized_conversation_id) is not None

    def ensure_codex_conversation_session(self, conversation_id: str) -> str:
        normalized_conversation_id = conversation_id.strip()
        if not normalized_conversation_id:
            raise RuntimeError("Missing conversation id.")

        metadata = self.session_metadata_store.load(normalized_conversation_id)
        if isinstance(metadata, dict):
            normalized = self.get_session_metadata(normalized_conversation_id)
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
            "codex_goal_loop": self._normalize_codex_goal_loop_payload(
                payload.get("codex_goal_loop"),
                backend_id=backend_id,
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
        transcript_messages = self.get_session_messages(session_id)
        message_sequences = self.session_ui_store.load_transcript_message_sequences(session_id)
        return [
            StoredSessionMessage(
                role=message.role,
                content=message.content,
                reasoning_content=message.reasoning_content,
                tool_call_id=message.tool_call_id,
                sequence=(
                    message_sequences[index]
                    if index < len(message_sequences)
                    else None
                ),
                source=session_meta["source"],
                channel_meta=channel_meta_payload,
            )
            for index, message in enumerate(transcript_messages)
        ]

    def get_session_activity_events(self, session_id: str) -> list[dict[str, Any]]:
        return self.session_ui_store.load_activity_events(session_id)

    def get_session_activity_page(
        self,
        session_id: str,
        *,
        before: int | None = None,
        limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int | None]]:
        return self.session_ui_store.load_activity_page(
            session_id,
            before=before,
            limit=limit,
        )

    def get_codex_workspace(self) -> CodexWorkspaceResponse:
        workspace = self.codex_workspace.load_workspace()
        projects = []
        for project in workspace.projects:
            sessions = []
            for session in project.sessions:
                metadata = self.session_metadata_store.load(session.thread_id) or {}
                goal_loop_payload = metadata.get("codex_goal_loop")
                sessions.append(
                    session.model_copy(
                        update={
                            "codex_goal_loop": (
                                CodexGoalLoopState.model_validate(goal_loop_payload)
                                if isinstance(goal_loop_payload, dict)
                                else None
                            )
                        }
                    )
                )
            projects.append(project.model_copy(update={"sessions": sessions}))
        return workspace.model_copy(update={"projects": projects})

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

    def update_codex_goal_loop(
        self,
        session_id: str,
        *,
        goal: str,
        definition_of_done: str,
    ) -> CodexGoalLoopState | None:
        metadata = self.get_session_metadata(session_id)
        if metadata["backend_id"] != "codex" or metadata["source"] == "channel":
            return None

        current_state = self.get_codex_goal_loop_state(session_id) or CodexGoalLoopState()
        next_status: CodexGoalLoopStatus = current_state.status
        if not goal and not definition_of_done:
            next_status = "idle" if current_state.status != "running" else current_state.status

        next_state = current_state.model_copy(
            update={
                "goal": goal,
                "definition_of_done": definition_of_done,
                "status": next_status,
                "updated_at": time(),
            }
        )
        saved = self._save_codex_goal_loop_state(session_id, next_state)
        return saved

    async def apply_codex_goal_loop_action(
        self,
        session_id: str,
        *,
        action: str,
    ) -> CodexGoalLoopState | None:
        metadata = self.get_session_metadata(session_id)
        if metadata["backend_id"] != "codex" or metadata["source"] == "channel":
            return None

        state = self.get_codex_goal_loop_state(session_id) or CodexGoalLoopState()
        if action == "clear":
            next_state = CodexGoalLoopState(updated_at=time())
            return self._save_codex_goal_loop_state(session_id, next_state)
        if action == "complete":
            next_state = state.model_copy(
                update={
                    "status": "completed",
                    "completed_at": time(),
                    "updated_at": time(),
                    "last_reason": "Marked complete by user.",
                    "last_background_session_id": None,
                }
            )
            saved = self._save_codex_goal_loop_state(session_id, next_state)
            if saved is not None:
                await self._publish_goal_loop_event(
                    "goal_loop_completed",
                    {
                        "session_id": session_id,
                        "status": saved.status,
                        "goal": saved.goal,
                        "iteration_count": saved.iteration_count,
                        "max_iterations": saved.max_iterations,
                        "last_reason": saved.last_reason,
                    },
                )
            return saved
        if action == "pause":
            next_state = state.model_copy(
                update={
                    "status": "paused",
                    "updated_at": time(),
                    "last_reason": "Paused by user.",
                    "last_background_session_id": None,
                }
            )
            saved = self._save_codex_goal_loop_state(session_id, next_state)
            if saved is not None:
                await self._publish_goal_loop_event(
                    "goal_loop_paused",
                    {
                        "session_id": session_id,
                        "status": saved.status,
                        "goal": saved.goal,
                        "iteration_count": saved.iteration_count,
                        "max_iterations": saved.max_iterations,
                        "last_reason": saved.last_reason,
                    },
                )
            return saved
        if action in {"start", "resume"}:
            if not state.goal or not state.definition_of_done:
                raise ValueError("Goal and definition of done are required before starting.")
            next_state = state.model_copy(
                update={
                    "status": "running",
                    "updated_at": time(),
                    "started_at": state.started_at or time(),
                    "completed_at": None,
                    "last_reason": (
                        "Goal loop started." if action == "start" else "Goal loop resumed."
                    ),
                }
            )
            saved = self._save_codex_goal_loop_state(session_id, next_state)
            if saved is None:
                return None
            self.update_codex_session_mode(session_id, "build")
            saved = await self._launch_goal_loop_iteration(
                session_id,
                reason="start" if action == "start" else "resume",
            )
            return saved
        raise ValueError(f"Unsupported Codex goal loop action: {action}")

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
                    codex_goal_loop=(
                        CodexGoalLoopState.model_validate(session_meta["codex_goal_loop"])
                        if isinstance(session_meta.get("codex_goal_loop"), dict)
                        else None
                    ),
                )
            )

        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def load_session_transcript(
        self,
        session_id: str,
        *,
        activity_limit: int | None = None,
    ) -> SessionTranscriptView:
        native_view = self._native_codex_session_view(
            session_id,
            activity_limit=activity_limit,
        )
        if native_view is not None:
            return native_view

        messages = self.build_transcript_messages(session_id)
        activity_events, activity_history = self.get_session_activity_page(
            session_id,
            limit=activity_limit,
        )
        return SessionTranscriptView(
            messages=messages,
            activity_events=activity_events,
            activity_history=activity_history,
            codex_turn_timings=self._fallback_codex_turn_timings(session_id),
        )

    def load_session_view(
        self,
        session_id: str,
    ) -> tuple[list[StoredSessionMessage], list[dict[str, Any]]]:
        transcript = self.load_session_transcript(session_id)
        return (transcript.messages, transcript.activity_events)

    def _codex_turn_timings_from_turns(
        self,
        turns: Any,
    ) -> list[dict[str, int | str | None]]:
        if not isinstance(turns, list):
            return []
        codex_turn_timings: list[dict[str, int | str | None]] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            turn_started_at_ms = turn.get("turnStartedAtMs")
            final_started_at_ms = turn.get("finalAssistantStartedAtMs")
            codex_turn_timings.append(
                {
                    "turn_id": str(turn.get("turnId") or turn.get("id") or ""),
                    "turn_started_at_ms": (
                        int(turn_started_at_ms)
                        if isinstance(turn_started_at_ms, (int, float))
                        else None
                    ),
                    "final_assistant_started_at_ms": (
                        int(final_started_at_ms)
                        if isinstance(final_started_at_ms, (int, float))
                        else None
                    ),
                }
            )
        return codex_turn_timings

    def _fallback_codex_turn_timings(
        self,
        session_id: str,
    ) -> list[dict[str, int | str | None]]:
        metadata = self.get_session_metadata(session_id)
        if metadata["backend_id"] != "codex":
            return []
        conversation_state = self.build_codex_ipc_conversation_state(session_id)
        return self._codex_turn_timings_from_turns(conversation_state.get("turns"))

    def _native_codex_session_view(
        self,
        session_id: str,
        *,
        activity_limit: int | None = None,
    ) -> SessionTranscriptView | None:
        context = self.get_session_context(session_id)
        thread_id = context.backend_state.get("thread_id")
        if context.backend_id != "codex" or not isinstance(thread_id, str) or not thread_id:
            return None

        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            return None

        if backend.should_use_local_session_view(context):
            activity_events, activity_history = self.get_session_activity_page(
                session_id,
                limit=activity_limit,
            )
            return SessionTranscriptView(
                messages=self.build_transcript_messages(session_id),
                activity_events=activity_events,
                activity_history=activity_history,
                codex_turn_timings=self._fallback_codex_turn_timings(session_id),
            )

        cached_ipc_view = self._cached_codex_ipc_session_view(
            session_id,
            activity_limit=activity_limit,
        )
        if cached_ipc_view is not None:
            return cached_ipc_view

        view = backend.load_thread_view(context, activity_limit=activity_limit)
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
        return SessionTranscriptView(
            messages=view["messages"],
            activity_events=view["activity_events"],
            activity_history=view["activity_history"],
            codex_turn_timings=view.get("codex_turn_timings", []),
        )

    def _cached_codex_ipc_session_view(
        self,
        session_id: str,
        *,
        activity_limit: int | None = None,
    ) -> SessionTranscriptView | None:
        context = self.get_session_context(session_id)
        if context.backend_id != "codex":
            return None

        metadata = self.get_session_metadata(
            session_id,
            include_conversation_state=True,
        )
        backend_state = metadata["backend_state"]
        conversation_state = backend_state.get("ipc_conversation_state")
        if not isinstance(conversation_state, dict):
            return None

        backend = self.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            return None

        view = backend.ipc_conversation_view(
            context,
            conversation_state,
            activity_limit=activity_limit,
        )
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
        return SessionTranscriptView(
            messages=view["messages"],
            activity_events=view["activity_events"],
            activity_history=view["activity_history"],
            codex_turn_timings=view.get("codex_turn_timings", []),
        )

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
        deleted = self.session_conversation_state_store.delete(session_id) or deleted
        self._timeline_sequence_counters.pop(session_id, None)

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
        self._annotate_timeline_sequence(event, data)
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
            goal_loop_state = self.get_codex_goal_loop_state(session_id)
            if context.backend_id == "codex" and goal_loop_state is not None and goal_loop_state.status == "running":
                paused_state = goal_loop_state.model_copy(
                    update={
                        "status": "paused",
                        "updated_at": time(),
                        "last_reason": "Paused because the user sent a manual message.",
                        "last_background_session_id": None,
                    }
                )
                self._save_codex_goal_loop_state(session_id, paused_state)
                await emit(
                    "goal_loop_paused",
                    {
                        "session_id": session_id,
                        "status": paused_state.status,
                        "goal": paused_state.goal,
                        "iteration_count": paused_state.iteration_count,
                        "max_iterations": paused_state.max_iterations,
                        "last_reason": paused_state.last_reason,
                    },
                )
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

    async def _publish_goal_loop_event(self, event: str, data: dict[str, Any]) -> None:
        self._annotate_timeline_sequence(event, data)
        self._persist_ui_event(event, data)
        await self.event_broker.publish(event, data)
        await self.codex_ipc_bridge.notify_stream_event(event, data)

    def _goal_loop_owner_session_id(self, background_session_id: str) -> str | None:
        owner_session_id = self._background_owner_sessions.get(background_session_id)
        if isinstance(owner_session_id, str) and owner_session_id:
            return owner_session_id
        return None

    def _build_goal_loop_control_prompt(
        self,
        session_id: str,
        state: CodexGoalLoopState,
        *,
        next_prompt: str | None = None,
    ) -> str:
        metadata = self.get_session_metadata(session_id)
        remaining_iterations = max(0, state.max_iterations - state.iteration_count)
        thread_id = str(metadata["backend_state"].get("thread_id") or session_id)
        next_prompt_block = (
            f"Immediate next focus:\n{next_prompt}\n\n"
            if isinstance(next_prompt, str) and next_prompt.strip()
            else ""
        )
        return (
            "You are continuing a persistent goal loop inside the current Codex thread.\n"
            f"Goal:\n{state.goal}\n\n"
            f"Definition of done:\n{state.definition_of_done}\n\n"
            f"{next_prompt_block}"
            f"Current iteration: {state.iteration_count + 1}\n"
            f"Remaining iteration budget after this turn: {max(0, remaining_iterations - 1)}\n"
            f"Thread id: {thread_id}\n\n"
            "Work on the goal directly in this repository. Use tools and make progress. "
            "Do not stop just because one step completed. Only mark completed when the "
            "definition of done is truly satisfied. Mark blocked only when you need human "
            "input, approval, or a hard blocker prevents safe continuation.\n\n"
            "At the very end of your response, emit exactly these markers on their own lines:\n"
            "YIER_GOAL_LOOP_STATUS: continue|completed|blocked\n"
            "YIER_GOAL_LOOP_REASON: <brief explanation>\n"
            "YIER_GOAL_LOOP_NEXT_PROMPT: <next concrete continuation prompt if status is continue>\n"
        )

    async def _launch_goal_loop_iteration(
        self,
        session_id: str,
        *,
        reason: str,
        next_prompt: str | None = None,
    ) -> CodexGoalLoopState:
        state = self.get_codex_goal_loop_state(session_id)
        if state is None:
            raise RuntimeError("Codex goal loop is not available for this session.")
        if not state.goal or not state.definition_of_done:
            raise RuntimeError("Goal and definition of done are required before running.")

        metadata = self.get_session_metadata(session_id)
        project_path = Path(metadata["project_path"]).resolve()
        thread_id = str(metadata["backend_state"].get("thread_id") or session_id)
        request_path = _write_codex_background_request(
            self,
            {
                "action": "resume",
                "caller_session_id": session_id,
                "thread_id": thread_id,
                "prompt": self._build_goal_loop_control_prompt(
                    session_id,
                    state,
                    next_prompt=next_prompt,
                ),
                "project_path": str(project_path),
            },
        )
        command = _build_codex_background_runner_command(self, request_path=request_path)
        background_session = await self.background_manager.start(command, str(project_path))
        background_started_payload = {
            "session_id": session_id,
            "tool_call_id": f"goal-loop:{background_session.session_id}",
            "tool_name": "resume_codex_background_session",
            "background_session_id": background_session.session_id,
            "command": background_session.command,
            "cwd": str(background_session.cwd),
            "state": background_session.state,
        }
        self._handle_internal_event("background_command_started", background_started_payload)
        self._persist_ui_event("background_command_started", background_started_payload)
        await self.event_broker.publish("background_command_started", background_started_payload)
        next_state = state.model_copy(
            update={
                "status": "running",
                "iteration_count": state.iteration_count + 1,
                "last_background_session_id": background_session.session_id,
                "last_reason": (
                    "Running goal loop iteration." if reason == "continue" else state.last_reason
                ),
                "updated_at": time(),
            }
        )
        saved = self._save_codex_goal_loop_state(session_id, next_state)
        if saved is None:
            raise RuntimeError("Failed to persist Codex goal loop state.")
        await self._publish_goal_loop_event(
            "goal_loop_iteration_started",
            {
                "session_id": session_id,
                "status": saved.status,
                "goal": saved.goal,
                "iteration_count": saved.iteration_count,
                "max_iterations": saved.max_iterations,
                "last_reason": saved.last_reason,
                "background_session_id": background_session.session_id,
            },
        )
        if reason in {"start", "resume"}:
            await self._publish_goal_loop_event(
                "goal_loop_started",
                {
                    "session_id": session_id,
                    "status": saved.status,
                    "goal": saved.goal,
                    "iteration_count": saved.iteration_count,
                    "max_iterations": saved.max_iterations,
                    "last_reason": saved.last_reason,
                    "background_session_id": background_session.session_id,
                },
            )
        return saved

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
        return self.codex_conversation_state.build_conversation_state(session_id)

    def apply_codex_ipc_stream_change(
        self,
        session_id: str,
        change: dict[str, Any],
    ) -> None:
        self.codex_conversation_state.apply_stream_change(session_id, change)

    def build_codex_ipc_queued_followups(self, session_id: str) -> list[dict[str, Any]]:
        return self.codex_conversation_state.build_queued_followups(session_id)

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
                    message_sequence = self._append_transcript_message(
                        session_id,
                        Message(role="assistant", content=event.message.content),
                    )
                    await emit(
                        "assistant_message",
                        {
                            "session_id": session_id,
                            "content": event.message.content,
                            "iteration": event.iteration,
                            "sequence": message_sequence,
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

    def _append_transcript_message(
        self,
        session_id: str,
        message: Message,
        *,
        sequence: int | None = None,
    ) -> int:
        message_sequence = self._reserve_timeline_sequence(
            session_id,
            explicit=sequence,
        )
        messages = self.transcript_store.get_session_messages(session_id) or []
        messages.append(message)
        self.transcript_store.save(session_id, messages)
        self.session_ui_store.append_transcript_message_sequence(
            session_id,
            message_sequence,
        )
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
        return message_sequence

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
            "goal_loop_started",
            "goal_loop_iteration_started",
            "goal_loop_iteration_finished",
            "goal_loop_paused",
            "goal_loop_blocked",
            "goal_loop_completed",
            "goal_loop_budget_exhausted",
        }:
            return

        self._annotate_timeline_sequence(event, data)
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
                await self._process_goal_loop_background_output(
                    owner_session_id=owner_session_id,
                    background_session_id=session.session_id,
                    content=new_content,
                    completed=False,
                )

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
            await self._process_goal_loop_background_output(
                owner_session_id=owner_session_id,
                background_session_id=session.session_id,
                content="",
                completed=True,
            )

        return completed_session_ids

    def _parse_background_json_lines(
        self,
        background_session_id: str,
        chunk: str,
    ) -> list[dict[str, Any]]:
        if not chunk:
            return []
        cursor = self._background_cursors.setdefault(
            background_session_id,
            {
                "stdout_chars": 0,
                "stderr_chars": 0,
                "end_emitted": False,
                "stdout_json_remainder": "",
            },
        )
        remainder = str(cursor.get("stdout_json_remainder") or "")
        buffer = f"{remainder}{chunk}"
        lines = buffer.splitlines(keepends=False)
        if buffer and not buffer.endswith("\n"):
            cursor["stdout_json_remainder"] = lines.pop() if lines else buffer
        else:
            cursor["stdout_json_remainder"] = ""

        events: list[dict[str, Any]] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def _parse_background_json_text(self, text: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def _background_result_payload(
        self,
        background_session_id: str,
    ) -> dict[str, Any] | None:
        try:
            session = self.background_manager.require_session(background_session_id)
        except (KeyError, ValueError):
            return None
        payloads = self._parse_background_json_text(session.stdout_buffer.render())
        for payload in reversed(payloads):
            if payload.get("event") == "codex_background_result":
                result = payload.get("result")
                if isinstance(result, dict):
                    return result
        return None

    async def _process_goal_loop_background_output(
        self,
        *,
        owner_session_id: str,
        background_session_id: str,
        content: str,
        completed: bool,
    ) -> None:
        state = self.get_codex_goal_loop_state(owner_session_id)
        if state is None or state.last_background_session_id != background_session_id:
            return

        if not completed:
            for payload in self._parse_background_json_lines(background_session_id, content):
                if payload.get("event") != "codex_background_stream_event":
                    continue
                if payload.get("source_event") != "approval_requested":
                    continue
                next_state = state.model_copy(
                    update={
                        "status": "blocked",
                        "last_reason": "Waiting on approval before the goal loop can continue.",
                        "last_background_session_id": None,
                        "updated_at": time(),
                    }
                )
                saved = self._save_codex_goal_loop_state(owner_session_id, next_state)
                if saved is None:
                    return
                await self.background_manager.stop(background_session_id)
                await self._publish_goal_loop_event(
                    "goal_loop_blocked",
                    {
                        "session_id": owner_session_id,
                        "status": saved.status,
                        "goal": saved.goal,
                        "iteration_count": saved.iteration_count,
                        "max_iterations": saved.max_iterations,
                        "last_reason": saved.last_reason,
                        "background_session_id": background_session_id,
                    },
                )
                return
            return

        await self._finalize_goal_loop_iteration(owner_session_id, background_session_id)

    async def _finalize_goal_loop_iteration(
        self,
        session_id: str,
        background_session_id: str,
    ) -> None:
        state = self.get_codex_goal_loop_state(session_id)
        if state is None or state.last_background_session_id != background_session_id:
            return

        result_payload = self._background_result_payload(background_session_id)
        latest_assistant_message = ""
        if isinstance(result_payload, dict):
            latest_value = result_payload.get("latest_assistant_message")
            if isinstance(latest_value, str):
                latest_assistant_message = latest_value

        markers = _extract_goal_loop_markers(latest_assistant_message)
        marker_status = markers.get("status", "").lower()
        marker_reason = markers.get("reason") or "Goal loop iteration finished."
        marker_next_prompt = markers.get("next_prompt") or ""

        await self._publish_goal_loop_event(
            "goal_loop_iteration_finished",
            {
                "session_id": session_id,
                "status": marker_status or state.status,
                "goal": state.goal,
                "iteration_count": state.iteration_count,
                "max_iterations": state.max_iterations,
                "last_reason": marker_reason,
                "background_session_id": background_session_id,
            },
        )

        if marker_status == "completed":
            next_state = state.model_copy(
                update={
                    "status": "completed",
                    "consecutive_failures": 0,
                    "completed_at": time(),
                    "updated_at": time(),
                    "last_reason": marker_reason,
                    "last_background_session_id": None,
                }
            )
            saved = self._save_codex_goal_loop_state(session_id, next_state)
            if saved is not None:
                await self._publish_goal_loop_event(
                    "goal_loop_completed",
                    {
                        "session_id": session_id,
                        "status": saved.status,
                        "goal": saved.goal,
                        "iteration_count": saved.iteration_count,
                        "max_iterations": saved.max_iterations,
                        "last_reason": saved.last_reason,
                        "background_session_id": background_session_id,
                    },
                )
            return

        if marker_status == "blocked":
            next_state = state.model_copy(
                update={
                    "status": "blocked",
                    "consecutive_failures": 0,
                    "updated_at": time(),
                    "last_reason": marker_reason,
                    "last_background_session_id": None,
                }
            )
            saved = self._save_codex_goal_loop_state(session_id, next_state)
            if saved is not None:
                await self._publish_goal_loop_event(
                    "goal_loop_blocked",
                    {
                        "session_id": session_id,
                        "status": saved.status,
                        "goal": saved.goal,
                        "iteration_count": saved.iteration_count,
                        "max_iterations": saved.max_iterations,
                        "last_reason": saved.last_reason,
                        "background_session_id": background_session_id,
                    },
                )
            return

        if marker_status == "continue" and marker_next_prompt:
            if state.iteration_count >= state.max_iterations:
                next_state = state.model_copy(
                    update={
                        "status": "paused",
                        "updated_at": time(),
                        "last_reason": "Iteration budget exhausted. Review progress and resume if needed.",
                        "last_background_session_id": None,
                    }
                )
                saved = self._save_codex_goal_loop_state(session_id, next_state)
                if saved is not None:
                    await self._publish_goal_loop_event(
                        "goal_loop_budget_exhausted",
                        {
                            "session_id": session_id,
                            "status": saved.status,
                            "goal": saved.goal,
                            "iteration_count": saved.iteration_count,
                            "max_iterations": saved.max_iterations,
                            "last_reason": saved.last_reason,
                            "background_session_id": background_session_id,
                        },
                    )
                return

            next_state = state.model_copy(
                update={
                    "status": "running",
                    "consecutive_failures": 0,
                    "updated_at": time(),
                    "last_reason": marker_reason,
                    "last_background_session_id": None,
                }
            )
            saved = self._save_codex_goal_loop_state(session_id, next_state)
            if saved is None:
                return
            await self._launch_goal_loop_iteration(
                session_id,
                reason="continue",
                next_prompt=marker_next_prompt,
            )
            return

        failure_count = state.consecutive_failures + 1
        failure_reason = (
            marker_reason
            if marker_status
            else "Goal loop markers were missing or invalid, so the loop was paused."
        )
        failure_status: CodexGoalLoopStatus = (
            "failed"
            if failure_count >= state.max_consecutive_failures
            else "paused"
        )
        next_state = state.model_copy(
            update={
                "status": failure_status,
                "consecutive_failures": failure_count,
                "updated_at": time(),
                "last_reason": failure_reason,
                "last_background_session_id": None,
            }
        )
        saved = self._save_codex_goal_loop_state(session_id, next_state)
        if saved is not None:
            await self._publish_goal_loop_event(
                "goal_loop_paused",
                {
                    "session_id": session_id,
                    "status": saved.status,
                    "goal": saved.goal,
                    "iteration_count": saved.iteration_count,
                    "max_iterations": saved.max_iterations,
                    "last_reason": saved.last_reason,
                    "background_session_id": background_session_id,
                },
            )

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
            create_find_codex_projects_tool(self),
            create_find_codex_sessions_tool(self),
            create_start_codex_background_session_tool(self, self.background_manager),
            create_resume_codex_background_session_tool(self, self.background_manager),
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
