from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


MCPRuntimeStatus = Literal[
    "connected",
    "disabled",
    "failed",
    "needs_auth",
    "needs_client_registration",
]
FrontendMode = Literal["proxy", "static", "missing"]
SessionSource = Literal["chat", "channel"]
LLMProvider = Literal["", "zai", "zai-coding-plan"]
BackendId = Literal["yier", "codex"]
WorkspaceSurface = Literal["yier", "codex", "claude"]
CodexWorkMode = Literal["plan", "build"]
CodexApprovalPolicy = Literal["untrusted", "on-failure", "on-request", "never"]
CodexSandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
CodexPersonality = Literal["none", "friendly", "pragmatic"]
CodexReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
CodexServiceTier = Literal["", "fast", "flex"]
CodexApprovalsReviewer = Literal["user", "guardian_subagent"]
ApprovalDecision = Literal["accept", "accept_for_session", "decline", "cancel"]
CodexInputItemType = Literal["text", "image", "localImage", "skill", "mention"]
CodexAttachmentKind = Literal["image", "text", "binary"]


class StoredLLMSettings(BaseModel):
    provider: LLMProvider = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""

    @property
    def is_preset(self) -> bool:
        return self.provider in {"zai", "zai-coding-plan"}

    @property
    def is_ready(self) -> bool:
        has_model = bool(self.model.strip())
        has_api_key = bool(self.api_key.strip())
        if self.is_preset:
            return bool(self.provider and has_model and has_api_key)
        return bool(self.base_url.strip() and has_model and has_api_key)


class WebSettings(BaseModel):
    llm: StoredLLMSettings = Field(default_factory=StoredLLMSettings)
    session_defaults: "SessionDefaultsSettings" = Field(
        default_factory=lambda: SessionDefaultsSettings()
    )
    codex: "StoredCodexSettings" = Field(default_factory=lambda: StoredCodexSettings())
    allowed_roots: list[str] = Field(default_factory=list)


class SessionDefaultsSettings(BaseModel):
    default_backend_id: BackendId = "yier"
    default_project_path: str = ""
    channel_backend_id: BackendId = "yier"
    channel_project_path: str = ""
    channel_auto_approve_codex_requests: bool = True
    workspace_surface: WorkspaceSurface = "yier"

    @field_validator("default_backend_id", "channel_backend_id")
    @classmethod
    def normalize_chat_backend_id(cls, value: BackendId) -> BackendId:
        return "yier" if value == "codex" else value

    @field_validator("workspace_surface")
    @classmethod
    def normalize_workspace_surface(cls, value: WorkspaceSurface) -> WorkspaceSurface:
        return "yier" if value == "codex" else value


class StoredCodexSettings(BaseModel):
    launcher_command: str = "codex app-server --listen stdio://"
    model: str = "gpt-5.4"
    sandbox: CodexSandboxMode = "workspace-write"
    approval_policy: CodexApprovalPolicy = "on-request"
    approvals_reviewer: CodexApprovalsReviewer = "user"
    personality: CodexPersonality = "friendly"
    reasoning_effort: CodexReasoningEffort = "medium"
    show_reasoning_cards: bool = False
    service_tier: CodexServiceTier = ""

    @field_validator("launcher_command", "model")
    @classmethod
    def strip_string_fields(cls, value: str) -> str:
        return value.strip()


class BackendHealth(BaseModel):
    ready: bool
    detail: str | None = None


class BackendOption(BaseModel):
    id: BackendId
    label: str


class CodexConfigPayload(BaseModel):
    launcher_command: str = ""
    model: str = ""
    sandbox: CodexSandboxMode = "workspace-write"
    approval_policy: CodexApprovalPolicy = "on-request"
    approvals_reviewer: CodexApprovalsReviewer = "user"
    personality: CodexPersonality = "friendly"
    reasoning_effort: CodexReasoningEffort = "medium"
    show_reasoning_cards: bool = False
    service_tier: CodexServiceTier = ""


class LLMConfigPayload(BaseModel):
    provider: LLMProvider = ""
    base_url: str = ""
    model: str = ""
    has_api_key: bool = False


class MCPRuntimeEntry(BaseModel):
    status: MCPRuntimeStatus
    tool_count: int = 0
    error: str | None = None


class FrontendHealth(BaseModel):
    ready: bool
    mode: FrontendMode
    detail: str | None = None


class LLMHealth(BaseModel):
    ready: bool
    detail: str | None = None


class MCPHealth(BaseModel):
    ready: bool
    detail: str | None = None
    runtime: dict[str, MCPRuntimeEntry] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    frontend: FrontendHealth
    llm: LLMHealth
    mcp: MCPHealth
    backends: dict[BackendId, BackendHealth] = Field(default_factory=dict)
    allowed_roots: list[str] = Field(default_factory=list)


class ConfigResponse(BaseModel):
    llm: LLMConfigPayload
    backends: list[BackendOption] = Field(default_factory=list)
    session_defaults: SessionDefaultsSettings = Field(
        default_factory=SessionDefaultsSettings
    )
    codex: CodexConfigPayload = Field(default_factory=CodexConfigPayload)
    allowed_roots: list[str] = Field(default_factory=list)
    mcp_runtime: dict[str, MCPRuntimeEntry] = Field(default_factory=dict)


class AuthSessionResponse(BaseModel):
    enabled: bool = False
    authenticated: bool = True


class AuthLoginRequest(BaseModel):
    password: str = ""

    @field_validator("password")
    @classmethod
    def strip_password(cls, value: str) -> str:
        return value.strip()


class SaveLLMRequest(BaseModel):
    provider: LLMProvider = ""
    base_url: str
    model: str
    api_key: str | None = None

    @field_validator("base_url", "model")
    @classmethod
    def strip_required_fields(cls, value: str) -> str:
        return value.strip()


class SaveAllowedRootsRequest(BaseModel):
    allowed_roots: list[str]

    @field_validator("allowed_roots")
    @classmethod
    def validate_allowed_roots(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class MCPConfigResponse(BaseModel):
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    runtime: dict[str, MCPRuntimeEntry] = Field(default_factory=dict)


class SaveMCPConfigRequest(BaseModel):
    mcp_servers: dict[str, dict[str, Any]]


class SaveAppSettingsRequest(BaseModel):
    session_defaults: SessionDefaultsSettings = Field(
        default_factory=SessionDefaultsSettings
    )
    codex: StoredCodexSettings = Field(default_factory=StoredCodexSettings)


class ChannelMetaPayload(BaseModel):
    platform: str
    account_id: str
    peer_id: str


class ApprovalOption(BaseModel):
    value: ApprovalDecision
    label: str


class PendingApproval(BaseModel):
    request_id: str | int
    method: str
    kind: str
    title: str
    detail: str
    options: list[ApprovalOption] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class PendingRequest(PendingApproval):
    item_id: str | None = None


class BackendRuntimePayload(BaseModel):
    backend_id: BackendId
    label: str
    ready: bool = True
    status: str = "idle"
    thread_id: str | None = None
    active_flags: list[str] = Field(default_factory=list)
    detail: str | None = None
    pending_request_count: int = 0
    pending_approval_count: int = 0
    ipc_owner_client_id: str | None = None


class CreateSessionRequest(BaseModel):
    backend_id: BackendId | None = None
    project_path: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str


class SelectDirectoryRequest(BaseModel):
    initial_path: str | None = None

    @field_validator("initial_path")
    @classmethod
    def strip_initial_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SelectDirectoryResponse(BaseModel):
    selected: bool = False
    project_path: str = ""


class SessionSummary(BaseModel):
    session_id: str
    title: str
    preview: str
    updated_at: float
    message_count: int = 0
    source: SessionSource = "chat"
    backend_id: BackendId = "yier"
    project_path: str = ""
    channel_meta: ChannelMetaPayload | None = None
    codex_work_mode: CodexWorkMode | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary] = Field(default_factory=list)


class DeleteSessionResponse(BaseModel):
    session_id: str
    deleted: bool


class StoredActivityEvent(BaseModel):
    event: str
    data: dict[str, Any] = Field(default_factory=dict)


class ActivityHistoryPayload(BaseModel):
    total_count: int = 0
    returned_count: int = 0
    next_before: int | None = None


class MessageAttachmentPayload(BaseModel):
    id: str | None = None
    name: str = ""
    mime_type: str = "application/octet-stream"
    size: int | None = None
    kind: CodexAttachmentKind = "binary"
    preview_url: str | None = None
    content_url: str | None = None
    path: str | None = None


class StoredSessionMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    reasoning_content: str | None = None
    tool_call_id: str | None = None
    sequence: int | None = None
    source: SessionSource = "chat"
    channel_meta: ChannelMetaPayload | None = None
    attachments: list[MessageAttachmentPayload] = Field(default_factory=list)


class CodexTurnTimingPayload(BaseModel):
    turn_id: str = ""
    turn_started_at_ms: int | None = None
    final_assistant_started_at_ms: int | None = None


class SessionTranscriptResponse(BaseModel):
    session_id: str
    source: SessionSource = "chat"
    backend_id: BackendId = "yier"
    project_path: str = ""
    channel_meta: ChannelMetaPayload | None = None
    codex_work_mode: CodexWorkMode | None = None
    backend_runtime: BackendRuntimePayload | None = None
    pending_requests: list[PendingRequest] = Field(default_factory=list)
    pending_approvals: list[PendingApproval] = Field(default_factory=list)
    messages: list[StoredSessionMessage] = Field(default_factory=list)
    activity_events: list[StoredActivityEvent] = Field(default_factory=list)
    activity_history: ActivityHistoryPayload = Field(
        default_factory=ActivityHistoryPayload
    )
    codex_turn_timings: list[CodexTurnTimingPayload] = Field(default_factory=list)


class SessionActivityPageResponse(BaseModel):
    session_id: str
    activity_events: list[StoredActivityEvent] = Field(default_factory=list)
    activity_history: ActivityHistoryPayload = Field(
        default_factory=ActivityHistoryPayload
    )


class CodexInputItem(BaseModel):
    type: CodexInputItemType
    text: str | None = None
    url: str | None = None
    path: str | None = None
    name: str | None = None
    text_elements: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("text", "url", "path", "name")
    @classmethod
    def strip_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AttachmentUploadResponse(BaseModel):
    id: str
    name: str
    mime_type: str
    size: int
    kind: CodexAttachmentKind
    preview_url: str | None = None
    input_items: list[CodexInputItem] = Field(default_factory=list)


class ChatStreamRequest(BaseModel):
    session_id: str
    message: str | None = None
    input_items: list[CodexInputItem] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)

    @field_validator("session_id")
    @classmethod
    def strip_session_id(cls, value: str) -> str:
        return value.strip()

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("attachment_ids")
    @classmethod
    def strip_attachment_ids(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]

    @model_validator(mode="after")
    def validate_input(self) -> "ChatStreamRequest":
        if self.message or self.input_items or self.attachment_ids:
            return self
        raise ValueError("message, input_items, or attachment_ids is required.")


class CodexTurnControlRequest(BaseModel):
    turn_id: str | None = None
    message: str | None = None
    input_items: list[CodexInputItem] = Field(default_factory=list)

    @field_validator("turn_id", "message")
    @classmethod
    def strip_optional_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class CodexTurnControlResponse(BaseModel):
    ok: bool = True
    result: dict[str, Any] = Field(default_factory=dict)


class ChannelWorkspaceResponse(BaseModel):
    platforms: list[dict[str, Any]] = Field(default_factory=list)
    accounts: list[dict[str, Any]] = Field(default_factory=list)


class ChannelPlatformsResponse(BaseModel):
    platforms: list[dict[str, Any]] = Field(default_factory=list)


class ChannelAccountsResponse(BaseModel):
    accounts: list[dict[str, Any]] = Field(default_factory=list)


class ChannelConfigResponse(BaseModel):
    enabled_platforms: list[str] = Field(default_factory=list)
    weixin: dict[str, Any] = Field(default_factory=dict)


class SaveChannelConfigRequest(BaseModel):
    enabled_platforms: list[str] = Field(default_factory=list)
    weixin: dict[str, Any] = Field(default_factory=dict)


class ChannelLoginRequest(BaseModel):
    account_id: str | None = None


class ChannelAccountActionResponse(BaseModel):
    account: dict[str, Any]


class ApprovalResponseRequest(BaseModel):
    request_id: str | int
    decision: ApprovalDecision
    content: dict[str, Any] | None = None


class UpdateCodexSessionModeRequest(BaseModel):
    codex_work_mode: CodexWorkMode


class OpenCodexSessionRequest(BaseModel):
    thread_id: str

    @field_validator("thread_id")
    @classmethod
    def strip_thread_id(cls, value: str) -> str:
        return value.strip()


class OpenCodexSessionResponse(BaseModel):
    session_id: str


class ArchiveCodexSessionRequest(BaseModel):
    thread_id: str

    @field_validator("thread_id")
    @classmethod
    def strip_thread_id(cls, value: str) -> str:
        return value.strip()


class ArchiveCodexSessionResponse(BaseModel):
    thread_id: str
    archived: bool = True


class CodexPairedEditorStateRequest(BaseModel):
    session_id: str = ""
    content: str = ""
    selection_start: int = Field(default=0, ge=0)
    selection_end: int = Field(default=0, ge=0)

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session_id(cls, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()


class CodexNativeSessionSummary(BaseModel):
    thread_id: str
    title: str
    preview: str
    updated_at: float
    started_at: float
    status: str = "idle"
    cwd: str
    project: str
    project_path: str
    source: str = "active"


class CodexPairingExtensionSummary(BaseModel):
    id: str
    app_name: str
    workspace_name: str
    extension_name: str
    extension_version: str
    bundle_id: str
    marketplace_id: str
    capability_names: list[str] = Field(default_factory=list)
    capability_count: int = 0
    socket_path: str
    is_online: bool = False
    needs_reload: bool = False
    last_seen_at: float = 0.0


class CodexProjectGroup(BaseModel):
    project: str
    project_path: str
    session_count: int
    sessions: list[CodexNativeSessionSummary] = Field(default_factory=list)


class CodexWorkspaceResponse(BaseModel):
    projects: list[CodexProjectGroup] = Field(default_factory=list)
    paired_editors: list[CodexPairingExtensionSummary] = Field(default_factory=list)


CodexFilesystemEntryKind = Literal["directory", "file", "other"]


class CodexFilesystemEntry(BaseModel):
    name: str
    path: str
    kind: CodexFilesystemEntryKind
    extension: str = ""
    readable: bool = True


class CodexFilesystemResponse(BaseModel):
    path: str
    parent_path: str | None = None
    roots: list[CodexFilesystemEntry] = Field(default_factory=list)
    entries: list[CodexFilesystemEntry] = Field(default_factory=list)


class CodexThreadCreateRequest(BaseModel):
    project_path: str | None = None

    @field_validator("project_path")
    @classmethod
    def strip_project_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class CodexThreadCreateResponse(BaseModel):
    thread_id: str
    state: dict[str, Any] | None = None


class CodexThreadStateResponse(BaseModel):
    thread_id: str
    state: dict[str, Any] | None = None


class CodexThreadNameRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()
