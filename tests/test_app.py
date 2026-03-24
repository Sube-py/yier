from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from litestar.testing import TestClient

from yier_agents import (
    AgentEndEvent,
    LLMEndEvent,
    Message,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolContext,
)
from yier_agents.src.skill import SkillCatalog
from yier_web.app import AppServices, create_app
from yier_web.chat import ChatService
from yier_web.config import AppConfigService, MCPValidationError
from yier_web.event_stream import EventStreamBroker
from yier_web.frontend import FrontendService
from yier_web.schemas import CodexWorkspaceResponse, MCPRuntimeEntry, SaveLLMRequest, StoredLLMSettings
from yier_web.tool_events import reset_tool_event_emitter, set_tool_event_emitter


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
        self.activity_events = {
            "session-a": [
                {
                    "event": "tool_call_start",
                    "data": {
                        "session_id": "session-a",
                        "tool_name": "run_command",
                        "tool_call_id": "call-1",
                        "arguments": {
                            "command": "printf 'hello'",
                        },
                        "iteration": 1,
                    },
                }
            ]
        }
        self.session_summaries = [
            {
                "session_id": "session-a",
                "title": "hello",
                "preview": "hi there",
                "updated_at": 123.0,
                "message_count": 2,
                "source": "chat",
                "backend_id": "yier",
                "project_path": "/tmp/project",
                "channel_meta": None,
                "codex_work_mode": None,
            }
        ]

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

    def create_session(self, backend_id: str | None = None, project_path: str | None = None) -> str:
        return "session-created"

    def get_session_messages(self, session_id: str) -> list[Message]:
        return self.sessions.get(session_id, [])

    def build_transcript_messages(self, session_id: str) -> list[dict[str, Any]]:
        return [
            {
                "role": message.role,
                "content": message.content,
                "reasoning_content": message.reasoning_content,
                "tool_call_id": message.tool_call_id,
                "source": "chat",
                "channel_meta": None,
            }
            for message in self.sessions.get(session_id, [])
        ]

    def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        return {
            "source": "chat",
            "backend_id": "yier",
            "project_path": "/tmp/project",
            "channel_meta": None,
            "backend_state": {},
            "codex_work_mode": None,
            "title": "hello",
            "preview": "hi there",
            "updated_at": 123.0,
        }

    def get_backend_runtime(self, session_id: str) -> dict[str, Any]:
        return {
            "backend_id": "yier",
            "label": "Yier Agent",
            "ready": True,
            "status": "idle",
            "thread_id": None,
            "active_flags": [],
            "detail": None,
            "pending_approval_count": 0,
        }

    def get_pending_approvals(self, session_id: str) -> list[dict[str, Any]]:
        return []

    def is_channel_session(self, session_id: str) -> bool:
        return False

    def get_session_activity_events(self, session_id: str) -> list[dict[str, Any]]:
        return self.activity_events.get(session_id, [])

    def load_session_view(self, session_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (self.build_transcript_messages(session_id), self.get_session_activity_events(session_id))

    def list_session_summaries(self, source: str | None = None) -> list[dict[str, Any]]:
        if source is None:
            return self.session_summaries
        return [item for item in self.session_summaries if item["source"] == source]

    def get_codex_workspace(self) -> CodexWorkspaceResponse:
        return CodexWorkspaceResponse(projects=[])

    def open_codex_native_session(self, thread_id: str) -> str | None:
        return thread_id if thread_id else None

    def update_codex_session_mode(self, session_id: str, codex_work_mode: str) -> bool:
        return session_id == "session-a"

    async def delete_session(self, session_id: str) -> bool:
        self.sessions.pop(session_id, None)
        self.activity_events.pop(session_id, None)
        self.session_summaries = [
            item for item in self.session_summaries if item["session_id"] != session_id
        ]
        return True

    async def respond_to_approval(
        self,
        session_id: str,
        request_id: str,
        decision: str,
        content: dict[str, Any] | None = None,
    ) -> bool:
        return True

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


class FakeChannelWorkspaceService:
    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def get_workspace_snapshot(self):
        class Snapshot:
            def __init__(self) -> None:
                self.platforms = [
                    type("Platform", (), {"model_dump": lambda self: {"name": "weixin", "label": "Weixin", "implemented": True, "account_count": 1, "running_count": 0}})()
                ]
                self.accounts = [
                    type("Account", (), {"model_dump": lambda self: {"platform": "weixin", "account_id": "wx-a", "configured": False, "enabled": True, "running": False, "name": None, "last_inbound_at": None, "last_outbound_at": None, "last_error": None, "login_status": None}})()
                ]

        return Snapshot()

    def load_config(self):
        class Config:
            def model_dump(self) -> dict[str, Any]:
                return {"enabled_platforms": ["weixin"], "weixin": {}}

        return Config()

    def save_config(self, payload: dict[str, Any]):
        class Config:
            def __init__(self, incoming: dict[str, Any]) -> None:
                self.incoming = incoming

            def model_dump(self) -> dict[str, Any]:
                return self.incoming

        return Config(payload)

    async def get_accounts(self):
        return [
            type("Account", (), {"model_dump": lambda self: {"platform": "weixin", "account_id": "wx-a", "configured": False, "enabled": True, "running": False, "name": None, "last_inbound_at": None, "last_outbound_at": None, "last_error": None, "login_status": None}})()
        ]

    async def login(self, platform: str, account_id: str | None = None):
        return {"platform": platform, "account_id": account_id or "wx-a", "status": "waiting", "qrcode_url": "https://example.test/qr"}

    async def start_account(self, platform: str, account_id: str):
        return type("Account", (), {"model_dump": lambda self: {"platform": platform, "account_id": account_id, "configured": True, "enabled": True, "running": True, "name": None, "last_inbound_at": None, "last_outbound_at": None, "last_error": None, "login_status": None}})()

    async def stop_account(self, platform: str, account_id: str):
        return type("Account", (), {"model_dump": lambda self: {"platform": platform, "account_id": account_id, "configured": True, "enabled": True, "running": False, "name": None, "last_inbound_at": None, "last_outbound_at": None, "last_error": None, "login_status": None}})()

    def get_registered_platforms(self):
        return [{"name": "weixin", "label": "Weixin", "implemented": True}]

    async def get_monitor_sessions(self):
        return []


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
            channel_workspace_service=FakeChannelWorkspaceService(),  # type: ignore[arg-type]
            event_broker=EventStreamBroker(),
            frontend_service=frontend_service,
        ),
    )
    return TestClient(app)


def test_config_service_creates_storage_under_home(tmp_path: Path) -> None:
    service = AppConfigService(project_root=tmp_path / "project", home_dir=tmp_path / "home")
    assert service.web_root.exists()
    assert service.sessions_path.exists()
    assert service.transcripts_path.exists()
    assert service.session_ui_path.exists()
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
    assert public.llm.provider == ""
    assert public.llm.base_url == "https://example.test/v1"
    assert public.llm.model == "demo-model"
    assert public.llm.has_api_key is True
    assert "api_key" not in public.model_dump()["llm"]


def test_llm_preset_settings_allow_blank_base_url(tmp_path: Path) -> None:
    service = AppConfigService(project_root=tmp_path / "project", home_dir=tmp_path / "home")
    service.save_llm_settings(
        SaveLLMRequest(
            provider="zai-coding-plan",
            base_url="",
            model="glm-4.7-flash",
            api_key="secret-token",
        )
    )

    stored = service.load_web_settings()
    assert stored.llm.provider == "zai-coding-plan"
    assert stored.llm.base_url == ""
    assert stored.llm.is_ready is True

    public = service.build_public_config({})
    assert public.llm.provider == "zai-coding-plan"
    assert public.llm.base_url == ""
    assert public.llm.model == "glm-4.7-flash"
    assert public.llm.has_api_key is True


def test_llm_settings_infer_legacy_zai_provider_without_rewriting_file(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    service.settings_path.write_text(
        json.dumps(
            {
                "llm": {
                    "base_url": "https://api.z.ai/api/coding/paas/v4",
                    "api_key": "secret-token",
                    "model": "glm-4.7-flash",
                },
                "allowed_roots": [],
            }
        ),
        encoding="utf-8",
    )

    stored = service.load_web_settings()

    assert stored.llm.provider == "zai-coding-plan"
    assert stored.llm.base_url == "https://api.z.ai/api/coding/paas/v4"

    persisted = json.loads(service.settings_path.read_text(encoding="utf-8"))
    assert persisted["llm"].get("provider") is None


def test_stored_llm_settings_require_base_url_only_for_custom_provider() -> None:
    assert StoredLLMSettings(base_url="", api_key="secret", model="demo").is_ready is False
    assert (
        StoredLLMSettings(
            provider="zai-coding-plan",
            base_url="",
            api_key="secret",
            model="glm-4.7-flash",
        ).is_ready
        is True
    )


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
        assert config_response.json()["llm"]["provider"] == ""
        assert config_response.json()["llm"]["has_api_key"] is False

        save_response = client.put(
            "/api/config/llm",
            json={
                "provider": "zai-coding-plan",
                "base_url": "https://example.test/v1",
                "model": "demo-model",
                "api_key": "secret-token",
            },
        )
        assert save_response.status_code == 200
        assert save_response.json()["llm"]["provider"] == "zai-coding-plan"
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

        list_response = client.get("/api/chat/sessions")
        assert list_response.status_code == 200
        assert list_response.json()["sessions"][0]["session_id"] == "session-a"

        transcript_response = client.get("/api/chat/sessions/session-a")
        assert transcript_response.status_code == 200
        assert transcript_response.json()["messages"][0]["content"] == "hello"
        assert transcript_response.json()["activity_events"][0]["event"] == "tool_call_start"

        codex_workspace_response = client.get("/api/codex/workspace")
        assert codex_workspace_response.status_code == 200
        assert codex_workspace_response.json()["projects"] == []

        open_codex_response = client.post("/api/codex/sessions/open", json={"thread_id": "thread-a"})
        assert open_codex_response.status_code == 201
        assert open_codex_response.json()["session_id"] == "thread-a"

        codex_mode_response = client.put(
            "/api/chat/sessions/session-a/codex-mode",
            json={"codex_work_mode": "plan"},
        )
        assert codex_mode_response.status_code == 200
        assert codex_mode_response.json()["ok"] is True

        delete_response = client.delete("/api/chat/sessions/session-a")
        assert delete_response.status_code == 200
        assert delete_response.json()["deleted"] is True

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

        channel_workspace_response = client.get("/api/channel/workspace")
        assert channel_workspace_response.status_code == 200
        assert channel_workspace_response.json()["platforms"][0]["name"] == "weixin"

        channel_platforms_response = client.get("/api/channel/platforms")
        assert channel_platforms_response.status_code == 200
        assert channel_platforms_response.json()["platforms"][0]["implemented"] is True

        channel_config_response = client.get("/api/channel/config")
        assert channel_config_response.status_code == 200
        assert channel_config_response.json()["enabled_platforms"] == ["weixin"]

        channel_login_response = client.post(
            "/api/channel/accounts/weixin/login",
            json={"account_id": None},
        )
        assert channel_login_response.status_code == 201
        assert channel_login_response.json()["status"] == "waiting"


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


def test_chat_service_build_llm_passes_provider_and_optional_base_url(tmp_path: Path) -> None:
    service = AppConfigService(project_root=tmp_path / "project", home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=tmp_path / "project",
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )

    llm = chat_service._build_llm(
        StoredLLMSettings(
            provider="zai-coding-plan",
            base_url="",
            api_key="secret-token",
            model="glm-4.7-flash",
        )
    )

    assert llm.provider == "zai-coding-plan"
    assert llm.base_url == "https://api.z.ai/api/coding/paas/v4"
    assert llm.api_key == "secret-token"
    assert llm.model == "glm-4.7-flash"


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


def test_chat_service_workspace_tools_include_background_tools(
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
    tool_names = {
        tool.name
        for tool in chat_service._build_workspace_tools(assistant_settings)
    }

    assert "start_background_command" in tool_names
    assert "list_background_commands" in tool_names
    assert "queue_background_followup" in tool_names


def test_chat_service_run_command_tool_emits_stream_events(
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
        tool
        for tool in chat_service._build_workspace_tools(assistant_settings)
        if tool.name == "run_command"
    )

    emitted_events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        emitted_events.append((event, data))

    token = set_tool_event_emitter(emit)

    import asyncio

    try:
        asyncio.run(
            run_command_tool.execute(
                run_command_tool.parameters(
                    command="printf 'hello'; printf 'oops' >&2",
                    cwd=".",
                ),
                ToolContext(session_id="session-1", message_id="message-1", call_id="call-2"),
            )
        )
    finally:
        reset_tool_event_emitter(token)

    event_names = [event_name for event_name, _payload in emitted_events]
    assert "command_start" in event_names
    assert "command_output" in event_names
    assert "command_end" in event_names
    assert any(
        payload["stream"] == "stdout" and "hello" in payload["content"]
        for event_name, payload in emitted_events
        if event_name == "command_output"
    )
    assert any(
        payload["stream"] == "stderr" and "oops" in payload["content"]
        for event_name, payload in emitted_events
        if event_name == "command_output"
    )


def test_chat_service_stream_chat_includes_tool_metadata_and_raw_payloads(
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

    raw_payload = {
        "kind": "shell_command",
        "request": {
            "command": "printf 'hello'",
            "cwd": str(project_root),
        },
        "process": {
            "session_id": None,
            "state": "completed",
            "exit_code": 0,
            "started_at": 1.0,
            "finished_at": 2.0,
            "runtime_seconds": 1,
            "timed_out": False,
        },
        "events": [
            {
                "index": 0,
                "timestamp": 1.0,
                "type": "started",
                "command": "printf 'hello'",
                "cwd": str(project_root),
            },
            {
                "index": 1,
                "timestamp": 2.0,
                "type": "stdout",
                "text": "hello",
                "stream": "stdout",
            },
        ],
        "latest_event_index": 1,
        "streams": {
            "stdout": {
                "text": "hello",
                "truncated": False,
            },
            "stderr": {
                "text": "",
                "truncated": False,
            },
        },
        "events_truncated": False,
        "dropped_event_count": 0,
    }

    class FakeAgent:
        async def run_stream(self, prompt: str, session_id: str):
            yield ToolCallStartEvent(
                session_id=session_id,
                timestamp=1.0,
                iteration=1,
                tool_name="run_command",
                tool_call_id="call-1",
                arguments={
                    "command": "printf 'hello'",
                    "cwd": ".",
                },
            )
            yield ToolCallEndEvent(
                session_id=session_id,
                timestamp=2.0,
                iteration=1,
                tool_name="run_command",
                tool_call_id="call-1",
                result="Command finished successfully.",
                is_error=False,
                metadata={
                    "command": "printf 'hello'",
                    "cwd": str(project_root),
                    "exit_code": 0,
                },
                raw=raw_payload,
            )
            yield AgentEndEvent(
                session_id=session_id,
                timestamp=3.0,
                finish_reason="stop",
                total_iterations=1,
            )

    async def fake_get_agent() -> FakeAgent:
        return FakeAgent()

    monkeypatch.setattr(chat_service, "_get_agent", fake_get_agent)

    import asyncio

    async def collect_events() -> list[dict[str, Any]]:
        return [event async for event in chat_service.stream_chat("session-1", "run it")]

    streamed_events = asyncio.run(collect_events())
    tool_end_event = next(event for event in streamed_events if event["event"] == "tool_call_end")

    assert tool_end_event["data"]["metadata"]["exit_code"] == 0
    assert tool_end_event["data"]["raw"]["kind"] == "shell_command"
    assert tool_end_event["data"]["raw"]["streams"]["stdout"]["text"] == "hello"


def test_chat_service_transcript_preserves_full_chat_history_for_refresh(
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

    class FakeAgent:
        async def run_stream(self, prompt: str, session_id: str):
            yield LLMEndEvent(
                session_id=session_id,
                timestamp=1.0,
                iteration=1,
                message=Message(role="assistant", content=f"Answer for {prompt}"),
                finish_reason="stop",
                usage=None,
            )
            yield AgentEndEvent(
                session_id=session_id,
                timestamp=2.0,
                finish_reason="stop",
                total_iterations=1,
            )

    async def fake_get_agent() -> FakeAgent:
        return FakeAgent()

    monkeypatch.setattr(chat_service, "_get_agent", fake_get_agent)

    import asyncio

    async def drain_stream() -> None:
        async for _event in chat_service.stream_chat("session-1", "first question"):
            pass

    asyncio.run(drain_stream())

    chat_service.session_store.save(
        "session-1",
        [Message(role="assistant", content="Compacted memory only keeps this")],
    )

    transcript = chat_service.get_session_messages("session-1")

    assert [message.role for message in transcript] == ["user", "assistant"]
    assert transcript[0].content == "first question"
    assert transcript[1].content == "Answer for first question"


def test_chat_service_persists_activity_events_under_session_id_file(
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

    class FakeAgent:
        async def run_stream(self, prompt: str, session_id: str):
            yield ToolCallStartEvent(
                session_id=session_id,
                timestamp=1.0,
                iteration=1,
                tool_name="run_command",
                tool_call_id="call-1",
                arguments={
                    "command": "printf 'hello'",
                },
            )
            yield AgentEndEvent(
                session_id=session_id,
                timestamp=2.0,
                finish_reason="stop",
                total_iterations=1,
            )

    async def fake_get_agent() -> FakeAgent:
        return FakeAgent()

    monkeypatch.setattr(chat_service, "_get_agent", fake_get_agent)

    import asyncio

    async def drain_stream() -> None:
        async for _event in chat_service.stream_chat("session-1", "run it"):
            pass

    asyncio.run(drain_stream())

    session_ui_file = service.session_ui_path / "session-1.json"
    assert session_ui_file.exists()
    assert chat_service.get_session_activity_events("session-1")[0]["event"] == "tool_call_start"
