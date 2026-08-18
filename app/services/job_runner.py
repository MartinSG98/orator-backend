"""Job dispatch behind one interface, per ADR 0007.

The API creates the job row and hands the id to a runner. What "run it"
means is the runtime's business: a daemon thread locally, a Step Functions
execution when deployed.
"""

import json
import threading
from abc import ABC, abstractmethod
from functools import lru_cache

from app.config import get_settings
from app.services import aws
from app.services.worker import run_synthesis_job


class JobRunner(ABC):
    @abstractmethod
    def dispatch(self, job_id: int) -> None: ...


class ThreadJobRunner(JobRunner):
    def dispatch(self, job_id: int) -> None:
        threading.Thread(
            target=run_synthesis_job, args=(job_id,), daemon=True
        ).start()


class StepFunctionsJobRunner(JobRunner):
    def __init__(self, state_machine_arn: str) -> None:
        if not state_machine_arn:
            raise RuntimeError("aws runtime requires ORATOR_STATE_MACHINE_ARN")
        self._arn = state_machine_arn
        self._sfn = aws.client("stepfunctions")

    def dispatch(self, job_id: int) -> None:
        self._sfn.start_execution(
            stateMachineArn=self._arn,
            name=f"job-{job_id}",
            input=json.dumps({"job_id": job_id}),
        )


@lru_cache
def get_job_runner() -> JobRunner:
    settings = get_settings()
    if settings.runtime == "aws":
        return StepFunctionsJobRunner(settings.state_machine_arn)
    return ThreadJobRunner()
