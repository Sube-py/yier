from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import threading
from typing import Any

from codex_app_server import AsyncAppServerClient

from yier_web.agent_backends.base import StreamEmitter


@dataclass(slots=True)
class PendingApprovalState:
    request_id: str | int
    method: str
    payload: dict[str, Any]
    record: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None
    decision: str | None = None


@dataclass(slots=True)
class TurnSnapshotState:
    params: dict[str, Any]
    turn_started_at_ms: int | None = None
    final_assistant_started_at_ms: int | None = None
    assistant_item_id: str | None = None
    assistant_text: str = ""
    plan_text: str = ""


@dataclass(slots=True)
class CodexSessionRuntime:
    session_id: str
    client: AsyncAppServerClient | None = None
    thread_id: str | None = None
    status: str = "idle"
    active_flags: list[str] = field(default_factory=list)
    pending_requests: dict[str, PendingApprovalState] = field(default_factory=dict)
    turn_handles: dict[str, Any] = field(default_factory=dict)
    turn_snapshots: dict[str, TurnSnapshotState] = field(default_factory=dict)
    assistant_buffers: dict[str, str] = field(default_factory=dict)
    pending_emit_tasks: list[Any] = field(default_factory=list)
    realtime_transcript_buffers: dict[str, str] = field(default_factory=dict)
    reasoning_buffers: dict[str, dict[str, str]] = field(default_factory=dict)
    plan_buffers: dict[str, str] = field(default_factory=dict)
    detail: str | None = None
    loop: asyncio.AbstractEventLoop | None = None
    emit: StreamEmitter | None = None
    streaming_turn_id: str | None = None
    thread_state_cache: dict[str, Any] | None = None
