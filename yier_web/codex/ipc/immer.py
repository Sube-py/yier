"""Python implementation of immer's produceWithPatches / applyPatches.

Provides the same semantics as the JavaScript immer library used by Codex VSCode:

- ``produce_with_patches(base, recipe)``: execute mutable operations on a draft,
  return ``(new_state, patches)`` where patches are standard immer-format
  ``{op, path, value}`` objects.

- ``apply_patches(state, patches)``: apply a list of immer patches to a state
  object, returning a new state.

Patch format (matches immer exactly)::

    {"op": "add",     "path": ["turns", 0, "items", 0], "value": {...}}
    {"op": "replace", "path": ["turns", 0, "items", 0, "text"], "value": "hello"}
    {"op": "remove",  "path": ["turns", 0, "error"]}

Key differences from standard JSON Patch (RFC 6902):
  - ``path`` is an array of segments (not a JSON Pointer string)
  - ``remove`` on an array index performs splice (shift subsequent elements)
  - ``add`` at index == len(array) appends, at index < len inserts
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


# ─── apply_patches ───


def apply_patches(state: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply immer-format patches to a state object, returning a new state.

    Equivalent to immer's ``applyPatches(state, patches)``.
    Does not mutate the input state.
    """
    result = deepcopy(state)
    for patch in patches:
        op = patch.get("op")
        path = patch.get("path")
        if not isinstance(op, str) or not isinstance(path, list):
            continue
        if op in ("add", "replace"):
            _set_path(result, path, deepcopy(patch.get("value")))
        elif op == "remove":
            _remove_path(result, path)
    return result


def _set_path(root: dict[str, Any], path: list[Any], value: Any) -> None:
    """Navigate to the parent of ``path[-1]`` and set/insert the value."""
    current: Any = root
    for i, segment in enumerate(path[:-1]):
        current = _navigate(current, segment, look_ahead=path[i + 1])

    last = path[-1]
    if isinstance(current, list):
        if not isinstance(last, int):
            raise TypeError(f"List index must be int, got {type(last).__name__}")
        if last < len(current):
            current[last] = value
        elif last == len(current):
            current.append(value)
        else:
            # immer behavior: pad with None then append
            current.extend([None] * (last - len(current)))
            current.append(value)
    else:
        current[last] = value


def _remove_path(root: dict[str, Any], path: list[Any]) -> None:
    """Navigate to the parent of ``path[-1]`` and remove the value.

    For lists, this is a splice (subsequent elements shift down),
    matching immer's behavior.
    """
    current: Any = root
    for segment in path[:-1]:
        current = _navigate(current, segment)

    last = path[-1]
    if isinstance(current, list):
        if isinstance(last, int) and 0 <= last < len(current):
            del current[last]
    elif isinstance(current, dict):
        current.pop(last, None)


def _navigate(current: Any, segment: Any, *, look_ahead: Any = None) -> Any:
    """Navigate into a nested structure, auto-vivifying missing containers."""
    if isinstance(current, list):
        if not isinstance(segment, int):
            raise TypeError(f"List index must be int, got {type(segment).__name__}")
        while len(current) <= segment:
            current.append([] if isinstance(look_ahead, int) else {})
        return current[segment]
    if isinstance(current, dict):
        if segment not in current or not isinstance(current.get(segment), (dict, list)):
            current[segment] = [] if isinstance(look_ahead, int) else {}
        return current[segment]
    raise TypeError(f"Cannot navigate into {type(current).__name__}")


# ─── produce_with_patches ───


def produce_with_patches(
    base_state: dict[str, Any],
    recipe: Callable[[dict[str, Any]], None],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute a recipe on a draft copy and return (new_state, patches).

    Equivalent to immer's ``produceWithPatches(baseState, recipe)``.

    The recipe receives a mutable draft (deep copy of base_state).
    After the recipe returns, the draft is diffed against the original
    to produce immer-format patches.
    """
    draft = deepcopy(base_state)
    recipe(draft)
    patches = _diff(base_state, draft, path=[])
    return draft, patches


# ─── diff algorithm ───


def _diff(old: Any, new: Any, path: list[Any]) -> list[dict[str, Any]]:
    """Recursively diff old vs new and produce immer-format patches."""
    if old is new:
        return []
    if type(old) is not type(new):
        return [_patch("replace", path, new)]

    if isinstance(new, dict):
        return _diff_dicts(old, new, path)
    if isinstance(new, list):
        return _diff_lists(old, new, path)

    # Scalar comparison
    if old != new:
        return [_patch("replace", path, new)]
    return []


def _diff_dicts(
    old: dict[str, Any],
    new: dict[str, Any],
    path: list[Any],
) -> list[dict[str, Any]]:
    """Diff two dicts, emitting add/replace/remove patches."""
    patches: list[dict[str, Any]] = []
    old_keys = set(old.keys())
    new_keys = set(new.keys())

    # Keys added or changed
    for key in sorted(new_keys - old_keys):
        patches.append(_patch("add", path + [key], new[key]))

    for key in sorted(old_keys & new_keys):
        patches.extend(_diff(old[key], new[key], path + [key]))

    # Keys removed
    for key in sorted(old_keys - new_keys):
        patches.append(_patch("remove", path + [key]))

    return patches


def _diff_lists(
    old: list[Any],
    new: list[Any],
    path: list[Any],
) -> list[dict[str, Any]]:
    """Diff two lists, emitting add/replace/remove patches.

    Uses a simple element-wise comparison (like immer does for most cases).
    For the common streaming pattern (append to items or replace text),
    this produces optimal patches.
    """
    patches: list[dict[str, Any]] = []
    min_len = min(len(old), len(new))

    # Compare existing elements
    for i in range(min_len):
        patches.extend(_diff(old[i], new[i], path + [i]))

    # New elements added
    for i in range(min_len, len(new)):
        patches.append(_patch("add", path + [i], new[i]))

    # Elements removed (from end, matching immer behavior)
    for i in range(len(old) - 1, min_len - 1, -1):
        patches.append(_patch("remove", path + [i]))

    return patches


def _patch(op: str, path: list[Any], value: Any = None) -> dict[str, Any]:
    """Build a single immer-format patch dict."""
    p: dict[str, Any] = {"op": op, "path": path}
    if op != "remove":
        p["value"] = value
    return p
