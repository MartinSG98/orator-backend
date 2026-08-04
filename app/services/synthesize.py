"""Speech synthesis via Polly async tasks, staged through S3.

Each chunk of text becomes one start_speech_synthesis_task call. Polly
writes the resulting MP3s to the staging bucket, the job polls the tasks
to completion, downloads the pieces, joins them, and stores the final MP3
locally under the media directory. Staged S3 objects are deleted after a
successful download.

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
POLL_TIMEOUT_SECONDS = 300


class SynthesisError(RuntimeError):
    pass


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


def poll_task(polly, task_id: str) -> str:
    """Wait for a task to finish and return its S3 output URI."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        task = polly.get_speech_synthesis_task(TaskId=task_id)["SynthesisTask"]
        status = task["TaskStatus"]
        if status == "completed":
            return task["OutputUri"]
        if status == "failed":
            reason = task.get("TaskStatusReason", "no reason given")
            raise SynthesisError(f"Polly task {task_id} failed: {reason}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise SynthesisError(f"Polly task {task_id} timed out after {POLL_TIMEOUT_SECONDS}s")


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


def synthesize(
    text: str,
    voice_id: str,
    engine: str,
    language_code: str,
    on_chunk_done=None,
) -> tuple[str, float | None]:
    """Run the full pipeline and return (audio_key, duration_seconds).

    The final MP3 goes through the storage layer, so the returned key is
    what job rows should carry, not a filesystem path.
    """
    settings = get_settings()
    if not settings.s3_bucket:
        raise SynthesisError(
            "no S3 staging bucket configured, set ORATOR_S3_BUCKET"
        )

    chunks = chunk_text(text, limit=SYNTH_CHUNK_LIMIT)
    if not chunks:
        raise SynthesisError("nothing to synthesise")
    if len(chunks) > 1 and not ffmpeg_available():
        raise SynthesisError(
            "ffmpeg is required to join audio for documents this long, "
            "install it and retry"
        )

    polly = aws.client("polly")
    s3 = aws.client("s3")

    task_ids = [
        start_task(polly, chunk, voice_id, engine, language_code) for chunk in chunks
    ]

    audio_key = f"audio/{uuid4().hex}.mp3"

    s3_keys: list[str] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        chunk_paths: list[Path] = []
        for index, task_id in enumerate(task_ids):
            key = s3_key_from_uri(poll_task(polly, task_id))
            s3_keys.append(key)
            local = Path(tmp_dir) / f"chunk_{index:04d}.mp3"
            s3.download_file(settings.s3_bucket, key, str(local))
            chunk_paths.append(local)
            if on_chunk_done is not None:
                on_chunk_done(index + 1)
        output_path = Path(tmp_dir) / "joined.mp3"
        duration = join_chunks(chunk_paths, output_path)
        get_storage().save_file(audio_key, output_path)

    for key in s3_keys:
        s3.delete_object(Bucket=settings.s3_bucket, Key=key)

    return audio_key, duration
