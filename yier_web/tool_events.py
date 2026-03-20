from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Awaitable, Callable


ToolEventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


_current_tool_event_emitter: ContextVar[ToolEventEmitter | None] = ContextVar(
    "current_tool_event_emitter",
    default=None,
)


def set_tool_event_emitter(emitter: ToolEventEmitter | None):
    return _current_tool_event_emitter.set(emitter)


def reset_tool_event_emitter(token: object) -> None:
    _current_tool_event_emitter.reset(token)


async def emit_tool_event(event: str, data: dict[str, Any]) -> None:
    emitter = _current_tool_event_emitter.get()
    if emitter is None:
        return
    await emitter(event, data)
