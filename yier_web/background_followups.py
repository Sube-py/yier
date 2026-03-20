from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from yier_agents import BackgroundCommandManager, Tool, ToolContext, ToolOutput

from yier_web.tool_events import emit_tool_event


@dataclass(frozen=True, slots=True)
class QueuedFollowup:
    queue_id: str
    owner_session_id: str
    trigger_session_id: str
    prompt: str
    source: str


class QueueBackgroundFollowupParams(BaseModel):
    session_id: str = Field(description="Background command session id.")
    prompt: str = Field(description="Follow-up task to run after the command completes.")


class FollowupQueueManager:
    def __init__(self) -> None:
        self._queue: list[QueuedFollowup] = []
        self._counter = 0

    def add(
        self,
        owner_session_id: str,
        session_id: str,
        prompt: str,
        *,
        source: str,
    ) -> QueuedFollowup:
        self._counter += 1
        item = QueuedFollowup(
            queue_id=f"q-{self._counter}",
            owner_session_id=owner_session_id,
            trigger_session_id=session_id,
            prompt=prompt,
            source=source,
        )
        self._queue.append(item)
        return item

    def list_items(self) -> tuple[QueuedFollowup, ...]:
        return tuple(self._queue)

    def count(self) -> int:
        return len(self._queue)

    def pop_ready(self, completed_session_ids: set[str]) -> list[QueuedFollowup]:
        ready: list[QueuedFollowup] = []
        pending: list[QueuedFollowup] = []
        for item in self._queue:
            if item.trigger_session_id in completed_session_ids:
                ready.append(item)
                continue
            pending.append(item)
        self._queue = pending
        return ready


def create_queue_background_followup_tool(
    background_manager: BackgroundCommandManager,
    followup_queue: FollowupQueueManager,
) -> Tool[QueueBackgroundFollowupParams]:
    async def execute(
        params: QueueBackgroundFollowupParams,
        ctx: ToolContext,
    ) -> ToolOutput:
        session = background_manager.require_session(params.session_id)
        if not session.is_running():
            return ToolOutput(
                content=(
                    f"Background command {session.session_id} has already finished. "
                    "Read its output and continue directly instead of queueing a follow-up."
                ),
                metadata={
                    "session_id": session.session_id,
                    "queued": False,
                    "state": session.state,
                },
            )

        item = followup_queue.add(
            ctx.session_id,
            session.session_id,
            params.prompt,
            source="agent",
        )
        await emit_tool_event(
            "background_followup_queued",
            {
                "session_id": ctx.session_id,
                "tool_call_id": ctx.call_id,
                "background_session_id": session.session_id,
                "queue_id": item.queue_id,
                "prompt": item.prompt,
            },
        )
        return ToolOutput(
            content=(
                f"Queued follow-up {item.queue_id} for {session.session_id}\n"
                f"Prompt: {item.prompt}"
            ),
            metadata={
                "queue_id": item.queue_id,
                "session_id": session.session_id,
                "prompt": item.prompt,
            },
        )

    return Tool(
        name="queue_background_followup",
        description=(
            "Queue a follow-up task to run automatically after a background command finishes. "
            "Use this when you want to keep watching a build, test run, or sync job and continue "
            "once it completes."
        ),
        parameters=QueueBackgroundFollowupParams,
        execute=execute,
    )
