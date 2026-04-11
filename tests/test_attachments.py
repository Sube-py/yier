from __future__ import annotations

import asyncio
from pathlib import Path

from litestar.datastructures import UploadFile
import pytest

from yier_web.attachments import AttachmentStorageError, AttachmentStorageService


def test_attachment_storage_converts_image_to_local_image_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = AttachmentStorageService(tmp_path)

        response = await service.save_upload(
            session_id="session-1",
            upload=UploadFile(
                content_type="image/png",
                filename="sample.png",
                file_data=b"\x89PNG\r\n\x1a\nsample",
            ),
        )

        assert response.kind == "image"
        assert response.preview_url == f"/api/chat/sessions/session-1/attachments/{response.id}/content"
        assert response.input_items[0].type == "localImage"
        assert response.input_items[1].type == "mention"
        assert service.input_items_for_attachment(
            session_id="session-1",
            attachment_id=response.id,
        )[0]["type"] == "localImage"

    asyncio.run(scenario())


def test_attachment_storage_converts_text_to_bounded_text_and_mention(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = AttachmentStorageService(tmp_path)

        response = await service.save_upload(
            session_id="session-1",
            upload=UploadFile(
                content_type="text/plain",
                filename="notes.txt",
                file_data=b"hello from upload",
            ),
        )

        assert response.kind == "text"
        assert response.input_items[0].type == "text"
        assert "hello from upload" in (response.input_items[0].text or "")
        assert response.input_items[1].type == "mention"

    asyncio.run(scenario())


def test_attachment_storage_rejects_empty_upload(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = AttachmentStorageService(tmp_path)

        with pytest.raises(AttachmentStorageError, match="empty"):
            await service.save_upload(
                session_id="session-1",
                upload=UploadFile(
                    content_type="text/plain",
                    filename="empty.txt",
                    file_data=b"",
                ),
            )

    asyncio.run(scenario())


def test_attachment_storage_normalizes_identifier_path_segments(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = AttachmentStorageService(tmp_path / "uploads")

        response = await service.save_upload(
            session_id="../session.with.dots",
            upload=UploadFile(
                content_type="text/plain",
                filename="../notes.txt",
                file_data=b"safe content",
            ),
        )

        path, _mime_type, name = service.media_path(
            session_id="../session.with.dots",
            attachment_id=response.id,
        )

        assert name == "notes.txt"
        assert path.is_relative_to(service.uploads_root)
        assert path.parent.parent == service.uploads_root / "_session_with_dots"

    asyncio.run(scenario())


def test_attachment_storage_registers_local_image_preview(tmp_path: Path) -> None:
    service = AttachmentStorageService(tmp_path / "uploads")
    source = tmp_path / "generated.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\npreview")

    response = service.register_local_image(
        session_id="session-1",
        source_path=str(source),
    )

    assert response is not None
    assert response.kind == "image"
    assert response.preview_url == f"/api/chat/sessions/session-1/attachments/{response.id}/content"
