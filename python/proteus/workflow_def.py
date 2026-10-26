"""Workflow class decorator. Kept free of non-Temporal imports."""

from __future__ import annotations

from typing import Any, TypeVar

from temporalio import workflow as temporal_workflow

T = TypeVar("T", bound=type)


_workflows: list[type] = []


def workflow_cls(cls: T | None = None, *, name: str | None = None) -> Any:
    """Apply ``temporalio.workflow.defn`` and register the class for discovery."""

    def decorate(klass: T) -> T:
        if name is None:
            defined = temporal_workflow.defn(klass)
        else:
            defined = temporal_workflow.defn(name=name)(klass)
        if defined not in _workflows:
            _workflows.append(defined)
        return defined

    if cls is None:
        return decorate
    return decorate(cls)


def registered_workflows() -> list[type]:
    return list(_workflows)
