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
        return await self.workspace_service.login(
            platform=platform, account_id=account_id
        )

    async def start_account(self, platform: str, account_id: str):
        return await self.workspace_service.start_account(
            platform=platform, account_id=account_id
        )

    async def stop_account(self, platform: str, account_id: str):
        return await self.workspace_service.stop_account(
            platform=platform, account_id=account_id
        )

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
        final_assistant_message: str | None = None
        error_message: str | None = None
        finish_reason: str | None = None
        async for event in self.chat_service.stream_chat(
            message.session_id, message.content
        ):
            await self.event_broker.publish(event["event"], event["data"])
            if event["event"] == "assistant_message":
                content = event["data"].get("content")
                if isinstance(content, str) and content.strip():
                    final_assistant_message = content
            elif event["event"] == "error":
                message_text = event["data"].get("message")
                if isinstance(message_text, str) and message_text.strip():
                    error_message = message_text.strip()
            elif event["event"] == "done":
                event_finish_reason = event["data"].get("finish_reason")
                if isinstance(event_finish_reason, str) and event_finish_reason.strip():
                    finish_reason = event_finish_reason.strip()

        if finish_reason == "error":
            text = (
                f"处理失败：{error_message}"
                if error_message is not None
                else "处理失败，请稍后重试。"
            )
        elif final_assistant_message is not None:
            text = final_assistant_message
        else:
            text = "这次没有拿到可发送的最终结果，请稍后重试。"

        await self.workspace_service.send_text(
            platform=message.channel_meta.platform,
            account_id=message.channel_meta.account_id,
            peer_id=message.channel_meta.peer_id,
            text=text,
        )
