from __future__ import annotations

from copy import deepcopy
from typing import Any

from yier_web.codex.ipc.constants import ipc_method_version
from yier_web.codex.ipc.debug import (
    conversation_state_summary,
    ipc_debug_full_enabled,
    ipc_debug_log,
)
from yier_web.codex.ipc.immer import apply_patches
from yier_web.codex.ipc.patches import patches_for_state_update


JsonDict = dict[str, Any]


class OwnerFollowerSession:
    """Track IPC ownership and state for one Codex conversation.

    The bridge owns the shared IPC socket. This object owns the per-conversation
    role decision: owners publish local SDK state, followers only apply remote
    owner updates and forward requests back to that owner.
    """

    def __init__(self, *, conversation_id: str, host_id: str = "local") -> None:
        self.conversation_id = conversation_id
        self.host_id = host_id
        self._state: JsonDict | None = None
        self._stream_role: JsonDict | None = None
        self._streaming_conversations: set[str] = set()

    @property
    def state(self) -> JsonDict | None:
        return self._state

    @property
    def stream_role(self) -> JsonDict | None:
        return self._stream_role

    @property
    def owner_client_id(self) -> str | None:
        if self._stream_role is None or self._stream_role.get("role") != "follower":
            return None
        owner_client_id = self._stream_role.get("ownerClientId")
        return owner_client_id if isinstance(owner_client_id, str) else None

    @property
    def is_owner(self) -> bool:
        return self._stream_role is not None and self._stream_role.get("role") == "owner"

    @property
    def is_follower(self) -> bool:
        return (
            self._stream_role is not None
            and self._stream_role.get("role") == "follower"
        )

    def ensure_owner(self, state: JsonDict | None = None) -> None:
        if state is not None:
            self._state = deepcopy(state)
        self._streaming_conversations.add(self.conversation_id)
        self._set_stream_role({"role": "owner"})

    def clear_role(self) -> None:
        self._set_stream_role(None)

    def handle_owner_disconnected(self, client_id: str) -> bool:
        if self.owner_client_id != client_id:
            return False
        self.clear_role()
        ipc_debug_log(
            "ipc owner disconnected",
            session_id=self.conversation_id,
            owner_client_id=client_id,
        )
        return True

    def apply_remote_stream_change(
        self,
        *,
        source_client_id: str,
        change: JsonDict,
    ) -> JsonDict | None:
        change_type = change.get("type")
        current_role = self._stream_role.get("role") if self._stream_role else None

        if current_role == "owner" and change_type != "snapshot":
            return None

        if change_type == "snapshot":
            conversation_state = change.get("conversationState")
            if not isinstance(conversation_state, dict):
                return None
            if current_role == "owner":
                ipc_debug_log(
                    "received owner snapshot; yielding ownership",
                    session_id=self.conversation_id,
                    owner_client_id=source_client_id,
                )
            self._state = deepcopy(conversation_state)
            self._streaming_conversations.add(self.conversation_id)
            self._set_stream_role(
                {"role": "follower", "ownerClientId": source_client_id}
            )
            return deepcopy(self._state)

        if change_type != "patches":
            return None

        patches = change.get("patches")
        if not isinstance(patches, list) or self._state is None:
            return None
        try:
            self._state = apply_patches(self._state, patches)
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        self._streaming_conversations.add(self.conversation_id)
        self._set_stream_role({"role": "follower", "ownerClientId": source_client_id})
        return deepcopy(self._state)

    def snapshot_payload(
        self,
        state: JsonDict,
        *,
        trigger_event: str | None = None,
    ) -> JsonDict:
        self.ensure_owner(state)
        conversation_state = deepcopy(state)
        if trigger_event:
            conversation_state["_yier_trigger_event"] = trigger_event
        return {
            "conversationId": self.conversation_id,
            "hostId": self.host_id,
            "change": {
                "type": "snapshot",
                "conversationState": conversation_state,
            },
            "version": ipc_method_version("thread-stream-state-changed"),
        }

    def patch_payload(
        self,
        state: JsonDict,
        *,
        trigger_event: str | None = None,
    ) -> JsonDict | None:
        if not self.is_owner:
            return None
        previous_state = self._state or {}
        patches = patches_for_state_update(previous_state, state)
        self._state = deepcopy(state)
        if not patches:
            return None
        payload = {
            "conversationId": self.conversation_id,
            "hostId": self.host_id,
            "change": {
                "type": "patches",
                "patches": patches,
            },
            "version": ipc_method_version("thread-stream-state-changed"),
        }
        ipc_debug_log(
            "broadcast owner stream patches",
            session_id=self.conversation_id,
            trigger_event=trigger_event or "",
            patch_count=len(patches),
            payload=payload if ipc_debug_full_enabled() else None,
        )
        return payload

    def _set_stream_role(self, role: JsonDict | None) -> None:
        if role == self._stream_role:
            return
        self._stream_role = role
        ipc_debug_log(
            "ipc stream role changed",
            session_id=self.conversation_id,
            role=role,
            state=conversation_state_summary(self._state or {}),
        )
