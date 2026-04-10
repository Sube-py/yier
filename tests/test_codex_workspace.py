from __future__ import annotations

import json
import os
from pathlib import Path
import socket

from codex_app_server import ThreadSortKey, ThreadSourceKind
from codex_app_server.generated.v2_all import ThreadListResponse, ThreadReadResponse

from yier_web.codex.sdk.workspace import CodexWorkspaceService


class _FakeCodexClient:
    def __init__(
        self,
        responses: list[ThreadListResponse],
        thread_read_response: ThreadReadResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses)
        self.thread_read_response = thread_read_response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def __enter__(self) -> _FakeCodexClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def thread_list(self, **kwargs: object) -> ThreadListResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)

    def thread_read(self, thread_id: str, *, include_turns: bool = False) -> ThreadReadResponse:
        self.calls.append(
            {
                "thread_id": thread_id,
                "include_turns": include_turns,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.thread_read_response is not None
        return self.thread_read_response


class _FakeCodexFactory:
    def __init__(
        self,
        responses: list[ThreadListResponse] | None = None,
        thread_read_response: ThreadReadResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = responses or []
        self.thread_read_response = thread_read_response
        self.error = error
        self.last_config = None
        self.last_client: _FakeCodexClient | None = None

    def __call__(self, *, config) -> _FakeCodexClient:  # type: ignore[no-untyped-def]
        self.last_config = config
        self.last_client = _FakeCodexClient(
            self.responses,
            thread_read_response=self.thread_read_response,
            error=self.error,
        )
        return self.last_client


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


def test_codex_workspace_prefers_sdk_thread_list_and_groups_by_project(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    project_alpha = tmp_path / "project-alpha"
    project_beta = tmp_path / "project-beta"
    project_alpha.mkdir()
    project_beta.mkdir()

    factory = _FakeCodexFactory(
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

    workspace = CodexWorkspaceService(home_dir, codex_factory=factory).load_workspace()

    assert [project.project for project in workspace.projects] == ["project-alpha", "project-beta"]
    assert [session.thread_id for session in workspace.projects[0].sessions] == [
        "thread-alpha-2",
        "thread-alpha-1",
    ]
    assert workspace.projects[0].sessions[0].status == "idle"
    assert workspace.projects[0].session_count == 2
    assert workspace.projects[1].sessions[0].title == "Beta 1"
    assert factory.last_config is not None
    assert factory.last_config.launch_args_override == ("codex", "app-server", "--listen", "stdio://")
    assert factory.last_client is not None
    assert [call["cursor"] for call in factory.last_client.calls] == [None, "cursor-2"]
    assert all(call["archived"] is False for call in factory.last_client.calls)
    assert all(call["sort_key"] == ThreadSortKey.updated_at for call in factory.last_client.calls)
    assert all(
        call["source_kinds"]
        == [
            ThreadSourceKind.cli,
            ThreadSourceKind.vscode,
            ThreadSourceKind.exec,
            ThreadSourceKind.app_server,
        ]
        for call in factory.last_client.calls
    )


def test_codex_workspace_falls_back_to_local_disk_when_sdk_list_fails(tmp_path: Path) -> None:
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

    workspace = CodexWorkspaceService(
        home_dir,
        codex_factory=_FakeCodexFactory(error=RuntimeError("sdk unavailable")),
    ).load_workspace()

    assert [project.project for project in workspace.projects] == ["project-alpha", "project-beta"]
    assert workspace.projects[0].sessions[0].thread_id == "thread-alpha-1"
    assert workspace.projects[0].sessions[0].status == "idle"
    assert workspace.projects[1].sessions[0].title == "Beta 1"


def test_codex_workspace_extracts_custom_sdk_session_source(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir()

    factory = _FakeCodexFactory(
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

    workspace = CodexWorkspaceService(home_dir, codex_factory=factory).load_workspace()

    assert workspace.projects[0].sessions[0].source == "yierShell"


def test_codex_workspace_reads_thread_from_sdk(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    project_root.mkdir()

    factory = _FakeCodexFactory(
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

    response = CodexWorkspaceService(home_dir, codex_factory=factory).read_thread(
        "thread-alpha-1",
        include_turns=True,
    )

    assert response is not None
    assert response.thread.id == "thread-alpha-1"
    assert factory.last_client is not None
    assert factory.last_client.calls == [
        {
            "thread_id": "thread-alpha-1",
            "include_turns": True,
        }
    ]


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
