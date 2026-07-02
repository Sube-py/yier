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
from yier_web.codex.ipc_manager import CodexIpcManager
from yier_web.config import AppConfigService
from yier_web.event_stream import EventStreamBroker
from yier_web.frontend import FrontendService
from yier_web.routes.codex import CodexController
from yier_web.schemas import CodexRemoteConnectionPayload, CodexWorkspaceResponse


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
        self.goal: dict[str, Any] | None = None
        self.goal_set_calls: list[dict[str, Any]] = []
        self.goal_get_calls = 0
        self.goal_clear_calls = 0
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
        if self.thread_id == "thread-plan":
            self.state = {
                "id": "thread-plan",
                "turns": [
                    {
                        "turnId": "turn-1",
                        "status": "completed",
                        "items": [
                            {"id": "plan-1", "type": "plan", "text": "Step 1"},
                            {
                                "id": "implement-plan:turn-1",
                                "type": "planImplementation",
                                "turnId": "turn-1",
                                "planContent": "Step 1",
                                "isCompleted": False,
                            },
                        ],
                    }
                ],
                "requests": [
                    {
                        "id": "implement-plan:turn-1",
                        "method": "item/plan/requestImplementation",
                        "params": {
                            "threadId": "thread-plan",
                            "turnId": "turn-1",
                            "planContent": "Step 1",
                        },
                    }
                ],
            }
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

    async def set_thread_goal(
        self,
        *,
        objective: str | None = None,
        status: str | None = None,
        token_budget: int | None = None,
    ) -> dict[str, Any]:
        self.goal_set_calls.append(
            {
                "objective": objective,
                "status": status,
                "token_budget": token_budget,
            }
        )
        previous_goal = self.goal or {}
        self.goal = {
            "threadId": self.thread_id,
            "objective": objective or previous_goal.get("objective") or "Existing goal",
            "status": status or previous_goal.get("status") or "active",
            "tokenBudget": token_budget
            if token_budget is not None
            else previous_goal.get("tokenBudget"),
            "tokensUsed": 0,
            "timeUsedSeconds": 0,
            "createdAt": 1,
            "updatedAt": 2,
        }
        if self.state is not None:
            self.state["threadGoal"] = self.goal
        return {"goal": self.goal}

    async def get_thread_goal(self) -> dict[str, Any] | None:
        self.goal_get_calls += 1
        return self.goal

    async def clear_thread_goal(self) -> dict[str, Any]:
        self.goal_clear_calls += 1
        self.goal = None
        if self.state is not None:
            self.state["threadGoal"] = None
        return {"cleared": True}

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
        queue_plan: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await manager.subscribe("thread-a", queue_a)
        await manager.subscribe("thread-created", queue_b)
        await manager.subscribe("thread-plan", queue_plan)

        snapshot_a = await _wait_for_event(queue_a, lambda event: event["type"] == "thread_snapshot")
        snapshot_b = await _wait_for_event(queue_b, lambda event: event["type"] == "thread_snapshot")
        snapshot_plan = await _wait_for_event(
            queue_plan,
            lambda event: event["type"] == "thread_snapshot",
        )
        assert snapshot_a["payload"]["thread_id"] == "thread-a"
        assert snapshot_b["payload"]["thread_id"] == "thread-created"
        assert snapshot_plan["payload"]["state"]["requests"] == [
            {
                "id": "implement-plan:turn-1",
                "method": "item/plan/requestImplementation",
                "params": {
                    "threadId": "thread-plan",
                    "turnId": "turn-1",
                    "planContent": "Step 1",
                },
            }
        ]

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
        manager.unsubscribe("thread-plan", queue_plan)

        assert factory.by_thread_id("thread-a").stopped is False
        assert factory.by_thread_id("thread-created").stopped is False
        assert factory.by_thread_id("thread-plan").stopped is False

        await manager.stop()

        assert factory.by_thread_id("thread-a").stopped is True
        assert factory.by_thread_id("thread-created").stopped is True
        assert factory.by_thread_id("thread-plan").stopped is True
        assert factory.workspace_session().stopped is True

    asyncio.run(scenario())


def test_codex_manager_updates_cached_mode_for_future_snapshots(tmp_path: Path) -> None:
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
        default_collaboration_mode = {
            "mode": "default",
            "settings": {
                "model": "gpt-5.4",
                "reasoning_effort": "medium",
                "developer_instructions": None,
            },
        }

        await manager.start()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await manager.subscribe("thread-plan", queue)
        await _wait_for_event(
            queue,
            lambda event: event["type"] == "thread_snapshot",
        )

        await manager.set_collaboration_mode("thread-plan", default_collaboration_mode)
        state_event = await _wait_for_event(
            queue,
            lambda event: event["type"] == "thread_state"
            and event["payload"]["state"].get("latestCollaborationMode")
            == default_collaboration_mode,
        )

        assert state_event["payload"]["state"]["latestCollaborationMode"] == default_collaboration_mode
        assert (
            factory.by_thread_id("thread-plan").collaboration_modes[-1]
            == default_collaboration_mode
        )

        manager.unsubscribe("thread-plan", queue)
        refreshed_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await manager.subscribe("thread-plan", refreshed_queue)
        refreshed_snapshot = await _wait_for_event(
            refreshed_queue,
            lambda event: event["type"] == "thread_snapshot",
        )

        assert (
            refreshed_snapshot["payload"]["state"]["latestCollaborationMode"]
            == default_collaboration_mode
        )

        await manager.stop()

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
        assert workspace_response.json()["remote_connections"] == []
        assert workspace_response.json()["active_remote_connection_id"] == ""

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

            default_collaboration_mode = {
                "mode": "default",
                "settings": {
                    "model": "gpt-5.4",
                    "reasoning_effort": "medium",
                    "developer_instructions": None,
                },
            }
            ws.send_json(
                {
                    "id": "prompt-default",
                    "type": "send_prompt",
                    "payload": {
                        "thread_id": "thread-a",
                        "prompt": "implement",
                        "collaboration_mode": default_collaboration_mode,
                    },
                }
            )
            receive_until(
                lambda message: message.get("type") == "ack"
                and message.get("id") == "prompt-default"
            )
            assert factory.by_thread_id("thread-a").run_prompt_calls[-1] == (
                "implement",
                False,
                default_collaboration_mode,
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
                    "id": "goal-set-1",
                    "type": "set_thread_goal",
                    "payload": {
                        "thread_id": "thread-a",
                        "objective": "Finish the web goal mode",
                        "status": "active",
                        "token_budget": 12000,
                    },
                }
            )
            goal_set_messages = receive_until(
                lambda message: message.get("type") == "ack"
                and message.get("id") == "goal-set-1"
            )
            assert goal_set_messages[-1]["payload"]["goal"]["objective"] == (
                "Finish the web goal mode"
            )
            assert factory.by_thread_id("thread-a").goal_set_calls[-1] == {
                "objective": "Finish the web goal mode",
                "status": "active",
                "token_budget": 12000,
            }

            ws.send_json(
                {
                    "id": "goal-get-1",
                    "type": "get_thread_goal",
                    "payload": {"thread_id": "thread-a"},
                }
            )
            goal_get_messages = receive_until(
                lambda message: message.get("type") == "ack"
                and message.get("id") == "goal-get-1"
            )
            assert goal_get_messages[-1]["payload"]["goal"]["status"] == "active"

            ws.send_json(
                {
                    "id": "goal-clear-1",
                    "type": "clear_thread_goal",
                    "payload": {"thread_id": "thread-a"},
                }
            )
            goal_clear_messages = receive_until(
                lambda message: message.get("type") == "ack"
                and message.get("id") == "goal-clear-1"
            )
            assert goal_clear_messages[-1]["payload"]["cleared"] is True
            assert factory.by_thread_id("thread-a").goal_clear_calls == 1

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


def test_codex_remote_connection_configures_ssh_app_server(tmp_path: Path) -> None:
    factory = FakeSessionFactory()
    config_service, client = build_app(tmp_path, factory)

    connection = config_service.save_codex_remote_connection(
        CodexRemoteConnectionPayload(
            display_name="Build host",
            ssh_host="builder.example.com",
            ssh_port=2222,
            identity_file="~/.ssh/build",
            remote_path="/srv/project",
            auto_connect=True,
        )
    )

    with client:
        workspace_response = client.get("/api/codex/workspace")

    assert workspace_response.status_code == 200
    payload = workspace_response.json()
    assert payload["active_remote_connection_id"] == connection.id
    assert payload["remote_connections"][0]["display_name"] == "Build host"

    session = factory.workspace_session()
    assert session.config.host_id == f"ssh:{connection.id}"
    args = session.config.app_server_config.launch_args_override
    assert args[:6] == (
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ServerAliveInterval=15",
    )
    assert "-p" in args
    assert "2222" in args
    assert "-i" in args
    assert "~/.ssh/build" in args
    assert "builder.example.com" in args
    assert "cd -- /srv/project" in args[-1]
    assert "codex app-server --listen stdio://" in args[-1]


def test_codex_remote_connection_routes_persist_and_switch_hosts(tmp_path: Path) -> None:
    factory = FakeSessionFactory()
    config_service, client = build_app(tmp_path, factory)

    with client:
        initial_workspace = client.get("/api/codex/workspace")
        assert initial_workspace.status_code == 200
        local_session = factory.workspace_session()
        assert local_session.config.host_id == "local"

        create_response = client.post(
            "/api/codex/remote-connections",
            json={
                "display_name": "Remote",
                "ssh_host": "user@remote",
                "ssh_port": 22,
                "identity_file": "",
                "remote_path": "~/repo",
                "auto_connect": True,
            },
        )
        assert create_response.status_code == 201
        connection_id = create_response.json()["connection"]["id"]
        assert local_session.stopped is True

        list_response = client.get("/api/codex/remote-connections")
        assert list_response.status_code == 200
        assert list_response.json()["active_connection_id"] == connection_id

        remote_workspace = client.get("/api/codex/workspace")
        assert remote_workspace.status_code == 200
        assert factory.sessions[-1].config.host_id == f"ssh:{connection_id}"

        local_response = client.post("/api/codex/remote-connections/activate-local")
        assert local_response.status_code == 201
        assert (
            config_service.load_web_settings().codex.active_remote_connection_id
            == ""
        )

        activate_response = client.post(
            f"/api/codex/remote-connections/{connection_id}/activate"
        )
        assert activate_response.status_code == 201
        assert (
            config_service.load_web_settings().codex.active_remote_connection_id
            == connection_id
        )


def test_codex_controller_lists_host_filesystem(tmp_path: Path) -> None:
    factory = FakeSessionFactory()
    _, client = build_app(tmp_path, factory)
    alpha_dir = tmp_path / "alpha"
    beta_dir = tmp_path / "beta"
    alpha_dir.mkdir()
    beta_dir.mkdir()
    code_file = tmp_path / "main.py"
    image_file = tmp_path / "preview.PNG"
    code_file.write_text("print('hello')\n")
    image_file.write_bytes(b"png")

    with client:
        response = client.get(
            "/api/codex/filesystem",
            params={"path": str(tmp_path)},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["path"] == str(tmp_path.resolve())
        assert payload["parent_path"] == str(tmp_path.resolve().parent)
        assert payload["roots"]
        assert payload["roots"][0]["kind"] == "directory"

        entries = payload["entries"]
        by_name = {entry["name"]: entry for entry in entries}
        assert by_name["alpha"]["kind"] == "directory"
        assert by_name["beta"]["kind"] == "directory"
        assert by_name["main.py"]["kind"] == "file"
        assert by_name["main.py"]["extension"] == ".py"
        assert by_name["preview.PNG"]["extension"] == ".png"

        first_file_index = next(
            index for index, entry in enumerate(entries) if entry["kind"] == "file"
        )
        last_directory_index = max(
            index
            for index, entry in enumerate(entries)
            if entry["kind"] == "directory"
        )
        assert last_directory_index < first_file_index


def test_codex_controller_rejects_invalid_filesystem_paths(tmp_path: Path) -> None:
    factory = FakeSessionFactory()
    _, client = build_app(tmp_path, factory)
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory\n")

    with client:
        file_response = client.get(
            "/api/codex/filesystem",
            params={"path": str(file_path)},
        )
        missing_response = client.get(
            "/api/codex/filesystem",
            params={"path": str(tmp_path / "missing")},
        )

        assert file_response.status_code == 400
        assert "not a directory" in file_response.json()["detail"]
        assert missing_response.status_code == 404
        assert "Path not found" in missing_response.json()["detail"]


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
