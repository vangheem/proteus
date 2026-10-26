"""Temporal bus. Activity calls start ProteusCallWorkflow; workflow calls start the named workflow.

The module is not named ``bus`` so ``from proteus import bus`` returns the client
instance instead of this submodule.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from temporalio.client import Client


class Bus:
    async def activity(self, component: str, name: str, arg: dict | None = None) -> dict:
        result = await self._execute(
            component,
            "ProteusCallWorkflow",
            {"activity": name, "arg": arg or {}},
        )
        return {"result": result}

    async def workflow(self, component: str, name: str, arg: dict | None = None) -> dict:
        result = await self._execute(component, name, arg or {})
        return {"result": result}

    async def _execute(self, component: str, workflow: str, arg: dict) -> Any:
        client = await _connect()
        workflow_id = f"proteus-{component}-{workflow}-{uuid.uuid4().hex}"
        return await client.execute_workflow(
            workflow,
            arg,
            id=workflow_id,
            task_queue=f"proteus-{component}",
        )


bus = Bus()


async def _connect() -> Client:
    address = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    return await Client.connect(address, namespace=namespace)
