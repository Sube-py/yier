from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EventStreamItem:
    event: str
    data: dict[str, Any]


class EventStreamBroker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[EventStreamItem]] = set()

    def subscribe(self) -> asyncio.Queue[EventStreamItem]:
        queue: asyncio.Queue[EventStreamItem] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[EventStreamItem]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        item = EventStreamItem(event=event, data=data)
        for subscriber in list(self._subscribers):
            await subscriber.put(item)
