from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from litestar import Controller, Request, delete, get, post, put
from litestar.datastructures import State, UploadFile
from litestar.exceptions import HTTPException
from litestar.response import File, Response, ServerSentEvent
from litestar.response.sse import ServerSentEventMessage
from litestar.status_codes import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

from yier_web.attachments import AttachmentStorageError
from yier_web.config import MCPValidationError
from yier_web.event_stream import EventStreamItem
from yier_web.schemas import (
    ApprovalResponseRequest,
    AttachmentUploadResponse,
    AuthLoginRequest,
    AuthSessionResponse,
    ChannelAccountActionResponse,
    ChannelAccountsResponse,
    ChannelConfigResponse,
    ChannelLoginRequest,
    ChannelPlatformsResponse,
    ChannelWorkspaceResponse,
    ChatStreamRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    DeleteSessionResponse,
    HealthResponse,
    LLMHealth,
    MCPConfigResponse,
    MCPHealth,
    SaveAllowedRootsRequest,
    SaveAppSettingsRequest,
    SaveChannelConfigRequest,
    SaveLLMRequest,
    SaveMCPConfigRequest,
    SelectDirectoryRequest,
    SelectDirectoryResponse,
    SessionActivityPageResponse,
    SessionListResponse,
    SessionTranscriptResponse,
)

EVENT_STREAM_PING_INTERVAL_SECONDS = 15.0


def _service(state: State, name: str) -> Any:
    return getattr(state, name)


def _shutdown_event(state: State) -> asyncio.Event | None:
    shutdown_event = getattr(state, "shutdown_event", None)
    if isinstance(shutdown_event, asyncio.Event):
        return shutdown_event
    return None


async def _wait_for_event_stream_item(
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


wait_for_event_stream_item = _wait_for_event_stream_item


class AuthController(Controller):
    path = "/auth"

    @get("/session")
    async def get_auth_session(
        self,
        request: Request[Any, Any, Any],
        state: State,
    ) -> AuthSessionResponse:
        payload = _service(state, "auth_service").session_payload(request)
        return AuthSessionResponse(**payload)

    @post("/login")
    async def login(
        self,
        data: AuthLoginRequest,
        request: Request[Any, Any, Any],
        state: State,
    ) -> Response:
        auth_service = _service(state, "auth_service")
        if not auth_service.verify_login_password(data.password):
            raise HTTPException(status_code=401, detail="Invalid password.")
        return auth_service.build_login_response(request)

    @post("/logout")
    async def logout(self, request: Request[Any, Any, Any], state: State) -> Response:
        return _service(state, "auth_service").build_logout_response(request)


class HealthController(Controller):
    @get("/health")
    async def health(self, state: State) -> HealthResponse:
        chat_service = _service(state, "chat_service")
        config_service = _service(state, "config_service")
        frontend_service = _service(state, "frontend_service")
        runtime = await chat_service.get_mcp_status()
        frontend = await frontend_service.get_status()
        settings = config_service.load_web_settings()
        llm_ready = settings.llm.is_ready
        failing_servers = [
            name
            for name, entry in runtime.items()
            if entry.status in {"failed", "needs_auth", "needs_client_registration"}
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
                detail=None
                if mcp_ready
                else f"Problematic MCP servers: {', '.join(failing_servers)}",
                runtime=runtime,
            ),
            backends=config_service.build_backend_health(),
            allowed_roots=settings.allowed_roots,
        )


class ConfigController(Controller):
    path = "/config"

    @get()
    async def get_config(self, state: State) -> Response:
        chat_service = _service(state, "chat_service")
        config_service = _service(state, "config_service")
        runtime = await chat_service.get_mcp_status()
        return Response(content=config_service.build_public_config(runtime).model_dump())

    @put("/llm")
    async def save_llm_config(self, data: SaveLLMRequest, state: State) -> Response:
        chat_service = _service(state, "chat_service")
        config_service = _service(state, "config_service")
        config_service.save_llm_settings(data)
        await chat_service.reload_agent()
        runtime = await chat_service.get_mcp_status()
        return Response(content=config_service.build_public_config(runtime).model_dump())

    @put("/roots")
    async def save_allowed_roots_config(
        self,
        data: SaveAllowedRootsRequest,
        state: State,
    ) -> Response:
        chat_service = _service(state, "chat_service")
        config_service = _service(state, "config_service")
        config_service.save_allowed_roots(data.allowed_roots)
        await chat_service.reload_agent()
        runtime = await chat_service.get_mcp_status()
        return Response(content=config_service.build_public_config(runtime).model_dump())

    @put("/app")
    async def save_app_config(self, data: SaveAppSettingsRequest, state: State) -> Response:
        chat_service = _service(state, "chat_service")
        config_service = _service(state, "config_service")
        config_service.save_app_settings(data)
        runtime = await chat_service.get_mcp_status()
        return Response(content=config_service.build_public_config(runtime).model_dump())

    @get("/mcp")
    async def get_mcp_config(self, state: State) -> MCPConfigResponse:
        chat_service = _service(state, "chat_service")
        config_service = _service(state, "config_service")
        return MCPConfigResponse(
            mcp_servers=config_service.load_mcp_servers(),
            runtime=await chat_service.get_mcp_status(),
        )

    @put("/mcp")
    async def save_mcp_config(
        self,
        data: SaveMCPConfigRequest,
        state: State,
    ) -> MCPConfigResponse:
        chat_service = _service(state, "chat_service")
        config_service = _service(state, "config_service")
        try:
            runtime = await chat_service.replace_mcp_servers(data.mcp_servers)
        except MCPValidationError as exc:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return MCPConfigResponse(
            mcp_servers=config_service.load_mcp_servers(),
            runtime=runtime,
        )

    @post("/mcp/reload")
    async def reload_mcp_config(self, state: State) -> MCPConfigResponse:
        chat_service = _service(state, "chat_service")
        config_service = _service(state, "config_service")
        runtime = await chat_service.reload_mcp()
        return MCPConfigResponse(
            mcp_servers=config_service.load_mcp_servers(),
            runtime=runtime,
        )


class SystemController(Controller):
    path = "/system"

    @post("/select-directory")
    async def select_directory(
        self,
        data: SelectDirectoryRequest,
        state: State,
    ) -> SelectDirectoryResponse:
        selected_path = await asyncio.to_thread(
            _service(state, "directory_picker_service").select_directory,
            data.initial_path,
        )
        return SelectDirectoryResponse(
            selected=bool(selected_path),
            project_path=selected_path or "",
        )


class ChatController(Controller):
    path = "/chat"

    @post("/sessions")
    async def create_session(
        self,
        request: Request[Any, Any, Any],
        state: State,
    ) -> CreateSessionResponse:
        try:
            raw_payload = await request.json()
        except Exception:
            raw_payload = {}
        payload = CreateSessionRequest.model_validate(raw_payload or {})
        session_id = await _service(state, "chat_service").create_session(
            backend_id=payload.backend_id,
            project_path=payload.project_path,
        )
        return CreateSessionResponse(session_id=session_id)

    @get("/sessions")
    async def list_sessions(self, state: State) -> SessionListResponse:
        sessions = _service(state, "chat_service").list_session_summaries()
        return SessionListResponse(sessions=sessions)

    @post("/sessions/{session_id:str}/attachments")
    async def upload_chat_attachment(
        self,
        session_id: str,
        request: Request[Any, Any, Any],
        state: State,
    ) -> AttachmentUploadResponse:
        try:
            form = await request.form()
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise AttachmentStorageError("Multipart form field 'file' is required.")
            payload = await _service(state, "chat_service").save_chat_attachment(
                session_id,
                upload,
            )
        except AttachmentStorageError as exc:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return AttachmentUploadResponse.model_validate(payload)

    @get("/sessions/{session_id:str}/attachments/{attachment_id:str}/content")
    async def get_chat_attachment_content(
        self,
        session_id: str,
        attachment_id: str,
        state: State,
    ) -> File:
        try:
            path, mime_type, name = _service(state, "chat_service").get_attachment_media_path(
                session_id,
                attachment_id,
            )
        except AttachmentStorageError as exc:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return File(
            path=path,
            filename=name,
            media_type=mime_type,
            content_disposition_type="inline",
        )

    @get("/sessions/{session_id:str}")
    async def get_session(
        self,
        session_id: str,
        state: State,
        activity_limit: int | None = None,
    ) -> SessionTranscriptResponse:
        chat_service = _service(state, "chat_service")
        transcript = await chat_service.load_session_transcript(
            session_id,
            activity_limit=activity_limit,
        )
        metadata = chat_service.get_session_metadata(session_id)
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
            backend_runtime=chat_service.get_backend_runtime(session_id),
            pending_requests=chat_service.get_pending_requests(session_id),
            pending_approvals=chat_service.get_pending_approvals(session_id),
            messages=transcript.messages,
            activity_events=transcript.activity_events,
            activity_history=transcript.activity_history,
            codex_turn_timings=transcript.codex_turn_timings,
        )

    @get("/sessions/{session_id:str}/activity-events")
    async def get_session_activity_events(
        self,
        session_id: str,
        state: State,
        before: int | None = None,
        limit: int | None = None,
    ) -> SessionActivityPageResponse:
        activity_events, activity_history = _service(
            state,
            "chat_service",
        ).get_session_activity_page(
            session_id,
            before=before,
            limit=limit,
        )
        return SessionActivityPageResponse(
            session_id=session_id,
            activity_events=activity_events,
            activity_history=activity_history,
        )

    @delete("/sessions/{session_id:str}", status_code=200)
    async def delete_session(self, session_id: str, state: State) -> DeleteSessionResponse:
        deleted = await _service(state, "chat_service").delete_session(session_id)
        return DeleteSessionResponse(session_id=session_id, deleted=deleted)

    @post("/sessions/{session_id:str}/approvals/respond")
    async def respond_to_approval(
        self,
        session_id: str,
        data: ApprovalResponseRequest,
        state: State,
    ) -> Response:
        handled = await _service(state, "chat_service").respond_to_pending_request(
            session_id=session_id,
            request_id=data.request_id,
            decision=data.decision,
            content=data.content,
        )
        if not handled:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail="Approval request not found.",
            )
        return Response(content={"ok": True})

    @post("/stream", status_code=200)
    async def stream_chat(self, data: ChatStreamRequest, state: State) -> ServerSentEvent:
        if not data.session_id:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="session_id is required.",
            )

        chat_service = _service(state, "chat_service")
        if chat_service.is_channel_session(data.session_id):
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="Channel-backed sessions are read-only in the chat workspace.",
            )
        try:
            if hasattr(chat_service, "build_chat_input_payload"):
                input_payload = chat_service.build_chat_input_payload(
                    session_id=data.session_id,
                    message=data.message,
                    input_items=data.input_items,
                    attachment_ids=data.attachment_ids,
                )
            else:
                input_payload = data.message or ""
        except AttachmentStorageError as exc:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        async def event_stream() -> AsyncIterator[ServerSentEventMessage]:
            async for item in chat_service.stream_chat(
                data.session_id,
                input_payload,
                raw_message=data.message,
                attachment_ids=data.attachment_ids,
            ):
                yield ServerSentEventMessage(
                    event=item["event"],
                    data=json.dumps(item["data"], ensure_ascii=False),
                )

        return ServerSentEvent(event_stream())


class ChannelController(Controller):
    path = "/channel"

    @get("/workspace")
    async def get_channel_workspace(self, state: State) -> ChannelWorkspaceResponse:
        snapshot = await _service(
            state,
            "channel_workspace_service",
        ).get_workspace_snapshot()
        return ChannelWorkspaceResponse(
            platforms=[item.model_dump() for item in snapshot.platforms],
            accounts=[item.model_dump() for item in snapshot.accounts],
        )

    @get("/platforms")
    async def get_channel_platforms(self, state: State) -> ChannelPlatformsResponse:
        return ChannelPlatformsResponse(
            platforms=_service(
                state,
                "channel_workspace_service",
            ).get_registered_platforms()
        )

    @get("/config")
    async def get_channel_config(self, state: State) -> ChannelConfigResponse:
        config = _service(state, "channel_workspace_service").load_config()
        return ChannelConfigResponse(**config.model_dump())

    @put("/config")
    async def save_channel_config(
        self,
        data: SaveChannelConfigRequest,
        state: State,
    ) -> ChannelConfigResponse:
        config = _service(state, "channel_workspace_service").save_config(
            data.model_dump()
        )
        return ChannelConfigResponse(**config.model_dump())

    @get("/accounts")
    async def get_channel_accounts(self, state: State) -> ChannelAccountsResponse:
        accounts = await _service(state, "channel_workspace_service").get_accounts()
        return ChannelAccountsResponse(accounts=[item.model_dump() for item in accounts])

    @post("/accounts/weixin/login")
    async def login_weixin_account(
        self,
        data: ChannelLoginRequest,
        state: State,
    ) -> Response:
        result = await _service(state, "channel_workspace_service").login(
            platform="weixin",
            account_id=data.account_id,
        )
        return Response(content=result)

    @post("/accounts/weixin/{account_id:str}/start")
    async def start_weixin_account(
        self,
        account_id: str,
        state: State,
    ) -> ChannelAccountActionResponse:
        account = await _service(state, "channel_workspace_service").start_account(
            platform="weixin",
            account_id=account_id,
        )
        return ChannelAccountActionResponse(account=account.model_dump())

    @post("/accounts/weixin/{account_id:str}/stop")
    async def stop_weixin_account(
        self,
        account_id: str,
        state: State,
    ) -> ChannelAccountActionResponse:
        account = await _service(state, "channel_workspace_service").stop_account(
            platform="weixin",
            account_id=account_id,
        )
        return ChannelAccountActionResponse(account=account.model_dump())

    @get("/monitor/sessions")
    async def get_channel_monitor_sessions(self, state: State) -> SessionListResponse:
        sessions = await _service(
            state,
            "channel_workspace_service",
        ).get_monitor_sessions()
        return SessionListResponse(sessions=sessions)


class EventsController(Controller):
    path = "/events"

    @get("/stream")
    async def stream_events(self, state: State) -> ServerSentEvent:
        event_broker = _service(state, "event_broker")
        shutdown_event = _shutdown_event(state)

        async def event_stream() -> AsyncIterator[ServerSentEventMessage]:
            subscriber = event_broker.subscribe()
            try:
                yield ServerSentEventMessage(
                    event="connected",
                    data=json.dumps({"status": "ok"}, ensure_ascii=False),
                )
                while True:
                    item, shutting_down = await _wait_for_event_stream_item(
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
                event_broker.unsubscribe(subscriber)

        return ServerSentEvent(event_stream())
