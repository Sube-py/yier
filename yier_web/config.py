from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from yier_agents.src.config import YIERConfig

from yier_web.schemas import ConfigResponse, LLMConfigPayload, MCPRuntimeEntry, SaveLLMRequest, WebSettings


RUNTIME_STATUSES = {
    "connected",
    "disabled",
    "failed",
    "needs_auth",
    "needs_client_registration",
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
        self.mcp_config_path = self.yier_root / ".yier.json"
        self.ensure_storage()

    def ensure_storage(self) -> None:
        self.yier_root.mkdir(parents=True, exist_ok=True)
        self.web_root.mkdir(parents=True, exist_ok=True)
        self.sessions_path.mkdir(parents=True, exist_ok=True)

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
            return WebSettings(allowed_roots=self.default_allowed_roots())

        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            settings = WebSettings.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return WebSettings(allowed_roots=self.default_allowed_roots())

        if not settings.allowed_roots:
            settings.allowed_roots = self.default_allowed_roots()
        return settings

    def save_llm_settings(self, payload: SaveLLMRequest) -> WebSettings:
        settings = self.load_web_settings()
        settings.llm.base_url = payload.base_url
        settings.llm.model = payload.model
        if payload.api_key is not None and payload.api_key != "":
            settings.llm.api_key = payload.api_key

        if not settings.allowed_roots:
            settings.allowed_roots = self.default_allowed_roots()

        self._write_json(self.settings_path, settings.model_dump())
        return settings

    def load_mcp_root_config(self) -> dict[str, Any]:
        return YIERConfig.load_config(self.yier_root)

    def load_mcp_servers(self) -> dict[str, dict[str, Any]]:
        raw = self.load_mcp_root_config().get("mcpServers", {})
        if isinstance(raw, dict):
            return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
        return {}

    def save_mcp_servers(self, mcp_servers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
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
                base_url=settings.llm.base_url,
                model=settings.llm.model,
                has_api_key=bool(settings.llm.api_key),
            ),
            allowed_roots=settings.allowed_roots,
            mcp_runtime=mcp_runtime,
        )

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
                raise MCPValidationError(f"MCP server '{normalized_name}' must be an object.")
            normalized[normalized_name] = self._normalize_mcp_server(normalized_name, server)
        return normalized

    def _normalize_mcp_server(self, name: str, server: dict[str, Any]) -> dict[str, Any]:
        server_type = server.get("type")
        if server_type not in {"stdio", "http", "sse"}:
            raise MCPValidationError(f"MCP server '{name}' has an invalid type.")

        normalized: dict[str, Any] = {"type": server_type}
        enabled = server.get("enabled", True)
        if not isinstance(enabled, bool):
            raise MCPValidationError(f"MCP server '{name}' has a non-boolean 'enabled' value.")
        normalized["enabled"] = enabled

        status = server.get("status")
        if status is not None:
            if status not in RUNTIME_STATUSES:
                raise MCPValidationError(f"MCP server '{name}' has an invalid status value.")
            normalized["status"] = status

        if server_type == "stdio":
            command = server.get("command", "")
            if not isinstance(command, str) or not command.strip():
                raise MCPValidationError(f"MCP server '{name}' must define a stdio command.")
            normalized["command"] = command.strip()
            normalized["args"] = self._coerce_string_list(server.get("args", []), name, "args")
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

    def _coerce_string_list(self, value: Any, server_name: str, field_name: str) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise MCPValidationError(
                f"MCP server '{server_name}' field '{field_name}' must be a string array."
            )
        return value

    def _coerce_string_map(self, value: Any, server_name: str, field_name: str) -> dict[str, str]:
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
