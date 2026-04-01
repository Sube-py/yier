from __future__ import annotations

import asyncio
import signal
from typing import Any

import pytest

from yier_web.app import install_shutdown_signal_bridge, wait_for_event_stream_item
from yier_web.event_stream import EventStreamItem


class FakeHandle:
    def __init__(self) -> None:
        self.run_count = 0

    def _run(self) -> None:
        self.run_count += 1


class FakeLoop:
    def __init__(self) -> None:
        self._signal_handlers: dict[int, FakeHandle] = {
            signal.SIGINT: FakeHandle(),
            signal.SIGTERM: FakeHandle(),
        }
        self.installed_handlers: dict[int, Any] = {}

    def add_signal_handler(self, signum: int, callback: Any) -> None:
        self.installed_handlers[signum] = callback

    def call_soon(self, callback: Any) -> None:
        callback()


def test_wait_for_event_stream_item_returns_queue_item() -> None:
    async def run() -> None:
        subscriber: asyncio.Queue[EventStreamItem] = asyncio.Queue()
        expected = EventStreamItem(event="connected", data={"status": "ok"})
        await subscriber.put(expected)

        item, shutting_down = await wait_for_event_stream_item(
            subscriber,
            shutdown_event=asyncio.Event(),
            timeout=0.1,
        )

        assert item == expected
        assert shutting_down is False

    asyncio.run(run())


def test_wait_for_event_stream_item_stops_when_shutdown_is_set() -> None:
    async def run() -> None:
        subscriber: asyncio.Queue[EventStreamItem] = asyncio.Queue()
        shutdown_event = asyncio.Event()
        shutdown_event.set()

        item, shutting_down = await wait_for_event_stream_item(
            subscriber,
            shutdown_event=shutdown_event,
            timeout=0.1,
        )

        assert item is None
        assert shutting_down is True

    asyncio.run(run())


def test_install_shutdown_signal_bridge_chains_existing_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    shutdown_event = asyncio.Event()
    loop = FakeLoop()

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    install_shutdown_signal_bridge(shutdown_event)

    assert signal.SIGINT in loop.installed_handlers
    assert signal.SIGTERM in loop.installed_handlers

    loop.installed_handlers[signal.SIGINT]()

    assert shutdown_event.is_set()
    assert loop._signal_handlers[signal.SIGINT].run_count == 1
