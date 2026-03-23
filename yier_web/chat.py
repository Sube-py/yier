from __future__ import annotations

import asyncio
from contextlib import suppress
import json
from pathlib import Path
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

from yier_web.background_followups import FollowupQueueManager, create_queue_background_followup_tool
from yier_web.config import AppConfigService
from yier_web.event_stream import EventStreamBroker
from yier_web.session_metadata_store import SessionMetadataStore
from yier_web.session_ui_store import SessionUIStore
from yier_web.schemas import (
    ChannelMetaPayload,
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


class ChatService:
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
        self.background_manager = BackgroundCommandManager(
            default_root=self.project_root,
            allow_shell=True,
            shell_program="/bin/bash",
        )
        self.followup_queue = FollowupQueueManager()
        self.event_broker = event_broker or EventStreamBroker()
        self._agent: Agent | None = None
        self._agent_signature: tuple[Any, ...] | None = None
        self._lock = asyncio.Lock()
        self._session_run_locks: dict[str, asyncio.Lock] = {}
        self._background_owner_sessions: dict[str, str] = {}
        self._background_cursors: dict[str, dict[str, Any]] = {}
        self._background_supervisor_task: asyncio.Task[None] | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.mcp_manager.start()
        self._started = True
        await self.reload_agent(force_mcp_reconnect=False)
        self._background_supervisor_task = asyncio.create_task(self._background_supervisor_loop())

    async def stop(self) -> None:
        if not self._started:
            return
        if self._background_supervisor_task is not None:
            self._background_supervisor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._background_supervisor_task
            self._background_supervisor_task = None
        await self.background_manager.close()
        await self.mcp_manager.stop()
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

    def create_session(self) -> str:
        session_id = str(uuid4())
        self.ensure_session_metadata(session_id)
        return session_id

    def get_session_messages(self, session_id: str) -> list[Message]:
        transcript_messages = self.transcript_store.get_session_messages(session_id)
        if transcript_messages:
            return transcript_messages
        return self.session_store.get_session_messages(session_id) or []

    def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        payload = self.session_metadata_store.load(session_id) or {}
        source = payload.get("source")
        if source not in {"chat", "channel"}:
            source = "chat"
        channel_meta = payload.get("channel_meta")
        if not isinstance(channel_meta, dict):
            channel_meta = None
        return {
            "source": source,
            "channel_meta": channel_meta,
        }

    def ensure_session_metadata(
        self,
        session_id: str,
        source: str = "chat",
        channel_meta: dict[str, Any] | None = None,
    ) -> None:
        existing = self.session_metadata_store.load(session_id) or {}
        payload = {
            "session_id": session_id,
            "source": source if source in {"chat", "channel"} else "chat",
            "channel_meta": channel_meta if isinstance(channel_meta, dict) else existing.get("channel_meta"),
        }
        self.session_metadata_store.save(session_id, payload)

    def mark_channel_session(
        self,
        session_id: str,
        channel_meta: dict[str, Any],
    ) -> None:
        self.ensure_session_metadata(session_id, source="channel", channel_meta=channel_meta)

    def is_channel_session(self, session_id: str) -> bool:
        return self.get_session_metadata(session_id)["source"] == "channel"

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

    def list_session_summaries(self, source: str | None = None) -> list[SessionSummary]:
        session_entries: dict[str, dict[str, Any]] = {}

        for directory in (
            self.config_service.transcripts_path,
            self.config_service.sessions_path,
            self.config_service.session_ui_path,
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
            title = self._session_title(messages)
            preview = self._session_preview(messages)
            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    title=title,
                    preview=preview,
                    updated_at=float(entry["updated_at"]),
                    message_count=self._message_count(messages),
                    source=session_meta["source"],
                    channel_meta=(
                        ChannelMetaPayload.model_validate(session_meta["channel_meta"])
                        if isinstance(session_meta["channel_meta"], dict)
                        else None
                    ),
                )
            )

        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def delete_session(self, session_id: str) -> bool:
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
        return deleted

    async def stream_chat(self, session_id: str, user_message: str) -> AsyncIterator[dict[str, Any]]:
        agent = await self._get_agent()
        if agent is None:
            yield {
                "event": "error",
                "data": {
                    "session_id": session_id,
                    "message": "LLM configuration is incomplete. Update settings before chatting.",
                },
            }
            yield {"event": "done", "data": {"session_id": session_id, "finish_reason": "error"}}
            return

        event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        producer = asyncio.create_task(
            self._produce_stream_events(
                agent=agent,
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

    async def _produce_stream_events(
        self,
        agent: Agent,
        session_id: str,
        user_message: str,
        event_queue: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        async def emit(event: str, data: dict[str, Any]) -> None:
            self._handle_internal_event(event, data)
            self._persist_ui_event(event, data)
            await event_queue.put({"event": event, "data": data})

        finish_reason = "stop"
        token = set_tool_event_emitter(emit)

        try:
            self.ensure_session_metadata(session_id)
            self._append_transcript_message(
                session_id,
                Message(role="user", content=user_message),
            )
            await emit("run_started", {"session_id": session_id})
            finish_reason = await self._run_agent_prompt(
                agent=agent,
                session_id=session_id,
                prompt=user_message,
                emit=emit,
            )
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
        }:
            return

        self.session_ui_store.append_activity_event(session_id, event, data)

    def _handle_internal_event(self, event: str, data: dict[str, Any]) -> None:
        if event != "background_command_started":
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
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.model,
        )
