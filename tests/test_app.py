from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
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
from yier_web.agent_backends.codex_backend import (
    CodexAppServerBackend,
    CodexSessionRuntime,
)
from yier_web.auth import AuthService, hash_password, verify_password
from yier_web.app import AppServices, create_app
from yier_web.chat import ChatService
from yier_web.config import AppConfigService, MCPValidationError
from yier_web.event_stream import EventStreamBroker
from yier_web.frontend import FrontendService
from yier_web.schemas import (
    CodexWorkspaceResponse,
    MCPRuntimeEntry,
    SaveAppSettingsRequest,
    SaveLLMRequest,
    StoredLLMSettings,
)
from yier_web.tool_events import reset_tool_event_emitter, set_tool_event_emitter


class FakeChatService:
    def __init__(self) -> None:
        self.runtime = {
            "primevue-docs": MCPRuntimeEntry(status="connected", tool_count=3),
        }
        self.paired_editor_updates: list[dict[str, Any]] = []
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

    def create_session(
        self, backend_id: str | None = None, project_path: str | None = None
    ) -> str:
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

    def get_session_activity_page(
        self,
        session_id: str,
        *,
        before: int | None = None,
        limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int | None]]:
        events = self.get_session_activity_events(session_id)
        total_count = len(events)
        normalized_before = (
            total_count if before is None else max(0, min(before, total_count))
        )
        if limit is None or limit <= 0:
            page = events[:normalized_before]
        else:
            start_index = max(0, normalized_before - limit)
            page = events[start_index:normalized_before]
        return (
            page,
            {
                "total_count": total_count,
                "returned_count": len(page),
                "next_before": (
                    normalized_before - len(page)
                    if normalized_before - len(page) > 0
                    else None
                ),
            },
        )

    def load_session_transcript(
        self,
        session_id: str,
        *,
        activity_limit: int | None = None,
    ) -> SimpleNamespace:
        activity_events, activity_history = self.get_session_activity_page(
            session_id,
            limit=activity_limit,
        )
        return SimpleNamespace(
            messages=self.build_transcript_messages(session_id),
            activity_events=activity_events,
            activity_history=activity_history,
            codex_turn_timings=[],
        )

    def load_session_view(
        self, session_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            self.build_transcript_messages(session_id),
            self.get_session_activity_events(session_id),
        )

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

    async def update_paired_editor_state(
        self,
        *,
        session_id: str,
        content: str,
        selection_start: int,
        selection_end: int,
    ) -> None:
        self.paired_editor_updates.append(
            {
                "session_id": session_id,
                "content": content,
                "selection_start": selection_start,
                "selection_end": selection_end,
            }
        )

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
                    type(
                        "Platform",
                        (),
                        {
                            "model_dump": lambda self: {
                                "name": "weixin",
                                "label": "Weixin",
                                "implemented": True,
                                "account_count": 1,
                                "running_count": 0,
                            }
                        },
                    )()
                ]
                self.accounts = [
                    type(
                        "Account",
                        (),
                        {
                            "model_dump": lambda self: {
                                "platform": "weixin",
                                "account_id": "wx-a",
                                "configured": False,
                                "enabled": True,
                                "running": False,
                                "name": None,
                                "last_inbound_at": None,
                                "last_outbound_at": None,
                                "last_error": None,
                                "login_status": None,
                            }
                        },
                    )()
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
            type(
                "Account",
                (),
                {
                    "model_dump": lambda self: {
                        "platform": "weixin",
                        "account_id": "wx-a",
                        "configured": False,
                        "enabled": True,
                        "running": False,
                        "name": None,
                        "last_inbound_at": None,
                        "last_outbound_at": None,
                        "last_error": None,
                        "login_status": None,
                    }
                },
            )()
        ]

    async def login(self, platform: str, account_id: str | None = None):
        return {
            "platform": platform,
            "account_id": account_id or "wx-a",
            "status": "waiting",
            "qrcode_url": "https://example.test/qr",
        }

    async def start_account(self, platform: str, account_id: str):
        return type(
            "Account",
            (),
            {
                "model_dump": lambda self: {
                    "platform": platform,
                    "account_id": account_id,
                    "configured": True,
                    "enabled": True,
                    "running": True,
                    "name": None,
                    "last_inbound_at": None,
                    "last_outbound_at": None,
                    "last_error": None,
                    "login_status": None,
                }
            },
        )()

    async def stop_account(self, platform: str, account_id: str):
        return type(
            "Account",
            (),
            {
                "model_dump": lambda self: {
                    "platform": platform,
                    "account_id": account_id,
                    "configured": True,
                    "enabled": True,
                    "running": False,
                    "name": None,
                    "last_inbound_at": None,
                    "last_outbound_at": None,
                    "last_error": None,
                    "login_status": None,
                }
            },
        )()

    def get_registered_platforms(self):
        return [{"name": "weixin", "label": "Weixin", "implemented": True}]

    async def get_monitor_sessions(self):
        return []


class FakeDirectoryPickerService:
    def __init__(self) -> None:
        self.selected_path = "/tmp/picked-project"
        self.calls: list[str | None] = []

    def select_directory(self, initial_path: str | None = None) -> str | None:
        self.calls.append(initial_path)
        return self.selected_path


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


def test_frontend_service_prefers_static_bundle_when_debug_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    dist_root = project_root / "web" / "dist"
    dist_root.mkdir(parents=True)
    (dist_root / "index.html").write_text("<html></html>", encoding="utf-8")
    frontend_service = FrontendService(project_root=project_root, debug=False)

    async def fake_vite_available() -> bool:
        return True

    monkeypatch.setattr(frontend_service, "_vite_available", fake_vite_available)

    import asyncio

    status = asyncio.run(frontend_service.get_status())

    assert status.mode == "static"


def test_frontend_service_uses_vite_proxy_when_debug_is_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    frontend_service = FrontendService(project_root=project_root, debug=True)

    async def fake_vite_available() -> bool:
        return True

    monkeypatch.setattr(frontend_service, "_vite_available", fake_vite_available)

    import asyncio

    status = asyncio.run(frontend_service.get_status())

    assert status.mode == "proxy"


def test_frontend_login_route_falls_back_to_vite_index_when_proxy_returns_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    config_service = AppConfigService(
        project_root=project_root, home_dir=tmp_path / "home"
    )
    frontend_service = FrontendService(project_root=project_root, debug=True)

    async def fake_vite_available() -> bool:
        return True

    async def fake_request(
        self,  # type: ignore[no-untyped-def]
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        request = httpx.Request(method, url, headers=headers, content=content)
        if url.endswith("/login?next=%2F"):
            return httpx.Response(404, request=request, text="Not found")
        if url.endswith("/"):
            return httpx.Response(
                200,
                request=request,
                text="<html><body>vite index</body></html>",
                headers={"content-type": "text/html; charset=utf-8"},
            )
        raise AssertionError(f"Unexpected Vite proxy request: {url}")

    monkeypatch.setattr(frontend_service, "_vite_available", fake_vite_available)
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    app = create_app(
        project_root=project_root,
        home_dir=tmp_path / "home",
        services=AppServices(
            config_service=config_service,
            chat_service=FakeChatService(),  # type: ignore[arg-type]
            channel_workspace_service=FakeChannelWorkspaceService(),  # type: ignore[arg-type]
            event_broker=EventStreamBroker(),
            frontend_service=frontend_service,
            directory_picker_service=FakeDirectoryPickerService(),
            auth_service=AuthService(),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/login?next=%2F", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "vite index" in response.text


def test_frontend_service_vite_client_ignores_env_proxy(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    frontend_service = FrontendService(project_root=project_root, debug=True)

    client = frontend_service._create_vite_client(timeout=1.5, follow_redirects=True)

    transport = getattr(client, "_transport", None)
    pool = getattr(transport, "_pool", None)
    proxy = getattr(pool, "_proxy", None)

    try:
        assert client.timeout.connect == 1.5
        assert client.follow_redirects is True
        assert proxy is None
    finally:
        import asyncio

        asyncio.run(client.aclose())


def test_frontend_static_entry_serves_html_inline(tmp_path: Path) -> None:
    client = build_test_client(tmp_path)

    with client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "content-disposition" not in response.headers
    assert response.text == "<html></html>"


def test_frontend_static_assets_preserve_asset_content_type(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    dist_root = project_root / "web" / "dist"
    assets_root = dist_root / "assets"
    assets_root.mkdir(parents=True)
    (dist_root / "index.html").write_text("<html></html>", encoding="utf-8")
    (assets_root / "app.js").write_text("console.log('hello')", encoding="utf-8")

    config_service = AppConfigService(
        project_root=project_root, home_dir=tmp_path / "home"
    )
    frontend_service = FrontendService(project_root=project_root)
    app = create_app(
        project_root=project_root,
        home_dir=tmp_path / "home",
        services=AppServices(
            config_service=config_service,
            chat_service=FakeChatService(),  # type: ignore[arg-type]
            channel_workspace_service=FakeChannelWorkspaceService(),  # type: ignore[arg-type]
            event_broker=EventStreamBroker(),
            frontend_service=frontend_service,
            directory_picker_service=FakeDirectoryPickerService(),
            auth_service=AuthService(),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "content-disposition" not in response.headers
    assert response.text == "console.log('hello')"


def build_test_client(tmp_path: Path) -> TestClient[Any]:
    project_root = tmp_path / "project"
    (project_root / "web" / "dist").mkdir(parents=True)
    (project_root / "web" / "dist" / "index.html").write_text(
        "<html></html>", encoding="utf-8"
    )

    config_service = AppConfigService(
        project_root=project_root, home_dir=tmp_path / "home"
    )
    chat_service = FakeChatService()
    frontend_service = FrontendService(project_root=project_root)
    directory_picker_service = FakeDirectoryPickerService()
    app = create_app(
        project_root=project_root,
        home_dir=tmp_path / "home",
        services=AppServices(
            config_service=config_service,
            chat_service=chat_service,  # type: ignore[arg-type]
            channel_workspace_service=FakeChannelWorkspaceService(),  # type: ignore[arg-type]
            event_broker=EventStreamBroker(),
            frontend_service=frontend_service,
            directory_picker_service=directory_picker_service,
            auth_service=AuthService(),
        ),
    )
    return TestClient(app)


def test_config_service_creates_storage_under_home(tmp_path: Path) -> None:
    service = AppConfigService(
        project_root=tmp_path / "project", home_dir=tmp_path / "home"
    )
    assert service.web_root.exists()
    assert service.sessions_path.exists()
    assert service.transcripts_path.exists()
    assert service.session_ui_path.exists()
    assert service.settings_path.parent == service.web_root


def test_llm_settings_are_saved_and_masked(tmp_path: Path) -> None:
    service = AppConfigService(
        project_root=tmp_path / "project", home_dir=tmp_path / "home"
    )
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


def test_password_hash_round_trip() -> None:
    password_hash = hash_password("secret-pass")

    assert verify_password("secret-pass", password_hash) is True
    assert verify_password("wrong-pass", password_hash) is False


def test_auth_service_allows_vite_virtual_modules() -> None:
    auth_service = AuthService()

    assert auth_service.is_public_path("/@id/virtual:vue-inspector-options") is True
    assert (
        auth_service.is_public_path("/@id/__x00__virtual:vue-devtools-options") is True
    )


def test_llm_preset_settings_allow_blank_base_url(tmp_path: Path) -> None:
    service = AppConfigService(
        project_root=tmp_path / "project", home_dir=tmp_path / "home"
    )
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


def test_codex_reasoning_cards_default_to_hidden(tmp_path: Path) -> None:
    service = AppConfigService(
        project_root=tmp_path / "project", home_dir=tmp_path / "home"
    )

    stored = service.load_web_settings()
    public = service.build_public_config({})

    assert stored.codex.show_reasoning_cards is False
    assert public.codex.show_reasoning_cards is False


def test_llm_settings_infer_legacy_zai_provider_without_rewriting_file(
    tmp_path: Path,
) -> None:
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
    assert (
        StoredLLMSettings(base_url="", api_key="secret", model="demo").is_ready is False
    )
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


def test_assistant_settings_enable_shell_and_normalize_allowed_roots(
    tmp_path: Path,
) -> None:
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
    assert assistant_settings.allowed_roots == (
        expected_downloads,
        expected_relative_root,
    )


def test_mcp_config_is_validated_and_written(tmp_path: Path) -> None:
    service = AppConfigService(
        project_root=tmp_path / "project", home_dir=tmp_path / "home"
    )
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
    assert service.load_mcp_servers()["primevue-docs"]["args"] == [
        "-y",
        "@primevue/mcp",
    ]

    with pytest.raises(MCPValidationError):
        service.save_mcp_servers({"broken": {"type": "stdio", "command": 123}})

def test_api_endpoints_cover_config_session_and_stream(tmp_path: Path) -> None:
    with build_test_client(tmp_path) as client:
        config_response = client.get("/api/config")
        assert config_response.status_code == 200
        assert config_response.json()["llm"]["provider"] == ""
        assert config_response.json()["llm"]["has_api_key"] is False
        assert (
            config_response.json()["session_defaults"]["workspace_surface"] == "codex"
        )

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

        app_config_response = client.put(
            "/api/config/app",
            json={
                "session_defaults": {
                    "default_backend_id": "yier",
                    "default_project_path": "./workspace",
                    "channel_backend_id": "yier",
                    "channel_project_path": "./workspace",
                    "channel_auto_approve_codex_requests": True,
                    "workspace_surface": "codex",
                },
                "codex": {
                    "launcher_command": "codex app-server --listen stdio://",
                    "model": "",
                    "sandbox": "workspace-write",
                    "approval_policy": "on-request",
                    "approvals_reviewer": "user",
                    "personality": "friendly",
                    "reasoning_effort": "medium",
                    "show_reasoning_cards": True,
                    "service_tier": "",
                },
            },
        )
        assert app_config_response.status_code == 200
        assert (
            app_config_response.json()["session_defaults"]["workspace_surface"]
            == "codex"
        )
        assert app_config_response.json()["codex"]["show_reasoning_cards"] is True

        session_response = client.post("/api/chat/sessions")
        assert session_response.status_code == 201
        assert session_response.json()["session_id"] == "session-created"

        select_directory_response = client.post(
            "/api/system/select-directory",
            json={"initial_path": "/tmp/project"},
        )
        assert select_directory_response.status_code == 201
        assert select_directory_response.json() == {
            "selected": True,
            "project_path": "/tmp/picked-project",
        }

        list_response = client.get("/api/chat/sessions")
        assert list_response.status_code == 200
        assert list_response.json()["sessions"][0]["session_id"] == "session-a"

        transcript_response = client.get("/api/chat/sessions/session-a")
        assert transcript_response.status_code == 200
        assert transcript_response.json()["messages"][0]["content"] == "hello"
        assert (
            transcript_response.json()["activity_events"][0]["event"]
            == "tool_call_start"
        )
        assert transcript_response.json()["activity_history"]["total_count"] == 1

        activity_page_response = client.get(
            "/api/chat/sessions/session-a/activity-events?limit=1"
        )
        assert activity_page_response.status_code == 200
        assert (
            activity_page_response.json()["activity_events"][0]["event"]
            == "tool_call_start"
        )

        codex_workspace_response = client.get("/api/codex/workspace")
        assert codex_workspace_response.status_code == 200
        assert codex_workspace_response.json()["projects"] == []

        open_codex_response = client.post(
            "/api/codex/sessions/open", json={"thread_id": "thread-a"}
        )
        assert open_codex_response.status_code == 201
        assert open_codex_response.json()["session_id"] == "thread-a"

        codex_mode_response = client.put(
            "/api/chat/sessions/session-a/codex-mode",
            json={"codex_work_mode": "plan"},
        )
        assert codex_mode_response.status_code == 200
        assert codex_mode_response.json()["ok"] is True

        paired_editor_response = client.post(
            "/api/codex/paired-editor/state",
            json={
                "session_id": "session-a",
                "content": "draft",
                "selection_start": 1,
                "selection_end": 4,
            },
        )
        assert paired_editor_response.status_code == 201
        assert paired_editor_response.json()["ok"] is True
        assert client.app.state.chat_service.paired_editor_updates == [
            {
                "session_id": "session-a",
                "content": "draft",
                "selection_start": 1,
                "selection_end": 4,
            }
        ]

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


def test_auth_redirects_frontend_and_blocks_api_when_password_is_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YIER_AUTH_PASSWORD", "deploy-secret")

    with build_test_client(tmp_path) as client:
        frontend_response = client.get("/", follow_redirects=False)
        assert frontend_response.status_code == 302
        assert frontend_response.headers["location"] == "/login?next=%2F"

        api_response = client.get("/api/config")
        assert api_response.status_code == 401
        assert api_response.json()["detail"] == "Authentication required."


def test_auth_login_logout_and_hashed_password_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YIER_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("YIER_AUTH_PASSWORD_HASH", hash_password("deploy-secret"))

    with build_test_client(tmp_path) as client:
        session_response = client.get("/api/auth/session")
        assert session_response.status_code == 200
        assert session_response.json() == {
            "enabled": True,
            "authenticated": False,
        }

        invalid_login_response = client.post(
            "/api/auth/login",
            json={"password": "wrong-secret"},
        )
        assert invalid_login_response.status_code == 401
        assert invalid_login_response.json()["detail"] == "Invalid password."

        login_response = client.post(
            "/api/auth/login",
            json={"password": "deploy-secret"},
        )
        assert login_response.status_code == 201
        assert login_response.json() == {
            "enabled": True,
            "authenticated": True,
        }
        assert "yier_auth_session" in login_response.cookies

        authorized_response = client.get("/api/config")
        assert authorized_response.status_code == 200

        logout_response = client.post("/api/auth/logout", json={})
        assert logout_response.status_code == 201
        assert logout_response.json() == {
            "enabled": True,
            "authenticated": False,
        }

        blocked_response = client.get("/api/config")
        assert blocked_response.status_code == 401


def test_chat_service_skips_llm_construction_without_saved_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )
    service = AppConfigService(
        project_root=tmp_path / "project", home_dir=tmp_path / "home"
    )
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


def test_chat_service_build_llm_passes_provider_and_optional_base_url(
    tmp_path: Path,
) -> None:
    service = AppConfigService(
        project_root=tmp_path / "project", home_dir=tmp_path / "home"
    )
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


def test_chat_service_create_codex_session_uses_native_thread_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    codex_backend = chat_service.backends["codex"]
    assert isinstance(codex_backend, CodexAppServerBackend)

    monkeypatch.setattr(
        codex_backend,
        "bootstrap_session",
        lambda project_path, source="chat", channel_meta=None: {
            "thread_id": "thread-native-42",
            "status": "idle",
            "active_flags": [],
            "detail": None,
        },
    )

    session_id = chat_service.create_session(backend_id="codex")
    metadata = chat_service.get_session_metadata(session_id)

    assert session_id == "thread-native-42"
    assert metadata["backend_id"] == "codex"
    assert metadata["backend_state"]["thread_id"] == "thread-native-42"
    assert metadata["codex_work_mode"] == "build"


def test_chat_service_build_codex_ipc_conversation_state_prefers_raw_requests_and_runtime_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    codex_backend = chat_service.backends["codex"]
    assert isinstance(codex_backend, CodexAppServerBackend)

    monkeypatch.setattr(
        codex_backend,
        "bootstrap_session",
        lambda project_path, source="chat", channel_meta=None: {
            "thread_id": "thread-native-7",
            "status": "idle",
            "active_flags": [],
            "detail": None,
        },
    )
    session_id = chat_service.create_session(backend_id="codex")

    monkeypatch.setattr(
        codex_backend,
        "load_thread_state",
        lambda context: {
            "thread": {
                "id": "thread-native-7",
                "name": "Native title",
                "source": "appServer",
                "cwd": str(project_root),
                "createdAt": 10,
                "updatedAt": 12,
                "status": {
                    "type": "active",
                    "activeFlags": ["waitingOnApproval"],
                },
                "turns": [{"id": "turn-1", "status": "inProgress", "items": []}],
            },
            "threadRuntimeStatus": {
                "type": "active",
                "activeFlags": ["waitingOnApproval"],
            },
        },
    )
    monkeypatch.setattr(
        codex_backend,
        "build_ipc_turns",
        lambda context, turns: [
            {
                "id": "turn-1",
                "turnId": "turn-1",
                "status": "inProgress",
                "items": [],
                "params": {"threadId": "thread-native-7", "input": []},
                "turnStartedAtMs": 1000,
                "finalAssistantStartedAtMs": None,
            }
        ],
    )
    monkeypatch.setattr(
        codex_backend,
        "pending_conversation_requests",
        lambda context: [
            {
                "id": "req-1",
                "method": "item/commandExecution/requestApproval",
                "params": {"itemId": "cmd-1"},
            }
        ],
    )

    payload = chat_service.build_codex_ipc_conversation_state(session_id)

    assert payload["requests"] == [
        {
            "id": "req-1",
            "method": "item/commandExecution/requestApproval",
            "params": {"itemId": "cmd-1"},
        }
    ]
    assert payload["turns"][0]["turnStartedAtMs"] == 1000
    assert payload["latestCollaborationMode"] == {
        "mode": "default",
        "settings": {
            "model": "",
            "reasoning_effort": None,
            "developer_instructions": None,
        },
    }
    assert payload["threadRuntimeStatus"] == {
        "type": "active",
        "activeFlags": ["waitingOnApproval"],
    }


def test_chat_service_build_codex_ipc_conversation_state_infers_waiting_on_approval_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    codex_backend = chat_service.backends["codex"]
    assert isinstance(codex_backend, CodexAppServerBackend)

    monkeypatch.setattr(
        codex_backend,
        "bootstrap_session",
        lambda project_path, source="chat", channel_meta=None: {
            "thread_id": "thread-native-9",
            "status": "idle",
            "active_flags": [],
            "detail": None,
        },
    )
    session_id = chat_service.create_session(backend_id="codex")

    monkeypatch.setattr(
        codex_backend,
        "load_thread_state",
        lambda context: {
            "thread": {
                "id": "thread-native-9",
                "name": "Native title",
                "source": "appServer",
                "cwd": str(project_root),
                "createdAt": 10,
                "updatedAt": 12,
                "turns": [{"id": "turn-1", "status": "inProgress", "items": []}],
            },
            "threadRuntimeStatus": {
                "type": "idle",
                "activeFlags": [],
            },
        },
    )
    monkeypatch.setattr(codex_backend, "build_ipc_turns", lambda context, turns: turns)
    monkeypatch.setattr(
        codex_backend,
        "pending_conversation_requests",
        lambda context: [
            {
                "id": "req-1",
                "method": "item/commandExecution/requestApproval",
                "params": {"itemId": "cmd-1"},
            }
        ],
    )

    payload = chat_service.build_codex_ipc_conversation_state(session_id)

    assert payload["threadRuntimeStatus"] == {
        "type": "active",
        "activeFlags": ["waitingOnApproval"],
    }


def test_chat_service_build_codex_ipc_conversation_state_keeps_active_status_for_live_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    codex_backend = chat_service.backends["codex"]
    assert isinstance(codex_backend, CodexAppServerBackend)

    monkeypatch.setattr(
        codex_backend,
        "bootstrap_session",
        lambda project_path, source="chat", channel_meta=None: {
            "thread_id": "thread-native-11",
            "status": "idle",
            "active_flags": [],
            "detail": None,
        },
    )
    session_id = chat_service.create_session(backend_id="codex")
    chat_service.update_session_backend_state(
        session_id,
        {"status": "active", "active_flags": []},
    )

    monkeypatch.setattr(
        codex_backend,
        "load_thread_state",
        lambda context: {
            "thread": {
                "id": "thread-native-11",
                "name": "Native title",
                "source": "appServer",
                "cwd": str(project_root),
                "createdAt": 10,
                "updatedAt": 12,
                "status": {
                    "type": "active",
                    "activeFlags": [],
                },
                "turns": [{"id": "turn-live", "status": "inProgress", "items": []}],
            },
            "threadRuntimeStatus": {
                "type": "active",
                "activeFlags": [],
            },
        },
    )
    monkeypatch.setattr(
        codex_backend,
        "build_ipc_turns",
        lambda context, turns: [
            {
                "id": "turn-live",
                "turnId": "turn-live",
                "status": "inProgress",
                "items": [],
                "params": {"threadId": "thread-native-11", "input": []},
                "turnStartedAtMs": 1000,
                "finalAssistantStartedAtMs": None,
            }
        ],
    )
    monkeypatch.setattr(
        codex_backend, "pending_conversation_requests", lambda context: []
    )

    payload = chat_service.build_codex_ipc_conversation_state(session_id)

    assert payload["threadRuntimeStatus"] == {
        "type": "active",
        "activeFlags": [],
    }


def test_chat_service_build_codex_ipc_conversation_state_uses_native_turn_count_while_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    codex_backend = chat_service.backends["codex"]
    assert isinstance(codex_backend, CodexAppServerBackend)

    monkeypatch.setattr(
        codex_backend,
        "bootstrap_session",
        lambda project_path, source="chat", channel_meta=None: {
            "thread_id": "thread-native-12",
            "status": "idle",
            "active_flags": [],
            "detail": None,
        },
    )
    session_id = chat_service.create_session(backend_id="codex")
    chat_service.update_session_backend_state(
        session_id,
        {"status": "active", "active_flags": []},
    )
    chat_service._append_transcript_message(
        session_id, Message(role="user", content="one")
    )
    chat_service._append_transcript_message(
        session_id, Message(role="assistant", content="two")
    )
    chat_service._append_transcript_message(
        session_id, Message(role="user", content="three")
    )

    monkeypatch.setattr(
        codex_backend,
        "load_thread_state",
        lambda context: {
            "thread": {
                "id": "thread-native-12",
                "name": "Native title",
                "source": "appServer",
                "cwd": str(project_root),
                "createdAt": 10,
                "updatedAt": 12,
                "status": {
                    "type": "active",
                    "activeFlags": [],
                },
                "turns": [
                    {"id": "turn-1", "status": "completed", "items": []},
                    {"id": "turn-2", "status": "completed", "items": []},
                    {"id": "turn-3", "status": "completed", "items": []},
                    {"id": "turn-4", "status": "completed", "items": []},
                    {"id": "turn-5", "status": "inProgress", "items": []},
                ],
            },
            "threadRuntimeStatus": {
                "type": "active",
                "activeFlags": [],
            },
        },
    )
    monkeypatch.setattr(codex_backend, "build_ipc_turns", lambda context, turns: turns)
    monkeypatch.setattr(
        codex_backend, "pending_conversation_requests", lambda context: []
    )

    payload = chat_service.build_codex_ipc_conversation_state(session_id)

    assert len(payload["turns"]) == 5
    assert payload["turns"][-1]["id"] == "turn-5"
    assert payload["turns"][-1]["status"] == "inProgress"


def test_chat_service_background_codex_turn_replays_events_to_ipc_and_broker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    codex_backend = chat_service.backends["codex"]
    assert isinstance(codex_backend, CodexAppServerBackend)

    monkeypatch.setattr(
        codex_backend,
        "bootstrap_session",
        lambda project_path, source="chat", channel_meta=None: {
            "thread_id": "thread-native-21",
            "status": "idle",
            "active_flags": [],
            "detail": None,
        },
    )
    session_id = chat_service.create_session(backend_id="codex")

    published_events: list[tuple[str, dict[str, Any]]] = []
    notified_events: list[tuple[str, dict[str, Any]]] = []

    async def fake_start_turn(context: Any, input_payload: Any) -> dict[str, Any]:
        return {
            "turn": {
                "id": "turn-21",
                "status": "inProgress",
                "items": [],
            }
        }

    async def fake_consume_turn_stream(context: Any, turn_id: str, emit: Any) -> str:
        await emit(
            "assistant_message",
            {
                "session_id": context.session_id,
                "item_id": "assistant-1",
                "content": "good night",
                "iteration": 0,
            },
        )
        await emit(
            "turn_completed",
            {
                "session_id": context.session_id,
                "turn_id": turn_id,
                "status": "completed",
            },
        )
        return "stop"

    async def fake_publish(event: str, data: dict[str, Any]) -> None:
        published_events.append((event, data))

    async def fake_notify_stream_event(event: str, data: dict[str, Any]) -> None:
        notified_events.append((event, data))

    monkeypatch.setattr(codex_backend, "start_turn", fake_start_turn)
    monkeypatch.setattr(codex_backend, "consume_turn_stream", fake_consume_turn_stream)
    monkeypatch.setattr(chat_service.event_broker, "publish", fake_publish)
    monkeypatch.setattr(
        chat_service.codex_ipc_bridge, "notify_stream_event", fake_notify_stream_event
    )

    import asyncio

    async def scenario() -> None:
        response = await chat_service.start_codex_turn_in_background(
            session_id, "good night"
        )
        assert response["turn"]["id"] == "turn-21"

        task = chat_service._ipc_stream_tasks[session_id]
        await task
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert published_events == [
        (
            "assistant_message",
            {
                "session_id": session_id,
                "item_id": "assistant-1",
                "content": "good night",
                "iteration": 0,
            },
        ),
        (
            "turn_completed",
            {
                "session_id": session_id,
                "turn_id": "turn-21",
                "status": "completed",
            },
        ),
        (
            "done",
            {
                "session_id": session_id,
                "finish_reason": "stop",
            },
        ),
    ]
    assert notified_events == published_events
    assert session_id not in chat_service._ipc_stream_tasks
    assert [
        message.model_dump(mode="json")
        for message in chat_service.build_transcript_messages(session_id)
    ] == [
        {
            "role": "user",
            "content": "good night",
            "reasoning_content": None,
            "tool_call_id": None,
            "sequence": None,
            "source": "chat",
            "channel_meta": None,
        }
    ]


def test_chat_service_load_session_view_uses_local_snapshot_for_active_codex_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    codex_backend = chat_service.backends["codex"]
    assert isinstance(codex_backend, CodexAppServerBackend)

    monkeypatch.setattr(
        codex_backend,
        "bootstrap_session",
        lambda project_path, source="chat", channel_meta=None: {
            "thread_id": "thread-native-22",
            "status": "idle",
            "active_flags": [],
            "detail": None,
        },
    )
    session_id = chat_service.create_session(backend_id="codex")
    chat_service.update_session_backend_state(session_id, {"status": "active"})
    chat_service._append_transcript_message(
        session_id, Message(role="user", content="hello")
    )
    chat_service._append_transcript_message(
        session_id, Message(role="assistant", content="hi")
    )
    chat_service.session_ui_store.append_activity_event(
        session_id,
        "reasoning",
        {
            "session_id": session_id,
            "item_id": "reasoning-1",
            "content": "Inspecting runtime state.",
            "iteration": 0,
        },
    )
    codex_backend._runtimes[session_id] = CodexSessionRuntime(
        session_id=session_id,
        thread_id="thread-native-22",
        status="active",
    )

    def fail_load_thread_view(
        context: Any, *, activity_limit: int | None = None
    ) -> dict[str, Any]:
        raise AssertionError(
            "load_thread_view should not run for active Codex sessions"
        )

    monkeypatch.setattr(codex_backend, "load_thread_view", fail_load_thread_view)

    messages, activity_events = chat_service.load_session_view(session_id)

    assert [message.model_dump(mode="json") for message in messages] == [
        {
            "role": "user",
            "content": "hello",
            "reasoning_content": None,
            "tool_call_id": None,
            "sequence": None,
            "source": "chat",
            "channel_meta": None,
        },
        {
            "role": "assistant",
            "content": "hi",
            "reasoning_content": None,
            "tool_call_id": None,
            "sequence": None,
            "source": "chat",
            "channel_meta": None,
        },
    ]
    assert activity_events == [
        {
            "event": "reasoning",
            "data": {
                "session_id": session_id,
                "item_id": "reasoning-1",
                "content": "Inspecting runtime state.",
                "iteration": 0,
            },
        }
    ]


def test_chat_service_load_session_view_prefers_native_thread_view_without_local_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    codex_backend = chat_service.backends["codex"]
    assert isinstance(codex_backend, CodexAppServerBackend)

    monkeypatch.setattr(
        codex_backend,
        "bootstrap_session",
        lambda project_path, source="chat", channel_meta=None: {
            "thread_id": "thread-native-31",
            "status": "idle",
            "active_flags": [],
            "detail": None,
        },
    )
    session_id = chat_service.create_session(backend_id="codex")
    chat_service.update_session_backend_state(session_id, {"status": "active"})
    chat_service._append_transcript_message(
        session_id, Message(role="user", content="local-user")
    )
    chat_service._append_transcript_message(
        session_id,
        Message(role="assistant", content="local-assistant"),
    )

    monkeypatch.setattr(
        codex_backend,
        "load_thread_view",
        lambda context, activity_limit=None: {
            "title": "Native Thread",
            "preview": "remote-preview",
            "updated_at": 1234,
            "messages": [
                Message(role="user", content="remote-user"),
                Message(role="assistant", content="remote-assistant"),
            ],
            "activity_events": [
                {
                    "event": "reasoning",
                    "data": {"session_id": session_id, "content": "remote"},
                }
            ],
            "activity_history": {
                "total_count": 1,
                "returned_count": 1,
                "next_before": None,
            },
            "codex_turn_timings": [],
        },
    )

    messages, activity_events = chat_service.load_session_view(session_id)

    assert [(message.role, message.content) for message in messages] == [
        ("user", "remote-user"),
        ("assistant", "remote-assistant"),
    ]
    assert activity_events == [
        {
            "event": "reasoning",
            "data": {"session_id": session_id, "content": "remote"},
        }
    ]


def test_chat_service_load_session_view_prefers_cached_ipc_state_for_remote_codex_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )
    codex_backend = chat_service.backends["codex"]
    assert isinstance(codex_backend, CodexAppServerBackend)

    monkeypatch.setattr(
        codex_backend,
        "bootstrap_session",
        lambda project_path, source="chat", channel_meta=None: {
            "thread_id": "thread-native-41",
            "status": "idle",
            "active_flags": [],
            "detail": None,
        },
    )
    session_id = chat_service.create_session(backend_id="codex")
    chat_service.update_session_backend_state(session_id, {"status": "idle"})

    monkeypatch.setattr(
        codex_backend,
        "load_thread_view",
        lambda context, activity_limit=None: {
            "title": "Stale Native Thread",
            "preview": "stale-preview",
            "updated_at": 10.0,
            "messages": [
                Message(role="assistant", content="stale-assistant"),
            ],
            "activity_events": [],
            "activity_history": {
                "total_count": 0,
                "returned_count": 0,
                "next_before": None,
            },
            "codex_turn_timings": [],
        },
    )

    chat_service.apply_codex_ipc_stream_change(
        session_id,
        {
            "type": "patches",
            "patches": [
                {
                    "op": "replace",
                    "path": ["turns"],
                    "value": [
                        {
                            "id": "turn-1",
                            "turnId": "turn-1",
                            "status": "inProgress",
                            "items": [],
                            "error": None,
                            "diff": None,
                            "params": {
                                "threadId": session_id,
                                "input": [
                                    {
                                        "type": "text",
                                        "text": "hello",
                                        "text_elements": [],
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "op": "replace",
                    "path": ["updatedAt"],
                    "value": 123000,
                },
                {
                    "op": "replace",
                    "path": ["title"],
                    "value": "Remote Thread",
                },
            ],
        },
    )
    chat_service.apply_codex_ipc_stream_change(
        session_id,
        {
            "type": "patches",
            "patches": [
                {
                    "op": "add",
                    "path": ["turns", 0, "items", 0],
                    "value": {
                        "type": "userMessage",
                        "id": "item-user-1",
                        "content": [
                            {"type": "text", "text": "hello", "text_elements": []}
                        ],
                    },
                },
                {
                    "op": "add",
                    "path": ["turns", 0, "items", 1],
                    "value": {
                        "type": "agentMessage",
                        "id": "item-agent-1",
                        "text": "world",
                        "phase": "final_answer",
                    },
                },
            ],
        },
    )

    messages, activity_events = chat_service.load_session_view(session_id)

    assert [(message.role, message.content) for message in messages] == [
        ("user", "hello"),
        ("assistant", "world"),
    ]
    assert activity_events == []


def test_chat_service_run_command_tool_supports_shell_pipes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

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

    import asyncio

    result = asyncio.run(
        run_command_tool.execute(
            run_command_tool.parameters(
                command="printf 'hello' | wc -c",
                cwd=".",
            ),
            ToolContext(
                session_id="session-1", message_id="message-1", call_id="call-1"
            ),
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
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

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
        tool.name for tool in chat_service._build_workspace_tools(assistant_settings)
    }

    assert "start_background_command" in tool_names
    assert "list_background_commands" in tool_names
    assert "queue_background_followup" in tool_names
    assert "start_codex_background_session" in tool_names
    assert "resume_codex_background_session" in tool_names


def test_chat_service_run_command_tool_emits_stream_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

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
                ToolContext(
                    session_id="session-1", message_id="message-1", call_id="call-2"
                ),
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
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

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
        return [
            event async for event in chat_service.stream_chat("session-1", "run it")
        ]

    streamed_events = asyncio.run(collect_events())
    tool_end_event = next(
        event for event in streamed_events if event["event"] == "tool_call_end"
    )

    assert tool_end_event["data"]["metadata"]["exit_code"] == 0
    assert tool_end_event["data"]["raw"]["kind"] == "shell_command"
    assert tool_end_event["data"]["raw"]["streams"]["stdout"]["text"] == "hello"


def test_chat_service_transcript_preserves_full_chat_history_for_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

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
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

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
    assert (
        chat_service.get_session_activity_events("session-1")[0]["event"]
        == "tool_call_start"
    )


def test_chat_service_load_session_transcript_limits_activity_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )

    for index in range(5):
        chat_service.session_ui_store.append_activity_event(
            "session-1",
            "reasoning",
            {
                "session_id": "session-1",
                "item_id": f"reasoning-{index}",
                "content": f"step {index}",
            },
        )

    transcript = chat_service.load_session_transcript("session-1", activity_limit=2)

    assert [event["data"]["item_id"] for event in transcript.activity_events] == [
        "reasoning-3",
        "reasoning-4",
    ]
    assert transcript.activity_history == {
        "total_count": 5,
        "returned_count": 2,
        "next_before": 3,
    }


def test_chat_service_get_session_activity_page_uses_before_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )

    for index in range(5):
        chat_service.session_ui_store.append_activity_event(
            "session-1",
            "reasoning",
            {
                "session_id": "session-1",
                "item_id": f"reasoning-{index}",
                "content": f"step {index}",
            },
        )

    activity_events, activity_history = chat_service.get_session_activity_page(
        "session-1",
        before=3,
        limit=2,
    )

    assert [event["data"]["item_id"] for event in activity_events] == [
        "reasoning-1",
        "reasoning-2",
    ]
    assert activity_history == {
        "total_count": 5,
        "returned_count": 2,
        "next_before": 1,
    }


def test_chat_service_get_session_metadata_migrates_legacy_conversation_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SkillCatalog, "discover", lambda *args, **kwargs: SkillCatalog()
    )

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    service = AppConfigService(project_root=project_root, home_dir=tmp_path / "home")
    chat_service = ChatService(
        project_root=project_root,
        config_service=service,
        mcp_manager=FakeMCPManager(),  # type: ignore[arg-type]
    )

    legacy_payload = {
        "session_id": "session-1",
        "source": "chat",
        "backend_id": "codex",
        "project_path": str(project_root),
        "backend_state": {
            "status": "idle",
            "ipc_conversation_state": {
                "id": "session-1",
                "turns": [{"id": "turn-1", "turnId": "turn-1", "items": []}],
            },
        },
        "codex_work_mode": "build",
        "title": "Legacy",
        "preview": "Legacy preview",
        "updated_at": 1.0,
    }
    service.session_meta_path.mkdir(parents=True, exist_ok=True)
    (service.session_meta_path / "session-1.json").write_text(
        json.dumps(legacy_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metadata = chat_service.get_session_metadata(
        "session-1",
        include_conversation_state=True,
    )

    assert metadata["backend_state"]["status"] == "idle"
    assert metadata["backend_state"]["ipc_conversation_state"]["id"] == "session-1"

    stored_metadata = json.loads(
        (service.session_meta_path / "session-1.json").read_text(encoding="utf-8")
    )
    assert "ipc_conversation_state" not in stored_metadata["backend_state"]
    conversation_state_file = service.session_conversation_state_path / "session-1.json"
    assert conversation_state_file.exists()
