from __future__ import annotations

import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any

from yier_web.codex_pairing import CodexPairingClientError, CodexPairingSocketClient


JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "yier-codex-pairing"
SERVER_TITLE = "Yier Codex Pairing"


def _server_version() -> str:
    try:
        return importlib.metadata.version("yier")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


class CodexPairingMCPServer:
    def __init__(self, client: CodexPairingSocketClient) -> None:
        self.client = client
        self.version = _server_version()

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        message_id = message.get("id")
        method = message.get("method")

        if not isinstance(method, str) or not method.strip():
            return self._error_response(message_id, -32600, "Invalid JSON-RPC request.")

        if method == "initialize":
            params = message.get("params")
            protocol_version = MCP_PROTOCOL_VERSION
            if isinstance(params, dict):
                requested_version = params.get("protocolVersion")
                if isinstance(requested_version, str) and requested_version.strip():
                    protocol_version = requested_version
            return self._success_response(
                message_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {
                        "tools": {
                            "listChanged": False,
                        }
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "title": SERVER_TITLE,
                        "version": self.version,
                    },
                },
            )

        if method in {"initialized", "notifications/initialized"}:
            return None

        if method == "ping":
            return self._success_response(message_id, {})

        if method == "tools/list":
            return self._success_response(message_id, {"tools": self._tool_definitions()})

        if method == "tools/call":
            return self._handle_tools_call(message_id, message.get("params"))

        return self._error_response(message_id, -32601, f"Method '{method}' is not supported.")

    def _handle_tools_call(self, message_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error_response(message_id, -32602, "tools/call requires params.")

        tool_name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return self._error_response(message_id, -32602, "tools/call requires a tool name.")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return self._error_response(
                message_id,
                -32602,
                "tools/call arguments must be an object when provided.",
            )

        try:
            result = self._call_tool(tool_name, arguments)
        except CodexPairingClientError as exc:
            return self._success_response(
                message_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": str(exc),
                        }
                    ],
                    "isError": True,
                },
            )
        except (TypeError, ValueError) as exc:
            return self._success_response(
                message_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": str(exc),
                        }
                    ],
                    "isError": True,
                },
            )

        rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        return self._success_response(
            message_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": rendered,
                    }
                ],
                "structuredContent": result,
                "isError": False,
            },
        )

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        editor_id = self._optional_string(arguments, "editor_id")

        if tool_name == "paired_editor_list":
            return {
                "editors": [
                    editor.model_dump(mode="json")
                    for editor in self.client.list_editors()
                ]
            }

        if tool_name == "paired_editor_ping":
            return self.client.ping(editor_id)

        if tool_name == "paired_editor_content":
            return self.client.content(editor_id)

        if tool_name == "paired_editor_selections":
            return self.client.selections(editor_id)

        if tool_name == "paired_editor_reload_window":
            return self.client.reload_window(editor_id)

        if tool_name == "paired_editor_mark_for_reload":
            return self.client.mark_for_reload(editor_id)

        if tool_name == "paired_editor_remove_highlights":
            return self.client.remove_highlights(
                textfield_id=self._required_string(arguments, "textfield_id"),
                animated=self._optional_bool(arguments, "animated", False),
                editor_id=editor_id,
            )

        if tool_name == "paired_editor_highlight_lines":
            return self.client.highlight_lines(
                lines=self._required_int_list(arguments, "lines"),
                textfield_id=self._required_string(arguments, "textfield_id"),
                editor_id=editor_id,
            )

        if tool_name == "paired_editor_highlight":
            return self.client.highlight(
                start_char=self._required_int(arguments, "start_char"),
                end_char=self._required_int(arguments, "end_char"),
                textfield_id=self._required_string(arguments, "textfield_id"),
                editor_id=editor_id,
            )

        if tool_name == "paired_editor_set_content":
            return self.client.set_content(
                content=self._required_string(arguments, "content"),
                textfield_id=self._required_string(arguments, "textfield_id"),
                editor_id=editor_id,
            )

        if tool_name == "paired_editor_replace_selection":
            return self.client.replace_selection(
                content=self._required_string(arguments, "content"),
                textfield_id=self._required_string(arguments, "textfield_id"),
                editor_id=editor_id,
            )

        raise ValueError(f"Unknown tool '{tool_name}'.")

    def _tool_definitions(self) -> list[dict[str, Any]]:
        no_args = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        editor_ref = {
            "editor_id": {
                "type": "string",
                "description": "Optional paired editor id. Defaults to the most recently seen online editor.",
            }
        }
        return [
            {
                "name": "paired_editor_list",
                "title": "Paired Editor List",
                "description": "List the online paired editors discovered from app_pairing_extensions.",
                "inputSchema": no_args,
            },
            {
                "name": "paired_editor_ping",
                "title": "Paired Editor Ping",
                "description": "Ping a paired editor bridge to verify the socket is responsive.",
                "inputSchema": self._object_schema(editor_ref),
            },
            {
                "name": "paired_editor_content",
                "title": "Paired Editor Content",
                "description": "Read visible editor textfields, full content, and selection metadata from the paired editor.",
                "inputSchema": self._object_schema(editor_ref),
            },
            {
                "name": "paired_editor_selections",
                "title": "Paired Editor Selections",
                "description": "Read the current selections from the paired editor.",
                "inputSchema": self._object_schema(editor_ref),
            },
            {
                "name": "paired_editor_reload_window",
                "title": "Paired Editor Reload Window",
                "description": "Ask the paired editor to reload its window immediately.",
                "inputSchema": self._object_schema(editor_ref),
            },
            {
                "name": "paired_editor_mark_for_reload",
                "title": "Paired Editor Mark For Reload",
                "description": "Mark the paired editor registration as needing reload.",
                "inputSchema": self._object_schema(editor_ref),
            },
            {
                "name": "paired_editor_remove_highlights",
                "title": "Paired Editor Remove Highlights",
                "description": "Remove line highlights previously added in the paired editor.",
                "inputSchema": self._object_schema(
                    {
                        **editor_ref,
                        "textfield_id": {
                            "type": "string",
                            "description": "The textfield id returned by paired_editor_content.",
                        },
                        "animated": {
                            "type": "boolean",
                            "description": "Fade out the highlights instead of removing them instantly.",
                        },
                    },
                    required=["textfield_id"],
                ),
            },
            {
                "name": "paired_editor_highlight_lines",
                "title": "Paired Editor Highlight Lines",
                "description": "Highlight one or more zero-based line numbers in the paired editor.",
                "inputSchema": self._object_schema(
                    {
                        **editor_ref,
                        "textfield_id": {
                            "type": "string",
                            "description": "The textfield id returned by paired_editor_content.",
                        },
                        "lines": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Zero-based line numbers to highlight.",
                        },
                    },
                    required=["textfield_id", "lines"],
                ),
            },
            {
                "name": "paired_editor_highlight",
                "title": "Paired Editor Highlight Range",
                "description": "Highlight a character range inside the paired editor textfield.",
                "inputSchema": self._object_schema(
                    {
                        **editor_ref,
                        "textfield_id": {
                            "type": "string",
                            "description": "The textfield id returned by paired_editor_content.",
                        },
                        "start_char": {
                            "type": "integer",
                            "description": "Zero-based start character offset.",
                        },
                        "end_char": {
                            "type": "integer",
                            "description": "Zero-based end character offset.",
                        },
                    },
                    required=["textfield_id", "start_char", "end_char"],
                ),
            },
            {
                "name": "paired_editor_set_content",
                "title": "Paired Editor Set Content",
                "description": "Replace the entire content of a paired editor textfield.",
                "inputSchema": self._object_schema(
                    {
                        **editor_ref,
                        "textfield_id": {
                            "type": "string",
                            "description": "The textfield id returned by paired_editor_content.",
                        },
                        "content": {
                            "type": "string",
                            "description": "The new full textfield content.",
                        },
                    },
                    required=["textfield_id", "content"],
                ),
            },
            {
                "name": "paired_editor_replace_selection",
                "title": "Paired Editor Replace Selection",
                "description": "Replace the current selection inside a paired editor textfield.",
                "inputSchema": self._object_schema(
                    {
                        **editor_ref,
                        "textfield_id": {
                            "type": "string",
                            "description": "The textfield id returned by paired_editor_content.",
                        },
                        "content": {
                            "type": "string",
                            "description": "The replacement text for the current selection.",
                        },
                    },
                    required=["textfield_id", "content"],
                ),
            },
        ]

    def _object_schema(
        self,
        properties: dict[str, Any],
        *,
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        }

    def _required_string(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{key}' must be a non-empty string.")
        return value

    def _optional_string(self, payload: dict[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{key}' must be a non-empty string when provided.")
        return value

    def _required_int(self, payload: dict[str, Any], key: str) -> int:
        value = payload.get(key)
        if not isinstance(value, int):
            raise ValueError(f"'{key}' must be an integer.")
        return value

    def _required_int_list(self, payload: dict[str, Any], key: str) -> list[int]:
        value = payload.get(key)
        if not isinstance(value, list) or not value:
            raise ValueError(f"'{key}' must be a non-empty array of integers.")
        if not all(isinstance(item, int) for item in value):
            raise ValueError(f"'{key}' must contain only integers.")
        return value

    def _optional_bool(self, payload: dict[str, Any], key: str, default: bool) -> bool:
        value = payload.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f"'{key}' must be a boolean.")
        return value

    def _success_response(self, message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": message_id,
            "result": result,
        }

    def _error_response(self, message_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": message_id,
            "error": {
                "code": code,
                "message": message,
            },
        }


def build_server() -> CodexPairingMCPServer:
    home_dir = Path(os.environ.get("YIER_PAIRING_HOME_DIR", str(Path.home()))).expanduser()
    client = CodexPairingSocketClient(home_dir=home_dir)
    return CodexPairingMCPServer(client)


def main() -> int:
    server = build_server()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": JSONRPC_VERSION,
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Invalid JSON.",
                },
            }
        else:
            if not isinstance(message, dict):
                response = {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": None,
                    "error": {
                        "code": -32600,
                        "message": "Invalid JSON-RPC request.",
                    },
                }
            else:
                response = server.handle_message(message)

        if response is None:
            continue
        sys.stdout.write(f"{json.dumps(response, ensure_ascii=False)}\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
