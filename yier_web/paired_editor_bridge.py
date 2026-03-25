from __future__ import annotations

import asyncio
from dataclasses import dataclass
import importlib.metadata
import json
from pathlib import Path
from time import time
from typing import Any
from uuid import uuid4

from yier_web.event_stream import EventStreamBroker


DEFAULT_APP_NAME = "Yier"
DEFAULT_BUNDLE_ID = "com.microsoft.VSCode"
DEFAULT_MARKETPLACE_ID = "openai.chatgpt"
DEFAULT_EXTENSION_NAME = f"oai_pwai {DEFAULT_APP_NAME}"
DEFAULT_WORKSPACE_NAME = DEFAULT_APP_NAME
DEFAULT_SOCKET_DIR = Path("/tmp")
DEFAULT_LOG_PATH = Path("/tmp/yier-paired-editor.jsonl")
PAIRING_CAPABILITIES = {
    "content": 1,
    "ping": 1,
    "selections": 1,
    "reload": 1,
    "markForReload": 1,
    "removeHighlights": 1,
    "highlightLines": 1,
    "highlight": 1,
    "setContent": 1,
    "replaceSelection": 1,
}


def _extension_version() -> str:
    try:
        return importlib.metadata.version("yier")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _clamp_offset(value: int, content: str) -> int:
    return max(0, min(value, len(content)))


def _path_safe_component(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return DEFAULT_APP_NAME
    return normalized.replace("/", "_").replace("\0", "_")


@dataclass(slots=True)
class PairedEditorState:
    session_id: str = ""
    workspace_name: str = DEFAULT_WORKSPACE_NAME
    textfield_id: str = ""
    content: str = ""
    selection_start: int = 0
    selection_end: int = 0


class PairedEditorBridgeError(RuntimeError):
    """Raised when the paired-editor bridge cannot satisfy a request."""


class CodexPairedEditorBridge:
    def __init__(
        self,
        *,
        home_dir: Path,
        event_broker: EventStreamBroker,
        app_name: str = DEFAULT_APP_NAME,
        bundle_id: str = DEFAULT_BUNDLE_ID,
        marketplace_id: str = DEFAULT_MARKETPLACE_ID,
        extension_name: str = DEFAULT_EXTENSION_NAME,
        socket_dir: Path = DEFAULT_SOCKET_DIR,
        log_path: Path = DEFAULT_LOG_PATH,
    ) -> None:
        self.home_dir = home_dir.resolve()
        self.event_broker = event_broker
        self.app_name = app_name
        self.bundle_id = bundle_id
        self.marketplace_id = marketplace_id
        self.extension_name = extension_name
        self.extension_version = _extension_version()
        self.socket_dir = socket_dir
        self.log_path = log_path
        self.app_pairing_extensions_dir = (
            self.home_dir
            / "Library"
            / "Application Support"
            / "com.openai.chat"
            / "app_pairing_extensions"
        )
        self._state = PairedEditorState()
        self._needs_reload = False
        self._server: asyncio.AbstractServer | None = None
        self._registered_pairing_id = self._new_pairing_id()
        self._lock = asyncio.Lock()

    @property
    def descriptor_path(self) -> Path:
        return self.app_pairing_extensions_dir / self._registered_pairing_id

    @property
    def socket_path(self) -> Path:
        return self.socket_dir / f"{self._registered_pairing_id}.sock"

    async def start(self) -> None:
        if self._server is not None:
            return

        self.app_pairing_extensions_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._append_log(
            "bridge_starting",
            {
                "pairing_id": self._registered_pairing_id,
                "descriptor_path": str(self.descriptor_path),
                "socket_path": str(self.socket_path),
            },
        )
        await self._restart_server()
        await self._write_descriptor()

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()

        self._remove_descriptor_file()
        self._remove_socket_file()
        self._append_log(
            "bridge_stopped",
            {
                "pairing_id": self._registered_pairing_id,
                "descriptor_path": str(self.descriptor_path),
                "socket_path": str(self.socket_path),
            },
        )

    async def update_state(
        self,
        *,
        session_id: str,
        workspace_name: str,
        content: str,
        selection_start: int,
        selection_end: int,
    ) -> None:
        normalized_content = content
        normalized_start = _clamp_offset(selection_start, normalized_content)
        normalized_end = _clamp_offset(selection_end, normalized_content)
        if normalized_end < normalized_start:
            normalized_start, normalized_end = normalized_end, normalized_start

        textfield_id = self._textfield_id(session_id)
        async with self._lock:
            previous_workspace_name = self._state.workspace_name
            self._state = PairedEditorState(
                session_id=session_id,
                workspace_name=workspace_name or DEFAULT_WORKSPACE_NAME,
                textfield_id=textfield_id,
                content=normalized_content,
                selection_start=normalized_start,
                selection_end=normalized_end,
            )

        self._append_log(
            "state_updated",
            {
                "pairing_id": self._registered_pairing_id,
                "session_id": session_id,
                "workspace_name": workspace_name or DEFAULT_WORKSPACE_NAME,
                "textfield_id": textfield_id,
                "content_length": len(normalized_content),
                "selection_start": normalized_start,
                "selection_end": normalized_end,
            },
        )

        if previous_workspace_name != self._state.workspace_name:
            await self._write_descriptor()

    async def snapshot(self) -> PairedEditorState:
        async with self._lock:
            return PairedEditorState(
                session_id=self._state.session_id,
                workspace_name=self._state.workspace_name,
                textfield_id=self._state.textfield_id,
                content=self._state.content,
                selection_start=self._state.selection_start,
                selection_end=self._state.selection_end,
            )

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        connection_id = f"{int(time() * 1000)}"
        try:
            length_bytes = await reader.readexactly(4)
            payload_length = int.from_bytes(length_bytes, byteorder="little")
            payload_bytes = await reader.readexactly(payload_length)
            response = await self._handle_payload(payload_bytes)
            response_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")
            writer.write(len(response_bytes).to_bytes(4, byteorder="little"))
            writer.write(response_bytes)
            await writer.drain()
            self._append_log(
                "socket_exchange",
                {
                    "connection_id": connection_id,
                    "pairing_id": self._registered_pairing_id,
                    "socket_path": str(self.socket_path),
                    "request": self._decode_json_payload(payload_bytes),
                    "response": response,
                },
            )
        except asyncio.IncompleteReadError:
            self._append_log(
                "socket_disconnected",
                {
                    "connection_id": connection_id,
                    "pairing_id": self._registered_pairing_id,
                    "socket_path": str(self.socket_path),
                },
            )
            return
        except Exception as exc:
            self._append_log(
                "socket_exchange_error",
                {
                    "connection_id": connection_id,
                    "pairing_id": self._registered_pairing_id,
                    "socket_path": str(self.socket_path),
                    "error": str(exc),
                },
            )
            raise
        finally:
            writer.close()
            await writer.wait_closed()

    async def _handle_payload(self, payload_bytes: bytes) -> dict[str, Any]:
        try:
            request = json.loads(payload_bytes.decode("utf-8"))
        except json.JSONDecodeError:
            return {"status": 400, "error": "Invalid JSON payload"}

        if not isinstance(request, dict):
            return {"status": 400, "error": "Invalid request payload"}

        command = request.get("command")
        payload = request.get("payload")
        if not isinstance(command, str) or not command.strip():
            return {"status": 400, "error": "Missing command"}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return {"status": 400, "error": "Invalid request payload"}

        try:
            return await self._dispatch_command(command, payload)
        except PairedEditorBridgeError as exc:
            return {"status": 400, "error": str(exc)}
        except Exception as exc:
            return {"status": 400, "error": str(exc)}

    async def _dispatch_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command == "ping":
            return {
                "status": "success",
                "name": self.marketplace_id,
                "version": self.extension_version,
            }

        if command == "content":
            state = await self.snapshot()
            if not state.textfield_id:
                return {"status": 400, "error": "No active editor found"}
            return {
                "status": "success",
                "textfields": [self._content_textfield(state)],
            }

        if command == "selections":
            state = await self.snapshot()
            if not state.textfield_id:
                return {"status": 400, "error": "No active editor found"}
            selection = self._selection_payload(state)
            return {
                "status": "success",
                "selections": [selection] if selection is not None else [],
            }

        if command == "reload":
            return {"status": "success"}

        if command == "markForReload":
            self._needs_reload = True
            await self._write_descriptor()
            return {
                "status": "success",
                "message": "Marked for reload",
            }

        if command == "removeHighlights":
            self._require_textfield(payload.get("textfieldID"))
            return {
                "status": "success",
                "message": "Removed highlight",
            }

        if command == "highlightLines":
            self._require_textfield(payload.get("textfieldID"))
            return {
                "status": "success",
                "message": "Text highlighted successfully",
            }

        if command == "highlight":
            self._require_textfield(payload.get("textfieldID"))
            return {
                "status": "success",
                "message": "Text highlighted successfully",
            }

        if command == "setContent":
            textfield_id = self._require_textfield(payload.get("textfieldID"))
            content = self._required_string(payload, "content")
            await self._set_content(textfield_id=textfield_id, content=content)
            return {
                "status": "success",
            "message": "Set content successfully",
        }

        if command == "replaceSelection":
            textfield_id = self._require_textfield(payload.get("textfieldID"))
            content = self._required_string(payload, "content")
            await self._replace_selection(textfield_id=textfield_id, content=content)
            return {
                "status": "success",
            "message": "Replaced selection successfully",
        }

        return {"status": 404, "error": "Unknown endpoint"}

    async def _set_content(self, *, textfield_id: str, content: str) -> None:
        async with self._lock:
            state = self._require_active_state(textfield_id)
            next_state = PairedEditorState(
                session_id=state.session_id,
                workspace_name=state.workspace_name,
                textfield_id=state.textfield_id,
                content=content,
                selection_start=len(content),
                selection_end=len(content),
            )
            self._state = next_state

        await self._publish_remote_update(next_state)

    async def _replace_selection(self, *, textfield_id: str, content: str) -> None:
        async with self._lock:
            state = self._require_active_state(textfield_id)
            selection_start = _clamp_offset(state.selection_start, state.content)
            selection_end = _clamp_offset(state.selection_end, state.content)
            if selection_end < selection_start:
                selection_start, selection_end = selection_end, selection_start

            next_content = (
                f"{state.content[:selection_start]}"
                f"{content}"
                f"{state.content[selection_end:]}"
            )
            next_state = PairedEditorState(
                session_id=state.session_id,
                workspace_name=state.workspace_name,
                textfield_id=state.textfield_id,
                content=next_content,
                selection_start=selection_start,
                selection_end=selection_start,
            )
            self._state = next_state

        await self._publish_remote_update(next_state)

    async def _publish_remote_update(self, state: PairedEditorState) -> None:
        await self.event_broker.publish(
            "codex_paired_editor_update",
            {
                "session_id": state.session_id,
                "textfield_id": state.textfield_id,
                "content": state.content,
                "selection_start": state.selection_start,
                "selection_end": state.selection_end,
            },
        )
        self._append_log(
            "remote_update_published",
            {
                "pairing_id": self._registered_pairing_id,
                "session_id": state.session_id,
                "textfield_id": state.textfield_id,
                "content_length": len(state.content),
                "selection_start": state.selection_start,
                "selection_end": state.selection_end,
            },
        )

    def _content_textfield(self, state: PairedEditorState) -> dict[str, Any]:
        selected_text = self._selected_text(state)
        if selected_text == state.content:
            selected_text = None
        return {
            "id": state.textfield_id,
            "content": state.content,
            "filename": state.textfield_id,
            "selectedText": selected_text,
            "selectionRange": (
                {
                    "location": state.selection_start,
                    "length": state.selection_end - state.selection_start,
                }
                if state.selection_start != state.selection_end
                else None
            ),
            "selectionLine": (
                self._selection_line(state.content, state.selection_start)
                if state.selection_start != state.selection_end
                else None
            ),
        }

    def _selection_payload(self, state: PairedEditorState) -> dict[str, Any] | None:
        selected_text = self._selected_text(state)
        if selected_text is None:
            return None
        return {
            "selectedText": selected_text,
            "selectionLine": self._selection_line(state.content, state.selection_start),
        }

    def _selected_text(self, state: PairedEditorState) -> str | None:
        if not state.textfield_id:
            return None
        if state.selection_start == state.selection_end:
            return None
        return state.content[state.selection_start : state.selection_end]

    def _selection_line(self, content: str, offset: int) -> int:
        return content[:offset].count("\n")

    def _textfield_id(self, session_id: str) -> str:
        normalized_session_id = session_id.strip()
        if not normalized_session_id:
            return ""
        return f"session:{normalized_session_id}:composer"

    def _new_pairing_id(self) -> str:
        safe_app_name = _path_safe_component(self.app_name)
        return f"{safe_app_name}-{uuid4()}"

    def _require_textfield(self, textfield_id: Any) -> str:
        if not isinstance(textfield_id, str) or not textfield_id.strip():
            raise PairedEditorBridgeError("Missing textfieldID")
        return textfield_id.strip()

    def _required_string(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise PairedEditorBridgeError(f"Missing {key}")
        return value

    def _require_active_state(self, textfield_id: str) -> PairedEditorState:
        if not self._state.textfield_id or self._state.textfield_id != textfield_id:
            raise PairedEditorBridgeError(f"No editor for id {textfield_id} found")
        return self._state

    async def _write_descriptor(self) -> None:
        payload = {
            "appName": self.app_name,
            "bundleID": self.bundle_id,
            "extensionVersion": self.extension_version,
            "marketplaceID": self.marketplace_id,
            "extensionName": self.extension_name,
            "workspaceName": self._state.workspace_name or DEFAULT_WORKSPACE_NAME,
            "id": self._registered_pairing_id,
            "capabilities": PAIRING_CAPABILITIES,
            "needsReload": self._needs_reload,
            "socketPath": str(self.socket_path),
            "timestamp": int(time() * 1000),
        }
        self.descriptor_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        self._append_log(
            "descriptor_written",
            {
                "pairing_id": self._registered_pairing_id,
                "descriptor_path": str(self.descriptor_path),
                "socket_path": str(self.socket_path),
                "workspace_name": payload["workspaceName"],
            },
        )

    def _remove_socket_file(self) -> None:
        if self.socket_path.exists():
            self.socket_path.unlink()
            self._append_log(
                "socket_removed",
                {
                    "pairing_id": self._registered_pairing_id,
                    "socket_path": str(self.socket_path),
                },
            )

    def _remove_descriptor_file(self, pairing_id: str | None = None) -> None:
        descriptor_path = self.app_pairing_extensions_dir / (pairing_id or self._registered_pairing_id)
        if descriptor_path.exists():
            descriptor_path.unlink()
            self._append_log(
                "descriptor_removed",
                {
                    "pairing_id": pairing_id or self._registered_pairing_id,
                    "descriptor_path": str(descriptor_path),
                },
            )

    def _remove_socket_file_for_pairing(self, pairing_id: str) -> None:
        socket_path = self.socket_dir / f"{pairing_id}.sock"
        if socket_path.exists():
            socket_path.unlink()
            self._append_log(
                "socket_removed",
                {
                    "pairing_id": pairing_id,
                    "socket_path": str(socket_path),
                },
            )

    async def _restart_server(self, previous_pairing_id: str | None = None) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
            self._append_log(
                "server_closed",
                {
                    "pairing_id": previous_pairing_id or self._registered_pairing_id,
                },
            )

        if previous_pairing_id is not None:
            self._remove_descriptor_file(previous_pairing_id)
            self._remove_socket_file_for_pairing(previous_pairing_id)

        self._remove_socket_file()
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path),
        )
        self.socket_path.chmod(0o600)
        self._append_log(
            "server_listening",
            {
                "pairing_id": self._registered_pairing_id,
                "descriptor_path": str(self.descriptor_path),
                "socket_path": str(self.socket_path),
            },
        )

    def _append_log(self, event: str, payload: dict[str, Any]) -> None:
        entry = {
            "timestamp": time(),
            "event": event,
            **payload,
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{json.dumps(entry, ensure_ascii=False, sort_keys=True)}\n")

    def _decode_json_payload(self, payload: bytes) -> Any:
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {
                "raw_hex": payload.hex(),
            }
