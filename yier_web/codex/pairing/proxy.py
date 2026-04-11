from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import socket
import threading
import time
from typing import Any


DEFAULT_PROXY_LOG_PATH = Path("/tmp/yier-codex-pairing-proxy.jsonl")


def _read_exact(connection: socket.socket, byte_count: int) -> bytes:
    remaining = byte_count
    chunks: list[bytes] = []
    while remaining > 0:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ConnectionError("socket closed before the expected bytes arrived")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _decode_json_payload(payload: bytes) -> Any:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "raw_hex": payload.hex(),
        }


class PairingDescriptorSocketOverride:
    def __init__(self, descriptor_path: Path, proxy_socket_path: Path) -> None:
        self.descriptor_path = descriptor_path
        self.proxy_socket_path = proxy_socket_path
        self._original_text: str | None = None

    def apply(self) -> None:
        payload = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(
                f"Descriptor at '{self.descriptor_path}' must contain a JSON object."
            )

        self._original_text = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
        payload["socketPath"] = str(self.proxy_socket_path)
        self.descriptor_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def restore(self) -> None:
        if self._original_text is None:
            return
        self.descriptor_path.write_text(self._original_text, encoding="utf-8")
        self._original_text = None


class CodexPairingProxyServer:
    def __init__(
        self,
        *,
        upstream_socket_path: Path,
        proxy_socket_path: Path,
        log_path: Path,
    ) -> None:
        self.upstream_socket_path = upstream_socket_path
        self.proxy_socket_path = proxy_socket_path
        self.log_path = log_path
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._server: socket.socket | None = None
        self._worker_threads: set[threading.Thread] = set()
        self._next_connection_id = 1

    def serve_forever(self) -> None:
        if self.proxy_socket_path.exists():
            self.proxy_socket_path.unlink()

        self.proxy_socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            self._server = server
            server.settimeout(0.2)
            server.bind(str(self.proxy_socket_path))
            server.listen()

            self._append_log(
                {
                    "event": "proxy_started",
                    "proxy_socket_path": str(self.proxy_socket_path),
                    "upstream_socket_path": str(self.upstream_socket_path),
                }
            )

            while not self._stop_event.is_set():
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise

                worker = threading.Thread(
                    target=self._handle_connection,
                    args=(connection,),
                    daemon=True,
                )
                with self._lock:
                    self._worker_threads.add(worker)
                worker.start()

        self._cleanup_proxy_socket()

    def stop(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass

        with self._lock:
            workers = list(self._worker_threads)
        for worker in workers:
            worker.join(timeout=1)

        self._cleanup_proxy_socket()
        self._append_log(
            {
                "event": "proxy_stopped",
                "proxy_socket_path": str(self.proxy_socket_path),
            }
        )

    def _handle_connection(self, client_connection: socket.socket) -> None:
        connection_id = self._allocate_connection_id()
        started_at = time.time()
        request_payload: Any = None
        response_payload: Any = None

        try:
            with client_connection:
                request_length = int.from_bytes(
                    _read_exact(client_connection, 4),
                    byteorder="little",
                )
                request_bytes = _read_exact(client_connection, request_length)
                request_payload = _decode_json_payload(request_bytes)

                with socket.socket(
                    socket.AF_UNIX, socket.SOCK_STREAM
                ) as upstream_connection:
                    upstream_connection.connect(str(self.upstream_socket_path))
                    upstream_connection.sendall(
                        request_length.to_bytes(4, byteorder="little")
                    )
                    upstream_connection.sendall(request_bytes)

                    response_length = int.from_bytes(
                        _read_exact(upstream_connection, 4),
                        byteorder="little",
                    )
                    response_bytes = _read_exact(upstream_connection, response_length)
                    response_payload = _decode_json_payload(response_bytes)

                client_connection.sendall(
                    response_length.to_bytes(4, byteorder="little")
                )
                client_connection.sendall(response_bytes)

                self._append_log(
                    {
                        "event": "exchange",
                        "connection_id": connection_id,
                        "duration_ms": round((time.time() - started_at) * 1000, 3),
                        "request": request_payload,
                        "response": response_payload,
                    }
                )
        except Exception as exc:
            self._append_log(
                {
                    "event": "exchange_error",
                    "connection_id": connection_id,
                    "duration_ms": round((time.time() - started_at) * 1000, 3),
                    "request": request_payload,
                    "response": response_payload,
                    "error": str(exc),
                }
            )
        finally:
            with self._lock:
                current = threading.current_thread()
                if current in self._worker_threads:
                    self._worker_threads.remove(current)

    def _allocate_connection_id(self) -> int:
        with self._lock:
            connection_id = self._next_connection_id
            self._next_connection_id += 1
            return connection_id

    def _append_log(self, payload: dict[str, Any]) -> None:
        entry = {
            "timestamp": time.time(),
            **payload,
        }
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")

    def _cleanup_proxy_socket(self) -> None:
        if self.proxy_socket_path.exists():
            self.proxy_socket_path.unlink()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Proxy a Codex paired-editor UNIX socket and log every length-prefixed JSON exchange."
        )
    )
    parser.add_argument(
        "--upstream-socket-path",
        required=True,
        help="The real paired-editor UNIX socket path to forward requests to.",
    )
    parser.add_argument(
        "--proxy-socket-path",
        required=True,
        help="The proxy UNIX socket path that desktop app requests should connect to.",
    )
    parser.add_argument(
        "--log-path",
        default=str(DEFAULT_PROXY_LOG_PATH),
        help="Where to write JSONL logs for proxied request and response payloads.",
    )
    parser.add_argument(
        "--descriptor-path",
        help="Optional app_pairing_extensions descriptor file to rewrite to the proxy socket path.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    upstream_socket_path = Path(args.upstream_socket_path).expanduser().resolve()
    proxy_socket_path = Path(args.proxy_socket_path).expanduser()
    log_path = Path(args.log_path).expanduser()

    descriptor_override: PairingDescriptorSocketOverride | None = None
    if args.descriptor_path:
        descriptor_override = PairingDescriptorSocketOverride(
            Path(args.descriptor_path).expanduser(),
            proxy_socket_path,
        )
        descriptor_override.apply()

    proxy = CodexPairingProxyServer(
        upstream_socket_path=upstream_socket_path,
        proxy_socket_path=proxy_socket_path,
        log_path=log_path,
    )

    stop_once = threading.Event()

    def _stop_proxy(_signum: int, _frame: Any) -> None:
        if stop_once.is_set():
            return
        stop_once.set()
        proxy.stop()

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _stop_proxy)
    signal.signal(signal.SIGTERM, _stop_proxy)

    try:
        proxy.serve_forever()
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        proxy.stop()
        if descriptor_override is not None:
            descriptor_override.restore()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
