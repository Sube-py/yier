from __future__ import annotations

from typing import Any


def protocol_collaboration_mode_name(value: Any) -> str:
    """Map internal/custom mode names onto Codex protocol mode names."""
    if isinstance(value, str) and value.strip().lower() == "plan":
        return "plan"
    return "default"


def codex_work_mode_from_collaboration_mode(value: Any) -> str:
    """Map Codex protocol collaboration mode names back to UI work modes."""
    if protocol_collaboration_mode_name(
        value.get("mode") if isinstance(value, dict) else value
    ) == "plan":
        return "plan"
    return "build"


def normalize_protocol_collaboration_mode(
    value: Any,
    *,
    default_model: str = "",
    default_reasoning_effort: Any = None,
) -> dict[str, Any]:
    """Normalize legacy/raw collaboration mode values for Codex IPC payloads."""
    default_settings = {
        "model": default_model,
        "reasoning_effort": (
            default_reasoning_effort
            if default_reasoning_effort is None
            else str(default_reasoning_effort)
        ),
        "developer_instructions": None,
    }
    default_mode = {
        "mode": "default",
        "settings": default_settings,
    }
    if isinstance(value, dict):
        normalized = dict(value)
        normalized["mode"] = protocol_collaboration_mode_name(value.get("mode"))
        settings = value.get("settings")
        merged_settings = dict(default_settings)
        if isinstance(settings, dict):
            model = settings.get("model")
            if isinstance(model, str) and model.strip():
                merged_settings["model"] = model.strip()
            reasoning_effort = settings.get("reasoning_effort")
            if reasoning_effort is None:
                merged_settings["reasoning_effort"] = reasoning_effort
            elif isinstance(reasoning_effort, str) and reasoning_effort.strip():
                merged_settings["reasoning_effort"] = reasoning_effort.strip()
            developer_instructions = settings.get("developer_instructions")
            if developer_instructions is None or isinstance(
                developer_instructions, str
            ):
                merged_settings["developer_instructions"] = developer_instructions
        normalized["settings"] = merged_settings
        return normalized
    if isinstance(value, str) and value.strip():
        return {
            "mode": protocol_collaboration_mode_name(value),
            "settings": default_settings,
        }
    return default_mode


def protocol_collaboration_mode_for_work_mode(
    work_mode: Any,
    *,
    current_value: Any = None,
    default_model: str = "",
    default_reasoning_effort: Any = None,
) -> dict[str, Any]:
    """Build a Codex protocol collaboration mode from a stored UI work mode."""
    normalized = normalize_protocol_collaboration_mode(
        current_value,
        default_model=default_model,
        default_reasoning_effort=default_reasoning_effort,
    )
    normalized["mode"] = protocol_collaboration_mode_name(work_mode)
    return normalized
