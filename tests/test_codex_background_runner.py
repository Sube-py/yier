from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shlex
from types import SimpleNamespace
from typing import Any

import pytest

from yier_agents import ToolContext

from yier_web.background_followups import (
    create_resume_codex_background_session_tool,
    create_start_codex_background_session_tool,
)
from yier_web.codex_background_runner import (
    CodexBackgroundRunnerRequest,
    load_request,
    run_request,
)
from yier_web.event_stream import EventStreamBroker
from yier_web.tool_events import reset_tool_event_emitter, set_tool_event_emitter


class FakeBackgroundSession:
    def __init__(self, session_id: str, command: str, cwd: Path) -> None:
        self.session_id = session_id
        self.command = command
        self.cwd = cwd
        self.state = "running"


class FakeBackgroundManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.sessions: dict[str, FakeBackgroundSession] = {}

    async def start(self, command: str, cwd: str | None = None) -> FakeBackgroundSession:
        self.calls.append((command, cwd))
        session = FakeBackgroundSession("bg-1", command, Path(cwd or ".").resolve())
        self.sessions[session.session_id] = session
        return session

    def get_session_raw_payload(self, session_id: str) -> dict[str, Any]:
        session = self.sessions[session_id]
        return {
            "kind": "background_command",
            "request": {
                "command": session.command,
                "cwd": str(session.cwd),
            },
            "process": {
                "session_id": session.session_id,
                "state": session.state,
                "exit_code": None,
                "started_at": 0,
                "finished_at": None,
                "runtime_seconds": 0,
                "timed_out": False,
            },
            "events": [],
            "latest_event_index": 0,
            "stdout_text": "",
            "stderr_text": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "events_truncated": False,
            "dropped_event_count": 0,
        }


class FakeConfigService:
    def __init__(self, project_root: Path, home_dir: Path) -> None:
        self.project_root = project_root.resolve()
        self.home_dir = home_dir.resolve()
        self.web_root = self.home_dir / ".yier" / "web"
        self.web_root.mkdir(parents=True, exist_ok=True)

    def resolve_project_path(self, raw_path: str | None) -> str:
        if raw_path:
            return str(Path(raw_path).resolve())
        return str(self.project_root)


class FakeChatService:
    def __init__(self, project_root: Path, home_dir: Path) -> None:
        self.project_root = project_root.resolve()
        self.config_service = FakeConfigService(project_root, home_dir)

    def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        return {
            "source": "channel",
            "backend_id": "yier",
            "project_path": str(self.project_root),
            "channel_meta": {"platform": "wechat"},
            "backend_state": {},
            "codex_work_mode": None,
            "title": "",
            "preview": "",
            "updated_at": 0.0,
        }


def _request_file_from_command(command: str) -> Path:
    parts = shlex.split(command)
    request_index = parts.index("--request-file")
    return Path(parts[request_index + 1]).resolve()


def test_start_codex_background_session_tool_starts_real_background_process(
    tmp_path: Path,
) -> None:
    chat_service = FakeChatService(tmp_path / "app", tmp_path / "home")
    background_manager = FakeBackgroundManager()
    tool = create_start_codex_background_session_tool(chat_service, background_manager)
    emitted_events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        emitted_events.append((event, data))

    token = set_tool_event_emitter(emit)
    try:
        result = asyncio.run(
            tool.execute(
                tool.parameters(prompt="Build the feature"),
                ToolContext(
                    session_id="caller-1",
                    message_id="message-1",
                    call_id="call-1",
                ),
            )
        )
    finally:
        reset_tool_event_emitter(token)

    assert background_manager.calls
    command, cwd = background_manager.calls[0]
    assert cwd == str(chat_service.project_root)
    assert "yier_web.codex_background_runner" in command
    request_file = _request_file_from_command(command)
    payload = json.loads(request_file.read_text(encoding="utf-8"))
    assert payload == {
        "action": "start",
        "caller_session_id": "caller-1",
        "thread_id": None,
        "prompt": "Build the feature",
        "project_path": str(chat_service.project_root),
    }
    assert result.metadata["background_session_id"] == "bg-1"
    assert "request_path" not in result.metadata
    assert result.raw["kind"] == "background_command"
    assert 'read_background_command(session_id="bg-1")' in result.content
    assert "call read_background_command" in result.content
    assert emitted_events == [
        (
            "background_command_started",
            {
                "session_id": "caller-1",
                "tool_call_id": "call-1",
                "tool_name": "start_codex_background_session",
                "background_session_id": "bg-1",
                "command": command,
                "cwd": str(chat_service.project_root),
                "state": "running",
            },
        )
    ]


def test_resume_codex_background_session_tool_writes_resume_request(
    tmp_path: Path,
) -> None:
    chat_service = FakeChatService(tmp_path / "app", tmp_path / "home")
    background_manager = FakeBackgroundManager()
    tool = create_resume_codex_background_session_tool(chat_service, background_manager)

    result = asyncio.run(
        tool.execute(
            tool.parameters(
                thread_id="thread-9",
                prompt="Resume this task",
                project_path=str(tmp_path / "workspace"),
            ),
            ToolContext(
                session_id="caller-2",
                message_id="message-2",
                call_id="call-2",
            ),
        )
    )

    command, cwd = background_manager.calls[0]
    request_file = _request_file_from_command(command)
    payload = json.loads(request_file.read_text(encoding="utf-8"))
    assert cwd == str((tmp_path / "workspace").resolve())
    assert payload["action"] == "resume"
    assert payload["thread_id"] == "thread-9"
    assert payload["project_path"] == str((tmp_path / "workspace").resolve())
    assert result.metadata["thread_id"] == "thread-9"
    assert 'wait_background_command(session_id="bg-1")' in result.content


def test_load_request_validates_resume_thread_id(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "action": "resume",
                "caller_session_id": "caller-1",
                "prompt": "Resume work",
                "thread_id": "thread-3",
            }
        ),
        encoding="utf-8",
    )

    request = load_request(request_path)

    assert request == CodexBackgroundRunnerRequest(
        action="resume",
        caller_session_id="caller-1",
        prompt="Resume work",
        thread_id="thread-3",
        project_path=None,
    )


def test_run_request_uses_chat_service_helpers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeChatServiceForRunner:
        def __init__(self, project_root: Path, config_service: Any) -> None:
            self.project_root = project_root
            self.config_service = config_service
            self.event_broker = EventStreamBroker()
            self._ipc_stream_tasks: dict[str, asyncio.Task[None]] = {}

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        async def start_codex_background_session_from_tool(
            self,
            *,
            caller_session_id: str,
            prompt: str,
            project_path: str | None = None,
        ) -> dict[str, Any]:
            calls.append(
                (
                    "start",
                    {
                        "caller_session_id": caller_session_id,
                        "prompt": prompt,
                        "project_path": project_path,
                    },
                )
            )

            async def emit_events() -> None:
                await self.event_broker.publish(
                    "assistant_message",
                    {
                        "session_id": "thread-1",
                        "content": "Done",
                    },
                )

            self._ipc_stream_tasks["thread-1"] = asyncio.create_task(asyncio.sleep(0))
            asyncio.create_task(emit_events())
            return {
                "session_id": "thread-1",
                "thread_id": "thread-1",
                "turn_id": "turn-1",
                "project_path": str(tmp_path / "workspace"),
                "status": "active",
            }

        async def resume_codex_background_session_from_tool(
            self,
            *,
            caller_session_id: str,
            thread_id: str,
            prompt: str,
            project_path: str | None = None,
        ) -> dict[str, Any]:
            calls.append(
                (
                    "resume",
                    {
                        "caller_session_id": caller_session_id,
                        "thread_id": thread_id,
                        "prompt": prompt,
                        "project_path": project_path,
                    },
                )
            )
            self._ipc_stream_tasks["thread-2"] = asyncio.create_task(asyncio.sleep(0))
            return {
                "session_id": "thread-2",
                "thread_id": "thread-2",
                "turn_id": "turn-2",
                "project_path": str(tmp_path / "workspace"),
                "status": "active",
            }

        def get_session_metadata(self, session_id: str) -> dict[str, Any]:
            return {
                "project_path": str(tmp_path / "workspace"),
                "backend_state": {
                    "thread_id": session_id,
                    "status": "completed",
                },
            }

        def build_transcript_messages(self, session_id: str) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    model_dump=lambda mode="json": {
                        "role": "user",
                        "content": "Do the work",
                    }
                ),
                SimpleNamespace(
                    model_dump=lambda mode="json": {
                        "role": "assistant",
                        "content": "Done",
                    }
                ),
            ]

    monkeypatch.setattr(
        "yier_web.codex_background_runner.ChatService",
        FakeChatServiceForRunner,
    )

    exit_code = asyncio.run(
        run_request(
            app_project_root=tmp_path / "app",
            home_dir=tmp_path / "home",
            request=CodexBackgroundRunnerRequest(
                action="start",
                caller_session_id="caller-1",
                prompt="Do the work",
                project_path=str(tmp_path / "workspace"),
            ),
        )
    )

    assert exit_code == 0
    assert calls == [
        (
            "start",
            {
                "caller_session_id": "caller-1",
                "prompt": "Do the work",
                "project_path": str(tmp_path / "workspace"),
            },
        )
    ]
    stdout_lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    assert stdout_lines[0]["event"] == "codex_background_runner_started"
    assert any(line["event"] == "codex_background_started" for line in stdout_lines)
    assert any(line["event"] == "codex_background_finished" for line in stdout_lines)
    result_line = next(
        line for line in stdout_lines if line["event"] == "codex_background_result"
    )
    assert result_line["ok"] is True
    assert result_line["result"]["latest_assistant_message"] == "Done"
    assert result_line["result"]["messages"][-1]["content"] == "Done"
