from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yier_channel.platforms.weixin import adapter as weixin_adapter_module
from yier_channel.platforms.weixin.adapter import WeixinAdapter
from yier_channel.platforms.weixin.cdn import build_cdn_upload_url
from yier_channel.platforms.weixin.media import detect_media_kind


def test_detect_media_kind_uses_file_extension() -> None:
    assert detect_media_kind(Path("photo.png")) == "image"
    assert detect_media_kind(Path("clip.mp4")) == "video"
    assert detect_media_kind(Path("notes.pdf")) == "file"


def test_build_cdn_upload_url_encodes_query_params() -> None:
    url = build_cdn_upload_url(
        cdn_base_url="https://cdn.example.com/c2c",
        upload_param="a+b/=?",
        filekey="file key",
    )

    assert url == "https://cdn.example.com/c2c/upload?encrypted_query_param=a%2Bb%2F%3D%3F&filekey=file+key"


def test_weixin_adapter_send_file_dispatches_image_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = WeixinAdapter(tmp_path)
    adapter.storage.save_account("wx-a", {"token": "token-1"})
    adapter._context_tokens[("wx-a", "peer-1")] = "ctx-1"

    file_path = tmp_path / "photo.png"
    file_path.write_bytes(b"hello")

    events: list[tuple[str, dict[str, object]]] = []

    async def event_sink(event: str, data: dict[str, object]) -> None:
        events.append((event, data))

    adapter.configure_sinks(None, event_sink)

    monkeypatch.setattr(
        weixin_adapter_module,
        "build_upload_metadata",
        lambda path: (b"hello", "file-key", 5, "00112233445566778899aabbccddeeff"),
    )
    monkeypatch.setattr(weixin_adapter_module, "detect_media_kind", lambda path: "image")

    upload_call: dict[str, object] = {}

    async def fake_upload_buffer_to_cdn(
        data: bytes,
        upload_param: str,
        filekey: str,
        cdn_base_url: str,
        aes_key: bytes,
    ) -> str:
        upload_call["payload"] = {
            "data": data,
            "upload_param": upload_param,
            "filekey": filekey,
            "cdn_base_url": cdn_base_url,
            "aes_key": aes_key,
        }
        return "download-param"

    async def fake_get_upload_url(
        token: str,
        *,
        filekey: str,
        media_type: int,
        to_user_id: str,
        plaintext: bytes,
        aes_key_hex: str,
    ) -> dict[str, str]:
        assert token == "token-1"
        assert filekey == "file-key"
        assert media_type == 1
        assert to_user_id == "peer-1"
        assert plaintext == b"hello"
        assert aes_key_hex == "00112233445566778899aabbccddeeff"
        return {"upload_param": "upload-param"}

    async def fake_send_image_message(
        token: str,
        to_user_id: str,
        context_token: str,
        *,
        encrypted_query_param: str,
        aes_key_hex: str,
        ciphertext_size: int,
        text: str = "",
    ) -> dict[str, str]:
        assert token == "token-1"
        assert to_user_id == "peer-1"
        assert context_token == "ctx-1"
        assert encrypted_query_param == "download-param"
        assert aes_key_hex == "00112233445566778899aabbccddeeff"
        assert ciphertext_size == 16
        assert text == "caption"
        return {"message_id": "mid-1"}

    async def unexpected_send(*args: object, **kwargs: object) -> dict[str, str]:
        raise AssertionError("unexpected media sender used")

    monkeypatch.setattr(weixin_adapter_module, "upload_buffer_to_cdn", fake_upload_buffer_to_cdn)
    monkeypatch.setattr(adapter.api, "get_upload_url", fake_get_upload_url)
    monkeypatch.setattr(adapter.api, "send_image_message", fake_send_image_message)
    monkeypatch.setattr(adapter.api, "send_video_message", unexpected_send)
    monkeypatch.setattr(adapter.api, "send_file_message", unexpected_send)

    result = asyncio.run(
        adapter.send_file(
            account_id="wx-a",
            peer_id="peer-1",
            file_path=file_path,
            text="caption",
        )
    )

    assert result == {
        "message_id": "mid-1",
        "media_kind": "image",
        "file_name": "photo.png",
    }
    assert upload_call["payload"] == {
        "data": b"hello",
        "upload_param": "upload-param",
        "filekey": "file-key",
        "cdn_base_url": "https://novac2c.cdn.weixin.qq.com/c2c",
        "aes_key": bytes.fromhex("00112233445566778899aabbccddeeff"),
    }
    assert any(
        event == "channel_outbound_message"
        and data["media_kind"] == "image"
        and data["file_name"] == "photo.png"
        and data["content"] == "caption"
        for event, data in events
    )
