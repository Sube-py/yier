"""Immer-format patch generation for streaming updates.

Produces incremental JSON patches by diffing conversation state snapshots,
matching the behavior of immer's ``produceWithPatches`` used by Codex VSCode.

Instead of manually tracking indices (the old ``StreamTracking`` approach),
this module uses ``immer.produce_with_patches`` to compute patches from
actual state mutations — the same approach the real Codex extension uses.

Only depends on ``immer``, ``constants`` and ``debug``.
"""

from __future__ import annotations

from typing import Any

from yier_web.codex.ipc.constants import ipc_method_version
from yier_web.codex.ipc.debug import ipc_debug_full_enabled, ipc_debug_log
from yier_web.codex.ipc.immer import produce_with_patches


def patches_for_state_update(
    old_state: dict[str, Any],
    new_state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compute immer-format patches between two conversation states.

    Uses ``produce_with_patches`` internally: the new_state is treated as
    the result of a recipe applied to old_state, and the diff is computed.

    Returns a list of ``{op, path, value}`` patches (may be empty if
    the states are identical).
    """
    # produce_with_patches expects a recipe, but we already have the new state.
    # We use it purely for the diff: apply the mutations that transform old → new.
    _, patches = produce_with_patches(
        old_state,
        _make_recipe_from_new_state(new_state),
    )
    return patches


def patches_for_recipe(
    old_state: dict[str, Any],
    recipe: Any,  # Callable[[dict], None]
) -> list[dict[str, Any]]:
    """Execute a recipe on old_state and return the generated patches.

    Convenience wrapper around ``produce_with_patches``.
    """
    _, patches = produce_with_patches(old_state, recipe)
    return patches


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


def _make_recipe_from_new_state(new_state: dict[str, Any]):
    """Create a recipe that overwrites the draft with new_state."""
    def recipe(draft: dict[str, Any]) -> None:
        draft.clear()
        draft.update(new_state)
    return recipe
