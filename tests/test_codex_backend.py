from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from codex_app_server import AppServerConfig
from codex_app_server.generated.v2_all import (
    ImageGenerationThreadItem,
    ImageViewThreadItem,
    LocalImageUserInput,
    MentionUserInput,
    PlanThreadItem,
    SkillUserInput,
    TextUserInput,
    UserMessageThreadItem,
)

from yier_web.agent_backends.base import ChatSessionContext
import yier_web.codex.backend as codex_backend_module
from yier_web.codex.backend import (
    CodexAppServerBackend,
    _codex_thread_sandbox_mode,
    _codex_turn_sandbox_policy_type,
)
from yier_web.codex.runtime import CodexSessionRuntime, PendingApprovalState, TurnSnapshotState
from yier_web.codex.sdk.client import ApprovalAwareAppServerClient
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
                "args": ["-m", "yier_web.codex.pairing.mcp"],
                "cwd": str(project_root.resolve()),
                "env": {
                    "YIER_PAIRING_HOME_DIR": str(home_dir.resolve()),
                },
                "startup_timeout_sec": 5,
                "tool_timeout_sec": 30,
            }
        }
    }


def test_approval_aware_async_client_routes_sync_server_requests_to_callback() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    client = ApprovalAwareAppServerClient(
        config=AppServerConfig(),
        approval_callback=lambda request_id, method, params: calls.append(
            (request_id, method, params)
        )
        or {"decision": "decline"},
    )

    response = client._sync._handle_server_request(
        {
            "id": "request-1",
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "pwd"},
        }
    )

    assert response == {"decision": "decline"}
    assert calls == [
        (
            "request-1",
            "item/commandExecution/requestApproval",
            {"command": "pwd"},
        )
    ]


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
    async def scenario() -> None:
        settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
        chat_service = SimpleNamespace(
            config_service=SimpleNamespace(load_web_settings=lambda: settings)
        )
        backend = CodexAppServerBackend(chat_service)

        async def fake_start_client(runtime, context):
            return object()

        async def fake_start_thread(
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

        monkeypatch.setattr(backend, "_start_client", fake_start_client)
        monkeypatch.setattr(backend, "_start_thread", fake_start_thread)

        payload = await backend.bootstrap_session(Path("/tmp/project"))

        assert payload == {
            "thread_id": "thread-native-1",
            "status": "idle",
            "active_flags": ["interactive"],
            "detail": "Ready",
        }
        assert "thread-native-1" in backend._runtimes
        assert backend._runtimes["thread-native-1"].session_id == "thread-native-1"

    asyncio.run(scenario())


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


def test_codex_backend_emits_assistant_delta_for_realtime_transcript_updates() -> None:
    chat_service = SimpleNamespace(update_session_backend_state=lambda session_id, state: None)
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )
    runtime = CodexSessionRuntime(session_id="session-1", streaming_turn_id="turn-1")
    runtime.turn_snapshots["turn-1"] = TurnSnapshotState(params={})
    emitted: list[tuple[str, dict[str, object]]] = []

    backend._emit_from_thread = lambda runtime, event, data: emitted.append((event, data))  # type: ignore[method-assign]

    backend._handle_turn_notification(
        runtime,
        context,
        SimpleNamespace(
            method="thread/realtime/transcriptUpdated",
            payload=SimpleNamespace(role="assistant", text="Hello", thread_id="thread-1"),
        ),
    )
    backend._handle_turn_notification(
        runtime,
        context,
        SimpleNamespace(
            method="thread/realtime/transcriptUpdated",
            payload=SimpleNamespace(
                role="assistant",
                text="Hello there",
                thread_id="thread-1",
            ),
        ),
    )

    assert emitted == [
        (
            "assistant_delta",
            {
                "session_id": "session-1",
                "item_id": "turn-1:assistant-transcript",
                "delta": "Hello",
            },
        ),
        (
            "assistant_delta",
            {
                "session_id": "session-1",
                "item_id": "turn-1:assistant-transcript",
                "delta": " there",
            },
        ),
    ]
    assert runtime.turn_snapshots["turn-1"].assistant_item_id == "turn-1:assistant-transcript"
    assert runtime.turn_snapshots["turn-1"].assistant_text == "Hello there"


def test_codex_backend_finalizes_transcript_backed_assistant_message_with_stable_item_id() -> None:
    chat_service = SimpleNamespace(
        update_session_backend_state=lambda session_id, state: None,
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
    runtime = CodexSessionRuntime(session_id="session-1")
    runtime.turn_snapshots["turn-1"] = TurnSnapshotState(
        params={},
        assistant_item_id="turn-1:assistant-transcript",
        assistant_text="Hello there",
    )
    runtime.assistant_buffers["turn-1:assistant-transcript"] = "Hello there"
    emitted: list[tuple[str, dict[str, object]]] = []

    backend._emit_from_thread = lambda runtime, event, data: emitted.append((event, data))  # type: ignore[method-assign]

    backend._handle_item_completed(
        runtime,
        context,
        SimpleNamespace(type="agentMessage", id="msg-1", text="Hello there"),
        turn_id="turn-1",
    )

    assert emitted == [
        (
            "assistant_message",
            {
                "session_id": "session-1",
                "item_id": "turn-1:assistant-transcript",
                "content": "Hello there",
                "iteration": 0,
            },
        )
    ]
    assert runtime.turn_snapshots["turn-1"].assistant_item_id == "turn-1:assistant-transcript"


def test_codex_backend_flushes_stream_emits_between_notifications() -> None:
    async def scenario() -> None:
        chat_service = SimpleNamespace(
            update_session_backend_state=lambda session_id, state: None,
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
        emitted: list[tuple[str, dict[str, object]]] = []
        observed_after_delta: list[list[tuple[str, dict[str, object]]]] = []

        async def emit(event: str, data: dict[str, object]) -> None:
            await asyncio.sleep(0)
            emitted.append((event, data))

        class FakeTurnHandle:
            async def stream(self):
                yield SimpleNamespace(
                    method="item/agentMessage/delta",
                    payload=SimpleNamespace(
                        item_id="msg-1",
                        delta="Hello",
                        turn_id="turn-1",
                    ),
                )
                observed_after_delta.append(list(emitted))
                yield SimpleNamespace(
                    method="turn/completed",
                    payload=SimpleNamespace(
                        turn=SimpleNamespace(
                            id="turn-1",
                            status="completed",
                            error=None,
                        ),
                    ),
                )

        runtime = CodexSessionRuntime(
            session_id="session-1",
            client=object(),
            thread_id="thread-1",
            loop=asyncio.get_running_loop(),
            emit=emit,
        )
        runtime.turn_handles["turn-1"] = FakeTurnHandle()
        runtime.turn_snapshots["turn-1"] = TurnSnapshotState(params={})

        finish_reason = await backend._consume_turn_stream(
            runtime,
            context,
            "turn-1",
        )

        assert finish_reason == "stop"
        assert observed_after_delta == [
            [
                (
                    "assistant_delta",
                    {
                        "session_id": "session-1",
                        "item_id": "msg-1",
                        "delta": "Hello",
                    },
                )
            ]
        ]
        assert emitted[-1] == (
            "turn_completed",
            {
                "session_id": "session-1",
                "turn_id": "turn-1",
                "status": "completed",
            },
        )

    asyncio.run(scenario())


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
            "error": None,
            "diff": None,
            "turnStartedAtMs": 1000,
            "finalAssistantStartedAtMs": 1200,
            "params": {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "谢谢啊"}],
            },
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


def test_codex_backend_thread_user_message_text_renders_typed_non_text_inputs() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())

    content = backend._thread_user_message_text(
        [
            TextUserInput(type="text", text="请看一下", text_elements=[]),
            LocalImageUserInput(type="localImage", path="/tmp/screenshot.png"),
            SkillUserInput(type="skill", name="Code", path="/skills/code"),
            MentionUserInput(type="mention", name="Docs", path="app://docs"),
        ]
    )

    assert content == (
        "请看一下\n"
        "[Local image] /tmp/screenshot.png\n"
        "[Skill] Code (/skills/code)\n"
        "[Mention] Docs (app://docs)"
    )


def test_codex_backend_turn_input_items_from_thread_items_preserves_typed_user_inputs() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())

    items = backend._turn_input_items_from_thread_items(
        [
            UserMessageThreadItem(
                type="userMessage",
                id="user-1",
                content=[
                    TextUserInput(type="text", text="hello", text_elements=[]),
                    LocalImageUserInput(type="localImage", path="/tmp/image.png"),
                    SkillUserInput(type="skill", name="Code", path="/skills/code"),
                    MentionUserInput(type="mention", name="Docs", path="app://docs"),
                ],
            )
        ]
    )

    assert items == [
        {"type": "text", "text": "hello", "text_elements": []},
        {"type": "localImage", "path": "/tmp/image.png"},
        {"type": "skill", "name": "Code", "path": "/skills/code"},
        {"type": "mention", "name": "Docs", "path": "app://docs"},
    ]


def test_codex_backend_thread_view_payload_includes_image_and_review_related_items() -> None:
    backend = CodexAppServerBackend(
        SimpleNamespace(register_local_image_preview=lambda *_args, **_kwargs: None)
    )
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
    )

    activity_events: list[dict[str, object]] = []
    messages = []
    activity_counter = [0]
    sequence_counter = [0]

    backend._append_thread_item_view(
        context,
        ImageViewThreadItem(type="imageView", id="image-view-1", path="/tmp/screenshot.png"),
        messages,
        activity_events,
        activity_counter,
        sequence_counter,
    )
    backend._append_thread_item_view(
        context,
        ImageGenerationThreadItem(
            type="imageGeneration",
            id="image-gen-1",
            result="image generated",
            revised_prompt="draw a skyline",
            saved_path="/tmp/generated.png",
            status="completed",
        ),
        messages,
        activity_events,
        activity_counter,
        sequence_counter,
    )

    assert messages == []
    assert [event["event"] for event in activity_events] == [
        "tool_call_start",
        "tool_call_end",
        "tool_call_start",
        "tool_call_end",
    ]
    assert activity_events[0]["data"]["tool_name"] == "image_view"
    assert activity_events[1]["data"]["result"] == "Viewed image: /tmp/screenshot.png"
    assert activity_events[2]["data"]["tool_name"] == "image_generation"
    assert activity_events[3]["data"]["result"] == "Generated image: /tmp/generated.png"


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
    async def scenario() -> None:
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

        async def fake_ensure_runtime(_context: ChatSessionContext) -> CodexSessionRuntime:
            return runtime

        monkeypatch.setattr(backend, "_ensure_runtime", fake_ensure_runtime)

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

        payload = await backend.load_thread_state(context)

        assert payload["thread"]["id"] == "thread-1"
        assert payload["thread"]["turns"][0]["id"] == "turn-native-1"
        assert payload["threadRuntimeStatus"]["type"] == "idle"

    asyncio.run(scenario())


def test_codex_backend_load_thread_state_uses_cached_thread_while_streaming_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
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
        runtime = CodexSessionRuntime(
            session_id="session-1",
            client=object(),
            thread_id="thread-1",
            status="active",
            streaming_turn_id="turn-live",
            thread_state_cache={
                "id": "thread-1",
                "status": {"type": "active", "activeFlags": []},
                "turns": [
                    {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                    },
                    {
                        "id": "turn-live",
                        "status": "inProgress",
                        "items": [],
                    },
                ],
            },
        )

        async def fake_ensure_runtime(_context: ChatSessionContext) -> CodexSessionRuntime:
            return runtime

        monkeypatch.setattr(backend, "_ensure_runtime", fake_ensure_runtime)

        class FakeThreadHandle:
            def __init__(self, client: object, thread_id: str) -> None:
                raise AssertionError("load_thread_state should not read native thread during active streaming")

        monkeypatch.setattr(codex_backend_module, "CodexThread", FakeThreadHandle)

        payload = await backend.load_thread_state(context)

        assert payload["thread"]["id"] == "thread-1"
        assert payload["thread"]["turns"][-1]["id"] == "turn-live"
        assert payload["threadRuntimeStatus"]["type"] == "active"

    asyncio.run(scenario())


def test_codex_backend_start_turn_updates_cached_thread_turns() -> None:
    async def scenario() -> None:
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

        class FakeClient:
            def turn_start(self, thread_id: str, turn_input: object, params: dict[str, object]) -> SimpleNamespace:
                assert thread_id == "thread-1"
                assert turn_input == "hello"
                assert params == {}
                return SimpleNamespace(turn=SimpleNamespace(id="turn-live"))

        runtime = CodexSessionRuntime(
            session_id="session-1",
            client=FakeClient(),
            thread_id="thread-1",
            thread_state_cache={
                "id": "thread-1",
                "turns": [
                    {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                    }
                ],
            },
        )

        backend._normalize_turn_input_payload = lambda context, payload: payload  # type: ignore[method-assign]
        backend._turn_params = lambda context: {}  # type: ignore[method-assign]
        backend._build_turn_state_params = lambda context, turn_input: {  # type: ignore[method-assign]
            "threadId": "thread-1",
            "input": [{"type": "text", "text": "hello"}],
            "cwd": "/tmp/project",
        }

        await backend._start_turn(runtime, context, "hello")

        assert runtime.thread_state_cache is not None
        assert runtime.thread_state_cache["turns"][-1]["id"] == "turn-live"
        assert runtime.thread_state_cache["turns"][-1]["status"] == "inProgress"

    asyncio.run(scenario())


def test_codex_backend_handle_turn_completed_updates_cached_thread_turn_status() -> None:
    chat_service = SimpleNamespace(update_session_backend_state=lambda session_id, state: None)
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
        thread_id="thread-1",
        thread_state_cache={
            "id": "thread-1",
            "turns": [
                {
                    "id": "turn-live",
                    "status": "inProgress",
                    "items": [],
                }
            ],
        },
    )
    backend._emit_from_thread = lambda runtime, event, data: None  # type: ignore[method-assign]

    backend._handle_turn_completed(
        runtime,
        context,
        SimpleNamespace(
            payload=SimpleNamespace(
                turn=SimpleNamespace(id="turn-live", status="completed", error=None),
            )
        ),
    )

    assert runtime.thread_state_cache is not None
    assert runtime.thread_state_cache["turns"][-1]["status"] == "completed"


def test_codex_backend_notification_belongs_to_turn_reads_turn_payload_id() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())

    turn_started = SimpleNamespace(
        method="turn/started",
        payload=SimpleNamespace(thread_id="thread-1", turn=SimpleNamespace(id="turn-live")),
    )
    turn_completed = SimpleNamespace(
        method="turn/completed",
        payload=SimpleNamespace(thread_id="thread-1", turn=SimpleNamespace(id="turn-live")),
    )

    assert backend._notification_belongs_to_turn(turn_started, "turn-live") is True
    assert backend._notification_belongs_to_turn(turn_completed, "turn-live") is True
    assert backend._notification_belongs_to_turn(turn_completed, "turn-other") is False


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
        get_session_metadata=lambda session_id: {"codex_work_mode": "plan"},
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
            "error": None,
            "diff": None,
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
        get_session_metadata=lambda session_id: {"codex_work_mode": "plan"},
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
            "diff": None,
            "params": {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
                "cwd": "/tmp/project",
            },
            "turnStartedAtMs": 111,
            "finalAssistantStartedAtMs": None,
        }
    ]


def test_codex_backend_build_ipc_turns_merges_active_snapshot_into_last_fallback_turn() -> None:
    settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(load_web_settings=lambda: settings),
        get_session_metadata=lambda session_id: {"codex_work_mode": "plan"},
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
                final_assistant_started_at_ms=222,
                assistant_item_id="assistant-1",
                assistant_text="world",
            )
        },
    )

    payload = backend.build_ipc_turns(
        context,
        [
            {
                "id": "session-1:turn:1",
                "turnId": "session-1:turn:1",
                "status": "inProgress",
                "error": None,
                "diff": None,
                "items": [
                    {
                        "id": "session-1:turn:1:user",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": "hello", "text_elements": []}],
                    }
                ],
                "params": {
                    "threadId": "thread-1",
                    "input": [{"type": "text", "text": "hello"}],
                    "cwd": "/tmp/project",
                },
                "turnStartedAtMs": 100,
                "finalAssistantStartedAtMs": None,
            }
        ],
    )

    assert payload == [
        {
            "id": "turn-live",
            "turnId": "turn-live",
            "status": "inProgress",
            "error": None,
            "diff": None,
            "items": [
                {
                    "id": "session-1:turn:1:user",
                    "type": "userMessage",
                    "content": [{"type": "text", "text": "hello", "text_elements": []}],
                },
                {
                    "type": "agentMessage",
                    "id": "assistant-1",
                    "text": "world",
                    "phase": "final_answer",
                    "memoryCitation": None,
                },
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


def test_codex_backend_thread_token_usage_update_persists_backend_state() -> None:
    updated_states: list[tuple[str, dict[str, object]]] = []
    chat_service = SimpleNamespace(
        update_session_backend_state=lambda session_id, state: updated_states.append((session_id, state)),
    )
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

    backend._handle_thread_notification(
        runtime,
        context,
        SimpleNamespace(
            method="thread/tokenUsage/updated",
            payload=SimpleNamespace(
                token_usage=SimpleNamespace(
                    total=SimpleNamespace(total_tokens=10, input_tokens=8, cached_input_tokens=2, output_tokens=2, reasoning_output_tokens=0),
                    last=SimpleNamespace(total_tokens=4, input_tokens=3, cached_input_tokens=1, output_tokens=1, reasoning_output_tokens=0),
                    model_context_window=1000,
                ),
            ),
        ),
    )

    assert updated_states == [
        (
            "session-1",
            {
                "latest_token_usage_info": {
                    "total": {
                        "total_tokens": 10,
                        "input_tokens": 8,
                        "cached_input_tokens": 2,
                        "output_tokens": 2,
                        "reasoning_output_tokens": 0,
                    },
                    "last": {
                        "total_tokens": 4,
                        "input_tokens": 3,
                        "cached_input_tokens": 1,
                        "output_tokens": 1,
                        "reasoning_output_tokens": 0,
                    },
                    "model_context_window": 1000,
                }
            },
        )
    ]
    assert emitted == [
        (
            "token_usage_updated",
            {
                "session_id": "session-1",
                "token_usage": {
                    "total": {
                        "total_tokens": 10,
                        "input_tokens": 8,
                        "cached_input_tokens": 2,
                        "output_tokens": 2,
                        "reasoning_output_tokens": 0,
                    },
                    "last": {
                        "total_tokens": 4,
                        "input_tokens": 3,
                        "cached_input_tokens": 1,
                        "output_tokens": 1,
                        "reasoning_output_tokens": 0,
                    },
                    "model_context_window": 1000,
                },
            },
        )
    ]


def test_codex_backend_build_turn_state_params_uses_object_collaboration_mode() -> None:
    settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(load_web_settings=lambda: settings),
        get_session_metadata=lambda session_id: {"codex_work_mode": "plan"},
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


def test_codex_backend_turn_params_include_collaboration_mode() -> None:
    settings = WebSettings(
        codex=StoredCodexSettings(
            sandbox="workspace-write",
            model="gpt-5.4",
            reasoning_effort="high",
        )
    )
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(load_web_settings=lambda: settings),
        get_session_metadata=lambda session_id: {"codex_work_mode": "plan"},
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
                "mode": "plan",
                "settings": {
                    "model": "gpt-5.4",
                    "reasoning_effort": "medium",
                    "developer_instructions": None,
                },
            },
        },
    )

    params = backend._turn_params(context)
    thread_params = backend._thread_params(context)

    assert params["collaboration_mode"] == {
        "mode": "plan",
        "settings": {
            "model": "gpt-5.4",
            "reasoning_effort": "medium",
            "developer_instructions": None,
        },
    }
    assert thread_params["sandbox"] == "read-only"
    assert params["sandbox_policy"] == {"type": "readOnly"}


def test_codex_backend_turn_params_fill_blank_model_from_defaults() -> None:
    settings = WebSettings(
        codex=StoredCodexSettings(
            sandbox="workspace-write",
            model="",
            reasoning_effort="medium",
        )
    )
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
                "mode": "plan",
                "settings": {
                    "model": "",
                    "reasoning_effort": "medium",
                    "developer_instructions": None,
                },
            },
        },
    )

    params = backend._turn_params(context)

    assert params["model"] == "gpt-5.4"
    assert params["collaboration_mode"] == {
        "mode": "plan",
        "settings": {
            "model": "gpt-5.4",
            "reasoning_effort": "medium",
            "developer_instructions": None,
        },
    }


def test_codex_backend_build_turn_state_params_normalizes_legacy_build_mode() -> None:
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
            "collaboration_mode": "build",
        },
    )

    payload = backend._build_turn_state_params(context, "hello")

    assert payload["collaborationMode"] == {
        "mode": "default",
        "settings": {
            "model": "gpt-5.4",
            "reasoning_effort": "medium",
            "developer_instructions": None,
        },
    }


def test_codex_backend_queues_plan_implementation_request_for_plan_items() -> None:
    emitted: list[tuple[str, dict[str, Any]]] = []
    settings = WebSettings(codex=StoredCodexSettings(sandbox="workspace-write"))
    chat_service = SimpleNamespace(
        config_service=SimpleNamespace(load_web_settings=lambda: settings),
        get_session_metadata=lambda session_id: {"codex_work_mode": "plan"},
    )
    backend = CodexAppServerBackend(chat_service)
    context = ChatSessionContext(
        session_id="session-1",
        source="chat",
        backend_id="codex",
        project_path=Path("/tmp/project"),
        channel_meta=None,
        backend_state={"thread_id": "thread-1", "collaboration_mode": {"mode": "plan"}},
    )
    runtime = CodexSessionRuntime(session_id="session-1", thread_id="thread-1")
    backend._emit_from_thread = lambda runtime, event, data, wait=False: emitted.append(  # type: ignore[method-assign]
        (event, data)
    )

    backend._handle_item_completed(
        runtime,
        context,
        PlanThreadItem(
            id="plan-item-1",
            text="1. Inspect code\n2. Apply patch",
            type="plan",
        ),
        turn_id="turn-1",
    )

    assert "turn-1:plan-request" in runtime.pending_requests
    pending = runtime.pending_requests["turn-1:plan-request"]
    assert pending.method == "item/plan/requestImplementation"
    assert pending.record["kind"] == "plan_implementation"
    assert pending.record["payload"]["planContent"] == "1. Inspect code\n2. Apply patch"
    assert emitted == [
        (
            "approval_requested",
            {
                "session_id": "session-1",
                **pending.record,
            },
        )
    ]


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


def test_codex_backend_build_pending_approval_record_normalizes_request_user_input() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())

    record = backend.build_pending_approval_record(
        0,
        "item/tool/requestUserInput",
        {
            "questions": [
                {
                    "id": "question_style",
                    "header": "Test style",
                    "question": "What should I do next?",
                    "isOther": True,
                    "options": [
                        {
                            "label": "Keep going",
                            "description": "Ask one more question.",
                        }
                    ],
                }
            ]
        },
    )

    assert record is not None
    assert record["request_id"] == "0"
    assert record["kind"] == "user_input"
    assert record["title"] == "User input required"
    assert record["detail"] == "What should I do next?"
    assert record["options"] == [
        {"value": "accept", "label": "Submit"},
        {"value": "cancel", "label": "Cancel"},
    ]
    payload = record["payload"]
    assert payload["_ipc_request_id"] == 0
    request = payload["request"]
    assert request["kind"] == "user_input"
    assert request["message"] == "What should I do next?"
    assert request["questions"][0]["id"] == "question_style"
    assert (
        request["requestedSchema"]["properties"]["question_style__other"]["type"]
        == "string"
    )


def test_codex_backend_normalizes_user_input_response_answers() -> None:
    backend = CodexAppServerBackend(SimpleNamespace())

    response = backend.build_response_payload_for_request(
        "item/tool/requestUserInput",
        {},
        "accept",
        {"answers": {"approach": ["Small changes first"]}},
    )

    assert response == {
        "answers": {
            "approach": {
                "answers": ["Small changes first"],
            },
        },
    }
