from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from yier_agents import Message


MCPRuntimeStatus = Literal[
    "connected",
    "disabled",
    "failed",
    "needs_auth",
    "needs_client_registration",
]
FrontendMode = Literal["proxy", "static", "missing"]


class StoredLLMSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""

    @property
    def is_ready(self) -> bool:
        return bool(self.base_url.strip() and self.api_key.strip() and self.model.strip())


class WebSettings(BaseModel):
    llm: StoredLLMSettings = Field(default_factory=StoredLLMSettings)
    allowed_roots: list[str] = Field(default_factory=list)


class LLMConfigPayload(BaseModel):
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
    base_url: str
    model: str
    api_key: str | None = None

    @field_validator("base_url", "model")
    @classmethod
    def strip_required_fields(cls, value: str) -> str:
        return value.strip()


class MCPConfigResponse(BaseModel):
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    runtime: dict[str, MCPRuntimeEntry] = Field(default_factory=dict)


class SaveMCPConfigRequest(BaseModel):
    mcp_servers: dict[str, dict[str, Any]]


class CreateSessionResponse(BaseModel):
    session_id: str


class SessionTranscriptResponse(BaseModel):
    session_id: str
    messages: list[Message] = Field(default_factory=list)


class ChatStreamRequest(BaseModel):
    session_id: str
    message: str

    @field_validator("session_id", "message")
    @classmethod
    def strip_fields(cls, value: str) -> str:
        return value.strip()
