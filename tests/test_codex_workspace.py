from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket

from codex_app_server import ThreadSortKey, ThreadSourceKind
from codex_app_server.generated.v2_all import ThreadListResponse, ThreadReadResponse

import yier_web.codex.sdk.workspace as workspace_module
from yier_web.codex.sdk.workspace import CodexWorkspaceService


def _patch_async_codex(
    monkeypatch,  # type: ignore[no-untyped-def]
    *,
    responses: list[ThreadListResponse] | None = None,
    thread_read_response: ThreadReadResponse | None = None,
    error: Exception | None = None,
):
    class _FakeAsyncCodex:
        last_instance: _FakeAsyncCodex | None = None
        instance_count = 0
        close_count = 0

        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            self.config = config
            self.responses = list(responses or [])
            self.thread_read_response = thread_read_response
            self.error = error
            self.calls: list[dict[str, object]] = []
            self.enter_count = 0
            self.close_count = 0
            type(self).instance_count += 1
            type(self).last_instance = self

        async def __aenter__(self) -> _FakeAsyncCodex:
            self.enter_count += 1
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def close(self) -> None:
            self.close_count += 1
            type(self).close_count += 1

        async def thread_list(self, **kwargs: object) -> ThreadListResponse:
            self.calls.append(kwargs)
            if self.error is not None:
                raise self.error
            return self.responses.pop(0)

    class _FakeAsyncThread:
        def __init__(self, codex: _FakeAsyncCodex, thread_id: str) -> None:
            self.codex = codex
            self.thread_id = thread_id

        async def read(self, *, include_turns: bool = False) -> ThreadReadResponse:
            self.codex.calls.append(
                {
                    "thread_id": self.thread_id,
                    "include_turns": include_turns,
                }
            )
            if self.codex.error is not None:
                raise self.codex.error
            assert self.codex.thread_read_response is not None
            return self.codex.thread_read_response

    monkeypatch.setattr(workspace_module, "AsyncCodex", _FakeAsyncCodex)
    monkeypatch.setattr(workspace_module, "AsyncThread", _FakeAsyncThread)
    return _FakeAsyncCodex


def _thread_payload(
    *,
    thread_id: str,
    cwd: Path,
    created_at: int,
    updated_at: int,
    preview: str,
    name: str | None = None,
    status: str = "idle",
) -> dict[str, object]:
    return {
        "id": thread_id,
        "cliVersion": "0.116.0",
        "createdAt": created_at,
        "cwd": str(cwd),
        "ephemeral": False,
        "gitInfo": None,
        "modelProvider": "openai",
        "name": name,
        "path": str(cwd / f"{thread_id}.jsonl"),
        "preview": preview,
        "source": "cli",
        "status": {"type": status},
        "turns": [],
        "updatedAt": updated_at,
    }


def _thread_list_response(
    payloads: list[dict[str, object]],
    *,
    next_cursor: str | None = None,
) -> ThreadListResponse:
    return ThreadListResponse.model_validate(
        {
            "data": payloads,
            "nextCursor": next_cursor,
        }
    )


def _thread_read_response(payload: dict[str, object]) -> ThreadReadResponse:
    return ThreadReadResponse.model_validate({"thread": payload})


def _write_codex_session(
    sessions_dir: Path,
    *,
    session_id: str,
    cwd: Path,
    started_at: str,
    updated_at: str,
    message: str,
) -> None:
    session_file = sessions_dir / f"{session_id}.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "timestamp": updated_at,
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": started_at,
                "cwd": str(cwd),
            },
        },
        {
            "timestamp": updated_at,
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": message,
            },
        },
    ]
    session_file.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def test_codex_workspace_prefers_sdk_thread_list_and_groups_by_project(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    home_dir = tmp_path / "home"
    project_alpha = tmp_path / "project-alpha"
    project_beta = tmp_path / "project-beta"
    project_alpha.mkdir()
    project_beta.mkdir()

    fake_codex = _patch_async_codex(
        monkeypatch,
        responses=[
            _thread_list_response(
                [
                    _thread_payload(
                        thread_id="thread-alpha-1",
                        cwd=project_alpha,
                        created_at=100,
                        updated_at=300,
                        preview="alpha work",
                        name="Alpha 1",
                    ),
                    _thread_payload(
                        thread_id="thread-alpha-2",
                        cwd=project_alpha,
                        created_at=200,
                        updated_at=400,
                        preview="alpha follow-up",
                        name="Alpha 2",
                    ),
                ],
                next_cursor="cursor-2",
            ),
            _thread_list_response(
                [
                    _thread_payload(
                        thread_id="thread-beta-1",
                        cwd=project_beta,
                        created_at=150,
                        updated_at=250,
                        preview="beta work",
                        name="Beta 1",
                    )
                ]
            ),
        ]
    )

    workspace = asyncio.run(CodexWorkspaceService(home_dir).load_workspace())

    assert [project.project for project in workspace.projects] == ["project-alpha", "project-beta"]
    assert [session.thread_id for session in workspace.projects[0].sessions] == [
        "thread-alpha-2",
        "thread-alpha-1",
    ]
    assert workspace.projects[0].sessions[0].status == "idle"
    assert workspace.projects[0].session_count == 2
    assert workspace.projects[1].sessions[0].title == "Beta 1"
    assert fake_codex.last_instance is not None
    assert fake_codex.last_instance.config.launch_args_override == (
        "codex",
        "app-server",
        "--listen",
        "stdio://",
    )
    assert [call["cursor"] for call in fake_codex.last_instance.calls] == [None, "cursor-2"]
    assert all(call["archived"] is False for call in fake_codex.last_instance.calls)
    assert all(
        call["sort_key"] == ThreadSortKey.updated_at
        for call in fake_codex.last_instance.calls
    )
    assert all(
        call["source_kinds"]
        == [
            ThreadSourceKind.cli,
            ThreadSourceKind.vscode,
            ThreadSourceKind.exec,
            ThreadSourceKind.app_server,
        ]
        for call in fake_codex.last_instance.calls
    )


def test_codex_workspace_falls_back_to_local_disk_when_sdk_list_fails(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    home_dir = tmp_path / "home"
    project_alpha = tmp_path / "project-alpha"
    project_beta = tmp_path / "project-beta"
    project_alpha.mkdir()
    project_beta.mkdir()

    codex_home = home_dir / ".codex"
    sessions_dir = codex_home / "sessions" / "2026" / "03" / "24"
    _write_codex_session(
        sessions_dir,
        session_id="thread-alpha-1",
        cwd=project_alpha,
        started_at="2026-03-24T01:00:00Z",
        updated_at="2026-03-24T03:00:00Z",
        message="alpha work",
    )
    _write_codex_session(
        sessions_dir,
        session_id="thread-beta-1",
        cwd=project_beta,
        started_at="2026-03-24T01:30:00Z",
        updated_at="2026-03-24T02:30:00Z",
        message="beta work",
    )
    (codex_home / "session_index.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "thread-alpha-1",
                        "thread_name": "Alpha 1",
                        "updated_at": "2026-03-24T03:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "id": "thread-beta-1",
                        "thread_name": "Beta 1",
                        "updated_at": "2026-03-24T02:30:00Z",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    _patch_async_codex(monkeypatch, error=RuntimeError("sdk unavailable"))
    workspace = asyncio.run(CodexWorkspaceService(home_dir).load_workspace())

    assert [project.project for project in workspace.projects] == ["project-alpha", "project-beta"]
    assert workspace.projects[0].sessions[0].thread_id == "thread-alpha-1"
    assert workspace.projects[0].sessions[0].status == "idle"
    assert workspace.projects[1].sessions[0].title == "Beta 1"


def test_codex_workspace_extracts_custom_sdk_session_source(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir()

    _patch_async_codex(
        monkeypatch,
        responses=[
            _thread_list_response(
                [
                    {
                        **_thread_payload(
                            thread_id="thread-custom-1",
                            cwd=project_root,
                            created_at=100,
                            updated_at=200,
                            preview="custom source",
                            name="Custom Source",
                        ),
                        "source": {"custom": "yierShell"},
                    }
                ]
            )
        ]
    )

    workspace = asyncio.run(CodexWorkspaceService(home_dir).load_workspace())

    assert workspace.projects[0].sessions[0].source == "yierShell"


def test_codex_workspace_reads_thread_from_sdk(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir()

    fake_codex = _patch_async_codex(
        monkeypatch,
        thread_read_response=_thread_read_response(
            _thread_payload(
                thread_id="thread-alpha-1",
                cwd=project_root,
                created_at=100,
                updated_at=300,
                preview="alpha work",
                name="Alpha 1",
            )
        )
    )

    response = asyncio.run(
        CodexWorkspaceService(home_dir).read_thread(
            "thread-alpha-1",
            include_turns=True,
        )
    )

    assert response is not None
    assert response.thread.id == "thread-alpha-1"
    assert fake_codex.last_instance is not None
    assert fake_codex.last_instance.calls == [
        {
            "thread_id": "thread-alpha-1",
            "include_turns": True,
        }
    ]


def test_codex_workspace_reuses_shared_async_codex_for_multiple_sdk_calls(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir()

    fake_codex = _patch_async_codex(
        monkeypatch,
        responses=[
            _thread_list_response(
                [
                    _thread_payload(
                        thread_id="thread-alpha-1",
                        cwd=project_root,
                        created_at=100,
                        updated_at=300,
                        preview="alpha work",
                        name="Alpha 1",
                    )
                ]
            ),
            _thread_list_response(
                [
                    _thread_payload(
                        thread_id="thread-alpha-2",
                        cwd=project_root,
                        created_at=150,
                        updated_at=350,
                        preview="alpha follow-up",
                        name="Alpha 2",
                    )
                ]
            ),
        ],
    )

    service = CodexWorkspaceService(home_dir)
    first_workspace = asyncio.run(service.load_workspace())
    second_workspace = asyncio.run(service.load_workspace())

    assert first_workspace.projects[0].sessions[0].thread_id == "thread-alpha-1"
    assert second_workspace.projects[0].sessions[0].thread_id == "thread-alpha-2"
    assert fake_codex.instance_count == 1
    assert fake_codex.last_instance is not None
    assert fake_codex.last_instance.enter_count == 1


def test_codex_workspace_stop_closes_shared_async_codex(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir()

    fake_codex = _patch_async_codex(
        monkeypatch,
        responses=[
            _thread_list_response(
                [
                    _thread_payload(
                        thread_id="thread-alpha-1",
                        cwd=project_root,
                        created_at=100,
                        updated_at=300,
                        preview="alpha work",
                        name="Alpha 1",
                    )
                ]
            )
        ],
    )

    service = CodexWorkspaceService(home_dir)
    asyncio.run(service.load_workspace())
    asyncio.run(service.stop())

    assert fake_codex.instance_count == 1
    assert fake_codex.close_count == 1


def test_codex_workspace_lists_online_paired_editors_only(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    pairing_dir = (
        home_dir
        / "Library"
        / "Application Support"
        / "com.openai.chat"
        / "app_pairing_extensions"
    )
    pairing_dir.mkdir(parents=True)

    online_socket = Path(f"/tmp/yier-online-{os.getpid()}.sock")
    if online_socket.exists():
        online_socket.unlink()
    online_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    online_server.bind(str(online_socket))

    newer_socket = Path(f"/tmp/yier-newer-{os.getpid()}.sock")
    if newer_socket.exists():
        newer_socket.unlink()
    newer_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    newer_server.bind(str(newer_socket))

    try:
        (pairing_dir / "vscode-online.json").write_text(
            json.dumps(
                {
                    "id": "editor-online",
                    "appName": "Visual Studio Code",
                    "workspaceName": "yier",
                    "extensionName": "oai_pwai Visual Studio Code",
                    "extensionVersion": "0.0.1731016154",
                    "bundleID": "com.microsoft.VSCode",
                    "marketplaceID": "openai.chatgpt",
                    "capabilities": {
                        "content": 1,
                        "highlightLines": 1,
                    },
                    "needsReload": False,
                    "socketPath": str(online_socket),
                    "timestamp": 1774000000000,
                }
            ),
            encoding="utf-8",
        )
        (pairing_dir / "vscode-stale.json").write_text(
            json.dumps(
                {
                    "id": "editor-stale",
                    "appName": "Visual Studio Code",
                    "workspaceName": "stale",
                    "extensionName": "oai_pwai Visual Studio Code",
                    "extensionVersion": "0.0.1731016154",
                    "bundleID": "com.microsoft.VSCode",
                    "marketplaceID": "openai.chatgpt",
                    "capabilities": {
                        "content": 1,
                    },
                    "needsReload": True,
                    "socketPath": str(tmp_path / "missing.sock"),
                    "timestamp": 1773000000000,
                }
            ),
            encoding="utf-8",
        )
        (pairing_dir / "vscode-newer.json").write_text(
            json.dumps(
                {
                    "id": "editor-newer",
                    "appName": "Visual Studio Code",
                    "workspaceName": "codex",
                    "extensionName": "oai_pwai Visual Studio Code",
                    "extensionVersion": "0.0.1731016154",
                    "bundleID": "com.microsoft.VSCode",
                    "marketplaceID": "openai.chatgpt",
                    "capabilities": {
                        "ping": 1,
                        "replaceSelection": 1,
                        "reload": 1,
                    },
                    "needsReload": True,
                    "socketPath": str(newer_socket),
                    "timestamp": 1775000000000,
                }
            ),
            encoding="utf-8",
        )

        paired_editors = CodexWorkspaceService(home_dir).list_paired_editors()
    finally:
        newer_server.close()
        online_server.close()
        if newer_socket.exists():
            newer_socket.unlink()
        if online_socket.exists():
            online_socket.unlink()

    assert [editor.id for editor in paired_editors] == ["editor-newer", "editor-online"]
    assert paired_editors[0].workspace_name == "codex"
    assert paired_editors[0].needs_reload is True
    assert paired_editors[0].capability_count == 3
    assert paired_editors[1].capability_names == ["content", "highlightLines"]
