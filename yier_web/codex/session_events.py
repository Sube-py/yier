from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import inspect
import logging
from typing import Any

JsonDict = dict[str, Any]
CodexSessionEvent = JsonDict
CodexSessionEventQueue = asyncio.Queue[CodexSessionEvent]
CodexSessionEventSink = Callable[[CodexSessionEvent], Awaitable[None] | None]
Unsubscribe = Callable[[], None]

logger = logging.getLogger(__name__)


class CodexSessionEventHub:
    """Fan out Codex session events to thread subscribers and channel sinks."""

    def __init__(self) -> None:
        self._thread_subscribers: dict[str, set[CodexSessionEventQueue]] = {}
        self._sinks: set[CodexSessionEventSink] = set()

    def subscribe_thread(
        self,
        thread_id: str,
        queue: CodexSessionEventQueue,
    ) -> bool:
        subscribers = self._thread_subscribers.setdefault(thread_id, set())
        was_empty = not subscribers
        subscribers.add(queue)
        return was_empty

    def unsubscribe_thread(
        self,
        thread_id: str,
        queue: CodexSessionEventQueue,
    ) -> bool:
        subscribers = self._thread_subscribers.get(thread_id)
        if subscribers is None or queue not in subscribers:
            return False
        subscribers.discard(queue)
        if not subscribers:
            self._thread_subscribers.pop(thread_id, None)
            return True
        return False

    def clear_thread(self, thread_id: str) -> None:
        self._thread_subscribers.pop(thread_id, None)

    def clear_thread_subscribers(self) -> None:
        self._thread_subscribers.clear()

    def clear(self) -> None:
        self._thread_subscribers.clear()
        self._sinks.clear()

    def add_sink(self, sink: CodexSessionEventSink) -> Unsubscribe:
        self._sinks.add(sink)

        def unsubscribe() -> None:
            self._sinks.discard(sink)

        return unsubscribe

    async def publish_thread_event(
        self,
        thread_id: str,
        event: CodexSessionEvent,
    ) -> None:
        for queue in list(self._thread_subscribers.get(thread_id, set())):
            queue.put_nowait(event)
        for sink in list(self._sinks):
            await self._publish_to_sink(sink, event)

    async def publish_to_thread_subscribers(
        self,
        thread_id: str,
        event: CodexSessionEvent,
    ) -> None:
        for queue in list(self._thread_subscribers.get(thread_id, set())):
            queue.put_nowait(event)

    async def publish_to_all_thread_subscribers(
        self,
        event: CodexSessionEvent,
    ) -> None:
        for subscribers in list(self._thread_subscribers.values()):
            for queue in list(subscribers):
                queue.put_nowait(event)

    async def publish_global_event(self, event: CodexSessionEvent) -> None:
        for subscribers in list(self._thread_subscribers.values()):
            for queue in list(subscribers):
                queue.put_nowait(event)
        for sink in list(self._sinks):
            await self._publish_to_sink(sink, event)

    async def _publish_to_sink(
        self,
        sink: CodexSessionEventSink,
        event: CodexSessionEvent,
    ) -> None:
        try:
            result = sink(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning("Codex session event sink failed: %s", exc)
