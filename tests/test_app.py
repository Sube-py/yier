from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from litestar.testing import TestClient

from yier_agents import Message, ToolContext
from yier_agents.src.skill import SkillCatalog
from yier_web.app import AppServices, create_app
from yier_web.chat import ChatService
from yier_web.config import AppConfigService, MCPValidationError
from yier_web.frontend import FrontendService
from yier_web.schemas import MCPRuntimeEntry, SaveLLMRequest


class FakeChatService:
    def __init__(self) -> None:
        self.runtime = {
            "primevue-docs": MCPRuntimeEntry(status="connected", tool_count=3),
        }
        self.sessions = {
            "session-a": [
                Message(role="user", content="hello"),
                Message(role="assistant", content="hi there"),
            ],
        }
        self.started = False
        self.reloaded = 0

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def reload_agent(self, force_mcp_reconnect: bool = False) -> None:
        self.reloaded += 1

    async def get_mcp_status(self) -> dict[str, MCPRuntimeEntry]:
        return self.runtime

    async def replace_mcp_servers(
        self,
        mcp_servers: dict[str, dict[str, Any]],
    ) -> dict[str, MCPRuntimeEntry]:
        self.reloaded += 1
        return self.runtime

    async def reload_mcp(self) -> dict[str, MCPRuntimeEntry]:
        self.reloaded += 1
        return self.runtime

    def create_session(self) -> str:
        return "session-created"

    def get_session_messages(self, session_id: str) -> list[Message]:
        return self.sessions.get(session_id, [])

    async def stream_chat(self, session_id: str, user_message: str):
        yield {"event": "run_started", "data": {"session_id": session_id}}
        yield {
            "event": "tool_call_start",
            "data": {
                "session_id": session_id,
                "tool_name": "list_files",
                "tool_call_id": "call-1",
                "arguments": {"path": "."},
                "iteration": 1,
            },
        }
        yield {
            "event": "assistant_message",
            "data": {
                "session_id": session_id,
                "content": f"Echo: {user_message}",
                "iteration": 1,
            },
        }
        yield {
            "event": "done",
            "data": {
                "session_id": session_id,
                "finish_reason": "stop",
            },
        }


class FakeMCPManager:
    def __init__(self) -> None:
        self.version = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def reload(self, force_reconnect: bool = False) -> None:
        self.version += 1

    async def reload_if_changed(self) -> bool:
        return False

    async def get_tools(self) -> list[Any]:
        return []

    async def get_status(self) -> dict[str, dict[str, Any]]:
        return {}


def build_test_client(tmp_path: Path) -> TestClient[Any]:
    project_root = tmp_path / "project"
    (project_root / "web" / "dist").mkdir(parents=True)
    (project_root / "web" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")

    config_service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = FakeChatService()
    frontend_service = FrontendService(project_root=project_root)
    app = create_app(
        project_root=project_root,
        home_dir=tmp_path / "home",
        services=AppServices(
            config_service=config_service,
            chat_service=chat_service,  # type: ignore[arg-type]
            frontend_service=frontend_service,
        ),
    )
    return TestClient(app)


def test_config_service_creates_storage_under_home(tmp_path: Path) -> None:
    service = AppConfigService(project_root=tmp_path / "project", home_dir=tmp_path / "home")
    assert service.web_root.exists()
    assert service.sessions_path.exists()
    assert service.settings_path.parent == service.web_root


def test_llm_settings_are_saved_and_masked(tmp_path: Path) -> None:
    service = AppConfigService(project_root=tmp_path / "project", home_dir=tmp_path / "home")
    service.save_llm_settings(
        SaveLLMRequest(
            base_url="https://example.test/v1",
            model="demo-model",
            api_key="secret-token",
        )
    )

    stored = service.load_web_settings()
    assert stored.llm.api_key == "secret-token"

    public = service.build_public_config({})
    assert public.llm.base_url == "https://example.test/v1"
    assert public.llm.model == "demo-model"
    assert public.llm.has_api_key is True
    assert "api_key" not in public.model_dump()["llm"]


def test_allowed_roots_are_saved_normalized_and_defaulted(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")

    saved = service.save_allowed_roots(["~/Desktop", "./workspace", "", "~/Desktop"])

    assert saved.allowed_roots == [
        str((tmp_path / "home" / "Desktop").resolve()),
        str((project_root / "workspace").resolve()),
    ]

    reset = service.save_allowed_roots([])
    assert reset.allowed_roots == service.default_allowed_roots()


def test_assistant_settings_enable_shell_and_normalize_allowed_roots(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    service.settings_path.write_text(
        """
        {
          "allowed_roots": [
            "  ~/Downloads  ",
            "~/Downloads",
            "",
            "./relative-root"
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    assistant_settings = service.build_assistant_settings()

    expected_downloads = (tmp_path / "home" / "Downloads").resolve()
    expected_relative_root = (project_root / "relative-root").resolve()
    assert assistant_settings.workspace_root == project_root.resolve()
    assert assistant_settings.session_storage_path == service.sessions_path
    assert assistant_settings.prompt_history_path == service.prompt_history_path
    assert assistant_settings.run_command.allow_shell is True
    assert assistant_settings.run_command.shell_program == "/bin/bash"
    assert assistant_settings.allowed_roots == (expected_downloads, expected_relative_root)


def test_mcp_config_is_validated_and_written(tmp_path: Path) -> None:
    service = AppConfigService(project_root=tmp_path / "project", home_dir=tmp_path / "home")
    saved = service.save_mcp_servers(
        {
            "primevue-docs": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@primevue/mcp"],
                "env": {},
            }
        }
    )
    assert saved["primevue-docs"]["command"] == "npx"
    assert service.load_mcp_servers()["primevue-docs"]["args"] == ["-y", "@primevue/mcp"]

    with pytest.raises(MCPValidationError):
        service.save_mcp_servers({"broken": {"type": "stdio", "command": 123}})


def test_api_endpoints_cover_config_session_and_stream(tmp_path: Path) -> None:
    with build_test_client(tmp_path) as client:
        config_response = client.get("/api/config")
        assert config_response.status_code == 200
        assert config_response.json()["llm"]["has_api_key"] is False

        save_response = client.put(
            "/api/config/llm",
            json={
                "base_url": "https://example.test/v1",
                "model": "demo-model",
                "api_key": "secret-token",
            },
        )
        assert save_response.status_code == 200
        assert save_response.json()["llm"]["has_api_key"] is True

        roots_response = client.put(
            "/api/config/roots",
            json={
                "allowed_roots": ["~/Desktop", "./workspace"],
            },
        )
        assert roots_response.status_code == 200
        assert roots_response.json()["allowed_roots"]

        session_response = client.post("/api/chat/sessions")
        assert session_response.status_code == 201
        assert session_response.json()["session_id"] == "session-created"

        transcript_response = client.get("/api/chat/sessions/session-a")
        assert transcript_response.status_code == 200
        assert transcript_response.json()["messages"][0]["content"] == "hello"

        stream_response = client.post(
            "/api/chat/stream",
            json={"session_id": "session-a", "message": "inspect this"},
        )
        payload = stream_response.text
        assert stream_response.status_code == 200
        assert "event: run_started" in payload
        assert "event: tool_call_start" in payload
        assert "Echo: inspect this" in payload
        assert "event: done" in payload


def test_chat_service_skips_llm_construction_without_saved_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog())
    service = AppConfigService(project_root=tmp_path / "project", home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=tmp_path / "project",
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )

    def fail_if_called(*args: Any, **kwargs: Any):
        raise AssertionError("LLM should not be constructed without saved credentials.")

    monkeypatch.setattr(chat_service, "_build_llm", fail_if_called)

    import asyncio

    asyncio.run(chat_service.start())
    assert chat_service._agent is None


def test_chat_service_run_command_tool_supports_shell_pipes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog())

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    assistant_settings = service.build_assistant_settings()
    run_command_tool = next(
        tool for tool in chat_service._build_workspace_tools(assistant_settings) if tool.name == "run_command"
    )

    import asyncio

    result = asyncio.run(
        run_command_tool.execute(
            run_command_tool.parameters(
                command="printf 'hello' | wc -c",
                cwd=".",
            ),
            ToolContext(session_id="session-1", message_id="message-1", call_id="call-1"),
        )
    )

    assert "Command: printf 'hello' | wc -c" in result.content
    assert "Exit code: 0" in result.content
    assert "[stdout]" in result.content
    assert "5" in result.content
    assert result.metadata["allow_shell"] is True
