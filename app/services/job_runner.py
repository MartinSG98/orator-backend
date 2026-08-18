"""Job dispatch behind one interface, per ADR 0007.

The API creates the job row and hands the id to a runner. What "run it"
means is the runtime's business: a daemon thread locally, a Step Functions
execution when deployed.
"""

import threading
from abc import ABC, abstractmethod
from functools import lru_cache

from app.config import get_settings
from app.services.worker import run_synthesis_job


class JobRunner(ABC):
    @abstractmethod
    def dispatch(self, job_id: int) -> None: ...


class ThreadJobRunner(JobRunner):
    def dispatch(self, job_id: int) -> None:
        threading.Thread(
            target=run_synthesis_job, args=(job_id,), daemon=True
        ).start()


@lru_cache
def get_job_runner() -> JobRunner:
    settings = get_settings()
    if settings.runtime == "aws":
        raise RuntimeError("Step Functions runner lands with the next commit")
    return ThreadJobRunner()
