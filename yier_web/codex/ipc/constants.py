"""IPC protocol constants and type aliases.

Matches the original Codex extension protocol:
  - INITIALIZING_CLIENT_ID = "initializing-client" (placeholder before initialize handshake)
  - REQUEST_TIMEOUT = 5s, RECONNECT_DELAY = 1s
  - MAX_FRAME_BYTES = 256 MB (single frame limit, from original xf constant)
  - IPC_METHOD_VERSIONS: per-method protocol version numbers (from original YF table)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

INITIALIZING_CLIENT_ID = "initializing-client"

IPC_REQUEST_TIMEOUT_SECONDS = 5.0
IPC_RECONNECT_DELAY_SECONDS = 1.0
IPC_MAX_FRAME_BYTES = 256 * 1024 * 1024  # 256 MB (matches original Codex protocol)

IPC_DEBUG_ENV = "YIER_CODEX_IPC_DEBUG"
IPC_DEBUG_FULL_ENV = "YIER_CODEX_IPC_DEBUG_FULL"
IPC_DEBUG_TEXT_LIMIT = 500

IPC_METHOD_VERSIONS: dict[str, int] = {
    # broadcast methods
    "thread-stream-state-changed": 6,
    "thread-read-state-changed": 1,
    "thread-archived": 2,
    "thread-unarchived": 1,
    "thread-queued-followups-changed": 1,
    "query-cache-invalidate": 1,
    "client-status-changed": 0,
    # request methods (thread-follower series)
    "thread-follower-start-turn": 1,
    "thread-follower-compact-thread": 1,
    "thread-follower-steer-turn": 1,
    "thread-follower-interrupt-turn": 1,
    "thread-follower-set-model-and-reasoning": 1,
    "thread-follower-set-collaboration-mode": 1,
    "thread-follower-edit-last-user-turn": 1,
    "thread-follower-command-approval-decision": 1,
    "thread-follower-file-approval-decision": 1,
    "thread-follower-permissions-request-approval-response": 1,
    "thread-follower-submit-user-input": 1,
    "thread-follower-submit-mcp-server-elicitation-response": 1,
    "thread-follower-set-queued-follow-ups-state": 1,
}

RequestCanHandle = Callable[[dict[str, Any]], Awaitable[bool]]
RequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
BroadcastHandler = Callable[[dict[str, Any]], Awaitable[None]]


def ipc_method_version(method: str) -> int:
    """Return the protocol version for a method, or 0 if unknown."""
    return int(IPC_METHOD_VERSIONS.get(method, 0))
