from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
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
from codex_app_server.models import Notification as VendorNotification

type ServerRequestId = str | int

type ApprovalHandler = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class ToolRequestUserInputOption(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str
    description: str


class ToolRequestUserInputQuestion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    header: str
    question: str
    is_other: bool = Field(default=False, alias="isOther")
    is_secret: bool = Field(default=False, alias="isSecret")
    options: list[ToolRequestUserInputOption] = Field(default_factory=list)


class ToolRequestUserInputParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    thread_id: str = Field(alias="threadId")
    turn_id: str = Field(alias="turnId")
    item_id: str = Field(alias="itemId")
    questions: list[ToolRequestUserInputQuestion]


@dataclass(slots=True)
class RequestAwareNotification:
    method: str
    payload: Any
    request_id: ServerRequestId | None = None


type SdkNotification = VendorNotification | RequestAwareNotification


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

    async def respond_to_server_request(
        self,
        request_id: ServerRequestId,
        result: dict[str, Any] | None = None,
    ) -> None:
        await self._client.respond_to_server_request(request_id, result)

    async def stream(self) -> AsyncIterator[SdkNotification]:
        self._client.acquire_turn_consumer(self.id)
        try:
            while True:
                event = await self._client.next_notification()
                yield event
                if self._is_turn_completed_event(event):
                    break
        finally:
            self._client.release_turn_consumer(self.id)

    def _is_turn_completed_event(self, event: SdkNotification) -> bool:
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
        collaboration_mode = kwargs.pop("collaboration_mode", None)
        params_model = TurnStartParams(thread_id=self.id, input=wire_input, **kwargs)
        if collaboration_mode is None:
            params: TurnStartParams | dict[str, Any] = params_model
        else:
            params = params_model.model_dump(
                by_alias=True,
                exclude_none=True,
                mode="json",
            )
            if hasattr(collaboration_mode, "model_dump"):
                params["collaborationMode"] = collaboration_mode.model_dump(
                    by_alias=True,
                    exclude_none=True,
                    mode="json",
                )
            else:
                params["collaborationMode"] = collaboration_mode
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
        manual_request_methods: frozenset[str] | None = None,
    ) -> None:
        super().__init__(config=config)
        self._approval_callback = approval_callback
        self._manual_request_methods = manual_request_methods or frozenset()
        # AsyncAppServerClient delegates wire reads to its internal sync client.
        # Install our handler there too so server requests that arrive during
        # regular SDK calls (for example turn_start) do not fall back to the
        # vendor client's default auto-approval behavior.
        self._sync._handle_server_request = self._handle_server_request

    def thread(self, thread_id: str) -> ApprovalAwareAsyncThread:
        return ApprovalAwareAsyncThread(self, thread_id)

    async def respond_to_server_request(
        self,
        request_id: ServerRequestId,
        result: dict[str, Any] | None = None,
    ) -> None:
        await self._call_sync(
            self._sync._write_message,
            {"id": request_id, "result": result or {}},
        )

    async def next_notification(self) -> SdkNotification:
        return await self._call_sync(self._handle_next_notification)

    def _handle_server_request(self, msg: dict[str, Any]) -> dict[str, Any]:
        method = msg.get("method")
        request_id = msg.get("id")
        if not isinstance(method, str) or not isinstance(request_id, (str, int)):
            return {}
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        return self._approval_callback(request_id, method, params)

    def _handle_next_notification(self) -> SdkNotification:
        while True:
            if self._sync._pending_notifications:
                return self._sync._pending_notifications.popleft()

            msg = self._sync._read_message()
            if "method" in msg and "id" in msg:
                method = msg.get("method")
                if (
                    isinstance(method, str)
                    and method in self._manual_request_methods
                ):
                    return self._manual_request_notification(msg)
                response = self._handle_server_request(msg)
                self._sync._write_message({"id": msg["id"], "result": response})
                continue
            if "method" in msg and "id" not in msg:
                return self._sync._coerce_notification(msg["method"], msg.get("params"))

    def _manual_request_notification(
        self,
        msg: dict[str, Any],
    ) -> RequestAwareNotification:
        method = msg.get("method")
        if not isinstance(method, str):
            raise RuntimeError("server request method must be a string")
        payload = msg.get("params")
        if not isinstance(payload, dict):
            payload = {}
        request_id = msg.get("id")
        if not isinstance(request_id, (str, int)):
            request_id = None
        return RequestAwareNotification(
            method=method,
            payload=self._coerce_manual_request_payload(method, payload),
            request_id=request_id,
        )

    def _coerce_manual_request_payload(
        self,
        method: str,
        payload: dict[str, Any],
    ) -> Any:
        if method == "item/tool/requestUserInput":
            try:
                return ToolRequestUserInputParams.model_validate(payload)
            except Exception:  # noqa: BLE001
                return payload
        return payload
