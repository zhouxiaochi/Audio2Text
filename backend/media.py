import asyncio
import json
import math
import shutil
from pathlib import Path


class MediaError(RuntimeError):
    pass


async def _run(*args: str) -> tuple[bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"required executable not found: {args[0]}") from exc
    stdout, stderr = await process.communicate()
    if process.returncode:
        message = stderr.decode(errors="replace").strip()
        raise MediaError(message[-2000:] or f"{args[0]} failed")
    return stdout, stderr


def ensure_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise MediaError(f"missing media tools: {', '.join(missing)}")


async def probe(path: Path) -> dict:
    stdout, _ = await _run(
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    )
    result = json.loads(stdout)
    audio_streams = [stream for stream in result.get("streams", []) if stream.get("codec_type") == "audio"]
    if not audio_streams:
        raise MediaError("uploaded file contains no audio stream")
    duration = float(result.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise MediaError("could not determine a positive audio duration")
    return {
        "duration": duration,
        "format_name": result.get("format", {}).get("format_name"),
        "audio_codec": audio_streams[0].get("codec_name"),
        "sample_rate": int(audio_streams[0].get("sample_rate") or 0),
        "channels": int(audio_streams[0].get("channels") or 0),
    }


async def normalize(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    await _run(
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(destination),
    )


async def split_chunks(
    source: Path,
    output_dir: Path,
    duration: float,
    chunk_seconds: int,
    overlap_seconds: int,
) -> list[dict]:
    if chunk_seconds < 300 or chunk_seconds > 600:
        raise MediaError("chunk_seconds must be between 300 and 600")
    if overlap_seconds < 0 or overlap_seconds >= chunk_seconds:
        raise MediaError("invalid overlap_seconds")
    output_dir.mkdir(parents=True, exist_ok=True)
    step = chunk_seconds - overlap_seconds
    count = max(1, math.ceil(max(0, duration - overlap_seconds) / step))
    chunks: list[dict] = []
    for index in range(count):
        start = index * step
        length = min(chunk_seconds, duration - start)
        if length <= 0:
            break
        path = output_dir / f"chunk-{index:04d}.flac"
        if not path.exists():
            await _run(
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{length:.3f}",
                "-i",
                str(source),
                "-c:a",
                "flac",
                str(path),
            )
        chunks.append({"index": index, "start": start, "duration": length, "path": str(path)})
    return chunks
