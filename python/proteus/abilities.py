"""Component access to coordinator coding-agent abilities.

Calls go through Temporal to task queue ``proteus-coordinator``. The Rust
coordinator executes them.
"""

from __future__ import annotations

from proteus.bus_client import bus


class Abilities:
    async def ask(self, prompt: str) -> dict:
        return await bus.activity("coordinator", "ask", {"prompt": prompt})

    async def task(self, prompt: str) -> dict:
        return await bus.activity("coordinator", "task", {"prompt": prompt})


abilities = Abilities()
