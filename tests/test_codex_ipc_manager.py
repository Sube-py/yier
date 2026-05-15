from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from codex_app_server.generated.v2_all import (
    AbsolutePathBuf,
    IdleThreadStatus,
    SessionSource,
    SessionSourceValue,
    Thread,
    ThreadListResponse,
    ThreadStatus,
)
import pytest
from litestar.exceptions import WebSocketDisconnect
from litestar.testing import TestClient

from yier_web.app import AppServices, create_app
from yier_web.auth import AuthService
from yier_web.channel_workspace import IntegratedChannelWorkspaceService
from yier_web.codex.ipc_manager import CodexIpcManager
from yier_web.config import AppConfigService
from yier_web.event_stream import EventStreamBroker
from yier_web.frontend import FrontendService
from yier_web.routes.codex import CodexController
from yier_web.schemas import CodexWorkspaceResponse


def fake_thread(
    thread_id: str,
    cwd: str,
    *,
    name: str = "",
    preview: str = "",
    created_at: int = 1,
    updated_at: int = 2,
) -> Thread:
    return Thread(
        cli_version="test",
        created_at=created_at,
        cwd=AbsolutePathBuf(root=cwd),
        ephemeral=False,
        id=thread_id,
        model_provider="openai",
        name=name,
        preview=preview,
        source=SessionSource(root=SessionSourceValue.app_server),
        status=ThreadStatus(root=IdleThreadStatus(type="idle")),
        turns=[],
        updated_at=updated_at,
    )


class FakeCodexIpcSession:
    def __init__(self, config: Any, *, notify: Callable[[str], None] | None = None) -> None:
        self.config = config
        self._notify = notify
        self.started = False
        self.stopped = False
        self.thread_id = config.thread_id or ""
        self.state: dict[str, Any] | None = (
            {
                "id": self.thread_id or "workspace",
                "threadId": self.thread_id or "workspace",
                "cwd": "/tmp/project-a",
                "title": "Thread",
                "turns": [],
            }
            if config.thread_id is not None
            else None
        )
        self.stream_role: dict[str, Any] | None = {"role": "owner"}
        self.queued_followups: list[dict[str, Any]] = []
        self.start_new_thread_calls: list[dict[str, Any]] = []
        self.run_prompt_calls: list[tuple[str, bool, dict[str, Any] | None]] = []
        self.steer_prompt_calls: list[str] = []
        self.interrupt_turn_calls: list[str | None] = []
        self.compact_calls = 0
        self.collaboration_modes: list[dict[str, Any] | None] = []
        self.user_input_responses: list[tuple[str, dict[str, Any]]] = []
        self.followup_calls: list[str] = []
        self.removed_followups: list[str] = []
        self.rename_calls: list[tuple[str, str | None]] = []
        self.archive_calls: list[tuple[str | None, str | None, bool]] = []
        self.unarchive_calls: list[str | None] = []
        self.fork_calls: list[str | None] = []
        self.list_threads_calls: list[dict[str, Any] | None] = []
        self._state_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.list_threads_response = ThreadListResponse(
            data=[
                fake_thread("thread-a", "/tmp/project-a", name="Alpha", preview="one"),
                fake_thread("thread-b", "/tmp/project-b", name="Beta", preview="two"),
            ]
        )

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def start_new_thread(self, params: dict[str, Any] | None = None) -> SimpleNamespace:
        self.start_new_thread_calls.append(params or {})
        self.thread_id = "thread-created"
        self.state = {
            "id": self.thread_id,
            "threadId": self.thread_id,
            "cwd": (params or {}).get("cwd", "/tmp/project-c"),
            "title": "Created thread",
            "turns": [],
        }
        return SimpleNamespace(id=self.thread_id)

    async def hydrate_initial_state(self, *, owner_wait_seconds: float = 0.4) -> None:
        return None

    async def list_threads(self, params: dict[str, Any] | None = None) -> ThreadListResponse:
        self.list_threads_calls.append(params)
        return self.list_threads_response

    async def run_prompt(
        self,
        prompt: str,
        *,
        wait_for_completion: bool,
        collaboration_mode: dict[str, Any] | None = None,
    ) -> None:
        self.run_prompt_calls.append((prompt, wait_for_completion, collaboration_mode))

    async def steer_prompt(self, prompt: str) -> None:
        self.steer_prompt_calls.append(prompt)

    async def interrupt_turn(self, turn_id: str | None = None) -> bool:
        self.interrupt_turn_calls.append(turn_id)
        return True

    async def compact_thread(self) -> bool:
        self.compact_calls += 1
        return True

    async def set_collaboration_mode(self, collaboration_mode: dict[str, Any] | None) -> None:
        self.collaboration_modes.append(collaboration_mode)

    async def submit_user_input_response(self, request_id: str, response: dict[str, Any]) -> bool:
        self.user_input_responses.append((request_id, response))
        return True

    async def enqueue_followup(self, prompt: str) -> dict[str, Any]:
        self.followup_calls.append(prompt)
        return {"message_id": "followup-1", "prompt": prompt}

    async def remove_followup(self, message_id: str) -> None:
        self.removed_followups.append(message_id)

    async def set_thread_name(self, name: str, thread_id: str | None = None) -> dict[str, Any]:
        self.rename_calls.append((name, thread_id))
        if self.state is not None:
            self.state["title"] = name
        return {"id": thread_id or self.thread_id, "name": name}

    async def archive_thread(
        self,
        thread_id: str | None = None,
        *,
        cwd: str | None = None,
        cleanup_worktree: bool = True,
    ) -> dict[str, Any]:
        self.archive_calls.append((thread_id, cwd, cleanup_worktree))
        return {"id": thread_id or self.thread_id}

    async def unarchive_thread(self, thread_id: str | None = None) -> dict[str, Any]:
        self.unarchive_calls.append(thread_id)
        return {"id": thread_id or self.thread_id}

    async def fork_thread(
        self,
        thread_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> SimpleNamespace:
        self.fork_calls.append(thread_id)
        self.thread_id = "thread-forked"
        self.state = {
            "id": self.thread_id,
            "threadId": self.thread_id,
            "cwd": "/tmp/project-a",
            "title": "Forked thread",
            "turns": [],
        }
        return SimpleNamespace(id=self.thread_id)

    async def watch_state(self, *, replay: bool = True):
        if replay:
            yield self.state
        while True:
            yield await self._state_queue.get()

    def emit_state(self, state: dict[str, Any] | None) -> None:
        self.state = state
        self._state_queue.put_nowait(state)


class FakeSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[FakeCodexIpcSession] = []

    def __call__(self, config: Any, *, notify: Callable[[str], None] | None = None) -> FakeCodexIpcSession:
        session = FakeCodexIpcSession(config, notify=notify)
        self.sessions.append(session)
        return session

    def by_thread_id(self, thread_id: str) -> FakeCodexIpcSession:
        for session in self.sessions:
            if session.thread_id == thread_id:
                return session
        raise KeyError(thread_id)

    def workspace_session(self) -> FakeCodexIpcSession:
        for session in self.sessions:
            if session.config.thread_id is None:
                return session
        raise KeyError("workspace")


class FakeChatService:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FakeChannelWorkspaceService:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def get_workspace_snapshot(self):
        return SimpleNamespace(platforms=[], accounts=[])


class FakeDirectoryPickerService:
    def select_directory(self, initial_path: str | None = None) -> str | None:
        return None


class DisconnectingCodexSocket:
    def __init__(self, manager: CodexIpcManager) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                auth_service=AuthService(),
                codex_ipc_manager=manager,
            )
        )
        self.accepted = False
        self.sent_messages: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict[str, Any]:
        await asyncio.sleep(0)
        raise WebSocketDisconnect(detail="disconnect event")

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent_messages.append(message)


def build_app(tmp_path: Path, factory: FakeSessionFactory) -> tuple[AppConfigService, TestClient[Any]]:
    project_root = tmp_path / "project"
    (project_root / "web" / "dist").mkdir(parents=True)
    (project_root / "web" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    config_service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    manager = CodexIpcManager(
        config_service=config_service,
        event_broker=EventStreamBroker(),
        session_factory=factory,
    )
    frontend_service = FrontendService(project_root=project_root)
    app = create_app(
        project_root=project_root,
        home_dir=tmp_path / "home",
        services=AppServices(
            config_service=config_service,
            chat_service=FakeChatService(),  # type: ignore[arg-type]
            channel_workspace_service=FakeChannelWorkspaceService(),  # type: ignore[arg-type]
            codex_ipc_manager=manager,
            event_broker=EventStreamBroker(),
            frontend_service=frontend_service,
            directory_picker_service=FakeDirectoryPickerService(),
            auth_service=AuthService(),
        ),
    )
    return config_service, TestClient(app)


async def _wait_for_event(queue: asyncio.Queue[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        if predicate(event):
            return event


def test_codex_manager_keeps_separate_sessions_alive(tmp_path: Path) -> None:
    async def scenario() -> None:
        config_service = AppConfigService(
            project_root=tmp_path / "project",
            home_dir=tmp_path / "home",
        )
        factory = FakeSessionFactory()
        manager = CodexIpcManager(
            config_service=config_service,
            event_broker=EventStreamBroker(),
            session_factory=factory,
        )

        await manager.start()
        workspace = await manager.workspace()
        assert len(workspace.projects) == 2
        assert factory.workspace_session().list_threads_calls[-1] == {
            "archived": False,
            "limit": 100,
            "sort_key": "updated_at",
            "sort_direction": "desc",
        }

        created = await manager.start_thread(project_path="/tmp/project-c")
        assert created["thread_id"] == "thread-created"

        queue_a: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        queue_b: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await manager.subscribe("thread-a", queue_a)
        await manager.subscribe("thread-created", queue_b)

        snapshot_a = await _wait_for_event(queue_a, lambda event: event["type"] == "thread_snapshot")
        snapshot_b = await _wait_for_event(queue_b, lambda event: event["type"] == "thread_snapshot")
        assert snapshot_a["payload"]["thread_id"] == "thread-a"
        assert snapshot_b["payload"]["thread_id"] == "thread-created"

        factory.by_thread_id("thread-a").emit_state(
            {"id": "thread-a", "phase": "working", "turns": []}
        )
        factory.by_thread_id("thread-created").emit_state(
            {"id": "thread-created", "phase": "working", "turns": []}
        )

        state_a = await _wait_for_event(
            queue_a,
            lambda event: event["type"] == "thread_state" and event["payload"]["state"].get("phase") == "working",
        )
        state_b = await _wait_for_event(
            queue_b,
            lambda event: event["type"] == "thread_state" and event["payload"]["state"].get("phase") == "working",
        )
        assert state_a["payload"]["thread_id"] == "thread-a"
        assert state_b["payload"]["thread_id"] == "thread-created"

        manager.unsubscribe("thread-a", queue_a)
        manager.unsubscribe("thread-created", queue_b)

        assert factory.by_thread_id("thread-a").stopped is False
        assert factory.by_thread_id("thread-created").stopped is False

        await manager.stop()

        assert factory.by_thread_id("thread-a").stopped is True
        assert factory.by_thread_id("thread-created").stopped is True
        assert factory.workspace_session().stopped is True

    asyncio.run(scenario())


def test_codex_controller_http_and_websocket_contract(tmp_path: Path) -> None:
    factory = FakeSessionFactory()
    _, client = build_app(tmp_path, factory)

    with client:
        workspace_response = client.get("/api/codex/workspace")
        assert workspace_response.status_code == 200
        assert len(workspace_response.json()["projects"]) == 2
        sessions = [
            session
            for project in workspace_response.json()["projects"]
            for session in project["sessions"]
        ]
        thread_a = next(session for session in sessions if session["thread_id"] == "thread-a")
        assert thread_a["cwd"] == "/tmp/project-a"
        assert thread_a["status"] == "idle"
        assert thread_a["source"] == "appServer"

        create_response = client.post(
            "/api/codex/threads",
            json={"project_path": "/tmp/project-c"},
        )
        assert create_response.status_code == 201
        assert create_response.json()["thread_id"] == "thread-created"

        state_response = client.get("/api/codex/threads/thread-created/state")
        assert state_response.status_code == 200
        assert state_response.json()["state"]["id"] == "thread-created"

        rename_response = client.put(
            "/api/codex/threads/thread-created/name",
            json={"name": "Renamed"},
        )
        assert rename_response.status_code == 200
        assert rename_response.json()["ok"] is True

        archive_response = client.post("/api/codex/threads/thread-created/archive")
        assert archive_response.status_code == 201
        assert archive_response.json()["thread_id"] == "thread-created"

        unarchive_response = client.post("/api/codex/threads/thread-created/unarchive")
        assert unarchive_response.status_code == 201
        assert unarchive_response.json()["ok"] is True

        with client.websocket_connect("/api/codex/ws") as ws:
            def receive_until(predicate: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
                messages: list[dict[str, Any]] = []
                while True:
                    message = ws.receive_json()
                    messages.append(message)
                    if predicate(message):
                        return messages

            ready = ws.receive_json()
            assert ready["type"] == "connection_ready"

            ws.send_json({"id": "list-1", "type": "list_threads", "payload": {}})
            list_messages = receive_until(lambda message: message.get("type") == "ack" and message.get("id") == "list-1")
            assert any(message["type"] == "workspace" for message in list_messages)
            assert factory.workspace_session().list_threads_calls[-1] == {
                "archived": False,
                "limit": 100,
                "sort_key": "updated_at",
                "sort_direction": "desc",
            }

            ws.send_json(
                {
                    "id": "sub-a",
                    "type": "subscribe_thread",
                    "payload": {"thread_id": "thread-a"},
                }
            )
            sub_a = receive_until(lambda message: message.get("type") == "ack" and message.get("id") == "sub-a")
            assert any(message["type"] in {"thread_snapshot", "thread_state"} for message in sub_a)

            ws.send_json(
                {
                    "id": "sub-b",
                    "type": "subscribe_thread",
                    "payload": {"thread_id": "thread-b"},
                }
            )
            sub_b = receive_until(lambda message: message.get("type") == "ack" and message.get("id") == "sub-b")
            assert any(message["type"] in {"thread_snapshot", "thread_state"} for message in sub_b)

            factory.by_thread_id("thread-a").emit_state(
                {"id": "thread-a", "phase": "working", "turns": []}
            )
            thread_state = receive_until(
                lambda message: message.get("type") == "thread_state"
                and message["payload"]["state"].get("phase") == "working"
            )
            assert thread_state[-1]["payload"]["thread_id"] == "thread-a"

            ws.send_json(
                {
                    "id": "prompt-1",
                    "type": "send_prompt",
                    "payload": {
                        "thread_id": "thread-a",
                        "prompt": "hello",
                        "collaboration_mode": {"mode": "build"},
                    },
                }
            )
            prompt_messages = receive_until(
                lambda message: message.get("type") == "ack" and message.get("id") == "prompt-1"
            )
            assert factory.by_thread_id("thread-a").run_prompt_calls[-1] == (
                "hello",
                False,
                {"mode": "build"},
            )

            ws.send_json(
                {
                    "id": "input-1",
                    "type": "submit_user_input_response",
                    "payload": {
                        "thread_id": "thread-a",
                        "request_id": "request-1",
                        "response": {"answer": "ok"},
                    },
                }
            )
            input_messages = receive_until(
                lambda message: message.get("type") == "ack" and message.get("id") == "input-1"
            )
            assert factory.by_thread_id("thread-a").user_input_responses[-1] == (
                "request-1",
                {"answer": "ok"},
            )

            ws.send_json(
                {
                    "id": "archive-1",
                    "type": "archive_thread",
                    "payload": {"thread_id": "thread-a"},
                }
            )
            archive_messages = receive_until(
                lambda message: message.get("type") == "ack" and message.get("id") == "archive-1"
            )
            assert any(message["type"] == "thread_archived" for message in archive_messages)

            ws.send_json(
                {
                    "id": "fork-1",
                    "type": "fork_thread",
                    "payload": {"thread_id": "thread-b"},
                }
            )
            fork_messages = receive_until(
                lambda message: message.get("type") == "ack" and message.get("id") == "fork-1"
            )
            assert fork_messages[-1]["payload"]["thread_id"] == "thread-forked"
            assert any(message["type"] == "workspace" for message in fork_messages)
            assert factory.by_thread_id("thread-forked").fork_calls[-1] == "thread-b"

            ws.send_json(
                {
                    "id": "sub-fork",
                    "type": "subscribe_thread",
                    "payload": {"thread_id": "thread-forked"},
                }
            )
            sub_fork = receive_until(
                lambda message: message.get("type") == "ack" and message.get("id") == "sub-fork"
            )
            assert any(
                message["type"] == "thread_snapshot"
                and message["payload"]["thread_id"] == "thread-forked"
                for message in sub_fork
            )

            ws.send_json(
                {
                    "id": "unarchive-1",
                    "type": "unarchive_thread",
                    "payload": {"thread_id": "thread-a"},
                }
            )
            unarchive_messages = receive_until(
                lambda message: message.get("type") == "ack" and message.get("id") == "unarchive-1"
            )
            assert any(message["type"] == "thread_unarchived" for message in unarchive_messages)

            ws.send_json(
                {
                    "id": "bad-1",
                    "type": "send_prompt",
                    "payload": {"thread_id": "thread-a"},
                }
            )
            error_messages = receive_until(
                lambda message: message.get("type") == "error" and message.get("id") == "bad-1"
            )
            error_message = error_messages[-1]
            assert error_message["type"] == "error"
            assert error_message["code"] == "bad_request"


def test_codex_websocket_embed_token_allows_unauthenticated_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YIER_AUTH_PASSWORD", "deploy-secret")
    monkeypatch.setenv("YIER_CODEX_EMBED_TOKEN", "embed-secret")
    factory = FakeSessionFactory()
    _, client = build_app(tmp_path, factory)

    with client:
        with client.websocket_connect(
            "/api/codex/ws",
            params={"embed_token": "embed-secret"},
        ) as ws:
            ready = ws.receive_json()
            assert ready["type"] == "connection_ready"

            ws.send_json({"id": "list-embed", "type": "list_threads", "payload": {}})
            while True:
                message = ws.receive_json()
                if message.get("type") == "ack" and message.get("id") == "list-embed":
                    assert message["ok"] is True
                    break


def test_codex_websocket_rejects_missing_or_invalid_embed_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YIER_AUTH_PASSWORD", "deploy-secret")
    monkeypatch.setenv("YIER_CODEX_EMBED_TOKEN", "embed-secret")
    factory = FakeSessionFactory()
    _, client = build_app(tmp_path, factory)

    with client:
        with client.websocket_connect("/api/codex/ws") as ws:
            message = ws.receive_json()
            assert message["type"] == "error"
            assert message["code"] == "unauthorized"

        with client.websocket_connect(
            "/api/codex/ws",
            params={"embed_token": "wrong-secret"},
        ) as ws:
            message = ws.receive_json()
            assert message["type"] == "error"
            assert message["code"] == "unauthorized"


def test_codex_websocket_keeps_authenticated_access_with_password_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YIER_AUTH_PASSWORD", "deploy-secret")
    factory = FakeSessionFactory()
    _, client = build_app(tmp_path, factory)

    with client:
        login_response = client.post(
            "/api/auth/login",
            json={"password": "deploy-secret"},
        )
        assert login_response.status_code == 201

        with client.websocket_connect("/api/codex/ws") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "connection_ready"


def test_codex_websocket_disconnect_during_cleanup_is_quiet(tmp_path: Path) -> None:
    async def scenario() -> None:
        config_service = AppConfigService(
            project_root=tmp_path / "project",
            home_dir=tmp_path / "home",
        )
        manager = CodexIpcManager(
            config_service=config_service,
            event_broker=EventStreamBroker(),
            session_factory=FakeSessionFactory(),
        )
        socket = DisconnectingCodexSocket(manager)

        controller = CodexController.__new__(CodexController)
        await CodexController.__dict__["websocket_handler"].fn(
            controller,
            socket,
        )

        assert socket.accepted is True

    asyncio.run(scenario())
