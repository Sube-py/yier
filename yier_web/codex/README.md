# Codex Backend Package

This package contains all Codex-specific backend integration for `yier_web`.

## Responsibilities

- Host the Codex backend implementation used by `ChatService`
- Keep Codex runtime state and session lifecycle logic together
- Group Codex-specific IPC, SDK, pairing, and background runner modules

## Main Files

- `backend.py`: Codex backend orchestration, turn lifecycle, approvals, and stream handling
- `runtime.py`: Shared runtime dataclasses for active Codex sessions
- `background.py`: Codex background follow-up tools and runner command helpers
- `background_runner.py`: Subprocess entrypoint for background Codex execution

## Subpackages

- `ipc/`: IPC transport and conversation-state synchronization
- `sdk/`: App Server and SDK-facing helpers
- `pairing/`: Paired-editor bridge, socket client, MCP server, and proxy

## Notes

- `yier_web/codex` is the canonical home for Codex-specific backend code.
- Generic backend abstractions still live in `yier_web/agent_backends`.
