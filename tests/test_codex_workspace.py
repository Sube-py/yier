from __future__ import annotations

import json
from pathlib import Path

from yier_web.codex_workspace import CodexWorkspaceService


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


def test_codex_workspace_groups_native_sessions_by_project(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    project_alpha = tmp_path / "project-alpha"
    project_beta = tmp_path / "project-beta"
    (project_alpha / ".git").mkdir(parents=True)
    (project_beta / ".git").mkdir(parents=True)

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
        session_id="thread-alpha-2",
        cwd=project_alpha,
        started_at="2026-03-24T02:00:00Z",
        updated_at="2026-03-24T04:00:00Z",
        message="alpha follow-up",
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
                json.dumps({"id": "thread-alpha-1", "thread_name": "Alpha 1", "updated_at": "2026-03-24T03:00:00Z"}),
                json.dumps({"id": "thread-alpha-2", "thread_name": "Alpha 2", "updated_at": "2026-03-24T04:00:00Z"}),
                json.dumps({"id": "thread-beta-1", "thread_name": "Beta 1", "updated_at": "2026-03-24T02:30:00Z"}),
            ]
        ),
        encoding="utf-8",
    )

    workspace = CodexWorkspaceService(home_dir).load_workspace()

    assert [project.project for project in workspace.projects] == ["project-alpha", "project-beta"]
    assert [session.thread_id for session in workspace.projects[0].sessions] == [
        "thread-alpha-2",
        "thread-alpha-1",
    ]
    assert workspace.projects[0].session_count == 2
    assert workspace.projects[1].sessions[0].title == "Beta 1"
