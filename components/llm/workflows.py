from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from proteus import workflow_cls

with workflow.unsafe.imports_passed_through():
    from activities import complete


@workflow_cls
class CompleteWorkflow:
    @workflow.run
    async def run(self, arg: dict) -> dict:
        return await workflow.execute_activity(
            complete,
            arg if isinstance(arg, dict) else {},
            schedule_to_close_timeout=timedelta(seconds=150),
            start_to_close_timeout=timedelta(seconds=150),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
