"""Codex IPC client.

Asyncio-based IPC client that connects to the Codex IPC router via Unix
domain socket.  Handles the full lifecycle:

  1. Connect to socket
  2. Send ``initialize`` request → receive clientId (UUID)
  3. Send/receive broadcast, request, response, client-discovery messages
  4. Auto-reconnect on disconnect (1 s delay)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from yier_web.codex.ipc.constants import (
    INITIALIZING_CLIENT_ID,
    IPC_MAX_FRAME_BYTES,
    IPC_METHOD_VERSIONS,
    IPC_RECONNECT_DELAY_SECONDS,
    IPC_REQUEST_TIMEOUT_SECONDS,
    BroadcastHandler,
    RequestCanHandle,
    RequestHandler,
)
from yier_web.codex.ipc.debug import (
    ipc_debug_full_enabled,
    ipc_debug_log,
    ipc_message_summary,
)
from yier_web.codex.ipc.frame_protocol import (
    ipc_socket_path,
    json_dumps,
    read_frame,
)

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


class CodexIpcClient:
    """Asyncio IPC client that connects to the Codex IPC router.

    Usage::

        client = CodexIpcClient(client_type="yier")
        client.add_broadcast_handler("thread-stream-state-changed", on_broadcast)
        client.add_request_handler("thread-follower-start-turn", can_handle, handler)
        await client.start()
        ...
        await client.stop()
    """

    def __init__(
        self,
        *,
        client_type: str,
        socket_path: Any | None = None,
    ) -> None:
        from pathlib import Path

        self.client_type = client_type
        self.socket_path = (
            socket_path if isinstance(socket_path, (str, Path))
            else ipc_socket_path()
        )
        if not isinstance(self.socket_path, Path):
            self.socket_path = Path(self.socket_path)
        self.client_id = INITIALIZING_CLIENT_ID
        self._closed = False
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._connected = asyncio.Event()
        self._request_handlers: dict[str, tuple[RequestCanHandle, RequestHandler]] = {}
        self._broadcast_handlers: dict[str, BroadcastHandler] = {}
        self._pending_responses: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._run_task: asyncio.Task[None] | None = None
        self._read_task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    # ─── public API ───

    def add_request_handler(
        self,
        method: str,
        can_handle: RequestCanHandle,
        handler: RequestHandler,
    ) -> None:
        self._request_handlers[method] = (can_handle, handler)

    def add_broadcast_handler(
        self,
        method: str,
        handler: BroadcastHandler,
    ) -> None:
        self._broadcast_handlers[method] = handler

    async def start(self) -> None:
        if self._run_task is not None:
            return
        self._closed = False
        ipc_debug_log(
            "starting ipc client",
            client_type=self.client_type,
            socket_path=str(self.socket_path),
        )
        self._run_task = asyncio.create_task(self._run(), name="codex-ipc-client")

    async def stop(self) -> None:
        self._closed = True
        self._connected.clear()
        ipc_debug_log("stopping ipc client", client_id=self.client_id)

        # 1. Close write end first → sends FIN to the router.
        #    Router detects the close, runs unregisterClient,
        #    broadcasts ``client-status-changed`` with status ``"disconnected"``,
        #    then closes its end → our reader gets EOF → _read_loop exits.
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None

        # 2. Reject pending responses (router may have already rejected some
        #    via the close path above, but this covers in-flight futures).
        for future in list(self._pending_responses.values()):
            if not future.done():
                future.set_exception(RuntimeError("disposed"))
        self._pending_responses.clear()

        # 3. Cancel _run_task (which also cancels _read_task via the
        #    finally block).  Use a short timeout so we don't hang forever
        #    if the OS is slow to deliver EOF.
        if self._run_task is not None:
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(
                    asyncio.shield(self._run_task), timeout=2.0
                )
            self._run_task = None
            self._read_task = None

    async def wait_until_connected(
        self, timeout: float = IPC_REQUEST_TIMEOUT_SECONDS,
    ) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    async def send_broadcast(self, method: str, params: dict[str, Any]) -> None:
        await self._ensure_connected()
        ipc_debug_log(
            "send broadcast",
            method=method,
            params=params
            if ipc_debug_full_enabled()
            else {"keys": sorted(params.keys())},
        )
        version = int(IPC_METHOD_VERSIONS.get(method, 0))
        await self._send_message(
            {
                "type": "broadcast",
                "method": method,
                "params": params,
                "sourceClientId": self.client_id,
                "version": version,
            }
        )

    async def send_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        target_client_id: str | None = None,
        allow_uninitialized: bool = False,
    ) -> dict[str, Any]:
        if allow_uninitialized:
            if self._writer is None:
                raise RuntimeError("not-connected")
        else:
            await self._ensure_connected()
        if not allow_uninitialized and self.client_id == INITIALIZING_CLIENT_ID:
            raise RuntimeError("not-initialized")
        request_id = str(uuid4())
        version = int(IPC_METHOD_VERSIONS.get(method, 0))
        ipc_debug_log(
            "send request",
            method=method,
            request_id=request_id,
            target_client_id=target_client_id,
            params=params
            if ipc_debug_full_enabled()
            else {"keys": sorted(params.keys())},
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_responses[request_id] = future
        await self._send_message(
            {
                "type": "request",
                "requestId": request_id,
                "sourceClientId": self.client_id,
                "version": version,
                "method": method,
                "params": params,
                "targetClientId": target_client_id,
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=IPC_REQUEST_TIMEOUT_SECONDS)
        finally:
            self._pending_responses.pop(request_id, None)

    # ─── connection loop ───

    async def _run(self) -> None:
        while not self._closed:
            try:
                ipc_debug_log(
                    "connecting to ipc socket",
                    client_type=self.client_type,
                    socket_path=str(self.socket_path),
                )
                reader, writer = await self._open_connection()
            except (FileNotFoundError, ConnectionError, OSError):
                ipc_debug_log(
                    "ipc socket connect failed",
                    client_type=self.client_type,
                    socket_path=str(self.socket_path),
                )
                await asyncio.sleep(IPC_RECONNECT_DELAY_SECONDS)
                continue

            self._writer = writer
            self.client_id = INITIALIZING_CLIENT_ID
            ipc_debug_log(
                "ipc socket connected", socket_path=str(self.socket_path)
            )
            self._read_task = asyncio.create_task(
                self._read_loop(reader), name="codex-ipc-reader"
            )
            try:
                response = await self.send_request(
                    "initialize",
                    {"clientType": self.client_type},
                    allow_uninitialized=True,
                )
                if response.get("resultType") == "success":
                    result = response.get("result")
                    if isinstance(result, dict) and isinstance(
                        result.get("clientId"), str
                    ):
                        self.client_id = result["clientId"]
                        self._connected.set()
                        ipc_debug_log(
                            "ipc client initialized",
                            client_id=self.client_id,
                            client_type=self.client_type,
                        )
                await self._read_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ipc_debug_log("ipc client loop error", error=str(exc))
            finally:
                self._connected.clear()
                ipc_debug_log(
                    "ipc socket disconnected", client_id=self.client_id
                )
                self.client_id = INITIALIZING_CLIENT_ID
                if self._read_task is not None:
                    self._read_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._read_task
                    self._read_task = None
                if self._writer is not None:
                    self._writer.close()
                    with contextlib.suppress(Exception):
                        await self._writer.wait_closed()
                    self._writer = None
                for future in list(self._pending_responses.values()):
                    if not future.done():
                        future.set_exception(RuntimeError("connection-closed"))
                self._pending_responses.clear()
                if not self._closed:
                    await asyncio.sleep(IPC_RECONNECT_DELAY_SECONDS)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while not self._closed:
            message = await read_frame(reader)
            ipc_debug_log(
                "recv message",
                summary=ipc_message_summary(message),
                payload=message if ipc_debug_full_enabled() else None,
            )
            await self._handle_message(message)

    # ─── message dispatch ───

    async def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type == "broadcast":
            await self._handle_broadcast(message)
        elif message_type == "client-discovery-request":
            await self._handle_client_discovery_request(message)
        elif message_type == "response":
            request_id = str(message.get("requestId", ""))
            future = self._pending_responses.get(request_id)
            if future is not None and not future.done():
                future.set_result(message)
        elif message_type == "request":
            await self._handle_request(message)

    async def _handle_broadcast(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", ""))
        handler = self._broadcast_handlers.get(method)
        if handler is None:
            return
        await handler(message)

    async def _handle_client_discovery_request(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("requestId", ""))
        request = message.get("request")
        if not isinstance(request, dict):
            await self._send_message(
                {
                    "type": "client-discovery-response",
                    "requestId": request_id,
                    "response": {"canHandle": False},
                }
            )
            return
        method = str(request.get("method", ""))
        params = request.get("params")
        version = int(request.get("version", 0) or 0)
        handler = self._request_handlers.get(method)
        if (
            version != int(IPC_METHOD_VERSIONS.get(method, 0))
            or handler is None
            or not isinstance(params, dict)
        ):
            await self._send_message(
                {
                    "type": "client-discovery-response",
                    "requestId": request_id,
                    "response": {"canHandle": False},
                }
            )
            return

        can_handle, _ = handler
        try:
            response = bool(await can_handle(params))
        except Exception:
            response = False
        await self._send_message(
            {
                "type": "client-discovery-response",
                "requestId": request_id,
                "response": {"canHandle": response},
            }
        )

    async def _handle_request(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("requestId", ""))
        method = str(message.get("method", ""))
        params = message.get("params")
        version = int(message.get("version", 0) or 0)
        handler = self._request_handlers.get(method)
        if version != int(IPC_METHOD_VERSIONS.get(method, 0)):
            await self._send_message(
                {
                    "type": "response",
                    "requestId": request_id,
                    "resultType": "error",
                    "error": "request-version-mismatch",
                }
            )
            return
        if handler is None or not isinstance(params, dict):
            await self._send_message(
                {
                    "type": "response",
                    "requestId": request_id,
                    "resultType": "error",
                    "error": "no-handler-for-request",
                }
            )
            return

        _, handle = handler
        try:
            result = await handle(message)
        except Exception as exc:
            await self._send_message(
                {
                    "type": "response",
                    "requestId": request_id,
                    "resultType": "error",
                    "error": str(exc) or "error-handling-request",
                }
            )
            return

        await self._send_message(
            {
                "type": "response",
                "requestId": request_id,
                "resultType": "success",
                "method": method,
                "handledByClientId": self.client_id,
                "result": result,
            }
        )

    # ─── internals ───

    async def _open_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open an IPC connection, using the appropriate transport per platform.

        - **Unix**: ``asyncio.open_unix_connection`` (Unix domain socket)
        - **Windows**: ``loop.create_pipe_connection`` (named pipe)

        ``create_pipe_connection`` is the standard asyncio abstraction that
        handles both, but ``open_unix_connection`` is used on Unix for clarity
        and broader event loop compatibility.
        """
        if sys.platform == "win32":
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            transport, _ = await loop.create_pipe_connection(
                lambda: protocol, str(self.socket_path),
            )
            writer = asyncio.StreamWriter(transport, protocol, reader)
            return reader, writer
        return await asyncio.open_unix_connection(self.socket_path)

    async def _ensure_connected(self) -> None:
        if self._connected.is_set():
            return
        connected = await self.wait_until_connected()
        if not connected:
            raise RuntimeError("not-connected")

    async def _send_message(self, payload: dict[str, Any]) -> None:
        writer = self._writer
        if writer is None:
            raise RuntimeError("not-connected")
        payload_bytes = json_dumps(payload)
        if len(payload_bytes) > IPC_MAX_FRAME_BYTES:
            logger.warning(
                "[codex-ipc] dropping message: payload too large "
                f"({len(payload_bytes)} > {IPC_MAX_FRAME_BYTES} bytes)"
            )
            return
        ipc_debug_log(
            "write frame",
            bytes=len(payload_bytes),
            summary=ipc_message_summary(payload),
            payload=payload if ipc_debug_full_enabled() else None,
        )
        async with self._write_lock:
            writer.write(len(payload_bytes).to_bytes(4, byteorder="little"))
            writer.write(payload_bytes)
            await writer.drain()
