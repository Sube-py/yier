from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
from typing import Any, AsyncIterator

from litestar import Litestar, Request, Router, delete, get, post, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from litestar.response import Response, ServerSentEvent
from litestar.response.sse import ServerSentEventMessage
from litestar.status_codes import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

from yier_web.auth import AuthService
from yier_web.chat import ChatService
from yier_web.channel_workspace import IntegratedChannelWorkspaceService
from yier_web.config import AppConfigService, MCPValidationError
from yier_web.directory_picker import LocalDirectoryPickerService
from yier_web.event_stream import EventStreamBroker, EventStreamItem
from yier_web.frontend import FrontendService
from yier_web.schemas import (
    ApprovalResponseRequest,
    AuthLoginRequest,
    AuthSessionResponse,
    ChatStreamRequest,
    ChannelAccountActionResponse,
    ChannelAccountsResponse,
    ChannelConfigResponse,
    ChannelLoginRequest,
    ChannelPlatformsResponse,
    ChannelWorkspaceResponse,
    CodexGoalLoopActionRequest,
    CodexGoalLoopResponse,
    CodexWorkspaceResponse,
    CodexPairedEditorStateRequest,
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
    SelectDirectoryRequest,
    SelectDirectoryResponse,
    SaveLLMRequest,
    SaveMCPConfigRequest,
    SessionActivityPageResponse,
    SessionListResponse,
    SessionTranscriptResponse,
    UpdateCodexGoalLoopRequest,
    UpdateCodexSessionModeRequest,
)


@dataclass(slots=True)
class AppServices:
    config_service: AppConfigService
    chat_service: ChatService
    channel_workspace_service: IntegratedChannelWorkspaceService
    event_broker: EventStreamBroker
    frontend_service: FrontendService
    directory_picker_service: LocalDirectoryPickerService
    auth_service: AuthService


EVENT_STREAM_PING_INTERVAL_SECONDS = 15.0


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def install_shutdown_signal_bridge(shutdown_event: asyncio.Event) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    if getattr(loop, "_yier_shutdown_signal_bridge_installed", False):
        return

    signal_handlers = getattr(loop, "_signal_handlers", None)
    if not isinstance(signal_handlers, dict):
        return

    installed = False
    for signum in (signal.SIGINT, signal.SIGTERM):
        existing_handle = signal_handlers.get(signum)
        if existing_handle is None or not hasattr(existing_handle, "_run"):
            continue

        def chained_handler(
            handle: Any = existing_handle,
            current_loop: asyncio.AbstractEventLoop = loop,
        ) -> None:
            shutdown_event.set()
            current_loop.call_soon(handle._run)

        try:
            loop.add_signal_handler(signum, chained_handler)
        except (NotImplementedError, RuntimeError, ValueError):
            continue
        installed = True

    if installed:
        setattr(loop, "_yier_shutdown_signal_bridge_installed", True)


def get_shutdown_event(state: State) -> asyncio.Event | None:
    shutdown_event = getattr(state, "shutdown_event", None)
    if isinstance(shutdown_event, asyncio.Event):
        return shutdown_event
    return None


async def wait_for_event_stream_item(
    subscriber: asyncio.Queue[EventStreamItem],
    shutdown_event: asyncio.Event | None,
    timeout: float,
) -> tuple[EventStreamItem | None, bool]:
    subscriber_task = asyncio.create_task(subscriber.get())
    shutdown_task: asyncio.Task[bool] | None = None
    wait_tasks: list[asyncio.Task[Any]] = [subscriber_task]

    if shutdown_event is not None:
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        wait_tasks.append(shutdown_task)

    try:
        done, pending = await asyncio.wait(
            wait_tasks,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass

        if not done:
            return None, False

        if shutdown_task is not None and shutdown_task in done:
            return None, True

        return subscriber_task.result(), False
    finally:
        if not subscriber_task.done():
            subscriber_task.cancel()
            try:
                await subscriber_task
            except asyncio.CancelledError:
                pass
        if shutdown_task is not None and not shutdown_task.done():
            shutdown_task.cancel()
            try:
                await shutdown_task
            except asyncio.CancelledError:
                pass


def build_services(
    project_root: Path | None = None,
    home_dir: Path | None = None,
) -> AppServices:
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
        frontend_service=FrontendService(
            project_root=resolved_root,
            debug=_env_flag("YIER_DEBUG"),
        ),
        directory_picker_service=LocalDirectoryPickerService(),
        auth_service=AuthService(),
    )


def get_services(state: State) -> AppServices:
    return AppServices(
        config_service=state.config_service,
        chat_service=state.chat_service,
        channel_workspace_service=state.channel_workspace_service,
        event_broker=state.event_broker,
        frontend_service=state.frontend_service,
        directory_picker_service=state.directory_picker_service,
        auth_service=state.auth_service,
    )


@get("/auth/session")
async def get_auth_session(request: Request[Any, Any, Any], state: State) -> AuthSessionResponse:
    payload = get_services(state).auth_service.session_payload(request)
    return AuthSessionResponse(**payload)


@post("/auth/login")
async def login(
    data: AuthLoginRequest,
    request: Request[Any, Any, Any],
    state: State,
) -> Response:
    auth_service = get_services(state).auth_service
    if not auth_service.verify_login_password(data.password):
        raise HTTPException(status_code=401, detail="Invalid password.")
    return auth_service.build_login_response(request)


@post("/auth/logout")
async def logout(request: Request[Any, Any, Any], state: State) -> Response:
    return get_services(state).auth_service.build_logout_response(request)


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


@post("/system/select-directory")
async def select_directory(
    data: SelectDirectoryRequest,
    state: State,
) -> SelectDirectoryResponse:
    services = get_services(state)
    selected_path = await asyncio.to_thread(
        services.directory_picker_service.select_directory,
        data.initial_path,
    )
    return SelectDirectoryResponse(
        selected=bool(selected_path),
        project_path=selected_path or "",
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


@post("/codex/paired-editor/state")
async def update_codex_paired_editor_state(
    data: CodexPairedEditorStateRequest,
    state: State,
) -> Response:
    await get_services(state).chat_service.update_paired_editor_state(
        session_id=data.session_id,
        content=data.content,
        selection_start=data.selection_start,
        selection_end=data.selection_end,
    )
    return Response(content={"ok": True})


@get("/chat/sessions/{session_id:str}")
async def get_session(
    session_id: str,
    state: State,
    activity_limit: int | None = None,
) -> SessionTranscriptResponse:
    services = get_services(state)
    transcript = services.chat_service.load_session_transcript(
        session_id,
        activity_limit=activity_limit,
    )
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
        codex_goal_loop=metadata["codex_goal_loop"],
        backend_runtime=services.chat_service.get_backend_runtime(session_id),
        pending_approvals=services.chat_service.get_pending_approvals(session_id),
        messages=transcript.messages,
        activity_events=transcript.activity_events,
        activity_history=transcript.activity_history,
        codex_turn_timings=transcript.codex_turn_timings,
    )


@get("/chat/sessions/{session_id:str}/activity-events")
async def get_session_activity_events(
    session_id: str,
    state: State,
    before: int | None = None,
    limit: int | None = None,
) -> SessionActivityPageResponse:
    activity_events, activity_history = get_services(state).chat_service.get_session_activity_page(
        session_id,
        before=before,
        limit=limit,
    )
    return SessionActivityPageResponse(
        session_id=session_id,
        activity_events=activity_events,
        activity_history=activity_history,
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


@put("/chat/sessions/{session_id:str}/codex-goal-loop")
async def update_codex_goal_loop(
    session_id: str,
    data: UpdateCodexGoalLoopRequest,
    state: State,
) -> CodexGoalLoopResponse:
    updated = get_services(state).chat_service.update_codex_goal_loop(
        session_id=session_id,
        goal=data.goal,
        definition_of_done=data.definition_of_done,
    )
    if updated is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Codex session not found.")
    return CodexGoalLoopResponse(codex_goal_loop=updated)


@post("/chat/sessions/{session_id:str}/codex-goal-loop/actions")
async def codex_goal_loop_action(
    session_id: str,
    data: CodexGoalLoopActionRequest,
    state: State,
) -> CodexGoalLoopResponse:
    updated = await get_services(state).chat_service.apply_codex_goal_loop_action(
        session_id=session_id,
        action=data.action,
    )
    if updated is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Codex session not found.")
    return CodexGoalLoopResponse(codex_goal_loop=updated)


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
    shutdown_event = get_shutdown_event(state)

    async def event_stream() -> AsyncIterator[ServerSentEventMessage]:
        subscriber = services.event_broker.subscribe()
        try:
            yield ServerSentEventMessage(
                event="connected",
                data=json.dumps({"status": "ok"}, ensure_ascii=False),
            )
            while True:
                item, shutting_down = await wait_for_event_stream_item(
                    subscriber,
                    shutdown_event,
                    timeout=EVENT_STREAM_PING_INTERVAL_SECONDS,
                )
                if shutting_down:
                    break
                if item is None:
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
        get_auth_session,
        login,
        logout,
        save_llm_config,
        save_allowed_roots_config,
        save_app_config,
        get_mcp_config,
        save_mcp_config,
        reload_mcp_config,
        create_session,
        select_directory,
        list_sessions,
        get_codex_workspace,
        open_codex_session,
        update_codex_paired_editor_state,
        get_session,
        get_session_activity_events,
        delete_session,
        respond_to_approval,
        update_codex_session_mode,
        update_codex_goal_loop,
        codex_goal_loop_action,
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

    async def before_request(request: Request[Any, Any, Any]) -> Response | None:
        auth_service = app_services.auth_service
        if not auth_service.enabled:
            return None

        request_path = request.url.path
        if auth_service.is_public_path(request_path):
            return None
        if auth_service.is_authenticated(request):
            return None
        if request_path.startswith("/api/"):
            return auth_service.build_unauthorized_api_response()
        return auth_service.build_login_redirect(request)

    @asynccontextmanager
    async def lifespan(app: Litestar) -> AsyncIterator[None]:
        shutdown_event = asyncio.Event()
        install_shutdown_signal_bridge(shutdown_event)
        app.state.shutdown_event = shutdown_event
        app.state.config_service = app_services.config_service
        app.state.chat_service = app_services.chat_service
        app.state.channel_workspace_service = app_services.channel_workspace_service
        app.state.event_broker = app_services.event_broker
        app.state.frontend_service = app_services.frontend_service
        app.state.directory_picker_service = app_services.directory_picker_service
        app.state.auth_service = app_services.auth_service
        await app_services.chat_service.start()
        await app_services.channel_workspace_service.start()
        try:
            yield
        finally:
            shutdown_event.set()
            await app_services.channel_workspace_service.stop()
            await app_services.chat_service.stop()

    return Litestar(
        route_handlers=[api_router, frontend_entry],
        before_request=before_request,
        lifespan=[lifespan],
    )


app = create_app()
