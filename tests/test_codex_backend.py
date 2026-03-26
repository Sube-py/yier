from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

import yier_web.agent_backends.codex_backend as codex_backend_module
from yier_web.agent_backends.base import ChatSessionContext
from yier_web.agent_backends.codex_backend import (
    CodexAppServerBackend,
    CodexSessionRuntime,
    PendingApprovalState,
    TurnSnapshotState,
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


def test_codex_backend_build_ipc_turns_includes_streaming_assistant_item_from_snapshot() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
        backend_state={"thread_id": "thread-1"},
    )
    runtime = CodexSessionRuntime(session_id="session-1", thread_id="thread-1", status="active")
    runtime.turn_snapshots["turn-1"] = TurnSnapshotState(
        params={"threadId": "thread-1", "input": [{"type": "text", "text": "谢谢啊"}]},
        turn_started_at_ms=1000,
        final_assistant_started_at_ms=1200,
        assistant_item_id="assistant-1",
        assistant_text="不客气，今天也辛苦了。",
    )
    backend._runtimes["session-1"] = runtime

    payload = backend.build_ipc_turns(
        context,
        [
            {
                "id": "turn-1",
                "status": "inProgress",
                "items": [],
            }
        ],
    )

    assert payload == [
        {
            "id": "turn-1",
            "turnId": "turn-1",
            "status": "inProgress",
            "items": [
                {
                    "type": "agentMessage",
                    "id": "assistant-1",
                    "text": "不客气，今天也辛苦了。",
                    "phase": "final_answer",
                    "memoryCitation": None,
                }
            ],
            "turnStartedAtMs": 1000,
            "finalAssistantStartedAtMs": 1200,
            "params": {"threadId": "thread-1", "input": [{"type": "text", "text": "谢谢啊"}]},
        }
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


def test_codex_backend_start_turn_returns_native_turn_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        chat_service = SimpleNamespace(
            update_session_backend_state=lambda session_id, state: None,
            get_session_metadata=lambda session_id: {"codex_work_mode": "build"},
            config_service=SimpleNamespace(
                load_web_settings=lambda: WebSettings(
                    codex=StoredCodexSettings(sandbox="workspace-write")
                )
            ),
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
            client=SimpleNamespace(
                turn_start=lambda thread_id, input_payload, params: SimpleNamespace(
                    model_dump=lambda mode="json": {
                        "turn": {
                            "id": "turn-native-1",
                            "status": "inProgress",
                            "items": [],
                        }
                    }
                ),
            ),
            thread_id="thread-1",
        )

        async def fake_ensure_runtime(_context: ChatSessionContext) -> CodexSessionRuntime:
            return runtime

        monkeypatch.setattr(backend, "_ensure_runtime", fake_ensure_runtime)

        payload = await backend.start_turn(context, "Ship it")

        assert payload == {
            "turn": {
                "id": "turn-native-1",
                "status": "inProgress",
                "items": [],
            }
        }

    asyncio.run(scenario())


def test_codex_backend_load_thread_state_returns_native_thread_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_service = SimpleNamespace(
        update_session_backend_state=lambda session_id, state: None,
        get_session_metadata=lambda session_id: {"codex_work_mode": "build"},
    )
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )
    runtime = CodexSessionRuntime(session_id="session-1", client=object(), thread_id="thread-1")

    monkeypatch.setattr(backend, "_ensure_runtime_blocking", lambda _context: runtime)

    class FakeThreadHandle:
        def __init__(self, client: object, thread_id: str) -> None:
            assert thread_id == "thread-1"

        def read(self, include_turns: bool = False) -> SimpleNamespace:
            assert include_turns is True
            return SimpleNamespace(
                thread=SimpleNamespace(
                    status=SimpleNamespace(
                        root=SimpleNamespace(type="idle", active_flags=[])
                    ),
                    model_dump=lambda mode="json": {
                        "id": "thread-1",
                        "name": "Native thread",
                        "cwd": "/tmp/project",
                        "createdAt": 100,
                        "updatedAt": 101,
                        "turns": [
                            {
                                "id": "turn-native-1",
                                "status": "completed",
                                "items": [],
                            }
                        ],
                    },
                )
            )

    monkeypatch.setattr(codex_backend_module, "CodexThread", FakeThreadHandle)

    payload = backend.load_thread_state(context)

    assert payload["thread"]["id"] == "thread-1"
    assert payload["thread"]["turns"][0]["id"] == "turn-native-1"
    assert payload["threadRuntimeStatus"]["type"] == "idle"


def test_codex_backend_pending_conversation_requests_returns_raw_request_shape() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )
    backend._runtimes["session-1"] = CodexSessionRuntime(
        session_id="session-1",
        pending_requests={
            "req-1": PendingApprovalState(
                request_id="req-1",
                method="item/commandExecution/requestApproval",
                payload={"itemId": "cmd-1", "reason": "Need approval"},
                record={},
            )
        },
    )

    payload = backend.pending_conversation_requests(context)

    assert payload == [
        {
            "id": "req-1",
            "method": "item/commandExecution/requestApproval",
            "params": {"itemId": "cmd-1", "reason": "Need approval"},
        }
    ]


def test_codex_backend_build_ipc_turns_merges_snapshot_state() -> None:
    settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(load_web_settings=lambda: settings),
    )
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
        backend_state={"thread_id": "thread-1", "collaboration_mode": "build"},
    )
    backend._runtimes["session-1"] = CodexSessionRuntime(
        session_id="session-1",
        turn_snapshots={
            "turn-1": TurnSnapshotState(
                params={
                    "threadId": "thread-1",
                    "input": [{"type": "text", "text": "hello"}],
                    "cwd": "/tmp/project",
                },
                turn_started_at_ms=111,
                final_assistant_started_at_ms=222,
            )
        },
    )

    payload = backend.build_ipc_turns(
        context,
        [{"id": "turn-1", "status": "completed", "items": []}],
    )

    assert payload == [
        {
            "id": "turn-1",
            "turnId": "turn-1",
            "status": "completed",
            "items": [
                {
                    "type": "agentMessage",
                    "id": "turn-1:assistant",
                    "text": "",
                    "phase": "final_answer",
                    "memoryCitation": None,
                }
            ],
            "params": {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
                "cwd": "/tmp/project",
            },
            "turnStartedAtMs": 111,
            "finalAssistantStartedAtMs": 222,
        }
    ]


def test_codex_backend_build_ipc_turns_appends_active_snapshot_placeholder() -> None:
    settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(load_web_settings=lambda: settings),
    )
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
        backend_state={"thread_id": "thread-1"},
    )
    backend._runtimes["session-1"] = CodexSessionRuntime(
        session_id="session-1",
        status="active",
        turn_snapshots={
            "turn-live": TurnSnapshotState(
                params={
                    "threadId": "thread-1",
                    "input": [{"type": "text", "text": "hello"}],
                    "cwd": "/tmp/project",
                },
                turn_started_at_ms=111,
                final_assistant_started_at_ms=None,
            )
        },
    )

    payload = backend.build_ipc_turns(context, [])

    assert payload == [
        {
            "id": "turn-live",
            "turnId": "turn-live",
            "status": "inProgress",
            "items": [],
            "error": None,
            "params": {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
                "cwd": "/tmp/project",
            },
            "turnStartedAtMs": 111,
            "finalAssistantStartedAtMs": None,
        }
    ]


def test_codex_backend_build_turn_state_params_uses_object_collaboration_mode() -> None:
    settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(load_web_settings=lambda: settings),
    )
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
        backend_state={
            "thread_id": "thread-1",
            "collaboration_mode": {
                "mode": "default",
                "settings": {
                    "model": "gpt-5.2-codex",
                    "reasoning_effort": "medium",
                    "developer_instructions": None,
                },
            },
        },
    )

    payload = backend._build_turn_state_params(context, "hello")

    assert payload["collaborationMode"] == {
        "mode": "default",
        "settings": {
            "model": "gpt-5.2-codex",
            "reasoning_effort": "medium",
            "developer_instructions": None,
        },
    }


def test_codex_backend_thread_runtime_status_payload_normalizes_root_active_flags() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())

    payload = backend._thread_runtime_status_payload(
        {
            "root": {
                "type": "active",
                "active_flags": ["waitingOnApproval"],
            }
        },
        fallback_status="idle",
        fallback_active_flags=[],
    )

    assert payload == {
        "type": "active",
        "activeFlags": ["waitingOnApproval"],
    }
