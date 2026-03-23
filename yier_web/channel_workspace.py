from __future__ import annotations

from pathlib import Path

from yier_channel import ChannelManager, ChannelWorkspaceService
from yier_channel.core.models import ChannelMessage

from yier_web.chat import ChatService
from yier_web.event_stream import EventStreamBroker


class IntegratedChannelWorkspaceService:
    def __init__(
        self,
        project_root: Path,
        chat_service: ChatService,
        event_broker: EventStreamBroker,
    ) -> None:
        self.project_root = project_root.resolve()
        self.chat_service = chat_service
        self.event_broker = event_broker
        self.manager = ChannelManager(
            message_sink=self._handle_inbound_message,
            event_sink=self._handle_channel_event,
        )
        self.workspace_service = ChannelWorkspaceService(manager=self.manager)

    async def start(self) -> None:
        await self.workspace_service.start()

    async def stop(self) -> None:
        await self.workspace_service.stop()

    async def get_workspace_snapshot(self):
        return await self.workspace_service.get_workspace_snapshot()

    def load_config(self):
        return self.workspace_service.load_config()

    def save_config(self, payload: dict):
        return self.workspace_service.save_config(payload)

    async def get_accounts(self):
        return await self.workspace_service.get_accounts()

    async def login(self, platform: str, account_id: str | None = None):
        return await self.workspace_service.login(platform=platform, account_id=account_id)

    async def start_account(self, platform: str, account_id: str):
        return await self.workspace_service.start_account(platform=platform, account_id=account_id)

    async def stop_account(self, platform: str, account_id: str):
        return await self.workspace_service.stop_account(platform=platform, account_id=account_id)

    def get_registered_platforms(self):
        return self.workspace_service.get_registered_platforms()

    async def get_monitor_sessions(self):
        return self.chat_service.list_session_summaries(source="channel")

    async def _handle_channel_event(self, event: str, data: dict) -> None:
        await self.event_broker.publish(event, data)

    async def _handle_inbound_message(self, message: ChannelMessage) -> None:
        self.chat_service.mark_channel_session(
            message.session_id,
            message.channel_meta.model_dump(),
        )
        assistant_messages: list[str] = []
        async for event in self.chat_service.stream_chat(message.session_id, message.content):
            await self.event_broker.publish(event["event"], event["data"])
            if event["event"] == "assistant_message":
                content = event["data"].get("content")
                if isinstance(content, str) and content.strip():
                    assistant_messages.append(content)
        if not assistant_messages:
            return
        await self.workspace_service.send_text(
            platform=message.channel_meta.platform,
            account_id=message.channel_meta.account_id,
            peer_id=message.channel_meta.peer_id,
            text="\n\n".join(assistant_messages),
        )
