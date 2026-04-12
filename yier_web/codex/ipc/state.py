from __future__ import annotations

from copy import deepcopy
from time import time
from typing import TYPE_CHECKING, Any

from yier_web.codex.backend import CodexAppServerBackend

if TYPE_CHECKING:
    from yier_web.chat import ChatService


class CodexConversationStateService:
    def __init__(self, chat_service: "ChatService") -> None:
        self.chat_service = chat_service

    async def build_conversation_state(self, session_id: str) -> dict[str, Any]:
        chat_service = self.chat_service
        metadata = chat_service.get_session_metadata(session_id)
        context = chat_service.get_session_context(session_id)
        backend = chat_service.backends.get(context.backend_id)
        runtime = chat_service.get_backend_runtime(session_id)
        backend_state = metadata["backend_state"]
        current_timestamp_ms = int(time() * 1000)
        native_state = await self._native_conversation_state(session_id)
        latest_model = backend_state.get("model") or ""
        latest_reasoning_effort = backend_state.get("reasoning_effort")

        if native_state is not None:
            backend = native_state["backend"]
            thread = native_state["thread"]
            turns = backend.build_ipc_turns(
                context,
                [turn for turn in thread.get("turns", []) if isinstance(turn, dict)],
            )
            created_at_ms = (
                int(thread.get("createdAt", 0) or 0) * 1000 or current_timestamp_ms
            )
            updated_at_ms = (
                int(thread.get("updatedAt", 0) or 0) * 1000 or current_timestamp_ms
            )
            title = thread.get("name") or metadata.get("title")
            source = thread.get("source") or metadata.get("source", "chat")
            cwd = thread.get("cwd") or metadata.get("project_path")
            git_info = thread.get("gitInfo", backend_state.get("git_info"))
            thread_runtime_status = native_state.get("threadRuntimeStatus") or {
                "type": runtime.status,
                "activeFlags": list(runtime.active_flags),
            }
            extra_state = {
                "preview": thread.get("preview"),
                "cliVersion": thread.get("cliVersion"),
                "ephemeral": thread.get("ephemeral"),
                "modelProvider": thread.get("modelProvider"),
                "path": thread.get("path"),
                "agentNickname": thread.get("agentNickname"),
                "agentRole": thread.get("agentRole"),
            }
        else:
            turns = self._build_fallback_turns(session_id, runtime.status)
            if isinstance(backend, CodexAppServerBackend):
                turns = backend.build_ipc_turns(context, turns)
            updated_at = metadata.get("updated_at")
            created_at_ms = (
                int(updated_at * 1000)
                if isinstance(updated_at, (int, float))
                else current_timestamp_ms
            )
            updated_at_ms = created_at_ms
            title = metadata.get("title")
            source = metadata.get("source", "chat")
            cwd = metadata.get("project_path")
            git_info = backend_state.get("git_info")
            thread_runtime_status = {
                "type": runtime.status,
                "activeFlags": list(runtime.active_flags),
            }
            extra_state = {}

        pending_requests = self._requests(session_id)
        latest_collaboration_mode = self._collaboration_mode(
            backend_state.get("collaboration_mode"),
            latest_model=latest_model,
            latest_reasoning_effort=latest_reasoning_effort,
        )
        thread_runtime_status = self._thread_runtime_status(
            thread_runtime_status,
            fallback_type=runtime.status,
            fallback_active_flags=list(runtime.active_flags),
            pending_requests=pending_requests,
            turns=turns,
        )

        conversation_state = {
            "id": session_id,
            "hostId": "local",
            "turns": turns,
            "pendingSteers": [],
            "requests": pending_requests,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
            "title": title,
            "source": source,
            "latestModel": latest_model,
            "latestReasoningEffort": latest_reasoning_effort,
            "previousTurnModel": None,
            "latestCollaborationMode": latest_collaboration_mode,
            "hasUnreadTurn": bool(backend_state.get("has_unread_turn")),
            "rolloutPath": backend_state.get("rollout_path") or "",
            "gitInfo": git_info,
            "resumeState": backend_state.get("resume_state") or "resumed",
            "latestTokenUsageInfo": backend_state.get("latest_token_usage_info"),
            "workspaceKind": backend_state.get("workspace_kind") or "project",
            "cwd": cwd,
            "threadId": runtime.thread_id or session_id,
            "threadRuntimeStatus": thread_runtime_status,
        }
        for key, value in extra_state.items():
            if value is not None:
                conversation_state[key] = value
        return conversation_state

    def apply_stream_change(
        self,
        session_id: str,
        change: dict[str, Any],
    ) -> None:
        metadata = self.chat_service.get_session_metadata(
            session_id,
            include_conversation_state=True,
        )
        if metadata["backend_id"] != "codex":
            return

        change_type = change.get("type")
        next_conversation_state: dict[str, Any] | None = None
        if change_type == "snapshot":
            conversation_state = change.get("conversationState")
            if isinstance(conversation_state, dict):
                next_conversation_state = deepcopy(conversation_state)
        elif change_type == "patches":
            patches = change.get("patches")
            if isinstance(patches, list):
                cached_state = metadata["backend_state"].get("ipc_conversation_state")
                base_state = (
                    deepcopy(cached_state) if isinstance(cached_state, dict) else {}
                )
                if self._apply_patches(base_state, patches):
                    next_conversation_state = base_state

        if next_conversation_state is None:
            return

        backend_updates: dict[str, Any] = {
            "ipc_conversation_state": next_conversation_state,
        }
        if "hasUnreadTurn" in next_conversation_state:
            backend_updates["has_unread_turn"] = bool(
                next_conversation_state.get("hasUnreadTurn")
            )
        if "latestTokenUsageInfo" in next_conversation_state:
            backend_updates["latest_token_usage_info"] = next_conversation_state.get(
                "latestTokenUsageInfo"
            )
        if "resumeState" in next_conversation_state:
            backend_updates["resume_state"] = (
                next_conversation_state.get("resumeState") or "resumed"
            )
        self.chat_service.update_session_backend_state(session_id, backend_updates)

    def build_queued_followups(self, session_id: str) -> list[dict[str, Any]]:
        metadata = self.chat_service.get_session_metadata(session_id)
        workspace_root = metadata.get("project_path")
        workspace_roots = (
            [workspace_root]
            if isinstance(workspace_root, str) and workspace_root
            else []
        )
        created_at_ms = int(time() * 1000)
        messages: list[dict[str, Any]] = []
        for item in self.chat_service.followup_queue.list_items():
            if item.owner_session_id != session_id:
                continue
            messages.append(
                {
                    "id": item.queue_id,
                    "text": item.prompt,
                    "context": {"workspaceRoots": workspace_roots},
                    "cwd": workspace_root or "/",
                    "createdAt": created_at_ms,
                }
            )
        return messages

    def _apply_patches(
        self,
        root: dict[str, Any],
        patches: list[dict[str, Any]],
    ) -> bool:
        try:
            for patch in patches:
                if not isinstance(patch, dict):
                    continue
                operation = patch.get("op")
                path = patch.get("path")
                if (
                    not isinstance(operation, str)
                    or not isinstance(path, list)
                    or not path
                ):
                    continue
                if operation in {"add", "replace"}:
                    self._set_path(root, path, deepcopy(patch.get("value")))
                elif operation == "remove":
                    self._remove_path(root, path)
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        return True

    def _set_path(
        self,
        root: dict[str, Any],
        path: list[Any],
        value: Any,
    ) -> None:
        current: Any = root
        for index, segment in enumerate(path[:-1]):
            next_segment = path[index + 1]
            if isinstance(current, list):
                if not isinstance(segment, int):
                    raise TypeError("List path segment must be an integer.")
                while len(current) <= segment:
                    current.append({} if not isinstance(next_segment, int) else [])
                child = current[segment]
                if not isinstance(child, (dict, list)):
                    child = {} if not isinstance(next_segment, int) else []
                    current[segment] = child
                current = child
                continue

            if not isinstance(current, dict):
                raise TypeError("Patch parent must be a dict or list.")
            child = current.get(segment)
            if not isinstance(child, (dict, list)):
                child = {} if not isinstance(next_segment, int) else []
                current[segment] = child
            current = child

        last_segment = path[-1]
        if isinstance(current, list):
            if not isinstance(last_segment, int):
                raise TypeError("List path segment must be an integer.")
            if last_segment < len(current):
                current[last_segment] = value
            elif last_segment == len(current):
                current.append(value)
            else:
                while len(current) < last_segment:
                    current.append(None)
                current.append(value)
            return

        if not isinstance(current, dict):
            raise TypeError("Patch parent must be a dict or list.")
        current[last_segment] = value

    def _remove_path(
        self,
        root: dict[str, Any],
        path: list[Any],
    ) -> None:
        current: Any = root
        for segment in path[:-1]:
            if isinstance(current, list):
                if not isinstance(segment, int):
                    raise TypeError("List path segment must be an integer.")
                current = current[segment]
                continue
            if not isinstance(current, dict):
                raise TypeError("Patch parent must be a dict or list.")
            current = current[segment]

        last_segment = path[-1]
        if isinstance(current, list):
            if not isinstance(last_segment, int):
                raise TypeError("List path segment must be an integer.")
            del current[last_segment]
            return
        if not isinstance(current, dict):
            raise TypeError("Patch parent must be a dict or list.")
        current.pop(last_segment, None)

    async def _native_conversation_state(
        self, session_id: str
    ) -> dict[str, Any] | None:
        context = self.chat_service.get_session_context(session_id)
        if context.backend_id != "codex":
            return None
        backend = self.chat_service.backends.get(context.backend_id)
        if not isinstance(backend, CodexAppServerBackend):
            return None
        thread_id = context.backend_state.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        try:
            native_state = await backend.load_thread_state(context)
        except Exception:
            return None
        thread = native_state.get("thread")
        if not isinstance(thread, dict):
            return None
        return {
            **native_state,
            "backend": backend,
        }

    def _requests(self, session_id: str) -> list[dict[str, Any]]:
        context = self.chat_service.get_session_context(session_id)
        backend = self.chat_service.backends.get(context.backend_id)
        if isinstance(backend, CodexAppServerBackend):
            raw_requests = backend.pending_conversation_requests(context)
            if raw_requests:
                return raw_requests
        return [
            approval.model_dump(mode="json")
            for approval in self.chat_service.get_pending_approvals(session_id)
        ]

    def _collaboration_mode(
        self,
        value: Any,
        *,
        latest_model: str,
        latest_reasoning_effort: Any,
    ) -> dict[str, Any]:
        default_settings = {
            "model": latest_model,
            "reasoning_effort": (
                latest_reasoning_effort
                if latest_reasoning_effort is None
                else str(latest_reasoning_effort)
            ),
            "developer_instructions": None,
        }
        default_mode = {
            "mode": "default",
            "settings": default_settings,
        }
        if isinstance(value, dict):
            mode = value.get("mode")
            settings = value.get("settings")
            if isinstance(mode, str) and mode.strip():
                normalized = dict(value)
                normalized["mode"] = mode.strip()
                merged_settings = dict(default_settings)
                if isinstance(settings, dict):
                    model = settings.get("model")
                    if isinstance(model, str):
                        merged_settings["model"] = model
                    reasoning_effort = settings.get("reasoning_effort")
                    if reasoning_effort is None or isinstance(reasoning_effort, str):
                        merged_settings["reasoning_effort"] = reasoning_effort
                    developer_instructions = settings.get("developer_instructions")
                    if developer_instructions is None or isinstance(
                        developer_instructions, str
                    ):
                        merged_settings["developer_instructions"] = (
                            developer_instructions
                        )
                normalized["settings"] = merged_settings
                return normalized
        if isinstance(value, str) and value.strip():
            return {
                "mode": value.strip(),
                "settings": default_settings,
            }
        return default_mode

    def _thread_runtime_status(
        self,
        value: Any,
        *,
        fallback_type: str,
        fallback_active_flags: list[str],
        pending_requests: list[dict[str, Any]],
        turns: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        root = value.get("root") if isinstance(value, dict) else None
        payload = root if isinstance(root, dict) else value
        status_type = fallback_type
        active_flags = list(fallback_active_flags)
        if isinstance(payload, dict):
            raw_type = payload.get("type")
            if isinstance(raw_type, str) and raw_type:
                status_type = raw_type
            raw_active_flags = payload.get("activeFlags")
            if not isinstance(raw_active_flags, list):
                raw_active_flags = payload.get("active_flags")
            if isinstance(raw_active_flags, list):
                active_flags = [
                    str(
                        flag.get("value")
                        if isinstance(flag, dict) and "value" in flag
                        else flag
                    )
                    for flag in raw_active_flags
                ]
        elif isinstance(payload, str) and payload:
            status_type = payload

        if fallback_type == "active" and status_type in {"", "idle"}:
            status_type = "active"

        if isinstance(turns, list) and any(
            isinstance(turn, dict) and turn.get("status") == "inProgress"
            for turn in turns
        ):
            status_type = "active"

        if self._has_waiting_on_approval_request(pending_requests):
            if status_type in {"", "idle"}:
                status_type = "active"
            if "waitingOnApproval" not in active_flags:
                active_flags.append("waitingOnApproval")

        return {
            "type": status_type or fallback_type,
            "activeFlags": active_flags,
        }

    def _has_waiting_on_approval_request(
        self,
        pending_requests: list[dict[str, Any]],
    ) -> bool:
        waiting_methods = {
            "item/fileChange/requestApproval",
            "item/commandExecution/requestApproval",
            "item/permissions/requestApproval",
            "mcpServer/elicitation/request",
            "elicitation/create",
        }
        for request in pending_requests:
            if not isinstance(request, dict):
                continue
            method = request.get("method")
            if isinstance(method, str) and method in waiting_methods:
                return True
        return False

    def _build_fallback_turns(
        self,
        session_id: str,
        runtime_status: str,
    ) -> list[dict[str, Any]]:
        transcript = (
            self.chat_service.transcript_store.get_session_messages(session_id) or []
        )
        turns: list[dict[str, Any]] = []
        current_turn: dict[str, Any] | None = None
        turn_index = 0
        current_timestamp_ms = int(time() * 1000)

        for message in transcript:
            content = getattr(message, "content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            role = getattr(message, "role", "")
            if role == "user":
                if current_turn is not None:
                    turns.append(current_turn)
                turn_index += 1
                current_turn = {
                    "id": f"{session_id}:turn:{turn_index}",
                    "turnId": f"{session_id}:turn:{turn_index}",
                    "status": "completed",
                    "error": None,
                    "diff": None,
                    "items": [
                        {
                            "id": f"{session_id}:turn:{turn_index}:user",
                            "type": "userMessage",
                            "content": [
                                {"type": "text", "text": content, "text_elements": []}
                            ],
                        }
                    ],
                    "turnStartedAtMs": current_timestamp_ms,
                    "finalAssistantStartedAtMs": None,
                }
                continue
            if role != "assistant":
                continue
            if current_turn is None:
                turn_index += 1
                current_turn = {
                    "id": f"{session_id}:turn:{turn_index}",
                    "turnId": f"{session_id}:turn:{turn_index}",
                    "status": "completed",
                    "error": None,
                    "diff": None,
                    "items": [],
                    "turnStartedAtMs": current_timestamp_ms,
                    "finalAssistantStartedAtMs": current_timestamp_ms,
                }
            current_turn["items"].append(
                {
                    "id": f"{session_id}:turn:{turn_index}:assistant:{len(current_turn['items'])}",
                    "type": "agentMessage",
                    "text": content,
                    "phase": "final_answer",
                    "memoryCitation": None,
                }
            )
            if current_turn.get("finalAssistantStartedAtMs") is None:
                current_turn["finalAssistantStartedAtMs"] = current_timestamp_ms

        if current_turn is not None:
            if runtime_status == "active":
                current_turn["status"] = "inProgress"
            turns.append(current_turn)
        return turns
