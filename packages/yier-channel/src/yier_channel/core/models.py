from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ChannelDirection = Literal["inbound", "outbound"]
ChannelSource = Literal["channel"]


class ChannelMeta(BaseModel):
    platform: str
    account_id: str
    peer_id: str


class ChannelMessage(BaseModel):
    id: str
    session_id: str
    content: str
    direction: ChannelDirection
    source: ChannelSource = "channel"
    channel_meta: ChannelMeta
    timestamp_ms: int
    raw: dict[str, Any] = Field(default_factory=dict)


class ChannelAccountSummary(BaseModel):
    platform: str
    account_id: str
    configured: bool = False
    enabled: bool = True
    running: bool = False
    name: str | None = None
    last_inbound_at: int | None = None
    last_outbound_at: int | None = None
    last_error: str | None = None
    login_status: str | None = None


class ChannelPlatformSummary(BaseModel):
    name: str
    label: str
    implemented: bool = True
    account_count: int = 0
    running_count: int = 0


class ChannelWorkspaceSnapshot(BaseModel):
    platforms: list[ChannelPlatformSummary] = Field(default_factory=list)
    accounts: list[ChannelAccountSummary] = Field(default_factory=list)


class ChannelConfig(BaseModel):
    enabled_platforms: list[str] = Field(default_factory=lambda: ["weixin"])
    weixin: dict[str, Any] = Field(default_factory=dict)
