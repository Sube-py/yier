"""Parameter extraction helpers for thread-follower requests.

Pure functions that dig values out of the ``params`` dict sent by
Codex VSCode.  No dependency on ChatService or the bridge class.
"""

from __future__ import annotations

from typing import Any

from yier_web.codex.collaboration_mode import normalize_protocol_collaboration_mode


def conversation_id(params: dict[str, Any]) -> str:
    """Extract the conversation/thread ID from params."""
    for key in ("conversationId", "threadId"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def params(request: dict[str, Any]) -> dict[str, Any]:
    """Extract params dict from a request message."""
    p = request.get("params")
    return p if isinstance(p, dict) else {}


def turn_start_params(params: dict[str, Any]) -> dict[str, Any]:
    """Extract turnStartParams, falling back to the full params."""
    tsp = params.get("turnStartParams")
    if isinstance(tsp, dict):
        return tsp
    return params


def prompt_text(params: dict[str, Any]) -> str:
    """Extract the user prompt text from params."""
    for key in ("prompt", "message", "text"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = params.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, dict):
        text_value = content.get("text")
        if isinstance(text_value, str) and text_value.strip():
            return text_value.strip()

    for key in ("input", "inputItems", "items"):
        value = params.get(key)
        if isinstance(value, list):
            parts = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                text_value = item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    parts.append(text_value.strip())
            if parts:
                return "\n".join(parts)
        if isinstance(value, dict):
            text_value = value.get("text")
            if isinstance(text_value, str) and text_value.strip():
                return text_value.strip()
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def message_text(params: dict[str, Any]) -> str:
    """Extract message text (checks ``message`` key first, then prompt)."""
    message = params.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return prompt_text(params)


def steer_input_payload(
    params: dict[str, Any],
) -> list[dict[str, Any]] | dict[str, Any] | str | None:
    """Extract the steer input payload (inputItems/items/input/text)."""
    for key in ("input", "inputItems", "items"):
        value = params.get(key)
        if isinstance(value, (list, dict)):
            return value
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = prompt_text(params)
    if text:
        return text
    return None


def start_turn_input_payload(
    params: dict[str, Any],
) -> list[dict[str, Any]] | dict[str, Any] | str | None:
    """Extract the start-turn input (broader than steer: also checks prompt)."""
    input_payload = steer_input_payload(params)
    if input_payload not in (None, "", []):
        return input_payload
    prompt = prompt_text(params)
    if prompt:
        return prompt
    return None


def turn_id(params: dict[str, Any]) -> str:
    """Extract the turn ID from params."""
    for key in ("expectedTurnId", "turnId", "turn_id"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def approval_request_id(params: dict[str, Any]) -> str | None:
    """Extract the approval/elicitation request ID from params."""
    for key in (
        "requestId",
        "request_id",
        "approvalRequestId",
        "approval_request_id",
        "elicitationRequestId",
        "elicitation_request_id",
    ):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def approval_decision(params: dict[str, Any]) -> str:
    """Normalize the approval decision string to a canonical value."""
    raw_decision = None
    for key in ("decision", "action", "response"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            raw_decision = value.strip().lower()
            break
    if raw_decision in {"accept", "approve", "approved", "allow"}:
        return "accept"
    if raw_decision in {
        "accept_for_session",
        "approve_for_session",
        "allow_for_session",
    }:
        return "accept_for_session"
    if raw_decision in {"decline", "deny", "reject", "rejected"}:
        return "decline"
    if raw_decision == "cancel":
        return "cancel"
    return "accept"


def collaboration_mode_value(
    params: dict[str, Any],
) -> dict[str, Any] | str | None:
    """Extract the collaboration mode from params."""
    for key in ("collaborationMode", "collaboration_mode"):
        value = params.get(key)
        if isinstance(value, dict):
            return normalize_protocol_collaboration_mode(value)
        if isinstance(value, str) and value.strip():
            return normalize_protocol_collaboration_mode(value)
    return None


def command_or_file_approval_response_payload(
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """Build the response payload for command/file approval decisions."""
    decision = approval_decision(params)
    if decision == "accept_for_session":
        return {"decision": "acceptForSession"}
    if decision in {"accept", "decline", "cancel"}:
        return {"decision": decision}
    return {"decision": "accept"}


def response_payload(params: dict[str, Any]) -> dict[str, Any] | None:
    """Build the response payload for submit-user-input / mcp-elicitation."""
    response = params.get("response")
    if isinstance(response, dict):
        return response
    content = approval_content(params)
    if content is not None:
        return content
    return None


def approval_content(params: dict[str, Any]) -> dict[str, Any] | None:
    """Extract approval content from params."""
    for key in ("content", "payload", "response"):
        value = params.get(key)
        if isinstance(value, dict):
            return value
    text = prompt_text(params)
    if text:
        return {"text": text}
    return None


def model_and_reasoning_updates(params: dict[str, Any]) -> dict[str, Any]:
    """Extract model/reasoning updates from params (camelCase -> snake_case)."""
    updates: dict[str, Any] = {}
    for source_key, target_key in (
        ("model", "model"),
        ("reasoningEffort", "reasoning_effort"),
        ("reasoning_effort", "reasoning_effort"),
        ("serviceTier", "service_tier"),
        ("service_tier", "service_tier"),
        ("effort", "reasoning_effort"),
    ):
        value = params.get(source_key)
        if isinstance(value, str) and value.strip():
            updates[target_key] = value.strip()
    return updates
