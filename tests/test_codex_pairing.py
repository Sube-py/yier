from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import threading
from typing import Any
from uuid import uuid4

import pytest

from yier_web.codex.pairing.client import CodexPairingClientError, CodexPairingSocketClient


def _read_exact(connection: socket.socket, byte_count: int) -> bytes:
    remaining = byte_count
    chunks: list[bytes] = []
    while remaining > 0:
        chunk = connection.recv(remaining)
        if not chunk:
            raise RuntimeError("socket closed before expected bytes arrived")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class _PairingBridgeServer:
    def __init__(self, socket_path: Path, response: dict[str, Any]) -> None:
        self.socket_path = socket_path
        self.response = response
        self.requests: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.settimeout(0.1)
        self._server.bind(str(socket_path))
        self._server.listen()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        self._server.close()
        self._thread.join(timeout=1)
        if self.socket_path.exists():
            self.socket_path.unlink()

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            try:
                connection, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                break

            with connection:
                message_length = int.from_bytes(_read_exact(connection, 4), byteorder="little")
                request = json.loads(_read_exact(connection, message_length).decode("utf-8"))
                self.requests.append(request)

                response_bytes = json.dumps(self.response).encode("utf-8")
                connection.sendall(len(response_bytes).to_bytes(4, byteorder="little"))
                connection.sendall(response_bytes)


def _short_socket_path(prefix: str) -> Path:
    return Path(f"/tmp/{prefix}-{os.getpid()}-{uuid4().hex[:8]}.sock")


def _write_pairing_descriptor(home_dir: Path, *, socket_path: Path) -> None:
    pairing_dir = (
        home_dir
        / "Library"
        / "Application Support"
        / "com.openai.chat"
        / "app_pairing_extensions"
    )
    pairing_dir.mkdir(parents=True)
    (pairing_dir / "vscode.json").write_text(
        json.dumps(
            {
                "id": "editor-1",
                "appName": "Visual Studio Code",
                "workspaceName": "yier",
                "extensionName": "oai_pwai Visual Studio Code",
                "extensionVersion": "0.0.1",
                "bundleID": "com.microsoft.VSCode",
                "marketplaceID": "openai.chatgpt",
                "capabilities": {
                    "content": 1,
                    "replaceSelection": 1,
                },
                "socketPath": str(socket_path),
                "timestamp": 1775000000000,
            }
        ),
        encoding="utf-8",
    )


def test_codex_pairing_socket_client_reads_editor_content(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    socket_path = _short_socket_path("yier-pairing-content")
    server = _PairingBridgeServer(
        socket_path,
        {
            "status": "success",
            "textfields": [
                {
                    "id": "textfield-1",
                    "content": "print('hello')\n",
                }
            ],
        },
    )
    _write_pairing_descriptor(home_dir, socket_path=socket_path)

    try:
        client = CodexPairingSocketClient(home_dir=home_dir)
        result = client.content()
    finally:
        server.close()

    assert result["editor"]["id"] == "editor-1"
    assert result["textfields"] == [{"id": "textfield-1", "content": "print('hello')\n"}]
    assert server.requests == [{"command": "content", "payload": {}}]


def test_codex_pairing_socket_client_sends_replace_selection_payload(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    socket_path = _short_socket_path("yier-pairing-replace")
    server = _PairingBridgeServer(socket_path, {"status": "success"})
    _write_pairing_descriptor(home_dir, socket_path=socket_path)

    try:
        client = CodexPairingSocketClient(home_dir=home_dir)
        result = client.replace_selection(content="updated", textfield_id="textfield-1")
    finally:
        server.close()

    assert result["editor"]["id"] == "editor-1"
    assert server.requests == [
        {
            "command": "replaceSelection",
            "payload": {
                "content": "updated",
                "textfieldID": "textfield-1",
            },
        }
    ]


def test_codex_pairing_socket_client_raises_bridge_errors(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    socket_path = _short_socket_path("yier-pairing-error")
    server = _PairingBridgeServer(
        socket_path,
        {
            "status": 400,
            "error": "Editor rejected request.",
        },
    )
    _write_pairing_descriptor(home_dir, socket_path=socket_path)

    try:
        client = CodexPairingSocketClient(home_dir=home_dir)
        with pytest.raises(CodexPairingClientError, match="Editor rejected request."):
            client.content()
    finally:
        server.close()
