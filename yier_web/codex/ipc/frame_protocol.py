r"""IPC frame protocol: socket path, length-prefixed framing, JSON encode/decode.

Wire format (identical to original Codex extension):

    ┌──────────────────────┬──────────────────────────┐
    │ 4 bytes  UInt32 LE   │   JSON payload (UTF-8)    │
    │ = payload length     │                          │
    └──────────────────────┴──────────────────────────┘

Socket path (from original vs() function):
  - macOS/Linux: $TMPDIR/codex-ipc/ipc-{uid}.sock  (Unix domain socket)
  - Windows:     \\.\pipe\codex-ipc                  (named pipe)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
import tempfile
from typing import Any

from yier_web.codex.ipc.constants import IPC_MAX_FRAME_BYTES

_IS_WINDOWS = sys.platform == "win32"


def ipc_socket_path() -> Path:
    """Return the IPC socket/pipe path for the current platform.

    Matches the original Codex extension's vs() / getIpcSocketPath():

    - **Windows**: ``\\\\.\\pipe\\codex-ipc``  (named pipe)
    - **macOS/Linux**: ``$TMPDIR/codex-ipc/ipc-{uid}.sock``  (Unix domain socket)
    """
    if _IS_WINDOWS:
        return Path(r"\\.\pipe\codex-ipc")
    sock_dir = Path(tempfile.gettempdir()) / "codex-ipc"
    uid = os.getuid()
    return sock_dir / f"ipc-{uid}.sock"


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read one length-prefixed JSON frame from a stream reader.

    Protocol: 4-byte LE uint32 length prefix + JSON payload.
    Raises ValueError if the frame exceeds IPC_MAX_FRAME_BYTES (256 MB)
    or if the payload is not a JSON object.
    """
    length_bytes = await reader.readexactly(4)
    payload_length = int.from_bytes(length_bytes, byteorder="little")
    if payload_length > IPC_MAX_FRAME_BYTES:
        raise ValueError(
            f"IPC frame exceeded limit ({payload_length} > {IPC_MAX_FRAME_BYTES} bytes)"
        )
    payload_bytes = await reader.readexactly(payload_length)
    payload = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("IPC payload must decode to an object.")
    return payload


def json_dumps(payload: dict[str, Any]) -> bytes:
    """Serialize a dict to UTF-8 JSON bytes (no BOM, no sort)."""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
