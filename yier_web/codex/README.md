# Codex IPC Workspace

This package contains the Codex-specific backend integration for the standalone
`/codex` workspace.

## Responsibilities

- Build `codex_ipc.CodexIpcConfig` from stored yier Codex settings.
- Keep one long-lived `CodexIpcSession` per active thread.
- Fan out raw `ConversationState` updates to WebSocket subscribers.
- Keep Codex separate from the Yier chat backend and agent tools.

## Main Files

- `ipc_manager.py`: session lifecycle, workspace listing, thread commands, and
  WebSocket fanout state.

## Notes

- HTTP and WebSocket routes live in `yier_web/routes/codex.py`.
- Generic backend abstractions still live in `yier_web/agent_backends`.
