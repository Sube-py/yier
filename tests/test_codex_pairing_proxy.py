from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import threading
import time
from uuid import uuid4

from yier_web.codex.pairing.proxy import (
    CodexPairingProxyServer,
    PairingDescriptorSocketOverride,
)


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
    def __init__(self, socket_path: Path, response: dict[str, object]) -> None:
        self.socket_path = socket_path
        self.response = response
        self.requests: list[dict[str, object]] = []
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


def _round_trip(socket_path: Path, payload: dict[str, object]) -> dict[str, object]:
    request_bytes = json.dumps(payload).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(str(socket_path))
        connection.sendall(len(request_bytes).to_bytes(4, byteorder="little"))
        connection.sendall(request_bytes)

        response_length = int.from_bytes(_read_exact(connection, 4), byteorder="little")
        response_bytes = _read_exact(connection, response_length)
    return json.loads(response_bytes.decode("utf-8"))


def test_codex_pairing_proxy_forwards_exchange_and_logs_it(tmp_path: Path) -> None:
    upstream_socket_path = _short_socket_path("yier-pairing-upstream")
    proxy_socket_path = _short_socket_path("yier-pairing-proxy")
    log_path = tmp_path / "pairing-proxy.jsonl"
    upstream_server = _PairingBridgeServer(
        upstream_socket_path,
        {
            "status": "success",
            "textfields": [{"id": "editor.py", "content": "print('hi')\n"}],
        },
    )
    proxy = CodexPairingProxyServer(
        upstream_socket_path=upstream_socket_path,
        proxy_socket_path=proxy_socket_path,
        log_path=log_path,
    )
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()

    try:
        for _ in range(50):
            if proxy_socket_path.exists():
                break
            time.sleep(0.02)

        response = _round_trip(
            proxy_socket_path,
            {
                "command": "content",
                "payload": {},
            },
        )
    finally:
        proxy.stop()
        proxy_thread.join(timeout=1)
        upstream_server.close()

    assert response == {
        "status": "success",
        "textfields": [{"id": "editor.py", "content": "print('hi')\n"}],
    }
    assert upstream_server.requests == [
        {
            "command": "content",
            "payload": {},
        }
    ]

    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    exchange_entries = [entry for entry in entries if entry["event"] == "exchange"]
    assert len(exchange_entries) == 1
    assert exchange_entries[0]["request"] == {"command": "content", "payload": {}}
    assert exchange_entries[0]["response"] == response


def test_pairing_descriptor_socket_override_rewrites_and_restores_socket_path(
    tmp_path: Path,
) -> None:
    descriptor_path = tmp_path / "Visual Studio Code.json"
    original_socket_path = "/tmp/original.sock"
    proxy_socket_path = tmp_path / "proxy.sock"
    descriptor_path.write_text(
        json.dumps(
            {
                "id": "editor-1",
                "appName": "Visual Studio Code",
                "socketPath": original_socket_path,
            }
        ),
        encoding="utf-8",
    )

    override = PairingDescriptorSocketOverride(descriptor_path, proxy_socket_path)
    override.apply()
    rewritten = json.loads(descriptor_path.read_text(encoding="utf-8"))
    assert rewritten["socketPath"] == str(proxy_socket_path)

    override.restore()
    restored = json.loads(descriptor_path.read_text(encoding="utf-8"))
    assert restored["socketPath"] == original_socket_path
