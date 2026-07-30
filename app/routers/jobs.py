import logging
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_engine, get_session
from app.models import (
    SynthesisJob,
    SynthesisJobCreate,
    SynthesisJobOut,
    Translation,
)
from app.services.synthesize import (
    SYNTH_CHUNK_LIMIT,
    SynthesisError,
    ffmpeg_available,
    synthesize,
)
from app.services.translate import chunk_text
from app.services.voice_catalog import get_language

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["synthesis"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_synthesis_job(job_id: int) -> None:
    """Executed in the background, owns its own session."""
    with Session(get_engine()) as session:
        job = session.get(SynthesisJob, job_id)
        if job is None:
            return
        translation = session.get(Translation, job.translation_id)
        job.status = "running"
        job.updated_at = _utcnow()
        session.add(job)
        session.commit()

        def on_chunk_done(done: int) -> None:
            job.done_chunks = done
            job.updated_at = _utcnow()
            session.add(job)
            session.commit()

        try:
            audio_path, duration = synthesize(
                translation.text,
                job.voice_id,
                job.engine,
                job.language_code,
                on_chunk_done=on_chunk_done,
            )
            job.audio_path = str(audio_path)
            job.duration_seconds = duration
            job.status = "completed"
        except (SynthesisError, BotoCoreError, ClientError) as exc:
            logger.warning("synthesis job %d failed: %s", job_id, exc)
            job.status = "failed"
            job.error = str(exc)[:500]
        job.updated_at = _utcnow()
        session.add(job)
        session.commit()


@router.post(
    "/translations/{translation_id}/synthesis",
    response_model=SynthesisJobOut,
    status_code=202,
)
def create_synthesis_job(
    translation_id: int,
    body: SynthesisJobCreate,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> SynthesisJob:
    translation = session.get(Translation, translation_id)
    if translation is None:
        raise HTTPException(404, "translation not found")

    if not get_settings().s3_bucket:
        raise HTTPException(409, "no S3 staging bucket configured, set ORATOR_S3_BUCKET")

    language = get_language(translation.language_code)
    if language is None:
        raise HTTPException(
            409, f"language '{translation.language_code}' is not in the current catalog"
        )
    voice = next((v for v in language["voices"] if v["id"] == body.voice_id), None)
    if voice is None:
        available = ", ".join(v["id"] for v in language["voices"])
        raise HTTPException(
            400,
            f"voice '{body.voice_id}' does not exist for {translation.language_code}, "
            f"available: {available}",
        )

    chunks = chunk_text(translation.text, limit=SYNTH_CHUNK_LIMIT)
    if len(chunks) > 1 and not ffmpeg_available():
        raise HTTPException(
            409,
            "this text needs multiple audio chunks and joining them requires "
            "ffmpeg, which is not installed on the server",
        )

    job = SynthesisJob(
        translation_id=translation_id,
        voice_id=voice["id"],
        engine=voice["engine"],
        language_code=language["code"],
        total_chunks=len(chunks),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    background.add_task(run_synthesis_job, job.id)
    return job


@router.get("/jobs", response_model=list[SynthesisJobOut])
def list_jobs(
    translation_id: int | None = None, session: Session = Depends(get_session)
) -> list[SynthesisJob]:
    query = select(SynthesisJob).order_by(SynthesisJob.id.desc())
    if translation_id is not None:
        query = query.where(SynthesisJob.translation_id == translation_id)
    return list(session.exec(query).all())


@router.get("/jobs/{job_id}", response_model=SynthesisJobOut)
def get_job(job_id: int, session: Session = Depends(get_session)) -> SynthesisJob:
    job = session.get(SynthesisJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs/{job_id}/audio")
def get_job_audio(job_id: int, session: Session = Depends(get_session)) -> FileResponse:
    job = session.get(SynthesisJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "completed" or job.audio_path is None:
        raise HTTPException(409, f"job is {job.status}, audio not available")

    filename = f"synthesis_{job.id}_{job.language_code}_{job.voice_id}.mp3"
    return FileResponse(job.audio_path, media_type="audio/mpeg", filename=filename)
