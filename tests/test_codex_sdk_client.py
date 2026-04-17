from __future__ import annotations

import asyncio
from types import SimpleNamespace

from codex_app_server import AppServerConfig, TextInput
from codex_app_server.generated.v2_all import CollaborationMode, ModeKind, Settings

from yier_web.codex.sdk.client import (
    ApprovalAwareAppServerClient,
    ApprovalAwareAsyncThread,
    RequestAwareNotification,
    ToolRequestUserInputParams,
)


def test_approval_aware_async_client_can_surface_manual_user_input_requests() -> None:
    async def scenario() -> None:
        client = ApprovalAwareAppServerClient(
            config=AppServerConfig(),
            approval_callback=lambda request_id, method, params: {"decision": "decline"},
            manual_request_methods=frozenset({"item/tool/requestUserInput"}),
        )
        client._sync._read_message = lambda: {
            "id": "req-1",
            "method": "item/tool/requestUserInput",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "item-1",
                "questions": [
                    {
                        "id": "choice",
                        "header": "Pick one",
                        "question": "Which option?",
                        "options": [
                            {
                                "label": "A",
                                "description": "First option",
                            }
                        ],
                    }
                ],
            },
        }

        event = await client.next_notification()

        assert isinstance(event, RequestAwareNotification)
        assert event.request_id == "req-1"
        assert event.method == "item/tool/requestUserInput"
        assert isinstance(event.payload, ToolRequestUserInputParams)
        assert event.payload.questions[0].id == "choice"

    asyncio.run(scenario())


def test_approval_aware_async_client_can_reply_to_server_requests() -> None:
    async def scenario() -> None:
        client = ApprovalAwareAppServerClient(
            config=AppServerConfig(),
            approval_callback=lambda request_id, method, params: {"decision": "decline"},
        )
        payloads: list[dict[str, object]] = []
        client._sync._write_message = payloads.append

        await client.respond_to_server_request(
            "req-2",
            {"answers": {"choice": {"answers": ["A"]}}},
        )

        assert payloads == [
            {
                "id": "req-2",
                "result": {"answers": {"choice": {"answers": ["A"]}}},
            }
        ]

    asyncio.run(scenario())


def test_approval_aware_thread_turn_embeds_collaboration_mode_in_dict_params() -> None:
    async def scenario() -> None:
        captured: dict[str, object] = {}

        class FakeClient:
            async def turn_start(self, thread_id, input_items, params=None):  # type: ignore[no-untyped-def]
                captured["thread_id"] = thread_id
                captured["input_items"] = input_items
                captured["params"] = params
                return SimpleNamespace(turn=SimpleNamespace(id="turn-123"))

        thread = ApprovalAwareAsyncThread(FakeClient(), "thread-123")  # type: ignore[arg-type]
        collaboration_mode = CollaborationMode(
            mode=ModeKind.plan,
            settings=Settings(model="gpt-5.4", reasoning_effort="medium"),
        )

        handle = await thread.turn(
            TextInput("plan this"),
            collaboration_mode=collaboration_mode,
        )

        assert handle.id == "turn-123"
        assert captured["thread_id"] == "thread-123"
        assert captured["input_items"] == [{"type": "text", "text": "plan this"}]
        assert isinstance(captured["params"], dict)
        assert captured["params"]["collaborationMode"] == {
            "mode": "plan",
            "settings": {
                "model": "gpt-5.4",
                "reasoning_effort": "medium",
            },
        }

    asyncio.run(scenario())
