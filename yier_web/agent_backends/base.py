from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol


StreamEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class ChatSessionContext:
    session_id: str
    source: str
    backend_id: str
    project_path: Path
    channel_meta: dict[str, Any] | None
    backend_state: dict[str, Any] = field(default_factory=dict)


class ChatBackend(Protocol):
    backend_id: str
    label: str

    async def stop(self) -> None: ...

    async def close_session(self, session_id: str) -> None: ...

    async def stream_chat(
        self,
        context: ChatSessionContext,
        user_message: list[dict[str, Any]] | dict[str, Any] | str,
        emit: StreamEmitter,
    ) -> str: ...

    def runtime_payload(self, context: ChatSessionContext) -> dict[str, Any]: ...

    def pending_requests(
        self, context: ChatSessionContext
    ) -> list[dict[str, Any]]: ...

    def pending_approvals(
        self, context: ChatSessionContext
    ) -> list[dict[str, Any]]: ...

    async def respond_to_pending_request(
        self,
        context: ChatSessionContext,
        request_id: str,
        decision: str,
        content: dict[str, Any] | None = None,
    ) -> bool: ...

    async def respond_to_approval(
        self,
        context: ChatSessionContext,
        request_id: str,
        decision: str,
        content: dict[str, Any] | None = None,
    ) -> bool: ...
