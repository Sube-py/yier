from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from yier_web.agent_backends.base import ChatSessionContext
from yier_web.agent_backends.codex_backend import (
    CodexAppServerBackend,
    CodexSessionRuntime,
    _codex_thread_sandbox_mode,
    _codex_turn_sandbox_policy_type,
)
from yier_web.schemas import StoredCodexSettings, WebSettings


@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        ("read-only", "read-only"),
        ("workspace-write", "workspace-write"),
        ("danger-full-access", "danger-full-access"),
        ("readOnly", "read-only"),
        ("workspaceWrite", "workspace-write"),
        ("dangerFullAccess", "danger-full-access"),
    ],
)
def test_codex_thread_sandbox_mode_normalizes_supported_values(incoming: str, expected: str) -> None:
    assert _codex_thread_sandbox_mode(incoming) == expected


@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        ("read-only", "readOnly"),
        ("workspace-write", "workspaceWrite"),
        ("danger-full-access", "dangerFullAccess"),
        ("readOnly", "readOnly"),
        ("workspaceWrite", "workspaceWrite"),
        ("dangerFullAccess", "dangerFullAccess"),
        ("externalSandbox", "externalSandbox"),
    ],
)
def test_codex_turn_sandbox_policy_type_normalizes_supported_values(
    incoming: str, expected: str
) -> None:
    assert _codex_turn_sandbox_policy_type(incoming) == expected


def test_codex_sandbox_helpers_reject_unknown_values() -> None:
    with pytest.raises(ValueError, match="Unsupported Codex thread sandbox mode"):
        _codex_thread_sandbox_mode("workspace-write-now")

    with pytest.raises(ValueError, match="Unsupported Codex turn sandbox policy type"):
        _codex_turn_sandbox_policy_type("workspace-write-now")


def test_codex_backend_uses_normalized_sandbox_in_thread_and_turn_params() -> None:
    settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(load_web_settings=lambda: settings)
    )
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )

    thread_params = backend._thread_params(context)
    turn_params = backend._turn_params(context)

    assert thread_params["sandbox"] == "workspace-write"
    assert turn_params["sandbox_policy"] == {"type": "workspaceWrite"}
    assert thread_params["approval_policy"] == "on-request"
    assert turn_params["approval_policy"] == "on-request"


def test_codex_backend_injects_pairing_mcp_server_when_paths_are_available() -> None:
    project_root = Path("/tmp/yier-project")
    home_dir = Path("/tmp/yier-home")
    settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(
            load_web_settings=lambda: settings,
            project_root=project_root,
            home_dir=home_dir,
        )
    )
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )

    thread_params = backend._thread_params(context)

    assert thread_params["config"] == {
        "mcp_servers": {
            "yier_codex_pairing": {
                "command": sys.executable,
                "args": ["-m", "yier_web.codex_pairing_mcp"],
                "cwd": str(project_root.resolve()),
                "env": {
                    "YIER_PAIRING_HOME_DIR": str(home_dir.resolve()),
                },
                "startup_timeout_sec": 5,
                "tool_timeout_sec": 30,
            }
        }
    }


def test_codex_backend_treats_elicitation_create_as_mcp_elicitation() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())

    assert backend._approval_kind("elicitation/create") == "mcp_elicitation"
    assert backend._build_approval_response(
        "elicitation/create",
        {},
        "accept",
        {"confirmed": True},
    ) == {"action": "accept", "content": {"confirmed": True}}


def test_codex_backend_normalizes_raw_elicitation_create_payload() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())

    payload = backend._approval_payload(
        "elicitation/create",
        {
            "message": "Allow this request?",
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "confirmed": {"type": "boolean"},
                },
                "required": ["confirmed"],
            },
        },
    )

    assert payload["request"] == {
        "mode": "form",
        "message": "Allow this request?",
        "requestedSchema": {
            "type": "object",
            "properties": {
                "confirmed": {"type": "boolean"},
            },
            "required": ["confirmed"],
        },
    }


def test_codex_backend_bootstrap_session_returns_native_thread_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(load_web_settings=lambda: settings)
    )
    backend = CodexAppServerBackend(chat_service)

    monkeypatch.setattr(backend, "_start_client", lambda runtime, context: object())

    def fake_start_thread(
        runtime,
        context,
        *,
        persist: bool = True,
    ) -> None:
        assert persist is False
        runtime.thread_id = "thread-native-1"
        runtime.status = "idle"
        runtime.active_flags = ["interactive"]
        runtime.detail = "Ready"

    monkeypatch.setattr(backend, "_start_thread_blocking", fake_start_thread)

    payload = backend.bootstrap_session(Path("/tmp/project"))

    assert payload == {
        "thread_id": "thread-native-1",
        "status": "idle",
        "active_flags": ["interactive"],
        "detail": "Ready",
    }
    assert "thread-native-1" in backend._runtimes
    assert backend._runtimes["thread-native-1"].session_id == "thread-native-1"


def test_codex_backend_reasoning_notifications_keep_item_identity_and_accumulate_content() -> None:
    chat_service = SimpleNamespace(update_session_backend_state=lambda session_id, state: None)
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )
    runtime = CodexSessionRuntime(session_id="session-1")
    emitted: list[tuple[str, dict[str, object]]] = []

    backend._emit_from_thread = lambda runtime, event, data: emitted.append((event, data))  # type: ignore[method-assign]

    backend._handle_turn_notification(
        runtime,
        context,
        SimpleNamespace(
            method="item/reasoning/textDelta",
            payload=SimpleNamespace(item_id="reasoning-1", delta="Inspect", turn_id="turn-1"),
        ),
    )
    backend._handle_turn_notification(
        runtime,
        context,
        SimpleNamespace(
            method="item/reasoning/textDelta",
            payload=SimpleNamespace(item_id="reasoning-1", delta=" files", turn_id="turn-1"),
        ),
    )

    assert emitted == [
        (
            "reasoning",
            {
                "session_id": "session-1",
                "item_id": "reasoning-1",
                "content": "Inspect",
                "iteration": 0,
            },
        ),
        (
            "reasoning",
            {
                "session_id": "session-1",
                "item_id": "reasoning-1",
                "content": "Inspect files",
                "iteration": 0,
            },
        ),
    ]


def test_codex_backend_emits_turn_completed_for_completed_turn() -> None:
    chat_service = SimpleNamespace(update_session_backend_state=lambda session_id, state: None)
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )
    runtime = CodexSessionRuntime(session_id="session-1", thread_id="thread-1")
    emitted: list[tuple[str, dict[str, object]]] = []

    backend._emit_from_thread = lambda runtime, event, data: emitted.append((event, data))  # type: ignore[method-assign]

    finish_reason = backend._handle_turn_completed(
        runtime,
        context,
        SimpleNamespace(
            payload=SimpleNamespace(
                turn=SimpleNamespace(id="turn-1", status="completed", error=None),
            )
        ),
    )

    assert finish_reason == "stop"
    assert emitted == [
        (
            "turn_completed",
            {
                "session_id": "session-1",
                "turn_id": "turn-1",
                "status": "completed",
            },
        )
    ]


def test_codex_backend_emits_turn_aborted_for_interrupted_turn() -> None:
    chat_service = SimpleNamespace(update_session_backend_state=lambda session_id, state: None)
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )
    runtime = CodexSessionRuntime(session_id="session-1", thread_id="thread-1")
    emitted: list[tuple[str, dict[str, object]]] = []

    backend._emit_from_thread = lambda runtime, event, data: emitted.append((event, data))  # type: ignore[method-assign]

    finish_reason = backend._handle_turn_completed(
        runtime,
        context,
        SimpleNamespace(
            payload=SimpleNamespace(
                turn=SimpleNamespace(id="turn-2", status="interrupted", error=None),
            )
        ),
    )

    assert finish_reason == "interrupted"
    assert emitted == [
        (
            "turn_aborted",
            {
                "session_id": "session-1",
                "turn_id": "turn-2",
                "status": "interrupted",
                "reason": "Turn interrupted.",
            },
        )
    ]


def test_codex_backend_emits_stream_error_for_failed_turn() -> None:
    chat_service = SimpleNamespace(update_session_backend_state=lambda session_id, state: None)
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )
    runtime = CodexSessionRuntime(session_id="session-1", thread_id="thread-1")
    emitted: list[tuple[str, dict[str, object]]] = []

    backend._emit_from_thread = lambda runtime, event, data: emitted.append((event, data))  # type: ignore[method-assign]

    finish_reason = backend._handle_turn_completed(
        runtime,
        context,
        SimpleNamespace(
            payload=SimpleNamespace(
                turn=SimpleNamespace(
                    id="turn-3",
                    status="failed",
                    error=SimpleNamespace(message="stream exploded", code="sandboxError"),
                ),
            )
        ),
    )

    assert finish_reason == "error"
    assert emitted == [
        (
            "stream_error",
            {
                "session_id": "session-1",
                "message": "stream exploded",
                "thread_id": "thread-1",
                "turn_id": "turn-3",
                "code": "sandboxError",
                "will_retry": False,
            },
        )
    ]


def test_codex_backend_emits_stream_error_for_realtime_notification() -> None:
    chat_service = SimpleNamespace(update_session_backend_state=lambda session_id, state: None)
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )
    runtime = CodexSessionRuntime(session_id="session-1", thread_id="thread-1")
    emitted: list[tuple[str, dict[str, object]]] = []

    backend._emit_from_thread = lambda runtime, event, data: emitted.append((event, data))  # type: ignore[method-assign]

    backend._handle_turn_notification(
        runtime,
        context,
        SimpleNamespace(
            method="thread/realtime/error",
            payload=SimpleNamespace(message="socket closed", thread_id="thread-1"),
        ),
    )

    assert emitted == [
        (
            "stream_error",
            {
                "session_id": "session-1",
                "message": "socket closed",
                "thread_id": "thread-1",
                "turn_id": None,
                "code": None,
                "will_retry": False,
            },
        )
    ]


def test_codex_backend_includes_item_id_when_finalizing_agent_message() -> None:
    chat_service = SimpleNamespace(
        _append_transcript_message=lambda session_id, message: None,
    )
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )
    runtime = CodexSessionRuntime(
        session_id="session-1",
        assistant_buffers={"msg-1": "hello"},
    )
    emitted: list[tuple[str, dict[str, object]]] = []

    backend._emit_from_thread = lambda runtime, event, data: emitted.append((event, data))  # type: ignore[method-assign]

    backend._handle_item_completed(
        runtime,
        context,
        SimpleNamespace(type="agentMessage", id="msg-1", text=""),
    )

    assert emitted == [
        (
            "assistant_message",
            {
                "session_id": "session-1",
                "item_id": "msg-1",
                "content": "hello",
                "iteration": 0,
            },
        )
    ]
