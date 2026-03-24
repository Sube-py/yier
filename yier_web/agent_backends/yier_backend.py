from __future__ import annotations

from typing import TYPE_CHECKING

from yier_web.agent_backends.base import ChatBackend, ChatSessionContext, StreamEmitter

if TYPE_CHECKING:
    from yier_web.chat import ChatService


class YierAgentBackend(ChatBackend):
    backend_id = "yier"
    label = "Yier Agent"

    def __init__(self, chat_service: "ChatService") -> None:
        self.chat_service = chat_service

    async def stop(self) -> None:
        return None

    async def close_session(self, session_id: str) -> None:
        return None

    async def stream_chat(
        self,
        context: ChatSessionContext,
        user_message: str,
        emit: StreamEmitter,
    ) -> str:
        return await self.chat_service._stream_with_yier_backend(context.session_id, user_message, emit)

    def runtime_payload(self, context: ChatSessionContext) -> dict[str, object]:
        ready = self.chat_service.config_service.load_web_settings().llm.is_ready
        return {
            "backend_id": self.backend_id,
            "label": self.label,
            "ready": ready,
            "status": "idle",
            "thread_id": None,
            "active_flags": [],
            "detail": None if ready else "LLM setup is incomplete for the Yier backend.",
            "pending_approval_count": 0,
        }

    def pending_approvals(self, context: ChatSessionContext) -> list[dict[str, object]]:
        return []

    async def respond_to_approval(
        self,
        context: ChatSessionContext,
        request_id: str,
        decision: str,
        content: dict[str, object] | None = None,
    ) -> bool:
        return False
