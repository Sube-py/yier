from __future__ import annotations

import json
from pathlib import Path
import socket
from typing import Any

from yier_web.codex_workspace import CodexWorkspaceService
from yier_web.schemas import CodexPairingExtensionSummary


DEFAULT_PAIRING_TIMEOUT_SECONDS = 5.0


class CodexPairingClientError(RuntimeError):
    """Raised when the editor pairing bridge cannot satisfy a request."""


class CodexPairingSocketClient:
    def __init__(
        self,
        home_dir: Path | None = None,
        *,
        workspace_service: CodexWorkspaceService | None = None,
        timeout_seconds: float = DEFAULT_PAIRING_TIMEOUT_SECONDS,
    ) -> None:
        resolved_home = (home_dir or Path.home()).resolve()
        self.workspace_service = workspace_service or CodexWorkspaceService(resolved_home)
        self.timeout_seconds = timeout_seconds

    def list_editors(self) -> list[CodexPairingExtensionSummary]:
        return self.workspace_service.list_paired_editors()

    def ping(self, editor_id: str | None = None) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(editor, "ping")
        return {
            "editor": self._editor_payload(editor),
            "response": payload,
        }

    def content(self, editor_id: str | None = None) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(editor, "content")
        return {
            "editor": self._editor_payload(editor),
            "textfields": payload.get("textfields", []),
            "response": payload,
        }

    def selections(self, editor_id: str | None = None) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(editor, "selections")
        return {
            "editor": self._editor_payload(editor),
            "selections": payload.get("selections", []),
            "response": payload,
        }

    def reload_window(self, editor_id: str | None = None) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(editor, "reload")
        return {
            "editor": self._editor_payload(editor),
            "response": payload,
        }

    def mark_for_reload(self, editor_id: str | None = None) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(editor, "markForReload")
        return {
            "editor": self._editor_payload(editor),
            "response": payload,
        }

    def remove_highlights(
        self,
        *,
        textfield_id: str,
        animated: bool = False,
        editor_id: str | None = None,
    ) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(
            editor,
            "removeHighlights",
            {
                "textfieldID": textfield_id,
                "animated": animated,
            },
        )
        return {
            "editor": self._editor_payload(editor),
            "response": payload,
        }

    def highlight_lines(
        self,
        *,
        lines: list[int],
        textfield_id: str,
        editor_id: str | None = None,
    ) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(
            editor,
            "highlightLines",
            {
                "lines": lines,
                "textfieldID": textfield_id,
            },
        )
        return {
            "editor": self._editor_payload(editor),
            "response": payload,
        }

    def highlight(
        self,
        *,
        start_char: int,
        end_char: int,
        textfield_id: str,
        editor_id: str | None = None,
    ) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(
            editor,
            "highlight",
            {
                "startChar": start_char,
                "endChar": end_char,
                "textfieldID": textfield_id,
            },
        )
        return {
            "editor": self._editor_payload(editor),
            "response": payload,
        }

    def set_content(
        self,
        *,
        content: str,
        textfield_id: str,
        editor_id: str | None = None,
    ) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(
            editor,
            "setContent",
            {
                "content": content,
                "textfieldID": textfield_id,
            },
        )
        return {
            "editor": self._editor_payload(editor),
            "response": payload,
        }

    def replace_selection(
        self,
        *,
        content: str,
        textfield_id: str,
        editor_id: str | None = None,
    ) -> dict[str, Any]:
        editor = self.resolve_editor(editor_id)
        payload = self._send_command(
            editor,
            "replaceSelection",
            {
                "content": content,
                "textfieldID": textfield_id,
            },
        )
        return {
            "editor": self._editor_payload(editor),
            "response": payload,
        }

    def resolve_editor(self, editor_id: str | None = None) -> CodexPairingExtensionSummary:
        editors = self.list_editors()
        if not editors:
            raise CodexPairingClientError("No online paired editors are available.")

        if editor_id is None:
            return editors[0]

        normalized_id = editor_id.strip()
        for editor in editors:
            if editor.id == normalized_id:
                return editor

        raise CodexPairingClientError(f"Paired editor '{editor_id}' is not online.")

    def _send_command(
        self,
        editor: CodexPairingExtensionSummary,
        command: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = {
            "command": command,
            "payload": payload or {},
        }
        message = json.dumps(request, ensure_ascii=False).encode("utf-8")

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(editor.socket_path)
                connection.sendall(len(message).to_bytes(4, byteorder="little"))
                connection.sendall(message)

                response_length_bytes = self._read_exact(connection, 4)
                response_length = int.from_bytes(response_length_bytes, byteorder="little")
                response_bytes = self._read_exact(connection, response_length)
        except (OSError, TimeoutError) as exc:
            raise CodexPairingClientError(
                f"Failed to reach paired editor '{editor.id}' via '{editor.socket_path}': {exc}"
            ) from exc

        try:
            response = json.loads(response_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CodexPairingClientError(
                f"Paired editor '{editor.id}' returned invalid JSON."
            ) from exc

        if not isinstance(response, dict):
            raise CodexPairingClientError(
                f"Paired editor '{editor.id}' returned an unexpected response payload."
            )

        status = response.get("status")
        if status != "success":
            error_message = response.get("error")
            if not isinstance(error_message, str) or not error_message.strip():
                error_message = f"Editor command '{command}' failed."
            raise CodexPairingClientError(str(error_message))

        return response

    def _read_exact(self, connection: socket.socket, byte_count: int) -> bytes:
        remaining = byte_count
        chunks: list[bytes] = []
        while remaining > 0:
            chunk = connection.recv(remaining)
            if not chunk:
                raise CodexPairingClientError("Paired editor socket closed unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _editor_payload(self, editor: CodexPairingExtensionSummary) -> dict[str, Any]:
        return editor.model_dump(mode="json")
