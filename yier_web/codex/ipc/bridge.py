"""Codex Thread Follower Bridge.

Bridges ``ChatService`` stream events to the Codex IPC system:

  **Outbound** (yier → Codex VSCode):
    - Lifecycle snapshots at key points (run_started, turn_completed, done, …)
    - Incremental Immer patches during streaming (assistant_delta, assistant_message, …)

  **Inbound** (Codex VSCode → yier):
    - ``thread-stream-state-changed`` → apply snapshot/patches to ConversationState
    - ``thread-queued-followups-changed`` → notify frontend
    - ``thread-read-state-changed`` → sync hasUnreadTurn
    - ``client-status-changed`` → notify frontend

  **Request handling** (Codex VSCode → yier):
    - 11 thread-follower request methods (start-turn, steer-turn, interrupt, …)
"""

from __future__ import annotations

import contextlib
from copy import deepcopy
import inspect
import logging
from time import time
from typing import TYPE_CHECKING, Any, Callable

from yier_web.codex.ipc.client import CodexIpcClient
from yier_web.codex.ipc.constants import ipc_method_version
from yier_web.codex.ipc.debug import (
    conversation_state_summary,
    ipc_debug_full_enabled,
    ipc_debug_log,
)
from yier_web.codex.ipc.patches import (
    build_stream_patches_payload,
    log_stream_patches,
    patches_for_state_update,
)
from yier_web.codex.ipc.params import (
    approval_request_id,
    command_or_file_approval_response_payload,
    collaboration_mode_value,
    conversation_id,
    message_text,
    model_and_reasoning_updates,
    params,
    response_payload,
    start_turn_input_payload,
    steer_input_payload,
    turn_id,
    turn_start_params,
)

if TYPE_CHECKING:
    from pathlib import Path

    from yier_web.chat import ChatService

logger = logging.getLogger(__name__)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class CodexThreadFollowerBridge:
    """Bridge between ChatService stream events and the Codex IPC system.

    Parameters
    ----------
    chat_service
        The application ChatService instance (for state building, session
        management, and event broker access).
    client_type
        IPC client type identifier sent during initialize (default ``"yier"``).
    socket_path
        Override the IPC socket path (defaults to the standard path
        from ``frame_protocol.ipc_socket_path()``).
    """

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
        self._last_broadcast_state: dict[str, dict[str, Any]] = {}
        self._register_broadcast_handlers()
        self._register_request_handlers()

    async def start(self) -> None:
        await self.client.start()

    async def stop(self) -> None:
        await self.client.stop()

    # ── stream event → IPC broadcast ──

    async def notify_stream_event(self, event: str, data: dict[str, Any]) -> None:
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return
        if not self._is_codex_session(session_id):
            return

        if event == "run_started":
            await self._emit_run_started_sequence(session_id)
            return
        if event == "done":
            await self._emit_done_sequence(session_id)
            return

        # --- lifecycle snapshots (full ConversationState) ---
        if event in {
            "turn_completed",
            "turn_aborted",
            "approval_requested",
            "approval_resolved",
            "stream_error",
        }:
            self._last_broadcast_state.pop(session_id, None)
            await self.broadcast_stream_state(session_id, trigger_event=event)
            return

        # --- incremental patches for real-time streaming progress ---
        if event in {"assistant_delta", "assistant_message", "token_usage_updated", "plan"}:
            await self._emit_patches_if_changed(session_id, event)
            return

        # --- queued followups ---
        if event in {
            "background_followup_queued",
            "background_followup_started",
            "background_followup_finished",
        }:
            await self.broadcast_queued_followups(session_id)

    # ── outbound: snapshots ──

    async def broadcast_stream_state(
        self,
        session_id: str,
        *,
        trigger_event: str | None = None,
        conversation_state: dict[str, Any] | None = None,
    ) -> None:
        """Send a full ConversationState snapshot via IPC broadcast."""
        if not self.client.is_connected or not self._is_codex_session(session_id):
            return
        if conversation_state is None:
            conversation_state = await _maybe_await(
                self.chat_service.build_codex_ipc_conversation_state(session_id)
            )
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
            "version": ipc_method_version("thread-stream-state-changed"),
            "type": "thread-stream-state-changed",
        }
        ipc_debug_log(
            "broadcast stream state",
            session_id=session_id,
            trigger_event=trigger_event or "",
            conversation_state=conversation_state_summary(
                payload["change"]["conversationState"]
            ),
            payload=payload if ipc_debug_full_enabled() else None,
        )
        with contextlib.suppress(Exception):
            await self.client.send_broadcast("thread-stream-state-changed", payload)

    async def _conversation_state(self, session_id: str) -> dict[str, Any]:
        return await _maybe_await(
            self.chat_service.build_codex_ipc_conversation_state(session_id)
        )

    async def _emit_run_started_sequence(self, session_id: str) -> None:
        """Build current state, diff against cached state, send patches + snapshot."""
        conversation_state = await self._conversation_state(session_id)
        turns = conversation_state.get("turns")
        if not isinstance(turns, list) or not turns:
            self._last_broadcast_state.pop(session_id, None)
            await self.broadcast_stream_state(
                session_id,
                trigger_event="run_started",
                conversation_state=conversation_state,
            )
            self._last_broadcast_state[session_id] = deepcopy(conversation_state)
            return

        # Send incremental patches vs last known state
        old_state = self._last_broadcast_state.get(session_id, {})
        patches = patches_for_state_update(old_state, conversation_state)
        if patches:
            await self._send_stream_patches(session_id, "run_started", patches)

        # Send snapshot (with items cleared on the latest turn for the snapshot,
        # matching Codex behavior: snapshot has items=[] so follower gets structure first)
        snapshot_state = deepcopy(conversation_state)
        snapshot_turns = snapshot_state.get("turns")
        turn_index = len(turns) - 1
        if isinstance(snapshot_turns, list) and turn_index < len(snapshot_turns):
            snapshot_turn = snapshot_turns[turn_index]
            if isinstance(snapshot_turn, dict):
                snapshot_turn["items"] = []

        await self.broadcast_stream_state(
            session_id,
            trigger_event="run_started",
            conversation_state=snapshot_state,
        )

        # Cache the full state (with items) for subsequent diffs
        self._last_broadcast_state[session_id] = deepcopy(conversation_state)

    async def _emit_done_sequence(self, session_id: str) -> None:
        """Build completed state, diff, send patches + final snapshot."""
        conversation_state = await self._conversation_state(session_id)
        turns = conversation_state.get("turns")
        if not isinstance(turns, list) or not turns:
            self._last_broadcast_state.pop(session_id, None)
            return

        # Mark turn as completed in the state for diff
        turn_index = len(turns) - 1
        turn = turns[turn_index]
        if isinstance(turn, dict):
            turn["status"] = "completed"
        conversation_state["hasUnreadTurn"] = True
        conversation_state["threadRuntimeStatus"] = {
            "type": "idle",
            "activeFlags": [],
        }

        # Send incremental patches
        old_state = self._last_broadcast_state.get(session_id, {})
        patches = patches_for_state_update(old_state, conversation_state)
        if patches:
            await self._send_stream_patches(session_id, "done", patches)

        # Send final snapshot
        await self.broadcast_stream_state(
            session_id,
            trigger_event="turn_completed",
            conversation_state=conversation_state,
        )

        # Mark as read
        conversation_state["hasUnreadTurn"] = False
        read_patches = patches_for_state_update(
            self._last_broadcast_state.get(session_id, {}),
            conversation_state,
        )
        # Update cache before sending read patch
        self._last_broadcast_state[session_id] = deepcopy(conversation_state)
        if read_patches:
            await self._send_stream_patches(session_id, "done", read_patches)

    async def _emit_patches_if_changed(
        self,
        session_id: str,
        trigger_event: str,
    ) -> None:
        """Build current state, diff against cached state, send patches if changed."""
        if not self.client.is_connected or not self._is_codex_session(session_id):
            return
        new_state = await self._conversation_state(session_id)
        old_state = self._last_broadcast_state.get(session_id, {})
        patches = patches_for_state_update(old_state, new_state)
        if patches:
            await self._send_stream_patches(session_id, trigger_event, patches)
        self._last_broadcast_state[session_id] = deepcopy(new_state)

    async def broadcast_queued_followups(self, session_id: str) -> None:
        """Send queued followup messages via IPC broadcast."""
        if not self.client.is_connected or not self._is_codex_session(session_id):
            return
        messages = self.chat_service.build_codex_ipc_queued_followups(session_id)
        ipc_debug_log(
            "broadcast queued followups",
            session_id=session_id,
            message_count=len(messages),
            payload=messages if ipc_debug_full_enabled() else None,
        )
        with contextlib.suppress(Exception):
            await self.client.send_broadcast(
                "thread-queued-followups-changed",
                {
                    "conversationId": session_id,
                    "messages": messages,
                    "version": ipc_method_version("thread-queued-followups-changed"),
                    "type": "thread-queued-followups-changed",
                },
            )

    # ── outbound: incremental patches ──

    async def _send_stream_patches(
        self,
        session_id: str,
        trigger_event: str,
        patches: list[dict[str, Any]],
    ) -> None:
        payload = build_stream_patches_payload(session_id, patches)
        log_stream_patches(session_id, trigger_event, payload)
        with contextlib.suppress(Exception):
            await self.client.send_broadcast("thread-stream-state-changed", payload)

    # ── inbound: broadcast handlers ──

    def _register_broadcast_handlers(self) -> None:
        self.client.add_broadcast_handler(
            "thread-stream-state-changed",
            self._handle_thread_stream_state_changed_broadcast,
        )
        self.client.add_broadcast_handler(
            "thread-queued-followups-changed",
            self._handle_thread_queued_followups_changed_broadcast,
        )
        self.client.add_broadcast_handler(
            "thread-read-state-changed",
            self._handle_thread_read_state_changed_broadcast,
        )
        self.client.add_broadcast_handler(
            "client-status-changed",
            self._handle_client_status_changed_broadcast,
        )
        self.client.add_broadcast_handler(
            "thread-archived",
            self._handle_thread_archived_broadcast,
        )
        self.client.add_broadcast_handler(
            "thread-unarchived",
            self._handle_thread_unarchived_broadcast,
        )

    async def _handle_thread_stream_state_changed_broadcast(
        self,
        message: dict[str, Any],
    ) -> None:
        p = params(message)
        session_id = conversation_id(p)
        if not session_id or not self._is_codex_session(session_id):
            return

        source_client_id = message.get("sourceClientId")
        if isinstance(source_client_id, str) and source_client_id:
            self.chat_service.update_session_backend_state(
                session_id,
                {"ipc_source_client_id": source_client_id},
            )
        change = p.get("change")
        change_type = change.get("type") if isinstance(change, dict) else None
        if isinstance(change, dict) and hasattr(
            self.chat_service, "apply_codex_ipc_stream_change"
        ):
            self.chat_service.apply_codex_ipc_stream_change(session_id, change)
        ipc_debug_log(
            "handle stream state broadcast",
            session_id=session_id,
            source_client_id=source_client_id,
            change_type=change_type,
        )
        await self.chat_service.event_broker.publish(
            "codex_session_updated",
            {
                "session_id": session_id,
                "source_client_id": source_client_id,
                "change_type": str(change_type or ""),
            },
        )

    async def _handle_thread_queued_followups_changed_broadcast(
        self,
        message: dict[str, Any],
    ) -> None:
        p = params(message)
        session_id = conversation_id(p)
        if not session_id or not self._is_codex_session(session_id):
            return

        messages = p.get("messages")
        message_count = len(messages) if isinstance(messages, list) else 0
        ipc_debug_log(
            "handle queued followups broadcast",
            session_id=session_id,
            source_client_id=message.get("sourceClientId"),
            message_count=message_count,
        )
        await self.chat_service.event_broker.publish(
            "codex_session_updated",
            {
                "session_id": session_id,
                "source_client_id": message.get("sourceClientId"),
                "change_type": "queued_followups",
            },
        )

    async def _handle_thread_read_state_changed_broadcast(
        self,
        message: dict[str, Any],
    ) -> None:
        p = params(message)
        session_id = conversation_id(p)
        if not session_id or not self._is_codex_session(session_id):
            return

        has_unread_turn = p.get("hasUnreadTurn")
        ipc_debug_log(
            "handle read state broadcast",
            session_id=session_id,
            source_client_id=message.get("sourceClientId"),
            has_unread_turn=has_unread_turn,
        )
        if isinstance(has_unread_turn, bool):
            self.chat_service.update_session_backend_state(
                session_id,
                {"has_unread_turn": has_unread_turn},
            )
        await self.chat_service.event_broker.publish(
            "codex_session_updated",
            {
                "session_id": session_id,
                "source_client_id": message.get("sourceClientId"),
                "change_type": "read_state_changed",
            },
        )

    async def _handle_client_status_changed_broadcast(
        self,
        message: dict[str, Any],
    ) -> None:
        p = params(message)
        ipc_debug_log(
            "handle client status broadcast",
            client_id=p.get("clientId"),
            client_type=p.get("clientType"),
            status=p.get("status"),
            source_client_id=message.get("sourceClientId"),
        )
        await self.chat_service.event_broker.publish(
            "codex_ipc_client_status_changed",
            {
                "client_id": p.get("clientId"),
                "client_type": p.get("clientType"),
                "status": p.get("status"),
                "source_client_id": message.get("sourceClientId"),
            },
        )

    async def _handle_thread_archived_broadcast(
        self,
        message: dict[str, Any],
    ) -> None:
        p = params(message)
        session_id = conversation_id(p)
        ipc_debug_log(
            "handle thread archived broadcast",
            session_id=session_id,
            source_client_id=message.get("sourceClientId"),
        )
        await self.chat_service.event_broker.publish(
            "codex_thread_archived",
            {
                "session_id": session_id,
                "source_client_id": message.get("sourceClientId"),
            },
        )

    async def _handle_thread_unarchived_broadcast(
        self,
        message: dict[str, Any],
    ) -> None:
        p = params(message)
        session_id = conversation_id(p)
        ipc_debug_log(
            "handle thread unarchived broadcast",
            session_id=session_id,
            source_client_id=message.get("sourceClientId"),
        )
        await self.chat_service.event_broker.publish(
            "codex_thread_unarchived",
            {
                "session_id": session_id,
                "source_client_id": message.get("sourceClientId"),
            },
        )

    # ── inbound: request handlers ──

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

    async def _can_handle_thread_request(self, p: dict[str, Any]) -> bool:
        cid = conversation_id(p)
        return await self.chat_service.can_handle_codex_conversation(cid)

    async def _handle_start_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        p = params(request)
        session_id = await self.chat_service.ensure_codex_conversation_session(
            conversation_id(p)
        )
        tsp = turn_start_params(p)
        input_payload = start_turn_input_payload(tsp)
        if input_payload in (None, "", []):
            raise RuntimeError("missing-thread-follower-prompt")
        model_updates = model_and_reasoning_updates(tsp)
        if model_updates:
            self.chat_service.update_session_backend_state(session_id, model_updates)
        collab_mode = collaboration_mode_value(tsp)
        ipc_debug_log(
            "handle start turn",
            session_id=session_id,
            request_id=request.get("requestId"),
            input_payload=input_payload,
            model_updates=model_updates,
            collaboration_mode=collab_mode,
        )
        if collab_mode is not None:
            self.chat_service.sync_codex_collaboration_mode(
                session_id,
                collab_mode,
            )
        start_response = await self.chat_service.start_codex_turn_in_background(
            session_id,
            input_payload,
        )
        await self.broadcast_stream_state(
            session_id, trigger_event="thread-follower-start-turn"
        )
        return {"result": start_response}

    async def _handle_steer_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        p = params(request)
        session_id = await self.chat_service.ensure_codex_conversation_session(
            conversation_id(p)
        )
        tid = turn_id(p) or None
        input_payload = steer_input_payload(p)
        if input_payload in (None, "", []):
            raise RuntimeError("missing-steer-input")
        ipc_debug_log(
            "handle steer turn",
            session_id=session_id,
            request_id=request.get("requestId"),
            turn_id=tid,
            input_payload=input_payload,
        )
        result = await self.chat_service.steer_codex_turn(
            session_id=session_id,
            turn_id=tid,
            input_payload=input_payload,
        )
        await self.broadcast_stream_state(
            session_id, trigger_event="thread-follower-steer-turn"
        )
        return {"result": result}

    async def _handle_interrupt_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        p = params(request)
        session_id = await self.chat_service.ensure_codex_conversation_session(
            conversation_id(p)
        )
        tid = turn_id(p) or None
        ipc_debug_log(
            "handle interrupt turn",
            session_id=session_id,
            request_id=request.get("requestId"),
            turn_id=tid,
        )
        await self.chat_service.interrupt_codex_turn(
            session_id=session_id,
            turn_id=tid,
        )
        await self.broadcast_stream_state(
            session_id, trigger_event="thread-follower-interrupt-turn"
        )
        return {"ok": True}

    async def interrupt_owner_turn(
        self,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        if not self.client.is_connected:
            raise RuntimeError("IPC not connected.")
        metadata = self.chat_service.get_session_metadata(session_id)
        backend_state = metadata.get("backend_state", {})
        target_client_id = backend_state.get("ipc_source_client_id")
        if not isinstance(target_client_id, str) or not target_client_id:
            raise RuntimeError("No IPC owner client found.")
        ipc_debug_log(
            "interrupt owner turn",
            session_id=session_id,
            target_client_id=target_client_id,
        )
        response = await self.client.send_request(
            "thread-follower-interrupt-turn",
            {"conversationId": session_id},
            target_client_id=target_client_id,
        )
        return response

    async def _handle_set_model_and_reasoning(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        p = params(request)
        session_id = await self.chat_service.ensure_codex_conversation_session(
            conversation_id(p)
        )
        updates = model_and_reasoning_updates(p)
        ipc_debug_log(
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

    async def _handle_set_collaboration_mode(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        p = params(request)
        session_id = await self.chat_service.ensure_codex_conversation_session(
            conversation_id(p)
        )
        collab_mode = collaboration_mode_value(p)
        if collab_mode is None:
            raise RuntimeError("missing-collaboration-mode")
        ipc_debug_log(
            "handle set collaboration mode",
            session_id=session_id,
            request_id=request.get("requestId"),
            collaboration_mode=collab_mode,
        )
        self.chat_service.sync_codex_collaboration_mode(
            session_id,
            collab_mode,
        )
        await self.broadcast_stream_state(
            session_id,
            trigger_event="thread-follower-set-collaboration-mode",
        )
        return {"ok": True}

    async def _handle_edit_last_user_turn(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        p = params(request)
        session_id = await self.chat_service.ensure_codex_conversation_session(
            conversation_id(p)
        )
        content = message_text(p)
        ipc_debug_log(
            "handle edit last user turn",
            session_id=session_id,
            request_id=request.get("requestId"),
            content=content,
        )
        if content:
            self.chat_service.edit_last_codex_user_turn(session_id, content)
        return {"ok": True}

    async def _handle_command_approval_decision(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._handle_approval_decision(
            request,
            preferred_kind="command",
            response_payload_builder=command_or_file_approval_response_payload,
        )

    async def _handle_file_approval_decision(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._handle_approval_decision(
            request,
            preferred_kind="file_change",
            response_payload_builder=command_or_file_approval_response_payload,
        )

    async def _handle_submit_user_input(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._handle_approval_decision(
            request,
            preferred_kind=None,
            response_payload_builder=response_payload,
        )

    async def _handle_submit_mcp_server_elicitation_response(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._handle_approval_decision(
            request,
            preferred_kind="mcp_elicitation",
            response_payload_builder=response_payload,
        )

    async def _handle_set_queued_followups_state(
        self, request: dict[str, Any]
    ) -> dict[str, Any]:
        p = params(request)
        session_id = await self.chat_service.ensure_codex_conversation_session(
            conversation_id(p)
        )
        self.chat_service.update_session_backend_state(
            session_id,
            {"queued_followups_state": p},
        )
        ipc_debug_log(
            "handle queued followups state",
            session_id=session_id,
            request_id=request.get("requestId"),
            params=p,
        )
        await self.broadcast_queued_followups(session_id)
        return {"ok": True}

    async def respond_to_pending_request(
        self,
        *,
        session_id: str,
        request: dict[str, Any],
        response_payload: dict[str, Any],
        decision: str,
    ) -> bool:
        if not self.client.is_connected:
            return False

        metadata = self.chat_service.get_session_metadata(session_id)
        backend_state = metadata.get("backend_state", {})
        target_client_id = (
            backend_state.get("ipc_source_client_id")
            if isinstance(backend_state, dict)
            else None
        )
        if not isinstance(target_client_id, str) or not target_client_id.strip():
            return False

        method = request.get("method")
        request_params = request.get("params")
        if not isinstance(method, str) or not isinstance(request_params, dict):
            return False

        follower_method = {
            "item/commandExecution/requestApproval": "thread-follower-command-approval-decision",
            "item/fileChange/requestApproval": "thread-follower-file-approval-decision",
            "item/tool/requestUserInput": "thread-follower-submit-user-input",
            "mcpServer/elicitation/request": "thread-follower-submit-mcp-server-elicitation-response",
            "elicitation/create": "thread-follower-submit-mcp-server-elicitation-response",
        }.get(method)
        if follower_method is None:
            return False

        outbound_params: dict[str, Any] = {
            "conversationId": session_id,
            "threadId": request_params.get("threadId") or session_id,
            "turnId": request_params.get("turnId"),
            "itemId": request_params.get("itemId"),
            "requestId": request_params.get("_ipc_request_id", request.get("id")),
        }
        if follower_method in {
            "thread-follower-command-approval-decision",
            "thread-follower-file-approval-decision",
        }:
            outbound_params["decision"] = decision
        else:
            outbound_params["response"] = response_payload

        try:
            response = await self.client.send_request(
                follower_method,
                {
                    key: value
                    for key, value in outbound_params.items()
                    if value is not None
                },
                target_client_id=target_client_id,
            )
        except Exception:
            return False

        return response.get("resultType") == "success"

    async def _handle_approval_decision(
        self,
        request: dict[str, Any],
        *,
        preferred_kind: str | None,
        response_payload_builder: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        p = params(request)
        session_id = await self.chat_service.ensure_codex_conversation_session(
            conversation_id(p)
        )
        req_id = approval_request_id(p)
        if req_id is None:
            resolver = getattr(self.chat_service, "resolve_pending_request_id", None)
            if resolver is None:
                resolver = getattr(
                    self.chat_service,
                    "resolve_pending_approval_request_id",
                    None,
                )
            if resolver is not None:
                req_id = resolver(
                    session_id,
                    preferred_kind=preferred_kind,
                )
        if req_id is None:
            raise RuntimeError("missing-approval-request-id")
        resp_payload = response_payload_builder(p)
        if resp_payload is None:
            raise RuntimeError("missing-response-payload")
        ipc_debug_log(
            "handle approval decision",
            session_id=session_id,
            request_id=request.get("requestId"),
            approval_request_id=req_id,
            preferred_kind=preferred_kind,
            response_payload=resp_payload,
        )
        handled = await self.chat_service.respond_to_codex_raw_request(
            session_id=session_id,
            request_id=req_id,
            response_payload=resp_payload,
        )
        if not handled:
            raise RuntimeError("approval-request-not-found")
        await self.broadcast_stream_state(
            session_id, trigger_event="approval-response"
        )
        return {"ok": True}

    # ── helpers ──

    def _is_codex_session(self, session_id: str) -> bool:
        if not isinstance(session_id, str) or not session_id.strip():
            return False
        metadata = self.chat_service.get_session_metadata(session_id)
        return metadata.get("backend_id") == "codex"
