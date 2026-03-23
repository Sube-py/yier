from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Literal


MediaKind = Literal["image", "video", "file"]


def detect_media_kind(path: Path) -> MediaKind:
    mime_type, _ = mimetypes.guess_type(path.name)
    normalized = (mime_type or "application/octet-stream").lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("video/"):
        return "video"
    return "file"
