from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse

from app.config import get_settings
from app.models import SynthesisJob, SynthesisJobCreate, SynthesisJobOut
from app.repository import Repository, get_repository
from app.services.job_runner import get_job_runner
from app.services.storage import get_storage
from app.services.synthesize import SYNTH_CHUNK_LIMIT, ffmpeg_available
from app.services.translate import chunk_text
from app.services.voice_catalog import get_language

router = APIRouter(prefix="/api", tags=["synthesis"])


@router.post(
    "/translations/{translation_id}/synthesis",
    response_model=SynthesisJobOut,
    status_code=202,
)
def create_synthesis_job(
    translation_id: int,
    body: SynthesisJobCreate,
    repo: Repository = Depends(get_repository),
) -> SynthesisJob:
    translation = repo.get_translation(translation_id)
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

    job = repo.add_job(
        SynthesisJob(
            translation_id=translation_id,
            voice_id=voice["id"],
            engine=voice["engine"],
            language_code=language["code"],
            total_chunks=len(chunks),
        )
    )

    get_job_runner().dispatch(job.id)
    return job


@router.get("/jobs", response_model=list[SynthesisJobOut])
def list_jobs(
    translation_id: int | None = None, repo: Repository = Depends(get_repository)
) -> list[SynthesisJob]:
    return repo.list_jobs(translation_id)


@router.get("/jobs/{job_id}", response_model=SynthesisJobOut)
def get_job(job_id: int, repo: Repository = Depends(get_repository)) -> SynthesisJob:
    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs/{job_id}/audio")
def get_job_audio(
    job_id: int, repo: Repository = Depends(get_repository)
) -> Response:
    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "completed" or job.audio_path is None:
        raise HTTPException(409, f"job is {job.status}, audio not available")

    storage = get_storage()
    filename = f"synthesis_{job.id}_{job.language_code}_{job.voice_id}.mp3"
    url = storage.presigned_url(job.audio_path, filename)
    if url is not None:
        return RedirectResponse(url, status_code=307)
    path = storage.local_path(job.audio_path)
    if path is None or not path.exists():
        raise HTTPException(404, "audio file is missing from storage")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)
