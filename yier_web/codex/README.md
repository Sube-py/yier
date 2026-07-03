# Codex IPC Workspace

This package contains the Codex-specific backend integration for the standalone
`/codex` workspace.

## Responsibilities

- Build `codex_ipc.CodexIpcConfig` from stored yier Codex settings.
- Keep one long-lived `CodexIpcSession` per active thread.
- Fan out Codex session events to WebSocket subscribers, SSE listeners, and
  future channel sinks through a shared session event hub.
- Own the Codex-only backend surface for the web app.

## Main Files

- `ipc_manager.py`: session lifecycle, workspace listing, thread commands, and
- session event fanout state.
- `session_events.py`: thread subscriber and channel sink registry for Codex
  session event fanout.

## Notes

- HTTP and WebSocket routes live in `yier_web/routes/codex.py`.

## Iframe Embed

The Codex embed route is `/codex/embed?embed_token=...`. It reuses the Codex
WebSocket and requires `YIER_CODEX_EMBED_TOKEN` for unauthenticated access.

- New thread: parent sends `postMessage({ type: 'yier:codex-start', cwd, mode, prompt })`
- Resume thread: parent sends `postMessage({ type: 'yier:codex-resume', threadId, mode })`

`mode` is optional and accepts `build` or `plan`; omit it to use the thread's
current/default mode. `prompt` is optional and is only allowed with
`yier:codex-start`; it is sent after the new thread is created and `mode` is
applied. On success, the iframe sends `postMessage` events to the parent window:

- `yier:codex-ready`
- `yier:codex-thread-created`
- `yier:codex-thread-resumed`
- `yier:codex-prompt-sent`
- `yier:codex-error`
