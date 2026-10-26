"""Activity decorator. Kept free of non-Temporal imports.

The module is not named ``activity`` so ``from proteus import activity`` returns
the decorator instead of this submodule.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from temporalio import activity as temporal_activity

F = TypeVar("F", bound=Callable[..., Any])

_activities: list[Callable[..., Any]] = []


def activity(fn: F | None = None, *, name: str | None = None) -> Any:
    """Apply ``temporalio.activity.defn`` and register the function for discovery."""

    def decorate(func: F) -> F:
        if name is None:
            defined = temporal_activity.defn(func)
        else:
            defined = temporal_activity.defn(name=name)(func)
        if defined not in _activities:
            _activities.append(defined)
        return defined

    if fn is None:
        return decorate
    return decorate(fn)


def registered_activities() -> list[Callable[..., Any]]:
    return list(_activities)
