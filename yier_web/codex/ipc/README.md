# Codex IPC Module

This module owns the bridge between `yier_web` and Codex follower IPC messages.

## Responsibilities

- Maintain the follower IPC client connection
- Broadcast stream patches and snapshots to Codex-compatible consumers
- Build and apply conversation-state updates used by the Codex UI flow

## Files

- `bridge.py`: IPC client, broadcast handling, and follower bridge logic
- `state.py`: Conversation-state building, patch application, and queued follow-up shaping

## Integration Points

- Used by `ChatService` for stream-event fanout
- Reads Codex backend state from `yier_web.codex.backend`
- Persists normalized IPC conversation state back through `ChatService`
