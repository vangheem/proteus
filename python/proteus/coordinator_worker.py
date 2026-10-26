"""Temporal worker for coordinator abilities.

The Rust coordinator starts this process. Activities call back into the
coordinator, which owns the coding-agent integration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

logger = logging.getLogger("proteus.abilities")
# The coordinator keeps waiting while the agent is working. This only bounds a
# hung activity so Temporal can still notice a dead worker.
ACTIVITY_TIMEOUT = timedelta(hours=24)


def _timeout() -> timedelta:
    return ACTIVITY_TIMEOUT


@activity.defn(name="ask")
async def ask(arg: dict) -> dict:
    return await _invoke("ask", arg)


@activity.defn(name="task")
async def task(arg: dict) -> dict:
    return await _invoke("task", arg)


async def _invoke(name: str, arg: dict) -> dict:
    url = os.environ.get(
        "PROTEUS_ABILITY_URL",
        "http://host.docker.internal:8080/__proteus/api/abilities/invoke",
    )
    payload = json.dumps({"name": name, "arg": arg or {}}).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    timeout = _timeout().total_seconds()

    def send() -> dict:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
        result = body.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"ability {name} returned no result")
        return result

    try:
        return await asyncio.to_thread(send)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"ability {name} failed: {exc.code} {detail}") from exc


@workflow.defn(name="ProteusCallWorkflow", sandboxed=False)
class ProteusCallWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        name = payload["activity"]
        arg = payload.get("arg") or {}
        return await workflow.execute_activity(
            name,
            arg,
            schedule_to_close_timeout=ACTIVITY_TIMEOUT,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
        )


async def _run() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    delay = 1.0
    while True:
        try:
            client = await Client.connect(address, namespace=namespace)
            worker = Worker(
                client,
                task_queue="proteus-coordinator",
                workflows=[ProteusCallWorkflow],
                activities=[ask, task],
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
            logger.info("ability worker listening on proteus-coordinator")
            await worker.run()
            delay = 1.0
        except Exception:
            logger.warning("ability worker unavailable; retrying in %.0fs", delay, exc_info=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


def main() -> None:
    global ACTIVITY_TIMEOUT
    logging.basicConfig(level=logging.INFO)
    ACTIVITY_TIMEOUT = _timeout()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
