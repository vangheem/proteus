"""Proteus component SDK.

Heavy imports stay lazy. ``activity`` and ``workflow_cls`` are bound here so
``from proteus import activity`` cannot be captured by a submodule of the same name.
Those two modules import only Temporal, which workflow code is allowed to load.
"""

from __future__ import annotations

from typing import Any

from proteus.activity_def import activity
from proteus.workflow_def import workflow_cls

__all__ = [
    "Component",
    "Store",
    "Cache",
    "bus",
    "abilities",
    "styles",
    "activity",
    "workflow_cls",
]


def __getattr__(name: str) -> Any:
    if name == "Component":
        from proteus.component import Component

        return Component
    if name == "Store":
        from proteus.store import Store

        return Store
    if name == "Cache":
        from proteus.cache import Cache

        return Cache
    if name == "bus":
        from proteus.bus_client import bus

        return bus
    if name == "abilities":
        from proteus.abilities import abilities

        return abilities
    if name == "styles":
        from proteus.styles import styles

        return styles
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
