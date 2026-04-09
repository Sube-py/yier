from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from yier_web.codex.ipc import CodexThreadFollowerBridge


async def _read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    length_bytes = await reader.readexactly(4)
    payload_length = int.from_bytes(length_bytes, byteorder="little")
    payload_bytes = await reader.readexactly(payload_length)
    return json_loads(payload_bytes)


def json_loads(payload_bytes: bytes) -> dict[str, Any]:
    import json

    payload = json.loads(payload_bytes.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


async def _send_frame(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    import json

    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    writer.write(len(payload_bytes).to_bytes(4, byteorder="little"))
    writer.write(payload_bytes)
    await writer.drain()


async def _wait_for_message(
    queue: asyncio.Queue[dict[str, Any]],
    *,
    predicate,
    timeout: float = 2.0,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for matching IPC message.")
        message = await asyncio.wait_for(queue.get(), timeout=remaining)
        if predicate(message):
            return message


class _FakeFollowupQueue:
    def __init__(self, prompts: list[str] | None = None) -> None:
        self.prompts = prompts or []

    def list_items(self) -> tuple[SimpleNamespace, ...]:
        return tuple(
            SimpleNamespace(owner_session_id="thread-1", prompt=prompt)
            for prompt in self.prompts
        )


class FakeChatService:
    def __init__(self) -> None:
        self.started_turns: list[tuple[str, list[dict[str, Any]] | dict[str, Any] | str]] = []
        self.interrupted_turns: list[tuple[str, str | None]] = []
        self.responded_raw_requests: list[tuple[str, str, dict[str, Any]]] = []
        self.updated_backend_states: list[tuple[str, dict[str, Any]]] = []
        self.published_events: list[tuple[str, dict[str, Any]]] = []
        self.followup_queue = _FakeFollowupQueue(["Review logs", "Push update"])
        self.event_broker = SimpleNamespace(publish=self._publish_event)

    async def _publish_event(self, event: str, data: dict[str, Any]) -> None:
        self.published_events.append((event, data))

    def can_handle_codex_conversation(self, conversation_id: str) -> bool:
        return conversation_id == "thread-1"

    def ensure_codex_conversation_session(self, conversation_id: str) -> str:
        if conversation_id != "thread-1":
            raise RuntimeError("unknown conversation")
        return conversation_id

    async def start_codex_turn_in_background(
        self,
        session_id: str,
        prompt: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any]:
        self.started_turns.append((session_id, prompt))
        return {
            "turn": {
                "id": "turn-started-1",
                "status": "inProgress",
                "items": [],
            }
        }

    async def steer_codex_turn(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        input_payload: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "input": input_payload,
        }

    async def interrupt_codex_turn(
        self,
        *,
        session_id: str,
        turn_id: str | None,
    ) -> dict[str, Any]:
        self.interrupted_turns.append((session_id, turn_id))
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "interrupted": True,
        }

    async def respond_to_codex_raw_request(
        self,
        session_id: str,
        request_id: str,
        response_payload: dict[str, Any],
    ) -> bool:
        self.responded_raw_requests.append((session_id, request_id, response_payload))
        return True

    def resolve_pending_approval_request_id(
        self,
        session_id: str,
        *,
        preferred_kind: str | None = None,
    ) -> str | None:
        if session_id != "thread-1":
            return None
        if preferred_kind in {None, "command"}:
            return "approval-1"
        return None

    def update_session_backend_state(self, session_id: str, updates: dict[str, Any]) -> None:
        self.updated_backend_states.append((session_id, updates))

    def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        if session_id == "thread-1":
            return {"backend_id": "codex"}
        return {"backend_id": "yier"}

    def get_backend_runtime(self, session_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            backend_id="codex",
            thread_id=session_id,
            status="active",
            active_flags=["streaming"],
            detail="Working",
            pending_approval_count=1,
        )

    def edit_last_codex_user_turn(self, session_id: str, content: str) -> None:
        self.updated_backend_states.append((session_id, {"edited_content": content}))

    def build_codex_ipc_conversation_state(self, session_id: str) -> dict[str, Any]:
        return {
            "id": session_id,
            "hostId": "local",
            "turns": [],
            "pendingSteers": [],
            "requests": [],
            "createdAt": 1,
            "updatedAt": 2,
            "title": "Thread 1",
            "source": "chat",
            "latestModel": "gpt-test",
            "latestReasoningEffort": None,
            "previousTurnModel": None,
            "latestCollaborationMode": {
                "mode": "default",
                "settings": {
                    "model": "gpt-test",
                    "reasoning_effort": None,
                    "developer_instructions": None,
                },
            },
            "hasUnreadTurn": False,
            "rolloutPath": "",
            "gitInfo": None,
            "resumeState": "resumed",
            "latestTokenUsageInfo": None,
            "cwd": "/tmp/project",
            "threadId": session_id,
            "threadRuntimeStatus": {
                "type": "active",
                "activeFlags": ["streaming"],
            },
        }

    def build_codex_ipc_queued_followups(self, session_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "q-1",
                "text": "Review logs",
                "context": {"workspaceRoots": ["/tmp/project"]},
                "cwd": "/tmp/project",
                "createdAt": 123,
            },
            {
                "id": "q-2",
                "text": "Push update",
                "context": {"workspaceRoots": ["/tmp/project"]},
                "cwd": "/tmp/project",
                "createdAt": 124,
            },
        ]


def _test_socket_path() -> Path:
    return Path("/tmp") / f"yipc-{os.getpid()}-{uuid4().hex[:8]}.sock"


def test_codex_thread_follower_bridge_publishes_local_event_for_stream_state_broadcast() -> None:
    async def scenario() -> None:
        fake_chat_service = FakeChatService()
        bridge = CodexThreadFollowerBridge(
            chat_service=fake_chat_service,  # type: ignore[arg-type]
            socket_path=_test_socket_path(),
        )

        await bridge.client._handle_broadcast(
            {
                "type": "broadcast",
                "method": "thread-stream-state-changed",
                "sourceClientId": "codex-app-client",
                "params": {
                    "conversationId": "thread-1",
                    "change": {
                        "type": "patches",
                        "patches": [
                            {
                                "op": "replace",
                                "path": ["turns", 7, "status"],
                                "value": "completed",
                            }
                        ],
                    },
                },
            }
        )

        assert fake_chat_service.published_events == [
            (
                "codex_session_updated",
                {
                    "session_id": "thread-1",
                    "source_client_id": "codex-app-client",
                    "change_type": "patches",
                },
            )
        ]

    asyncio.run(scenario())


def test_codex_thread_follower_bridge_handles_start_turn_and_broadcast(tmp_path: Path) -> None:
    async def scenario() -> None:
        socket_path = _test_socket_path()
        messages_from_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        client_ready = asyncio.Event()
        server_writer: asyncio.StreamWriter | None = None

        async def handle_connection(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal server_writer
            initialize_request = await _read_frame(reader)
            assert initialize_request["method"] == "initialize"
            server_writer = writer
            await _send_frame(
                writer,
                {
                    "type": "response",
                    "requestId": initialize_request["requestId"],
                    "resultType": "success",
                    "method": "initialize",
                    "handledByClientId": "router-client",
                    "result": {"clientId": "client-yier"},
                },
            )
            client_ready.set()
            while True:
                try:
                    message = await _read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                await messages_from_client.put(message)

        server = await asyncio.start_unix_server(handle_connection, path=socket_path)
        fake_chat_service = FakeChatService()
        bridge = CodexThreadFollowerBridge(
            chat_service=fake_chat_service,  # type: ignore[arg-type]
            socket_path=socket_path,
        )

        try:
            await bridge.start()
            assert await bridge.client.wait_until_connected(timeout=2.0)
            await asyncio.wait_for(client_ready.wait(), timeout=2.0)
            assert server_writer is not None

            await _send_frame(
                server_writer,
                {
                    "type": "client-discovery-request",
                    "requestId": "discovery-1",
                    "request": {
                        "method": "thread-follower-start-turn",
                        "params": {"conversationId": "thread-1"},
                        "version": 1,
                    },
                },
            )
            discovery_response = await asyncio.wait_for(messages_from_client.get(), timeout=2.0)
            assert discovery_response == {
                "type": "client-discovery-response",
                "requestId": "discovery-1",
                "response": {"canHandle": True},
            }

            await _send_frame(
                server_writer,
                {
                    "type": "request",
                    "requestId": "request-1",
                    "method": "thread-follower-start-turn",
                    "params": {
                        "conversationId": "thread-1",
                        "prompt": "Implement syncing",
                    },
                    "version": 1,
                },
            )
            request_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("type") == "snapshot",
            )
            assert request_broadcast["params"]["conversationId"] == "thread-1"
            assert request_broadcast["params"]["type"] == "thread-stream-state-changed"
            assert request_broadcast["params"]["version"] == 5
            assert (
                request_broadcast["params"]["change"]["conversationState"]["_yier_trigger_event"]
                == "thread-follower-start-turn"
            )

            request_response = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "response"
                and message.get("requestId") == "request-1",
            )
            assert request_response["type"] == "response"
            assert request_response["requestId"] == "request-1"
            assert request_response["resultType"] == "success"
            assert request_response["result"]["result"]["turn"]["id"] == "turn-started-1"
            assert fake_chat_service.started_turns == [("thread-1", "Implement syncing")]

            fake_chat_service.build_codex_ipc_conversation_state = lambda session_id: {  # type: ignore[method-assign]
                "id": session_id,
                "hostId": "local",
                "turns": [
                    {
                        "id": "turn-started-1",
                        "turnId": "turn-started-1",
                        "status": "inProgress",
                        "items": [
                            {
                                "type": "userMessage",
                                "id": "item-user-1",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Implement syncing",
                                        "text_elements": [],
                                    }
                                ],
                            }
                        ],
                        "error": None,
                        "diff": None,
                        "turnStartedAtMs": 100,
                        "finalAssistantStartedAtMs": None,
                        "params": {
                            "threadId": session_id,
                            "input": [
                                {
                                    "type": "text",
                                    "text": "Implement syncing",
                                    "text_elements": [],
                                }
                            ],
                        },
                    }
                ],
                "pendingSteers": [],
                "requests": [],
                "createdAt": 1,
                "updatedAt": 2,
                "title": "Thread 1",
                "source": "chat",
                "latestModel": "gpt-test",
                "latestReasoningEffort": None,
                "previousTurnModel": None,
                "latestCollaborationMode": {
                    "mode": "default",
                    "settings": {
                        "model": "gpt-test",
                        "reasoning_effort": None,
                        "developer_instructions": None,
                    },
                },
                "hasUnreadTurn": False,
                "rolloutPath": "",
                "gitInfo": {
                    "branch": "main",
                    "sha": "abc123",
                    "originUrl": "git@example.com:repo.git",
                },
                "resumeState": "resumed",
                "latestTokenUsageInfo": None,
                "cwd": "/tmp/project",
                "threadId": session_id,
                "threadRuntimeStatus": {
                    "type": "active",
                    "activeFlags": ["streaming"],
                },
            }

            await bridge.notify_stream_event(
                "run_started",
                {"session_id": "thread-1", "turn_id": "turn-started-1"},
            )
            git_info_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "replace",
                        "path": ["gitInfo"],
                        "value": {
                            "branch": "main",
                            "sha": "abc123",
                            "originUrl": "git@example.com:repo.git",
                        },
                    }
                ],
            )
            assert git_info_broadcast["params"]["conversationId"] == "thread-1"

            start_turn_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("type") == "patches"
                and message.get("params", {}).get("change", {}).get("patches", [])[0].get("op") == "add",
            )
            assert start_turn_broadcast["params"]["change"]["patches"] == [
                {
                    "op": "add",
                    "path": ["turns", 0],
                    "value": {
                        "params": {
                            "threadId": "thread-1",
                            "input": [
                                {
                                    "type": "text",
                                    "text": "Implement syncing",
                                    "text_elements": [],
                                }
                            ],
                        },
                        "turnId": None,
                        "status": "inProgress",
                        "turnStartedAtMs": 100,
                        "finalAssistantStartedAtMs": None,
                        "error": None,
                        "diff": None,
                        "items": [],
                    },
                },
                {
                    "op": "replace",
                    "path": ["latestCollaborationMode"],
                    "value": {
                        "mode": "default",
                        "settings": {
                            "model": "gpt-test",
                            "reasoning_effort": None,
                            "developer_instructions": None,
                        },
                    },
                },
                {
                    "op": "replace",
                    "path": ["updatedAt"],
                    "value": 2,
                },
            ]

            turn_id_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "replace",
                        "path": ["turns", 0, "turnId"],
                        "value": "turn-started-1",
                    }
                ],
            )
            assert turn_id_broadcast["params"]["type"] == "thread-stream-state-changed"
            assert turn_id_broadcast["params"]["version"] == 5

            requests_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "replace",
                        "path": ["requests"],
                        "value": [],
                    }
                ],
            )
            assert requests_broadcast["params"]["conversationId"] == "thread-1"

            snapshot_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("type") == "snapshot",
            )
            snapshot_state = snapshot_broadcast["params"]["change"]["conversationState"]
            assert snapshot_state["_yier_trigger_event"] == "run_started"
            assert snapshot_state["turns"][0]["status"] == "inProgress"
            assert snapshot_state["turns"][0]["items"] == []
            assert snapshot_state["turns"][0]["turnId"] == "turn-started-1"

            user_item_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "add",
                        "path": ["turns", 0, "items", 0],
                        "value": {
                            "type": "userMessage",
                            "id": "item-user-1",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Implement syncing",
                                    "text_elements": [],
                                }
                            ],
                        },
                    }
                ],
            )
            assert user_item_broadcast["params"]["conversationId"] == "thread-1"
        finally:
            await bridge.stop()
            if server_writer is not None:
                server_writer.close()
                await server_writer.wait_closed()
            server.close()
            socket_path.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_codex_thread_follower_bridge_accepts_object_collaboration_mode() -> None:
    async def scenario() -> None:
        socket_path = _test_socket_path()
        messages_from_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        client_ready = asyncio.Event()
        server_writer: asyncio.StreamWriter | None = None

        async def handle_connection(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal server_writer
            initialize_request = await _read_frame(reader)
            server_writer = writer
            await _send_frame(
                writer,
                {
                    "type": "response",
                    "requestId": initialize_request["requestId"],
                    "resultType": "success",
                    "method": "initialize",
                    "handledByClientId": "router-client",
                    "result": {"clientId": "client-yier"},
                },
            )
            client_ready.set()
            while True:
                try:
                    message = await _read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                await messages_from_client.put(message)

        server = await asyncio.start_unix_server(handle_connection, path=socket_path)
        fake_chat_service = FakeChatService()
        bridge = CodexThreadFollowerBridge(
            chat_service=fake_chat_service,  # type: ignore[arg-type]
            socket_path=socket_path,
        )

        try:
            await bridge.start()
            assert await bridge.client.wait_until_connected(timeout=2.0)
            await asyncio.wait_for(client_ready.wait(), timeout=2.0)
            assert server_writer is not None

            await _send_frame(
                server_writer,
                {
                    "type": "request",
                    "requestId": "set-collab-1",
                    "method": "thread-follower-set-collaboration-mode",
                    "params": {
                        "conversationId": "thread-1",
                        "collaborationMode": {
                            "mode": "default",
                            "settings": {
                                "model": "gpt-5.2-codex",
                                "reasoning_effort": "medium",
                                "developer_instructions": None,
                            },
                        },
                    },
                    "version": 1,
                },
            )

            request_response = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "response"
                and message.get("requestId") == "set-collab-1",
            )
            assert request_response["resultType"] == "success"
            assert request_response["result"] == {"ok": True}
            assert fake_chat_service.updated_backend_states[-1] == (
                "thread-1",
                {
                    "collaboration_mode": {
                        "mode": "default",
                        "settings": {
                            "model": "gpt-5.2-codex",
                            "reasoning_effort": "medium",
                            "developer_instructions": None,
                        },
                    }
                },
            )
        finally:
            await bridge.stop()
            if server_writer is not None:
                server_writer.close()
                await server_writer.wait_closed()
            server.close()
            socket_path.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_codex_thread_follower_bridge_emits_patches_for_assistant_message() -> None:
    async def scenario() -> None:
        socket_path = _test_socket_path()
        messages_from_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        client_ready = asyncio.Event()
        server_writer: asyncio.StreamWriter | None = None

        async def handle_connection(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal server_writer
            initialize_request = await _read_frame(reader)
            server_writer = writer
            await _send_frame(
                writer,
                {
                    "type": "response",
                    "requestId": initialize_request["requestId"],
                    "resultType": "success",
                    "method": "initialize",
                    "handledByClientId": "router-client",
                    "result": {"clientId": "client-yier"},
                },
            )
            client_ready.set()
            while True:
                try:
                    message = await _read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                await messages_from_client.put(message)

        server = await asyncio.start_unix_server(handle_connection, path=socket_path)
        fake_chat_service = FakeChatService()
        fake_chat_service.build_codex_ipc_conversation_state = lambda session_id: {  # type: ignore[method-assign]
            "id": session_id,
            "hostId": "local",
            "turns": [
                {
                    "id": "turn-1",
                    "turnId": "turn-1",
                    "status": "inProgress",
                    "items": [
                        {
                            "type": "userMessage",
                            "id": "item-user-1",
                            "content": [{"type": "text", "text": "hello", "text_elements": []}],
                        },
                        {
                            "type": "agentMessage",
                            "id": "item-agent-1",
                            "text": "world",
                            "phase": "final_answer",
                            "memoryCitation": None,
                        },
                    ],
                    "error": None,
                    "diff": None,
                    "turnStartedAtMs": 100,
                    "finalAssistantStartedAtMs": 120,
                    "params": {
                        "threadId": session_id,
                        "input": [{"type": "text", "text": "hello", "text_elements": []}],
                    },
                }
            ],
            "pendingSteers": [],
            "requests": [],
            "createdAt": 1,
            "updatedAt": 2,
            "title": "Thread 1",
            "source": "chat",
            "latestModel": "gpt-test",
            "latestReasoningEffort": None,
            "previousTurnModel": None,
            "latestCollaborationMode": {
                "mode": "default",
                "settings": {
                    "model": "gpt-test",
                    "reasoning_effort": None,
                    "developer_instructions": None,
                },
            },
            "hasUnreadTurn": True,
            "rolloutPath": "",
            "gitInfo": None,
            "resumeState": "resumed",
            "latestTokenUsageInfo": None,
            "cwd": "/tmp/project",
            "threadId": session_id,
            "threadRuntimeStatus": {
                "type": "idle",
                "activeFlags": [],
            },
        }
        bridge = CodexThreadFollowerBridge(
            chat_service=fake_chat_service,  # type: ignore[arg-type]
            socket_path=socket_path,
        )

        try:
            await bridge.start()
            assert await bridge.client.wait_until_connected(timeout=2.0)
            await asyncio.wait_for(client_ready.wait(), timeout=2.0)
            assert server_writer is not None

            await bridge.notify_stream_event(
                "assistant_message",
                {"session_id": "thread-1", "item_id": "item-agent-1", "content": "world"},
            )

            patch_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("type") == "patches",
            )
            assert patch_broadcast["params"]["type"] == "thread-stream-state-changed"
            assert patch_broadcast["params"]["version"] == 5
            assert patch_broadcast["params"]["change"]["patches"] == [
                {
                    "op": "add",
                    "path": ["turns", 0, "items", 1],
                    "value": {
                        "type": "agentMessage",
                        "id": "item-agent-1",
                        "text": "world",
                        "phase": "final_answer",
                        "memoryCitation": None,
                    },
                }
            ]
        finally:
            await bridge.stop()
            if server_writer is not None:
                server_writer.close()
                await server_writer.wait_closed()
            server.close()
            socket_path.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_codex_thread_follower_bridge_interrupt_turn_returns_ok_without_extra_payload() -> None:
    async def scenario() -> None:
        socket_path = _test_socket_path()
        messages_from_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        client_ready = asyncio.Event()
        server_writer: asyncio.StreamWriter | None = None

        async def handle_connection(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal server_writer
            initialize_request = await _read_frame(reader)
            server_writer = writer
            await _send_frame(
                writer,
                {
                    "type": "response",
                    "requestId": initialize_request["requestId"],
                    "resultType": "success",
                    "method": "initialize",
                    "handledByClientId": "router-client",
                    "result": {"clientId": "client-yier"},
                },
            )
            client_ready.set()
            while True:
                try:
                    message = await _read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                await messages_from_client.put(message)

        server = await asyncio.start_unix_server(handle_connection, path=socket_path)
        fake_chat_service = FakeChatService()
        bridge = CodexThreadFollowerBridge(
            chat_service=fake_chat_service,  # type: ignore[arg-type]
            socket_path=socket_path,
        )

        try:
            await bridge.start()
            assert await bridge.client.wait_until_connected(timeout=2.0)
            await asyncio.wait_for(client_ready.wait(), timeout=2.0)
            assert server_writer is not None

            await _send_frame(
                server_writer,
                {
                    "type": "request",
                    "requestId": "interrupt-request-1",
                    "method": "thread-follower-interrupt-turn",
                    "params": {
                        "conversationId": "thread-1",
                    },
                    "version": 1,
                },
            )

            request_response = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "response"
                and message.get("requestId") == "interrupt-request-1",
            )
            assert request_response["resultType"] == "success"
            assert request_response["result"] == {"ok": True}
            assert fake_chat_service.interrupted_turns == [("thread-1", None)]
        finally:
            await bridge.stop()
            if server_writer is not None:
                server_writer.close()
                await server_writer.wait_closed()
            server.close()
            socket_path.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_codex_thread_follower_bridge_emits_first_assistant_delta_like_codex_app() -> None:
    async def scenario() -> None:
        socket_path = _test_socket_path()
        messages_from_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        client_ready = asyncio.Event()
        server_writer: asyncio.StreamWriter | None = None

        async def handle_connection(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal server_writer
            initialize_request = await _read_frame(reader)
            server_writer = writer
            await _send_frame(
                writer,
                {
                    "type": "response",
                    "requestId": initialize_request["requestId"],
                    "resultType": "success",
                    "method": "initialize",
                    "handledByClientId": "router-client",
                    "result": {"clientId": "client-yier"},
                },
            )
            client_ready.set()
            while True:
                try:
                    message = await _read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                await messages_from_client.put(message)

        server = await asyncio.start_unix_server(handle_connection, path=socket_path)
        fake_chat_service = FakeChatService()
        fake_chat_service.build_codex_ipc_conversation_state = lambda session_id: {  # type: ignore[method-assign]
            "id": session_id,
            "hostId": "local",
            "turns": [
                {
                    "id": "turn-1",
                    "turnId": "turn-1",
                    "status": "inProgress",
                    "items": [
                        {
                            "type": "userMessage",
                            "id": "item-user-1",
                            "content": [{"type": "text", "text": "hello", "text_elements": []}],
                        },
                        {
                            "type": "agentMessage",
                            "id": "item-agent-1",
                            "text": "world",
                            "phase": "final_answer",
                            "memoryCitation": None,
                        },
                    ],
                    "error": None,
                    "diff": None,
                    "turnStartedAtMs": 100,
                    "finalAssistantStartedAtMs": 120,
                    "params": {
                        "threadId": session_id,
                        "input": [{"type": "text", "text": "hello", "text_elements": []}],
                    },
                }
            ],
            "pendingSteers": [],
            "requests": [],
            "createdAt": 1,
            "updatedAt": 2,
            "title": "Thread 1",
            "source": "chat",
            "latestModel": "gpt-test",
            "latestReasoningEffort": None,
            "previousTurnModel": None,
            "latestCollaborationMode": {
                "mode": "default",
                "settings": {
                    "model": "gpt-test",
                    "reasoning_effort": None,
                    "developer_instructions": None,
                },
            },
            "hasUnreadTurn": False,
            "rolloutPath": "",
            "gitInfo": None,
            "resumeState": "resumed",
            "latestTokenUsageInfo": None,
            "cwd": "/tmp/project",
            "threadId": session_id,
            "threadRuntimeStatus": {
                "type": "active",
                "activeFlags": [],
            },
        }
        bridge = CodexThreadFollowerBridge(
            chat_service=fake_chat_service,  # type: ignore[arg-type]
            socket_path=socket_path,
        )

        try:
            await bridge.start()
            assert await bridge.client.wait_until_connected(timeout=2.0)
            await asyncio.wait_for(client_ready.wait(), timeout=2.0)
            assert server_writer is not None

            await bridge.notify_stream_event(
                "assistant_delta",
                {"session_id": "thread-1", "item_id": "item-agent-1", "delta": "world"},
            )

            assistant_start_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "add",
                        "path": ["turns", 0, "items", 1],
                        "value": {
                            "type": "agentMessage",
                            "id": "item-agent-1",
                            "text": "",
                            "phase": "final_answer",
                            "memoryCitation": None,
                        },
                    },
                    {
                        "op": "replace",
                        "path": ["turns", 0, "finalAssistantStartedAtMs"],
                        "value": 120,
                    },
                ],
            )
            assert assistant_start_broadcast["params"]["type"] == "thread-stream-state-changed"
            assert assistant_start_broadcast["params"]["version"] == 5

            text_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "replace",
                        "path": ["turns", 0, "items", 1, "text"],
                        "value": "world",
                    }
                ],
            )
            assert text_broadcast["params"]["conversationId"] == "thread-1"
        finally:
            await bridge.stop()
            if server_writer is not None:
                server_writer.close()
                await server_writer.wait_closed()
            server.close()
            socket_path.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_codex_thread_follower_bridge_emits_completion_sequence_before_done_patch() -> None:
    async def scenario() -> None:
        socket_path = _test_socket_path()
        messages_from_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        client_ready = asyncio.Event()
        server_writer: asyncio.StreamWriter | None = None

        async def handle_connection(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal server_writer
            initialize_request = await _read_frame(reader)
            server_writer = writer
            await _send_frame(
                writer,
                {
                    "type": "response",
                    "requestId": initialize_request["requestId"],
                    "resultType": "success",
                    "method": "initialize",
                    "handledByClientId": "router-client",
                    "result": {"clientId": "client-yier"},
                },
            )
            client_ready.set()
            while True:
                try:
                    message = await _read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                await messages_from_client.put(message)

        server = await asyncio.start_unix_server(handle_connection, path=socket_path)
        fake_chat_service = FakeChatService()
        fake_chat_service.build_codex_ipc_conversation_state = lambda session_id: {  # type: ignore[method-assign]
            "id": session_id,
            "hostId": "local",
            "turns": [
                {
                    "id": "turn-0",
                    "turnId": "turn-0",
                    "status": "completed",
                    "items": [
                        {
                            "type": "userMessage",
                            "id": "item-user-0",
                            "content": [{"type": "text", "text": "old", "text_elements": []}],
                        },
                        {
                            "type": "agentMessage",
                            "id": "item-agent-0",
                            "text": "done",
                            "phase": "final_answer",
                        },
                    ],
                    "error": None,
                    "diff": None,
                    "turnStartedAtMs": 1,
                    "finalAssistantStartedAtMs": 2,
                    "params": {
                        "threadId": session_id,
                        "input": [{"type": "text", "text": "old", "text_elements": []}],
                    },
                },
                {
                    "id": "turn-1",
                    "turnId": "turn-1",
                    "status": "inProgress",
                    "items": [
                        {
                            "type": "userMessage",
                            "id": "item-user-1",
                            "content": [{"type": "text", "text": "hello", "text_elements": []}],
                        },
                        {
                            "type": "agentMessage",
                            "id": "item-agent-1",
                            "text": "world",
                            "phase": "final_answer",
                            "memoryCitation": None,
                        },
                    ],
                    "error": None,
                    "diff": None,
                    "turnStartedAtMs": 100,
                    "finalAssistantStartedAtMs": 120,
                    "params": {
                        "threadId": session_id,
                        "input": [{"type": "text", "text": "hello", "text_elements": []}],
                    },
                },
            ],
            "pendingSteers": [],
            "requests": [],
            "createdAt": 1,
            "updatedAt": 2,
            "title": "Thread 1",
            "source": "chat",
            "latestModel": "gpt-test",
            "latestReasoningEffort": None,
            "previousTurnModel": None,
            "latestCollaborationMode": {
                "mode": "default",
                "settings": {
                    "model": "gpt-test",
                    "reasoning_effort": None,
                    "developer_instructions": None,
                },
            },
            "hasUnreadTurn": False,
            "rolloutPath": "",
            "gitInfo": None,
            "resumeState": "resumed",
            "latestTokenUsageInfo": {"total": {"totalTokens": 10}},
            "cwd": "/tmp/project",
            "threadId": session_id,
            "threadRuntimeStatus": {
                "type": "active",
                "activeFlags": [],
            },
        }
        bridge = CodexThreadFollowerBridge(
            chat_service=fake_chat_service,  # type: ignore[arg-type]
            socket_path=socket_path,
        )

        try:
            await bridge.start()
            assert await bridge.client.wait_until_connected(timeout=2.0)
            await asyncio.wait_for(client_ready.wait(), timeout=2.0)
            assert server_writer is not None

            await bridge.notify_stream_event(
                "run_started",
                {"session_id": "thread-1", "turn_id": "turn-1"},
            )
            await bridge.notify_stream_event(
                "assistant_message",
                {"session_id": "thread-1", "item_id": "item-agent-1", "content": "world"},
            )
            await bridge.notify_stream_event(
                "done",
                {"session_id": "thread-1", "finish_reason": "stop"},
            )

            completed_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "replace",
                        "path": ["turns", 1, "status"],
                        "value": "completed",
                    }
                ],
            )
            assert completed_broadcast["params"]["conversationId"] == "thread-1"

            unread_true_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "replace",
                        "path": ["hasUnreadTurn"],
                        "value": True,
                    }
                ],
            )
            assert unread_true_broadcast["params"]["conversationId"] == "thread-1"

            snapshot_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("type") == "snapshot"
                and message.get("params", {}).get("change", {}).get("conversationState", {}).get("_yier_trigger_event")
                == "turn_completed",
            )
            snapshot_state = snapshot_broadcast["params"]["change"]["conversationState"]
            assert snapshot_state["turns"][1]["status"] == "completed"
            assert snapshot_state["hasUnreadTurn"] is True
            assert snapshot_state["threadRuntimeStatus"] == {
                "type": "idle",
                "activeFlags": [],
            }

            done_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "replace",
                        "path": ["hasUnreadTurn"],
                        "value": False,
                    }
                ],
            )
            assert done_broadcast["params"]["conversationId"] == "thread-1"
        finally:
            await bridge.stop()
            if server_writer is not None:
                server_writer.close()
                await server_writer.wait_closed()
            server.close()
            socket_path.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_codex_thread_follower_bridge_emits_latest_token_usage_patch() -> None:
    async def scenario() -> None:
        socket_path = _test_socket_path()
        messages_from_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        client_ready = asyncio.Event()
        server_writer: asyncio.StreamWriter | None = None

        async def handle_connection(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal server_writer
            initialize_request = await _read_frame(reader)
            server_writer = writer
            await _send_frame(
                writer,
                {
                    "type": "response",
                    "requestId": initialize_request["requestId"],
                    "resultType": "success",
                    "method": "initialize",
                    "handledByClientId": "router-client",
                    "result": {"clientId": "client-yier"},
                },
            )
            client_ready.set()
            while True:
                try:
                    message = await _read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                await messages_from_client.put(message)

        server = await asyncio.start_unix_server(handle_connection, path=socket_path)
        fake_chat_service = FakeChatService()
        fake_chat_service.build_codex_ipc_conversation_state = lambda session_id: {  # type: ignore[method-assign]
            **FakeChatService().build_codex_ipc_conversation_state(session_id),
            "latestTokenUsageInfo": {
                "total": {
                    "totalTokens": 42,
                    "inputTokens": 40,
                    "outputTokens": 2,
                    "cachedInputTokens": 0,
                    "reasoningOutputTokens": 0,
                }
            },
        }
        bridge = CodexThreadFollowerBridge(
            chat_service=fake_chat_service,  # type: ignore[arg-type]
            socket_path=socket_path,
        )

        try:
            await bridge.start()
            assert await bridge.client.wait_until_connected(timeout=2.0)
            await asyncio.wait_for(client_ready.wait(), timeout=2.0)
            assert server_writer is not None

            await bridge.notify_stream_event(
                "token_usage_updated",
                {"session_id": "thread-1"},
            )

            token_broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("patches")
                == [
                    {
                        "op": "replace",
                        "path": ["latestTokenUsageInfo"],
                        "value": {
                            "total": {
                                "totalTokens": 42,
                                "inputTokens": 40,
                                "outputTokens": 2,
                                "cachedInputTokens": 0,
                                "reasoningOutputTokens": 0,
                            }
                        },
                    }
                ],
            )
            assert token_broadcast["params"]["conversationId"] == "thread-1"
        finally:
            await bridge.stop()
            if server_writer is not None:
                server_writer.close()
                await server_writer.wait_closed()
            server.close()
            socket_path.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_codex_thread_follower_bridge_maps_approval_request_without_request_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        socket_path = _test_socket_path()
        messages_from_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        client_ready = asyncio.Event()
        server_writer: asyncio.StreamWriter | None = None

        async def handle_connection(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal server_writer
            initialize_request = await _read_frame(reader)
            server_writer = writer
            await _send_frame(
                writer,
                {
                    "type": "response",
                    "requestId": initialize_request["requestId"],
                    "resultType": "success",
                    "method": "initialize",
                    "handledByClientId": "router-client",
                    "result": {"clientId": "client-yier"},
                },
            )
            client_ready.set()
            while True:
                try:
                    message = await _read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                await messages_from_client.put(message)

        server = await asyncio.start_unix_server(handle_connection, path=socket_path)
        fake_chat_service = FakeChatService()
        bridge = CodexThreadFollowerBridge(
            chat_service=fake_chat_service,  # type: ignore[arg-type]
            socket_path=socket_path,
        )

        try:
            await bridge.start()
            assert await bridge.client.wait_until_connected(timeout=2.0)
            await asyncio.wait_for(client_ready.wait(), timeout=2.0)
            assert server_writer is not None

            await _send_frame(
                server_writer,
                {
                    "type": "request",
                    "requestId": "approval-request-1",
                    "method": "thread-follower-command-approval-decision",
                    "params": {
                        "conversationId": "thread-1",
                        "decision": "approve",
                        "content": {"note": "ship it"},
                    },
                    "version": 1,
                },
            )
            broadcast = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "broadcast"
                and message.get("method") == "thread-stream-state-changed"
                and message.get("params", {}).get("change", {}).get("type") == "snapshot"
                and message.get("params", {})
                .get("change", {})
                .get("conversationState", {})
                .get("_yier_trigger_event")
                == "approval-response",
            )
            assert broadcast["params"]["conversationId"] == "thread-1"

            request_response = await _wait_for_message(
                messages_from_client,
                predicate=lambda message: message.get("type") == "response"
                and message.get("requestId") == "approval-request-1",
            )
            assert request_response["type"] == "response"
            assert request_response["resultType"] == "success"
            assert request_response["result"]["ok"] is True
            assert fake_chat_service.responded_raw_requests == [
                ("thread-1", "approval-1", {"decision": "accept"})
            ]
        finally:
            await bridge.stop()
            if server_writer is not None:
                server_writer.close()
                await server_writer.wait_closed()
            server.close()
            socket_path.unlink(missing_ok=True)

    asyncio.run(scenario())
