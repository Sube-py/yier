# Codex SDK Module

This module contains the App Server and SDK-facing helpers for Codex integration.

## Responsibilities

- Build normalized App Server config objects
- Isolate SDK client behavior from backend orchestration
- Provide workspace listing logic through SDK first, with disk fallback

## Files

- `config.py`: Launcher parsing, sandbox normalization, plan-mode prompt constants, and MCP config helpers
- `client.py`: App Server client wrapper with approval-aware request interception
- `workspace.py`: Codex workspace discovery, session grouping, paired-editor listing, and local fallback logic

## Notes

- Keep transport- and backend-specific orchestration out of this package.
- Prefer extending this package when adding new Codex SDK entrypoints.
