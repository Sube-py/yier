from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from yier_agents import (
    Agent,
    CompactionConfig,
    ErrorEvent,
    JSONSessionStore,
    LLM,
    LLMEndEvent,
    MCPManager,
    Message,
    MessageCompactEvent,
    SkillCatalog,
    Tool,
    ToolCallEndEvent,
    ToolCallStartEvent,
    create_list_files_tool,
    create_read_file_tool,
    create_replace_in_file_tool,
    create_run_command_tool,
    create_search_files_tool,
    create_write_file_tool,
)

from yier_web.config import AppConfigService
from yier_web.schemas import MCPRuntimeEntry, StoredLLMSettings


class ChatService:
    def __init__(
        self,
        project_root: Path,
        config_service: AppConfigService,
        mcp_manager: MCPManager | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config_service = config_service
        self.mcp_manager = mcp_manager or MCPManager(config_dir=self.config_service.yier_root)
        self.skill_catalog: SkillCatalog | None = None
        self.session_store = JSONSessionStore(self.config_service.sessions_path)
        self._agent: Agent | None = None
        self._agent_signature: tuple[Any, ...] | None = None
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.mcp_manager.start()
        self._started = True
        await self.reload_agent(force_mcp_reconnect=False)

    async def stop(self) -> None:
        if not self._started:
            return
        await self.mcp_manager.stop()
        self._started = False
        self._agent = None
        self._agent_signature = None

    async def reload_agent(self, force_mcp_reconnect: bool = False) -> None:
        async with self._lock:
            if self._started and force_mcp_reconnect:
                await self.mcp_manager.reload(force_reconnect=True)
            await self._rebuild_agent_locked()

    async def replace_mcp_servers(
        self,
        mcp_servers: dict[str, dict[str, Any]],
    ) -> dict[str, MCPRuntimeEntry]:
        self.config_service.save_mcp_servers(mcp_servers)
        if self._started:
            await self.mcp_manager.reload(force_reconnect=True)
        await self.reload_agent()
        return await self.get_mcp_status()

    async def reload_mcp(self) -> dict[str, MCPRuntimeEntry]:
        if self._started:
            await self.mcp_manager.reload(force_reconnect=True)
        await self.reload_agent()
        return await self.get_mcp_status()

    async def get_mcp_status(self) -> dict[str, MCPRuntimeEntry]:
        if self._started:
            await self.mcp_manager.reload_if_changed()
        snapshot = await self.mcp_manager.get_status()
        return {
            name: MCPRuntimeEntry(**payload)
            for name, payload in snapshot.items()
        }

    def create_session(self) -> str:
        return str(uuid4())

    def get_session_messages(self, session_id: str) -> list[Message]:
        return self.session_store.get_session_messages(session_id) or []

    async def stream_chat(self, session_id: str, user_message: str) -> AsyncIterator[dict[str, Any]]:
        agent = await self._get_agent()
        if agent is None:
            yield {
                "event": "error",
                "data": {
                    "session_id": session_id,
                    "message": "LLM configuration is incomplete. Update settings before chatting.",
                },
            }
            yield {"event": "done", "data": {"session_id": session_id, "finish_reason": "error"}}
            return

        yield {"event": "run_started", "data": {"session_id": session_id}}

        finish_reason = "stop"
        try:
            async for event in agent.run_stream(user_message, session_id):
                if isinstance(event, ToolCallStartEvent):
                    yield {
                        "event": "tool_call_start",
                        "data": {
                            "session_id": session_id,
                            "tool_name": event.tool_name,
                            "tool_call_id": event.tool_call_id,
                            "arguments": event.arguments,
                            "iteration": event.iteration,
                        },
                    }
                elif isinstance(event, ToolCallEndEvent):
                    yield {
                        "event": "tool_call_end",
                        "data": {
                            "session_id": session_id,
                            "tool_name": event.tool_name,
                            "tool_call_id": event.tool_call_id,
                            "result": event.result,
                            "is_error": event.is_error,
                            "iteration": event.iteration,
                        },
                    }
                elif isinstance(event, LLMEndEvent):
                    finish_reason = event.finish_reason
                    if event.message.reasoning_content:
                        yield {
                            "event": "reasoning",
                            "data": {
                                "session_id": session_id,
                                "content": event.message.reasoning_content,
                                "iteration": event.iteration,
                            },
                        }
                    if event.finish_reason == "stop" and event.message.content:
                        yield {
                            "event": "assistant_message",
                            "data": {
                                "session_id": session_id,
                                "content": event.message.content,
                                "iteration": event.iteration,
                            },
                        }
                elif isinstance(event, MessageCompactEvent):
                    yield {
                        "event": "reasoning",
                        "data": {
                            "session_id": session_id,
                            "content": (
                                f"Conversation memory compacted from "
                                f"{event.original_count} to {event.compacted_count} messages."
                            ),
                            "iteration": event.iteration,
                        },
                    }
                elif isinstance(event, ErrorEvent):
                    finish_reason = "error"
                    yield {
                        "event": "error",
                        "data": {
                            "session_id": session_id,
                            "message": f"{event.error_type}: {event.error_message}",
                            "iteration": event.iteration,
                        },
                    }
        except Exception as exc:
            finish_reason = "error"
            yield {
                "event": "error",
                "data": {
                    "session_id": session_id,
                    "message": str(exc),
                },
            }

        yield {
            "event": "done",
            "data": {
                "session_id": session_id,
                "finish_reason": finish_reason,
            },
        }

    async def _get_agent(self) -> Agent | None:
        if not self._started:
            await self.start()

        async with self._lock:
            await self.mcp_manager.reload_if_changed()
            signature = self._agent_state_signature()
            if signature != self._agent_signature:
                await self._rebuild_agent_locked()
            return self._agent

    async def _rebuild_agent_locked(self) -> None:
        settings = self.config_service.load_web_settings()
        signature = self._agent_state_signature()
        if not settings.llm.is_ready:
            self._agent = None
            self._agent_signature = signature
            return

        workspace_tools = self._build_workspace_tools(settings.allowed_roots)
        mcp_tools = await self.mcp_manager.get_tools()
        llm = self._build_llm(settings.llm)
        skill_catalog = self._get_skill_catalog()
        self._agent = Agent(
            llm=llm,
            tools=[*workspace_tools, *mcp_tools],
            system_prompt=(
                "You are Yier, a lightweight local-first desktop assistant. "
                "Help the user manage files, inspect code, write code, and operate "
                "carefully inside the allowed filesystem roots. Be concise, practical, "
                "and explicit about actions you take."
            ),
            verbose=False,
            max_iterations=100,
            enable_memory=True,
            skill_catalog=skill_catalog,
            session_store=self.session_store,
            compaction_config=CompactionConfig(
                enabled=True,
                trigger_message_count=24,
                preserve_recent_messages=8,
                summary_max_tokens=512,
            ),
        )
        self._agent_signature = signature

    def _agent_state_signature(self) -> tuple[Any, ...]:
        settings = self.config_service.load_web_settings()
        return (
            self.config_service.settings_marker(),
            tuple(settings.allowed_roots),
            self.config_service.mcp_marker(),
            self.mcp_manager.version,
        )

    def _build_workspace_tools(self, allowed_roots: list[str]) -> list[Tool]:
        roots = [Path(item).expanduser().resolve() for item in allowed_roots]
        return [
            create_list_files_tool(roots),
            create_read_file_tool(roots),
            create_replace_in_file_tool(roots),
            create_run_command_tool(roots),
            create_write_file_tool(roots),
            create_search_files_tool(roots),
        ]

    def _get_skill_catalog(self) -> SkillCatalog:
        if self.skill_catalog is None:
            self.skill_catalog = SkillCatalog.discover(
                self.project_root,
                include_project=True,
                include_global=True,
            )
        return self.skill_catalog

    def _build_llm(self, settings: StoredLLMSettings) -> LLM:
        return LLM(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.model,
        )
