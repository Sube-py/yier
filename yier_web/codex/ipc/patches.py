"""Immer-format patch generation for streaming updates.

Produces incremental JSON patches from conversation state snapshots,
so Codex VSCode can render real-time typing progress without full
state syncs.

Only depends on ``constants`` and ``debug`` — no ChatService dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yier_web.codex.ipc.constants import ipc_method_version
from yier_web.codex.ipc.debug import ipc_debug_full_enabled, ipc_debug_log


@dataclass(slots=True)
class StreamTracking:
    """Per-session state for generating incremental streaming patches.

    Tracks only what's needed to turn ``build_conversation_state()``
    output into correct Immer-format patches without a full state machine.
    """

    agent_item_index: int = -1
    final_assistant_started_sent: bool = False


def patches_for_stream_event(
    tracking: StreamTracking,
    conversation_state: dict[str, Any],
    event: str,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compute Immer-format patches for a streaming event.

    Mutates *tracking* in place (callers should hold a per-session instance).
    """
    if event == "token_usage_updated":
        value = conversation_state.get("latestTokenUsageInfo")
        if value is not None:
            return [{"op": "replace", "path": ["latestTokenUsageInfo"], "value": value}]
        return []

    turns = conversation_state.get("turns")
    if not isinstance(turns, list) or not turns:
        return []
    latest_turn_index = len(turns) - 1
    latest_turn = turns[-1]
    if not isinstance(latest_turn, dict):
        return []
    items = latest_turn.get("items") or []

    if event == "assistant_delta":
        return assistant_delta_patches(
            tracking, latest_turn_index, latest_turn, items, data
        )
    if event == "assistant_message":
        return assistant_message_patches(tracking, latest_turn_index, items)
    return []


def assistant_delta_patches(
    tracking: StreamTracking,
    turn_index: int,
    turn: dict[str, Any],
    items: list[dict[str, Any]],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Patches for a single assistant text delta."""
    patches: list[dict[str, Any]] = []

    # first delta for this turn -> add agent item placeholder
    if tracking.agent_item_index < 0:
        for i in range(len(items) - 1, -1, -1):
            if isinstance(items[i], dict) and items[i].get("type") == "agentMessage":
                tracking.agent_item_index = i
                break
        if tracking.agent_item_index < 0:
            tracking.agent_item_index = len(items)

        item_id = data.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            item_id = f"{data.get('session_id', '')}:turn:{turn_index}:assistant"
        patches.append(
            {
                "op": "add",
                "path": ["turns", turn_index, "items", tracking.agent_item_index],
                "value": {
                    "type": "agentMessage",
                    "id": item_id,
                    "text": "",
                    "phase": "final_answer",
                    "memoryCitation": None,
                },
            }
        )

    idx = tracking.agent_item_index
    agent_item = items[idx] if idx < len(items) else {}
    patches.append(
        {
            "op": "replace",
            "path": ["turns", turn_index, "items", idx, "text"],
            "value": agent_item.get("text", "") if isinstance(agent_item, dict) else "",
        }
    )

    # send finalAssistantStartedAtMs once
    final_started_at = turn.get("finalAssistantStartedAtMs")
    if final_started_at is not None and not tracking.final_assistant_started_sent:
        patches.append(
            {
                "op": "replace",
                "path": ["turns", turn_index, "finalAssistantStartedAtMs"],
                "value": final_started_at,
            }
        )
        tracking.final_assistant_started_sent = True

    return patches


def assistant_message_patches(
    tracking: StreamTracking,
    turn_index: int,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Patches for a completed assistant message."""
    if tracking.agent_item_index < 0:
        for i in range(len(items) - 1, -1, -1):
            if isinstance(items[i], dict) and items[i].get("type") == "agentMessage":
                tracking.agent_item_index = i
                break
        if tracking.agent_item_index < 0:
            tracking.agent_item_index = len(items)

    idx = tracking.agent_item_index
    op = "replace" if idx < len(items) else "add"
    agent_item = items[idx] if idx < len(items) else {}
    return [
        {
            "op": op,
            "path": ["turns", turn_index, "items", idx],
            "value": agent_item if isinstance(agent_item, dict) else {},
        }
    ]


def build_stream_patches_payload(
    session_id: str,
    patches: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the full IPC broadcast payload for streaming patches."""
    return {
        "conversationId": session_id,
        "change": {
            "type": "patches",
            "patches": patches,
        },
        "version": ipc_method_version("thread-stream-state-changed"),
        "type": "thread-stream-state-changed",
    }


def log_stream_patches(
    session_id: str,
    trigger_event: str,
    payload: dict[str, Any],
) -> None:
    ipc_debug_log(
        "broadcast stream patches",
        session_id=session_id,
        trigger_event=trigger_event,
        patch_count=len(payload.get("change", {}).get("patches", [])),
        payload=payload if ipc_debug_full_enabled() else None,
    )
