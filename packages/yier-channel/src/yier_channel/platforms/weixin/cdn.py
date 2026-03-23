from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def aes_ecb_padded_size(plaintext_size: int) -> int:
    return ((plaintext_size // 16) + 1) * 16


def build_cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    query = urlencode({"encrypted_query_param": upload_param, "filekey": filekey})
    return f"{cdn_base_url.rstrip('/')}/upload?{query}"


async def upload_buffer_to_cdn(
    data: bytes,
    upload_param: str,
    filekey: str,
    cdn_base_url: str,
    aes_key: bytes,
) -> str:
    ciphertext = encrypt_aes_ecb(data, aes_key)
    url = build_cdn_upload_url(cdn_base_url=cdn_base_url, upload_param=upload_param, filekey=filekey)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            headers={"Content-Type": "application/octet-stream"},
            content=ciphertext,
        )
        response.raise_for_status()
    download_param = response.headers.get("x-encrypted-param", "").strip()
    if not download_param:
        raise ValueError("CDN upload response did not include x-encrypted-param")
    return download_param


def build_upload_metadata(file_path: Path) -> tuple[bytes, str, int, str]:
    plaintext = file_path.read_bytes()
    filekey = secrets.token_hex(16)
    rawsize = len(plaintext)
    aes_key = secrets.token_bytes(16)
    return plaintext, filekey, rawsize, aes_key.hex()
