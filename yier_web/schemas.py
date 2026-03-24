from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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
CodexWorkMode = Literal["plan", "build"]
CodexApprovalPolicy = Literal["untrusted", "on-failure", "on-request", "never"]
CodexSandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
CodexPersonality = Literal["none", "friendly", "pragmatic"]
CodexReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
CodexServiceTier = Literal["", "fast", "flex"]
CodexApprovalsReviewer = Literal["user", "guardian_subagent"]
ApprovalDecision = Literal["accept", "accept_for_session", "decline", "cancel"]


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
    session_defaults: "SessionDefaultsSettings" = Field(default_factory=lambda: SessionDefaultsSettings())
    codex: "StoredCodexSettings" = Field(default_factory=lambda: StoredCodexSettings())
    allowed_roots: list[str] = Field(default_factory=list)


class SessionDefaultsSettings(BaseModel):
    default_backend_id: BackendId = "yier"
    default_project_path: str = ""
    channel_backend_id: BackendId = "yier"
    channel_project_path: str = ""
    channel_auto_approve_codex_requests: bool = True


class StoredCodexSettings(BaseModel):
    launcher_command: str = "codex app-server --listen stdio://"
    model: str = ""
    sandbox: CodexSandboxMode = "workspace-write"
    approval_policy: CodexApprovalPolicy = "on-request"
    approvals_reviewer: CodexApprovalsReviewer = "user"
    personality: CodexPersonality = "friendly"
    reasoning_effort: CodexReasoningEffort = "medium"
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
    session_defaults: SessionDefaultsSettings = Field(default_factory=SessionDefaultsSettings)
    codex: CodexConfigPayload = Field(default_factory=CodexConfigPayload)
    allowed_roots: list[str] = Field(default_factory=list)
    mcp_runtime: dict[str, MCPRuntimeEntry] = Field(default_factory=dict)


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
    session_defaults: SessionDefaultsSettings = Field(default_factory=SessionDefaultsSettings)
    codex: StoredCodexSettings = Field(default_factory=StoredCodexSettings)


class ChannelMetaPayload(BaseModel):
    platform: str
    account_id: str
    peer_id: str


class ApprovalOption(BaseModel):
    value: ApprovalDecision
    label: str


class PendingApproval(BaseModel):
    request_id: str
    method: str
    kind: str
    title: str
    detail: str
    options: list[ApprovalOption] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class BackendRuntimePayload(BaseModel):
    backend_id: BackendId
    label: str
    ready: bool = True
    status: str = "idle"
    thread_id: str | None = None
    active_flags: list[str] = Field(default_factory=list)
    detail: str | None = None
    pending_approval_count: int = 0


class CreateSessionRequest(BaseModel):
    backend_id: BackendId | None = None
    project_path: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str


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


class StoredSessionMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    reasoning_content: str | None = None
    tool_call_id: str | None = None
    source: SessionSource = "chat"
    channel_meta: ChannelMetaPayload | None = None


class SessionTranscriptResponse(BaseModel):
    session_id: str
    source: SessionSource = "chat"
    backend_id: BackendId = "yier"
    project_path: str = ""
    channel_meta: ChannelMetaPayload | None = None
    codex_work_mode: CodexWorkMode | None = None
    backend_runtime: BackendRuntimePayload | None = None
    pending_approvals: list[PendingApproval] = Field(default_factory=list)
    messages: list[StoredSessionMessage] = Field(default_factory=list)
    activity_events: list[StoredActivityEvent] = Field(default_factory=list)


class ChatStreamRequest(BaseModel):
    session_id: str
    message: str

    @field_validator("session_id", "message")
    @classmethod
    def strip_fields(cls, value: str) -> str:
        return value.strip()


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
    request_id: str
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


class CodexNativeSessionSummary(BaseModel):
    thread_id: str
    title: str
    preview: str
    updated_at: float
    started_at: float
    cwd: str
    project: str
    project_path: str
    source: str = "active"


class CodexProjectGroup(BaseModel):
    project: str
    project_path: str
    session_count: int
    sessions: list[CodexNativeSessionSummary] = Field(default_factory=list)


class CodexWorkspaceResponse(BaseModel):
    projects: list[CodexProjectGroup] = Field(default_factory=list)
