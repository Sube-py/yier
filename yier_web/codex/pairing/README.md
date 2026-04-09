# Codex Pairing Module

This module groups the paired-editor integration used by Codex-related flows.

## Responsibilities

- Expose the local paired-editor bridge served by `yier_web`
- Talk to paired-editor UNIX sockets
- Provide an MCP server wrapper for paired-editor tools
- Offer a proxy for debugging and logging paired-editor socket traffic

## Files

- `bridge.py`: Local paired-editor bridge published by `yier_web`
- `client.py`: Socket client for online paired editors
- `mcp.py`: MCP server exposing paired-editor tools to Codex
- `proxy.py`: Socket proxy used for inspection and logging

## Notes

- Descriptor files and socket metadata are aligned with the OpenAI desktop pairing format.
- This module is Codex-specific and should not become a generic editor integration bucket.
