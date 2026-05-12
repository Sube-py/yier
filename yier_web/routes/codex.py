from __future__ import annotations

import asyncio
import contextlib
from typing import Any, cast

from litestar import Controller, get, post, put, websocket
from litestar.connection import WebSocket
from litestar.datastructures import State
from litestar.exceptions import HTTPException, WebSocketDisconnect
from litestar.status_codes import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

from yier_web.codex.ipc_manager import CodexIpcManager, CodexSubscriberQueue
from yier_web.schemas import (
    ArchiveCodexSessionResponse,
    CodexThreadCreateRequest,
    CodexThreadCreateResponse,
    CodexThreadNameRequest,
    CodexThreadStateResponse,
    CodexWorkspaceResponse,
)


def _codex_manager(state: State) -> CodexIpcManager:
    manager = getattr(state, "codex_ipc_manager", None)
    if manager is None:
        raise RuntimeError("Codex IPC manager is not configured.")
    return cast(CodexIpcManager, manager)


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


class CodexController(Controller):
    path = "/codex"

    @get("/workspace")
    async def get_workspace(self, state: State) -> CodexWorkspaceResponse:
        return await _codex_manager(state).workspace()

    @get("/threads/{thread_id:str}/state")
    async def get_thread_state(
        self,
        thread_id: str,
        state: State,
    ) -> CodexThreadStateResponse:
        manager = _codex_manager(state)
        thread_state = await manager.get_thread_state(thread_id)
        if thread_state is None:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail="Codex thread state not found.",
            )
        return CodexThreadStateResponse(thread_id=thread_id, state=thread_state)

    @post("/threads")
    async def create_thread(
        self,
        data: CodexThreadCreateRequest,
        state: State,
    ) -> CodexThreadCreateResponse:
        payload = await _codex_manager(state).start_thread(
            project_path=data.project_path,
        )
        return CodexThreadCreateResponse(
            thread_id=str(payload["thread_id"]),
            state=payload.get("state") if isinstance(payload.get("state"), dict) else None,
        )

    @post("/threads/{thread_id:str}/archive")
    async def archive_thread(
        self,
        thread_id: str,
        state: State,
    ) -> ArchiveCodexSessionResponse:
        await _codex_manager(state).archive_thread(thread_id)
        return ArchiveCodexSessionResponse(thread_id=thread_id)

    @post("/threads/{thread_id:str}/unarchive")
    async def unarchive_thread(
        self,
        thread_id: str,
        state: State,
    ) -> dict[str, bool]:
        await _codex_manager(state).unarchive_thread(thread_id)
        return {"ok": True}

    @put("/threads/{thread_id:str}/name")
    async def rename_thread(
        self,
        thread_id: str,
        data: CodexThreadNameRequest,
        state: State,
    ) -> dict[str, bool]:
        if not data.name:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="name is required.",
            )
        await _codex_manager(state).rename_thread(thread_id, data.name)
        return {"ok": True}

    @websocket("/ws")
    async def websocket_handler(self, socket: WebSocket) -> None:
        manager = _codex_manager(socket.app.state)
        await socket.accept()
        outbox: CodexSubscriberQueue = asyncio.Queue()
        subscribed_thread_ids: set[str] = set()

        await outbox.put(
            {
                "type": "connection_ready",
                "payload": {"ok": True},
            }
        )

        receive_task: asyncio.Task[Any] | None = asyncio.create_task(
            socket.receive_json()
        )
        send_task: asyncio.Task[dict[str, Any]] | None = asyncio.create_task(
            outbox.get()
        )

        try:
            while True:
                pending_tasks = [
                    task for task in (receive_task, send_task) if task is not None
                ]
                done, _pending = await asyncio.wait(
                    pending_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if receive_task in done:
                    try:
                        incoming = receive_task.result()
                    except WebSocketDisconnect:
                        break
                    receive_task = asyncio.create_task(socket.receive_json())
                    await self._handle_ws_message(
                        manager=manager,
                        message=incoming,
                        outbox=outbox,
                        subscribed_thread_ids=subscribed_thread_ids,
                    )

                if send_task in done:
                    outbound = send_task.result()
                    await socket.send_json(outbound)
                    send_task = asyncio.create_task(outbox.get())
        finally:
            if receive_task is not None:
                receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive_task
            if send_task is not None:
                send_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await send_task
            for thread_id in subscribed_thread_ids:
                manager.unsubscribe(thread_id, outbox)

    async def _handle_ws_message(
        self,
        *,
        manager: CodexIpcManager,
        message: Any,
        outbox: CodexSubscriberQueue,
        subscribed_thread_ids: set[str],
    ) -> None:
        request_id = None
        try:
            if not isinstance(message, dict):
                raise ValueError("WebSocket message must be an object.")
            request_id = message.get("id")
            if not isinstance(request_id, str) or not request_id:
                request_id = None
            message_type = message.get("type")
            if not isinstance(message_type, str) or not message_type:
                raise ValueError("WebSocket message type is required.")
            payload = message.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            result = await self._execute_ws_command(
                manager=manager,
                message_type=message_type,
                payload=payload,
                outbox=outbox,
                subscribed_thread_ids=subscribed_thread_ids,
            )
            await outbox.put(
                {
                    "id": request_id,
                    "type": "ack",
                    "ok": True,
                    "payload": result,
                }
            )
        except Exception as exc:  # noqa: BLE001
            await outbox.put(
                {
                    "id": request_id,
                    "type": "error",
                    "code": "bad_request",
                    "message": str(exc),
                }
            )

    async def _execute_ws_command(
        self,
        *,
        manager: CodexIpcManager,
        message_type: str,
        payload: dict[str, Any],
        outbox: CodexSubscriberQueue,
        subscribed_thread_ids: set[str],
    ) -> dict[str, Any]:
        if message_type == "list_threads":
            workspace = await manager.workspace()
            await outbox.put(
                {
                    "type": "workspace",
                    "payload": workspace.model_dump(mode="json"),
                }
            )
            return workspace.model_dump(mode="json")

        if message_type == "start_thread":
            result = await manager.start_thread(
                project_path=_payload_text(payload, "project_path") or None,
            )
            workspace = await manager.workspace()
            await outbox.put(
                {
                    "type": "workspace",
                    "payload": workspace.model_dump(mode="json"),
                }
            )
            return result

        thread_id = _payload_text(payload, "thread_id")
        if not thread_id:
            raise ValueError("thread_id is required.")

        if message_type == "subscribe_thread":
            state = await manager.subscribe(thread_id, outbox)
            subscribed_thread_ids.add(thread_id)
            return {"thread_id": thread_id, "state": state}

        if message_type == "unsubscribe_thread":
            manager.unsubscribe(thread_id, outbox)
            subscribed_thread_ids.discard(thread_id)
            return {"thread_id": thread_id}

        if message_type == "send_prompt":
            prompt = _payload_text(payload, "prompt")
            if not prompt:
                raise ValueError("prompt is required.")
            collaboration_mode = payload.get("collaboration_mode")
            await manager.send_prompt(
                thread_id,
                prompt,
                collaboration_mode=(
                    dict(collaboration_mode)
                    if isinstance(collaboration_mode, dict)
                    else None
                ),
            )
            return {"thread_id": thread_id}

        if message_type == "steer_prompt":
            prompt = _payload_text(payload, "prompt")
            if not prompt:
                raise ValueError("prompt is required.")
            await manager.steer_prompt(thread_id, prompt)
            return {"thread_id": thread_id}

        if message_type == "enqueue_followup":
            prompt = _payload_text(payload, "prompt")
            if not prompt:
                raise ValueError("prompt is required.")
            return await manager.enqueue_followup(thread_id, prompt)

        if message_type == "remove_followup":
            message_id = _payload_text(payload, "message_id")
            if not message_id:
                raise ValueError("message_id is required.")
            await manager.remove_followup(thread_id, message_id)
            return {"thread_id": thread_id, "message_id": message_id}

        if message_type == "interrupt_turn":
            turn_id = _payload_text(payload, "turn_id") or None
            interrupted = await manager.interrupt_turn(thread_id, turn_id)
            return {"thread_id": thread_id, "forwarded": interrupted}

        if message_type == "compact_thread":
            forwarded = await manager.compact_thread(thread_id)
            return {"thread_id": thread_id, "forwarded": forwarded}

        if message_type == "set_collaboration_mode":
            collaboration_mode = payload.get("collaboration_mode")
            await manager.set_collaboration_mode(
                thread_id,
                dict(collaboration_mode)
                if isinstance(collaboration_mode, dict)
                else None,
            )
            return {"thread_id": thread_id}

        if message_type == "submit_user_input_response":
            request_id = _payload_text(payload, "request_id")
            response = payload.get("response")
            if not request_id:
                raise ValueError("request_id is required.")
            submitted = await manager.submit_user_input_response(
                thread_id,
                request_id,
                dict(response) if isinstance(response, dict) else {"answers": {}},
            )
            return {"thread_id": thread_id, "submitted": submitted}

        if message_type == "rename_thread":
            name = _payload_text(payload, "name")
            if not name:
                raise ValueError("name is required.")
            await manager.rename_thread(thread_id, name)
            return {"thread_id": thread_id}

        if message_type == "archive_thread":
            await manager.archive_thread(thread_id)
            return {"thread_id": thread_id}

        if message_type == "unarchive_thread":
            await manager.unarchive_thread(thread_id)
            return {"thread_id": thread_id}

        raise ValueError(f"Unsupported Codex command: {message_type}")
