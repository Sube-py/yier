from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import shlex
import threading
from typing import TYPE_CHECKING, Any

from codex_app_server import AppServerClient, AppServerConfig
from codex_app_server.errors import AppServerError, TransportClosedError
from codex_app_server.models import Notification

from yier_agents import Message

from yier_web.agent_backends.base import ChatBackend, ChatSessionContext, StreamEmitter

if TYPE_CHECKING:
    from yier_web.chat import ChatService


DEFAULT_CODEX_LAUNCHER = "codex app-server --listen stdio://"
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
class CodexSessionRuntime:
    session_id: str
    client: AppServerClient | None = None
    thread_id: str | None = None
    status: str = "idle"
    active_flags: list[str] = field(default_factory=list)
    pending_requests: dict[str, PendingApprovalState] = field(default_factory=dict)
    assistant_buffers: dict[str, str] = field(default_factory=dict)
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
            return await asyncio.to_thread(
                self._run_turn_blocking,
                runtime,
                context,
                user_message,
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

    def _start_thread_blocking(self, runtime: CodexSessionRuntime, context: ChatSessionContext) -> None:
        assert runtime.client is not None
        response = runtime.client.thread_start(self._thread_params(context))
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

    def _resume_thread_blocking(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        thread_id: str,
    ) -> None:
        assert runtime.client is not None
        response = runtime.client.thread_resume(thread_id, self._thread_params(context))
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

    def _run_turn_blocking(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        user_message: str,
    ) -> str:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        runtime.status = "active"
        runtime.detail = None
        turn_response = runtime.client.turn_start(
            runtime.thread_id,
            user_message,
            params=self._turn_params(context),
        )
        turn_id = turn_response.turn.id
        finish_reason = "stop"

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

        return finish_reason

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
            self.chat_service.update_session_backend_state(
                context.session_id,
                {"status": runtime.status, "active_flags": runtime.active_flags},
            )
            return

        if notification.method == "item/agentMessage/delta":
            item_id = self._notification_value(notification, "item_id")
            delta = self._notification_value(notification, "delta")
            if not isinstance(item_id, str) or not isinstance(delta, str):
                return
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
            "item/plan/delta",
        }:
            delta = self._notification_value(notification, "delta")
            if isinstance(delta, str) and delta.strip():
                self._emit_from_thread(
                    runtime,
                    "reasoning",
                    {
                        "session_id": context.session_id,
                        "content": delta,
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

    def _handle_turn_completed(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        notification: Notification,
    ) -> str:
        turn = getattr(notification.payload, "turn", None)
        status = getattr(turn, "status", None)
        runtime.status = "idle" if status == "completed" else str(status or "idle")
        runtime.active_flags = []
        runtime.detail = None
        finish_reason = "stop"
        if status == "interrupted":
            finish_reason = "interrupted"
        elif status == "failed":
            finish_reason = "error"
            error = getattr(turn, "error", None)
            message = getattr(error, "message", "Codex turn failed.")
            self._emit_from_thread(
                runtime,
                "error",
                {
                    "session_id": context.session_id,
                    "message": message,
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
            request = params.get("request")
            title = "MCP server input"
            if isinstance(request, dict):
                detail = request.get("message") or detail

        return {
            "method": method,
            "kind": kind,
            "title": title,
            "detail": detail,
            "payload": params,
            "options": self._approval_options(kind),
            "item_id": item_id,
        }

    def _approval_kind(self, method: str) -> str:
        if method == "item/commandExecution/requestApproval":
            return "command"
        if method == "item/fileChange/requestApproval":
            return "file_change"
        if method == "item/permissions/requestApproval":
            return "permissions"
        if method == "mcpServer/elicitation/request":
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
        if method == "mcpServer/elicitation/request":
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
        if method == "mcpServer/elicitation/request":
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
        return {
            "cwd": str(context.project_path),
            "approvalPolicy": settings.approval_policy,
            "approvalsReviewer": settings.approvals_reviewer,
            "model": settings.model or None,
            "personality": settings.personality,
            "sandbox": _codex_thread_sandbox_mode(settings.sandbox),
            "serviceTier": settings.service_tier or None,
        }

    def _turn_params(self, context: ChatSessionContext) -> dict[str, Any]:
        settings = self.chat_service.config_service.load_web_settings().codex
        approval_policy = settings.approval_policy
        if context.source == "channel":
            approval_policy = "never"
        return {
            "cwd": str(context.project_path),
            "approvalPolicy": approval_policy,
            "approvalsReviewer": settings.approvals_reviewer,
            "effort": settings.reasoning_effort,
            "model": settings.model or None,
            "personality": settings.personality,
            "sandboxPolicy": {"type": _codex_turn_sandbox_policy_type(settings.sandbox)},
            "serviceTier": settings.service_tier or None,
        }

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
        raw_flags = getattr(root, "active_flags", [])
        return [getattr(flag, "value", str(flag)) for flag in raw_flags]

    def _request_id_string(self, request_id: Any) -> str:
        root = getattr(request_id, "root", request_id)
        if root is None:
            return ""
        return str(root)

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
