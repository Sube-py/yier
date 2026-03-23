from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from yier_channel.core.models import ChannelAccountSummary, ChannelMessage, ChannelPlatformSummary


MessageSink = Callable[[ChannelMessage], Awaitable[None]]
EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class PlatformAdapter(ABC):
    def __init__(self) -> None:
        self._message_sink: MessageSink | None = None
        self._event_sink: EventSink | None = None

    @property
    @abstractmethod
    def platform(self) -> str:
        raise NotImplementedError

    def configure_sinks(self, message_sink: MessageSink | None, event_sink: EventSink | None) -> None:
        self._message_sink = message_sink
        self._event_sink = event_sink

    async def emit_message(self, message: ChannelMessage) -> None:
        if self._message_sink is not None:
            await self._message_sink(message)

    async def emit_event(self, event: str, data: dict[str, Any]) -> None:
        if self._event_sink is not None:
            await self._event_sink(event, data)

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_accounts(self) -> list[ChannelAccountSummary]:
        raise NotImplementedError

    @abstractmethod
    async def get_platform_summary(self) -> ChannelPlatformSummary:
        raise NotImplementedError

    @abstractmethod
    async def login(self, account_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def start_account(self, account_id: str) -> ChannelAccountSummary:
        raise NotImplementedError

    @abstractmethod
    async def stop_account(self, account_id: str) -> ChannelAccountSummary:
        raise NotImplementedError

    @abstractmethod
    async def send_text(self, account_id: str, peer_id: str, text: str) -> dict[str, Any]:
        raise NotImplementedError
