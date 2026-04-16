from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
import inspect
import logging
import os
from pathlib import Path
from time import time
from typing import TYPE_CHECKING, Any, TypedDict
from urllib.parse import urlparse
from pydantic import BaseModel

from codex_app_server import (
    AsyncAppServerClient,
    ImageInput,
    Input,
    LocalImageInput,
    MentionInput,
    SkillInput,
    ThreadResumeParams,
    ThreadStartParams,
    TextInput,
)
from codex_app_server.generated.v2_all import (
    AgentMessageDeltaNotification,
    AgentMessageThreadItem,
    CollabAgentToolCallThreadItem,
    CommandExecutionOutputDeltaNotification,
    CommandExecutionThreadItem,
    ContextCompactionThreadItem,
    DynamicToolCallThreadItem,
    EnteredReviewModeThreadItem,
    ErrorNotification as SdkErrorNotification,
    ExitedReviewModeThreadItem,
    FileChangeThreadItem,
    HookPromptThreadItem,
    ImageGenerationThreadItem,
    ImageUserInput,
    ImageViewThreadItem,
    ItemCompletedNotification,
    ItemStartedNotification,
    LocalImageUserInput,
    McpToolCallThreadItem,
    MentionUserInput,
    PlanDeltaNotification,
    PlanThreadItem,
    ReasoningThreadItem,
    ReasoningSummaryTextDeltaNotification,
    ReasoningTextDeltaNotification,
    ServerRequestResolvedNotification,
    SkillUserInput,
    TextUserInput,
    Thread as ThreadV2,
    ThreadItem,
    ThreadRealtimeErrorNotification,
    ThreadRealtimeTranscriptUpdatedNotification,
    ThreadResumeResponse,
    ThreadStartResponse,
    ThreadStartedNotification,
    ThreadStatus,
    ThreadStatusChangedNotification,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    UserInput,
    UserMessageThreadItem,
    WebSearchThreadItem,
    TurnPlanUpdatedNotification,
    TurnStartResponse,
    TurnStartedNotification,
    TurnStatus,
)
from codex_app_server.errors import AppServerError, TransportClosedError
from codex_app_server.models import Notification

from yier_agents import Message

from yier_web.agent_backends.base import ChatBackend, ChatSessionContext, StreamEmitter
from yier_web.attachments import AttachmentStorageError
from yier_web.codex.collaboration_mode import (
    codex_work_mode_from_collaboration_mode,
    normalize_protocol_collaboration_mode,
)
from yier_web.codex.runtime import (
    CodexSessionRuntime,
    PendingApprovalState,
    TurnSnapshotState,
)
from yier_web.codex.sdk.config import (
    DEFAULT_CODEX_LAUNCHER,
    PLAN_MODE_PROMPT,
    PLAN_MODE_PROMPT_PREFIX,
    build_app_server_config,
    build_pairing_mcp_config,
    normalize_codex_thread_sandbox_mode,
    normalize_codex_turn_sandbox_policy_type,
)
from yier_web.codex.sdk.client import (
    ApprovalAwareAppServerClient,
    ApprovalAwareAsyncThread,
    ApprovalAwareAsyncTurnHandle,
)
from yier_web.schemas import MessageAttachmentPayload, StoredSessionMessage

if TYPE_CHECKING:
    from yier_web.chat import ChatService

CODEX_IPC_DEBUG_ENV = "YIER_CODEX_IPC_DEBUG"
logger = logging.getLogger(__name__)

CodexThread = ApprovalAwareAsyncThread


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


type CodexThreadItemRoot = (
    UserMessageThreadItem
    | HookPromptThreadItem
    | AgentMessageThreadItem
    | PlanThreadItem
    | ReasoningThreadItem
    | CommandExecutionThreadItem
    | FileChangeThreadItem
    | McpToolCallThreadItem
    | DynamicToolCallThreadItem
    | CollabAgentToolCallThreadItem
    | WebSearchThreadItem
    | ImageViewThreadItem
    | ImageGenerationThreadItem
    | EnteredReviewModeThreadItem
    | ExitedReviewModeThreadItem
    | ContextCompactionThreadItem
)

type CodexUserInputRoot = (
    TextUserInput
    | ImageUserInput
    | LocalImageUserInput
    | SkillUserInput
    | MentionUserInput
)


class CodexThreadViewPayload(TypedDict):
    title: str
    preview: str
    updated_at: float
    messages: list[StoredSessionMessage]
    activity_events: list[dict[str, Any]]
    activity_history: dict[str, int | None]
    codex_turn_timings: list[dict[str, int | str | None]]


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
        rendered = ", ".join(f"{key}={value!r}" for key, value in fields.items())
        logger.warning(f"[codex-backend] {message} | {rendered}")
        return
    logger.warning(f"[codex-backend] {message}")


def _codex_thread_sandbox_mode(value: str) -> str:
    return normalize_codex_thread_sandbox_mode(value)


def _codex_turn_sandbox_policy_type(value: str) -> str:
    return normalize_codex_turn_sandbox_policy_type(value)


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

    async def bootstrap_session(
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
            await self._bootstrap_runtime(runtime, context, persist=False)
        except Exception:
            if runtime.client is not None:
                try:
                    await _maybe_await(runtime.client.close())
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

    async def _bootstrap_runtime(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        *,
        persist: bool,
    ) -> None:
        runtime.client = await _maybe_await(self._start_client(runtime, context))
        await self._start_thread(runtime, context, persist=persist)

    async def stream_chat(
        self,
        context: ChatSessionContext,
        user_message: list[dict[str, Any]] | dict[str, Any] | str,
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
            response = await self._start_turn(
                runtime,
                context,
                user_message,
            )
            turn_id = self._response_turn_id(response)
            if not turn_id:
                raise RuntimeError(
                    "Codex turn_start response did not include a turn id."
                )
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
            return await self._consume_turn_stream(
                runtime,
                context,
                turn_id,
            )
        finally:
            runtime.emit = None

    def runtime_payload(self, context: ChatSessionContext) -> dict[str, Any]:
        runtime = self._runtimes.get(context.session_id)
        thread_id = (
            runtime.thread_id if runtime else context.backend_state.get("thread_id")
        )
        status = (
            runtime.status if runtime else context.backend_state.get("status", "idle")
        )
        active_flags = (
            runtime.active_flags
            if runtime
            else list(context.backend_state.get("active_flags", []))
        )
        detail = runtime.detail if runtime else context.backend_state.get("detail")
        pending_count = len(runtime.pending_requests) if runtime else 0
        if runtime is None:
            ipc_state = context.backend_state.get("ipc_conversation_state")
            if isinstance(ipc_state, dict):
                trs = ipc_state.get("threadRuntimeStatus")
                if isinstance(trs, dict):
                    trs_type = trs.get("type")
                    if isinstance(trs_type, str) and trs_type:
                        status = trs_type
                    trs_flags = trs.get("activeFlags")
                    if isinstance(trs_flags, list):
                        active_flags = [
                            str(f.get("value") if isinstance(f, dict) else f)
                            for f in trs_flags
                        ]
                # Override: if the latest turn is inProgress, always report active
                turns = ipc_state.get("turns")
                if isinstance(turns, list) and turns:
                    last_turn = turns[-1]
                    if isinstance(last_turn, dict):
                        last_status = last_turn.get("status")
                        if last_status == "inProgress":
                            status = "active"
                        _codex_ipc_debug_log(
                            "runtime_payload ipc fallback",
                            session_id=context.session_id,
                            trs_type=trs_type,
                            last_turn_status=last_status,
                            resolved_status=status,
                        )
        return {
            "backend_id": self.backend_id,
            "label": self.label,
            "ready": True,
            "status": status,
            "thread_id": thread_id,
            "active_flags": active_flags,
            "detail": detail,
            "pending_request_count": pending_count,
            "pending_approval_count": pending_count,
            "ipc_owner_client_id": context.backend_state.get("ipc_source_client_id"),
        }

    def pending_requests(self, context: ChatSessionContext) -> list[dict[str, Any]]:
        runtime = self._runtimes.get(context.session_id)
        if runtime is None:
            return []
        return [
            pending.record
            for pending in runtime.pending_requests.values()
            if not pending.event.is_set()
        ]

    def pending_approvals(self, context: ChatSessionContext) -> list[dict[str, Any]]:
        return self.pending_requests(context)

    def pending_conversation_requests(
        self, context: ChatSessionContext
    ) -> list[dict[str, Any]]:
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

    async def respond_to_pending_request(
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
        if pending.method == "item/tool/requestUserInput":
            pending.response = (
                dict(content) if isinstance(content, dict) else {"answers": {}}
            )
        elif pending.method == "item/plan/requestImplementation":
            pending.response = (
                dict(content) if isinstance(content, dict) else {}
            )
        else:
            pending.response = self._build_approval_response(
                method=pending.method,
                params=pending.payload,
                decision=decision,
                content=content,
            )
        pending.event.set()
        return True

    async def respond_to_approval(
        self,
        context: ChatSessionContext,
        request_id: str,
        decision: str,
        content: dict[str, Any] | None = None,
    ) -> bool:
        return await self.respond_to_pending_request(
            context,
            request_id,
            decision,
            content,
        )

    async def steer_turn(
        self,
        context: ChatSessionContext,
        turn_id: str | None,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any]:
        runtime = await self._ensure_runtime(context)
        assert runtime.client is not None
        assert runtime.thread_id is not None
        resolved_turn_id = turn_id or await self._resolve_active_turn_id(
            runtime,
            context,
        )
        if not resolved_turn_id:
            raise RuntimeError("No active Codex turn found for steer request.")
        turn_handle = self._turn_handle(runtime, resolved_turn_id)
        response = await turn_handle.steer(self._sdk_input_from_payload(input_payload))
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
        response = await self._start_turn(
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
            return await self._consume_turn_stream(
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
        resolved_turn_id = turn_id or await self._resolve_active_turn_id(
            runtime,
            context,
        )
        if not resolved_turn_id:
            raise RuntimeError("No active Codex turn found for interrupt request.")
        turn_handle = self._turn_handle(runtime, resolved_turn_id)
        response = await turn_handle.interrupt()
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

    async def load_thread_state(self, context: ChatSessionContext) -> dict[str, Any]:
        runtime = await self._ensure_runtime(context)
        assert runtime.client is not None
        assert runtime.thread_id is not None
        if runtime.streaming_turn_id is not None:
            cached_payload = self._cached_thread_state_payload(runtime)
            if cached_payload is not None:
                return cached_payload
        response = await self._read_thread(runtime, include_turns=True)
        self._update_runtime_from_thread(context, runtime, response.thread)
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
        if normalized_turns and self._can_merge_snapshot_into_last_turn(
            context,
            normalized_turns[-1],
            snapshot,
        ):
            last_turn = normalized_turns[-1]
            last_turn["id"] = turn_id
            last_turn["turnId"] = turn_id
            last_turn["status"] = "inProgress"
            last_turn["error"] = last_turn.get("error")
            last_turn["diff"] = last_turn.get("diff")
            last_turn["turnStartedAtMs"] = snapshot.turn_started_at_ms
            last_turn["finalAssistantStartedAtMs"] = (
                snapshot.final_assistant_started_at_ms
            )
            last_turn["params"] = dict(snapshot.params)
            last_turn["items"] = self._normalized_ipc_turn_items(
                {
                    "id": turn_id,
                    "items": last_turn.get("items", []),
                },
                snapshot,
            )
            return normalized_turns

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

    async def load_thread_view(
        self,
        context: ChatSessionContext,
        *,
        activity_limit: int | None = None,
    ) -> CodexThreadViewPayload:
        runtime = await self._ensure_runtime(context)
        assert runtime.client is not None
        assert runtime.thread_id is not None
        response = await self._read_thread(runtime, include_turns=True)
        self._update_runtime_from_thread(context, runtime, response.thread)
        return self._thread_view_payload(
            context,
            response.thread,
            activity_limit=activity_limit,
        )

    def build_thread_view(
        self,
        context: ChatSessionContext,
        thread: ThreadV2,
        *,
        activity_limit: int | None = None,
    ) -> CodexThreadViewPayload:
        return self._thread_view_payload(
            context,
            thread,
            activity_limit=activity_limit,
        )

    def should_use_local_session_view(self, context: ChatSessionContext) -> bool:
        runtime = self._runtimes.get(context.session_id)
        if runtime is None:
            return False
        return runtime.status == "active"

    async def _ensure_runtime(self, context: ChatSessionContext) -> CodexSessionRuntime:
        runtime = self._runtimes.get(context.session_id)
        if runtime is None:
            runtime = CodexSessionRuntime(session_id=context.session_id)
            self._runtimes[context.session_id] = runtime
            runtime.client = await _maybe_await(self._start_client(runtime, context))

        if runtime.thread_id:
            return runtime

        thread_id = context.backend_state.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            await self._resume_thread(runtime, context, thread_id)
        else:
            await self._start_thread(runtime, context)
        return runtime

    async def _start_client(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
    ) -> AsyncAppServerClient:
        codex_settings = self.chat_service.config_service.load_web_settings().codex
        launcher_command = codex_settings.launcher_command or DEFAULT_CODEX_LAUNCHER
        client = ApprovalAwareAppServerClient(
            config=build_app_server_config(
                launcher_command=launcher_command,
                cwd=str(context.project_path),
                client_name="yier_web",
                client_title="Yier Web",
            ),
            approval_callback=lambda request_id, method, params: (
                self._handle_server_request(
                    runtime,
                    context,
                    request_id,
                    method,
                    params,
                )
            ),
        )
        await client.start()
        await client.initialize()
        return client

    async def _start_thread(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        *,
        persist: bool = True,
    ) -> None:
        assert runtime.client is not None
        response: ThreadStartResponse = await _maybe_await(
            runtime.client.thread_start(
                ThreadStartParams(**self._thread_params(context))
            )
        )
        runtime.thread_id = response.thread.id
        self._apply_thread_snapshot(runtime, response.thread)
        if persist and context.session_id:
            self._persist_runtime_status(context, runtime)

    async def _resume_thread(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        thread_id: str,
    ) -> None:
        assert runtime.client is not None
        response: ThreadResumeResponse = await _maybe_await(
            runtime.client.thread_resume(
                thread_id,
                ThreadResumeParams(thread_id=thread_id, **self._thread_params(context)),
            )
        )
        runtime.thread_id = response.thread.id
        self._apply_thread_snapshot(runtime, response.thread)
        self._persist_runtime_status(context, runtime)

    async def _resolve_active_turn_id(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
    ) -> str | None:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        response = await self._read_thread(runtime, include_turns=True)
        turns = list(response.thread.turns)
        for turn in reversed(turns):
            status = self._turn_status_value(turn.status)
            turn_id = turn.id
            if status not in {"completed", "failed", "interrupted"}:
                return turn_id
        for turn in reversed(turns):
            turn_id = turn.id
            if turn_id:
                return turn_id
        return None

    async def _start_runtime_turn(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        turn_input: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> tuple[Any | None, TurnStartResponse | BaseModel | dict[str, Any], str | None]:
        if isinstance(runtime.client, AsyncAppServerClient):
            return await self._start_public_sdk_turn(runtime, context, turn_input)
        return await self._start_low_level_turn(runtime, context, turn_input)

    async def _start_public_sdk_turn(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        turn_input: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> tuple[Any, dict[str, Any], str]:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        thread_handle = self._thread_handle(runtime.client, runtime.thread_id)
        turn_handle = await thread_handle.turn(
            self._sdk_input_from_payload(turn_input),
            **self._turn_params(context),
        )
        turn_id = turn_handle.id
        return (
            turn_handle,
            {
                "turn": {
                    "id": turn_id,
                    "status": "inProgress",
                    "items": [],
                }
            },
            turn_id,
        )

    async def _start_low_level_turn(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        turn_input: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> tuple[None, TurnStartResponse | BaseModel | dict[str, Any], str | None]:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        # Unit tests use lightweight fake clients that implement only the
        # low-level shape; production runtimes use the public SDK branch.
        response = await _maybe_await(
            runtime.client.turn_start(
                runtime.thread_id,
                turn_input,
                params=self._turn_params(context),
            )
        )
        return (None, response, self._response_turn_id(response))

    async def _start_turn(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> TurnStartResponse | BaseModel | dict[str, Any]:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        runtime.status = "active"
        runtime.detail = None
        turn_input = self._normalize_turn_input_payload(context, input_payload)
        _codex_ipc_debug_log(
            "_start_turn before client.turn_start",
            session_id=context.session_id,
            thread_id=runtime.thread_id,
            input_type=type(turn_input).__name__,
        )
        turn_handle, response, turn_id = await self._start_runtime_turn(
            runtime,
            context,
            turn_input,
        )
        if not turn_id:
            raise RuntimeError("Codex turn_start response did not include a turn id.")
        _codex_ipc_debug_log(
            "_start_turn after client.turn_start",
            session_id=context.session_id,
            thread_id=runtime.thread_id,
            turn_id=turn_id,
        )
        if turn_handle is not None:
            runtime.turn_handles[turn_id] = turn_handle
        runtime.turn_snapshots[turn_id] = TurnSnapshotState(
            params=self._build_turn_state_params(context, turn_input),
            turn_started_at_ms=int(time() * 1000),
            final_assistant_started_at_ms=None,
        )
        self._upsert_cached_thread_turn(runtime, turn_id, status="inProgress")
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

    async def _consume_turn_stream(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        turn_id: str,
    ) -> str:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        finish_reason = "stop"
        runtime.streaming_turn_id = turn_id
        try:
            turn_handle = self._turn_handle(runtime, turn_id)
            async for notification in turn_handle.stream():
                notification_thread_id = self._notification_value(
                    notification, "thread_id"
                )
                if (
                    notification_thread_id
                    and notification_thread_id != runtime.thread_id
                ):
                    continue
                if not self._notification_belongs_to_turn(notification, turn_id):
                    if notification.method.startswith("thread/"):
                        self._handle_thread_notification(runtime, context, notification)
                        await self._flush_pending_runtime_emits(runtime)
                    continue

                if notification.method == "turn/completed":
                    finish_reason = self._handle_turn_completed(
                        runtime, context, notification
                    )
                    await self._flush_pending_runtime_emits(runtime)
                    break

                self._handle_turn_notification(runtime, context, notification)
                await self._flush_pending_runtime_emits(runtime)
        finally:
            await self._flush_pending_runtime_emits(runtime)
            runtime.turn_handles.pop(turn_id, None)
            runtime.streaming_turn_id = None

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

    def _sdk_input_from_payload(
        self,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> Input:
        if isinstance(input_payload, str):
            return TextInput(input_payload)
        if isinstance(input_payload, dict):
            return self._sdk_input_item_from_payload(input_payload)

        items = [
            self._sdk_input_item_from_payload(item)
            for item in input_payload
            if isinstance(item, dict)
        ]
        if not items:
            return TextInput("")
        return items

    def _sdk_input_item_from_payload(self, item: dict[str, Any]) -> Any:
        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text")
            return TextInput(text if isinstance(text, str) else "")
        if item_type == "image":
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError("Codex image input requires a non-empty url.")
            return ImageInput(url.strip())
        if item_type == "localImage":
            path = item.get("path")
            if not isinstance(path, str) or not path.strip():
                raise ValueError("Codex localImage input requires a non-empty path.")
            return LocalImageInput(path.strip())
        if item_type == "skill":
            name = item.get("name")
            path = item.get("path")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(path, str)
                or not path.strip()
            ):
                raise ValueError("Codex skill input requires non-empty name and path.")
            return SkillInput(name.strip(), path.strip())
        if item_type == "mention":
            name = item.get("name")
            path = item.get("path")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(path, str)
                or not path.strip()
            ):
                raise ValueError(
                    "Codex mention input requires non-empty name and path."
                )
            return MentionInput(name.strip(), path.strip())
        raise ValueError(f"Unsupported Codex input item type: {item_type}")

    def _handle_thread_notification(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        notification: Notification,
    ) -> None:
        payload = notification.payload
        if (
            isinstance(payload, ThreadStatusChangedNotification)
            or notification.method == "thread/status/changed"
        ):
            status = (
                payload.status
                if isinstance(payload, ThreadStatusChangedNotification)
                else getattr(payload, "status", None)
            )
            runtime.status = self._thread_status_value(status)
            runtime.active_flags = self._thread_active_flags(status)
            if runtime.thread_state_cache is not None:
                runtime.thread_state_cache["status"] = self._safe_json_value(status)
        elif (
            isinstance(payload, ThreadTokenUsageUpdatedNotification)
            or notification.method == "thread/tokenUsage/updated"
        ):
            token_usage = (
                payload.token_usage
                if isinstance(payload, ThreadTokenUsageUpdatedNotification)
                else getattr(payload, "token_usage", None)
            )
            normalized_token_usage = self._safe_model_dump(token_usage)
            if isinstance(normalized_token_usage, dict):
                self.chat_service.update_session_backend_state(
                    context.session_id,
                    {"latest_token_usage_info": normalized_token_usage},
                )
                self._emit_from_thread(
                    runtime,
                    "token_usage_updated",
                    {
                        "session_id": context.session_id,
                        "token_usage": normalized_token_usage,
                    },
                )
            return
        elif (
            isinstance(payload, ThreadStartedNotification)
            or notification.method == "thread/started"
        ):
            thread = (
                payload.thread
                if isinstance(payload, ThreadStartedNotification)
                else getattr(payload, "thread", None)
            )
            if isinstance(thread, ThreadV2):
                runtime.thread_id = thread.id
                runtime.status = self._thread_status_value(thread.status)
                runtime.active_flags = self._thread_active_flags(thread.status)
                self._cache_thread_state(runtime, thread)
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
        payload = notification.payload
        if (
            isinstance(payload, TurnStartedNotification)
            or notification.method == "turn/started"
        ):
            runtime.status = "active"
            runtime.active_flags = []
            runtime.realtime_transcript_buffers.clear()
            turn_id = (
                payload.turn.id
                if isinstance(payload, TurnStartedNotification)
                else getattr(getattr(payload, "turn", None), "id", None)
            )
            if not isinstance(turn_id, str) or not turn_id:
                return
            snapshot = runtime.turn_snapshots.get(turn_id)
            if snapshot is None:
                snapshot = TurnSnapshotState(
                    params=self._default_turn_state_params(context),
                )
                runtime.turn_snapshots[turn_id] = snapshot
            if snapshot.turn_started_at_ms is None:
                snapshot.turn_started_at_ms = int(time() * 1000)
            self._upsert_cached_thread_turn(runtime, turn_id, status="inProgress")
            self.chat_service.update_session_backend_state(
                context.session_id,
                {"status": runtime.status, "active_flags": runtime.active_flags},
            )
            return

        if (
            isinstance(payload, ThreadRealtimeErrorNotification)
            or notification.method == "thread/realtime/error"
        ):
            message = (
                payload.message
                if isinstance(payload, ThreadRealtimeErrorNotification)
                else getattr(payload, "message", None)
            )
            thread_id = (
                payload.thread_id
                if isinstance(payload, ThreadRealtimeErrorNotification)
                else getattr(payload, "thread_id", None)
            )
            if isinstance(message, str) and message.strip():
                self._emit_from_thread(
                    runtime,
                    "stream_error",
                    {
                        "session_id": context.session_id,
                        "message": message,
                        "thread_id": thread_id
                        if isinstance(thread_id, str) and thread_id
                        else runtime.thread_id,
                        "turn_id": None,
                        "code": None,
                        "will_retry": False,
                    },
                )
            return

        if (
            isinstance(payload, ThreadRealtimeTranscriptUpdatedNotification)
            or notification.method == "thread/realtime/transcriptUpdated"
        ):
            role = (
                payload.role
                if isinstance(payload, ThreadRealtimeTranscriptUpdatedNotification)
                else getattr(payload, "role", None)
            )
            text = (
                payload.text
                if isinstance(payload, ThreadRealtimeTranscriptUpdatedNotification)
                else getattr(payload, "text", None)
            )
            turn_id = runtime.streaming_turn_id
            if (
                role != "assistant"
                or not isinstance(text, str)
                or not text
                or not turn_id
            ):
                return
            transcript_key = self._transcript_buffer_key(turn_id, role)
            previous_text = runtime.realtime_transcript_buffers.get(transcript_key, "")
            runtime.realtime_transcript_buffers[transcript_key] = text
            snapshot = runtime.turn_snapshots.get(turn_id)
            item_id = self._assistant_transcript_item_id(runtime, turn_id)
            if snapshot is not None:
                if snapshot.final_assistant_started_at_ms is None:
                    snapshot.final_assistant_started_at_ms = int(time() * 1000)
                snapshot.assistant_item_id = item_id
                snapshot.assistant_text = text
            runtime.assistant_buffers[item_id] = text
            delta = self._transcript_delta(previous_text, text)
            if not delta:
                return
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

        if isinstance(payload, SdkErrorNotification) or notification.method == "error":
            error = (
                payload.error
                if isinstance(payload, SdkErrorNotification)
                else getattr(payload, "error", None)
            )
            message = getattr(error, "message", None)
            if isinstance(message, str) and message.strip():
                code = getattr(error, "code", None)
                thread_id = (
                    payload.thread_id
                    if isinstance(payload, SdkErrorNotification)
                    else getattr(payload, "thread_id", None)
                )
                turn_id = (
                    payload.turn_id
                    if isinstance(payload, SdkErrorNotification)
                    else getattr(payload, "turn_id", None)
                )
                will_retry = (
                    payload.will_retry
                    if isinstance(payload, SdkErrorNotification)
                    else bool(getattr(payload, "will_retry", False))
                )
                self._emit_from_thread(
                    runtime,
                    "stream_error",
                    {
                        "session_id": context.session_id,
                        "message": message,
                        "thread_id": thread_id
                        if isinstance(thread_id, str) and thread_id
                        else runtime.thread_id,
                        "turn_id": turn_id
                        if isinstance(turn_id, str) and turn_id
                        else None,
                        "code": str(code) if code is not None else None,
                        "will_retry": will_retry,
                    },
                )
            return

        if (
            isinstance(payload, AgentMessageDeltaNotification)
            or notification.method == "item/agentMessage/delta"
        ):
            item_id = (
                payload.item_id
                if isinstance(payload, AgentMessageDeltaNotification)
                else getattr(payload, "item_id", None)
            )
            delta = (
                payload.delta
                if isinstance(payload, AgentMessageDeltaNotification)
                else getattr(payload, "delta", None)
            )
            turn_id = (
                payload.turn_id
                if isinstance(payload, AgentMessageDeltaNotification)
                else getattr(payload, "turn_id", None)
            )
            if (
                not isinstance(item_id, str)
                or not isinstance(delta, str)
                or not isinstance(turn_id, str)
                or not turn_id
            ):
                return
            snapshot = runtime.turn_snapshots.get(turn_id)
            if snapshot is not None and snapshot.final_assistant_started_at_ms is None:
                snapshot.final_assistant_started_at_ms = int(time() * 1000)
            if snapshot is not None:
                snapshot.assistant_item_id = item_id
                snapshot.assistant_text = f"{snapshot.assistant_text}{delta}"
            runtime.assistant_buffers[item_id] = (
                f"{runtime.assistant_buffers.get(item_id, '')}{delta}"
            )
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

        if isinstance(
            payload,
            (ReasoningTextDeltaNotification, ReasoningSummaryTextDeltaNotification),
        ) or notification.method in {
            "item/reasoning/textDelta",
            "item/reasoning/summaryTextDelta",
        }:
            item_id = (
                payload.item_id
                if isinstance(
                    payload,
                    (
                        ReasoningTextDeltaNotification,
                        ReasoningSummaryTextDeltaNotification,
                    ),
                )
                else getattr(payload, "item_id", None)
            )
            delta = (
                payload.delta
                if isinstance(
                    payload,
                    (
                        ReasoningTextDeltaNotification,
                        ReasoningSummaryTextDeltaNotification,
                    ),
                )
                else getattr(payload, "delta", None)
            )
            if (
                isinstance(item_id, str)
                and item_id
                and isinstance(delta, str)
                and delta
            ):
                content = self._accumulate_reasoning_delta(
                    runtime,
                    item_id,
                    notification.method,
                    delta,
                )
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

        if (
            isinstance(payload, PlanDeltaNotification)
            or notification.method == "item/plan/delta"
        ):
            delta = (
                payload.delta
                if isinstance(payload, PlanDeltaNotification)
                else getattr(payload, "delta", None)
            )
            item_id = (
                payload.item_id
                if isinstance(payload, PlanDeltaNotification)
                else getattr(payload, "item_id", None)
            )
            turn_id = (
                payload.turn_id
                if isinstance(payload, PlanDeltaNotification)
                else getattr(payload, "turn_id", None)
            )
            activity_id = (
                turn_id
                if isinstance(turn_id, str) and turn_id
                else item_id
                if item_id
                else ""
            )
            if activity_id and isinstance(delta, str) and delta:
                content = self._accumulate_plan_delta(runtime, activity_id, delta)
                snapshot = runtime.turn_snapshots.get(activity_id)
                if snapshot is not None:
                    snapshot.plan_text = content
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

        if (
            isinstance(payload, TurnPlanUpdatedNotification)
            or notification.method == "turn/plan/updated"
        ):
            lines: list[str] = []
            explanation = (
                payload.explanation
                if isinstance(payload, TurnPlanUpdatedNotification)
                else getattr(payload, "explanation", None)
            )
            plan = (
                payload.plan
                if isinstance(payload, TurnPlanUpdatedNotification)
                else getattr(payload, "plan", [])
            )
            turn_id = (
                payload.turn_id
                if isinstance(payload, TurnPlanUpdatedNotification)
                else getattr(payload, "turn_id", "")
            )
            if isinstance(explanation, str) and explanation.strip():
                lines.append(explanation.strip())
            if isinstance(plan, list):
                for entry in plan:
                    step = getattr(entry, "step", None)
                    status = getattr(entry, "status", None)
                    if isinstance(step, str) and step.strip():
                        rendered_status = (
                            status.value if hasattr(status, "value") else status
                        )
                        if rendered_status is not None:
                            lines.append(f"[{rendered_status}] {step.strip()}")
                        else:
                            lines.append(step.strip())
            if lines:
                activity_id = turn_id if isinstance(turn_id, str) else ""
                content = "\n".join(lines)
                if activity_id:
                    runtime.plan_buffers[activity_id] = content
                    snapshot = runtime.turn_snapshots.get(activity_id)
                    if snapshot is not None:
                        snapshot.plan_text = content
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

        if (
            isinstance(payload, CommandExecutionOutputDeltaNotification)
            or notification.method == "item/commandExecution/outputDelta"
        ):
            item_id = (
                payload.item_id
                if isinstance(payload, CommandExecutionOutputDeltaNotification)
                else getattr(payload, "item_id", None)
            )
            delta = (
                payload.delta
                if isinstance(payload, CommandExecutionOutputDeltaNotification)
                else getattr(payload, "delta", None)
            )
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

        if (
            isinstance(payload, ItemStartedNotification)
            or notification.method == "item/started"
        ):
            item = (
                payload.item.root
                if isinstance(payload, ItemStartedNotification)
                else self._coerce_thread_item(getattr(payload, "item", None))
            )
            if item is None:
                return
            self._handle_item_started(runtime, context, item)
            return

        if (
            isinstance(payload, ItemCompletedNotification)
            or notification.method == "item/completed"
        ):
            item = (
                payload.item.root
                if isinstance(payload, ItemCompletedNotification)
                else self._coerce_thread_item(getattr(payload, "item", None))
            )
            turn_id = (
                payload.turn_id
                if isinstance(payload, ItemCompletedNotification)
                else getattr(payload, "turn_id", None)
            )
            if item is None:
                return
            if isinstance(item, AgentMessageThreadItem) and turn_id:
                snapshot = runtime.turn_snapshots.get(turn_id)
                if (
                    snapshot is not None
                    and snapshot.final_assistant_started_at_ms is None
                ):
                    snapshot.final_assistant_started_at_ms = int(time() * 1000)
            self._handle_item_completed(
                runtime,
                context,
                item,
                turn_id=turn_id if isinstance(turn_id, str) and turn_id else None,
            )
            return

        if (
            isinstance(payload, ServerRequestResolvedNotification)
            or notification.method == "serverRequest/resolved"
        ):
            request_id = self._request_id_string(
                payload.request_id
                if isinstance(payload, ServerRequestResolvedNotification)
                else getattr(payload, "request_id", None)
            )
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
        item: CodexThreadItemRoot | Any,
    ) -> None:
        typed_item = (
            item if self._is_thread_item_root(item) else self._coerce_thread_item(item)
        )
        if typed_item is None:
            return
        item = typed_item
        if isinstance(item, CommandExecutionThreadItem):
            self._emit_from_thread(
                runtime,
                "command_start",
                {
                    "session_id": context.session_id,
                    "tool_call_id": item.id,
                    "tool_name": "command_execution",
                    "command": item.command,
                    "cwd": item.cwd,
                    "is_background": False,
                },
            )
            return

        if isinstance(item, FileChangeThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": "file_change",
                    "tool_call_id": item.id,
                    "arguments": {
                        "change_count": len(item.changes),
                    },
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, McpToolCallThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": f"mcp:{item.server}/{item.tool}",
                    "tool_call_id": item.id,
                    "arguments": self._safe_json_value(item.arguments),
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, DynamicToolCallThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": f"dynamic:{item.tool}",
                    "tool_call_id": item.id,
                    "arguments": self._safe_json_value(item.arguments),
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, CollabAgentToolCallThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": f"collab:{item.tool.value}",
                    "tool_call_id": item.id,
                    "arguments": {
                        "receiver_thread_ids": list(item.receiver_thread_ids),
                        "prompt": item.prompt,
                    },
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, WebSearchThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": "web_search",
                    "tool_call_id": item.id,
                    "arguments": {"query": item.query},
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, ImageGenerationThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": "image_generation",
                    "tool_call_id": item.id,
                    "arguments": {
                        "revised_prompt": item.revised_prompt,
                    },
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, ImageViewThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_start",
                {
                    "session_id": context.session_id,
                    "tool_name": "image_view",
                    "tool_call_id": item.id,
                    "arguments": {
                        "path": item.path,
                    },
                    "iteration": 0,
                },
            )

    def _handle_item_completed(
        self,
        runtime: CodexSessionRuntime,
        context: ChatSessionContext,
        item: CodexThreadItemRoot | Any,
        *,
        turn_id: str | None = None,
    ) -> None:
        typed_item = (
            item if self._is_thread_item_root(item) else self._coerce_thread_item(item)
        )
        if typed_item is None:
            return
        item = typed_item
        if isinstance(item, CommandExecutionThreadItem):
            self._emit_from_thread(
                runtime,
                "command_end",
                {
                    "session_id": context.session_id,
                    "tool_call_id": item.id,
                    "tool_name": "command_execution",
                    "command": item.command,
                    "cwd": item.cwd,
                    "exit_code": item.exit_code or 0,
                    "timed_out": False,
                    "is_background": False,
                },
            )
            return

        if isinstance(item, AgentMessageThreadItem):
            resolved_turn_id = turn_id
            snapshot = (
                runtime.turn_snapshots.get(resolved_turn_id)
                if isinstance(resolved_turn_id, str) and resolved_turn_id
                else None
            )
            emitted_item_id = item.id
            if (
                snapshot is not None
                and isinstance(snapshot.assistant_item_id, str)
                and snapshot.assistant_item_id
                and snapshot.assistant_item_id != item.id
                and item.id not in runtime.assistant_buffers
            ):
                emitted_item_id = snapshot.assistant_item_id
            content = item.text or runtime.assistant_buffers.get(emitted_item_id, "")
            runtime.assistant_buffers.pop(emitted_item_id, None)
            runtime.assistant_buffers.pop(item.id, None)
            if snapshot is not None:
                snapshot.assistant_item_id = emitted_item_id
                snapshot.assistant_text = content
            if isinstance(resolved_turn_id, str) and resolved_turn_id:
                self._clear_transcript_buffers(runtime, resolved_turn_id)
            if content.strip():
                message_sequence = self.chat_service._append_transcript_message(
                    context.session_id,
                    Message(role="assistant", content=content),
                )
                self._emit_from_thread(
                    runtime,
                    "assistant_message",
                    {
                        "session_id": context.session_id,
                        "item_id": emitted_item_id,
                        "content": content,
                        "iteration": 0,
                        **(
                            {"sequence": message_sequence}
                            if isinstance(message_sequence, int)
                            else {}
                        ),
                    },
                )
            return

        if isinstance(item, ReasoningThreadItem):
            content = "\n".join(item.summary or item.content or [])
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

        if isinstance(item, FileChangeThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": "file_change",
                    "tool_call_id": item.id,
                    "result": self._summarize_file_change(item),
                    "is_error": item.status.value not in {"completed", "inProgress"},
                    "metadata": {
                        "status": item.status.value,
                        "change_count": len(item.changes),
                    },
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, McpToolCallThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": f"mcp:{item.server}/{item.tool}",
                    "tool_call_id": item.id,
                    "result": self._summarize_tool_result(item),
                    "is_error": item.error is not None,
                    "metadata": {"status": item.status.value},
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, DynamicToolCallThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": f"dynamic:{item.tool}",
                    "tool_call_id": item.id,
                    "result": self._summarize_tool_result(item),
                    "is_error": item.success is False,
                    "metadata": {"status": item.status.value},
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, CollabAgentToolCallThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": f"collab:{item.tool.value}",
                    "tool_call_id": item.id,
                    "result": self._summarize_collab_result(item),
                    "is_error": item.status.value not in {"completed", "inProgress"},
                    "metadata": {"status": item.status.value},
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, WebSearchThreadItem):
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": "web_search",
                    "tool_call_id": item.id,
                    "result": f"Searched the web for {item.query}.",
                    "is_error": False,
                    "metadata": {},
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, ImageViewThreadItem):
            preview = self._preview_metadata_for_local_image(
                context.session_id, item.path
            )
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": "image_view",
                    "tool_call_id": item.id,
                    "result": f"Viewed image: {item.path}",
                    "is_error": False,
                    "metadata": {
                        "path": item.path,
                        **preview,
                    },
                    "raw": None,
                    "iteration": 0,
                },
            )
            return

        if isinstance(item, ImageGenerationThreadItem):
            result = item.saved_path or item.result
            preview = self._preview_metadata_for_local_image(context.session_id, result)
            self._emit_from_thread(
                runtime,
                "tool_call_end",
                {
                    "session_id": context.session_id,
                    "tool_name": "image_generation",
                    "tool_call_id": item.id,
                    "result": f"Generated image: {result}",
                    "is_error": item.status != "completed",
                    "metadata": {
                        "status": item.status,
                        "saved_path": item.saved_path,
                        "result": item.result,
                        "revised_prompt": item.revised_prompt,
                        **preview,
                    },
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
        payload = notification.payload
        if isinstance(payload, TurnCompletedNotification):
            turn = payload.turn
            turn_id = turn.id
            status = self._turn_status_value(turn.status)
            error = turn.error
        else:
            turn = getattr(payload, "turn", None)
            turn_id = getattr(turn, "id", None)
            if not isinstance(turn_id, str) or not turn_id:
                return "stop"
            status = self._turn_status_value(getattr(turn, "status", None))
            error = getattr(turn, "error", None)
        runtime.status = "idle" if status == "completed" else status
        runtime.active_flags = []
        runtime.detail = None
        if turn_id:
            self._upsert_cached_thread_turn(runtime, turn_id, status=status)
            self._clear_transcript_buffers(runtime, turn_id)
        finish_reason = "stop"
        if status == "interrupted":
            finish_reason = "interrupted"
            self._emit_from_thread(
                runtime,
                "turn_aborted",
                {
                    "session_id": context.session_id,
                    "turn_id": turn_id,
                    "status": status,
                    "reason": "Turn interrupted.",
                },
            )
        elif status == "completed":
            self._emit_from_thread(
                runtime,
                "turn_completed",
                {
                    "session_id": context.session_id,
                    "turn_id": turn_id,
                    "status": status,
                },
            )
        elif status == "failed":
            finish_reason = "error"
            message = error.message if error is not None else "Codex turn failed."
            error_code = getattr(error, "code", None) if error is not None else None
            self._emit_from_thread(
                runtime,
                "stream_error",
                {
                    "session_id": context.session_id,
                    "message": message,
                    "thread_id": runtime.thread_id,
                    "turn_id": turn_id,
                    "code": str(error_code) if error_code is not None else None,
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
        response = pending.response or self._build_approval_response(
            method, params, "cancel", None
        )
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
        elif kind == "plan_implementation":
            title = "Implement this plan?"
            detail = "Codex drafted a plan and is waiting for confirmation to implement it."
        elif kind == "user_input":
            request = self._normalized_user_input_request(method, params)
            title = "User input required"
            if isinstance(request, dict):
                message = request.get("message")
                if isinstance(message, str) and message.strip():
                    detail = message.strip()

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
        request = self._normalized_user_input_request(method, params)
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

    def _normalized_user_input_request(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        if method != "item/tool/requestUserInput":
            return None

        questions = params.get("questions")
        if not isinstance(questions, list):
            return None

        schema_properties: dict[str, Any] = {}
        schema_required: list[str] = []
        normalized_questions: list[dict[str, Any]] = []

        for entry in questions:
            if not isinstance(entry, dict):
                continue
            question_id = entry.get("id")
            if not isinstance(question_id, str) or not question_id.strip():
                continue
            normalized_id = question_id.strip()
            prompt = entry.get("question")
            header = entry.get("header")
            options = entry.get("options")
            is_other = bool(entry.get("isOther"))

            normalized_question = {
                "id": normalized_id,
                "header": header if isinstance(header, str) else "",
                "question": prompt if isinstance(prompt, str) else "",
                "isOther": is_other,
                "isSecret": bool(entry.get("isSecret")),
                "options": [
                    {
                        "label": str(option.get("label") or "").strip(),
                        "description": str(option.get("description") or "").strip(),
                    }
                    for option in options
                    if isinstance(option, dict)
                    and isinstance(option.get("label"), str)
                    and option.get("label", "").strip()
                ]
                if isinstance(options, list)
                else [],
            }
            normalized_questions.append(normalized_question)

            schema_entry: dict[str, Any] = {
                "title": (
                    header.strip()
                    if isinstance(header, str) and header.strip()
                    else normalized_id
                ),
                "description": (
                    prompt.strip() if isinstance(prompt, str) and prompt.strip() else ""
                ),
            }
            option_entries = normalized_question["options"]
            if option_entries:
                schema_entry["type"] = "string"
                schema_entry["oneOf"] = [
                    {
                        "const": option["label"],
                        "title": option["label"],
                        "description": option["description"],
                    }
                    for option in option_entries
                ]
            else:
                schema_entry["type"] = "string"
            schema_properties[normalized_id] = schema_entry

            if is_other:
                schema_properties[f"{normalized_id}__other"] = {
                    "type": "string",
                    "title": (
                        f"{schema_entry['title']} (Other)"
                        if isinstance(schema_entry["title"], str)
                        else f"{normalized_id} (Other)"
                    ),
                    "description": "Provide a custom answer instead of the preset options.",
                }
            else:
                schema_required.append(normalized_id)

        if not normalized_questions:
            return None

        requested_schema: dict[str, Any] = {
            "type": "object",
            "properties": schema_properties,
        }
        if schema_required:
            requested_schema["required"] = schema_required

        prompt_text = self._user_input_prompt_text(normalized_questions)
        return {
            "kind": "user_input",
            "mode": "form",
            "message": prompt_text,
            "questions": normalized_questions,
            "requestedSchema": requested_schema,
        }

    def _user_input_prompt_text(self, questions: list[dict[str, Any]]) -> str:
        for entry in questions:
            prompt = entry.get("question")
            if isinstance(prompt, str) and prompt.strip():
                return prompt.strip()
        for entry in questions:
            header = entry.get("header")
            if isinstance(header, str) and header.strip():
                return header.strip()
        return "Please answer the questions below."

    def _approval_kind(self, method: str) -> str:
        if method == "item/commandExecution/requestApproval":
            return "command"
        if method == "item/fileChange/requestApproval":
            return "file_change"
        if method == "item/plan/requestImplementation":
            return "plan_implementation"
        if method == "item/permissions/requestApproval":
            return "permissions"
        if method in {"mcpServer/elicitation/request", "elicitation/create"}:
            return "mcp_elicitation"
        if method == "item/tool/requestUserInput":
            return "user_input"
        return "approval"

    def _approval_options(self, kind: str) -> list[dict[str, str]]:
        if kind in {"command", "file_change", "permissions"}:
            return [
                {"value": "accept", "label": "Accept once"},
                {"value": "accept_for_session", "label": "Accept for session"},
                {"value": "decline", "label": "Decline"},
                {"value": "cancel", "label": "Cancel"},
            ]
        if kind == "plan_implementation":
            return [
                {"value": "accept", "label": "Implement"},
                {"value": "cancel", "label": "Cancel"},
            ]
        if kind == "user_input":
            return [
                {"value": "accept", "label": "Submit"},
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
        if (
            context.source != "channel"
            or not settings.channel_auto_approve_codex_requests
        ):
            return None
        if method == "item/plan/requestImplementation":
            return {"implemented": False}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
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
        if method == "item/plan/requestImplementation":
            response: dict[str, Any] = {
                "implemented": normalized_decision == "accept",
            }
            if isinstance(content, dict):
                response.update(content)
            return response
        if method == "item/tool/requestUserInput":
            if normalized_decision == "cancel":
                return {"answers": {}}
            return dict(content) if isinstance(content, dict) else {"answers": {}}
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

    def normalize_pending_request_id(self, value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
        return None

    def build_pending_approval_record(
        self,
        request_id: Any,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        normalized_request_id = self.normalize_pending_request_id(request_id)
        if normalized_request_id is None:
            return None
        record = self._build_pending_approval(normalized_request_id, method, params)
        payload = record.get("payload")
        if isinstance(payload, dict) and not isinstance(request_id, str):
            payload = {
                **payload,
                "_ipc_request_id": request_id,
            }
        return {
            "request_id": normalized_request_id,
            **record,
            "payload": payload if isinstance(payload, dict) else record.get("payload"),
        }

    def build_response_payload_for_request(
        self,
        method: str,
        params: dict[str, Any],
        decision: str,
        content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._build_approval_response(method, params, decision, content)

    def _preview_metadata_for_local_image(
        self,
        session_id: str,
        path: str | None,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or not path.strip():
            return {}
        if not hasattr(self.chat_service, "register_local_image_preview"):
            return {}
        try:
            preview = self.chat_service.register_local_image_preview(session_id, path)
        except AttachmentStorageError:
            return {}
        if not isinstance(preview, dict):
            return {}
        metadata: dict[str, Any] = {}
        preview_url = preview.get("preview_url")
        if isinstance(preview_url, str) and preview_url:
            metadata["preview_url"] = preview_url
        name = preview.get("name")
        if isinstance(name, str) and name:
            metadata["preview_name"] = name
        mime_type = preview.get("mime_type")
        if isinstance(mime_type, str) and mime_type:
            metadata["mime_type"] = mime_type
        size = preview.get("size")
        if isinstance(size, int):
            metadata["size"] = size
        return metadata

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
        reasoning_effort_override = self._context_string_override(
            context, "reasoning_effort"
        )
        model_override = self._context_string_override(context, "model")
        service_tier_override = self._context_string_override(context, "service_tier")
        return {
            "cwd": str(context.project_path),
            "approval_policy": approval_policy,
            "approvals_reviewer": settings.approvals_reviewer,
            "effort": reasoning_effort_override or settings.reasoning_effort,
            "model": model_override or settings.model or None,
            "personality": settings.personality,
            "sandbox_policy": {
                "type": _codex_turn_sandbox_policy_type(settings.sandbox)
            },
            "service_tier": service_tier_override or settings.service_tier or None,
        }

    def _pairing_mcp_config(self) -> dict[str, Any] | None:
        config_service = getattr(self.chat_service, "config_service", None)
        project_root = getattr(config_service, "project_root", None)
        home_dir = getattr(config_service, "home_dir", None)
        return build_pairing_mcp_config(
            server_name=self.pairing_mcp_server_name,
            project_root=project_root,
            home_dir=home_dir,
        )

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

    def _cache_thread_state(
        self,
        runtime: CodexSessionRuntime,
        thread: Any,
    ) -> None:
        runtime.thread_state_cache = self._safe_model_dump(thread)

    def _cached_thread_state_payload(
        self,
        runtime: CodexSessionRuntime,
    ) -> dict[str, Any] | None:
        if runtime.thread_state_cache is None:
            return None
        thread_payload = deepcopy(runtime.thread_state_cache)
        return {
            "thread": thread_payload,
            "threadRuntimeStatus": self._thread_runtime_status_payload(
                thread_payload.get("status"),
                runtime.status,
                runtime.active_flags,
            ),
            "detail": runtime.detail,
        }

    def _upsert_cached_thread_turn(
        self,
        runtime: CodexSessionRuntime,
        turn_id: str,
        *,
        status: str,
    ) -> None:
        if runtime.thread_state_cache is None:
            runtime.thread_state_cache = {
                "id": runtime.thread_id,
                "turns": [],
            }
        turns = runtime.thread_state_cache.get("turns")
        if not isinstance(turns, list):
            turns = []
            runtime.thread_state_cache["turns"] = turns

        cached_turn: dict[str, Any] | None = None
        for turn in turns:
            if isinstance(turn, dict) and turn.get("id") == turn_id:
                cached_turn = turn
                break
        if cached_turn is None:
            cached_turn = {
                "id": turn_id,
                "turnId": turn_id,
                "status": status,
                "items": [],
                "error": None,
            }
            turns.append(cached_turn)
            return

        cached_turn["turnId"] = turn_id
        cached_turn["status"] = status
        cached_turn.setdefault("items", [])
        cached_turn.setdefault("error", None)

    def _can_merge_snapshot_into_last_turn(
        self,
        context: ChatSessionContext,
        turn: dict[str, Any],
        snapshot: TurnSnapshotState,
    ) -> bool:
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id.startswith(
            f"{context.session_id}:turn:"
        ):
            return False
        turn_input = (
            turn.get("params", {}).get("input")
            if isinstance(turn.get("params"), dict)
            else None
        )
        snapshot_input = snapshot.params.get("input")
        return self._normalize_input_items_for_state(
            turn_input or []
        ) == self._normalize_input_items_for_state(snapshot_input or [])

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
            normalized["finalAssistantStartedAtMs"] = (
                snapshot.final_assistant_started_at_ms
            )
        else:
            normalized["turnStartedAtMs"] = turn.get("turnStartedAtMs")
            normalized["finalAssistantStartedAtMs"] = turn.get(
                "finalAssistantStartedAtMs"
            )
            params = self._fallback_turn_state_params(context, turn)
        normalized["items"] = self._normalized_ipc_turn_items(
            turn,
            snapshot,
        )
        normalized["params"] = params
        normalized["error"] = turn.get("error")
        normalized["diff"] = turn.get("diff")
        return normalized

    def _normalized_ipc_turn_items(
        self,
        turn: dict[str, Any],
        snapshot: TurnSnapshotState | None,
    ) -> list[dict[str, Any]]:
        raw_items = turn.get("items")
        items = (
            [dict(item) for item in raw_items if isinstance(item, dict)]
            if isinstance(raw_items, list)
            else []
        )
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

        # Convert raw "plan" items to "proposed-plan" format for IPC clients.
        has_plan_item = False
        for item in items:
            if item.get("type") == "plan":
                has_plan_item = True
                item["type"] = "proposed-plan"
                item["content"] = item.pop("text", "")
                item["completed"] = True

        # If snapshot has plan_text but no plan item in the turn, inject one.
        if not has_plan_item and snapshot.plan_text:
            items.append(
                {
                    "type": "proposed-plan",
                    "id": f"{turn.get('id')}:plan",
                    "content": snapshot.plan_text,
                    "completed": True,
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
            return [{"type": "text", "text": input_payload, "text_elements": []}]
        if isinstance(input_payload, dict):
            normalized_item = dict(input_payload)
            if (
                normalized_item.get("type") == "text"
                and "text_elements" not in normalized_item
            ):
                normalized_item["text_elements"] = []
            return [normalized_item]
        normalized: list[dict[str, Any]] = []
        for item in input_payload:
            if isinstance(item, dict):
                normalized_item = dict(item)
                if (
                    normalized_item.get("type") == "text"
                    and "text_elements" not in normalized_item
                ):
                    normalized_item["text_elements"] = []
                normalized.append(normalized_item)
        return normalized

    def _turn_input_items_from_thread_items(self, items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        for raw_item in items:
            item = self._coerce_thread_item(raw_item)
            if not isinstance(item, UserMessageThreadItem):
                continue
            return [
                normalized
                for content_item in item.content
                if (normalized := self._user_input_to_state_item(content_item.root))
                is not None
            ]
        return []

    async def _close_runtime(self, runtime: CodexSessionRuntime) -> None:
        for pending in runtime.pending_requests.values():
            pending.decision = "cancel"
            pending.response = {"decision": "cancel"}
            pending.event.set()
        if runtime.client is not None:
            try:
                await _maybe_await(runtime.client.close())
            except (AppServerError, TransportClosedError):
                pass

    async def _read_thread(
        self,
        runtime: CodexSessionRuntime,
        *,
        include_turns: bool,
    ) -> Any:
        assert runtime.client is not None
        assert runtime.thread_id is not None
        thread_handle = self._thread_handle(runtime.client, runtime.thread_id)
        return await _maybe_await(thread_handle.read(include_turns=include_turns))

    def _thread_handle(
        self, client: ApprovalAwareAppServerClient, thread_id: str
    ) -> ApprovalAwareAsyncThread:
        if hasattr(client, "thread"):
            return client.thread(thread_id)
        return CodexThread(client, thread_id)

    def _turn_handle(
        self,
        runtime: CodexSessionRuntime,
        turn_id: str,
    ) -> ApprovalAwareAsyncTurnHandle:
        stored_handle = runtime.turn_handles.get(turn_id)
        if stored_handle is not None:
            return stored_handle
        assert runtime.client is not None
        assert runtime.thread_id is not None
        return ApprovalAwareAsyncTurnHandle(
            runtime.client,
            runtime.thread_id,
            turn_id,
        )

    def _update_runtime_from_thread(
        self,
        context: ChatSessionContext,
        runtime: CodexSessionRuntime,
        thread: Any,
    ) -> None:
        self._apply_thread_snapshot(runtime, thread)
        self._persist_runtime_status(context, runtime, include_detail=True)

    def _apply_thread_snapshot(
        self,
        runtime: CodexSessionRuntime,
        thread: Any,
    ) -> None:
        runtime.status = self._thread_status_value(thread.status)
        runtime.active_flags = self._thread_active_flags(thread.status)
        self._cache_thread_state(runtime, thread)

    def _persist_runtime_status(
        self,
        context: ChatSessionContext,
        runtime: CodexSessionRuntime,
        *,
        include_detail: bool = False,
    ) -> None:
        updates: dict[str, Any] = {
            "thread_id": runtime.thread_id,
            "status": runtime.status,
            "active_flags": runtime.active_flags,
        }
        if include_detail:
            updates["detail"] = runtime.detail
        self.chat_service.update_session_backend_state(context.session_id, updates)

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
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is runtime.loop:
            task = running_loop.create_task(runtime.emit(event, data))
            if wait:
                raise RuntimeError(
                    "Cannot synchronously wait for Codex stream emit on the active event loop."
                )
            runtime.pending_emit_tasks.append(task)
            return

        future = asyncio.run_coroutine_threadsafe(
            runtime.emit(event, data), runtime.loop
        )
        if wait:
            future.result()
            return
        runtime.pending_emit_tasks.append(future)

    async def _flush_pending_runtime_emits(self, runtime: CodexSessionRuntime) -> None:
        pending = list(runtime.pending_emit_tasks)
        runtime.pending_emit_tasks.clear()
        for task in pending:
            if isinstance(task, asyncio.Future):
                await task
            else:
                await asyncio.wrap_future(task)

    def _codex_work_mode(self, context: ChatSessionContext) -> str:
        metadata = self.chat_service.get_session_metadata(context.session_id)
        work_mode = metadata.get("codex_work_mode")
        if work_mode in {"plan", "build"}:
            return work_mode
        return codex_work_mode_from_collaboration_mode(
            context.backend_state.get("collaboration_mode")
        )

    def _thread_view_payload(
        self,
        context: ChatSessionContext,
        thread: ThreadV2,
        *,
        activity_limit: int | None = None,
    ) -> CodexThreadViewPayload:
        messages: list[StoredSessionMessage] = []
        activity_events: list[dict[str, Any]] | deque[dict[str, Any]]
        activity_events = (
            deque(maxlen=activity_limit)
            if isinstance(activity_limit, int) and activity_limit > 0
            else []
        )
        activity_event_counter = [0]
        sequence_counter = [0]

        normalized_turns = self.build_ipc_turns(
            context,
            [turn.model_dump(mode="json", by_alias=True) for turn in thread.turns],
        )
        for turn in normalized_turns:
            for raw_item in turn.get("items", []) or []:
                if self._append_raw_ipc_item_view(
                    context,
                    raw_item,
                    messages,
                    activity_events,
                    activity_event_counter,
                    sequence_counter,
                ):
                    continue
                item = self._coerce_thread_item(raw_item)
                if item is None:
                    continue
                self._append_thread_item_view(
                    context,
                    item,
                    messages,
                    activity_events,
                    activity_event_counter,
                    sequence_counter,
                )

        title = thread.name or thread.preview or thread.id or "Codex session"
        preview = thread.preview or title
        updated_at = float(thread.updated_at)
        returned_activity_events = list(activity_events)
        return {
            "title": title,
            "preview": preview,
            "updated_at": updated_at,
            "messages": messages,
            "activity_events": returned_activity_events,
            "activity_history": {
                "total_count": activity_event_counter[0],
                "returned_count": len(returned_activity_events),
                "next_before": (
                    activity_event_counter[0] - len(returned_activity_events)
                    if activity_event_counter[0] > len(returned_activity_events)
                    else None
                ),
            },
            "codex_turn_timings": self._codex_turn_timings_from_turns(normalized_turns),
        }

    def ipc_conversation_view(
        self,
        context: ChatSessionContext,
        conversation_state: dict[str, Any],
        *,
        activity_limit: int | None = None,
    ) -> dict[str, Any]:
        messages: list[StoredSessionMessage] = []
        activity_events: list[dict[str, Any]] | deque[dict[str, Any]]
        activity_events = (
            deque(maxlen=activity_limit)
            if isinstance(activity_limit, int) and activity_limit > 0
            else []
        )
        activity_event_counter = [0]
        sequence_counter = [0]

        turns = conversation_state.get("turns")
        normalized_turns = (
            self.build_ipc_turns(context, turns) if isinstance(turns, list) else []
        )
        for turn in normalized_turns:
            items = turn.get("items")
            if not isinstance(items, list):
                continue
            for raw_item in items:
                if self._append_raw_ipc_item_view(
                    context,
                    raw_item,
                    messages,
                    activity_events,
                    activity_event_counter,
                    sequence_counter,
                ):
                    continue
                item = self._coerce_thread_item(raw_item)
                if item is None:
                    continue
                self._append_thread_item_view(
                    context,
                    item,
                    messages,
                    activity_events,
                    activity_event_counter,
                    sequence_counter,
                )
        requests = conversation_state.get("requests")
        if isinstance(requests, list):
            self._append_ipc_request_activity_events(
                context,
                requests,
                activity_events,
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
            )

        title = (
            conversation_state.get("title")
            or conversation_state.get("preview")
            or context.session_id
        )
        preview = conversation_state.get("preview") or title
        updated_at_raw = conversation_state.get("updatedAt")
        updated_at = (
            float(updated_at_raw or 0) / 1000
            if isinstance(updated_at_raw, (int, float))
            else 0.0
        )
        returned_activity_events = list(activity_events)
        return {
            "title": title,
            "preview": preview,
            "updated_at": updated_at,
            "messages": messages,
            "activity_events": returned_activity_events,
            "activity_history": {
                "total_count": activity_event_counter[0],
                "returned_count": len(returned_activity_events),
                "next_before": (
                    activity_event_counter[0] - len(returned_activity_events)
                    if activity_event_counter[0] > len(returned_activity_events)
                    else None
                ),
            },
            "codex_turn_timings": self._codex_turn_timings_from_turns(normalized_turns),
        }

    def _append_raw_ipc_item_view(
        self,
        context: ChatSessionContext,
        raw_item: Any,
        messages: list[StoredSessionMessage],
        activity_events: list[dict[str, Any]] | deque[dict[str, Any]],
        activity_event_counter: list[int],
        sequence_counter: list[int],
    ) -> bool:
        if not isinstance(raw_item, dict):
            return False
        item_type = raw_item.get("type")
        if item_type != "userInputResponse":
            return False
        if not raw_item.get("completed"):
            return False

        request_id = self.normalize_pending_request_id(raw_item.get("requestId"))
        if request_id is None:
            return False

        request_payload = self._approval_payload(
            "item/tool/requestUserInput",
            {"questions": raw_item.get("questions")},
        )
        detail = "Please answer the questions below."
        request = request_payload.get("request")
        if isinstance(request, dict):
            message = request.get("message")
            if isinstance(message, str) and message.strip():
                detail = message.strip()
        answers = raw_item.get("answers")
        if isinstance(answers, dict):
            request_payload = {
                **request_payload,
                "answers": answers,
            }

        self._append_activity_event(
            activity_events,
            "approval_requested",
            {
                "session_id": context.session_id,
                "request_id": request_id,
                "method": "item/tool/requestUserInput",
                "kind": "user_input",
                "title": "User input required",
                "detail": detail,
                "options": self._approval_options("user_input"),
                "payload": request_payload,
            },
            activity_event_counter=activity_event_counter,
            sequence_counter=sequence_counter,
        )
        self._append_activity_event(
            activity_events,
            "approval_resolved",
            {
                "session_id": context.session_id,
                "request_id": request_id,
                "decision": "accept",
            },
            activity_event_counter=activity_event_counter,
            sequence_counter=sequence_counter,
        )
        return True

    def _append_ipc_request_activity_events(
        self,
        context: ChatSessionContext,
        requests: list[dict[str, Any]],
        activity_events: list[dict[str, Any]] | deque[dict[str, Any]],
        *,
        activity_event_counter: list[int],
        sequence_counter: list[int],
    ) -> None:
        for request in requests:
            if not isinstance(request, dict):
                continue
            method = request.get("method")
            params = request.get("params")
            record = (
                self.build_pending_approval_record(request.get("id"), method, params)
                if isinstance(method, str) and isinstance(params, dict)
                else None
            )
            if record is None:
                continue
            self._append_activity_event(
                activity_events,
                "approval_requested",
                {
                    "session_id": context.session_id,
                    **record,
                },
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
            )

    def _codex_turn_timings_from_turns(
        self,
        turns: list[dict[str, Any]],
    ) -> list[dict[str, int | str | None]]:
        codex_turn_timings: list[dict[str, int | str | None]] = []
        for turn in turns:
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

    def _append_thread_item_view(
        self,
        context: ChatSessionContext,
        item: CodexThreadItemRoot,
        messages: list[StoredSessionMessage],
        activity_events: list[dict[str, Any]] | deque[dict[str, Any]],
        activity_event_counter: list[int],
        sequence_counter: list[int],
    ) -> None:
        if isinstance(item, UserMessageThreadItem):
            content, attachments = self._thread_user_message_view(
                context.session_id, item.content
            )
            if content or attachments:
                messages.append(
                    StoredSessionMessage(
                        role="user",
                        content=content,
                        sequence=self._next_thread_view_sequence(sequence_counter),
                        source=context.source,
                        channel_meta=context.channel_meta,
                        attachments=attachments,
                    )
                )
            return

        if isinstance(item, AgentMessageThreadItem):
            content = item.text
            if content.strip():
                messages.append(
                    StoredSessionMessage(
                        role="assistant",
                        content=content,
                        sequence=self._next_thread_view_sequence(sequence_counter),
                        source=context.source,
                        channel_meta=context.channel_meta,
                    )
                )
            return

        if isinstance(item, PlanThreadItem):
            self._append_activity_event(
                activity_events,
                "plan",
                {
                    "session_id": context.session_id,
                    "item_id": item.id,
                    "content": item.text,
                    "iteration": 0,
                },
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
            )
            return

        if isinstance(item, HookPromptThreadItem):
            content = "\n".join(
                fragment.text.strip()
                for fragment in item.fragments
                if isinstance(fragment.text, str) and fragment.text.strip()
            )
            if content:
                self._append_activity_event(
                    activity_events,
                    "plan",
                    {
                        "session_id": context.session_id,
                        "item_id": item.id,
                        "content": content,
                        "iteration": 0,
                    },
                    activity_event_counter=activity_event_counter,
                    sequence_counter=sequence_counter,
                )
            return

        if isinstance(item, ReasoningThreadItem):
            content = "\n".join(item.summary or item.content or [])
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
                    activity_event_counter=activity_event_counter,
                    sequence_counter=sequence_counter,
                )
            return

        if isinstance(item, CommandExecutionThreadItem):
            self._append_activity_event(
                activity_events,
                "command_start",
                {
                    "session_id": context.session_id,
                    "tool_call_id": item.id,
                    "tool_name": "command_execution",
                    "command": item.command,
                    "cwd": item.cwd,
                    "is_background": False,
                },
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
            )
            if isinstance(item.aggregated_output, str) and item.aggregated_output:
                self._append_activity_event(
                    activity_events,
                    "command_output",
                    {
                        "session_id": context.session_id,
                        "tool_call_id": item.id,
                        "tool_name": "command_execution",
                        "stream": "stdout",
                        "content": item.aggregated_output,
                        "is_background": False,
                    },
                    activity_event_counter=activity_event_counter,
                    sequence_counter=sequence_counter,
                )
            self._append_activity_event(
                activity_events,
                "command_end",
                {
                    "session_id": context.session_id,
                    "tool_call_id": item.id,
                    "tool_name": "command_execution",
                    "command": item.command,
                    "cwd": item.cwd,
                    "exit_code": int(item.exit_code or 0),
                    "timed_out": False,
                    "is_background": False,
                },
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
            )
            return

        if isinstance(item, FileChangeThreadItem):
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
                tool_name="file_change",
                tool_call_id=item.id,
                arguments={"change_count": len(item.changes)},
                result=self._summarize_file_change(item),
                is_error=item.status.value not in {"completed", "inProgress"},
                metadata={
                    "status": item.status.value,
                    "change_count": len(item.changes),
                    "changes": self._safe_json_value(item.changes),
                },
            )
            return

        if isinstance(item, McpToolCallThreadItem):
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
                tool_name=f"mcp:{item.server}/{item.tool}",
                tool_call_id=item.id,
                arguments=self._safe_json_value(item.arguments),
                result=self._summarize_tool_result(item),
                is_error=item.error is not None,
                metadata={"status": item.status.value},
            )
            return

        if isinstance(item, DynamicToolCallThreadItem):
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
                tool_name=f"dynamic:{item.tool}",
                tool_call_id=item.id,
                arguments=self._safe_json_value(item.arguments),
                result=self._summarize_tool_result(item),
                is_error=item.success is False,
                metadata={"status": item.status.value},
            )
            return

        if isinstance(item, CollabAgentToolCallThreadItem):
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
                tool_name=f"collab:{item.tool.value}",
                tool_call_id=item.id,
                arguments={
                    "receiver_thread_ids": list(item.receiver_thread_ids),
                    "prompt": item.prompt,
                },
                result=self._summarize_collab_result(item),
                is_error=item.status.value not in {"completed", "inProgress"},
                metadata={"status": item.status.value},
            )
            return

        if isinstance(item, WebSearchThreadItem):
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
                tool_name="web_search",
                tool_call_id=item.id,
                arguments={"query": item.query},
                result=f"Searched the web for {item.query}.",
                is_error=False,
                metadata={},
            )
            return

        if isinstance(item, ImageViewThreadItem):
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
                tool_name="image_view",
                tool_call_id=item.id,
                arguments={
                    "path": item.path,
                },
                result=f"Viewed image: {item.path}",
                is_error=False,
                metadata={
                    "path": item.path,
                    **self._preview_metadata_for_local_image(
                        context.session_id, item.path
                    ),
                },
            )
            return

        if isinstance(item, ImageGenerationThreadItem):
            result = item.saved_path or item.result
            self._append_tool_activity_events(
                context.session_id,
                activity_events,
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
                tool_name="image_generation",
                tool_call_id=item.id,
                arguments={
                    "revised_prompt": item.revised_prompt,
                },
                result=f"Generated image: {result}",
                is_error=item.status != "completed",
                metadata={
                    "status": item.status,
                    "saved_path": item.saved_path,
                    "result": item.result,
                    "revised_prompt": item.revised_prompt,
                    **self._preview_metadata_for_local_image(
                        context.session_id, result
                    ),
                },
            )
            return

        if isinstance(item, EnteredReviewModeThreadItem):
            self._append_activity_event(
                activity_events,
                "plan",
                {
                    "session_id": context.session_id,
                    "item_id": item.id,
                    "content": f"Entered review mode: {item.review}",
                    "iteration": 0,
                },
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
            )
            return

        if isinstance(item, ExitedReviewModeThreadItem):
            self._append_activity_event(
                activity_events,
                "plan",
                {
                    "session_id": context.session_id,
                    "item_id": item.id,
                    "content": f"Exited review mode: {item.review}",
                    "iteration": 0,
                },
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
            )
            return

        if isinstance(item, ContextCompactionThreadItem):
            self._append_activity_event(
                activity_events,
                "plan",
                {
                    "session_id": context.session_id,
                    "item_id": item.id,
                    "content": "Context compacted.",
                    "iteration": 0,
                },
                activity_event_counter=activity_event_counter,
                sequence_counter=sequence_counter,
            )

    def _append_tool_activity_events(
        self,
        session_id: str,
        activity_events: list[dict[str, Any]] | deque[dict[str, Any]],
        *,
        activity_event_counter: list[int],
        sequence_counter: list[int],
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
            activity_event_counter=activity_event_counter,
            sequence_counter=sequence_counter,
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
            activity_event_counter=activity_event_counter,
            sequence_counter=sequence_counter,
        )

    def _append_activity_event(
        self,
        activity_events: list[dict[str, Any]] | deque[dict[str, Any]],
        event: str,
        data: dict[str, Any],
        *,
        activity_event_counter: list[int] | None = None,
        sequence_counter: list[int] | None = None,
    ) -> None:
        if sequence_counter is not None and "sequence" not in data:
            data = {
                **data,
                "sequence": self._next_thread_view_sequence(sequence_counter),
            }
        if activity_event_counter is not None:
            activity_event_counter[0] += 1
        activity_events.append({"event": event, "data": data})

    def _next_thread_view_sequence(self, sequence_counter: list[int]) -> int:
        sequence = sequence_counter[0]
        sequence_counter[0] += 1
        return sequence

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
        runtime.plan_buffers[activity_id] = (
            f"{runtime.plan_buffers.get(activity_id, '')}{delta}"
        )
        return runtime.plan_buffers[activity_id]

    def _thread_user_message_view(
        self,
        session_id: str,
        contents: list[UserInput] | Any,
    ) -> tuple[str, list[MessageAttachmentPayload]]:
        if not isinstance(contents, list):
            return "", []

        parts: list[str] = []
        attachments: list[MessageAttachmentPayload] = []
        seen_attachment_keys: set[str] = set()
        for item in contents:
            root = self._coerce_user_input(item)
            if root is None:
                continue
            rendered = self._render_user_input_text(root)
            if rendered:
                parts.append(rendered)
            attachment = self._user_input_attachment(session_id, root)
            if attachment is None:
                continue
            attachment_key = (
                attachment.path
                or attachment.preview_url
                or attachment.content_url
                or attachment.name
            )
            if attachment_key in seen_attachment_keys:
                continue
            seen_attachment_keys.add(attachment_key)
            attachments.append(attachment)
        if not parts:
            return "", attachments
        return self._strip_plan_mode_prompt("\n".join(parts)), attachments

    def _thread_user_message_text(self, contents: list[UserInput] | Any) -> str:
        return self._thread_user_message_view("", contents)[0]

    def _assistant_transcript_item_id(
        self,
        runtime: CodexSessionRuntime,
        turn_id: str,
    ) -> str:
        snapshot = runtime.turn_snapshots.get(turn_id)
        existing_item_id = snapshot.assistant_item_id if snapshot is not None else None
        if isinstance(existing_item_id, str) and existing_item_id:
            return existing_item_id
        return f"{turn_id}:assistant-transcript"

    def _transcript_buffer_key(self, turn_id: str, role: str) -> str:
        return f"{turn_id}:{role}"

    def _transcript_delta(self, previous_text: str, current_text: str) -> str:
        if not current_text:
            return ""
        if not previous_text:
            return current_text
        if current_text.startswith(previous_text):
            return current_text[len(previous_text) :]
        common_prefix_length = 0
        for previous_char, current_char in zip(previous_text, current_text):
            if previous_char != current_char:
                break
            common_prefix_length += 1
        return current_text[common_prefix_length:]

    def _clear_transcript_buffers(
        self,
        runtime: CodexSessionRuntime,
        turn_id: str,
    ) -> None:
        runtime.realtime_transcript_buffers.pop(
            self._transcript_buffer_key(turn_id, "assistant"),
            None,
        )

    def _user_input_to_state_item(
        self,
        item: CodexUserInputRoot,
    ) -> dict[str, Any] | None:
        if isinstance(item, TextUserInput):
            return {
                "type": "text",
                "text": item.text,
                "text_elements": self._safe_json_value(item.text_elements or []),
            }
        if isinstance(item, ImageUserInput):
            return {
                "type": "image",
                "url": item.url,
            }
        if isinstance(item, LocalImageUserInput):
            return {
                "type": "localImage",
                "path": item.path,
            }
        if isinstance(item, SkillUserInput):
            return {
                "type": "skill",
                "name": item.name,
                "path": item.path,
            }
        if isinstance(item, MentionUserInput):
            return {
                "type": "mention",
                "name": item.name,
                "path": item.path,
            }
        return None

    def _render_user_input_text(self, item: CodexUserInputRoot) -> str:
        if isinstance(item, TextUserInput):
            return item.text.strip()
        if isinstance(item, LocalImageUserInput):
            return f"[Local image] {item.path}"
        if isinstance(item, SkillUserInput):
            return f"[Skill] {item.name} ({item.path})"
        if isinstance(item, MentionUserInput):
            return f"[Mention] {item.name} ({item.path})"
        return ""

    def _user_input_attachment(
        self,
        session_id: str,
        item: CodexUserInputRoot,
    ) -> MessageAttachmentPayload | None:
        if isinstance(item, ImageUserInput):
            parsed_name = Path(urlparse(item.url).path).name
            return MessageAttachmentPayload(
                name=parsed_name or "image",
                mime_type="image/*",
                kind="image",
                preview_url=item.url,
                content_url=item.url,
            )

        if isinstance(item, LocalImageUserInput):
            preview = self._preview_metadata_for_local_image(session_id, item.path)
            return MessageAttachmentPayload(
                name=preview.get("preview_name")
                if isinstance(preview.get("preview_name"), str)
                else Path(item.path).name,
                mime_type=preview.get("mime_type")
                if isinstance(preview.get("mime_type"), str)
                else "image/*",
                size=preview.get("size") if isinstance(preview.get("size"), int) else None,
                kind="image",
                preview_url=preview.get("preview_url")
                if isinstance(preview.get("preview_url"), str)
                else None,
                content_url=preview.get("preview_url")
                if isinstance(preview.get("preview_url"), str)
                else None,
                path=item.path,
            )

        if isinstance(item, MentionUserInput):
            return MessageAttachmentPayload(
                name=item.name or Path(item.path).name,
                mime_type="application/octet-stream",
                kind="binary",
                path=item.path,
            )

        return None

    def _strip_plan_mode_prompt(self, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(PLAN_MODE_PROMPT_PREFIX):
            return normalized
        _, _, remainder = normalized.partition("User request:")
        return remainder.strip() if remainder.strip() else normalized

    def _notification_belongs_to_turn(
        self, notification: Notification, turn_id: str
    ) -> bool:
        notification_turn_id = self._notification_value(notification, "turn_id")
        if notification_turn_id is None:
            turn = getattr(notification.payload, "turn", None)
            notification_turn_id = getattr(turn, "id", None)
        if notification_turn_id is None:
            return (
                notification.method.startswith("thread/")
                or notification.method == "serverRequest/resolved"
            )
        return notification_turn_id == turn_id

    def _notification_value(self, notification: Notification, attribute: str) -> Any:
        payload = notification.payload
        return getattr(payload, attribute, None)

    def _unwrap_thread_item(self, item: Any) -> Any:
        if item is None:
            return None
        return getattr(item, "root", item)

    def _coerce_thread_item(self, item: Any) -> CodexThreadItemRoot | None:
        unwrapped = self._unwrap_thread_item(item)
        if unwrapped is None:
            return None
        if self._is_thread_item_root(unwrapped):
            return unwrapped
        candidate: dict[str, Any] | None = None
        if isinstance(unwrapped, dict):
            candidate = unwrapped
        elif hasattr(unwrapped, "type"):
            dumped = self._safe_model_dump(unwrapped)
            candidate = dumped if isinstance(dumped, dict) else None
        if isinstance(candidate, dict):
            try:
                return ThreadItem.model_validate(candidate).root
            except Exception:
                return None
        return None

    def _is_thread_item_root(self, item: Any) -> bool:
        return isinstance(
            item,
            (
                UserMessageThreadItem,
                HookPromptThreadItem,
                AgentMessageThreadItem,
                PlanThreadItem,
                ReasoningThreadItem,
                CommandExecutionThreadItem,
                FileChangeThreadItem,
                McpToolCallThreadItem,
                DynamicToolCallThreadItem,
                CollabAgentToolCallThreadItem,
                WebSearchThreadItem,
                ImageViewThreadItem,
                ImageGenerationThreadItem,
                EnteredReviewModeThreadItem,
                ExitedReviewModeThreadItem,
                ContextCompactionThreadItem,
            ),
        )

    def _coerce_user_input(self, item: Any) -> CodexUserInputRoot | None:
        unwrapped = getattr(item, "root", item)
        if isinstance(
            unwrapped,
            (
                TextUserInput,
                ImageUserInput,
                LocalImageUserInput,
                SkillUserInput,
                MentionUserInput,
            ),
        ):
            return unwrapped
        if isinstance(unwrapped, dict):
            try:
                return UserInput.model_validate(unwrapped).root
            except Exception:
                return None
        return None

    def _thread_status_value(self, status: ThreadStatus | dict[str, Any] | None) -> str:
        if isinstance(status, ThreadStatus):
            return status.root.type
        if isinstance(status, dict):
            root = status.get("root")
            if isinstance(root, dict):
                status_type = root.get("type")
                if isinstance(status_type, str) and status_type:
                    return status_type
            status_type = status.get("type")
            if isinstance(status_type, str) and status_type:
                return status_type
        return "idle"

    def _thread_active_flags(
        self, status: ThreadStatus | dict[str, Any] | None
    ) -> list[str]:
        if isinstance(status, ThreadStatus):
            root = status.root
            active_flags = getattr(root, "active_flags", [])
            return [flag.value for flag in active_flags]
        if isinstance(status, dict):
            root = status.get("root")
            if isinstance(root, dict):
                raw_flags = root.get("activeFlags")
                if isinstance(raw_flags, list):
                    return [
                        str(flag.get("value") if isinstance(flag, dict) else flag)
                        for flag in raw_flags
                    ]
            raw_flags = status.get("activeFlags")
            if isinstance(raw_flags, list):
                return [
                    str(flag.get("value") if isinstance(flag, dict) else flag)
                    for flag in raw_flags
                ]
        return []

    def _turn_status_value(self, status: TurnStatus | str | None) -> str:
        if isinstance(status, TurnStatus):
            return status.value
        if isinstance(status, str) and status:
            return status
        return "idle"

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
        return normalize_protocol_collaboration_mode(
            context.backend_state.get("collaboration_mode"),
            default_model=default_model if isinstance(default_model, str) else "",
            default_reasoning_effort=(
                default_effort
                if default_effort is None or isinstance(default_effort, str)
                else None
            ),
        )

    def _response_turn_id(
        self, response: TurnStartResponse | BaseModel | dict[str, Any]
    ) -> str | None:
        if isinstance(response, TurnStartResponse) and response.turn.id:
            return response.turn.id
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
            return {
                str(key): self._safe_json_value(item) for key, item in value.items()
            }
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json")
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        if hasattr(value, "__dict__"):
            return {
                str(key): self._safe_json_value(item)
                for key, item in vars(value).items()
                if not str(key).startswith("_")
            }
        return {"value": value}

    def _safe_json_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._safe_json_value(item) for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._safe_json_value(item) for item in value]
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "__dict__"):
            return {
                str(key): self._safe_json_value(item)
                for key, item in vars(value).items()
                if not str(key).startswith("_")
            }
        return value

    def _summarize_file_change(self, item: FileChangeThreadItem) -> str:
        count = len(item.changes)
        status = item.status.value
        return f"{count} file change{'s' if count != 1 else ''} with status {status}."

    def _summarize_tool_result(
        self,
        item: McpToolCallThreadItem | DynamicToolCallThreadItem,
    ) -> str:
        if isinstance(item, McpToolCallThreadItem):
            status = item.status.value
            if item.error is not None:
                return item.error.message
            return f"Finished with status {status}."
        status = item.status.value
        return f"Finished with status {status}."

    def _summarize_collab_result(self, item: CollabAgentToolCallThreadItem) -> str:
        receiver_count = len(item.receiver_thread_ids)
        status = item.status.value
        return f"{receiver_count} agent target{'s' if receiver_count != 1 else ''}, status {status}."
