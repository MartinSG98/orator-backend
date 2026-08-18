"""Speech synthesis via Polly async tasks, staged through S3.

The pipeline is three resumable steps, so both runtimes can drive it:
start_synthesis fires one Polly task per text chunk, check_tasks reports
how far they got, finalize_synthesis downloads the pieces, joins them, and
stores the final MP3 through the storage layer. The local runtime composes
the three in synthesize() with a polling loop, the aws runtime spreads them
across Step Functions states.

A single-chunk result is copied as-is. Multi-chunk results are joined with
ffmpeg's concat demuxer in stream copy mode, which splices the MP3s without
re-encoding, all chunks come from the same Polly voice and format so their
streams are identical. ffmpeg comes bundled in the virtualenv through
imageio-ffmpeg, a system install is preferred when one exists.
"""

import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from app.config import get_settings
from app.services import aws
from app.services.storage import get_storage
from app.services.translate import chunk_text

SYNTH_CHUNK_LIMIT = 2800  # Polly's practical per-task limit for neural voices is 3000
S3_PREFIX = "polly-staging/"
POLL_INTERVAL_SECONDS = 5
POLL_BASE_TIMEOUT_SECONDS = 300
POLL_PER_CHUNK_TIMEOUT_SECONDS = 30


class SynthesisError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskProgress:
    total: int
    completed: int
    failed_reason: str | None
    output_uris: list[str] | None  # set only once every task has completed


def ffmpeg_path() -> str | None:
    """System ffmpeg when present, otherwise the one bundled in the venv."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None


def start_task(polly, text: str, voice_id: str, engine: str, language_code: str) -> str:
    settings = get_settings()
    response = polly.start_speech_synthesis_task(
        Text=text,
        VoiceId=voice_id,
        Engine=engine,
        LanguageCode=language_code,
        OutputFormat="mp3",
        OutputS3BucketName=settings.s3_bucket,
        OutputS3KeyPrefix=S3_PREFIX,
    )
    return response["SynthesisTask"]["TaskId"]


def start_synthesis(
    text: str, voice_id: str, engine: str, language_code: str
) -> list[str]:
    """Chunk the text and fire one Polly task per chunk, returning task ids."""
    settings = get_settings()
    if not settings.s3_bucket:
        raise SynthesisError("no S3 staging bucket configured, set ORATOR_S3_BUCKET")

    chunks = chunk_text(text, limit=SYNTH_CHUNK_LIMIT)
    if not chunks:
        raise SynthesisError("nothing to synthesise")
    if len(chunks) > 1 and not ffmpeg_available():
        raise SynthesisError(
            "ffmpeg is required to join audio for documents this long, "
            "install it and retry"
        )

    polly = aws.client("polly")
    return [
        start_task(polly, chunk, voice_id, engine, language_code) for chunk in chunks
    ]


def check_tasks(task_ids: list[str]) -> TaskProgress:
    """One polling round over every task."""
    polly = aws.client("polly")
    uris: list[str | None] = []
    completed = 0
    failed_reason: str | None = None
    for task_id in task_ids:
        task = polly.get_speech_synthesis_task(TaskId=task_id)["SynthesisTask"]
        status = task["TaskStatus"]
        if status == "completed":
            completed += 1
            uris.append(task["OutputUri"])
        elif status == "failed":
            failed_reason = task.get("TaskStatusReason", "no reason given")
            uris.append(None)
        else:
            uris.append(None)
    all_done = completed == len(task_ids)
    return TaskProgress(
        total=len(task_ids),
        completed=completed,
        failed_reason=failed_reason,
        output_uris=[uri for uri in uris if uri is not None] if all_done else None,
    )


def finalize_synthesis(output_uris: list[str]) -> tuple[str, float | None]:
    """Download the staged pieces, join, store, clean up.

    Returns (audio_key, duration_seconds). The key is what job rows carry.
    """
    settings = get_settings()
    s3 = aws.client("s3")
    audio_key = f"audio/{uuid4().hex}.mp3"
    s3_keys = [s3_key_from_uri(uri) for uri in output_uris]

    with tempfile.TemporaryDirectory() as tmp_dir:
        chunk_paths: list[Path] = []
        for index, key in enumerate(s3_keys):
            local = Path(tmp_dir) / f"chunk_{index:04d}.mp3"
            s3.download_file(settings.s3_bucket, key, str(local))
            chunk_paths.append(local)
        output_path = Path(tmp_dir) / "joined.mp3"
        duration = join_chunks(chunk_paths, output_path)
        get_storage().save_file(audio_key, output_path)

    for key in s3_keys:
        s3.delete_object(Bucket=settings.s3_bucket, Key=key)

    return audio_key, duration


def synthesize(
    text: str,
    voice_id: str,
    engine: str,
    language_code: str,
    on_chunk_done=None,
) -> tuple[str, float | None]:
    """The local composition: start, poll to completion, finalize."""
    task_ids = start_synthesis(text, voice_id, engine, language_code)
    deadline = (
        time.monotonic()
        + POLL_BASE_TIMEOUT_SECONDS
        + POLL_PER_CHUNK_TIMEOUT_SECONDS * len(task_ids)
    )
    reported = 0
    while True:
        progress = check_tasks(task_ids)
        if progress.failed_reason is not None:
            raise SynthesisError(f"Polly task failed: {progress.failed_reason}")
        if on_chunk_done is not None and progress.completed != reported:
            reported = progress.completed
            on_chunk_done(reported)
        if progress.output_uris is not None:
            return finalize_synthesis(progress.output_uris)
        if time.monotonic() > deadline:
            raise SynthesisError("Polly tasks timed out")
        time.sleep(POLL_INTERVAL_SECONDS)


def s3_key_from_uri(output_uri: str) -> str:
    # OutputUri looks like https://s3.<region>.amazonaws.com/<bucket>/<key>
    path = urlparse(output_uri).path.lstrip("/")
    bucket, _, key = path.partition("/")
    return key


def join_chunks(chunk_paths: list[Path], output_path: Path) -> float | None:
    """Write the final MP3 and return its duration in seconds if known."""
    if len(chunk_paths) == 1:
        shutil.copyfile(chunk_paths[0], output_path)
        return _duration_or_none(output_path)

    exe = ffmpeg_path()
    if exe is None:
        raise SynthesisError("ffmpeg is required to join multi-chunk audio")

    list_file = chunk_paths[0].parent / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in chunk_paths), encoding="utf-8"
    )
    result = subprocess.run(
        [exe, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", str(output_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SynthesisError(f"ffmpeg concat failed: {result.stderr[-300:]}")
    return _duration_or_none(output_path)


def _duration_or_none(path: Path) -> float | None:
    """Decode the file with ffmpeg and read the resulting duration.

    Decoding to the null muxer doubles as a validity check, a corrupt file
    reports no time and comes back as None.
    """
    exe = ffmpeg_path()
    if exe is None:
        return None
    result = subprocess.run(
        [exe, "-i", str(path), "-f", "null", "-"], capture_output=True, text=True
    )
    times = re.findall(r"time=(\d+):(\d+):(\d+\.?\d*)", result.stderr)
    if not times:
        return None
    hours, minutes, seconds = times[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
