from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, AsyncIterator

from litestar import Litestar, Request, Router, delete, get, post, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from litestar.response import Response, ServerSentEvent
from litestar.response.sse import ServerSentEventMessage
from litestar.status_codes import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

from yier_web.chat import ChatService
from yier_web.channel_workspace import IntegratedChannelWorkspaceService
from yier_web.config import AppConfigService, MCPValidationError
from yier_web.event_stream import EventStreamBroker
from yier_web.frontend import FrontendService
from yier_web.schemas import (
    ApprovalResponseRequest,
    ChatStreamRequest,
    ChannelAccountActionResponse,
    ChannelAccountsResponse,
    ChannelConfigResponse,
    ChannelLoginRequest,
    ChannelPlatformsResponse,
    ChannelWorkspaceResponse,
    CodexWorkspaceResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    DeleteSessionResponse,
    LLMHealth,
    MCPConfigResponse,
    MCPHealth,
    HealthResponse,
    OpenCodexSessionRequest,
    OpenCodexSessionResponse,
    SaveAppSettingsRequest,
    SaveAllowedRootsRequest,
    SaveChannelConfigRequest,
    SaveLLMRequest,
    SaveMCPConfigRequest,
    SessionListResponse,
    SessionTranscriptResponse,
    UpdateCodexSessionModeRequest,
)


@dataclass(slots=True)
class AppServices:
    config_service: AppConfigService
    chat_service: ChatService
    channel_workspace_service: IntegratedChannelWorkspaceService
    event_broker: EventStreamBroker
    frontend_service: FrontendService


def build_services(project_root: Path | None = None, home_dir: Path | None = None) -> AppServices:
    resolved_root = (project_root or Path.cwd()).resolve()
    config_service = AppConfigService(project_root=resolved_root, home_dir=home_dir)
    event_broker = EventStreamBroker()
    chat_service = ChatService(
        project_root=resolved_root,
        config_service=config_service,
        event_broker=event_broker,
    )
    return AppServices(
        config_service=config_service,
        chat_service=chat_service,
        channel_workspace_service=IntegratedChannelWorkspaceService(
            project_root=resolved_root,
            chat_service=chat_service,
            event_broker=event_broker,
        ),
        event_broker=event_broker,
        frontend_service=FrontendService(project_root=resolved_root),
    )


def get_services(state: State) -> AppServices:
    return AppServices(
        config_service=state.config_service,
        chat_service=state.chat_service,
        channel_workspace_service=state.channel_workspace_service,
        event_broker=state.event_broker,
        frontend_service=state.frontend_service,
    )


@get("/health")
async def health(state: State) -> HealthResponse:
    services = get_services(state)
    runtime = await services.chat_service.get_mcp_status()
    frontend = await services.frontend_service.get_status()
    settings = services.config_service.load_web_settings()
    llm_ready = settings.llm.is_ready
    failing_servers = [
        name for name, entry in runtime.items() if entry.status in {"failed", "needs_auth", "needs_client_registration"}
    ]
    mcp_ready = not failing_servers
    return HealthResponse(
        frontend=frontend,
        llm=LLMHealth(
            ready=llm_ready,
            detail=(
                None
                if llm_ready
                else (
                    "Set provider, API key, and model in Settings. Base URL is optional for preset providers."
                    if settings.llm.is_preset
                    else "Set base URL, API key, and model in Settings."
                )
            ),
        ),
        mcp=MCPHealth(
            ready=mcp_ready,
            detail=None if mcp_ready else f"Problematic MCP servers: {', '.join(failing_servers)}",
            runtime=runtime,
        ),
        backends=services.config_service.build_backend_health(),
        allowed_roots=settings.allowed_roots,
    )


@get("/config")
async def get_config(state: State) -> Response:
    services = get_services(state)
    runtime = await services.chat_service.get_mcp_status()
    return Response(content=services.config_service.build_public_config(runtime).model_dump())


@put("/config/llm")
async def save_llm_config(data: SaveLLMRequest, state: State) -> Response:
    services = get_services(state)
    services.config_service.save_llm_settings(data)
    await services.chat_service.reload_agent()
    runtime = await services.chat_service.get_mcp_status()
    return Response(content=services.config_service.build_public_config(runtime).model_dump())


@put("/config/roots")
async def save_allowed_roots_config(data: SaveAllowedRootsRequest, state: State) -> Response:
    services = get_services(state)
    services.config_service.save_allowed_roots(data.allowed_roots)
    await services.chat_service.reload_agent()
    runtime = await services.chat_service.get_mcp_status()
    return Response(content=services.config_service.build_public_config(runtime).model_dump())


@put("/config/app")
async def save_app_config(data: SaveAppSettingsRequest, state: State) -> Response:
    services = get_services(state)
    services.config_service.save_app_settings(data)
    runtime = await services.chat_service.get_mcp_status()
    return Response(content=services.config_service.build_public_config(runtime).model_dump())


@get("/config/mcp")
async def get_mcp_config(state: State) -> MCPConfigResponse:
    services = get_services(state)
    return MCPConfigResponse(
        mcp_servers=services.config_service.load_mcp_servers(),
        runtime=await services.chat_service.get_mcp_status(),
    )


@put("/config/mcp")
async def save_mcp_config(data: SaveMCPConfigRequest, state: State) -> MCPConfigResponse:
    services = get_services(state)
    try:
        runtime = await services.chat_service.replace_mcp_servers(data.mcp_servers)
    except MCPValidationError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MCPConfigResponse(
        mcp_servers=services.config_service.load_mcp_servers(),
        runtime=runtime,
    )


@post("/config/mcp/reload")
async def reload_mcp_config(state: State) -> MCPConfigResponse:
    services = get_services(state)
    runtime = await services.chat_service.reload_mcp()
    return MCPConfigResponse(
        mcp_servers=services.config_service.load_mcp_servers(),
        runtime=runtime,
    )


@post("/chat/sessions")
async def create_session(request: Request[Any, Any, Any], state: State) -> CreateSessionResponse:
    try:
        raw_payload = await request.json()
    except Exception:
        raw_payload = {}
    payload = CreateSessionRequest.model_validate(raw_payload or {})
    return CreateSessionResponse(
        session_id=get_services(state).chat_service.create_session(
            backend_id=payload.backend_id,
            project_path=payload.project_path,
        )
    )


@get("/chat/sessions")
async def list_sessions(state: State) -> SessionListResponse:
    sessions = get_services(state).chat_service.list_session_summaries()
    return SessionListResponse(sessions=sessions)


@get("/codex/workspace")
async def get_codex_workspace(state: State) -> CodexWorkspaceResponse:
    return get_services(state).chat_service.get_codex_workspace()


@post("/codex/sessions/open")
async def open_codex_session(data: OpenCodexSessionRequest, state: State) -> OpenCodexSessionResponse:
    session_id = get_services(state).chat_service.open_codex_native_session(data.thread_id)
    if session_id is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Codex session not found.")
    return OpenCodexSessionResponse(session_id=session_id)


@get("/chat/sessions/{session_id:str}")
async def get_session(session_id: str, state: State) -> SessionTranscriptResponse:
    services = get_services(state)
    messages, activity_events = services.chat_service.load_session_view(session_id)
    metadata = services.chat_service.get_session_metadata(session_id)
    return SessionTranscriptResponse(
        session_id=session_id,
        source=metadata["source"],
        backend_id=metadata["backend_id"],
        project_path=metadata["project_path"],
        channel_meta=(
            metadata["channel_meta"]
            if isinstance(metadata["channel_meta"], dict)
            else None
        ),
        codex_work_mode=metadata["codex_work_mode"],
        backend_runtime=services.chat_service.get_backend_runtime(session_id),
        pending_approvals=services.chat_service.get_pending_approvals(session_id),
        messages=messages,
        activity_events=activity_events,
    )


@delete("/chat/sessions/{session_id:str}", status_code=200)
async def delete_session(session_id: str, state: State) -> DeleteSessionResponse:
    deleted = await get_services(state).chat_service.delete_session(session_id)
    return DeleteSessionResponse(session_id=session_id, deleted=deleted)


@post("/chat/sessions/{session_id:str}/approvals/respond")
async def respond_to_approval(
    session_id: str,
    data: ApprovalResponseRequest,
    state: State,
) -> Response:
    handled = await get_services(state).chat_service.respond_to_approval(
        session_id=session_id,
        request_id=data.request_id,
        decision=data.decision,
        content=data.content,
    )
    if not handled:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Approval request not found.")
    return Response(content={"ok": True})


@put("/chat/sessions/{session_id:str}/codex-mode")
async def update_codex_session_mode(
    session_id: str,
    data: UpdateCodexSessionModeRequest,
    state: State,
) -> Response:
    updated = get_services(state).chat_service.update_codex_session_mode(
        session_id=session_id,
        codex_work_mode=data.codex_work_mode,
    )
    if not updated:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Codex session not found.")
    return Response(content={"ok": True})


@post("/chat/stream", status_code=200)
async def stream_chat(data: ChatStreamRequest, state: State) -> ServerSentEvent:
    if not data.session_id:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="session_id is required.")
    if not data.message:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="message is required.")

    services = get_services(state)
    if services.chat_service.is_channel_session(data.session_id):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Channel-backed sessions are read-only in the chat workspace.",
        )

    async def event_stream() -> AsyncIterator[ServerSentEventMessage]:
        async for item in services.chat_service.stream_chat(data.session_id, data.message):
            yield ServerSentEventMessage(
                event=item["event"],
                data=json.dumps(item["data"], ensure_ascii=False),
            )

    return ServerSentEvent(event_stream())


@get("/channel/workspace")
async def get_channel_workspace(state: State) -> ChannelWorkspaceResponse:
    snapshot = await get_services(state).channel_workspace_service.get_workspace_snapshot()
    return ChannelWorkspaceResponse(
        platforms=[item.model_dump() for item in snapshot.platforms],
        accounts=[item.model_dump() for item in snapshot.accounts],
    )


@get("/channel/platforms")
async def get_channel_platforms(state: State) -> ChannelPlatformsResponse:
    services = get_services(state)
    return ChannelPlatformsResponse(platforms=services.channel_workspace_service.get_registered_platforms())


@get("/channel/config")
async def get_channel_config(state: State) -> ChannelConfigResponse:
    config = get_services(state).channel_workspace_service.load_config()
    return ChannelConfigResponse(**config.model_dump())


@put("/channel/config")
async def save_channel_config(data: SaveChannelConfigRequest, state: State) -> ChannelConfigResponse:
    config = get_services(state).channel_workspace_service.save_config(data.model_dump())
    return ChannelConfigResponse(**config.model_dump())


@get("/channel/accounts")
async def get_channel_accounts(state: State) -> ChannelAccountsResponse:
    accounts = await get_services(state).channel_workspace_service.get_accounts()
    return ChannelAccountsResponse(accounts=[item.model_dump() for item in accounts])


@post("/channel/accounts/weixin/login")
async def login_weixin_account(data: ChannelLoginRequest, state: State) -> Response:
    result = await get_services(state).channel_workspace_service.login(
        platform="weixin",
        account_id=data.account_id,
    )
    return Response(content=result)


@post("/channel/accounts/weixin/{account_id:str}/start")
async def start_weixin_account(account_id: str, state: State) -> ChannelAccountActionResponse:
    account = await get_services(state).channel_workspace_service.start_account(
        platform="weixin",
        account_id=account_id,
    )
    return ChannelAccountActionResponse(account=account.model_dump())


@post("/channel/accounts/weixin/{account_id:str}/stop")
async def stop_weixin_account(account_id: str, state: State) -> ChannelAccountActionResponse:
    account = await get_services(state).channel_workspace_service.stop_account(
        platform="weixin",
        account_id=account_id,
    )
    return ChannelAccountActionResponse(account=account.model_dump())


@get("/channel/monitor/sessions")
async def get_channel_monitor_sessions(state: State) -> SessionListResponse:
    sessions = await get_services(state).channel_workspace_service.get_monitor_sessions()
    return SessionListResponse(sessions=sessions)


@get("/events/stream")
async def stream_events(state: State) -> ServerSentEvent:
    services = get_services(state)

    async def event_stream() -> AsyncIterator[ServerSentEventMessage]:
        subscriber = services.event_broker.subscribe()
        try:
            yield ServerSentEventMessage(
                event="connected",
                data=json.dumps({"status": "ok"}, ensure_ascii=False),
            )
            while True:
                try:
                    item = await asyncio.wait_for(subscriber.get(), timeout=15)
                except TimeoutError:
                    yield ServerSentEventMessage(
                        event="ping",
                        data=json.dumps({"status": "alive"}, ensure_ascii=False),
                    )
                    continue

                yield ServerSentEventMessage(
                    event=item.event,
                    data=json.dumps(item.data, ensure_ascii=False),
                )
        finally:
            services.event_broker.unsubscribe(subscriber)

    return ServerSentEvent(event_stream())


api_router = Router(
    path="/api",
    route_handlers=[
        health,
        get_config,
        save_llm_config,
        save_allowed_roots_config,
        save_app_config,
        get_mcp_config,
        save_mcp_config,
        reload_mcp_config,
        create_session,
        list_sessions,
        get_codex_workspace,
        open_codex_session,
        get_session,
        delete_session,
        respond_to_approval,
        update_codex_session_mode,
        stream_chat,
        get_channel_workspace,
        get_channel_platforms,
        get_channel_config,
        save_channel_config,
        get_channel_accounts,
        login_weixin_account,
        start_weixin_account,
        stop_weixin_account,
        get_channel_monitor_sessions,
        stream_events,
    ],
)


@get(path=["/", "/{path:path}"], include_in_schema=False)
async def frontend_entry(request: Request[Any, Any, Any], state: State, path: str = "") -> Response:
    if path.startswith("api/"):
        raise HTTPException(status_code=HTTP_404_NOT_FOUND)
    services = get_services(state)
    return await services.frontend_service.handle_request(request, path)


def create_app(
    project_root: Path | None = None,
    home_dir: Path | None = None,
    services: AppServices | None = None,
) -> Litestar:
    resolved_root = (project_root or Path.cwd()).resolve()
    app_services = services or build_services(project_root=resolved_root, home_dir=home_dir)

    @asynccontextmanager
    async def lifespan(app: Litestar) -> AsyncIterator[None]:
        app.state.config_service = app_services.config_service
        app.state.chat_service = app_services.chat_service
        app.state.channel_workspace_service = app_services.channel_workspace_service
        app.state.event_broker = app_services.event_broker
        app.state.frontend_service = app_services.frontend_service
        await app_services.chat_service.start()
        await app_services.channel_workspace_service.start()
        try:
            yield
        finally:
            await app_services.channel_workspace_service.stop()
            await app_services.chat_service.stop()

    return Litestar(
        route_handlers=[api_router, frontend_entry],
        lifespan=[lifespan],
    )


app = create_app()
