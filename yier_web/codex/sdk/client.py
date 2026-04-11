from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from codex_app_server import AsyncAppServerClient, AppServerConfig
from codex_app_server._inputs import _to_wire_input
from codex_app_server.generated.v2_all import (
    ThreadReadResponse,
    TurnCompletedNotification,
    TurnInterruptResponse,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerResponse,
)
from codex_app_server.models import Notification

type ApprovalHandler = Callable[[str, str, dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ApprovalAwareAsyncTurnHandle:
    """Turn-scoped async handle backed directly by AsyncAppServerClient."""

    _client: AsyncAppServerClient
    thread_id: str
    id: str

    async def steer(self, input: Any) -> TurnSteerResponse:
        return await self._client.turn_steer(
            self.thread_id,
            self.id,
            _to_wire_input(input),
        )

    async def interrupt(self) -> TurnInterruptResponse:
        return await self._client.turn_interrupt(self.thread_id, self.id)

    async def stream(self) -> AsyncIterator[Notification]:
        self._client.acquire_turn_consumer(self.id)
        try:
            while True:
                event = await self._client.next_notification()
                yield event
                if self._is_turn_completed_event(event):
                    break
        finally:
            self._client.release_turn_consumer(self.id)

    def _is_turn_completed_event(self, event: Notification) -> bool:
        return (
            event.method == "turn/completed"
            and isinstance(event.payload, TurnCompletedNotification)
            and event.payload.turn.id == self.id
        )


@dataclass(slots=True)
class ApprovalAwareAsyncThread:
    """Small thread wrapper that mirrors the public SDK shape we actually use."""

    _client: AsyncAppServerClient
    id: str

    async def read(self, *, include_turns: bool = False) -> ThreadReadResponse:
        return await self._client.thread_read(self.id, include_turns=include_turns)

    async def turn(self, input: Any, **kwargs: Any) -> ApprovalAwareAsyncTurnHandle:
        wire_input = _to_wire_input(input)
        params = TurnStartParams(
            thread_id=self.id,
            input=wire_input,
            **kwargs,
        )
        response = await self._client.turn_start(self.id, wire_input, params=params)
        return self._turn_handle_from_response(response)

    def _turn_handle_from_response(
        self,
        response: TurnStartResponse,
    ) -> ApprovalAwareAsyncTurnHandle:
        return ApprovalAwareAsyncTurnHandle(
            self._client,
            self.id,
            response.turn.id,
        )


class ApprovalAwareAppServerClient(AsyncAppServerClient):
    """Async app-server client that intercepts approval requests during streaming."""

    def __init__(
        self,
        config: AppServerConfig,
        approval_callback: ApprovalHandler,
    ) -> None:
        super().__init__(config=config)
        self._approval_callback = approval_callback
        # AsyncAppServerClient delegates wire reads to its internal sync client.
        # Install our handler there too so server requests that arrive during
        # regular SDK calls (for example turn_start) do not fall back to the
        # vendor client's default auto-approval behavior.
        self._sync._handle_server_request = self._handle_server_request

    def thread(self, thread_id: str) -> ApprovalAwareAsyncThread:
        return ApprovalAwareAsyncThread(self, thread_id)

    async def next_notification(self) -> Notification:
        return await self._call_sync(self._handle_next_notification)

    def _handle_server_request(self, msg: dict[str, Any]) -> dict[str, Any]:
        method = msg.get("method")
        request_id = msg.get("id")
        if not isinstance(method, str) or not isinstance(request_id, str):
            return {}
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        return self._approval_callback(request_id, method, params)

    def _handle_next_notification(self) -> Notification:
        while True:
            if self._sync._pending_notifications:
                return self._sync._pending_notifications.popleft()

            msg = self._sync._read_message()
            if "method" in msg and "id" in msg:
                response = self._handle_server_request(msg)
                self._sync._write_message({"id": msg["id"], "result": response})
                continue
            if "method" in msg and "id" not in msg:
                return self._sync._coerce_notification(msg["method"], msg.get("params"))
