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
from yier_web.codex.ws_commands import (
    CodexWsCommandContext,
    CodexWsCommandStrategyFactory,
)
from yier_web.auth import AuthService
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


def _auth_service(state: State) -> AuthService:
    auth_service = getattr(state, "auth_service", None)
    if auth_service is None:
        raise RuntimeError("Auth service is not configured.")
    return cast(AuthService, auth_service)


class CodexController(Controller):
    path = "/codex"
    ws_command_factory = CodexWsCommandStrategyFactory()

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
            state=payload.get("state")
            if isinstance(payload.get("state"), dict)
            else None,
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
        if not _auth_service(socket.app.state).is_codex_websocket_authorized(socket):
            await socket.send_json(
                {
                    "type": "error",
                    "code": "unauthorized",
                    "message": "Authentication or a valid Codex embed token is required.",
                }
            )
            await socket.close(code=1008, reason="Codex WebSocket unauthorized.")
            return

        outbox: CodexSubscriberQueue = asyncio.Queue()
        subscribed_thread_ids: set[str] = set()

        await outbox.put(
            {
                "type": "connection_ready",
                "payload": {"ok": True},
            }
        )

        receive_task: asyncio.Task[Any] = asyncio.create_task(socket.receive_json())
        send_task: asyncio.Task[dict[str, Any]] = asyncio.create_task(outbox.get())

        try:
            while True:
                done, _pending = await asyncio.wait(
                    {receive_task, send_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if receive_task in done:
                    incoming = receive_task.result()
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
        except WebSocketDisconnect:
            pass
        finally:
            for task in (receive_task, send_task):
                if task.done():
                    continue
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
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
        strategy = self.ws_command_factory.get(message_type)
        return await strategy.execute(
            CodexWsCommandContext(
                manager=manager,
                payload=payload,
                outbox=outbox,
                subscribed_thread_ids=subscribed_thread_ids,
            )
        )
