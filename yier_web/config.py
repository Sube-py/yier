from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
from typing import Any

from pydantic import ValidationError

from yier_agents.src.config import (
    AssistantRunCommandSettings,
    AssistantSettings,
    YIERConfig,
)

from yier_web.schemas import (
    BackendHealth,
    BackendOption,
    CodexConfigPayload,
    ConfigResponse,
    LLMConfigPayload,
    MCPRuntimeEntry,
    SaveAppSettingsRequest,
    SaveLLMRequest,
    WebSettings,
)


RUNTIME_STATUSES = {
    "connected",
    "disabled",
    "failed",
    "needs_auth",
    "needs_client_registration",
}

PROVIDER_BASE_URLS = {
    "zai": "https://api.z.ai/api/paas/v4",
    "zai-coding-plan": "https://api.z.ai/api/coding/paas/v4",
}


class MCPValidationError(ValueError):
    """Raised when MCP configuration payloads are malformed."""


class AppConfigService:
    def __init__(self, project_root: Path, home_dir: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.home_dir = (home_dir or Path.home()).resolve()
        self.yier_root = self.home_dir / ".yier"
        self.web_root = self.yier_root / "web"
        self.settings_path = self.web_root / "settings.json"
        self.sessions_path = self.web_root / "sessions"
        self.transcripts_path = self.web_root / "transcripts"
        self.session_ui_path = self.web_root / "session_ui"
        self.session_meta_path = self.web_root / "session_meta"
        self.session_conversation_state_path = (
            self.web_root / "session_conversation_state"
        )
        self.uploads_path = self.web_root / "uploads"
        self.prompt_history_path = self.web_root / "prompt_history.txt"
        self.mcp_config_path = self.yier_root / ".yier.json"
        self.ensure_storage()

    def ensure_storage(self) -> None:
        self.yier_root.mkdir(parents=True, exist_ok=True)
        self.web_root.mkdir(parents=True, exist_ok=True)
        self.sessions_path.mkdir(parents=True, exist_ok=True)
        self.transcripts_path.mkdir(parents=True, exist_ok=True)
        self.session_ui_path.mkdir(parents=True, exist_ok=True)
        self.session_meta_path.mkdir(parents=True, exist_ok=True)
        self.session_conversation_state_path.mkdir(parents=True, exist_ok=True)
        self.uploads_path.mkdir(parents=True, exist_ok=True)

    def default_allowed_roots(self) -> list[str]:
        defaults = [
            self.project_root,
            self.yier_root,
            self.home_dir / "Desktop",
            self.home_dir / "Documents",
            self.home_dir / "Downloads",
        ]
        unique_roots: list[str] = []
        seen: set[str] = set()
        for root in defaults:
            resolved = root.resolve()
            serialized = str(resolved)
            if serialized in seen:
                continue
            seen.add(serialized)
            unique_roots.append(serialized)
        return unique_roots

    def load_web_settings(self) -> WebSettings:
        if not self.settings_path.exists():
            return self._finalize_web_settings(
                WebSettings(allowed_roots=self.default_allowed_roots())
            )

        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            settings = WebSettings.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return self._finalize_web_settings(
                WebSettings(allowed_roots=self.default_allowed_roots())
            )

        return self._finalize_web_settings(settings)

    def save_llm_settings(self, payload: SaveLLMRequest) -> WebSettings:
        settings = self.load_web_settings()
        settings.llm.provider = payload.provider
        settings.llm.base_url = payload.base_url
        settings.llm.model = payload.model
        if payload.api_key is not None and payload.api_key != "":
            settings.llm.api_key = payload.api_key

        settings.allowed_roots = self._normalize_allowed_roots(settings.allowed_roots)
        if not settings.allowed_roots:
            settings.allowed_roots = self.default_allowed_roots()

        self._write_json(self.settings_path, settings.model_dump())
        return settings

    def save_allowed_roots(self, allowed_roots: list[str]) -> WebSettings:
        settings = self.load_web_settings()
        normalized_roots = self._normalize_allowed_roots(allowed_roots)
        settings.allowed_roots = normalized_roots or self.default_allowed_roots()
        self._write_json(self.settings_path, settings.model_dump())
        return settings

    def save_app_settings(self, payload: SaveAppSettingsRequest) -> WebSettings:
        settings = self.load_web_settings()
        settings.session_defaults = payload.session_defaults
        settings.codex = payload.codex
        settings.allowed_roots = self._normalize_allowed_roots(settings.allowed_roots)
        if not settings.allowed_roots:
            settings.allowed_roots = self.default_allowed_roots()
        settings = self._finalize_web_settings(settings)
        self._write_json(self.settings_path, settings.model_dump())
        return settings

    def build_assistant_settings(self) -> AssistantSettings:
        settings = self.load_web_settings()
        return AssistantSettings(
            workspace_root=self.project_root,
            session_storage_path=self.sessions_path,
            prompt_history_path=self.prompt_history_path,
            allowed_roots=tuple(
                Path(root).expanduser().resolve()
                for root in self._normalize_allowed_roots(settings.allowed_roots)
            ),
            include_skill_directories_in_allowed_roots=True,
            max_iterations=100,
            system_prompt=(
                "You are Yier, a lightweight local-first desktop assistant. "
                "Help the user manage files, inspect code, write code, and operate "
                "carefully inside the allowed filesystem roots. Be concise, practical, "
                "and explicit about actions you take."
            ),
            run_command=AssistantRunCommandSettings(
                allow_shell=True,
                shell_program="/bin/bash",
            ),
        )

    def load_mcp_root_config(self) -> dict[str, Any]:
        return YIERConfig.load_config(self.yier_root)

    def load_mcp_servers(self) -> dict[str, dict[str, Any]]:
        raw = self.load_mcp_root_config().get("mcpServers", {})
        if isinstance(raw, dict):
            return {
                str(key): value for key, value in raw.items() if isinstance(value, dict)
            }
        return {}

    def save_mcp_servers(
        self, mcp_servers: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        normalized = self._normalize_mcp_servers(mcp_servers)
        payload = self.load_mcp_root_config()
        payload["mcpServers"] = normalized
        self._write_json(self.mcp_config_path, payload)
        return normalized

    def settings_marker(self) -> tuple[bool, int, int]:
        return self._path_marker(self.settings_path)

    def mcp_marker(self) -> tuple[bool, int, int]:
        return self._path_marker(self.mcp_config_path)

    def build_public_config(
        self,
        mcp_runtime: dict[str, MCPRuntimeEntry],
    ) -> ConfigResponse:
        settings = self.load_web_settings()
        return ConfigResponse(
            llm=LLMConfigPayload(
                provider=settings.llm.provider,
                base_url=settings.llm.base_url,
                model=settings.llm.model,
                has_api_key=bool(settings.llm.api_key),
            ),
            backends=self.backend_options(),
            session_defaults=settings.session_defaults,
            codex=CodexConfigPayload(
                launcher_command=settings.codex.launcher_command,
                model=settings.codex.model,
                sandbox=settings.codex.sandbox,
                approval_policy=settings.codex.approval_policy,
                approvals_reviewer=settings.codex.approvals_reviewer,
                personality=settings.codex.personality,
                reasoning_effort=settings.codex.reasoning_effort,
                show_reasoning_cards=settings.codex.show_reasoning_cards,
                service_tier=settings.codex.service_tier,
            ),
            allowed_roots=settings.allowed_roots,
            mcp_runtime=mcp_runtime,
        )

    def backend_options(self) -> list[BackendOption]:
        return [
            BackendOption(id="yier", label="Yier Agent"),
            BackendOption(id="codex", label="Codex App Server"),
        ]

    def build_backend_health(self) -> dict[str, BackendHealth]:
        settings = self.load_web_settings()
        codex_command = (
            settings.codex.launcher_command or "codex app-server --listen stdio://"
        )
        codex_binary = self._resolve_command_binary(codex_command)
        return {
            "yier": BackendHealth(
                ready=settings.llm.is_ready,
                detail=(
                    None
                    if settings.llm.is_ready
                    else "Configure provider/API key/model in Settings to use the built-in Yier backend."
                ),
            ),
            "codex": BackendHealth(
                ready=codex_binary is not None,
                detail=(
                    None
                    if codex_binary is not None
                    else f"Cannot find the launcher for `{codex_command}`. Install Codex or update the launcher command."
                ),
            ),
        }

    def _normalize_mcp_servers(
        self,
        mcp_servers: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for name, server in mcp_servers.items():
            normalized_name = str(name).strip()
            if not normalized_name:
                raise MCPValidationError("MCP server names cannot be empty.")
            if not isinstance(server, dict):
                raise MCPValidationError(
                    f"MCP server '{normalized_name}' must be an object."
                )
            normalized[normalized_name] = self._normalize_mcp_server(
                normalized_name, server
            )
        return normalized

    def _normalize_mcp_server(
        self, name: str, server: dict[str, Any]
    ) -> dict[str, Any]:
        server_type = server.get("type")
        if server_type not in {"stdio", "http", "sse"}:
            raise MCPValidationError(f"MCP server '{name}' has an invalid type.")

        normalized: dict[str, Any] = {"type": server_type}
        enabled = server.get("enabled", True)
        if not isinstance(enabled, bool):
            raise MCPValidationError(
                f"MCP server '{name}' has a non-boolean 'enabled' value."
            )
        normalized["enabled"] = enabled

        status = server.get("status")
        if status is not None:
            if status not in RUNTIME_STATUSES:
                raise MCPValidationError(
                    f"MCP server '{name}' has an invalid status value."
                )
            normalized["status"] = status

        if server_type == "stdio":
            command = server.get("command", "")
            if not isinstance(command, str) or not command.strip():
                raise MCPValidationError(
                    f"MCP server '{name}' must define a stdio command."
                )
            normalized["command"] = command.strip()
            normalized["args"] = self._coerce_string_list(
                server.get("args", []), name, "args"
            )
            env = server.get("env", {})
            normalized["env"] = self._coerce_string_map(env, name, "env")
        else:
            url = server.get("url", "")
            if not isinstance(url, str) or not url.strip():
                raise MCPValidationError(f"MCP server '{name}' must define a URL.")
            normalized["url"] = url.strip()
            headers = server.get("headers", {})
            normalized["headers"] = self._coerce_string_map(headers, name, "headers")

        return normalized

    def _coerce_string_list(
        self, value: Any, server_name: str, field_name: str
    ) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise MCPValidationError(
                f"MCP server '{server_name}' field '{field_name}' must be a string array."
            )
        return value

    def _coerce_string_map(
        self, value: Any, server_name: str, field_name: str
    ) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise MCPValidationError(
                f"MCP server '{server_name}' field '{field_name}' must be a string object."
            )
        normalized: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise MCPValidationError(
                    f"MCP server '{server_name}' field '{field_name}' must contain only strings."
                )
            normalized[key] = item
        return normalized

    def _infer_provider_from_base_url(self, base_url: str) -> str:
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            return ""
        for provider, provider_base_url in PROVIDER_BASE_URLS.items():
            if normalized_base_url == provider_base_url.rstrip("/"):
                return provider
        return ""

    def _normalize_allowed_roots(self, allowed_roots: list[str]) -> list[str]:
        normalized_roots: list[str] = []
        seen: set[str] = set()
        for root in allowed_roots:
            candidate = str(root).strip()
            if not candidate:
                continue
            resolved = str(self._resolve_user_path(candidate))
            if resolved in seen:
                continue
            seen.add(resolved)
            normalized_roots.append(resolved)
        return normalized_roots

    def resolve_project_path(self, raw_path: str | None) -> str:
        candidate = raw_path.strip() if isinstance(raw_path, str) else ""
        resolved = (
            self._resolve_user_path(candidate) if candidate else self.project_root
        )
        return str(resolved.resolve())

    def _finalize_web_settings(self, settings: WebSettings) -> WebSettings:
        settings.allowed_roots = self._normalize_allowed_roots(settings.allowed_roots)
        if not settings.allowed_roots:
            settings.allowed_roots = self.default_allowed_roots()
        inferred_provider = self._infer_provider_from_base_url(settings.llm.base_url)
        if not settings.llm.provider and inferred_provider:
            settings.llm = settings.llm.model_copy(
                update={"provider": inferred_provider}
            )
        settings.session_defaults.default_project_path = self.resolve_project_path(
            settings.session_defaults.default_project_path
        )
        settings.session_defaults.channel_project_path = self.resolve_project_path(
            settings.session_defaults.channel_project_path
        )
        if not settings.codex.launcher_command:
            settings.codex.launcher_command = "codex app-server --listen stdio://"
        return settings

    def _resolve_user_path(self, raw_path: str) -> Path:
        if raw_path == "~":
            return self.home_dir.resolve()
        if raw_path.startswith("~/"):
            return (self.home_dir / raw_path[2:]).resolve()
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.project_root / candidate).resolve()

    def _resolve_command_binary(self, launcher_command: str) -> str | None:
        try:
            parts = shlex.split(launcher_command)
        except ValueError:
            return None
        if not parts:
            return None
        binary = parts[0]
        if binary.startswith("/"):
            return binary if Path(binary).exists() else None
        return shutil.which(binary)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except PermissionError:
            pass

    def _path_marker(self, path: Path) -> tuple[bool, int, int]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return (False, 0, 0)
        return (True, stat.st_mtime_ns, stat.st_size)
