from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from yier_web.agent_backends.base import ChatSessionContext
from yier_web.agent_backends.codex_backend import (
    CodexAppServerBackend,
    _codex_thread_sandbox_mode,
    _codex_turn_sandbox_policy_type,
)
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
    assert turn_params["sandboxPolicy"] == {"type": "workspaceWrite"}
