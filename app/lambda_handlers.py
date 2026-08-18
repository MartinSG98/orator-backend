"""Lambda entry points for the aws runtime.

One deployment artifact serves every function, Terraform just points each
Lambda at a different handler in this module. The api handler wraps the
FastAPI app with Mangum. The synth_* handlers are the Step Functions
states, thin status-keeping wrappers around the shared synthesis core.

The state machine contract, input and output shapes the ASL definition in
the Terraform module relies on:

    execution input   {"job_id": 7}
    synth_start   ->  {"job_id": 7, "task_ids": [...], "polls": 0}
    synth_check   ->  same plus {"done": n, "total": n, "output_uris": [...] | null}
                      raises SynthesisError when any Polly task failed
    choice            output_uris present and not null -> synth_finalize,
                      polls above the cap -> failure path, otherwise wait
                      and check again
    synth_finalize -> {"job_id": 7, "status": "completed"}

Failures raised by any state land in a catch that marks the job row failed
through the Step Functions DynamoDB integration, no Lambda involved. The
job's key in that update is PK="JOB#{job_id}", SK="META".
"""

from datetime import datetime, timezone
from typing import Any

from mangum import Mangum

from app.main import app
from app.repository import get_repository
from app.services.synthesize import (
    SynthesisError,
    check_tasks,
    finalize_synthesis,
    start_synthesis,
)

api = Mangum(app)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def synth_start(event: dict[str, Any], context: Any) -> dict[str, Any]:
    job_id = event["job_id"]
    repo = get_repository()
    job = repo.get_job(job_id)
    if job is None:
        raise SynthesisError(f"job {job_id} does not exist")
    translation = repo.get_translation(job.translation_id)
    if translation is None:
        raise SynthesisError(f"translation {job.translation_id} does not exist")

    job.status = "running"
    job.updated_at = _utcnow()
    repo.save_job(job)

    task_ids = start_synthesis(
        translation.text, job.voice_id, job.engine, job.language_code
    )
    return {"job_id": job_id, "task_ids": task_ids, "polls": 0}


def synth_check(event: dict[str, Any], context: Any) -> dict[str, Any]:
    job_id = event["job_id"]
    task_ids = event["task_ids"]
    progress = check_tasks(task_ids)
    if progress.failed_reason is not None:
        raise SynthesisError(f"Polly task failed: {progress.failed_reason}")

    repo = get_repository()
    job = repo.get_job(job_id)
    if job is not None and job.done_chunks != progress.completed:
        job.done_chunks = progress.completed
        job.updated_at = _utcnow()
        repo.save_job(job)

    return {
        "job_id": job_id,
        "task_ids": task_ids,
        "polls": event["polls"] + 1,
        "done": progress.completed,
        "total": progress.total,
        "output_uris": progress.output_uris,
    }


def synth_finalize(event: dict[str, Any], context: Any) -> dict[str, Any]:
    job_id = event["job_id"]
    audio_key, duration = finalize_synthesis(event["output_uris"])

    repo = get_repository()
    job = repo.get_job(job_id)
    if job is not None:
        job.audio_path = audio_key
        job.duration_seconds = duration
        job.status = "completed"
        job.updated_at = _utcnow()
        repo.save_job(job)

    return {"job_id": job_id, "status": "completed"}
