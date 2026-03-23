from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    name: str
    label: str
    implemented: bool = True


_PLATFORM_REGISTRY: dict[str, PlatformSpec] = {}


def register_platform(spec: PlatformSpec) -> None:
    _PLATFORM_REGISTRY[spec.name] = spec


def get_platform(name: str) -> PlatformSpec | None:
    return _PLATFORM_REGISTRY.get(name)


def list_platforms() -> list[PlatformSpec]:
    return [spec for _, spec in sorted(_PLATFORM_REGISTRY.items(), key=lambda item: item[0])]
