from __future__ import annotations

from typing import TYPE_CHECKING

from yier_web.agent_backends.base import (
    ChatBackend,
    ChatSessionContext,
    PendingRequestId,
    StreamEmitter,
)

if TYPE_CHECKING:
    from yier_web.chat import ChatService

PLAN_MODE_PROMPT_PREFIX = "<yier-plan-mode>"
PLAN_MODE_PROMPT = (
    f"{PLAN_MODE_PROMPT_PREFIX}\n"
    "You are in PLAN MODE. For this request you MUST NOT make any changes to files, "
    "run shell commands, or perform any side-effect actions. Instead:\n"
    "1. Analyze the user's request thoroughly.\n"
    "2. Read files and inspect the codebase as needed to understand the context.\n"
    "3. Produce a concrete, step-by-step implementation plan that another engineer "
    "could execute without ambiguity.\n"
    "4. Format the plan with clear numbered steps, specifying exact file paths, "
    "function names, and code changes where applicable.\n"
    "5. End the plan with a brief summary of the expected outcome.\n\n"
    "User request:"
)

PLAN_TOOLS_ALLOWLIST = frozenset({
    "list_files",
    "read_file",
    "search_files",
    "list_background_commands",
    "read_background_command",
})


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
        return await self.chat_service._stream_with_yier_backend(
            context.session_id, user_message, emit
        )

    def runtime_payload(self, context: ChatSessionContext) -> dict[str, object]:
        ready = self.chat_service.config_service.load_web_settings().llm.is_ready
        return {
            "backend_id": self.backend_id,
            "label": self.label,
            "ready": ready,
            "status": "idle",
            "thread_id": None,
            "active_flags": [],
            "detail": None
            if ready
            else "LLM setup is incomplete for the Yier backend.",
            "pending_request_count": 0,
            "pending_approval_count": 0,
        }

    def pending_requests(self, context: ChatSessionContext) -> list[dict[str, object]]:
        return []

    def pending_approvals(self, context: ChatSessionContext) -> list[dict[str, object]]:
        return self.pending_requests(context)

    async def respond_to_pending_request(
        self,
        context: ChatSessionContext,
        request_id: PendingRequestId,
        decision: str,
        content: dict[str, object] | None = None,
    ) -> bool:
        return False

    async def respond_to_approval(
        self,
        context: ChatSessionContext,
        request_id: PendingRequestId,
        decision: str,
        content: dict[str, object] | None = None,
    ) -> bool:
        return await self.respond_to_pending_request(
            context,
            request_id,
            decision,
            content,
        )
