from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket

from yier_web.event_stream import EventStreamBroker
from yier_web.codex.pairing.bridge import CodexPairedEditorBridge


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


def _round_trip(socket_path: Path, payload: dict[str, object]) -> dict[str, object]:
    request_bytes = json.dumps(payload).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(str(socket_path))
        connection.sendall(len(request_bytes).to_bytes(4, byteorder="little"))
        connection.sendall(request_bytes)

        response_length = int.from_bytes(_read_exact(connection, 4), byteorder="little")
        response_bytes = _read_exact(connection, response_length)
    return json.loads(response_bytes.decode("utf-8"))


async def _start_bridge(tmp_path: Path) -> tuple[CodexPairedEditorBridge, EventStreamBroker]:
    broker = EventStreamBroker()
    bridge = CodexPairedEditorBridge(
        home_dir=tmp_path / "home",
        event_broker=broker,
    )
    await bridge.start()
    return bridge, broker


def test_paired_editor_bridge_registers_descriptor_and_serves_content(tmp_path: Path) -> None:
    async def scenario() -> None:
        bridge, _ = await _start_bridge(tmp_path)
        pairing_id = bridge.descriptor_path.name
        socket_path = bridge.socket_path
        await bridge.update_state(
            session_id="session-1",
            workspace_name="demo-project",
            content="hello world",
            selection_start=1,
            selection_end=5,
        )

        descriptor = json.loads(bridge.descriptor_path.read_text(encoding="utf-8"))
        content_response = await asyncio.to_thread(
            _round_trip,
            socket_path,
            {
                "command": "content",
                "payload": {},
            },
        )
        selection_response = await asyncio.to_thread(
            _round_trip,
            socket_path,
            {
                "command": "selections",
                "payload": {},
            },
        )

        assert descriptor["appName"] == "Yier"
        assert descriptor["id"] == pairing_id
        assert descriptor["workspaceName"] == "demo-project"
        assert descriptor["socketPath"] == str(socket_path)
        assert pairing_id.startswith("Yier-")
        assert bridge.descriptor_path.name == pairing_id
        assert bridge.socket_path.name == f"{pairing_id}.sock"
        assert descriptor["capabilities"]["replaceSelection"] == 1
        assert content_response == {
            "status": "success",
            "textfields": [
                {
                    "id": "session:session-1:composer",
                    "content": "hello world",
                    "filename": "session:session-1:composer",
                    "selectedText": "ello",
                    "selectionRange": {"location": 1, "length": 4},
                    "selectionLine": 0,
                }
            ],
        }
        assert selection_response == {
            "status": "success",
            "selections": [
                {
                    "selectedText": "ello",
                    "selectionLine": 0,
                }
            ],
        }

        await bridge.stop()
        assert bridge.descriptor_path.exists() is False
        assert socket_path.exists() is False

    asyncio.run(scenario())


def test_paired_editor_bridge_reports_missing_active_editor(tmp_path: Path) -> None:
    async def scenario() -> None:
        bridge, _ = await _start_bridge(tmp_path)

        content_response = await asyncio.to_thread(
            _round_trip,
            bridge.socket_path,
            {
                "command": "content",
                "payload": {},
            },
        )
        selections_response = await asyncio.to_thread(
            _round_trip,
            bridge.socket_path,
            {
                "command": "selections",
                "payload": {},
            },
        )

        assert content_response == {
            "status": 400,
            "error": "No active editor found",
        }
        assert selections_response == {
            "status": 400,
            "error": "No active editor found",
        }

        await bridge.stop()

    asyncio.run(scenario())


def test_paired_editor_bridge_mutations_publish_remote_updates(tmp_path: Path) -> None:
    async def scenario() -> None:
        bridge, broker = await _start_bridge(tmp_path)
        subscriber = broker.subscribe()
        await bridge.update_state(
            session_id="session-2",
            workspace_name="demo-project",
            content="hello",
            selection_start=1,
            selection_end=4,
        )

        socket_path = bridge.socket_path
        replace_response = await asyncio.to_thread(
            _round_trip,
            socket_path,
            {
                "command": "replaceSelection",
                "payload": {
                    "textfieldID": "session:session-2:composer",
                    "content": "XX",
                },
            },
        )
        event = await asyncio.wait_for(subscriber.get(), timeout=1)
        state_after_replace = await bridge.snapshot()

        assert replace_response == {
            "status": "success",
            "message": "Replaced selection successfully",
        }
        assert event.event == "codex_paired_editor_update"
        assert event.data == {
            "session_id": "session-2",
            "textfield_id": "session:session-2:composer",
            "content": "hXXo",
            "selection_start": 1,
            "selection_end": 1,
        }
        assert state_after_replace.content == "hXXo"
        assert state_after_replace.selection_start == 1
        assert state_after_replace.selection_end == 1

        set_response = await asyncio.to_thread(
            _round_trip,
            socket_path,
            {
                "command": "setContent",
                "payload": {
                    "textfieldID": "session:session-2:composer",
                    "content": "updated",
                },
            },
        )
        event = await asyncio.wait_for(subscriber.get(), timeout=1)
        state_after_set = await bridge.snapshot()

        assert set_response == {
            "status": "success",
            "message": "Set content successfully",
        }
        assert event.data == {
            "session_id": "session-2",
            "textfield_id": "session:session-2:composer",
            "content": "updated",
            "selection_start": 7,
            "selection_end": 7,
        }
        assert state_after_set.content == "updated"
        assert state_after_set.selection_start == 7
        assert state_after_set.selection_end == 7

        await bridge.stop()
        broker.unsubscribe(subscriber)

    asyncio.run(scenario())


def test_paired_editor_bridge_keeps_descriptor_and_socket_names_stable_when_session_changes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        bridge, _ = await _start_bridge(tmp_path)
        await bridge.update_state(
            session_id="thread-a",
            workspace_name="demo-project",
            content="first",
            selection_start=0,
            selection_end=0,
        )

        first_descriptor_path = bridge.descriptor_path
        first_socket_path = bridge.socket_path
        assert first_descriptor_path.name.startswith("Yier-")
        assert first_socket_path.name == f"{first_descriptor_path.name}.sock"
        assert first_descriptor_path.exists() is True
        assert first_socket_path.exists() is True

        await bridge.update_state(
            session_id="thread-b",
            workspace_name="demo-project",
            content="second",
            selection_start=0,
            selection_end=0,
        )

        assert first_descriptor_path.exists() is True
        assert first_socket_path.exists() is True
        assert bridge.descriptor_path == first_descriptor_path
        assert bridge.socket_path == first_socket_path
        assert bridge.descriptor_path.exists() is True
        assert bridge.socket_path.exists() is True

        await bridge.stop()

    asyncio.run(scenario())
