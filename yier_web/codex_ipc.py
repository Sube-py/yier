from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import tempfile
from time import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4


if TYPE_CHECKING:
    from yier_web.chat import ChatService


INITIALIZING_CLIENT_ID = "initializing-client"
IPC_REQUEST_TIMEOUT_SECONDS = 5.0
IPC_RECONNECT_DELAY_SECONDS = 1.0
IPC_DEBUG_ENV = "YIER_CODEX_IPC_DEBUG"
IPC_DEBUG_FULL_ENV = "YIER_CODEX_IPC_DEBUG_FULL"
IPC_DEBUG_TEXT_LIMIT = 4000
IPC_METHOD_VERSIONS = {
    "thread-stream-state-changed": 5,
    "thread-archived": 2,
    "thread-unarchived": 1,
    "thread-follower-start-turn": 1,
    "thread-follower-steer-turn": 1,
    "thread-follower-interrupt-turn": 1,
    "thread-follower-set-model-and-reasoning": 1,
    "thread-follower-set-collaboration-mode": 1,
    "thread-follower-edit-last-user-turn": 1,
    "thread-follower-command-approval-decision": 1,
    "thread-follower-file-approval-decision": 1,
    "thread-follower-submit-user-input": 1,
    "thread-follower-submit-mcp-server-elicitation-response": 1,
    "thread-follower-set-queued-follow-ups-state": 1,
    "thread-queued-followups-changed": 1,
}
RequestCanHandle = Callable[[dict[str, Any]], Awaitable[bool]]
RequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
BroadcastHandler = Callable[[dict[str, Any]], Awaitable[None]]
logger = logging.getLogger(__name__)


def _env_flag(name: str) -> bool:
    value = os.getenv(name, "")
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _ipc_debug_enabled() -> bool:
    return _env_flag(IPC_DEBUG_ENV)


def _ipc_debug_full_enabled() -> bool:
    return _env_flag(IPC_DEBUG_FULL_ENV)


def _truncate_debug_text(value: str, limit: int = IPC_DEBUG_TEXT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...<truncated {len(value) - limit} chars>"


def _debug_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        return repr(value)


def _ipc_debug_log(message: str, **fields: Any) -> None:
    if not _ipc_debug_enabled():
        return
    filtered_fields = {
        key: value
        for key, value in fields.items()
        if value is not None
    }
    if filtered_fields:
        rendered = ", ".join(
            f"{key}={_truncate_debug_text(_debug_json(value))}"
            for key, value in filtered_fields.items()
        )
        logger.warning(f"[codex-ipc] {message} | {rendered}")
        return
    logger.warning(f"[codex-ipc] {message}")


def _ipc_message_summary(message: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "type": message.get("type"),
        "method": message.get("method"),
        "requestId": message.get("requestId"),
        "sourceClientId": message.get("sourceClientId"),
        "targetClientId": message.get("targetClientId"),
        "resultType": message.get("resultType"),
        "version": message.get("version"),
    }
    params = message.get("params")
    if isinstance(params, dict):
        summary["paramKeys"] = sorted(params.keys())
        conversation_id = (
            params.get("conversationId")
            or params.get("conversation_id")
            or params.get("threadId")
            or params.get("thread_id")
        )
        if conversation_id is not None:
            summary["conversationId"] = conversation_id
    request = message.get("request")
    if isinstance(request, dict):
        summary["requestMethod"] = request.get("method")
        summary["requestVersion"] = request.get("version")
    error = message.get("error")
    if error is not None:
        summary["error"] = error
    return summary


def _conversation_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    requests = state.get("requests")
    turns = state.get("turns")
    return {
        "id": state.get("id"),
        "threadId": state.get("threadId"),
        "title": state.get("title"),
        "turnCount": len(turns) if isinstance(turns, list) else 0,
        "requestCount": len(requests) if isinstance(requests, list) else 0,
        "requestMethods": [
            request.get("method")
            for request in requests
            if isinstance(request, dict) and isinstance(request.get("method"), str)
        ]
        if isinstance(requests, list)
        else [],
        "latestCollaborationMode": state.get("latestCollaborationMode"),
        "threadRuntimeStatus": state.get("threadRuntimeStatus"),
        "triggerEvent": state.get("_yier_trigger_event"),
    }


def _ipc_method_version(method: str) -> int:
    return int(IPC_METHOD_VERSIONS.get(method, 0))


def _uid_socket_path() -> Path:
    return Path(tempfile.gettempdir()) / "codex-ipc" / f"ipc-{os.getuid()}.sock"


def _json_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


async def _read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    length_bytes = await reader.readexactly(4)
    payload_length = int.from_bytes(length_bytes, byteorder="little")
    payload_bytes = await reader.readexactly(payload_length)
    payload = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("IPC payload must decode to an object.")
    return payload


class CodexIpcClient:
    def __init__(
        self,
        *,
        client_type: str,
        socket_path: Path | None = None,
    ) -> None:
        self.client_type = client_type
        self.socket_path = socket_path or _uid_socket_path()
        self.client_id = INITIALIZING_CLIENT_ID
        self._closed = False
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._connected = asyncio.Event()
        self._request_handlers: dict[str, tuple[RequestCanHandle, RequestHandler]] = {}
        self._broadcast_handlers: dict[str, BroadcastHandler] = {}
        self._pending_responses: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._run_task: asyncio.Task[None] | None = None
        self._read_task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def add_request_handler(
        self,
        method: str,
        can_handle: RequestCanHandle,
        handler: RequestHandler,
    ) -> None:
        self._request_handlers[method] = (can_handle, handler)

    def add_broadcast_handler(
        self,
        method: str,
        handler: BroadcastHandler,
    ) -> None:
        self._broadcast_handlers[method] = handler

    async def start(self) -> None:
        if self._run_task is not None:
            return
        self._closed = False
        _ipc_debug_log(
            "starting ipc client",
            client_type=self.client_type,
            socket_path=str(self.socket_path),
        )
        self._run_task = asyncio.create_task(self._run(), name="codex-ipc-client")

    async def stop(self) -> None:
        self._closed = True
        self._connected.clear()
        _ipc_debug_log("stopping ipc client", client_id=self.client_id)
        if self._read_task is not None:
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._read_task
            self._read_task = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
        for future in list(self._pending_responses.values()):
            if not future.done():
                future.set_exception(RuntimeError("disposed"))
        self._pending_responses.clear()
        if self._run_task is not None:
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task
            self._run_task = None

    async def wait_until_connected(self, timeout: float = IPC_REQUEST_TIMEOUT_SECONDS) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    async def send_broadcast(self, method: str, params: dict[str, Any]) -> None:
        await self._ensure_connected()
        _ipc_debug_log(
            "send broadcast",
            method=method,
            params=params if _ipc_debug_full_enabled() else {"keys": sorted(params.keys())},
        )
        await self._send_message(
            {
                "type": "broadcast",
                "method": method,
                "params": params,
                "sourceClientId": self.client_id,
                "version": _ipc_method_version(method),
            }
        )

    async def send_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        target_client_id: str | None = None,
        allow_uninitialized: bool = False,
    ) -> dict[str, Any]:
        if allow_uninitialized:
            if self._writer is None:
                raise RuntimeError("not-connected")
        else:
            await self._ensure_connected()
        if not allow_uninitialized and self.client_id == INITIALIZING_CLIENT_ID:
            raise RuntimeError("not-initialized")
        request_id = str(uuid4())
        _ipc_debug_log(
            "send request",
            method=method,
            request_id=request_id,
            target_client_id=target_client_id,
            params=params if _ipc_debug_full_enabled() else {"keys": sorted(params.keys())},
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_responses[request_id] = future
        await self._send_message(
            {
                "type": "request",
                "requestId": request_id,
                "sourceClientId": self.client_id,
                "version": _ipc_method_version(method),
                "method": method,
                "params": params,
                "targetClientId": target_client_id,
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=IPC_REQUEST_TIMEOUT_SECONDS)
        finally:
            self._pending_responses.pop(request_id, None)

    async def _run(self) -> None:
        while not self._closed:
            try:
                _ipc_debug_log(
                    "connecting to ipc socket",
                    client_type=self.client_type,
                    socket_path=str(self.socket_path),
                )
                reader, writer = await asyncio.open_unix_connection(self.socket_path)
            except (FileNotFoundError, ConnectionError, OSError):
                _ipc_debug_log(
                    "ipc socket connect failed",
                    client_type=self.client_type,
                    socket_path=str(self.socket_path),
                )
                await asyncio.sleep(IPC_RECONNECT_DELAY_SECONDS)
                continue

            self._writer = writer
            self.client_id = INITIALIZING_CLIENT_ID
            _ipc_debug_log("ipc socket connected", socket_path=str(self.socket_path))
            self._read_task = asyncio.create_task(self._read_loop(reader), name="codex-ipc-reader")
            try:
                response = await self.send_request(
                    "initialize",
                    {"clientType": self.client_type},
                    allow_uninitialized=True,
                )
                if response.get("resultType") == "success":
                    result = response.get("result")
                    if isinstance(result, dict) and isinstance(result.get("clientId"), str):
                        self.client_id = result["clientId"]
                        self._connected.set()
                        _ipc_debug_log(
                            "ipc client initialized",
                            client_id=self.client_id,
                            client_type=self.client_type,
                        )
                await self._read_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _ipc_debug_log("ipc client loop error", error=str(exc))
            finally:
                self._connected.clear()
                _ipc_debug_log("ipc socket disconnected", client_id=self.client_id)
                self.client_id = INITIALIZING_CLIENT_ID
                if self._read_task is not None:
                    self._read_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._read_task
                    self._read_task = None
                if self._writer is not None:
                    self._writer.close()
                    with contextlib.suppress(Exception):
                        await self._writer.wait_closed()
                    self._writer = None
                for future in list(self._pending_responses.values()):
                    if not future.done():
                        future.set_exception(RuntimeError("connection-closed"))
                self._pending_responses.clear()
                if not self._closed:
                    await asyncio.sleep(IPC_RECONNECT_DELAY_SECONDS)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while not self._closed:
            message = await _read_frame(reader)
            _ipc_debug_log(
                "recv message",
                summary=_ipc_message_summary(message),
                payload=message if _ipc_debug_full_enabled() else None,
            )
            await self._handle_message(message)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type == "broadcast":
            await self._handle_broadcast(message)
            return
        if message_type == "client-discovery-request":
            await self._handle_client_discovery_request(message)
            return
        if message_type == "response":
            request_id = str(message.get("requestId", ""))
            future = self._pending_responses.get(request_id)
            if future is not None and not future.done():
                future.set_result(message)
            return
        if message_type == "request":
            await self._handle_request(message)

    async def _handle_broadcast(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", ""))
        handler = self._broadcast_handlers.get(method)
        if handler is None:
            return
        await handler(message)

    async def _handle_client_discovery_request(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("requestId", ""))
        request = message.get("request")
        if not isinstance(request, dict):
            await self._send_message(
                {
                    "type": "client-discovery-response",
                    "requestId": request_id,
                    "response": {"canHandle": False},
                }
            )
            return
        method = str(request.get("method", ""))
        params = request.get("params")
        version = int(request.get("version", 0) or 0)
        handler = self._request_handlers.get(method)
        if version != _ipc_method_version(method) or handler is None or not isinstance(params, dict):
            await self._send_message(
                {
                    "type": "client-discovery-response",
                    "requestId": request_id,
                    "response": {"canHandle": False},
                }
            )
            return

        can_handle, _ = handler
        try:
            response = bool(await can_handle(params))
        except Exception:
            response = False
        await self._send_message(
            {
                "type": "client-discovery-response",
                "requestId": request_id,
                "response": {"canHandle": response},
            }
        )

    async def _handle_request(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("requestId", ""))
        method = str(message.get("method", ""))
        params = message.get("params")
        version = int(message.get("version", 0) or 0)
        handler = self._request_handlers.get(method)
        if version != _ipc_method_version(method):
            await self._send_message(
                {
                    "type": "response",
                    "requestId": request_id,
                    "resultType": "error",
                    "error": "request-version-mismatch",
                }
            )
            return
        if handler is None or not isinstance(params, dict):
            await self._send_message(
                {
                    "type": "response",
                    "requestId": request_id,
                    "resultType": "error",
                    "error": "no-handler-for-request",
                }
            )
            return

        _, handle = handler
        try:
            result = await handle(message)
        except Exception as exc:
            await self._send_message(
                {
                    "type": "response",
                    "requestId": request_id,
                    "resultType": "error",
                    "error": str(exc) or "error-handling-request",
                }
            )
            return

        await self._send_message(
            {
                "type": "response",
                "requestId": request_id,
                "resultType": "success",
                "method": method,
                "handledByClientId": self.client_id,
                "result": result,
            }
        )

    async def _ensure_connected(self) -> None:
        if self._connected.is_set():
            return
        connected = await self.wait_until_connected()
        if not connected:
            raise RuntimeError("not-connected")

    async def _send_message(self, payload: dict[str, Any]) -> None:
        writer = self._writer
        if writer is None:
            raise RuntimeError("not-connected")
        payload_bytes = _json_dumps(payload)
        _ipc_debug_log(
            "write frame",
            bytes=len(payload_bytes),
            summary=_ipc_message_summary(payload),
            payload=payload if _ipc_debug_full_enabled() else None,
        )
        async with self._write_lock:
            writer.write(len(payload_bytes).to_bytes(4, byteorder="little"))
            writer.write(payload_bytes)
            await writer.drain()


class CodexThreadFollowerBridge:
    def __init__(
        self,
        *,
        chat_service: ChatService,
        client_type: str = "yier",
        socket_path: Path | None = None,
    ) -> None:
        self.chat_service = chat_service
        self.client = CodexIpcClient(
            client_type=client_type,
            socket_path=socket_path,
        )
        self._register_request_handlers()

    async def start(self) -> None:
        await self.client.start()

    async def stop(self) -> None:
        await self.client.stop()

    async def notify_stream_event(self, event: str, data: dict[str, Any]) -> None:
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return
        if not self._is_codex_session(session_id):
            return

        if event in {
            "run_started",
            "approval_requested",
            "approval_resolved",
            "turn_completed",
            "turn_aborted",
            "stream_error",
            "done",
        }:
            await self.broadcast_stream_state(session_id, trigger_event=event)

        if event in {
            "background_followup_queued",
            "background_followup_started",
            "background_followup_finished",
        }:
            await self.broadcast_queued_followups(session_id)

    async def broadcast_stream_state(
        self,
        session_id: str,
        *,
        trigger_event: str | None = None,
    ) -> None:
        if not self.client.is_connected or not self._is_codex_session(session_id):
            return
        conversation_state = self.chat_service.build_codex_ipc_conversation_state(session_id)
        payload = {
            "conversationId": session_id,
            "change": {
                "type": "snapshot",
                "conversationState": {
                    **conversation_state,
                    "_yier_trigger_event": trigger_event or "",
                    "_yier_updated_at": time(),
                },
            },
        }
        _ipc_debug_log(
            "broadcast stream state",
            session_id=session_id,
            trigger_event=trigger_event or "",
            conversation_state=_conversation_state_summary(
                payload["change"]["conversationState"]
            ),
            payload=payload if _ipc_debug_full_enabled() else None,
        )
        with contextlib.suppress(Exception):
            await self.client.send_broadcast("thread-stream-state-changed", payload)

    async def broadcast_queued_followups(self, session_id: str) -> None:
        if not self.client.is_connected or not self._is_codex_session(session_id):
            return
        messages = self.chat_service.build_codex_ipc_queued_followups(session_id)
        _ipc_debug_log(
            "broadcast queued followups",
            session_id=session_id,
            message_count=len(messages),
            payload=messages if _ipc_debug_full_enabled() else None,
        )
        with contextlib.suppress(Exception):
            await self.client.send_broadcast(
                "thread-queued-followups-changed",
                {
                    "conversationId": session_id,
                    "messages": messages,
                },
            )

    def _register_request_handlers(self) -> None:
        self.client.add_request_handler(
            "thread-follower-start-turn",
            self._can_handle_thread_request,
            self._handle_start_turn,
        )
        self.client.add_request_handler(
            "thread-follower-steer-turn",
            self._can_handle_thread_request,
            self._handle_steer_turn,
        )
        self.client.add_request_handler(
            "thread-follower-interrupt-turn",
            self._can_handle_thread_request,
            self._handle_interrupt_turn,
        )
        self.client.add_request_handler(
            "thread-follower-set-model-and-reasoning",
            self._can_handle_thread_request,
            self._handle_set_model_and_reasoning,
        )
        self.client.add_request_handler(
            "thread-follower-set-collaboration-mode",
            self._can_handle_thread_request,
            self._handle_set_collaboration_mode,
        )
        self.client.add_request_handler(
            "thread-follower-edit-last-user-turn",
            self._can_handle_thread_request,
            self._handle_edit_last_user_turn,
        )
        self.client.add_request_handler(
            "thread-follower-command-approval-decision",
            self._can_handle_thread_request,
            self._handle_command_approval_decision,
        )
        self.client.add_request_handler(
            "thread-follower-file-approval-decision",
            self._can_handle_thread_request,
            self._handle_file_approval_decision,
        )
        self.client.add_request_handler(
            "thread-follower-submit-user-input",
            self._can_handle_thread_request,
            self._handle_submit_user_input,
        )
        self.client.add_request_handler(
            "thread-follower-submit-mcp-server-elicitation-response",
            self._can_handle_thread_request,
            self._handle_submit_mcp_server_elicitation_response,
        )
        self.client.add_request_handler(
            "thread-follower-set-queued-follow-ups-state",
            self._can_handle_thread_request,
            self._handle_set_queued_followups_state,
        )

    async def _can_handle_thread_request(self, params: dict[str, Any]) -> bool:
        conversation_id = self._conversation_id(params)
        return self.chat_service.can_handle_codex_conversation(conversation_id)

    async def _handle_start_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        params = self._params(request)
        session_id = self.chat_service.ensure_codex_conversation_session(
            self._conversation_id(params)
        )
        turn_start_params = self._turn_start_params(params)
        input_payload = self._start_turn_input_payload(turn_start_params)
        if input_payload in (None, "", []):
            raise RuntimeError("missing-thread-follower-prompt")
        model_updates = self._model_and_reasoning_updates(turn_start_params)
        if model_updates:
            self.chat_service.update_session_backend_state(session_id, model_updates)
        collaboration_mode = self._collaboration_mode_value(turn_start_params)
        _ipc_debug_log(
            "handle start turn",
            session_id=session_id,
            request_id=request.get("requestId"),
            input_payload=input_payload,
            model_updates=model_updates,
            collaboration_mode=collaboration_mode,
        )
        if collaboration_mode is not None:
            self.chat_service.update_session_backend_state(
                session_id,
                {"collaboration_mode": collaboration_mode},
            )
        start_response = await self.chat_service.start_codex_turn_in_background(
            session_id,
            input_payload,
        )
        await self.broadcast_stream_state(session_id, trigger_event="thread-follower-start-turn")
        return {"result": start_response}

    async def _handle_steer_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        params = self._params(request)
        session_id = self.chat_service.ensure_codex_conversation_session(
            self._conversation_id(params)
        )
        turn_id = self._turn_id(params)
        if not turn_id:
            turn_id = None
        input_payload = self._steer_input_payload(params)
        if input_payload in (None, "", []):
            raise RuntimeError("missing-steer-input")
        _ipc_debug_log(
            "handle steer turn",
            session_id=session_id,
            request_id=request.get("requestId"),
            turn_id=turn_id,
            input_payload=input_payload,
        )
        result = await self.chat_service.steer_codex_turn(
            session_id=session_id,
            turn_id=turn_id,
            input_payload=input_payload,
        )
        await self.broadcast_stream_state(session_id, trigger_event="thread-follower-steer-turn")
        return {"result": result}

    async def _handle_interrupt_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        params = self._params(request)
        session_id = self.chat_service.ensure_codex_conversation_session(
            self._conversation_id(params)
        )
        turn_id = self._turn_id(params)
        _ipc_debug_log(
            "handle interrupt turn",
            session_id=session_id,
            request_id=request.get("requestId"),
            turn_id=turn_id or None,
        )
        await self.chat_service.interrupt_codex_turn(
            session_id=session_id,
            turn_id=turn_id or None,
        )
        await self.broadcast_stream_state(session_id, trigger_event="thread-follower-interrupt-turn")
        return {"ok": True}

    async def _handle_set_model_and_reasoning(self, request: dict[str, Any]) -> dict[str, Any]:
        params = self._params(request)
        session_id = self.chat_service.ensure_codex_conversation_session(
            self._conversation_id(params)
        )
        updates = self._model_and_reasoning_updates(params)
        _ipc_debug_log(
            "handle set model and reasoning",
            session_id=session_id,
            request_id=request.get("requestId"),
            updates=updates,
        )
        if updates:
            self.chat_service.update_session_backend_state(session_id, updates)
        await self.broadcast_stream_state(
            session_id,
            trigger_event="thread-follower-set-model-and-reasoning",
        )
        return {"ok": True}

    async def _handle_set_collaboration_mode(self, request: dict[str, Any]) -> dict[str, Any]:
        params = self._params(request)
        session_id = self.chat_service.ensure_codex_conversation_session(
            self._conversation_id(params)
        )
        collaboration_mode = self._collaboration_mode_value(params)
        if collaboration_mode is None:
            raise RuntimeError("missing-collaboration-mode")
        _ipc_debug_log(
            "handle set collaboration mode",
            session_id=session_id,
            request_id=request.get("requestId"),
            collaboration_mode=collaboration_mode,
        )
        applied = {"collaboration_mode": collaboration_mode}
        self.chat_service.update_session_backend_state(session_id, applied)
        await self.broadcast_stream_state(
            session_id,
            trigger_event="thread-follower-set-collaboration-mode",
        )
        return {"ok": True}

    async def _handle_edit_last_user_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        params = self._params(request)
        session_id = self.chat_service.ensure_codex_conversation_session(
            self._conversation_id(params)
        )
        content = self._message_text(params)
        _ipc_debug_log(
            "handle edit last user turn",
            session_id=session_id,
            request_id=request.get("requestId"),
            content=content,
        )
        if content:
            self.chat_service.edit_last_codex_user_turn(session_id, content)
        return {"ok": True}

    async def _handle_command_approval_decision(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._handle_approval_decision(
            request,
            preferred_kind="command",
            response_payload_builder=self._command_or_file_approval_response_payload,
        )

    async def _handle_file_approval_decision(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._handle_approval_decision(
            request,
            preferred_kind="file_change",
            response_payload_builder=self._command_or_file_approval_response_payload,
        )

    async def _handle_submit_user_input(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._handle_approval_decision(
            request,
            preferred_kind=None,
            response_payload_builder=lambda params: self._response_payload(params),
        )

    async def _handle_submit_mcp_server_elicitation_response(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._handle_approval_decision(
            request,
            preferred_kind="mcp_elicitation",
            response_payload_builder=lambda params: self._response_payload(params),
        )

    async def _handle_set_queued_followups_state(self, request: dict[str, Any]) -> dict[str, Any]:
        params = self._params(request)
        session_id = self.chat_service.ensure_codex_conversation_session(
            self._conversation_id(params)
        )
        self.chat_service.update_session_backend_state(
            session_id,
            {"queued_followups_state": params},
        )
        _ipc_debug_log(
            "handle queued followups state",
            session_id=session_id,
            request_id=request.get("requestId"),
            params=params,
        )
        await self.broadcast_queued_followups(session_id)
        return {"ok": True}

    async def _handle_approval_decision(
        self,
        request: dict[str, Any],
        *,
        preferred_kind: str | None,
        response_payload_builder: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        params = self._params(request)
        session_id = self.chat_service.ensure_codex_conversation_session(
            self._conversation_id(params)
        )
        request_id = self._approval_request_id(params)
        if request_id is None:
            request_id = self.chat_service.resolve_pending_approval_request_id(
                session_id,
                preferred_kind=preferred_kind,
            )
        if request_id is None:
            raise RuntimeError("missing-approval-request-id")
        response_payload = response_payload_builder(params)
        if response_payload is None:
            raise RuntimeError("missing-response-payload")
        _ipc_debug_log(
            "handle approval decision",
            session_id=session_id,
            request_id=request.get("requestId"),
            approval_request_id=request_id,
            preferred_kind=preferred_kind,
            response_payload=response_payload,
        )
        handled = await self.chat_service.respond_to_codex_raw_request(
            session_id=session_id,
            request_id=request_id,
            response_payload=response_payload,
        )
        if not handled:
            raise RuntimeError("approval-request-not-found")
        await self.broadcast_stream_state(session_id, trigger_event="approval-response")
        return {"ok": True}

    def _is_codex_session(self, session_id: str) -> bool:
        if not isinstance(session_id, str) or not session_id.strip():
            return False
        metadata = self.chat_service.get_session_metadata(session_id)
        return metadata.get("backend_id") == "codex"

    def _conversation_id(self, params: dict[str, Any]) -> str:
        for key in ("conversationId", "conversation_id", "threadId", "thread_id"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _params(self, request: dict[str, Any]) -> dict[str, Any]:
        params = request.get("params")
        return params if isinstance(params, dict) else {}

    def _turn_start_params(self, params: dict[str, Any]) -> dict[str, Any]:
        turn_start_params = params.get("turnStartParams")
        if isinstance(turn_start_params, dict):
            return turn_start_params
        return params

    def _collaboration_mode_value(self, params: dict[str, Any]) -> dict[str, Any] | str | None:
        for key in ("collaborationMode", "collaboration_mode"):
            value = params.get(key)
            if isinstance(value, dict):
                return dict(value)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _prompt_text(self, params: dict[str, Any]) -> str:
        for key in ("prompt", "message", "text"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        content = params.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, dict):
            text_value = content.get("text")
            if isinstance(text_value, str) and text_value.strip():
                return text_value.strip()

        for key in ("input", "inputItems", "items"):
            value = params.get(key)
            if isinstance(value, list):
                parts = []
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    text_value = item.get("text")
                    if isinstance(text_value, str) and text_value.strip():
                        parts.append(text_value.strip())
                if parts:
                    return "\n".join(parts)
            if isinstance(value, dict):
                text_value = value.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    return text_value.strip()
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _message_text(self, params: dict[str, Any]) -> str:
        message = params.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return self._prompt_text(params)

    def _steer_input_payload(self, params: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any] | str | None:
        for key in ("input", "inputItems", "items"):
            value = params.get(key)
            if isinstance(value, (list, dict)):
                return value
            if isinstance(value, str) and value.strip():
                return value.strip()
        text = self._prompt_text(params)
        if text:
            return text
        return None

    def _start_turn_input_payload(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]] | dict[str, Any] | str | None:
        input_payload = self._steer_input_payload(params)
        if input_payload not in (None, "", []):
            return input_payload
        prompt = self._prompt_text(params)
        if prompt:
            return prompt
        return None

    def _turn_id(self, params: dict[str, Any]) -> str:
        for key in ("expectedTurnId", "turnId", "turn_id"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _approval_request_id(self, params: dict[str, Any]) -> str | None:
        for key in (
            "requestId",
            "request_id",
            "approvalRequestId",
            "approval_request_id",
            "elicitationRequestId",
            "elicitation_request_id",
        ):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _approval_decision(self, params: dict[str, Any]) -> str:
        raw_decision = None
        for key in ("decision", "action", "response"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                raw_decision = value.strip().lower()
                break
        if raw_decision in {"accept", "approve", "approved", "allow"}:
            return "accept"
        if raw_decision in {"accept_for_session", "approve_for_session", "allow_for_session"}:
            return "accept_for_session"
        if raw_decision in {"decline", "deny", "reject", "rejected"}:
            return "decline"
        if raw_decision == "cancel":
            return "cancel"
        return "accept"

    def _command_or_file_approval_response_payload(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        decision = self._approval_decision(params)
        if decision == "accept_for_session":
            return {"decision": "acceptForSession"}
        if decision in {"accept", "decline", "cancel"}:
            return {"decision": decision}
        return {"decision": "accept"}

    def _response_payload(self, params: dict[str, Any]) -> dict[str, Any] | None:
        response = params.get("response")
        if isinstance(response, dict):
            return response
        content = self._approval_content(params)
        if content is not None:
            return content
        return None

    def _approval_content(self, params: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("content", "payload", "response"):
            value = params.get(key)
            if isinstance(value, dict):
                return value
        text = self._prompt_text(params)
        if text:
            return {"text": text}
        return None

    def _model_and_reasoning_updates(self, params: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        for source_key, target_key in (
            ("model", "model"),
            ("reasoningEffort", "reasoning_effort"),
            ("reasoning_effort", "reasoning_effort"),
            ("serviceTier", "service_tier"),
            ("service_tier", "service_tier"),
            ("effort", "reasoning_effort"),
        ):
            value = params.get(source_key)
            if isinstance(value, str) and value.strip():
                updates[target_key] = value.strip()
        return updates
