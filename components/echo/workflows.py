from datetime import timedelta

from temporalio import workflow
from proteus import workflow_cls

with workflow.unsafe.imports_passed_through():
    from activities import echo


@workflow_cls
class EchoWorkflow:
    @workflow.run
    async def run(self, arg: dict) -> dict:
        return await workflow.execute_activity(
            echo,
            arg,
            schedule_to_close_timeout=timedelta(seconds=30),
            start_to_close_timeout=timedelta(seconds=30),
        )
