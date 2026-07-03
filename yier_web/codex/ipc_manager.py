from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shlex
import tomllib
import uuid
from typing import Any, Callable

from codex_ipc import (
    AppServerConfig,
    CodexIpcConfig,
    CodexIpcSession,
    JsonDict,
    SshConnectionConfig,
    SshWebsocketAppServerConfig,
)
from codex_app_server import AsyncAppServerClient
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
    LoginAccountResponse,
)
from yier_web.config import AppConfigService
from yier_web.event_stream import EventStreamBroker
from yier_web.schemas import (
    CodexNativeSessionSummary,
    CodexProjectGroup,
    CodexRemoteConnection,
    CodexRemoteConnectionChatGptLoginResponse,
    CodexRemoteConnectionStatus,
    CodexRemoteConnectionTestResponse,
    CodexRemoteConnectionsResponse,
    CodexWorkspaceResponse,
    StoredCodexSettings,
)

logger = logging.getLogger(__name__)
CODEX_POSIX_INSTALL_URL = "https://chatgpt.com/codex/install.sh"
CODEX_CONFIG_FILE = "config.toml"

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


def _codex_home(home_dir: Path | None = None) -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return (home_dir or Path.home()).expanduser() / ".codex"


def _load_codex_home_config(codex_home: Path) -> JsonDict:
    config_path = codex_home / CODEX_CONFIG_FILE
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        logger.warning("Unable to parse %s: %s", config_path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _config_string(config: JsonDict, key: str) -> str | None:
    value = config.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _thread_summary(
    thread: Thread, *, host_id: str = "local"
) -> CodexNativeSessionSummary:
    cwd = thread.cwd.root
    project, project_path = _project_from_cwd(thread.cwd)
    name = _compact_text(thread.name)
    preview = _compact_text(thread.preview, limit=120)
    title = name or preview or thread.id
    return CodexNativeSessionSummary(
        thread_id=thread.id,
        host_id=host_id,
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
        self._remote_connection_statuses: dict[str, CodexRemoteConnectionStatus] = {}
        self._remote_login_forwards: dict[str, asyncio.subprocess.Process] = {}
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
        await self._stop_all_remote_login_forwards()

    async def workspace(self) -> CodexWorkspaceResponse:
        settings = self.config_service.load_web_settings().codex
        try:
            response = await self.list_threads()
        except Exception as exc:
            active_id = settings.active_remote_connection_id.strip()
            if active_id:
                self._set_remote_connection_status(
                    active_id,
                    "error",
                    _compact_text(exc, limit=180) or exc.__class__.__name__,
                )
            raise
        active_id = settings.active_remote_connection_id.strip()
        if active_id:
            self._set_remote_connection_status(active_id, "connected", "Connected")
        workspace = self._workspace_from_threads(
            response,
            host_id=self._host_id(settings),
        )
        remote = self.remote_connections()
        workspace.remote_connections = remote.connections
        workspace.active_remote_connection_id = remote.active_connection_id
        workspace.remote_connection_statuses = remote.statuses
        return workspace

    def remote_connections(self) -> CodexRemoteConnectionsResponse:
        settings = self.config_service.load_web_settings().codex
        return CodexRemoteConnectionsResponse(
            connections=settings.remote_connections,
            active_connection_id=settings.active_remote_connection_id,
            statuses=self._remote_statuses_for(settings),
        )

    async def activate_remote_connection(self, connection_id: str) -> None:
        previous_active_id = (
            self.config_service.load_web_settings().codex.active_remote_connection_id
        )
        self.config_service.set_active_codex_remote_connection(connection_id)
        if previous_active_id and previous_active_id != connection_id:
            await self.stop_remote_chatgpt_login(previous_active_id)
            self._set_remote_connection_status(
                previous_active_id,
                "disconnected",
                "Disconnected",
            )
        if connection_id:
            self._set_remote_connection_status(
                connection_id, "connecting", "Connecting"
            )
        await self._restart_sessions()

    async def restart_remote_connection(self, connection_id: str) -> None:
        if self._remote_connection_by_id(connection_id) is None:
            raise ValueError("Remote connection not found.")
        await self.stop_remote_chatgpt_login(connection_id)
        self.config_service.set_active_codex_remote_connection(connection_id)
        self._set_remote_connection_status(
            connection_id,
            "connecting",
            "Restarting connection",
        )
        await self._restart_sessions()

    async def install_remote_codex(
        self,
        connection_id: str,
    ) -> CodexRemoteConnectionTestResponse:
        connection = self._remote_connection_by_id(connection_id)
        if connection is None:
            raise ValueError("Remote connection not found.")
        self._set_remote_connection_status(
            connection_id, "connecting", "Installing Codex"
        )
        install_script = (
            "if command -v curl >/dev/null 2>&1; then "
            f'installer_script="$(curl -fsSL {CODEX_POSIX_INSTALL_URL})" || exit; '
            "elif command -v wget >/dev/null 2>&1; then "
            f'installer_script="$(wget -qO- {CODEX_POSIX_INSTALL_URL})" || exit; '
            "else echo 'curl or wget is required to install Codex' >&2; exit 127; fi; "
            "printf '%s\\n' \"$installer_script\" | "
            "CODEX_RELEASE=latest CODEX_NON_INTERACTIVE=1 sh"
        )
        process = await asyncio.create_subprocess_exec(
            *self._ssh_base_args(connection),
            self._remote_login_shell_command(install_script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            detail = "Remote Codex install timed out."
            self._set_remote_connection_status(connection_id, "error", detail)
            return CodexRemoteConnectionTestResponse(ok=False, detail=detail)
        detail = "\n".join(
            item.decode("utf-8", errors="replace").strip()
            for item in (stdout, stderr)
            if item
        ).strip()
        ok = process.returncode == 0
        if ok:
            await self.restart_remote_connection(connection_id)
        else:
            self._set_remote_connection_status(
                connection_id,
                "error",
                detail or f"Install exited with code {process.returncode}.",
            )
        return CodexRemoteConnectionTestResponse(
            ok=ok,
            detail=detail or ("Codex installed." if ok else "Codex install failed."),
        )

    async def start_remote_chatgpt_login(
        self,
        connection_id: str,
    ) -> CodexRemoteConnectionChatGptLoginResponse:
        connection = self._remote_connection_by_id(connection_id)
        if connection is None:
            raise ValueError("Remote connection not found.")
        self._set_remote_connection_status(
            connection_id,
            "connecting",
            "Starting ChatGPT login",
        )
        login_id = uuid.uuid4().hex
        try:
            async with AsyncAppServerClient(
                config=self._remote_app_server_config(connection)
            ) as client:
                await client.initialize()
                response = await client.request(
                    "account/login/start",
                    {
                        "type": "chatgpt",
                        "codexStreamlinedLogin": True,
                    },
                    response_model=LoginAccountResponse,
                )
        except Exception as exc:
            detail = _compact_text(exc, limit=180) or exc.__class__.__name__
            self._set_remote_connection_status(connection_id, "error", detail)
            return CodexRemoteConnectionChatGptLoginResponse(ok=False, detail=detail)
        if response.root.type != "chatgpt":
            detail = f"Unexpected login response type: {response.root.type}."
            self._set_remote_connection_status(connection_id, "error", detail)
            return CodexRemoteConnectionChatGptLoginResponse(ok=False, detail=detail)
        login_id = response.root.login_id
        try:
            await self._start_remote_chatgpt_login_forward(connection, connection_id)
        except Exception as exc:
            detail = _compact_text(exc, limit=180) or exc.__class__.__name__
            self._set_remote_connection_status(connection_id, "error", detail)
            return CodexRemoteConnectionChatGptLoginResponse(ok=False, detail=detail)
        self._set_remote_connection_status(
            connection_id,
            "connecting",
            "Waiting for ChatGPT login",
        )
        return CodexRemoteConnectionChatGptLoginResponse(
            ok=True,
            auth_url=response.root.auth_url,
            login_id=login_id,
        )

    async def stop_remote_chatgpt_login(self, connection_id: str) -> None:
        process = self._remote_login_forwards.pop(connection_id, None)
        if process is None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def login_remote_api_key(
        self,
        connection_id: str,
        api_key: str,
    ) -> CodexRemoteConnectionTestResponse:
        connection = self._remote_connection_by_id(connection_id)
        if connection is None:
            raise ValueError("Remote connection not found.")
        self._set_remote_connection_status(
            connection_id,
            "connecting",
            "Signing in with API key",
        )
        try:
            async with AsyncAppServerClient(
                config=self._remote_app_server_config(connection)
            ) as client:
                await client.initialize()
                await client.account_login_api_key(api_key)
                account = await client.account_read(refresh_token=False)
        except Exception as exc:
            detail = _compact_text(exc, limit=180) or exc.__class__.__name__
            self._set_remote_connection_status(connection_id, "error", detail)
            return CodexRemoteConnectionTestResponse(ok=False, detail=detail)
        account_type = (
            account.account.root.type if account.account is not None else "unknown"
        )
        detail = f"Signed in with {account_type}."
        self._set_remote_connection_status(connection_id, "connected", detail)
        return CodexRemoteConnectionTestResponse(ok=True, detail=detail)

    async def test_remote_connection(
        self,
        connection_id: str,
    ) -> CodexRemoteConnectionTestResponse:
        connection = self._remote_connection_by_id(connection_id)
        if connection is None:
            raise ValueError("Remote connection not found.")
        self._set_remote_connection_status(connection_id, "connecting", "Checking")
        args = self._ssh_base_args(connection)
        script = "command -v codex >/dev/null 2>&1 && codex --version"
        process = await asyncio.create_subprocess_exec(
            *args,
            self._remote_login_shell_command(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=12)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            self._set_remote_connection_status(
                connection_id,
                "error",
                "SSH connection timed out.",
            )
            return CodexRemoteConnectionTestResponse(
                ok=False,
                detail="SSH connection timed out.",
            )
        output = "\n".join(
            item.decode("utf-8", errors="replace").strip()
            for item in (stdout, stderr)
            if item
        ).strip()
        ok = process.returncode == 0
        detail = output or (
            "Codex is available on the remote host."
            if ok
            else f"SSH exited with code {process.returncode}."
        )
        self._set_remote_connection_status(
            connection_id,
            "connected" if ok else "error",
            detail,
        )
        return CodexRemoteConnectionTestResponse(ok=ok, detail=detail)

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
            "state": self._state_with_host_id(
                managed.state or managed.session.state,
                managed.session.config.host_id,
            ),
        }

    async def get_thread_state(self, thread_id: str) -> JsonDict | None:
        managed = await self._ensure_thread(thread_id)
        return self._state_with_host_id(
            managed.state or managed.session.state,
            managed.session.config.host_id,
        )

    async def subscribe(
        self,
        thread_id: str,
        queue: CodexSubscriberQueue,
    ) -> JsonDict | None:
        managed = await self._ensure_thread(thread_id)
        self._subscribers.setdefault(thread_id, set()).add(queue)
        state = self._state_with_host_id(
            managed.state or managed.session.state,
            managed.session.config.host_id,
        )
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
        if collaboration_mode is not None:
            await self._apply_latest_collaboration_mode(
                thread_id,
                managed,
                collaboration_mode,
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

    async def set_thread_goal(
        self,
        thread_id: str,
        *,
        objective: str | None = None,
        status: str | None = None,
        token_budget: int | None = None,
    ) -> JsonDict:
        managed = await self._ensure_thread(thread_id)
        response = await managed.session.set_thread_goal(
            objective=objective,
            status=status,
            token_budget=token_budget,
        )
        latest_state = managed.session.state
        if isinstance(latest_state, dict):
            managed.state = latest_state
            await self._fanout_thread_state(
                thread_id,
                latest_state,
                session=managed.session,
            )
        return response

    async def get_thread_goal(self, thread_id: str) -> JsonDict | None:
        managed = await self._ensure_thread(thread_id)
        goal = await managed.session.get_thread_goal()
        latest_state = managed.session.state
        if isinstance(latest_state, dict):
            managed.state = latest_state
            await self._fanout_thread_state(
                thread_id,
                latest_state,
                session=managed.session,
            )
        return goal

    async def clear_thread_goal(self, thread_id: str) -> JsonDict:
        managed = await self._ensure_thread(thread_id)
        response = await managed.session.clear_thread_goal()
        latest_state = managed.session.state
        if isinstance(latest_state, dict):
            managed.state = latest_state
            await self._fanout_thread_state(
                thread_id,
                latest_state,
                session=managed.session,
            )
        return response

    async def set_collaboration_mode(
        self,
        thread_id: str,
        collaboration_mode: JsonDict | None,
    ) -> None:
        managed = await self._ensure_thread(thread_id)
        await managed.session.set_collaboration_mode(collaboration_mode)
        await self._apply_latest_collaboration_mode(
            thread_id,
            managed,
            collaboration_mode,
        )

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

    async def _restart_sessions(self) -> None:
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
        state_with_host = self._state_with_host_id(state, session.config.host_id)
        payload = {
            "type": "thread_state",
            "payload": {
                "thread_id": thread_id,
                "state": state_with_host,
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

    def _state_with_host_id(
        self,
        state: JsonDict | None,
        host_id: str,
    ) -> JsonDict | None:
        if not isinstance(state, dict):
            return state
        if isinstance(state.get("hostId"), str) and state["hostId"]:
            return state
        return {**state, "hostId": host_id or "local"}

    async def _apply_latest_collaboration_mode(
        self,
        thread_id: str,
        managed: ManagedCodexThread,
        collaboration_mode: JsonDict | None,
    ) -> None:
        latest_state = managed.session.state
        if isinstance(latest_state, dict):
            managed.state = latest_state
        elif isinstance(managed.state, dict):
            latest_state = managed.state

        if not isinstance(latest_state, dict):
            return

        latest_state["latestCollaborationMode"] = (
            dict(collaboration_mode)
            if isinstance(collaboration_mode, dict)
            else {
                "mode": "default",
                "settings": {
                    "model": latest_state.get("latestModel") or "",
                    "reasoning_effort": latest_state.get("latestReasoningEffort"),
                    "developer_instructions": None,
                },
            }
        )
        await self._fanout_thread_state(
            thread_id,
            latest_state,
            session=managed.session,
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
        codex_home = _codex_home(self.config_service.home_dir)
        codex_config = _load_codex_home_config(codex_home)
        return CodexIpcConfig(
            thread_id=thread_id,
            host_id=self._host_id(settings),
            client_type="yier",
            model=self._thread_model(settings, codex_config),
            reasoning_effort=self._reasoning_effort(settings, codex_config),
            app_server_config=self._app_server_config(settings, codex_home=codex_home),
            default_thread_params=self._default_thread_params(
                settings,
                codex_config,
                cwd=self._default_thread_cwd(settings),
            ),
        )

    def _new_session(self, config: CodexIpcConfig) -> CodexIpcSession:
        return self._session_factory(config, notify=self._notify)

    def _app_server_config(
        self,
        settings: StoredCodexSettings,
        *,
        codex_home: Path,
    ) -> AppServerConfig:
        remote_connection = self._active_remote_connection(settings)
        if remote_connection is not None:
            return self._remote_app_server_config(
                remote_connection,
                cwd=None,
                client_name="yier_codex",
                client_title=f"Yier Codex ({remote_connection.display_name})",
            )

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
            env={"CODEX_HOME": str(codex_home)},
            client_name="yier_codex",
            client_title="Yier Codex",
        )

    def _remote_app_server_config(
        self,
        connection: CodexRemoteConnection,
        *,
        cwd: str | None = None,
        client_name: str = "yier_codex",
        client_title: str | None = None,
    ) -> AppServerConfig:
        return AppServerConfig(
            ssh_websocket=SshWebsocketAppServerConfig(
                connection=SshConnectionConfig(
                    host=connection.ssh_host,
                    alias=connection.ssh_alias or None,
                    port=connection.ssh_port,
                    identity=connection.identity_file or None,
                ),
                remote_cwd=connection.remote_path or "~",
            ),
            cwd=cwd,
            client_name=client_name,
            client_title=client_title or f"Yier Codex ({connection.display_name})",
        )

    def _host_id(self, settings: StoredCodexSettings) -> str:
        remote_connection = self._active_remote_connection(settings)
        if remote_connection is None:
            return "local"
        return f"ssh:{remote_connection.id}"

    def _active_remote_connection(
        self,
        settings: StoredCodexSettings,
    ) -> CodexRemoteConnection | None:
        active_id = settings.active_remote_connection_id.strip()
        if not active_id:
            return None
        for connection in settings.remote_connections:
            if connection.id == active_id:
                return connection
        return None

    def _remote_connection_by_id(
        self,
        connection_id: str,
    ) -> CodexRemoteConnection | None:
        normalized_id = connection_id.strip()
        if not normalized_id:
            return None
        for (
            connection
        ) in self.config_service.load_web_settings().codex.remote_connections:
            if connection.id == normalized_id:
                return connection
        return None

    def _remote_statuses_for(
        self,
        settings: StoredCodexSettings,
    ) -> dict[str, CodexRemoteConnectionStatus]:
        active_id = settings.active_remote_connection_id.strip()
        statuses: dict[str, CodexRemoteConnectionStatus] = {}
        known_ids = {connection.id for connection in settings.remote_connections}
        for stale_id in set(self._remote_connection_statuses) - known_ids:
            self._remote_connection_statuses.pop(stale_id, None)
        for connection in settings.remote_connections:
            status = self._remote_connection_statuses.get(connection.id)
            if status is None:
                status = CodexRemoteConnectionStatus(
                    status="connecting"
                    if connection.id == active_id
                    else "disconnected",
                    detail="Connecting"
                    if connection.id == active_id
                    else "Disconnected",
                )
            statuses[connection.id] = status
        return statuses

    def _set_remote_connection_status(
        self,
        connection_id: str,
        status: str,
        detail: str = "",
    ) -> None:
        self._remote_connection_statuses[connection_id] = CodexRemoteConnectionStatus(
            status=status,  # type: ignore[arg-type]
            detail=detail,
        )

    def _ssh_base_args(
        self,
        connection: CodexRemoteConnection,
        *,
        use_tty: bool = False,
    ) -> tuple[str, ...]:
        args = [
            "ssh",
            "-tt" if use_tty else "-T",
            "-v",
            "-o",
            "BatchMode=yes",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=12",
        ]
        if connection.ssh_alias:
            args.append(connection.ssh_alias)
            return tuple(args)
        if connection.identity_file:
            args.extend(["-i", connection.identity_file])
        if connection.ssh_port is not None:
            args.extend(["-p", str(connection.ssh_port)])
        args.append(connection.ssh_host)
        return tuple(args)

    def _remote_login_shell_command(self, script: str) -> str:
        path_prefix = (
            'PATH="${CODEX_INSTALL_DIR:-$HOME/.local/bin}:$PATH"; export PATH; '
        )
        return f'exec "${{SHELL:-sh}}" -l -i -c {shlex.quote(path_prefix + script)}'

    async def _start_remote_chatgpt_login_forward(
        self,
        connection: CodexRemoteConnection,
        connection_id: str,
    ) -> None:
        await self.stop_remote_chatgpt_login(connection_id)
        process = await asyncio.create_subprocess_exec(
            *self._ssh_base_args(connection, use_tty=False),
            "-N",
            "-L",
            "1455:127.0.0.1:1455",
            "-o",
            "ExitOnForwardFailure=yes",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=0.4)
        except asyncio.TimeoutError:
            self._remote_login_forwards[connection_id] = process
            return
        stderr = b""
        if process.stderr is not None:
            with contextlib.suppress(Exception):
                stderr = await process.stderr.read()
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            detail or f"SSH port forward exited with {process.returncode}."
        )

    async def _stop_all_remote_login_forwards(self) -> None:
        for connection_id in list(self._remote_login_forwards):
            await self.stop_remote_chatgpt_login(connection_id)

    def _thread_start_params(self, *, project_path: str | None) -> JsonDict:
        settings = self.config_service.load_web_settings().codex
        remote_connection = self._active_remote_connection(settings)
        if remote_connection is not None:
            resolved_project_path = project_path or remote_connection.remote_path or "~"
        else:
            resolved_project_path = self.config_service.resolve_project_path(
                project_path
            )
        return {"cwd": resolved_project_path}

    def _default_thread_cwd(self, settings: StoredCodexSettings) -> str:
        remote_connection = self._active_remote_connection(settings)
        if remote_connection is not None:
            return remote_connection.remote_path or "~"
        return str(self.config_service.project_root)

    def _default_thread_params(
        self,
        settings: StoredCodexSettings,
        codex_config: JsonDict,
        *,
        cwd: str,
    ) -> JsonDict:
        params: JsonDict = {
            "cwd": cwd,
            "model": self._thread_model(settings, codex_config),
            "model_provider": _config_string(codex_config, "model_provider"),
            "approval_policy": self._approval_policy(settings, codex_config),
            "approvals_reviewer": self._approvals_reviewer(settings, codex_config),
            "sandbox": self._sandbox_mode(settings, codex_config),
            "service_tier": self._service_tier(settings, codex_config),
            "personality": self._personality(settings, codex_config),
            "base_instructions": _config_string(codex_config, "base_instructions"),
            "developer_instructions": _config_string(
                codex_config,
                "developer_instructions",
            ),
        }
        reasoning_effort = self._reasoning_effort(settings, codex_config)
        if reasoning_effort is not None:
            params["config"] = {"model_reasoning_effort": reasoning_effort}
        ephemeral = codex_config.get("ephemeral")
        if isinstance(ephemeral, bool):
            params["ephemeral"] = ephemeral
        return {key: value for key, value in params.items() if value is not None}

    def _thread_model(
        self,
        settings: StoredCodexSettings,
        codex_config: JsonDict,
    ) -> str | None:
        return _config_string(codex_config, "model") or settings.model or None

    def _approval_policy(
        self,
        settings: StoredCodexSettings,
        codex_config: JsonDict,
    ) -> str | None:
        return (
            _config_string(codex_config, "approval_policy") or settings.approval_policy
        )

    def _approvals_reviewer(
        self,
        settings: StoredCodexSettings,
        codex_config: JsonDict,
    ) -> str | None:
        return (
            _config_string(codex_config, "approvals_reviewer")
            or settings.approvals_reviewer
        )

    def _sandbox_mode(
        self,
        settings: StoredCodexSettings,
        codex_config: JsonDict,
    ) -> str | None:
        return _config_string(codex_config, "sandbox_mode") or settings.sandbox

    def _service_tier(
        self,
        settings: StoredCodexSettings,
        codex_config: JsonDict,
    ) -> str | None:
        return (
            _config_string(codex_config, "service_tier")
            or settings.service_tier
            or None
        )

    def _personality(
        self,
        settings: StoredCodexSettings,
        codex_config: JsonDict,
    ) -> str | None:
        value = _config_string(codex_config, "personality") or settings.personality
        return value if value != "none" else None

    def _reasoning_effort(
        self,
        settings: StoredCodexSettings,
        codex_config: JsonDict,
    ) -> str | None:
        value = _config_string(codex_config, "model_reasoning_effort")
        if value is None:
            value = settings.reasoning_effort.strip()
        return value if value and value != "none" else None

    def _workspace_from_threads(
        self,
        response: ThreadListResponse,
        *,
        host_id: str,
    ) -> CodexWorkspaceResponse:
        threads = response.data

        projects: dict[str, list[CodexNativeSessionSummary]] = {}
        for thread in threads:
            if thread.ephemeral:
                continue
            summary = _thread_summary(thread, host_id=host_id)
            project_key = (
                f"{summary.host_id}::{summary.project_path or summary.project}"
            )
            projects.setdefault(project_key, []).append(summary)

        project_groups: list[CodexProjectGroup] = []
        for _, sessions in projects.items():
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
                    project=sessions[0].project if sessions else "Untitled project",
                    project_path=sessions[0].project_path if sessions else "",
                    host_id=sessions[0].host_id if sessions else "local",
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
