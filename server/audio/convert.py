"""Phone recording -> 24 kHz mono 16-bit WAV for Muse REST (PRD 11.2, 14.2).

Conversion runs ffmpeg as an async subprocess inside a private temp dir that is removed as soon as the
call returns (PRD 15.1: answer recordings are deleted immediately). Nothing is ever written elsewhere.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import re
import shutil
import tempfile
import wave
from pathlib import Path

log = logging.getLogger("breadcrumbs.audio")

TARGET_RATE = 24_000
TARGET_CHANNELS = 1
TARGET_BITS = 16
FFMPEG_TIMEOUT_S = 30.0
MAX_INPUT_BYTES = 32 * 1024 * 1024
_EXT_RE = re.compile(r"[^a-z0-9]")


class ConversionError(Exception):
    """The upload could not be turned into a valid WAV (bad input, missing ffmpeg, timeout)."""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def wav_info(data: bytes) -> tuple[int, int, int, float]:
    """(channels, sample_rate, bits_per_sample, duration_s) of a PCM WAV; ConversionError if invalid."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError, ValueError) as exc:
        raise ConversionError(f"Not a valid PCM WAV file: {exc}") from exc
    if rate <= 0:
        raise ConversionError("WAV has an invalid sample rate")
    return channels, rate, width * 8, frames / rate


def is_target_wav(data: bytes) -> bool:
    """True when `data` is already a 24 kHz mono 16-bit WAV (no conversion needed)."""
    try:
        channels, rate, bits, _ = wav_info(data)
    except ConversionError:
        return False
    return (channels, rate, bits) == (TARGET_CHANNELS, TARGET_RATE, TARGET_BITS)


def _extension(filename_hint: str) -> str:
    """A safe input extension for ffmpeg's format probe, from "answer.webm", "webm" or a mime-ish hint."""
    hint = filename_hint.strip().lower().split(";")[0]
    raw = Path(hint).suffix.lstrip(".") or hint.rsplit("/", 1)[-1]
    ext = _EXT_RE.sub("", raw)[:5]
    return ext or "bin"


def _canonical_wav(data: bytes) -> bytes:
    """Rewrite a target-format WAV with only fmt + data chunks (drops metadata like -map_metadata -1)."""
    with wave.open(io.BytesIO(data), "rb") as src:
        pcm = src.readframes(src.getnframes())
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(TARGET_CHANNELS)
        dst.setsampwidth(TARGET_BITS // 8)
        dst.setframerate(TARGET_RATE)
        dst.writeframes(pcm)
    return out.getvalue()


async def to_wav_24k(data: bytes, filename_hint: str) -> bytes:
    """Convert any ffmpeg-readable audio to a clean 24 kHz mono s16 WAV."""
    if not data:
        raise ConversionError("The recording is empty")
    if len(data) > MAX_INPUT_BYTES:
        raise ConversionError("The recording is too large (32 MB limit)")
    if is_target_wav(data):
        return _canonical_wav(data)
    if not ffmpeg_available():
        raise ConversionError("ffmpeg is not installed on the server (brew install ffmpeg)")

    workdir = await asyncio.to_thread(tempfile.mkdtemp, prefix="bc-audio-")
    try:
        src = Path(workdir) / f"in.{_extension(filename_hint)}"
        dst = Path(workdir) / "out.wav"
        await asyncio.to_thread(src.write_bytes, data)
        await _run_ffmpeg(src, dst)
        out = await asyncio.to_thread(dst.read_bytes) if dst.exists() else b""
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, True)

    channels, rate, bits, duration = wav_info(out)
    if (channels, rate, bits) != (TARGET_CHANNELS, TARGET_RATE, TARGET_BITS):
        raise ConversionError(f"ffmpeg produced an unexpected format ({channels} ch, {rate} Hz, {bits} bit)")
    log.info("converted %d bytes to WAV (%.1f s)", len(data), duration)
    return out


async def _run_ffmpeg(src: Path, dst: Path) -> None:
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-ac", str(TARGET_CHANNELS), "-ar", str(TARGET_RATE), "-c:a", "pcm_s16le",
        "-map_metadata", "-1",
        str(dst),
    ]  # fmt: skip
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as exc:
        raise ConversionError("ffmpeg is not installed on the server (brew install ffmpeg)") from exc
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=FFMPEG_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise ConversionError(f"ffmpeg timed out after {FFMPEG_TIMEOUT_S:g} s") from exc
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip().splitlines()
        raise ConversionError(f"ffmpeg failed: {detail[-1][:200] if detail else f'exit {proc.returncode}'}")
