from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from yier_agents import (
    BackgroundCommandManager,
    RunCommandParams,
    Tool,
    ToolContext,
    ToolOutput,
)
from yier_agents.src.tools.background_command import START_BACKGROUND_COMMAND_DESCRIPTION, StartBackgroundCommandParams
from yier_agents.src.tools.workspace import (
    RUN_COMMAND_DESCRIPTION,
    WorkspaceAccess,
    _default_shell_program,
    _normalize_allowed_roots,
    _normalize_default_root,
    _validate_command,
)

from yier_web.tool_events import emit_tool_event


def create_streaming_run_command_tool(
    allowed_roots: list[Path] | None = None,
    default_root: Path | None = None,
    *,
    allow_shell: bool = False,
    shell_program: str | None = None,
) -> Tool[RunCommandParams]:
    access = WorkspaceAccess(
        allowed_roots=_normalize_allowed_roots(allowed_roots),
        default_root=_normalize_default_root(default_root),
    )
    resolved_shell_program = shell_program or _default_shell_program()

    async def execute(params: RunCommandParams, ctx: ToolContext) -> ToolOutput:
        _validate_command(params.command, allow_shell=allow_shell)

        default_cwd = access.default_root or Path.cwd()
        working_directory = access.resolve_file(params.cwd or str(default_cwd))
        if not working_directory.exists():
            raise FileNotFoundError(f"Working directory not found: {working_directory}")
        if not working_directory.is_dir():
            raise NotADirectoryError(f"Working directory is not a directory: {working_directory}")

        await emit_tool_event(
            "command_start",
            {
                "session_id": ctx.session_id,
                "tool_call_id": ctx.call_id,
                "tool_name": "run_command",
                "command": params.command,
                "cwd": str(working_directory),
                "is_background": False,
            },
        )

        if allow_shell:
            process = await asyncio.create_subprocess_shell(
                params.command,
                cwd=str(working_directory),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                executable=resolved_shell_program,
            )
        else:
            argv = shlex.split(params.command)
            if not argv:
                raise ValueError("Command must not be empty")
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(working_directory),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        async def consume_stream(
            stream: asyncio.StreamReader | None,
            stream_name: str,
            sink: list[str],
        ) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                sink.append(text)
                await emit_tool_event(
                    "command_output",
                    {
                        "session_id": ctx.session_id,
                        "tool_call_id": ctx.call_id,
                        "tool_name": "run_command",
                        "stream": stream_name,
                        "content": text,
                        "is_background": False,
                    },
                )

        readers = [
            asyncio.create_task(consume_stream(process.stdout, "stdout", stdout_chunks)),
            asyncio.create_task(consume_stream(process.stderr, "stderr", stderr_chunks)),
        ]

        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=params.timeout_seconds)
        except TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()

        await asyncio.gather(*readers)

        stdout_text = "".join(stdout_chunks)
        stderr_text = "".join(stderr_chunks)
        exit_code = process.returncode if process.returncode is not None else -1

        await emit_tool_event(
            "command_end",
            {
                "session_id": ctx.session_id,
                "tool_call_id": ctx.call_id,
                "tool_name": "run_command",
                "command": params.command,
                "cwd": str(working_directory),
                "exit_code": exit_code,
                "timed_out": timed_out,
                "is_background": False,
            },
        )

        sections = [
            f"Command: {params.command}",
            f"Working directory: {working_directory}",
            f"Exit code: {exit_code}",
        ]
        if timed_out:
            sections.append(f"Timed out after {params.timeout_seconds} seconds")
        if stdout_text:
            sections.extend(["", "[stdout]", stdout_text])
        if stderr_text:
            sections.extend(["", "[stderr]", stderr_text])

        content = "\n".join(sections)
        truncated = False
        if len(content) > params.max_output_chars:
            content = f"{content[:params.max_output_chars]}\n... [truncated]"
            truncated = True

        return ToolOutput(
            content=content,
            metadata={
                "command": params.command,
                "cwd": str(working_directory),
                "exit_code": exit_code,
                "timed_out": timed_out,
                "truncated": truncated,
                "allow_shell": allow_shell,
            },
        )

    return Tool(
        name="run_command",
        description=RUN_COMMAND_DESCRIPTION,
        parameters=RunCommandParams,
        execute=execute,
    )


def create_streaming_start_background_command_tool(
    manager: BackgroundCommandManager,
) -> Tool[StartBackgroundCommandParams]:
    async def execute(
        params: StartBackgroundCommandParams,
        ctx: ToolContext,
    ) -> ToolOutput:
        session = await manager.start(params.command, params.cwd)
        await emit_tool_event(
            "background_command_started",
            {
                "session_id": ctx.session_id,
                "tool_call_id": ctx.call_id,
                "tool_name": "start_background_command",
                "background_session_id": session.session_id,
                "command": session.command,
                "cwd": str(session.cwd),
                "state": session.state,
            },
        )
        return ToolOutput(
            content=(
                f"Started background command {session.session_id}\n"
                f"Command: {session.command}\n"
                f"Working directory: {session.cwd}\n"
                "Use list_background_commands, read_background_command, wait_background_command, "
                "or stop_background_command to manage it."
            ),
            metadata={
                "session_id": session.session_id,
                "command": session.command,
                "cwd": str(session.cwd),
                "state": session.state,
            },
        )

    return Tool(
        name="start_background_command",
        description=START_BACKGROUND_COMMAND_DESCRIPTION,
        parameters=StartBackgroundCommandParams,
        execute=execute,
    )
