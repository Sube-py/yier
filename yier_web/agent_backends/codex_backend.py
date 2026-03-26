from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import shlex
import sys
import threading
from time import time
from typing import TYPE_CHECKING, Any

from codex_app_server import (
    AppServerClient,
    AppServerConfig,
    Thread as CodexThread,
    ThreadResumeParams,
    ThreadStartParams,
)
from codex_app_server.errors import AppServerError, TransportClosedError
from codex_app_server.models import Notification

from yier_agents import Message

from yier_web.agent_backends.base import ChatBackend, ChatSessionContext, StreamEmitter
from yier_web.schemas import StoredSessionMessage

if TYPE_CHECKING:
    from yier_web.chat import ChatService


DEFAULT_CODEX_LAUNCHER = "codex app-server --listen stdio://"
CODEX_IPC_DEBUG_ENV = "YIER_CODEX_IPC_DEBUG"
PLAN_MODE_PROMPT_PREFIX = "<yier-codex-plan-mode>"
PLAN_MODE_PROMPT = (
    f"{PLAN_MODE_PROMPT_PREFIX}\n"
    "Work in planning mode for this request. Analyze the task, avoid making changes, "
    "and return a concrete implementation plan that another engineer could execute."
)
CODEX_THREAD_SANDBOX_MODE_MAP = {
    "read-only": "read-only",
    "workspace-write": "workspace-write",
    "danger-full-access": "danger-full-access",
    "readOnly": "read-only",
    "workspaceWrite": "workspace-write",
    "dangerFullAccess": "danger-full-access",
}
CODEX_TURN_SANDBOX_POLICY_TYPE_MAP = {
    "read-only": "readOnly",
    "workspace-write": "workspaceWrite",
    "danger-full-access": "dangerFullAccess",
    "readOnly": "readOnly",
    "workspaceWrite": "workspaceWrite",
    "dangerFullAccess": "dangerFullAccess",
    "externalSandbox": "externalSandbox",
}
logger = logging.getLogger(__name__)


def _codex_ipc_debug_enabled() -> bool:
    return os.getenv(CODEX_IPC_DEBUG_ENV, "").strip().lower() not in {
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
        logger.warning(f"[codex-backend] {message} | {rendered}")
        return
    logger.warning(f"[codex-backend] {message}")


def _codex_thread_sandbox_mode(value: str) -> str:
    normalized = value.strip()
    sandbox_mode = CODEX_THREAD_SANDBOX_MODE_MAP.get(normalized)
    if sandbox_mode is None:
        raise ValueError(f"Unsupported Codex thread sandbox mode: {value}")
    return sandbox_mode


def _codex_turn_sandbox_policy_type(value: str) -> str:
    normalized = value.strip()
    sandbox_mode = CODEX_TURN_SANDBOX_POLICY_TYPE_MAP.get(normalized)
    if sandbox_mode is None:
        raise ValueError(f"Unsupported Codex turn sandbox policy type: {value}")
    return sandbox_mode




@dataclass(slots=True)
class PendingApprovalState:
    request_id: str
    method: str
    payload: dict[str, Any]
    record: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None
    decision: str | None = None


@dataclass(slots=True)
class TurnSnapshotState:
    params: dict[str, Any]
    turn_started_at_ms: int | None = None
    final_assistant_started_at_ms: int | None = None
    assistant_item_id: str | None = None
    assistant_text: str = ""


@dataclass(slots=True)
class CodexSessionRuntime:
    session_id: str
    client: AppServerClient | None = None
    thread_id: str | None = None
    status: str = "idle"
    active_flags: list[str] = field(default_factory=list)
    pending_requests: dict[str, PendingApprovalState] = field(default_factory=dict)
    turn_snapshots: dict[str, TurnSnapshotState] = field(default_factory=dict)
    assistant_buffers: dict[str, str] = field(default_factory=dict)
    reasoning_buffers: dict[str, dict[str, str]] = field(default_factory=dict)
    plan_buffers: dict[str, str] = field(default_factory=dict)
    detail: str | None = None
    loop: asyncio.AbstractEventLoop | None = None
    emit: StreamEmitter | None = None


class ApprovalAwareAppServerClient(AppServerClient):
    def __init__(
        self,
        config: AppServerConfig,
        approval_callback,
    ) -> None:
        super().__init__(config=config)
        self._approval_callback = approval_callback

    def _handle_server_request(self, msg: dict[str, Any]) -> dict[str, Any]:
        method = msg.get("method")
        params = msg.get("params")
        request_id = msg.get("id")
        if not isinstance(method, str) or not isinstance(request_id, str):
            return {}
        if not isinstance(params, dict):
            params = {}
        return self._approval_callback(request_id, method, params)


class CodexAppServerBackend(ChatBackend):
    backend_id = "codex"
    label = "Codex App Server"
    pairing_mcp_server_name = "yier_codex_pairing"

    def __init__(self, chat_service: "ChatService") -> None:
        self.chat_service = chat_service
        self._runtimes: dict[str, CodexSessionRuntime] = {}

    async def stop(self) -> None:
        for runtime in list(self._runtimes.values()):
            await self._close_runtime(runtime)
        self._runtimes.clear()

    async def close_session(self, session_id: str) -> None:
        runtime = self._runtimes.pop(session_id, None)
        if runtime is not None:
            await self._close_runtime(runtime)

    def bootstrap_session(
        self,
        project_path: Path,
        source: str = "chat",
        channel_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = ChatSessionContext(
            session_id="",
            source=source,
            backend_id=self.backend_id,
            project_path=project_path,
            channel_meta=channel_meta,
        )
        runtime = CodexSessionRuntime(session_id="")
        try:
            runtime.client = self._start_client(runtime, context)
            self._start_thread_blocking(runtime, context, persist=False)
        except Exception:
            if runtime.client is not None:
                try:
                    runtime.client.close()
                except (AppServerError, TransportClosedError):
                    pass
            raise

        assert runtime.thread_id is not None
        runtime.session_id = runtime.thread_id
        self._runtimes[runtime.thread_id] = runtime
        return {
            "thread_id": runtime.thread_id,
            "status": runtime.status,
            "active_flags": list(runtime.active_flags),
            "detail": runtime.detail,
        }

    async def stream_chat(
        self,
        context: ChatSessionContext,
        user_message: str,
        emit: StreamEmitter,
    ) -> str:
        runtime = await self._ensure_runtime(context)
        runtime.loop = asyncio.get_running_loop()
        runtime.emit = emit
        try:
            _codex_ipc_debug_log(
                "stream_chat start_turn begin",
                session_id=context.session_id,
                thread_id=runtime.thread_id,
            )
            response = await asyncio.to_thread(
                self._start_turn_blocking,
                runtime,
                context,
                user_message,
            )
            turn_id = self._response_turn_id(response)
            if not turn_id:
                raise RuntimeError("Codex turn_start response did not include a turn id.")
            _codex_ipc_debug_log(
                "stream_chat start_turn completed",
                session_id=context.session_id,
                thread_id=runtime.thread_id,
                turn_id=turn_id,
                runtime_status=runtime.status,
            )
            await emit(
                "run_started",
                {
                    "session_id": context.session_id,
                    "turn_id": turn_id,
                },
            )
            return await asyncio.to_thread(
                self._consume_turn_stream_blocking,
                runtime,
                context,
                turn_id,
            )
        finally:
            runtime.emit = None

    def runtime_payload(self, context: ChatSessionContext) -> dict[str, Any]:
        runtime = self._runtimes.get(context.session_id)
        thread_id = runtime.thread_id if runtime else context.backend_state.get("thread_id")
        status = runtime.status if runtime else context.backend_state.get("status", "idle")
        active_flags = runtime.active_flags if runtime else list(context.backend_state.get("active_flags", []))
        detail = runtime.detail if runtime else context.backend_state.get("detail")
        pending_count = len(runtime.pending_requests) if runtime else 0
        return {
            "backend_id": self.backend_id,
            "label": self.label,
            "ready": True,
            "status": status,
            "thread_id": thread_id,
            "active_flags": active_flags,
            "detail": detail,
            "pending_approval_count": pending_count,
        }

    def pending_approvals(self, context: ChatSessionContext) -> list[dict[str, Any]]:
        runtime = self._runtimes.get(context.session_id)
        if runtime is None:
            return []
        return [
            pending.record
            for pending in runtime.pending_requests.values()
            if not pending.event.is_set()
        ]

    def pending_conversation_requests(self, context: ChatSessionContext) -> list[dict[str, Any]]:
        runtime = self._runtimes.get(context.session_id)
        if runtime is None:
            return []
        return [
            {
                "id": pending.request_id,
                "method": pending.method,
                "params": dict(pending.payload),
            }
            for pending in runtime.pending_requests.values()
            if not pending.event.is_set()
        ]

    async def respond_to_approval(
        self,
        context: ChatSessionContext,
        request_id: str,
        decision: str,
        content: dict[str, Any] | None = None,
    ) -> bool:
        runtime = self._runtimes.get(context.session_id)
        if runtime is None:
            return False
        pending = runtime.pending_requests.get(request_id)
        if pending is None:
            return False
        pending.decision = decision
        pending.response = self._build_approval_response(
            method=pending.method,
            params=pending.payload,
            decision=decision,
            content=content,
        )
        pending.event.set()
        return True

    async def steer_turn(
        self,
        context: ChatSessionContext,
        turn_id: str | None,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any]:
        runtime = await self._ensure_runtime(context)
        assert runtime.client is not None
        assert runtime.thread_id is not None
        resolved_turn_id = turn_id or await asyncio.to_thread(
            self._resolve_active_turn_id_blocking,
            runtime,
            context,
        )
        if not resolved_turn_id:
            raise RuntimeError("No active Codex turn found for steer request.")
        response = await asyncio.to_thread(
            runtime.client.turn_steer,
            runtime.thread_id,
            resolved_turn_id,
            input_payload,
        )
        return self._safe_model_dump(response)

    async def start_turn(
        self,
        context: ChatSessionContext,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any]:
        runtime = await self._ensure_runtime(context)
        _codex_ipc_debug_log(
            "start_turn begin",
            session_id=context.session_id,
            thread_id=runtime.thread_id,
            input_type=type(input_payload).__name__,
        )
        response = await asyncio.to_thread(
            self._start_turn_blocking,
            runtime,
            context,
            input_payload,
        )
        _codex_ipc_debug_log(
            "start_turn completed",
            session_id=context.session_id,
            thread_id=runtime.thread_id,
            turn_id=self._response_turn_id(response),
            runtime_status=runtime.status,
        )
        return self._safe_model_dump(response)

    async def consume_turn_stream(
        self,
        context: ChatSessionContext,
        turn_id: str,
        emit: StreamEmitter,
    ) -> str:
        runtime = await self._ensure_runtime(context)
        runtime.loop = asyncio.get_running_loop()
        runtime.emit = emit
        try:
            return await asyncio.to_thread(
                self._consume_turn_stream_blocking,
                runtime,
                context,
                turn_id,
            )
        finally:
            runtime.emit = None
            runtime.loop = None

    async def interrupt_turn(
        self,
        context: ChatSessionContext,
        turn_id: str | None,
    ) -> dict[str, Any]:
        runtime = await self._ensure_runtime(context)
        assert runtime.client is not None
        assert runtime.thread_id is not None
        resolved_turn_id = turn_id or await asyncio.to_thread(
            self._resolve_active_turn_id_blocking,
            runtime,
            context,
        )
        if not resolved_turn_id:
            raise RuntimeError("No active Codex turn found for interrupt request.")
        response = await asyncio.to_thread(
            runtime.client.turn_interrupt,
            runtime.thread_id,
            resolved_turn_id,
        )
        return self._safe_model_dump(response)

    async def respond_to_raw_request(
        self,
        context: ChatSessionContext,
        request_id: str,
        response_payload: dict[str, Any],
    ) -> bool:
        runtime = self._runtimes.get(context.session_id)
        if runtime is None:
            return False
        pending = runtime.pending_requests.get(request_id)
        if pending is None:
            return False
        pending.response = dict(response_payload)
        pending.event.set()
        return True

    def load_thread_state(self, context: ChatSessionContext) -> dict[str, Any]:
        runtime = self._ensure_runtime_blocking(context)
        assert runtime.client is not None
        assert runtime.thread_id is not None
        response = CodexThread(runtime.client, runtime.thread_id).read(include_turns=True)
        runtime.status = self._thread_status_value(response.thread.status)
        runtime.active_flags = self._thread_active_flags(response.thread.status)
        self.chat_service.update_session_backend_state(
            context.session_id,
            {
                "thread_id": runtime.thread_id,
                "status": runtime.status,
                "active_flags": runtime.active_flags,
                "detail": runtime.detail,
            },
        )
        return {
            "thread": self._safe_model_dump(response.thread),
            "threadRuntimeStatus": self._thread_runtime_status_payload(
                self._safe_model_dump(response.thread.status),
                runtime.status,
                runtime.active_flags,
            ),
            "detail": runtime.detail,
        }

    def build_ipc_turns(
        self,
        context: ChatSessionContext,
        turns: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        runtime = self._runtimes.get(context.session_id)
        normalized_turns = [
            self._normalize_ipc_turn(
                context,
                turn,
                runtime.turn_snapshots.get(turn.get("id")) if runtime else None,
            )
            for turn in turns
        ]
        if runtime is None or runtime.status != "active":
            return normalized_turns

        seen_turn_ids = {
            turn.get("id")
            for turn in turns
            if isinstance(turn.get("id"), str) and turn.get("id")
        }
        missing_snapshots = [
            (turn_id, snapshot)
            for turn_id, snapshot in runtime.turn_snapshots.items()
            if turn_id not in seen_turn_ids
        ]
        if not missing_snapshots:
            return normalized_turns

        missing_snapshots.sort(key=lambda item: item[1].turn_started_at_ms or 0)
        turn_id, snapshot = missing_snapshots[-1]
        normalized_turns.append(
            self._normalize_ipc_turn(
                context,
                {
                    "id": turn_id,
                    "turnId": turn_id,
                    "status": "inProgress",
                    "items": [],
                    "error": None,
                },
                snapshot,
            )
        )
        return normalized_turns

    def load_thread_view(self, context: ChatSessionContext) -> dict[str, Any]:
        runtime = self._ensure_runtime_blocking(context)
        assert runtime.client is not None
        assert runtime.thread_id is not None
        response = CodexThread(runtime.client, runtime.thread_id).read(include_turns=True)
        runtime.status = self._thread_status_value(response.thread.status)
        runtime.active_flags = self._thread_active_flags(response.thread.status)
        self.chat_service.update_session_backend_state(
            context.session_id,
            {
                "thread_id": runtime.thread_id,
                "status": runtime.status,
                "active_flags": runtime.active_flags,
                "detail": runtime.detail,
            },
        )
        return self._thread_view_payload(context, response.thread)

    def should_use_local_session_view(self, context: ChatSessionContext) -> bool:
        runtime = self._runtimes.get(context.session_id)
        if runtime is not None:
            return runtime.status == "active"
        return context.backend_state.get("status") == "active"

    async def _ensure_runtime(self, context: ChatSessionContext) -> CodexSessionRuntime:
        runtime = self._runtimes.get(context.session_id)
        if runtime is None:
            runtime = CodexSessionRuntime(session_id=context.session_id)
            self._runtimes[context.session_id] = runtime
            runtime.client = await asyncio.to_thread(self._start_client, runtime, context)

        if runtime.thread_id:
            return runtime

        thread_id = context.backend_state.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            await asyncio.to_thread(self._resume_thread_blocking, runtime, context, thread_id)
        else:
            await asyncio.to_thread(self._start_thread_blocking, runtime, context)
        return runtime

    def _ensure_runtime_blocking(self, context: ChatSessionContext) -> CodexSessionRuntime:
        runtime = self._runtimes.get(context.session_id)
        if runtime is None:
            runtime = CodexSessionRuntime(session_id=context.session_id)
            self._runtimes[context.session_id] = runtime
            runtime.client = self._start_client(runtime, context)

        if runtime.thread_id:
            return runtime

        thread_id = context.backend_state.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            self._resume_thread_blocking(runtime, context, thread_id)
        else:
            self._start_thread_blocking(runtime, context)
        return runtime

    def _start_client(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
    ) -> AppServerClient:
        codex_settings = self.chat_service.config_service.load_web_settings().codex
        launcher_command = codex_settings.launcher_command or DEFAULT_CODEX_LAUNCHER
        launch_args = tuple(shlex.split(launcher_command))
        if not launch_args:
            raise RuntimeError("Codex launcher command is empty.")
        client = ApprovalAwareAppServerClient(
            config=AppServerConfig(
                launch_args_override=launch_args,
                cwd=str(context.project_path),
                client_name="yier_web",
                client_title="Yier Web",
            ),
            approval_callback=lambda request_id, method, params: self._handle_server_request(
                runtime,
                context,
                request_id,
                method,
                params,
            ),
        )
        client.start()
        client.initialize()
        return client

    def _start_thread_blocking(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        *,
        persist: bool = True,
    ) -> None:
        assert runtime.client is not None
        response = runtime.client.thread_start(ThreadStartParams(**self._thread_params(context)))
        runtime.thread_id = response.thread.id
        runtime.status = self._thread_status_value(response.thread.status)
        runtime.active_flags = self._thread_active_flags(response.thread.status)
        if persist and context.session_id:
            self.chat_service.update_session_backend_state(
                context.session_id,
                {
                    "thread_id": runtime.thread_id,
                    "status": runtime.status,
                    "active_flags": runtime.active_flags,
                },
            )

    def _resume_thread_blocking(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        thread_id: str,
    ) -> None:
        assert runtime.client is not None
        response = runtime.client.thread_resume(
            thread_id,
            ThreadResumeParams(thread_id=thread_id, **self._thread_params(context)),
        )
        runtime.thread_id = response.thread.id
        runtime.status = self._thread_status_value(response.thread.status)
        runtime.active_flags = self._thread_active_flags(response.thread.status)
        self.chat_service.update_session_backend_state(
            context.session_id,
            {
                "thread_id": runtime.thread_id,
                "status": runtime.status,
                "active_flags": runtime.active_flags,
            },
        )

    def _resolve_active_turn_id_blocking(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
    ) -> str | None:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        response = CodexThread(runtime.client, runtime.thread_id).read(include_turns=True)
        turns = list(getattr(response.thread, "turns", []) or [])
        for turn in reversed(turns):
            status = str(getattr(turn, "status", "") or "")
            turn_id = getattr(turn, "id", None)
            if not isinstance(turn_id, str) or not turn_id:
                continue
            if status not in {"completed", "failed", "interrupted"}:
                return turn_id
        for turn in reversed(turns):
            turn_id = getattr(turn, "id", None)
            if isinstance(turn_id, str) and turn_id:
                return turn_id
        return None

    def _run_turn_blocking(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        user_message: str,
    ) -> str:
        response = self._start_turn_blocking(runtime, context, user_message)
        turn_id = self._response_turn_id(response)
        if not turn_id:
            raise RuntimeError("Codex turn_start response did not include a turn id.")
        return self._consume_turn_stream_blocking(runtime, context, turn_id)

    def _start_turn_blocking(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> Any:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        runtime.status = "active"
        runtime.detail = None
        turn_input = self._normalize_turn_input_payload(context, input_payload)
        _codex_ipc_debug_log(
            "_start_turn_blocking before client.turn_start",
            session_id=context.session_id,
            thread_id=runtime.thread_id,
            input_type=type(turn_input).__name__,
        )
        response = runtime.client.turn_start(
            runtime.thread_id,
            turn_input,
            params=self._turn_params(context),
        )
        turn_id = self._response_turn_id(response)
        if not turn_id:
            raise RuntimeError("Codex turn_start response did not include a turn id.")
        _codex_ipc_debug_log(
            "_start_turn_blocking after client.turn_start",
            session_id=context.session_id,
            thread_id=runtime.thread_id,
            turn_id=turn_id,
        )
        runtime.turn_snapshots[turn_id] = TurnSnapshotState(
            params=self._build_turn_state_params(context, turn_input),
            turn_started_at_ms=int(time() * 1000),
            final_assistant_started_at_ms=None,
        )
        self.chat_service.update_session_backend_state(
            context.session_id,
            {
                "thread_id": runtime.thread_id,
                "status": runtime.status,
                "active_flags": runtime.active_flags,
                "detail": runtime.detail,
            },
        )
        return response

    def _consume_turn_stream_blocking(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        turn_id: str,
    ) -> str:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        finish_reason = "stop"
        runtime.client.acquire_turn_consumer(turn_id)
        try:
            while True:
                notification = runtime.client.next_notification()
                notification_thread_id = self._notification_value(notification, "thread_id")
                if notification_thread_id and notification_thread_id != runtime.thread_id:
                    continue
                if not self._notification_belongs_to_turn(notification, turn_id):
                    if notification.method.startswith("thread/"):
                        self._handle_thread_notification(runtime, context, notification)
                    continue

                if notification.method == "turn/completed":
                    finish_reason = self._handle_turn_completed(runtime, context, notification)
                    break

                self._handle_turn_notification(runtime, context, notification)
        finally:
            runtime.client.release_turn_consumer(turn_id)

        return finish_reason

    def _normalize_turn_input_payload(
        self,
        context: ChatSessionContext,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> list[dict[str, Any]] | dict[str, Any] | str:
        if self._codex_work_mode(context) != "plan":
            return input_payload

        plan_text = f"{PLAN_MODE_PROMPT}\n\nUser request:"
        if isinstance(input_payload, str):
            return f"{plan_text}\n{input_payload}"
        if isinstance(input_payload, dict):
            return [
                {"type": "text", "text": plan_text},
                input_payload,
            ]
        return [
            {"type": "text", "text": plan_text},
            *input_payload,
        ]

    def _handle_thread_notification(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        notification: Notification,
    ) -> None:
        if notification.method == "thread/status/changed":
            status = getattr(notification.payload, "status", None)
            runtime.status = self._thread_status_value(status)
            runtime.active_flags = self._thread_active_flags(status)
        elif notification.method == "thread/started":
            thread = getattr(notification.payload, "thread", None)
            if thread is not None:
                runtime.thread_id = getattr(thread, "id", runtime.thread_id)
                runtime.status = self._thread_status_value(getattr(thread, "status", None))
                runtime.active_flags = self._thread_active_flags(getattr(thread, "status", None))
        self.chat_service.update_session_backend_state(
            context.session_id,
            {
                "thread_id": runtime.thread_id,
                "status": runtime.status,
                "active_flags": runtime.active_flags,
                "detail": runtime.detail,
            },
        )

    def _handle_turn_notification(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        notification: Notification,
    ) -> None:
        if notification.method == "turn/started":
            runtime.status = "active"
            runtime.active_flags = []
            turn = getattr(notification.payload, "turn", None)
            turn_id = getattr(turn, "id", None)
            if isinstance(turn_id, str) and turn_id:
                snapshot = runtime.turn_snapshots.get(turn_id)
                if snapshot is None:
                    snapshot = TurnSnapshotState(
                        params=self._default_turn_state_params(context),
                    )
                    runtime.turn_snapshots[turn_id] = snapshot
                if snapshot.turn_started_at_ms is None:
                    snapshot.turn_started_at_ms = int(time() * 1000)
            self.chat_service.update_session_backend_state(
                context.session_id,
                {"status": runtime.status, "active_flags": runtime.active_flags},
            )
            return

        if notification.method == "thread/realtime/error":
            message = self._notification_value(notification, "message")
            thread_id = self._notification_value(notification, "thread_id")
            if isinstance(message, str) and message.strip():
                self._emit_from_thread(
                    runtime,
                    "stream_error",
                    {
                        "session_id": context.session_id,
                        "message": message,
                        "thread_id": thread_id if isinstance(thread_id, str) else runtime.thread_id,
                        "turn_id": None,
                        "code": None,
                        "will_retry": False,
                    },
                )
            return

        if notification.method == "error":
            error = self._notification_value(notification, "error")
            message = getattr(error, "message", None)
            code = getattr(error, "code", None)
            turn_id = self._notification_value(notification, "turn_id")
            thread_id = self._notification_value(notification, "thread_id")
            will_retry = self._notification_value(notification, "will_retry")
            if isinstance(message, str) and message.strip():
                self._emit_from_thread(
                    runtime,
                    "stream_error",
                    {
                        "session_id": context.session_id,
                        "message": message,
                        "thread_id": thread_id if isinstance(thread_id, str) else runtime.thread_id,
                        "turn_id": turn_id if isinstance(turn_id, str) else None,
                        "code": str(code) if code is not None else None,
                        "will_retry": bool(will_retry),
                    },
                )
            return

        if notification.method == "item/agentMessage/delta":
            item_id = self._notification_value(notification, "item_id")
            delta = self._notification_value(notification, "delta")
            turn_id = self._notification_value(notification, "turn_id")
            if not isinstance(item_id, str) or not isinstance(delta, str):
                return
            if isinstance(turn_id, str) and turn_id:
                snapshot = runtime.turn_snapshots.get(turn_id)
                if snapshot is not None and snapshot.final_assistant_started_at_ms is None:
                    snapshot.final_assistant_started_at_ms = int(time() * 1000)
                if snapshot is not None:
                    snapshot.assistant_item_id = item_id
                    snapshot.assistant_text = f"{snapshot.assistant_text}{delta}"
            runtime.assistant_buffers[item_id] = f"{runtime.assistant_buffers.get(item_id, '')}{delta}"
            self._emit_from_thread(
                runtime,
                "assistant_delta",
                {
                    "session_id": context.session_id,
                    "item_id": item_id,
                    "delta": delta,
                },
            )
            return

        if notification.method in {
            "item/reasoning/textDelta",
            "item/reasoning/summaryTextDelta",
        }:
            delta = self._notification_value(notification, "delta")
            item_id = self._notification_value(notification, "item_id")
            if isinstance(delta, str) and isinstance(item_id, str) and item_id and delta:
                content = self._accumulate_reasoning_delta(runtime, item_id, notification.method, delta)
                self._emit_from_thread(
                    runtime,
                    "reasoning",
                    {
                        "session_id": context.session_id,
                        "item_id": item_id,
                        "content": content,
                        "iteration": 0,
                    },
                )
            return

        if notification.method == "item/plan/delta":
            delta = self._notification_value(notification, "delta")
            item_id = self._notification_value(notification, "item_id")
            turn_id = self._notification_value(notification, "turn_id")
            activity_id = (
                turn_id
                if isinstance(turn_id, str) and turn_id
                else item_id if isinstance(item_id, str) and item_id else ""
            )
            if isinstance(delta, str) and activity_id and delta:
                content = self._accumulate_plan_delta(runtime, activity_id, delta)
                self._emit_from_thread(
                    runtime,
                    "plan",
                    {
                        "session_id": context.session_id,
                        "item_id": activity_id,
                        "content": content,
                        "iteration": 0,
                    },
                )
            return

        if notification.method == "turn/plan/updated":
            explanation = self._notification_value(notification, "explanation")
            plan = self._notification_value(notification, "plan")
            turn_id = self._notification_value(notification, "turn_id")
            lines: list[str] = []
            if isinstance(explanation, str) and explanation.strip():
                lines.append(explanation.strip())
            if isinstance(plan, list):
                for entry in plan:
                    step = getattr(entry, "step", None)
                    status = getattr(entry, "status", None)
                    if isinstance(step, str) and step.strip():
                        if status is not None:
                            lines.append(f"[{status}] {step.strip()}")
                        else:
                            lines.append(step.strip())
            if lines:
                activity_id = turn_id if isinstance(turn_id, str) and turn_id else ""
                content = "\n".join(lines)
                if activity_id:
                    runtime.plan_buffers[activity_id] = content
                self._emit_from_thread(
                    runtime,
                    "plan",
                    {
                        "session_id": context.session_id,
                        "item_id": activity_id,
                        "content": content,
                        "iteration": 0,
                    },
                )
            return

        if notification.method == "item/commandExecution/outputDelta":
            item_id = self._notification_value(notification, "item_id")
            delta = self._notification_value(notification, "delta")
            if not isinstance(item_id, str) or not isinstance(delta, str):
                return
            self._emit_from_thread(
                runtime,
                "command_output",
                {
                    "session_id": context.session_id,
                    "tool_call_id": item_id,
                    "tool_name": "command_execution",
                    "stream": "stdout",
                    "content": delta,
                    "is_background": False,
                },
            )
            return

        if notification.method == "item/started":
            item = self._unwrap_thread_item(getattr(notification.payload, "item", None))
            if item is None:
                return
            self._handle_item_started(runtime, context, item)
            return

        if notification.method == "item/completed":
            item = self._unwrap_thread_item(getattr(notification.payload, "item", None))
            if item is None:
                return
            turn_id = self._notification_value(notification, "turn_id")
            if (
                getattr(item, "type", "") == "agentMessage"
                and isinstance(turn_id, str)
                and turn_id
            ):
                snapshot = runtime.turn_snapshots.get(turn_id)
                if snapshot is not None and snapshot.final_assistant_started_at_ms is None:
                    snapshot.final_assistant_started_at_ms = int(time() * 1000)
            self._handle_item_completed(runtime, context, item)
            return

        if notification.method == "serverRequest/resolved":
            request_id = self._request_id_string(self._notification_value(notification, "request_id"))
            if not request_id:
                return
            pending = runtime.pending_requests.pop(request_id, None)
            if pending is None:
                return
            self._emit_from_thread(
                runtime,
                "approval_resolved",
                {
                    "session_id": context.session_id,
                    "request_id": request_id,
                    "decision": pending.decision or "accept",
                },
            )

    def _handle_item_started(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        item: Any,
    ) -> None:
        item_type = getattr(item, "type", "")
        if item_type == "commandExecution":
            self._emit_from_thread(
                runtime,
                "command_start",
                {
                    "session_id": context.session_id,
                    "tool_call_id": item.id,
                    "tool_name": "command_execution",
                    "command": getattr(item, "command", ""),
                    "cwd": getattr(item, "cwd", ""),
                    "is_background": False,
                },
            )
            return

        if item_type == "fileChange":
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": "file_change",
                    "tool_call_id": item.id,
                    "arguments": {
                        "change_count": len(getattr(item, "changes", []) or []),
                    },
                    "iteration": 0,
                },
            )
            return

        if item_type == "mcpToolCall":
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": f"mcp:{getattr(item, 'server', 'unknown')}/{getattr(item, 'tool', 'tool')}",
                    "tool_call_id": item.id,
                    "arguments": self._safe_model_dump(getattr(item, "arguments", {})),
                    "iteration": 0,
                },
            )
            return

        if item_type == "dynamicToolCall":
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": f"dynamic:{getattr(item, 'tool', 'tool')}",
                    "tool_call_id": item.id,
                    "arguments": self._safe_model_dump(getattr(item, "arguments", {})),
                    "iteration": 0,
                },
            )
            return

        if item_type == "collabAgentToolCall":
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": f"collab:{getattr(item, 'tool', 'tool')}",
                    "tool_call_id": item.id,
                    "arguments": {
                        "receiver_thread_ids": list(getattr(item, "receiver_thread_ids", [])),
                        "prompt": getattr(item, "prompt", None),
                    },
                    "iteration": 0,
                },
            )
            return

        if item_type == "webSearch":
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": "web_search",
                    "tool_call_id": item.id,
                    "arguments": {"query": getattr(item, "query", "")},
                    "iteration": 0,
                },
            )

    def _handle_item_completed(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        item: Any,
    ) -> None:
        item_type = getattr(item, "type", "")
        if item_type == "commandExecution":
            exit_code = getattr(item, "exit_code", 0)
            self._emit_from_thread(
                runtime,
                "command_end",
                {
                    "session_id": context.session_id,
                    "tool_call_id": item.id,
                    "tool_name": "command_execution",
                    "command": getattr(item, "command", ""),
                    "cwd": getattr(item, "cwd", ""),
                    "exit_code": exit_code or 0,
                    "timed_out": False,
                    "is_background": False,
                },
            )
            return

        if item_type == "agentMessage":
            content = getattr(item, "text", "") or runtime.assistant_buffers.pop(item.id, "")
            turn_id = getattr(item, "turn_id", None)
            if not isinstance(turn_id, str) or not turn_id:
                turn_id = getattr(item, "turnId", None)
            if isinstance(turn_id, str) and turn_id:
                snapshot = runtime.turn_snapshots.get(turn_id)
                if snapshot is not None:
                    snapshot.assistant_item_id = item.id
                    snapshot.assistant_text = content
            if content.strip():
                self.chat_service._append_transcript_message(
                    context.session_id,
                    Message(role="assistant", content=content),
                )
                self._emit_from_thread(
                    runtime,
                    "assistant_message",
                    {
                        "session_id": context.session_id,
                        "item_id": item.id,
                        "content": content,
                        "iteration": 0,
                    },
                )
            return

        if item_type == "reasoning":
            content = "\n".join(getattr(item, "summary", []) or getattr(item, "content", []) or [])
            if content.strip():
                self._emit_from_thread(
                    runtime,
                    "reasoning",
                    {
                        "session_id": context.session_id,
                        "item_id": item.id,
                        "content": content,
                        "iteration": 0,
                    },
                )
            return

        if item_type == "fileChange":
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": "file_change",
                    "tool_call_id": item.id,
                    "result": self._summarize_file_change(item),
                    "is_error": getattr(item, "status", "") not in {"completed", "inProgress"},
                    "metadata": {
                        "status": getattr(item, "status", ""),
                        "change_count": len(getattr(item, "changes", []) or []),
                    },
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if item_type == "mcpToolCall":
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": f"mcp:{getattr(item, 'server', 'unknown')}/{getattr(item, 'tool', 'tool')}",
                    "tool_call_id": item.id,
                    "result": self._summarize_tool_result(item),
                    "is_error": getattr(item, "error", None) is not None,
                    "metadata": {"status": getattr(item, "status", "")},
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if item_type == "dynamicToolCall":
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": f"dynamic:{getattr(item, 'tool', 'tool')}",
                    "tool_call_id": item.id,
                    "result": self._summarize_tool_result(item),
                    "is_error": getattr(item, "success", True) is False,
                    "metadata": {"status": getattr(item, "status", "")},
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if item_type == "collabAgentToolCall":
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": f"collab:{getattr(item, 'tool', 'tool')}",
                    "tool_call_id": item.id,
                    "result": self._summarize_collab_result(item),
                    "is_error": getattr(item, "status", "") not in {"completed", "inProgress"},
                    "metadata": {"status": getattr(item, "status", "")},
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if item_type == "webSearch":
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": "web_search",
                    "tool_call_id": item.id,
                    "result": f"Searched the web for {getattr(item, 'query', '')}.",
                    "is_error": False,
                    "metadata": {},
                    "raw": None,
                    "iteration": 0,
                },
            )

    def _handle_turn_completed(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        notification: Notification,
    ) -> str:
        turn = getattr(notification.payload, "turn", None)
        turn_id = getattr(turn, "id", None)
        status = getattr(turn, "status", None)
        runtime.status = "idle" if status == "completed" else str(status or "idle")
        runtime.active_flags = []
        runtime.detail = None
        finish_reason = "stop"
        if status == "interrupted":
            finish_reason = "interrupted"
            self._emit_from_thread(
                runtime,
                "turn_aborted",
                {
                    "session_id": context.session_id,
                    "turn_id": turn_id if isinstance(turn_id, str) else None,
                    "status": str(status),
                    "reason": "Turn interrupted.",
                },
            )
        elif status == "completed":
            self._emit_from_thread(
                runtime,
                "turn_completed",
                {
                    "session_id": context.session_id,
                    "turn_id": turn_id if isinstance(turn_id, str) else None,
                    "status": str(status),
                },
            )
        elif status == "failed":
            finish_reason = "error"
            error = getattr(turn, "error", None)
            message = getattr(error, "message", "Codex turn failed.")
            self._emit_from_thread(
                runtime,
                "stream_error",
                {
                    "session_id": context.session_id,
                    "message": message,
                    "thread_id": runtime.thread_id,
                    "turn_id": turn_id if isinstance(turn_id, str) else None,
                    "code": str(getattr(error, "code", None)) if getattr(error, "code", None) is not None else None,
                    "will_retry": False,
                },
            )

        self.chat_service.update_session_backend_state(
            context.session_id,
            {
                "thread_id": runtime.thread_id,
                "status": runtime.status,
                "active_flags": runtime.active_flags,
                "detail": runtime.detail,
            },
        )
        return finish_reason

    def _handle_server_request(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        request_id: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        auto_response = self._auto_approval_response(context, method, params)
        if auto_response is not None:
            return auto_response

        record = self._build_pending_approval(request_id, method, params)
        pending = PendingApprovalState(
            request_id=request_id,
            method=method,
            payload=params,
            record={
                "request_id": request_id,
                **record,
            },
        )
        runtime.pending_requests[request_id] = pending
        self._emit_from_thread(
            runtime,
            "approval_requested",
            {
                "session_id": context.session_id,
                **pending.record,
            },
            wait=True,
        )
        pending.event.wait()
        response = pending.response or self._build_approval_response(method, params, "cancel", None)
        return response

    def _build_pending_approval(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = params.get("itemId")
        kind = self._approval_kind(method)
        title = "Approval required"
        detail = params.get("reason") or "Codex needs confirmation to continue."
        if kind == "command":
            command = params.get("command")
            cwd = params.get("cwd")
            title = "Approve command"
            detail = f"{command or 'A command'} in {cwd or 'the current workspace'}"
        elif kind == "file_change":
            title = "Approve file change"
            detail = params.get("reason") or "Codex wants to apply file changes."
        elif kind == "permissions":
            title = "Grant permissions"
            detail = params.get("reason") or "Codex requested additional permissions."
        elif kind == "mcp_elicitation":
            request = self._normalized_elicitation_request(method, params)
            title = "MCP server input"
            if isinstance(request, dict):
                detail = request.get("message") or detail

        return {
            "method": method,
            "kind": kind,
            "title": title,
            "detail": detail,
            "payload": self._approval_payload(method, params),
            "options": self._approval_options(kind),
            "item_id": item_id,
        }

    def _approval_payload(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = dict(params)
        request = self._normalized_elicitation_request(method, params)
        if request is not None:
            payload["request"] = request
        return payload

    def _normalized_elicitation_request(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        if method == "mcpServer/elicitation/request":
            request = params.get("request")
            return request if isinstance(request, dict) else None

        if method != "elicitation/create":
            return None

        normalized: dict[str, Any] = {}
        message = params.get("message")
        if isinstance(message, str) and message.strip():
            normalized["message"] = message

        meta = params.get("_meta")
        if meta is not None:
            normalized["_meta"] = meta

        requested_schema = params.get("requestedSchema")
        if isinstance(requested_schema, dict):
            normalized["mode"] = "form"
            normalized["requestedSchema"] = requested_schema
            return normalized

        url = params.get("url")
        if isinstance(url, str) and url.strip():
            normalized["mode"] = "url"
            normalized["url"] = url
            elicitation_id = params.get("elicitationId")
            if isinstance(elicitation_id, str) and elicitation_id.strip():
                normalized["elicitationId"] = elicitation_id
            return normalized

        return None

    def _approval_kind(self, method: str) -> str:
        if method == "item/commandExecution/requestApproval":
            return "command"
        if method == "item/fileChange/requestApproval":
            return "file_change"
        if method == "item/permissions/requestApproval":
            return "permissions"
        if method in {"mcpServer/elicitation/request", "elicitation/create"}:
            return "mcp_elicitation"
        return "approval"

    def _approval_options(self, kind: str) -> list[dict[str, str]]:
        if kind in {"command", "file_change", "permissions"}:
            return [
                {"value": "accept", "label": "Accept once"},
                {"value": "accept_for_session", "label": "Accept for session"},
                {"value": "decline", "label": "Decline"},
                {"value": "cancel", "label": "Cancel"},
            ]
        return [
            {"value": "accept", "label": "Accept"},
            {"value": "decline", "label": "Decline"},
            {"value": "cancel", "label": "Cancel"},
        ]

    def _auto_approval_response(
        self,
        context: ChatSessionContext,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        settings = self.chat_service.config_service.load_web_settings().session_defaults
        if context.source != "channel" or not settings.channel_auto_approve_codex_requests:
            return None
        if method in {"mcpServer/elicitation/request", "elicitation/create"}:
            return {"action": "decline", "content": None}
        return self._build_approval_response(method, params, "accept", None)

    def _build_approval_response(
        self,
        method: str,
        params: dict[str, Any],
        decision: str,
        content: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_decision = decision.strip().lower()
        if method in {"mcpServer/elicitation/request", "elicitation/create"}:
            if normalized_decision == "accept":
                return {"action": "accept", "content": content}
            if normalized_decision == "decline":
                return {"action": "decline", "content": None}
            return {"action": "cancel", "content": None}

        if method == "item/permissions/requestApproval":
            if normalized_decision == "accept":
                return {
                    "scope": "turn",
                    "permissions": params.get("permissions", {}),
                }
            if normalized_decision == "accept_for_session":
                return {
                    "scope": "session",
                    "permissions": params.get("permissions", {}),
                }
            return {"scope": "turn", "permissions": {}}

        if normalized_decision == "accept_for_session":
            return {"decision": "acceptForSession"}
        if normalized_decision == "decline":
            return {"decision": "decline"}
        if normalized_decision == "cancel":
            return {"decision": "cancel"}
        return {"decision": "accept"}

    def _thread_params(self, context: ChatSessionContext) -> dict[str, Any]:
        settings = self.chat_service.config_service.load_web_settings().codex
        model_override = self._context_string_override(context, "model")
        service_tier_override = self._context_string_override(context, "service_tier")
        params = {
            "cwd": str(context.project_path),
            "approval_policy": settings.approval_policy,
            "approvals_reviewer": settings.approvals_reviewer,
            "model": model_override or settings.model or None,
            "personality": settings.personality,
            "sandbox": _codex_thread_sandbox_mode(settings.sandbox),
            "service_tier": service_tier_override or settings.service_tier or None,
        }
        pairing_config = self._pairing_mcp_config()
        if pairing_config is not None:
            params["config"] = pairing_config
        return params

    def _turn_params(self, context: ChatSessionContext) -> dict[str, Any]:
        settings = self.chat_service.config_service.load_web_settings().codex
        approval_policy = settings.approval_policy
        if context.source == "channel":
            approval_policy = "never"
        reasoning_effort_override = self._context_string_override(context, "reasoning_effort")
        model_override = self._context_string_override(context, "model")
        service_tier_override = self._context_string_override(context, "service_tier")
        return {
            "cwd": str(context.project_path),
            "approval_policy": approval_policy,
            "approvals_reviewer": settings.approvals_reviewer,
            "effort": reasoning_effort_override or settings.reasoning_effort,
            "model": model_override or settings.model or None,
            "personality": settings.personality,
            "sandbox_policy": {"type": _codex_turn_sandbox_policy_type(settings.sandbox)},
            "service_tier": service_tier_override or settings.service_tier or None,
        }

    def _pairing_mcp_config(self) -> dict[str, Any] | None:
        config_service = getattr(self.chat_service, "config_service", None)
        project_root = getattr(config_service, "project_root", None)
        home_dir = getattr(config_service, "home_dir", None)
        if not isinstance(project_root, Path) or not isinstance(home_dir, Path):
            return None

        return {
            "mcp_servers": {
                self.pairing_mcp_server_name: {
                    "command": sys.executable,
                    "args": ["-m", "yier_web.codex_pairing_mcp"],
                    "cwd": str(project_root.resolve()),
                    "env": {
                        "YIER_PAIRING_HOME_DIR": str(home_dir.resolve()),
                    },
                    "startup_timeout_sec": 5,
                    "tool_timeout_sec": 30,
                }
            }
        }

    def _thread_runtime_status_payload(
        self,
        raw_status: Any,
        fallback_status: str,
        fallback_active_flags: list[str],
    ) -> dict[str, Any]:
        payload = raw_status.get("root") if isinstance(raw_status, dict) else None
        if not isinstance(payload, dict):
            payload = raw_status
        if isinstance(payload, dict):
            status_type = payload.get("type")
            active_flags = payload.get("activeFlags")
            if not isinstance(active_flags, list):
                active_flags = payload.get("active_flags")
            if isinstance(status_type, str):
                return {
                    "type": status_type,
                    "activeFlags": (
                        [
                            str(
                                flag.get("value")
                                if isinstance(flag, dict) and "value" in flag
                                else flag
                            )
                            for flag in active_flags
                        ]
                        if isinstance(active_flags, list)
                        else []
                    ),
                }
        return {
            "type": fallback_status,
            "activeFlags": list(fallback_active_flags),
        }

    def _normalize_ipc_turn(
        self,
        context: ChatSessionContext,
        turn: dict[str, Any],
        snapshot: TurnSnapshotState | None,
    ) -> dict[str, Any]:
        normalized = dict(turn)
        turn_id = turn.get("id")
        if isinstance(turn_id, str) and turn_id:
            normalized["turnId"] = turn_id
        params = None
        if snapshot is not None:
            params = dict(snapshot.params)
            normalized["turnStartedAtMs"] = snapshot.turn_started_at_ms
            normalized["finalAssistantStartedAtMs"] = snapshot.final_assistant_started_at_ms
        else:
            normalized["turnStartedAtMs"] = turn.get("turnStartedAtMs")
            normalized["finalAssistantStartedAtMs"] = turn.get("finalAssistantStartedAtMs")
            params = self._fallback_turn_state_params(context, turn)
        normalized["items"] = self._normalized_ipc_turn_items(
            turn,
            snapshot,
        )
        normalized["params"] = params
        return normalized

    def _normalized_ipc_turn_items(
        self,
        turn: dict[str, Any],
        snapshot: TurnSnapshotState | None,
    ) -> list[dict[str, Any]]:
        raw_items = turn.get("items")
        items = [dict(item) for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        if snapshot is None:
            return items

        assistant_text = snapshot.assistant_text
        assistant_item_id = snapshot.assistant_item_id
        has_agent_message = False
        for item in items:
            if item.get("type") != "agentMessage":
                continue
            has_agent_message = True
            if isinstance(assistant_item_id, str) and assistant_item_id:
                item["id"] = assistant_item_id
            if assistant_text:
                item["text"] = assistant_text
            elif "text" not in item:
                item["text"] = ""
            if "phase" not in item:
                item["phase"] = "final_answer"
            if "memoryCitation" not in item:
                item["memoryCitation"] = None

        should_add_assistant_item = (
            (isinstance(assistant_item_id, str) and assistant_item_id)
            or assistant_text
            or snapshot.final_assistant_started_at_ms is not None
        )
        if not has_agent_message and should_add_assistant_item:
            items.append(
                {
                    "type": "agentMessage",
                    "id": (
                        assistant_item_id
                        if isinstance(assistant_item_id, str) and assistant_item_id
                        else f"{turn.get('id')}:assistant"
                    ),
                    "text": assistant_text,
                    "phase": "final_answer",
                    "memoryCitation": None,
                }
            )

        return items

    def _build_turn_state_params(
        self,
        context: ChatSessionContext,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any]:
        turn_params = self._turn_params(context)
        return {
            "threadId": context.backend_state.get("thread_id") or context.session_id,
            "input": self._normalize_input_items_for_state(input_payload),
            "cwd": turn_params.get("cwd"),
            "approvalPolicy": turn_params.get("approval_policy"),
            "approvalsReviewer": turn_params.get("approvals_reviewer"),
            "sandboxPolicy": turn_params.get("sandbox_policy"),
            "model": turn_params.get("model"),
            "serviceTier": turn_params.get("service_tier"),
            "effort": turn_params.get("effort"),
            "summary": "none",
            "personality": turn_params.get("personality"),
            "outputSchema": None,
            "collaborationMode": self._context_collaboration_mode(
                context,
                default_model=turn_params.get("model"),
                default_effort=turn_params.get("effort"),
            ),
            "attachments": [],
        }

    def _default_turn_state_params(self, context: ChatSessionContext) -> dict[str, Any]:
        return self._build_turn_state_params(context, [])

    def _fallback_turn_state_params(
        self,
        context: ChatSessionContext,
        turn: dict[str, Any],
    ) -> dict[str, Any]:
        params = self._default_turn_state_params(context)
        input_items = self._turn_input_items_from_thread_items(turn.get("items"))
        if input_items:
            params["input"] = input_items
        return params

    def _normalize_input_items_for_state(
        self,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> list[dict[str, Any]]:
        if isinstance(input_payload, str):
            return [{"type": "text", "text": input_payload}]
        if isinstance(input_payload, dict):
            return [dict(input_payload)]
        normalized: list[dict[str, Any]] = []
        for item in input_payload:
            if isinstance(item, dict):
                normalized.append(dict(item))
        return normalized

    def _turn_input_items_from_thread_items(self, items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        for raw_item in items:
            item = self._unwrap_thread_item(raw_item)
            if not isinstance(item, dict):
                dumped = self._safe_model_dump(item)
                item = dumped if isinstance(dumped, dict) else {}
            if item.get("type") != "userMessage":
                continue
            content = item.get("content")
            if isinstance(content, list):
                return [entry for entry in content if isinstance(entry, dict)]
        return []

    async def _close_runtime(self, runtime: CodexSessionRuntime) -> None:
        for pending in runtime.pending_requests.values():
            pending.decision = "cancel"
            pending.response = {"decision": "cancel"}
            pending.event.set()
        if runtime.client is not None:
            try:
                await asyncio.to_thread(runtime.client.close)
            except (AppServerError, TransportClosedError):
                pass

    def _emit_from_thread(
        self,
        runtime: CodexSessionRuntime,
        event: str,
        data: dict[str, Any],
        *,
        wait: bool = False,
    ) -> None:
        if runtime.loop is None or runtime.emit is None:
            return
        future = asyncio.run_coroutine_threadsafe(runtime.emit(event, data), runtime.loop)
        if wait:
            future.result()

    def _codex_work_mode(self, context: ChatSessionContext) -> str:
        metadata = self.chat_service.get_session_metadata(context.session_id)
        work_mode = metadata.get("codex_work_mode")
        return work_mode if work_mode in {"plan", "build"} else "build"

    def _thread_view_payload(
        self,
        context: ChatSessionContext,
        thread: Any,
    ) -> dict[str, Any]:
        messages: list[StoredSessionMessage] = []
        activity_events: list[dict[str, Any]] = []

        turns = getattr(thread, "turns", []) or []
        for turn in turns:
            for raw_item in getattr(turn, "items", []) or []:
                item = self._unwrap_thread_item(raw_item)
                if item is None:
                    continue
                self._append_thread_item_view(context, item, messages, activity_events)

        title = getattr(thread, "name", None) or getattr(thread, "preview", None) or getattr(thread, "id", "Codex session")
        preview = getattr(thread, "preview", None) or title
        updated_at = float(getattr(thread, "updated_at", 0) or 0)
        return {
            "title": title,
            "preview": preview,
            "updated_at": updated_at,
            "messages": messages,
            "activity_events": activity_events,
        }

    def _append_thread_item_view(
        self,
        context: ChatSessionContext,
        item: Any,
        messages: list[StoredSessionMessage],
        activity_events: list[dict[str, Any]],
    ) -> None:
        item_type = getattr(item, "type", "")
        if item_type == "userMessage":
            content = self._thread_user_message_text(getattr(item, "content", []))
            if content:
                messages.append(
                    StoredSessionMessage(
                        role="user",
                        content=content,
                        source=context.source,
                        channel_meta=context.channel_meta,
                    )
                )
            return

        if item_type == "agentMessage":
            content = getattr(item, "text", "") or ""
            if content.strip():
                messages.append(
                    StoredSessionMessage(
                        role="assistant",
                        content=content,
                        source=context.source,
                        channel_meta=context.channel_meta,
                    )
                )
            return

        if item_type == "plan":
            self._append_activity_event(
                activity_events,
                "plan",
                {
                    "session_id": context.session_id,
                    "item_id": item.id,
                    "content": getattr(item, "text", ""),
                    "iteration": 0,
                },
            )
            return

        if item_type == "reasoning":
            content = "\n".join(getattr(item, "summary", []) or getattr(item, "content", []) or [])
            if content.strip():
                self._append_activity_event(
                    activity_events,
                    "reasoning",
                    {
                        "session_id": context.session_id,
                        "item_id": item.id,
                        "content": content,
                        "iteration": 0,
                    },
                )
            return

        if item_type == "commandExecution":
            self._append_activity_event(
                activity_events,
                "command_start",
                {
                    "session_id": context.session_id,
                    "tool_call_id": item.id,
                    "tool_name": "command_execution",
                    "command": getattr(item, "command", ""),
                    "cwd": getattr(item, "cwd", ""),
                    "is_background": False,
                },
            )
            output = getattr(item, "aggregated_output", None)
            if isinstance(output, str) and output:
                self._append_activity_event(
                    activity_events,
                    "command_output",
                    {
                        "session_id": context.session_id,
                        "tool_call_id": item.id,
                        "tool_name": "command_execution",
                        "stream": "stdout",
                        "content": output,
                        "is_background": False,
                    },
                )
            self._append_activity_event(
                activity_events,
                "command_end",
                {
                    "session_id": context.session_id,
                    "tool_call_id": item.id,
                    "tool_name": "command_execution",
                    "command": getattr(item, "command", ""),
                    "cwd": getattr(item, "cwd", ""),
                    "exit_code": int(getattr(item, "exit_code", 0) or 0),
                    "timed_out": False,
                    "is_background": False,
                },
            )
            return

        if item_type == "fileChange":
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                tool_name="file_change",
                tool_call_id=item.id,
                arguments={"change_count": len(getattr(item, "changes", []) or [])},
                result=self._summarize_file_change(item),
                is_error=getattr(item, "status", "") not in {"completed", "inProgress"},
                metadata={
                    "status": getattr(item, "status", ""),
                    "change_count": len(getattr(item, "changes", []) or []),
                },
            )
            return

        if item_type == "mcpToolCall":
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                tool_name=f"mcp:{getattr(item, 'server', 'unknown')}/{getattr(item, 'tool', 'tool')}",
                tool_call_id=item.id,
                arguments=self._safe_model_dump(getattr(item, "arguments", {})),
                result=self._summarize_tool_result(item),
                is_error=getattr(item, "error", None) is not None,
                metadata={"status": getattr(item, "status", "")},
            )
            return

        if item_type == "dynamicToolCall":
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                tool_name=f"dynamic:{getattr(item, 'tool', 'tool')}",
                tool_call_id=item.id,
                arguments=self._safe_model_dump(getattr(item, "arguments", {})),
                result=self._summarize_tool_result(item),
                is_error=getattr(item, "success", True) is False,
                metadata={"status": getattr(item, "status", "")},
            )
            return

        if item_type == "collabAgentToolCall":
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                tool_name=f"collab:{getattr(item, 'tool', 'tool')}",
                tool_call_id=item.id,
                arguments={
                    "receiver_thread_ids": list(getattr(item, "receiver_thread_ids", []) or []),
                    "prompt": getattr(item, "prompt", None),
                },
                result=self._summarize_collab_result(item),
                is_error=getattr(item, "status", "") not in {"completed", "inProgress"},
                metadata={"status": getattr(item, "status", "")},
            )
            return

        if item_type == "webSearch":
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                tool_name="web_search",
                tool_call_id=item.id,
                arguments={"query": getattr(item, "query", "")},
                result=f"Searched the web for {getattr(item, 'query', '')}.",
                is_error=False,
                metadata={},
            )

    def _append_tool_activity_events(
        self,
        session_id: str,
        activity_events: list[dict[str, Any]],
        *,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        result: str,
        is_error: bool,
        metadata: dict[str, Any],
    ) -> None:
        self._append_activity_event(
            activity_events,
            "tool_call_start",
            {
                "session_id": session_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "arguments": arguments,
                "iteration": 0,
            },
        )
        self._append_activity_event(
            activity_events,
            "tool_call_end",
            {
                "session_id": session_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "result": result,
                "is_error": is_error,
                "metadata": metadata,
                "raw": None,
                "iteration": 0,
            },
        )

    def _append_activity_event(
        self,
        activity_events: list[dict[str, Any]],
        event: str,
        data: dict[str, Any],
    ) -> None:
        activity_events.append({"event": event, "data": data})

    def _accumulate_reasoning_delta(
        self,
        runtime: CodexSessionRuntime,
        item_id: str,
        method: str,
        delta: str,
    ) -> str:
        buffer = runtime.reasoning_buffers.setdefault(
            item_id,
            {"content": "", "summary": ""},
        )
        if method == "item/reasoning/summaryTextDelta":
            buffer["summary"] = f"{buffer['summary']}{delta}"
        else:
            buffer["content"] = f"{buffer['content']}{delta}"
        return buffer["summary"] or buffer["content"]

    def _accumulate_plan_delta(
        self,
        runtime: CodexSessionRuntime,
        activity_id: str,
        delta: str,
    ) -> str:
        runtime.plan_buffers[activity_id] = f"{runtime.plan_buffers.get(activity_id, '')}{delta}"
        return runtime.plan_buffers[activity_id]

    def _thread_user_message_text(self, contents: Any) -> str:
        if not isinstance(contents, list):
            return ""

        parts: list[str] = []
        for item in contents:
            root = getattr(item, "root", item)
            item_type = getattr(root, "type", None)
            if item_type != "text":
                continue
            text = getattr(root, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if not parts:
            return ""
        return self._strip_plan_mode_prompt("\n".join(parts))

    def _strip_plan_mode_prompt(self, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(PLAN_MODE_PROMPT_PREFIX):
            return normalized
        _, _, remainder = normalized.partition("User request:")
        return remainder.strip() if remainder.strip() else normalized

    def _notification_belongs_to_turn(self, notification: Notification, turn_id: str) -> bool:
        notification_turn_id = self._notification_value(notification, "turn_id")
        if notification_turn_id is None:
            return notification.method.startswith("thread/") or notification.method == "serverRequest/resolved"
        return notification_turn_id == turn_id

    def _notification_value(self, notification: Notification, attribute: str) -> Any:
        payload = notification.payload
        return getattr(payload, attribute, None)

    def _unwrap_thread_item(self, item: Any) -> Any:
        if item is None:
            return None
        return getattr(item, "root", item)

    def _thread_status_value(self, status: Any) -> str:
        root = getattr(status, "root", status)
        return getattr(root, "type", "idle")

    def _thread_active_flags(self, status: Any) -> list[str]:
        root = getattr(status, "root", status)
        raw_flags = getattr(root, "active_flags", None)
        if raw_flags is None:
            raw_flags = getattr(root, "activeFlags", [])
        return [getattr(flag, "value", str(flag)) for flag in raw_flags]

    def _request_id_string(self, request_id: Any) -> str:
        root = getattr(request_id, "root", request_id)
        if root is None:
            return ""
        return str(root)

    def _context_string_override(
        self,
        context: ChatSessionContext,
        key: str,
    ) -> str:
        value = context.backend_state.get(key)
        if not isinstance(value, str):
            return ""
        return value.strip()

    def _context_collaboration_mode(
        self,
        context: ChatSessionContext,
        *,
        default_model: Any,
        default_effort: Any,
    ) -> dict[str, Any]:
        raw_value = context.backend_state.get("collaboration_mode")
        default_settings = {
            "model": default_model if isinstance(default_model, str) else "",
            "reasoning_effort": (
                default_effort if default_effort is None or isinstance(default_effort, str) else None
            ),
            "developer_instructions": None,
        }
        default_mode = {
            "mode": "default",
            "settings": default_settings,
        }
        if isinstance(raw_value, dict):
            mode = raw_value.get("mode")
            settings = raw_value.get("settings")
            if isinstance(mode, str) and mode.strip():
                normalized = dict(raw_value)
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
        if isinstance(raw_value, str) and raw_value.strip():
            return {
                "mode": raw_value.strip(),
                "settings": default_settings,
            }
        return default_mode

    def _response_turn_id(self, response: Any) -> str | None:
        turn = getattr(response, "turn", None)
        turn_id = getattr(turn, "id", None)
        if isinstance(turn_id, str) and turn_id:
            return turn_id
        response_payload = self._safe_model_dump(response)
        raw_turn = response_payload.get("turn")
        if not isinstance(raw_turn, dict):
            return None
        raw_turn_id = raw_turn.get("id")
        if isinstance(raw_turn_id, str) and raw_turn_id:
            return raw_turn_id
        return None

    def _safe_model_dump(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json")
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        return {"value": value}

    def _summarize_file_change(self, item: Any) -> str:
        changes = getattr(item, "changes", []) or []
        count = len(changes)
        status = getattr(item, "status", "completed")
        return f"{count} file change{'s' if count != 1 else ''} with status {status}."

    def _summarize_tool_result(self, item: Any) -> str:
        status = getattr(item, "status", "completed")
        error = getattr(item, "error", None)
        if error is not None:
            return getattr(error, "message", f"Finished with status {status}.")
        return f"Finished with status {status}."

    def _summarize_collab_result(self, item: Any) -> str:
        receivers = getattr(item, "receiver_thread_ids", []) or []
        status = getattr(item, "status", "completed")
        return f"{len(receivers)} agent target{'s' if len(receivers) != 1 else ''}, status {status}."
