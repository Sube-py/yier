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

import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable

from yier_web.codex.ipc.client import CodexIpcClient
from yier_web.codex.ipc.constants import ipc_method_version
from yier_web.codex.ipc.debug import (
    conversation_state_summary,
    ipc_debug_full_enabled,
    ipc_debug_log,
)
from yier_web.codex.ipc.owner_follower import OwnerFollowerSession
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
        self._sessions: dict[str, OwnerFollowerSession] = {}
        self._register_broadcast_handlers()
        self._register_request_handlers()

    async def start(self) -> None:
        await self.client.start()

    async def stop(self) -> None:
        await self.client.stop()

    # ── stream event → IPC broadcast ──

    def _session(self, session_id: str) -> OwnerFollowerSession:
        session = self._sessions.get(session_id)
        if session is None:
            session = OwnerFollowerSession(conversation_id=session_id)
            self._sessions[session_id] = session
        return session

    def owner_client_id(self, session_id: str) -> str | None:
        session = self._sessions.get(session_id)
        return session.owner_client_id if session is not None else None

    def is_follower(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        return bool(session is not None and session.is_follower)

    def mark_owner(self, session_id: str, state: dict[str, Any] | None = None) -> None:
        self._session(session_id).ensure_owner(state)

    async def notify_stream_event(self, event: str, data: dict[str, Any]) -> None:
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return
        if not self._is_codex_session(session_id):
            return
        if self.is_follower(session_id):
            return

        if event == "run_started":
            await self.broadcast_stream_state(session_id, trigger_event=event)
            return
        if event == "done":
            await self.broadcast_stream_state(session_id, trigger_event=event)
            return

        # --- lifecycle snapshots (full ConversationState) ---
        if event in {
            "turn_completed",
            "turn_aborted",
            "approval_requested",
            "approval_resolved",
            "stream_error",
        }:
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
        session = self._session(session_id)
        if session.is_follower:
            return
        payload = session.snapshot_payload(
            conversation_state,
            trigger_event=trigger_event,
        )
        ipc_debug_log(
            "broadcast stream state",
            session_id=session_id,
            trigger_event=trigger_event or "",
            conversation_state=conversation_state_summary(
                payload["change"]["conversationState"]
            ),
            payload=payload if ipc_debug_full_enabled() else None,
        )
        await self.client.send_broadcast("thread-stream-state-changed", payload)

    async def _conversation_state(self, session_id: str) -> dict[str, Any]:
        return await _maybe_await(
            self.chat_service.build_codex_ipc_conversation_state(session_id)
        )

    async def _emit_patches_if_changed(
        self,
        session_id: str,
        trigger_event: str,
    ) -> None:
        """Build current state, diff against cached state, send patches if changed."""
        if not self.client.is_connected or not self._is_codex_session(session_id):
            return
        session = self._session(session_id)
        if session.is_follower:
            return
        new_state = await self._conversation_state(session_id)
        if session.state is None:
            await self.broadcast_stream_state(
                session_id,
                trigger_event=trigger_event,
                conversation_state=new_state,
            )
            return
        payload = session.patch_payload(new_state, trigger_event=trigger_event)
        if payload is not None:
            await self.client.send_broadcast("thread-stream-state-changed", payload)

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
        await self.client.send_broadcast(
            "thread-queued-followups-changed",
            {
                "conversationId": session_id,
                "messages": messages,
                "version": ipc_method_version("thread-queued-followups-changed"),
                "type": "thread-queued-followups-changed",
            },
        )

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
        if source_client_id == self.client.client_id:
            return
        if isinstance(source_client_id, str) and source_client_id:
            self.chat_service.update_session_backend_state(
                session_id,
                {"ipc_source_client_id": source_client_id},
            )
        change = p.get("change")
        change_type = change.get("type") if isinstance(change, dict) else None
        applied_state = None
        if isinstance(source_client_id, str) and isinstance(change, dict):
            applied_state = self._session(session_id).apply_remote_stream_change(
                source_client_id=source_client_id,
                change=change,
            )
        if applied_state is not None and hasattr(
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
        client_id = p.get("clientId")
        status = p.get("status")
        if isinstance(client_id, str) and status == "disconnected":
            for session_id, session in self._sessions.items():
                if session.handle_owner_disconnected(client_id):
                    self.chat_service.update_session_backend_state(
                        session_id,
                        {"ipc_source_client_id": None},
                    )
                    await self.chat_service.event_broker.publish(
                        "codex_session_updated",
                        {
                            "session_id": session_id,
                            "source_client_id": client_id,
                            "change_type": "owner_disconnected",
                        },
                    )
        ipc_debug_log(
            "handle client status broadcast",
            client_id=client_id,
            client_type=p.get("clientType"),
            status=status,
            source_client_id=message.get("sourceClientId"),
        )
        await self.chat_service.event_broker.publish(
            "codex_ipc_client_status_changed",
            {
                "client_id": client_id,
                "client_type": p.get("clientType"),
                "status": status,
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
        if not await self.chat_service.can_handle_codex_conversation(cid):
            return False
        session = self._sessions.get(cid)
        if session is None:
            return True
        return not session.is_follower

    async def _handle_start_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        p = params(request)
        session_id = await self.chat_service.ensure_codex_conversation_session(
            conversation_id(p)
        )
        if not self._session(session_id).is_owner:
            self.mark_owner(
                session_id,
                await self._conversation_state(session_id),
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
        state = await self._conversation_state(session_id)
        self.mark_owner(session_id, state)
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
        target_client_id = self.owner_client_id(session_id)
        if not isinstance(target_client_id, str) or not target_client_id:
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

    async def forward_start_turn(
        self,
        *,
        session_id: str,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any] | None:
        target_client_id = self.owner_client_id(session_id)
        if not self.client.is_connected or not target_client_id:
            return None
        response = await self.client.send_request(
            "thread-follower-start-turn",
            {
                "conversationId": session_id,
                "turnStartParams": {"input": input_payload},
            },
            target_client_id=target_client_id,
        )
        if response.get("resultType") != "success":
            error = str(response.get("error", ""))
            if error in {"client-disconnected", "no-client-found"}:
                self._session(session_id).clear_role()
                self.chat_service.update_session_backend_state(
                    session_id,
                    {"ipc_source_client_id": None},
                )
                return None
            raise RuntimeError(f"thread-follower-start-turn failed: {error}")
        result = response.get("result")
        return result if isinstance(result, dict) else response

    async def forward_steer_turn(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any] | None:
        target_client_id = self.owner_client_id(session_id)
        if not self.client.is_connected or not target_client_id:
            return None
        params_payload: dict[str, Any] = {
            "conversationId": session_id,
            "input": input_payload,
        }
        if turn_id:
            params_payload["turnId"] = turn_id
        response = await self.client.send_request(
            "thread-follower-steer-turn",
            params_payload,
            target_client_id=target_client_id,
        )
        if response.get("resultType") != "success":
            raise RuntimeError(
                f"thread-follower-steer-turn failed: {response.get('error', '')}"
            )
        result = response.get("result")
        return result if isinstance(result, dict) else response

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
        target_client_id = self.owner_client_id(session_id)
        if not isinstance(target_client_id, str) or not target_client_id.strip():
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
