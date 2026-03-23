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
    allowed_roots: list[str] = Field(default_factory=list)


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
    allowed_roots: list[str] = Field(default_factory=list)


class ConfigResponse(BaseModel):
    llm: LLMConfigPayload
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


class ChannelMetaPayload(BaseModel):
    platform: str
    account_id: str
    peer_id: str


class CreateSessionResponse(BaseModel):
    session_id: str


class SessionSummary(BaseModel):
    session_id: str
    title: str
    preview: str
    updated_at: float
    message_count: int = 0
    source: SessionSource = "chat"
    channel_meta: ChannelMetaPayload | None = None


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
    channel_meta: ChannelMetaPayload | None = None
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
