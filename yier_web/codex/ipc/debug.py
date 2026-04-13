"""IPC debug logging utilities.

Controlled by environment variables:
  YIER_CODEX_IPC_DEBUG       — enable debug logging (off by default)
  YIER_CODEX_IPC_DEBUG_FULL   — log full payloads (off by default)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from yier_web.codex.ipc.constants import IPC_DEBUG_ENV, IPC_DEBUG_FULL_ENV, IPC_DEBUG_TEXT_LIMIT

logger = logging.getLogger(__name__)


def _env_flag(name: str) -> bool:
    value = os.getenv(name, "")
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def ipc_debug_enabled() -> bool:
    return _env_flag(IPC_DEBUG_ENV)


def ipc_debug_full_enabled() -> bool:
    return _env_flag(IPC_DEBUG_FULL_ENV)


def _truncate_debug_text(value: str, limit: int = IPC_DEBUG_TEXT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...<truncated {len(value) - limit} chars>"


def _debug_json(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, default=str, sort_keys=True, indent=2
        )
    except Exception:
        return repr(value)


def ipc_debug_log(message: str, **fields: Any) -> None:
    """Log a debug message if YIER_CODEX_IPC_DEBUG is set."""
    if not ipc_debug_enabled():
        return
    filtered_fields = {key: value for key, value in fields.items() if value is not None}
    if filtered_fields:
        rendered = ""
        for key, value in filtered_fields.items():
            rendered += f"{key}={_truncate_debug_text(_debug_json(value))}\n"
        logger.warning(f"[codex-ipc] {message} | {rendered}\n\n\n\n")
        return
    logger.warning(f"[codex-ipc] {message}")


def ipc_message_summary(message: dict[str, Any]) -> dict[str, Any]:
    """Produce a compact summary of an IPC message for logging."""
    summary: dict[str, Any] = {
        "type": message.get("type"),
        "method": message.get("method"),
        "requestId": message.get("requestId"),
        "sourceClientId": message.get("sourceClientId"),
        "targetClientId": message.get("targetClientId"),
        "resultType": message.get("resultType"),
        "version": message.get("version"),
    }
    params = message.get("params")
    if isinstance(params, dict):
        summary["paramKeys"] = sorted(params.keys())
        conversation_id = (
            params.get("conversationId")
            or params.get("conversation_id")
            or params.get("threadId")
            or params.get("thread_id")
        )
        if conversation_id is not None:
            summary["conversationId"] = conversation_id
    request = message.get("request")
    if isinstance(request, dict):
        summary["requestMethod"] = request.get("method")
        summary["requestVersion"] = request.get("version")
    error = message.get("error")
    if error is not None:
        summary["error"] = error
    return summary


def conversation_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Produce a compact summary of a ConversationState for logging."""
    requests = state.get("requests")
    turns = state.get("turns")
    return {
        "id": state.get("id"),
        "threadId": state.get("threadId"),
        "title": state.get("title"),
        "turnCount": len(turns) if isinstance(turns, list) else 0,
        "requestCount": len(requests) if isinstance(requests, list) else 0,
        "requestMethods": [
            request.get("method")
            for request in requests
            if isinstance(request, dict) and isinstance(request.get("method"), str)
        ]
        if isinstance(requests, list)
        else [],
        "latestCollaborationMode": state.get("latestCollaborationMode"),
        "threadRuntimeStatus": state.get("threadRuntimeStatus"),
        "triggerEvent": state.get("_yier_trigger_event"),
    }
