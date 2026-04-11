from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from pathlib import Path
import signal
import sys
import traceback
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from yier_web.chat import ChatService
from yier_web.config import AppConfigService


def _emit_json(payload: dict[str, object], *, stream: object | None = None) -> None:
    output_stream = stream if stream is not None else sys.stdout
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=output_stream,
        flush=True,
    )


class CodexBackgroundRunnerRequest(BaseModel):
    action: Literal["start", "resume"] = Field(
        description="Whether to create a new Codex session or resume an existing thread."
    )
    caller_session_id: str = Field(
        description="Caller session id to inherit source metadata from."
    )
    thread_id: str | None = Field(
        default=None,
        description="Existing Codex thread id when resuming a session.",
    )
    prompt: str = Field(description="Prompt to send to Codex.")
    project_path: str | None = Field(
        default=None,
        description="Optional project path override for the Codex session.",
    )

    @field_validator("caller_session_id")
    @classmethod
    def validate_caller_session_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Caller session id must not be empty.")
        return normalized

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

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

    def validate_resume_requirements(self) -> None:
        if self.action == "resume" and not self.thread_id:
            raise ValueError("Thread id is required when action is resume.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one Codex session start/resume flow inside a background subprocess."
    )
    parser.add_argument("--app-project-root", required=True)
    parser.add_argument("--home-dir", required=True)
    parser.add_argument("--request-file", required=True)
    return parser.parse_args(argv)


def load_request(request_file: Path) -> CodexBackgroundRunnerRequest:
    payload = json.loads(request_file.read_text(encoding="utf-8"))
    request = CodexBackgroundRunnerRequest.model_validate(payload)
    request.validate_resume_requirements()
    return request


def _install_shutdown_handlers(shutdown_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, shutdown_event.set)


def _final_exit_code(status: str) -> int:
    if status in {"failed", "error"}:
        return 1
    return 0


async def _forward_stream_events(
    *,
    chat_service: ChatService,
    target_session_id: list[str | None],
    stop_event: asyncio.Event,
) -> None:
    subscriber = chat_service.event_broker.subscribe()
    try:
        while True:
            wait_task = asyncio.create_task(subscriber.get())
            stop_task = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                [wait_task, stop_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for pending_task in pending:
                pending_task.cancel()
            for pending_task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await pending_task

            if stop_task in done:
                return

            item = wait_task.result()
            session_id = item.data.get("session_id")
            if not isinstance(session_id, str):
                continue
            if session_id != target_session_id[0]:
                continue
            _emit_json(
                {
                    "event": "codex_background_stream_event",
                    "source_event": item.event,
                    "session_id": session_id,
                    "data": item.data,
                }
            )
    finally:
        chat_service.event_broker.unsubscribe(subscriber)


async def run_request(
    *,
    app_project_root: Path,
    home_dir: Path,
    request: CodexBackgroundRunnerRequest,
) -> int:
    config_service = AppConfigService(
        project_root=app_project_root.resolve(),
        home_dir=home_dir.resolve(),
    )
    chat_service = ChatService(
        project_root=app_project_root.resolve(),
        config_service=config_service,
    )
    shutdown_event = asyncio.Event()
    broker_stop_event = asyncio.Event()
    target_session_id: list[str | None] = [None]
    _install_shutdown_handlers(shutdown_event)

    await chat_service.start()
    broker_task = asyncio.create_task(
        _forward_stream_events(
            chat_service=chat_service,
            target_session_id=target_session_id,
            stop_event=broker_stop_event,
        )
    )
    try:
        _emit_json(
            {
                "event": "codex_background_runner_started",
                "action": request.action,
                "caller_session_id": request.caller_session_id,
                "thread_id": request.thread_id,
                "project_path": request.project_path,
            }
        )
        if request.action == "start":
            result = await chat_service.start_codex_background_session_from_tool(
                caller_session_id=request.caller_session_id,
                prompt=request.prompt,
                project_path=request.project_path,
            )
        else:
            result = await chat_service.resume_codex_background_session_from_tool(
                caller_session_id=request.caller_session_id,
                thread_id=request.thread_id or "",
                prompt=request.prompt,
                project_path=request.project_path,
            )

        target_session_id[0] = result["session_id"]
        _emit_json(
            {
                "event": "codex_background_started",
                **result,
            }
        )

        session_id = result["session_id"]
        task = chat_service._ipc_stream_tasks.get(session_id)
        if task is not None:
            shutdown_task = asyncio.create_task(shutdown_event.wait())
            done, pending = await asyncio.wait(
                [task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for pending_task in pending:
                pending_task.cancel()
            for pending_task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await pending_task
            if shutdown_task in done:
                _emit_json(
                    {
                        "event": "codex_background_runner_stopping",
                        "reason": "signal",
                        "session_id": session_id,
                    }
                )
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                return 143
            if task.done():
                exception = task.exception()
                if exception is not None:
                    raise exception

        metadata = chat_service.get_session_metadata(session_id)
        backend_state = metadata.get("backend_state", {})
        status = str(backend_state.get("status") or result.get("status") or "completed")
        finish_payload = {
            "event": "codex_background_finished",
            "session_id": session_id,
            "thread_id": str(backend_state.get("thread_id") or result["thread_id"]),
            "project_path": metadata["project_path"],
            "status": status,
        }
        _emit_json(finish_payload)
        transcript_messages = [
            message.model_dump(mode="json")
            for message in chat_service.build_transcript_messages(session_id)
        ]
        _emit_json(
            {
                "event": "codex_background_result",
                "ok": status not in {"failed", "error"},
                "result": {
                    **finish_payload,
                    "messages": transcript_messages,
                    "latest_assistant_message": next(
                        (
                            message["content"]
                            for message in reversed(transcript_messages)
                            if message.get("role") == "assistant"
                            and message.get("content")
                        ),
                        None,
                    ),
                },
            }
        )
        return _final_exit_code(status)
    finally:
        broker_stop_event.set()
        broker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await broker_task
        await chat_service.stop()


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    request_file = Path(args.request_file).resolve()
    try:
        request = load_request(request_file)
    finally:
        request_file.unlink(missing_ok=True)

    return await run_request(
        app_project_root=Path(args.app_project_root),
        home_dir=Path(args.home_dir),
        request=request,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except ValidationError as exc:
        _emit_json(
            {
                "event": "codex_background_runner_error",
                "error_type": "validation_error",
                "message": str(exc),
            },
            stream=sys.stderr,
        )
        return 1
    except Exception as exc:
        _emit_json(
            {
                "event": "codex_background_runner_error",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc().splitlines(),
            },
            stream=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
