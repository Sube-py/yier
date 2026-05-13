from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import logging
from pathlib import Path
import shlex
from typing import Any, Callable

from codex_ipc import AppServerConfig, CodexIpcConfig, CodexIpcSession, JsonDict
from codex_app_server.generated.v2_all import (
    AbsolutePathBuf,
    ActiveThreadStatus,
    CustomSessionSource,
    IdleThreadStatus,
    NotLoadedThreadStatus,
    SessionSource,
    SessionSourceValue,
    SubAgentSessionSource,
    SystemErrorThreadStatus,
    Thread,
    ThreadListResponse,
    ThreadStatus,
)
from yier_web.config import AppConfigService
from yier_web.event_stream import EventStreamBroker
from yier_web.schemas import (
    CodexNativeSessionSummary,
    CodexProjectGroup,
    CodexWorkspaceResponse,
    StoredCodexSettings,
)

logger = logging.getLogger(__name__)

CodexSubscriberQueue = asyncio.Queue[dict[str, Any]]
CodexSessionFactory = Callable[..., CodexIpcSession]


@dataclass(slots=True)
class ManagedCodexThread:
    session: CodexIpcSession
    watcher_task: asyncio.Task[None]
    state: JsonDict | None = None


def _compact_text(value: object, *, limit: int = 72) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return ""
    compacted = " ".join(text.split())
    if len(compacted) <= limit:
        return compacted
    return f"{compacted[: limit - 3]}..."


def _thread_status(status: ThreadStatus) -> str:
    match status.root:
        case NotLoadedThreadStatus(type=status_type):
            return status_type
        case IdleThreadStatus(type=status_type):
            return status_type
        case SystemErrorThreadStatus(type=status_type):
            return status_type
        case ActiveThreadStatus(type=status_type):
            return status_type


def _thread_source(source: SessionSource) -> str:
    match source.root:
        case SessionSourceValue() as source_value:
            return source_value.value
        case CustomSessionSource(custom=custom):
            return custom
        case SubAgentSessionSource():
            return "subAgent"


def _project_from_cwd(cwd: AbsolutePathBuf) -> tuple[str, str]:
    path = Path(cwd.root).expanduser()
    project = path.name or cwd.root
    return (project, str(path))


def _summary_used_at(summary: CodexNativeSessionSummary) -> float:
    return summary.updated_at or summary.started_at


def _thread_summary(thread: Thread) -> CodexNativeSessionSummary:
    cwd = thread.cwd.root
    project, project_path = _project_from_cwd(thread.cwd)
    name = _compact_text(thread.name)
    preview = _compact_text(thread.preview, limit=120)
    title = name or preview or thread.id
    return CodexNativeSessionSummary(
        thread_id=thread.id,
        title=title,
        preview=preview or title,
        updated_at=float(thread.updated_at),
        started_at=float(thread.created_at),
        status=_thread_status(thread.status),
        cwd=cwd,
        project=project,
        project_path=project_path,
        source=_thread_source(thread.source),
    )


class CodexIpcManager:
    """Owns long-lived Codex IPC sessions for the web workspace.

    The manager keeps one ``CodexIpcSession`` per active thread so UI subscription
    changes do not interrupt ongoing turns.
    """

    def __init__(
        self,
        *,
        config_service: AppConfigService,
        event_broker: EventStreamBroker,
        session_factory: CodexSessionFactory = CodexIpcSession,
    ) -> None:
        self.config_service = config_service
        self.event_broker = event_broker
        self._session_factory = session_factory
        self._threads: dict[str, ManagedCodexThread] = {}
        self._subscribers: dict[str, set[CodexSubscriberQueue]] = {}
        self._workspace_session: CodexIpcSession | None = None
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False
        for managed in list(self._threads.values()):
            managed.watcher_task.cancel()
        for managed in list(self._threads.values()):
            with contextlib.suppress(asyncio.CancelledError):
                await managed.watcher_task
            await managed.session.stop()
        self._threads.clear()
        self._subscribers.clear()

        if self._workspace_session is not None:
            await self._workspace_session.stop()
            self._workspace_session = None

    async def workspace(self) -> CodexWorkspaceResponse:
        response = await self.list_threads()
        return self._workspace_from_threads(response)

    async def list_threads(self) -> ThreadListResponse:
        session = await self._ensure_workspace_session()
        return await session.list_threads(
            {
                "archived": False,
                "limit": 100,
                "sort_key": "updated_at",
                "sort_direction": "desc",
            }
        )

    async def start_thread(self, *, project_path: str | None = None) -> JsonDict:
        params = self._thread_start_params(project_path=project_path)
        session = self._new_session(self._config())
        await session.start()
        try:
            await session.start_new_thread(params)
            thread_id = session.thread_id
            if not thread_id:
                raise RuntimeError("Codex did not return a thread id.")
            await self._register_session(thread_id, session)
        except Exception:
            await session.stop()
            raise
        return {
            "thread_id": thread_id,
            "state": session.state,
        }

    async def open_thread(self, thread_id: str) -> JsonDict:
        managed = await self._ensure_thread(thread_id)
        return {
            "thread_id": managed.session.thread_id,
            "state": managed.state or managed.session.state,
        }

    async def get_thread_state(self, thread_id: str) -> JsonDict | None:
        managed = await self._ensure_thread(thread_id)
        return managed.state or managed.session.state

    async def subscribe(
        self,
        thread_id: str,
        queue: CodexSubscriberQueue,
    ) -> JsonDict | None:
        managed = await self._ensure_thread(thread_id)
        self._subscribers.setdefault(thread_id, set()).add(queue)
        state = managed.state or managed.session.state
        await queue.put(
            {
                "type": "thread_snapshot",
                "payload": {
                    "thread_id": thread_id,
                    "state": state,
                    "stream_role": managed.session.stream_role,
                    "queued_followups": managed.session.queued_followups,
                },
            }
        )
        return state

    def unsubscribe(self, thread_id: str, queue: CodexSubscriberQueue) -> None:
        subscribers = self._subscribers.get(thread_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(thread_id, None)

    async def send_prompt(
        self,
        thread_id: str,
        prompt: str,
        *,
        collaboration_mode: JsonDict | None = None,
    ) -> None:
        managed = await self._ensure_thread(thread_id)
        await managed.session.run_prompt(
            prompt.strip(),
            wait_for_completion=False,
            collaboration_mode=collaboration_mode,
        )

    async def steer_prompt(self, thread_id: str, prompt: str) -> None:
        managed = await self._ensure_thread(thread_id)
        await managed.session.steer_prompt(prompt.strip())

    async def interrupt_turn(self, thread_id: str, turn_id: str | None = None) -> bool:
        managed = await self._ensure_thread(thread_id)
        return await managed.session.interrupt_turn(turn_id)

    async def compact_thread(self, thread_id: str) -> bool:
        managed = await self._ensure_thread(thread_id)
        return await managed.session.compact_thread()

    async def set_collaboration_mode(
        self,
        thread_id: str,
        collaboration_mode: JsonDict | None,
    ) -> None:
        managed = await self._ensure_thread(thread_id)
        await managed.session.set_collaboration_mode(collaboration_mode)

    async def submit_user_input_response(
        self,
        thread_id: str,
        request_id: str,
        response: JsonDict,
    ) -> bool:
        managed = await self._ensure_thread(thread_id)
        return await managed.session.submit_user_input_response(request_id, response)

    async def enqueue_followup(self, thread_id: str, prompt: str) -> JsonDict:
        managed = await self._ensure_thread(thread_id)
        return await managed.session.enqueue_followup(prompt.strip())

    async def remove_followup(self, thread_id: str, message_id: str) -> None:
        managed = await self._ensure_thread(thread_id)
        await managed.session.remove_followup(message_id)

    async def rename_thread(self, thread_id: str, name: str) -> None:
        managed = await self._ensure_thread(thread_id)
        await managed.session.set_thread_name(name.strip(), thread_id)

    async def archive_thread(self, thread_id: str) -> None:
        managed = await self._ensure_thread(thread_id)
        cwd = None
        state = managed.state or managed.session.state
        if isinstance(state, dict) and isinstance(state.get("cwd"), str):
            cwd = state["cwd"]
        await managed.session.archive_thread(thread_id, cwd=cwd)
        await self._broadcast_thread_event("thread_archived", thread_id)
        await self._close_thread(thread_id)

    async def fork_thread(self, thread_id: str) -> JsonDict:
        source_thread_id = thread_id.strip()
        if not source_thread_id:
            raise ValueError("thread_id is required.")

        session = self._new_session(self._config(thread_id=source_thread_id))
        await session.start()
        try:
            await session.fork_thread(source_thread_id)
            forked_thread_id = session.thread_id
            if not forked_thread_id:
                raise RuntimeError("Codex did not return a forked thread id.")
            managed = await self._register_session(forked_thread_id, session)
        except Exception:
            await session.stop()
            raise
        return {
            "thread_id": forked_thread_id,
            "state": managed.state or managed.session.state,
        }

    async def unarchive_thread(self, thread_id: str) -> None:
        session = await self._ensure_workspace_session()
        await session.unarchive_thread(thread_id)
        await self._broadcast_thread_event("thread_unarchived", thread_id)

    async def _ensure_workspace_session(self) -> CodexIpcSession:
        if self._workspace_session is None:
            self._workspace_session = self._new_session(self._config())
            await self._workspace_session.start()
        return self._workspace_session

    async def _ensure_thread(self, thread_id: str) -> ManagedCodexThread:
        normalized_thread_id = thread_id.strip()
        if not normalized_thread_id:
            raise ValueError("thread_id is required.")
        managed = self._threads.get(normalized_thread_id)
        if managed is not None:
            return managed

        async with self._lock:
            managed = self._threads.get(normalized_thread_id)
            if managed is not None:
                return managed
            session = self._new_session(self._config(thread_id=normalized_thread_id))
            await session.start()
            try:
                await session.hydrate_initial_state()
            except Exception:
                await session.stop()
                raise
            return await self._register_session(normalized_thread_id, session)

    async def _register_session(
        self,
        thread_id: str,
        session: CodexIpcSession,
    ) -> ManagedCodexThread:
        existing = self._threads.get(thread_id)
        if existing is not None:
            if existing.session is not session:
                await session.stop()
            return existing

        watcher_task = asyncio.create_task(
            self._watch_thread(thread_id, session),
            name=f"codex-ipc-watch:{thread_id}",
        )
        managed = ManagedCodexThread(
            session=session,
            watcher_task=watcher_task,
            state=session.state,
        )
        self._threads[thread_id] = managed
        if session.state is not None:
            await self._fanout_thread_state(thread_id, session.state, session=session)
        return managed

    async def _close_thread(self, thread_id: str) -> None:
        managed = self._threads.pop(thread_id, None)
        if managed is None:
            return
        managed.watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await managed.watcher_task
        await managed.session.stop()
        self._subscribers.pop(thread_id, None)

    async def _watch_thread(
        self,
        thread_id: str,
        session: CodexIpcSession,
    ) -> None:
        async for state in session.watch_state(replay=True):
            managed = self._threads.get(thread_id)
            if managed is not None:
                managed.state = state
            await self._fanout_thread_state(thread_id, state, session=session)

    async def _fanout_thread_state(
        self,
        thread_id: str,
        state: JsonDict | None,
        *,
        session: CodexIpcSession,
    ) -> None:
        payload = {
            "type": "thread_state",
            "payload": {
                "thread_id": thread_id,
                "state": state,
                "stream_role": session.stream_role,
                "queued_followups": session.queued_followups,
            },
        }
        for queue in list(self._subscribers.get(thread_id, set())):
            queue.put_nowait(payload)
        await self.event_broker.publish(
            "codex_thread_state",
            payload["payload"],
        )

    async def _broadcast_thread_event(self, event_type: str, thread_id: str) -> None:
        payload = {
            "type": event_type,
            "payload": {
                "thread_id": thread_id,
            },
        }
        for subscribers in list(self._subscribers.values()):
            for queue in list(subscribers):
                queue.put_nowait(payload)
        await self.event_broker.publish(event_type, payload["payload"])

    def _config(self, *, thread_id: str | None = None) -> CodexIpcConfig:
        settings = self.config_service.load_web_settings().codex
        return CodexIpcConfig(
            thread_id=thread_id,
            host_id="local",
            client_type="yier",
            model=settings.model or None,
            reasoning_effort=self._reasoning_effort(settings),
            app_server_config=self._app_server_config(settings),
        )

    def _new_session(self, config: CodexIpcConfig) -> CodexIpcSession:
        return self._session_factory(config, notify=self._notify)

    def _app_server_config(self, settings: StoredCodexSettings) -> AppServerConfig:
        command = settings.launcher_command or "codex app-server --listen stdio://"
        try:
            args = tuple(shlex.split(command))
        except ValueError:
            args = ("codex", "app-server", "--listen", "stdio://")
        if not args:
            args = ("codex", "app-server", "--listen", "stdio://")
        return AppServerConfig(
            launch_args_override=args,
            cwd=str(self.config_service.project_root),
            client_name="yier_codex",
            client_title="Yier Codex",
        )

    def _thread_start_params(self, *, project_path: str | None) -> JsonDict:
        settings = self.config_service.load_web_settings().codex
        resolved_project_path = self.config_service.resolve_project_path(project_path)
        params: JsonDict = {
            "cwd": resolved_project_path,
            "model": settings.model or None,
            "approval_policy": settings.approval_policy,
            "approvals_reviewer": settings.approvals_reviewer,
            "sandbox": settings.sandbox,
            "service_tier": settings.service_tier or None,
        }
        if settings.personality != "none":
            params["personality"] = settings.personality
        return {key: value for key, value in params.items() if value is not None}

    def _reasoning_effort(self, settings: StoredCodexSettings) -> str | None:
        value = settings.reasoning_effort.strip()
        return value if value and value != "none" else None

    def _workspace_from_threads(
        self, response: ThreadListResponse
    ) -> CodexWorkspaceResponse:
        threads = response.data

        projects: dict[str, list[CodexNativeSessionSummary]] = {}
        for thread in threads:
            if thread.ephemeral:
                continue
            summary = _thread_summary(thread)
            projects.setdefault(summary.project_path or summary.project, []).append(
                summary
            )

        project_groups: list[CodexProjectGroup] = []
        for project_path, sessions in projects.items():
            sessions.sort(
                key=lambda item: (
                    _summary_used_at(item),
                    item.started_at,
                    item.thread_id,
                ),
                reverse=True,
            )
            project_groups.append(
                CodexProjectGroup(
                    project=sessions[0].project
                    if sessions
                    else Path(project_path).name,
                    project_path=project_path,
                    session_count=len(sessions),
                    sessions=sessions,
                )
            )

        project_groups.sort(
            key=lambda group: (
                _summary_used_at(group.sessions[0]) if group.sessions else 0.0,
                group.project.lower(),
            ),
            reverse=True,
        )
        return CodexWorkspaceResponse(projects=project_groups, paired_editors=[])

    def _notify(self, message: str) -> None:
        logger.info("codex-ipc: %s", message)
