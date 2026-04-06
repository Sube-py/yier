from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from yier_agents import BackgroundCommandManager, Tool, ToolContext, ToolOutput

from yier_web.tool_events import emit_tool_event

if TYPE_CHECKING:
    from yier_web.chat import ChatService


@dataclass(frozen=True, slots=True)
class QueuedFollowup:
    queue_id: str
    owner_session_id: str
    trigger_session_id: str
    prompt: str
    source: str


class QueueBackgroundFollowupParams(BaseModel):
    session_id: str = Field(description="Background command session id.")
    prompt: str = Field(description="Follow-up task to run after the command completes.")


class StartCodexBackgroundSessionParams(BaseModel):
    prompt: str = Field(
        description="Task prompt to send to Codex after creating the background session."
    )
    project_path: str | None = Field(
        default=None,
        description="Optional project path override. Defaults to the caller session project path.",
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Prompt must not be empty.")
        return normalized

    @field_validator("project_path")
    @classmethod
    def validate_project_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ResumeCodexBackgroundSessionParams(BaseModel):
    thread_id: str = Field(description="Existing Codex thread id to resume.")
    prompt: str = Field(
        description="Task prompt to send to Codex after resuming the background session."
    )
    project_path: str | None = Field(
        default=None,
        description="Optional project path override. Defaults to the caller session project path.",
    )

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Thread id must not be empty.")
        return normalized

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Prompt must not be empty.")
        return normalized

    @field_validator("project_path")
    @classmethod
    def validate_project_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class FollowupQueueManager:
    def __init__(self) -> None:
        self._queue: list[QueuedFollowup] = []
        self._counter = 0

    def add(
        self,
        owner_session_id: str,
        session_id: str,
        prompt: str,
        *,
        source: str,
    ) -> QueuedFollowup:
        self._counter += 1
        item = QueuedFollowup(
            queue_id=f"q-{self._counter}",
            owner_session_id=owner_session_id,
            trigger_session_id=session_id,
            prompt=prompt,
            source=source,
        )
        self._queue.append(item)
        return item

    def list_items(self) -> tuple[QueuedFollowup, ...]:
        return tuple(self._queue)

    def count(self) -> int:
        return len(self._queue)

    def pop_ready(self, completed_session_ids: set[str]) -> list[QueuedFollowup]:
        ready: list[QueuedFollowup] = []
        pending: list[QueuedFollowup] = []
        for item in self._queue:
            if item.trigger_session_id in completed_session_ids:
                ready.append(item)
                continue
            pending.append(item)
        self._queue = pending
        return ready


def _resolve_codex_background_project_path(
    chat_service: ChatService,
    caller_session_id: str,
    project_path: str | None,
) -> Path:
    caller_metadata = chat_service.get_session_metadata(caller_session_id)
    resolved_project_path = chat_service.config_service.resolve_project_path(
        project_path or caller_metadata["project_path"]
    )
    return Path(resolved_project_path).resolve()


def _write_codex_background_request(
    chat_service: ChatService,
    payload: dict[str, str | None],
) -> Path:
    request_dir = chat_service.config_service.web_root / "codex_background_requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    request_path = request_dir / f"{uuid4().hex}.json"
    request_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return request_path


def _build_codex_background_runner_command(
    chat_service: ChatService,
    *,
    request_path: Path,
) -> str:
    parts = [
        "uv",
        "run",
        "--project",
        str(chat_service.project_root),
        "python",
        "-m",
        "yier_web.codex_background_runner",
        "--app-project-root",
        str(chat_service.project_root),
        "--home-dir",
        str(chat_service.config_service.home_dir),
        "--request-file",
        str(request_path),
    ]
    return " ".join(shlex.quote(part) for part in parts)


def create_queue_background_followup_tool(
    background_manager: BackgroundCommandManager,
    followup_queue: FollowupQueueManager,
) -> Tool[QueueBackgroundFollowupParams]:
    async def execute(
        params: QueueBackgroundFollowupParams,
        ctx: ToolContext,
    ) -> ToolOutput:
        session = background_manager.require_session(params.session_id)
        if not session.is_running():
            return ToolOutput(
                content=(
                    f"Background command {session.session_id} has already finished. "
                    "Read its output and continue directly instead of queueing a follow-up."
                ),
                metadata={
                    "session_id": session.session_id,
                    "queued": False,
                    "state": session.state,
                },
            )

        item = followup_queue.add(
            ctx.session_id,
            session.session_id,
            params.prompt,
            source="agent",
        )
        await emit_tool_event(
            "background_followup_queued",
            {
                "session_id": ctx.session_id,
                "tool_call_id": ctx.call_id,
                "background_session_id": session.session_id,
                "queue_id": item.queue_id,
                "prompt": item.prompt,
            },
        )
        return ToolOutput(
            content=(
                f"Queued follow-up {item.queue_id} for {session.session_id}\n"
                f"Prompt: {item.prompt}"
            ),
            metadata={
                "queue_id": item.queue_id,
                "session_id": session.session_id,
                "prompt": item.prompt,
            },
        )

    return Tool(
        name="queue_background_followup",
        description=(
            "Queue a follow-up task to run automatically after a background command finishes. "
            "Use this when you want to keep watching a build, test run, or sync job and continue "
            "once it completes."
        ),
        parameters=QueueBackgroundFollowupParams,
        execute=execute,
    )


def create_start_codex_background_session_tool(
    chat_service: ChatService,
    background_manager: BackgroundCommandManager,
) -> Tool[StartCodexBackgroundSessionParams]:
    async def execute(
        params: StartCodexBackgroundSessionParams,
        ctx: ToolContext,
    ) -> ToolOutput:
        resolved_project_path = _resolve_codex_background_project_path(
            chat_service,
            ctx.session_id,
            params.project_path,
        )
        request_path = _write_codex_background_request(
            chat_service,
            {
                "action": "start",
                "caller_session_id": ctx.session_id,
                "thread_id": None,
                "prompt": params.prompt,
                "project_path": str(resolved_project_path),
            },
        )
        command = _build_codex_background_runner_command(
            chat_service,
            request_path=request_path,
        )
        session = await background_manager.start(command, str(resolved_project_path))
        await emit_tool_event(
            "background_command_started",
            {
                "session_id": ctx.session_id,
                "tool_call_id": ctx.call_id,
                "tool_name": "start_codex_background_session",
                "background_session_id": session.session_id,
                "command": session.command,
                "cwd": str(session.cwd),
                "state": session.state,
            },
        )
        return ToolOutput(
            content=(
                f"Started Codex background runner {session.session_id}\n"
                f"Working directory: {session.cwd}\n"
                f"Project path: {resolved_project_path}\n"
                "This returned session id is a background command session id.\n"
                f"To watch live progress now, call read_background_command(session_id=\"{session.session_id}\").\n"
                f"You can call read_background_command(session_id=\"{session.session_id}\") repeatedly while it is running.\n"
                f"If you use wait_background_command(session_id=\"{session.session_id}\"), call read_background_command(session_id=\"{session.session_id}\") again after wait finishes to fetch the final Codex result from stdout.\n"
                "Live Codex progress also continues through Codex IPC."
            ),
            metadata={
                "session_id": session.session_id,
                "background_session_id": session.session_id,
                "command": session.command,
                "cwd": str(session.cwd),
                "state": session.state,
                "project_path": str(resolved_project_path),
            },
            raw=background_manager.get_session_raw_payload(session.session_id),
        )

    return Tool(
        name="start_codex_background_session",
        description=(
            "Start a real background shell process that creates a new Codex session and "
            "runs a Codex turn inside it. Safe to use with agents that support background "
            "shell workflows. Important: the returned session id is for the background "
            "command tools, not the Codex thread id. Use read_background_command with "
            "that session id to inspect live progress, and you may call read repeatedly "
            "while the process runs. If you use wait_background_command, call "
            "read_background_command again after wait finishes to fetch the final Codex "
            "result from stdout. Live progress also continues through Codex IPC."
        ),
        parameters=StartCodexBackgroundSessionParams,
        execute=execute,
    )


def create_resume_codex_background_session_tool(
    chat_service: ChatService,
    background_manager: BackgroundCommandManager,
) -> Tool[ResumeCodexBackgroundSessionParams]:
    async def execute(
        params: ResumeCodexBackgroundSessionParams,
        ctx: ToolContext,
    ) -> ToolOutput:
        resolved_project_path = _resolve_codex_background_project_path(
            chat_service,
            ctx.session_id,
            params.project_path,
        )
        request_path = _write_codex_background_request(
            chat_service,
            {
                "action": "resume",
                "caller_session_id": ctx.session_id,
                "thread_id": params.thread_id,
                "prompt": params.prompt,
                "project_path": str(resolved_project_path),
            },
        )
        command = _build_codex_background_runner_command(
            chat_service,
            request_path=request_path,
        )
        session = await background_manager.start(command, str(resolved_project_path))
        await emit_tool_event(
            "background_command_started",
            {
                "session_id": ctx.session_id,
                "tool_call_id": ctx.call_id,
                "tool_name": "resume_codex_background_session",
                "background_session_id": session.session_id,
                "command": session.command,
                "cwd": str(session.cwd),
                "state": session.state,
            },
        )
        return ToolOutput(
            content=(
                f"Started Codex background runner {session.session_id}\n"
                f"Target thread id: {params.thread_id}\n"
                f"Working directory: {session.cwd}\n"
                f"Project path: {resolved_project_path}\n"
                "This returned session id is a background command session id.\n"
                f"To watch live progress now, call read_background_command(session_id=\"{session.session_id}\").\n"
                f"You can call read_background_command(session_id=\"{session.session_id}\") repeatedly while it is running.\n"
                f"If you use wait_background_command(session_id=\"{session.session_id}\"), call read_background_command(session_id=\"{session.session_id}\") again after wait finishes to fetch the final Codex result from stdout.\n"
                "Live Codex progress also continues through Codex IPC."
            ),
            metadata={
                "session_id": session.session_id,
                "background_session_id": session.session_id,
                "thread_id": params.thread_id,
                "command": session.command,
                "cwd": str(session.cwd),
                "state": session.state,
                "project_path": str(resolved_project_path),
            },
            raw=background_manager.get_session_raw_payload(session.session_id),
        )

    return Tool(
        name="resume_codex_background_session",
        description=(
            "Start a real background shell process that resumes an existing Codex thread "
            "by explicit thread id and runs a Codex turn inside it. Safe to use with "
            "agents that support background shell workflows. Important: the returned "
            "session id is for the background command tools, not the Codex thread id. "
            "Use read_background_command with that session id to inspect live progress, "
            "and you may call read repeatedly while the process runs. If you use "
            "wait_background_command, call read_background_command again after wait "
            "finishes to fetch the final Codex result from stdout. Live progress also "
            "continues through Codex IPC."
        ),
        parameters=ResumeCodexBackgroundSessionParams,
        execute=execute,
    )
