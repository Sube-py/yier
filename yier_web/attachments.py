from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from litestar.datastructures import UploadFile

from yier_web.schemas import AttachmentUploadResponse, CodexInputItem


MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_TEXT_ATTACHMENT_CHARS = 120_000
TEXT_EXTENSIONS = {
    ".cfg",
    ".conf",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}


class AttachmentStorageError(ValueError):
    """Raised when an uploaded attachment cannot be accepted."""


class AttachmentStorageService:
    def __init__(self, uploads_root: Path) -> None:
        self.uploads_root = uploads_root.resolve()
        self.uploads_root.mkdir(parents=True, exist_ok=True)

    async def save_upload(
        self,
        *,
        session_id: str,
        upload: UploadFile,
    ) -> AttachmentUploadResponse:
        normalized_session_id = self._safe_identifier(session_id)
        data = await upload.read()
        size = len(data)
        if size <= 0:
            raise AttachmentStorageError("Attachment is empty.")
        if size > MAX_ATTACHMENT_BYTES:
            raise AttachmentStorageError(
                f"Attachment is too large. Maximum size is {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."
            )

        attachment_id = uuid4().hex
        original_name = upload.filename or "attachment"
        safe_name = self._safe_filename(original_name)
        mime_type = (
            upload.content_type
            or mimetypes.guess_type(safe_name)[0]
            or "application/octet-stream"
        )
        attachment_dir = self._attachment_dir(normalized_session_id, attachment_id)
        attachment_dir.mkdir(parents=True, exist_ok=False)
        file_path = attachment_dir / safe_name
        file_path.write_bytes(data)

        kind = self._attachment_kind(file_path, mime_type, data)
        input_items = self._input_items_for_attachment(
            session_id=normalized_session_id,
            attachment_id=attachment_id,
            file_path=file_path,
            name=safe_name,
            mime_type=mime_type,
            kind=kind,
            size=size,
            data=data,
        )
        preview_url = (
            f"/api/chat/sessions/{normalized_session_id}/attachments/{attachment_id}/content"
            if kind == "image"
            else None
        )
        response = AttachmentUploadResponse(
            id=attachment_id,
            name=safe_name,
            mime_type=mime_type,
            size=size,
            kind=kind,
            preview_url=preview_url,
            input_items=input_items,
        )
        self._write_record(normalized_session_id, response, file_path)
        return response

    def input_items_for_attachment(
        self,
        *,
        session_id: str,
        attachment_id: str,
    ) -> list[dict[str, Any]]:
        record = self._read_record(session_id, attachment_id)
        input_items = record.get("input_items")
        if not isinstance(input_items, list):
            raise AttachmentStorageError("Attachment input metadata is missing.")
        return [
            item
            for item in input_items
            if isinstance(item, dict) and isinstance(item.get("type"), str)
        ]

    def register_local_image(
        self,
        *,
        session_id: str,
        source_path: str,
    ) -> AttachmentUploadResponse | None:
        if "://" in source_path:
            return None
        normalized_session_id = self._safe_identifier(session_id)
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            return None

        source_key = str(source)
        registry = self._read_registry(normalized_session_id)
        for record in registry.values():
            if not isinstance(record, dict):
                continue
            if record.get("kind") != "image":
                continue
            if (
                record.get("source_path") == source_key
                or record.get("file_path") == source_key
            ):
                return AttachmentUploadResponse.model_validate(record)

        size = source.stat().st_size
        if size <= 0 or size > MAX_ATTACHMENT_BYTES:
            return None
        data = source.read_bytes()
        safe_name = self._safe_filename(source.name or "generated-image")
        mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        if self._attachment_kind(source, mime_type, data) != "image":
            return None

        attachment_id = uuid4().hex
        attachment_dir = self._attachment_dir(normalized_session_id, attachment_id)
        attachment_dir.mkdir(parents=True, exist_ok=False)
        file_path = attachment_dir / safe_name
        file_path.write_bytes(data)
        input_items = self._input_items_for_attachment(
            session_id=normalized_session_id,
            attachment_id=attachment_id,
            file_path=file_path,
            name=safe_name,
            mime_type=mime_type,
            kind="image",
            size=size,
            data=data,
        )
        response = AttachmentUploadResponse(
            id=attachment_id,
            name=safe_name,
            mime_type=mime_type,
            size=size,
            kind="image",
            preview_url=f"/api/chat/sessions/{normalized_session_id}/attachments/{attachment_id}/content",
            input_items=input_items,
        )
        self._write_record(
            normalized_session_id, response, file_path, source_path=source
        )
        return response

    def media_path(
        self, *, session_id: str, attachment_id: str
    ) -> tuple[Path, str, str]:
        record = self._read_record(session_id, attachment_id)
        raw_path = record.get("file_path")
        if not isinstance(raw_path, str):
            raise AttachmentStorageError("Attachment file path is missing.")
        file_path = Path(raw_path).resolve()
        if not self._is_within_root(file_path):
            raise AttachmentStorageError("Attachment path is outside managed storage.")
        if not file_path.is_file():
            raise AttachmentStorageError("Attachment file does not exist.")
        mime_type = str(record.get("mime_type") or "application/octet-stream")
        name = str(record.get("name") or file_path.name)
        return file_path, mime_type, name

    def _write_record(
        self,
        session_id: str,
        response: AttachmentUploadResponse,
        file_path: Path,
        *,
        source_path: Path | None = None,
    ) -> None:
        registry = self._read_registry(session_id)
        record = response.model_dump(mode="json")
        record["file_path"] = str(file_path.resolve())
        if source_path is not None:
            record["source_path"] = str(source_path.resolve())
        registry[response.id] = record
        registry_path = self._registry_path(session_id)
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_record(self, session_id: str, attachment_id: str) -> dict[str, Any]:
        normalized_session_id = self._safe_identifier(session_id)
        normalized_attachment_id = self._safe_identifier(attachment_id)
        registry = self._read_registry(normalized_session_id)
        record = registry.get(normalized_attachment_id)
        if not isinstance(record, dict):
            raise AttachmentStorageError("Attachment not found.")
        return record

    def _read_registry(self, session_id: str) -> dict[str, Any]:
        registry_path = self._registry_path(self._safe_identifier(session_id))
        if not registry_path.exists():
            return {}
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _registry_path(self, session_id: str) -> Path:
        return (
            self.uploads_root / self._safe_identifier(session_id) / "attachments.json"
        )

    def _attachment_dir(self, session_id: str, attachment_id: str) -> Path:
        return self.uploads_root / session_id / attachment_id

    def _input_items_for_attachment(
        self,
        *,
        session_id: str,
        attachment_id: str,
        file_path: Path,
        name: str,
        mime_type: str,
        kind: str,
        size: int,
        data: bytes,
    ) -> list[CodexInputItem]:
        mention = CodexInputItem(
            type="mention",
            name=name,
            path=str(file_path.resolve()),
        )
        if kind == "image":
            return [
                CodexInputItem(
                    type="localImage",
                    path=str(file_path.resolve()),
                ),
                mention,
            ]

        if kind == "text":
            decoded = self._decode_text(data)
            truncated = len(decoded) > MAX_TEXT_ATTACHMENT_CHARS
            content = decoded[:MAX_TEXT_ATTACHMENT_CHARS]
            suffix = (
                "\n\n[Attachment truncated for Codex input. Open the mentioned file for the full content.]"
                if truncated
                else ""
            )
            return [
                CodexInputItem(
                    type="text",
                    text=(
                        f"Uploaded text attachment `{name}` ({mime_type}, {size} bytes):\n\n"
                        f"{content}{suffix}"
                    ),
                ),
                mention,
            ]

        return [
            CodexInputItem(
                type="text",
                text=(
                    f"Uploaded binary attachment `{name}` ({mime_type}, {size} bytes). "
                    "The file is stored in managed app storage and is referenced as a mention."
                ),
            ),
            mention,
        ]

    def _attachment_kind(self, file_path: Path, mime_type: str, data: bytes) -> str:
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("text/") or file_path.suffix.lower() in TEXT_EXTENSIONS:
            return "text"
        try:
            data[:4096].decode("utf-8")
        except UnicodeDecodeError:
            return "binary"
        return "text"

    def _decode_text(self, data: bytes) -> str:
        for encoding in ("utf-8", "utf-16", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    def _safe_filename(self, name: str) -> str:
        candidate = Path(name).name.strip() or "attachment"
        candidate = re.sub(r"[^A-Za-z0-9._ -]+", "_", candidate)
        return candidate[:180] or "attachment"

    def _safe_identifier(self, value: str) -> str:
        candidate = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
        if not candidate:
            raise AttachmentStorageError("Identifier is empty.")
        return candidate

    def _is_within_root(self, file_path: Path) -> bool:
        try:
            file_path.relative_to(self.uploads_root)
        except ValueError:
            return False
        return True
