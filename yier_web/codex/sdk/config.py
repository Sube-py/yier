from __future__ import annotations

from pathlib import Path
import shlex
import sys
from typing import Any

from codex_app_server import AppServerConfig


DEFAULT_CODEX_LAUNCHER = "codex app-server --listen stdio://"
PLAN_MODE_PROMPT_PREFIX = "<yier-codex-plan-mode>"
PLAN_MODE_PROMPT = (
    f"{PLAN_MODE_PROMPT_PREFIX}\n"
    "Work in planning mode for this request. Analyze the task, avoid making changes, "
    "and return a concrete implementation plan that another engineer could execute."
)

_CODEX_THREAD_SANDBOX_MODE_MAP = {
    "read-only": "read-only",
    "workspace-write": "workspace-write",
    "danger-full-access": "danger-full-access",
    "readOnly": "read-only",
    "workspaceWrite": "workspace-write",
    "dangerFullAccess": "danger-full-access",
}
_CODEX_TURN_SANDBOX_POLICY_TYPE_MAP = {
    "read-only": "readOnly",
    "workspace-write": "workspaceWrite",
    "danger-full-access": "dangerFullAccess",
    "readOnly": "readOnly",
    "workspaceWrite": "workspaceWrite",
    "dangerFullAccess": "dangerFullAccess",
    "externalSandbox": "externalSandbox",
}


def normalize_codex_thread_sandbox_mode(value: str) -> str:
    normalized = value.strip()
    sandbox_mode = _CODEX_THREAD_SANDBOX_MODE_MAP.get(normalized)
    if sandbox_mode is None:
        raise ValueError(f"Unsupported Codex thread sandbox mode: {value}")
    return sandbox_mode


def normalize_codex_turn_sandbox_policy_type(value: str) -> str:
    normalized = value.strip()
    sandbox_mode = _CODEX_TURN_SANDBOX_POLICY_TYPE_MAP.get(normalized)
    if sandbox_mode is None:
        raise ValueError(f"Unsupported Codex turn sandbox policy type: {value}")
    return sandbox_mode


def build_app_server_config(
    *,
    launcher_command: str,
    cwd: str,
    client_name: str,
    client_title: str,
) -> AppServerConfig:
    launch_args = tuple(shlex.split(launcher_command))
    if not launch_args:
        raise RuntimeError("Codex launcher command is empty.")
    return AppServerConfig(
        launch_args_override=launch_args,
        cwd=cwd,
        client_name=client_name,
        client_title=client_title,
    )


def build_pairing_mcp_config(
    *,
    server_name: str,
    project_root: Path | None,
    home_dir: Path | None,
    command: str | None = None,
    module_path: str = "yier_web.codex.pairing.mcp",
) -> dict[str, Any] | None:
    if not isinstance(project_root, Path) or not isinstance(home_dir, Path):
        return None
    return {
        "mcp_servers": {
            server_name: {
                "command": command or sys.executable,
                "args": ["-m", module_path],
                "cwd": str(project_root.resolve()),
                "env": {
                    "YIER_PAIRING_HOME_DIR": str(home_dir.resolve()),
                },
                "startup_timeout_sec": 5,
                "tool_timeout_sec": 30,
            }
        }
    }
