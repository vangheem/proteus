"""Built-in workflow that dispatches an activity by name on this worker."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

_ACTIVITY_TIMEOUT = timedelta(seconds=30)


@workflow.defn(name="ProteusCallWorkflow", sandboxed=False)
class ProteusCallWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        activity_name = payload["activity"]
        arg = payload.get("arg") or {}
        return await workflow.execute_activity(
            activity_name,
            arg,
            schedule_to_close_timeout=_ACTIVITY_TIMEOUT,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
