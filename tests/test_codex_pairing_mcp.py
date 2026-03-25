from __future__ import annotations

from typing import Any

from yier_web.codex_pairing import CodexPairingClientError
from yier_web.codex_pairing_mcp import CodexPairingMCPServer, MCP_PROTOCOL_VERSION
from yier_web.schemas import CodexPairingExtensionSummary


class _FakePairingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_editors(self) -> list[CodexPairingExtensionSummary]:
        return [
            CodexPairingExtensionSummary(
                id="editor-1",
                app_name="Visual Studio Code",
                workspace_name="yier",
                extension_name="oai_pwai Visual Studio Code",
                extension_version="0.0.1",
                bundle_id="com.microsoft.VSCode",
                marketplace_id="openai.chatgpt",
                capability_names=["content", "replaceSelection"],
                capability_count=2,
                socket_path="/tmp/editor.sock",
                is_online=True,
                needs_reload=False,
                last_seen_at=1775000000000,
            )
        ]

    def ping(self, editor_id: str | None = None) -> dict[str, Any]:
        self.calls.append(("ping", {"editor_id": editor_id}))
        return {"ok": True, "editor_id": editor_id}

    def replace_selection(
        self,
        *,
        content: str,
        textfield_id: str,
        editor_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "content": content,
            "textfield_id": textfield_id,
            "editor_id": editor_id,
        }
        self.calls.append(("replace_selection", payload))
        return payload

    def content(self, editor_id: str | None = None) -> dict[str, Any]:
        raise CodexPairingClientError(f"paired editor '{editor_id}' is offline")


def test_codex_pairing_mcp_server_handles_initialize_and_lists_tools() -> None:
    server = CodexPairingMCPServer(_FakePairingClient())  # type: ignore[arg-type]

    initialize_response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        }
    )
    tools_response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        }
    )

    assert initialize_response == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {
                    "listChanged": False,
                }
            },
            "serverInfo": {
                "name": "yier-codex-pairing",
                "title": "Yier Codex Pairing",
                "version": server.version,
            },
        },
    }
    assert tools_response is not None
    tool_names = [tool["name"] for tool in tools_response["result"]["tools"]]
    assert "paired_editor_list" in tool_names
    assert "paired_editor_replace_selection" in tool_names


def test_codex_pairing_mcp_server_routes_tool_calls() -> None:
    client = _FakePairingClient()
    server = CodexPairingMCPServer(client)  # type: ignore[arg-type]

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "paired_editor_replace_selection",
                "arguments": {
                    "editor_id": "editor-1",
                    "textfield_id": "textfield-1",
                    "content": "updated",
                },
            },
        }
    )

    assert response is not None
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"] == {
        "content": "updated",
        "textfield_id": "textfield-1",
        "editor_id": "editor-1",
    }
    assert client.calls == [
        (
            "replace_selection",
            {
                "content": "updated",
                "textfield_id": "textfield-1",
                "editor_id": "editor-1",
            },
        )
    ]


def test_codex_pairing_mcp_server_surfaces_tool_errors_as_mcp_errors() -> None:
    client = _FakePairingClient()
    server = CodexPairingMCPServer(client)  # type: ignore[arg-type]

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "paired_editor_content",
                "arguments": {
                    "editor_id": "editor-1",
                },
            },
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert response["result"]["content"] == [
        {
            "type": "text",
            "text": "paired editor 'editor-1' is offline",
        }
    ]
