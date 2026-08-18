"""Start-to-finish execution of one synthesis job, used by the local runtime.

The aws runtime runs the same pipeline, but split across Step Functions
states instead of one process, so this module stays thin: status handling
around the shared synthesis core.
"""

import logging
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError

from app.repository import get_repository
from app.services.synthesize import SynthesisError, synthesize

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_synthesis_job(job_id: int) -> None:
    repo = get_repository()
    job = repo.get_job(job_id)
    if job is None:
        return
    translation = repo.get_translation(job.translation_id)
    if translation is None:
        return
    job.status = "running"
    job.updated_at = _utcnow()
    job = repo.save_job(job)

    def on_chunk_done(done: int) -> None:
        job.done_chunks = done
        job.updated_at = _utcnow()
        repo.save_job(job)

    try:
        audio_key, duration = synthesize(
            translation.text,
            job.voice_id,
            job.engine,
            job.language_code,
            on_chunk_done=on_chunk_done,
        )
        job.audio_path = audio_key
        job.duration_seconds = duration
        job.status = "completed"
    except (SynthesisError, BotoCoreError, ClientError) as exc:
        logger.warning("synthesis job %d failed: %s", job_id, exc)
        job.status = "failed"
        job.error = str(exc)[:500]
    job.updated_at = _utcnow()
    repo.save_job(job)
